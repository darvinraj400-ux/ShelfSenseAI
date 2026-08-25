"""
============================================================
 ShelfSenseAI - Phase 3C Product Matching Service
============================================================

This module connects a shop's `Product` to external `MarketItem`s through
the `ProductMarketMatch` mapping table. It is the bridge between the
internal retail domain (what the shop sells) and the external market
intelligence domain (what the government/retailers report).

MATCHING ALGORITHM
------------------
The matching pipeline uses a two-pass approach over the ~405 MarketItems
in our PriceCatcher dataset:

PASS 1 — EXACT (deterministic identity matches):
  1. Normalized title equality: clean_text(product.name) == item.normalized_title
     -> confidence = 1.00 (perfect match)
  2. Brand containment: same brand AND one name contains the other
     -> confidence = 0.95 (near-perfect match)

PASS 2 — FUZZY (RapidFuzz token similarity):
  Uses RapidFuzz's WRatio algorithm (weighted combination of multiple
  string similarity metrics) blended with a package-size agreement term:

    confidence = 0.75 * title_score + 0.25 * package_score

  Where package_score is:
    1.0 = same base unit AND quantity within +-5% (e.g. 10kg vs 10.2kg)
    0.6 = same base unit but different quantity (e.g. 10kg vs 1kg)
    0.3 = different base unit (e.g. kg vs l)
    0.0 = product has no package defined

DESIGN DECISIONS
----------------
- A cheap `token_set_ratio` pre-filter (FUZZY_GATE=55) skips clearly
  unrelated titles before the more expensive WRatio call, keeping the
  O(n) scan fast even with 405 market items.
- Only candidates the shop has NOT already verified or rejected are
  considered. Verified links are never overwritten by re-matching.
- Suggestions are refreshed atomically: stale unverified rows are
  deleted and fresh top-K suggestions are inserted.
- The scoring functions are PURE (no DB access) and fully unit-testable.
- Synchronous execution: with only ~405 items, async is unnecessary overhead.
============================================================
"""
from decimal import Decimal

from rapidfuzz import fuzz
from sqlalchemy import func

from app import db, MarketItem, ProductMarketMatch
from utils.normalization import clean_text, normalize_package_size

# -------------------------------------------------
# CONSTANTS — Tuning parameters for the matching algorithm.
# -------------------------------------------------
MIN_CONFIDENCE = 0.55      # Below this threshold, a suggestion is not shown.
                            # Tuned to exclude noise while catching valid matches.
TOP_K = 3                  # Maximum suggestions per product. Keeps the UI clean
                            # while giving the owner enough choices.
PACKAGE_TOLERANCE = 0.05   # +-5% quantity agreement counts as "same package".
                            # Handles rounding differences (10.0 vs 10.4 kg).
FUZZY_GATE = 55            # Cheap token_set_ratio pre-filter. Items below this
                            # score are skipped entirely before the expensive
                            # WRatio call. Prevents obvious non-matches (e.g.
                            # "BERAS" vs "TEPUNG") from consuming CPU.


# -------------------------------------------------
# PURE SCORING HELPERS
#
# These functions take plain numbers/strings and return scores.
# They do NOT access the database, making them fully unit-testable
# in isolation without a MySQL connection.
# -------------------------------------------------

def package_key(quantity, unit):
    """Convert raw (quantity, unit) into a normalized (base_quantity, base_unit) pair.

    This is the prerequisite for comparing package sizes between a shop
    product and a market item. The normalization converts both to the
    same base unit (kg, l, or unit) so that 500g and 0.5kg are recognized
    as equivalent.

    Args:
        quantity: Numeric package size (e.g. 500, 10, 1.5).
        unit: Unit string (e.g. 'g', 'kg', 'L', 'pcs').

    Returns:
        A tuple (base_quantity, base_unit) after normalization,
        or None if the product has no package defined (quantity is None
        or unit is blank). A None return signals "package unknown" to
        the caller, which typically assigns a package_score of 0.0.
    """
    # Guard: if quantity is None or unit is empty/whitespace, the package
    # is undefined — we cannot compare sizes.
    if quantity is None or not (unit or '').strip():
        return None
    # Normalize to base units (e.g. 500g -> (0.5, 'kg')).
    q, u = normalize_package_size(float(quantity), unit)
    return (q, u)


