#!/usr/bin/env python
# seed_demo.py
# -------------------------------------------------
# Seeds demo data for the ShelfSense AI presentation:
#   • ONE shop: "Demo Retail Shop"
#   • 3 users, one per role (owner / manager / staff), all in that shop
#   • realistic products based on REAL PriceCatcher items, belonging to the
#     SHOP (not to the owner) — with baseline margins and a visible
#     price history trail
#
# Demonstrates the shop architecture:
#     Demo Retail Shop
#        ├── owner@demo.my     (owner)
#        ├── manager@demo.my   (manager)
#        ├── staff@demo.my     (staff)
#        └── products...       (shared by the whole shop team)
#
# Safe to re-run: existing shops/users/products are left untouched.
# Run:  ./venv/Scripts/python seed_demo.py
# -------------------------------------------------

from datetime import datetime, timedelta, timezone

from app import app, db, Shop, User, Product, PriceHistory, Inventory

DEMO_PASSWORD = "Demo1234!"  # demo-only password, all three accounts
DEMO_SHOP_NAME = "Demo Retail Shop"

DEMO_USERS = [
    ("owner@demo.my",   "owner",   "Shop Owner — full control"),
    ("manager@demo.my", "manager", "Manager — products & pricing"),
    ("staff@demo.my",   "staff",   "Staff — view products"),
]

# name, brand, cost_price, selling_price, target_margin, category, history: [(cost, margin, days_ago), ...]
# selling_price = current price on the shelf; suggested_price = cost x (1 + margin%).
# Costs are set so suggested_price lands near the real PriceCatcher market average.
DEMO_PRODUCTS = [
    ("BERAS CAP JASMINE (SST5%)", "JASMINE", 23.50, 26.00, 12.0, "BERAS", [
        (23.00, 12.0, 40),   # cost rose → margin kept → justified
        (22.50, 12.0, 70),
    ]),
    ("TELUR AYAM GRED A", "Generic", 12.00, 14.10, 18.0, "TELUR", [
        (11.50, 18.0, 30),
        (12.00, 18.0, 15),
    ]),
    ("SUSU TEPUNG SEGERA DUTCHLADY (BIASA )", "Dutch Lady", 16.50, 20.00, 22.0,
     "KRIMER DAN SUSU TEPUNG", [
        (16.50, 20.0, 25),   # margin raised 20→22 with NO cost change → flag in demo
    ]),
    ("GULA PUTIH BERTAPIS HALUS (PELBAGAI JENAMA)", "Generic", 2.60, 3.00, 15.0, "GULA", [
        (2.55, 15.0, 20),
    ]),
    ("SUSU SEGAR KURMA FARM FRESH", "Farm Fresh", 7.90, 9.50, 20.0, "TERSEDIA MINUM", []),
]


def seed():
    with app.app_context():
        # ---- the shared shop ----
        shop = Shop.query.filter_by(name=DEMO_SHOP_NAME).first()
        if shop is None:
            shop = Shop(name=DEMO_SHOP_NAME)
            db.session.add(shop)
            db.session.flush()
            print(f"  + shop {DEMO_SHOP_NAME}")
        else:
            print(f"  = shop {DEMO_SHOP_NAME} exists (id={shop.id})")

        # ---- users (all attached to the same shop) ----
        users = {}
        for email, role, _desc in DEMO_USERS:
            u = User.query.filter_by(email=email).first()
            if u is None:
                u = User(email=email, role=role, shop_id=shop.id)
                u.set_password(DEMO_PASSWORD)
                db.session.add(u)
                db.session.flush()
                print(f"  + user {email} ({role})")
            else:
                if u.shop_id != shop.id:
                    # Reattach to the demo shop (e.g. after a schema migration).
                    u.shop_id = shop.id
                    print(f"  ~ user {email} moved to {DEMO_SHOP_NAME}")
                print(f"  = user {email} exists (role={u.role})")
            users[email] = u

        # ---- products (belong to the SHOP, shared by all three users) ----
        for (name, brand, cost, selling, margin, category, hist) in DEMO_PRODUCTS:
            p = Product.query.filter_by(name=name, shop_id=shop.id).first()
            if p is None:
                p = Product(name=name, brand=brand, cost_price=cost,
                            selling_price=selling, target_margin=margin,
                            category=category, shop_id=shop.id)
                p.baseline_margin = margin
                db.session.add(p)
                db.session.flush()
                print(f"  + product {name} (baseline {margin}%)")
            else:
                print(f"  = product {name} exists")

            # ---- inventory record (zero stock - never invent stock levels) ----
            if p.inventory is None:
                db.session.add(Inventory(shop_id=shop.id, product_id=p.id,
                                         current_stock=0, minimum_stock=0))
                print(f"    · inventory initialized (stock 0)")

            # ---- price history trail (skip rows already logged) ----
            # Idempotency: the snapshot dates are anchored to "today" (now - days_ago),
            # so re-running this seed on a DIFFERENT day used to re-add the same
            # cost/margin snapshots with shifted dates -> duplicate-ish rows
            # accumulated. Now a (cost, margin) snapshot already logged for the
            # product is NEVER logged twice (the pair is stable across days), and a
            # date already used is skipped too. Existing rows are never deleted.
            existing_dates = {h.created_at.date() for h in p.history}
            existing_snapshots = {(h.cost_price, h.target_margin) for h in p.history}
            for (h_cost, h_margin, days_ago) in hist:
                d = (datetime.now(timezone.utc) - timedelta(days=days_ago)).date()
                if d in existing_dates or (h_cost, h_margin) in existing_snapshots:
                    continue
                db.session.add(PriceHistory(
                    product_id=p.id, cost_price=h_cost,
                    target_margin=h_margin,
                    created_at=datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc),
                ))
                print(f"    · history {d}: cost {h_cost}, margin {h_margin}%")

        db.session.commit()
        print("\n[DONE] Demo data ready.")
        print(f"   Shop:   {DEMO_SHOP_NAME} (id={shop.id})")
        print("   Login:  owner@demo.my / manager@demo.my / staff@demo.my")
        print(f"   Password (all): {DEMO_PASSWORD}")


if __name__ == "__main__":
    seed()
