"""
====================================================================
 ShelfSenseAI - ETL: PriceCatcher -> Market Data (premise-level)
====================================================================
Loads the raw PriceCatcher archive into the Phase 3A market schema:

    price (raw archive)        ->  MarketSource + MarketItem
    price x lookup_premise     ->  MarketPriceObservation
                                   (ONE row per raw price record —
                                    premise-level, NOT averaged)

ARCHITECTURE (Phase 6):

    PriceCatcher monthly Parquet
            |
            v
    Raw PriceCatcher archive (lookup_item / lookup_premise / price)
            |
            v
    THIS ETL: normalization (unit prices) + premise-level observations
            |
            v
    MarketPriceObservation  ->  Market Analysis  ->  Pricing Engine

WHY PREMISE-LEVEL (not AVG):
    The previous ETL grouped by (item_code, date, state, district) and
    stored AVG(price), which threw away ~83% of the raw market
    information (1.84M raw rows -> ~311K aggregated rows). This version
    stores one MarketPriceObservation per raw price record, so market
    analysis can answer "which store is cheapest?", "what is the
    spread?", "how many stores sell this item?" from real observations.

WHAT A RERUN DOES (safety contract):
    1. MarketSource "PriceCatcher" is created once and never deleted,
       so its id is stable.
    2. MarketItems are UPSERTED by (source_id, external_id) — ids are
       stable, so ProductMarketMatch rows linking shop products to
       PriceCatcher items SURVIVE reruns.
    3. Only PriceCatcher MarketPriceObservations are deleted and
       rebuilt deterministically from the raw archive. Other sources
       (ManaMurah/FAMA, manual) are never touched.
    4. The DB unique index (market_item_id, premise_code, observed_at)
       (migration e5d4c3b2a1f0) makes duplicate observations impossible
       even if a bug ever tried to insert one.
    Re-running the ETL therefore produces the SAME final state.

PERFORMANCE:
    Observations are rebuilt server-side with INSERT ... SELECT (no
    per-row ORM objects), chunked by month so each transaction stays
    small. ~2M rows take roughly a minute on a local machine.

Package-size parsing (the dirty `unit` column, unchanged):
    clean "N unit"      -> normalize_package_size()   (360 rows)
    "N X Ng" multipack  -> total weight (5 X 79g = 395 g)
    "M54".."M74" sizes  -> diaper pack count (unit = 'unit')
    count-nouns         -> '100 beg'=100, '1 batang'=1, '6sheets'=6,
                           '10 PAD'=10, '1 biji'=1, '1 unit'=1
    bare 'paket'        -> count regexed out of the ITEM NAME
                           ("PANADOL ACTIFAST 10S" -> 10 units)
    'senaskah'          -> 1 unit (one magazine issue)
    '+- 500g'           -> '+-' stripped, nominal weight used
    unparseable         -> FALLBACK (1, 'unit'), logged as an issue

Run from the project root:
    ./venv/Scripts/python.exe scripts/etl_pricecatcher.py
====================================================================
"""
import os
import re
import sys
import time
from collections import Counter

# Make the project root importable no matter where the script is run from.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text                              # noqa: E402

from app import (app, db, MarketSource, MarketItem,       # noqa: E402
                 MarketPriceObservation, ProductMarketMatch)
from utils.normalization import (clean_text,              # noqa: E402
                                 normalize_package_size)

SOURCE_NAME = 'PriceCatcher'
SOURCE_TYPE = 'government'


# -------------------------------------------------
# Layered package-size parser (unchanged from Phase 3A)
# -------------------------------------------------
# Count-nouns that carry no measurement dimension - the NUMBER in the
# unit string is the package count (100 beg = 100 tea bags, ...).
_COUNT_NOUNS = {'beg', 'bags', 'bag', 'batang', 'biji', 'unit', 'units',
                'pcs', 'pc', 'piece', 'pieces', 'sheets', 'pad', 'pads',
                'paket', 'pack', 'packs', 'roll', 'rolls'}
_PAKET_NOUNS = {'paket', 'pack', 'packs'}


