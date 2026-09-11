"""
============================================================
 ShelfSenseAI - Phase 8 Pricing Decision Workflow Tests
============================================================

Covers the decision-workflow layer on top of the (unchanged) Phase 7
pricing engine:

  Test 1  - record a recommendation decision (snapshot + PENDING)
  Test 2  - apply: price changes + PriceHistory + decision APPLIED
  Test 3  - transaction rollback on failure (nothing partially written)
  Test 4  - dismiss with user + timestamp + reason
  Test 5  - stale recommendation rejected when price changed since snapshot
  Test 6  - cross-shop access blocked (view/apply/dismiss)
  Test 7  - staff role cannot record/apply/dismiss
  Test 8  - arbitrary client price is ignored (server recomputes)
  Test 9  - recommendation status preserved independently of decision
  Test 10 - historical snapshot survives market changes (no recalculation)
  Test 11 - PriceHistory integration (no duplicate price-history mechanism)
  Test 12 - decision records cannot attach across shops
  Test 13 - duplicate apply is safe (idempotent, no duplicate history)
  Test 14 - dismissed recommendation cannot be applied afterwards
  Test 15 - repeated apply POSTs create no duplicate PriceHistory events

Run:
    ./venv/Scripts/python.exe tests/test_pricing_workflow.py
    pytest tests/test_pricing_workflow.py -q
"""
import os
import sys
import json
import string
import random as rnd
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from app import (app, db, User, Shop, Product, Inventory,  # noqa: E402
                 PriceHistory, PricingRecommendationDecision)
from services.pricing_workflow import (record_decision, apply_decision,  # noqa: E402
                                       dismiss_decision, get_pending_decision,
                                       get_recent_decisions, DISMISS_REASONS)
from werkzeug.security import generate_password_hash  # noqa: E402

PW = "Test1234!"
TEST_SHOPS = ["P8WorkflowShopA", "P8WorkflowShopB"]
PASSED = FAILED = 0


def check(label, cond):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  [PASS] {label}")
    else:
        FAILED += 1
        print(f"  [FAIL] {label}")


def _purge():
    """FK-safe removal of every row this suite created."""
    with app.app_context():
        rows = db.session.execute(
            text("SELECT id FROM shop WHERE name IN :n"),
            {"n": tuple(TEST_SHOPS)}).fetchall()
        if not rows:
            return
        sids = ",".join(str(r[0]) for r in rows)
        for tbl, col in [("pricing_recommendation_decision", "shop_id"),
                         ("inventory_adjustment", "product_id"),
                         ("sale", "product_id"),
                         ("price_history", "product_id"),
                         ("inventory", "product_id"),
                         ("product_market_match", "shop_product_id")]:
            db.session.execute(text(
                f"DELETE FROM {tbl} WHERE {col} IN "
                f"({f'(SELECT id FROM product WHERE shop_id IN ({sids}))' if col != 'shop_id' else sids})"))
        db.session.execute(text(
            f"DELETE FROM product WHERE shop_id IN ({sids})"))
        db.session.execute(text(
            f"DELETE FROM user WHERE shop_id IN ({sids})"))
        db.session.execute(text(
            "DELETE FROM shop WHERE name IN :n"), {"n": tuple(TEST_SHOPS)})
        db.session.commit()


def _slug():
    return "".join(rnd.choices(string.ascii_lowercase, k=6))


def _make_shop(name):
    slug = _slug()
    email = f"p8own_{slug}@shelfsense.my"
    with app.app_context():
        u = User(email=email, password_hash=generate_password_hash(PW),
                 role="owner")
        db.session.add(u)
        db.session.flush()
        s = Shop(name=name)
        db.session.add(s)
        db.session.flush()
        u.shop_id = s.id
        db.session.commit()
        return u.id, s.id, email


