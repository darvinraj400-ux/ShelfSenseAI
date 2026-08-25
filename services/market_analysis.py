"""
============================================================
 ShelfSenseAI - Phase 3D Market Analysis Engine
============================================================

This module aggregates market price observations for a shop product's
VERIFIED market matches into clean, normalized statistics. It is the
statistical backbone that powers the Market Intelligence tab and feeds
the pricing engine.

PIPELINE
--------
1. Take every price observation of every VERIFIED ProductMarketMatch
   whose MarketSource is active (MarketSource.is_active).
2. Each observation stores a `normalized_unit_price` = RM per BASE unit
   (kg / l / unit). Scale it back to the SHOP PRODUCT's own package size,
   e.g. a 10 kg bag of rice compares against "market price per 10 kg"
   (RM/kg x 10).
3. Filter invalid prices (<= 0 / None) — the outlier handling step.
4. Compute statistical metrics:
     N        = number of valid observations ("competitors")
     min/max  = price range across all observations
     mean     = arithmetic average
     median   = middle value (robust center, less sensitive to outliers)
     spread   = max - min (price volatility indicator)
     PPI      = Price Position Index: (shop price / median) x 100
               (100 = exactly at the market median)

ARCHITECTURAL DECISIONS
-----------------------
- `compute_metrics` is PURE (plain numbers in, dict out) and unit-testable.
- `get_market_stats` is the DB-aware layer used by the API route.
- Products without verified matches return an all-None metrics dict with n=0,
  ensuring the UI never crashes on empty data.
- Package-less products compare per base unit (no scaling applied).
============================================================
"""
from statistics import median as _median, mean as _mean

from app import (db, Product, ProductMarketMatch,            # noqa: E402
                 MarketPriceObservation, MarketItem, MarketSource)
from utils.normalization import normalize_package_size        # noqa: E402


# -------------------------------------------------
# UTILITY HELPERS
# -------------------------------------------------

def _r2(v):
    """Round a float to 2 decimal places for consistent display."""
    return round(v, 2)


def _r1(v):
    """Round a float to 1 decimal place (used for PPI percentages)."""
    return round(v, 1)


def _fmt_qty(v):
    """Format a quantity for display: 10.0 -> '10', 0.5 -> '0.5'.

    This removes trailing zeros for clean display in the UI while
    preserving the decimal for fractional quantities.
    """
    if v == int(v):
        return str(int(v))
    return ('%g' % v).rstrip('0').rstrip('.')


def _product_base_quantity(product):
    """Compute the normalized base quantity of ONE product package.

    This converts the product's package size to base units, e.g.:
      - 10 kg  -> 10.0 (kg is already base)
      - 500 g  -> 0.5  (grams converted to kg)
      - 2 L    -> 2.0  (litres is already base)

    The result is used to SCALE market prices (which are per base unit)
    UP to the product's package size for direct comparison.

    Args:
        product: A Product ORM object with .quantity and .unit.

    Returns:
        The base quantity as a float, or None if the product has no
        package defined (quantity is None or unit is blank).
    """
    # Guard: product must have both quantity and unit defined.
    if product.quantity is None or not (product.unit or '').strip():
        return None

    # Normalize to base units (e.g. 500g -> (0.5, 'kg')).
    q, _ = normalize_package_size(float(product.quantity), product.unit)
    return q


# -------------------------------------------------
# PURE METRIC COMPUTATION
#
# This function takes a list of already-scaled market prices
# (in RM, matching the product's package size) and computes
# statistical metrics. No database access — fully unit-testable.
# -------------------------------------------------

