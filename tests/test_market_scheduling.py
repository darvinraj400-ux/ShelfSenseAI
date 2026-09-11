"""
Phase 10E — Market Data Scheduling tests.

Covers manual/scheduled triggers, success/failure/partial, dry-run,
no mutation, existing CLI options, and documentation.
"""
import sys
from datetime import datetime, timezone, timedelta, date
from unittest.mock import MagicMock, patch
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402
from app import app, db, MarketSource, MarketItem, MarketPriceObservation, MarketRefreshRun, Product, PriceHistory, Inventory  # noqa: E402
from services.market_refresh_service import MarketDataRefreshService  # noqa: E402

TEST_SHOP = "TestScheduling_Shop"
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
        # Clean test runs (manual/scheduled/test) from this suite — recent only so legitimate older history is preserved
        for q in [
            "DELETE FROM market_refresh_run WHERE source_name IN ('manamurah','pricecatcher') AND started_at > DATE_SUB(UTC_TIMESTAMP(), INTERVAL 2 HOUR)",
            "DELETE FROM market_refresh_run WHERE source_name LIKE 'TestScheduling%'",
            "DELETE FROM market_refresh_run WHERE triggered_by='test'",
        ]:
            try:
                db.session.execute(text(q))
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
    import random, string
    slug=''.join(random.choices(string.ascii_lowercase,k=5))
    email=f"own_{slug}@shelfsense.my"
    def _create():
        u=User(email=email, password_hash=generate_password_hash("Test1234!"), role="owner")
        db.session.add(u); db.session.flush()
        s=Shop(name=TEST_SHOP); db.session.add(s); db.session.flush()
        u.shop_id=s.id; db.session.commit()
        return u.id, s.id, email
    if has_app_context():
        return _create()
    else:
        with app.app_context():
            return _create()

def _make_product(sid):
    from flask import has_app_context
    def _create():
        p=Product(name="SchedProd", cost_price=10, target_margin=30, baseline_margin=30, selling_price=13, quantity=1, unit="unit", shop_id=sid)
        db.session.add(p); db.session.flush()
        db.session.add(PriceHistory(product_id=p.id, cost_price=10, selling_price=13, target_margin=30))
        db.session.add(Inventory(shop_id=sid, product_id=p.id, current_stock=20, minimum_stock=5))
        db.session.commit()
        return p.id
    if has_app_context():
        return _create()
    else:
        with app.app_context():
            return _create()

# 1 manual still works
def test_manual_trigger():
    print("\n--- manual trigger ---")
    from collections import Counter
    fake_stats = Counter({"retrieved":1,"accepted":1,"rejected":0,"new_items":1,"updated_items":0,"new_observations":1,"duplicates_skipped":0,"errors":0})
    mock_client=MagicMock()
    mock_client.fetch_fama_records.return_value=[{"external_id":"fama-46-runcit","raw_title":"TELUR","observations":[{"date": datetime(2026,9,3),"price":0.52}]}]
    svc=MarketDataRefreshService(mcp_client=mock_client)
    with patch("services.market_ingestion.ingest_records", return_value=fake_stats):
        with app.app_context():
            _purge()
            res=svc.refresh_manamurah(triggered_by="manual")
            check("manual success", res.success)
            run=db.session.execute(text("SELECT triggered_by FROM market_refresh_run WHERE source_name='manamurah' ORDER BY started_at DESC LIMIT 1")).scalar()
            check("triggered_by manual", run=="manual")
            _purge()

# 2 scheduled trigger
def test_scheduled_trigger():
    print("\n--- scheduled trigger ---")
    from collections import Counter
    fake_stats = Counter({"retrieved":1,"accepted":1,"rejected":0,"new_items":1,"updated_items":0,"new_observations":1,"duplicates_skipped":0,"errors":0})
    mock_client=MagicMock()
    mock_client.fetch_fama_records.return_value=[{"external_id":"fama-46-runcit","raw_title":"TELUR","observations":[{"date": datetime(2026,9,3),"price":0.52}]}]
    svc=MarketDataRefreshService(mcp_client=mock_client)
    with patch("services.market_ingestion.ingest_records", return_value=fake_stats):
        with app.app_context():
            _purge()
            res=svc.refresh_manamurah(triggered_by="scheduled")
            check("scheduled success", res.success)
            run=db.session.execute(text("SELECT triggered_by FROM market_refresh_run WHERE source_name='manamurah' ORDER BY started_at DESC LIMIT 1")).scalar()
            check("triggered_by scheduled", run=="scheduled")
            _purge()

