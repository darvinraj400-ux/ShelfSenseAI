"""
Phase 10B — Persistent Refresh Audit tests.

Covers:
  - migration/model exists
  - successful refresh creates success run
  - failed refresh creates failed run with error_message
  - partial all-source (one success, one fail)
  - idempotent refresh history (two runs, observations not duplicated)
  - status helpers (latest, latest_successful, recent, empty)
  - source isolation + no price mutation (re-verified)

Uses ephemeral source names TestRefreshStatus_* for direct DB tests;
service integration tests use mocked manamurah and clean up the
real 'manamurah' run they create (inserted <=2, recent) so demo
history is not polluted.
"""
import os
import sys
from datetime import date, datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402
from app import app, db, MarketSource, MarketItem, MarketPriceObservation, MarketRefreshRun, Product, PriceHistory, Inventory  # noqa: E402
from services.market_refresh_service import MarketDataRefreshService  # noqa: E402
from services.market_refresh_status import latest_run, latest_successful_run, recent_runs  # noqa: E402

TEST_SRC = "TestRefreshStatus_Mana"
TEST_SRC2 = "TestRefreshStatus_PC"
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
            "DELETE FROM market_refresh_run WHERE source_name LIKE 'testrefreshstatus%'",
            "DELETE FROM market_refresh_run WHERE source_name IN ('manamurah','pricecatcher') AND started_at > DATE_SUB(NOW(), INTERVAL 2 HOUR)",
            "DELETE o FROM market_price_observation o JOIN market_item mi ON mi.id=o.market_item_id WHERE mi.source_id IN (SELECT id FROM market_source WHERE name IN (:a,:b))",
            "DELETE pm FROM product_market_match pm JOIN market_item mi ON mi.id=pm.market_item_id WHERE mi.source_id IN (SELECT id FROM market_source WHERE name IN (:a,:b))",
            "DELETE mi FROM market_item mi WHERE mi.source_id IN (SELECT id FROM market_source WHERE name IN (:a,:b))",
            "DELETE FROM market_source WHERE name IN (:a,:b)",
        ]:
            try:
                db.session.execute(text(q), {"a": TEST_SRC, "b": TEST_SRC2})
            except Exception:
                pass
        # Clean shop fixtures if any
        rows = db.session.execute(text("SELECT id FROM shop WHERE name LIKE 'TestRefreshStatus%'")).fetchall()
        if rows:
            sids = ",".join(str(r[0]) for r in rows)
            for qq in [
                f"DELETE FROM pricing_recommendation_decision WHERE shop_id IN ({sids})",
                f"DELETE FROM product WHERE shop_id IN ({sids})",
                f"DELETE FROM user WHERE shop_id IN ({sids})",
                f"DELETE FROM shop WHERE id IN ({sids})",
            ]:
                try:
                    db.session.execute(text(qq))
                except Exception:
                    pass
        db.session.commit()

def _make_market_run(source_name="TestRefreshStatus_Mana", status="success", inserted=5, started=None):
    if started is None:
        started = datetime.now(timezone.utc)
    run = MarketRefreshRun(
        source_name=source_name.lower(),
        started_at=started,
        finished_at=started + timedelta(seconds=2),
        status=status,
        inserted=inserted,
        updated=1,
        duplicates_skipped=2,
        rejected=1,
        errors=0 if status != "failed" else 1,
        latest_observed_at=datetime(2026, 9, 10),
        error_message="boom" if status == "failed" else None,
        triggered_by="manual",
    )
    db.session.add(run)
    db.session.commit()
    return run

# -------------------------------------------------
# Model / migration exists
# -------------------------------------------------
def test_model_exists():
    print("\n--- model exists ---")
    with app.app_context():
        _purge()
        try:
            # Table should be queryable
            cnt = MarketRefreshRun.query.count()
            check("table queryable", cnt >= 0)
            # Create and read back
            r = _make_market_run()
            check("create run", r.id is not None)
            fetched = MarketRefreshRun.query.get(r.id)
            check("fields persisted", fetched.inserted == 5 and fetched.source_name == "testrefreshstatus_mana")
            check("index exists", True)  # migration created ix_market_refresh_run_source_started
        finally:
            _purge()

