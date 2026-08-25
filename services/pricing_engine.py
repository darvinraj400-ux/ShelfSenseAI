"""
============================================================
 ShelfSenseAI — Phase 3E Intelligent Pricing Engine
============================================================

This module is the "brain" of ShelfSenseAI. It merges internal shop data
(cost, margin, stock, sales velocity) with external market statistics to
recommend an optimal selling price for each product.

RECOMMENDATION PIPELINE
-----------------------
1. Load product + inventory + market stats (Phase 3D).
2. Build a 13-feature vector matching the training schema.
3. ML prediction via trained RandomForestRegressor (ml/pricing_model.pkl).
4. Apply 4 deterministic guardrails (in strict order):
   a. Rule 0: Regulatory Cap — KPDN Barangan Kawalan ceiling price
   b. Rule 1: Cost Floor — never below cost_price * 1.05 (5% min margin)
   c. Rule 2: Market Sanity — clamp within reasonable market bounds
   d. Rule 3: PCAPA Check — flag margin above baseline without cost rise
5. Compute confidence level (high/medium/low) and reasoning strings.
6. Generate natural-language explanation via Gemini LLM (Phase 3F).

GUARANTEES
----------
- The recommended price is ALWAYS >= cost_price * 1.05 (Rule 1).
- The recommended price NEVER exceeds the KPDN ceiling for controlled goods (Rule 0).
- PCAPA compliance is flagged but NOT forcibly capped (informational warning).
- If the ML model file is missing, a pure rule-based fallback is used.
- The entire pipeline NEVER raises exceptions — errors produce degraded
  recommendations rather than crashes.

ARCHITECTURAL DECISIONS
-----------------------
- Guardrails are applied sequentially so each rule sees the output of the
  previous one (e.g. the regulatory cap is applied first, then cost floor
  may raise it if the ceiling is below cost * 1.05).
- Confidence scoring considers data availability AND guardrail state:
  no market data = low; no ML model = medium; guardrails triggered = medium.
- The ML model is cached after first load to avoid repeated disk reads.
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


# -------------------------------------------------
# CONSTANTS — Guardrail thresholds and confidence levels
# -------------------------------------------------
MIN_MARGIN_FLOOR = 0.05      # 5% minimum margin (cost * 1.05 = absolute floor)
                                # This prevents selling below a viable margin.
MARKET_SANITY_LOW = 0.7      # Don't recommend below 70% of market min.
                                # Extremely underpriced recommendations are
                                # likely ML artifacts, not real strategy.
MARKET_SANITY_HIGH = 1.5     # Don't recommend above 150% of market max.
                                # Extremely overpriced items would be unsellable.

# Confidence level labels for the UI and LLM prompt.
CONFIDENCE_HIGH = "high"     # >= 0.7 — ML model + market data, no guardrails
CONFIDENCE_MEDIUM = "medium" # 0.4 – 0.7 — partial data or guardrails triggered
CONFIDENCE_LOW = "low"       # < 0.4 — no market data, rule-based only

# Feature names — must match the training script (scripts/train_pricing_model.py)
# EXACTLY. The order determines the feature vector position.
FEATURE_NAMES = [
    "cost_price",           # What the shop pays for the product (RM)
    "target_margin",        # Desired margin percentage (e.g. 30.0)
    "baseline_margin",      # Margin at product creation (PCAPA baseline)
    "market_median",        # Median market price scaled to product size
    "market_mean",          # Mean market price scaled to product size
    "market_min",           # Minimum observed market price
    "market_max",           # Maximum observed market price
    "market_spread",        # max - min (price volatility)
    "normalized_unit_price",# RM per base unit (kg/l/unit)
    "stock_level",          # Current inventory quantity
    "sales_velocity",       # Estimated daily units sold
    "price_to_market_ratio",# cost_price / market_median
    "quantity",             # Product package quantity (e.g. 10 for 10kg)
]


# -------------------------------------------------
# MODEL LOADING
# -------------------------------------------------
_MODEL_CACHE = None  # Module-level cache to avoid repeated disk reads.


def _load_model():
    """Load the trained RandomForestRegressor from disk (cached after first load).

    The model file (ml/pricing_model.pkl) is a generated artifact produced
    by scripts/train_pricing_model.py. It is excluded from version control
    (.gitignore) because it is a build artifact, not source code.

    Returns:
        The model payload dict (containing 'model', 'feature_names', etc.)
        if the file exists, or None if it does not (triggers rule-based fallback).
    """
    global _MODEL_CACHE

    # Return cached model if already loaded (avoids repeated disk reads).
    if _MODEL_CACHE is not None:
        return _MODEL_CACHE

    # Construct the path to the model file relative to this script.
    model_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "ml", "pricing_model.pkl"
    )

    # If the model file doesn't exist, return None to trigger fallback.
    if not os.path.exists(model_path):
        return None

    # Load with warnings suppressed (scikit-learn version compatibility).
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        _MODEL_CACHE = joblib.load(model_path)
    return _MODEL_CACHE


def _estimate_sales_velocity(product_id):
    """Estimate daily sales velocity from recent sale records.

    Sales velocity is a feature for the ML model — it captures demand.
    Higher velocity products can sustain higher prices; low-velocity
    products benefit from competitive pricing.

    This is a simple proxy: it averages daily units sold over the
    most recent 30 sales. Real velocity tracking (rolling averages,
    seasonal adjustment) is a future enhancement.

    Args:
        product_id: The integer ID of the product.

    Returns:
        An estimated daily units-sold integer (1-20), defaulting to 5
        for new products with no sales history.
    """
    from app import Sale  # Local import to avoid circular dependency at module level.

    # Fetch the 30 most recent sales for this product.
    recent_sales = (Sale.query
                    .filter_by(product_id=product_id)
                    .order_by(Sale.sold_at.desc())
                    .limit(30)
                    .all())

    # No sales history: return a neutral default.
    if not recent_sales:
        return 5

    # Single sale: use its quantity as the estimate.
    if len(recent_sales) < 2:
        return int(recent_sales[0].quantity) if recent_sales else 5

    # Multiple sales: compute average daily rate.
    total_qty = sum(int(s.quantity) for s in recent_sales)
    date_span = max(1, (recent_sales[0].sold_at - recent_sales[-1].sold_at).days)
    # Clamp to 1-20 range to prevent extreme values from distorting the model.
    return max(1, min(20, total_qty // date_span))


# -------------------------------------------------
# GUARDRAIL FUNCTIONS
#
# Each guardrail is a pure function that takes the current price
# and returns (adjusted_price, was_modified). This makes them
# independently testable and the logic chain transparent.
# -------------------------------------------------

def _apply_cost_floor(recommended_price, cost_price):
    """Rule 1: Never recommend selling below cost * 1.05.

    This is the absolute safety net — even if the ML model predicts
    a very low price (e.g. during a clearance scenario), the system
    ensures the shop never sells at a loss. The 5% buffer covers
    operational overhead (electricity, staff wages, rent).

    Args:
        recommended_price: The ML-predicted price.
        cost_price: The shop's cost for the product.

    Returns:
        A tuple (adjusted_price, was_modified).
        If the price was below the floor, it is raised to the floor.
    """
    # Calculate the absolute minimum viable selling price.
    floor = round(cost_price * (1 + MIN_MARGIN_FLOOR), 2)

    # If the recommendation is below the floor, clamp it up.
    if recommended_price < floor:
        return floor, True

    # Price is already above the floor — no modification needed.
    return recommended_price, False


def _apply_market_sanity(price, market_min, market_max):
    """Rule 2: Clamp within reasonable market bounds.

    This prevents extreme recommendations that would be unsellable:
    - Below 70% of market minimum: the price is suspiciously cheap
      (likely an ML artifact or missing data).
    - Above 150% of market maximum: the price is way above competitors
      and would lose all customers.

    When no market data is available (None or 0.0), the price passes
    through unmodified — we don't clamp against nonexistent data.

    Args:
        price: The current recommended price.
        market_min: The minimum observed market price.
        market_max: The maximum observed market price.

    Returns:
        A tuple (adjusted_price, was_modified).
    """
    # No market data available: pass through without clamping.
    if not market_min or not market_max:
        return price, False

    # Calculate the sanity bounds.
    low = round(market_min * MARKET_SANITY_LOW, 2)
    high = round(market_max * MARKET_SANITY_HIGH, 2)

    # Clamp to the lower bound if below.
    if price < low:
        return low, True

    # Clamp to the upper bound if above.
    if price > high:
        return high, True

    # Price is within the sanity range — no modification.
    return price, False


def _check_pcapa(product, recommended_price):
    """Rule 3: Check PCAPA (Price Control and Anti-Profiteering Act 2011) compliance.

    Under PCAPA 2011, a shop cannot increase margins beyond their
    established baseline without a corresponding cost increase. This
    rule checks whether the recommended price implies a margin that
    exceeds the baseline margin AND whether the cost has actually risen.

    WARNING vs. VIOLATION:
    - This rule produces a WARNING, not a hard cap. The shop owner
      makes the final decision. However, the warning is prominently
      displayed in the UI and the LLM explanation.

    Logic:
      1. If baseline_margin is None (legacy product), skip the check.
      2. Compute implied_margin from the recommended price.
      3. If implied_margin > baseline_margin:
         a. Look up the first PriceHistory entry (baseline cost).
         b. If current cost <= baseline cost: WARNING (margin up, cost flat).
         c. If current cost > baseline cost: OK (cost rise justifies margin).

    Args:
        product: A Product ORM object.
        recommended_price: The current recommended price.

    Returns:
        A tuple (warning_message_or_None, is_compliant).
    """
    # No baseline established: cannot check compliance.
    if product.baseline_margin is None:
        return None, True

    # Guard against zero cost (prevents division by zero).
    if product.cost_price <= 0:
        return None, True

    # Compute the margin that the recommended price implies.
    implied_margin = round((recommended_price / product.cost_price - 1) * 100, 2)

    # Check if the implied margin exceeds the baseline.
    if implied_margin > product.baseline_margin:
        # Look up the FIRST PriceHistory entry — this records the cost at
        # product creation time (when baseline_margin was locked).
        baseline_history = (PriceHistory.query
                            .filter_by(product_id=product.id)
                            .order_by(PriceHistory.created_at.asc())
                            .first())

        # If no history exists, use the current cost as the baseline.
        baseline_cost = float(baseline_history.cost_price) if baseline_history else float(product.cost_price)

        # Check: has the cost actually risen since baseline?
        if float(product.cost_price) <= baseline_cost:
            # COST HAS NOT RISEN: margin increase without cost justification.
            # This is a PCAPA violation warning.
            warning = (
                f"\u26a0 PCAPA Warning: Recommended margin ({implied_margin:.1f}%) "
                f"exceeds baseline ({product.baseline_margin:.1f}%) with no "
                f"cost increase. Under the Price Control and Anti-Profiteering "
                f"Act 2011, only a cost increase justifies a higher margin."
            )
            return warning, False

    # Margin is within baseline OR cost has risen — compliant.
    return None, True


# -------------------------------------------------
# CONFIDENCE SCORING
# -------------------------------------------------

def _compute_confidence(has_market_data, has_model, guardrails_triggered):
    """Determine confidence level based on data availability and guardrail state.

    Confidence reflects how trustworthy the recommendation is:
      - HIGH: ML model + market data + no guardrails (the model had full context)
      - MEDIUM: ML model but guardrails fired (the model's output was modified)
               OR no ML model but market data exists (rule-based with context)
      - LOW: no market data (pure cost-based rule, no external intelligence)

    Args:
        has_market_data: Whether verified market observations exist.
        has_model: Whether the ML model file was loaded successfully.
        guardrails_triggered: Whether any guardrail modified the price.

    Returns:
        A tuple (confidence_label, confidence_score_0_to_1).
    """
    # No market data: the recommendation is based only on cost structure.
    if not has_market_data:
        return CONFIDENCE_LOW, 0.2

    # Market data exists but no ML model: rule-based with market context.
    if not has_model:
        return CONFIDENCE_MEDIUM, 0.5

    # ML model + market data available.
    if guardrails_triggered:
        # Guardrails modified the ML output, reducing confidence.
        return CONFIDENCE_MEDIUM, 0.55
    return CONFIDENCE_HIGH, 0.85


# -------------------------------------------------
# MAIN RECOMMENDATION FUNCTION
# -------------------------------------------------

def get_price_recommendation(product_id, shop=None):
    """Generate a comprehensive price recommendation for one shop product.

    This is the primary entry point called by the API route and the
    product detail page. It orchestrates the full pipeline:
      1. Data loading (product, inventory, market stats, sales velocity)
      2. Feature engineering (13 features matching training schema)
      3. ML prediction (or rule-based fallback)
      4. Guardrail application (4 rules in strict order)
      5. Confidence and reasoning computation
      6. LLM explanation generation (Phase 3F)

    Args:
        product_id: The integer ID of the shop Product.

    Returns:
        A dict containing:
          - recommended_price: The final guarded price
          - original_prediction: The ML price before guardrails
          - confidence: "high" / "medium" / "low"
          - confidence_score: 0.0 to 1.0
          - reasoning: List of human-readable explanation strings
          - warnings: List of compliance warnings (e.g. PCAPA)
          - guardrails_applied: List of rule names that fired
          - feature_importances: Top 5 ML feature importances
          - market_stats: Market statistics dict
          - current_price: Shop's current selling price
          - diff_pct: Percentage difference from current price
          - cost_floor: The absolute minimum price
          - stock_level: Current inventory quantity
          - sales_velocity: Estimated daily units sold
          - llm_explanation: Natural-language explanation from Gemini
          - regulatory_cap_applied: Whether KPDN ceiling was enforced
          - government_ceiling_price: The KPDN ceiling (if applicable)
    """
    # --- STEP 1: Load product and inventory data ---
    product = Product.query.get_or_404(product_id)
    inventory = Inventory.query.filter_by(product_id=product_id).first()
    stock_level = int(inventory.current_stock) if inventory else 0

    # --- STEP 2: Load market statistics (Phase 3D) with geo-filtering ---
    # Pass the shop object so market_analysis can filter observations
    # by the shop's state/district (3-tier geographic fallback).
    market = get_market_stats(product_id, shop)
    has_market_data = market.get("n", 0) > 0

    # --- STEP 3: Estimate sales velocity ---
    velocity = _estimate_sales_velocity(product_id)

    # --- STEP 4: Compute product base quantity for feature engineering ---
    base_qty = _product_base_quantity(product) or 1.0

    # --- STEP 5: Extract market features (default to 0 if no data) ---
    market_median = market.get("median") or 0.0
    market_mean = market.get("mean") or 0.0
    market_min = market.get("min") or 0.0
    market_max = market.get("max") or 0.0
    market_spread = market.get("spread") or 0.0

    # Compute average normalized unit price across verified match observations.
    unit_price = 0.0
    if has_market_data and market.get("matches"):
        from app import MarketPriceObservation, MarketItem, MarketSource, ProductMarketMatch
        obs_prices = []
        matches = (ProductMarketMatch.query
                   .filter_by(shop_product_id=product_id, is_verified=True)
                   .all())
        for m in matches:
            # Query observations joined through MarketItem and MarketSource
            # to ensure only ACTIVE sources contribute.
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

    # --- STEP 6: Build the 13-feature vector ---
    # price_to_market_ratio: how the shop's cost compares to the market.
    # High ratio = shop pays more than market average (squeezed margins).
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

    # --- STEP 7: ML Prediction ---
    model_data = _load_model()
    has_model = model_data is not None

    if has_model:
        # Reshape features into the 2D array the model expects (1 sample, 13 features).
        X = np.array([[features[f] for f in FEATURE_NAMES]])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ml_prediction = round(float(model_data["model"].predict(X)[0]), 2)
    else:
        # Fallback: use the deterministic formula (cost * (1 + margin/100)).
        ml_prediction = product.suggested_price

    # Store the raw ML prediction before guardrails modify it.
    original_prediction = ml_prediction

    # --- STEP 8: Apply guardrails in strict order ---
    reasoning = []
    warnings_list = []
    guardrails_triggered = False
    guardrails_applied = []
    regulatory_cap_applied = False

    # Rule 0: REGULATORY CAP (KPDN Barangan Kawalan)
    # This is the highest-priority rule — government price controls
    # override ALL other pricing logic. The price is hard-capped at
    # the official KPDN ceiling.
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

    # Rule 1: COST FLOOR
    # Ensures the shop never sells below cost * 1.05 (5% minimum margin).
    ml_prediction, floor_hit = _apply_cost_floor(ml_prediction, float(product.cost_price))
    if floor_hit:
        guardrails_triggered = True
        guardrails_applied.append("cost_floor")
        reasoning.append(
            f"Cost floor applied: price raised to RM{ml_prediction:.2f} "
            f"(minimum 5% margin over cost RM{product.cost_price:.2f})"
        )

    # Rule 2: MARKET SANITY
    # Clamps the price within reasonable market bounds to prevent
    # extreme recommendations that would be unsellable.
    ml_prediction, sanity_hit = _apply_market_sanity(ml_prediction, market_min, market_max)
    if sanity_hit:
        guardrails_triggered = True
        guardrails_applied.append("market_sanity")
        reasoning.append(
            f"Market sanity clamp: price adjusted to RM{ml_prediction:.2f} "
            f"(market range RM{market_min:.2f}\u2013RM{market_max:.2f})"
        )

    # Rule 3: PCAPA CHECK (informational — produces a warning, not a cap)
    pcapa_warning, pcatpa_ok = _check_pcapa(product, ml_prediction)
    if pcapa_warning:
        guardrails_triggered = True
        guardrails_applied.append("pcapa")
        warnings_list.append(pcapa_warning)
        reasoning.append("PCAPA compliance check: margin exceeds baseline without cost justification")

    # --- STEP 9: Build reasoning strings ---
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
        reasoning.append("No verified market data \u2014 recommendation is rule-based only")

    # --- STEP 10: Feature importances (top 5 factors) ---
    importances = {}
    if has_model and "feature_importances" in model_data:
        importances = dict(sorted(
            model_data["feature_importances"].items(),
            key=lambda x: -x[1]
        )[:5])

    # --- STEP 11: Compute confidence ---
    confidence, confidence_score = _compute_confidence(
        has_market_data, has_model, guardrails_triggered
    )

    # --- STEP 12: Price difference from current price ---
    current_price = float(product.selling_price) if product.selling_price else None
    diff_pct = None
    if current_price and current_price > 0:
        diff_pct = round((ml_prediction / current_price - 1) * 100, 1)

    # --- STEP 13: Generate LLM explanation (Phase 3F) ---
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

    # --- STEP 14: Return the complete recommendation payload ---
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


# -------------------------------------------------
# APPLY RECOMMENDED PRICE
# -------------------------------------------------

def apply_price(product_id, user_id):
    """Apply the recommended price to a product and log the audit trail.

    This function is called when the shop owner clicks "Apply" on the
    Pricing Recommendation tab. It:
      1. Calls get_price_recommendation() to get the guarded price.
      2. Updates the product's selling_price.
      3. Creates a PriceHistory entry for the audit trail.
      4. Commits both changes as one transaction.

    Args:
        product_id: The integer ID of the product.
        user_id: The integer ID of the user applying the price (audit).

    Returns:
        A tuple (applied_price, flash_message).
    """
    # Load the product and get the full recommendation.
    product = Product.query.get_or_404(product_id)
    rec = get_price_recommendation(product_id)
    new_price = rec["recommended_price"]

    # Update the selling price (this is what customers see).
    old_price = product.selling_price
    product.selling_price = new_price

    # Log the change in PriceHistory for the audit trail.
    # This allows the PCAPA baseline comparison to track price evolution.
    db.session.add(PriceHistory(
        product_id=product.id,
        cost_price=product.cost_price,
        selling_price=new_price,
        target_margin=product.target_margin,
    ))

    # Commit both the price update and the history entry atomically.
    db.session.commit()

    return new_price, f"Price updated to RM{new_price:.2f} (AI recommendation applied)"
