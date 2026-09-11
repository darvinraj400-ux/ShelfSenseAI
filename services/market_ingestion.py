"""
====================================================================
 ShelfSenseAI - Source-Agnostic Market Data Ingestion Layer
====================================================================

Validates, normalizes, and upserts external market data into the
Phase 3A market schema. This module is the SINGLE ingestion path for
every non-PriceCatcher source; the legacy PriceCatcher ETL
(scripts/etl_pricecatcher.py) keeps its own delete-and-reinsert
strategy because it rebuilds a full snapshot, whereas this layer
performs incremental, idempotent UPSERTS.

DATA FLOW (Phase 5):
    services/mcp_client.py   (protocol layer, plain dicts)
          |
          v
    services/market_ingestion.py   (validation + upsert, THIS FILE)
          |
          v
    MarketSource -> MarketItem -> MarketPriceObservation
          |
          v
    services/market_analysis.py (unchanged — reads ALL active sources)

KEY DESIGN DECISIONS:
  1. UPSERT, NOT DELETE-AND-REINSERT. Running a sync twice must not
     double the data. MarketItem identity is (source_id, external_id);
     MarketPriceObservation identity is (market_item_id, observed_at,
     state, district). Existing rows are updated in place; only
     genuinely new rows are inserted.
  2. VALIDATION BEFORE PERSISTENCE. Invalid records (non-numeric or
     non-positive prices, unparseable dates, missing titles) are
     counted and logged, never silently inserted.
  3. NORMALIZATION REUSE. utils/normalization.py remains the single
     authority for text cleaning and unit-price math.
  4. SAFE BATCHING. Each item's observations commit as one batch; a
     failure rolls back only that item, not the whole run.
"""
import logging
from datetime import datetime, timezone

from app import db, MarketSource, MarketItem, MarketPriceObservation
from utils.normalization import (clean_text, normalize_package_size,
                                 calculate_unit_price)

log = logging.getLogger(__name__)

# The ManaMurah MarketSource is created lazily on first sync with this
# canonical name. source_type='online_retailer' is the closest existing
# enum value for a third-party aggregator API (the enum also allows
# 'government' for PriceCatcher and 'manual' for hand-entered rows).
MANAMURAH_SOURCE_NAME = 'ManaMurah'
MANAMURAH_SOURCE_TYPE = 'online_retailer'


def get_or_create_source(name=MANAMURAH_SOURCE_NAME,
                         source_type=MANAMURAH_SOURCE_TYPE):
    """Return the MarketSource row for `name`, creating it on first use.

    Idempotent: repeated syncs return the same row instead of
    duplicating the source. Returns the MarketSource instance."""
    source = MarketSource.query.filter_by(name=name).first()
    if source is None:
        source = MarketSource(name=name, source_type=source_type,
                              is_active=True)
        db.session.add(source)
        db.session.flush()          # assign source.id before children use it
        log.info('Created MarketSource %s (id=%s, type=%s)',
                 name, source.id, source_type)
    return source


def validate_record(record):
    """Validate one normalized market record (dict from mcp_client).

    Returns (ok, reason). A record is valid when it has a non-empty
    raw_title, a non-empty external_id, and at least one valid
    observation. Observation-level problems are filtered later by
    validate_observation so one bad point does not kill the record."""
    if not isinstance(record, dict):
        return False, 'record is not a dict'
    if not (record.get('raw_title') or '').strip():
        return False, 'missing raw_title'
    if not (record.get('external_id') or '').strip():
        return False, 'missing external_id'
    observations = record.get('observations')
    if not isinstance(observations, list) or not observations:
        return False, 'no observations'
    return True, None


def validate_observation(obs):
    """Validate one observation dict {date, price, state?, district?}.

    Returns (ok, reason). Enforces: real date object, numeric price
    strictly greater than zero. These mirror the mcp_client checks and
    form the second (database-side) line of defence — the ingestion
    layer must not trust its caller."""
    if not isinstance(obs, dict):
        return False, 'observation is not a dict'
    d = obs.get('date')
    if not isinstance(d, datetime):
        # Accept plain dates by widening them to midnight datetimes.
        from datetime import date as _date
        if isinstance(d, _date) and not isinstance(d, datetime):
            obs = dict(obs, date=datetime(d.year, d.month, d.day))
        else:
            return False, 'missing or invalid date'
    price = obs.get('price')
    if not isinstance(price, (int, float)) or isinstance(price, bool):
        return False, 'price is not numeric'
    if price <= 0:
        return False, 'price is not positive'
    return True, None


def upsert_market_item(record, source):
    """Upsert one MarketItem for (source, external_id).

    Existing items are updated in place (title/category/package info
    may have changed upstream); new items are created. Returns
    (market_item, created_bool)."""
    existing = MarketItem.query.filter_by(
        source_id=source.id,
        external_id=record['external_id']).first()
    if existing:
        # Refresh mutable fields so upstream renames are reflected.
        existing.raw_title = record['raw_title']
        existing.normalized_title = clean_text(record['raw_title'])
        existing.category = record.get('category')
        qty, unit = _normalize_package(record)
        existing.package_quantity = qty
        existing.package_unit = unit
        return existing, False

    qty, unit = _normalize_package(record)
    item = MarketItem(
        source_id=source.id,
        external_id=record['external_id'],
        raw_title=record['raw_title'],
        normalized_title=clean_text(record['raw_title']),
        brand=record.get('brand'),
        category=record.get('category'),
        package_quantity=qty,
        package_unit=unit,
    )
    db.session.add(item)
    db.session.flush()              # assign item.id before observations
    return item, True


