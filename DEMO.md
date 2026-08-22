# ShelfSense AI — Demo Presentation Script

A guided walkthrough covering **frontend**, **backend**, and **database**.
Everything below is verified working against the live system.

---

## Demo accounts

| Role    | Email            | Password   |
|---------|------------------|------------|
| Owner   | `owner@demo.my`  | `Demo1234!` |
| Manager | `manager@demo.my`| `Demo1234!` |
| Staff   | `staff@demo.my`  | `Demo1234!` |

> Demo products (owner account): BERAS CAP JASMINE, TELUR AYAM GRED A,
> SUSU TEPUNG DUTCHLADY, GULA PUTIH, SUSU SEGAR KURMA, MILO 'O' PANAS.
> Re-seed anytime: `./venv/Scripts/python seed_demo.py` (safe to re-run).

---

## 1. FRONTEND (User Interface)

### 1.1 Registration — two paths
- Open `http://127.0.0.1:5000/register`
- Point out the **account-purpose selector**: *Create a new shop* OR
  *Join an existing shop* (there is no public role dropdown anywhere).
- **Create a new shop** → Shop Name + Email + Password → brand-new shop,
  registrant becomes its **Owner**.
- **Join an existing shop** → Email + Password → employee account with **no
  shop membership yet**; the Owner's invitation decides the final role.
- **Talk track:** *"There is no role picker. You either create a brand-new
  shop and become its Owner, or create an employee account that joins a shop
  only after the Owner invites you — and you explicitly accept."*

### 1.2 Login & role badge
- Log in as `owner@demo.my` / `Demo1234!`
- Show the **navbar badge** (`Owner`) next to the email — every role sees its own badge.

### 1.3 Dashboard — product table
- The table shows: Product, Brand, Category, **Size** (package size, e.g. 1 kg),
  Cost Price, **Current Price**, **Stock**, Margin %, **Suggested Price**.
- **Talk track:** *"Size is what's inside one package — Stock is how many
  sellable packages you have. Two different numbers on purpose."*
- **Talk track:** *"Two prices per product: the **current price** the shop actually
  charges, and the **suggested price** computed from cost × target margin. When they
  differ, that's a pricing decision the system can later recommend."*
- The interesting part is how we justify that margin (see section 2.2).

### 1.4 PriceCatcher autocomplete (the wow moment)
- Click **+ Add Product**.
- Type `milo` in Product Name → a **live dropdown of real government
  PriceCatcher items** appears (MILO (PAKET), MILO 3 IN 1 (PAKET) - ORIGINAL…).
  The lookup runs against `price_catcher_item` — the denormalized copy of
  `lookup_item` holding all 406 items (having market price data is optional).
- Click one → it auto-fills **Product Name + Category**.
- **Talk track:** *"We integrated Malaysia's open PriceCatcher data — the same
  dataset KPDN publishes. Clients pick real items instead of typing names."*

### 1.5 Employee invitations & notifications (Phase 2C + 2D)
- The Owner's navbar shows **Employees** (manager/staff never see it).
- **Invite** → pick a role (Manager/Staff, never Owner) → a secure 43-char
  token link is generated and shown on screen (no email service in this phase).
- **In-app notifications:** every logged-in user has a 🔔 bell in the navbar
  with an unread-count badge. Inviting an email that already has an account
  (or the invitee registering later) creates a *"Shop Invitation"*
  notification with **Accept / Reject** buttons on the Notifications page.
- Opening the token link with a NEW email creates the employee account (no
  shop membership yet) and lands the user on their notifications, where they
  **explicitly Accept** — an employee is never auto-added to a shop.
- The invitation is authoritative for shop + role; shop/role tampering is
  ignored, and an account that already belongs to another shop can never be
  moved. Invitations expire after 48h, can be revoked by the Owner, and a
  declined invitation shows as **Declined** (distinct from revoked).
- **Remove employee:** the Owner can remove a Manager/Staff member from the
  team table (Remove button). The account is kept but **unassigned**
  (shop_id → NULL, role → unassigned) — the same state a fresh "Join an
  existing shop" account starts in — so the person can be re-invited later.
  The Owner can never remove themselves or another owner; the removed
  employee gets an in-app notification about the removal.

### 1.6 Sales & Inventory (Phase 2B)
- The navbar now has **Products / Sales / Inventory**.
- **Record Sale** (`/sales/new`): pick a product, quantity, and the per-unit
  price actually charged — the price pre-fills from the product's current
  price but is editable (promotions/discounts). The sale + stock decrease
  happen in ONE transaction; insufficient stock is rejected.
- **Sales History** (`/sales`): date/time, product, qty, per-unit price,
  calculated revenue (qty × price — never stored).
- **Inventory** (`/inventory`): shows **Product Size** (e.g. 1 kg) next to
  **Current Stock** — the size-vs-stock distinction is explicit.
- **Receive Stock** (`/inventory/<id>/receive`): the obvious "how do I add
  stock?" flow — enter a positive quantity + reason, stock increases
  atomically and the movement is logged to `inventory_adjustment` (with the
  acting user).
- **Adjust** (`/inventory/<id>/adjust`): owner/manager corrections (+/-) with
  a reason — still logged, stock can never go negative. The inventory page
  also shows the last 10 **stock movements** (who/what/when).