def compute_metrics(scaled_prices, shop_price=None):
    """Compute statistical metrics from a list of market prices.

    This is the core statistical function that processes pre-scaled
    market prices (each already multiplied by the product's package
    quantity to match its unit of comparison).

    Processing steps:
      1. Filter out invalid values (None, <= 0) — outlier handling.
      2. If no valid prices remain, return an empty metrics dict.
      3. Compute min, max, mean, median, spread from valid prices.
      4. If a shop_price is provided, compute PPI and comparison badge.

    Price Position Index (PPI):
      PPI = (shop_price / market_median) * 100
      - PPI = 100: shop price equals the market median
      - PPI = 110: shop price is 10% above the median (overpriced)
      - PPI = 90:  shop price is 10% below the median (underpriced)

    Args:
        scaled_prices: List of floats/Decimals — market prices already
                       scaled to the product's package size (RM each).
        shop_price: The shop's current selling price for comparison (RM),
                    or None if no comparison is desired.

    Returns:
        A dict with keys: n, min, max, mean, median, spread, ppi,
        comparison. All None when no valid data exists.
    """
    # Step 1: Filter invalid prices (None, zero, negative).
    # These represent data quality issues, missing records, or errors
    # in the PriceCatcher dataset.
    valid = [float(p) for p in scaled_prices
             if p is not None and float(p) > 0]

    # Step 2: No valid data -> return empty metrics.
    if not valid:
        return {
            'n': 0, 'min': None, 'max': None, 'mean': None,
            'median': None, 'spread': None, 'ppi': None,
            'comparison': None,
        }

    # Step 3: Compute basic statistics.
    lo, hi = min(valid), max(valid)
    med = _median(valid)  # median is more robust to outliers than mean
    avg = _mean(valid)

    # Step 4: Compute Price Position Index (PPI) if shop price is available.
    ppi = None
    comparison = None
    if shop_price is not None and float(shop_price) > 0 and med > 0:
        shop = float(shop_price)
        # PPI = (shop price / market median) * 100
        ppi = _r1(shop / med * 100)

        # Compute the comparison badge for the UI.
        pct = shop / med - 1  # positive = above median, negative = below
        if abs(pct) < 0.0005:
            # Within 0.05% of the median — treat as "at median".
            comparison = {'pct': 0.0, 'above': False, 'at_median': True}
        else:
            comparison = {'pct': _r1(abs(pct) * 100),
                          'above': pct > 0, 'at_median': False}

    return {
        'n': len(valid),
        'min': _r2(lo),
        'max': _r2(hi),
        'mean': _r2(avg),
        'median': _r2(med),
        'spread': _r2(hi - lo),
        'ppi': ppi,
        'comparison': comparison,
    }


def _pkg_label(market_item):
    """Generate a human-readable package label for a market item.

    Example: quantity=1.0, unit='kg' -> '1 kg'.
    Used in the UI to display what size the market item represents.
    """
    qty = float(market_item.package_quantity)
    return f"{_fmt_qty(qty)} {market_item.package_unit}".strip()# -------------------------------------------------
# GEOGRAPHIC FILTERING (Phase 4 Add-on)
#
# When the shop has a geographic location (Shop.state), the engine
# filters market observations to match that region. This ensures the
# market median and ML features are hyper-localized to the user's
# actual area — prices in Kuala Lumpur may differ significantly from
# rural Johor.
#
# FALLBACK CHAIN:
#   1. Try district-level filtering (narrowest, most relevant)
#   2. If insufficient data (< 3 observations), fall back to state-level
#   3. If no state data, fall back to national (all observations)
#
# This ensures the UI never shows empty data for shops in less-
# represented regions while still prioritizing local pricing.
# -------------------------------------------------

# Minimum number of observations needed for meaningful statistics.
# Below this threshold, we fall back to a broader geographic scope.
_MIN_OBSERVATIONS = 3


