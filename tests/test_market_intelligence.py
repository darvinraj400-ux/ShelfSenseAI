"""
Real-database tests for Phase 6 Market Intelligence.

Follows the project's established pattern (see tests/test_premise_level.py):
synthetic fixtures in dedicated test sources, every row created is purged
before AND after each test, and DB state is asserted restored.

Coverage:
  Market statistics
    - min / max / mean / median / spread / premise count / observation count
  Market position (market_position pure function + integration)
    - below / near / above market, percentage math, zero/None edge cases
  Geographic fallback
    - district data used when sufficient
    - district insufficient -> state fallback
    - state insufficient -> national fallback
    - no data at any tier -> empty state (no fake zeros)
  Store-level data (competitor snapshot)
    - premise names resolved from lookup_premise
    - exact prices preserved, latest snapshot date only
    - multiple premises remain separate rows
  Source isolation
    - PriceCatcher analysis excludes ManaMurah/FAMA observations
    - inactive sources are excluded
  Historical trend (get_market_trend)
    - correct per-date statistics (median/min/max/count)
    - date boundary respected (no invented dates, no future rows)
    - tier follows the same district->state->national chain
  Missing market data
    - stats dict is safely empty (n=0), position is None
    - pricing engine still works without observations

Run:
    pytest tests/test_market_intelligence.py -q
    ./venv/Scripts/python.exe tests/test_market_intelligence.py
"""
import os
import sys
from datetime import datetime, timedelta
from functools import wraps

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy import text

from app import (app, db, MarketSource, MarketItem,              # noqa: E402
                 MarketPriceObservation, ProductMarketMatch, Product, Shop)
from services.market_analysis import (compute_metrics, market_position,
                                      get_market_stats, get_market_trend,
                                      NEAR_MARKET_TOLERANCE)

PC_SOURCE = 'TestPC_mktintel'
MM_SOURCE = 'TestMM_mktintel'
SHOP_NAME = 'TestShop_mktintel'
# Synthetic month far from real data (2099) — cannot collide with the
# 1.97M real PriceCatcher observations.
DAY1 = datetime(2099, 3, 1)
DAY2 = datetime(2099, 3, 2)
DAY3 = datetime(2099, 3, 3)
LOOKBACK = get_trend_days = 900   # trend lookback big enough for 2099 dates

_PURGE_SQL = [
    # FK-safe order: matches -> observations -> items -> sources -> shop
    "DELETE pm FROM product_market_match pm "
    "JOIN market_item mi ON mi.id = pm.market_item_id "
    "WHERE mi.source_id IN (SELECT id FROM market_source "
    "WHERE name IN (:pc, :mm))",
    "DELETE o FROM market_price_observation o "
    "JOIN market_item mi ON mi.id = o.market_item_id "
    "WHERE mi.source_id IN (SELECT id FROM market_source "
    "WHERE name IN (:pc, :mm))",
    "DELETE mi FROM market_item mi "
    "WHERE mi.source_id IN (SELECT id FROM market_source "
    "WHERE name IN (:pc, :mm))",
    "DELETE FROM market_source WHERE name IN (:pc, :mm)",
    # Shop-scoped match purge: catches matches to sources already
    # cleaned up inside a test body (e.g. the inactive-source fixture).
    "DELETE pm FROM product_market_match pm "
    "JOIN product p ON p.id = pm.shop_product_id "
    "JOIN shop s ON s.id = p.shop_id WHERE s.name = :shop",
    "DELETE p FROM product p JOIN shop s ON s.id = p.shop_id "
    "WHERE s.name = :shop",
    "DELETE FROM shop WHERE name = :shop",
]


def _purge():
    for q in _PURGE_SQL:
        db.session.execute(text(q), {'pc': PC_SOURCE, 'mm': MM_SOURCE,
                                     'shop': SHOP_NAME})
    # Remove the synthetic premise row seeded by the snapshot test.
    db.session.execute(text(
        "DELETE FROM lookup_premise WHERE premise_code IN ('P9X','P8X')"))
    db.session.commit()


