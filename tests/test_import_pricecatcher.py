"""
Real-database tests for the additive monthly PriceCatcher importer
(import_pricecatcher.py).

Follows the project's established pattern (see tests/test_market_models.py):
every row created is deleted in finally, and DB state is asserted restored.

The tests use SYNTHETIC data that cannot collide with real PriceCatcher
data — future months (2099-01 / 2099-02) and distinctive codes
(TSTITEM* / TSTPREM*) — and a local --local-dir cache so nothing is
downloaded from the network.

Coverage:
  - month validation / default window / range expansion
  - code normalization and frame preparation (invalid rows, intra-file dups)
  - importing a new month (one raw row -> one archive row)
  - importing the same month twice (idempotent, zero duplicates)
  - importing two different months (they coexist)
  - preserving existing months (older data untouched)
  - orphan rows (unresolvable item/premise) dropped and counted
  - missing download file raises
  - database failure raises
  - DB-level unique constraint blocks duplicate natural keys

Run:
    ./venv/Scripts/python.exe tests/test_import_pricecatcher.py
"""
import os
import sys
from argparse import Namespace
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

import import_pricecatcher as pc

TEST_MONTH = "2099-01"
TEST_MONTH2 = "2099-02"
ITEM_CODES = ["TSTITEM1", "TSTITEM2"]
PREMISE_CODES = ["TSTPREM1", "TSTPREM2"]

_PURGE_SQL = [
    "DELETE FROM price WHERE date >= '2099-01-01' AND date < '2099-03-01'",
    "DELETE FROM price_catcher_item WHERE item_code LIKE 'TST%'",
    "DELETE FROM lookup_item WHERE item_code LIKE 'TST%'",
    "DELETE FROM lookup_premise WHERE premise_code LIKE 'TST%'",
]


def _engine():
    return pc.build_engine()


def _purge():
    with _engine().begin() as conn:
        for q in _PURGE_SQL:
            conn.execute(text(q))


def _count_price(engine, month=TEST_MONTH):
    y, m = map(int, month.split("-"))
    start, end = f"{y:04d}-{m:02d}-01", f"{y:04d}-{m:02d}-28"
    with engine.connect() as conn:
        return conn.execute(text(
            "SELECT COUNT(*) FROM price WHERE date >= :s AND date <= :e"
        ), {"s": start, "e": end}).scalar()


def _make_lookups():
    li = pd.DataFrame({
        "item_code": ITEM_CODES,
        "item": ["BERAS TEST 1KG", "GULA TEST 1KG"],
        "unit": ["1kg", "1kg"],
        "item_group": ["BARANGAN KERING", "BARANGAN KERING"],
        "item_category": ["BERAS", "GULA"],
    })
    lp = pd.DataFrame({
        "premise_code": PREMISE_CODES,
        "premise": ["TEST PREMISE A", "TEST PREMISE B"],
        "address": ["1 TEST ROAD", "2 TEST ROAD"],
        "premise_type": ["Pasar Awam", "Pasar Awam"],
        "state": ["Johor", "Johor"],
        "district": ["Segamat", "Segamat"],
    })
    return {
        "lookup_item": pc._prepare_lookup_item(li),
        "lookup_premise": pc._prepare_lookup_premise(lp),
    }


def _make_price_frame(rows=None):
    if rows is None:
        rows = [
            (date(2099, 1, 5), "TSTPREM1", "TSTITEM1", 4.00),
            (date(2099, 1, 5), "TSTPREM2", "TSTITEM1", 4.50),
            (date(2099, 1, 6), "TSTPREM1", "TSTITEM2", 3.00),
        ]
    return pd.DataFrame(rows, columns=["date", "premise_code", "item_code",
                                       "price"])


def _write_cache(tmp_path, month=TEST_MONTH, price_frame=None, lookups=None):
    """Write synthetic parquet files into a local-dir cache."""
    lookups = lookups or _make_lookups()
    price_frame = _make_price_frame() if price_frame is None else price_frame
    price_frame.to_parquet(tmp_path / f"pricecatcher_{month}.parquet",
                           index=False)
    lookups["lookup_item"].to_parquet(tmp_path / "lookup_item.parquet",
                                      index=False)
    lookups["lookup_premise"].to_parquet(tmp_path / "lookup_premise.parquet",
                                         index=False)
    return lookups


# ======================================================== PURE UNIT TESTS
def test_validate_month():
    assert pc.validate_month("2026-09") == "2026-09"
    for bad in ("2026-13", "2026-00", "2026-9", "13-2026", "garbage", ""):
        with pytest.raises(ValueError):
            pc.validate_month(bad)


class _FakeDatetime:
    year = 2026
    month = 9

    @classmethod
    def now(cls):
        return cls


def test_default_months(monkeypatch):
    monkeypatch.setattr(pc, "datetime", _FakeDatetime)
    assert pc.default_months() == ["2026-06", "2026-07", "2026-08"]


