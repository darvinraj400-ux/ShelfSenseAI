#!/usr/bin/env python
# import_pricecatcher.py
# -------------------------------------------------
# Downloads PriceCatcher Parquet files (monthly price snapshots + item/premise
# lookups) and loads them into the MySQL database used by the Flask
# ShelfSense AI app.
#
# ARCHITECTURE (Phase 6 — historical archive):
#
#     PriceCatcher monthly Parquet
#             |
#             v
#     Raw PriceCatcher archive  (lookup_item / lookup_premise / price)
#             |
#             v
#     scripts/etl_pricecatcher.py  (premise-level MarketPriceObservation)
#
# The raw tables are a HISTORICAL ARCHIVE. This script NEVER drops or
# truncates them, and a second import of the same month must not create
# duplicate rows. Each monthly file is deduplicated against the rows
# already stored (natural key: date + premise_code + item_code), so
# months coexist and older months are never deleted.
#
# USAGE (from the project root):
#     python import_pricecatcher.py                        # default: last 3 months
#     python import_pricecatcher.py --month 2026-09        # one month
#     python import_pricecatcher.py --month 2026-06 --month 2026-09
#     python import_pricecatcher.py --start-month 2026-06 --end-month 2026-09
#     python import_pricecatcher.py --month 2026-09 --local-dir ./pc_cache
#
# --local-dir caches downloaded Parquet files so re-runs (and the
# idempotency tests) work offline. Delete cached files to force a
# re-download.
# -------------------------------------------------

import argparse
import os
import re
import sys
from datetime import datetime

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

# Windows consoles default to cp1252, which cannot encode the non-ASCII
# characters used in the progress prints ("→", "⚠️", "✅"...). Force UTF-8
# output so the script does not crash on the very first message.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()                     # reads .env into os.environ

# ----------------------------------------------------------------------
# 1️⃣ Constants
# ----------------------------------------------------------------------
BASE_URL = "https://storage.data.gov.my/pricecatcher/"
FILES = {
    "lookup_item": "lookup_item.parquet",
    "lookup_premise": "lookup_premise.parquet",
}

# Default import window: the most recent N complete calendar months
# (the current month's file is usually not published yet). Running with
# no arguments therefore reproduces the old "last 3 months" behaviour —
# but ADDITIVELY, never by dropping what is already stored.
DEFAULT_WINDOW_MONTHS = 3

# Item groups the shop does not stock (wet-market style fresh produce and
# ready-to-cook meals). These items are excluded from the lookup table and
# any price rows referencing them are treated as orphans (see below).
EXCLUDED_ITEM_GROUPS = {"BARANGAN SEGAR", "MAKANAN SIAP MASAK"}

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

# Chunk size for price INSERTs — caps each statement well below MySQL's
# max_allowed_packet (which causes "MySQL server has gone away").
PRICE_CHUNK = 2000
LOOKUP_CHUNK = 500


# ----------------------------------------------------------------------
# 2️⃣ Engine / CLI helpers
# ----------------------------------------------------------------------
def build_engine(db_url: str | None = None):
    """Create the SQLAlchemy engine from DATABASE_URL (or an override)."""
    db_url = db_url or os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL not found in .env file")
    return create_engine(
        db_url,
        echo=False,
        future=True,
        pool_pre_ping=True,
        pool_recycle=280,
    )


def validate_month(value: str) -> str:
    """Return `value` when it is a valid 'YYYY-MM' month, else raise ValueError."""
    if not isinstance(value, str) or not _MONTH_RE.match(value):
        raise ValueError(f"invalid month {value!r}: expected YYYY-MM (e.g. 2026-09)")
    return value


def default_months(window: int = DEFAULT_WINDOW_MONTHS) -> list[str]:
    """The most recent `window` complete calendar months, oldest first.

    The current month is excluded because its monthly file is usually not
    published yet. On 2026-09-08 this returns ['2026-06', '2026-07',
    '2026-08'] — the same window the old hardcoded importer used.
    """
    year, month = datetime.now().year, datetime.now().month
    months = []
    for _ in range(window):
        month -= 1
        if month == 0:
            month = 12
            year -= 1
        months.append(f"{year:04d}-{month:02d}")
    return months[::-1]


