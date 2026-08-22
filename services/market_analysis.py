"""
============================================================
 ShelfSenseAI - Phase 3D Market Analysis Engine
============================================================
 Aggregates market price observations for a shop product's
 VERIFIED market matches into clean, normalized statistics.

 Pipeline:
   1. Take every observation of every VERIFIED match whose source
      is active (MarketSource.is_active).
   2. Each observation stores a normalized_unit_price = RM per BASE
      unit (kg / l / unit). Scale it back to the SHOP PRODUCT's own
      package size, e.g. a 10 kg bag of rice compares against
      "market price per 10 kg" (RM/kg x 10).
   3. Filter invalid prices (<= 0 / None) and compute:
        N        = number of valid observations ("competitors")
        min/max  = price range
        mean     = average
        median   = middle value (robust center)
        spread   = max - min
        PPI      = Price Position Index: (shop price / median) x 100
                  (100 = exactly at the market median)
   4. A `comparison` object drives the UI badge:
      "+3.2% above median" / "-5.0% below median" / "at median".

 `compute_metrics` is PURE (plain numbers in/out) and unit-testable;
 `get_market_stats` is the DB-aware layer used by the API route.
============================================================
"""
from statistics import median as _median, mean as _mean

from app import (db, Product, ProductMarketMatch,            # noqa: E402
                 MarketPriceObservation, MarketItem, MarketSource)
from utils.normalization import normalize_package_size        # noqa: E402


def _r2(v):
    return round(v, 2)


def _r1(v):
    return round(v, 1)


def _fmt_qty(v):
    """Short quantity label: 10.0 -> '10', 0.5 -> '0.5'."""
    if v == int(v):
        return str(int(v))
    return ('%g' % v).rstrip('0').rstrip('.')


def _product_base_quantity(product):
    """Normalized quantity of ONE product package (10 kg -> 10.0,
    500 g -> 0.5). None when the product has no package defined."""
    if product.quantity is None or not (product.unit or '').strip():
        return None
    q, _ = normalize_package_size(float(product.quantity), product.unit)
    return q


def compute_metrics(scaled_prices, shop_price=None):
    """Pure metric math over a list of market prices already scaled to the
    product's package size (RM each). Returns the metrics dict.

    Invalid values (None / <= 0) are filtered out BEFORE any statistic is
    computed - the outlier handling step.

    shop_price is the shop's OWN selling price for one product package
    (same unit of comparison as the scaled prices)."""
    valid = [float(p) for p in scaled_prices
             if p is not None and float(p) > 0]
    if not valid:
        return {
            'n': 0, 'min': None, 'max': None, 'mean': None,
            'median': None, 'spread': None, 'ppi': None,
            'comparison': None,
        }
    lo, hi = min(valid), max(valid)
    med = _median(valid)
    avg = _mean(valid)
    ppi = None
    comparison = None
    if shop_price is not None and float(shop_price) > 0 and med > 0:
        shop = float(shop_price)
        ppi = _r1(shop / med * 100)
        pct = shop / med - 1
        if abs(pct) < 0.0005:
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
    qty = float(market_item.package_quantity)
    return f"{_fmt_qty(qty)} {market_item.package_unit}".strip()


def get_market_stats(product_id):
    """Aggregate statistics for one shop product's verified matches.

    Returns the compute_metrics() result merged with product/shop metadata
    and a per-match breakdown. Never raises on missing data - a product
    with no verified matches gets an all-None metrics dict with n=0."""
    product = Product.query.get_or_404(product_id)
    base_qty = _product_base_quantity(product)

    matches = (ProductMarketMatch.query
               .filter_by(shop_product_id=product_id, is_verified=True)
               .all())

    scaled = []
    match_meta = []
    for m in matches:
        # Only observations from ACTIVE market sources count.
        obs_list = (MarketPriceObservation.query
                    .join(MarketItem)
                    .join(MarketSource)
                    .filter(MarketPriceObservation.market_item_id ==
                            m.market_item_id,
                            MarketSource.is_active.is_(True))
                    .order_by(MarketPriceObservation.observed_at.asc())
                    .all())
        for obs in obs_list:
            up = obs.normalized_unit_price
            if up is None or float(up) <= 0:
                continue                      # invalid observation -> drop
            if base_qty is not None:
                # Scale RM per base unit up to the product's package size,
                # e.g. RM 2.60/kg x 10 kg = RM 26.00 per 10 kg bag.
                scaled.append(float(up) * base_qty)
            else:
                # Product has no package defined: compare per base unit.
                scaled.append(float(up))
        match_meta.append({
            'market_item_id': m.market_item_id,
            'title': m.market_item.raw_title,
            'package': _pkg_label(m.market_item),
            'observations': len(obs_list),
        })

    shop_price = (float(product.selling_price)
                  if product.selling_price is not None else None)

    metrics = compute_metrics(scaled, shop_price)
    metrics.update({
        'product_id': product.id,
        'product_name': product.name,
        'package_label': product.size_label
                         if base_qty is not None else None,
        'scaling_note': ('Market prices scaled to your package size '
                         f'({product.size_label}).' if base_qty is not None
                         else 'Product has no package size - market prices '
                              'shown per base unit (kg/l/unit).'),
        'shop_price': _r2(shop_price) if shop_price is not None else None,
        'has_package': base_qty is not None,
        'match_count': len(matches),
        'matches': match_meta,
    })
    return metrics
