#!/usr/bin/env python
# import_pricecatcher.py
# -------------------------------------------------
# Downloads the three PriceCatcher Parquet files and loads them
# into the MySQL database used by the Flask ShelfSense AI app.
# -------------------------------------------------

import os
import re
import sys
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.types import VARCHAR
from dotenv import load_dotenv

# Windows consoles default to cp1252, which cannot encode the non-ASCII
# characters used in the progress prints ("→", "⚠️", "✅"...). Force UTF-8
# output so the script does not crash on the very first message.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ----------------------------------------------------------------------
# 1️⃣ Load environment variables (so we get DATABASE_URL from .env)
# ----------------------------------------------------------------------
load_dotenv()                     # reads .env into os.environ
db_url = os.getenv("DATABASE_URL")
if not db_url:
    raise RuntimeError("DATABASE_URL not found in .env file")

# ----------------------------------------------------------------------
# 2️⃣ Create a SQLAlchemy engine that talks to the same MySQL instance
# ----------------------------------------------------------------------
# pool_pre_ping: checks the connection is alive before using it, and
#   transparently reconnects if the server already closed it.
# pool_recycle: forces connections to be recycled before MySQL's own
#   `wait_timeout` (default 8h, but many hosts set it much lower) kills them.
engine = create_engine(
    db_url,
    echo=False,
    future=True,
    pool_pre_ping=True,
    pool_recycle=280,
)

# ----------------------------------------------------------------------
# 3️⃣ Helper: fetch the Parquet files from the public URLs (or local cache)
# ----------------------------------------------------------------------
BASE_URL = "https://storage.data.gov.my/pricecatcher/"
FILES = {
    "lookup_item": "lookup_item.parquet",
    "lookup_premise": "lookup_premise.parquet",
    "price": "pricecatcher_2026-08.parquet",
}

def fetch_parquet(name: str) -> pd.DataFrame:
    """Download (or read from cache) a Parquet file and return a DataFrame."""
    url = f"{BASE_URL}{FILES[name]}"
    print(f"Downloading {name} from {url} ...")
    df = pd.read_parquet(url)
    print(f"  → {len(df):,} rows, {df.shape[1]} columns")
    return df

# ----------------------------------------------------------------------
# 4️⃣ Load each table into MySQL
# ----------------------------------------------------------------------
def load_dataframe(df: pd.DataFrame, table_name: str, *, if_exists: str = "replace",
                    chunksize: int = 5000, dtype: dict | None = None):
    """
    Write a DataFrame to MySQL in manageable batches.

    * if_exists='replace' will DROP + CREATE the table each run (good for dev).
      Change to 'append' if you want to keep existing rows and just add new ones.
    * chunksize caps how many rows go into each INSERT batch, so we never
      build a single statement large enough to exceed MySQL's
      max_allowed_packet (which causes "MySQL server has gone away").
    * dtype lets us force specific columns (e.g. FK/PK key columns) to an
      explicit, identical SQL type across tables, which is required for
      MySQL to allow foreign keys between them.
    """
    print(f"\nLoading {table_name} ({len(df):,} rows)…")
    total = len(df)
    for start in range(0, total, chunksize):
        end = min(start + chunksize, total)
        df.iloc[start:end].to_sql(
            name=table_name,
            con=engine,
            if_exists=if_exists if start == 0 else "append",
            index=False,
            method="multi",
            dtype=dtype,
        )
        print(f"  → wrote rows {start:,}-{end:,} of {total:,}")
    print(f"  → Table '{table_name}' created/populated.")

# ----------------------------------------------------------------------
# 5️⃣ Pull the three DataFrames
# ----------------------------------------------------------------------
dfs = {name: fetch_parquet(name) for name in FILES.keys()}

# ----------------------------------------------------------------------
# 5.5️⃣ Normalize key columns, then force an IDENTICAL explicit type everywhere
# ----------------------------------------------------------------------
# Two separate problems tend to bite here, and both showed up in testing:
#
# (a) TYPE MISMATCH ACROSS TABLES (fixed error 150, "FK incorrectly formed"):
#     pandas.to_sql() infers a column type per-table, independently, so
#     item_code in lookup_item and item_code in price could end up as
#     different types/lengths even though the values look the same.
#
# (b) FORMATTING MISMATCH WITHIN THE DATA ITSELF (causes error 1452,
#     "Cannot add or update a child row"): if a code column has any NaNs in
#     one of the parquet files, pandas silently upcasts that whole column to
#     float64. A plain str(value) on a float produces "123.0" instead of
#     "123" — so "123.0" in price never matches "123" in lookup_premise,
#     even though they're logically the same code. This is the most common
#     cause of FK integrity failures when joining separately-generated
#     government open-data files.
#
# normalize_code() strips that float artifact so codes compare cleanly.

