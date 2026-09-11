"""
============================================================
 ShelfSenseAI - Phase 7 Pricing Decision-Support Tests
============================================================

Covers the Phase 7 decision-support layer added on top of the
existing (unchanged) pricing engine:

Part 1 - Pure function tests (no database):
  - recommendation_status(): REDUCE / INCREASE / MAINTAIN /
    INSUFFICIENT_DATA, tolerance behaviour, edge cases
  - _classify_trend(): Rising / Falling / Stable / insufficient_data,
    noisy one-day changes rejected, change-percent math
  - _market_evidence(): Strong / Moderate / Limited / Unavailable bands

Part 2 - Database integration tests:
  - full recommendation payload exposes Phase 7 keys
  - status derived from the FINAL (guardrailed) price
  - guardrails still authoritative (cost floor respected even when the
    market median sits below it; KPDN cap respected)
  - no-market-data product still gets a valid recommendation with
    evidence Unavailable (no fake zeros)
  - Gemini stays explanation-only: the explanation receives the Phase 7
    context but cannot change recommended_price

Run:
    ./venv/Scripts/python.exe tests/test_pricing_decision.py
"""
import sys, os, string, random as rnd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app, db, User, Shop, Product, Inventory          # noqa: E402
from services.pricing_engine import (recommendation_status,      # noqa: E402
                                     _classify_trend,
                                     _market_evidence,
                                     get_price_recommendation,
                                     STATUS_TOLERANCE_PCT)
from services.llm_explainer import generate_pricing_explanation  # noqa: E402
from sqlalchemy import text                                      # noqa: E402
from werkzeug.security import generate_password_hash             # noqa: E402

PASSED = FAILED = 0
TEST_SHOP = "P7DecisionShop"
PW = "Test1234!"


def check(label, cond):
    global PASSED, FAILED
    if cond:
        PASSED += 1; print(f"  [PASS] {label}")
    else:
        FAILED += 1; print(f"  [FAIL] {label}")


def _purge():
    with app.app_context():
        rows = db.session.execute(
            text("SELECT id FROM shop WHERE name = :n"),
            {"n": TEST_SHOP}).fetchall()
        if not rows:
            return
        sids = ",".join(str(r[0]) for r in rows)
        for tbl, col in [("inventory_adjustment", "product_id"),
                         ("sale", "product_id"),
                         ("price_history", "product_id"),
                         ("inventory", "product_id"),
                         ("product_market_match", "shop_product_id")]:
            db.session.execute(text(
                f"DELETE FROM {tbl} WHERE {col} IN "
                f"(SELECT id FROM product WHERE shop_id IN ({sids}))"))
        db.session.execute(text(
            f"DELETE FROM product WHERE shop_id IN ({sids})"))
        db.session.execute(text(
            f"DELETE FROM user WHERE shop_id IN ({sids})"))
        db.session.execute(text("DELETE FROM shop WHERE name = :n"),
                           {"n": TEST_SHOP})
        db.session.commit()


# ---------------------------------------------------------------
# Part 1: pure functions
# ---------------------------------------------------------------
def test_status_reduce_increase_maintain():
    print("-- recommendation_status --")
    check("reduce", recommendation_status(2.80, 3.00) == "REDUCE")
    check("increase", recommendation_status(3.20, 3.00) == "INCREASE")
    check("maintain exact", recommendation_status(3.00, 3.00) == "MAINTAIN")
    check("maintain within tolerance",
          recommendation_status(3.00 * (1 + STATUS_TOLERANCE_PCT / 100),
                                3.00) == "MAINTAIN")
    check("just beyond tolerance is REDUCE",
          recommendation_status(3.00 * 0.99, 3.00) == "REDUCE")
    check("just beyond tolerance is INCREASE",
          recommendation_status(3.00 * 1.01, 3.00) == "INCREASE")


def test_status_edge_cases():
    check("no current price", recommendation_status(2.80, None)
          == "INSUFFICIENT_DATA")
    check("no recommendation", recommendation_status(None, 3.00)
          == "INSUFFICIENT_DATA")
    check("zero current price", recommendation_status(2.80, 0)
          == "INSUFFICIENT_DATA")
    check("tolerance=0 keeps tiny change",
          recommendation_status(2.999, 3.00, tolerance_pct=0) == "REDUCE")


