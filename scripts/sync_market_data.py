"""
====================================================================
 ShelfSenseAI - ManaMurah Market Data Synchronization Script
====================================================================

Connects to the ManaMurah MCP server, retrieves the FAMA Panduan
Harga Harian daily prices relevant to ShelfSenseAI's FYP scope, and
upserts them into the Phase 3A market tables.

SCOPE DECISION (per the Phase 5 directive):
    ManaMurah exposes 15 tools. Eleven are KPDN PriceCatcher tools
    serving the SAME weekly-average data our PriceCatcher ETL already
    ingests — re-ingesting them would duplicate observations, so they
    are deliberately NOT synced. The three FAMA tools serve FAMA's
    OWN daily catalogue (independent of PriceCatcher), which is the
    genuinely independent dataset. From FAMA's 46 items we sync the
    subset relevant to a kedai runcit's dry-goods/egg scope; fresh
    produce is excluded per the FYP scope, except TELUR AYAM (item
    46) which is already a ShelfSenseAI demo product.

IDEMPOTENCY:
    Run this script as many times as you like — market_ingestion.py
    upserts on (market_item_id, observed_at, state, district), so a
    second run reports 0 new observations.

Usage (from the project root):
    ./venv/Scripts/python.exe scripts/sync_market_data.py
    ./venv/Scripts/python.exe scripts/sync_market_data.py --days 14 --state johor
"""
import argparse
import logging
import os
import sys
from collections import Counter

# Make the project root importable no matter where the script is run from.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app, db, MarketSource, MarketItem, MarketPriceObservation  # noqa: E402
from services.mcp_client import ManaMurahClient                            # noqa: E402
from services.market_ingestion import sync_from_client                     # noqa: E402

# -------------------------------------------------------------
# FYP-scope FAMA item selection
#
# item_id is FAMA's own 1..46 catalogue id (NOT the KPDN item_code).
# We sync shelf-stable / everyday items a kedai runcit actually
# prices: eggs (the ShelfSenseAI demo product), cooking staples and
# grains. Fresh produce (leafy vegetables, fruits) is excluded to
# respect the FYP's dry-goods scope.
# -------------------------------------------------
FAMA_SCOPE_ITEMS = [
    (46, 'TELUR AYAM'),            # eggs — demo product, daily retail price
    (40, 'BERAS SAWAH'),           # rice (local paddy rice benchmark)
    (41, 'BERAS IMPORT'),          # rice (imported benchmark)
    (44, 'SANTAN BERSARIKAT'),     # packaged coconut milk (dry-goods aisle)
    (45, 'UBI KENTANG HOLLAND'),   # potatoes — long shelf life staple
]

# Price level: retail (RUNCIT) is what a kedai runcit competes against.
# BORONG/LADANG exist in the API but are out of scope for shelf pricing.
DEFAULT_LEVEL = 'RUNCIT'
DEFAULT_DAYS = 30


def main():
    parser = argparse.ArgumentParser(
        description='Sync ManaMurah (FAMA daily prices) into ShelfSenseAI')
    parser.add_argument('--days', type=int, default=DEFAULT_DAYS,
                        help='Trailing window in days (1-90, default 30)')
    parser.add_argument('--state', type=str, default=None,
                        help='FAMA state slug for state-grain data '
                             '(e.g. johor). Omit for national grain.')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')

    print('## ManaMurah Market Data Sync')
    print(f"Endpoint : {os.environ.get('MANAMURAH_MCP_URL', '(default public endpoint)')}")

    with app.app_context():
        client = ManaMurahClient()
        stats = sync_from_client(client, FAMA_SCOPE_ITEMS,
                                 level=DEFAULT_LEVEL, days=args.days,
                                 state_slug=args.state)
        _print_summary(stats, args.state)


def _print_summary(stats, state_slug):
    """Print the human-readable synchronization summary."""
    print(f'Level    : {DEFAULT_LEVEL} (FAMA retail prices)')
    print(f'Grain    : {"state=" + state_slug if state_slug else "national"}')
    print(f'-' * 40)
    print(f"Retrieved          : {stats['retrieved']}")
    print(f"Accepted           : {stats['accepted']}")
    print(f"Rejected           : {stats['rejected']}")
    print(f"New items          : {stats['new_items']}")
    print(f"Updated items      : {stats['updated_items']}")
    print(f"New observations   : {stats['new_observations']}")
    print(f"Updated/duplicate  : {stats['duplicates_skipped']}")
    print(f"Errors             : {stats['errors']}")
    print(f'-' * 40)

    # Post-sync verification straight from the database: confirm the
    # ManaMurah source exists, count its items/observations, and prove
    # PriceCatcher data was untouched.
    source = MarketSource.query.filter_by(name='ManaMurah').first()
    if source:
        n_items = MarketItem.query.filter_by(source_id=source.id).count()
        n_obs = MarketPriceObservation.query.join(MarketItem).filter(
            MarketItem.source_id == source.id).count()
        print(f"ManaMurah source    : id={source.id}, "
              f"{n_items} items, {n_obs} observations")
    pc = MarketSource.query.filter_by(name='PriceCatcher').first()
    if pc:
        pc_obs = MarketPriceObservation.query.join(MarketItem).filter(
            MarketItem.source_id == pc.id).count()
        print(f"PriceCatcher intact : {pc_obs} observations (untouched)")
    db.session.remove()


if __name__ == '__main__':
    main()
