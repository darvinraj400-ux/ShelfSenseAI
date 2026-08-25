"""
============================================================
 ShelfSenseAI - Phase 3F LLM Explainer Tests
============================================================

Tests the three-layer fallback chain for the Gemini LLM pricing
explainer service:

Fallback Tests:
  - No API key -> deterministic fallback (never crashes)
  - API error -> deterministic fallback (never crashes)
  - Empty response -> deterministic fallback (never crashes)

Payload Structure Tests:
  - Mocked Gemini success -> LLM text returned
  - PCAPA warnings -> included in fallback output
  - No market data -> fallback mentions it explicitly

Integration Test:
  - Full pricing pipeline includes llm_explanation in payload
  - Fallback string is non-empty even without Gemini configured

All tests use unittest.mock to simulate API failures without
requiring a real Gemini API key.

Run:
    ./venv/Scripts/python.exe tests/test_llm_explainer.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch, MagicMock
from services.llm_explainer import generate_pricing_explanation, _fallback

PASSED = FAILED = 0


def check(label, cond):
    global PASSED, FAILED
    if cond:
        PASSED += 1; print(f"  [PASS] {label}")
    else:
        FAILED += 1; print(f"  [FAIL] {label}")


# Stub product object
class FakeProduct:
    def __init__(self):
        self.name = "BERAS CAP JASMINE"
        self.cost_price = 23.50
        self.target_margin = 12.0
        self.baseline_margin = 12.0
        self.selling_price = 26.00


# ======================================================== FALLBACK TESTS
def test_fallback_no_key():
    """Test Layer 3: fallback when GEMINI_API_KEY is not set.

    Verifies that generate_pricing_explanation() returns a non-empty
    string even when the API key is missing from the environment.
    """
    print("\n--- Fallback: No API Key ---")
    product = FakeProduct()
    market_stats = {"n": 4, "median": 26.00, "min": 24.00, "max": 28.00}
    rec = {"recommended_price": 26.00, "confidence": "high",
           "guardrails_applied": [], "warnings": [], "diff_pct": 0.0}

    with patch.dict(os.environ, {}, clear=True):
        # Remove GEMINI_API_KEY if present
        os.environ.pop("GEMINI_API_KEY", None)
        result = generate_pricing_explanation(product, market_stats, rec)

    check("Returns a string", isinstance(result, str))
    check("Non-empty", len(result) > 0)
    check("Contains product name or price", "26.00" in result or "BERAS" in result)
    check("Does not crash", True)


def test_fallback_api_error():
    """Test Layer 2: fallback when the Gemini API raises an exception.

    Mocks _call_gemini to raise RuntimeError, simulating network errors,
    rate limits, or any API failure. Verifies the fallback is returned.
    """
    print("\n--- Fallback: API Error ---")
    product = FakeProduct()
    market_stats = {"n": 4, "median": 26.00, "min": 24.00, "max": 28.00}
    rec = {"recommended_price": 26.00, "confidence": "high",
           "guardrails_applied": [], "warnings": [], "diff_pct": 0.0}

    # Mock _call_gemini to raise (simulates any API failure)
    with patch("services.llm_explainer._call_gemini", side_effect=RuntimeError("API down")):
        result = generate_pricing_explanation(product, market_stats, rec)

    check("Returns fallback string", isinstance(result, str))
    check("Non-empty fallback", len(result) > 0)


def test_fallback_empty_response():
    """Test Layer 2: fallback when Gemini returns an empty response.

    Mocks _call_gemini to raise RuntimeError('empty response'),
    simulating a valid API call that returns no useful text.
    """
    print("\n--- Fallback: Empty Response ---")
    product = FakeProduct()
    market_stats = {"n": 0, "median": None, "min": None, "max": None}
    rec = {"recommended_price": 26.00, "confidence": "low",
           "guardrails_applied": ["cost_floor"], "warnings": [], "diff_pct": None}

    # _call_gemini raises RuntimeError on empty response — mock that
    with patch("services.llm_explainer._call_gemini",
               side_effect=RuntimeError("Gemini returned empty response")):
        result = generate_pricing_explanation(product, market_stats, rec)

    check("Empty response triggers fallback", isinstance(result, str))
    check("Fallback is non-empty", len(result) > 0)


# ======================================================== PAYLOAD STRUCTURE
def test_payload_structure():
    """Test that a successful Gemini call returns the LLM text.

    Mocks _call_gemini to return a realistic explanation string
    and verifies it is passed through correctly.
    """
    print("\n--- Payload Structure ---")
    product = FakeProduct()
    market_stats = {"n": 4, "median": 26.00, "min": 24.00, "max": 28.00}
    rec = {"recommended_price": 26.00, "confidence": "high",
           "guardrails_applied": [], "warnings": [], "diff_pct": 0.0}

    # Mock _call_gemini to return success text
    with patch("services.llm_explainer._call_gemini",
               return_value="The recommended price of RM26.00 is competitive with the market median."):
        result = generate_pricing_explanation(product, market_stats, rec)

    check("Returns LLM text on success", "RM26.00" in result)
    check("Result is a string", isinstance(result, str))


def test_fallback_with_warnings():
    """Test that the fallback includes PCAPA warnings when present.

    Verifies that the deterministic fallback string incorporates
    any compliance warnings from the recommendation payload.
    """
    print("\n--- Fallback with PCAPA Warning ---")
    product = FakeProduct()
    product.baseline_margin = 12.0
    market_stats = {"n": 4, "median": 26.00, "min": 24.00, "max": 28.00}
    rec = {"recommended_price": 30.00, "confidence": "medium",
           "guardrails_applied": ["pcapa"], "warnings": ["PCAPA Warning"],
           "diff_pct": 15.4}

    result = _fallback(product, market_stats, rec)
    check("Fallback includes price", "30.00" in result)
    check("Fallback includes warning", "PCAPA" in result or "⚠" in result)


def test_fallback_no_market():
    """Test that the fallback mentions no market data when n=0.

    Verifies that when no verified market matches exist, the fallback
    explanation explicitly states this fact.
    """
    print("\n--- Fallback: No Market Data ---")
    product = FakeProduct()
    market_stats = {"n": 0, "median": None, "min": None, "max": None}
    rec = {"recommended_price": 26.00, "confidence": "low",
           "guardrails_applied": [], "warnings": [], "diff_pct": None}

    result = _fallback(product, market_stats, rec)
    check("Fallback mentions no market data",
          "No verified market data" in result or "no market" in result.lower())
    check("Fallback includes price", "26.00" in result)


# ======================================================== INTEGRATION
def test_pricing_payload_has_llm():
    """Test that the full pricing pipeline includes llm_explanation.

    Creates a real product in the database and calls get_price_recommendation()
    without a Gemini API key. Verifies the payload contains a non-empty
    llm_explanation string (the deterministic fallback).
    """
    print("\n--- Pricing Payload has llm_explanation ---")
    # This tests that get_price_recommendation includes llm_explanation
    # even when Gemini is not configured (uses fallback)
    import os as _os
    _os.environ.pop("GEMINI_API_KEY", None)

    from app import app, db, User, Shop, Product, Inventory, PriceHistory
    from services.pricing_engine import get_price_recommendation
    from sqlalchemy import text
    from werkzeug.security import generate_password_hash

    TEST_SHOPS = ["LLMTestShop"]

    with app.app_context():
        # Cleanup
        rows = db.session.execute(
            text("SELECT id FROM shop WHERE name IN :n"),
            {"n": tuple(TEST_SHOPS)}).fetchall()
        if rows:
            sids = ",".join(str(r[0]) for r in rows)
            for tbl, col in [("inventory_adjustment","product_id"),("price_history","product_id"),
                              ("inventory","product_id"),("product_market_match","shop_product_id")]:
                db.session.execute(text(
                    f"DELETE FROM {tbl} WHERE {col} IN "
                    f"(SELECT id FROM product WHERE shop_id IN ({sids}))"))
            db.session.execute(text(f"DELETE FROM product WHERE shop_id IN ({sids})"))
            db.session.execute(text(f"DELETE FROM user WHERE shop_id IN ({sids})"))
            db.session.execute(text("DELETE FROM shop WHERE name IN :n"),
                               {"n": tuple(TEST_SHOPS)})

        u = User(email="llm_owner@shelfsense.my",
                 password_hash=generate_password_hash("Test1234!"), role="owner")
        db.session.add(u); db.session.flush()
        s = Shop(name="LLMTestShop"); db.session.add(s); db.session.flush()
        u.shop_id = s.id; db.session.commit()

        p = Product(name="LLMProd", cost_price=10.0, target_margin=30.0,
                    baseline_margin=30.0, selling_price=13.0,
                    quantity=1, unit="unit", shop_id=s.id)
        db.session.add(p); db.session.flush()
        db.session.add(PriceHistory(product_id=p.id, cost_price=10.0,
                                    selling_price=13.0, target_margin=30.0))
        db.session.add(Inventory(shop_id=s.id, product_id=p.id,
                                 current_stock=20, minimum_stock=5))
        db.session.commit()
        pid = p.id

        rec = get_price_recommendation(pid)
        check("llm_explanation in payload", "llm_explanation" in rec)
        check("llm_explanation is a string", isinstance(rec["llm_explanation"], str))
        check("llm_explanation is non-empty", len(rec["llm_explanation"]) > 0)

        # Cleanup
        db.session.execute(text(f"DELETE FROM inventory WHERE product_id={pid}"))
        db.session.execute(text(f"DELETE FROM price_history WHERE product_id={pid}"))
        db.session.execute(text(f"DELETE FROM product WHERE id={pid}"))
        db.session.execute(text(f"DELETE FROM user WHERE id={u.id}"))
        db.session.execute(text(f"DELETE FROM shop WHERE id={s.id}"))
        db.session.commit()


# ======================================================== MAIN
def main():
    global PASSED, FAILED
    PASSED = FAILED = 0
    print("=" * 60)
    print("ShelfSenseAI - Phase 3F LLM Explainer Tests")
    print("=" * 60)

    test_fallback_no_key()
    test_fallback_api_error()
    test_fallback_empty_response()
    test_payload_structure()
    test_fallback_with_warnings()
    test_fallback_no_market()
    test_pricing_payload_has_llm()

    total = PASSED + FAILED
    print(f"\n{'=' * 60}")
    print(f"test_llm_explainer: {PASSED}/{total} passed" +
          (f" ({FAILED} FAILED)" if FAILED else ""))
    print("=" * 60)
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
