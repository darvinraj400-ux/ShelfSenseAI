"""
============================================================
 ShelfSenseAI - Phase 3E Pricing Engine Tests
============================================================

Comprehensive test suite for the intelligent pricing engine, covering:

Part 1 — Pure function tests (no database required):
  - Cost floor guardrail (Rule 1): price >= cost * 1.05
  - Market sanity guardrail (Rule 2): clamp within market bounds
  - PCAPA compliance check (Rule 3): flag margin above baseline
  - Confidence scoring: data availability -> confidence level

Part 2 — Database + API integration tests:
  - Full recommendation pipeline (get_price_recommendation)
  - Pricing API endpoint (GET /api/product/<pid>/pricing)
  - Apply price endpoint (POST /api/product/<pid>/apply-price)
  - Role-based access control (owner/manager/staff permissions)
  - Shop isolation (cross-shop access blocked)
  - Cost floor enforcement in real recommendations
  - No-market-data fallback behavior

Each test creates its own shop/product fixtures and cleans them up
in the finally block, ensuring the database is restored to its
pre-test state.

Run:
    ./venv/Scripts/python.exe tests/test_pricing_engine.py
"""
import sys, os, re, string, random as rnd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app, db, User, Shop, Product, Inventory, PriceHistory
from services.pricing_engine import (_apply_cost_floor, _apply_market_sanity,
                                     _check_pcapa, _compute_confidence,
                                     get_price_recommendation)
from sqlalchemy import text
from werkzeug.security import generate_password_hash

PASSED = FAILED = 0
TEST_SHOPS = ["PricingTestShopA", "PricingTestShopB"]
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
                  qty=1, unit="unit"):
    with app.app_context():
        p = Product(name=name, cost_price=cost, target_margin=margin,
                    baseline_margin=margin, selling_price=selling,
                    quantity=qty, unit=unit, shop_id=sid)
        db.session.add(p); db.session.flush()
        db.session.add(PriceHistory(product_id=p.id, cost_price=cost,
                                    selling_price=selling, target_margin=margin))
        db.session.add(Inventory(shop_id=sid, product_id=p.id,
                                 current_stock=20, minimum_stock=5))
        db.session.commit()
        return p.id


def _make_user(sid, role, slug):
    email = f"{role}_{slug}@{DOMAIN}"
    with app.app_context():
        u = User(email=email, password_hash=generate_password_hash(PW),
                 role=role, shop_id=sid)
        db.session.add(u); db.session.commit()
        return u.id, email


def _csrf_of(client, path):
    r = client.get(path)
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', r.data.decode())
    return m.group(1) if m else ""


def _logout(client):
    """Clear the session so the next _login starts fresh."""
    client.get("/logout", follow_redirects=True)

def _login(client, email):
    csrf = _csrf_of(client, "/login")
    r = client.post("/login", data={"email": email, "password": PW,
                                    "csrf_token": csrf}, follow_redirects=True)
    return csrf  # return token for subsequent POST requests


# =============================== PURE FUNCTION TESTS
def test_cost_floor():
    """Test Rule 1: Cost floor guardrail.

    Verifies that:
    - Prices below cost * 1.05 are raised to the floor.
    - Prices at or above the floor are unchanged.
    - The floor is correctly computed for different cost values.
    """
    print("\n--- Cost Floor ---")
    p, h = _apply_cost_floor(8.0, 10.0)
    check("Below floor raised to 10.50", h and p == 10.50)
    p, h = _apply_cost_floor(10.50, 10.0)
    check("At floor unchanged", not h and p == 10.50)
    p, h = _apply_cost_floor(15.0, 10.0)
    check("Above floor unchanged", not h and p == 15.0)
    p, h = _apply_cost_floor(5.0, 100.0)
    check("High cost floor 105.00", h and p == 105.0)


def test_market_sanity():
    """Test Rule 2: Market sanity guardrail.

    Verifies that:
    - Prices below 70% of market min are clamped up.
    - Prices above 150% of market max are clamped down.
    - Prices within the range are unchanged.
    - None/0 market data passes through unclamped.
    """
    print("\n--- Market Sanity ---")
    p, h = _apply_market_sanity(5.0, 10.0, 20.0)
    check("Below min clamped to 7.00", h and p == 7.0)
    p, h = _apply_market_sanity(40.0, 10.0, 20.0)
    check("Above max clamped to 30.00", h and p == 30.0)
    p, h = _apply_market_sanity(15.0, 10.0, 20.0)
    check("Within range unchanged", not h and p == 15.0)
    p, h = _apply_market_sanity(15.0, None, None)
    check("No market data (None) passes through", not h and p == 15.0)
    p, h = _apply_market_sanity(15.0, 0.0, 0.0)
    check("No market data (0.0) passes through", not h and p == 15.0)


