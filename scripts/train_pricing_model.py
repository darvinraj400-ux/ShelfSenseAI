"""
============================================================
 ShelfSenseAI - ML Training Pipeline (KPDN Big Data Edition)
============================================================

Trains the RandomForestRegressor on REAL historical KPDN PriceCatcher data
spanning January 2022 to August 2026 (~4.5 years, 56 monthly files).

This replaces the previous synthetic-data approach with actual government
market data, giving the model real-world pricing distributions to learn from.

MEMORY-EFFICIENT PIPELINE
--------------------------
Instead of loading all 56 files into memory at once, each month is:
  1. Downloaded individually.
  2. Merged with item + premise lookups.
  3. Filtered for Johor state.
  4. Aggregated by item_code + date.
  5. Appended to a running list of aggregated rows.

This means we never hold more than ~1 month of raw data in memory.

FULL PIPELINE
-------------
1. Download lookup tables (item catalog + premise directory) from data.gov.my.
2. For each of 56 monthly files:
   a. Download the parquet file.
   b. Merge with lookups to get item names and premise locations.
   c. Filter for Johor state.
   d. Aggregate by item_code + date: compute market_median, min, max, spread.
3. Combine all monthly aggregates.
4. Simulate shop cost_price (80-90% of market price) as training label basis.
5. Generate multiple shop scenarios per item-date (varying margin, stock, velocity).
6. Train RandomForestRegressor on the resulting feature matrix.
7. Evaluate and save to ml/pricing_model.pkl.

USAGE
-----
    python scripts/train_pricing_model.py

The model file is idempotent - re-running overwrites the previous version.
Excluded from version control (.gitignore).
============================================================
"""
import os
import sys
import re
import random
import warnings
from datetime import datetime

import numpy as np
import pandas as pd
import joblib
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error, r2_score

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
RANDOM_SEED = 42
BASE_URL = "https://storage.data.gov.my/pricecatcher/"

# Date range: January 2022 to August 2026
START_YEAR, START_MONTH = 2022, 1
END_YEAR, END_MONTH = 2026, 8

# Localization filter: Johor state, Segamat district (if available)
TARGET_STATE = "Johor"
TARGET_DISTRICT = "Segamat"  # Used if district column exists

# Training configuration
N_SCENARIOS_PER_ITEM_DATE = 5   # Shop scenarios per item-date aggregate
COST_FACTOR_RANGE = (0.80, 0.90)  # cost = market_price * factor
MARGIN_RANGE = (5.0, 50.0)        # target margin %
STOCK_RANGE = (0, 100)
VELOCITY_RANGE = (1, 20)

# Minimum premises required for a data point to be considered reliable
MIN_PREMISES = 5

# Excluded item groups (fresh produce and ready-to-cook items)
EXCLUDED_ITEM_GROUPS = {"BARANGAN SEGAR", "MAKANAN SIAP MASAK"}

# Feature names - MUST match services/pricing_engine.py exactly
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
# Unit parsing helpers
# ---------------------------------------------------------------------------
def _parse_unit_quantity(unit_str):
    """Extract numeric quantity from a unit string like '10 kg' or '500 g'.

    Args:
        unit_str: The raw unit string from the PriceCatcher dataset.

    Returns:
        Float quantity, defaulting to 1.0 if parsing fails.
    """
    if not unit_str or not isinstance(unit_str, str):
        return 1.0
    m = re.match(r"([0-9.]+)", unit_str.strip())
    return float(m.group(1)) if m else 1.0


def _parse_unit_name(unit_str):
    """Extract unit name from a string like '10 kg' -> 'kg'.

    Args:
        unit_str: The raw unit string from the PriceCatcher dataset.

    Returns:
        Lowercase unit name string, defaulting to 'unit' if parsing fails.
    """
    if not unit_str or not isinstance(unit_str, str):
        return "unit"
    m = re.match(r"[0-9.]+\s*([a-zA-Z]+)", unit_str.strip())
    return m.group(1).lower() if m else "unit"