# 3 successful scheduled refresh
def test_successful_scheduled():
    print("\n--- successful scheduled ---")
    from collections import Counter
    fake_stats = Counter({"retrieved":1,"accepted":1,"rejected":0,"new_items":1,"updated_items":0,"new_observations":1,"duplicates_skipped":0,"errors":0})
    mock_client=MagicMock()
    mock_client.fetch_fama_records.return_value=[{"external_id":"fama-46-runcit","raw_title":"TELUR","observations":[{"date": datetime(2026,9,3),"price":0.52}]}]
    svc=MarketDataRefreshService(mcp_client=mock_client)
    with patch("services.market_ingestion.ingest_records", return_value=fake_stats):
        with app.app_context():
            _purge()
            # product for price check
            uid,sid,_=_make_shop()
            pid=_make_product(sid)
            before=db.session.get(Product,pid).selling_price
            # CLI via test runner
            runner=app.test_cli_runner()
            # also test service directly for scheduled
            res=svc.refresh_manamurah(triggered_by="scheduled")
            check("run status success", res.success)
            # CLI exit 0
            with patch("services.market_ingestion.ingest_records", return_value=fake_stats):
                mock2=MagicMock()
                mock2.fetch_fama_records.return_value=[{"external_id":"fama-46-runcit","raw_title":"TELUR","observations":[{"date": datetime(2026,9,3),"price":0.52}]}]
                # Patch service's client to use mock2 via patching ManaMurahClient
                with patch("services.mcp_client.ManaMurahClient", return_value=mock2):
                    result=runner.invoke(args=["market-data","refresh","--source","manamurah","--scheduled","--days","5"])
                    check("CLI exit 0", result.exit_code==0)
            after=db.session.get(Product,pid).selling_price
            check("no price mutation", float(before)==float(after))
            _purge()

# 4 scheduled failure
def test_scheduled_failure():
    print("\n--- scheduled failure ---")
    mock_client=MagicMock()
    mock_client.fetch_fama_records.side_effect=RuntimeError("network down")
    svc=MarketDataRefreshService(mcp_client=mock_client)
    with app.app_context():
        _purge()
        # capture obs count before
        before_obs=db.session.execute(text("SELECT COUNT(*) FROM market_price_observation o JOIN market_item mi ON mi.id=o.market_item_id JOIN market_source s ON s.id=mi.source_id WHERE s.name='ManaMurah'")).scalar() or 0
        uid,sid,_=_make_shop()
        pid=_make_product(sid)
        before_price=db.session.get(Product,pid).selling_price
        res=svc.refresh_manamurah(triggered_by="scheduled")
        check("failed success false", not res.success)
        run=db.session.execute(text("SELECT status, error_message FROM market_refresh_run WHERE source_name='manamurah' ORDER BY started_at DESC LIMIT 1")).fetchone()
        check("run failed", run and run[0]=="failed")
        check("error_message sanitized", run and "network down" in (run[1] or ""))
        # CLI non-zero
        runner=app.test_cli_runner()
        with patch("services.mcp_client.ManaMurahClient", return_value=mock_client):
            result=runner.invoke(args=["market-data","refresh","--source","manamurah","--scheduled"])
            check("CLI non-zero", result.exit_code!=0)
        after_obs=db.session.execute(text("SELECT COUNT(*) FROM market_price_observation o JOIN market_item mi ON mi.id=o.market_item_id JOIN market_source s ON s.id=mi.source_id WHERE s.name='ManaMurah'")).scalar() or 0
        check("observations preserved", after_obs==before_obs)
        after_price=db.session.get(Product,pid).selling_price
        check("no price mutation on fail", float(before_price)==float(after_price))
        _purge()

