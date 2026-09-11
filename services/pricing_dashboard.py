"""
============================================================
 ShelfSenseAI — Phase 9 Shop-wide Pricing Intelligence
============================================================

Aggregates the EXISTING per-product pricing intelligence (Phase 6 market
analysis + Phase 7 recommendation engine + Phase 8 decision records) into
a shop-level decision-support overview. This module is a READ-ONLY
reporting layer:

  - It NEVER sets or modifies a selling price.
  - It NEVER creates PricingRecommendationDecision rows (the Phase 8
    snapshot is only written during product review, per its design).
  - It NEVER calls Gemini (recommendations here use skip_llm=True; the
    LLM is explanation-only and irrelevant to aggregate metrics).
  - It REUSES the existing engine status, market tier, evidence rating,
    and geographic fallback — no second implementation of any of them.

PRIORITY (deterministic, transparent, price-neutral):
  Priority only sorts the opportunity table; it can never move a price.
    high     — evidence Strong/Moderate AND status REDUCE/INCREASE
    medium   — evidence Moderate/Limited AND status REDUCE/INCREASE,
               OR evidence Strong/Moderate AND status MAINTAIN
    low      — status MAINTAIN with Limited/Unavailable evidence
    none     — status INSUFFICIENT_DATA (nothing to act on)
"""
from collections import Counter

from sqlalchemy import func

from app import (db, Product, Shop, PricingRecommendationDecision,
                 ProductMarketMatch)
from services.market_analysis import get_market_stats
from services.pricing_engine import (get_price_recommendation,           # noqa: E402
                                     _market_evidence,
                                     _classify_trend, get_market_trend)

# Priorities, ordered highest first (used for sorting + badge styling).
PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2, "none": 3}


def _priority(status, evidence):
    """Deterministic attention priority from ENGINE status + evidence only."""
    if status == "INSUFFICIENT_DATA":
        return "none"
    if evidence in ("Strong", "Moderate") and status in ("REDUCE", "INCREASE"):
        return "high"
    if status in ("REDUCE", "INCREASE"):
        return "medium"
    if evidence in ("Strong", "Moderate"):
        return "medium"
    return "low"


def _empty_summary():
    return {"total_products": 0, "market_covered": 0, "above_market": 0,
            "below_market": 0, "near_market": 0, "no_selling_price": 0,
            "pending": 0, "applied": 0, "dismissed": 0,
            "generated_total": 0, "applied_this_month": 0,
            "dismissed_this_month": 0, "generated_this_month": 0,
            "status_counts": {}, "decision_counts": {},
            "position_counts": {}, "priority_counts": {}}


def get_shop_decision_summary(shop_id):
    """Phase 8 decision statistics for one shop (SQL-aggregated).

    Reads ONLY the existing PricingRecommendationDecision rows — never
    creates them. Distinct products are counted so a long PENDING history
    of one product does not inflate the review workload."""
    if not shop_id:
        return {"pending": 0, "applied": 0, "dismissed": 0,
                "generated_total": 0, "applied_this_month": 0,
                "dismissed_this_month": 0, "generated_this_month": 0}
    q = (db.session.query(
            PricingRecommendationDecision.decision,
            func.count(PricingRecommendationDecision.id),
            func.count(func.distinct(PricingRecommendationDecision.product_id)),
            func.min(func.date(PricingRecommendationDecision.generated_at)),
            func.max(func.date(PricingRecommendationDecision.generated_at)))
         .filter(PricingRecommendationDecision.shop_id == shop_id)
         .group_by(PricingRecommendationDecision.decision))
    out = {"pending": 0, "applied": 0, "dismissed": 0, "generated_total": 0,
           "pending_products": 0, "applied_this_month": 0,
           "dismissed_this_month": 0, "generated_this_month": 0}
    from datetime import date
    month_start = date.today().replace(day=1)
    for decision, n_rows, n_products, first_day, last_day in q.all():
        out["generated_total"] += n_rows
        key = {"PENDING": "pending", "APPLIED": "applied",
               "DISMISSED": "dismissed"}.get(decision)
        if key:
            out[key] = n_products  # distinct products, not raw rows
        if first_day and last_day and str(last_day) >= str(month_start):
            # count this bucket's rows generated this month (per decision state)
            sub = (db.session.query(func.count(PricingRecommendationDecision.id))
                   .filter(PricingRecommendationDecision.shop_id == shop_id,
                           PricingRecommendationDecision.decision == decision,
                           func.date(PricingRecommendationDecision.generated_at)
                           >= month_start).scalar()) or 0
            if key:
                out[f"{key}_this_month"] = sub
            out["generated_this_month"] += sub
    return out