def _iter_months(start: str, end: str) -> list[str]:
    """Inclusive list of 'YYYY-MM' months from start to end."""
    sy, sm = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    months = []
    while (sy, sm) <= (ey, em):
        months.append(f"{sy:04d}-{sm:02d}")
        sm += 1
        if sm == 13:
            sm = 1
            sy += 1
    return months


def resolve_months(args) -> list[str]:
    """Turn parsed CLI args into a validated, ordered list of months."""
    if args.month:
        return [validate_month(m) for m in args.month]
    if args.start_month:
        start = validate_month(args.start_month)
        end = validate_month(args.end_month or args.start_month)
        if start > end:
            raise ValueError(f"--start-month {start} is after --end-month {end}")
        return _iter_months(start, end)
    return default_months()


# ----------------------------------------------------------------------
# 3️⃣ Download helpers (local-dir aware)
# ----------------------------------------------------------------------
def fetch_parquet(name: str, local_dir: str | None = None) -> pd.DataFrame:
    """Download (or read from --local-dir cache) a lookup Parquet file."""
    local_path = os.path.join(local_dir, FILES[name]) if local_dir else None
    if local_path and os.path.exists(local_path):
        print(f"Reading {name} from local cache {local_path} ...")
        return pd.read_parquet(local_path)
    url = f"{BASE_URL}{FILES[name]}"
    print(f"Downloading {name} from {url} ...")
    df = pd.read_parquet(url)
    if local_path:
        os.makedirs(local_dir, exist_ok=True)
        df.to_parquet(local_path, index=False)
    print(f"  → {len(df):,} rows, {df.shape[1]} columns")
    return df


def fetch_month_frame(month: str, local_dir: str | None = None) -> pd.DataFrame:
    """Download (or read from cache) one month's price Parquet file.

    Raises on failure — the caller decides whether a missing month is fatal.
    """
    filename = f"pricecatcher_{month}.parquet"
    local_path = os.path.join(local_dir, filename) if local_dir else None
    if local_path and os.path.exists(local_path):
        print(f"Reading price data for {month} from local cache {local_path} ...")
        return pd.read_parquet(local_path)
    url = f"{BASE_URL}{filename}"
    print(f"Downloading price data for {month} from {url} ...")
    df = pd.read_parquet(url)
    if local_path:
        os.makedirs(local_dir, exist_ok=True)
        df.to_parquet(local_path, index=False)
    print(f"  → {month}: {len(df):,} rows")
    return df


# ----------------------------------------------------------------------
# 4️⃣ Code normalization (unchanged from the original importer)
# ----------------------------------------------------------------------
# TYPE MISMATCH ACROSS TABLES (fixed error 150, "FK incorrectly formed"):
# pandas.to_sql() infers a column type per-table, independently, so
# item_code in lookup_item and item_code in price could end up as
# different types/lengths even though the values look the same.
#
# FORMATTING MISMATCH WITHIN THE DATA ITSELF (causes error 1452,
# "Cannot add or update a child row"): if a code column has any NaNs in
# one of the parquet files, pandas silently upcasts that whole column to
# float64. A plain str(value) on a float produces "123.0" instead of
# "123" — so "123.0" in price never matches "123" in lookup_premise,
# even though they're logically the same code.
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


