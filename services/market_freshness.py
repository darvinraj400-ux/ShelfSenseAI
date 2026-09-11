"""
====================================================================
 ShelfSenseAI - Market Freshness Classification (Phase 10F)
====================================================================

Centralized, source-specific freshness classification.
Reuses Phase 10C thresholds — no duplicated magic numbers.

  fresh     ≤ threshold_fresh  (ManaMurah 3d, PriceCatcher 35d)
  aging   ≤ threshold_aging   (ManaMurah 7d, PriceCatcher 60d)
  stale   > threshold_aging
  unavailable: no observation / missing source / missing date

Source isolation: every query is filtered by MarketSource → MarketItem
→ MarketPriceObservation, so PriceCatcher freshness never uses ManaMurah
rows.

UTC-consistent: all age calculations use UTC, handle naive/aware,
future dates (treated as fresh, age 0).

Usage:
  freshness = classify_market_freshness(source="PriceCatcher",
                                        latest_observed_at=datetime(...))
  # or for a product's aggregated market:
  freshness = get_product_market_freshness(product_id, shop)
"""
from datetime import datetime, timezone, timedelta, date
from typing import Dict, Optional

from app import db, MarketSource, MarketItem, MarketPriceObservation, ProductMarketMatch
from services.market_refresh_health import (
    MANAMURAH_FRESH_DAYS,
    MANAMURAH_AGING_DAYS,
    PRICECATCHER_FRESH_DAYS,
    PRICECATCHER_AGING_DAYS,
    DEFAULT_FRESH_DAYS,
    DEFAULT_AGING_DAYS,
)

def _thresholds_for(source_name: Optional[str]):
    s = (source_name or "").strip().lower()
    if s == "manamurah":
        return MANAMURAH_FRESH_DAYS, MANAMURAH_AGING_DAYS
    if s == "pricecatcher":
        return PRICECATCHER_FRESH_DAYS, PRICECATCHER_AGING_DAYS
    return DEFAULT_FRESH_DAYS, DEFAULT_AGING_DAYS

def _age_days(latest) -> Optional[int]:
    if latest is None:
        return None
    try:
        if isinstance(latest, date) and not isinstance(latest, datetime):
            latest = datetime(latest.year, latest.month, latest.day)
        if getattr(latest, 'tzinfo', None) is not None:
            latest = latest.astimezone(timezone.utc).replace(tzinfo=None)
        now = datetime.utcnow()
        # Future observation (clock skew) → age 0, still fresh
        if latest > now:
            return 0
        delta = now - latest
        return max(0, delta.days)
    except Exception:
        return None

def classify_market_freshness(source: Optional[str], latest_observed_at) -> Dict:
    """Classify freshness for a source's latest observation.

    Returns dict:
      freshness: 'fresh'|'aging'|'stale'|'unavailable'
      age_days: int | None
      label: human-readable e.g. 'Fresh (12 days ago)'
      warning: optional string for UI (None when fresh/unavailable)
      thresholds: (fresh_days, aging_days) for reference
      source: normalized source name or None
    """
    norm_source = (source or "").strip().lower() if source else None
    if latest_observed_at is None:
        return {
            "freshness": "unavailable",
            "age_days": None,
            "label": "Unavailable",
            "warning": None,
            "thresholds": _thresholds_for(source),
            "latest_observed_at": None,
            "source": norm_source,
        }
    fresh_days, aging_days = _thresholds_for(source)
    age = _age_days(latest_observed_at)
    if age is None:
        return {
            "freshness": "unavailable",
            "age_days": None,
            "label": "Unavailable",
            "warning": None,
            "thresholds": (fresh_days, aging_days),
            "latest_observed_at": latest_observed_at,
            "source": norm_source,
        }
    if age <= fresh_days:
        label = f"Fresh ({age} days ago)"
        warning = None
        freshness = "fresh"
    elif age <= aging_days:
        label = f"Aging ({age} days ago)"
        warning = f"Market observations are becoming less current ({age} days old, aging threshold {aging_days} days)"
        freshness = "aging"
    else:
        label = f"Stale ({age} days ago)"
        warning = f"Market evidence is historical and current conditions may have changed ({age} days old, stale threshold {aging_days} days)"
        freshness = "stale"
    return {
        "freshness": freshness,
        "age_days": age,
        "label": label,
        "warning": warning,
        "thresholds": (fresh_days, aging_days),
        "latest_observed_at": latest_observed_at,
        "source": norm_source,
    }

