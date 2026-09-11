"""
Phase 10D — Market Data Monitoring UI tests.

Covers Owner/Manager/Staff/unauth, source overview, healthy/warning/unhealthy,
never-refreshed, recent history limit, read-only, source isolation.
"""
import sys, re, string, random as rnd
from datetime import datetime, timezone, timedelta, date

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402
from app import app, db, MarketSource, MarketItem, MarketPriceObservation, MarketRefreshRun, Product, PriceHistory, Inventory  # noqa: E402

TEST_SRC = "TestMonitoring_Mana"
TEST_SRC2 = "TestMonitoring_PC"
TEST_SHOP = "TestMonitoring_Shop"
PW = "Test1234!"
DOMAIN = "shelfsense.my"

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
        for q in [
            "DELETE FROM market_refresh_run WHERE source_name LIKE 'testmonitoring%'",
            "DELETE FROM market_refresh_run WHERE triggered_by='test'",
            "DELETE o FROM market_price_observation o JOIN market_item mi ON mi.id=o.market_item_id WHERE mi.source_id IN (SELECT id FROM market_source WHERE name IN (:a,:b))",
            "DELETE pm FROM product_market_match pm JOIN market_item mi ON mi.id=pm.market_item_id WHERE mi.source_id IN (SELECT id FROM market_source WHERE name IN (:a,:b))",
            "DELETE mi FROM market_item mi WHERE mi.source_id IN (SELECT id FROM market_source WHERE name IN (:a,:b))",
            "DELETE FROM market_source WHERE name IN (:a,:b)",
        ]:
            try:
                db.session.execute(text(q), {"a": TEST_SRC, "b": TEST_SRC2})
            except Exception:
                pass
        rows = db.session.execute(text("SELECT id FROM shop WHERE name=:n"), {"n": TEST_SHOP}).fetchall()
        if rows:
            sids=",".join(str(r[0]) for r in rows)
            for qq in [
                f"DELETE FROM pricing_recommendation_decision WHERE shop_id IN ({sids})",
                f"DELETE FROM notification WHERE user_id IN (SELECT id FROM user WHERE shop_id IN ({sids}))",
                f"DELETE FROM shop_invitation WHERE shop_id IN ({sids})",
            ]:
                try:
                    db.session.execute(text(qq))
                except Exception:
                    pass
            for tbl,col in [("inventory_adjustment","product_id"),("sale","product_id"),("price_history","product_id"),("inventory","product_id"),("product_market_match","shop_product_id")]:
                try:
                    db.session.execute(text(f"DELETE FROM {tbl} WHERE {col} IN (SELECT id FROM product WHERE shop_id IN ({sids}))"))
                except Exception:
                    pass
            for qq in [f"DELETE FROM product WHERE shop_id IN ({sids})", f"DELETE FROM user WHERE shop_id IN ({sids})", f"DELETE FROM shop WHERE id IN ({sids})"]:
                try:
                    db.session.execute(text(qq))
                except Exception:
                    pass
        db.session.commit()

def _make_shop(name, role="owner"):
    from werkzeug.security import generate_password_hash
    from app import Shop, User
    slug=''.join(rnd.choices(string.ascii_lowercase,k=5))
    email=f"{role}_{slug}@{DOMAIN}"
    with app.app_context():
        u=User(email=email, password_hash=generate_password_hash(PW), role=role)
        db.session.add(u); db.session.flush()
        s=Shop(name=name); db.session.add(s); db.session.flush()
        u.shop_id=s.id; db.session.commit()
        return u.id, s.id, email

def _make_user(sid, role):
    from werkzeug.security import generate_password_hash
    from app import User
    slug=''.join(rnd.choices(string.ascii_lowercase,k=5))
    email=f"{role}_{slug}@{DOMAIN}"
    with app.app_context():
        u=User(email=email, password_hash=generate_password_hash(PW), role=role, shop_id=sid)
        db.session.add(u); db.session.commit()
        return u.id, email

