"""
====================================================================
 ShelfSenseAI - Market Data Refresh Service (Phase 10A)
====================================================================

Orchestration layer that REUSES existing ingestion/ETL logic without
duplicating it.

    PriceCatcher ──▶ import_pricecatcher (raw archive, additive)
                    + scripts/etl_pricecatcher (premise-level
                      MarketPriceObservation, stable MarketItem ids)

    ManaMurah    ──▶ services/mcp_client (streamable HTTP, defensive
                    parsing) + services/market_ingestion (validation +
                    idempotent upsert, source isolation)

This service does NOT:
  - touch Product.selling_price / PriceHistory / PricingRecommendationDecision
  - create migrations, monitoring pages, or scheduling
  - rebuild the 1.97M observation table except via the existing ETL
  - duplicate normalize_code / normalize_package_size / idempotency keys

One programmatic entry point: MarketDataRefreshService.refresh()

Usage:
    from services.market_refresh_service import MarketDataRefreshService
    svc = MarketDataRefreshService()
    results = svc.refresh(sources='all')  # or 'pricecatcher'/'manamurah'
    results['manamurah'].inserted etc.

The CLI wrapper lives in app.py (``flask market-data refresh``).

Idempotency:
  PriceCatcher: uq_price_natural + uq_market_obs_premise + fetch_existing_keys
  ManaMurah:  (source_id,external_id) + (market_item_id,observed_at,state,district)
  Re-running refresh() with identical data reports 0 new / N duplicates.

Source isolation:
  PriceCatcher ETL deletes only WHERE source_id = pricecatcher_id.
  ManaMurah ingestion upserts only WHERE source_id = manamurah_id.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional

log = logging.getLogger(__name__)

VALID_SOURCES = ("pricecatcher", "manamurah", "all")


@dataclass
class MarketRefreshResult:
    """Unified result for one source refresh.

    success: True when the source completed without an unhandled exception.
             Per-record rejections do NOT make success False — they are
             counted in rejected/duplicates.
    inserted: New rows created (PriceCatcher: price rows inserted /
              observations inserted; ManaMurah: new_observations).
    updated: Existing rows refreshed in place (PriceCatcher: MarketItems
             updated; ManaMurah: updated_items).
    duplicates: Rows already present and skipped (PriceCatcher: duplicates
                from existing archive keys; ManaMurah: duplicates_skipped).
    rejected: Records/observations rejected by validation or orphan filter.
    errors: Unhandled per-item exceptions rolled back (ingestion layer).
    latest_observed_at: Most recent observed_at for the source after refresh,
                        or None if source has no observations.
    error_message: Human-readable failure reason when success is False.
    raw: Original source-specific counters (for debugging / dry-run detail).
    """
    source: str
    success: bool
    inserted: int = 0
    updated: int = 0
    duplicates: int = 0
    rejected: int = 0
    errors: int = 0
    latest_observed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    raw: Dict = field(default_factory=dict)


def _latest_for_source(source_id: int):
    """Most recent observed_at for a source, or None."""
    try:
        from app import db
        from sqlalchemy import text
        row = db.session.execute(
            text("SELECT MAX(observed_at) FROM market_price_observation "
                 "WHERE market_item_id IN (SELECT id FROM market_item WHERE source_id=:sid)"),
            {"sid": source_id},
        ).scalar()
        return row
    except Exception:
        return None


def _manamurah_result(stats: Dict, error_message: Optional[str] = None) -> MarketRefreshResult:
    """Normalize market_ingestion Counter → unified result."""
    # stats keys: retrieved, accepted, rejected, new_items, updated_items,
    # new_observations, duplicates_skipped, errors
    return MarketRefreshResult(
        source="manamurah",
        success=error_message is None,
        inserted=int(stats.get("new_observations", 0)),
        updated=int(stats.get("updated_items", 0)),
        duplicates=int(stats.get("duplicates_skipped", 0)),
        rejected=int(stats.get("rejected", 0)),
        errors=int(stats.get("errors", 0)),
        latest_observed_at=None,  # filled by caller after DB lookup
        error_message=error_message,
        raw=dict(stats),
    )


def _pricecatcher_result(raw: Dict, latest=None, error_message=None) -> MarketRefreshResult:
    """Normalize PriceCatcher ETL/import counters → unified result.

    raw may contain keys from import_month (inserted, duplicates, orphans,
    invalid) and/or from ETL (items_created, items_updated, deleted,
    inserted_observations, issues). We preserve everything in raw and map
    the most relevant ones to the unified fields without inventing numbers.
    """
    inserted = int(raw.get("inserted_observations", raw.get("inserted", 0)))
    duplicates = int(raw.get("duplicates", 0))
    rejected = int(raw.get("orphans", 0)) + int(raw.get("skipped_empty", 0)) + int(raw.get("fallback_1unit", 0) * 0)  # fallback is not rejection
    # Orphans are the true rejections for PriceCatcher; keep invalid separate in raw
    rejected = int(raw.get("orphans", 0)) + int(raw.get("invalid", 0))
    updated = int(raw.get("items_updated", raw.get("updated_items", 0)))
    errors = int(raw.get("errors", 0))
    return MarketRefreshResult(
        source="pricecatcher",
        success=error_message is None,
        inserted=inserted,
        updated=updated,
        duplicates=duplicates,
        rejected=rejected,
        errors=errors,
        latest_observed_at=latest,
        error_message=error_message,
        raw=dict(raw),
    )


def _sanitize_error(msg: Optional[str]) -> Optional[str]:
    """Sanitize error message before persistence (no secrets, truncated)."""
    if not msg:
        return None
    s = str(msg).strip()
    # Redact common secret patterns
    lowered = s.lower()
    if "database_url" in lowered or "password" in lowered or "://.*:.*@" in s:
        # Generic redaction when a URL with credentials might be present
        # Keep only the exception type / first line
        s = s.split("\n")[0][:500]
        if "://" in s:
            s = "[redacted connection info] " + s.split(" ", 1)[-1][:500]
    # Truncate to column-safe length (Text, but keep reasonable)
    if len(s) > 2000:
        s = s[:2000] + "…"
    return s


def _status_for(result: MarketRefreshResult) -> str:
    """Derive persisted status from unified result."""
    if not result.success:
        return "failed"
    if result.errors and int(result.errors) > 0:
        return "partial"
    return "success"


def _record_run(source_name: str, started_at, finished_at,
                result: MarketRefreshResult, triggered_by: str = "manual"):
    """Persist a MarketRefreshRun row for one source attempt.

    Safe to call even when result is a failure — always writes started/
    finished/error. Failures to write are logged, never raised.
    Skips dry_run runs (raw.get('dry_run') is True) to keep history clean.
    """
    if result.raw and result.raw.get("dry_run"):
        return None
    try:
        from app import db, MarketRefreshRun
        from flask import has_app_context
        from app import app as flask_app

        def _write():
            # Invalidate health cache for this source so next /market-data load sees fresh counts
            try:
                from services.market_refresh_health import _invalidate_obs_cache
                _invalidate_obs_cache(source_name)
            except Exception:
                pass
            latest = result.latest_observed_at
            # Normalize latest_observed_at to datetime if it's a date
            if latest is not None:
                try:
                    from datetime import date as _date
                    if isinstance(latest, _date) and not isinstance(latest, datetime):
                        latest = datetime(latest.year, latest.month, latest.day)
                except Exception:
                    pass
            run = MarketRefreshRun(
                source_name=source_name.strip().lower(),
                started_at=started_at,
                finished_at=finished_at,
                status=_status_for(result),
                inserted=int(result.inserted or 0),
                updated=int(result.updated or 0),
                duplicates_skipped=int(result.duplicates or 0),
                rejected=int(result.rejected or 0),
                errors=int(result.errors or 0),
                latest_observed_at=latest,
                error_message=_sanitize_error(result.error_message),
                triggered_by=triggered_by,
            )
            db.session.add(run)
            db.session.commit()
            return run

        if has_app_context():
            return _write()
        else:
            with flask_app.app_context():
                return _write()
    except Exception as exc:
        log.error("Failed to persist MarketRefreshRun for %s: %s", source_name, exc)
        try:
            from app import db
            db.session.rollback()
        except Exception:
            pass
        return None


class MarketDataRefreshService:
    """Orchestrates existing market refresh paths.

    Keeps Flask/app concerns at the edge: callers (CLI, future API) can
    inject dependencies for testing. No automatic repricing is ever
    triggered — this service only writes Market* tables.
    """

    def __init__(self, mcp_client=None):
        """Optionally inject a ManaMurahClient (tests mock it)."""
        self.mcp_client = mcp_client

    # -------------------------------------------------
    # Public entry point
    # -------------------------------------------------
    def refresh(self, sources: str = "all", dry_run: bool = False,
                days: int = 30, state: Optional[str] = None,
                triggered_by: str = "manual") -> Dict[str, MarketRefreshResult]:
        """Refresh one or both sources and return per-source results.

        Args:
            sources: 'pricecatcher' | 'manamurah' | 'all'
            dry_run: When True, ManaMurah does NOT commit (validation only);
                     PriceCatcher does NOT run the destructive ETL rebuild
                     and reports a dry-run success without DB writes.
                     The flag is only advertised as safe because ManaMurah's
                     dry-run truly skips commits; PriceCatcher's dry-run
                     skips the ETL entirely and is therefore safe by omission.
            days, state: Forwarded to ManaMurah FAMA sync (RUNCIT, 1-90 days,
                         optional state slug).
            triggered_by: 'manual' | 'scheduled' (future 10E).

        Returns:
            Dict mapping source name -> MarketRefreshResult. Failed sources
            are still present with success=False and error_message set.
            Each source's run is persisted to market_refresh_run (except
            dry_run, which is validation-only and not persisted).
        """
        norm = (sources or "all").strip().lower()
        if norm not in VALID_SOURCES:
            raise ValueError(f"Invalid sources {sources!r}: expected one of {VALID_SOURCES}")

        results: Dict[str, MarketRefreshResult] = {}
        targets = ["pricecatcher", "manamurah"] if norm == "all" else [norm]

        for tgt in targets:
            started = datetime.now(timezone.utc)
            try:
                if tgt == "manamurah":
                    results[tgt] = self.refresh_manamurah(dry_run=dry_run, days=days, state=state, triggered_by=triggered_by)
                else:
                    results[tgt] = self.refresh_pricecatcher(dry_run=dry_run, triggered_by=triggered_by)
            except Exception as exc:  # per-source isolation: one failure does not kill the other
                log.exception("Market refresh failed for %s: %s", tgt, exc)
                finished = datetime.now(timezone.utc)
                res = MarketRefreshResult(
                    source=tgt, success=False, error_message=str(exc), raw={"exception": type(exc).__name__}
                )
                _record_run(tgt, started, finished, res, triggered_by=triggered_by)
                results[tgt] = res
        return results

    # -------------------------------------------------
    # ManaMurah (reuses market_ingestion + mcp_client)
    # -------------------------------------------------
    def refresh_manamurah(self, dry_run: bool = False, days: int = 30,
                          state: Optional[str] = None,
                          triggered_by: str = "manual") -> MarketRefreshResult:
        """Fetch FAMA dry-goods via MCP and upsert through market_ingestion.

        Reuses services.mcp_client.ManaMurahClient and
        services.market_ingestion.sync_from_client / ingest_records — no
        duplicate validation or idempotency logic.

        dry_run: when True, fetched records are validated but NOT committed
                 (no DB writes, no history row).
        """
        from app import app
        from services.market_ingestion import ingest_records
        started_at = datetime.now(timezone.utc)

        # Lazily import to keep module importable without the mcp package
        if self.mcp_client is not None:
            client = self.mcp_client
        else:
            from services.mcp_client import ManaMurahClient
            client = ManaMurahClient()

        # FAMA scope is the hard-wired dry-goods list from sync_market_data
        # — do NOT broaden it here.
        try:
            from scripts.sync_market_data import FAMA_SCOPE_ITEMS
            scope_items = FAMA_SCOPE_ITEMS
        except Exception:
            # Fallback if script not importable (still the same 5 items)
            scope_items = [
                (46, 'TELUR AYAM'), (40, 'BERAS SAWAH'), (41, 'BERAS IMPORT'),
                (44, 'SANTAN BERSARIKAT'), (45, 'UBI KENTANG HOLLAND'),
            ]

        # Fetch outside the app context (network, no DB)
        try:
            records = client.fetch_fama_records(scope_items, level='RUNCIT', days=int(days), state_slug=state)
        except Exception as exc:
            log.error("ManaMurah fetch failed: %s", exc)
            finished_at = datetime.now(timezone.utc)
            res = MarketRefreshResult(source="manamurah", success=False, error_message=str(exc), raw={"fetch_error": str(exc)})
            _record_run("manamurah", started_at, finished_at, res, triggered_by=triggered_by)
            return res

        # Dry-run: validate without persisting (safe — no DB writes)
        if dry_run:
            from services.market_ingestion import validate_record, validate_observation
            from collections import Counter
            stats = Counter()
            stats['retrieved'] = len(records)
            for rec in records:
                ok, _ = validate_record(rec)
                if not ok:
                    stats['rejected'] += 1
                    continue
                stats['accepted'] += 1
                for obs in rec.get('observations', []):
                    ok2, _ = validate_observation(obs)
                    if not ok2:
                        stats['rejected'] += 1
                    else:
                        # Would be inserted/updated, but not persisted in dry-run
                        stats['new_observations'] += 0
            stats['dry_run'] = True
            return MarketRefreshResult(
                source="manamurah", success=True, inserted=0, updated=0,
                duplicates=0, rejected=int(stats['rejected']), errors=0,
                latest_observed_at=None, error_message=None,
                raw=dict(stats),
            )

        # Real ingest inside app context
        def _ingest():
            stats = ingest_records(records, source_name='ManaMurah', commit=True)
            latest = None
            try:
                from app import MarketSource
                src = MarketSource.query.filter_by(name='ManaMurah').first()
                if src:
                    latest = _latest_for_source(src.id)
            except Exception:
                pass
            res = _manamurah_result(stats, None)
            res.latest_observed_at = latest
            finished_at = datetime.now(timezone.utc)
            _record_run("manamurah", started_at, finished_at, res, triggered_by=triggered_by)
            return res

        # Ensure an app context exists
        try:
            from flask import has_app_context
            if has_app_context():
                return _ingest()
            else:
                with app.app_context():
                    return _ingest()
        except Exception as exc:
            log.exception("ManaMurah ingest failed: %s", exc)
            finished_at = datetime.now(timezone.utc)
            res = MarketRefreshResult(source="manamurah", success=False, error_message=str(exc), raw={"exception": type(exc).__name__})
            _record_run("manamurah", started_at, finished_at, res, triggered_by=triggered_by)
            return res

    # -------------------------------------------------
    # PriceCatcher (reuses ETL, safe in dry-run)
    # -------------------------------------------------
    def refresh_pricecatcher(self, dry_run: bool = False,
                           triggered_by: str = "manual") -> MarketRefreshResult:
        """Rebuild PriceCatcher market observations via the existing ETL.

        In dry_run mode the ETL is NOT executed — this makes dry_run safe
        by omission (no 1.97M delete/rebuild). The method returns a dry-run
        success with zero counts and raw={'dry_run': True}.

        When not dry_run, the method reuses _get_or_create_source,
        _upsert_market_items, and _rebuild_observations from
        scripts/etl_pricecatcher — the authoritative path that preserves
        stable MarketItem ids and ProductMarketMatch compatibility and
        deletes ONLY PriceCatcher observations.

        Note: the network import step (import_pricecatcher.py) is NOT run
        here — the raw archive (price/lookup_*) is the source of truth and
        is populated separately via ``python import_pricecatcher.py``. The
        ETL is the DB transform that this service orchestrates. Triggering
        a network download from a web request would be inappropriate for 10A.
        """
        from app import app
        started_at = datetime.now(timezone.utc)

        if dry_run:
            # Safe by omission: report dry-run without touching the 1.97M rows
            # Not persisted — dry runs are validation-only.
            return MarketRefreshResult(
                source="pricecatcher", success=True, inserted=0, updated=0,
                duplicates=0, rejected=0, errors=0,
                error_message=None, raw={"dry_run": True, "note": "PriceCatcher ETL not executed in dry-run mode"}
            )

        def _run():
            from scripts.etl_pricecatcher import _get_or_create_source, _upsert_market_items, _rebuild_observations
            source = _get_or_create_source()
            by_code, stats, issues = _upsert_market_items(source)
            n_deleted, n_inserted = _rebuild_observations(source, by_code)
            raw = dict(stats)
            raw.update({"deleted": n_deleted, "inserted_observations": n_inserted, "issues": len(issues)})
            latest = _latest_for_source(source.id)
            res = _pricecatcher_result(raw, latest=latest)
            finished_at = datetime.now(timezone.utc)
            _record_run("pricecatcher", started_at, finished_at, res, triggered_by=triggered_by)
            return res

        try:
            from flask import has_app_context
            if has_app_context():
                return _run()
            else:
                with app.app_context():
                    return _run()
        except Exception as exc:
            log.exception("PriceCatcher refresh failed: %s", exc)
            finished_at = datetime.now(timezone.utc)
            res = MarketRefreshResult(source="pricecatcher", success=False, error_message=str(exc), raw={"exception": type(exc).__name__})
            _record_run("pricecatcher", started_at, finished_at, res, triggered_by=triggered_by)
            return res
