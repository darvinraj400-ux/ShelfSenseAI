"""
============================================================
 ShelfSenseAI — Phase 8 Pricing Decision Workflow
============================================================

Turns the Phase 7 recommendation screen into a decision-support workflow.
Every recommendation an owner/manager reviews is snapshotted as a
PricingRecommendationDecision (PENDING), and the retailer can explicitly
APPLY it (price changes through the existing audit trail) or DISMISS it
(reason recorded). Nothing here changes a price automatically: the only
code paths that touch Product.selling_price require an explicit POST from
an authorized owner/manager.

GUARANTEES
----------
- The recommendation snapshot (what the retailer SAW) is written once and
  never recalculated — historical auditability.
- Apply is STALE-SAFE: the snapshot's current_price must match the live
  product price, otherwise the apply is rejected (the market/cost inputs
  may have changed under the recommendation).
- Apply is SERVER-SIDE-VALIDATED: the price applied is always freshly
  recomputed by the deterministic engine, never taken from the client.
- Apply is ATOMIC: product price update + PriceHistory + decision row
  commit together or not at all.
- Gemini is never involved in any decision; this module doesn't call it.

SNAPSHOT DESIGN
---------------
record_decision() stores only deterministic fields from
get_price_recommendation() (price, status, market stats, trend,
guardrails). The LLM explanation is deliberately NOT snapshotted as
authority data — Gemini is explanation-only.
"""
from datetime import datetime, timezone

from app import db, Product, PriceHistory, PricingRecommendationDecision
from services.pricing_engine import get_price_recommendation


# Dismissal reasons offered in the UI (free-text "Other" note allowed).
DISMISS_REASONS = [
    "Market price is not representative",
    "Supplier cost changed",
    "Customer demand considerations",
    "Promotion / campaign",
    "Competitor is temporary",
    "Management decision",
    "Other",
]

_MAX_NOTE_LEN = 255  # decision_reason column width


def _snapshot_from_rec(rec):
    """Extract the deterministic snapshot fields from a recommendation payload."""
    m = rec.get("market_stats") or {}
    return {
        "recommended_price": float(rec["recommended_price"]),
        "current_price": (float(rec["current_price"])
                          if rec.get("current_price") is not None else None),
        "recommendation_status": rec.get("status") or "INSUFFICIENT_DATA",
        "market_evidence": rec.get("market_evidence"),
        "market_tier": m.get("market_tier"),
        "market_median": m.get("median"),
        "trend_classification": rec.get("trend_direction"),
        "trend_percent": rec.get("trend_change_percent"),
        "guardrails_applied": ",".join(rec.get("guardrails_applied") or []),
    }


def record_decision(product_id, shop=None, user_id=None):
    """Snapshot the CURRENT recommendation as a PENDING decision record.

    Called when an owner/manager reviews the pricing pane. If an identical
    PENDING snapshot already exists (same product, same recommended price,
    same snapshot current price, same engine status), it is REUSED instead
    of piling up duplicate rows. Returns (decision, created_bool).

    This only READS the pricing engine and INSERTs a record — it never
    modifies the product's price.
    """
    product = Product.query.get_or_404(product_id)
    rec = get_price_recommendation(product_id, shop=shop)
    snap = _snapshot_from_rec(rec)

    existing = (PricingRecommendationDecision.query
                .filter_by(product_id=product_id, decision="PENDING",
                           recommended_price=snap["recommended_price"],
                           current_price=snap["current_price"],
                           recommendation_status=snap["recommendation_status"])
                .order_by(PricingRecommendationDecision.generated_at.desc())
                .first())
    if existing:
        return existing, False

    decision = PricingRecommendationDecision(
        product_id=product_id,
        shop_id=product.shop_id,
        **snap,
    )
    db.session.add(decision)
    db.session.commit()
    return decision, True


def get_pending_decision(product_id):
    """The most recent PENDING decision for a product, or None."""
    return (PricingRecommendationDecision.query
            .filter_by(product_id=product_id, decision="PENDING")
            .order_by(PricingRecommendationDecision.generated_at.desc())
            .first())