def _extract_count_from_name(name):
    """Find a package count embedded in an item NAME (used when the unit
    column only says 'paket'). Patterns seen in the data:
        '50S X 3'      -> 150   (multiply)
        '1 X 10'S'     -> 10
        '10 PADS'      -> 10
        '6 LOZENGES'   -> 6
        '10S'          -> 10
    Returns an int, or None when no count is found."""
    n = name or ''
    m = re.search(r'(\d+)\s*[xX]\s*(\d+)', n)
    if m:
        return int(m.group(1)) * int(m.group(2))
    m = re.search(r'(\d+)\s*PADS?', n, re.IGNORECASE)
    if m:
        return int(m.group(1))
    m = re.search(r'(\d+)\s*LOZENGES?', n, re.IGNORECASE)
    if m:
        return int(m.group(1))
    m = re.search(r'(\d+)\s*S\b', n)
    if m:
        return int(m.group(1))
    return None


def parse_package(unit, item_name):
    """Parse the legacy `unit` column into (quantity, base_unit).

    Returns None when parsing genuinely fails (caller falls back to
    (1, 'unit')). Never raises - every branch is defensive."""
    u = (unit or '').strip()
    if not u or u.lower() == 'none':
        return None
    # Approximate weights: '+- 500g' / '+-350g' -> strip the prefix.
    u = re.sub(r'^[+\-]+\s*', '', u)

    # Multipack: '5 X 79g', '30x33g', '2 X 10G', '25 x 18g' -> total weight.
    m = re.match(r'^([0-9.]+)\s*[xX]\s*([0-9.]+)\s*([a-zA-Z]+)$', u)
    if m:
        try:
            total = float(m.group(1)) * float(m.group(2))
            return normalize_package_size(total, m.group(3))
        except (ValueError, TypeError):
            return None

    # M-sizes: 'M54'..'M74' (diaper pack sizes) -> count per pack.
    m = re.match(r'^M(\d+)$', u)
    if m:
        return (int(m.group(1)), 'unit')

    # 'N unit-ish': '250 g', '850g', '1.5 liter', '100 beg', '10 PAD',
    # '1 batang', '6sheets', '1 unit', '1 biji'.
    m = re.match(r'^([0-9.]+)\s*([a-zA-Z]+)$', u)
    if m:
        try:
            q = float(m.group(1))
        except (ValueError, TypeError):
            return None
        noun = m.group(2).lower()
        if noun in _COUNT_NOUNS:
            return (q, 'unit')
        return normalize_package_size(q, noun)

    # Bare count-nouns with no leading number.
    low = u.lower()
    if low in _PAKET_NOUNS:
        count = _extract_count_from_name(item_name)
        return (count, 'unit') if count else None
    if low == 'senaskah':
        return (1, 'unit')
    return None


# -------------------------------------------------
# MarketItem upsert (stable ids)
# -------------------------------------------------
def _get_or_create_source():
    """Return (and activate) the PriceCatcher MarketSource.

    The source id is STABLE across reruns — the old ETL deleted and
    recreated it, which cascaded into deleting every MarketItem and
    ProductMarketMatch. Never delete the source again."""
    source = MarketSource.query.filter_by(name=SOURCE_NAME).first()
    if source is None:
        source = MarketSource(name=SOURCE_NAME, source_type=SOURCE_TYPE,
                              is_active=True)
        db.session.add(source)
        db.session.flush()
        print(f'Created MarketSource: {source.name} (id={source.id})')
    else:
        source.is_active = True
        print(f'Using existing MarketSource: {source.name} (id={source.id})')
    db.session.commit()
    return source