def package_score(product_qty, product_unit, item_qty, item_unit):
    """Score how well a shop product's package agrees with a market item's.

    This function implements the package-size penalty that prevents
    "BERAS 10 kg" from being suggested for "BERAS 1 kg" — the title
    might be similar, but the package sizes are fundamentally different.

    Scoring table:
        1.0 = same base unit AND quantity within +-5%
              (e.g. product=10kg, item=10.4kg — both are "10 kg bags")
        0.6 = same base unit but different quantity
              (e.g. product=10kg, item=1kg — both are "kg" but different sizes)
        0.3 = different base unit
              (e.g. product=1kg, item=1l — weight vs volume)
        0.0 = product has no package defined
              (quantity or unit is None/blank)

    Args:
        product_qty: Shop product's package quantity (e.g. 10).
        product_unit: Shop product's unit (e.g. 'kg').
        item_qty: Market item's package quantity (e.g. 0.5).
        item_unit: Market item's unit (e.g. 'kg').

    Returns:
        A float score between 0.0 and 1.0.
    """
    # Step 1: Normalize the shop product's package to base units.
    pk = package_key(product_qty, product_unit)
    if pk is None:
        # Product has no package defined — cannot compare, so score is 0.
        return 0.0

    pq, pu = pk  # pu = base unit (e.g. 'kg'), pq = base quantity (e.g. 10.0)

    # Step 2: Parse the market item's quantity (may be None or non-numeric).
    try:
        iq = float(item_qty)
    except (TypeError, ValueError):
        iq = 0.0

    # Step 3: Normalize the market item's unit string for comparison.
    iu = (item_unit or '').strip().lower()

    # Step 4: Compare base units first — different units get a penalty (0.3).
    # This handles weight-vs-volume comparisons (kg vs l).
    if iu != pu:
        return 0.3

    # Step 5: Same base unit — check quantity agreement.
    # If item quantity is zero or unknown, default to 0.6 (same unit, unknown size).
    if iq <= 0:
        return 0.6

    # Step 6: Calculate the relative difference between quantities.
    # Using the max as denominator ensures the ratio is symmetric
    # (10 vs 12 and 12 vs 10 produce the same ratio).
    ratio = abs(pq - iq) / max(pq, iq)

    # Step 7: If within +-5% tolerance, it's the same package size (1.0).
    # Otherwise, same unit but different size (0.6).
    return 1.0 if ratio <= PACKAGE_TOLERANCE else 0.6


def _exact_pass(name, brand, candidates):
    """Deterministic identity matches — no fuzzy logic, no RapidFuzz.

    This pass finds market items that are unambiguously the same product
    as the shop item, based on exact text comparison after normalization.

    Two matching criteria:
      1. Normalized title equality (confidence = 1.00):
         clean_text(product.name) == item.normalized_title
         This handles case differences, trademark symbols, and whitespace.

      2. Brand containment (confidence = 0.95):
         Same brand AND one name is a substring of the other.
         This catches "SOS CILI ADABI EXTRA PEDAS" matching "SOS CILI ADABI"
         when both share the brand "Adabi".

    Args:
        name: The shop product's name (e.g. 'BERAS CAP JASMINE').
        brand: The shop product's brand (e.g. 'JASMINE'), or None.
        candidates: List of MarketItem objects to search.

    Returns:
        A list of (MarketItem, Decimal('1.00'|'0.95'), 'exact') tuples.
    """
    # Normalize the product name for comparison.
    pname = clean_text(name)
    # Normalize the brand (lowercase + trimmed) for equality check.
    pbrand = clean_text(brand) if brand else ''

    out = []
    for it in candidates:
        # Criterion 1: Exact normalized title match.
        # After clean_text(), "BERAS CAP JASMINE" and "beras cap jasmine"
        # become identical strings.
        if it.normalized_title == pname:
            out.append((it, Decimal('1.00'), 'exact'))
            continue

        # Criterion 2: Brand containment match.
        # Both must have a non-empty brand, and the brands must be identical.
        # Then check if one title contains the other (substring match).
        mbrand = clean_text(it.brand) if it.brand else ''
        if pbrand and mbrand and pbrand == mbrand:
            if pname in it.normalized_title or it.normalized_title in pname:
                out.append((it, Decimal('0.95'), 'exact'))

    return out