def test_pcapa():
    """Test Rule 3: PCAPA compliance check.

    Verifies that:
    - Margin above baseline WITHOUT cost increase triggers warning.
    - Margin at or below baseline is compliant.
    - Cost rise that justifies higher margin is compliant.
    """
    print("\n--- PCAPA ---")
    _purge()
    uid, sid, email = _make_shop(TEST_SHOPS[0])
    pid = _make_product(sid, "PCAPA", cost=10.0, margin=30.0, selling=13.0)

    with app.app_context():
        p = db.session.get(Product, pid)
        w, ok = _check_pcapa(p, 13.5)  # 35% > 30%
        check("Margin above baseline -> warning", not ok and w is not None)
        w, ok = _check_pcapa(p, 13.0)  # 30% = 30%
        check("Margin at baseline -> ok", ok)
        w, ok = _check_pcapa(p, 12.5)  # 25% < 30%
        check("Margin below baseline -> ok", ok)

        p.cost_price = 12.0
        db.session.add(PriceHistory(product_id=pid, cost_price=12.0,
                                    selling_price=13.0, target_margin=30.0))
        db.session.commit()
        p = db.session.get(Product, pid)
        w, ok = _check_pcapa(p, 16.2)  # 35% on 12.0, but baseline_cost=10.0, cost rose
        check("Cost rise justifies higher margin -> ok", ok)
    _purge()


def test_confidence():
    """Test confidence level computation.

    Verifies the confidence classification:
    - No market data -> 'low'
    - No ML model -> 'medium'
    - Full data, no guardrails -> 'high'
    - Guardrails triggered -> 'medium'
    """
    print("\n--- Confidence ---")
    c, _ = _compute_confidence(False, True, False)
    check("No market data = low", c == "low")
    c, _ = _compute_confidence(True, False, False)
    check("No model = medium", c == "medium")
    c, _ = _compute_confidence(True, True, False)
    check("All data, no guardrails = high", c == "high")
    c, _ = _compute_confidence(True, True, True)
    check("Guardrails triggered = medium", c == "medium")


# =============================== DB + API TESTS
def test_recommendation_basic():
    """Test the full recommendation pipeline with a real product.

    Verifies that get_price_recommendation() returns a complete payload
    with all required fields, and that the recommended price is above
    the cost floor.
    """
    print("\n--- Basic Recommendation ---")
    _purge()
    uid, sid, email = _make_shop(TEST_SHOPS[0])
    pid = _make_product(sid, "Basic", cost=10.0, margin=30.0, selling=13.0, qty=10, unit="kg")
    with app.app_context():
        rec = get_price_recommendation(pid)
        check("has recommended_price", rec["recommended_price"] is not None)
        check("has confidence", rec["confidence"] in ("high", "medium", "low"))
        check("has reasoning", len(rec["reasoning"]) > 0)
        check("cost_floor = 10.50", rec["cost_floor"] == 10.50)
        check("stock_level = 20", rec["stock_level"] == 20)
        check("diff_pct computed", rec["diff_pct"] is not None)
        floor = round(10.0 * 1.05, 2)
        check(f"recommended >= cost floor ({floor})",
              rec["recommended_price"] >= floor)
    _purge()


def test_api_pricing():
    """Test the pricing API endpoints via HTTP requests.

    Verifies:
    - GET /api/product/<pid>/pricing returns 200 with recommended_price.
    - POST /api/product/<pid>/apply-price updates selling_price.
    - PriceHistory entry is created for the audit trail.
    """
    print("\n--- API ---")
    _purge()
    uid, sid, email = _make_shop(TEST_SHOPS[0])
    pid = _make_product(sid, "API", cost=10.0, margin=30.0, selling=13.0)

    with app.test_client() as c:
        tok = _login(c, email)
        r = c.get(f"/api/product/{pid}/pricing")
        check("GET /pricing 200", r.status_code == 200)
        data = r.get_json()
        check("GET /pricing has recommended_price",
              data is not None and "recommended_price" in data)

        r = c.post(f"/api/product/{pid}/apply-price",
                   headers={"X-CSRFToken": tok})
        check("POST /apply-price 200", r.status_code == 200)
        data = r.get_json()
        check("POST /apply-price has new_price",
              data is not None and "new_price" in data)

    with app.app_context():
        p = db.session.get(Product, pid)
        if data:
            check("Selling price updated",
                  float(p.selling_price) == float(data["new_price"]))
        hist = PriceHistory.query.filter_by(product_id=pid).count()
        check("PriceHistory entries >= 2", hist >= 2)
    _purge()