def get_product_market_freshness(product_id: int, shop=None) -> Dict:
    """Freshness for a product's aggregated market (source-isolated).

    Finds the latest observation among the product's VERIFIED matches
    and classifies it using the observation's actual source thresholds.
    Respects the same geographic filtering as get_market_stats (district
    → state → national) so district-filtered evidence is judged by its
    district data, not national.
    If product has no verified matches or no observations → unavailable.
    Preserves source semantics: a PriceCatcher-only product is judged
    by PriceCatcher cadence, not ManaMurah. When a product has verified
    matches from **multiple active sources**, freshness is the most
    conservative among contributing sources (stale > aging > fresh) so
    a stale source is not hidden behind a fresh one. This ensures the
    freshness shown alongside the combined evidence (median from all
    observations) does not silently combine fresh+stale into a generic
    'fresh' value.
    """
    from sqlalchemy import text
    # Find verified matches
    matches = ProductMarketMatch.query.filter_by(shop_product_id=product_id, is_verified=True).all()
    if not matches:
        return classify_market_freshness(None, None)
    # Use the same geographic filtering as market_analysis to find the
    # latest observation that actually contributed to the evidence.
    # Import here to avoid circular import at module load.
    try:
        from services.market_analysis import _fetch_localized_observations
        # Group latest per source that actually contributed
        per_source_latest: Dict[str, object] = {}
        per_source_name: Dict[str, str] = {}
        for m in matches:
            obs_list, _ = _fetch_localized_observations(m.market_item_id, shop)
            if not obs_list:
                continue
            cur_latest = max(o.observed_at for o in obs_list if o.observed_at)
            mi = MarketItem.query.get(m.market_item_id)
            src = MarketSource.query.get(mi.source_id) if mi and mi.source_id else None
            src_key = (src.name.strip().lower() if src and src.name else "unknown")
            # Keep the most recent per source (in case multiple items from same source)
            if src_key not in per_source_latest or cur_latest > per_source_latest[src_key]:
                per_source_latest[src_key] = cur_latest
                per_source_name[src_key] = src.name if src else src_key
        if not per_source_latest:
            return classify_market_freshness(None, None)
        # Classify each contributing source
        per_source_classified = {
            k: classify_market_freshness(per_source_name[k], v)
            for k, v in per_source_latest.items()
        }
        # If only one source contributed, return it directly
        if len(per_source_classified) == 1:
            return next(iter(per_source_classified.values()))
        # Multiple sources: most conservative (stale > aging > fresh > unavailable)
        # Order: unavailable is least conservative (no data), but if any source has data, ignore unavailable
        order = {"stale": 3, "aging": 2, "fresh": 1, "unavailable": 0}
        # Filter to sources that actually have observations (exclude unavailable)
        candidates = {k: v for k, v in per_source_classified.items() if v["freshness"] != "unavailable"}
        if not candidates:
            return classify_market_freshness(None, None)
        most_conservative_key = max(candidates, key=lambda k: order.get(candidates[k]["freshness"], 0))
        return candidates[most_conservative_key]
    except Exception:
        # Fallback: unfiltered latest (should not happen)
        latest = None
        source_of_latest = None
        for m in matches:
            mi = MarketItem.query.get(m.market_item_id)
            src = MarketSource.query.get(mi.source_id) if mi else None
            row = db.session.execute(
                text("SELECT MAX(observed_at) FROM market_price_observation WHERE market_item_id=:mid"),
                {"mid": m.market_item_id},
            ).scalar()
            if row and (latest is None or row > latest):
                latest = row
                source_of_latest = src.name if src else None
        if latest is None:
            return classify_market_freshness(None, None)
        return classify_market_freshness(source_of_latest, latest)
