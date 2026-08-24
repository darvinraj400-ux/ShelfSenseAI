"""
============================================================
 ShelfSenseAI — Phase 3E Intelligent Pricing Engine
============================================================
 Merges internal shop data (cost, margin, stock, velocity)
 with external market statistics to recommend an optimal
 selling price.

 The recommendation pipeline:
   1. Load product + inventory + market stats
   2. Build feature vector (13 features matching training schema)
   3. ML prediction via trained RandomForestRegressor
   4. Apply deterministic guardrails:
      a. Cost Floor  — never below cost_price × 1.05
      b. Market Sanity — clamp within market bounds
      c. PCAPA Check — flag margin above baseline without cost rise
   5. Compute confidence level and human-readable reasoning
============================================================
"""
import os
import warnings

import joblib
import numpy as np

from app import (  # noqa: E402
    db, Product, Inventory, PriceHistory
)
from services.market_analysis import get_market_stats, _product_base_quantity  # noqa: E402
from services.llm_explainer import generate_pricing_explanation  # noqa: E402
from utils.normalization import normalize_package_size  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MIN_MARGIN_FLOOR = 0.05      # 5% minimum margin (cost × 1.05)
MARKET_SANITY_LOW = 0.7      # Don't recommend below 70% of market min
MARKET_SANITY_HIGH = 1.5     # Don't recommend above 150% of market max

CONFIDENCE_HIGH = "high"     # ≥ 0.7
CONFIDENCE_MEDIUM = "medium" # 0.4 – 0.7
CONFIDENCE_LOW = "low"       # < 0.4

# Feature names — must match training script exactly
FEATURE_NAMES = [
    "cost_price",
    "target_margin",
    "baseline_margin",
    "market_median",
    "market_mean",
    "market_min",
    "market_max",
    "market_spread",
    "normalized_unit_price",
    "stock_level",
    "sales_velocity",
    "price_to_market_ratio",
    "quantity",
]


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------

_MODEL_CACHE = None


def _load_model():
    """Load the trained model from disk (cached after first load)."""
    global _MODEL_CACHE
    if _MODEL_CACHE is not None:
        return _MODEL_CACHE

    model_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "ml", "pricing_model.pkl"
    )
    if not os.path.exists(model_path):
        return None

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        _MODEL_CACHE = joblib.load(model_path)
    return _MODEL_CACHE


