"""
Real-database tests for the source-agnostic market ingestion layer
(services/market_ingestion.py).

Follows the project's established pattern (see tests/test_market_models.py):
everything runs inside ONE app.app_context() in main(), every created row
is deleted in finally/teardown, and DB state is asserted restored.

Coverage:
  - get_or_create_source idempotency (second call returns same row)
  - new MarketItem upsert / existing MarketItem update
  - new observation / duplicate observation (idempotency)
  - invalid price, invalid date, missing fields -> rejected, not inserted
  - price update on re-sync (upsert refreshes stale values)
  - multi-source coexistence: PriceCatcher rows untouched by ManaMurah sync
  - end-to-end sync_from_client with a mocked MCP client

Run standalone:
    ./venv/Scripts/python.exe tests/test_market_ingestion.py
"""
import os
import sys
from datetime import date, datetime
from functools import wraps
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text                                    # noqa: E402

from app import (app, db, MarketSource, MarketItem,            # noqa: E402
                 MarketPriceObservation)
from services.market_ingestion import (get_or_create_source,   # noqa: E402
                                       validate_record,
                                       validate_observation,
                                       upsert_market_item,
                                       upsert_observation,
                                       ingest_records,
                                       sync_from_client)

TEST_SOURCE_NAME = 'TestSource_ingest'
TEST_PC_SOURCE_NAME = 'TestPriceCatcher_ingest'
PASSED = FAILED = 0


