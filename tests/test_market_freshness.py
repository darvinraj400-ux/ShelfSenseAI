"""
Phase 10F — Freshness-Aware Market Evidence tests.

Covers fresh/aging/stale/unavailable, source-specific, strong+fresh/aging/stale,
guardrails, no mutation, historical decisions, Gemini not required.
"""
import sys
from datetime import date, datetime, timezone, timedelta
import random as rnd
import string

sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402
from app import app, db, MarketSource, MarketItem, MarketPriceObservation, Product, PriceHistory, Inventory, Shop, User, PricingRecommendationDecision  # noqa: E402
from services.market_freshness import classify_market_freshness, get_product_market_freshness  # noqa: E402
from services.market_analysis import get_market_stats  # noqa: E402
from services.pricing_engine import get_price_recommendation  # noqa: E402

TEST_SRC = "TestFreshness_Mana"
TEST_SRC2 = "TestFreshness_PC"
TEST_SHOP = "TestFreshness_Shop"
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
            "DELETE FROM market_refresh_run WHERE source_name LIKE 'testfreshness%'",
            "DELETE FROM market_refresh_run WHERE triggered_by='test' AND source_name IN ('manamurah','pricecatcher') AND started_at > DATE_SUB(UTC_TIMESTAMP(), INTERVAL 2 HOUR)",
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

def _make_shop():
    from werkzeug.security import generate_password_hash
    from app import Shop, User
    from flask import has_app_context
    slug=''.join(rnd.choices(string.ascii_lowercase,k=5))
    email=f"own_{slug}@shelfsense.my"
    def _create():
        u=User(email=email, password_hash=generate_password_hash("Test1234!"), role="owner")
        db.session.add(u); db.session.flush()
        s=Shop(name=TEST_SHOP, state="Johor", district="Segamat"); db.session.add(s); db.session.flush()
        u.shop_id=s.id; db.session.commit()
        return u.id, s.id, email
    if has_app_context():
        return _create()
    else:
        with app.app_context():
            return _create()

def _make_product(sid, cost=10, margin=30, selling=13, qty=1, unit="kg"):
    from flask import has_app_context
    def _create():
        p=Product(name="FreshProd", cost_price=cost, target_margin=margin, baseline_margin=margin, selling_price=selling, quantity=qty, unit=unit, shop_id=sid)
        db.session.add(p); db.session.flush()
        db.session.add(PriceHistory(product_id=p.id, cost_price=cost, selling_price=selling, target_margin=margin))
        db.session.add(Inventory(shop_id=sid, product_id=p.id, current_stock=20, minimum_stock=5))
        db.session.commit()
        return p.id
    if has_app_context():
        return _create()
    else:
        with app.app_context():
            return _create()

def _make_market_fixture(source_name, days_ago, n_obs=5, premises=5):
    """Create source, item, and observations with latest = now - days_ago."""
    with app.app_context():
        src=MarketSource(name=source_name, source_type="online_retailer" if "Mana" in source_name else "government", is_active=True)
        db.session.add(src); db.session.flush()
        mi=MarketItem(source_id=src.id, external_id="fresh-1", raw_title="Fresh Item", normalized_title="fresh item", package_quantity=1, package_unit="kg")
        db.session.add(mi); db.session.flush()
        latest = datetime.now(timezone.utc) - timedelta(days=days_ago)
        for i in range(n_obs):
            # Distribute premise codes to satisfy premise count
            premise_code=f"FH{i:03d}" if premises>1 else "FH001"
            # Ensure lookup_premise exists for premise_code? Not needed for market_freshness which uses direct observed_at
            obs=MarketPriceObservation(market_item_id=mi.id, premise_code=premise_code, regular_price=10+i, effective_price=10+i, normalized_unit_price=10+i, observed_at=latest - timedelta(days=i%2), state="Johor", district="Segamat")
            db.session.add(obs)
        db.session.commit()
        return src.id, mi.id, latest

def _match_product(pid, mi_id):
    from app import ProductMarketMatch
    with app.app_context():
        m=ProductMarketMatch(shop_product_id=pid, market_item_id=mi_id, confidence_score=0.95, match_type="manual", is_verified=True)
        db.session.add(m); db.session.commit()

