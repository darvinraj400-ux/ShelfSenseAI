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
from datetime import datetime, timedelta

import joblib
import numpy as np

from app import (  # noqa: E402
    db, Product, Inventory, PriceHistory
)
from services.market_analysis import (get_market_stats,           # noqa: E402
                                      get_market_trend, _product_base_quantity)
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


# -------------------------------------------------
# PHASE 7 — DECISION-SUPPORT SIGNALS (pure functions)
#
# These helpers turn the Phase 6 market-intelligence data into
# deterministic, traceable pricing-context signals. They NEVER set the
# final price — the existing guardrail pipeline below stays authoritative.
# All thresholds are documented constants with dedicated unit tests
# (tests/test_pricing_decision.py).
# -------------------------------------------------

# Recommendation status: changes smaller than this percentage of the
# current price are reported as MAINTAIN (avoids meaningless one-cent
# suggestions; matches the +/-0.5% band already used by the UI badges).
STATUS_TOLERANCE_PCT = 0.5

# Market trend classification: the median change between the FIRST and
# LAST observation dates of the trend window. Changes within +/- this
# band are "Stable". A span shorter than TREND_MIN_SPAN_DAYS (or fewer
# than TREND_MIN_POINTS dates) is "insufficient_data" — one-day changes
# are too noisy to act on, so no trend is reported for them.
TREND_STABLE_BAND_PCT = 1.0
TREND_MIN_POINTS = 2
TREND_MIN_SPAN_DAYS = 7

# Market evidence: how much real PriceCatcher evidence stands behind the
# market statistics shown to the retailer (premise count + observation
# count + geographic tier). Documented bands:
#   Unavailable  n < 3            — not enough observations for statistics
#   Limited      < 5 premises or < 20 observations
#   Moderate     >= 3 premises and >= 5 observations
#   Strong       >= 5 premises, >= 20 observations, district/state tier
EVIDENCE_STRONG_PREMISES = 5
EVIDENCE_STRONG_OBS = 20
EVIDENCE_MODERATE_PREMISES = 3
EVIDENCE_MODERATE_OBS = 5


def _classify_trend(points):
    """Classify the market trend from Phase 6 trend points.

    Deterministic: compares the market median on the FIRST date with the
    median on the LAST date of the (already date-bounded) trend window.

    Args:
        points: List of dicts from get_market_trend() with keys
                'date' (ISO string) and 'median' (RM or None).

    Returns:
        {'direction': 'Rising' | 'Falling' | 'Stable' | 'insufficient_data',
         'change_percent': float | None}
    """
    usable = [p for p in (points or [])
              if p.get('median') is not None and p.get('date')]
    if len(usable) < TREND_MIN_POINTS:
        return {'direction': 'insufficient_data', 'change_percent': None}

    first, last = usable[0], usable[-1]
    try:
        span_days = ((datetime.fromisoformat(last['date'])
                      - datetime.fromisoformat(first['date'])).days)
    except (TypeError, ValueError):
        return {'direction': 'insufficient_data', 'change_percent': None}

    if span_days < TREND_MIN_SPAN_DAYS or first['median'] <= 0:
        return {'direction': 'insufficient_data', 'change_percent': None}

    pct = (last['median'] - first['median']) / first['median'] * 100
    if pct > TREND_STABLE_BAND_PCT:
        direction = 'Rising'
    elif pct < -TREND_STABLE_BAND_PCT:
        direction = 'Falling'
    else:
        direction = 'Stable'
    return {'direction': direction, 'change_percent': round(pct, 1)}


def _market_evidence(stats):
    """Rate the market evidence behind the Phase 6 statistics.

    Pure function over a get_market_stats() result dict. Rates how much
    independent store-level evidence the market benchmark rests on —
    NOT an "AI confidence" (there is no probabilistic model here).

    Returns 'Strong' | 'Moderate' | 'Limited' | 'Unavailable'.
    """
    n = stats.get('n') or 0
    premises = stats.get('premise_count') or 0
    if n < 3 or premises < 1:
        return 'Unavailable'
    tier = stats.get('market_tier')
    if (premises >= EVIDENCE_STRONG_PREMISES
            and n >= EVIDENCE_STRONG_OBS
            and tier in ('district', 'state')):
        return 'Strong'
    if premises >= EVIDENCE_MODERATE_PREMISES and n >= EVIDENCE_MODERATE_OBS:
        return 'Moderate'
    return 'Limited'


