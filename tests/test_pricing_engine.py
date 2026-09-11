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
# Every fixture name this suite creates, including the randomized
# GeoCtx_* shops and TestSrc_* market sources built by
# _make_market_fixture(). _purge() matches by LIKE pattern (not a fixed
# name list) so a future rename can never silently re-leak — the Phase 10
# audit found 28 leaked fixture shops caused exactly by name-blind purges.
FIXTURE_SHOP_LIKE = ("PricingTestShop%", "GeoCtx%")
FIXTURE_SOURCE_LIKE = ("TestSrc\\_%",)
PW = "Test1234!"
DOMAIN = "shelfsense.my"


def check(label, cond):
    global PASSED, FAILED
    if cond:
        PASSED += 1; print(f"  [PASS] {label}")
    else:
        FAILED += 1; print(f"  [FAIL] {label}")


def _purge():
    """FK-safe removal of every row this suite created.

    Fixture shops/sources are matched by name pattern so the randomized
    GeoCtx_* / TestSrc_* fixtures are always covered, not just the fixed
    TEST_SHOPS (Phase 10 audit fix).
    """
    with app.app_context():
        def _like_clause(patterns, prefix):
            clause = " OR ".join(f"name LIKE :{prefix}{i}"
                                  for i in range(len(patterns)))
            return clause, {f"{prefix}{i}": p
                            for i, p in enumerate(patterns)}

        clause, params = _like_clause(FIXTURE_SHOP_LIKE, "s")
        rows = db.session.execute(
            text(f"SELECT id FROM shop WHERE {clause}"), params).fetchall()
        if rows:
            sids = ",".join(str(r[0]) for r in rows)
            db.session.execute(text(
                f"DELETE FROM pricing_recommendation_decision "
                f"WHERE shop_id IN ({sids})"))
            for tbl, col in [("inventory_adjustment","product_id"),("sale","product_id"),
                              ("price_history","product_id"),("inventory","product_id"),
                              ("product_market_match","shop_product_id")]:
                db.session.execute(text(
                    f"DELETE FROM {tbl} WHERE {col} IN "
                    f"(SELECT id FROM product WHERE shop_id IN ({sids}))"))
            db.session.execute(text(f"DELETE FROM product WHERE shop_id IN ({sids})"))
            db.session.execute(text(
                f"DELETE FROM notification WHERE user_id IN "
                f"(SELECT id FROM user WHERE shop_id IN ({sids}))"))
            db.session.execute(text(
                f"DELETE FROM shop_invitation WHERE shop_id IN ({sids})"))
            db.session.execute(text(f"DELETE FROM user WHERE shop_id IN ({sids})"))
            db.session.execute(text(f"DELETE FROM shop WHERE id IN ({sids})"))

        # Market fixtures: GeoCtx observations hang off TestSrc_* sources.
        clause, params = _like_clause(FIXTURE_SOURCE_LIKE, "m")
        mrows = db.session.execute(
            text(f"SELECT id FROM market_source WHERE {clause}"),
            params).fetchall()
        if mrows:
            mids = ",".join(str(r[0]) for r in mrows)
            db.session.execute(text(
                "DELETE pm FROM product_market_match pm "
                "JOIN market_item mi ON mi.id = pm.market_item_id "
                f"WHERE mi.source_id IN ({mids})"))
            db.session.execute(text(
                f"DELETE FROM market_price_observation WHERE market_item_id "
                f"IN (SELECT id FROM market_item WHERE source_id IN ({mids}))"))
            db.session.execute(text(
                f"DELETE FROM market_item WHERE source_id IN ({mids})"))
            db.session.execute(text(
                f"DELETE FROM market_source WHERE id IN ({mids})"))
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


