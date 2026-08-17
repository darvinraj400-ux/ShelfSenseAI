"""
Real-database tests for the Phase 3A market-data models
(MarketSource, MarketItem, MarketPriceObservation, ProductMarketMatch).

These use the project's real MySQL database and the established
cleanup pattern: every row created here is deleted in `finally`,
so the database is restored to its pre-test state.

IMPORTANT pattern note: the WHOLE run happens inside ONE
`app.app_context()` (see main()). Flask-SQLAlchemy 3.x gives each
nested app context its own session, so opening a `with
app.app_context():` block inside a helper would attach ORM objects
to a different session than the caller - the helpers here therefore
assume the caller already provides the app context.

Run standalone:
    ./venv/Scripts/python.exe tests/test_market_models.py

or collect with pytest (if ever added). Requires the Phase 3A
migration to have been applied (`flask db upgrade`).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text                          # noqa: E402
from sqlalchemy.exc import IntegrityError            # noqa: E402

from app import (app, db, MarketSource, MarketItem,  # noqa: E402
                 MarketPriceObservation, ProductMarketMatch,
                 Product, Shop)
from utils.normalization import clean_text            # noqa: E402


# Unique names so a crashed previous run can be detected and purged.
TEST_SOURCE_NAME = 'TestSource_3A'
TEST_SHOP_NAME = 'TestShop_3A'
TEST_PRODUCT_NAME = 'TestProduct_3A'

# FK-safe delete order: children before parents.
_PURGE_SQL = [
    "DELETE FROM product_market_match"
    " WHERE shop_product_id IN"
    " (SELECT id FROM product WHERE name = :pn)",
    "DELETE FROM market_price_observation"
    " WHERE market_item_id IN"
    " (SELECT id FROM market_item WHERE source_id IN"
    "  (SELECT id FROM market_source WHERE name = :sn))",
    "DELETE FROM market_item"
    " WHERE source_id IN (SELECT id FROM market_source WHERE name = :sn)",
    "DELETE FROM market_source WHERE name = :sn",
    "DELETE FROM product WHERE name = :pn",
    "DELETE FROM shop WHERE name = :shn",
]


def _purge_leftovers():
    """Remove any rows left behind by a previously crashed run."""
    for q in _PURGE_SQL:
        db.session.execute(text(q), {'pn': TEST_PRODUCT_NAME,
                                     'sn': TEST_SOURCE_NAME,
                                     'shn': TEST_SHOP_NAME})
    db.session.commit()


def _counts():
    """Row counts for the tables under test + the baseline product/shop tables."""
    return {
        'market_source': MarketSource.query.count(),
        'market_item': MarketItem.query.count(),
        'market_price_observation': MarketPriceObservation.query.count(),
        'product_market_match': ProductMarketMatch.query.count(),
        'product': Product.query.count(),
        'shop': Shop.query.count(),
    }


def _create_fixture():
    """Create the full chain: source -> item -> observation, plus a shop
    product to link to. Returns the objects. Caller must clean up."""
    source = MarketSource(name=TEST_SOURCE_NAME,
                          source_type='government', is_active=True)
    db.session.add(source)
    db.session.flush()

    item = MarketItem(
        source_id=source.id,
        external_id='PC-TEST-001',
        raw_title='  BERAS CAP JASMINE\u2122 500g ',
        normalized_title=clean_text('  BERAS CAP JASMINE\u2122 500g '),
        brand='TestBrand',
        category='BERAS',
        package_quantity=500, package_unit='g',
    )
    db.session.add(item)
    db.session.flush()

    # On-promo observation: effective price must derive to promo_price
    # and normalized unit price must derive to RM per kg (10 / 0.5 kg = 20).
    obs = MarketPriceObservation(
        market_item=item,
        regular_price=12.00,
        promo_price=10.00,
        is_on_promo=True,
    )
    db.session.add(obs)
    db.session.flush()

    shop = Shop(name=TEST_SHOP_NAME)
    db.session.add(shop)
    db.session.flush()

    product = Product(name=TEST_PRODUCT_NAME, cost_price=8.0,
                      target_margin=20.0, shop_id=shop.id)
    db.session.add(product)
    db.session.flush()

    match = ProductMarketMatch(
        shop_product_id=product.id,
        market_item_id=item.id,
        confidence_score=0.95,
        match_type='exact',
        is_verified=False,
    )
    db.session.add(match)
    db.session.commit()
    return source, item, obs, shop, product, match


def _cleanup(source, item, obs, shop, product, match):
    """Remove the fixture rows in FK-safe order.
    Uses raw SQL on purpose: ORM delete() with the loaded
    `product.market_matches` collection (cascade='all, delete-orphan')
    would re-delete an already-deleted match and emit a spurious
    '0 rows matched' warning. Raw SQL deletes are deterministic."""
    _purge_leftovers()


# -------------------------------------------------
# Tests
# -------------------------------------------------
def test_market_source_creation():
    before = _counts()
    source, item, obs, shop, product, match = _create_fixture()
    try:
        # MarketSource basics
        assert source.name == TEST_SOURCE_NAME
        assert source.source_type == 'government'
        assert source.is_active is True
        assert source.id is not None
    finally:
        _cleanup(source, item, obs, shop, product, match)
    assert _counts() == before, 'DB not restored after test'


def test_market_item_normalized_title_and_package():
    before = _counts()
    source, item, obs, shop, product, match = _create_fixture()
    try:
        # raw kept untouched, normalized is the clean form
        assert item.raw_title == '  BERAS CAP JASMINE\u2122 500g '
        assert item.normalized_title == 'beras cap jasmine 500g'
        assert item.brand == 'TestBrand'
        assert item.category == 'BERAS'
        assert float(item.package_quantity) == 500
        assert item.package_unit == 'g'
        # relationship up to the source works
        assert item.source.name == TEST_SOURCE_NAME
        assert item.source.source_type == 'government'
    finally:
        _cleanup(source, item, obs, shop, product, match)
    assert _counts() == before, 'DB not restored after test'


def test_observation_derived_prices():
    before = _counts()
    source, item, obs, shop, product, match = _create_fixture()
    try:
        # effective_price = promo because is_on_promo
        assert float(obs.effective_price) == 10.00
        assert float(obs.regular_price) == 12.00
        assert obs.is_on_promo is True
        # normalized unit price auto-computed: 10 RM / 0.5 kg = 20 RM/kg
        assert float(obs.normalized_unit_price) == 20.0
        # relationship up to the item works
        assert obs.market_item.id == item.id
        assert obs.market_item.raw_title == item.raw_title
    finally:
        _cleanup(source, item, obs, shop, product, match)
    assert _counts() == before, 'DB not restored after test'


def test_observation_no_promo_uses_regular_price():
    before = _counts()
    source, item, obs, shop, product, match = _create_fixture()
    try:
        obs2 = MarketPriceObservation(market_item=item,
                                      regular_price=12.00,
                                      promo_price=None,
                                      is_on_promo=False)
        db.session.add(obs2)
        db.session.flush()
        assert float(obs2.effective_price) == 12.00
        # 12 RM / 0.5 kg = 24 RM/kg
        assert float(obs2.normalized_unit_price) == 24.0
        db.session.delete(obs2)
        db.session.commit()
    finally:
        _cleanup(source, item, obs, shop, product, match)
    assert _counts() == before, 'DB not restored after test'


def test_product_market_match_link():
    before = _counts()
    source, item, obs, shop, product, match = _create_fixture()
    try:
        # product side
        assert match.shop_product_id == product.id
        assert product.market_matches[0].id == match.id
        assert match.shop_product.name == TEST_PRODUCT_NAME
        # market side
        assert match.market_item_id == item.id
        assert item.product_matches[0].id == match.id
        assert match.market_item.raw_title == item.raw_title
        # match metadata
        assert float(match.confidence_score) == 0.95
        assert match.match_type == 'exact'
        assert match.is_verified is False
    finally:
        _cleanup(source, item, obs, shop, product, match)
    assert _counts() == before, 'DB not restored after test'


def test_unique_constraint_blocks_duplicate_link():
    before = _counts()
    source, item, obs, shop, product, match = _create_fixture()
    try:
        dup = ProductMarketMatch(shop_product_id=product.id,
                                 market_item_id=item.id,
                                 confidence_score=0.50,
                                 match_type='fuzzy')
        db.session.add(dup)
        try:
            db.session.commit()
            assert False, 'duplicate match should have been rejected'
        except IntegrityError:
            db.session.rollback()   # session is usable again after rollback
    finally:
        _cleanup(source, item, obs, shop, product, match)
    assert _counts() == before, 'DB not restored after test'


def test_manual_match_type_and_verified_flag():
    before = _counts()
    source, item, obs, shop, product, match = _create_fixture()
    try:
        match.match_type = 'manual'
        match.is_verified = True
        match.confidence_score = None
        db.session.commit()
        fresh = db.session.get(ProductMarketMatch, match.id)
        assert fresh.match_type == 'manual'
        assert fresh.is_verified is True
        assert fresh.confidence_score is None
    finally:
        _cleanup(source, item, obs, shop, product, match)
    assert _counts() == before, 'DB not restored after test'


# -------------------------------------------------
# runner (works without pytest)
# -------------------------------------------------
def _all_tests():
    return [(name, fn) for name, fn in sorted(globals().items())
            if name.startswith('test_') and callable(fn)]


def main():
    # ONE app context for the whole run - helpers must not open their own.
    with app.app_context():
        _purge_leftovers()
        tests = _all_tests()
        passed = 0
        failed = []
        for name, fn in tests:
            try:
                fn()
                passed += 1
            except Exception as exc:                    # noqa: BLE001
                failed.append((name, exc))
        _purge_leftovers()
    print(f"test_market_models: {passed}/{len(tests)} passed")
    for name, exc in failed:
        print(f"  FAIL {name}: {type(exc).__name__}: {exc}")
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