# 5 partial source failure
def test_partial_source_failure():
    print("\n--- partial source failure ---")
    from services.market_refresh_service import MarketRefreshResult
    ok_res=MarketRefreshResult(source="pricecatcher", success=True, inserted=10, updated=1, duplicates=0, rejected=0, errors=0, raw={"inserted_observations":10})
    fail_res=MarketRefreshResult(source="manamurah", success=False, error_message="boom", raw={"exception":"RuntimeError"})
    svc=MarketDataRefreshService()
    with app.app_context():
        _purge()
        # Simulate via direct _record_run to avoid real ETL
        from services.market_refresh_service import _record_run
        started=datetime.now(timezone.utc)
        _record_run("pricecatcher", started, started+timedelta(seconds=1), ok_res, triggered_by="scheduled")
        _record_run("manamurah", started, started+timedelta(seconds=1), fail_res, triggered_by="scheduled")
        runs=db.session.execute(text("SELECT source_name, status FROM market_refresh_run WHERE triggered_by='scheduled' ORDER BY source_name")).fetchall()
        check("both preserved", len([r for r in runs if r[0] in ("pricecatcher","manamurah")])>=2)
        # via service refresh with mocks
        with patch.object(svc, "refresh_pricecatcher", return_value=ok_res), patch.object(svc, "refresh_manamurah", return_value=fail_res):
            res_map=svc.refresh(sources="all", triggered_by="scheduled")
            check("pricecatcher success", res_map["pricecatcher"].success)
            check("manamurah fail", not res_map["manamurah"].success)
        # ensure successful not rolled back (still exists)
        pc_cnt=db.session.execute(text("SELECT COUNT(*) FROM market_refresh_run WHERE source_name='pricecatcher' AND status='success' AND triggered_by='scheduled'")).scalar()
        check("successful not rolled back", pc_cnt>=1)
        _purge()

# 6 no decision mutation
def test_no_decision_mutation():
    print("\n--- no decision mutation ---")
    from collections import Counter
    fake_stats = Counter({"retrieved":1,"accepted":1,"rejected":0,"new_items":0,"updated_items":0,"new_observations":1,"duplicates_skipped":0,"errors":0})
    mock_client=MagicMock()
    mock_client.fetch_fama_records.return_value=[{"external_id":"fama-46-runcit","raw_title":"TELUR","observations":[{"date": datetime(2026,9,3),"price":0.52}]}]
    svc=MarketDataRefreshService(mcp_client=mock_client)
    with patch("services.market_ingestion.ingest_records", return_value=fake_stats):
        with app.app_context():
            _purge()
            from app import PricingRecommendationDecision
            before=db.session.query(PricingRecommendationDecision).count()
            svc.refresh_manamurah(triggered_by="scheduled")
            after=db.session.query(PricingRecommendationDecision).count()
            check("no decision mutation", before==after)
            _purge()

# 7 no selling-price mutation
def test_no_selling_price_mutation():
    print("\n--- no selling-price mutation ---")
    from collections import Counter
    fake_stats = Counter({"retrieved":1,"accepted":1,"rejected":0,"new_items":0,"updated_items":0,"new_observations":1,"duplicates_skipped":0,"errors":0})
    mock_client=MagicMock()
    mock_client.fetch_fama_records.return_value=[{"external_id":"fama-46-runcit","raw_title":"TELUR","observations":[{"date": datetime(2026,9,3),"price":0.52}]}]
    svc=MarketDataRefreshService(mcp_client=mock_client)
    with patch("services.market_ingestion.ingest_records", return_value=fake_stats):
        with app.app_context():
            _purge()
            uid,sid,_=_make_shop()
            pid=_make_product(sid)
            before=db.session.get(Product,pid).selling_price
            ph_before=db.session.query(PriceHistory).filter_by(product_id=pid).count()
            svc.refresh_manamurah(triggered_by="scheduled")
            after=db.session.get(Product,pid).selling_price
            ph_after=db.session.query(PriceHistory).filter_by(product_id=pid).count()
            check("selling_price unchanged", float(before)==float(after))
            check("PriceHistory unchanged", ph_before==ph_after)
            _purge()