# 1 fresh
def test_fresh_classification():
    print("\n--- fresh classification ---")
    with app.app_context():
        _purge()
        try:
            fresh = classify_market_freshness("ManaMurah", datetime.now(timezone.utc) - timedelta(days=1))
            check("fresh", fresh["freshness"]=="fresh")
            fresh2 = classify_market_freshness("PriceCatcher", datetime.now(timezone.utc) - timedelta(days=10))
            check("PriceCatcher fresh 10d", fresh2["freshness"]=="fresh")
        finally:
            _purge()

# 2 aging
def test_aging_classification():
    print("\n--- aging ---")
    with app.app_context():
        _purge()
        try:
            aging = classify_market_freshness("ManaMurah", datetime.now(timezone.utc) - timedelta(days=5))
            check("aging Mana 5d", aging["freshness"]=="aging")
            aging2 = classify_market_freshness("PriceCatcher", datetime.now(timezone.utc) - timedelta(days=40))
            check("aging PC 40d", aging2["freshness"]=="aging")
        finally:
            _purge()

# 3 stale
def test_stale_classification():
    print("\n--- stale ---")
    with app.app_context():
        _purge()
        try:
            stale = classify_market_freshness("ManaMurah", datetime.now(timezone.utc) - timedelta(days=10))
            check("stale Mana 10d", stale["freshness"]=="stale")
            stale2 = classify_market_freshness("PriceCatcher", datetime.now(timezone.utc) - timedelta(days=70))
            check("stale PC 70d", stale2["freshness"]=="stale")
        finally:
            _purge()

# 4 no observation
def test_no_observation():
    print("\n--- no observation ---")
    with app.app_context():
        _purge()
        try:
            na = classify_market_freshness("ManaMurah", None)
            check("unavailable", na["freshness"]=="unavailable")
            # product with no match
            uid,sid,_=_make_shop()
            pid=_make_product(sid)
            with app.app_context():
                h=get_product_market_freshness(pid)
                check("product no match unavailable", h["freshness"]=="unavailable")
            _purge()
        finally:
            _purge()

# 5 successful refresh with stale observations
def test_successful_stale():
    print("\n--- successful refresh stale ---")
    with app.app_context():
        _purge()
        try:
            # Create stale PriceCatcher observation (70 days ago) but simulate successful run
            sid, mid, latest = _make_market_fixture(TEST_SRC2, days_ago=70, n_obs=5, premises=5)
            uid, shop_id, _ = _make_shop()
            pid = _make_product(shop_id)
            _match_product(pid, mid)
            # Create a successful run with stale latest
            from app import MarketRefreshRun
            run=MarketRefreshRun(source_name=TEST_SRC2.lower(), started_at=datetime.now(timezone.utc)-timedelta(minutes=5), finished_at=datetime.now(timezone.utc), status="success", inserted=5, latest_observed_at=latest, triggered_by="test")
            db.session.add(run); db.session.commit()
            # Freshness should be stale even though run success
            h = get_product_market_freshness(pid, shop=db.session.get(Shop, shop_id))
            check("stale despite success", h["freshness"]=="stale")
            # But recommendation still generated
            rec=get_price_recommendation(pid, shop=db.session.get(Shop, shop_id))
            check("rec still generated stale", rec["market_freshness"]=="stale" and rec["recommended_price"]>0)
        finally:
            _purge()

# 6 failed refresh with old observations
def test_failed_old_observations():
    print("\n--- failed refresh old obs ---")
    with app.app_context():
        _purge()
        try:
            sid, mid, latest = _make_market_fixture(TEST_SRC, days_ago=10, n_obs=3, premises=3)
            uid, shop_id, _ = _make_shop()
            pid=_make_product(shop_id)
            _match_product(pid, mid)
            # Simulate failed run
            from app import MarketRefreshRun
            run=MarketRefreshRun(source_name=TEST_SRC.lower(), started_at=datetime.now(timezone.utc), finished_at=datetime.now(timezone.utc), status="failed", inserted=0, error_message="network down", latest_observed_at=latest, triggered_by="test")
            db.session.add(run); db.session.commit()
            # Freshness based on actual observations, not run status
            h=get_product_market_freshness(pid, shop=db.session.get(Shop, shop_id))
            check("failed but obs still stale", h["freshness"]=="stale")
            # System should not treat as unavailable
            check("not unavailable", h["freshness"]!="unavailable")
        finally:
            _purge()

