"""
============================================================
 ShelfSenseAI — Phase 3E: Pricing Model Training Script
============================================================

This script generates synthetic historical sales data using real PriceCatcher
market observations, then trains a RandomForestRegressor to predict optimal
selling prices.

THE COLD-START PROBLEM
----------------------
ShelfSenseAI is a new system with no real historical sales data. To train
a machine learning model, we need training examples. This script solves the
"cold-start problem" by generating SYNTHETIC training data that blends:
  - Real market prices from PriceCatcher (actual government data)
  - Simulated shop scenarios (cost, margin, stock, velocity)

Each synthetic scenario represents a plausible shop configuration, and the
"optimal price" label is computed using a deterministic formula that balances:
  1. Covering cost with the desired margin
  2. Staying competitive with the market median
  3. Adjusting for inventory levels (high stock -> nudge price down)

TRAINING PIPELINE
-----------------
1. Load real market data from the database (MarketItem + MarketPriceObservation).
2. For each market item, generate N_SAMPLES_PER_ITEM synthetic shop scenarios.
3. Engineer 13 features matching the inference schema.
4. Split into train/test sets (80/20).
5. Train a RandomForestRegressor (100 trees, max_depth=10).
6. Evaluate (MAE, R²) and save to ml/pricing_model.pkl.

USAGE
-----
    ./venv/Scripts/python scripts/train_pricing_model.py

The model file (ml/pricing_model.pkl) is idempotent — re-running regenerates
synthetic data and retrains. The file is excluded from version control
(.gitignore) because it is a generated artifact.

NOTE: This script must be run AFTER the ETL pipeline (scripts/etl_pricecatcher.py)
has populated the MarketItem and MarketPriceObservation tables.
============================================================
"""
import os
import sys
import random
import warnings

import numpy as np
import joblib
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error, r2_score

# ---------------------------------------------------------------------------
# App context setup — we need the real DB to read PriceCatcher observations.
# The project root is added to sys.path so imports work from any working dir.
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("FLASK_APP", "app.py")

from app import (  # noqa: E402
    app, db, MarketItem, MarketPriceObservation, MarketSource
)
from utils.normalization import normalize_package_size  # noqa: E402


# ---------------------------------------------------------------------------
# CONSTANTS — Training configuration
# ---------------------------------------------------------------------------
RANDOM_SEED = 42              # Reproducibility: same seed -> same synthetic data.
N_SAMPLES_PER_ITEM = 8        # Synthetic shop scenarios per market item.
                                # 405 items * 8 = 3,240 total training samples.
COST_FACTOR_RANGE = (0.7, 1.1)  # Wholesale cost as fraction of market price.
                                  # 0.7 = 30% wholesale discount, 1.1 = 10% above market.
MARGIN_RANGE = (5.0, 50.0)     # Target margin range (%) — covers all realistic scenarios.
STOCK_RANGE = (0, 100)         # Inventory levels (0 = out of stock, 100 = fully stocked).
VELOCITY_RANGE = (1, 20)       # Daily units sold (1 = slow mover, 20 = high demand).

# Feature names — must match the inference schema in services/pricing_engine.py
# EXACTLY. The order determines the feature vector position in the model.
FEATURE_NAMES = [
    "cost_price",           # What the shop pays for the product (RM)
    "target_margin",        # Desired margin percentage
    "baseline_margin",      # Historical baseline margin (PCAPA reference)
    "market_median",        # Median market price (scaled to product size)
    "market_mean",          # Mean market price
    "market_min",           # Minimum observed market price
    "market_max",           # Maximum observed market price
    "market_spread",        # max - min (price volatility)
    "normalized_unit_price",# RM per base unit (kg/l/unit)
    "stock_level",          # Current inventory quantity
    "sales_velocity",       # Estimated daily units sold
    "price_to_market_ratio",# cost_price / market_median
    "quantity",             # Product package quantity
]


# ---------------------------------------------------------------------------
# DATA LOADING
# ---------------------------------------------------------------------------

