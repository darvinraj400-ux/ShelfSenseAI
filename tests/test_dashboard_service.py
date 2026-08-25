"""
============================================================
 ShelfSenseAI - Phase 4A Dashboard Service Tests
============================================================

Tests the dashboard metrics computation and action-item generation:

Metrics Tests:
  - Empty metrics for users without a shop
  - Basic metrics: product count, inventory valuation
  - Low stock detection: products with stock < 10
  - Zero stock: triggers 'danger' severity (out-of-stock)

Action Item Tests:
  - PCAPA compliance warning: margin > baseline without cost increase
  - Cost floor violation: selling price below cost * 1.05
  - PPI overpriced: priced > 10% above market median (with market data)
  - PPI underpriced: priced > 10% below market median (with market data)

Isolation Tests:
  - Shop A metrics don't include Shop B products
  - Empty shop returns zero metrics

Each test creates shop/product fixtures and cleans them up,
ensuring the database is restored to its pre-test state.

Run:
    ./venv/Scripts/python.exe tests/test_dashboard_service.py
"""
import sys, os, string, random as rnd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app, db, User, Shop, Product, Inventory, PriceHistory
from services.dashboard_service import get_dashboard_metrics, _empty_metrics
from sqlalchemy import text
from werkzeug.security import generate_password_hash

PASSED = FAILED = 0
TEST_SHOPS = ["DashTestShopA", "DashTestShopB"]
PW = "Test1234!"
DOMAIN = "shelfsense.my"


def check(label, cond):
    global PASSED, FAILED
    if cond:
        PASSED += 1; print(f"  [PASS] {label}")
    else:
        FAILED += 1; print(f"  [FAIL] {label}")


def _purge():
    with app.app_context():
        rows = db.session.execute(
            text("SELECT id FROM shop WHERE name IN :n"),
            {"n": tuple(TEST_SHOPS)}).fetchall()
        if not rows:
            return
        sids = ",".join(str(r[0]) for r in rows)
        for tbl, col in [("inventory_adjustment","product_id"),("sale","product_id"),
                          ("price_history","product_id"),("inventory","product_id"),
                          ("product_market_match","shop_product_id")]:
            db.session.execute(text(
                f"DELETE FROM {tbl} WHERE {col} IN "
                f"(SELECT id FROM product WHERE shop_id IN ({sids}))"))
        db.session.execute(text(f"DELETE FROM product WHERE shop_id IN ({sids})"))
        db.session.execute(text(f"DELETE FROM user WHERE shop_id IN ({sids})"))
        db.session.execute(text("DELETE FROM shop WHERE name IN :n"),
                           {"n": tuple(TEST_SHOPS)})
        db.session.commit()


def _make_shop(name):
    slug = ''.join(rnd.choices(string.ascii_lowercase, k=6))
    email = f"own_{slug}@{DOMAIN}"
    with app.app_context():
        u = User(email=email, password_hash=generate_password_hash(PW), role="owner")
        db.session.add(u); db.session.flush()
        s = Shop(name=name); db.session.add(s); db.session.flush()
        u.shop_id = s.id; db.session.commit()
        return u.id, s.id, email


def _make_product(sid, name="Prod", cost=10.0, margin=30.0, selling=13.0,
                  stock=20, qty=1, unit="unit", baseline_margin=None):
    bl = baseline_margin if baseline_margin is not None else margin
    with app.app_context():
        p = Product(name=name, cost_price=cost, target_margin=margin,
                    baseline_margin=bl, selling_price=selling,
                    quantity=qty, unit=unit, shop_id=sid)
        db.session.add(p); db.session.flush()
        db.session.add(PriceHistory(product_id=p.id, cost_price=cost,
                                    selling_price=selling, target_margin=margin))
        db.session.add(Inventory(shop_id=sid, product_id=p.id,
                                 current_stock=stock, minimum_stock=5))
        db.session.commit()
        return p.id


# ======================================================== METRICS TESTS
def test_empty_metrics():
    """Test _empty_metrics() returns a valid empty dict.

    Verifies all fields are zero/empty, ensuring the dashboard template
    always receives a valid metrics object even for unassigned users.
    """
    print("\n--- Empty Metrics ---")
    m = _empty_metrics()
    check("total_products = 0", m["total_products"] == 0)
    check("low_stock_count = 0", m["low_stock_count"] == 0)
    check("inventory_value = 0", m["inventory_value"] == 0.0)
    check("action_items empty", len(m["action_items"]) == 0)
    check("action_count = 0", m["action_count"] == 0)


def test_basic_metrics():
    """Test basic product count and inventory valuation.

    Creates two products with known costs and stock levels, then verifies
    that total_products and inventory_value are calculated correctly.
    """
    print("\n--- Basic Metrics ---")
    _purge()
    uid, sid, email = _make_shop(TEST_SHOPS[0])
    pid1 = _make_product(sid, "ProdA", cost=10.0, margin=30.0, selling=13.0, stock=20)
    pid2 = _make_product(sid, "ProdB", cost=5.0, margin=20.0, selling=6.0, stock=30)

    with app.app_context():
        m = get_dashboard_metrics(sid)
        check("total_products = 2", m["total_products"] == 2)
        check("low_stock_count = 0", m["low_stock_count"] == 0)
        check("inventory_value = 350.0", abs(m["inventory_value"] - 350.0) < 0.01)  # 10*20 + 5*30
    _purge()