def _make_product(sid, name="P8 Product", cost=2.0, margin=25.0, selling=3.0):
    with app.app_context():
        p = Product(name=name, cost_price=cost, target_margin=margin,
                    baseline_margin=margin, selling_price=selling,
                    quantity=1, unit="unit", shop_id=sid)
        db.session.add(p)
        db.session.flush()
        db.session.add(PriceHistory(product_id=p.id, cost_price=cost,
                                    selling_price=selling, target_margin=margin))
        db.session.add(Inventory(shop_id=sid, product_id=p.id,
                                 current_stock=20, minimum_stock=5))
        db.session.commit()
        return p.id


def _make_user(sid, role):
    slug = _slug()
    email = f"p8_{role}_{slug}@shelfsense.my"
    with app.app_context():
        u = User(email=email, password_hash=generate_password_hash(PW),
                 role=role, shop_id=sid)
        db.session.add(u)
        db.session.commit()
        return u.id, email


def _csrf_of(client, path):
    r = client.get(path)
    import re
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', r.data.decode())
    return m.group(1) if m else ""


def _login(client, email):
    csrf = _csrf_of(client, "/login")
    client.post("/login", data={"email": email, "password": PW,
                                "csrf_token": csrf}, follow_redirects=True)
    return csrf


def _logout(client):
    client.get("/logout", follow_redirects=True)


# ---------------------------------------------------------------------------
# Test 1 - record a recommendation decision
# ---------------------------------------------------------------------------
def test_record_decision():
    print("\n--- Test 1: record decision (snapshot + PENDING) ---")
    _purge()
    uid, sid, email = _make_shop(TEST_SHOPS[0])
    pid = _make_product(sid, cost=1.5, margin=25.0, selling=3.0)
    with app.app_context():
        shop = db.session.get(Shop, sid)
        d, created = record_decision(pid, shop=shop, user_id=uid)
        check("decision created", created is True)
        check("decision is PENDING", d.decision == "PENDING")
        check("product_id recorded", d.product_id == pid)
        check("shop_id recorded", d.shop_id == sid)
        check("recommended_price snapshotted", d.recommended_price > 0)
        check("current_price snapshot = 3.0", d.current_price == 3.0)
        check("recommendation_status valid",
              d.recommendation_status in ("MAINTAIN", "REDUCE", "INCREASE",
                                          "INSUFFICIENT_DATA"))
        check("generated_at set", d.generated_at is not None)
        check("decided_by is NULL while pending", d.decided_by is None)
        # identical re-record reuses the same row (no pile-up)
        d2, created2 = record_decision(pid, shop=shop, user_id=uid)
        check("identical snapshot reuses row", created2 is False and d2.id == d.id)
        n = PricingRecommendationDecision.query.filter_by(product_id=pid).count()
        check("still exactly one row", n == 1)
    _purge()


# ---------------------------------------------------------------------------
# Test 2 - apply recommendation
# ---------------------------------------------------------------------------
def test_apply_decision():
    print("\n--- Test 2: apply (price + PriceHistory + APPLIED) ---")
    _purge()
    uid, sid, email = _make_shop(TEST_SHOPS[0])
    pid = _make_product(sid, cost=2.0, margin=50.0, selling=10.0)
    with app.app_context():
        shop = db.session.get(Shop, sid)
        d, _ = record_decision(pid, shop=shop, user_id=uid)
        did = d.id
        pre_hist = PriceHistory.query.filter_by(product_id=pid).count()
        status, msg = apply_decision(did, uid, shop=shop)
        check("apply status = applied", status == "applied")
        p = db.session.get(Product, pid)
        check("selling price changed", float(p.selling_price) != 10.0)
        check("price equals engine recommendation",
              abs(float(p.selling_price) - d.recommended_price) < 0.005)
        hist = PriceHistory.query.filter_by(product_id=pid)\
            .order_by(PriceHistory.created_at.desc()).all()
        check("PriceHistory row created", len(hist) == pre_hist + 1)
        check("PriceHistory holds NEW price",
              float(hist[0].selling_price) == float(p.selling_price))
        d = db.session.get(PricingRecommendationDecision, did)
        check("decision marked APPLIED", d.decision == "APPLIED")
        check("decided_by recorded", d.decided_by == uid)
        check("decided_at recorded", d.decided_at is not None)
    _purge()