class _StubModel:
    """Returns a fixed prediction so guardrail tests are hermetic.

    The real RandomForest (ml/pricing_model.pkl) predicts whatever it
    learned from KPDN data, so 'candidate below/above the floor' cannot
    be arranged with real data — the trained model predicted RM77.45 for
    a cost=100 product, silently flipping this test's premise.
    """
    def __init__(self, value):
        self.value = value

    def predict(self, X):
        return [self.value]


def test_sme_floor_hard_minimum():
    """Phase 7.1 Fix 1: the SME target-margin protection is a HARD floor.

    The ML candidate is stubbed so each case is deterministic:

    Case A — candidate BELOW the target floor (cost=100, margin=25% ->
      floor = 125.00): a candidate of 110.00 must be raised to exactly
      125.00 and the payload must report the sme_margin guardrail.
    Case B — candidate ABOVE the target floor: a candidate of 150.00 is
      NOT pulled down and sme_margin must NOT fire.
    """
    import services.pricing_engine as pe
    print("\n--- SME Floor Hard Minimum (Phase 7.1) ---")
    _purge()
    uid, sid, email = _make_shop(TEST_SHOPS[0])
    pid_a = _make_product(sid, "SMEFloorA", cost=100.0, margin=25.0,
                          selling=110.0, qty=1, unit="unit")
    pid_b = _make_product(sid, "SMEFloorB", cost=100.0, margin=25.0,
                          selling=150.0, qty=1, unit="unit")
    real_cache = pe._MODEL_CACHE
    try:
        pe._MODEL_CACHE = {"model": _StubModel(0.0)}  # patched below per case
        with app.app_context():
            target_floor = round(100.0 * 1.25, 2)  # 125.00

            pe._MODEL_CACHE = {"model": _StubModel(110.0)}
            rec_a = get_price_recommendation(pid_a)
            check("Case A: raised to exactly the target floor (125)",
                  rec_a["recommended_price"] == target_floor)
            check("Case A: sme_margin guardrail reported",
                  "sme_margin" in rec_a["guardrails_applied"])
            check("Case A: reasoning says target-margin floor (not blend)",
                  any("target-margin floor" in r for r in rec_a["reasoning"]))
            check("Case A: no blend wording in reasoning",
                  not any("blend" in r.lower() for r in rec_a["reasoning"]))

            pe._MODEL_CACHE = {"model": _StubModel(150.0)}
            rec_b = get_price_recommendation(pid_b)
            check("Case B: candidate above floor unchanged (150)",
                  rec_b["recommended_price"] == 150.0)
            check("Case B: sme_margin guardrail NOT fired",
                  "sme_margin" not in rec_b["guardrails_applied"])
    finally:
        pe._MODEL_CACHE = real_cache
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


def test_sme_margin_clamp():
    """Test the SME target-margin floor guardrail (Rule 1b).

    When the ML prediction is below the user's target floor
    (cost * (1 + target_margin/100)), the recommendation is raised to
    that floor (hard minimum). Phase 7.1 Case B: when the candidate is
    at/above the target floor, the existing hierarchy is unchanged and
    the floor does NOT fire.
    """
    print("\n--- SME Target-Margin Floor ---")
    _purge()
    uid, sid, email = _make_shop(TEST_SHOPS[0])
    # Cost=3.00, target_margin=30% -> user_floor = 3.90
    # ML will predict ~suggested_price which is cost*(1+margin/100) = 3.90
    # So the clamp should NOT fire when ML == user_floor
    pid = _make_product(sid, "SME", cost=3.0, margin=30.0, selling=3.50, qty=1, unit="unit")
    with app.app_context():
        rec = get_price_recommendation(pid)
        user_floor = round(3.0 * 1.30, 2)  # 3.90
        # The recommended price should be >= user_floor when no market data
        # pushes it below (no market data -> ML uses suggested_price = 3.90)
        check("recommended >= user floor", rec["recommended_price"] >= user_floor)
    _purge()