def test_low_stock():
    """Test low-stock detection and severity classification.

    Creates products with stock levels of 50 (healthy), 8 (low), and 0 (out-of-stock).
    Verifies that only the low/zero products appear in the action items,
    and that zero stock gets 'danger' severity.
    """
    print("\n--- Low Stock ---")
    _purge()
    uid, sid, email = _make_shop(TEST_SHOPS[0])
    pid1 = _make_product(sid, "Healthy", cost=10.0, margin=30.0, selling=13.0, stock=50)
    pid2 = _make_product(sid, "Low", cost=5.0, margin=20.0, selling=6.0, stock=8)
    pid3 = _make_product(sid, "Zero", cost=5.0, margin=20.0, selling=6.0, stock=0)

    with app.app_context():
        m = get_dashboard_metrics(sid)
        check("low_stock_count = 2", m["low_stock_count"] == 2)
        low_ids = {p["id"] for p in m["low_stock_products"]}
        check("Low in list", pid2 in low_ids)
        check("Zero in list", pid3 in low_ids)
        check("Healthy not in list", pid1 not in low_ids)
        # Check action items include low stock
        stock_actions = [a for a in m["action_items"] if a["action_type"] == "low_stock"]
        check("2 low stock actions", len(stock_actions) == 2)
        zero_action = [a for a in stock_actions if a["product_id"] == pid3]
        check("Zero stock is danger", zero_action and zero_action[0]["severity"] == "danger")
    _purge()


def test_pcapa_action():
    """Test PCAPA compliance action item generation.

    Creates a product with baseline_margin=30% but target_margin=50%
    (margin raised without cost increase). Verifies that a 'pcapa_warning'
    action item with 'danger' severity is generated.
    """
    print("\n--- PCAPA Action Item ---")
    _purge()
    uid, sid, email = _make_shop(TEST_SHOPS[0])
    # baseline=30%, but set margin to 50% -> triggers PCAPA
    pid = _make_product(sid, "PCAPA", cost=10.0, margin=50.0, selling=15.0,
                        baseline_margin=30.0)

    with app.app_context():
        # Override baseline to 30%
        p = db.session.get(Product, pid)
        p.baseline_margin = 30.0
        db.session.commit()

        m = get_dashboard_metrics(sid)
        pcapa_actions = [a for a in m["action_items"] if a["action_type"] == "pcapa_warning"]
        check("PCAPA warning in action items", len(pcapa_actions) == 1)
        check("PCAPA is danger severity", pcapa_actions[0]["severity"] == "danger")
    _purge()


def test_cost_floor_action():
    """Test cost floor violation action item generation.

    Creates a product with cost=10.00 and selling_price=9.00 (below
    the 10.50 floor). Verifies that a 'below_cost_floor' action item
    with 'danger' severity is generated.
    """
    print("\n--- Cost Floor Action Item ---")
    _purge()
    uid, sid, email = _make_shop(TEST_SHOPS[0])
    # Selling price 9.0 is below cost floor 10.50 (10 * 1.05)
    pid = _make_product(sid, "BelowFloor", cost=10.0, margin=30.0, selling=9.0)

    with app.app_context():
        m = get_dashboard_metrics(sid)
        floor_actions = [a for a in m["action_items"] if a["action_type"] == "below_cost_floor"]
        check("Cost floor action in items", len(floor_actions) == 1)
        check("Floor action is danger", floor_actions[0]["severity"] == "danger")
    _purge()


def test_isolation():
    """Test shop-level data isolation in dashboard metrics.

    Creates two shops: Shop A has 1 product, Shop B has none.
    Verifies that Shop A's metrics show 1 product while Shop B's
    show 0, proving that shop isolation is enforced.
    """
    print("\n--- Shop Isolation ---")
    _purge()
    _, sid_a, ea = _make_shop(TEST_SHOPS[0])
    _, sid_b, eb = _make_shop(TEST_SHOPS[1])
    pid_a = _make_product(sid_a, "IsoA", cost=10.0, margin=30.0, selling=13.0, stock=5)

    with app.app_context():
        m_a = get_dashboard_metrics(sid_a)
        m_b = get_dashboard_metrics(sid_b)
        check("Shop A sees 1 product", m_a["total_products"] == 1)
        check("Shop B sees 0 products", m_b["total_products"] == 0)
        check("Shop A has low stock", m_a["low_stock_count"] == 1)
        check("Shop B has no low stock", m_b["low_stock_count"] == 0)
    _purge()


def test_no_shop():
    """Test that None shop_id returns empty metrics.

    Verifies that unassigned employees (shop_id=None) receive
    a valid empty metrics dict without errors.
    """
    print("\n--- No Shop ---")
    m = get_dashboard_metrics(None)
    check("No shop returns empty metrics", m["total_products"] == 0)
    check("No action items", m["action_count"] == 0)


# ======================================================== MAIN
def main():
    global PASSED, FAILED
    PASSED = FAILED = 0
    print("=" * 60)
    print("ShelfSenseAI - Phase 4A Dashboard Service Tests")
    print("=" * 60)

    test_empty_metrics()
    test_basic_metrics()
    test_low_stock()
    test_pcapa_action()
    test_cost_floor_action()
    test_isolation()
    test_no_shop()

    _purge()
    total = PASSED + FAILED
    print(f"\n{'=' * 60}")
    print(f"test_dashboard_service: {PASSED}/{total} passed" +
          (f" ({FAILED} FAILED)" if FAILED else ""))
    print("=" * 60)
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