def recommendation_status(recommended_price, current_price,
                          tolerance_pct=STATUS_TOLERANCE_PCT):
    """Derive the action status from the final price vs the current price.

    Deterministic and traced to the two inputs only:
      recommended <  current (beyond tolerance) -> 'REDUCE'
      recommended >  current (beyond tolerance) -> 'INCREASE'
      |change| within tolerance                -> 'MAINTAIN'
      no current price                         -> 'INSUFFICIENT_DATA'

    Args:
        recommended_price: Final guardrailed recommendation (RM) or None.
        current_price: The shop's current selling price (RM) or None.
        tolerance_pct: Band (in % of current price) treated as MAINTAIN.
    """
    if not recommended_price or not current_price or current_price <= 0:
        return 'INSUFFICIENT_DATA'
    pct = (recommended_price / current_price - 1) * 100
    if abs(pct) <= tolerance_pct:
        return 'MAINTAIN'
    return 'REDUCE' if recommended_price < current_price else 'INCREASE'


def _get_model_path():
    """Resolve the ML model file location for the current environment.

    Leapcell builds download the model into /tmp/ml/ (the only writable
    directory on its read-only filesystem), while local development keeps
    the model at <project_root>/ml/pricing_model.pkl. The /tmp location
    is checked first so the deployed build always wins when present.
    """
    # Leapcell / production: /tmp/ml/pricing_model.pkl
    tmp_path = "/tmp/ml/pricing_model.pkl"
    if os.path.exists(tmp_path):
        return tmp_path
    # Local dev: <project_root>/ml/pricing_model.pkl
    local_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "ml", "pricing_model.pkl"
    )
    if os.path.exists(local_path):
        return local_path
    # Optional: allow env var override for testing
    return os.getenv("MODEL_PATH", local_path)


def _load_model():
    """Load the trained RandomForestRegressor from disk (cached after first load).

    The model file (ml/pricing_model.pkl) is a generated artifact produced
    by scripts/train_pricing_model.py. It is excluded from version control
    (.gitignore) because it is a build artifact, not source code. On Leapcell
    the model is downloaded at build time into /tmp/ml/ (see build.sh).

    Returns:
        The model payload dict (containing 'model', 'feature_names', etc.)
        if the file exists, or None if it does not (triggers rule-based fallback).
    """
    global _MODEL_CACHE

    # Return cached model if already loaded (avoids repeated disk reads).
    if _MODEL_CACHE is not None:
        return _MODEL_CACHE

    # Resolve the path for this environment (Leapcell /tmp first, then local).
    model_path = _get_model_path()

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

    # TOLERANCE THRESHOLD: A 1.5% absolute tolerance prevents the PCAPA
    # guardrail from self-sabotaging after the SME Margin Clamp raises
    # a price. Without this, rounding artifacts (e.g. 25.3% vs 25.0%)
    # would trigger false legal warnings that confuse shop owners.
    PCAPA_EPSILON = 1.5  # percentage points

    # Check if the implied margin exceeds the baseline (with tolerance).
    if implied_margin > product.baseline_margin + PCAPA_EPSILON:
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