def _prepare_lookup_item(frame: pd.DataFrame) -> pd.DataFrame:
    """Clean + dedupe a lookup_item frame before it is upserted.

    Applies the same rules the original importer applied before loading:
      * blank item names are dropped (they cannot be useful catalogue rows),
      * item groups the shop does not sell are excluded,
      * codes are normalized and deduplicated (a malformed "1000" and
        "1000.0" both become "1000" and would collide on the PK).
    """
    df = frame.copy()
    df["item_code"] = normalize_code(df["item_code"])
    df = df.loc[df["item_code"].notna()].reset_index(drop=True)

    excluded = df["item_group"].isin(EXCLUDED_ITEM_GROUPS)
    if excluded.any():
        print(f"ℹ️  Excluding {int(excluded.sum()):,} item(s) from groups: "
              f"{', '.join(sorted(EXCLUDED_ITEM_GROUPS))}")
        df = df.loc[~excluded].reset_index(drop=True)

    df = df.loc[df["item"].astype(str).str.strip() != ""].reset_index(drop=True)

    dup_mask = df["item_code"].duplicated(keep="last")
    if dup_mask.any():
        print(f"⚠️  Dropped {int(dup_mask.sum()):,} duplicate item_code row(s) "
              f"after normalization (kept last).")
        df = df.loc[~dup_mask].reset_index(drop=True)
    return df


def _prepare_lookup_premise(frame: pd.DataFrame) -> pd.DataFrame:
    """Clean + dedupe a lookup_premise frame before it is upserted."""
    df = frame.copy()
    df["premise_code"] = normalize_code(df["premise_code"])
    df = df.loc[df["premise_code"].notna()].reset_index(drop=True)
    dup_mask = df["premise_code"].duplicated(keep="last")
    if dup_mask.any():
        print(f"⚠️  Dropped {int(dup_mask.sum()):,} duplicate premise_code "
              f"row(s) after normalization (kept last).")
        df = df.loc[~dup_mask].reset_index(drop=True)
    return df


def _prepare_price_frame(frame: pd.DataFrame, month: str) -> pd.DataFrame:
    """Normalize + validate one month's price frame.

    Returns a frame with only rows that are worth attempting to insert:
    non-blank keys, a real date, a positive price, and no intra-file
    duplicate natural keys (kept last). The caller then filters against
    what is already stored in the archive.
    """
    df = frame.copy()
    df["item_code"] = normalize_code(df["item_code"])
    df["premise_code"] = normalize_code(df["premise_code"])
    # Normalize dates to plain datetime.date so they compare/hash exactly
    # like the values fetch_existing_keys() returns from MySQL.
    df["date"] = pd.to_datetime(df["date"]).dt.date

    before = len(df)
    valid = (
        df["date"].notna()
        & df["item_code"].notna()
        & df["premise_code"].notna()
        & df["price"].notna()
        & (df["price"] > 0)
    )
    df = df.loc[valid].reset_index(drop=True)
    n_invalid = before - len(df)

    before = len(df)
    df = (df.drop_duplicates(subset=["date", "premise_code", "item_code"],
                             keep="last")
            .reset_index(drop=True))
    n_dup_in_file = before - len(df)
    if n_invalid:
        print(f"  ⚠️  {month}: dropped {n_invalid:,} row(s) with blank keys, "
              f"missing dates or non-positive prices")
    if n_dup_in_file:
        print(f"  ⚠️  {month}: dropped {n_dup_in_file:,} intra-file duplicate "
              f"natural key(s) (kept last)")
    return df


