"""
Phase 4E — Full FYP Integration Test (E2E)

Simulates the complete user journey through ShelfSenseAI using the Flask
test_client(), proving the system works end-to-end:

    Register → Login → Create Product → Receive Stock → Record Sale
    → Dashboard → Market Intelligence → AI Pricing

Run:
    ./venv/Scripts/python.exe tests/test_integration.py
    pytest tests/test_integration.py -v
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from app import (app, db, User, Shop, Product, Inventory,  # noqa: E402
                 Sale, PriceHistory, InventoryAdjustment)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
PW = 'IntegrationTest123!'
TEST_SHOP = 'IntegrationTestShop'
TEST_EMAIL = 'integration_owner@shelfsense.my'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _purge():
    """Remove all test data created during this integration test."""
    with app.app_context():
        shop = Shop.query.filter_by(name=TEST_SHOP).first()
        if shop is None:
            return
        shop_id = shop.id

        # Delete in FK-safe order: children first, then parents
        # 1. Audit / adjustment records
        db.session.execute(text(
            "DELETE FROM inventory_adjustment WHERE shop_id = :sid"),
            {'sid': shop_id})
        # 2. Sales
        db.session.execute(text(
            "DELETE FROM sale WHERE product_id IN "
            "(SELECT id FROM product WHERE shop_id = :sid)"),
            {'sid': shop_id})
        # 3. Inventory
        db.session.execute(text(
            "DELETE FROM inventory WHERE shop_id = :sid"),
            {'sid': shop_id})
        # 4. Price history
        db.session.execute(text(
            "DELETE FROM price_history WHERE product_id IN "
            "(SELECT id FROM product WHERE shop_id = :sid)"),
            {'sid': shop_id})
        # 5. Market matches
        db.session.execute(text(
            "DELETE FROM product_market_match WHERE shop_product_id IN "
            "(SELECT id FROM product WHERE shop_id = :sid)"),
            {'sid': shop_id})
        # 6. Products
        db.session.execute(text(
            "DELETE FROM product WHERE shop_id = :sid"),
            {'sid': shop_id})
        # 7. Notifications for all users in this shop
        db.session.execute(text(
            "DELETE FROM notification WHERE user_id IN "
            "(SELECT id FROM user WHERE shop_id = :sid)"),
            {'sid': shop_id})
        # 8. Shop invitations
        db.session.execute(text(
            "DELETE FROM shop_invitation WHERE shop_id = :sid"),
            {'sid': shop_id})
        # 9. ALL users in this shop (including staff created during test)
        db.session.execute(text(
            "DELETE FROM user WHERE shop_id = :sid"),
            {'sid': shop_id})
        # 10. Also delete by email in case user was unassigned
        db.session.execute(text(
            "DELETE FROM user WHERE email LIKE :pattern"),
            {'pattern': '%integration_%@shelfsense.my'})
        # 11. Finally, the shop itself
        db.session.execute(text(
            "DELETE FROM shop WHERE id = :sid"),
            {'sid': shop_id})
        db.session.commit()


def _csrf_of(client, path):
    """Extract the CSRF token from a GET request's HTML body."""
    r = client.get(path)
    m = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', r.data)
    assert m, f'no csrf token found on {path}'
    return m.group(1).decode()


def _login(client, email):
    """Log in via POST and return the CSRF token for subsequent requests."""
    tok = _csrf_of(client, '/login')
    r = client.post('/login', data={
        'email': email, 'password': PW, 'csrf_token': tok,
    })
    assert r.status_code == 302, f'login failed for {email}'
    return tok