def _fuzzy_pass(name, quantity, unit, candidates, top_k):
    """RapidFuzz token similarity + package agreement -> top-k fuzzy matches.

    This pass uses RapidFuzz's WRatio algorithm to find market items
    that are SIMILAR but not identical to the shop product name. The
    similarity score is blended with the package-size agreement to
    produce a final confidence score.

    Pipeline for each candidate:
      1. Cheap pre-filter: token_set_ratio < FUZZY_GATE -> skip
         (avoids expensive WRatio for obviously unrelated items)
      2. Compute title_score = WRatio(product_name, item_title) / 100.0
         (WRatio combines multiple similarity metrics: ratio, partial_ratio,
          token_sort_ratio, token_set_ratio — weighted by string length)
      3. Compute package_score (0.0 to 1.0) for size agreement
      4. Blend: confidence = 0.75 * title_score + 0.25 * package_score
      5. Filter by MIN_CONFIDENCE, sort by confidence descending, take top-k

    Args:
        name: The shop product's name.
        quantity: The shop product's package quantity.
        unit: The shop product's unit.
        candidates: List of MarketItem objects to score.
        top_k: Maximum number of results to return.

    Returns:
        A list of (MarketItem, Decimal(confidence), 'fuzzy') tuples,
        sorted by confidence descending, limited to top_k.
    """
    # Normalize the product name for comparison.
    pname = clean_text(name)
    scored = []

    for it in candidates:
        # CHEAP PRE-FILTER: token_set_ratio is much faster than WRatio.
        # It tokenizes both strings and compares token sets, ignoring order.
        # Items below the gate threshold are clearly unrelated and skipped.
        if fuzz.token_set_ratio(pname, it.normalized_title) < FUZZY_GATE:
            continue

        # FULL SIMILARITY SCORE: WRatio is RapidFuzz's most comprehensive
        # metric — it runs multiple algorithms and picks the best score,
        # weighted by string length. Dividing by 100 normalizes to 0-1 range.
        title_score = fuzz.WRatio(pname, it.normalized_title) / 100.0

        # PACKAGE AGREEMENT: penalizes matches where the package sizes differ.
        # A 10kg bag should not be suggested for a 1kg product.
        ps = package_score(quantity, unit,
                           it.package_quantity, it.package_unit)

        # BLENDED CONFIDENCE: 75% title similarity + 25% package agreement.
        # The weight favors title similarity because product name is the
        # primary identification signal; package size is secondary.
        conf = round(0.75 * title_score + 0.25 * ps, 2)

        # Apply the confidence threshold to filter out weak matches.
        if conf >= MIN_CONFIDENCE:
            scored.append((it, Decimal(str(conf)), 'fuzzy'))

    # Sort by confidence descending (best matches first).
    scored.sort(key=lambda t: t[1], reverse=True)

    # Limit to top_k to keep the UI manageable.
    return scored[:top_k]


def score_product(product, candidates, top_k=TOP_K):
    """Rank all matching candidates for a single shop product.

    This is the main scoring entry point. It runs both passes
    (exact + fuzzy) and returns a combined, ranked list:
      - Exact matches first (confidence 1.00 or 0.95)
      - Fuzzy matches second (confidence 0.55 to ~0.99)

    The `product` parameter only needs `.name`, `.brand`, `.quantity`,
    and `.unit` attributes — no database access required. This allows
    tests to pass lightweight stub objects instead of full ORM models.

    Args:
        product: An object with name, brand, quantity, unit attributes.
        candidates: List of MarketItem objects to score against.
        top_k: Maximum fuzzy results (exact matches are always included).

    Returns:
        A list of (MarketItem, Decimal(confidence), match_type) tuples,
        sorted by confidence descending.
    """
    # Step 1: Run the exact pass — deterministic identity matches.
    exact = _exact_pass(product.name, product.brand, candidates)

    # Step 2: Exclude exact-match items from the fuzzy pass to avoid duplicates.
    exact_ids = {id(it) for it, _, _ in exact}
    remaining = [it for it in candidates if id(it) not in exact_ids]

    # Step 3: Run the fuzzy pass on the remaining candidates.
    fuzzy = _fuzzy_pass(product.name, product.quantity, product.unit,
                        remaining, top_k)

    # Step 4: Combine — exact matches always come first in the list.
    return exact + fuzzy