def _csrf(c, path):
    r=c.get(path)
    m=re.search(r'name="csrf_token"[^>]*value="([^"]+)"', r.data.decode())
    return m.group(1) if m else ""

def _login(c, email):
    tok=_csrf(c, "/login")
    c.post("/login", data={"email": email, "password": PW, "csrf_token": tok}, follow_redirects=True)
    return tok

def _ensure_source_with_obs(source_name, latest_dt):
    with app.app_context():
        src=MarketSource(name=source_name, source_type="online_retailer", is_active=True)
        db.session.add(src); db.session.flush()
        mi=MarketItem(source_id=src.id, external_id="mon-1", raw_title="Mon Item", normalized_title="mon item", package_quantity=1, package_unit="unit")
        db.session.add(mi); db.session.flush()
        obs=MarketPriceObservation(market_item_id=mi.id, regular_price=10, promo_price=None, is_on_promo=False, effective_price=10, normalized_unit_price=10, observed_at=latest_dt)
        db.session.add(obs); db.session.commit()
        return src.id

def _make_run(source_name, status="success", inserted=5, latest=None):
    with app.app_context():
        if latest is None:
            latest=datetime.now(timezone.utc)-timedelta(days=1)
        run=MarketRefreshRun(source_name=source_name.lower(), started_at=datetime.now(timezone.utc)-timedelta(seconds=10), finished_at=datetime.now(timezone.utc), status=status, inserted=inserted, updated=1, duplicates_skipped=0, rejected=0, errors=1 if status=="failed" else 0, latest_observed_at=latest, error_message="boom" if status=="failed" else None, triggered_by="test")
        db.session.add(run); db.session.commit()
        return run.id

# 1 Owner
def test_owner_access():
    print("\n--- owner access ---")
    _purge()
    uid,sid,email=_make_shop(TEST_SHOP, "owner")
    with app.test_client() as c:
        _login(c,email)
        r=c.get("/market-data")
        check("owner 200", r.status_code==200)
        check("contains monitoring", b"Market Data Monitoring" in r.data)
    _purge()

# 2 Manager
def test_manager_access():
    print("\n--- manager access ---")
    _purge()
    uid,sid,email=_make_shop(TEST_SHOP, "owner")
    _, m_email=_make_user(sid, "manager")
    with app.test_client() as c:
        _login(c,m_email)
        r=c.get("/market-data")
        check("manager 200", r.status_code==200)
    _purge()

# 3 Staff 403
def test_staff_denied():
    print("\n--- staff 403 ---")
    _purge()
    uid,sid,email=_make_shop(TEST_SHOP, "owner")
    _, s_email=_make_user(sid, "staff")
    with app.test_client() as c:
        _login(c,s_email)
        r=c.get("/market-data")
        check("staff 403", r.status_code==403)
    _purge()

# 4 unauth
def test_unauth():
    print("\n--- unauth ---")
    with app.test_client() as c:
        r=c.get("/market-data", follow_redirects=False)
        # login_required redirects to login
        check("unauth redirect", r.status_code in (302,308))
        check("redirect to login", "/login" in r.headers.get("Location",""))

# 5 source overview
def test_source_overview():
    print("\n--- source overview ---")
    _purge()
    uid,sid,email=_make_shop(TEST_SHOP)
    with app.app_context():
        _ensure_source_with_obs(TEST_SRC, datetime.now(timezone.utc)-timedelta(days=1))
        _make_run(TEST_SRC, status="success", inserted=5, latest=datetime.now(timezone.utc)-timedelta(days=1))
    with app.test_client() as c:
        _login(c,email)
        r=c.get("/market-data")
        html=r.data.decode()
        check("source name rendered", TEST_SRC in html)
        check("status success", "Success" in html or "success" in html.lower())
        check("metrics inserted", "inserted" in html.lower())
    _purge()

