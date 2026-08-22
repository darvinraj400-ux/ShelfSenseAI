"""
Tests for the Phase 3C product matching service + Market Intelligence API.

Part 1 - PURE unit tests of the scoring logic (no database, stub objects):
    package_score, exact pass, fuzzy pass, package penalty, confidence
    floor, ranking.

Part 2 - REAL-DB integration tests (project's MySQL, single app context,
    full cleanup - the DB is restored to its pre-test state):
    apply_suggestions, refresh semantics, detail page, verify/reject/
    remove API, roles, shop isolation, CSRF.

Run:
    ./venv/Scripts/python.exe tests/test_matching.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text                                  # noqa: E402

from decimal import Decimal                                   # noqa: E402

from app import (app, db, User, Shop, Product,               # noqa: E402
                 ProductMarketMatch, MarketItem)
from services.matching import (package_score, score_product, # noqa: E402
                               find_matches, apply_suggestions,
                               MIN_CONFIDENCE, TOP_K)


# -------------------------------------------------
# Part 1 - pure scoring logic
# -------------------------------------------------
class StubItem:
    def __init__(self, iid, normalized_title, brand=None,
                 package_quantity=1.0, package_unit='kg'):
        self.id = iid
        self.normalized_title = normalized_title
        self.brand = brand
        self.package_quantity = package_quantity
        self.package_unit = package_unit


class StubProduct:
    def __init__(self, name, brand=None, quantity=None, unit=None):
        self.name = name
        self.brand = brand
        self.quantity = quantity
        self.unit = unit


def test_package_score_same_unit_tolerance():
    # 500 g vs 0.5 kg -> both normalize to 0.5 kg -> 1.0
    assert package_score(500, 'g', 0.5, 'kg') == 1.0
    assert package_score(10, 'kg', 10, 'kg') == 1.0
    # 10 kg vs 10.4 kg (within +-5%) -> 1.0
    assert package_score(10, 'kg', 10.4, 'kg') == 1.0


def test_package_score_same_unit_different_quantity():
    # 10 kg vs 1 kg -> same base unit, quantity off by far -> 0.6
    assert package_score(10, 'kg', 1, 'kg') == 0.6
    assert package_score(10, 'kg', 20, 'kg') == 0.6


def test_package_score_different_unit():
    assert package_score(1, 'kg', 1, 'l') == 0.3
    assert package_score(250, 'ml', 500, 'g') == 0.3   # l vs kg


def test_package_score_unknown_product_package():
    assert package_score(None, None, 1, 'kg') == 0.0
    assert package_score(1, '', 1, 'kg') == 0.0


def test_exact_pass_identical_title():
    items = [StubItem(1, 'beras cap jasmine', package_quantity=10, package_unit='kg'),
             StubItem(2, 'susu pekat manis', package_quantity=1, package_unit='kg')]
    res = score_product(StubProduct('  BERAS CAP JASMINE ', quantity=10, unit='kg'),
                        items)
    assert res and res[0][1] == 1.00 and res[0][2] == 'exact'
    assert res[0][0].id == 1


def test_exact_pass_brand_containment():
    items = [StubItem(1, 'sos cili adabi', brand='Adabi')]
    res = score_product(StubProduct('SOS CILI ADABI EXTRA PEDAS', brand='adabi'),
                        items)
    # NOTE: compare against Decimal('0.95') - Decimal == float compares the
    # float's EXACT binary value (0.9499999...), which is not equal.
    assert res and res[0][2] == 'exact' and res[0][1] == Decimal('0.95')


def test_fuzzy_pass_finds_similar():
    items = [StubItem(1, 'beras cap jasmine', package_quantity=10, package_unit='kg')]
    res = score_product(StubProduct('BERAS CAP JASMINE PREMIUM', quantity=10,
                                    unit='kg'), items)
    assert any(t == 'fuzzy' for _, _, t in res)
    assert res[0][1] >= MIN_CONFIDENCE


def test_package_penalty_lowers_confidence():
    # Same fuzzy title, but the 1 kg package must score lower than the 10 kg one.
    items = [StubItem(1, 'beras cap jasmine', package_quantity=10, package_unit='kg'),
             StubItem(2, 'beras cap jasmine', package_quantity=1, package_unit='kg')]
    res = score_product(StubProduct('BERAS CAP JASMINE SPECIAL', quantity=10,
                                    unit='kg'), items)
    conf = {it.id: c for it, c, _ in res}
    assert 1 in conf and 2 in conf
    assert conf[1] > conf[2], '10kg match must beat the 1kg match'


def test_confidence_floor_filters_dissimilar():
    items = [StubItem(1, 'tuala wanita kotex', package_quantity=10, package_unit='unit'),
             StubItem(2, 'majalah cleo', package_quantity=1, package_unit='unit')]
    res = score_product(StubProduct('BERAS CAP JASMINE', quantity=10, unit='kg'),
                        items)
    assert res == []


def test_score_product_ranks_exact_first_and_respects_top_k():
    items = [StubItem(i, f'beras cap jasmine var {i}', package_quantity=10,
                      package_unit='kg') for i in range(1, 8)]
    items.append(StubItem(99, 'beras cap jasmine', package_quantity=10,
                          package_unit='kg'))
    res = score_product(StubProduct('BERAS CAP JASMINE', quantity=10, unit='kg'),
                        items, top_k=3)
    # exact match first
    assert res[0][0].id == 99 and res[0][2] == 'exact'
    # at most top_k fuzzy suggestions follow
    assert len(res) <= 4  # 1 exact + 3 fuzzy
    assert all(t == 'exact' or t == 'fuzzy' for _, _, t in res)


# -------------------------------------------------
# Part 2 - real DB integration (fixture + cleanup)
# -------------------------------------------------
TEST_SHOP_A = 'TestShop_MatchA'
TEST_SHOP_B = 'TestShop_MatchB'
TEST_USERS = {'owner_a', 'manager_a', 'staff_a', 'owner_b'}
PW = 'password123'
# This exact name normalizes (clean_text) to the real market title
# 'beras cap jasmine sst5' -> the exact pass must hit it at 1.00.
TEST_PRODUCT = 'BERAS CAP JASMINE (SST5%)'


def _purge():
    # FK-safe order: children before parents. Scoped to the TEST SHOPS only
    # (never by product name - demo data may share names with fixtures).
    shops = "(SELECT id FROM shop WHERE name IN (:a, :b))"
    products = f"(SELECT id FROM product WHERE shop_id IN {shops})"
    for q in [
        f"DELETE FROM product_market_match WHERE shop_product_id IN {products}",
        f"DELETE FROM inventory_adjustment WHERE product_id IN {products}",
        f"DELETE FROM price_history WHERE product_id IN {products}",
        f"DELETE FROM inventory WHERE product_id IN {products}",
        f"DELETE FROM sale WHERE product_id IN {products}",
        f"DELETE FROM product WHERE shop_id IN {shops}",
        "DELETE FROM user WHERE email IN"
        " ('owner_a@shelfsense.my','manager_a@shelfsense.my',"
        "  'staff_a@shelfsense.my','owner_b@shelfsense.my')",
        f"DELETE FROM shop WHERE name IN (:a, :b)",
    ]:
        db.session.execute(text(q), {'pn': TEST_PRODUCT,
                                     'a': TEST_SHOP_A, 'b': TEST_SHOP_B})
    db.session.commit()


def _make_fixture():
    """Shop A (owner/manager/staff) + product 'BERAS CAP JASMINE' 10 kg,
    Shop B (owner). Returns (shop_a, owner_a, manager_a, staff_a, product_a,
    shop_b, owner_b)."""
    shop_a = Shop(name=TEST_SHOP_A)
    shop_b = Shop(name=TEST_SHOP_B)
    db.session.add_all([shop_a, shop_b])
    db.session.flush()

    def user(email, role, shop_id):
        u = User(email=email, role=role, shop_id=shop_id)
        u.set_password(PW)
        db.session.add(u)
        db.session.flush()
        return u

    owner_a = user('owner_a@shelfsense.my', 'owner', shop_a.id)
    manager_a = user('manager_a@shelfsense.my', 'manager', shop_a.id)
    staff_a = user('staff_a@shelfsense.my', 'staff', shop_a.id)
    owner_b = user('owner_b@shelfsense.my', 'owner', shop_b.id)

    product_a = Product(name=TEST_PRODUCT, brand=None, category='BERAS',
                        quantity=10, unit='kg', cost_price=20.0,
                        selling_price=26.0, target_margin=30.0,
                        shop_id=shop_a.id)
    db.session.add(product_a)
    db.session.flush()
    db.session.commit()
    return (shop_a, owner_a, manager_a, staff_a, product_a,
            shop_b, owner_b)


def _suggestions_of(product):
    return ProductMarketMatch.query.filter_by(
        shop_product_id=product.id, is_verified=False,
        is_rejected=False).all()


# --- pure DB tests -------------------------------------------------
def test_apply_suggestions_creates_exact_and_fuzzy():
    with app.app_context():
        _purge()
        *_, product_a, _, _ = _make_fixture()
        before = ProductMarketMatch.query.count()
        n = apply_suggestions(product_a)
        db.session.commit()
        rows = _suggestions_of(product_a)
        assert n == len(rows) and n > 0
        # the exact 'BERAS CAP JASMINE' market item must be among them at 1.00
        exact = [r for r in rows if float(r.confidence_score) == 1.00
                 and r.match_type == 'exact']
        assert exact, ('expected an exact 1.00 match, got: '
                       f'{[(r.match_type, r.confidence_score) for r in rows]}')
        # every suggestion points at a real market item
        for r in rows:
            assert db.session.get(MarketItem, r.market_item_id) is not None
        assert ProductMarketMatch.query.count() == before + n
        _purge()


def test_refresh_keeps_verified_and_rejected():
    with app.app_context():
        _purge()
        *_, product_a, _, _ = _make_fixture()
        apply_suggestions(product_a)
        db.session.commit()
        rows = _suggestions_of(product_a)
        assert len(rows) > 1

        # verify the best, reject the second-best
        rows.sort(key=lambda r: float(r.confidence_score or 0), reverse=True)
        rows[0].is_verified = True
        rows[1].is_rejected = True
        db.session.commit()
        verified_id, rejected_id = rows[0].id, rows[1].id

        # re-run matching: stale suggestions refresh, verified/rejected survive
        apply_suggestions(product_a)
        db.session.commit()
        all_rows = ProductMarketMatch.query.filter_by(
            shop_product_id=product_a.id).all()
        ids = {r.id for r in all_rows}
        assert verified_id in ids and rejected_id in ids
        v = next(r for r in all_rows if r.id == verified_id)
        rj = next(r for r in all_rows if r.id == rejected_id)
        assert v.is_verified is True and rj.is_rejected is True
        _purge()


def test_find_matches_excludes_verified_and_rejected():
    with app.app_context():
        _purge()
        *_, product_a, _, _ = _make_fixture()
        apply_suggestions(product_a)
        db.session.commit()
        rows = _suggestions_of(product_a)
        rows[0].is_verified = True
        rows[1].is_rejected = True
        db.session.commit()
        matches = find_matches(product_a)
        found = {m.id for m, _, _ in matches}
        assert rows[0].market_item_id not in found
        assert rows[1].market_item_id not in found
        assert len(matches) > 0
        _purge()


# --- API / route tests ----------------------------------------------
def _csrf_of(client, path):
    r = client.get(path)
    m = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', r.data)
    assert m, 'no csrf token found on ' + path
    return m.group(1).decode()


def _login(client, email):
    """Log in and return the session's CSRF token (captured PRE-login -
    after login the /login page 302s away and has no token). The token is
    stable for the session, so it also works for API calls."""
    tok = _csrf_of(client, '/login')
    r = client.post('/login', data={'email': email, 'password': PW,
                                    'csrf_token': tok})
    assert r.status_code == 302, f'login failed for {email}'
    return tok


def _api(client, url, method='POST', tok=None):
    return client.open(url, method=method,
                       headers={'X-CSRFToken': tok} if tok else {})


def test_detail_page_and_api_permissions():
    with app.app_context():
        _purge()
        *_, product_a, _, owner_b = _make_fixture()
        apply_suggestions(product_a)
        db.session.commit()
        pid = product_a.id

    # owner sees the detail page
    c = app.test_client()
    tok = _login(c, 'owner_a@shelfsense.my')
    r = c.get(f'/product/{pid}')
    assert r.status_code == 200
    assert b'Market Intelligence' in r.data

    # staff can view but cannot verify / re-match (valid CSRF token, so the
    # role check - not the CSRF check - is what rejects them)
    cs = app.test_client()
    staff_tok = _login(cs, 'staff_a@shelfsense.my')
    assert cs.get(f'/product/{pid}').status_code == 200
    assert _api(cs, f'/api/product/{pid}/match',
                tok=staff_tok).status_code == 403
    assert _api(cs, f'/api/market-match/1/verify',
                tok=staff_tok).status_code == 403

    # cross-shop owner is blocked on both the page and the API
    cb = app.test_client()
    _login(cb, 'owner_b@shelfsense.my')
    assert cb.get(f'/product/{pid}').status_code == 403
    b_tok = None
    assert _api(cb, f'/api/product/{pid}/market', method='GET',
                tok=b_tok).status_code == 403

    # manager can verify
    cm = app.test_client()
    m_tok = _login(cm, 'manager_a@shelfsense.my')
    state = _api(cm, f'/api/product/{pid}/market', method='GET',
                 tok=m_tok).get_json()
    assert state and state['suggested']
    mid = state['suggested'][0]['match_id']
    r = _api(cm, f'/api/market-match/{mid}/verify', tok=m_tok)
    assert r.status_code == 200
    body = r.get_json()
    assert any(v['match_id'] == mid for v in body['verified'])
    assert all(s['match_id'] != mid for s in body['suggested'])

    with app.app_context():
        _purge()


def test_verify_reject_remove_lifecycle_and_csrf():
    with app.app_context():
        _purge()
        *_, product_a, _, _ = _make_fixture()
        apply_suggestions(product_a)
        db.session.commit()
        pid = product_a.id

    c = app.test_client()
    tok = _login(c, 'owner_a@shelfsense.my')

    state = _api(c, f'/api/product/{pid}/market', method='GET',
                 tok=tok).get_json()
    suggested = state['suggested']
    assert len(suggested) >= 2
    mid1, mid2 = suggested[0]['match_id'], suggested[1]['match_id']

    # reject the second suggestion -> it disappears permanently
    r = _api(c, f'/api/market-match/{mid2}/reject', tok=tok)
    assert r.status_code == 200
    assert all(s['match_id'] != mid2 for s in r.get_json()['suggested'])

    # verifying it afterwards is rejected with 400
    assert _api(c, f'/api/market-match/{mid2}/verify', tok=tok).status_code == 400

    # verify the first -> moves to verified
    r = _api(c, f'/api/market-match/{mid1}/verify', tok=tok)
    assert r.status_code == 200
    body = r.get_json()
    assert any(v['match_id'] == mid1 for v in body['verified'])

    # rejecting a verified link is refused (400); removing works
    assert _api(c, f'/api/market-match/{mid1}/reject', tok=tok).status_code == 400
    r = _api(c, f'/api/market-match/{mid1}', method='DELETE', tok=tok)
    assert r.status_code == 200
    assert all(v['match_id'] != mid1 for v in r.get_json()['verified'])

    # CSRF: a POST without the token is not accepted
    r = c.post(f'/api/product/{pid}/match')
    assert r.status_code != 200

    with app.app_context():
        _purge()


# -------------------------------------------------
# runner
# -------------------------------------------------
def _all_tests():
    return [(name, fn) for name, fn in sorted(globals().items())
            if name.startswith('test_') and callable(fn)]


def main():
    tests = _all_tests()
    passed = 0
    failed = []
    for name, fn in tests:
        try:
            fn()
            passed += 1
        except Exception as exc:                        # noqa: BLE001
            failed.append((name, exc))
        finally:
            with app.app_context():
                _purge()
    print(f"test_matching: {passed}/{len(tests)} passed")
    for name, exc in failed:
        print(f"  FAIL {name}: {type(exc).__name__}: {exc}")
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