# -------------------------------------------------
# DATABASE-AWARE LAYER
#
# These functions interact with SQLAlchemy and the database.
# They are used by the Flask routes in app.py.
# -------------------------------------------------

def _excluded_ids(product):
    """Get the set of market_item_ids the shop has already verified or rejected.

    This prevents the matching engine from suggesting items that the
    owner/manager has already explicitly confirmed or rejected. Verified
    links are permanent; rejected suggestions are hidden permanently
    to avoid appearing like a bug when they keep reappearing.

    Args:
        product: A Product ORM object with .market_matches relationship.

    Returns:
        A set of market_item_id integers to exclude from matching.
    """
    # Filter the product's match list for verified or rejected rows,
    # then extract just the market_item_id values.
    return {m.market_item_id for m in product.market_matches
            if m.is_verified or m.is_rejected}


def find_matches(product, top_k=TOP_K):
    """Find ranked market-matching suggestions for a shop product.

    This function first tries to narrow the candidate pool to items
    in the same category (e.g. BERAS products match BERAS market items).
    If that category filter produces no candidates (e.g. the user typed
    a category not in the PriceCatcher taxonomy), it falls back to the
    full catalogue so the Search button never returns nothing forever.

    Args:
        product: A Product ORM object (needs .name, .brand, .quantity,
                 .unit, .category, and .market_matches).
        top_k: Maximum number of fuzzy results.

    Returns:
        A list of (MarketItem, Decimal, match_type) tuples from score_product().
    """
    # Step 1: Build the exclusion set (already verified/rejected items).
    excluded = _excluded_ids(product)

    # Step 2: Try category-filtered candidates first for relevance.
    candidates = []
    if product.category:
        # Query market items matching the product's category (case-insensitive).
        candidates = [mi for mi in
                      MarketItem.query.filter(
                          func.lower(MarketItem.category) ==
                          product.category.strip().lower()).all()
                      if mi.id not in excluded]

    # Step 3: Fallback to full catalogue if category filter yields nothing.
    if not candidates:
        candidates = [mi for mi in MarketItem.query.all()
                      if mi.id not in excluded]

    # Step 4: Score all candidates and return the ranked list.
    return score_product(product, candidates, top_k)


def apply_suggestions(product, top_k=TOP_K):
    """Refresh a product's unverified market-matching suggestions in place.

    This function is the primary entry point called by routes after
    creating or editing a product. It performs an atomic refresh:
      1. Delete all stale unverified + unrejected suggestion rows
      2. Insert the fresh top-k suggestions from find_matches()

    CRITICAL: Verified and rejected rows are NEVER touched. This ensures
    that the owner's explicit confirmations and rejections persist across
    re-matching cycles.

    Args:
        product: A Product ORM object.
        top_k: Maximum suggestions to create.

    Returns:
        The number of new suggestion rows created. The caller must commit.
    """
    # Step 1: Delete stale suggestions (only unverified + unrejected rows).
    # synchronize_session=False avoids SQLAlchemy identity-map issues.
    ProductMarketMatch.query.filter_by(
        shop_product_id=product.id,
        is_verified=False,
        is_rejected=False,
    ).delete(synchronize_session=False)

    # Step 2: Score the product against the market catalogue.
    created = 0
    for item, conf, mtype in find_matches(product, top_k):
        # Step 3: Insert each new suggestion as an unverified match.
        db.session.add(ProductMarketMatch(
            shop_product_id=product.id,
            market_item_id=item.id,
            confidence_score=conf,
            match_type=mtype,
            is_verified=False,
            is_rejected=False,
        ))
        created += 1

    # Step 4: Return the count — caller is responsible for committing.
    return created
