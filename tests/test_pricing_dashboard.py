"""
============================================================
 ShelfSenseAI - Phase 9 Pricing Dashboard Tests
============================================================

Covers the shop-wide pricing intelligence dashboard:

  Authorization   - anonymous denied, staff denied, cross-shop isolation
  Summary         - total/market-covered/above/below/pending/applied/dismissed
  Filtering       - status, decision, evidence, tier, search
  Sorting         - difference, priority, name
  Empty states    - no products / no coverage / no recommendations / no history
  Integration     - drill-down link, Phase 8 authoritative, GET mutates nothing

Run:
    ./venv/Scripts/python.exe tests/test_pricing_dashboard.py
    pytest tests/test_pricing_dashboard.py -q
"""
import os
import sys
import re
import string
import random as rnd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from app import (app, db, User, Shop, Product, Inventory,  # noqa: E402
                 PriceHistory, PricingRecommendationDecision)
from services.pricing_workflow import (record_decision, apply_decision,  # noqa: E402
                                       dismiss_decision)
from services.pricing_dashboard import (get_shop_pricing_opportunities,  # noqa: E402
                                        get_shop_pricing_summary,
                                        get_shop_decision_summary,
                                        _priority, PRIORITY_ORDER)
from werkzeug.security import generate_password_hash  # noqa: E402

PW = "Test1234!"
TEST_SHOPS = ["P9DashShopA", "P9DashShopB"]
PASSED = FAILED = 0


def check(label, cond):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  [PASS] {label}")
    else:
        FAILED += 1
        print(f"  [FAIL] {label}")


def _purge():
    with app.app_context():
        rows = db.session.execute(
            text("SELECT id FROM shop WHERE name IN :n"),
            {"n": tuple(TEST_SHOPS)}).fetchall()
        if not rows:
            return
        sids = ",".join(str(r[0]) for r in rows)
        for tbl, col in [("pricing_recommendation_decision", "shop_id"),
                         ("inventory_adjustment", "product_id"),
                         ("sale", "product_id"),
                         ("price_history", "product_id"),
                         ("inventory", "product_id"),
                         ("product_market_match", "shop_product_id")]:
            if col == "shop_id":
                db.session.execute(text(f"DELETE FROM {tbl} WHERE shop_id IN ({sids})"))
            else:
                db.session.execute(text(
                    f"DELETE FROM {tbl} WHERE {col} IN "
                    f"(SELECT id FROM product WHERE shop_id IN ({sids}))"))
        db.session.execute(text(f"DELETE FROM product WHERE shop_id IN ({sids})"))
        db.session.execute(text(f"DELETE FROM user WHERE shop_id IN ({sids})"))
        db.session.execute(text("DELETE FROM shop WHERE name IN :n"),
                           {"n": tuple(TEST_SHOPS)})
        db.session.commit()


def _slug():
    return "".join(rnd.choices(string.ascii_lowercase, k=6))


def _make_shop(name):
    slug = _slug()
    email = f"p9own_{slug}@shelfsense.my"
    with app.app_context():
        u = User(email=email, password_hash=generate_password_hash(PW), role="owner")
        db.session.add(u); db.session.flush()
        s = Shop(name=name); db.session.add(s); db.session.flush()
        u.shop_id = s.id; db.session.commit()
        return u.id, s.id, email


def _make_product(sid, name, cost=2.0, margin=50.0, selling=10.0, category=None):
    with app.app_context():
        p = Product(name=name, category=category, cost_price=cost,
                    target_margin=margin, baseline_margin=margin,
                    selling_price=selling, quantity=1, unit="unit", shop_id=sid)
        db.session.add(p); db.session.flush()
        db.session.add(PriceHistory(product_id=p.id, cost_price=cost,
                                    selling_price=selling, target_margin=margin))
        db.session.add(Inventory(shop_id=sid, product_id=p.id,
                                 current_stock=10, minimum_stock=2))
        db.session.commit()
        return p.id