# ---------------------------------------------------------------------------
# Test 3 - transaction rollback
# ---------------------------------------------------------------------------
def test_rollback_on_failure():
    print("\n--- Test 3: rollback leaves nothing half-written ---")
    _purge()
    uid, sid, email = _make_shop(TEST_SHOPS[0])
    pid = _make_product(sid)
    with app.app_context():
        shop = db.session.get(Shop, sid)
        d, _ = record_decision(pid, shop=shop, user_id=uid)
        did = d.id
        price_before = float(db.session.get(Product, pid).selling_price)
        hist_before = PriceHistory.query.filter_by(product_id=pid).count()
        # Force a failure inside the atomic block: make the PriceHistory
        # insert fail with a NULL in a NOT NULL column.
        orig_add = db.session.add
        def _boom(obj):
            from app import PriceHistory as PH
            if isinstance(obj, PH):
                obj.target_margin = None  # NOT NULL -> IntegrityError on flush
            return orig_add(obj)
        db.session.add = _boom
        try:
            from sqlalchemy.exc import IntegrityError
            try:
                apply_decision(did, uid, shop=shop)
                check("apply raised IntegrityError", False)
            except IntegrityError:
                check("apply raised IntegrityError", True)
        finally:
            db.session.add = orig_add
            db.session.rollback()
        check("product price unchanged",
              float(db.session.get(Product, pid).selling_price) == price_before)
        check("PriceHistory unchanged",
              PriceHistory.query.filter_by(product_id=pid).count() == hist_before)
        d = db.session.get(PricingRecommendationDecision, did)
        check("decision still PENDING", d.decision == "PENDING")
        check("decided_by still NULL", d.decided_by is None)
    _purge()


# ---------------------------------------------------------------------------
# Test 4 - dismiss recommendation
# ---------------------------------------------------------------------------
def test_dismiss_decision():
    print("\n--- Test 4: dismiss with user + timestamp + reason ---")
    _purge()
    uid, sid, email = _make_shop(TEST_SHOPS[0])
    pid = _make_product(sid)
    with app.app_context():
        shop = db.session.get(Shop, sid)
        d, _ = record_decision(pid, shop=shop, user_id=uid)
        price_before = float(db.session.get(Product, pid).selling_price)
        status, msg = dismiss_decision(d.id, uid, "Market price is not representative",
                                       note="customers prefer premium")
        check("dismiss status = dismissed", status == "dismissed")
        d = db.session.get(PricingRecommendationDecision, d.id)
        check("decision = DISMISSED", d.decision == "DISMISSED")
        check("decided_by recorded", d.decided_by == uid)
        check("decided_at recorded", d.decided_at is not None)
        check("reason recorded", "Market price is not representative" in d.decision_reason)
        check("note appended", "customers prefer premium" in d.decision_reason)
        check("product price NOT changed",
              float(db.session.get(Product, pid).selling_price) == price_before)
        # invalid reason rejected server-side
        d2, _ = record_decision(pid, shop=shop, user_id=uid)
        status2, _ = dismiss_decision(d2.id, uid, "Because I feel like it")
        check("invalid reason rejected", status2 == "invalid")
        # 'Other' requires a note
        d3, _ = record_decision(pid, shop=shop, user_id=uid)
        status3, _ = dismiss_decision(d3.id, uid, "Other", note=None)
        check("Other without note rejected", status3 == "invalid")
        status4, _ = dismiss_decision(d3.id, uid, "Other", note="price war ongoing")
        check("Other with note accepted", status4 == "dismissed")
    _purge()