# ---------------------------------------------------------------------------
# Step 1: Download lookup tables
# ---------------------------------------------------------------------------
def download_lookups():
    """Download the item catalog and premise directory from data.gov.my.

    These are small reference tables (~757 items, ~3,895 premises) used
    to enrich raw transaction data with item names and premise locations.

    Returns:
        Tuple of (item_df, premise_df) DataFrames.
    """
    print("[1/6] Downloading lookup tables...")

    item_url = f"{BASE_URL}lookup_item.parquet"
    premise_url = f"{BASE_URL}lookup_premise.parquet"

    print(f"  Items:   {item_url}")
    item_df = pd.read_parquet(item_url)
    # Normalize item_code to int then string for consistent joins
    item_df["item_code"] = item_df["item_code"].astype(int).astype(str)
    print(f"  -> {len(item_df):,} items, columns: {list(item_df.columns)}")

    print(f"  Premises: {premise_url}")
    premise_df = pd.read_parquet(premise_url)
    # Drop NaN premise codes (row -1 is junk data from the source)
    premise_df["premise_code"] = pd.to_numeric(
        premise_df["premise_code"], errors="coerce"
    )
    premise_df = premise_df.dropna(subset=["premise_code"])
    premise_df["premise_code"] = premise_df["premise_code"].astype(int).astype(str)
    # Filter out entries with NaN state (invalid premises)
    premise_df = premise_df.dropna(subset=["state"])
    print(f"  -> {len(premise_df):,} valid premises, columns: {list(premise_df.columns)}")

    return item_df, premise_df


# ---------------------------------------------------------------------------
# Step 2: Download and process monthly transactions (memory-efficient)
# ---------------------------------------------------------------------------
def download_and_process_monthly(item_df, premise_df):
    """Download each monthly file, merge, filter, and aggregate on the fly.

    Instead of loading all 56 files into memory, each month is:
      1. Downloaded.
      2. Merged with item + premise lookups (inner join to get state).
      3. Filtered for Johor state.
      4. Excluded item groups removed.
      5. Aggregated by item_code + date (median, mean, min, max, count).

    This keeps memory usage to ~1 month at a time.

    Args:
        item_df: Pre-processed item lookup DataFrame.
        premise_df: Pre-processed premise lookup DataFrame.

    Returns:
        DataFrame with one row per item-date aggregate across all months.
    """
    print("\n[2/6] Downloading and processing monthly transactions...")

    # Build lookup dicts for fast merging (avoids repeated DataFrame merges)
    # premise_code -> state mapping
    premise_state = dict(zip(premise_df["premise_code"], premise_df["state"]))
    premise_district = dict(zip(
        premise_df["premise_code"],
        premise_df.get("district", pd.Series(dtype=str))
    ))

    # item_code -> (item, unit, item_group, item_category) mapping
    item_lookup = {}
    for _, row in item_df.iterrows():
        item_lookup[row["item_code"]] = (
            row["item"],
            row["unit"],
            row.get("item_group", ""),
            row.get("item_category", ""),
        )

    # Collect aggregated rows from all months
    all_agg_rows = []
    current = datetime(START_YEAR, START_MONTH, 1)
    end = datetime(END_YEAR, END_MONTH, 1)
    success_count = 0
    fail_count = 0
    total_raw_rows = 0

    while current <= end:
        year = current.year
        month = current.month
        filename = f"pricecatcher_{year}-{month:02d}.parquet"
        url = f"{BASE_URL}{filename}"

        try:
            # Download single month
            df = pd.read_parquet(url)

            # Normalize date to string (YYYY-MM-DD)
            if "date" in df.columns:
                df["date"] = df["date"].astype(str).str[:10]

            # Normalize premise_code for lookup
            df["premise_code"] = df["premise_code"].astype(int).astype(str)

            # Normalize item_code for lookup
            df["item_code"] = df["item_code"].astype(int).astype(str)

            # Filter for valid premises in Johor (state lookup)
            df["state"] = df["premise_code"].map(premise_state)
            df = df.dropna(subset=["state"])
            df = df[df["state"].str.upper() == TARGET_STATE.upper()]

            if len(df) == 0:
                fail_count += 1
                # Advance to next month
                if month == 12:
                    current = datetime(year + 1, 1, 1)
                else:
                    current = datetime(year, month + 1, 1)
                continue

            # Add item metadata via lookup
            df["item_meta"] = df["item_code"].map(item_lookup)
            df = df.dropna(subset=["item_meta"])
            df["item"] = df["item_meta"].apply(lambda x: x[0])
            df["unit"] = df["item_meta"].apply(lambda x: x[1])
            df["item_group"] = df["item_meta"].apply(lambda x: x[2])
            df["item_category"] = df["item_meta"].apply(lambda x: x[3])
            df = df.drop(columns=["item_meta", "state"])

            # Filter out excluded item groups
            df = df[~df["item_group"].isin(EXCLUDED_ITEM_GROUPS)]

            # Ensure price is numeric and positive
            df["price"] = pd.to_numeric(df["price"], errors="coerce")
            df = df[df["price"] > 0]

            total_raw_rows += len(df)

            # Aggregate by item_code + date for this month
            if len(df) > 0:
                agg = df.groupby(["item_code", "date"]).agg(
                    market_median=("price", "median"),
                    market_mean=("price", "mean"),
                    market_min=("price", "min"),
                    market_max=("price", "max"),
                    n_premises=("price", "count"),
                    unit=("unit", "first"),
                    item_category=("item_category", "first"),
                ).reset_index()
                agg["market_spread"] = agg["market_max"] - agg["market_min"]

                # Filter unreliable aggregates (fewer than MIN_PREMISES)
                agg = agg[agg["n_premises"] >= MIN_PREMISES]

                all_agg_rows.append(agg)

            success_count += 1
            if success_count % 6 == 0:
                print(f"  -> Processed {success_count} months so far "
                      f"({year}-{month:02d}), {total_raw_rows:,} raw rows...")

        except Exception as e:
            fail_count += 1
            print(f"  [SKIP] Failed: {filename} ({e})")

        # Advance to next month
        if month == 12:
            current = datetime(year + 1, 1, 1)
        else:
            current = datetime(year, month + 1, 1)

    print(f"\n  -> Processed {success_count} months, {fail_count} failed")
    print(f"  -> Total raw rows processed: {total_raw_rows:,}")

    if not all_agg_rows:
        print("ERROR: No data aggregated. Check network connection.")
        sys.exit(1)

    # Combine all monthly aggregates
    combined = pd.concat(all_agg_rows, ignore_index=True)

    # Re-aggregate across months (some item-dates appear in multiple months)
    # Final aggregation: take the median across all months for each item-date
    final_agg = combined.groupby(["item_code", "date"]).agg(
        market_median=("market_median", "median"),
        market_mean=("market_mean", "mean"),
        market_min=("market_min", "min"),
        market_max=("market_max", "max"),
        n_premises=("n_premises", "sum"),
        unit=("unit", "first"),
        item_category=("item_category", "first"),
    ).reset_index()

    final_agg["market_spread"] = final_agg["market_max"] - final_agg["market_min"]

    # Compute normalized unit price
    final_agg["parsed_qty"] = final_agg["unit"].apply(_parse_unit_quantity)
    final_agg["parsed_unit"] = final_agg["unit"].apply(_parse_unit_name)
    final_agg["normalized_unit_price"] = final_agg.apply(
        lambda r: r["market_median"] / r["parsed_qty"]
        if r["parsed_qty"] > 0 else r["market_median"],
        axis=1,
    )

    print(f"  -> Unique item-dates: {len(final_agg):,}")
    print(f"  -> Unique items: {final_agg['item_code'].nunique()}")
    if len(final_agg) > 0:
        print(f"  -> Date range: {final_agg['date'].min()} to {final_agg['date'].max()}")

    return final_agg