def _load_market_data():
    """Load all market items with their price observations from the database.

    This function queries the Phase 3A market schema to extract real
    PriceCatcher data. For each MarketItem, it collects all price
    observations and computes per-item statistics (median, mean, min, max).

    Returns:
        A list of dicts, one per market item, containing:
          - item_id, normalized_title, package_qty, package_unit
          - prices: list of all observation prices
          - median, mean, min, max: summary statistics
          - unit_price: normalized unit price from the first observation

    Raises:
        SystemExit: If no PriceCatcher MarketSource exists (ETL not run).
    """
    with app.app_context():
        # Verify that the ETL has been run (PriceCatcher source must exist).
        source = MarketSource.query.filter_by(name="PriceCatcher").first()
        if source is None:
            print("ERROR: No MarketSource 'PriceCatcher' found. Run ETL first.")
            sys.exit(1)

        # Fetch all market items from the PriceCatcher source.
        items = (MarketItem.query
                 .filter_by(source_id=source.id)
                 .all())

        # For each item, collect and summarize its price observations.
        item_data = []
        for item in items:
            # Query all observations ordered chronologically.
            obs_list = (MarketPriceObservation.query
                        .filter_by(market_item_id=item.id)
                        .order_by(MarketPriceObservation.observed_at.asc())
                        .all())

            # Skip items with no observations (no price data available).
            if not obs_list:
                continue

            # Extract valid prices (filter out None/zero values).
            prices = [float(o.regular_price) for o in obs_list
                      if o.regular_price and float(o.regular_price) > 0]

            # Skip items where all observations are invalid.
            if not prices:
                continue

            # Store the item's data as a structured dict.
            item_data.append({
                "item_id": item.id,
                "normalized_title": item.normalized_title or "",
                "package_qty": float(item.package_quantity) if item.package_quantity else 1.0,
                "package_unit": (item.package_unit or "unit").lower(),
                "prices": prices,
                "median": float(np.median(prices)),
                "mean": float(np.mean(prices)),
                "min": float(np.min(prices)),
                "max": float(np.max(prices)),
                "unit_price": (float(obs_list[0].normalized_unit_price)
                               if obs_list[0].normalized_unit_price else None),
            })

        return item_data


# ---------------------------------------------------------------------------
# SYNTHETIC DATA GENERATION
# ---------------------------------------------------------------------------

def _generate_synthetic_samples(item_data, rng):
    """Generate synthetic training samples from real market data.

    For each market item, this function simulates N_SAMPLES_PER_ITEM
    different shop scenarios. Each scenario represents a plausible
    shop configuration with:
      - A wholesale cost (70-110% of market average)
      - A target margin (5-50%)
      - An inventory level (0-100 units)
      - A sales velocity (1-20 units/day)

    The "optimal price" label is computed deterministically using a
    formula that balances three factors:
      1. Cost coverage with desired margin
      2. Market competitiveness (weighted blend with median)
      3. Stock adjustment (high stock -> lower price to move inventory)

    Args:
        item_data: List of dicts from _load_market_data().
        rng: A random.Random instance with a fixed seed for reproducibility.

    Returns:
        A list of dicts, each containing 13 features + the optimal_price label.
    """
    samples = []

    for item in item_data:
        # Extract pre-computed statistics for this market item.
        market_median = item["median"]
        market_mean = item["mean"]
        market_min = item["min"]
        market_max = item["max"]
        market_spread = market_max - market_min
        unit_price = item["unit_price"] or market_mean
        pkg_qty = item["package_qty"]

        # Generate N_SAMPLES_PER_ITEM synthetic scenarios for this item.
        for _ in range(N_SAMPLES_PER_ITEM):
            # --- SIMULATE SHOP COST ---
            # Wholesale cost: 70-110% of market average.
            # This models the reality that shops buy at varying discounts.
            cost_factor = rng.uniform(*COST_FACTOR_RANGE)
            cost_price = market_mean * cost_factor

            # --- SIMULATE SHOP MARGIN ---
            # Target margin: 5-50%, covering all realistic retail scenarios.
            target_margin = rng.uniform(*MARGIN_RANGE)

            # Baseline margin: same or slightly lower (simulates historical data).
            # Used for PCAPA compliance checking.
            baseline_margin = max(5.0, target_margin - rng.uniform(0, 15))

            # --- SIMULATE INVENTORY AND DEMAND ---
            stock_level = rng.randint(*STOCK_RANGE)

            # Sales velocity: inversely correlated with price.
            # Higher prices tend to reduce demand (basic economics).
            base_velocity = rng.uniform(*VELOCITY_RANGE)
            price_pressure = 1.0 - (target_margin / 100.0) * 0.3
            sales_velocity = max(1, int(base_velocity * price_pressure))

            # Price-to-market ratio: how the shop's cost compares to the market.
            price_to_market = cost_price / market_median if market_median > 0 else 1.0

            # --- COMPUTE OPTIMAL SELLING PRICE (THE LABEL) ---
            # The "ideal" price balances three factors:

            # Factor 1: Absolute minimum (cost floor) — 5% above cost.
            cost_floor = cost_price * 1.05

            # Factor 2: Margin target price (cost * (1 + margin/100)).
            margin_price = cost_price * (1 + target_margin / 100)

            # Factor 3: Weighted blend of margin target and market median.
            # 60% margin target + 40% market median (when market data exists).
            # This teaches the model to balance internal goals with external reality.
            if market_median > 0:
                blend = 0.6 * margin_price + 0.4 * market_median
            else:
                blend = margin_price

            # Factor 4: Stock adjustment — high stock nudges price down
            # to move inventory (basic supply-demand dynamics).
            stock_ratio = stock_level / max(STOCK_RANGE[1], 1)
            stock_adjustment = 1.0 - (stock_ratio * 0.10)  # up to 10% discount

            optimal_price = blend * stock_adjustment

            # Enforce cost floor — the label must never suggest a loss.
            optimal_price = max(optimal_price, cost_floor)

            # Cap at 130% of market max to prevent extreme overpricing.
            if market_max > 0:
                optimal_price = min(optimal_price, market_max * 1.3)

            optimal_price = round(optimal_price, 2)

            # --- ASSEMBLE THE TRAINING SAMPLE ---
            samples.append({
                "cost_price": round(cost_price, 2),
                "target_margin": round(target_margin, 1),
                "baseline_margin": round(baseline_margin, 1),
                "market_median": round(market_median, 2),
                "market_mean": round(market_mean, 2),
                "market_min": round(market_min, 2),
                "market_max": round(market_max, 2),
                "market_spread": round(market_spread, 2),
                "normalized_unit_price": round(unit_price, 4),
                "stock_level": stock_level,
                "sales_velocity": sales_velocity,
                "price_to_market_ratio": round(price_to_market, 4),
                "quantity": pkg_qty,
                "optimal_price": optimal_price,
            })

    return samples