def normalize_code(series: pd.Series) -> pd.Series:
    def conv(v):
        if pd.isna(v):
            return None
        if isinstance(v, float):
            return str(int(v)) if v.is_integer() else str(v).strip()
        s = str(v).strip()
        if s == "":
            return None
        # Some source files already store codes as strings that carry a
        # trailing float artifact baked in upstream, e.g. "1000.0" or
        # "-1.0", instead of a genuine Python float. Strip that too, so
        # "1000.0" (string) and "1000.0" (from an actual float) both
        # normalize to "1000" and match cleanly against the other table.
        if re.fullmatch(r"-?\d+\.0", s):
            s = s[:-2]
        return s
    return series.map(conv)

for tbl in ("lookup_item", "price"):
    dfs[tbl]["item_code"] = normalize_code(dfs[tbl]["item_code"])
for tbl in ("lookup_premise", "price"):
    dfs[tbl]["premise_code"] = normalize_code(dfs[tbl]["premise_code"])

# A normalized code of None means the source row had a blank/missing code.
# That can't go into a PRIMARY KEY column (MySQL rejects NULL in a PK), and
# it's not a real, referenceable code anyway — so drop those rows from the
# lookup tables themselves. Any price rows pointing at a blank code will
# then correctly get caught as orphans in the next step.
for key_col, tbl in (("item_code", "lookup_item"), ("premise_code", "lookup_premise")):
    before_n = len(dfs[tbl])
    dfs[tbl] = dfs[tbl].loc[dfs[tbl][key_col].notna()].reset_index(drop=True)
    dropped = before_n - len(dfs[tbl])
    if dropped:
        print(f"⚠️  Dropped {dropped:,} row(s) from {tbl} with a blank/missing {key_col}.")

    # Normalization can also make two previously-distinct-looking rows
    # collide (e.g. a malformed "1000" and "1000.0" both becoming "1000"),
    # which would violate the PRIMARY KEY we're about to add. Keep the
    # first occurrence and report how many duplicates were dropped.
    dup_mask = dfs[tbl][key_col].duplicated(keep="first")
    n_dups = int(dup_mask.sum())
    if n_dups:
        print(f"⚠️  Dropped {n_dups:,} duplicate {key_col} row(s) from {tbl} after normalization.")
        dfs[tbl] = dfs[tbl].loc[~dup_mask].reset_index(drop=True)

# ----------------------------------------------------------------------
# 5.5.5️⃣ Exclude item groups the shop does not sell
# ----------------------------------------------------------------------
# The shop does not stock fresh produce (wet-market style goods) or
# ready-to-cook meals, so those PriceCatcher items are dropped from the
# lookup. This runs BEFORE the orphan filter in 5.6, so any `price` rows
# pointing at excluded items are dropped there too — otherwise they would
# violate the foreign key when the tables are rebuilt.
EXCLUDED_ITEM_GROUPS = {"BARANGAN SEGAR", "MAKANAN SIAP MASAK"}

excluded_mask = dfs["lookup_item"]["item_group"].isin(EXCLUDED_ITEM_GROUPS)
n_excluded = int(excluded_mask.sum())
if n_excluded:
    print(f"ℹ️  Excluding {n_excluded:,} item(s) from groups: "
          f"{', '.join(sorted(EXCLUDED_ITEM_GROUPS))}")
    dfs["lookup_item"] = dfs["lookup_item"].loc[~excluded_mask].reset_index(drop=True)
else:
    print(f"ℹ️  No items matched the excluded groups "
          f"{sorted(EXCLUDED_ITEM_GROUPS)} — nothing to drop.")

# ----------------------------------------------------------------------
# 5.6️⃣ Drop genuine orphan rows in `price` before loading
# ----------------------------------------------------------------------
# Even after normalizing formatting, the price snapshot and the lookup
# snapshots come from separately-generated files and may not be perfectly
# in sync (e.g. a premise closed between snapshots). Rather than let the
# whole FK step blow up on a handful of orphan rows, filter them out and
# report exactly how many/what happened so nothing silently vanishes.
valid_item_codes = set(dfs["lookup_item"]["item_code"])
valid_premise_codes = set(dfs["lookup_premise"]["premise_code"])

before = len(dfs["price"])
orphan_mask = (
    ~dfs["price"]["item_code"].isin(valid_item_codes)
    | ~dfs["price"]["premise_code"].isin(valid_premise_codes)
)
n_orphans = int(orphan_mask.sum())
if n_orphans:
    print(f"⚠️  Dropping {n_orphans:,} of {before:,} price rows with no matching "
          f"item_code/premise_code in the lookup tables.")
    dfs["price"] = dfs["price"].loc[~orphan_mask].reset_index(drop=True)
else:
    print("✅ Every price row has a matching item_code and premise_code.")

item_code_len = max(dfs["lookup_item"]["item_code"].str.len().max(),
                     dfs["price"]["item_code"].str.len().max())
premise_code_len = max(dfs["lookup_premise"]["premise_code"].str.len().max(),
                        dfs["price"]["premise_code"].str.len().max())

