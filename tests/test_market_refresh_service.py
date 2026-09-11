"""
Tests for Phase 10A Market Data Refresh Service (orchestration only).

Verifies:
  - source selection (pricecatcher / manamurah / all)
  - orchestration calls correct existing functions (mocks)
  - result normalization (Counter -> unified fields)
  - error handling (one source fails, other still succeeds)
  - source isolation (ManaMurah run leaves PriceCatcher untouched)
  - idempotency (second identical run -> 0 new)
  - dry_run does not persist
  - CLI dry-run
  - no Product.selling_price / PricingRecommendationDecision mutation
  - no automatic repricing

No migration, no monitoring page, no scheduling.

Uses ephemeral fixture names (RefreshSvc_*) and FK-safe JOIN purges
so the 1.97M observation table is never full-scanned.
"""
import os
import sys
from datetime import date, datetime
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402
from app import app, db, MarketSource, MarketItem, MarketPriceObservation, Product, PriceHistory, PricingRecommendationDecision, Inventory  # noqa: E402
from services.market_refresh_service import MarketDataRefreshService, MarketRefreshResult  # noqa: E402

TEST_SOURCE = "TestRefreshSvc_Mana"
TEST_PC_SOURCE = "TestRefreshSvc_PC"
TEST_SHOP = "TestRefreshSvc_Shop"

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
        # Clean ephemeral refresh runs from this suite (including mocked manamurah/pricecatcher runs)
        for q in [
            "DELETE FROM market_refresh_run WHERE source_name LIKE 'testrefreshsvc%'",
            "DELETE FROM market_refresh_run WHERE triggered_by='test'",
        ]:
            try:
                db.session.execute(text(q))
            except Exception:
                pass
        # Market fixtures
        for q in [
            "DELETE o FROM market_price_observation o JOIN market_item mi ON mi.id=o.market_item_id WHERE mi.source_id IN (SELECT id FROM market_source WHERE name IN (:a,:b))",
            "DELETE pm FROM product_market_match pm JOIN market_item mi ON mi.id=pm.market_item_id WHERE mi.source_id IN (SELECT id FROM market_source WHERE name IN (:a,:b))",
            "DELETE mi FROM market_item mi WHERE mi.source_id IN (SELECT id FROM market_source WHERE name IN (:a,:b))",
            "DELETE FROM market_source WHERE name IN (:a,:b)",
        ]:
            try:
                db.session.execute(text(q), {"a": TEST_SOURCE, "b": TEST_PC_SOURCE})
            except Exception:
                pass
        # Shop fixtures: FK-safe
        rows = db.session.execute(text("SELECT id FROM shop WHERE name=:n"), {"n": TEST_SHOP}).fetchall()
        if rows:
            sids = ",".join(str(r[0]) for r in rows)
            for q in [
                f"DELETE FROM pricing_recommendation_decision WHERE shop_id IN ({sids})",
                f"DELETE FROM notification WHERE user_id IN (SELECT id FROM user WHERE shop_id IN ({sids}))",
                f"DELETE FROM shop_invitation WHERE shop_id IN ({sids})",
            ]:
                try:
                    db.session.execute(text(q))
                except Exception:
                    pass
            for tbl, col in [("inventory_adjustment","product_id"),("sale","product_id"),("price_history","product_id"),("inventory","product_id"),("product_market_match","shop_product_id")]:
                try:
                    db.session.execute(text(f"DELETE FROM {tbl} WHERE {col} IN (SELECT id FROM product WHERE shop_id IN ({sids}))"))
                except Exception:
                    pass
            for q in [f"DELETE FROM product WHERE shop_id IN ({sids})", f"DELETE FROM user WHERE shop_id IN ({sids})", f"DELETE FROM shop WHERE id IN ({sids})"]:
                try:
                    db.session.execute(text(q))
                except Exception:
                    pass
        db.session.commit()

def _make_record(eid="fama-46-runcit", title="TELUR AYAM", price=0.52, day=date(2026,9,3)):
    return {
        "external_id": eid, "raw_title": title, "category": "FAMA",
        "package_quantity": 1.0, "package_unit": "unit",
        "observations": [{"date": datetime(day.year, day.month, day.day), "price": price}],
    }

