"""
====================================================================
 ShelfSenseAI - Market Refresh Status Helpers (Phase 10B)
====================================================================

Read-only helpers over market_refresh_run history.

No pricing, no repricing, no UI — pure queries.
"""
from datetime import datetime, timezone
from typing import List, Optional

from app import db, MarketRefreshRun


def latest_run(source_name: str) -> Optional[MarketRefreshRun]:
    """Most recent run for a source, or None."""
    if not source_name:
        return None
    return (MarketRefreshRun.query
            .filter_by(source_name=source_name.strip().lower())
            .order_by(MarketRefreshRun.started_at.desc())
            .first())


def latest_successful_run(source_name: str) -> Optional[MarketRefreshRun]:
    """Most recent successful run for a source, or None."""
    if not source_name:
        return None
    return (MarketRefreshRun.query
            .filter_by(source_name=source_name.strip().lower(), status='success')
            .order_by(MarketRefreshRun.started_at.desc())
            .first())


def recent_runs(source_name: Optional[str] = None, limit: int = 10) -> List[MarketRefreshRun]:
    """Recent runs, newest first. Filtered by source if given."""
    q = MarketRefreshRun.query.order_by(MarketRefreshRun.started_at.desc())
    if source_name:
        q = q.filter_by(source_name=source_name.strip().lower())
    return q.limit(limit).all()


def latest_observation_date(source_name: str):
    """Latest observed_at for a source's last successful run, or None."""
    run = latest_successful_run(source_name)
    if run and run.latest_observed_at:
        return run.latest_observed_at
    # Fallback: latest run that has a latest_observed_at (even if partial)
    run = latest_run(source_name)
    return run.latest_observed_at if run else None