def with_db(fn):
    """Decorator: run a test inside its own Flask app context with
    pre/post purge.

    This makes the file work under BOTH runners: pytest collects each
    test function individually (no shared main() context), while the
    standalone runner keeps the project's original single-context
    pattern. Flask-SQLAlchemy 3.x gives each app context its own
    session, but every test commits and re-queries the shared MySQL
    database, so cross-context visibility is guaranteed.

    The post-purge runs in a finally block: without it a failing test
    leaks its fixtures into the shared demo database (Phase 10 audit:
    this file leaked 9 TestSource_ingest / TestPriceCatcher_ingest
    sources because pytest never called main()'s purge).
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        with app.app_context():
            _purge()
            try:
                return fn(*args, **kwargs)
            finally:
                _purge()
    return wrapper


def check(label, cond):
    global PASSED, FAILED
    if cond:
        PASSED += 1; print(f"  [PASS] {label}")
    else:
        FAILED += 1; print(f"  [FAIL] {label}")


# -------------------------------------------------
# Cleanup helpers (FK-safe: children before parents)
# -------------------------------------------------
_PURGE_SQL = [
    # JOIN form so MySQL drives from the tiny market_item side via the
    # market_item_id index; the subquery form forces a full scan of the
    # 1.97M-row observation table even when there is nothing to delete.
    "DELETE o FROM market_price_observation o "
    "JOIN market_item mi ON mi.id = o.market_item_id "
    "WHERE mi.source_id IN (SELECT id FROM market_source WHERE name = :sn)",
    "DELETE mi FROM market_item mi "
    "WHERE mi.source_id IN (SELECT id FROM market_source WHERE name = :sn)",
    "DELETE FROM market_source WHERE name = :sn",
]

# The two source names this suite creates. The previous purge also
# unconditionally deleted the 'ManaMurah' source — which holds the REAL
# demo data synced by scripts/sync_market_data.py ('fama-<n>-runcit').
# No test writes to that source (they all pass source_name=
# TEST_SOURCE_NAME / TEST_PC_SOURCE_NAME), so real demo data is now
# simply never touched instead of being scope-guessed.
TEST_SOURCE_NAMES = (TEST_SOURCE_NAME, TEST_PC_SOURCE_NAME)


def _purge():
    """Remove rows from this test run AND any leftovers from a previous
    crashed run, so tests start from a known state. Real synced market
    data (PriceCatcher, the ManaMurah demo rows) is never deleted."""
    for sn in TEST_SOURCE_NAMES:
        for q in _PURGE_SQL:
            db.session.execute(text(q), {'sn': sn})
    db.session.commit()


def _make_record(external_id='fama-46-runcit', title='TELUR AYAM',
                 price=0.52, day=date(2026, 9, 3)):
    """Build a minimal valid market record for testing."""
    return {
        'external_id': external_id,
        'raw_title': title,
        'category': 'FAMA',
        'package_quantity': 1.0,
        'package_unit': 'unit',
        'observations': [{'date': datetime(day.year, day.month, day.day),
                          'price': price, 'state': None, 'district': None}],
    }


# ======================================================== SOURCE TESTS
@with_db
def test_get_or_create_source_idempotent():
    s1 = get_or_create_source(TEST_SOURCE_NAME, 'online_retailer')
    db.session.flush()
    s2 = get_or_create_source(TEST_SOURCE_NAME, 'online_retailer')
    check('same row returned on second call', s1.id == s2.id)
    check('source type set', s1.source_type == 'online_retailer')
    check('source is active', s1.is_active is True)


# ======================================================== VALIDATION TESTS
@with_db
def test_validate_record_rejections():
    ok, _ = validate_record(_make_record())
    check('valid record accepted', ok)
    bad = _make_record(); bad['raw_title'] = '   '
    ok, why = validate_record(bad)
    check('blank title rejected', not ok and 'raw_title' in why)
    bad = _make_record(); bad['external_id'] = ''
    ok, why = validate_record(bad)
    check('missing external_id rejected', not ok and 'external_id' in why)
    bad = _make_record(); bad['observations'] = []
    ok, why = validate_record(bad)
    check('no observations rejected', not ok and 'observations' in why)
    ok, _ = validate_record('not a dict')
    check('non-dict rejected', not ok)


@with_db
def test_validate_observation_rejections():
    ok, _ = validate_observation({'date': datetime(2026, 9, 3), 'price': 1.0})
    check('valid observation accepted', ok)
    ok, why = validate_observation({'date': datetime(2026, 9, 3), 'price': 0})
    check('zero price rejected', not ok and 'positive' in why)
    ok, why = validate_observation({'date': datetime(2026, 9, 3), 'price': -2})
    check('negative price rejected', not ok)
    ok, why = validate_observation({'date': datetime(2026, 9, 3), 'price': 'x'})
    check('non-numeric price rejected', not ok and 'numeric' in why)
    ok, why = validate_observation({'price': 1.0})
    check('missing date rejected', not ok and 'date' in why)


# ======================================================== UPSERT TESTS
@with_db
def test_upsert_item_new_then_update():
    source = get_or_create_source(TEST_SOURCE_NAME, 'online_retailer')
    db.session.flush()
    rec = _make_record()
    item, created = upsert_market_item(rec, source)
    db.session.flush()
    check('first upsert creates', created)
    check('normalized_title applied', item.normalized_title == 'telur ayam')
    # Second upsert of the same external_id must UPDATE, not duplicate.
    rec2 = _make_record(title='TELUR AYAM GRED A')
    item2, created2 = upsert_market_item(rec2, source)
    db.session.flush()
    check('second upsert updates same row', item2.id == item.id)
    check('no duplicate created', not created2)
    check('title refreshed', item2.raw_title == 'TELUR AYAM GRED A')
    count = MarketItem.query.filter_by(source_id=source.id).count()
    check('exactly one item exists', count == 1)


@with_db
def test_upsert_observation_new_duplicate_and_update():
    source = get_or_create_source(TEST_SOURCE_NAME, 'online_retailer')
    db.session.flush()
    item, _ = upsert_market_item(_make_record(), source)
    db.session.flush()
    obs = {'date': datetime(2026, 9, 3), 'price': 0.52}
    o1, c1 = upsert_observation(item, obs)
    db.session.flush()
    check('first observation created', c1)
    # Same key again -> duplicate detected, no new row.
    o2, c2 = upsert_observation(item, dict(obs))
    db.session.flush()
    check('duplicate detected (not created)', not c2)
    check('same row returned', o2.id == o1.id)
    n = MarketPriceObservation.query.filter_by(market_item_id=item.id).count()
    check('still exactly one observation', n == 1)
    # Changed price on the same key -> refreshed in place.
    o3, c3 = upsert_observation(item, dict(obs, price=0.55))
    db.session.flush()
    check('price update does not create row', not c3 and o3.id == o1.id)
    check('price refreshed to 0.55', float(o3.regular_price) == 0.55)


# ======================================================== INGESTION TESTS
@with_db
def test_ingest_records_full_flow():
    stats = ingest_records(
        [_make_record(), _make_record(external_id='fama-40-runcit',
                                      title='BERAS SAWAH', price=3.10,
                                      day=date(2026, 9, 4))],
        source_name=TEST_SOURCE_NAME)
    check('two retrieved', stats['retrieved'] == 2)
    check('two accepted', stats['accepted'] == 2)
    check('two new items', stats['new_items'] == 2)
    check('two new observations', stats['new_observations'] == 2)
    check('no errors', stats['errors'] == 0)


@with_db
def test_ingest_records_idempotent_rerun():
    """Running the same sync twice must not double the data."""
    records = [_make_record(), _make_record(external_id='fama-40-runcit',
                                            title='BERAS SAWAH', price=3.10,
                                            day=date(2026, 9, 4))]
    ingest_records(records, source_name=TEST_SOURCE_NAME)
    stats2 = ingest_records(records, source_name=TEST_SOURCE_NAME)
    check('rerun creates zero new items', stats2['new_items'] == 0)
    check('rerun creates zero new observations',
          stats2['new_observations'] == 0)
    check('rerun skips duplicates',
          stats2['duplicates_skipped'] == 2)


@with_db
def test_ingest_rejects_invalid_and_continues():
    """One invalid record must not block the valid ones."""
    bad = _make_record(external_id='bad-1')
    bad['observations'] = [{'date': datetime(2026, 9, 3), 'price': -5}]
    good = _make_record(external_id='good-1', title='BERAS IMPORT',
                        price=3.30)
    stats = ingest_records([bad, good], source_name=TEST_SOURCE_NAME)
    check('invalid record rejected', stats['rejected'] >= 1)
    check('valid record still accepted', stats['accepted'] >= 1)
    check('no crash errors', stats['errors'] == 0)
    # The good item must exist in the database.
    src = MarketSource.query.filter_by(name=TEST_SOURCE_NAME).first()
    items = MarketItem.query.filter_by(source_id=src.id).all()
    check('good item persisted',
          any(i.external_id == 'good-1' for i in items))
    check('bad item NOT persisted',
          not any(i.external_id == 'bad-1' for i in items))


@with_db
def test_ingest_missing_required_fields():
    stats = ingest_records([{'raw_title': '', 'external_id': 'x',
                             'observations': []}],
                           source_name=TEST_SOURCE_NAME)
    check('empty record rejected', stats['rejected'] == 1)
    check('empty record not accepted', stats['accepted'] == 0)


# ======================================================== MULTI-SOURCE TESTS
@with_db
def test_multi_source_coexistence():
    """A ManaMurah sync must never delete or alter PriceCatcher rows."""
    # Create a fake PriceCatcher source with one item + observation.
    pc = MarketSource(name=TEST_PC_SOURCE_NAME,
                      source_type='government', is_active=True)
    db.session.add(pc); db.session.flush()
    pc_item = MarketItem(source_id=pc.id, external_id='PC-1',
                         raw_title='GULA PUTIH 1KG',
                         normalized_title='gula putih 1kg',
                         package_quantity=1, package_unit='kg')
    db.session.add(pc_item); db.session.flush()
    pc_obs = MarketPriceObservation(
        market_item_id=pc_item.id, regular_price=2.85, is_on_promo=False,
        effective_price=2.85, normalized_unit_price=2.85,
        state='Johor', observed_at=datetime(2026, 9, 1))
    db.session.add(pc_obs)
    db.session.commit()
    pc_before = (MarketItem.query.filter_by(source_id=pc.id).count(),
                 MarketPriceObservation.query
                 .filter_by(market_item_id=pc_item.id).count())

    # Run a ManaMurah sync against a DIFFERENT source.
    ingest_records([_make_record()], source_name=TEST_SOURCE_NAME)

    pc_after = (MarketItem.query.filter_by(source_id=pc.id).count(),
                MarketPriceObservation.query
                .filter_by(market_item_id=pc_item.id).count())
    check('PriceCatcher rows untouched', pc_before == pc_after)

    # ManaMurah rows coexist alongside them.
    mm = MarketSource.query.filter_by(name=TEST_SOURCE_NAME).first()
    check('ManaMurah source exists alongside',
          mm is not None and mm.id != pc.id)


# ======================================================== E2E WITH MOCK CLIENT
@with_db
def test_sync_from_client_mocked():
    """End-to-end: mocked MCP client -> ingestion -> database."""
    client = MagicMock()
    client.fetch_fama_records.return_value = [
        _make_record(external_id='fama-46-runcit', title='TELUR AYAM',
                     price=0.52, day=date(2026, 9, 5))]
    stats = sync_from_client(client, [(46, 'TELUR AYAM')],
                             source_name=TEST_SOURCE_NAME)
    check('mock client was called with scope items',
          client.fetch_fama_records.called)
    check('record ingested', stats['new_observations'] == 1)
    src = MarketSource.query.filter_by(name=TEST_SOURCE_NAME).first()
    item = MarketItem.query.filter_by(source_id=src.id,
                                      external_id='fama-46-runcit').first()
    check('item in database', item is not None)
    obs = MarketPriceObservation.query.filter_by(market_item_id=item.id).all()
    check('observation in database', len(obs) == 1)
    check('normalized unit price computed',
          obs[0].normalized_unit_price is not None)


# -------------------------------------------------
# runner (works without pytest)
# -------------------------------------------------
def main():
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith('test_') and callable(fn)]
    with app.app_context():
        _purge()
        failed = []
        global FAILED
        for name, fn in tests:
            try:
                fn()
                _purge()          # isolate each test's DB state
            except Exception as exc:                    # noqa: BLE001
                FAILED += 1
                failed.append((name, exc))
                _purge()
        _purge()
    print(f"test_market_ingestion: {PASSED}/{PASSED + FAILED} checks passed")
    for name, exc in failed:
        print(f"  FAIL {name}: {type(exc).__name__}: {exc}")
    return 1 if FAILED or failed else 0


if __name__ == '__main__':
    sys.exit(main())