# ---------------------------------------------------------------------------
# Test 5 - current price changed before Apply (stale)
# ---------------------------------------------------------------------------
def test_stale_recommendation_rejected():
    print("\n--- Test 5: stale snapshot rejected after price change ---")
    _purge()
    uid, sid, email = _make_shop(TEST_SHOPS[0])
    pid = _make_product(sid, cost=2.0, margin=50.0, selling=10.0)
    with app.app_context():
        shop = db.session.get(Shop, sid)
        d, _ = record_decision(pid, shop=shop, user_id=uid)
        check("snapshot current price = 10.0", d.current_price == 10.0)
        # Another employee changes the price behind the snapshot.
        p = db.session.get(Product, pid)
        p.selling_price = 3.20
        db.session.commit()
        hist_before = PriceHistory.query.filter_by(product_id=pid).count()
        status, msg = apply_decision(d.id, uid, shop=shop)
        check("apply rejected as stale", status == "stale")
        check("price NOT overwritten",
              float(db.session.get(Product, pid).selling_price) == 3.20)
        check("no PriceHistory written",
              PriceHistory.query.filter_by(product_id=pid).count() == hist_before)
        check("decision still PENDING",
              db.session.get(PricingRecommendationDecision, d.id).decision == "PENDING")
        check("stale message explains", "outdated" in msg)
    _purge()


# ---------------------------------------------------------------------------
# Test 6 + 7 - cross-shop + role via HTTP endpoints
# ---------------------------------------------------------------------------
def test_cross_shop_and_roles():
    print("\n--- Test 6/7: cross-shop 403 + staff denied ---")
    _purge()
    _, sid_a, email_a = _make_shop(TEST_SHOPS[0])
    _, sid_b, email_b = _make_shop(TEST_SHOPS[1])
    pid_a = _make_product(sid_a)
    with app.app_context():
        shop_a = db.session.get(Shop, sid_a)
        d, _ = record_decision(pid_a, shop=shop_a, user_id=1)
        did = d.id
    staff_email = _make_user(sid_b, "staff")[1]
    staff_a_email = _make_user(sid_a, "staff")[1]

    with app.test_client() as c:
        tok = _login(c, email_b)
        # Shop B owner against Shop A's decision.
        check("cross-shop apply 403",
              c.post(f"/api/decision/{did}/apply",
                     headers={"X-CSRFToken": tok}).status_code == 403)
        check("cross-shop dismiss 403",
              c.post(f"/api/decision/{did}/dismiss", headers={"X-CSRFToken": tok},
                     json={"reason": "Other", "note": "x"}).status_code == 403)
        check("cross-shop record 403",
              c.post(f"/api/product/{pid_a}/decision",
                     headers={"X-CSRFToken": tok}).status_code == 403)
        # Shop A staff: authenticated, same shop, but role denied.
        _logout(c)
        tok = _login(c, staff_a_email)
        check("staff record 403",
              c.post(f"/api/product/{pid_a}/decision",
                     headers={"X-CSRFToken": tok}).status_code == 403)
        check("staff apply 403",
              c.post(f"/api/decision/{did}/apply",
                     headers={"X-CSRFToken": tok}).status_code == 403)
        check("staff dismiss 403",
              c.post(f"/api/decision/{did}/dismiss", headers={"X-CSRFToken": tok},
                     json={"reason": "Other", "note": "x"}).status_code == 403)
        # Unauthenticated user gets 403 (project convention for API routes).
        _logout(c)
        check("anonymous apply 403",
              c.post(f"/api/decision/{did}/apply",
                     headers={"X-CSRFToken": tok}).status_code == 403)
    _purge()