def _make_user(sid, role):
    slug = _slug()
    email = f"p9_{role}_{slug}@shelfsense.my"
    with app.app_context():
        u = User(email=email, password_hash=generate_password_hash(PW),
                 role=role, shop_id=sid)
        db.session.add(u); db.session.commit()
        return u.id, email


def _csrf_of(client, path):
    r = client.get(path)
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', r.data.decode())
    return m.group(1) if m else ""


def _login(client, email):
    csrf = _csrf_of(client, "/login")
    client.post("/login", data={"email": email, "password": PW,
                                "csrf_token": csrf}, follow_redirects=True)
    return csrf


# ---------------------------------------------------------------------------
# Pure priority function (deterministic, price-neutral)
# ---------------------------------------------------------------------------
def test_priority_function():
    print("\n--- Priority function ---")
    check("strong + REDUCE = high", _priority("REDUCE", "Strong") == "high")
    check("moderate + INCREASE = high", _priority("INCREASE", "Moderate") == "high")
    check("limited + REDUCE = medium", _priority("REDUCE", "Limited") == "medium")
    check("strong + MAINTAIN = medium", _priority("MAINTAIN", "Strong") == "medium")
    check("limited + MAINTAIN = low", _priority("MAINTAIN", "Limited") == "low")
    check("INSUFFICIENT_DATA = none", _priority("INSUFFICIENT_DATA", "Strong") == "none")
    check("priority order constant", PRIORITY_ORDER["high"] < PRIORITY_ORDER["medium"]
          < PRIORITY_ORDER["low"] < PRIORITY_ORDER["none"])


# ---------------------------------------------------------------------------
# Summary metrics
# ---------------------------------------------------------------------------
def test_summary_metrics():
    print("\n--- Summary metrics ---")
    _purge()
    uid, sid, email = _make_shop(TEST_SHOPS[0])
    # Three products: all get engine recommendations (no market data ->
    # status from rule-based path, evidence Unavailable).
    p1 = _make_product(sid, "Alpha", selling=10.0)
    p2 = _make_product(sid, "Beta", selling=20.0)
    p3 = _make_product(sid, "Gamma", selling=30.0)
    # Phase 8 decisions: apply on p1, dismiss on p2, leave p3 pending.
    with app.app_context():
        shop = db.session.get(Shop, sid)
        d1, _ = record_decision(p1, shop=shop, user_id=uid)
        apply_decision(d1.id, uid, shop=shop)
        d2, _ = record_decision(p2, shop=shop, user_id=uid)
        dismiss_decision(d2.id, uid, "Management decision")
        d3, _ = record_decision(p3, shop=shop, user_id=uid)

    with app.app_context():
        shop = db.session.get(Shop, sid)
        s = get_shop_pricing_summary(shop)
        check("total products = 3", s["total_products"] == 3)
        check("pending counts 1 product", s["pending"] == 1)
        check("applied counts 1 product", s["applied"] == 1)
        check("dismissed counts 1 product", s["dismissed"] == 1)
        check("generated_total = 3 decisions", s["generated_total"] == 3)
        check("applied_this_month >= 1", s["applied_this_month"] >= 1)
        check("status_counts present", isinstance(s["status_counts"], dict))
        check("no market coverage (no matches)",
              s["market_covered"] == 0)
        # shop decision summary works standalone
        ds = get_shop_decision_summary(sid)
        check("decision summary applied=1", ds["applied"] == 1)
        check("decision summary dismissed=1", ds["dismissed"] == 1)
        check("decision summary pending=1", ds["pending"] == 1)
    _purge()