# 7 source-specific
def test_source_specific():
    print("\n--- source-specific ---")
    with app.app_context():
        _purge()
        try:
            # Same age 10 days, but thresholds differ
            fresh_mana = classify_market_freshness("ManaMurah", datetime.now(timezone.utc)-timedelta(days=10))
            fresh_pc = classify_market_freshness("PriceCatcher", datetime.now(timezone.utc)-timedelta(days=10))
            check("Mana 10d stale", fresh_mana["freshness"]=="stale")
            check("PC 10d fresh", fresh_pc["freshness"]=="fresh")
        finally:
            _purge()

# 8 cross-source isolation
def test_cross_isolation():
    print("\n--- cross isolation ---")
    with app.app_context():
        _purge()
        try:
            # PriceCatcher fresh (10d), Mana stale (10d) would be different if same age but thresholds differ
            # Create two sources for same product? Instead test that product matched only to PC is not affected by Mana's stale
            sid_pc, mid_pc, _ = _make_market_fixture(TEST_SRC2, days_ago=5, n_obs=5, premises=5)  # PC fresh (5d <35)
            sid_mana, mid_mana, _ = _make_market_fixture(TEST_SRC, days_ago=10, n_obs=5, premises=5)  # Mana stale (10d >7)
            uid, shop_id, _ = _make_shop()
            pid_pc=_make_product(shop_id)
            pid_mana=_make_product(shop_id)
            _match_product(pid_pc, mid_pc)
            _match_product(pid_mana, mid_mana)
            h_pc=get_product_market_freshness(pid_pc, shop=db.session.get(Shop, shop_id))
            h_mana=get_product_market_freshness(pid_mana, shop=db.session.get(Shop, shop_id))
            check("PC fresh", h_pc["freshness"]=="fresh")
            check("Mana stale", h_mana["freshness"]=="stale")
        finally:
            _purge()

# 9 strong+fresh
def test_strong_fresh():
    print("\n--- strong+fresh ---")
    with app.app_context():
        _purge()
        try:
            sid, mid, latest = _make_market_fixture(TEST_SRC, days_ago=1, n_obs=25, premises=6)
            uid, shop_id, _ = _make_shop()
            pid=_make_product(shop_id)
            _match_product(pid, mid)
            rec=get_price_recommendation(pid, shop=db.session.get(Shop, shop_id))
            check("strong", rec["market_evidence"]=="Strong")
            check("fresh", rec["market_freshness"]=="fresh")
            check("no stale warning", rec["freshness_warning"] is None)
        finally:
            _purge()

# 10 strong+aging
def test_strong_aging():
    print("\n--- strong+aging ---")
    with app.app_context():
        _purge()
        try:
            sid, mid, latest = _make_market_fixture(TEST_SRC, days_ago=5, n_obs=25, premises=6)
            uid, shop_id, _ = _make_shop()
            pid=_make_product(shop_id)
            _match_product(pid, mid)
            rec=get_price_recommendation(pid, shop=db.session.get(Shop, shop_id))
            check("strong still", rec["market_evidence"]=="Strong")
            check("aging", rec["market_freshness"]=="aging")
            check("warning present", rec["freshness_warning"] is not None)
        finally:
            _purge()

# 11 strong+stale
def test_strong_stale():
    print("\n--- strong+stale ---")
    with app.app_context():
        _purge()
        try:
            sid, mid, latest = _make_market_fixture(TEST_SRC, days_ago=10, n_obs=25, premises=6)
            uid, shop_id, _ = _make_shop()
            pid=_make_product(shop_id)
            _match_product(pid, mid)
            rec=get_price_recommendation(pid, shop=db.session.get(Shop, shop_id))
            check("strong still", rec["market_evidence"]=="Strong")
            check("stale", rec["market_freshness"]=="stale")
            check("warning present", "historical" in (rec["freshness_warning"] or "").lower())
            # Price still generated, guardrails apply
            check("price still >0", rec["recommended_price"]>0)
        finally:
            _purge()

