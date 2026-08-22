"""
============================================================
 ShelfSenseAI — Phase 3E: Pricing Model Training Script
============================================================
 Generates synthetic historical sales data using real PriceCatcher
 market observations, then trains a RandomForestRegressor to predict
 optimal selling prices.

 Run:
   ./venv/Scripts/python scripts/train_pricing_model.py

 Idempotent — re-running regenerates synthetic data and retrains.
 The model file (ml/pricing_model.pkl) is a generated artifact,
 excluded from version control.
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
# App context — we need the real DB to read PriceCatcher observations
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("FLASK_APP", "app.py")

from app import (  # noqa: E402
    app, db, MarketItem, MarketPriceObservation, MarketSource
)
from utils.normalization import normalize_package_size  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
RANDOM_SEED = 42
N_SAMPLES_PER_ITEM = 8        # synthetic shop scenarios per market item
COST_FACTOR_RANGE = (0.7, 1.1)  # wholesale cost as fraction of market price
MARGIN_RANGE = (5.0, 50.0)     # target margin range %
STOCK_RANGE = (0, 100)         # inventory levels
VELOCITY_RANGE = (1, 20)       # daily units sold

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
# Synthetic data generation
# ---------------------------------------------------------------------------

def _load_market_data():
    """Load all market items with their price observations, grouped by item+date."""
    with app.app_context():
        source = MarketSource.query.filter_by(name="PriceCatcher").first()
        if source is None:
            print("ERROR: No MarketSource 'PriceCatcher' found. Run ETL first.")
            sys.exit(1)

        # Get all items with observations
        items = (MarketItem.query
                 .filter_by(source_id=source.id)
                 .all())

        # For each item, collect observations grouped by date
        item_data = []
        for item in items:
            obs_list = (MarketPriceObservation.query
                        .filter_by(market_item_id=item.id)
                        .order_by(MarketPriceObservation.observed_at.asc())
                        .all())
            if not obs_list:
                continue

            # Compute per-item statistics across all dates
            prices = [float(o.regular_price) for o in obs_list
                      if o.regular_price and float(o.regular_price) > 0]
            if not prices:
                continue

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


def _generate_synthetic_samples(item_data, rng):
    """Generate synthetic training samples from real market data.

    For each market item, we simulate N_SAMPLES_PER_ITEM shop scenarios:
    - cost_price = market_avg × random factor (wholesale discount)
    - target_margin = random margin
    - stock_level = random inventory
    - sales_velocity = random daily rate
    - optimal_price = the price that balances margin and competitiveness
    """
    samples = []

    for item in item_data:
        market_median = item["median"]
        market_mean = item["mean"]
        market_min = item["min"]
        market_max = item["max"]
        market_spread = market_max - market_min
        unit_price = item["unit_price"] or market_mean
        pkg_qty = item["package_qty"]

        for _ in range(N_SAMPLES_PER_ITEM):
            # Wholesale cost: 70-110% of market average
            cost_factor = rng.uniform(*COST_FACTOR_RANGE)
            cost_price = market_mean * cost_factor

            # Target margin: 5-50%
            target_margin = rng.uniform(*MARGIN_RANGE)

            # Baseline margin: same or slightly lower (simulates historical baseline)
            baseline_margin = max(5.0, target_margin - rng.uniform(0, 15))

            # Stock level
            stock_level = rng.randint(*STOCK_RANGE)

            # Sales velocity (inversely correlated with price — higher price = fewer sales)
            base_velocity = rng.uniform(*VELOCITY_RANGE)
            price_pressure = 1.0 - (target_margin / 100.0) * 0.3
            sales_velocity = max(1, int(base_velocity * price_pressure))

            # Price-to-market ratio
            price_to_market = cost_price / market_median if market_median > 0 else 1.0

            # --- Optimal selling price (the label) ---
            # The "ideal" price balances:
            # 1. Covering cost with desired margin
            # 2. Staying competitive with market median
            # 3. Adjusting for stock level (high stock → lower price to move inventory)

            cost_floor = cost_price * 1.05  # absolute minimum
            margin_price = cost_price * (1 + target_margin / 100)

            # Weighted blend of margin target and market median
            # If market median exists, weight towards it
            if market_median > 0:
                blend = 0.6 * margin_price + 0.4 * market_median
            else:
                blend = margin_price

            # Stock adjustment: if stock is high, nudge price down
            stock_ratio = stock_level / max(STOCK_RANGE[1], 1)
            stock_adjustment = 1.0 - (stock_ratio * 0.10)  # up to 10% discount for full stock

            optimal_price = blend * stock_adjustment

            # Enforce cost floor
            optimal_price = max(optimal_price, cost_floor)

            # Don't let it exceed market max × 1.3 (overpriced)
            if market_max > 0:
                optimal_price = min(optimal_price, market_max * 1.3)

            optimal_price = round(optimal_price, 2)

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
# Training
# ---------------------------------------------------------------------------

def train():
    """Main training pipeline."""
    print("=" * 60)
    print("ShelfSenseAI — Phase 3E Pricing Model Training")
    print("=" * 60)

    # 1. Load real market data
    print("\n[1/4] Loading market data from database...")
    item_data = _load_market_data()
    print(f"  Loaded {len(item_data)} market items with price observations")

    if len(item_data) < 10:
        print("ERROR: Insufficient market data. Need at least 10 items.")
        sys.exit(1)

    # 2. Generate synthetic training data
    print("\n[2/4] Generating synthetic training samples...")
    rng = random.Random(RANDOM_SEED)
    samples = _generate_synthetic_samples(item_data, rng)
    print(f"  Generated {len(samples)} training samples from {len(item_data)} items")

    # 3. Prepare feature matrix and labels
    X = np.array([[s[f] for f in FEATURE_NAMES] for s in samples])
    y = np.array([s["optimal_price"] for s in samples])

    print(f"\n[3/4] Training RandomForestRegressor...")
    print(f"  Features: {len(FEATURE_NAMES)}")
    print(f"  Samples:  {len(X)}")

    # Train/test split
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=RANDOM_SEED
    )

    # Train model
    model = RandomForestRegressor(
        n_estimators=100,
        max_depth=10,
        random_state=RANDOM_SEED,
        n_jobs=-1
    )
    model.fit(X_train, y_train)

    # Evaluate
    y_pred = model.predict(X_test)
    mae = mean_absolute_error(y_test, y_pred)
    r2 = r2_score(y_test, y_pred)

    print(f"\n  Model Performance:")
    print(f"    MAE:  RM{mae:.4f}")
    print(f"    R²:   {r2:.4f}")

    # Feature importances
    importances = dict(zip(FEATURE_NAMES, model.feature_importances_))
    top_5 = sorted(importances.items(), key=lambda x: -x[1])[:5]
    print(f"\n  Top 5 Feature Importances:")
    for name, imp in top_5:
        print(f"    {name:25s} {imp:.4f}")

    # 4. Save model
    print(f"\n[4/4] Saving model...")
    ml_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ml")
    os.makedirs(ml_dir, exist_ok=True)
    model_path = os.path.join(ml_dir, "pricing_model.pkl")

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
    warnings.filterwarnings("ignore")
    train()
