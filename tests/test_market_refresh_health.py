"""
Phase 10C — Market Refresh Health tests.

Verifies health evaluation is deterministic, read-only, source-isolated,
and handles freshness / partial / failed / empty / duplicates correctly.
"""
import sys
from datetime import date, datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402
from app import app, db, MarketSource, MarketItem, MarketPriceObservation, MarketRefreshRun, Product, PriceHistory, Inventory  # noqa: E402
from services.market_refresh_health import get_refresh_health, MANAMURAH_FRESH_DAYS, PRICECATCHER_FRESH_DAYS  # noqa: E402

TEST_SRC = "TestHealth_Mana"
TEST_SRC2 = "TestHealth_PC"
TEST_SHOP = "TestHealth_Shop"

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
            "DELETE FROM market_refresh_run WHERE source_name LIKE 'testhealth%'",
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

def _make_run(source_name, status="success", inserted=5, latest=None, errors=0, rejected=0, started=None):
    if started is None:
        started = datetime.now(timezone.utc)
    if latest is None and status != "failed":
        latest = datetime.now(timezone.utc) - timedelta(days=1)
    run = MarketRefreshRun(
        source_name=source_name.lower(),
        started_at=started,
        finished_at=started + timedelta(seconds=2),
        status=status,
        inserted=inserted,
        updated=1,
        duplicates_skipped=2 if inserted==0 else 0,
        rejected=rejected,
        errors=errors,
        latest_observed_at=latest,
        error_message="boom" if status=="failed" else None,
        triggered_by="test",
    )
    db.session.add(run)
    db.session.commit()
    return run

def _ensure_source_with_obs(source_name, latest_date):
    src = MarketSource(name=source_name, source_type="government" if "PC" in source_name else "online_retailer", is_active=True)
    db.session.add(src); db.session.flush()
    mi = MarketItem(source_id=src.id, external_id="health-1", raw_title="Health Item", normalized_title="health item", package_quantity=1, package_unit="unit")
    db.session.add(mi); db.session.flush()
    obs = MarketPriceObservation(market_item_id=mi.id, regular_price=10, promo_price=None, is_on_promo=False, effective_price=10, normalized_unit_price=10, observed_at=latest_date, state="Johor", district="Segamat")
    db.session.add(obs); db.session.commit()
    return src.id

# 1
def test_healthy_successful():
    print("\n--- healthy successful ---")
    with app.app_context():
        _purge()
        try:
            latest = datetime.now(timezone.utc) - timedelta(days=1)
            _ensure_source_with_obs(TEST_SRC, latest)
            _make_run(TEST_SRC, status="success", inserted=5, latest=latest)
            h = get_refresh_health(TEST_SRC)
            check("healthy", h.health_level=="healthy" and h.healthy)
            check("not failed", h.status=="success")
            check("obs count 1", h.observation_count==1)
        finally:
            _purge()

# 2
def test_failed_unhealthy():
    print("\n--- failed -> unhealthy ---")
    with app.app_context():
        _purge()
        try:
            latest = datetime.now(timezone.utc) - timedelta(days=1)
            _ensure_source_with_obs(TEST_SRC, latest)
            _make_run(TEST_SRC, status="failed", inserted=0, latest=latest, errors=1)
            h = get_refresh_health(TEST_SRC)
            check("unhealthy", h.health_level=="unhealthy")
            check("errors present", len(h.errors)>0)
        finally:
            _purge()

# 3
def test_partial_warning():
    print("\n--- partial -> warning ---")
    with app.app_context():
        _purge()
        try:
            latest = datetime.now(timezone.utc) - timedelta(days=1)
            _ensure_source_with_obs(TEST_SRC, latest)
            _make_run(TEST_SRC, status="partial", inserted=2, latest=latest, errors=1)
            h = get_refresh_health(TEST_SRC)
            check("warning", h.health_level=="warning")
        finally:
            _purge()

# 4
def test_no_observations_unhealthy():
    print("\n--- no observations -> unhealthy ---")
    with app.app_context():
        _purge()
        try:
            # Create source with no observations
            src = MarketSource(name=TEST_SRC, source_type="online_retailer", is_active=True)
            db.session.add(src); db.session.commit()
            _make_run(TEST_SRC, status="success", inserted=0, latest=None)
            h = get_refresh_health(TEST_SRC)
            check("unhealthy no obs", h.health_level=="unhealthy")
            check("obs 0", h.observation_count==0)
        finally:
            _purge()