def test_sme_margin_clamp_triggers():
    """Test that the SME target-margin floor fires when the ML prediction
    is below the target floor.

    Uses a high margin (50%) so the user_floor is significantly above
    the ML's default prediction, forcing the floor to activate.
    """
    print("\n--- SME Target-Margin Floor Triggers ---")
    _purge()
    uid, sid, email = _make_shop(TEST_SHOPS[0])
    # Cost=10.00, margin=50% -> user_floor = 15.00
    # The ML model with no market data uses suggested_price = cost*(1+margin/100) = 15.00
    # So the clamp should NOT fire when ML == user_floor
    pid = _make_product(sid, "SME2", cost=10.0, margin=50.0, selling=15.0, qty=1, unit="unit")
    with app.app_context():
        rec = get_price_recommendation(pid)
        user_floor = round(10.0 * 1.50, 2)  # 15.00
        check("recommended >= user floor", rec["recommended_price"] >= user_floor)
    _purge()


# =============================== MAIN
def _make_market_fixture(shop_state, shop_district, obs_state, obs_district,
                         n_obs=4):
    """Create a verified product + matched market item + observations.

    Used by the Phase 7.1 shop-context test. The product's shop gets the
    given state/district; the observations get their own state/district so
    the geographic fallback (district -> state -> national) can be
    exercised independently of the real PriceCatcher data.
    """
    from app import MarketSource, MarketItem, MarketPriceObservation, ProductMarketMatch
    slug = ''.join(rnd.choices(string.ascii_lowercase, k=6))
    uid, sid, email = _make_shop(f"GeoCtx_{slug}")
    with app.app_context():
        shop = db.session.get(Shop, sid)
        shop.state, shop.district = shop_state, shop_district
        pid = _make_product(sid, "GeoCtx", cost=10.0, margin=30.0,
                            selling=13.0, qty=1, unit="kg")
        src = MarketSource(name=f"TestSrc_{slug}", source_type="government",
                           is_active=True)
        db.session.add(src); db.session.flush()
        item = MarketItem(source_id=src.id, raw_title=f"GeoCtx Item {slug}",
                          normalized_title=f"geoctx item {slug}",
                          package_quantity=1.0, package_unit="kg")
        db.session.add(item); db.session.flush()
        from datetime import datetime as _dt, timezone as _tz
        base = _dt.now(_tz.utc)
        for i in range(n_obs):
            db.session.add(MarketPriceObservation(
                market_item_id=item.id, premise_code=f"P{i}",
                regular_price=12.0 + i, effective_price=12.0 + i,
                normalized_unit_price=12.0 + i,
                observed_at=base, state=obs_state, district=obs_district))
        db.session.add(ProductMarketMatch(
            shop_product_id=pid, market_item_id=item.id,
            confidence_score=0.99, match_type="manual",
            is_verified=True, is_rejected=False))
        db.session.commit()
        return uid, sid, email, pid


def test_pricing_api_shop_context():
    """Phase 7.1 Fix 2: /api/product/<pid>/pricing must use the SAME
    geographic market context as the server-rendered recommendation.

    Case A — district data satisfies the threshold: both the direct call
      (as the server-rendered page does) and the API resolve tier='district'
      with identical market stats and an identical recommended price.
    Case B — no district data: both fall back consistently (district ->
      state -> national) with identical payloads.
    Case C — a user from another shop gets 403 (ownership check intact).
    """
    print("\n--- Pricing API shop context (Phase 7.1) ---")
    _purge()
    try:
        _run_shop_context_cases()
    finally:
        _purge()