# ---------------------------------------------------------------------------
# The Integration Test
# ---------------------------------------------------------------------------
def test_full_fyp_user_flow():
    """
    End-to-end flow: Register -> Login -> Product -> Stock -> Sale
    -> Dashboard -> Market Intel -> AI Pricing.
    """
    _purge()
    c = app.test_client()

    # ── 1. REGISTER (creates Shop + Owner) ─────────────────────────────
    tok = _csrf_of(c, '/register')
    r = c.post('/register', data={
        'account_type': 'shop',
        'shop_name': TEST_SHOP,
        'email': TEST_EMAIL,
        'password': PW,
        'confirm': PW,
        'csrf_token': tok,
    })
    assert r.status_code == 302, f'register failed: {r.status_code}'
    assert 'login' in r.headers.get('Location', ''), \
        f'register should redirect to login, got: {r.headers.get("Location")}'

    # Verify shop + user were created
    with app.app_context():
        shop = Shop.query.filter_by(name=TEST_SHOP).first()
        assert shop is not None, 'shop not created'
        user = User.query.filter_by(email=TEST_EMAIL).first()
        assert user is not None, 'user not created'
        assert user.role == 'owner'
        assert user.shop_id == shop.id
        shop_id = shop.id
        user_id = user.id

    # ── 2. LOGIN ───────────────────────────────────────────────────────
    tok = _login(c, TEST_EMAIL)

    # Verify we land on the dashboard
    r = c.get('/dashboard')
    assert r.status_code == 200
    assert b'IntegrationTestShop' in r.data

    # ── 3. CREATE PRODUCT ──────────────────────────────────────────────
    tok = _csrf_of(c, '/product/new')
    r = c.post('/product/new', data={
        'name': 'Test Rice 10kg',
        'brand': 'TestBrand',
        'category': 'BERAS',
        'quantity': '10',
        'unit': 'kg',
        'cost_price': '25.00',
        'selling_price': '30.00',
        'target_margin': '15',
        'csrf_token': tok,
    })
    assert r.status_code == 302, f'create product failed: {r.status_code}'

    # Verify product + inventory record created
    with app.app_context():
        p = Product.query.filter_by(name='Test Rice 10kg',
                                    shop_id=shop_id).first()
        assert p is not None, 'product not created'
        assert p.quantity == 10.0
        assert p.unit == 'kg'
        assert p.cost_price == 25.0
        assert p.target_margin == 15.0
        assert p.baseline_margin == 15.0
        pid = p.id

        inv = Inventory.query.filter_by(product_id=pid).first()
        assert inv is not None, 'inventory record not created'
        assert float(inv.current_stock) == 0, 'new product should start at 0 stock'

        hist = PriceHistory.query.filter_by(product_id=pid).first()
        assert hist is not None, 'price history not created'

    # ── 4. RECEIVE STOCK ───────────────────────────────────────────────
    tok = _csrf_of(c, f'/inventory/{pid}/receive')
    r = c.post(f'/inventory/{pid}/receive', data={
        'quantity_received': '20',
        'reason': 'Initial stock for integration test',
        'csrf_token': tok,
    })
    assert r.status_code == 302, f'receive stock failed: {r.status_code}'

    with app.app_context():
        inv = Inventory.query.filter_by(product_id=pid).first()
        assert float(inv.current_stock) == 20, \
            f'stock should be 20, got {inv.current_stock}'

        adj = InventoryAdjustment.query.filter_by(product_id=pid).first()
        assert adj is not None, 'adjustment record not created'
        assert float(adj.quantity_change) == 20

    # ── 5. RECORD SALE ─────────────────────────────────────────────────
    tok = _csrf_of(c, '/sales/new')
    r = c.post('/sales/new', data={
        'product_id': str(pid),
        'quantity': '3',
        'selling_price': '30.00',
        'csrf_token': tok,
    })
    assert r.status_code == 302, f'record sale failed: {r.status_code}'

    with app.app_context():
        inv = Inventory.query.filter_by(product_id=pid).first()
        assert float(inv.current_stock) == 17, \
            f'stock should be 17 after sale, got {inv.current_stock}'

        sale = Sale.query.filter_by(product_id=pid).first()
        assert sale is not None, 'sale not created'
        assert float(sale.quantity) == 3
        assert float(sale.selling_price) == 30.0

    # ── 6. DASHBOARD CHECK ─────────────────────────────────────────────
    r = c.get('/dashboard')
    assert r.status_code == 200
    assert b'Test Rice 10kg' in r.data, 'product not shown on dashboard'
    assert b'IntegrationTestShop' in r.data

    # ── 7. MARKET INTELLIGENCE (matching) ──────────────────────────────
    r = c.post(f'/api/product/{pid}/match',
               headers={'X-CSRFToken': tok})
    assert r.status_code == 200, f'match API failed: {r.status_code}'
    data = r.get_json()
    assert 'suggested' in data, 'match response missing "suggested"'
    assert 'verified' in data, 'match response missing "verified"'
    print(f'  Market matches found: {len(data["suggested"])} suggestions')

    # ── 8. AI PRICING ──────────────────────────────────────────────────
    r = c.get(f'/api/product/{pid}/pricing')
    assert r.status_code == 200, f'pricing API failed: {r.status_code}'
    pricing = r.get_json()
    assert 'recommended_price' in pricing, 'pricing missing recommended_price'
    assert 'confidence' in pricing, 'pricing missing confidence'
    assert 'llm_explanation' in pricing, 'pricing missing llm_explanation'
    assert pricing['recommended_price'] > 0, 'recommended price must be positive'
    print(f'  Recommended price: RM{pricing["recommended_price"]:.2f}')
    print(f'  Confidence: {pricing["confidence"]}')
    print(f'  LLM explanation: {pricing["llm_explanation"][:80]}...')

    # ── 9. PRODUCT DETAIL PAGE ─────────────────────────────────────────
    r = c.get(f'/product/{pid}')
    assert r.status_code == 200
    assert b'Test Rice 10kg' in r.data
    assert b'Market Intelligence' in r.data, 'Market Intelligence tab missing'
    assert b'Pricing Recommendation' in r.data, 'Pricing tab missing'

    # ── 10. ROLE PERMISSIONS ───────────────────────────────────────────
    # Staff cannot access employees page
    with app.app_context():
        staff = User(email='integration_staff@shelfsense.my',
                     role='staff', shop_id=shop_id)
        staff.set_password(PW)
        db.session.add(staff)
        db.session.commit()
        staff_email = staff.email

    # Logout owner, login as staff
    c.get('/logout')
    tok = _login(c, staff_email)

    # Staff CAN see dashboard
    r = c.get('/dashboard')
    assert r.status_code == 200

    # Staff CANNOT access employees page
    r = c.get('/employees')
    assert r.status_code == 403, f'staff should get 403 on employees, got {r.status_code}'

    # Staff CANNOT edit products
    r = c.get(f'/product/{pid}/edit')
    assert r.status_code == 403, f'staff should get 403 on edit, got {r.status_code}'

    # Staff CAN view product detail
    r = c.get(f'/product/{pid}')
    assert r.status_code == 200

    # Staff CAN record sales
    tok2 = _csrf_of(c, '/sales/new')
    # Need to login again since _csrf_of consumes a request
    c.get('/logout')
    tok = _login(c, staff_email)
    tok2 = _csrf_of(c, '/sales/new')
    r = c.post('/sales/new', data={
        'product_id': str(pid),
        'quantity': '2',
        'selling_price': '29.50',
        'csrf_token': tok2,
    })
    assert r.status_code == 302, f'staff sale failed: {r.status_code}'

    with app.app_context():
        inv = Inventory.query.filter_by(product_id=pid).first()
        assert float(inv.current_stock) == 15, \
            f'stock should be 15 after staff sale, got {inv.current_stock}'

    # ── CLEANUP ────────────────────────────────────────────────────────
    _purge()

    # Verify cleanup
    with app.app_context():
        assert Shop.query.filter_by(name=TEST_SHOP).first() is None
        assert User.query.filter_by(email=TEST_EMAIL).first() is None
        assert Product.query.filter_by(name='Test Rice 10kg').first() is None

    print('\n  All 10 integration checks passed!')


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    PASSED = FAILED = 0
    try:
        test_full_fyp_user_flow()
        PASSED += 1
        print(f'\n  test_integration: {PASSED}/{PASSED + FAILED} passed')
    except Exception as e:
        FAILED += 1
        print(f'\n  test_integration: FAIL — {e}')
        import traceback
        traceback.print_exc()
        _purge()
        sys.exit(1)