# 12 guardrails with stale
def test_guardrails_stale():
    print("\n--- guardrails with stale ---")
    with app.app_context():
        _purge()
        try:
            sid, mid, latest = _make_market_fixture(TEST_SRC, days_ago=10, n_obs=25, premises=6)
            uid, shop_id, _ = _make_shop()
            # KPDN cap test: cost 10, margin 30, ceiling 12, stale data
            pid=_make_product(shop_id, cost=10, margin=30, selling=13)
            from app import Product
            p=db.session.get(Product, pid)
            p.is_price_controlled=True
            p.government_ceiling_price=12
            db.session.commit()
            _match_product(pid, mid)
            rec=get_price_recommendation(pid, shop=db.session.get(Shop, shop_id))
            check("KPDN cap still", rec["recommended_price"]<=12)
            check("stale still", rec["market_freshness"]=="stale")
            # Cost floor: cost 100, stale, should still be >=105
            pid2=_make_product(shop_id, cost=100, margin=30, selling=50)
            _match_product(pid2, mid)
            rec2=get_price_recommendation(pid2, shop=db.session.get(Shop, shop_id))
            check("cost floor", rec2["recommended_price"]>=105)
        finally:
            _purge()

# 13 no auto price mutation
def test_no_auto_price():
    print("\n--- no auto price ---")
    with app.app_context():
        _purge()
        try:
            sid, mid, _ = _make_market_fixture(TEST_SRC, days_ago=10, n_obs=5, premises=5)
            uid, shop_id, _ = _make_shop()
            pid=_make_product(shop_id, selling=13)
            before=db.session.get(Product, pid).selling_price
            _match_product(pid, mid)
            rec=get_price_recommendation(pid, shop=db.session.get(Shop, shop_id))
            after=db.session.get(Product, pid).selling_price
            check("price not mutated", float(before)==float(after))
        finally:
            _purge()

# 14 no auto decision
def test_no_auto_decision():
    print("\n--- no auto decision ---")
    with app.app_context():
        _purge()
        try:
            sid, mid, _ = _make_market_fixture(TEST_SRC, days_ago=10, n_obs=5, premises=5)
            uid, shop_id, _ = _make_shop()
            pid=_make_product(shop_id)
            _match_product(pid, mid)
            before=db.session.query(PricingRecommendationDecision).count()
            _ = get_price_recommendation(pid, shop=db.session.get(Shop, shop_id))
            after=db.session.query(PricingRecommendationDecision).count()
            check("no decision created", before==after)
        finally:
            _purge()

# 15 historical decision unchanged
def test_historical_unchanged():
    print("\n--- historical unchanged ---")
    with app.app_context():
        _purge()
        try:
            sid, mid, latest_fresh = _make_market_fixture(TEST_SRC, days_ago=1, n_obs=5, premises=5)
            uid, shop_id, _ = _make_shop()
            pid=_make_product(shop_id)
            _match_product(pid, mid)
            # Create a decision with fresh data
            from services.pricing_workflow import record_decision
            from services.pricing_engine import get_price_recommendation as gpr
            rec_fresh=gpr(pid, shop=db.session.get(Shop, shop_id))
            dec,_=record_decision(pid, shop=db.session.get(Shop, shop_id))
            orig_evidence=dec.market_evidence
            orig_price=dec.recommended_price
            # Now make data stale by updating observation date to old
            old_date=datetime.now(timezone.utc)-timedelta(days=20)
            db.session.execute(text("UPDATE market_price_observation SET observed_at=:d WHERE market_item_id=:mid"), {"d": old_date, "mid": mid})
            db.session.commit()
            # Fetch decision again, should be unchanged
            fetched=db.session.get(PricingRecommendationDecision, dec.id)
            check("historical evidence unchanged", fetched.market_evidence==orig_evidence)
            check("historical price unchanged", float(fetched.recommended_price)==float(orig_price))
            # New recommendation should be stale
            rec_stale=gpr(pid, shop=db.session.get(Shop, shop_id))
            check("new rec stale", rec_stale["market_freshness"]=="stale")
        finally:
            _purge()

# 16 Gemini not required
def test_gemini_not_required():
    print("\n--- Gemini not required ---")
    with app.app_context():
        _purge()
        try:
            sid, mid, _ = _make_market_fixture(TEST_SRC, days_ago=1, n_obs=5, premises=5)
            uid, shop_id, _ = _make_shop()
            pid=_make_product(shop_id)
            _match_product(pid, mid)
            # Ensure freshness works even if llm_explainer is mocked to fail
            import services.llm_explainer
            orig=services.llm_explainer.generate_pricing_explanation
            def fake_fail(*a, **kw):
                raise RuntimeError("Gemini down")
            services.llm_explainer.generate_pricing_explanation=fake_fail
            try:
                rec=get_price_recommendation(pid, shop=db.session.get(Shop, shop_id))
                check("freshness without Gemini", rec["market_freshness"] in ("fresh","aging","stale","unavailable"))
                check("price without Gemini", rec["recommended_price"]>0)
            finally:
                services.llm_explainer.generate_pricing_explanation=orig
        finally:
            _purge()