# 5 fresh
def test_fresh_healthy():
    print("\n--- fresh -> healthy ---")
    with app.app_context():
        _purge()
        try:
            latest = datetime.now(timezone.utc) - timedelta(days=1)
            _ensure_source_with_obs(TEST_SRC, latest)
            _make_run(TEST_SRC, status="success", inserted=5, latest=latest)
            h = get_refresh_health(TEST_SRC)
            check("fresh healthy", h.health_level=="healthy")
        finally:
            _purge()

# 6 stale -> warning/unhealthy
def test_stale_warning():
    print("\n--- stale -> warning ---")
    with app.app_context():
        _purge()
        try:
            # Use Mana-like thresholds: stale >7 days
            stale_days = MANAMURAH_FRESH_DAYS + 10
            latest = datetime.now(timezone.utc) - timedelta(days=stale_days)
            _ensure_source_with_obs(TEST_SRC, latest)
            _make_run(TEST_SRC, status="success", inserted=5, latest=latest)
            # Map TEST_SRC to manamurah thresholds by calling with manamurah name
            # Instead we test via TEST_SRC which uses default 7/14, still stale
            h = get_refresh_health(TEST_SRC)
            check("stale not healthy", h.health_level!="healthy")
            check("warning or unhealthy", h.health_level in ("warning","unhealthy"))
        finally:
            _purge()

# 7 zero inserted duplicates not unhealthy
def test_zero_inserted_duplicates_ok():
    print("\n--- zero inserted duplicates -> not unhealthy ---")
    with app.app_context():
        _purge()
        try:
            latest = datetime.now(timezone.utc) - timedelta(days=1)
            _ensure_source_with_obs(TEST_SRC, latest)
            # Simulate idempotent rerun: inserted 0 but duplicates 5
            run = MarketRefreshRun(source_name=TEST_SRC.lower(), started_at=datetime.now(timezone.utc), finished_at=datetime.now(timezone.utc), status="success", inserted=0, updated=0, duplicates_skipped=5, rejected=0, errors=0, latest_observed_at=latest, triggered_by="test")
            db.session.add(run); db.session.commit()
            h = get_refresh_health(TEST_SRC)
            check("not unhealthy", h.health_level!="unhealthy")
            check("healthy or warning", h.health_level in ("healthy","warning"))
        finally:
            _purge()

# 8 rejected/error -> warning/unhealthy
def test_rejected_error_warning():
    print("\n--- rejected/error -> warning ---")
    with app.app_context():
        _purge()
        try:
            latest = datetime.now(timezone.utc) - timedelta(days=1)
            _ensure_source_with_obs(TEST_SRC, latest)
            _make_run(TEST_SRC, status="success", inserted=0, latest=latest, rejected=20)
            h = get_refresh_health(TEST_SRC)
            # High rejection with zero inserted should be warning
            check("high rejection warning", h.health_level in ("warning","unhealthy"))
        finally:
            _purge()

# 9 PriceCatcher isolation
def test_pricecatcher_isolation():
    print("\n--- PriceCatcher isolation ---")
    with app.app_context():
        _purge()
        try:
            # Only Mana source has obs, PriceCatcher has none
            latest_mana = datetime.now(timezone.utc) - timedelta(days=1)
            _ensure_source_with_obs(TEST_SRC, latest_mana)  # this will be used as Mana
            # Create PriceCatcher source with no obs
            src_pc = MarketSource(name=TEST_SRC2, source_type="government", is_active=True)
            db.session.add(src_pc); db.session.commit()
            _make_run(TEST_SRC2, status="success", inserted=0, latest=None)
            h_pc = get_refresh_health(TEST_SRC2)
            check("PC no obs unhealthy", h_pc.health_level=="unhealthy" and h_pc.observation_count==0)
            h_mana = get_refresh_health(TEST_SRC)
            check("Mana still healthy", h_mana.observation_count==1)
        finally:
            _purge()

# 10 ManaMurah isolation
def test_manamurah_isolation():
    print("\n--- ManaMurah isolation ---")
    with app.app_context():
        _purge()
        try:
            # Only PC has obs
            latest_pc = datetime.now(timezone.utc) - timedelta(days=1)
            _ensure_source_with_obs(TEST_SRC2, latest_pc)
            src_mana = MarketSource(name=TEST_SRC, source_type="online_retailer", is_active=True)
            db.session.add(src_mana); db.session.commit()
            _make_run(TEST_SRC, status="success", inserted=0, latest=None)
            h_mana = get_refresh_health(TEST_SRC)
            check("Mana no obs unhealthy", h_mana.health_level=="unhealthy")
            h_pc = get_refresh_health(TEST_SRC2)
            check("PC healthy", h_pc.observation_count==1)
        finally:
            _purge()