def _upsert_market_items(source):
    """Upsert one MarketItem per price_catcher_item row.

    Returns (by_code, stats, issues) where by_code maps item_code ->
    (MarketItem, base_qty). Existing items are UPDATED in place so their
    ids (and every ProductMarketMatch pointing at them) survive reruns.
    """
    catalog = db.session.execute(text(
        'SELECT item_code, item, unit, item_category '
        'FROM price_catcher_item ORDER BY item_code'
    )).fetchall()

    stats = Counter()
    issues = []
    by_code = {}
    for item_code, item, unit, category in catalog:
        # Skip the known junk row: item_code='-1' with empty fields.
        if not item or not str(item).strip():
            stats['skipped_empty'] += 1
            issues.append((item_code, repr(item), unit,
                           'skipped: empty item name'))
            continue
        pkg = parse_package(unit, item)
        if pkg is None:
            pkg = (1, 'unit')
            stats['fallback_1unit'] += 1
            issues.append((item_code, item, unit, 'fallback 1/unit'))
        else:
            stats['parsed'] += 1
        qty, pkg_unit = pkg

        mi = MarketItem.query.filter_by(source_id=source.id,
                                        external_id=item_code).first()
        if mi is None:
            mi = MarketItem(
                source_id=source.id,
                external_id=item_code,
                raw_title=item,
                normalized_title=clean_text(item),
                brand=None,                       # deferred to matching phase
                category=category or None,
                package_quantity=qty,
                package_unit=pkg_unit,
            )
            db.session.add(mi)
            stats['items_created'] += 1
        else:
            mi.raw_title = item
            mi.normalized_title = clean_text(item)
            mi.category = category or None
            mi.package_quantity = qty
            mi.package_unit = pkg_unit
            stats['items_updated'] += 1
        db.session.flush()

        # Base quantity in BASE units (kg/l/unit) — the same conversion
        # calculate_unit_price() applies, used server-side per observation.
        base_qty, _ = normalize_package_size(float(qty), pkg_unit)
        if base_qty <= 0:
            base_qty = 1.0
            stats['clamped_qty'] += 1
            issues.append((item_code, item, unit,
                           'base_qty<=0 clamped to 1.0'))
        by_code[item_code] = (mi, float(base_qty))
        stats['items'] += 1
    db.session.commit()
    return by_code, stats, issues


# -------------------------------------------------
# Premise-level observation rebuild (server-side, bulk)
# -------------------------------------------------
def _rebuild_observations(source, by_code):
    """Rebuild THIS source's observations from the raw archive.

    Deletes only PriceCatcher observations, then re-inserts one
    MarketPriceObservation per raw price record (item, date, premise),
    with state/district resolved from lookup_premise and the unit price
    normalized per observation (RM/base unit).

    Chunked by month AND committed per month: each month is an
    independent, resumable transaction, so an interrupted run can simply
    be re-run (the next run deletes and rebuilds deterministically).
    Returns (n_deleted, n_inserted)."""
    start = time.time()

    # 1. Delete only this source's observations (never ManaMurah/FAMA).
    with db.engine.begin() as conn:
        n_deleted = conn.execute(text(
            'DELETE FROM market_price_observation WHERE market_item_id IN '
            '(SELECT id FROM market_item WHERE source_id = :sid)'
        ), {'sid': source.id}).rowcount
    print(f'Deleted {n_deleted:,} previous PriceCatcher observations.')

    # 2. Item -> base_qty lookup used to normalize unit prices.
    #    A real (non-temporary) helper table so it survives across the
    #    per-month transactions. Dropped at the end of the run.
    with db.engine.begin() as conn:
        conn.execute(text('DROP TABLE IF EXISTS etl_item_qty'))
        conn.execute(text(
            'CREATE TABLE etl_item_qty ('
            'external_id VARCHAR(20) PRIMARY KEY, base_qty DECIMAL(12,6) '
            'NOT NULL)'))
        params = [{'e': code, 'q': qty}
                  for code, (_mi, qty) in by_code.items()]
        for i in range(0, len(params), 500):
            conn.execute(text(
                'INSERT INTO etl_item_qty (external_id, base_qty) '
                'VALUES (:e, :q)'), params[i:i + 500])

    # 3. Insert premise-level observations, month by month. Each month is
    #    its own transaction (fresh connection), so an interrupted run is
    #    simply re-run — the rebuild is deterministic.
    with db.engine.connect() as conn:
        months = [r[0] for r in conn.execute(text(
            "SELECT DISTINCT DATE_FORMAT(date, '%Y-%m') FROM price "
            "ORDER BY 1")).fetchall()]
    n_inserted = 0
    for month in months:
        y, m = int(month[:4]), int(month[5:7])
        start_d, end_d = (f'{month}-01',
                          f'{y:04d}-{m + 1:02d}-01' if m < 12
                          else f'{y + 1:04d}-01-01')
        month_start = time.time()
        with db.engine.begin() as conn:
            n = conn.execute(text("""
                INSERT INTO market_price_observation
                    (market_item_id, premise_code, regular_price,
                     promo_price, is_on_promo, effective_price,
                     normalized_unit_price, state, district, observed_at)
                SELECT mi.id, p.premise_code, p.price, NULL, 0, p.price,
                       ROUND(p.price / t.base_qty, 4),
                       lp.state, lp.district, p.date
                FROM price p
                JOIN lookup_premise lp ON p.premise_code = lp.premise_code
                JOIN market_item mi
                     ON mi.source_id = :sid AND mi.external_id = p.item_code
                JOIN etl_item_qty t ON t.external_id = p.item_code
                WHERE p.date >= :s AND p.date < :e
                  AND lp.state IS NOT NULL AND lp.state != ''
                  AND p.price > 0
                """), {'sid': source.id, 's': start_d,
                       'e': end_d}).rowcount
        n_inserted += n
        print(f'  {month}: {n:,} premise-level observations '
              f'({time.time() - month_start:.1f}s)', flush=True)
    with db.engine.begin() as conn:
        conn.execute(text('DROP TABLE IF EXISTS etl_item_qty'))

    elapsed = time.time() - start
    print(f'Rebuilt observations: {n_inserted:,} inserted '
          f'({elapsed:.1f}s)')
    return n_deleted, n_inserted