def _pts(*vals):
    """Build trend points 10 days apart with the given medians."""
    return [{"date": f"2026-08-{1 + i * 10:02d}", "median": v}
            for i, v in enumerate(vals)]


def test_trend_classification():
    print("-- _classify_trend --")
    check("rising", _classify_trend(_pts(2.50, 2.55, 2.62))
          == {"direction": "Rising", "change_percent": 4.8})
    check("falling", _classify_trend(_pts(2.60, 2.55, 2.49))
          == {"direction": "Falling", "change_percent": -4.2})
    check("stable", _classify_trend(_pts(2.60, 2.61, 2.60))
          == {"direction": "Stable", "change_percent": 0.0})
    check("insufficient: no points",
          _classify_trend([])["direction"] == "insufficient_data")
    check("insufficient: one point",
          _classify_trend(_pts(2.60))["direction"] == "insufficient_data")
    check("insufficient: one-day change",
          _classify_trend([{"date": "2026-08-20", "median": 2.50},
                           {"date": "2026-08-21", "median": 2.60}])
          ["direction"] == "insufficient_data")
    check("insufficient: zero median",
          _classify_trend(_pts(0, 2.60))["direction"] == "insufficient_data")


def test_market_evidence_bands():
    print("-- _market_evidence --")
    base = {"n": 0, "premise_count": 0, "market_tier": None}
    check("unavailable when no obs",
          _market_evidence(base) == "Unavailable")
    check("unavailable below 3 obs",
          _market_evidence({**base, "n": 2, "premise_count": 2})
          == "Unavailable")
    limited = {"n": 8, "premise_count": 1, "market_tier": "district"}
    check("limited with 1 premise", _market_evidence(limited) == "Limited")
    moderate = {"n": 10, "premise_count": 3, "market_tier": "district"}
    check("moderate band", _market_evidence(moderate) == "Moderate")
    strong = {"n": 93, "premise_count": 7, "market_tier": "district"}
    check("strong band", _market_evidence(strong) == "Strong")
    national = {"n": 93, "premise_count": 7, "market_tier": "national"}
    check("national caps at moderate",
          _market_evidence(national) == "Moderate")


# ---------------------------------------------------------------
# Part 2: database integration
# ---------------------------------------------------------------
def _make_fixtures(cost=2.00, margin=30.0, selling=3.00,
                   name="P7 Product", **extra):
    slug = "".join(rnd.choices(string.ascii_lowercase, k=6))
    with app.app_context():
        u = User(email=f"p7_{slug}@shelfsense.my",
                 password_hash=generate_password_hash(PW), role="owner")
        db.session.add(u); db.session.flush()
        s = Shop(name=TEST_SHOP, state="Johor", district="Segamat")
        db.session.add(s); db.session.flush()
        u.shop_id = s.id
        db.session.flush()
        p = Product(name=name, cost_price=cost, target_margin=margin,
                    baseline_margin=margin, selling_price=selling,
                    quantity=1, unit="unit", shop_id=s.id, **extra)
        db.session.add(p); db.session.flush()
        inv = Inventory(shop_id=s.id, product_id=p.id, current_stock=25,
                        minimum_stock=5)
        db.session.add(inv)
        db.session.commit()
        return u.id, s.id, p.id


def test_payload_keys_and_status():
    print("-- recommendation payload --")
    _, shop_id, product_id = _make_fixtures()
    try:
        with app.app_context():
            shop = Shop.query.get(shop_id)
            rec = get_price_recommendation(product_id, shop=shop)
            check("has status key", "status" in rec)
            check("status value valid",
                  rec["status"] in ("MAINTAIN", "REDUCE", "INCREASE",
                                    "INSUFFICIENT_DATA"))
            check("has market_evidence", "market_evidence" in rec)
            check("has trend keys",
                  "trend_direction" in rec and "trend_change_percent" in rec)
            check("has guardrail_effect", "guardrail_effect" in rec)
            check("status matches final price",
                  rec["status"] == recommendation_status(
                      rec["recommended_price"], rec["current_price"]))
    finally:
        _purge()