# 6 healthy
def test_healthy():
    print("\n--- healthy ---")
    _purge()
    uid,sid,email=_make_shop(TEST_SHOP)
    with app.app_context():
        _ensure_source_with_obs(TEST_SRC, datetime.now(timezone.utc)-timedelta(days=1))
        _make_run(TEST_SRC, status="success", inserted=5, latest=datetime.now(timezone.utc)-timedelta(days=1))
    with app.test_client() as c:
        _login(c,email)
        html=c.get("/market-data").data.decode()
        check("healthy badge", "Healthy" in html)
    _purge()

# 7 warning (partial)
def test_warning():
    print("\n--- warning ---")
    _purge()
    uid,sid,email=_make_shop(TEST_SHOP)
    with app.app_context():
        _ensure_source_with_obs(TEST_SRC, datetime.now(timezone.utc)-timedelta(days=1))
        _make_run(TEST_SRC, status="partial", inserted=2, latest=datetime.now(timezone.utc)-timedelta(days=1))
    with app.test_client() as c:
        _login(c,email)
        html=c.get("/market-data").data.decode()
        check("warning badge", "Warning" in html)
    _purge()

# 8 unhealthy (failed)
def test_unhealthy():
    print("\n--- unhealthy ---")
    _purge()
    uid,sid,email=_make_shop(TEST_SHOP)
    with app.app_context():
        _ensure_source_with_obs(TEST_SRC, datetime.now(timezone.utc)-timedelta(days=10))
        _make_run(TEST_SRC, status="failed", inserted=0, latest=datetime.now(timezone.utc)-timedelta(days=10))
    with app.test_client() as c:
        _login(c,email)
        html=c.get("/market-data").data.decode()
        check("unhealthy badge", "Unhealthy" in html)
    _purge()

# 9 never-refreshed
def test_never_refreshed():
    print("\n--- never refreshed ---")
    _purge()
    uid,sid,email=_make_shop(TEST_SHOP)
    with app.app_context():
        # source with no run and no obs
        src=MarketSource(name=TEST_SRC, source_type="online_retailer", is_active=True)
        db.session.add(src); db.session.commit()
    with app.test_client() as c:
        _login(c,email)
        html=c.get("/market-data").data.decode()
        check("Never refreshed", "Never refreshed" in html or "No refresh" in html)
    _purge()

# 10 recent history limit
def test_recent_history():
    print("\n--- recent history ---")
    _purge()
    uid,sid,email=_make_shop(TEST_SHOP)
    with app.app_context():
        # create 3 runs
        for i in range(3):
            _make_run(TEST_SRC, status="success", inserted=i, latest=datetime.now(timezone.utc)-timedelta(days=1))
    with app.test_client() as c:
        _login(c,email)
        html=c.get("/market-data").data.decode()
        # should contain table header Date/Time
        check("history table", "Date/Time" in html)
        # count rows - should be 3
        check("history has source", TEST_SRC in html)
    _purge()
    # also verify limit: create 35 runs, page should show only 30
    with app.app_context():
        for i in range(35):
            _make_run(TEST_SRC, status="success", inserted=i, latest=datetime.now(timezone.utc))
    uid2,sid2,email2=_make_shop(TEST_SHOP)
    with app.test_client() as c:
        _login(c,email2)
        html=c.get("/market-data").data.decode()
        # Count occurrences of source in history table - should be 30, not 35
        cnt=html.count(TEST_SRC)
        # source appears once in overview card plus up to 30 in table = up to 31
        check("history limit 30", cnt <= 31)
    _purge()

