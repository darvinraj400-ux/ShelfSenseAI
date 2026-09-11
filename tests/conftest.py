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