def test_summary_counts_distinct_products():
    print("\n--- Pending counts distinct products (not raw rows) ---")
    _purge()
    uid, sid, email = _make_shop(TEST_SHOPS[0])
    p1 = _make_product(sid, "Alpha")
    with app.app_context():
        shop = db.session.get(Shop, sid)
        # one product, multiple pending snapshots (different prices)
        record_decision(p1, shop=shop, user_id=uid)
        p = db.session.get(Product, p1)
        p.selling_price = 9.0
        db.session.commit()
        record_decision(p1, shop=shop, user_id=uid)
    with app.app_context():
        shop = db.session.get(Shop, sid)
        s = get_shop_pricing_summary(shop)
        check("2 pending rows exist",
              PricingRecommendationDecision.query.filter_by(
                  shop_id=sid, decision="PENDING").count() == 2)
        check("but pending products = 1", s["pending"] == 1)
    _purge()


# ---------------------------------------------------------------------------
# Filtering + sorting
# ---------------------------------------------------------------------------
def test_filters_and_sorting():
    print("\n--- Filters + sorting ---")
    _purge()
    uid, sid, email = _make_shop(TEST_SHOPS[0])
    _make_product(sid, "Alpha Prod", selling=10.0, category="Drinks")
    _make_product(sid, "Beta Prod", selling=20.0)
    _make_product(sid, "Gamma Prod", selling=30.0)
    with app.app_context():
        shop = db.session.get(Shop, sid)
        r = get_shop_pricing_opportunities(shop, per_page=50)
        check("all 3 rows returned", r["total"] == 3)
        names = [row["name"] for row in r["rows"]]

        # search
        r = get_shop_pricing_opportunities(shop, per_page=50, search="alpha")
        check("search 'alpha' -> 1 row", r["total"] == 1
              and r["rows"][0]["name"] == "Alpha Prod")

        # status filter (no market data -> INSUFFICIENT_DATA is unlikely;
        # rule-based path yields a real status, so filter by whatever exists)
        st = r["rows"][0]["status"] if r["rows"] else None
        if st:
            r2 = get_shop_pricing_opportunities(shop, per_page=50,
                                                status_filter=st)
            check("status filter returns subset",
                  all(row["status"] == st for row in r2["rows"]))
        # a nonsense filter returns zero
        r3 = get_shop_pricing_opportunities(shop, per_page=50,
                                            status_filter="NOT_A_STATUS")
        check("bogus status filter -> 0 rows", r3["total"] == 0)

        # evidence filter: all rows are Unavailable (no market data)
        r4 = get_shop_pricing_opportunities(shop, per_page=50,
                                            evidence_filter="Unavailable")
        check("evidence filter Unavailable -> 3 rows", r4["total"] == 3)

        # tier filter: none are district
        r5 = get_shop_pricing_opportunities(shop, per_page=50,
                                            tier_filter="district")
        check("tier district -> 0 rows", r5["total"] == 0)

        # decision filter: no decisions yet
        r6 = get_shop_pricing_opportunities(shop, per_page=50,
                                            decision_filter="PENDING")
        check("no decisions -> 0 rows", r6["total"] == 0)

        # sorting by name
        r7 = get_shop_pricing_opportunities(shop, per_page=50, sort="name")
        sorted_names = [row["name"] for row in r7["rows"]]
        check("name sort ascending",
              sorted_names == sorted(sorted_names, key=str.lower))
    _purge()


def test_sorting_by_diff_and_pagination():
    print("\n--- Diff sorting + pagination ---")
    _purge()
    uid, sid, email = _make_shop(TEST_SHOPS[0])
    # products with different gaps between selling and target price
    _make_product(sid, "BigGap", cost=2.0, margin=50.0, selling=20.0)
    _make_product(sid, "SmallGap", cost=2.0, margin=50.0, selling=4.0)
    _make_product(sid, "MidGap", cost=2.0, margin=50.0, selling=10.0)
    with app.app_context():
        shop = db.session.get(Shop, sid)
        # no market data -> rule-based candidate = cost*(1+margin) = 3.00;
        # SME floor keeps 3.00; diff = 3.00 - selling.
        r = get_shop_pricing_opportunities(shop, per_page=50, sort="above_market")
        diffs = [row["diff"] for row in r["rows"] if row["diff"] is not None]
        check("above_market sorted descending",
              diffs == sorted(diffs, reverse=True))
        r2 = get_shop_pricing_opportunities(shop, per_page=2, sort="name")
        check("per_page=2 -> page size 2", len(r2["rows"]) == 2)
        check("pages computed", r2["pages"] == 2)
        r3 = get_shop_pricing_opportunities(shop, page=2, per_page=2, sort="name")
        check("page 2 has remaining rows", len(r3["rows"]) == 1)
    _purge()