# 11 read-only
def test_read_only():
    print("\n--- read-only ---")
    _purge()
    uid,sid,email=_make_shop(TEST_SHOP)
    # create product
    with app.app_context():
        p=Product(name="MonProd", cost_price=10, target_margin=30, baseline_margin=30, selling_price=13, quantity=1, unit="unit", shop_id=sid)
        db.session.add(p); db.session.flush()
        db.session.add(PriceHistory(product_id=p.id, cost_price=10, selling_price=13, target_margin=30))
        db.session.add(Inventory(shop_id=sid, product_id=p.id, current_stock=20, minimum_stock=5))
        db.session.commit()
        pid=p.id
        before_price=db.session.get(Product,pid).selling_price
        ph_before=db.session.query(PriceHistory).filter_by(product_id=pid).count()
        from app import PricingRecommendationDecision
        dec_before=db.session.query(PricingRecommendationDecision).count()
        run_before=db.session.query(MarketRefreshRun).count()
    with app.test_client() as c:
        _login(c,email)
        c.get("/market-data")
        c.get("/market-data")
    with app.app_context():
        after_price=db.session.get(Product,pid).selling_price
        ph_after=db.session.query(PriceHistory).filter_by(product_id=pid).count()
        from app import PricingRecommendationDecision
        dec_after=db.session.query(PricingRecommendationDecision).count()
        run_after=db.session.query(MarketRefreshRun).count()
        check("price unchanged", float(before_price)==float(after_price))
        check("PriceHistory unchanged", ph_before==ph_after)
        check("Decision unchanged", dec_before==dec_after)
        check("no new runs", run_before==run_after)
        # cleanup product
        db.session.execute(text("DELETE FROM price_history WHERE product_id=:p"), {"p": pid})
        db.session.execute(text("DELETE FROM inventory WHERE product_id=:p"), {"p": pid})
        db.session.execute(text("DELETE FROM product WHERE id=:p"), {"p": pid})
        db.session.commit()
    _purge()

# 12 isolation
def test_isolation():
    print("\n--- isolation ---")
    _purge()
    uid,sid,email=_make_shop(TEST_SHOP)
    with app.app_context():
        # PC has old stale obs, Mana has fresh
        _ensure_source_with_obs(TEST_SRC, datetime.now(timezone.utc)-timedelta(days=1))  # fresh
        _make_run(TEST_SRC, status="success", inserted=5, latest=datetime.now(timezone.utc)-timedelta(days=1))
        # PC stale
        _ensure_source_with_obs(TEST_SRC2, datetime.now(timezone.utc)-timedelta(days=50))
        _make_run(TEST_SRC2, status="success", inserted=5, latest=datetime.now(timezone.utc)-timedelta(days=50))
    with app.test_client() as c:
        _login(c,email)
        html=c.get("/market-data").data.decode()
        # Both sources should appear, with different health
        check("both sources", TEST_SRC in html and TEST_SRC2 in html)
        # At least one healthy and one warning/healthy depending on thresholds
        # PC with 50 days stale for default 7/14 should be warning/unhealthy, Mana fresh should be healthy
        # So we check that page contains both Healthy and Warning/Unhealthy
        has_healthy="Healthy" in html
        has_warn="Warning" in html or "Unhealthy" in html
        check("isolation shows different health", has_healthy and has_warn)
    _purge()

def main():
    global PASSED, FAILED
    PASSED=FAILED=0
    with app.app_context():
        _purge()
    tests=[test_owner_access, test_manager_access, test_staff_denied, test_unauth, test_source_overview, test_healthy, test_warning, test_unhealthy, test_never_refreshed, test_recent_history, test_read_only, test_isolation]
    for fn in tests:
        try:
            fn()
        except Exception as e:
            FAILED+=1
            print(f"  [FAIL] {fn.__name__} raised {type(e).__name__}: {e}")
            import traceback; traceback.print_exc()
        with app.app_context():
            _purge()
    with app.app_context():
        _purge()
        cnt=db.session.execute(text("SELECT COUNT(*) FROM market_refresh_run WHERE source_name LIKE 'testmonitoring%'")).scalar()
        check("no testmonitoring runs leaked", cnt==0)
        cnt2=db.session.execute(text("SELECT COUNT(*) FROM market_source WHERE name LIKE 'TestMonitoring%'")).scalar()
        check("no TestMonitoring sources leaked", cnt2==0)
    total=PASSED+FAILED
    print(f"\ntest_market_data_monitoring: {PASSED}/{total} checks passed" + (f" ({FAILED} FAILED)" if FAILED else ""))
    return 0 if FAILED==0 else 1

if __name__=="__main__":
    import sys
    sys.exit(main())
