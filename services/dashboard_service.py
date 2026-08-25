"""
============================================================
 ShelfSenseAI — Phase 4A Dashboard Service
============================================================

This module computes high-level business metrics and an "Action Required"
list for the main dashboard view. It synthesizes data from multiple
domains (products, inventory, market intelligence, pricing rules) into
a single, actionable overview for the shop owner.

PIPELINE
--------
1. Count total products and compute total inventory valuation.
2. Identify low-stock products (inventory < 10 units).
3. For each product with verified market matches, compute PPI (Price
   Position Index) and flag overpriced/underpriced items.
4. Check PCAPA compliance: flag products where margin exceeds baseline
   without a cost increase.
5. Check cost floor: flag products selling below cost * 1.05.
6. Return structured metrics + prioritized action items.

DESIGN DECISIONS
----------------
- The function is called on every dashboard load (no caching).
  With ~5-10 products per shop, this is fast enough.
- PPI checks only fire for products with verified market matches —
  products without market data are silently skipped (no false alarms).
- Action items are severity-ranked: danger (red) > warning (yellow) > info (blue).
- The function queries the database directly (no caching layer) because
  the dashboard is always fresh — stale metrics would be misleading.
============================================================
"""
from app import db, Product, Inventory, PriceHistory  # noqa: E402
from services.market_analysis import get_market_stats  # noqa: E402


# -------------------------------------------------
# CONSTANTS — Thresholds for action-item triggers
# -------------------------------------------------
LOW_STOCK_THRESHOLD = 10     # Products with stock < 10 trigger a low-stock warning.
                              # This threshold is tuned for small Malaysian retail
                              # shops where 10 units represents ~1 week of stock.
PPI_OVERPRICED = 110.0       # PPI > 110% = priced > 10% above market median.
                              # These products risk losing customers to competitors.
PPI_UNDERPRICED = 90.0       # PPI < 90% = priced > 10% below market median.
                              # These products may be leaving money on the table.


# -------------------------------------------------
# MAIN METRICS FUNCTION
# -------------------------------------------------

def get_dashboard_metrics(shop_id):
    """Compute dashboard metrics and action items for one shop.

    This function is the primary entry point called by the dashboard route.
    It aggregates data across products, inventory, market intelligence, and
    pricing rules to produce a comprehensive overview.

    Returns a dict with:
      - total_products: Total number of products in the shop.
      - low_stock_count: Number of products with stock < 10.
      - low_stock_products: List of dicts {id, name, stock} for low-stock items.
      - inventory_value: Total valuation (sum of cost_price * stock for all products).
      - action_items: List of prioritized action items requiring owner attention.
      - action_count: Total number of action items.

    Args:
        shop_id: The integer ID of the shop, or None for unassigned users.

    Returns:
        A dict of metrics. Returns empty metrics if shop_id is None.
    """
    # Guard: unassigned users have no shop — return empty metrics.
    if not shop_id:
        return _empty_metrics()

    # --- Step 1: Load all products and inventory for this shop ---
    products = Product.query.filter_by(shop_id=shop_id).all()
    inventory_map = {inv.product_id: inv
                     for inv in Inventory.query.filter_by(shop_id=shop_id).all()}

    # --- Step 2: Initialize counters and accumulators ---
    total_products = len(products)
    low_stock_count = 0
    low_stock_products = []
    inventory_value = 0.0
    action_items = []

    # --- Step 3: Iterate through each product to compute metrics ---
    for p in products:
        inv = inventory_map.get(p.id)
        stock = int(inv.current_stock) if inv else 0

        # Accumulate total inventory valuation (cost * stock for each product).
        inventory_value += float(p.cost_price) * stock

        # --- LOW STOCK CHECK ---
        # Products with stock < 10 trigger a warning. Zero stock triggers
        # a higher-severity "danger" alert (out-of-stock).
        if stock < LOW_STOCK_THRESHOLD:
            low_stock_count += 1
            low_stock_products.append({
                "id": p.id,
                "name": p.name,
                "stock": stock,
            })
            action_items.append({
                "product_id": p.id,
                "product_name": p.name,
                "action_type": "low_stock",
                "severity": "warning" if stock > 0 else "danger",
                "message": f"Low stock: {stock} units remaining"
                           if stock > 0 else "Out of stock",
            })

        # --- PPI (Price Position Index) CHECK ---
        # Only check if the product has verified market matches with valid data.
        stats = get_market_stats(p.id)
        if stats.get("n", 0) > 0 and stats.get("median") and stats.get("ppi"):
            ppi = float(stats["ppi"])
            if ppi > PPI_OVERPRICED:
                # Product is priced > 10% above market median — overpriced.
                action_items.append({
                    "product_id": p.id,
                    "product_name": p.name,
                    "action_type": "overpriced",
                    "severity": "warning",
                    "message": f"PPI {ppi:.0f}% \u2014 priced {ppi - 100:.1f}% above market median",
                })
            elif ppi < PPI_UNDERPRICED:
                # Product is priced > 10% below market median — underpriced.
                action_items.append({
                    "product_id": p.id,
                    "product_name": p.name,
                    "action_type": "underpriced",
                    "severity": "info",
                    "message": f"PPI {ppi:.0f}% \u2014 priced {100 - ppi:.1f}% below market median",
                })

        # --- PCAPA COMPLIANCE CHECK ---
        # Flag products where margin exceeds baseline without a cost increase.
        # This mirrors the PCAPA check in the pricing engine.
        if (p.baseline_margin is not None
                and p.target_margin > p.baseline_margin):
            # Look up the FIRST PriceHistory entry to get the baseline cost.
            last_hist = (PriceHistory.query
                         .filter_by(product_id=p.id)
                         .order_by(PriceHistory.created_at.asc())
                         .first())
            baseline_cost = float(last_hist.cost_price) if last_hist else float(p.cost_price)

            # If current cost hasn't risen above baseline, flag as PCAPA violation.
            if float(p.cost_price) <= baseline_cost:
                action_items.append({
                    "product_id": p.id,
                    "product_name": p.name,
                    "action_type": "pcapa_warning",
                    "severity": "danger",
                    "message": (f"PCAPA: margin {p.target_margin:.0f}% exceeds "
                                f"baseline {p.baseline_margin:.0f}% with no cost increase"),
                })

        # --- COST FLOOR CHECK ---
        # Flag products selling below cost * 1.05 (5% minimum margin).
        if p.selling_price is not None:
            floor = float(p.cost_price) * 1.05
            if float(p.selling_price) < floor:
                action_items.append({
                    "product_id": p.id,
                    "product_name": p.name,
                    "action_type": "below_cost_floor",
                    "severity": "danger",
                    "message": f"Selling price RM{float(p.selling_price):.2f} is below cost floor RM{floor:.2f}",
                })

    # --- Step 4: Return the complete metrics dict ---
    return {
        "total_products": total_products,
        "low_stock_count": low_stock_count,
        "low_stock_products": low_stock_products,
        "inventory_value": round(inventory_value, 2),
        "action_items": action_items,
        "action_count": len(action_items),
    }


def _empty_metrics():
    """Return empty metrics for users without a shop (unassigned employees).

    This ensures the dashboard template always receives a valid metrics
    dict, even when the user has no shop membership yet.
    """
    return {
        "total_products": 0,
        "low_stock_count": 0,
        "low_stock_products": [],
        "inventory_value": 0.0,
        "action_items": [],
        "action_count": 0,
    }
