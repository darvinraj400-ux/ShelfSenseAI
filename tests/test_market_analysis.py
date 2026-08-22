"""
Tests for the Phase 3D market analysis service + API.

Part 1 - PURE unit tests of compute_metrics (median/mean parity, invalid
    price filtering, PPI above/below/at median, empty input).
Part 2 - REAL-DB integration (project MySQL, full cleanup): get_market_stats
    for 0 / 1 / multiple verified matches, scaling to the product's package
    size, package-less products, and the API endpoint (roles + isolation).

Run:
    ./venv/Scripts/python.exe tests/test_market_analysis.py
"""
import os
import re
import sys
from statistics import median, mean

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text                                   # noqa: E402

from app import (app, db, User, Shop, Product,               # noqa: E402
                 ProductMarketMatch, MarketItem,
                 MarketPriceObservation)
from services.market_analysis import (compute_metrics,        # noqa: E402
                                       get_market_stats)


# -------------------------------------------------
# Part 1 - pure metrics
# -------------------------------------------------
def test_metrics_basic_odd_count():
    m = compute_metrics([10, 12, 14, 16, 18])
    assert m['n'] == 5
    assert m['min'] == 10 and m['max'] == 18
    assert m['median'] == 14 and m['mean'] == 14
    assert m['spread'] == 8


def test_metrics_even_count_median_is_average_of_middle():
    m = compute_metrics([10, 20, 30, 40])
    assert m['median'] == 25
    assert m['mean'] == 25
    assert m['min'] == 10 and m['max'] == 40


def test_metrics_filters_invalid_prices():
    m = compute_metrics([10, 0, -5, None, 20, 0.0])
    assert m['n'] == 2
    assert m['min'] == 10 and m['max'] == 20


def test_ppi_above_median():
    m = compute_metrics([25, 25, 25], shop_price=26.0)
    assert m['ppi'] == 104.0
    assert m['comparison'] == {'pct': 4.0, 'above': True, 'at_median': False}


def test_ppi_below_median():
    m = compute_metrics([25, 25, 25], shop_price=20.0)
    assert m['ppi'] == 80.0
    assert m['comparison'] == {'pct': 20.0, 'above': False,
                               'at_median': False}


def test_ppi_at_median():
    m = compute_metrics([25, 25, 25], shop_price=25.0)
    assert m['ppi'] == 100.0
    assert m['comparison']['at_median'] is True


def test_ppi_without_shop_price():
    m = compute_metrics([25, 25, 25], shop_price=None)
    assert m['ppi'] is None and m['comparison'] is None


def test_metrics_empty_input():
    m = compute_metrics([])
    assert m['n'] == 0
    for k in ('min', 'max', 'mean', 'median', 'spread', 'ppi',
              'comparison'):
        assert m[k] is None, k


# -------------------------------------------------
# Part 2 - real DB integration
# -------------------------------------------------
TEST_SHOP_A = 'TestShop_AnalysisA'
TEST_SHOP_B = 'TestShop_AnalysisB'
EMAILS = {'owner_a': 'owner_an@shelfsense.my',
          'owner_b': 'owner_anb@shelfsense.my'}
PW = 'password123'
PRODUCT_A = 'BERAS CAP JASMINE (SST5%)'   # 10 kg, sells at RM26
PRODUCT_B = 'SUGAR LOOSE PACK'            # no package size defined


def _purge():
    shops = "(SELECT id FROM shop WHERE name IN (:a, :b))"
    products = f"(SELECT id FROM product WHERE shop_id IN {shops})"
    for q in [
        f"DELETE FROM product_market_match WHERE shop_product_id IN {products}",
        f"DELETE FROM product WHERE shop_id IN {shops}",
        "DELETE FROM user WHERE email IN (:ea, :eb)",
        f"DELETE FROM shop WHERE name IN (:a, :b)",
    ]:
        db.session.execute(text(q), {'a': TEST_SHOP_A, 'b': TEST_SHOP_B,
                                     'ea': EMAILS['owner_a'],
                                     'eb': EMAILS['owner_b']})
    db.session.commit()


def _make_fixture():
    shop_a = Shop(name=TEST_SHOP_A)
    shop_b = Shop(name=TEST_SHOP_B)
    db.session.add_all([shop_a, shop_b])
    db.session.flush()

    def user(email, shop_id):
        u = User(email=email, role='owner', shop_id=shop_id)
        u.set_password(PW)
        db.session.add(u)
        db.session.flush()
        return u

    owner_a = user(EMAILS['owner_a'], shop_a.id)
    owner_b = user(EMAILS['owner_b'], shop_b.id)

    product_a = Product(name=PRODUCT_A, category='BERAS', quantity=10,
                        unit='kg', cost_price=20.0, selling_price=26.0,
                        target_margin=30.0, shop_id=shop_a.id)
    product_b = Product(name=PRODUCT_B, category='GULA', quantity=None,
                        unit=None, cost_price=3.0, selling_price=3.5,
                        target_margin=16.0, shop_id=shop_a.id)
    db.session.add_all([product_a, product_b])
    db.session.flush()
    db.session.commit()
    return (shop_a.id, shop_b.id, owner_a.id, owner_b.id,
            product_a.id, product_b.id)


def _beras_market_items():
    """Two real market items in BERAS with observations, for linking."""
    rows = db.session.execute(text("""
        SELECT mi.id FROM market_item mi
        WHERE mi.category = 'BERAS'
          AND EXISTS (SELECT 1 FROM market_price_observation o
                      WHERE o.market_item_id = mi.id)
        ORDER BY mi.id LIMIT 2""")).fetchall()
    return [r[0] for r in rows]