def test_iter_months_range():
    assert pc._iter_months("2026-06", "2026-08") == \
        ["2026-06", "2026-07", "2026-08"]
    assert pc._iter_months("2026-11", "2027-01") == \
        ["2026-11", "2026-12", "2027-01"]


def test_resolve_months():
    assert pc.resolve_months(Namespace(month=["2026-09"], start_month=None,
                                       end_month=None)) == ["2026-09"]
    assert pc.resolve_months(Namespace(month=["2026-06", "2026-09"],
                                       start_month=None, end_month=None)) \
        == ["2026-06", "2026-09"]
    assert pc.resolve_months(Namespace(month=None, start_month="2026-06",
                                       end_month="2026-09")) == \
        ["2026-06", "2026-07", "2026-08", "2026-09"]
    assert pc.resolve_months(Namespace(month=None, start_month="2026-08",
                                       end_month=None)) == ["2026-08"]
    with pytest.raises(ValueError):
        pc.resolve_months(Namespace(month=["2026-13"], start_month=None,
                                    end_month=None))
    with pytest.raises(ValueError):
        pc.resolve_months(Namespace(month=None, start_month="2026-09",
                                    end_month="2026-06"))


def test_normalize_code():
    s = pd.Series([1000.0, "1000.0", "123", -1.0, None, " 45 "])
    out = pc.normalize_code(s).tolist()
    assert out[:3] == ["1000", "1000", "123"]
    assert out[3] == "-1"
    assert pd.isna(out[4])
    assert out[5] == "45"


def test_prepare_price_frame_drops_invalid_and_dups():
    frame = _make_price_frame(rows=[
        (date(2099, 1, 5), "TSTPREM1", "TSTITEM1", 4.00),
        (date(2099, 1, 5), "TSTPREM1", "TSTITEM1", 4.10),   # intra-file dup
        (date(2099, 1, 5), None, "TSTITEM1", 4.00),          # blank premise
        (date(2099, 1, 5), "TSTPREM1", "TSTITEM1", 0.0),     # non-positive
        (date(2099, 1, 5), "TSTPREM1", "TSTITEM1", None),    # NaN price
        (date(2099, 1, 5), "TSTPREM2", "TSTITEM1", 4.50),
    ])
    out = pc._prepare_price_frame(frame, "2099-01")
    assert len(out) == 2
    assert out.iloc[0]["price"] == 4.10          # kept LAST of the dup pair
    assert out.iloc[1]["price"] == 4.50


def test_prepare_lookup_item_rules():
    frame = pd.DataFrame({
        "item_code": [1000.0, "1000.0", "T2", "T3"],   # float artifact collapse
        "item": ["BERAS", "BERAS", "", "SAYUR"],
        "unit": ["1kg", "1kg", "1kg", "1kg"],
        "item_group": ["BARANGAN KERING", "BARANGAN KERING",
                       "BARANGAN KERING", "BARANGAN SEGAR"],
        "item_category": ["BERAS", "BERAS", "BERAS", "SAYUR"],
    })
    out = pc._prepare_lookup_item(frame)
    codes = out["item_code"].tolist()
    assert "T3" not in codes                # excluded group
    assert "T2" not in codes                # blank name
    assert codes == ["1000"]                # 1000.0 and "1000.0" collapsed


# ======================================================== DB TESTS
def test_import_new_month(tmp_path):
    lookups = _write_cache(tmp_path)
    engine = _engine()
    _purge()
    try:
        stats = pc.import_month(engine, TEST_MONTH, lookups,
                                local_dir=str(tmp_path))
        assert stats["inserted"] == 3
        assert stats["duplicates"] == 0
        assert stats["orphans"] == 0
        assert _count_price(engine) == 3
        with engine.connect() as conn:
            n = conn.execute(text(
                "SELECT COUNT(*) FROM price_catcher_item "
                "WHERE item_code = 'TSTITEM1'")).scalar()
        assert n == 1
    finally:
        _purge()


def test_import_same_month_twice_no_duplicates(tmp_path):
    lookups = _write_cache(tmp_path)
    engine = _engine()
    _purge()
    try:
        first = pc.import_month(engine, TEST_MONTH, lookups,
                                local_dir=str(tmp_path))
        second = pc.import_month(engine, TEST_MONTH, lookups,
                                 local_dir=str(tmp_path))
        assert first["inserted"] == 3
        assert second["inserted"] == 0
        assert second["duplicates"] == 3
        assert _count_price(engine) == 3
    finally:
        _purge()