# ---------------------------------------------------------------------------
# Step 3: Generate training samples
# ---------------------------------------------------------------------------
def generate_samples(agg_df, rng):
    """Generate training samples from aggregated market data.

    For each item-date row, creates N_SCENARIOS_PER_ITEM_DATE shop scenarios
    with varying cost, margin, stock, and velocity. The target label is a
    blended optimal price balancing margin goals with market competitiveness.

    Args:
        agg_df: Aggregated features DataFrame.
        rng: Random instance with fixed seed for reproducibility.

    Returns:
        List of dicts, each containing 13 features + optimal_price label.
    """
    print("\n[3/6] Generating training samples...")

    samples = []
    for _, row in agg_df.iterrows():
        market_median = float(row["market_median"])
        market_mean = float(row["market_mean"])
        market_min = float(row["market_min"])
        market_max = float(row["market_max"])
        market_spread = float(row["market_spread"])
        unit_price = float(row["normalized_unit_price"])
        pkg_qty = float(row["parsed_qty"])

        for _ in range(N_SCENARIOS_PER_ITEM_DATE):
            # Simulate shop cost (80-90% of market price)
            cost_factor = rng.uniform(*COST_FACTOR_RANGE)
            cost_price = market_median * cost_factor

            # Simulate target margin (5-50%)
            target_margin = rng.uniform(*MARGIN_RANGE)
            baseline_margin = max(5.0, target_margin - rng.uniform(0, 15))

            # Simulate inventory and demand
            stock_level = rng.randint(*STOCK_RANGE)
            base_velocity = rng.uniform(*VELOCITY_RANGE)
            price_pressure = 1.0 - (target_margin / 100.0) * 0.3
            sales_velocity = max(1, int(base_velocity * price_pressure))

            # Price-to-market ratio
            price_to_market = cost_price / market_median if market_median > 0 else 1.0

            # --- Compute optimal price (the training label) ---
            # Floor: cost * 1.05 (minimum viable margin)
            cost_floor = cost_price * 1.05

            # Margin target price
            margin_price = cost_price * (1 + target_margin / 100)

            # Blended price: 60% margin target + 40% market median
            if market_median > 0:
                blend = 0.6 * margin_price + 0.4 * market_median
            else:
                blend = margin_price

            # Stock adjustment: high stock nudges price down
            stock_ratio = stock_level / max(STOCK_RANGE[1], 1)
            stock_adjustment = 1.0 - (stock_ratio * 0.10)

            optimal_price = blend * stock_adjustment
            optimal_price = max(optimal_price, cost_floor)

            # Cap at 130% of market max
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

    print(f"  Generated {len(samples):,} training samples")
    return samples


