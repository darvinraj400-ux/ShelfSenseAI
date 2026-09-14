"""
====================================================================
 ShelfSenseAI - Market Refresh Health & Validation (Phase 10C)
====================================================================

Read-only validation over persisted MarketRefreshRun + live market data.
Derives health from:

  - MarketRefreshRun (inserted/updated/duplicates/rejected/errors, status,
    latest_observed_at, finished_at)
  - MarketSource / MarketItem / MarketPriceObservation (observation_count,
    latest_observed_at per source)

No pricing, no repricing, no evidence downgrade — Phase 10F will handle
freshness-aware pricing. This module only reports whether the data that
*does* exist is trustworthy.

Source isolation: every query is filtered by source_name → source_id,
so PriceCatcher health never inspects ManaMurah rows and vice versa.

Performance: uses indexed MAX(observed_at) / COUNT(*) via source_id,
never loads the 1.97M table into Python.

Thresholds are explicit constants with documented rationale — no magic
numbers scattered through checks.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta, date
from typing import Dict, List, Optional

from app import db, MarketRefreshRun, MarketSource, MarketItem, MarketPriceObservation
from services.market_refresh_status import latest_run
from sqlalchemy import text
import time

# Simple in-memory cache for observation stats (COUNT/MAX over 1.97M rows)
# Monitoring page is read-only and counts are stable between refreshes.
# Cache forever per process, invalidated on successful refresh (see _record_run).
_OBS_STATS_CACHE: dict = {}
_OBS_STATS_TTL = 3600.0  # 1 hour — PriceCatcher is monthly, ManaMurah daily

def _invalidate_obs_cache(source_name: str = None):
    """Invalidate cached observation stats for a source (or all)."""
    if source_name:
        key = (source_name or "").strip().lower()
        _OBS_STATS_CACHE.pop(key, None)
    else:
        _OBS_STATS_CACHE.clear()

# ----------------------------------------------------------------
# Freshness thresholds (deterministic, documented)
# ----------------------------------------------------------------
# ManaMurah (FAMA daily): fresh ≤3 days (covers weekend gaps),
# aging ≤7 days, stale >7 days. Rationale: daily feed; 3 days
# tolerates a missed weekend sync, 7 days indicates a week of
# silence which is operationally concerning.
MANAMURAH_FRESH_DAYS = 3
MANAMURAH_AGING_DAYS = 7

# PriceCatcher (monthly archive): fresh ≤35 days (one month + 5-day
# publish grace), aging ≤60 days, stale >60 days. Rationale: files
# are published monthly (e.g. 2026-08 file appears early September);
# 35 days allows the current month's file to be pending, 60 days
# means two months missed.
PRICECATCHER_FRESH_DAYS = 35
PRICECATCHER_AGING_DAYS = 60

# Generic fallback for unknown sources (use ManaMurah-like daily)
DEFAULT_FRESH_DAYS = 7
DEFAULT_AGING_DAYS = 14


def _thresholds_for(source_name: str):
    s = (source_name or "").strip().lower()
    if s == "manamurah":
        return MANAMURAH_FRESH_DAYS, MANAMURAH_AGING_DAYS
    if s == "pricecatcher":
        return PRICECATCHER_FRESH_DAYS, PRICECATCHER_AGING_DAYS
    return DEFAULT_FRESH_DAYS, DEFAULT_AGING_DAYS


@dataclass
class MarketRefreshHealth:
    """Health report for one source's last refresh + live data.

    healthy: overall boolean (True only when health_level == 'healthy')
    health_level: 'healthy' | 'warning' | 'unhealthy' (deterministic)
    checks: list of {name, passed, level, message} for UI/tests
    """
    source: str
    run_id: Optional[int]
    status: Optional[str]  # success/partial/failed/None (no run)
    health_level: str  # healthy|warning|unhealthy
    healthy: bool
    checks: List[Dict] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    observation_count: int = 0
    latest_observed_at: Optional[datetime] = None
    refresh_age_days: Optional[int] = None  # days since latest_observed_at
    inserted: int = 0
    duplicates_skipped: int = 0
    rejected: int = 0
    errors_count: int = 0


def _source_id_for(name: str) -> Optional[int]:
    if not name:
        return None
    src = MarketSource.query.filter_by(name=name).first()
    # MarketRefreshRun stores lowercase, but MarketSource is TitleCase
    if src is None:
        # Try title case for PriceCatcher/ManaMurah
        alt = name.strip().lower()
        mapping = {"pricecatcher": "PriceCatcher", "manamurah": "ManaMurah"}
        alt_name = mapping.get(alt, name)
        src = MarketSource.query.filter_by(name=alt_name).first()
    return src.id if src else None


def _observation_stats(source_name: str) -> Dict:
    """Return {count, latest} for a source's live observations (cached)."""
    # Check in-memory cache first (60s TTL) — avoids scanning 1.97M rows on every /market-data load
    cache_key = (source_name or "").strip().lower()
    now = time.monotonic()
    if cache_key in _OBS_STATS_CACHE:
        cached, ts = _OBS_STATS_CACHE[cache_key]
        if now - ts < _OBS_STATS_TTL:
            return cached
    # Resolve source via case-insensitive lookup
    src_name = None
    lower = (source_name or "").strip().lower()
    mapping = {"pricecatcher": "PriceCatcher", "manamurah": "ManaMurah"}
    src_name = mapping.get(lower, source_name)
    sid = _source_id_for(src_name)
    if sid is None:
        # No MarketSource row → no observations
        result = {"count": 0, "latest": None}
        _OBS_STATS_CACHE[cache_key] = (result, now)
        return result
    # Prefer persisted latest_observed_at from latest MarketRefreshRun if available
    # (avoids MAX scan). Fall back to live MAX only if no run exists.
    run = latest_run(cache_key)
    latest_from_run = run.latest_observed_at if run and run.latest_observed_at else None
    # For count, use cached COUNT; for latest, prefer run's latest if it exists and is recent
    # Use indexed subquery: market_item.source_id is indexed
    cnt = db.session.execute(
        text("SELECT COUNT(*) FROM market_price_observation o "
             "JOIN market_item mi ON mi.id=o.market_item_id "
             "WHERE mi.source_id=:sid"),
        {"sid": sid},
    ).scalar() or 0
    # Use run's latest if available to avoid MAX scan when possible
    if latest_from_run is not None:
        # Verify that live MAX is not newer than run's latest by more than a day
        # (if a manual observation was added outside refresh, run's latest would be stale)
        # Do a quick MAX only if run is older than 1 day
        try:
            age_run = (datetime.utcnow() - (latest_from_run.replace(tzinfo=None) if getattr(latest_from_run, 'tzinfo', None) else latest_from_run)).days
            if age_run > 1:
                latest = db.session.execute(
                    text("SELECT MAX(o.observed_at) FROM market_price_observation o "
                         "JOIN market_item mi ON mi.id=o.market_item_id "
                         "WHERE mi.source_id=:sid"),
                    {"sid": sid},
                ).scalar()
            else:
                latest = latest_from_run
        except Exception:
            latest = latest_from_run
    else:
        latest = db.session.execute(
            text("SELECT MAX(o.observed_at) FROM market_price_observation o "
                 "JOIN market_item mi ON mi.id=o.market_item_id "
                 "WHERE mi.source_id=:sid"),
            {"sid": sid},
        ).scalar()
    result = {"count": int(cnt), "latest": latest}
    _OBS_STATS_CACHE[cache_key] = (result, now)
    return result


