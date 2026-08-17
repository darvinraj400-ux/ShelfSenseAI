# 📦 ShelfSenseAI

**Smart inventory & pricing compliance for Malaysian retail.**

ShelfSenseAI is a Flask + MySQL web application (Final Year Project) that helps a
grocery shop manage products, pricing, sales and stock — while enforcing
**PCAPA 2011-style margin-baseline compliance** and grounding suggested prices in
real market data from Malaysia's public **PriceCatcher** (KPDN) dataset.

The system is built around a **shop ownership model**: every product, sale, stock
record and employee belongs to a *Shop*, not to an individual user. Owner,
Manager and Staff accounts of one shop share the same data, while different shops
are fully isolated.

```
Shop (ABC Mini Mart)
 │
 ├── Owner   (full control)
 ├── Manager (products, pricing, stock)
 ├── Staff   (view + record sales)
 │
 └── Products
       ├── PriceHistory         ← every cost/margin change (audit trail)
       ├── Sales                ← per-unit price snapshot at sale time
       └── Inventory
             └── InventoryAdjustment  ← manual stock movements with reason
```

---

## ✨ Features

### Phase 1 — Shops, roles & products
- Registration creates a **new Shop + Owner** automatically (no public role picker).
- Owner / Manager / Staff roles with server-side authorization (`@role_required`).
- Product CRUD with **PCAPA baseline margin** locked at creation.
- **Suggested price** computed live: `cost × (1 + margin%)`.
- **PriceHistory** audit trail: every cost/margin/selling-price change is logged.
- **Compliance warning**: raising the margin above baseline *without a cost
  increase* is flagged as profiteering risk under the Price Control and
  Anti-Profiteering Act 2011.
- **PriceCatcher autocomplete**: type a product name and pick real government
  catalog items (406 items in `price_catcher_item`).

### Phase 2A — Product identity
- Identity fields: brand, category, quantity, unit (stored numerically, e.g. `1` + `kg`).
- Products stand alone — they do **not** need a PriceCatcher match to exist.

### Phase 2B — Sales & inventory
- **Sales**: record a quantity sold at the per-unit price *actually charged*
  (a snapshot — later price changes never rewrite history). Sale + stock
  decrease happen in **one transaction**; insufficient stock is rejected.
- **Inventory**: one stock row per product, adjusted by owner/manager with a
  required reason. Stock can **never go negative**.
- Products with sales history **cannot be deleted** (history is preserved).

### Phase 2C — Employee invitations
- Owners invite Manager/Staff by email with a **cryptographically secure token**
  (48-hour expiry, revocable, one-time use).
- Invitees join the owner's **existing shop** — no new shop is ever created, and
  shop/role cannot be tampered with (they come from the invitation row).
- Existing accounts belonging to another shop are never silently moved.

---

## 🛠 Tech stack

| Layer    | Technology |
|----------|------------|
| Backend  | Flask 3, Flask-SQLAlchemy, Flask-Migrate, Flask-Login, Flask-WTF |
| Database | MySQL (`mysql+pymysql`) |
| Frontend | Server-rendered Jinja2 templates + Bootstrap 5.3 (CDN), Poppins font |
| Data     | pandas + pyarrow (PriceCatcher Parquet import) |

---

## 📁 Project structure

```
app.py                  # Backend: models, routes, pricing & PCAPA logic, auth
forms.py                # WTForms definitions (server-side validation + CSRF)
seed_demo.py            # Idempotent demo data (shop, 3 users, 5 products)
import_pricecatcher.py  # Downloads KPDN PriceCatcher data into MySQL
requirements.txt        # Python dependencies
DEMO.md                 # Guided demo/presentation script
templates/              # Jinja2 pages (dashboard, sales, inventory, employees…)
migrations/             # Flask-Migrate schema history (5 revisions)
```

---

## 🚀 Getting started

**Prerequisites:** Python 3.10+, a running MySQL server, and a database created
(e.g. `shelfsense`).

```bash
# 1. Create & activate a virtual environment
python -m venv venv
# Windows:  .\venv\Scripts\activate      macOS/Linux: source venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure the database — create a .env file with:
#    DATABASE_URL=mysql+pymysql://USER:PASSWORD@localhost:3306/shelfsense
#    (SECRET_KEY is a hardcoded dev placeholder; set a real value in production)

# 4. Apply the schema (Flask-Migrate)
flask --app app.py db upgrade

# 5. (Optional) load demo data — idempotent, safe to re-run
python seed_demo.py

# 6. Run the app
python app.py          # → http://127.0.0.1:5000
```

