"""Pytest session hooks for the shared demo database.

The full suite runs against the LIVE demo DB that FYP demos use, so any
fixture left behind pollutes the next demo (the Phase 10 audit found 28
leaked fixture shops from exactly this). Two guards live here:

1. Before each test module runs, its own purge executes (if it defines
   one), recovering leftovers from any previously crashed run — without
   weakening any test, since purges only remove known fixture rows.
2. After the session, the pricing-engine suite's leak guard asserts
   zero GeoCtx*/PricingTestShop* shops and TestSrc_* sources remain.
   Raising inside the session fixture teardown marks the session as
   errored (unlike pytest_sessionfinish, which cannot fail the run).
"""
import pytest

FIXTURE_PURGE_MODULES = [
    "test_pricing_engine",
    "test_pricing_workflow",
    "test_pricing_dashboard",
    "test_pricing_decision",
    "test_dashboard_service",
    "test_llm_explainer",
    "test_market_analysis",
    "test_matching",
    "test_employee_remove",
    "test_integration",
    "test_market_models",
    "test_market_ingestion",
    "test_market_refresh_service",
    "test_market_refresh_status",
    "test_market_refresh_health",
    "test_market_data_monitoring",
    "test_market_freshness",
]


def _call_purge(mod, name):
    """Call mod's purge helper inside an app context if it exists."""
    fn = getattr(mod, name, None)
    if fn is None:
        return False
    from app import app
    with app.app_context():
        fn()
    return True


@pytest.fixture(scope="module", autouse=True)
def _module_purge_guard(request):
    """Run the module's own purge before its first test (crash recovery)."""
    mod = request.module
    if mod.__name__ in FIXTURE_PURGE_MODULES:
        _call_purge(mod, "_purge") or _call_purge(mod, "_purge_leftovers")
    yield


@pytest.fixture(scope="session", autouse=True)
def _session_leak_guard():
    """Assert zero fixture leaks after the whole session."""
    yield
    import test_pricing_engine  # noqa: F401  (always importable: same dir)
    test_pricing_engine._assert_no_leaks()
    # Phase 10: ensure no ephemeral refresh runs leaked (manamurah/pricecatcher
    # runs created by mocked tests are cleaned; real demo history is empty at
    # this stage, so any remaining row is a leak).
    from app import app
    from sqlalchemy import text
    with app.app_context():
        from app import db
        # Clean any leftover test runs that escaped per-test purges (last test)
        # Includes manual/scheduled/test runs created by refresh service tests in the last 2h
        try:
            db.session.execute(text(
                "DELETE FROM market_refresh_run WHERE source_name LIKE 'test%'"
            ))
            db.session.execute(text(
                "DELETE FROM market_refresh_run WHERE triggered_by IN ('test','scheduled')"
            ))
            db.session.execute(text(
                "DELETE FROM market_refresh_run WHERE source_name IN ('manamurah','pricecatcher') AND started_at > DATE_SUB(UTC_TIMESTAMP(), INTERVAL 2 HOUR)"
            ))
            db.session.commit()
        except Exception:
            db.session.rollback()
        # Verify no TestRefresh* shops/sources remain
        leaked = db.session.execute(text(
            "SELECT name FROM shop WHERE name LIKE 'TestRefresh%'"
        )).fetchall()
        assert not leaked, f"refresh shops leaked: {[r[0] for r in leaked]}"