def _build_observation_query(market_item_id, state=None, district=None):
    """Build a SQLAlchemy query for market observations with geographic filters.

    This helper constructs a reusable query object that can be filtered by
    state and/or district. The query always joins through MarketItem to
    MarketSource to ensure only observations from active sources are returned.

    Args:
        market_item_id: The integer ID of the MarketItem to query observations for.
        state: Optional state string to filter observations by (e.g. 'Johor').
        district: Optional district string to filter by (e.g. 'Segamat').

    Returns:
        A SQLAlchemy query object that can be further filtered or executed.
    """
    query = (MarketPriceObservation.query
             .join(MarketItem)
             .join(MarketSource)
             .filter(
                 MarketPriceObservation.market_item_id == market_item_id,
                 MarketSource.is_active.is_(True)))

    # Apply geographic filters progressively — state narrows first,
    # then district further restricts if provided.
    if state:
        query = query.filter(MarketPriceObservation.state == state)
    if district:
        query = query.filter(MarketPriceObservation.district == district)

    return query.order_by(MarketPriceObservation.observed_at.asc())


def _fetch_localized_observations(market_item_id, shop=None):
    """Fetch price observations for a market item with geographic fallback.

    Implements the 3-tier geographic fallback chain:
      1. If shop has state+district: try district-level first
      2. If insufficient district data: fall back to state-level
      3. If no state data (or no state matches): fall back to national

    Args:
        market_item_id: The integer ID of the MarketItem.
        shop: Optional Shop ORM object with .state and .district attributes.
              If None or has no location data, returns all observations.

    Returns:
        A tuple of (observations_list, localization_string) where the
        localization string describes the geographic scope used.
    """
    # Extract geographic info from the shop object.
    shop_state = getattr(shop, 'state', None)
    shop_district = getattr(shop, 'district', None)

    # If no location data at all, return all observations (national scope).
    if not shop_state:
        obs = _build_observation_query(market_item_id).all()
        return obs, 'national'

    # TIER 1: Try district-level filtering (most localized, most relevant).
    if shop_district:
        obs = _build_observation_query(market_item_id,
                                       state=shop_state,
                                       district=shop_district).all()
        # Only use district data if we have enough observations for
        # meaningful statistics — otherwise fall back to state.
        if len(obs) >= _MIN_OBSERVATIONS:
            return obs, f'{shop_district}, {shop_state}'

    # TIER 2: Fall back to state-level filtering.
    obs = _build_observation_query(market_item_id,
                                   state=shop_state).all()
    if len(obs) >= _MIN_OBSERVATIONS:
        return obs, shop_state

    # TIER 3: Fall back to national (all observations).
    # This ensures shops in under-represented regions still get data.
    obs = _build_observation_query(market_item_id).all()
    return obs, 'national (no local data)'