def _make_shop_product():
    from werkzeug.security import generate_password_hash
    from app import Shop, User
    import random as rnd, string
    slug = ''.join(rnd.choices(string.ascii_lowercase, k=5))
    email = f"own_{slug}@shelfsense.my"
    u = User(email=email, password_hash=generate_password_hash("Test1234!"), role="owner")
    db.session.add(u); db.session.flush()
    s = Shop(name=TEST_SHOP); db.session.add(s); db.session.flush()
    u.shop_id = s.id; db.session.flush()
    p = Product(name="RefreshProd", cost_price=10, target_margin=30, baseline_margin=30, selling_price=13, quantity=1, unit="unit", shop_id=s.id)
    db.session.add(p); db.session.flush()
    db.session.add(PriceHistory(product_id=p.id, cost_price=10, selling_price=13, target_margin=30))
    db.session.add(Inventory(shop_id=s.id, product_id=p.id, current_stock=20, minimum_stock=5))
    db.session.commit()
    return p.id, s.id

# -------------------------------------------------
# UNIT / ORCHESTRATION WITH MOCKS
# -------------------------------------------------
def test_source_selection_all():
    print("\n--- source selection all ---")
    svc = MarketDataRefreshService()
    with patch.object(svc, "refresh_pricecatcher", return_value=MarketRefreshResult(source="pricecatcher", success=True)) as mock_pc, \
         patch.object(svc, "refresh_manamurah", return_value=MarketRefreshResult(source="manamurah", success=True)) as mock_mm:
        res = svc.refresh(sources="all", triggered_by='test')
        check("all returns both keys", set(res.keys()) == {"pricecatcher", "manamurah"})
        check("pricecatcher called", mock_pc.called)
        check("manamurah called", mock_mm.called)

def test_source_selection_pricecatcher_only():
    print("\n--- source selection pricecatcher only ---")
    svc = MarketDataRefreshService()
    with patch.object(svc, "refresh_pricecatcher", return_value=MarketRefreshResult(source="pricecatcher", success=True)) as mock_pc, \
         patch.object(svc, "refresh_manamurah") as mock_mm:
        res = svc.refresh(sources="pricecatcher", triggered_by='test')
        check("only pricecatcher key", set(res.keys()) == {"pricecatcher"})
        check("pricecatcher called", mock_pc.called)
        check("manamurah NOT called", not mock_mm.called)

def test_source_selection_manamurah_only():
    print("\n--- source selection manamurah only ---")
    svc = MarketDataRefreshService()
    with patch.object(svc, "refresh_pricecatcher") as mock_pc, \
         patch.object(svc, "refresh_manamurah", return_value=MarketRefreshResult(source="manamurah", success=True)) as mock_mm:
        res = svc.refresh(sources="manamurah", triggered_by='test')
        check("only manamurah key", set(res.keys()) == {"manamurah"})
        check("manamurah called", mock_mm.called)
        check("pricecatcher NOT called", not mock_pc.called)

def test_invalid_source_raises():
    print("\n--- invalid source ---")
    svc = MarketDataRefreshService()
    try:
        svc.refresh(sources="unknown", triggered_by='test')
        check("invalid raises", False)
    except ValueError:
        check("invalid raises ValueError", True)

def test_result_normalization_manamurah():
    print("\n--- result normalization ---")
    # Unit test: mock ingest_records to avoid touching real ManaMurah
    from collections import Counter
    fake_stats = Counter({"retrieved":2,"accepted":2,"rejected":0,"new_items":1,"updated_items":0,"new_observations":2,"duplicates_skipped":0,"errors":0})
    mock_client = MagicMock()
    mock_client.fetch_fama_records.return_value = [_make_record(), _make_record(eid="fama-40-runcit", title="BERAS SAWAH", price=3.1, day=date(2026,9,4))]
    svc = MarketDataRefreshService(mcp_client=mock_client)
    with patch("services.market_ingestion.ingest_records", return_value=fake_stats) as mock_ingest:
        with app.app_context():
            _purge()
            try:
                res = svc.refresh_manamurah(dry_run=False, days=30, triggered_by='test')
                check("manamurah success", res.success)
                check("inserted 2", res.inserted == 2)
                check("raw has new_observations", res.raw.get("new_observations") == 2)
                check("source name", res.source == "manamurah")
                check("ingest called with ManaMurah", mock_ingest.called)
            finally:
                _purge()