def _normalize_package(record):
    """Normalize a record's package size through the shared utility.

    Falls back to (1, 'unit') when the source provides no usable size,
    mirroring the PriceCatcher ETL fallback convention."""
    try:
        return normalize_package_size(float(record.get('package_quantity', 1)),
                                      record.get('package_unit') or 'unit')
    except (ValueError, TypeError):
        return (1.0, 'unit')


def upsert_observation(item, obs, defaults=None):
    """Upsert one MarketPriceObservation with full idempotency.

    Uniqueness key: (market_item_id, observed_at, state, district).
    Existing rows get their price fields refreshed; new rows are
    created. Returns (observation, created_bool)."""
    defaults = defaults or {}
    observed_at = obs['date']
    state = (obs.get('state') or defaults.get('state'))
    district = (obs.get('district') or defaults.get('district'))

    existing = MarketPriceObservation.query.filter_by(
        market_item_id=item.id,
        observed_at=observed_at,
        state=state,
        district=district).first()
    if existing:
        # Refresh the price values (an upstream correction on a later
        # sync should win over the stale row).
        existing.regular_price = obs['price']
        existing.is_on_promo = False
        existing.effective_price = obs['price']
        existing.normalized_unit_price = _unit_price(item, obs['price'])
        return existing, False

    unit_price = _unit_price(item, obs['price'])
    observation = MarketPriceObservation(
        market_item_id=item.id,
        regular_price=obs['price'],
        promo_price=None,
        is_on_promo=False,
        effective_price=obs['price'],       # derived in __init__ anyway
        normalized_unit_price=unit_price,
        state=state,
        district=district,
        observed_at=observed_at,
    )
    db.session.add(observation)
    return observation, True


def _unit_price(item, price):
    """Compute the RM-per-base-unit price using the shared utility."""
    try:
        return calculate_unit_price(price,
                                    float(item.package_quantity),
                                    item.package_unit)
    except (ValueError, TypeError, ZeroDivisionError):
        return price


def ingest_records(records, source_name=MANAMURAH_SOURCE_NAME,
                   source_type=MANAMURAH_SOURCE_TYPE, commit=True):
    """Ingest a list of market records into the Phase 3A tables.

    Per-item batching: each record's changes commit independently so a
    failure on one item rolls back only that item.

    Returns a Counter summary:
        retrieved, accepted, rejected, new_items, updated_items,
        new_observations, updated_observations, duplicates_skipped,
        errors
    """
    from collections import Counter
    stats = Counter()

    source = get_or_create_source(source_name, source_type)

    for record in records:
        stats['retrieved'] += 1
        ok, reason = validate_record(record)
        if not ok:
            stats['rejected'] += 1
            log.warning('Rejected record %r: %s',
                        record.get('external_id') if isinstance(record, dict)
                        else record, reason)
            continue
        stats['accepted'] += 1

        try:
            # Pre-filter observations BEFORE creating the MarketItem so a
            # record whose every observation is invalid never leaves an
            # empty, useless MarketItem row behind.
            defaults = {'state': record.get('observation_state'),
                        'district': record.get('observation_district')}
            valid_obs = []
            for obs in record.get('observations', []):
                ok, reason = validate_observation(obs)
                if not ok:
                    stats['rejected'] += 1
                    log.warning('Rejected observation for %s: %s',
                                record['external_id'], reason)
                else:
                    valid_obs.append(obs)
            if not valid_obs:
                # Every observation failed validation: reject the whole
                # record instead of persisting an observation-less item.
                stats['accepted'] -= 1
                stats['rejected'] += 1
                log.warning('Rejected record %s: no valid observations',
                            record['external_id'])
                continue

            item, created = upsert_market_item(record, source)
            stats['new_items' if created else 'updated_items'] += 1

            for obs in valid_obs:
                _, obs_created = upsert_observation(item, obs, defaults)
                if obs_created:
                    stats['new_observations'] += 1
                else:
                    # Row already existed: either identical (a plain
                    # duplicate) or refreshed in place.
                    stats['duplicates_skipped'] += 1

            if commit:
                db.session.commit()
        except Exception as exc:
            # Roll back only this item's batch; the run continues.
            db.session.rollback()
            stats['errors'] += 1
            log.error('Ingestion error for %s: %s',
                      record.get('external_id'), exc)

    if not commit:
        db.session.commit()
    return stats


def sync_from_client(client, items, level='RUNCIT', days=30,
                     state_slug=None, source_name=MANAMURAH_SOURCE_NAME):
    """One-shot convenience: fetch records from an MCP client and
    ingest them. Returns the stats Counter from ingest_records.

    Kept separate from ingest_records so the fetch (network) and the
    persist (database) halves stay independently testable."""
    records = client.fetch_fama_records(items, level=level, days=days,
                                        state_slug=state_slug)
    return ingest_records(records, source_name=source_name)