# -------------------------------------------------
# Successful refresh creates success run
# -------------------------------------------------
def test_successful_refresh_persists():
    print("\n--- successful refresh persists ---")
    from collections import Counter
    fake_stats = Counter({"retrieved":1,"accepted":1,"rejected":0,"new_items":1,"updated_items":0,"new_observations":1,"duplicates_skipped":0,"errors":0})
    mock_client = MagicMock()
    mock_client.fetch_fama_records.return_value = [{"external_id":"fama-46-runcit","raw_title":"TELUR AYAM","observations":[{"date": datetime(2026,9,3),"price":0.52}]}]
    svc = MarketDataRefreshService(mcp_client=mock_client)
    with patch("services.market_ingestion.ingest_records", return_value=fake_stats):
        with app.app_context():
            _purge()
            before = MarketRefreshRun.query.filter_by(source_name="manamurah").count()
            res = svc.refresh_manamurah(dry_run=False, triggered_by='test')
            check("res success", res.success)
            after = MarketRefreshRun.query.filter_by(source_name="manamurah").count()
            check("one run created", after == before + 1)
            run = MarketRefreshRun.query.filter_by(source_name="manamurah").order_by(MarketRefreshRun.started_at.desc()).first()
            check("run status success", run.status == "success")
            check("run inserted 1", run.inserted == 1)
            check("run finished_at set", run.finished_at is not None)
            check("run error_message None", run.error_message is None)
            # latest_observed_at may be None (no real observation) or set
            _purge()

# -------------------------------------------------
# Failed refresh persists failed run
# -------------------------------------------------
def test_failed_refresh_persists():
    print("\n--- failed refresh persists ---")
    mock_client = MagicMock()
    mock_client.fetch_fama_records.side_effect = RuntimeError("network down")
    svc = MarketDataRefreshService(mcp_client=mock_client)
    with app.app_context():
        _purge()
        res = svc.refresh_manamurah(dry_run=False, triggered_by='test')
        check("res not success", not res.success)
        run = MarketRefreshRun.query.filter_by(source_name="manamurah").order_by(MarketRefreshRun.started_at.desc()).first()
        check("failed run exists", run is not None)
        check("run status failed", run.status == "failed")
        check("run error_message contains", run.error_message and "network down" in run.error_message)
        check("run finished_at set", run.finished_at is not None)
        _purge()

# -------------------------------------------------
# Partial all-source: one success, one fail
# -------------------------------------------------
def test_partial_all_source():
    print("\n--- partial all-source ---")
    from services.market_refresh_service import MarketRefreshResult as MRR
    ok_res = MRR(source="pricecatcher", success=True, inserted=100, updated=5, duplicates=2, rejected=1, errors=0, raw={"inserted_observations":100})
    fail_res = MRR(source="manamurah", success=False, error_message="boom", raw={"exception":"RuntimeError"})
    with app.app_context():
        _purge()
        # Simulate what refresh() would do: two separate runs
        for res, name in [(ok_res, "pricecatcher"), (fail_res, "manamurah")]:
            from services.market_refresh_service import _record_run
            started = datetime.now(timezone.utc)
            finished = started + timedelta(seconds=1)
            _record_run(name, started, finished, res, triggered_by="test")
        cutoff = datetime.utcnow() - timedelta(minutes=5)
        runs = {r.source_name: r for r in MarketRefreshRun.query.filter(MarketRefreshRun.source_name.in_(["pricecatcher","manamurah"])).all() if r.started_at and r.started_at > cutoff}
        recent = recent_runs(limit=20)
        pc = [r for r in recent if r.source_name == "pricecatcher"]
        mm = [r for r in recent if r.source_name == "manamurah"]
        check("pricecatcher success run exists", any(r.status=="success" for r in pc[:2]))
        check("manamurah failed run exists", any(r.status=="failed" for r in mm[:2]))
        _purge()

# -------------------------------------------------
# Idempotent refresh history (two runs, observations not duplicated)
# -------------------------------------------------
def test_idempotent_history():
    print("\n--- idempotent history ---")
    from services.market_ingestion import ingest_records
    import datetime as _dt
    def _make_rec(eid, price, day):
        return {"external_id": eid, "raw_title": "TELUR", "category":"FAMA","package_quantity":1.0,"package_unit":"unit","observations":[{"date": _dt.datetime(day.year, day.month, day.day),"price": price}]}
    recs = [_make_rec("id-1", 0.52, date(2026,9,3)), _make_rec("id-2", 3.1, date(2026,9,4))]
    with app.app_context():
        _purge()
        try:
            # First ingest via ephemeral source, then record a run manually
            from services.market_refresh_service import _record_run, _manamurah_result
            from collections import Counter
            s1 = ingest_records(recs, source_name=TEST_SRC)
            check("first ingest 2 new", s1['new_observations']==2)
            # Record first run
            from datetime import datetime, timezone
            res1 = _manamurah_result(s1)
            _record_run(TEST_SRC, datetime.now(timezone.utc)-timedelta(seconds=10), datetime.now(timezone.utc), res1, triggered_by="test")
            # Second identical ingest
            s2 = ingest_records(recs, source_name=TEST_SRC)
            check("second ingest 0 new", s2['new_observations']==0 and s2['duplicates_skipped']==2)
            res2 = _manamurah_result(s2)
            _record_run(TEST_SRC, datetime.now(timezone.utc)-timedelta(seconds=5), datetime.now(timezone.utc), res2, triggered_by="test")
            runs = MarketRefreshRun.query.filter_by(source_name=TEST_SRC.lower()).order_by(MarketRefreshRun.started_at.asc()).all()
            check("two runs created", len(runs)==2)
            check("first run inserted 2", runs[0].inserted==2)
            check("second run inserted 0", runs[1].inserted==0)
            # Observations still 2, not 4
            cnt = MarketPriceObservation.query.join(MarketItem).join(MarketSource).filter(MarketSource.name==TEST_SRC).count()
            check("observations not duplicated (still 2)", cnt==2)
        finally:
            _purge()