### Loading PriceCatcher market data (optional)

`import_pricecatcher.py` downloads the monthly Parquet files from
`storage.data.gov.my`, normalizes code columns, **excludes BARANGAN SEGAR and
MAKANAN SIAP MASAK** item groups, drops orphan/duplicate rows, loads the three
tables with PKs/FKs, and builds the denormalized `price_catcher_item` table
(all 406 lookup items — having market price rows is optional, not a requirement):

```bash
python import_pricecatcher.py
```

The app itself works without this step; only the autocomplete needs
`price_catcher_item`.

---

## 👥 Demo accounts

Seeded by `seed_demo.py` (shop: **Demo Retail Shop**):

| Role    | Email            | Password   |
|---------|------------------|------------|
| Owner   | `owner@demo.my`  | `Demo1234!` |
| Manager | `manager@demo.my`| `Demo1234!` |
| Staff   | `staff@demo.my`  | `Demo1234!` |

---

## 🔐 Role permissions

| Action                 | Owner | Manager | Staff |
|------------------------|:-----:|:-------:|:-----:|
| Add / edit / delete products | ✅ | ✅ | ❌ |
| View dashboard & products     | ✅ | ✅ | ✅ |
| Record sales                  | ✅ | ✅ | ✅ |
| View sales / inventory        | ✅ | ✅ | ✅ |
| Adjust inventory (stock)      | ✅ | ✅ | ❌ |
| Invite / manage employees     | ✅ | ❌ | ❌ |

Every route enforces both **role** (`@role_required`) and **shop ownership**
(`current_user.shop_id`) — cross-shop URLs return 403, and CSRF protects every
POST.

---

## 🗄 Database tables

| Table | Purpose |
|-------|---------|
| `shop` | Ownership boundary — every record hangs off a shop |
| `user` | Accounts with `role` (owner/manager/staff) + `shop_id` |
| `product` | Products with cost, current price, target & baseline margin, identity fields |
| `price_history` | Audit trail of cost/margin/price changes |
| `sale` | Completed sales (per-unit price snapshot) |
| `inventory` | Current stock per product (never negative) |
| `inventory_adjustment` | Manual stock movements with reason |
| `shop_invitation` | Employee invitations (token, status, expiry) |
| `lookup_item` / `lookup_premise` / `price` | KPDN PriceCatcher reference + market prices |
| `price_catcher_item` | Denormalized item list with surrogate `id` (autocomplete) |

Schema history is versioned in `migrations/` (chain:
`d5ae4caca1e9` baseline → `71666be7efdd` shop → `b42d62e76c2f` identity →
`7c699f870e84` sales/inventory → `9cdb356ab630` invitations).

---

## 🧮 Pricing compliance logic

- **Suggested price** = `cost_price × (1 + target_margin / 100)` — computed live
  (a `@property`), never stored, so it can't go stale.
- **Baseline margin** is locked when a product is created. Editing a product and
  raising the margin **above baseline without a cost increase** triggers a
  compliance warning (live in the UI and enforced server-side on save).
- **PriceHistory** keeps the last 10 changes visible on the edit page so every
  margin increase can be traced against its cost justification.

---

## 🧪 Testing

During development, Phases 1–2C were verified with automated test harnesses
against the real HTTP stack and MySQL (real CSRF tokens, logged-in sessions):

- **88 checks, 88 passed** across Phase 1 (auth/shops/isolation/CSRF), 2A
  (identity/current price), 2B (sales/inventory/transactions) and 2C
  (invitations/tampering/isolation).

Those harnesses were temporary scripts (removed after each phase) — a permanent
committed `pytest` suite is on the roadmap. `seed_demo.py` is verified idempotent
(re-running adds no duplicate data).

---

## 🗺 Roadmap (later phases — not yet built)

- 📊 Analytics: daily revenue / profit dashboards, demand forecasting
- 🧠 Pricing engine + market intelligence (recommendations from PriceCatcher averages)
- 🤖 Gemini 2.5 Flash explanation layer (interprets pricing recommendations)
- 👋 Employee deactivation (soft removal preserving history)
- 📧 Real email delivery for invitations
- 🧪 Committed, reusable test suite

---

See **`DEMO.md`** for the guided presentation/demo script.