def test_role_permissions():
    """Test RBAC enforcement on pricing endpoints.

    Verifies:
    - Owner can GET /pricing and POST /apply-price.
    - Manager can GET /pricing and POST /apply-price.
    - Staff can GET /pricing but gets 403 on POST /apply-price.
    """
    print("\n--- Roles ---")
    _purge()
    uid, sid, email = _make_shop(TEST_SHOPS[0])
    pid = _make_product(sid, "Perm", cost=10.0, margin=30.0, selling=13.0)
    slug = ''.join(rnd.choices(string.ascii_lowercase, k=6))
    _, me = _make_user(sid, "manager", slug)
    slug2 = ''.join(rnd.choices(string.ascii_lowercase, k=6))
    _, se = _make_user(sid, "staff", slug2)

    with app.test_client() as c:
        tok = _login(c, email)
        check("Owner GET /pricing 200",
              c.get(f"/api/product/{pid}/pricing").status_code == 200)
        check("Owner POST /apply-price 200",
              c.post(f"/api/product/{pid}/apply-price",
                     headers={"X-CSRFToken": tok}).status_code == 200)

        _logout(c)
        tok = _login(c, me)
        check("Manager GET /pricing 200",
              c.get(f"/api/product/{pid}/pricing").status_code == 200)
        check("Manager POST /apply-price 200",
              c.post(f"/api/product/{pid}/apply-price",
                     headers={"X-CSRFToken": tok}).status_code == 200)

        _logout(c)
        tok = _login(c, se)
        check("Staff GET /pricing 200",
              c.get(f"/api/product/{pid}/pricing").status_code == 200)
        check("Staff POST /apply-price 403",
              c.post(f"/api/product/{pid}/apply-price",
                     headers={"X-CSRFToken": tok}).status_code == 403)
    _purge()


def test_shop_isolation():
    """Test that cross-shop access is blocked on pricing endpoints.

    Verifies that a user from Shop B cannot access pricing data
    or apply prices to products belonging to Shop A.
    """
    print("\n--- Isolation ---")
    _purge()
    _, sid_a, ea = _make_shop(TEST_SHOPS[0])
    _, sid_b, eb = _make_shop(TEST_SHOPS[1])
    pid_a = _make_product(sid_a, "IsoA", cost=10.0, margin=30.0, selling=13.0)

    with app.test_client() as c:
        tok = _login(c, eb)
        check("Cross-shop GET /pricing 403",
              c.get(f"/api/product/{pid_a}/pricing").status_code == 403)
        check("Cross-shop POST /apply-price 403",
              c.post(f"/api/product/{pid_a}/apply-price",
                     headers={"X-CSRFToken": tok}).status_code == 403)
    _purge()


def test_cost_floor_enforced():
    """Test that the cost floor is enforced in real recommendations.

    Creates a product with high cost and verifies that the recommended
    price is never below cost * 1.05, even when the ML model might
    predict a lower value.
    """
    print("\n--- Floor Enforcement ---")
    _purge()
    uid, sid, email = _make_shop(TEST_SHOPS[0])
    pid = _make_product(sid, "Floor", cost=100.0, margin=30.0, selling=50.0, qty=5, unit="kg")
    with app.app_context():
        rec = get_price_recommendation(pid)
        floor = round(100.0 * 1.05, 2)
        check(f"rec >= cost floor ({floor})", rec["recommended_price"] >= floor)
        check("cost_floor in response", rec["cost_floor"] == floor)
    _purge()


def test_no_market_data():
    """Test recommendation behavior when no market data exists.

    Verifies that:
    - Confidence is 'low' (no market intelligence available).
    - A price is still returned (rule-based fallback).
    - Market stats show n=0 (no observations).
    """
    print("\n--- No Market Data ---")
    _purge()
    uid, sid, email = _make_shop(TEST_SHOPS[0])
    pid = _make_product(sid, "NoMkt", cost=10.0, margin=30.0, selling=13.0)
    with app.app_context():
        rec = get_price_recommendation(pid)
        check("low confidence when no market data", rec["confidence"] == "low")
        check("returns a price anyway", rec["recommended_price"] > 0)
        check("n=0 in market stats", rec["market_stats"]["n"] == 0)
    _purge()


# =============================== MAIN
def main():
    global PASSED, FAILED
    PASSED = FAILED = 0
    print("=" * 60)
    print("ShelfSenseAI - Phase 3E Pricing Engine Tests")
    print("=" * 60)

    test_cost_floor()
    test_market_sanity()
    test_pcapa()
    test_confidence()
    test_recommendation_basic()
    test_api_pricing()
    test_role_permissions()
    test_shop_isolation()
    test_cost_floor_enforced()
    test_no_market_data()

    _purge()
    total = PASSED + FAILED
    print(f"\n{'=' * 60}")
    print(f"test_pricing_engine: {PASSED}/{total} passed" +
          (f" ({FAILED} FAILED)" if FAILED else ""))
    print("=" * 60)
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
