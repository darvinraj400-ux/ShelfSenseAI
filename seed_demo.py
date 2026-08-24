#!/usr/bin/env python
# seed_demo.py
# -------------------------------------------------
# Seeds demo data for the ShelfSense AI presentation:
#   • ONE shop: "Demo Retail Shop"
#   • 3 users, one per role (owner / manager / staff), all in that shop
#   • 5 realistic products based on REAL PriceCatcher items, belonging to the
#     SHOP (not to the owner) — with correct quantity/unit (package size),
#     realistic inventory stock levels, baseline margins, and a visible
#     price history trail
#
# Demonstrates the shop architecture:
#     Demo Retail Shop
#        ├── owner@demo.my     (owner)
#        ├── manager@demo.my   (manager)
#        ├── staff@demo.my     (staff)
#        └── products...       (shared by the whole shop team)
#
# Data-quality guarantees (Phase 4B):
#   • quantity is ALWAYS a positive float (the package size)
#   • unit is ALWAYS a clean text string (kg, L, pcs, etc.)
#   • inventory stock is seeded at realistic levels for the demo:
#       - one product with HIGH stock  (triggers "healthy" indicator)
#       - one product with LOW stock   (triggers low-stock warning)
#       - one product with ZERO stock  (triggers out-of-stock alert)
#       - others at normal levels
#
# Safe to re-run: existing shops/users/products are left untouched.
# Dirty demo products from earlier sessions are cleaned up.
# Run:  ./venv/Scripts/python seed_demo.py
# -------------------------------------------------

from datetime import datetime, timedelta, timezone

from app import app, db, Shop, User, Product, PriceHistory, Inventory, InventoryAdjustment

DEMO_PASSWORD = "Demo1234!"  # demo-only password, all three accounts
DEMO_SHOP_NAME = "Demo Retail Shop"

DEMO_USERS = [
    ("owner@demo.my",   "owner",   "Shop Owner — full control"),
    ("manager@demo.my", "manager", "Manager — products & pricing"),
    ("staff@demo.my",   "staff",   "Staff — view products"),
]

# ── Dirty products to clean up (added manually in earlier sessions) ──
DIRTY_PRODUCT_NAMES = [
    "100 PLUS (ORIGINAL)",
    "MILO (PAKET)",
]

# ── Clean demo products ──
# Each tuple: (name, brand, cost_price, selling_price, target_margin,
#              category, quantity, unit, stock,
#              history: [(cost, margin, days_ago), ...],
#              is_price_controlled, government_ceiling_price)
#
# quantity / unit = package size (NOT inventory stock)
#   BERAS  → 10 kg bag
#   TELUR  → 30 pcs tray
#   SUSU   → 900 g tin
#   GULA   → 1 kg pack (KPDN Barangan Kawalan — ceiling RM 2.85)
#   MINYAK → 1 kg polybag (KPDN Barangan Kawalan — ceiling RM 2.50)
#   SUSU_SEGAR → 1 L carton
#
# stock = how many sellable packages the shop currently has on the shelf.
DEMO_PRODUCTS = [
    ("BERAS CAP JASMINE (SST5%)", "JASMINE", 23.50, 26.00, 12.0, "BERAS",
     10.0, "kg", 25,
     [
         (23.00, 12.0, 40),   # cost rose → margin kept → justified
         (22.50, 12.0, 70),
     ], False, None),
    ("TELUR AYAM GRED A", "Generic", 12.00, 14.10, 18.0, "TELUR",
     30.0, "pcs", 8,
     [
         (11.50, 18.0, 30),
         (12.00, 18.0, 15),
     ], False, None),
    ("SUSU TEPUNG SEGERA DUTCHLADY (BIASA)", "Dutch Lady", 16.50, 20.00, 22.0,
     "KRIMER DAN SUSU TEPUNG", 900.0, "g", 3,
     [
         (16.50, 20.0, 25),   # margin raised 20→22 with NO cost change → flag in demo
     ], False, None),
    ("GULA PUTIH BERTAPIS KASAR (PELBAGAI JENAMA)", "Generic", 2.60, 2.85, 10.0, "GULA",
     1.0, "kg", 15,
     [
         (2.55, 10.0, 20),
     ], True, 2.85),   # KPDN Barangan Kawalan — ceiling RM 2.85
    ("MINYAK MASAK SAWIT TULEN (PELBAGAI JENAMA)", "Generic", 2.20, 2.50, 13.0, "MINYAK MASAK",
     1.0, "kg", 20,
     [
         (2.15, 13.0, 15),
     ], True, 2.50),   # KPDN Barangan Kawalan — ceiling RM 2.50
    ("SUSU SEGAR KURMA FARM FRESH", "Farm Fresh", 7.90, 9.50, 20.0, "TERSEDIA MINUM",
     1.0, "L", 50,
     [], False, None),
]