# ---------------------------------------------------------------------------
# TRAINING PIPELINE
# ---------------------------------------------------------------------------

def train():
    """Main training pipeline: load data, generate samples, train, evaluate, save.

    This function orchestrates the complete ML training process:
      1. Load real market data from the database.
      2. Generate synthetic training samples.
      3. Build the feature matrix (X) and label vector (y).
      4. Split into train/test sets (80/20).
      5. Train a RandomForestRegressor.
      6. Evaluate on the test set (MAE, R²).
      7. Save the trained model to ml/pricing_model.pkl.

    Returns:
        The model payload dict containing the trained model and metadata.
    """
    print("=" * 60)
    print("ShelfSenseAI — Phase 3E Pricing Model Training")
    print("=" * 60)

    # --- Step 1: Load real market data from the database ---
    print("\n[1/4] Loading market data from database...")
    item_data = _load_market_data()
    print(f"  Loaded {len(item_data)} market items with price observations")

    # Ensure we have enough data to train a meaningful model.
    if len(item_data) < 10:
        print("ERROR: Insufficient market data. Need at least 10 items.")
        sys.exit(1)

    # --- Step 2: Generate synthetic training samples ---
    print("\n[2/4] Generating synthetic training samples...")
    # Use a fixed seed for reproducibility — same seed always produces
    # the same synthetic data, which is important for debugging.
    rng = random.Random(RANDOM_SEED)
    samples = _generate_synthetic_samples(item_data, rng)
    print(f"  Generated {len(samples)} training samples from {len(item_data)} items")

    # --- Step 3: Build feature matrix and label vector ---
    X = np.array([[s[f] for f in FEATURE_NAMES] for s in samples])
    y = np.array([s["optimal_price"] for s in samples])

    print(f"\n[3/4] Training RandomForestRegressor...")
    print(f"  Features: {len(FEATURE_NAMES)}")
    print(f"  Samples:  {len(X)}")

    # --- Step 4: Train/test split (80/20) ---
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=RANDOM_SEED
    )

    # --- Step 5: Train the model ---
    # RandomForestRegressor with 100 trees and max_depth=10.
    # - 100 trees: enough for stable predictions without overfitting.
    # - max_depth=10: prevents memorizing noise in the synthetic data.
    # - n_jobs=-1: use all CPU cores for faster training.
    model = RandomForestRegressor(
        n_estimators=100,
        max_depth=10,
        random_state=RANDOM_SEED,
        n_jobs=-1
    )
    model.fit(X_train, y_train)

    # --- Step 6: Evaluate on the test set ---
    y_pred = model.predict(X_test)
    mae = mean_absolute_error(y_test, y_pred)
    r2 = r2_score(y_test, y_pred)

    print(f"\n  Model Performance:")
    print(f"    MAE:  RM{mae:.4f}")  # Mean Absolute Error
    print(f"    R\u00b2:   {r2:.4f}")  # Coefficient of determination

    # Display top 5 feature importances for interpretability.
    importances = dict(zip(FEATURE_NAMES, model.feature_importances_))
    top_5 = sorted(importances.items(), key=lambda x: -x[1])[:5]
    print(f"\n  Top 5 Feature Importances:")
    for name, imp in top_5:
        print(f"    {name:25s} {imp:.4f}")

    # --- Step 7: Save the model to disk ---
    print(f"\n[4/4] Saving model...")
    ml_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ml")
    os.makedirs(ml_dir, exist_ok=True)
    model_path = os.path.join(ml_dir, "pricing_model.pkl")

    # Save a payload dict (not just the model) so we can load feature names,
    # importances, and training metadata alongside the model.
    payload = {
        "model": model,
        "feature_names": FEATURE_NAMES,
        "train_samples": len(X_train),
        "test_samples": len(X_test),
        "mae": mae,
        "r2": r2,
        "feature_importances": importances,
    }
    joblib.dump(payload, model_path)
    print(f"  Saved to: {model_path}")
    print(f"  File size: {os.path.getsize(model_path) / 1024:.1f} KB")

    print("\n" + "=" * 60)
    print("Training complete. Model ready for inference.")
    print("=" * 60)

    return payload


if __name__ == "__main__":
    # Suppress sklearn warnings for cleaner output.
    warnings.filterwarnings("ignore")
    train()