# Pad a bit of headroom in case future monthly files have longer codes.
item_code_len = int(item_code_len) + 5
premise_code_len = int(premise_code_len) + 5

DTYPES = {
    "lookup_item": {"item_code": VARCHAR(item_code_len)},
    "lookup_premise": {"premise_code": VARCHAR(premise_code_len)},
    "price": {
        "item_code": VARCHAR(item_code_len),
        "premise_code": VARCHAR(premise_code_len),
    },
}

# ----------------------------------------------------------------------
# 5.7️⃣ Drop any existing tables from a previous run before rebuilding
# ----------------------------------------------------------------------
# to_sql(if_exists="replace") issues DROP TABLE per-table, but MySQL won't
# drop a parent table (lookup_item/lookup_premise) while a child table
# (price) still exists with a foreign key pointing at it. Since this
# script fully rebuilds all three tables every run anyway, drop them
# ourselves first, in child-to-parent order, with FK checks disabled as
# a safety net so drop order/existence never blocks a re-run.
with engine.begin() as conn:
    conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
    # price_catcher_item is a derived table, so drop it first.
    for tbl_name in ("price_catcher_item", "price", "lookup_item", "lookup_premise"):
        conn.execute(text(f"DROP TABLE IF EXISTS `{tbl_name}`"))
    conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))

# ----------------------------------------------------------------------
# 6️⃣ Write them to MySQL
# ----------------------------------------------------------------------
# Smaller lookup tables can go in bigger chunks; the price table (likely the
# largest by far) uses a smaller chunksize to stay well under the packet limit.
CHUNKSIZES = {
    "lookup_item": 5000,
    "lookup_premise": 5000,
    "price": 2000,
}

for tbl_name, df in dfs.items():
    load_dataframe(
        df,
        tbl_name,
        if_exists="replace",
        chunksize=CHUNKSIZES.get(tbl_name, 2000),
        dtype=DTYPES.get(tbl_name),
    )

# ----------------------------------------------------------------------
# 7️⃣ Fix primary keys and foreign keys (raw SQL)
# ----------------------------------------------------------------------
with engine.begin() as conn:   # begin() gives us a transaction that auto‑commits
    # ---- lookup_item -------------------------------------------------
    conn.execute(text(
        """
        ALTER TABLE lookup_item
        ADD PRIMARY KEY (item_code);
        """
    ))
    conn.execute(text(
        """
        CREATE INDEX idx_lookup_item_item
        ON lookup_item (item);
        """
    ))

    # ---- lookup_premise ----------------------------------------------
    conn.execute(text(
        """
        ALTER TABLE lookup_premise
        ADD PRIMARY KEY (premise_code);
        """
    ))

    # ---- price -------------------------------------------------------
    conn.execute(text(
        """
        ALTER TABLE price
        ADD COLUMN price_id INT AUTO_INCREMENT PRIMARY KEY FIRST,
        ADD INDEX idx_price_item_code (item_code),
        ADD INDEX idx_price_premise_code (premise_code);
        """
    ))

    # Foreign keys – MySQL requires the referenced columns to be indexed
    # (they are because they are PKs).
    conn.execute(text(
        """
        ALTER TABLE price
        ADD CONSTRAINT fk_price_item
            FOREIGN KEY (item_code) REFERENCES lookup_item(item_code)
            ON UPDATE CASCADE ON DELETE RESTRICT,
        ADD CONSTRAINT fk_price_premise
            FOREIGN KEY (premise_code) REFERENCES lookup_premise(premise_code)
            ON UPDATE CASCADE ON DELETE RESTRICT;
        """
    ))

# ----------------------------------------------------------------------
# 8️⃣ Build the denormalized PriceCatcherItem table
# ----------------------------------------------------------------------
# A denormalized copy of lookup_item with a surrogate `id` as PK. EVERY
# lookup item is included — whether or not it has rows in the `price` table
# (market data is optional; membership is NOT gated on it). This lets the
# app reference items by a single integer id instead of the string item_code.
with engine.begin() as conn:
    conn.execute(text("DROP TABLE IF EXISTS price_catcher_item"))
    conn.execute(text(f"""
        CREATE TABLE price_catcher_item (
            id            INT AUTO_INCREMENT PRIMARY KEY,
            item_code     VARCHAR({item_code_len}) NOT NULL,
            item          VARCHAR(255) NOT NULL,
            unit          VARCHAR(50),
            item_group    VARCHAR(100),
            item_category VARCHAR(100),
            UNIQUE KEY uq_pc_item_code (item_code)
        )
    """))
    n_items = conn.execute(text("""
        INSERT INTO price_catcher_item (item_code, item, unit, item_group, item_category)
        SELECT item_code, item, unit, item_group, item_category
        FROM lookup_item
        ORDER BY item_code
    """)).rowcount
print(f"✅ price_catcher_item built with {n_items:,} rows (all lookup items).")

print("\n✅ All tables are now loaded, primary keys set, and foreign keys in place.")
print("You can now start/restart your Flask app and the /autocomplete route will work.")