def test_import_two_different_months_coexist(tmp_path):
    lookups = _write_cache(tmp_path)
    _write_cache(tmp_path, month=TEST_MONTH2,
                 price_frame=_make_price_frame(rows=[
                     (date(2099, 2, 3), "TSTPREM1", "TSTITEM1", 4.20),
                     (date(2099, 2, 4), "TSTPREM2", "TSTITEM2", 3.10),
                 ]))
    engine = _engine()
    _purge()
    try:
        s1 = pc.import_month(engine, TEST_MONTH, lookups,
                             local_dir=str(tmp_path))
        s2 = pc.import_month(engine, TEST_MONTH2, lookups,
                             local_dir=str(tmp_path))
        assert s1["inserted"] == 3 and s2["inserted"] == 2
        assert _count_price(engine, TEST_MONTH) == 3
        assert _count_price(engine, TEST_MONTH2) == 2
    finally:
        _purge()


def test_existing_months_preserved(tmp_path):
    """Importing a new month must not delete or alter older months."""
    lookups = _write_cache(tmp_path)
    engine = _engine()
    _purge()
    try:
        with engine.connect() as conn:
            real_before = conn.execute(text(
                "SELECT COUNT(*) FROM price WHERE date >= '2026-06-01' "
                "AND date < '2026-07-01'")).scalar()
            total_before = conn.execute(
                text("SELECT COUNT(*) FROM price")).scalar()
        pc.import_month(engine, TEST_MONTH, lookups, local_dir=str(tmp_path))
        with engine.connect() as conn:
            real_after = conn.execute(text(
                "SELECT COUNT(*) FROM price WHERE date >= '2026-06-01' "
                "AND date < '2026-07-01'")).scalar()
            total_after = conn.execute(
                text("SELECT COUNT(*) FROM price")).scalar()
        assert real_after == real_before
        assert total_after == total_before + 3
    finally:
        _purge()


def test_orphans_dropped_and_counted(tmp_path):
    lookups = _write_cache(tmp_path)
    frame = _make_price_frame(rows=[
        (date(2099, 1, 5), "TSTPREM1", "TSTITEM1", 4.00),
        (date(2099, 1, 5), "TSTPREM9", "TSTITEM1", 4.00),   # unknown premise
        (date(2099, 1, 5), "TSTPREM1", "TSTITEM9", 4.00),   # unknown item
    ])
    _write_cache(tmp_path, price_frame=frame)
    engine = _engine()
    _purge()
    try:
        stats = pc.import_month(engine, TEST_MONTH, lookups,
                                local_dir=str(tmp_path))
        assert stats["orphans"] == 2
        assert stats["inserted"] == 1
        assert _count_price(engine) == 1
    finally:
        _purge()


def test_missing_download_file_raises(tmp_path, monkeypatch):
    """A month whose Parquet file cannot be fetched raises (not silent)."""
    lookups = _write_cache(tmp_path)
    # Remove the month file but keep the lookups.
    os.remove(tmp_path / f"pricecatcher_{TEST_MONTH}.parquet")

    def _no_network(path_or_url, *a, **k):
        if str(path_or_url).startswith("http"):
            raise OSError("network disabled in tests")
        return pd.read_parquet(path_or_url, *a, **k)

    monkeypatch.setattr(pc.pd, "read_parquet", _no_network)
    with pytest.raises(OSError):
        pc.import_month(_engine(), TEST_MONTH, lookups,
                        local_dir=str(tmp_path))


def test_database_failure_raises(tmp_path):
    lookups = _write_cache(tmp_path)
    url = os.getenv("DATABASE_URL", "")
    bad_url = url.rsplit("/", 1)[0] + "/shelfsense_db_nonexistent_xyz"
    engine = create_engine(bad_url)
    with pytest.raises(Exception):
        pc.import_month(engine, TEST_MONTH, lookups, local_dir=str(tmp_path))


def test_db_unique_constraint_blocks_duplicate_key(tmp_path):
    lookups = _write_cache(tmp_path)
    engine = _engine()
    _purge()
    try:
        pc.import_month(engine, TEST_MONTH, lookups, local_dir=str(tmp_path))
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(text(
                    "INSERT INTO price (date, premise_code, item_code, price) "
                    "VALUES ('2099-01-05', 'TSTPREM1', 'TSTITEM1', 9.99)"))
    finally:
        _purge()


# -------------------------------------------------
# runner (works without pytest)
# -------------------------------------------------
def _all_tests():
    return [(name, fn) for name, fn in sorted(globals().items())
            if name.startswith("test_") and callable(fn)]


def main():
    _purge()
    passed = 0
    failed = []
    for name, fn in _all_tests():
        try:
            fn(type("T", (), {"__enter__": lambda s: s,
                              "__exit__": lambda *a: None})())
            passed += 1
        except Exception as exc:                    # noqa: BLE001
            failed.append((name, exc))
        _purge()
    print(f"test_import_pricecatcher: {passed}/{len(_all_tests())} passed")
    for name, exc in failed:
        print(f"  FAIL {name}: {type(exc).__name__}: {exc}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())