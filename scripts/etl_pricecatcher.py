"""
====================================================================
 ShelfSenseAI - ETL: PriceCatcher -> Phase 3A Market Data
====================================================================
 One-time (but idempotent) loader that moves the LEGACY KPDN
 PriceCatcher data into the Phase 3A market schema:

     price_catcher_item (406 rows)  ->  MarketSource + MarketItem
     price (268,489 rows)           ->  MarketPriceObservation
                                        (rolled up: one row per
                                         item + date, AVG across
                                         premises)

 The legacy `price_catcher_item` / `price` / `lookup_*` tables are
 NEVER touched - they stay as a safe backup.

 Idempotency: if a MarketSource named "PriceCatcher" already exists,
 every MarketItem / MarketPriceObservation / ProductMarketMatch that
 references it is deleted first, then the load re-runs. Safe to
 execute many times.

 Package-size parsing (the dirty `unit` column):
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
from collections import Counter
from datetime import datetime

# Make the project root importable no matter where the script is run from.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text                              # noqa: E402

from app import (app, db, MarketSource, MarketItem,       # noqa: E402
                 MarketPriceObservation, ProductMarketMatch)
from utils.normalization import (clean_text,              # noqa: E402
                                 normalize_package_size,
                                 calculate_unit_price)

SOURCE_NAME = 'PriceCatcher'
SOURCE_TYPE = 'government'

# -------------------------------------------------
# Layered package-size parser
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
# Main ETL
# -------------------------------------------------
def main():
    with app.app_context():
        print(f'ETL: PriceCatcher -> Phase 3A market schema')
        print(f'=' * 60)

        # --- 1. Idempotent purge of a previous run -------------------
        source = MarketSource.query.filter_by(name=SOURCE_NAME).first()
        if source:
            # Fetch ids via SQL (NOT source.items) so the ORM never loads
            # the collection - bulk-deleting rows while the identity map
            # still holds them would make delete(source) try to NULL the
            # children's source_id afterwards (StaleDataError).
            item_ids = [r[0] for r in db.session.execute(
                text('SELECT id FROM market_item WHERE source_id = :sid'),
                {'sid': source.id}).fetchall()]
            if item_ids:
                # Defensive: matches reference market items; drop them first.
                n_match = ProductMarketMatch.query.filter(
                    ProductMarketMatch.market_item_id.in_(item_ids)
                ).delete(synchronize_session=False)
                n_obs = MarketPriceObservation.query.filter(
                    MarketPriceObservation.market_item_id.in_(item_ids)
                ).delete(synchronize_session=False)
                n_item = MarketItem.query.filter(
                    MarketItem.source_id == source.id
                ).delete(synchronize_session=False)
                print(f'Purged previous run: {n_item} items, '
                      f'{n_obs} observations, {n_match} matches')
            # Drop all ORM identity-map state before deleting the source.
            db.session.expire_all()
            db.session.delete(source)
            db.session.commit()

        # --- 2. MarketSource -----------------------------------------
        source = MarketSource(name=SOURCE_NAME, source_type=SOURCE_TYPE,
                              is_active=True)
        db.session.add(source)
        db.session.flush()
        print(f'Created MarketSource: {source.name} '
              f'(id={source.id}, type={source.source_type})')

        # --- 3. MarketItem -------------------------------------------
        catalog = db.session.execute(text(
            'SELECT item_code, item, unit, item_category '
            'FROM price_catcher_item ORDER BY item_code'
        )).fetchall()

        stats = Counter()
        issues = []
        item_by_code = {}
        for item_code, item, unit, category in catalog:
            # Skip the known junk row: item_code='-1' with empty fields.
            if not item or not item.strip():
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
            db.session.flush()
            item_by_code[item_code] = (mi, qty, pkg_unit)
            stats['items'] += 1
        db.session.commit()
        print(f'MarketItems loaded: {stats["items"]} '
              f'({stats["parsed"]} parsed, '
              f'{stats["fallback_1unit"]} fell back to 1/unit, '
              f'{stats["skipped_empty"]} skipped empty)')

        # --- 4. MarketPriceObservation --------------------------------
        # One row per (item, date): AVG(price) across premises.
        rows = db.session.execute(text(
            'SELECT p.item_code, p.date, AVG(p.price) AS avg_price '
            'FROM price p '
            'GROUP BY p.item_code, p.date '
            'ORDER BY p.item_code, p.date'
        )).fetchall()

        n_obs = 0
        for item_code, obs_date, avg_price in rows:
            entry = item_by_code.get(item_code)
            if entry is None:
                continue                      # price row with no catalog item
            mi, qty, pkg_unit = entry
            reg = round(float(avg_price), 2)
            unit_price = calculate_unit_price(reg, qty, pkg_unit)
            db.session.add(MarketPriceObservation(
                market_item_id=mi.id,
                regular_price=reg,
                promo_price=None,
                is_on_promo=False,
                effective_price=reg,          # no promo in legacy data
                normalized_unit_price=unit_price,
                observed_at=datetime(obs_date.year, obs_date.month,
                                     obs_date.day),
            ))
            n_obs += 1
        db.session.commit()
        print(f'MarketPriceObservations created: {n_obs} '
              f'(one per item+date, AVG across premises)')

        # --- 5. Report -------------------------------------------------
        print(f'-' * 60)
        print(f'SUMMARY')
        print(f'  MarketSource        : 1 ({SOURCE_NAME})')
        print(f'  MarketItems         : {stats["items"]}')
        print(f'  Observations        : {n_obs}')
        print(f'  Parsed normally     : {stats["parsed"]}')
        print(f'  Fallback 1/unit     : {stats["fallback_1unit"]}')
        print(f'  Skipped (empty row) : {stats["skipped_empty"]}')
        if issues:
            print(f'  ISSUES ({len(issues)}):')
            for code, name, unit, why in issues:
                print(f'    - {code}: {name[:50]!r} unit={unit!r} -> {why}')
        db.session.remove()


if __name__ == '__main__':
    main()