def _clean_dirty_products(shop_id):
    """Remove dirty products added in earlier sessions that have broken
    quantity/unit values.  Returns the number of products removed."""
    removed = 0
    for name in DIRTY_PRODUCT_NAMES:
        p = Product.query.filter_by(name=name, shop_id=shop_id).first()
        if p is not None:
            # Clean up child rows first (FK constraints)
            InventoryAdjustment.query.filter_by(product_id=p.id).delete()
            Inventory.query.filter_by(product_id=p.id).delete()
            PriceHistory.query.filter_by(product_id=p.id).delete()
            # Import here to avoid circular imports at module level
            from app import ProductMarketMatch
            ProductMarketMatch.query.filter_by(shop_product_id=p.id).delete()
            db.session.delete(p)
            removed += 1
            print(f"  - removed dirty product: {name}")
    if removed:
        db.session.flush()
    return removed


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

        # ---- clean up dirty products from earlier sessions (Phase 4B) ----
        _clean_dirty_products(shop.id)

        # ---- products (belong to the SHOP, shared by all three users) ----
        for (name, brand, cost, selling, margin, category,
             qty, unit, stock, hist, is_kpdn, ceiling) in DEMO_PRODUCTS:
            p = Product.query.filter_by(name=name, shop_id=shop.id).first()
            if p is None:
                p = Product(name=name, brand=brand, cost_price=cost,
                            selling_price=selling, target_margin=margin,
                            category=category, shop_id=shop.id,
                            quantity=qty, unit=unit,
                            is_price_controlled=is_kpdn,
                            government_ceiling_price=ceiling)
                p.baseline_margin = margin
                db.session.add(p)
                db.session.flush()
                print(f"  + product {name} ({qty} {unit}, baseline {margin}%)")
            else:
                # Ensure quantity/unit/selling_price are correct.
                # Older products may have dirty data (e.g. unit='100g' as a
                # combined string, or quantity=NULL from pre-Phase 2A).
                needs_patch = (
                    p.quantity is None or p.unit is None
                    or p.quantity != qty or (p.unit or '') != unit
                    or p.selling_price is None
                    or p.is_price_controlled != is_kpdn
                    or p.government_ceiling_price != ceiling
                )
                if needs_patch:
                    p.quantity = qty
                    p.unit = unit
                    if p.selling_price is None:
                        p.selling_price = selling
                    if p.cost_price != cost:
                        p.cost_price = cost
                    p.is_price_controlled = is_kpdn
                    p.government_ceiling_price = ceiling
                    print(f"  ~ product {name}: patched -> {qty} {unit}, sell {selling}, kpdn={is_kpdn}")
                else:
                    print(f"  = product {name} exists")

            # ---- inventory record ----
            inv = Inventory.query.filter_by(product_id=p.id).first()
            if inv is None:
                db.session.add(Inventory(shop_id=shop.id, product_id=p.id,
                                         current_stock=stock, minimum_stock=0))
                print(f"    * inventory initialized (stock {stock})")
            else:
                # Patch stock if it looks like default/seed data (0 or stale).
                # Real user activity (received stock) is never overwritten.
                if inv.current_stock == 0 and stock > 0:
                    inv.current_stock = stock
                    print(f"    * inventory stock updated -> {stock}")
                elif inv.current_stock != stock and stock > 0:
                    # Stale seed data from an older run - update to current target.
                    inv.current_stock = stock
                    print(f"    * inventory stock corrected -> {stock}")
                else:
                    print(f"    * inventory exists (stock {inv.current_stock})")

            # ---- price history trail (skip rows already logged) ----
            existing_dates = {h.created_at.date() for h in p.history}
            existing_snapshots = {(h.cost_price, h.target_margin) for h in p.history}
            for (h_cost, h_margin, days_ago) in hist:
                d = (datetime.now(timezone.utc) - timedelta(days=days_ago)).date()
                if d in existing_dates or (h_cost, h_margin) in existing_snapshots:
                    continue
                db.session.add(PriceHistory(
                    product_id=p.id, cost_price=h_cost,
                    target_margin=h_margin,
                    created_at=datetime.combine(d, datetime.min.time(),
                                                tzinfo=timezone.utc),
                ))
                print(f"    · history {d}: cost {h_cost}, margin {h_margin}%")

        db.session.commit()
        print("\n[DONE] Demo data ready.")
        print(f"   Shop:   {DEMO_SHOP_NAME} (id={shop.id})")
        print("   Login:  owner@demo.my / manager@demo.my / staff@demo.my")
        print(f"   Password (all): {DEMO_PASSWORD}")


if __name__ == "__main__":
    seed()