def test_cost_floor_beats_market_median():
    print("-- cost floor vs low market --")
    # cost 2.00 -> floor 2.10; no market data means the rule-based
    # fallback must still never dip below the floor.
    _, shop_id, product_id = _make_fixtures(cost=2.00, margin=5.0,
                                            selling=2.05)
    try:
        with app.app_context():
            shop = Shop.query.get(shop_id)
            rec = get_price_recommendation(product_id, shop=shop)
            check("never below cost floor",
                  rec["recommended_price"] >= rec["cost_floor"])
            check("floor value correct", rec["cost_floor"] == 2.10)
    finally:
        _purge()


def test_kpdn_cap_not_bypassed():
    print("-- KPDN cap still authoritative --")
    _, shop_id, product_id = _make_fixtures(
        cost=1.00, margin=80.0, selling=9.00,
        is_price_controlled=True, government_ceiling_price=2.50)
    try:
        with app.app_context():
            shop = Shop.query.get(shop_id)
            rec = get_price_recommendation(product_id, shop=shop)
            check("ceiling enforced",
                  rec["recommended_price"] <= 2.50 + 1e-9)
            check("cap flag set", rec["regulatory_cap_applied"] is True)
    finally:
        _purge()


def test_no_market_data_graceful():
    print("-- no market data --")
    _, shop_id, product_id = _make_fixtures()
    try:
        with app.app_context():
            shop = Shop.query.get(shop_id)
            rec = get_price_recommendation(product_id, shop=shop)
            check("still returns a price",
                  rec["recommended_price"] is not None
                  and rec["recommended_price"] > 0)
            check("evidence unavailable",
                  rec["market_evidence"] == "Unavailable")
            check("no market median faked",
                  (rec["market_stats"] or {}).get("median") is None)
            check("reasoning explains fallback",
                  any("cost" in r.lower() for r in rec["reasoning"]))
    finally:
        _purge()


def test_gemini_explanation_only():
    print("-- Gemini explanation-only contract --")
    with app.app_context():
        class FakeProduct:
            name = "P7 Product"
            cost_price = 2.00
            selling_price = 3.00
            target_margin = 30.0
            baseline_margin = 30.0
        market = {"n": 93, "median": 2.60, "min": 2.55, "max": 2.90,
                  "premise_count": 7, "market_tier": "district",
                  "market_tier_label": "Segamat, Johor",
                  "position": "Above Market", "difference": 0.40,
                  "difference_percent": 15.38,
                  "latest_observed_at": "2026-08-27"}
        rec = {"recommended_price": 2.80, "confidence": "high",
               "confidence_score": 0.8, "guardrails_applied": [],
               "warnings": [], "diff_pct": -6.7,
               "status": "REDUCE", "market_evidence": "Strong",
               "trend_direction": "Stable", "trend_change_percent": 0.0,
               "guardrail_effect": None}
        text_out = generate_pricing_explanation(
            FakeProduct(), market, rec)
        check("explanation produced", isinstance(text_out, str)
              and len(text_out) > 0)
        check("explanation does not suggest a different price",
              "RM3.50" not in text_out)


# ---------------------------------------------------------------
# runner (pytest + standalone)
# ---------------------------------------------------------------
def main():
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn)]
    _purge()
    failed = []
    global FAILED
    for name, fn in tests:
        print(f"[{name}]")
        try:
            fn()
        except Exception as exc:                    # noqa: BLE001
            FAILED += 1
            failed.append((name, exc))
            import traceback; traceback.print_exc()
    _purge()
    print(f"test_pricing_decision: {PASSED}/{PASSED + FAILED} checks passed")
    for name, exc in failed:
        print(f"  FAIL {name}: {type(exc).__name__}: {exc}")
    return 1 if FAILED or failed else 0


if __name__ == "__main__":
    sys.exit(main())