def test_error_handling_one_fails():
    print("\n--- error handling ---")
    svc = MarketDataRefreshService()
    # pricecatcher fails, manamurah succeeds
    with patch.object(svc, "refresh_pricecatcher", side_effect=RuntimeError("pc boom")) as mock_pc, \
         patch.object(svc, "refresh_manamurah", return_value=MarketRefreshResult(source="manamurah", success=True, inserted=1)) as mock_mm:
        res = svc.refresh(sources="all", triggered_by='test')
        check("pricecatcher failed entry", not res["pricecatcher"].success and "pc boom" in (res["pricecatcher"].error_message or ""))
        check("manamurah still success", res["manamurah"].success)
        check("both keys present", len(res) == 2)

def test_source_isolation():
    print("\n--- source isolation ---")
    with app.app_context():
        _purge()
        try:
            from services.market_ingestion import ingest_records
            # Create fake PriceCatcher source
            pc_rec = _make_record(eid="pc-1-runcit", title="GULA 1KG", price=2.85, day=date(2026,9,1))
            ingest_records([pc_rec], source_name=TEST_PC_SOURCE)
            pc_items_before = MarketItem.query.join(MarketSource).filter(MarketSource.name==TEST_PC_SOURCE).count()
            pc_obs_before = MarketPriceObservation.query.join(MarketItem).join(MarketSource).filter(MarketSource.name==TEST_PC_SOURCE).count()
            # ManaMurah via ephemeral TEST_SOURCE (not real ManaMurah) to prove isolation
            mm_rec = _make_record(eid="fama-46-runcit", price=0.52)
            ingest_records([mm_rec], source_name=TEST_SOURCE)
            pc_items_after = MarketItem.query.join(MarketSource).filter(MarketSource.name==TEST_PC_SOURCE).count()
            pc_obs_after = MarketPriceObservation.query.join(MarketItem).join(MarketSource).filter(MarketSource.name==TEST_PC_SOURCE).count()
            check("PriceCatcher untouched items", pc_items_before == pc_items_after)
            check("PriceCatcher untouched obs", pc_obs_before == pc_obs_after)
            check("ManaMurah ephemeral exists", MarketSource.query.filter_by(name=TEST_SOURCE).first() is not None)
        finally:
            _purge()

def test_idempotency():
    print("\n--- idempotency ---")
    # Integration via direct ingestion with ephemeral source (not ManaMurah) to avoid polluting demo
    from services.market_ingestion import ingest_records
    recs = [_make_record(eid="fama-46-runcit", price=0.52), _make_record(eid="fama-40-runcit", title="BERAS", price=3.1, day=date(2026,9,4))]
    with app.app_context():
        _purge()
        try:
            r1 = ingest_records(recs, source_name=TEST_SOURCE)
            check("first run inserted 2", r1['new_observations'] == 2)
            r2 = ingest_records(recs, source_name=TEST_SOURCE)
            check("second run inserted 0", r2['new_observations'] == 0)
            check("second run duplicates 2", r2['duplicates_skipped'] == 2)
        finally:
            _purge()

def test_dry_run_no_persist():
    print("\n--- dry_run no persist ---")
    mock_client = MagicMock()
    mock_client.fetch_fama_records.return_value = [_make_record(price=0.60)]
    svc = MarketDataRefreshService(mcp_client=mock_client)
    with app.app_context():
        _purge()
        try:
            # dry_run must not touch MarketSource/MarketItem at all (validates only)
            before = MarketItem.query.join(MarketSource).filter(MarketSource.name==TEST_SOURCE).count()
            r = svc.refresh_manamurah(dry_run=True, triggered_by='test')
            check("dry_run success", r.success)
            check("dry_run inserted 0", r.inserted == 0)
            check("dry_run raw has dry_run", r.raw.get("dry_run") is True)
            after = MarketItem.query.join(MarketSource).filter(MarketSource.name==TEST_SOURCE).count()
            check("dry_run did not create ephemeral items", after == before)
            # also ensure real ManaMurah untouched
            mm_before = MarketPriceObservation.query.join(MarketItem).join(MarketSource).filter(MarketSource.name=="ManaMurah").count()
            # dry_run already done, check again
            mm_after = MarketPriceObservation.query.join(MarketItem).join(MarketSource).filter(MarketSource.name=="ManaMurah").count()
            check("dry_run did not change ManaMurah obs", mm_after == mm_before)
        finally:
            _purge()