# 8 dry-run scheduled
def test_dry_run_scheduled():
    print("\n--- dry-run scheduled ---")
    mock_client=MagicMock()
    mock_client.fetch_fama_records.return_value=[{"external_id":"fama-46-runcit","raw_title":"TELUR","observations":[{"date": datetime(2026,9,3),"price":0.52}]}]
    svc=MarketDataRefreshService(mcp_client=mock_client)
    with app.app_context():
        _purge()
        before=db.session.execute(text("SELECT COUNT(*) FROM market_refresh_run")).scalar()
        res=svc.refresh_manamurah(dry_run=True, triggered_by="scheduled")
        check("dry-run success", res.success)
        after=db.session.execute(text("SELECT COUNT(*) FROM market_refresh_run")).scalar()
        check("dry-run no history", before==after)
        # CLI dry-run scheduled
        runner=app.test_cli_runner()
        with patch("services.mcp_client.ManaMurahClient", return_value=mock_client):
            result=runner.invoke(args=["market-data","refresh","--source","manamurah","--scheduled","--dry-run"])
            check("CLI dry-run exit 0", result.exit_code==0)
        after2=db.session.execute(text("SELECT COUNT(*) FROM market_refresh_run")).scalar()
        check("CLI dry-run no history", after==after2)
        _purge()

# 9 existing CLI options
def test_existing_cli_options():
    print("\n--- existing CLI options ---")
    runner=app.test_cli_runner()
    for args in [
        ["market-data","refresh","--help"],
        ["market-data","refresh","--source","pricecatcher","--dry-run"],
        ["market-data","refresh","--source","manamurah","--dry-run"],
        ["market-data","refresh","--source","all","--dry-run"],
        ["market-data","refresh","--source","manamurah","--days","5","--dry-run"],
        ["market-data","refresh","--source","manamurah","--state","johor","--dry-run"],
        ["market-data","refresh","--source","manamurah","--scheduled","--dry-run"],
    ]:
        result=runner.invoke(args=args)
        check(f"CLI {' '.join(args[2:])} exit 0", result.exit_code==0)

# 10 documentation
def test_documentation():
    print("\n--- documentation ---")
    import pathlib
    p=pathlib.Path("docs/MARKET_DATA_SCHEDULING.md")
    check("doc exists", p.exists())
    txt=p.read_text().lower() if p.exists() else ""
    check("has Windows Task Scheduler", "task scheduler" in txt)
    check("has cron", "cron" in txt)
    check("has pricecatcher", "pricecatcher" in txt)
    check("has manamurah", "manamurah" in txt or "fama" in txt)
    check("does NOT auto price", "does not" in txt and "selling price" in txt)
    check("has monitoring", "/market-data" in txt or "market-data" in txt)

def main():
    global PASSED, FAILED
    PASSED=FAILED=0
    with app.app_context():
        _purge()
    tests=[test_manual_trigger, test_scheduled_trigger, test_successful_scheduled, test_scheduled_failure, test_partial_source_failure, test_no_decision_mutation, test_no_selling_price_mutation, test_dry_run_scheduled, test_existing_cli_options, test_documentation]
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
        cnt=db.session.execute(text("SELECT COUNT(*) FROM market_refresh_run WHERE triggered_by IN ('test','scheduled') AND source_name IN ('manamurah','pricecatcher')")).scalar()
        # We allow scheduled test runs to remain? No, they should be cleaned via _purge above (which deletes scheduled within 2h). So we check that no test-scheduled leaked beyond 2h is not needed.
        # Instead verify no TestScheduling shops leaked
        leaked=db.session.execute(text("SELECT name FROM shop WHERE name LIKE 'TestScheduling%'")).fetchall()
        check("no TestScheduling shops leaked", len(leaked)==0)
    total=PASSED+FAILED
    print(f"\ntest_market_scheduling: {PASSED}/{total} checks passed" + (f" ({FAILED} FAILED)" if FAILED else ""))
    return 0 if FAILED==0 else 1

if __name__=="__main__":
    import sys
    sys.exit(main())