def get_shop_pricing_opportunities(shop, page=1, per_page=20,
                                   status_filter=None, decision_filter=None,
                                   evidence_filter=None, tier_filter=None,
                                   priority_filter=None, search=None,
                                   sort="priority"):
    """Shop-wide pricing opportunity rows (paginated, filtered, sorted).

    Each row reuses the REAL Phase 7 engine output (skip_llm=True so the
    read stays cheap and Gemini is never involved) and the LATEST existing
    Phase 8 decision for context. Rows are computed for the WHOLE filtered
    set, then sorted server-side, then paginated — so per_page slicing
    always shows the true top matches, not the top of page 1.

    Returns dict: rows, total, page, per_page, pages, summary.
    """
    if shop is None:
        summary = _empty_summary()
        return {"rows": [], "total": 0, "page": 1, "per_page": per_page,
                "pages": 1, "summary": summary}

    products = (Product.query.filter_by(shop_id=shop.id)
                .order_by(Product.name.asc()).all())
    if not products:
        summary = _empty_summary()
        summary.update(get_shop_decision_summary(shop.id))
        return {"rows": [], "total": 0, "page": 1, "per_page": per_page,
                "pages": 1, "summary": summary}

    # Latest decision per product, one query (no N+1): max(id) per product.
    latest_ids = [pid for (pid,) in (
        db.session.query(func.max(PricingRecommendationDecision.id))
        .filter(PricingRecommendationDecision.shop_id == shop.id,
                PricingRecommendationDecision.product_id
                .in_([p.id for p in products]))
        .group_by(PricingRecommendationDecision.product_id).all())]
    decisions = {d.product_id: d for d in (
        PricingRecommendationDecision.query.filter(
            PricingRecommendationDecision.id.in_(latest_ids)).all()
        if latest_ids else [])}

    # Decision counters across the whole shop (for summary cards).
    decision_counts = dict(db.session.query(
        PricingRecommendationDecision.decision,
        func.count(func.distinct(PricingRecommendationDecision.product_id)))
        .filter(PricingRecommendationDecision.shop_id == shop.id)
        .group_by(PricingRecommendationDecision.decision).all())

    rows = []
    for p in products:
        rec = get_price_recommendation(p.id, shop=shop, skip_llm=True)
        m = rec.get("market_stats") or {}
        status = rec.get("status") or "INSUFFICIENT_DATA"
        evidence = rec.get("market_evidence") or "Unavailable"
        trend_dir = rec.get("trend_direction")
        tier = m.get("market_tier")
        position = m.get("position")
        current = rec.get("current_price")
        recommended = rec.get("recommended_price")
        diff = (round(recommended - current, 2)
                if (recommended is not None and current is not None) else None)
        prio = _priority(status, evidence)
        d = decisions.get(p.id)
        # Phase 10F freshness (read-only qualification, from market_analysis)
        freshness = rec.get("market_freshness") or m.get("market_freshness") or "unavailable"
        freshness_label = rec.get("freshness_label") or m.get("freshness_label") or "Unavailable"
        freshness_warning = rec.get("freshness_warning") or m.get("freshness_warning")
        rows.append({
            "product_id": p.id,
            "name": p.name,
            "category": p.category,
            "current_price": current,
            "recommended_price": recommended,
            "diff": diff,
            "status": status,
            "market_median": m.get("median"),
            "market_tier": tier,
            "market_tier_label": m.get("market_tier_label"),
            "evidence": evidence,
            "trend": (trend_dir if trend_dir != "insufficient_data" else None),
            "trend_percent": rec.get("trend_change_percent"),
            "position": position,
            "guardrails_applied": rec.get("guardrails_applied") or [],
            "priority": prio,
            "decision": (d.decision if d else None),
            "decision_reason": (d.decision_reason if d else None),
            "decision_at": (d.decided_at if d else None),
            # Phase 10F
            "freshness": freshness,
            "freshness_label": freshness_label,
            "freshness_warning": freshness_warning,
        })

    # ---- filtering (server-side, post-engine) ----
    def _keep(r):
        if status_filter and r["status"] != status_filter:
            return False
        if decision_filter and r["decision"] != decision_filter:
            return False
        if evidence_filter and r["evidence"] != evidence_filter:
            return False
        if tier_filter and r["market_tier"] != tier_filter:
            return False
        if priority_filter and r["priority"] != priority_filter:
            return False
        if search and search.lower() not in (r["name"] or "").lower():
            return False
        return True
    rows = [r for r in rows if _keep(r)]

    # ---- sorting (server-side) ----
    import math
    def _diff_key(r):
        # above-market first desc, then below-market asc; None last
        return math.inf if r["diff"] is None else -r["diff"]
    sorters = {
        "priority": lambda r: (PRIORITY_ORDER.get(r["priority"], 9),
                               r["name"] or ""),
        "above_market": lambda r: _diff_key(r),
        "below_market": lambda r: (math.inf if r["diff"] is None else r["diff"]),
        "diff": lambda r: (math.inf if r["diff"] is None else abs(r["diff"])),
        "name": lambda r: (r["name"] or "").lower(),
        "pending_first": lambda r: (0 if r["decision"] == "PENDING" else 1,
                                    PRIORITY_ORDER.get(r["priority"], 9)),
    }
    rows.sort(key=sorters.get(sort, sorters["priority"]))

    # ---- aggregate summary over the FULL shop (before pagination) ----
    total_products = len(products)
    market_covered = sum(1 for r in rows if r["market_tier"] is not None
                         and r["status"] != "INSUFFICIENT_DATA")
    # Recompute position counts from unfiltered rows is unnecessary — use
    # the full row list before filtering. (rows were filtered in place; the
    # counts below therefore describe the filtered view, matching the table.)
    position_counts = Counter(r["position"] for r in rows
                              if r["position"])
    status_counts = Counter(r["status"] for r in rows)
    priority_counts = Counter(r["priority"] for r in rows)

    total = len(rows)
    pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, pages))
    start = (page - 1) * per_page

    decision_summary = get_shop_decision_summary(shop.id)
    decision_summary.setdefault("pending", decision_counts.get("PENDING", 0))
    decision_summary.setdefault("applied", decision_counts.get("APPLIED", 0))
    decision_summary.setdefault("dismissed",
                                decision_counts.get("DISMISSED", 0))
    summary = {
        "total_products": total_products,
        "market_covered": market_covered,
        "above_market": sum(1 for r in rows
                            if r["position"] == "Above Market"),
        "below_market": sum(1 for r in rows
                            if r["position"] == "Below Market"),
        "near_market": position_counts.get("Near Market", 0),
        "no_selling_price": sum(1 for r in rows
                                if r["current_price"] is None),
        "pending": decision_counts.get("PENDING", 0),
        "applied": decision_counts.get("APPLIED", 0),
        "dismissed": decision_counts.get("DISMISSED", 0),
        "generated_total": decision_summary.get("generated_total", 0),
        "applied_this_month": decision_summary.get("applied_this_month", 0),
        "dismissed_this_month":
            decision_summary.get("dismissed_this_month", 0),
        "generated_this_month":
            decision_summary.get("generated_this_month", 0),
        "status_counts": dict(status_counts),
        "decision_counts": dict(decision_counts),
        "position_counts": dict(position_counts),
        "priority_counts": dict(priority_counts),
    }
    return {"rows": rows[start:start + per_page], "total": total,
            "page": page, "per_page": per_page, "pages": pages,
            "summary": summary}


def get_shop_pricing_summary(shop):
    """Lightweight shop pricing summary (summary cards only, no table).

    Convenience wrapper over get_shop_pricing_opportunities with a 1-row
    page — useful for tests and any future API endpoint."""
    return get_shop_pricing_opportunities(shop, page=1, per_page=1)["summary"]