# ---------------------------------------------------------------------------
# Test 8 - arbitrary client price must be ignored
# ---------------------------------------------------------------------------
def test_arbitrary_client_price_rejected():
    print("\n--- Test 8: client cannot inject RM0.01 ---")
    _purge()
    uid, sid, email = _make_shop(TEST_SHOPS[0])
    pid = _make_product(sid, cost=2.0, margin=50.0, selling=10.0)
    with app.app_context():
        shop = db.session.get(Shop, sid)
        d, _ = record_decision(pid, shop=shop, user_id=uid)
        did = d.id
        honest_price = d.recommended_price
    # A malicious client POSTs a forged price alongside the apply request.
    # The apply endpoint accepts NO price field at all — it recomputes —
    # so any 'recommended_price' payload is simply ignored. Verify that no
    # endpoint accepts a client price and the engine value is what lands.
    with app.test_client() as c:
        tok = _login(c, email)
        # try both JSON body and query-string injections
        r1 = c.post(f"/api/decision/{did}/apply?recommended_price=0.01",
                    headers={"X-CSRFToken": tok})
        r2 = c.post(f"/api/decision/{did}/apply",
                    headers={"X-CSRFToken": tok},
                    json={"recommended_price": 0.01})
        check("apply with injected price (query) ok", r1.status_code == 200)
        with app.app_context():
            p = db.session.get(Product, pid)
            check("engine price applied, NOT 0.01",
                  abs(float(p.selling_price) - honest_price) < 0.005
                  and float(p.selling_price) != 0.01)
    _purge()


# ---------------------------------------------------------------------------
# Test 9 - recommendation status vs decision state separation
# ---------------------------------------------------------------------------
def test_status_decision_separation():
    print("\n--- Test 9: status (engine) != decision (retailer) ---")
    _purge()
    uid, sid, email = _make_shop(TEST_SHOPS[0])
    pid = _make_product(sid, cost=2.0, margin=50.0, selling=10.0)
    with app.app_context():
        shop = db.session.get(Shop, sid)
        d, _ = record_decision(pid, shop=shop, user_id=uid)
        check("engine status recorded",
              d.recommendation_status in ("MAINTAIN", "REDUCE", "INCREASE",
                                          "INSUFFICIENT_DATA"))
        check("decision is workflow state", d.decision == "PENDING")
        dismiss_decision(d.id, uid, "Management decision")
        d = db.session.get(PricingRecommendationDecision, d.id)
        check("dismiss keeps engine status",
              d.recommendation_status in ("MAINTAIN", "REDUCE", "INCREASE",
                                          "INSUFFICIENT_DATA")
              and d.decision == "DISMISSED")
        # A REDUCE status never changes a price by itself.
        check("dismissed REDUCE changed no price",
              float(db.session.get(Product, pid).selling_price) == 10.0)
    _purge()


# ---------------------------------------------------------------------------
# Test 10 - historical snapshot preserved
# ---------------------------------------------------------------------------
def test_snapshot_preserved():
    print("\n--- Test 10: snapshot immutable after market changes ---")
    _purge()
    uid, sid, email = _make_shop(TEST_SHOPS[0])
    pid = _make_product(sid, cost=2.0, margin=50.0, selling=10.0)
    with app.app_context():
        shop = db.session.get(Shop, sid)
        d, _ = record_decision(pid, shop=shop, user_id=uid)
        orig = dict(recommended_price=d.recommended_price,
                    market_median=d.market_median,
                    market_evidence=d.market_evidence,
                    trend_classification=d.trend_classification,
                    trend_percent=d.trend_percent,
                    generated_at=d.generated_at)
    # Simulate time passing / market changes: new decisions appear.
    with app.app_context():
        shop = db.session.get(Shop, sid)
        record_decision(pid, shop=shop, user_id=uid)
        d = db.session.get(PricingRecommendationDecision, d.id)
        # (force a different pending row by changing the price in between)
        p = db.session.get(Product, pid)
        p.selling_price = 9.0
        db.session.commit()
        record_decision(pid, shop=shop, user_id=uid)
        d = db.session.get(PricingRecommendationDecision, d.id)
        check("recommended_price unchanged",
              d.recommended_price == orig["recommended_price"])
        check("market_median unchanged", d.market_median == orig["market_median"])
        check("evidence unchanged", d.market_evidence == orig["market_evidence"])
        check("trend unchanged",
              d.trend_classification == orig["trend_classification"]
              and d.trend_percent == orig["trend_percent"])
        check("generated_at unchanged", d.generated_at == orig["generated_at"])
        hist = get_recent_decisions(pid)
        check("history keeps all snapshots", len(hist) == 3)
    _purge()