def get_recent_decisions(product_id, limit=10):
    """Recent decision records for the product history card (bounded)."""
    return (PricingRecommendationDecision.query
            .filter_by(product_id=product_id)
            .order_by(PricingRecommendationDecision.generated_at.desc())
            .limit(limit)
            .all())


def apply_decision(decision_id, user_id, shop=None):
    """Apply a PENDING decision atomically (owner/manager only upstream).

    Server-side flow — the client never supplies the price:
      1. Load the decision row; enforce shop ownership.
      2. Reject if not PENDING (idempotency: double-apply is a no-op).
      3. STALE CHECK: the product's LIVE selling price must still equal
         the snapshot's current_price. If another employee changed the
         price since the snapshot, the recommendation is outdated —
         return 'stale' and let the retailer review a fresh one.
      4. RECOMPUTE the recommendation with the deterministic engine and
         apply THAT price (never a client-supplied value; also guards
         against the recommendation moving between snapshot and apply).
      5. Update selling_price, write a PriceHistory row (existing audit
         trail), mark the decision APPLIED — one transaction.

    Returns (status, message) where status is
    'applied' | 'already_applied' | 'stale' | 'invalid'.
    """
    d = PricingRecommendationDecision.query.get_or_404(decision_id)
    product = Product.query.get_or_404(d.product_id)

    # --- Authorization guard (defense in depth; the route also checks) ---
    if product.shop_id != d.shop_id:
        return 'invalid', "Decision does not belong to this product's shop."

    # --- Idempotency: already applied (or dismissed) ---
    if d.decision == "APPLIED":
        return 'already_applied', "This recommendation was already applied."
    if d.decision == "DISMISSED":
        return 'invalid', "A dismissed recommendation cannot be applied."

    # --- Stale check: live price must match the snapshot ---
    live_price = (float(product.selling_price)
                  if product.selling_price is not None else None)
    if live_price != d.current_price:
        return 'stale', (
            "This recommendation is outdated because the product price has "
            "changed. Please review the latest recommendation.")

    # --- Recompute with the deterministic engine (authoritative price) ---
    rec = get_price_recommendation(product.id, shop=shop)
    new_price = float(rec["recommended_price"])
    if not new_price or new_price <= 0:
        return 'invalid', "Recommendation is not a valid price."

    old_price = live_price

    # --- Atomic write: price + PriceHistory + decision ---
    try:
        product.selling_price = round(new_price, 2)
        db.session.add(PriceHistory(
            product_id=product.id,
            cost_price=product.cost_price,
            selling_price=product.selling_price,
            target_margin=product.target_margin,
        ))
        d.decision = "APPLIED"
        d.decision_reason = None
        d.decided_by = user_id
        d.decided_at = datetime.now(timezone.utc)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    return 'applied', (f"Pricing recommendation applied successfully. "
                       f"Selling price updated to RM{new_price:.2f}.")


def dismiss_decision(decision_id, user_id, reason, note=None):
    """Dismiss a PENDING decision with a reason (owner/manager only upstream).

    The reason must be one of DISMISS_REASONS; a short optional note is
    appended (length-capped). Returns (status, message) with status in
    'dismissed' | 'already_decided' | 'invalid'.
    """
    d = PricingRecommendationDecision.query.get_or_404(decision_id)
    if d.decision != "PENDING":
        return 'already_decided', (
            f"This recommendation was already {d.decision.lower()}.")

    if reason not in DISMISS_REASONS:
        return 'invalid', "Please choose a valid dismissal reason."

    stored = reason
    note = (note or "").strip()
    if reason == "Other":
        if not note:
            return 'invalid', "Please provide a short explanation."
        stored = f"Other: {note}"
    elif note:
        stored = f"{reason} — {note}"
    stored = stored[:_MAX_NOTE_LEN]

    try:
        d.decision = "DISMISSED"
        d.decision_reason = stored
        d.decided_by = user_id
        d.decided_at = datetime.now(timezone.utc)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    return 'dismissed', "Pricing recommendation dismissed."
