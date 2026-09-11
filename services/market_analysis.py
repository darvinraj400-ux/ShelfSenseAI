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
from datetime import datetime, timedelta

from sqlalchemy import func, text                               # noqa: E402

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


def _infer_base_qty_from_market(matches):
    """Infer a fallback base quantity from matched MarketItem packages.

    When a shop product has no unit defined (user left it blank),
    the system cannot compute base_qty and falls back to showing
    per-base-unit prices. This causes the ML model to hallucinate
    absurd prices (e.g. RM 28 for toothpaste) because it compares
    the product's cost_price against a per-kg market price that is
    4x higher than the per-package price.

    This function looks at the first matched MarketItem's package
    info and returns its normalized base quantity as a reference.
    This ensures the market median is scaled to the SAME package
    size that the market data was recorded at.

    Example: Product 'UBAT GIGI COLGATE' has no unit set.
      MarketItem is 0.25 kg (1 tube).
      normalized_unit_price = RM 55.28/kg.
      base_qty = 0.25 → scaled price = RM 55.28 * 0.25 = RM 13.82.
      ML now correctly compares RM 12 cost vs RM 13.82 market.

    Args:
        matches: List of ProductMarketMatch ORM objects.

    Returns:
        A float base quantity from the first match's MarketItem,
        or None if no matches have valid package data.
    """
    if not matches:
        return None

    # Import here to avoid circular imports at module level.
    from app import MarketItem

    for m in matches:
        mi = MarketItem.query.get(m.market_item_id)
        if mi is not None and mi.package_quantity and float(mi.package_quantity) > 0:
            q, _ = normalize_package_size(
                float(mi.package_quantity), mi.package_unit)
            if q > 0:
                return q
    return None


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


# -------------------------------------------------
# MARKET POSITION (Phase 6D)
#
# A retailer's price is classified relative to the market median
# using a configurable tolerance band. Prices within the band are
# 'Near Market'; anything outside is 'Above Market' / 'Below Market'.
# The threshold is a named constant (documented + tested), NOT a
# magic number buried in display logic.
# -------------------------------------------------

# Tolerance band around the market median, as a fraction (0.05 = 5%).
# Rationale: everyday-goods prices commonly fluctuate a few percent
# between stores; within 5% of the median the shop is competitive and
# no pricing action is suggested. Documented in DEVELOPMENT_JOURNEY.md
# and asserted by tests/test_market_intelligence.py.
NEAR_MARKET_TOLERANCE = 0.05