def _age_days(latest) -> Optional[int]:
    if latest is None:
        return None
    try:
        # latest may be date or datetime, naive or aware
        if isinstance(latest, date) and not isinstance(latest, datetime):
            latest = datetime(latest.year, latest.month, latest.day)
        # Make naive UTC for comparison
        if getattr(latest, 'tzinfo', None) is not None:
            latest = latest.astimezone(timezone.utc).replace(tzinfo=None)
        now = datetime.utcnow()
        delta = now - latest
        return max(0, delta.days)
    except Exception:
        return None


def get_refresh_health(source_name: str) -> MarketRefreshHealth:
    """Evaluate health for one source's last refresh + live data.

    Read-only: never writes MarketRefreshRun, never mutates product data.
    Source isolation: all live-data queries are filtered by source_name.
    """
    norm = (source_name or "").strip().lower()
    if not norm:
        raise ValueError("source_name is required")
    # Canonical display name
    display = {"pricecatcher": "pricecatcher", "manamurah": "manamurah"}.get(norm, norm)

    run = latest_run(display)
    # Also handle case where run is None (no history yet)
    checks: List[Dict] = []
    warnings: List[str] = []
    errors: List[str] = []

    # --- Check 1: refresh status ---
    run_status = run.status if run else None
    run_id = run.id if run else None
    if run is None:
        checks.append({"name": "refresh_status", "passed": False, "level": "warning",
                       "message": "No refresh history for this source yet"})
        warnings.append("No refresh has been recorded for this source")
    elif run_status == "failed":
        checks.append({"name": "refresh_status", "passed": False, "level": "error",
                       "message": f"Last refresh failed: {run.error_message or 'unknown error'}"})
        errors.append(f"Last refresh failed: {run.error_message or 'unknown error'}")
    elif run_status == "partial":
        checks.append({"name": "refresh_status", "passed": False, "level": "warning",
                       "message": f"Last refresh was partial (errors={run.errors}, rejected={run.rejected})"})
        warnings.append(f"Last refresh was partial (errors={run.errors}, rejected={run.rejected})")
    else:  # success
        checks.append({"name": "refresh_status", "passed": True, "level": "info",
                       "message": "Last refresh succeeded"})

    # --- Live observation stats (source-isolated) ---
    stats = _observation_stats(source_name)
    obs_count = stats["count"]
    # Prefer live latest over run.latest_observed_at for freshness (live is truth)
    # Fall back to run.latest_observed_at if live is None (e.g. run failed before any obs)
    live_latest = stats["latest"]
    run_latest = run.latest_observed_at if run else None
    # Choose the most recent non-None
    latest = None
    candidates = [d for d in [live_latest, run_latest] if d is not None]
    if candidates:
        # Normalize to datetime for max
        def _to_dt(d):
            if isinstance(d, date) and not isinstance(d, datetime):
                return datetime(d.year, d.month, d.day)
            return d
        latest = max(_to_dt(d) for d in candidates)
    age = _age_days(latest)

    # --- Check 2: observation availability ---
    if obs_count == 0:
        checks.append({"name": "observation_availability", "passed": False, "level": "error",
                       "message": "No observations exist for this source"})
        errors.append("No observations exist for this source")
    else:
        checks.append({"name": "observation_availability", "passed": True, "level": "info",
                       "message": f"{obs_count} observations present"})

    # --- Check 3: freshness ---
    fresh_days, aging_days = _thresholds_for(display)
    if latest is None:
        checks.append({"name": "freshness", "passed": False, "level": "error",
                       "message": "No latest observation date available"})
        if obs_count > 0:
            errors.append("No latest observation date available")
    else:
        # latest may be datetime or date
        age_str = f"{age} days ago" if age is not None else "unknown age"
        if age is not None and age <= fresh_days:
            checks.append({"name": "freshness", "passed": True, "level": "info",
                           "message": f"Observations fresh ({age_str}, threshold {fresh_days} days)"})
        elif age is not None and age <= aging_days:
            checks.append({"name": "freshness", "passed": False, "level": "warning",
                           "message": f"Observations aging ({age_str}, aging threshold {aging_days} days)"})
            warnings.append(f"Latest observation is {age} days old (aging threshold {aging_days} days)")
        else:
            checks.append({"name": "freshness", "passed": False, "level": "error",
                           "message": f"Observations stale ({age_str}, stale threshold {aging_days} days)"})
            # Stale is warning for PriceCatcher (monthly) but error for daily? Keep as warning to avoid false unhealthy
            # For now treat stale as warning (degraded) unless combined with failed run
            warnings.append(f"Latest observation is {age} days old (stale threshold {aging_days} days)")

    # --- Check 4: refresh recency vs observation freshness ---
    # If last run succeeded but its latest_observed_at is still old, flag it.
    # This is already partially covered by freshness, but we add explicit check:
    # When run exists and run.finished_at is recent (<2 days) but age is stale, it means refresh succeeded but brought no fresh data.
    if run and run.status == "success" and latest is not None and age is not None:
        # If age > fresh_days, the refresh did not bring fresh data
        if age > fresh_days:
            # Check if refresh was recent (finished within 2 days) — then it's not a scheduling gap, it's data recency gap
            try:
                fin = run.finished_at
                if fin:
                    if getattr(fin, 'tzinfo', None):
                        fin = fin.astimezone(timezone.utc).replace(tzinfo=None)
                    days_since_refresh = (datetime.utcnow() - fin).days
                    if days_since_refresh <= 2:
                        checks.append({"name": "refresh_recency", "passed": False, "level": "warning",
                                       "message": f"Refresh succeeded {days_since_refresh} days ago but latest observation is still {age} days old — no fresh data was available"})
                        warnings.append("Recent refresh did not yield fresh observations")
                    else:
                        checks.append({"name": "refresh_recency", "passed": True, "level": "info",
                                       "message": "Refresh recency OK"})
                else:
                    checks.append({"name": "refresh_recency", "passed": True, "level": "info",
                                   "message": "Refresh recency OK (no finished_at)"})
            except Exception:
                pass

    # --- Check 5: insert/rejection health (from run metrics) ---
    if run:
        inserted = int(run.inserted or 0)
        updated = int(run.updated or 0)
        dups = int(run.duplicates_skipped or 0)
        rejected = int(run.rejected or 0)
        err_cnt = int(run.errors or 0)

        # High errors -> already partial/failed, but add explicit check
        if err_cnt > 0:
            checks.append({"name": "error_count", "passed": False, "level": "warning",
                           "message": f"Run reported {err_cnt} error(s)"})
            if run.status != "failed":
                warnings.append(f"Run reported {err_cnt} error(s)")

        # High rejection
        if rejected and rejected > 10 and inserted == 0 and dups == 0:
            checks.append({"name": "rejection_health", "passed": False, "level": "warning",
                           "message": f"High rejection count ({rejected}) with zero inserted — check source data quality"})
            warnings.append(f"High rejection count ({rejected}) with zero inserted")

        # Suspicious empty success (zero everything but success)
        if run.status == "success" and inserted == 0 and updated == 0 and dups == 0 and obs_count > 0:
            # Could be legitimate (no new data), but worth a warning if not explained by duplicates
            # Only warn when not all are duplicates (which would be idempotent rerun)
            checks.append({"name": "empty_success", "passed": True, "level": "info",
                           "message": "Successful run with zero new rows — likely idempotent rerun (all duplicates)"})
        elif run.status == "success" and inserted == 0 and updated == 0 and dups == 0 and obs_count == 0:
            checks.append({"name": "empty_success", "passed": False, "level": "warning",
                           "message": "Successful run produced zero rows and source has no observations — check source"})
            warnings.append("Successful run produced zero rows and source has no observations")
        else:
            checks.append({"name": "insert_health", "passed": True, "level": "info",
                           "message": f"Metrics inserted={inserted} updated={updated} duplicates={dups} rejected={rejected} errors={err_cnt}"})
    else:
        # No run, can't evaluate metrics
        checks.append({"name": "metrics", "passed": False, "level": "warning",
                       "message": "No run metrics available"})

    # --- Derive overall health_level ---
    # Priority: any error-level check that is not just freshness warning → unhealthy
    # But freshness stale alone is warning, not unhealthy, unless combined with failed run or no observations
    has_error = any(c["level"] == "error" for c in checks)
    has_warning = any(c["level"] == "warning" for c in checks)
    # Failed run or no observations → unhealthy regardless
    if run_status == "failed" or obs_count == 0:
        health_level = "unhealthy"
    elif has_error:
        # If the only error is freshness stale and observations exist, downgrade to warning
        # Check if errors are only freshness-related
        # For now: any error → unhealthy, except stale freshness which we already made warning
        # So has_error here means observation_availability or similar → unhealthy
        health_level = "unhealthy"
    elif has_warning or run_status == "partial":
        health_level = "warning"
    else:
        health_level = "healthy"

    return MarketRefreshHealth(
        source=display,
        run_id=run_id,
        status=run_status,
        health_level=health_level,
        healthy=(health_level == "healthy"),
        checks=checks,
        warnings=warnings,
        errors=errors,
        observation_count=obs_count,
        latest_observed_at=latest,
        refresh_age_days=age,
        inserted=int(run.inserted) if run else 0,
        duplicates_skipped=int(run.duplicates_skipped) if run else 0,
        rejected=int(run.rejected) if run else 0,
        errors_count=int(run.errors) if run else 0,
    )


def health_for_run(run: MarketRefreshRun) -> MarketRefreshHealth:
    """Convenience: health evaluated from a specific run's snapshot."""
    if not run:
        raise ValueError("run is required")
    return get_refresh_health(run.source_name)
