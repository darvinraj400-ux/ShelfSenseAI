"""
Tests for the Phase 2D follow-up feature: Owner removes an employee.

Removal UNASSIGNS the user (shop_id -> NULL, role -> 'unassigned') and
creates a notification; the account row is never deleted. Safety rules:
owner-only route, no self-removal, no removing another owner, strict shop
isolation, CSRF-protected.

Run:
    ./venv/Scripts/python.exe tests/test_employee_remove.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text                                   # noqa: E402

from app import (app, db, User, Shop, Notification,           # noqa: E402
                 ShopInvitation)

PW = 'password123'
TEST_SHOP_A = 'TestShop_RemoveA'
TEST_SHOP_B = 'TestShop_RemoveB'
EMAILS = {
    'owner_a': 'owner_rm_a@shelfsense.my',
    'manager_a': 'manager_rm_a@shelfsense.my',
    'staff_a': 'staff_rm_a@shelfsense.my',
    'second_owner_a': 'owner2_rm_a@shelfsense.my',
    'owner_b': 'owner_rm_b@shelfsense.my',
}


def _purge():
    shops = "(SELECT id FROM shop WHERE name IN (:a, :b))"
    qs = [
        "DELETE FROM notification WHERE user_id IN"
        " (SELECT id FROM user WHERE email IN :emails)",
        "DELETE FROM shop_invitation WHERE shop_id IN " + shops,
        "DELETE FROM user WHERE email IN :emails",
        "DELETE FROM shop WHERE name IN (:a, :b)",
    ]
    emails = tuple(EMAILS.values())
    for q in qs:
        db.session.execute(text(q), {'a': TEST_SHOP_A, 'b': TEST_SHOP_B,
                                     'emails': emails})
    db.session.commit()


def _make_fixture():
    """Shops A (owner, manager, staff, + a second owner for the guard test)
    and B (owner). Returns {label: user} plus shop ids."""
    shop_a = Shop(name=TEST_SHOP_A)
    shop_b = Shop(name=TEST_SHOP_B)
    db.session.add_all([shop_a, shop_b])
    db.session.flush()

    def user(label, role, shop_id):
        u = User(email=EMAILS[label], role=role, shop_id=shop_id)
        u.set_password(PW)
        db.session.add(u)
        db.session.flush()
        return u

    users = {
        'owner_a': user('owner_a', 'owner', shop_a.id),
        'manager_a': user('manager_a', 'manager', shop_a.id),
        'staff_a': user('staff_a', 'staff', shop_a.id),
        # a same-shop owner (defensive guard path)
        'second_owner_a': user('second_owner_a', 'owner', shop_a.id),
        'owner_b': user('owner_b', 'owner', shop_b.id),
    }
    db.session.commit()
    return users, shop_a.id, shop_b.id


def _csrf_of(client, path):
    r = client.get(path)
    m = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', r.data)
    assert m, 'no csrf token found on ' + path
    return m.group(1).decode()


def _login(client, email):
    tok = _csrf_of(client, '/login')
    r = client.post('/login', data={'email': email, 'password': PW,
                                    'csrf_token': tok})
    assert r.status_code == 302, f'login failed for {email}'
    return tok


def _remove(client, uid, tok):
    return client.post(f'/employees/{uid}/remove',
                       headers={'X-CSRFToken': tok})


# -------------------------------------------------
# Tests
# -------------------------------------------------
def test_owner_removes_staff():
    with app.app_context():
        _purge()
        users, shop_a_id, _ = _make_fixture()
        staff_id = users['staff_a'].id          # ints - survive context exit
        owner_id = users['owner_a'].id
        n_before = Notification.query.filter_by(user_id=staff_id).count()
    c = app.test_client()
    tok = _login(c, EMAILS['owner_a'])
    r = _remove(c, staff_id, tok)
    assert r.status_code == 302 and r.headers.get('Location', '').endswith('/employees')
    with app.app_context():
        staff2 = db.session.get(User, staff_id)
        assert staff2.shop_id is None
        assert staff2.role == 'unassigned'
        assert Notification.query.filter_by(
            user_id=staff2.id, type='shop_membership').count() == n_before + 1
        # owner unchanged
        assert db.session.get(User, owner_id).role == 'owner'
        _purge()


def test_owner_removes_manager():
    with app.app_context():
        _purge()
        users, shop_a_id, _ = _make_fixture()
        mgr_id = users['manager_a'].id
    c = app.test_client()
    tok = _login(c, EMAILS['owner_a'])
    assert _remove(c, mgr_id, tok).status_code == 302
    with app.app_context():
        m2 = db.session.get(User, mgr_id)
        assert m2.shop_id is None and m2.role == 'unassigned'
        _purge()


def test_owner_cannot_remove_self():
    with app.app_context():
        _purge()
        users, shop_a_id, _ = _make_fixture()
        owner_id = users['owner_a'].id
    c = app.test_client()
    tok = _login(c, EMAILS['owner_a'])
    r = _remove(c, owner_id, tok)
    assert r.status_code == 302
    with app.app_context():
        o2 = db.session.get(User, owner_id)
        assert o2.shop_id == shop_a_id and o2.role == 'owner'
        _purge()


def test_owner_cannot_remove_another_owner():
    with app.app_context():
        _purge()
        users, shop_a_id, _ = _make_fixture()
        second_id = users['second_owner_a'].id
    c = app.test_client()
    tok = _login(c, EMAILS['owner_a'])
    r = _remove(c, second_id, tok)
    assert r.status_code == 302
    with app.app_context():
        s2 = db.session.get(User, second_id)
        assert s2.shop_id == shop_a_id and s2.role == 'owner'
        _purge()


def test_manager_and_staff_cannot_remove():
    with app.app_context():
        _purge()
        users, _, _ = _make_fixture()
        staff_id = users['staff_a'].id
    cm = app.test_client()
    m_tok = _login(cm, EMAILS['manager_a'])
    assert _remove(cm, staff_id, m_tok).status_code == 403
    cs = app.test_client()
    s_tok = _login(cs, EMAILS['staff_a'])
    assert _remove(cs, staff_id, s_tok).status_code == 403
    with app.app_context():
        assert db.session.get(User, staff_id).shop_id is not None
        _purge()


def test_cross_shop_removal_blocked():
    with app.app_context():
        _purge()
        users, _, _ = _make_fixture()
        staff_id = users['staff_a'].id
    cb = app.test_client()
    b_tok = _login(cb, EMAILS['owner_b'])
    assert _remove(cb, staff_id, b_tok).status_code == 403
    with app.app_context():
        assert db.session.get(User, staff_id).shop_id is not None
        _purge()


def test_remove_requires_csrf():
    with app.app_context():
        _purge()
        users, _, _ = _make_fixture()
        staff_id = users['staff_a'].id
    c = app.test_client()
    _login(c, EMAILS['owner_a'])
    # no CSRF token -> CSRFProtect rejects the POST
    r = c.post(f'/employees/{staff_id}/remove')
    assert r.status_code != 302 or not r.headers.get('Location', '').endswith('/employees')
    with app.app_context():
        assert db.session.get(User, staff_id).shop_id is not None
        _purge()


def test_removed_employee_can_be_reinvited():
    with app.app_context():
        _purge()
        users, shop_a_id, _ = _make_fixture()
        staff = users['staff_a']
        owner_id = users['owner_a'].id
        email = staff.email
        # remove the staff member
        staff.shop_id = None
        staff.role = 'unassigned'
        db.session.commit()
        before = ShopInvitation.query.filter_by(
            shop_id=shop_a_id, email=email, status='pending').count()
    c = app.test_client()
    tok = _login(c, EMAILS['owner_a'])
    # owner re-invites the same email (now unassigned, so no member guard)
    r = c.post('/employees', data={'email': email, 'role': 'staff',
                                   'csrf_token': tok})
    assert r.status_code == 302
    with app.app_context():
        assert ShopInvitation.query.filter_by(
            shop_id=shop_a_id, email=email,
            status='pending').count() == before + 1
        _purge()


# -------------------------------------------------
# runner
# -------------------------------------------------
def _all_tests():
    return [(name, fn) for name, fn in sorted(globals().items())
            if name.startswith('test_') and callable(fn)]


def main():
    # IMPORTANT: no persistent app context around the loop. Flask-Login caches
    # the current user on `g`, which is scoped to the APP context - a long-
    # lived app context would leak one client's login into all later clients
    # (every request would see the first user as authenticated). Each test
    # wraps its own DB work in app_context(); client requests then run clean.
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
    print(f"test_employee_remove: {passed}/{len(tests)} passed")
    for name, exc in failed:
        print(f"  FAIL {name}: {type(exc).__name__}: {exc}")
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