def market_position(shop_price, market_median,
                    tolerance=NEAR_MARKET_TOLERANCE):
    """Classify the retailer's price relative to the market median.

    Pure function (no DB access) so it is unit-testable in isolation.

    Categories:
      'Below Market' — more than `tolerance` UNDER the median.
      'Near Market'  — within +/- `tolerance` of the median.
      'Above Market' — more than `tolerance` OVER the median.

    Args:
        shop_price: The retailer's current selling price (RM), or None.
        market_median: The market median price (RM), or None.
        tolerance: Fractional band around the median (default 5%,
                   see NEAR_MARKET_TOLERANCE).

    Returns:
        A dict with:
          position: 'Below Market' | 'Near Market' | 'Above Market' | None
          difference: shop_price - median (RM, rounded 2dp) or None
          difference_percent: signed % vs median (rounded 2dp) or None
        Returns position=None (with None differences) when either input
        is missing or the median is not positive — the UI must show an
        empty state rather than a fake zero.
    """
    if (shop_price is None or market_median is None
            or float(market_median) <= 0 or float(shop_price) <= 0):
        return {'position': None, 'difference': None,
                'difference_percent': None}

    shop = float(shop_price)
    med = float(market_median)
    diff = shop - med
    pct = diff / med * 100

    if abs(pct) <= tolerance * 100:
        position = 'Near Market'
    elif pct < 0:
        position = 'Below Market'
    else:
        position = 'Above Market'

    return {
        'position': position,
        'difference': _r2(diff),
        'difference_percent': _r2(pct),
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
# -------------------------------------------------
# HISTORICAL MARKET TREND (Phase 6F)
#
# Per-date market statistics computed SERVER-SIDE. MariaDB 10.4
# supports PERCENTILE_CONT as a window function, so the median for
# every observation date is calculated inside MySQL — we never pull
# millions of observation rows into Python just to compute a median.
# One indexed range scan per matched market item returns at most one
# row per day (bounded by the lookback window).
# -------------------------------------------------

# Default lookback window for the trend chart (days).
TREND_LOOKBACK_DAYS = 90


def get_market_trend(market_item_ids, shop=None,
                     lookback_days=TREND_LOOKBACK_DAYS):
    """Per-date market statistics for one or more market items.

    All aggregation (COUNT, MIN, MAX, AVG, median via PERCENTILE_CONT)
    happens in the database; Python only formats the returned rows.

    Args:
        market_item_ids: List of MarketItem ids (the product's verified
            matches). An empty list returns [] immediately.
        shop: Optional Shop ORM object. When it has state/district the
            trend uses the SAME 3-tier geographic fallback as the
            snapshot stats (district -> state -> national), so the
            chart and the summary cards always describe the same market.
        lookback_days: Only observations within the last N days are
            included (indexed date range scan; no full-table work).

    Returns:
        A dict:
          tier: 'district' | 'state' | 'national' | None
          tier_label: human-readable scope (e.g. 'Segamat, Johor')
          points: list of {date, median, min, max, mean, observations,
                           premises} — one row per date that actually has
           observations (missing dates are NOT invented), oldest first.
    """
    if not market_item_ids:
        return {'tier': None, 'tier_label': None, 'points': []}

    # Dedupe: verified matches can repeat the same market item; a raw
    # IN (...) list of thousands of ids would bloat the query.
    ids = sorted({int(i) for i in market_item_ids})

    shop_state = getattr(shop, 'state', None)
    shop_district = getattr(shop, 'district', None)

    # Resolve the geographic tier with the same fallback chain as the
    # snapshot: district -> state -> national (_MIN_OBSERVATIONS applies).
    def _count(state, district):
        q = (db.session.query(func.count(MarketPriceObservation.id))
             .join(MarketItem).join(MarketSource)
             .filter(MarketPriceObservation.market_item_id.in_(ids),
                     MarketSource.is_active.is_(True)))
        if state:
            q = q.filter(MarketPriceObservation.state == state)
        if district:
            q = q.filter(MarketPriceObservation.district == district)
        return q.scalar() or 0

    tier, tier_label = 'national', 'National'
    filters = []
    if shop_state and shop_district:
        n = _count(shop_state, shop_district)
        if n >= _MIN_OBSERVATIONS:
            tier, tier_label = 'district', f'{shop_district}, {shop_state}'
            filters = [shop_state, shop_district]
    if not filters and shop_state:
        n = _count(shop_state, None)
        if n >= _MIN_OBSERVATIONS:
            tier, tier_label = 'state', shop_state
            filters = [shop_state]
    if not filters and shop_state:
        # State/district had insufficient data -> national, labelled
        # honestly so the UI never implies local data is being shown.
        tier_label = 'National (no sufficient local data)'

    # Indexed range scan: item ids + optional geo filters + date window.
    # MariaDB 10.4 supports PERCENTILE_CONT only as a WINDOW function, so
    # the median is computed in an inner query (window partitioned by
    # date over the raw rows) and the outer GROUP BY takes MAX(med).
    # Everything else (COUNT/MIN/MAX/AVG) is plain server-side grouping.
    placeholders = ','.join(f':i{k}' for k in range(len(ids)))
    params = {f'i{k}': v for k, v in enumerate(ids)}
    params['since'] = datetime.utcnow().date() - timedelta(days=lookback_days)
    geo_sql = ''
    if len(filters) == 2:
        geo_sql = ' AND o.state = :st AND o.district = :di'
        params['st'], params['di'] = filters
    elif len(filters) == 1:
        geo_sql = ' AND o.state = :st'
        params['st'] = filters[0]

    sql = text(f'''
        SELECT observed_at AS d,
               COUNT(*) AS n,
               COUNT(DISTINCT premise_code) AS premises,
               MIN(regular_price) AS lo,
               MAX(regular_price) AS hi,
               AVG(regular_price) AS avg,
               MAX(med) AS median
        FROM (
            SELECT o.observed_at, o.regular_price, o.premise_code,
                   PERCENTILE_CONT(0.5) WITHIN GROUP
                     (ORDER BY o.regular_price)
                     OVER (PARTITION BY o.observed_at) AS med
            FROM market_price_observation o
            JOIN market_item mi ON mi.id = o.market_item_id
            JOIN market_source ms ON ms.id = mi.source_id
            WHERE o.market_item_id IN ({placeholders})
              AND ms.is_active = 1
              AND o.observed_at >= :since
              {geo_sql}
        ) raw
        GROUP BY observed_at
        ORDER BY observed_at ASC
    ''')
    rows = db.session.execute(sql, params).fetchall()

    points = [{
        'date': r.d.date().isoformat() if hasattr(r.d, 'date') else str(r.d),
        'median': _r2(float(r.median)) if r.median is not None else None,
        'min': _r2(float(r.lo)) if r.lo is not None else None,
        'max': _r2(float(r.hi)) if r.hi is not None else None,
        'mean': _r2(float(r.avg)) if r.avg is not None else None,
        'observations': int(r.n),
        'premises': int(r.premises),
    } for r in rows]

    return {'tier': tier, 'tier_label': tier_label, 'points': points}


def _competitor_snapshot(market_item_ids, latest_date, shop=None):
    """Latest-snapshot store-level prices for the competitor table.

    Returns one row per (market item, premise) on the LATEST observation
    date only, so the 'current market' table never mixes a months-old
    competitor price with a fresh one. Premise names are resolved from
    the normalized lookup_premise archive in a single batched query
    (no N+1). Prices are the exact reported prices — never averaged.

    Args:
        market_item_ids: List of MarketItem ids.
        latest_date: The latest observation date (datetime/date) or None.
        shop: Optional Shop for location labelling only.

    Returns:
        List of dicts: {premise_code, premise, item, market_item_id,
        price, unit_price, date, location} sorted by price ascending.
    """
    if not market_item_ids or latest_date is None:
        return []
    ids = sorted({int(i) for i in market_item_ids})

    rows = (db.session.query(
                MarketPriceObservation, MarketItem.raw_title)
            .join(MarketItem, MarketPriceObservation.market_item_id == MarketItem.id)
            .join(MarketSource, MarketItem.source_id == MarketSource.id)
            .filter(MarketPriceObservation.market_item_id.in_(ids),
                    MarketSource.is_active.is_(True),
                    MarketPriceObservation.observed_at == latest_date)
            .order_by(MarketPriceObservation.regular_price.asc()).all())

    codes = sorted({o.premise_code for o, _ in rows if o.premise_code})
    names = {}
    if codes:
        placeholders = ','.join(f':pc{i}' for i in range(len(codes)))
        params = {f'pc{i}': c for i, c in enumerate(codes)}
        for r in db.session.execute(text(
                f'SELECT premise_code, premise FROM lookup_premise '
                f'WHERE premise_code IN ({placeholders})'), params):
            names[r[0]] = r[1]

    snapshot = []
    for o, title in rows:
        snapshot.append({
            'premise_code': o.premise_code,
            'premise': names.get(o.premise_code) or o.premise_code,
            'item': title,
            'market_item_id': o.market_item_id,
            'price': _r2(float(o.regular_price)) if o.regular_price is not None else None,
            'unit_price': _r2(float(o.normalized_unit_price))
                          if o.normalized_unit_price is not None else None,
            'date': o.observed_at.date().isoformat() if o.observed_at else None,
            'location': ', '.join(filter(None, [o.district, o.state])) or 'National',
        })
    return snapshot


def get_market_stats(product_id, shop=None, page=1, per_page=15):
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

    # Extract shop location early — used in Step 5 for tier classification.
    shop_state = getattr(shop, 'state', None) if shop else None
    shop_district = getattr(shop, 'district', None) if shop else None

    # Step 3: Collect scaled prices from all observations of all matches,
    # using geographic localization when the shop has location data.
    # Also collect raw observation metadata for the transparency table
    # (Explainable AI: shows the user the actual data points driving stats).
    scaled = []          # Market prices scaled to the product's package size
    match_meta = []      # Per-match metadata for the UI breakdown
    raw_observations = [] # Raw observation dicts for the transparency table
    premise_codes = set()  # Distinct reporting stores (premise-level sources)
    primary_locale = 'national'  # Track the dominant localization scope
    _loaded_obs = []     # Observation lists per match (for date range)

    for m in matches:
        # Step 4: Load observations with geographic fallback chain.
        # The localization note describes whether data is local, state,
        # or national in scope.
        obs_list, locale_note = _fetch_localized_observations(
            m.market_item_id, shop)
        _loaded_obs.append(obs_list)

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

            # Determine the effective base quantity for scaling.
            # Priority: product's own unit > inferred from market item > 1.0.
            effective_qty = base_qty
            if effective_qty is None:
                # Product has no unit — try to infer from the matched MarketItem.
                # This prevents the ML model from comparing per-kg prices
                # against per-package costs (the 'RM 56 toothpaste' bug).
                effective_qty = _infer_base_qty_from_market([m])
            if effective_qty is None:
                # Fallback: assume product is 1 base unit (1 kg / 1 L).
                effective_qty = 1.0

            # SCALE UP: Convert RM per base unit to RM per product package.
            # Example: RM 55.28/kg * 0.25 kg = RM 13.82 per tube.
            scaled.append(float(up) * effective_qty)

            # Track distinct reporting premises (Phase 6 premise-level
            # observations) so the stats can report how many stores the
            # market data actually covers.
            obs_premise = getattr(obs, 'premise_code', None)
            if obs_premise:
                premise_codes.add(obs_premise)

            # Collect raw observation for the transparency table.
            # This gives users visibility into the actual KPDN data points
            # that feed the market summary statistics.
            # Geographic tier: classify each observation by relevance to
            # the shop's location (District > State > National).
            obs_district = getattr(obs, 'district', None)
            obs_state = getattr(obs, 'state', None)
            if shop_district and obs_district == shop_district:
                tier = 'district'
            elif shop_state and obs_state == shop_state:
                tier = 'state'
            else:
                tier = 'national'
            raw_observations.append({
                'date': obs.observed_at.strftime('%d %b %Y')
                        if obs.observed_at else '—',
                'item': m.market_item.raw_title,
                'package': _pkg_label(m.market_item),
                'premise': obs_premise,   # code; mapped to a name below
                'price': round(float(obs.regular_price), 2),
                'location': ', '.join(filter(None, [
                    obs_district, obs_state])) or 'National',
                'tier': tier,
            })

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

    # Step 6.5: Resolve premise names for the transparency table from the
    # raw lookup_premise archive (premise metadata is NOT duplicated on
    # observations — it stays normalized in the archive table).
    _premise_names = {}
    if premise_codes:
        codes = sorted(premise_codes)
        placeholders = ','.join(f':pc{i}' for i in range(len(codes)))
        params = {f'pc{i}': c for i, c in enumerate(codes)}
        rows = db.session.execute(text(
            f'SELECT premise_code, premise FROM lookup_premise '
            f'WHERE premise_code IN ({placeholders})'), params).fetchall()
        _premise_names = {r[0]: r[1] for r in rows}
    # Swap codes for human-readable store names in the transparency table.
    for entry in raw_observations:
        code = entry.get('premise')
        entry['premise'] = _premise_names.get(code) if code else None

    # Step 7: Compute statistics from the scaled prices.
    metrics = compute_metrics(scaled, shop_price)
    # Phase 6: distinct reporting stores behind the observations.
    metrics['premise_count'] = len(premise_codes)

    # Phase 6A/6B: structured market tier + honest human-readable label.
    # The UI shows WHICH market the stats describe (district / state /
    # national) so national fallback is never passed off as local data.
    # _fetch_localized_observations returns a locale NOTE string; map it
    # back to the structured tier name it represents.
    if primary_locale == 'national (no local data)':
        tier_label = 'National (no sufficient local data)'
        tier_name = 'national'
    elif primary_locale == 'national':
        tier_label = 'National'
        tier_name = 'national'
    elif shop_district and primary_locale == f'{shop_district}, {shop_state}':
        tier_label = primary_locale
        tier_name = 'district'
    elif shop_state and primary_locale == shop_state:
        tier_label = primary_locale
        tier_name = 'state'
    else:
        tier_label = primary_locale or 'National'
        tier_name = 'national'
    metrics['market_tier'] = tier_name
    metrics['market_tier_label'] = tier_label

    # Phase 6A: latest / earliest observation dates across the matched
    # items (from the already-loaded observation objects — no extra query).
    _dates = [o.observed_at for obs_list in _loaded_obs
              for o in obs_list if o.observed_at is not None]
    metrics['latest_observed_at'] = (max(_dates).date().isoformat()
                                     if _dates else None)
    metrics['earliest_observed_at'] = (min(_dates).date().isoformat()
                                       if _dates else None)

    # Phase 6D: retailer position vs the market median (pure function).
    position = market_position(shop_price, metrics.get('median'))
    metrics.update(position)

    # Phase 6E: price distribution quartiles (for the range visualization).
    if scaled:
        valid = sorted(float(p) for p in scaled if p is not None and p > 0)
        if valid:
            n = len(valid)
            q1 = valid[n // 4] if n >= 4 else valid[0]
            q3 = valid[(3 * n) // 4] if n >= 4 else valid[-1]
            metrics['distribution'] = {
                'q1': _r2(q1), 'median': metrics['median'],
                'q3': _r2(q3),
            }

    # Phase 6C: store-level competitor snapshot from the LATEST market
    # snapshot date only (no mixing of stale prices with fresh ones).
    match_item_ids = [m.market_item_id for m in matches]
    metrics['competitor_snapshot'] = _competitor_snapshot(
        match_item_ids, (max(_dates) if _dates else None), shop)
    metrics['snapshot_date'] = metrics['latest_observed_at']

    # Step 8: Build the localization description for the UI.
    # This tells the user whether their market data is local, state-level,
    # or national (and explains why if data fell back to a broader scope).
    # shop_state and shop_district were extracted in Step 2.
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

    # Step 9: Enrich metrics with product metadata, match details, and
    # recent observations for the Explainable AI transparency table.
    # Observations are sorted by geographic tier (district > state > national)
    # then by date (newest first), and paginated for the UI.
    _tier_order = {'district': 0, 'state': 1, 'national': 2}
    raw_observations.sort(
        key=lambda o: (_tier_order.get(o.get('tier', 'national'), 2),
                       o.get('date', '')),
        reverse=False  # district first (0), then state (1), then national (2)
    )
    # Within each tier, sort by date descending (newest first).
    # Re-sort: first by tier (ascending), then by date (descending).
    raw_observations.sort(
        key=lambda o: (_tier_order.get(o.get('tier', 'national'), 2),
                       ''),
        reverse=False
    )
    # Stable sort by date descending within each tier group.
    from functools import cmp_to_key
    def _obs_cmp(a, b):
        ta = _tier_order.get(a.get('tier', 'national'), 2)
        tb = _tier_order.get(b.get('tier', 'national'), 2)
        if ta != tb:
            return ta - tb  # district(0) before state(1) before national(2)
        # Same tier: newer dates first
        return (b.get('date', '') > a.get('date', '')) - (b.get('date', '') < a.get('date', ''))
    raw_observations.sort(key=cmp_to_key(_obs_cmp))

    # Pagination: slice the sorted observations for the current page.
    total_observations = len(raw_observations)
    start = (page - 1) * per_page
    end = start + per_page
    paginated_observations = raw_observations[start:end]

    metrics.update({
        'product_id': product.id,
        'product_name': product.name,
        'package_label': product.size_label
                         if base_qty is not None else None,
        'scaling_note': ('Market prices scaled to your package size '
                         f'({product.size_label}).')
                         if base_qty is not None
                         else 'Market prices scaled to matched package size'
                              ' (set product unit for precise scaling).',
        'localization': localization,
        'shop_price': _r2(shop_price) if shop_price is not None else None,
        'has_package': base_qty is not None,
        'match_count': len(matches),
        'matches': match_meta,
        'recent_observations': paginated_observations,
        'observations_total': total_observations,
        'observations_page': page,
        'observations_per_page': per_page,
        'observations_pages': max(1, (total_observations + per_page - 1) // per_page),
    })

    return metrics