# ---------------------------------------------------------------------------
# Authorization + isolation via HTTP
# ---------------------------------------------------------------------------
def test_authorization_and_isolation():
    print("\n--- Authorization + cross-shop isolation ---")
    _purge()
    uid_a, sid_a, email_a = _make_shop(TEST_SHOPS[0])
    _, sid_b, email_b = _make_shop(TEST_SHOPS[1])
    _make_product(sid_a, "ShopA Product")
    _make_product(sid_b, "ShopB Product")
    staff_email = _make_user(sid_a, "staff")[1]

    with app.test_client() as c:
        # anonymous
        r = c.get("/pricing-dashboard", follow_redirects=False)
        check("anonymous redirected to login (302)", r.status_code == 302)

        # staff denied (management pricing intelligence)
        _login(c, staff_email)
        check("staff gets 403",
              c.get("/pricing-dashboard").status_code == 403)

        # owner of shop A sees only shop A products
        _logout_if_needed(c)
        _login(c, email_a)
        r = c.get("/pricing-dashboard")
        check("owner 200", r.status_code == 200)
        html = r.data.decode()
        check("sees own product", "ShopA Product" in html)
        check("does NOT see other shop's product", "ShopB Product" not in html)

        # owner of shop B likewise
        c.get("/logout")
        _login(c, email_b)
        html = c.get("/pricing-dashboard").data.decode()
        check("shop B sees own product", "ShopB Product" in html)
        check("shop B does NOT see shop A product", "ShopA Product" not in html)
    _purge()


def _logout_if_needed(c):
    c.get("/logout")


# ---------------------------------------------------------------------------
# GET is side-effect free
# ---------------------------------------------------------------------------
def test_dashboard_read_only():
    print("\n--- Dashboard GET mutates nothing ---")
    _purge()
    uid, sid, email = _make_shop(TEST_SHOPS[0])
    pid = _make_product(sid, "Stable Price", selling=10.0)
    with app.app_context():
        shop = db.session.get(Shop, sid)
        price_before = float(db.session.get(Product, pid).selling_price)
        hist_before = PriceHistory.query.filter_by(product_id=pid).count()
        dec_before = PricingRecommendationDecision.query.filter_by(
            product_id=pid).count()
    with app.test_client() as c:
        _login(c, email)
        # repeated reads with filters/sorting/pagination
        c.get("/pricing-dashboard")
        c.get("/pricing-dashboard?status=REDUCE&sort=diff&page=1")
        c.get("/pricing-dashboard?evidence=Strong&search=stable")
        c.get("/pricing-dashboard?decision=PENDING&tier=district&page=2")
    with app.app_context():
        check("price unchanged after GETs",
              float(db.session.get(Product, pid).selling_price) == price_before)
        check("no PriceHistory rows created",
              PriceHistory.query.filter_by(product_id=pid).count() == hist_before)
        check("no decision records created by dashboard reads",
              PricingRecommendationDecision.query.filter_by(
                  product_id=pid).count() == dec_before)
    _purge()