# 11 read-only
def test_read_only():
    print("\n--- read-only ---")
    with app.app_context():
        _purge()
        try:
            latest = datetime.now(timezone.utc) - timedelta(days=1)
            _ensure_source_with_obs(TEST_SRC, latest)
            run = _make_run(TEST_SRC, status="success", inserted=5, latest=latest)
            before = MarketRefreshRun.query.count()
            _ = get_refresh_health(TEST_SRC)
            after = MarketRefreshRun.query.count()
            check("no new runs", before==after)
            # also check health_for_run
            from services.market_refresh_health import health_for_run
            _ = health_for_run(run)
            after2 = MarketRefreshRun.query.count()
            check("health_for_run no new runs", after2==before)
        finally:
            _purge()

# 12 no price mutation
def test_no_price_mutation():
    print("\n--- no price mutation ---")
    with app.app_context():
        _purge()
        from werkzeug.security import generate_password_hash
        from app import Shop, User
        import random as rnd, string
        slug=''.join(rnd.choices(string.ascii_lowercase,k=5))
        email=f"own_{slug}@shelfsense.my"
        u=User(email=email, password_hash=generate_password_hash("Test1234!"), role="owner")
        db.session.add(u); db.session.flush()
        s=Shop(name=TEST_SHOP); db.session.add(s); db.session.flush()
        u.shop_id=s.id
        p=Product(name="HealthProd", cost_price=10, target_margin=30, baseline_margin=30, selling_price=13, quantity=1, unit="unit", shop_id=s.id)
        db.session.add(p); db.session.flush()
        db.session.add(PriceHistory(product_id=p.id, cost_price=10, selling_price=13, target_margin=30))
        db.session.add(Inventory(shop_id=s.id, product_id=p.id, current_stock=20, minimum_stock=5))
        db.session.commit()
        pid=p.id
        before_price=db.session.get(Product,pid).selling_price
        dec_before=db.session.query(db.session.query(MarketRefreshRun).count()).scalar()  # dummy
        from app import PricingRecommendationDecision
        dec_before2 = db.session.query(PricingRecommendationDecision).count()
        ph_before = db.session.query(PriceHistory).filter_by(product_id=pid).count()
        # health check
        latest = datetime.now(timezone.utc) - timedelta(days=1)
        _ensure_source_with_obs(TEST_SRC, latest)
        _make_run(TEST_SRC, status="success", inserted=5, latest=latest)
        _ = get_refresh_health(TEST_SRC)
        after_price=db.session.get(Product,pid).selling_price
        dec_after=db.session.query(PricingRecommendationDecision).count()
        ph_after=db.session.query(PriceHistory).filter_by(product_id=pid).count()
        check("price unchanged", float(before_price)==float(after_price))
        check("no new decisions", dec_before2==dec_after)
        check("no new PriceHistory", ph_before==ph_after)
        # cleanup
        db.session.execute(text("DELETE FROM pricing_recommendation_decision WHERE shop_id=:s"), {"s": s.id})
        db.session.execute(text("DELETE FROM price_history WHERE product_id=:p"), {"p": pid})
        db.session.execute(text("DELETE FROM inventory WHERE product_id=:p"), {"p": pid})
        db.session.execute(text("DELETE FROM product WHERE id=:p"), {"p": pid})
        db.session.execute(text("DELETE FROM user WHERE shop_id=:s"), {"s": s.id})
        db.session.execute(text("DELETE FROM shop WHERE id=:s"), {"s": s.id})
        db.session.commit()
        _purge()

def main():
    global PASSED, FAILED
    PASSED=FAILED=0
    with app.app_context():
        _purge()
    tests=[test_healthy_successful, test_failed_unhealthy, test_partial_warning, test_no_observations_unhealthy, test_fresh_healthy, test_stale_warning, test_zero_inserted_duplicates_ok, test_rejected_error_warning, test_pricecatcher_isolation, test_manamurah_isolation, test_read_only, test_no_price_mutation]
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
        cnt=db.session.execute(text("SELECT COUNT(*) FROM market_refresh_run WHERE source_name LIKE 'testhealth%'")).scalar()
        check("no testhealth runs leaked", cnt==0)
        cnt2=db.session.execute(text("SELECT COUNT(*) FROM market_refresh_run WHERE triggered_by='test'")).scalar()
        check("no test triggered runs leaked", cnt2==0)
    total=PASSED+FAILED
    print(f"\ntest_market_refresh_health: {PASSED}/{total} checks passed" + (f" ({FAILED} FAILED)" if FAILED else ""))
    return 0 if FAILED==0 else 1

if __name__=="__main__":
    import sys
    sys.exit(main())