- Changing a product's **Quantity/Unit** (package size) never touches
  inventory stock.
- **Staff** can view products/sales/inventory and record sales, but cannot
  receive/adjust stock or edit/delete products.
- Products with sales history **cannot be deleted** — history is preserved.

---

## 2. BACKEND (Logic & Rules)

### 2.1 Autocomplete API
- The dropdown is backed by `GET /autocomplete?q=milo`
  (login required) — a case-insensitive prefix search over `price_catcher_item`,
  the denormalized copy of `lookup_item` (all 406 items, surrogate `id`).
- Can show raw JSON in the browser for the backend demo.

### 2.2 Margin justification — answering the supervisor's question
The core answer to *"is the target margin justified, or arbitrary?"*:

1. **Market benchmark:** suggested prices land near real market averages.
   | Product | Suggested | Market avg |
   |---------|-----------|------------|
   | BERAS CAP JASMINE | RM26.32 | RM26.00 |
   | GULA PUTIH | RM2.99 | RM2.95 |
   (TELUR AYAM GRED A was removed from this table - it is a BARANGAN SEGAR
   item, so its market data was excluded alongside the lookup items.)

2. **PCAPA 2011 baseline:** every product locks a **baseline margin** at creation.
   - Edit *MILO 'O' PANAS*, raise margin 30% → 50% with **no cost change** →
     a **live amber warning** appears instantly, and on save the server flashes:
     *"Compliance warning: margin raised from 30% to 50% with no cost increase.
     Under the Price Control and Anti-Profiteering Act 2011, only a cost increase
     justifies a higher margin."*
   - **Talk track:** *"The law doesn't ban high margins — it bans raising your
     margin beyond your own baseline without cost justification. Our system
     enforces exactly that rule."*

3. **Price history trail:** the edit page shows a **Price History** table logging
   every cost/margin change, so increases can be traced against cost justification.

---

## 3. DATABASE (MySQL)

### 3.1 Schema overview
| Table | Purpose | Rows |
|-------|---------|------|
| `user` | accounts + `role` (owner/manager/staff) | 3 demo + existing |
| `product` | products + `baseline_margin` | 6 demo |
| `price_history` | cost/margin change log | auto |
| `lookup_item` | PriceCatcher items (autocomplete) | 406 |
| `lookup_premise` | PriceCatcher outlets | 3,887 |
| `price` | real market prices | **268,489** |
| `price_catcher_item` | denormalized copy of `lookup_item` (surrogate `id`) | 406 |
| `sale` | completed sales (per-unit price snapshot) | 0 (recorded live) |
| `inventory` | current stock per product (never negative) | 1 per product |
| `inventory_adjustment` | manual stock changes with reason | logged live |
| `shop_invitation` | employee invitations (token, status, expiry) | 0 (live) |
| `notification` | in-app notifications (invitations, unread badge) | 0 (live) |

### 3.2 Live SQL demo queries
```sql
-- 1. Market price range for an item (proves prices are real)
SELECT MIN(price), MAX(price), ROUND(AVG(price),2)
FROM price WHERE item_code = '1582';  -- BERAS CAP JASMINE: 25.90–26.00, avg 26.00

-- 2. Role column in action
SELECT email, role FROM user WHERE role != 'staff';

-- 3. Margin baseline vs current (compliance view)
SELECT name, baseline_margin, target_margin,
       CASE WHEN target_margin > baseline_margin THEN 'ABOVE BASELINE' ELSE 'ok' END AS status
FROM product;

-- 4. Price history audit trail
SELECT p.name, h.cost_price, h.target_margin, h.created_at
FROM price_history h JOIN product p ON p.id = h.product_id
ORDER BY h.created_at DESC LIMIT 10;
```

### 3.3 Data import pipeline
- `import_pricecatcher.py` downloads the monthly PriceCatcher Parquet files
  from `storage.data.gov.my`, normalizes codes (handles float artifacts like
  `123.0` vs `123`), excludes `BARANGAN SEGAR` / `MAKANAN SIAP MASAK` items
  (and their price rows), drops orphans/duplicates, loads MySQL with proper
  PKs + FKs, and builds the denormalized `price_catcher_item` table — a
  copy of all `lookup_item` rows with a surrogate `id` (having market price
  data is optional, not a membership requirement).

---

## Suggested demo flow (10 minutes)

1. **Register** → choose **Create a new shop** → Shop Name + email → new shop + Owner created
2. **Login as owner** → dashboard + role badge
3. **Add product** → type `milo` → autocomplete fills name + category
4. **Show market alignment** → suggested price ≈ market average (database query)
5. **Edit MILO** → raise margin without cost → live + server compliance warning
6. **Show price history** table (the audit trail)
7. **Staff login** → badge changes, sees their own data
8. **Record a sale** → stock-in via Inventory Adjust, then Record Sale →
   stock decreases, sale appears in Sales History with its price snapshot
9. **Wrap up** → role permission matrix is the next milestone (80%)

---

## Quick start (for demo day)

```bash
./venv/Scripts/python app.py          # starts on http://127.0.0.1:5000
./venv/Scripts/python seed_demo.py    # (re)seed demo users + products
```
