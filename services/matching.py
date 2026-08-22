"""
============================================================
 ShelfSenseAI - Phase 3C Product Matching Service
============================================================
 Connects a shop's `Product` to `MarketItem`s through the
 `ProductMarketMatch` mapping table.

 Two scoring passes over the market catalogue:

   * EXACT pass - deterministic identity matches:
       1. normalized_title == clean_text(product.name)      -> 1.00
       2. brand equality + one title contains the other     -> 0.95

   * FUZZY pass - RapidFuzz token similarity blended with a
     package-size agreement term, so "BERAS 10 kg" is never
     suggested for "BERAS 1 kg":

       confidence = 0.75 * title_score + 0.25 * package_score

       package_score: 1.0 same base unit + qty within +-5%
                     0.6 same base unit, different quantity
                     0.3 different base unit
                     0.0 product has no package defined

 Only candidates the shop has NOT already verified or rejected are
 considered; verified links are never overwritten.

 The pure scoring helpers (`package_score`, `_exact_pass`,
 `_fuzzy_pass`, `score_product`) do not touch the database and are
 fully unit-testable. `find_matches` / `apply_suggestions` are the
 DB-aware layer used by the routes.
============================================================
"""
from decimal import Decimal

from rapidfuzz import fuzz
from sqlalchemy import func

from app import db, MarketItem, ProductMarketMatch
from utils.normalization import clean_text, normalize_package_size

MIN_CONFIDENCE = 0.55      # below this, do not suggest
TOP_K = 3                  # how many suggestions to keep per product
PACKAGE_TOLERANCE = 0.05   # +-5% quantity agreement counts as "same package"
FUZZY_GATE = 55            # cheap token_set_ratio pre-filter


# -------------------------------------------------
# Pure scoring helpers (no DB access - unit-testable)
# -------------------------------------------------
def package_key(quantity, unit):
    """(quantity, unit) -> (base_quantity, base_unit) or None.

    None when the product has no package defined (quantity or unit blank),
    which the caller treats as "package unknown" (package_score = 0.0)."""
    if quantity is None or not (unit or '').strip():
        return None
    q, u = normalize_package_size(float(quantity), unit)
    return (q, u)


def package_score(product_qty, product_unit, item_qty, item_unit):
    """How well a shop product's package agrees with a market item's.

    Returns one of 1.0 / 0.6 / 0.3 / 0.0 (see module docstring for the
    table). Pure: takes plain numbers/strings, returns a float."""
    pk = package_key(product_qty, product_unit)
    if pk is None:
        return 0.0                       # shop product package unknown
    pq, pu = pk
    try:
        iq = float(item_qty)
    except (TypeError, ValueError):
        iq = 0.0
    iu = (item_unit or '').strip().lower()
    if iu != pu:
        return 0.3                       # different base unit (kg vs l, ...)
    if iq <= 0:
        return 0.6
    ratio = abs(pq - iq) / max(pq, iq)
    return 1.0 if ratio <= PACKAGE_TOLERANCE else 0.6


def _exact_pass(name, brand, candidates):
    """Deterministic identity matches -> list of (item, Decimal, 'exact')."""
    pname = clean_text(name)
    pbrand = clean_text(brand) if brand else ''
    out = []
    for it in candidates:
        if it.normalized_title == pname:
            out.append((it, Decimal('1.00'), 'exact'))
            continue
        mbrand = clean_text(it.brand) if it.brand else ''
        if pbrand and mbrand and pbrand == mbrand:
            if pname in it.normalized_title or it.normalized_title in pname:
                out.append((it, Decimal('0.95'), 'exact'))
    return out


def _fuzzy_pass(name, quantity, unit, candidates, top_k):
    """RapidFuzz token similarity + package agreement -> top-k fuzzy rows."""
    pname = clean_text(name)
    scored = []
    for it in candidates:
        # Cheap gate first: skip clearly unrelated titles before the
        # more expensive WRatio call.
        if fuzz.token_set_ratio(pname, it.normalized_title) < FUZZY_GATE:
            continue
        title_score = fuzz.WRatio(pname, it.normalized_title) / 100.0
        ps = package_score(quantity, unit,
                           it.package_quantity, it.package_unit)
        conf = round(0.75 * title_score + 0.25 * ps, 2)
        if conf >= MIN_CONFIDENCE:
            scored.append((it, Decimal(str(conf)), 'fuzzy'))
    scored.sort(key=lambda t: t[1], reverse=True)
    return scored[:top_k]


def score_product(product, candidates, top_k=TOP_K):
    """Ranked [(MarketItem, confidence, match_type)] for ONE product.

    Exact matches first (each 1.00/0.95), then the fuzzy top-k. `product`
    only needs `.name`, `.brand`, `.quantity`, `.unit` attributes, so
    tests can pass a stub - no database involved."""
    exact = _exact_pass(product.name, product.brand, candidates)
    exact_ids = {id(it) for it, _, _ in exact}
    remaining = [it for it in candidates if id(it) not in exact_ids]
    fuzzy = _fuzzy_pass(product.name, product.quantity, product.unit,
                        remaining, top_k)
    return exact + fuzzy


# -------------------------------------------------
# DB-aware layer (used by the routes)
# -------------------------------------------------
def _excluded_ids(product):
    """market_item_ids the shop already verified or rejected."""
    return {m.market_item_id for m in product.market_matches
            if m.is_verified or m.is_rejected}


def find_matches(product, top_k=TOP_K):
    """Ranked suggestions for a shop product (never verified/rejected items).

    Candidates are category-preferred: when the product has a category,
    matching `item_category` items are scored first. If that filter empties
    the pool (a free-typed category that matches nothing in the taxonomy),
    fall back to the full catalogue so the Search button never returns
    nothing forever."""
    excluded = _excluded_ids(product)
    candidates = []
    if product.category:
        candidates = [mi for mi in
                      MarketItem.query.filter(
                          func.lower(MarketItem.category) ==
                          product.category.strip().lower()).all()
                      if mi.id not in excluded]
    if not candidates:
        candidates = [mi for mi in MarketItem.query.all()
                      if mi.id not in excluded]
    return score_product(product, candidates, top_k)


def apply_suggestions(product, top_k=TOP_K):
    """Refresh a product's unverified suggestions in place.

    Drops the product's stale unverified+unrejected suggestion rows and
    inserts the fresh top-k. NEVER touches verified or rejected rows.
    Returns the number of suggestions created. Caller must commit."""
    ProductMarketMatch.query.filter_by(
        shop_product_id=product.id,
        is_verified=False,
        is_rejected=False,
    ).delete(synchronize_session=False)
    created = 0
    for item, conf, mtype in find_matches(product, top_k):
        db.session.add(ProductMarketMatch(
            shop_product_id=product.id,
            market_item_id=item.id,
            confidence_score=conf,
            match_type=mtype,
            is_verified=False,
            is_rejected=False,
        ))
        created += 1
    return created