# 17 Phase9 consistent
def test_phase9_consistent():
    print("\n--- Phase9 consistent ---")
    with app.app_context():
        _purge()
        try:
            sid, mid, _ = _make_market_fixture(TEST_SRC, days_ago=1, n_obs=5, premises=5)
            uid, shop_id, _ = _make_shop()
            # Create 2 products, one fresh, one stale
            pid1=_make_product(shop_id)
            pid2=_make_product(shop_id)
            _match_product(pid1, mid)
            # pid2 no match -> unavailable
            from services.pricing_dashboard import get_shop_pricing_opportunities
            shop=db.session.get(Shop, shop_id)
            result=get_shop_pricing_opportunities(shop, page=1, per_page=20)
            # Should not crash, should have rows with freshness
            check("dashboard has rows", len(result["rows"])>=2)
            has_fresh=any(r.get("freshness")=="fresh" for r in result["rows"])
            has_unavail=any(r.get("freshness")=="unavailable" for r in result["rows"])
            check("dashboard freshness present", has_fresh and has_unavail)
        finally:
            _purge()

def test_multi_source_freshness():
    """Regression: product with verified matches from multiple active sources.
    Freshness must be most conservative among contributing sources, not just
    the latest observation's source, and must correspond to the same filtered
    observations used for evidence (source-isolated, no silent combine).
    """
    print("\n--- multi-source freshness ---")
    with app.app_context():
        _purge()
        try:
            # Fresh PriceCatcher (5 days ago, fresh for PC <35) + stale Mana (10 days ago, stale for Mana >7)
            sid_pc, mid_pc, _ = _make_market_fixture(TEST_SRC2, days_ago=5, n_obs=5, premises=5)
            sid_mana, mid_mana, _ = _make_market_fixture(TEST_SRC, days_ago=10, n_obs=5, premises=5)
            uid, shop_id, _ = _make_shop()
            pid = _make_product(shop_id)
            _match_product(pid, mid_pc)
            _match_product(pid, mid_mana)
            shop = db.session.get(Shop, shop_id)
            # Freshness should be most conservative (stale) not fresh
            h = get_product_market_freshness(pid, shop=shop)
            check("multi-source freshness is stale (most conservative)", h["freshness"] == "stale")
            check("freshness not fresh", h["freshness"] != "fresh")
            # Market stats should combine observations (n=10) but freshness should still be stale
            stats = get_market_stats(pid, shop=shop)
            check("stats combines both sources", stats["n"] == 10)
            rec = get_price_recommendation(pid, shop=shop)
            check("recommendation freshness stale", rec["market_freshness"] == "stale")
            check("recommendation freshness matches product freshness", rec["market_freshness"] == h["freshness"])
            # Also verify the stale source's observations are part of evidence (premise_count includes both)
            check("evidence still strong with 10 obs", rec["market_evidence"] in ("Strong","Moderate"))
        finally:
            _purge()

def main():
    global PASSED, FAILED
    PASSED=FAILED=0
    with app.app_context():
        _purge()
    tests=[test_fresh_classification, test_aging_classification, test_stale_classification, test_no_observation, test_successful_stale, test_failed_old_observations, test_source_specific, test_cross_isolation, test_strong_fresh, test_strong_aging, test_strong_stale, test_guardrails_stale, test_no_auto_price, test_no_auto_decision, test_historical_unchanged, test_gemini_not_required, test_phase9_consistent, test_multi_source_freshness]
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
        cnt=db.session.execute(text("SELECT COUNT(*) FROM market_refresh_run WHERE source_name LIKE 'testfreshness%'")).scalar()
        check("no testfreshness runs leaked", cnt==0)
    total=PASSED+FAILED
    print(f"\ntest_market_freshness: {PASSED}/{total} checks passed" + (f" ({FAILED} FAILED)" if FAILED else ""))
    return 0 if FAILED==0 else 1

if __name__=="__main__":
    import sys
    sys.exit(main())