# ----------------------------------------------------------------------
# 5️⃣ Schema / load helpers (NON-destructive)
# ----------------------------------------------------------------------
def ensure_schema(engine, item_code_len: int, premise_code_len: int):
    """Create any missing raw tables (never drops existing data).

    Idempotent: on a database that already has the tables this is a
    no-op. Also adds the natural-key unique index to `price` if it is
    missing (fresh `flask db upgrade` installs get it from the migration
    instead — CREATE ... IF NOT EXISTS makes both orders safe).
    """
    with engine.begin() as conn:
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS lookup_item (
                item_code     VARCHAR({item_code_len}) NOT NULL,
                item          TEXT,
                unit          TEXT,
                item_group    TEXT,
                item_category TEXT,
                PRIMARY KEY (item_code),
                KEY idx_lookup_item_item (item(191))
            )
        """))
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS lookup_premise (
                premise_code VARCHAR({premise_code_len}) NOT NULL,
                premise      TEXT,
                address      TEXT,
                premise_type TEXT,
                state        TEXT,
                district     TEXT,
                PRIMARY KEY (premise_code)
            )
        """))
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS price (
                price_id     INT AUTO_INCREMENT PRIMARY KEY,
                date         DATE,
                premise_code VARCHAR({premise_code_len}),
                item_code    VARCHAR({item_code_len}),
                price        DOUBLE,
                UNIQUE KEY uq_price_natural (date, premise_code, item_code),
                KEY idx_price_item_code (item_code),
                KEY idx_price_premise_code (premise_code),
                CONSTRAINT fk_price_item
                    FOREIGN KEY (item_code) REFERENCES lookup_item(item_code)
                    ON UPDATE CASCADE ON DELETE RESTRICT,
                CONSTRAINT fk_price_premise
                    FOREIGN KEY (premise_code) REFERENCES lookup_premise(premise_code)
                    ON UPDATE CASCADE ON DELETE RESTRICT
            )
        """))
        conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS price_catcher_item (
                id            INT AUTO_INCREMENT PRIMARY KEY,
                item_code     VARCHAR({item_code_len}) NOT NULL,
                item          VARCHAR(255) NOT NULL,
                unit          VARCHAR(50),
                item_group    VARCHAR(100),
                item_category VARCHAR(100),
                UNIQUE KEY uq_pc_item_code (item_code)
            )
        """))
    # Live databases created by the original importer predate the natural-key
    # unique index. Add it when missing (MariaDB supports IF NOT EXISTS).
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_price_natural "
            "ON price (date, premise_code, item_code)"
        ))


def upsert_lookup(engine, table: str, frame: pd.DataFrame, columns: list[str],
                  key_col: str) -> int:
    """Upsert a lookup frame (INSERT ... ON DUPLICATE KEY UPDATE).

    Existing rows are refreshed in place, new rows are appended — the
    lookup tables only ever grow, so historical prices keep their FK
    targets even when premises close or items are reclassified.
    """
    df = frame.copy()
    df = df.drop_duplicates(subset=[key_col], keep="last")
    # object-dtype first: where() cannot place None into an object column
    # that already holds NaN (it keeps NaN, which pymysql refuses to bind).
    df = df.astype(object).where(pd.notna(df), None)
    params = [dict(zip(columns, row))
              for row in df[columns].itertuples(index=False, name=None)]
    if not params:
        return 0
    update_cols = [c for c in columns if c != key_col]
    set_clause = ", ".join(f"`{c}`=VALUES(`{c}`)" for c in update_cols)
    sql = (f"INSERT INTO `{table}` ({', '.join('`' + c + '`' for c in columns)}) "
           f"VALUES ({', '.join(':' + c for c in columns)}) "
           f"ON DUPLICATE KEY UPDATE {set_clause}")
    with engine.begin() as conn:
        for i in range(0, len(params), LOOKUP_CHUNK):
            conn.execute(text(sql), params[i:i + LOOKUP_CHUNK])
    return len(params)


def fetch_valid_codes(engine) -> tuple[set, set]:
    """All item/premise codes currently in the archive lookups (DB side)."""
    with engine.connect() as conn:
        items = {r[0] for r in conn.execute(text("SELECT item_code FROM lookup_item"))}
        premises = {r[0] for r in conn.execute(text("SELECT premise_code FROM lookup_premise"))}
    return items, premises


def fetch_existing_keys(engine, month: str) -> set[tuple]:
    """All (date, premise_code, item_code) keys already stored for a month.

    Used to skip rows that a previous import of the same month already
    wrote — this is what makes re-importing idempotent. The query is an
    index range scan on uq_price_natural's (date) prefix.
    """
    y, m = map(int, month.split("-"))
    start = f"{y:04d}-{m:02d}-01"
    end_y, end_m = (y, m + 1) if m < 12 else (y + 1, 1)
    end = f"{end_y:04d}-{end_m:02d}-01"
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT date, premise_code, item_code FROM price "
            "WHERE date >= :s AND date < :e"
        ), {"s": start, "e": end}).fetchall()
    return {(r[0], r[1], r[2]) for r in rows}