# ---------------------------------------------------------------------------
# Step 4: Train and save
# ---------------------------------------------------------------------------
def train_and_save(samples):
    """Train RandomForestRegressor and save to disk.

    Args:
        samples: List of training sample dicts.

    Returns:
        The model payload dict.
    """
    print("\n[4/6] Training RandomForestRegressor...")

    # Build feature matrix and label vector
    X = np.array([[s[f] for f in FEATURE_NAMES] for s in samples])
    y = np.array([s["optimal_price"] for s in samples])

    print(f"  Features: {len(FEATURE_NAMES)}")
    print(f"  Samples:  {len(X):,}")

    # Train/test split (80/20)
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=RANDOM_SEED
    )

    # Train the model
    model = RandomForestRegressor(
        n_estimators=200,     # More trees for larger dataset
        max_depth=15,         # Deeper trees for complex patterns
        min_samples_split=5,  # Prevent overfitting on noise
        random_state=RANDOM_SEED,
        n_jobs=-1,
    )
    model.fit(X_train, y_train)

    # Evaluate
    y_pred = model.predict(X_test)
    mae = mean_absolute_error(y_test, y_pred)
    r2 = r2_score(y_test, y_pred)

    print(f"\n  Model Performance:")
    print(f"    MAE:  RM{mae:.4f}")
    print(f"    R2:   {r2:.4f}")

    # Feature importances
    importances = dict(zip(FEATURE_NAMES, model.feature_importances_))
    top_5 = sorted(importances.items(), key=lambda x: -x[1])[:5]
    print(f"\n  Top 5 Feature Importances:")
    for name, imp in top_5:
        print(f"    {name:25s} {imp:.4f}")

    # Save model
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
        "training_date": datetime.now().isoformat(),
        "data_source": f"KPDN PriceCatcher {START_YEAR}-{END_YEAR}",
        "localization": f"{TARGET_STATE}" + (f"/{TARGET_DISTRICT}" if TARGET_DISTRICT else ""),
    }
    joblib.dump(payload, model_path)

    print(f"\n  Saved to: {model_path}")
    print(f"  File size: {os.path.getsize(model_path) / 1024:.1f} KB")

    return payload


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    """Execute the full Big Data training pipeline."""
    print("=" * 60)
    print("ShelfSenseAI - ML Training (KPDN Big Data)")
    print(f"Data range: {START_YEAR}-{START_MONTH:02d} to {END_YEAR}-{END_MONTH:02d}")
    print(f"Localization: {TARGET_STATE}" + (f", {TARGET_DISTRICT}" if TARGET_DISTRICT else ""))
    print("=" * 60)

    # Step 1: Download lookups
    item_df, premise_df = download_lookups()

    # Step 2: Download, merge, filter, and aggregate monthly (memory-efficient)
    agg = download_and_process_monthly(item_df, premise_df)

    if len(agg) < 50:
        print(f"WARNING: Only {len(agg)} item-dates after aggregation. Model quality may suffer.")

    # Step 3: Generate training samples
    rng = random.Random(RANDOM_SEED)
    samples = generate_samples(agg, rng)

    # Step 4: Train and save
    payload = train_and_save(samples)

    print("\n" + "=" * 60)
    print("Training complete. Model ready for inference.")
    print("=" * 60)


if __name__ == "__main__":
    warnings.filterwarnings("ignore")
    main()