# ---------------------------------------------------------------------------
# Integration: drill-down + Phase 8 remains authoritative
# ---------------------------------------------------------------------------
def test_drilldown_and_phase8_authoritative():
    print("\n--- Drill-down + Phase 8 workflow intact ---")
    _purge()
    uid, sid, email = _make_shop(TEST_SHOPS[0])
    pid = _make_product(sid, "Drilldown Prod", cost=2.0, margin=50.0,
                        selling=10.0)
    with app.test_client() as c:
        tok = _login(c, email)
        html = c.get("/pricing-dashboard").data.decode()
        check("dashboard links to product page",
              f'href="/product/{pid}"' in html)
        # open product page (this records the PENDING decision, per Phase 8)
        check("product page reachable", c.get(f"/product/{pid}").status_code == 200)
        # Phase 8 workflow still authoritative: decision + apply via existing API
        r = c.post(f"/api/product/{pid}/decision", headers={"X-CSRFToken": tok})
        check("Phase 8 decision endpoint works", r.status_code == 200)
        did = r.get_json()["decision_id"]
        r = c.post(f"/api/decision/{did}/apply", headers={"X-CSRFToken": tok})
        check("Phase 8 apply works from dashboard path", r.status_code == 200)
    with app.app_context():
        s = get_shop_pricing_summary(db.session.get(Shop, sid))
        check("applied count now 1", s["applied"] == 1)
        check("price actually changed (Phase 8 did it)",
              float(db.session.get(Product, pid).selling_price) != 10.0)
    # Dismiss path also still works
    with app.test_client() as c:
        tok = _login(c, email)
        with app.app_context():
            shop = db.session.get(Shop, sid)
            d, _ = record_decision(pid, shop=shop, user_id=uid)
            did = d.id
        r = c.post(f"/api/decision/{did}/dismiss", headers={"X-CSRFToken": tok},
                   json={"reason": "Other", "note": "phase9 check"})
        check("Phase 8 dismiss works", r.status_code == 200)
    _purge()


# ---------------------------------------------------------------------------
# Empty states
# ---------------------------------------------------------------------------
def test_empty_states():
    print("\n--- Empty states ---")
    _purge()
    uid, sid, email = _make_shop(TEST_SHOPS[0])
    # Shop with NO products
    with app.app_context():
        shop = db.session.get(Shop, sid)
        r = get_shop_pricing_opportunities(shop)
        check("no products: total 0", r["total"] == 0)
        check("no products: summary zeros",
              r["summary"]["total_products"] == 0
              and r["summary"]["pending"] == 0)
    with app.test_client() as c:
        _login(c, email)
        html = c.get("/pricing-dashboard").data.decode()
        check("empty-state message shown", "No products in your shop yet" in html)
        check("add-product link offered", "Add Product" in html)
    # Shop with a product but no market data / no decisions
    _make_product(sid, "Lonely Product")
    with app.test_client() as c:
        _login(c, email)
        html = c.get("/pricing-dashboard").data.decode()
        check("no-decision-history message shown",
              "No decision history yet" in html)
        check("no-market-position message shown",
              "No market comparison available" in html)
    _purge()


# ---------------------------------------------------------------------------
# Unassigned user
# ---------------------------------------------------------------------------
def test_unassigned_user():
    print("\n--- Unassigned user ---")
    _purge()
    with app.app_context():
        u = User(email=f"p9_unassigned_{_slug()}@shelfsense.my",
                 password_hash=generate_password_hash(PW), role="owner")
        db.session.add(u); db.session.commit()
        email = u.email
    with app.test_client() as c:
        _login(c, email)
        r = c.get("/pricing-dashboard")
        check("unassigned user gets page (not 500)", r.status_code == 200)
        check("unassigned info shown", "not linked to a shop" in r.data.decode())
    # clean up this user (no shop → purge won't catch them)
    with app.app_context():
        u = User.query.filter_by(email=email).first()
        if u:
            db.session.delete(u)
            db.session.commit()
    _purge()


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------
def main():
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn)]
    _purge()
    failed = []
    global FAILED
    for name, fn in tests:
        print(f"[{name}]")
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            FAILED += 1
            failed.append((name, exc))
            import traceback
            traceback.print_exc()
    _purge()
    total = PASSED + FAILED
    print(f"\ntest_pricing_dashboard: {PASSED}/{total} checks passed")
    for name, exc in failed:
        print(f"  FAIL {name}: {type(exc).__name__}: {exc}")
    return 1 if FAILED or failed else 0


if __name__ == "__main__":
    sys.exit(main())