def insert_price_rows(engine, frame: pd.DataFrame):
    """Append a prepared price frame in chunks (never replaces)."""
    total = len(frame)
    for start in range(0, total, PRICE_CHUNK):
        end = min(start + PRICE_CHUNK, total)
        frame.iloc[start:end].to_sql(
            name="price",
            con=engine,
            if_exists="append",
            index=False,
            method="multi",
        )
    print(f"  → inserted {total:,} new row(s) into price")


def rebuild_price_catcher_item(engine) -> int:
    """Refresh the denormalized price_catcher_item table (upsert, stable ids).

    Driven by the current lookup_item contents; item_code is unique so
    repeated runs update in place instead of duplicating rows.
    """
    with engine.begin() as conn:
        n = conn.execute(text("""
            INSERT INTO price_catcher_item
                (item_code, item, unit, item_group, item_category)
            SELECT item_code, item, unit, item_group, item_category
            FROM lookup_item
            ON DUPLICATE KEY UPDATE
                item=VALUES(item), unit=VALUES(unit),
                item_group=VALUES(item_group), item_category=VALUES(item_category)
        """)).rowcount
    return n


# ----------------------------------------------------------------------
# 6️⃣ Per-month import
# ----------------------------------------------------------------------
def import_month(engine, month: str, lookups: dict, local_dir: str | None = None,
                 dry_run: bool = False) -> dict:
    """Import one month's price data into the archive. Returns a stats dict.

    Order of operations (each is safe to re-run):
      1. fetch/normalize the month's price frame,
      2. ensure the raw schema exists (no-op when present),
      3. upsert the lookups (additive, refresh-in-place),
      4. drop rows whose item/premise no longer resolve (orphans),
      5. drop rows already stored for that month (idempotency),
      6. append the genuinely new rows,
      7. refresh price_catcher_item.

    `dry_run` performs every check but writes nothing — useful for
    previewing how many rows a month would add.
    """
    stats = {"month": month, "downloaded": 0, "invalid": 0, "orphans": 0,
             "duplicates": 0, "inserted": 0, "lookup_refreshed": 0}

    frame = fetch_month_frame(month, local_dir)
    stats["downloaded"] = len(frame)
    frame = _prepare_price_frame(frame, month)
    stats["invalid"] = stats["downloaded"] - len(frame)

    # Column lengths for a fresh schema (headroom for future longer codes).
    item_len = max(frame["item_code"].str.len().max(),
                   lookups["lookup_item"]["item_code"].str.len().max()) + 5
    premise_len = max(frame["premise_code"].str.len().max(),
                      lookups["lookup_premise"]["premise_code"].str.len().max()) + 5

    if not dry_run:
        ensure_schema(engine, int(item_len), int(premise_len))

        # 3. Upsert lookups (additive — never deletes rows).
        n_item = upsert_lookup(engine, "lookup_item",
                               lookups["lookup_item"],
                               ["item_code", "item", "unit", "item_group",
                                "item_category"], "item_code")
        n_premise = upsert_lookup(engine, "lookup_premise",
                                  lookups["lookup_premise"],
                                  ["premise_code", "premise", "address",
                                   "premise_type", "state", "district"],
                                  "premise_code")
        stats["lookup_refreshed"] = n_item + n_premise

        # 4. Orphan rows: price rows whose item/premise resolve nowhere in
        # the archive lookups (snapshots can drift). They cannot satisfy a
        # foreign key, so they are dropped and counted — never silently.
        valid_items, valid_premises = fetch_valid_codes(engine)
        before = len(frame)
        frame = frame.loc[
            frame["item_code"].isin(valid_items)
            & frame["premise_code"].isin(valid_premises)
        ].reset_index(drop=True)
        stats["orphans"] = before - len(frame)

        # 5. Idempotency: skip rows already stored for this month.
        existing = fetch_existing_keys(engine, month)
        if existing:
            keys = frame[["date", "premise_code", "item_code"]].itertuples(
                index=False, name=None)
            dupe_mask = pd.Series([k in existing for k in keys],
                                  index=frame.index)
            stats["duplicates"] = int(dupe_mask.sum())
            frame = frame.loc[~dupe_mask].reset_index(drop=True)

        # 6. Append the genuinely new rows.
        if len(frame):
            insert_price_rows(engine, frame)
        stats["inserted"] = len(frame)

        # 7. Refresh the denormalized autocomplete table.
        rebuild_price_catcher_item(engine)
    return stats