# ---------------------------------------------------------------------------
# Test 11 - PriceHistory integration
# ---------------------------------------------------------------------------
def test_pricehistory_integration():
    print("\n--- Test 11: apply writes through EXISTING PriceHistory ---")
    _purge()
    uid, sid, email = _make_shop(TEST_SHOPS[0])
    pid = _make_product(sid, cost=2.0, margin=50.0, selling=10.0)
    with app.app_context():
        shop = db.session.get(Shop, sid)
        d, _ = record_decision(pid, shop=shop, user_id=uid)
        apply_decision(d.id, uid, shop=shop)
        rows = (PriceHistory.query.filter_by(product_id=pid)
                .order_by(PriceHistory.created_at.desc()).all())
        check("last history row = applied price",
              float(rows[0].selling_price)
              == float(db.session.get(Product, pid).selling_price))
        check("last history row keeps cost snapshot",
              float(rows[0].cost_price) == 2.0)
        check("last history row keeps margin snapshot",
              float(rows[0].target_margin) == 50.0)
        # the PCAPA baseline tracker still works off the same rows
        first = PriceHistory.query.filter_by(product_id=pid)\
            .order_by(PriceHistory.created_at.asc()).first()
        check("baseline row still first", float(first.selling_price) == 10.0)
    _purge()


# ---------------------------------------------------------------------------
# Test 12 - decisions cannot be attached across shops
# ---------------------------------------------------------------------------
def test_decision_shop_ownership():
    print("\n--- Test 12: decision rows pinned to the product's shop ---")
    _purge()
    uid_a, sid_a, _ = _make_shop(TEST_SHOPS[0])
    _, sid_b, email_b = _make_shop(TEST_SHOPS[1])
    pid_a = _make_product(sid_a)
    with app.app_context():
        shop_a = db.session.get(Shop, sid_a)
        d, _ = record_decision(pid_a, shop=shop_a, user_id=uid_a)
        check("decision shop_id = product's shop", d.shop_id == sid_a)
        check("decision shop_id != other shop", d.shop_id != sid_b)
    # Even a direct fabricated cross-shop apply via the service is blocked
    # by the product/decision consistency guard inside apply_decision.
    with app.app_context():
        # shop_b's product, decision belongs to shop_a's product
        pid_b = _make_product(sid_b)
        shop_b = db.session.get(Shop, sid_b)
        status, _ = apply_decision(d.id, uid_a, shop=shop_b)
        check("mismatched shop context rejected", status == "invalid")
    # HTTP-level: shop B's owner cannot touch shop A's decision.
    with app.test_client() as c:
        tok = _login(c, email_b)
        check("shop B owner blocked from shop A decision",
              c.post(f"/api/decision/{d.id}/apply",
                     headers={"X-CSRFToken": tok}).status_code == 403)
    _purge()


# ---------------------------------------------------------------------------
# Test 13 + 15 - duplicate apply safety
# ---------------------------------------------------------------------------
def test_duplicate_apply_safe():
    print("\n--- Test 13/15: duplicate apply is idempotent ---")
    _purge()
    uid, sid, email = _make_shop(TEST_SHOPS[0])
    pid = _make_product(sid, cost=2.0, margin=50.0, selling=10.0)
    with app.app_context():
        shop = db.session.get(Shop, sid)
        d, _ = record_decision(pid, shop=shop, user_id=uid)
        s1, _ = apply_decision(d.id, uid, shop=shop)
        hist_after_first = PriceHistory.query.filter_by(product_id=pid).count()
        price_after_first = float(db.session.get(Product, pid).selling_price)
        s2, _ = apply_decision(d.id, uid, shop=shop)
        s3, _ = apply_decision(d.id, uid, shop=shop)
        check("first apply succeeded", s1 == "applied")
        check("second apply reports already_applied", s2 == "already_applied")
        check("third apply reports already_applied", s3 == "already_applied")
        check("price unchanged by repeats",
              float(db.session.get(Product, pid).selling_price) == price_after_first)
        check("no duplicate PriceHistory rows",
              PriceHistory.query.filter_by(product_id=pid).count() == hist_after_first)
    # And via HTTP (repeated POST requests, CSRF-protected):
    with app.test_client() as c:
        tok = _login(c, email)
        with app.app_context():
            shop = db.session.get(Shop, sid)
            d2, _ = record_decision(pid, shop=shop, user_id=uid)
            did = d2.id
        c.post(f"/api/decision/{did}/apply", headers={"X-CSRFToken": tok})
        r = c.post(f"/api/decision/{did}/apply", headers={"X-CSRFToken": tok})
        check("HTTP repeat returns 200 already_applied", r.status_code == 200)
        check("HTTP repeat is already_applied", r.get_json()["status"] == "already_applied")
        with app.app_context():
            check("still one new history row",
                  PriceHistory.query.filter_by(product_id=pid).count()
                  == hist_after_first + 1)
    _purge()