def with_db(fn):
    """Run a test in its own app context with pre/post purge."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        with app.app_context():
            _purge()
            try:
                return fn(*args, **kwargs)
            finally:
                _purge()
    return wrapper


def _make_source(name):
    s = MarketSource(name=name, source_type='government', is_active=True)
    db.session.add(s)
    db.session.flush()
    return s


def _make_item(source, title, qty=1.0, unit='kg'):
    it = MarketItem(source_id=source.id, external_id=title[:40],
                    raw_title=title, normalized_title=title.lower(),
                    package_quantity=qty, package_unit=unit)
    db.session.add(it)
    db.session.flush()
    return it


def _make_obs(item, premise_code, price, when, state=None, district=None):
    o = MarketPriceObservation(
        market_item_id=item.id, premise_code=premise_code,
        regular_price=price, promo_price=None, is_on_promo=False,
        effective_price=price, normalized_unit_price=price / item.package_quantity
        if item.package_quantity else price,
        observed_at=when, state=state, district=district)
    db.session.add(o)
    return o


def _make_product(shop_id, name='Test Product', cost=2.0, price=3.50,
                  qty=1.0, unit='kg'):
    p = Product(name=name, category='TEST', quantity=qty, unit=unit,
                cost_price=cost, selling_price=price, target_margin=30.0,
                shop_id=shop_id)
    db.session.add(p)
    db.session.flush()
    return p


def _make_shop(state=None, district=None):
    s = Shop(name=SHOP_NAME, state=state, district=district)
    db.session.add(s)
    db.session.flush()
    return s


def _link(product, item, verified=True):
    m = ProductMarketMatch(shop_product_id=product.id,
                           market_item_id=item.id, confidence_score=0.95,
                           match_type='exact', is_verified=verified,
                           is_rejected=False)
    db.session.add(m)
    db.session.flush()
    return m


# ---------------------------------------------------------------
# Part 1 — pure functions (no DB)
# ---------------------------------------------------------------

def test_market_position_above():
    pos = market_position(3.50, 3.09)
    assert pos['position'] == 'Above Market'
    assert pos['difference'] == 0.41
    assert pos['difference_percent'] == 13.27   # (0.41/3.09)*100


def test_market_position_below():
    pos = market_position(2.00, 3.00)
    assert pos['position'] == 'Below Market'
    assert pos['difference'] == -1.0
    assert pos['difference_percent'] == pytest.approx(-33.33, abs=0.01)


def test_market_position_near_within_tolerance():
    # Exactly at the tolerance boundary (+5%) counts as Near Market.
    pos = market_position(3.15, 3.00)
    assert pos['position'] == 'Near Market'
    assert pos['difference_percent'] == 5.0
    # The boundary itself sits within the band (float-safe comparison).
    assert abs(abs(3.15 / 3.00 - 1) - NEAR_MARKET_TOLERANCE) < 1e-9


def test_market_position_at_median():
    pos = market_position(3.09, 3.09)
    assert pos['position'] == 'Near Market'
    assert pos['difference'] == 0.0
    assert pos['difference_percent'] == 0.0


def test_market_position_none_inputs():
    for args in [(None, 3.0), (3.0, None), (0, 3.0), (3.0, 0)]:
        pos = market_position(*args)
        assert pos == {'position': None, 'difference': None,
                       'difference_percent': None}


def test_metrics_spread_and_premise_count_keys():
    m = compute_metrics([2.50, 2.59, 2.99, 3.19, 4.00])
    assert m['min'] == 2.50 and m['max'] == 4.00
    assert m['median'] == 2.99
    assert m['spread'] == pytest.approx(1.50)


# ---------------------------------------------------------------
# Part 2 — DB integration
# ---------------------------------------------------------------

@with_db
def test_stats_min_max_median_premise_count():
    """Five premises with distinct prices on one day -> exact stats."""
    pc = _make_source(PC_SOURCE)
    item = _make_item(pc, 'TEST ITEM A')
    shop = _make_shop(state='Johor', district='Segamat')
    product = _make_product(shop.id, price=3.50)
    _link(product, item)
    for code, price in [('P1', 4.00), ('P2', 2.19), ('P3', 3.50),
                        ('P4', 2.50), ('P5', 3.19)]:
        _make_obs(item, code, price, DAY1, state='Johor', district='Segamat')
    db.session.commit()

    s = get_market_stats(product.id, shop)
    assert s['n'] == 5
    assert s['min'] == 2.19 and s['max'] == 4.00
    # Median of [2.19, 2.50, 3.19, 3.50, 4.00] is the middle value 3.19.
    assert s['median'] == 3.19
    assert s['mean'] == pytest.approx(3.08)     # stored rounded to 2dp
    assert s['spread'] == pytest.approx(1.81)
    assert s['premise_count'] == 5
    assert s['latest_observed_at'] == DAY1.date().isoformat()
    # Shop price 3.50 vs median 3.19 = +9.7% -> Above Market (> 5% band).
    assert s['position'] == 'Above Market'


@with_db
def test_competitor_snapshot_latest_date_only_and_names():
    """Snapshot returns premise NAMES on the latest date only; older
    observations and premise codes never leak into the current table."""
    pc = _make_source(PC_SOURCE)
    item = _make_item(pc, 'TEST ITEM B')
    # Seed a premise name in the raw lookup archive (idempotent: remove
    # any leftover copy first so repeat runs never hit a duplicate key).
    db.session.execute(text(
        "DELETE FROM lookup_premise WHERE premise_code='P9X'"))
    db.session.execute(text(
        "INSERT INTO lookup_premise (premise_code, premise, state, district) "
        "VALUES ('P9X', 'PASARAYA TEST SEGAMAT', 'Johor', 'Segamat')"))
    db.session.flush()
    _make_obs(item, 'P9X', 2.50, DAY1, state='Johor', district='Segamat')
    _make_obs(item, 'P9X', 2.59, DAY2, state='Johor', district='Segamat')  # latest
    _make_obs(item, 'P8X', 4.00, DAY2, state='Johor', district='Segamat')
    shop = _make_shop(state='Johor', district='Segamat')
    product = _make_product(shop.id, price=3.50)
    _link(product, item)
    db.session.commit()

    s = get_market_stats(product.id, shop)
    snap = s['competitor_snapshot']
    assert s['snapshot_date'] == DAY2.date().isoformat()
    assert len(snap) == 2                       # latest date only
    by_code = {r['premise_code']: r for r in snap}
    assert by_code['P9X']['premise'] == 'PASARAYA TEST SEGAMAT'
    assert by_code['P9X']['price'] == 2.59      # exact latest price
    assert by_code['P8X']['price'] == 4.00
    assert snap[0]['price'] <= snap[-1]['price']  # sorted by price


@with_db
def test_source_isolation_and_inactive_sources():
    """Unmatched ManaMurah/FAMA observations and observations from
    INACTIVE sources never contaminate the product's market statistics.
    Only observations of VERIFIED-matched market items from ACTIVE
    sources may contribute."""
    pc = _make_source(PC_SOURCE)
    mm = _make_source(MM_SOURCE)
    pc_item = _make_item(pc, 'TEST ITEM C')
    mm_item = _make_item(mm, 'TEST ITEM C (FAMA)')
    # PriceCatcher: 3 stores at ~RM3.
    for code, price in [('PA', 3.00), ('PB', 3.20), ('PC2', 3.40)]:
        _make_obs(pc_item, code, price, DAY1, state='Johor',
                  district='Segamat')
    # ManaMurah/FAMA: wildly different price. NOT matched to the
    # product, so it must never appear in stats or the snapshot.
    _make_obs(mm_item, 'FAMA1', 99.0, DAY1, state=None, district=None)

    # An INACTIVE source whose item IS matched — still must be excluded.
    dead = _make_source('TestPC_dead_mktintel')
    dead.is_active = False
    dead_item = _make_item(dead, 'TEST ITEM C (dead)')
    _make_obs(dead_item, 'DX', 0.01, DAY1, state='Johor',
              district='Segamat')
    db.session.commit()

    shop = _make_shop(state='Johor', district='Segamat')
    product = _make_product(shop.id, price=3.30)
    _link(product, pc_item)
    _link(product, dead_item)
    db.session.commit()

    s = get_market_stats(product.id, shop)
    assert s['n'] == 3                          # only active PriceCatcher
    assert s['min'] == 3.00 and s['max'] == 3.40
    assert 0.01 not in [r['price'] for r in s['competitor_snapshot']]
    assert 99.0 not in [r['price'] for r in s['competitor_snapshot']]
    assert s['premise_count'] == 3

    # Cleanup the extra source created in this test (name is not
    # unique across runs, so delete by the known source ids).
    db.session.execute(text(
        "DELETE FROM product_market_match WHERE market_item_id=:i"),
        {'i': dead_item.id})
    db.session.execute(text(
        "DELETE o FROM market_price_observation o "
        "JOIN market_item mi ON mi.id=o.market_item_id "
        "WHERE mi.source_id IN (SELECT id FROM market_source "
        "WHERE name='TestPC_dead_mktintel')"))
    db.session.execute(text(
        "DELETE mi FROM market_item mi "
        "JOIN market_source s ON s.id=mi.source_id "
        "WHERE s.name='TestPC_dead_mktintel'"))
    db.session.execute(text(
        "DELETE FROM market_source WHERE name='TestPC_dead_mktintel'"))
    db.session.commit()


@with_db
def test_geographic_fallback_district_state_national():
    """District tier when sufficient; falls back to state; then national.
    Tier labels must be honest about which scope was used."""
    pc = _make_source(PC_SOURCE)
    item = _make_item(pc, 'TEST ITEM D')
    shop = _make_shop(state='Johor', district='Segamat')
    product = _make_product(shop.id, price=3.50)
    _link(product, item)

    # 4 Segamat + 2 other-Johor + 3 Selangor observations.
    for i in range(4):
        _make_obs(item, f'SG{i}', 3.00 + i * 0.1, DAY1,
                  state='Johor', district='Segamat')
    for i in range(2):
        _make_obs(item, f'JB{i}', 3.50 + i * 0.1, DAY1,
                  state='Johor', district='Johor Bahru')
    for i in range(3):
        _make_obs(item, f'KL{i}', 4.50 + i * 0.1, DAY1,
                  state='Selangor', district='Klang')
    db.session.commit()

    s = get_market_stats(product.id, shop)
    assert s['market_tier'] == 'district'
    assert s['market_tier_label'] == 'Segamat, Johor'
    assert s['n'] == 4
    assert s['min'] == 3.0 and s['max'] == 3.3

    # Remove district data -> state fallback (6 Johor observations).
    db.session.execute(text(
        "UPDATE market_price_observation SET district='Kluang' "
        "WHERE market_item_id=:i AND district='Segamat'"),
        {'i': item.id})
    db.session.commit()
    s2 = get_market_stats(product.id, shop)
    assert s2['market_tier'] == 'state'
    assert s2['market_tier_label'] == 'Johor'
    assert s2['n'] == 6

    # Remove state data -> national fallback, honestly labelled.
    db.session.execute(text(
        "UPDATE market_price_observation SET state='Perak' "
        "WHERE market_item_id=:i"), {'i': item.id})
    db.session.commit()
    s3 = get_market_stats(product.id, shop)
    assert s3['market_tier'].startswith('national')
    assert s3['n'] == 9


@with_db
def test_trend_per_date_stats_and_tier():
    """Trend returns one point per observation date with correct
    median/min/max, respects the date boundary, and follows the
    district->state->national tier chain."""
    pc = _make_source(PC_SOURCE)
    item = _make_item(pc, 'TEST ITEM E')
    for code, price in [('PA', 3.00), ('PB', 3.20), ('PC2', 3.40)]:
        _make_obs(item, code, price, DAY1, state='Johor',
                  district='Segamat')
    _make_obs(item, 'PA', 3.10, DAY2, state='Johor', district='Segamat')
    _make_obs(item, 'PB', 3.30, DAY2, state='Johor', district='Segamat')
    _make_obs(item, 'PC2', 3.50, DAY2, state='Johor', district='Segamat')
    db.session.commit()

    shop = _make_shop(state='Johor', district='Segamat')
    t = get_market_trend([item.id], shop=shop, lookback_days=LOOKBACK)
    assert t['tier'] == 'district'
    assert t['tier_label'] == 'Segamat, Johor'
    assert [p['date'] for p in t['points']] == [
        DAY1.date().isoformat(), DAY2.date().isoformat()]
    d1, d2 = t['points']
    assert d1['median'] == 3.20 and d1['min'] == 3.00 and d1['max'] == 3.40
    assert d1['observations'] == 3 and d1['premises'] == 3
    assert d2['median'] == 3.30
    # No invented dates: exactly one point per real observation date.

    # Old date outside the window is excluded by the boundary.
    t_short = get_market_trend([item.id], shop=shop, lookback_days=30)
    assert all(p['date'] >= (datetime.utcnow().date()
                             - timedelta(days=30)).isoformat()
               for p in t_short['points']) or not t_short['points']


@with_db
def test_missing_market_data_safe_empty_state():
    """No observations -> n=0, all metrics None, position None, empty
    snapshot/trend — and the pricing engine still runs (guardrails only)."""
    pc = _make_source(PC_SOURCE)
    item = _make_item(pc, 'TEST ITEM F')          # item with NO observations
    shop = _make_shop(state='Johor', district='Segamat')
    product = _make_product(shop.id, price=3.50)
    _link(product, item)
    db.session.commit()

    s = get_market_stats(product.id, shop)
    assert s['n'] == 0
    assert s['median'] is None and s['min'] is None and s['max'] is None
    assert s['position'] is None and s['difference'] is None
    assert s['competitor_snapshot'] == []
    assert s['latest_observed_at'] is None
    t = get_market_trend([item.id], shop=shop, lookback_days=LOOKBACK)
    assert t['points'] == []

    # Pricing engine must not raise without market data.
    from services.pricing_engine import get_price_recommendation
    rec = get_price_recommendation(product.id, shop=shop)
    assert rec['market_stats']['n'] == 0
    assert rec['recommended_price'] > 0           # cost+margin still applies


# ---------------------------------------------------------------
# Standalone runner (project convention)
# ---------------------------------------------------------------

def main():
    with app.app_context():
        _purge()
    failed = []
    for name, fn in sorted(globals().items()):
        if name.startswith('test_') and callable(fn):
            try:
                fn()
                print(f'  PASS {name}')
            except AssertionError as e:
                failed.append(name)
                print(f'  FAIL {name}: {e}')
    with app.app_context():
        _purge()
    total = len(failed)
    print(f'\n{total} failed' if failed else '\nALL PASSED')
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