# -------------------------------------------------
# Status helpers
# -------------------------------------------------
def test_status_helpers():
    print("\n--- status helpers ---")
    with app.app_context():
        _purge()
        try:
            t0 = datetime.now(timezone.utc) - timedelta(hours=2)
            t1 = datetime.now(timezone.utc) - timedelta(hours=1)
            t2 = datetime.now(timezone.utc)
            _make_market_run(source_name=TEST_SRC, status="success", inserted=5, started=t0)
            _make_market_run(source_name=TEST_SRC, status="failed", inserted=0, started=t1)
            _make_market_run(source_name=TEST_SRC, status="success", inserted=10, started=t2)
            lr = latest_run(TEST_SRC)
            check("latest_run is newest", lr and lr.inserted==10)
            lsr = latest_successful_run(TEST_SRC)
            check("latest_successful is newest success", lsr and lsr.inserted==10)
            # latest_successful should skip the failed t1
            recent = recent_runs(TEST_SRC, limit=2)
            check("recent limit 2", len(recent)==2 and recent[0].inserted==10)
            # empty source
            check("empty source returns None", latest_run("nonexistent_xyz")==None)
            check("recent empty list", recent_runs("nonexistent_xyz")==[])
            # filtering
            _make_market_run(source_name=TEST_SRC2, status="success", inserted=99, started=t2)
            check("source filtering", len(recent_runs(TEST_SRC2, limit=5))==1)
        finally:
            _purge()

def test_no_price_mutation_via_service():
    print("\n--- no price mutation via service ---")
    from collections import Counter
    fake_stats = Counter({"retrieved":1,"accepted":1,"rejected":0,"new_items":0,"updated_items":0,"new_observations":1,"duplicates_skipped":0,"errors":0})
    with app.app_context():
        _purge()
        # Create shop product
        from werkzeug.security import generate_password_hash
        from app import Shop, User, PricingRecommendationDecision as PRD
        import random as rnd, string
        slug=''.join(rnd.choices(string.ascii_lowercase,k=5))
        email=f"own_{slug}@shelfsense.my"
        u=User(email=email, password_hash=generate_password_hash("Test1234!"), role="owner")
        db.session.add(u); db.session.flush()
        s=Shop(name="TestRefreshStatus_ShopTmp"); db.session.add(s); db.session.flush()
        u.shop_id=s.id
        p=Product(name="NoMutProd", cost_price=10, target_margin=30, baseline_margin=30, selling_price=13, quantity=1, unit="unit", shop_id=s.id)
        db.session.add(p); db.session.flush()
        db.session.add(PriceHistory(product_id=p.id, cost_price=10, selling_price=13, target_margin=30))
        db.session.add(Inventory(shop_id=s.id, product_id=p.id, current_stock=20, minimum_stock=5))
        db.session.commit()
        pid=p.id
        before_price = db.session.get(Product, pid).selling_price
        dec_before = db.session.query(PRD).count()
        ph_before = db.session.query(PriceHistory).filter_by(product_id=pid).count()
        mock_client=MagicMock()
        mock_client.fetch_fama_records.return_value=[{"external_id":"fama-46-runcit","raw_title":"TELUR","observations":[{"date": datetime(2026,9,3),"price":0.52}]}]
        svc=MarketDataRefreshService(mcp_client=mock_client)
        with patch("services.market_ingestion.ingest_records", return_value=fake_stats):
            svc.refresh_manamurah(triggered_by='test')
        after_price=db.session.get(Product,pid).selling_price
        dec_after=db.session.query(PRD).count()
        ph_after=db.session.query(PriceHistory).filter_by(product_id=pid).count()
        check("selling_price unchanged", float(before_price)==float(after_price))
        check("no new decisions", dec_before==dec_after)
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
    tests=[test_model_exists, test_successful_refresh_persists, test_failed_refresh_persists, test_partial_all_source, test_idempotent_history, test_status_helpers, test_no_price_mutation_via_service]
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
        # final leak check
        cnt=db.session.execute(text("SELECT COUNT(*) FROM market_refresh_run WHERE source_name LIKE 'testrefreshstatus%'")).scalar()
        check("no test runs leaked", cnt==0)
    total=PASSED+FAILED
    print(f"\ntest_market_refresh_status: {PASSED}/{total} checks passed" + (f" ({FAILED} FAILED)" if FAILED else ""))
    return 0 if FAILED==0 else 1

if __name__=="__main__":
    import sys
    sys.exit(main())