def _estimate_sales_velocity(product_id):
    """Estimate daily sales velocity from recent PriceHistory changes.

    Falls back to a default of 5 if no history is available.
    This is a simple proxy — real velocity tracking is a future feature.
    """
    from app import Sale  # local import to avoid circular
    recent_sales = (Sale.query
                    .filter_by(product_id=product_id)
                    .order_by(Sale.sold_at.desc())
                    .limit(30)
                    .all())
    if not recent_sales:
        return 5  # default for new products

    # Average daily rate over the observation period
    if len(recent_sales) < 2:
        return int(recent_sales[0].quantity) if recent_sales else 5

    total_qty = sum(int(s.quantity) for s in recent_sales)
    date_span = max(1, (recent_sales[0].sold_at - recent_sales[-1].sold_at).days)
    return max(1, min(20, total_qty // date_span))


# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------

def _apply_cost_floor(recommended_price, cost_price):
    """Rule 1: Never recommend selling below cost × 1.05."""
    floor = round(cost_price * (1 + MIN_MARGIN_FLOOR), 2)
    if recommended_price < floor:
        return floor, True
    return recommended_price, False


def _apply_market_sanity(price, market_min, market_max):
    """Rule 2: Clamp within reasonable market bounds.
    Treat None OR 0.0 as 'no market data available' -> pass through."""
    if not market_min or not market_max:
        return price, False

    low = round(market_min * MARKET_SANITY_LOW, 2)
    high = round(market_max * MARKET_SANITY_HIGH, 2)

    if price < low:
        return low, True
    if price > high:
        return high, True
    return price, False


def _check_pcapa(product, recommended_price):
    """Rule 3: Check PCAPA compliance.

    Returns (warning_message_or_None, is_compliant).
    """
    if product.baseline_margin is None:
        return None, True

    # What margin does the recommended price imply?
    if product.cost_price <= 0:
        return None, True

    implied_margin = round((recommended_price / product.cost_price - 1) * 100, 2)

    # Check against baseline
    if implied_margin > product.baseline_margin:
        # Check if cost has risen since baseline was established.
        # The FIRST PriceHistory entry records the cost at creation time
        # (when baseline_margin was locked). Compare current cost against it.
        baseline_history = (PriceHistory.query
                            .filter_by(product_id=product.id)
                            .order_by(PriceHistory.created_at.asc())
                            .first())
        baseline_cost = float(baseline_history.cost_price) if baseline_history else float(product.cost_price)

        if float(product.cost_price) <= baseline_cost:
            warning = (
                f"⚠ PCAPA Warning: Recommended margin ({implied_margin:.1f}%) "
                f"exceeds baseline ({product.baseline_margin:.1f}%) with no "
                f"cost increase. Under the Price Control and Anti-Profiteering "
                f"Act 2011, only a cost increase justifies a higher margin."
            )
            return warning, False

    return None, True


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------

def _compute_confidence(has_market_data, has_model, guardrails_triggered):
    """Determine confidence level based on data availability and guardrail state."""
    if not has_market_data:
        return CONFIDENCE_LOW, 0.2
    if not has_model:
        return CONFIDENCE_MEDIUM, 0.5

    # Model available + market data: high confidence unless guardrails intervened
    if guardrails_triggered:
        return CONFIDENCE_MEDIUM, 0.55
    return CONFIDENCE_HIGH, 0.85


# ---------------------------------------------------------------------------
# Main recommendation function
# ---------------------------------------------------------------------------

def get_price_recommendation(product_id):
    """Generate a price recommendation for one shop product.

    Returns a dict with:
      - recommended_price
      - original_prediction (before guardrails)
      - confidence (high/medium/low)
      - confidence_score (0-1)
      - reasoning (list of human-readable strings)
      - warnings (list of compliance warnings)
      - guardrails_applied (list of which rules fired)
      - feature_importances (top factors)
      - market_stats (from Phase 3D)
    """
    product = Product.query.get_or_404(product_id)
    inventory = Inventory.query.filter_by(product_id=product_id).first()
    stock_level = int(inventory.current_stock) if inventory else 0

    # Market stats (Phase 3D)
    market = get_market_stats(product_id)
    has_market_data = market.get("n", 0) > 0

    # Sales velocity
    velocity = _estimate_sales_velocity(product_id)

    # Package quantity
    base_qty = _product_base_quantity(product) or 1.0

    # Market features (use 0 if no market data)
    market_median = market.get("median") or 0.0
    market_mean = market.get("mean") or 0.0
    market_min = market.get("min") or 0.0
    market_max = market.get("max") or 0.0
    market_spread = market.get("spread") or 0.0
    unit_price = 0.0
    if has_market_data and market.get("matches"):
        # Use the average unit price across verified matches
        from app import MarketPriceObservation, MarketItem, MarketSource, ProductMarketMatch
        obs_prices = []
        matches = (ProductMarketMatch.query
                   .filter_by(shop_product_id=product_id, is_verified=True)
                   .all())
        for m in matches:
            obs = (MarketPriceObservation.query
                   .join(MarketItem)
                   .join(MarketSource)
                   .filter(MarketPriceObservation.market_item_id == m.market_item_id,
                           MarketSource.is_active.is_(True))
                   .all())
            for o in obs:
                if o.normalized_unit_price and float(o.normalized_unit_price) > 0:
                    obs_prices.append(float(o.normalized_unit_price))
        if obs_prices:
            unit_price = float(np.mean(obs_prices))

    # Build feature vector
    price_to_market = (float(product.cost_price) / market_median
                       if market_median > 0 else 1.0)

    features = {
        "cost_price": float(product.cost_price),
        "target_margin": float(product.target_margin),
        "baseline_margin": float(product.baseline_margin or product.target_margin),
        "market_median": market_median,
        "market_mean": market_mean,
        "market_min": market_min,
        "market_max": market_max,
        "market_spread": market_spread,
        "normalized_unit_price": unit_price,
        "stock_level": stock_level,
        "sales_velocity": velocity,
        "price_to_market_ratio": round(price_to_market, 4),
        "quantity": float(product.quantity) if product.quantity else 1.0,
    }

    # --- ML Prediction ---
    model_data = _load_model()
    has_model = model_data is not None

    if has_model:
        X = np.array([[features[f] for f in FEATURE_NAMES]])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ml_prediction = round(float(model_data["model"].predict(X)[0]), 2)
    else:
        # Fallback: pure rule-based (cost × margin)
        ml_prediction = product.suggested_price

    original_prediction = ml_prediction

    # --- Apply guardrails ---
    reasoning = []
    warnings_list = []
    guardrails_triggered = False
    guardrails_applied = []
    regulatory_cap_applied = False

    # Rule 0: Regulatory Cap (KPDN Barangan Kawalan)
    if product.is_price_controlled and product.government_ceiling_price:
        ceiling = float(product.government_ceiling_price)
        if ml_prediction > ceiling:
            ml_prediction = ceiling
            regulatory_cap_applied = True
            guardrails_triggered = True
            guardrails_applied.append("regulatory_cap")
            reasoning.append(
                f"Regulatory cap applied: price capped at RM{ceiling:.2f} "
                f"(KPDN ceiling price for Barangan Kawalan)"
            )

    # Rule 1: Cost Floor
    ml_prediction, floor_hit = _apply_cost_floor(ml_prediction, float(product.cost_price))
    if floor_hit:
        guardrails_triggered = True
        guardrails_applied.append("cost_floor")
        reasoning.append(
            f"Cost floor applied: price raised to RM{ml_prediction:.2f} "
            f"(minimum 5% margin over cost RM{product.cost_price:.2f})"
        )

    # Rule 2: Market Sanity
    ml_prediction, sanity_hit = _apply_market_sanity(ml_prediction, market_min, market_max)
    if sanity_hit:
        guardrails_triggered = True
        guardrails_applied.append("market_sanity")
        reasoning.append(
            f"Market sanity clamp: price adjusted to RM{ml_prediction:.2f} "
            f"(market range RM{market_min:.2f}–RM{market_max:.2f})"
        )

    # Rule 3: PCAPA Check
    pcapa_warning, pcatpa_ok = _check_pcapa(product, ml_prediction)
    if pcapa_warning:
        guardrails_triggered = True
        guardrails_applied.append("pcapa")
        warnings_list.append(pcapa_warning)
        reasoning.append("PCAPA compliance check: margin exceeds baseline without cost justification")

    # --- Reasoning ---
    if has_model:
        reasoning.insert(0, f"ML model prediction: RM{original_prediction:.2f}")
    else:
        reasoning.insert(0, "Rule-based recommendation (no ML model available)")

    if has_market_data:
        reasoning.append(
            f"Market context: median RM{market_median:.2f}, "
            f"your stock {stock_level} units, velocity ~{velocity}/day"
        )
    else:
        reasoning.append("No verified market data — recommendation is rule-based only")

    # Feature importances
    importances = {}
    if has_model and "feature_importances" in model_data:
        importances = dict(sorted(
            model_data["feature_importances"].items(),
            key=lambda x: -x[1]
        )[:5])

    # --- Confidence ---
    confidence, confidence_score = _compute_confidence(
        has_market_data, has_model, guardrails_triggered
    )

    # Price difference
    current_price = float(product.selling_price) if product.selling_price else None
    diff_pct = None
    if current_price and current_price > 0:
        diff_pct = round((ml_prediction / current_price - 1) * 100, 1)

    # Phase 3F: generate natural-language explanation via Gemini
    mkt_stats_payload = {
        "n": market.get("n", 0),
        "median": market.get("median"),
        "min": market.get("min"),
        "max": market.get("max"),
    }
    llm_payload = {
        "recommended_price": ml_prediction,
        "confidence": confidence,
        "guardrails_applied": guardrails_applied,
        "warnings": warnings_list,
        "diff_pct": diff_pct,
    }
    llm_explanation = generate_pricing_explanation(
        product, mkt_stats_payload, llm_payload)

    return {
        "recommended_price": ml_prediction,
        "original_prediction": original_prediction,
        "confidence": confidence,
        "confidence_score": confidence_score,
        "reasoning": reasoning,
        "warnings": warnings_list,
        "guardrails_applied": guardrails_applied,
        "feature_importances": importances,
        "market_stats": mkt_stats_payload,
        "current_price": current_price,
        "diff_pct": diff_pct,
        "cost_floor": round(float(product.cost_price) * (1 + MIN_MARGIN_FLOOR), 2),
        "stock_level": stock_level,
        "sales_velocity": velocity,
        "llm_explanation": llm_explanation,
        "regulatory_cap_applied": regulatory_cap_applied,
        "government_ceiling_price": float(product.government_ceiling_price) if product.government_ceiling_price else None,
    }


# ---------------------------------------------------------------------------
# Apply recommended price
# ---------------------------------------------------------------------------

def apply_price(product_id, user_id):
    """Apply the recommended price to a product.

    Updates selling_price and logs a PriceHistory entry.
    Returns the applied price and a flash-safe message.
    """
    product = Product.query.get_or_404(product_id)
    rec = get_price_recommendation(product_id)
    new_price = rec["recommended_price"]

    old_price = product.selling_price
    product.selling_price = new_price

    # Log the change in PriceHistory
    db.session.add(PriceHistory(
        product_id=product.id,
        cost_price=product.cost_price,
        selling_price=new_price,
        target_margin=product.target_margin,
    ))

    db.session.commit()

    return new_price, f"Price updated to RM{new_price:.2f} (AI recommendation applied)"