# -------------------------------------------------
# Main ETL
# -------------------------------------------------
def main():
    with app.app_context():
        started = time.time()
        print('ETL: PriceCatcher -> premise-level market observations')
        print('=' * 60)

        # --- 1. MarketSource (stable id) ------------------------------
        source = _get_or_create_source()

        # --- 2. MarketItem (upsert, stable ids) ------------------------
        by_code, stats, issues = _upsert_market_items(source)
        print(f'MarketItems: {stats["items"]} '
              f'({stats["items_created"]} created, '
              f'{stats["items_updated"]} refreshed; '
              f'{stats["parsed"]} parsed, '
              f'{stats["fallback_1unit"]} fell back to 1/unit, '
              f'{stats["skipped_empty"]} skipped empty)')

        # --- 3. Observations (premise-level rebuild) -------------------
        n_deleted, n_obs = _rebuild_observations(source, by_code)

        # --- 4. Verify other sources untouched + matches preserved -----
        n_matches = ProductMarketMatch.query.join(MarketItem).filter(
            MarketItem.source_id == source.id).count()
        other = db.session.execute(text(
            'SELECT ms.name, COUNT(mpo.id) '
            'FROM market_source ms '
            'JOIN market_item mi ON mi.source_id = ms.id '
            'LEFT JOIN market_price_observation mpo '
            '       ON mpo.market_item_id = mi.id '
            'WHERE ms.name != :n '
            'GROUP BY ms.name ORDER BY ms.name'
        ), {'n': SOURCE_NAME}).fetchall()

        print('-' * 60)
        print('SUMMARY')
        print(f'  Observations rebuilt : {n_obs:,} '
              f'(deleted {n_deleted:,}, then re-inserted)')
        print(f'  ProductMarketMatch   : {n_matches:,} preserved '
              f'(stable MarketItem ids)')
        for name, count in other:
            print(f'  {name:<22}: {count:,} observations (untouched)')
        print(f'  Total time           : {time.time() - started:.1f}s')
        if issues:
            print(f'  ISSUES ({len(issues)}):')
            for code, name, unit, why in issues:
                print(f'    - {code}: {name[:50]!r} unit={unit!r} -> {why}')
        db.session.remove()


if __name__ == '__main__':
    main()