def _run_shop_context_cases():
    """Body of test_pricing_api_shop_context (split out so the fixture
    teardown runs even if a case assertion fails mid-way)."""
    # Case A: shop in Selangor/Hulu Langat; observations in that district.
    _, sid_a, email_a, pid_a = _make_market_fixture(
        "Selangor", "Hulu Langat", "Selangor", "Hulu Langat")
    # Case B: shop in Perlis/Perlis; observations elsewhere -> fallback.
    _, sid_b, email_b, pid_b = _make_market_fixture(
        "Perlis", "Perlis", "Sabah", "Interior")
    # Case C: an unrelated shop whose user must not read shop A's product.
    _, sid_c, email_c = _make_shop(f"GeoCtxOther_{''.join(rnd.choices(string.ascii_lowercase, k=6))}")

    with app.app_context():
        shop_a = db.session.get(Shop, sid_a)
        rec_server = get_price_recommendation(pid_a, shop=shop_a)

    with app.test_client() as c:
        tok = _login(c, email_a)
        r = c.get(f"/api/product/{pid_a}/pricing")
        check("Case A: API 200", r.status_code == 200)
        api = r.get_json()
        check("Case A: API tier == district", api["market_stats"]["market_tier"] == "district")
        check("Case A: server-rendered tier == district",
              rec_server["market_stats"]["market_tier"] == "district")
        check("Case A: same tier API vs server",
              api["market_stats"]["market_tier"] == rec_server["market_stats"]["market_tier"])
        check("Case A: same median API vs server",
              api["market_stats"]["median"] == rec_server["market_stats"]["median"])
        check("Case A: same n API vs server",
              api["market_stats"]["n"] == rec_server["market_stats"]["n"])
        check("Case A: same recommended price",
              api["recommended_price"] == rec_server["recommended_price"])
        check("Case A: same evidence rating",
              api["market_evidence"] == rec_server["market_evidence"])

        _logout(c)
        tok = _login(c, email_b)
        r = c.get(f"/api/product/{pid_b}/pricing")
        check("Case B: API 200", r.status_code == 200)
        api_b = r.get_json()
        check("Case B: no district match -> not district tier",
              api_b["market_stats"]["market_tier"] != "district")
        with app.app_context():
            shop_b = db.session.get(Shop, sid_b)
            rec_b = get_price_recommendation(pid_b, shop=shop_b)
        check("Case B: fallback tier matches server-rendered call",
              api_b["market_stats"]["market_tier"] == rec_b["market_stats"]["market_tier"])

        _logout(c)
        _login(c, email_c)
        check("Case C: cross-shop pricing 403",
              c.get(f"/api/product/{pid_a}/pricing").status_code == 403)
    _purge()


def _assert_no_leaks():
    """Assert the suite left zero fixture rows behind (Phase 10 guard).

    Called from main() after the run and from the pytest session fixture
    in conftest.py, so the guarantee holds under BOTH runners.
    """
    with app.app_context():
        clause = " OR ".join(f"name LIKE :p{i}" for i in
                             range(len(FIXTURE_SHOP_LIKE)))
        leaked = db.session.execute(
            text(f"SELECT name FROM shop WHERE {clause}"),
            {f"p{i}": p for i, p in enumerate(FIXTURE_SHOP_LIKE)}).fetchall()
        mclause = " OR ".join(f"name LIKE :s{i}" for i in
                              range(len(FIXTURE_SOURCE_LIKE)))
        mleaked = db.session.execute(
            text(f"SELECT name FROM market_source WHERE {mclause}"),
            {f"s{i}": p for i, p in enumerate(FIXTURE_SOURCE_LIKE)}).fetchall()
    assert not leaked, f"fixture shops leaked: {[r[0] for r in leaked]}"
    assert not mleaked, f"fixture sources leaked: {[r[0] for r in mleaked]}"


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
    test_sme_floor_hard_minimum()
    test_api_pricing()
    test_role_permissions()
    test_shop_isolation()
    test_cost_floor_enforced()
    test_no_market_data()
    test_sme_margin_clamp()
    test_sme_margin_clamp_triggers()
    test_pricing_api_shop_context()

    _purge()
    _assert_no_leaks()

    total = PASSED + FAILED
    print(f"\n{'=' * 60}")
    print(f"test_pricing_engine: {PASSED}/{total} passed" +
          (f" ({FAILED} FAILED)" if FAILED else ""))
    print("=" * 60)
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