# ----------------------------------------------------------------------
# 7️⃣ CLI entry point
# ----------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Import PriceCatcher monthly data into the historical "
                    "archive (additive — never drops existing months).")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--month", action="append", metavar="YYYY-MM",
                       help="import one month (repeatable for several months)")
    group.add_argument("--start-month", "--start", metavar="YYYY-MM",
                       help="first month of an inclusive range")
    parser.add_argument("--end-month", "--end", metavar="YYYY-MM",
                        help="last month of an inclusive range "
                             "(defaults to --start-month)")
    parser.add_argument("--local-dir", metavar="DIR",
                        help="cache/download directory for Parquet files "
                             "(re-runs read from here; delete files to "
                             "re-download)")
    parser.add_argument("--db-url", metavar="URL",
                        help="override DATABASE_URL from .env")
    parser.add_argument("--dry-run", action="store_true",
                        help="download + validate + report, but write nothing")
    args = parser.parse_args(argv)

    try:
        months = resolve_months(args)
    except ValueError as exc:
        parser.error(str(exc))

    engine = build_engine(args.db_url)

    print("## PriceCatcher historical import")
    print(f"Months : {', '.join(months)}"
          + ("  [DRY RUN — nothing will be written]" if args.dry_run else ""))
    print("=" * 60)

    # Lookups are downloaded once and shared across all months.
    try:
        lookups = {
            "lookup_item": _prepare_lookup_item(fetch_parquet("lookup_item",
                                                              args.local_dir)),
            "lookup_premise": _prepare_lookup_premise(
                fetch_parquet("lookup_premise", args.local_dir)),
        }
    except Exception as exc:                            # noqa: BLE001
        print(f"❌ Failed to fetch lookup tables: {exc}")
        return 1

    totals = {"downloaded": 0, "invalid": 0, "orphans": 0,
              "duplicates": 0, "inserted": 0}
    ok_months = 0
    for month in months:
        print(f"\n--- {month} ---")
        try:
            stats = import_month(engine, month, lookups,
                                 local_dir=args.local_dir,
                                 dry_run=args.dry_run)
        except Exception as exc:                        # noqa: BLE001
            print(f"  ❌ {month} failed: {exc}")
            continue
        ok_months += 1
        for k in totals:
            totals[k] += stats[k]
        print(f"  → {month}: {stats['downloaded']:,} downloaded, "
              f"{stats['inserted']:,} inserted, "
              f"{stats['duplicates']:,} already stored (skipped), "
              f"{stats['orphans']:,} orphans dropped")

    print("\n" + "=" * 60)
    print("SUMMARY")
    print(f"  Months attempted      : {len(months)} (succeeded: {ok_months})")
    print(f"  Rows downloaded       : {totals['downloaded']:,}")
    print(f"  Rows inserted (new)   : {totals['inserted']:,}")
    print(f"  Rows already stored   : {totals['duplicates']:,} (skipped)")
    print(f"  Orphans dropped       : {totals['orphans']:,}")
    if ok_months == 0:
        print("❌ No month could be imported.")
        return 1
    print("✅ Archive is up to date for the requested months.")
    return 0


if __name__ == "__main__":
    sys.exit(main())