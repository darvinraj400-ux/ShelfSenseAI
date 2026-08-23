"""
============================================================
 ShelfSenseAI — Phase 4A Dashboard Service
============================================================
 Computes high-level business metrics and an "Action Required"
 list for the main dashboard, powered by Phase 3 market
 intelligence and pricing rules.

 Pipeline:
   1. Count products, low stock, inventory valuation
   2. For each product with verified market matches, compute PPI
   3. Flag products with PPI outside 90-110% range
   4. Flag products triggering PCAPA warnings or cost-floor issues
   5. Flag products with low stock (< 10 units)
   6. Return structured metrics + action items
============================================================
"""
from app import db, Product, Inventory, PriceHistory  # noqa: E402
from services.market_analysis import get_market_stats  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
LOW_STOCK_THRESHOLD = 10
PPI_OVERPRICED = 110.0   # > 110% of market median = overpriced
PPI_UNDERPRICED = 90.0   # < 90% of market median = underpriced


# ---------------------------------------------------------------------------
# Main metrics function
# ---------------------------------------------------------------------------

def get_dashboard_metrics(shop_id):
    """Compute dashboard metrics for one shop.

    Returns a dict with:
      - total_products: int
      - low_stock_count: int
      - low_stock_products: list of (id, name, stock) tuples
      - inventory_value: float (total cost × stock)
      - action_items: list of dicts, each with:
          product_id, product_name, action_type, severity, message
      - action_count: int (len of action_items)
    """
    if not shop_id:
        return _empty_metrics()

    products = Product.query.filter_by(shop_id=shop_id).all()
    inventory_map = {inv.product_id: inv
                     for inv in Inventory.query.filter_by(shop_id=shop_id).all()}

    total_products = len(products)
    low_stock_count = 0
    low_stock_products = []
    inventory_value = 0.0
    action_items = []

    for p in products:
        inv = inventory_map.get(p.id)
        stock = int(inv.current_stock) if inv else 0

        # Inventory valuation: cost × stock
        inventory_value += float(p.cost_price) * stock

        # Low stock check
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

        # PPI check (only if product has verified market matches)
        stats = get_market_stats(p.id)
        if stats.get("n", 0) > 0 and stats.get("median") and stats.get("ppi"):
            ppi = float(stats["ppi"])
            if ppi > PPI_OVERPRICED:
                action_items.append({
                    "product_id": p.id,
                    "product_name": p.name,
                    "action_type": "overpriced",
                    "severity": "warning",
                    "message": f"PPI {ppi:.0f}% — priced {ppi - 100:.1f}% above market median",
                })
            elif ppi < PPI_UNDERPRICED:
                action_items.append({
                    "product_id": p.id,
                    "product_name": p.name,
                    "action_type": "underpriced",
                    "severity": "info",
                    "message": f"PPI {ppi:.0f}% — priced {100 - ppi:.1f}% below market median",
                })

        # PCAPA check: margin above baseline without cost increase
        if (p.baseline_margin is not None
                and p.target_margin > p.baseline_margin):
            last_hist = (PriceHistory.query
                         .filter_by(product_id=p.id)
                         .order_by(PriceHistory.created_at.asc())
                         .first())
            baseline_cost = float(last_hist.cost_price) if last_hist else float(p.cost_price)
            if float(p.cost_price) <= baseline_cost:
                action_items.append({
                    "product_id": p.id,
                    "product_name": p.name,
                    "action_type": "pcapa_warning",
                    "severity": "danger",
                    "message": (f"PCAPA: margin {p.target_margin:.0f}% exceeds "
                                f"baseline {p.baseline_margin:.0f}% with no cost increase"),
                })

        # Cost floor check: selling price below cost × 1.05
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

    return {
        "total_products": total_products,
        "low_stock_count": low_stock_count,
        "low_stock_products": low_stock_products,
        "inventory_value": round(inventory_value, 2),
        "action_items": action_items,
        "action_count": len(action_items),
    }


def _empty_metrics():
    """Return empty metrics for users without a shop."""
    return {
        "total_products": 0,
        "low_stock_count": 0,
        "low_stock_products": [],
        "inventory_value": 0.0,
        "action_items": [],
        "action_count": 0,
    }