# -------------------------------------------------
# DATABASE-AWARE METRICS
#
# This function queries the database to collect all relevant
# market observations and passes them to compute_metrics().
# -------------------------------------------------
def get_market_stats(product_id, shop=None):
    """Aggregate statistics for one shop product's verified market matches.

    This is the main entry point used by the API route and the product
    detail page. It:
      1. Loads the product and computes its base package quantity.
      2. Finds all VERIFIED ProductMarketMatch rows for the product.
      3. For each match, loads price observations with geographic
         filtering based on the shop's location (3-tier fallback).
      4. Scales each observation's unit price to the product's package size.
      5. Passes the scaled prices to compute_metrics() for statistics.
      6. Returns the metrics merged with product metadata and match details.

    GEOGRAPHIC LOCALIZATION:
      When the shop has a state/district set, market observations are
      filtered to match that region. This ensures the median price
      reflects local market conditions rather than a national average
      that may not be relevant.

      Fallback chain: district -> state -> national (broadest scope).

    Key behavior: NEVER raises on missing data. A product with no verified
    matches gets an all-None metrics dict with n=0, which the UI handles
    gracefully by hiding the statistics card.

    Args:
        product_id: The integer ID of the shop Product.
        shop: Optional Shop ORM object used for geographic filtering.
              If provided and has state/district set, observations are
              filtered to match the shop's region. If None, all
              observations are used (backward compatible).

    Returns:
        A dict containing:
          - Standard compute_metrics keys (n, min, max, mean, median, etc.)
          - product_id, product_name, package_label
          - scaling_note (explains whether prices are scaled or per-base-unit)
          - localization (description of geographic scope used)
          - shop_price (the product's current selling price)
          - has_package (bool: whether the product has a package size)
          - match_count (number of verified matches)
          - matches (list of per-match metadata dicts)
    """
    # Step 1: Load the product and compute its base package quantity.
    product = Product.query.get_or_404(product_id)
    base_qty = _product_base_quantity(product)

    # Step 2: Fetch all VERIFIED matches for this product.
    matches = (ProductMarketMatch.query
               .filter_by(shop_product_id=product_id, is_verified=True)
               .all())

    # Step 3: Collect scaled prices from all observations of all matches,
    # using geographic localization when the shop has location data.
    scaled = []          # Market prices scaled to the product's package size
    match_meta = []      # Per-match metadata for the UI breakdown
    primary_locale = 'national'  # Track the dominant localization scope

    for m in matches:
        # Step 4: Load observations with geographic fallback chain.
        # The localization note describes whether data is local, state,
        # or national in scope.
        obs_list, locale_note = _fetch_localized_observations(
            m.market_item_id, shop)

        # Track the dominant locale for display purposes.
        # If any match uses national fallback, note that in the summary.
        if locale_note == 'national (no local data)':
            primary_locale = 'national (no local data)'
        elif primary_locale == 'national' and locale_note != 'national':
            primary_locale = locale_note

        for obs in obs_list:
            # Step 5: Skip invalid observations (None or <= 0).
            up = obs.normalized_unit_price
            if up is None or float(up) <= 0:
                continue

            if base_qty is not None:
                # SCALE UP: Convert RM per base unit to RM per product package.
                # Example: RM 2.60/kg * 10 kg = RM 26.00 per 10 kg bag.
                scaled.append(float(up) * base_qty)
            else:
                # NO PACKAGE: Compare per base unit (no scaling possible).
                scaled.append(float(up))

        # Record per-match metadata for the UI breakdown card.
        match_meta.append({
            'market_item_id': m.market_item_id,
            'title': m.market_item.raw_title,
            'package': _pkg_label(m.market_item),
            'observations': len(obs_list),
        })

    # Step 6: Get the shop's own selling price for PPI comparison.
    shop_price = (float(product.selling_price)
                  if product.selling_price is not None else None)

    # Step 7: Compute statistics from the scaled prices.
    metrics = compute_metrics(scaled, shop_price)

    # Step 8: Build the localization description for the UI.
    # This tells the user whether their market data is local, state-level,
    # or national (and explains why if data fell back to a broader scope).
    shop_state = getattr(shop, 'state', None) if shop else None
    shop_district = getattr(shop, 'district', None) if shop else None
    if shop_state:
        if shop_district:
            localization = (f'Filtered to {shop_district}, {shop_state}. '
                           f'Fallback: {primary_locale}.')
        else:
            localization = (f'Filtered to {shop_state}. '
                           f'Fallback: {primary_locale}.')
    else:
        localization = ('No shop location set — showing national market data. '
                        'Set your shop state/district for localized pricing.')

    # Step 9: Enrich metrics with product metadata and match details.
    metrics.update({
        'product_id': product.id,
        'product_name': product.name,
        'package_label': product.size_label
                         if base_qty is not None else None,
        'scaling_note': ('Market prices scaled to your package size '
                         f'({product.size_label}).' if base_qty is not None
                         else 'Product has no package size - market prices '
                              'shown per base unit (kg/l/unit).'),
        'localization': localization,
        'shop_price': _r2(shop_price) if shop_price is not None else None,
        'has_package': base_qty is not None,
        'match_count': len(matches),
        'matches': match_meta,
    })

    return metrics