# ---------------------------------------------------------------------------
# Test 14 - dismissed recommendation cannot be applied
# ---------------------------------------------------------------------------
def test_dismissed_cannot_be_applied():
    print("\n--- Test 14: DISMISSED is terminal ---")
    _purge()
    uid, sid, email = _make_shop(TEST_SHOPS[0])
    pid = _make_product(sid, cost=2.0, margin=50.0, selling=10.0)
    with app.app_context():
        shop = db.session.get(Shop, sid)
        d, _ = record_decision(pid, shop=shop, user_id=uid)
        did = d.id
        dismiss_decision(did, uid, "Competitor is temporary")
        price_before = float(db.session.get(Product, pid).selling_price)
        status, _ = apply_decision(did, uid, shop=shop)
        check("apply on dismissed rejected", status == "invalid")
        check("price unchanged",
              float(db.session.get(Product, pid).selling_price) == price_before)
        check("decision remains DISMISSED",
              db.session.get(PricingRecommendationDecision, did).decision == "DISMISSED")
    _purge()


# ---------------------------------------------------------------------------
# HTTP flow: record-on-review -> apply end to end (CSRF included)
# ---------------------------------------------------------------------------
def test_http_full_flow():
    print("\n--- HTTP flow: review records decision, apply works ---")
    _purge()
    uid, sid, email = _make_shop(TEST_SHOPS[0])
    pid = _make_product(sid, cost=2.0, margin=50.0, selling=10.0)
    with app.test_client() as c:
        tok = _login(c, email)
        # Visiting the product page (owner) records the PENDING decision.
        r = c.get(f"/product/{pid}")
        check("product page 200", r.status_code == 200)
        check("page shows decision record", b"Decision record #" in r.data)
        check("page shows dismiss modal", b"dismissModal" in r.data)
        check("page shows history section header or empty",
              b"Pricing Recommendation History" in r.data)
        with app.app_context():
            n = PricingRecommendationDecision.query.filter_by(product_id=pid).count()
            check("page view recorded exactly one decision", n == 1)
        # Apply via the HTTP endpoint (CSRF token enforced globally).
        r = c.post(f"/api/product/{pid}/decision", headers={"X-CSRFToken": tok})
        check("decision endpoint 200", r.status_code == 200)
        j = r.get_json()
        check("decision endpoint returns id", "decision_id" in j)
        did = j["decision_id"]
        r = c.post(f"/api/decision/{did}/apply", headers={"X-CSRFToken": tok})
        check("HTTP apply 200", r.status_code == 200)
        check("HTTP apply message present", "message" in r.get_json())
    _purge()


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------
def main():
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn)]
    _purge()
    failed = []
    global FAILED
    for name, fn in tests:
        print(f"[{name}]")
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            FAILED += 1
            failed.append((name, exc))
            import traceback
            traceback.print_exc()
    _purge()
    total = PASSED + FAILED
    print(f"\ntest_pricing_workflow: {PASSED}/{total} checks passed")
    for name, exc in failed:
        print(f"  FAIL {name}: {type(exc).__name__}: {exc}")
    return 1 if FAILED or failed else 0


if __name__ == "__main__":
    sys.exit(main())