def get_price_recommendation(product_id, shop=None, skip_llm=False):
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
        shop: Optional Shop ORM object for geographic market filtering.
        skip_llm: When True the Gemini explanation is NOT generated
            (llm_explanation is None). Phase 9: the shop-wide dashboard
            reads many recommendations at once and is a pure READ — the
            explanation is descriptive only, so skipping it can never
            change the recommended price, status, or any other field.
            The product page and pricing API keep the default (False).

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

    # Rule 1b: SME TARGET-MARGIN FLOOR (Hypermarket Bias Protection)
    # The ML model is trained on KPDN data dominated by hypermarkets
    # (Lotus's, Mydin) which operate on 3-5% margins. A small kedai
    # runcit needs 20-40% margins to survive. If the ML prediction is
    # below the shop's target floor (cost * (1 + target_margin/100)),
    # the price is raised to that floor. This is a HARD deterministic
    # minimum: the retailer's configured target margin always wins over
    # market pressure when the two conflict (Phase 7.1: an earlier
    # "60/40 blend" formulation was mathematically a no-op because
    # max(blend, floor) == floor whenever candidate <= floor; the
    # dead blend arithmetic has been removed and the behaviour is now
    # documented as what it actually is — a hard target-margin floor).
    user_floor = round(float(product.cost_price) * (1 + float(product.target_margin) / 100), 2)
    if ml_prediction < user_floor:
        # Hard floor: never recommend below the SME target-margin price.
        ml_prediction = user_floor
        guardrails_triggered = True
        guardrails_applied.append("sme_margin")
        reasoning.append(
            f"SME target-margin floor applied: raised from RM{original_prediction:.2f} "
            f"to RM{ml_prediction:.2f} (target margin {product.target_margin}% "
            f"to protect small shop viability against hypermarket pricing)"
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

    # --- STEP 12b: Phase 7 decision-support context ---
    # Trend signal reuses the Phase 6 trend query (server-side aggregation,
    # date-bounded) — no duplicated market SQL here. The trend NEVER moves
    # the price; it only enriches the explanation and evidence rating.
    from app import ProductMarketMatch  # local import (mirrors STEP 5)
    _mids = sorted({mid for (mid,) in (
        ProductMarketMatch.query
        .filter_by(shop_product_id=product_id, is_verified=True)
        .with_entities(ProductMarketMatch.market_item_id).all())})
    trend = get_market_trend(_mids, shop=shop) if _mids else         {'tier': None, 'tier_label': None, 'points': []}
    trend_signal = _classify_trend(trend.get('points'))

    # Market evidence rating (Strong/Moderate/Limited/Unavailable) over
    # the same Phase 6 statistics the UI displays.
    evidence = _market_evidence(market)

    # Phase 10F: freshness qualification (read-only, not a guardrail)
    # Reuses the source-isolated freshness already computed in market stats.
    # Never changes the price — only qualifies the evidence.
    _fresh_dict = market.get('freshness') if isinstance(market.get('freshness'), dict) else {}
    market_freshness = market.get('market_freshness') or _fresh_dict.get('freshness') or 'unavailable'
    freshness_label = market.get('freshness_label') or _fresh_dict.get('label') or 'Unavailable'
    freshness_warning = market.get('freshness_warning') or _fresh_dict.get('warning')
    freshness_age = market.get('freshness_age_days') if market.get('freshness_age_days') is not None else _fresh_dict.get('age_days')
    freshness_source = market.get('freshness_source') or _fresh_dict.get('source')

    # Recommendation status: derived AFTER the guardrails from the final
    # price vs the current price (MAINTAIN / REDUCE / INCREASE /
    # INSUFFICIENT_DATA). Pure comparison — no new pricing logic.
    status = recommendation_status(ml_prediction, current_price)

    # Guardrail-effect explanation (STEP 20): when a guardrail changed the
    # candidate, say so in human terms.
    guardrail_effect = None
    if 'cost_floor' in guardrails_applied and has_market_data             and market_median > 0 and market_median < ml_prediction:
        guardrail_effect = (
            f"The local market median (RM{market_median:.2f}) is below the "
            f"minimum economically viable price for this product, so the "
            f"recommendation prioritizes your cost floor "
            f"(RM{round(float(product.cost_price) * (1 + MIN_MARGIN_FLOOR), 2):.2f}) "
            f"rather than matching the market.")
    elif 'regulatory_cap' in guardrails_applied:
        guardrail_effect = (
            f"Regulatory price constraint applied: the suggestion was capped "
            f"at the KPDN ceiling of "
            f"RM{float(product.government_ceiling_price):.2f}.")
    elif 'sme_margin' in guardrails_applied:
        guardrail_effect = (
            f"Your target margin of {product.target_margin}% was protected: "
            f"the market-informed candidate sat below your target-margin floor "
            f"(cost \u00d7 {1 + float(product.target_margin) / 100:.2f} = "
            f"RM{round(float(product.cost_price) * (1 + float(product.target_margin) / 100), 2):.2f}), "
            f"so it was raised to that hard minimum.")
    elif 'market_sanity' in guardrails_applied:
        guardrail_effect = (
            "The market-informed candidate fell outside the observed market "
            "range, so it was clamped to a realistic price band.")

    # Market context line for the reasoning list (traceable to inputs).
    if has_market_data:
        evidence_note = (
            f"Market evidence is {evidence.lower()}: "
            f"{market.get('premise_count')} premises reported "
            f"{market.get('n')} observations "
            f"({market.get('market_tier_label') or 'national scope'}).")
        if trend_signal['direction'] == 'Rising':
            trend_note = (f"Market prices have been rising "
                          f"({trend_signal['change_percent']:+.1f}% over the observed period).")
        elif trend_signal['direction'] == 'Falling':
            trend_note = (f"Market prices have been falling "
                          f"({trend_signal['change_percent']:+.1f}% over the observed period).")
        elif trend_signal['direction'] == 'Stable':
            trend_note = "Market prices have remained relatively stable over the observed period."
        else:
            trend_note = "Not enough market history to establish a price trend yet."
    else:
        evidence_note = ("No sufficient market observations were found for this "
                         "product; the recommendation is based on your cost, "
                         "target margin, sales/inventory context, and existing "
                         "pricing rules.")
        trend_note = None

    # Phase 10F: freshness qualification (read-only, separate from evidence strength)
    freshness_note = None
    if has_market_data and market_freshness != 'unavailable':
        if market_freshness == 'fresh':
            freshness_note = f"Market data is {freshness_label.lower()} — evidence reflects current conditions."
        elif market_freshness == 'aging':
            # Use the warning from the freshness service if available
            freshness_note = f"Market data is {freshness_label.lower()} — {freshness_warning or 'observations are becoming less current.'}"
        elif market_freshness == 'stale':
            freshness_note = f"Market data is {freshness_label.lower()} — {freshness_warning or 'historical; current conditions may have changed.'}"
    # Phase 7: evidence/trend/freshness notes are appended AFTER the guardrail
    # reasons so the traceability order reads: prediction -> constraints
    # -> market context -> freshness qualification.
    if evidence_note:
        reasoning.append(evidence_note)
    if trend_note:
        reasoning.append(trend_note)
    if freshness_note:
        reasoning.append(freshness_note)
    if guardrail_effect:
        reasoning.append(guardrail_effect)

    # --- STEP 13: Generate LLM explanation (Phase 3F) ---
    # Phase 6: the payload now carries the full market-intelligence
    # context (tier, premise count, retailer position) so the explainer
    # can describe WHERE the shop sits in the local market. This is
    # context only — the recommended price was already fixed by the
    # guardrailed pipeline above and is NOT modified here.
    mkt_stats_payload = {
        "n": market.get("n", 0),
        "median": market.get("median"),
        "min": market.get("min"),
        "max": market.get("max"),
        "premise_count": market.get("premise_count"),
        "market_tier": market.get("market_tier"),
        "market_tier_label": market.get("market_tier_label"),
        "position": market.get("position"),
        "difference": market.get("difference"),
        "difference_percent": market.get("difference_percent"),
        "latest_observed_at": market.get("latest_observed_at"),
        # Phase 10F freshness (read-only qualification)
        "market_freshness": market_freshness,
        "freshness_label": freshness_label,
        "freshness_warning": freshness_warning,
        "freshness_age_days": freshness_age,
        "freshness_source": freshness_source,
    }
    llm_payload = {
        "recommended_price": ml_prediction,
        "confidence": confidence,
        "guardrails_applied": guardrails_applied,
        "warnings": warnings_list,
        "diff_pct": diff_pct,
        # Phase 7 decision-support context (explanatory only):
        "status": status,
        "market_evidence": evidence,
        "trend_direction": trend_signal['direction'],
        "trend_change_percent": trend_signal['change_percent'],
        "guardrail_effect": guardrail_effect,
        "target_margin": float(product.target_margin),
        "sales_velocity": velocity,
        "stock_level": stock_level,
        # Phase 10F freshness (explanatory only, never a guardrail)
        "market_freshness": market_freshness,
        "freshness_label": freshness_label,
        "freshness_warning": freshness_warning,
        "freshness_age_days": freshness_age,
        "freshness_source": freshness_source,
    }
    llm_explanation = (None if skip_llm
                       else generate_pricing_explanation(
                           product, mkt_stats_payload, llm_payload))

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
        # Phase 7: decision-support context
        "status": status,
        "market_evidence": evidence,
        "trend_direction": trend_signal['direction'],
        "trend_change_percent": trend_signal['change_percent'],
        "trend_tier_label": trend.get('tier_label'),
        "guardrail_effect": guardrail_effect,
        "evidence_note": evidence_note,
        "trend_note": trend_note,
        # Phase 10F freshness (read-only qualification, never a guardrail)
        "market_freshness": market_freshness,
        "freshness_label": freshness_label,
        "freshness_warning": freshness_warning,
        "freshness_age_days": freshness_age,
        "freshness_source": freshness_source,
        "freshness_note": freshness_note,
        "regulatory_cap_applied": regulatory_cap_applied,
        "government_ceiling_price": float(product.government_ceiling_price) if product.government_ceiling_price else None,
    }