def _obs_prices(market_item_id):
    """All observation normalized unit prices for an item."""
    rows = db.session.execute(text("""
        SELECT normalized_unit_price FROM market_price_observation
        WHERE market_item_id = :mid AND normalized_unit_price > 0
        ORDER BY observed_at"""), {'mid': market_item_id}).fetchall()
    return [float(r[0]) for r in rows]


def test_stats_with_no_verified_matches():
    with app.app_context():
        _purge()
        (_, _, _, _, product_a_id, _) = _make_fixture()
        s = get_market_stats(product_a_id)
        assert s['n'] == 0 and s['match_count'] == 0
        assert s['min'] is None and s['median'] is None
        assert s['product_name'] == PRODUCT_A
        assert s['shop_price'] == 26.0
        assert s['has_package'] is True
        assert s['package_label'] == '10 kg'
        _purge()


def test_stats_one_verified_match_scaled_to_product_size():
    with app.app_context():
        _purge()
        (_, _, _, _, product_a_id, _) = _make_fixture()
        item_id = _beras_market_items()[0]
        db.session.add(ProductMarketMatch(shop_product_id=product_a_id,
                                          market_item_id=item_id,
                                          confidence_score=1.0,
                                          match_type='exact',
                                          is_verified=True))
        db.session.commit()

        raw = _obs_prices(item_id)
        assert raw, 'test market item unexpectedly has no observations'
        # product is 10 kg -> each RM/kg observation scales x10
        expected_scaled = [p * 10 for p in raw]

        s = get_market_stats(product_a_id)
        assert s['match_count'] == 1
        assert s['n'] == len(raw)
        assert s['min'] == round(min(expected_scaled), 2)
        assert s['max'] == round(max(expected_scaled), 2)
        assert s['median'] == round(median(expected_scaled), 2)
        assert s['mean'] == round(mean(expected_scaled), 2)
        assert s['spread'] == round(max(expected_scaled)
                                    - min(expected_scaled), 2)
        # 26.0 / median x 100
        assert s['ppi'] == round(26.0 / median(expected_scaled) * 100, 1)
        assert s['matches'][0]['market_item_id'] == item_id
        _purge()


def test_stats_multiple_verified_matches_combined():
    with app.app_context():
        _purge()
        (_, _, _, _, product_a_id, _) = _make_fixture()
        item1, item2 = _beras_market_items()
        for it in (item1, item2):
            db.session.add(ProductMarketMatch(shop_product_id=product_a_id,
                                              market_item_id=it,
                                              confidence_score=0.9,
                                              match_type='fuzzy',
                                              is_verified=True))
        db.session.commit()
        s = get_market_stats(product_a_id)
        expected_n = len(_obs_prices(item1)) + len(_obs_prices(item2))
        assert s['match_count'] == 2
        assert s['n'] == expected_n
        assert len(s['matches']) == 2
        _purge()


def test_stats_product_without_package_uses_base_unit():
    with app.app_context():
        _purge()
        (_, _, _, _, _, product_b_id) = _make_fixture()
        item_id = _beras_market_items()[0]
        db.session.add(ProductMarketMatch(shop_product_id=product_b_id,
                                          market_item_id=item_id,
                                          confidence_score=0.9,
                                          match_type='fuzzy',
                                          is_verified=True))
        db.session.commit()
        s = get_market_stats(product_b_id)
        assert s['has_package'] is False
        assert s['package_label'] is None
        assert 'per base unit' in s['scaling_note']
        raw = _obs_prices(item_id)
        assert s['n'] == len(raw)
        # unscaled: market median is the raw RM/kg median
        assert s['median'] == round(median(raw), 2)
        _purge()


def test_api_market_stats_endpoint():
    with app.app_context():
        _purge()
        (shop_a_id, shop_b_id, owner_a_id, owner_b_id,
         product_a_id, _) = _make_fixture()
        item_id = _beras_market_items()[0]
        db.session.add(ProductMarketMatch(shop_product_id=product_a_id,
                                          market_item_id=item_id,
                                          confidence_score=1.0,
                                          match_type='exact',
                                          is_verified=True))
        db.session.commit()
        pid = product_a_id

    def login(client, email):
        r = client.get('/login')
        tok = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"',
                        r.data).group(1).decode()
        assert client.post('/login', data={'email': email, 'password': PW,
                                           'csrf_token': tok}
                           ).status_code == 302
        return tok

    c = app.test_client()
    tok = login(c, EMAILS['owner_a'])
    r = c.get(f'/api/product/{pid}/market-stats')
    assert r.status_code == 200
    body = r.get_json()
    assert body['product_id'] == pid
    assert body['match_count'] == 1
    assert body['n'] > 0
    assert body['median'] is not None and body['ppi'] is not None
    assert isinstance(body['comparison'], dict)

    # cross-shop owner is blocked
    cb = app.test_client()
    login(cb, EMAILS['owner_b'])
    assert cb.get(f'/api/product/{pid}/market-stats').status_code == 403

    with app.app_context():
        _purge()


# -------------------------------------------------
# runner
# -------------------------------------------------
def _all_tests():
    return [(name, fn) for name, fn in sorted(globals().items())
            if name.startswith('test_') and callable(fn)]


def main():
    # No persistent app context around client tests (Flask-Login caches the
    # user on `g`, which leaks across clients inside one app context).
    with app.app_context():
        _purge()
    tests = _all_tests()
    passed = 0
    failed = []
    for name, fn in tests:
        try:
            fn()
            passed += 1
        except Exception as exc:                    # noqa: BLE001
            failed.append((name, exc))
    with app.app_context():
        _purge()
    print(f"test_market_analysis: {passed}/{len(tests)} passed")
    for name, exc in failed:
        print(f"  FAIL {name}: {type(exc).__name__}: {exc}")
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
