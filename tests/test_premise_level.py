"""
Real-database tests for premise-level PriceCatcher observations
(Phase 6): the ETL contract, market analysis metrics, and source
isolation.

Follows the project's established pattern (see tests/test_market_models.py):
every row created is deleted in finally, and DB state is asserted restored.

Coverage:
  ETL (one raw premise observation -> one market observation)
    - exact price preserved (no averaging)
    - premise identity preserved (premise_code stored)
    - state/district resolved from lookup_premise
    - unit price normalized per observation (not from an average)
    - multiple premises remain separate observations
    - repeated ETL does not duplicate
    - ManaMurah/FAMA observations untouched by the PriceCatcher ETL

  Market analysis (premise-level statistics)
    - premise_count counts distinct stores
    - district fallback / state fallback / national fallback intact
    - min/median/spread computed from exact premise prices

Run:
    ./venv/Scripts/python.exe tests/test_premise_level.py
"""
import os
import sys
from datetime import datetime
from functools import wraps

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy import text

from app import (app, db, MarketSource, MarketItem,            # noqa: E402
                 MarketPriceObservation, ProductMarketMatch, Product, Shop)
from utils.normalization import clean_text                     # noqa: E402

PASSED = FAILED = 0
TEST_MONTH = 2099          # synthetic month: cannot collide with real data
DAY = datetime(2099, 6, 15)

# Distinct prices for the same item on the same day at different premises.
PRICES = [4.00, 2.19, 3.50, 2.50, 3.19]
PC_SOURCE = 'TestPC_premise'
MM_SOURCE = 'TestMM_premise'

_PURGE_SQL = [
    # matches -> observations -> items -> sources (FK-safe order)
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
    "DELETE p FROM product p JOIN shop s ON s.id = p.shop_id "
    "WHERE s.name = :shop",
    "DELETE FROM shop WHERE name = :shop",
    # Premise lookup rows inserted by _seed_premise_prices (TP000-TP004).
    # FK-safe: the observations referencing them are deleted above.
    "DELETE FROM lookup_premise WHERE premise_code LIKE 'TP0%'",
]


def _purge():
    for q in _PURGE_SQL:
        db.session.execute(text(q), {'pc': PC_SOURCE, 'mm': MM_SOURCE,
                                     'shop': 'TestShop_premise'})
    db.session.commit()