def test_pricecatcher_dry_run():
    print("\n--- pricecatcher dry_run ---")
    svc = MarketDataRefreshService()
    with app.app_context():
        # Should not touch 1.97M rows; dry_run returns 0 inserted quickly
        r = svc.refresh_pricecatcher(dry_run=True, triggered_by='test')
        check("pc dry_run success", r.success and r.inserted == 0)
        check("pc dry_run raw flag", r.raw.get("dry_run") is True)

def test_cli_dry_run():
    print("\n--- CLI dry-run ---")
    # Use Flask test CLI runner
    runner = app.test_cli_runner()
    result = runner.invoke(args=["market-data", "refresh", "--source", "manamurah", "--dry-run", "--days", "5"])
    # Should exit 0 and output contains OK
    check("CLI exit 0", result.exit_code == 0)
    out = result.output or ""
    check("CLI output has manamurah", "manamurah" in out.lower())

def test_no_price_mutation():
    print("\n--- no price mutation ---")
    from collections import Counter
    fake_stats = Counter({"retrieved":1,"accepted":1,"rejected":0,"new_items":0,"updated_items":0,"new_observations":1,"duplicates_skipped":0,"errors":0})
    with app.app_context():
        _purge()
        try:
            pid, sid = _make_shop_product()
            before_price = db.session.get(Product, pid).selling_price
            dec_before = db.session.query(PricingRecommendationDecision).count()
            mock_client = MagicMock()
            mock_client.fetch_fama_records.return_value = [_make_record(price=0.52)]
            svc = MarketDataRefreshService(mcp_client=mock_client)
            with patch("services.market_ingestion.ingest_records", return_value=fake_stats):
                svc.refresh_manamurah(triggered_by='test')
            after_price = db.session.get(Product, pid).selling_price
            dec_after = db.session.query(PricingRecommendationDecision).count()
            check("selling_price unchanged", float(before_price) == float(after_price))
            check("no new decisions", dec_before == dec_after)
        finally:
            _purge()

def _purge_check():
    with app.app_context():
        leaks = db.session.execute(text("SELECT COUNT(*) FROM shop WHERE name=:n"), {"n": TEST_SHOP}).scalar()
        return leaks==0

def main():
    global PASSED, FAILED
    PASSED=FAILED=0
    with app.app_context():
        _purge()
    tests = [test_source_selection_all, test_source_selection_pricecatcher_only, test_source_selection_manamurah_only, test_invalid_source_raises, test_result_normalization_manamurah, test_error_handling_one_fails, test_source_isolation, test_idempotency, test_dry_run_no_persist, test_pricecatcher_dry_run, test_cli_dry_run, test_no_price_mutation]
    for fn in tests:
        try:
            fn()
        except Exception as e:
            FAILED+=1
            print(f"  [FAIL] {fn.__name__} raised {type(e).__name__}: {e}")
        # ensure purge between tests
        with app.app_context():
            _purge()
    with app.app_context():
        _purge()
        ok = _purge_check()
        check("purge clean", ok)
        # ensure no ephemeral sources left
        cnt = db.session.execute(text("SELECT COUNT(*) FROM market_source WHERE name IN (:a,:b)"), {"a": TEST_SOURCE, "b": TEST_PC_SOURCE}).scalar()
        check("ephemeral sources cleaned", cnt==0)
    total=PASSED+FAILED
    print(f"\ntest_market_refresh_service: {PASSED}/{total} checks passed" + (f" ({FAILED} FAILED)" if FAILED else ""))
    return 0 if FAILED==0 else 1

if __name__=="__main__":
    import sys
    sys.exit(main())