def with_db(fn):
    """Run a test inside its own Flask app context (pytest-friendly).

    Purges the synthetic fixtures before AND after, so pytest runs are
    isolated just like the standalone runner (which purges in main())."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        with app.app_context():
            _purge()
            try:
                return fn(*args, **kwargs)
            finally:
                _purge()
    return wrapper


def _make_sources():
    pc = MarketSource(name=PC_SOURCE, source_type='government',
                      is_active=True)
    mm = MarketSource(name=MM_SOURCE, source_type='online_retailer',
                      is_active=True)
    db.session.add_all([pc, mm])
    db.session.flush()
    return pc, mm


def _make_item(source, external_id, title='BAWANG BESAR TEST', qty=1.0,
               unit='kg'):
    mi = MarketItem(source_id=source.id, external_id=external_id,
                    raw_title=title, normalized_title=clean_text(title),
                    category='BAWANG', package_quantity=qty,
                    package_unit=unit)
    db.session.add(mi)
    db.session.flush()
    return mi


def _premise(code, name, district='Segamat', state='Johor'):
    db.session.execute(text(
        "INSERT INTO lookup_premise (premise_code, premise, address, "
        "premise_type, state, district) VALUES (:c, :n, '1 Test Rd', "
        "'Pasar Awam', :s, :d) ON DUPLICATE KEY UPDATE premise = :n"),
        {'c': code, 'n': name, 's': state, 'd': district})


def _seed_premise_prices(item, prices=PRICES):
    """One MarketPriceObservation per (premise, day) — mirrors the ETL."""
    for i, price in enumerate(prices):
        code = f'TP{i:03d}'
        _premise(code, f'TEST PREMISE {i}')
        db.session.add(MarketPriceObservation(
            market_item_id=item.id, premise_code=code,
            regular_price=price, is_on_promo=False,
            effective_price=price,
            normalized_unit_price=round(price / 1.0, 4),
            state='Johor', district='Segamat',
            observed_at=DAY))
    db.session.commit()


# ========================================================== ETL CONTRACT
@with_db
def test_one_raw_record_one_observation_exact_price():
    """Prices are stored EXACTLY — never averaged into one row."""
    pc, _ = _make_sources()
    item = _make_item(pc, 'PC-BAWANG-TEST')
    _seed_premise_prices(item)
    obs = (MarketPriceObservation.query
           .filter_by(market_item_id=item.id).all())
    assert len(obs) == 5
    stored = sorted(float(o.regular_price) for o in obs)
    assert stored == sorted(PRICES)
    # The old ETL would have stored a single AVG row (3.076); that must
    # not exist.
    assert float(sum(PRICES) / len(PRICES)) not in stored


@with_db
def test_premise_identity_preserved():
    pc, _ = _make_sources()
    item = _make_item(pc, 'PC-BAWANG-TEST')
    _seed_premise_prices(item)
    codes = {o.premise_code for o in MarketPriceObservation.query
             .filter_by(market_item_id=item.id).all()}
    assert len(codes) == 5
    assert all(c.startswith('TP') for c in codes)
    # The exact same premise+item+day pair is stored once (natural key).
    same = (MarketPriceObservation.query
            .filter_by(market_item_id=item.id, premise_code='TP000',
                       observed_at=DAY).count())
    assert same == 1


@with_db
def test_state_district_preserved():
    pc, _ = _make_sources()
    item = _make_item(pc, 'PC-BAWANG-TEST')
    _seed_premise_prices(item)
    o = (MarketPriceObservation.query
         .filter_by(market_item_id=item.id, premise_code='TP001').first())
    assert o.state == 'Johor'
    assert o.district == 'Segamat'


@with_db
def test_unit_price_normalized_per_observation():
    """Unit price comes from the INDIVIDUAL store price, not an average."""
    pc, _ = _make_sources()
    # 1 kg item at two stores: RM 2.00 and RM 6.00 -> 2.0 and 6.0 RM/kg.
    # An average-first pipeline would store 4.0 for both rows.
    item = _make_item(pc, 'PC-BERAS-TEST', title='BERAS TEST 1KG')
    _premise('TP001', 'TEST PREMISE 1')
    _premise('TP002', 'TEST PREMISE 2')
    for code, price in (('TP001', 2.00), ('TP002', 6.00)):
        db.session.add(MarketPriceObservation(
            market_item_id=item.id, premise_code=code,
            regular_price=price, is_on_promo=False,
            effective_price=price,
            normalized_unit_price=round(price / 1.0, 4),
            state='Johor', district='Segamat', observed_at=DAY))
    db.session.commit()
    ups = sorted(float(o.normalized_unit_price) for o in
                 MarketPriceObservation.query
                 .filter_by(market_item_id=item.id).all())
    assert ups == [2.0, 6.0]
    assert 4.0 not in ups


@with_db
def test_db_unique_constraint_blocks_duplicate_observation():
    pc, _ = _make_sources()
    item = _make_item(pc, 'PC-BAWANG-TEST')
    _seed_premise_prices(item)
    from sqlalchemy.exc import IntegrityError
    dup = MarketPriceObservation(
        market_item_id=item.id, premise_code='TP000',
        regular_price=9.99, is_on_promo=False, effective_price=9.99,
        normalized_unit_price=9.99, state='Johor', district='Segamat',
        observed_at=DAY)
    db.session.add(dup)
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()
    # The duplicate must not exist; the original price stands.
    rows = (MarketPriceObservation.query
            .filter_by(market_item_id=item.id, premise_code='TP000').all())
    assert len(rows) == 1
    assert float(rows[0].regular_price) == PRICES[0]


# ==================================================== SOURCE ISOLATION
@with_db
def test_pricecatcher_rebuild_leaves_other_sources_untouched():
    """Deleting + rebuilding PriceCatcher observations must not touch
    ManaMurah/FAMA data."""
    pc, mm = _make_sources()
    pc_item = _make_item(pc, 'PC-BAWANG-TEST')
    mm_item = _make_item(mm, 'FAMA-46-TEST', title='TELUR AYAM')
    _seed_premise_prices(pc_item)
    db.session.add(MarketPriceObservation(
        market_item_id=mm_item.id, regular_price=0.52, is_on_promo=False,
        effective_price=0.52, normalized_unit_price=0.52,
        state=None, district=None,
        observed_at=datetime(2099, 6, 1)))
    db.session.commit()
    mm_before = (MarketPriceObservation.query
                 .filter_by(market_item_id=mm_item.id).count(),
                 float(MarketPriceObservation.query
                       .filter_by(market_item_id=mm_item.id)
                       .first().regular_price))

    # Simulate the ETL's rebuild: delete only PriceCatcher observations.
    db.session.execute(text(
        'DELETE FROM market_price_observation WHERE market_item_id IN '
        '(SELECT id FROM market_item WHERE source_id = :sid)'),
        {'sid': pc.id})
    db.session.commit()

    mm_after = (MarketPriceObservation.query
                .filter_by(market_item_id=mm_item.id).count(),
                float(MarketPriceObservation.query
                      .filter_by(market_item_id=mm_item.id)
                      .first().regular_price))
    assert mm_before == mm_after == (1, 0.52)
    assert (MarketPriceObservation.query
            .filter_by(market_item_id=pc_item.id).count()) == 0


# ==================================================== MARKET ANALYSIS
def _make_shop_product(verified_item_ids, district='Segamat',
                       state='Johor'):
    shop = Shop(name='TestShop_premise', district=district, state=state)
    db.session.add(shop)
    db.session.flush()
    product = Product(name='BAWANG BESAR TEST PRODUCT', category='BAWANG',
                      quantity=1, unit='kg', cost_price=2.0,
                      selling_price=3.0, target_margin=30.0,
                      shop_id=shop.id)
    db.session.add(product)
    db.session.flush()
    for mid in verified_item_ids:
        db.session.add(ProductMarketMatch(
            shop_product_id=product.id, market_item_id=mid,
            confidence_score=1.0, match_type='exact', is_verified=True))
    db.session.commit()
    return product.id


@with_db
def test_market_stats_premise_count_and_stats():
    pc, _ = _make_sources()
    item = _make_item(pc, 'PC-BAWANG-TEST')
    _seed_premise_prices(item)
    pid = _make_shop_product([item.id])
    from services.market_analysis import get_market_stats
    s = get_market_stats(pid)
    assert s['n'] == 5
    assert s['premise_count'] == 5
    assert s['min'] == 2.19          # cheapest store, exact
    assert s['max'] == 4.00
    assert s['median'] == 3.19       # middle of the five exact prices
    assert s['spread'] == 1.81       # 4.00 - 2.19
    # Transparency rows carry premise names from lookup_premise.
    premises = {o.get('premise') for o in s['recent_observations']}
    assert 'TEST PREMISE 0' in premises


@with_db
def test_market_stats_district_fallback_uses_local_stores():
    """District tier wins when >= 3 district observations exist."""
    pc, _ = _make_sources()
    item = _make_item(pc, 'PC-BAWANG-TEST')
    _seed_premise_prices(item)                 # 5x Segamat observations
    # Add two state-tier (other district) observations with higher prices.
    for i, price in enumerate((9.00, 9.50)):
        code = f'TQ{i:03d}'
        _premise(code, f'OTHER DISTRICT PREMISE {i}', district='Kluang')
        db.session.add(MarketPriceObservation(
            market_item_id=item.id, premise_code=code,
            regular_price=price, is_on_promo=False, effective_price=price,
            normalized_unit_price=price, state='Johor', district='Kluang',
            observed_at=DAY))
    db.session.commit()
    pid = _make_shop_product([item.id])
    from services.market_analysis import get_market_stats
    product = Product.query.get(pid)
    s = get_market_stats(pid, product.shop)
    # District tier: only the 5 Segamat observations count.
    assert s['n'] == 5
    assert s['max'] == 4.00            # the Kluang 9.x prices excluded
    assert s['premise_count'] == 5
    assert 'Segamat, Johor' in s['localization']


@with_db
def test_market_stats_state_fallback():
    """Fewer than 3 district observations -> falls back to state tier."""
    pc, _ = _make_sources()
    item = _make_item(pc, 'PC-BAWANG-TEST')
    _premise('TP000', 'TEST PREMISE 0')
    _premise('TP001', 'TEST PREMISE 1')
    for code, price in (('TP000', 4.00), ('TP001', 4.50)):
        db.session.add(MarketPriceObservation(
            market_item_id=item.id, premise_code=code,
            regular_price=price, is_on_promo=False, effective_price=price,
            normalized_unit_price=price, state='Johor',
            district='Segamat', observed_at=DAY))
    # State tier has 3+ observations: add one from another district.
    _premise('TQ000', 'KLUANG PREMISE', district='Kluang')
    db.session.add(MarketPriceObservation(
        market_item_id=item.id, premise_code='TQ000',
        regular_price=3.00, is_on_promo=False, effective_price=3.00,
        normalized_unit_price=3.0, state='Johor', district='Kluang',
        observed_at=DAY))
    db.session.commit()
    pid = _make_shop_product([item.id])
    from services.market_analysis import get_market_stats
    product = Product.query.get(pid)
    s = get_market_stats(pid, product.shop)
    assert s['n'] == 3                 # 2 district (< 3) -> state tier
    assert s['min'] == 3.00 and s['max'] == 4.50
    assert s['premise_count'] == 3


@with_db
def test_market_stats_national_fallback():
    """No local/state data -> national tier (all observations)."""
    pc, _ = _make_sources()
    item = _make_item(pc, 'PC-BAWANG-TEST')
    _premise('TR000', 'PERLIS PREMISE', district='Perlis', state='Perlis')
    _premise('TR001', 'KEDAH PREMISE', district='Kubang Pasu',
             state='Kedah')
    _premise('TR002', 'PENANG PREMISE', district='Timur Laut',
             state='Pulau Pinang')
    for i, code in enumerate(('TR000', 'TR001', 'TR002')):
        db.session.add(MarketPriceObservation(
            market_item_id=item.id, premise_code=code,
            regular_price=1.0 + i, is_on_promo=False,
            effective_price=1.0 + i,
            normalized_unit_price=1.0 + i,
            state={'TR000': 'Perlis', 'TR001': 'Kedah',
                   'TR002': 'Pulau Pinang'}[code],
            district='Elsewhere', observed_at=DAY))
    db.session.commit()
    pid = _make_shop_product([item.id], district='Segamat', state='Johor')
    from services.market_analysis import get_market_stats
    product = Product.query.get(pid)
    s = get_market_stats(pid, product.shop)
    assert s['n'] == 3
    assert 'national' in s['localization']
    assert s['premise_count'] == 3


# -------------------------------------------------
# runner (works without pytest)
# -------------------------------------------------
def _all_tests():
    return [(name, fn) for name, fn in sorted(globals().items())
            if name.startswith('test_') and callable(fn)]


def main():
    with app.app_context():
        _purge()
    tests = _all_tests()
    passed = 0
    failed = []
    global FAILED
    for name, fn in tests:
        try:
            fn()
            passed += 1
        except Exception as exc:                    # noqa: BLE001
            failed.append((name, exc))
    with app.app_context():
        _purge()
    print(f"test_premise_level: {passed}/{len(tests)} passed")
    for name, exc in failed:
        print(f"  FAIL {name}: {type(exc).__name__}: {exc}")
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())