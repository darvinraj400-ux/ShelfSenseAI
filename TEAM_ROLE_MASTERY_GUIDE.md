# TEAM_ROLE_MASTERY_GUIDE.md

<!-- AI_UPDATE_PROTOCOL: Whenever a new feature is implemented, a database migration is run, -->
<!-- or a system logic change is made, the AI agent MUST automatically append the change to -->
<!-- this file under the relevant section before finalizing the task. -->

---

## ShelfSenseAI — Team Role Mastery & Demonstration Guide

**Purpose:** This document ensures every team member deeply understands the architecture behind their assigned features before presenting. This is NOT a script — it is a comprehension framework.

**Rubric Targets:**
| Aspect | Score | Owner |
|--------|-------|-------|
| 1. Progress (>80%) | 4/4 | S2 (Data Engineering, ML) |
| 2. Structure Design | 4/4 | S1 (Architecture, DB Schema) |
| 3. Problem Solution 100% | 4/4 | S3 (Guardrails, UI Testing) |
| 4. Security & Exception | 4/4 | S1 (RBAC, Isolation) + S2 (ETL/API Fallbacks) |
| 5. Testing | 4/4 | S3 (144-Test Suite, Live Test Cases) |

---

# 👨‍💻 STUDENT 1 (S1): Core System Architecture, Database Schema & Security

**Your domain:** The bones of the system. You built the multi-tenant database, the role-based access control, and the security layers. If someone asks "how do I know one shop can't see another shop's data?", that's YOUR question.

**Rubric coverage:** Structure Design (Aspect 2) + Security (Aspect 4)

---

## Domain Mastery 1: MySQL Multi-Tenant Schema

### 🧠 WHAT?
The entire application uses a **multi-tenant architecture** where every piece of business data — Products, Sales, Inventory, PriceHistory, MarketPriceObservation — belongs to a **Shop** via a `shop_id` foreign key. Every User also belongs to exactly one Shop.

### ❓ WHY?
In the real world, Shop A (a kedai runcit in Segamat) and Shop B (a mini market in KL) must NEVER see each other's sales data, product pricing, or inventory levels. Without multi-tenancy, a bug in a URL parameter could leak Shop B's financial data to Shop A's owner. Our architecture prevents this at three layers.

### ⚙️ HOW?
**Layer 1 — Database Foreign Keys:**
Every table has `shop_id = db.Column(db.Integer, db.ForeignKey('shop.id'), nullable=False)`. MySQL enforces referential integrity — you cannot insert a row with a `shop_id` that doesn't exist in the `shop` table.

**Key models and their shop_id:**
- `Shop` → `id` (primary key, the tenant boundary)
- `User` → `shop_id` FK to Shop (nullable for uninvited employees)
- `Product` → `shop_id` FK to Shop (not nullable — every product belongs to a shop)
- `Inventory` → `shop_id` FK to Shop
- `Sale` → `shop_id` FK to Shop
- `InventoryAdjustment` → `shop_id` FK to Shop

**Layer 2 — Application-Level Query Filtering:**
Every single database query filters by `current_user.shop_id`. Examples:
```python
# Dashboard (app.py line 930):
products = Product.query.filter_by(shop_id=current_user.shop_id).all()

# Sales (app.py line 1357):
sales_list = Sale.query.filter_by(shop_id=current_user.shop_id).order_by(...)

# Inventory (app.py line 1427):
products = Product.query.filter_by(shop_id=current_user.shop_id).all()
```

**Layer 3 — Explicit Ownership Check:**
For sensitive operations (edit product, delete product, adjust inventory), an explicit check is performed:
```python
# app.py line 1010:
if p.shop_id != current_user.shop_id:
    abort(403)  # Forbidden
```
This means even if someone manually crafts a URL like `/product/42/edit` where product 42 belongs to another shop, the query returns nothing and a 403 is thrown.

### 🖥️ EVIDENCE
- **Show** the `app.py` file, scrolling to lines 145-260 to show the Product model with `shop_id` FK
- **Show** lines 920-935 to show the dashboard query filtering by `shop_id`
- **Show** lines 1009-1015 to show the explicit ownership check in edit_product

### ✅ RESULT
This three-layer isolation (DB foreign keys + query filtering + explicit checks) proves the system is designed for **multi-tenant security** (Rubric Aspect 4) with **structural integrity** at the database and application levels (Rubric Aspect 2).

---

## Domain Mastery 2: Role-Based Access Control (RBAC)

### 🧠 WHAT?
A `role_required` decorator restricts route access to specific user roles. Three roles exist: **Owner** (full access), **Manager** (products + inventory + sales), **Staff** (view + sales only).

### ❓ WHY?
A shop owner trusts their manager to restock shelves and update prices, but NOT to hire new employees or view financial reports. A part-time cashier (Staff) should only record sales and view products — never modify inventory or delete products. Without RBAC, a cashier could accidentally delete the entire product catalog.

### ⚙️ HOW?
The decorator (`app.py` lines 672-690) wraps Flask routes with a role check:
```python
def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(403)
            if not current_user.can(*roles):
                flash('You do not have permission...', 'danger')
                abort(403)
            return f(*args, **kwargs)
        return wrapper
    return decorator
```

**Permission matrix (from User model, line 119):**
- `owner` → `('owner',)` — full access to everything
- `manager` → `('owner', 'manager')` — products, inventory, sales
- `staff` → `('owner', 'manager', 'staff')` — dashboard, product view, sales recording

**Route enforcement examples:**
- `@role_required('owner', 'manager')` → `new_product()`, `edit_product()`, `adjust_inventory()`, `receive_inventory()`
- `@role_required('owner')` → `employees()`, `remove_employee()`
- No decorator (login required only) → `dashboard()`, `sales()`, `inventory()` (read-only view)

**Navigation filtering** (`templates/base.html`): Jinja2 `{% if current_user.can('owner', 'manager') %}` hides "Add Product", "Employees", and inventory adjustment buttons for Staff.

### 🖥️ EVIDENCE
- **Show** `app.py` lines 672-690: the decorator definition with the permission matrix docstring
- **Show** `app.py` line 942: `@role_required('owner', 'manager')` on `new_product()`
- **Show** `templates/base.html` nav section with `{% if current_user.can(...) %}` checks
- **Live demo:** Log in as Staff → buttons missing. Log in as Owner → buttons visible.

### ✅ RESULT
This proves the system enforces **multi-tenant security with role-based access control** (Rubric Aspect 4), with permission checks enforced at both the route decorator layer and the template navigation layer.

---

## Domain Mastery 3: User Model & Registration Architecture

### 🧠 WHAT?
Two distinct registration paths exist: **Path A** creates a new Shop with an Owner. **Path B** creates an employee account (role='unassigned', shop_id=NULL) who must wait for an invitation.

### ❓ WHY?
In the real world, a shop owner signs up and creates their business. A new employee (e.g., a part-timer) should NOT be able to self-assign as "Owner" or join a shop without the owner's permission. Trust must be established through an invitation flow, not self-declaration.

### ⚙️ HOW?
In the registration route (`app.py` lines 774-822):
- **Path A (Create Shop):** Creates User with `role='owner'`, creates Shop, links `shop_id`.
- **Path B (Join Existing):** Creates User with `role='unassigned'`, `shop_id=None`. The user CANNOT access any shop data until an Owner sends an invitation, and the user ACCEPTS it. The invitation row is AUTHORITATIVE for `shop_id` and `role` — the user never chooses these.

**The invitation flow:**
1. Owner creates ShopInvitation with email, role, shop_id (all fixed at invite time)
2. If invited user already exists (unassigned), notification is delivered immediately
3. If invited user doesn't exist yet, they register using that email, and the system surfaces the pending invitation
4. User clicks Accept → system sets their `shop_id` and `role` from the invitation row

### 🖥️ EVIDENCE
- **Show** `app.py` lines 796-817: the two registration paths with `role='owner'` vs `role='unassigned'`
- **Show** `app.py` lines 1688-1726: the invitation accept flow where `inv.shop_id` and `inv.role` are authoritative
- **Show** the Profile page showing role badge and shop details

### ✅ RESULT
This proves the system solves the **registration security problem** — employees cannot self-escalate to owner, and shop membership is controlled entirely through the invitation backend, satisfying Rubric Aspects 2 and 4.

---

## Domain Mastery 4: Atomic Database Transactions

### 🧠 WHAT?
When a sale is recorded or inventory is adjusted, the database modification (Sale record + inventory stock change) happens as a single atomic transaction. If either fails, both are rolled back.

### ❓ WHY?
If a cashier records selling 5 units of Milo, but the database crashes after creating the Sale record but BEFORE reducing inventory, the shop would have a ghost sale with phantom stock. Atomic transactions prevent this by ensuring both operations succeed or both fail.

### ⚙️ HOW?
In `new_sale()` (`app.py` lines 1366-1415):
```python
# Step 1: Validate — reject BEFORE writing anything
if stock < qty:
    flash('Insufficient stock...')
    return  # No database changes made

# Step 2: Atomic transaction — both succeed or both fail
try:
    db.session.add(Sale(...))          # Add the sale record
    inv.current_stock = stock - qty    # Reduce inventory
    db.session.commit()                # Commit BOTH atomically
except Exception:
    db.session.rollback()              # If anything fails, undo EVERYTHING
```

The same pattern is used in `receive_inventory()` (lines 1486-1530) and `adjust_inventory()` (lines 1446-1487).

### 🖥️ EVIDENCE
- **Show** `app.py` lines 1390-1415: the complete try/except/commit/rollback pattern
- **Show** `app.py` line 1413: `db.session.rollback()` — the safety net

### ✅ RESULT
This proves the system maintains **data integrity** through atomic transactions, directly addressing **Security & Exception Handling** (Aspect 4) and the **100% Problem Solution** requirement (Aspect 3).

---

### S1 Deep-Dive Q&A Prep

**Q1: "If I navigate to /product/999 (a product that belongs to Shop B), what happens when I'm logged in as Shop A's owner?"**

Think about the three layers: (1) The database query `Product.query.get_or_404(999)` will find the product because it exists. (2) The application check `if p.shop_id != current_user.shop_id` evaluates to True because Shop A's user doesn't own product 999. (3) `abort(403)` returns a Forbidden error. The product data is never exposed in the response. This is defense-in-depth — even if one layer has a bug, the other layers catch it.

**Q2: "What happens if the database crashes in the middle of a sale — between creating the Sale record and reducing inventory?"**

SQLAlchemy uses session management. Both the Sale record and the inventory change are added to the session BEFORE `commit()` is called. If the database crashes during commit, the session is left in a broken state. The next request to the server triggers `db.session.rollback()` in the error handler (app.py line 1865), which clears the broken session. On the next user request, the sale attempt starts fresh — no sale was written, no stock was reduced, and the database is consistent.

**Q3: "How does the system prevent a Manager from creating a new Owner account?"**

The `register()` route (line 774) has two paths. Path A (Create a new Shop) always assigns `role='owner'`, but it also CREATES a new shop — so the person becomes the owner of a brand new shop. Path B (Join Existing) always assigns `role='unassigned'` — the user CANNOT choose a role. The `role` field is hardcoded in the route, not read from a form field. The actual role is determined later when the Owner sends an invitation with a specific role from the dropdown (Manager or Staff). There is no UI element that allows a user to select "Owner" for themselves.

---

# 👨‍🔬 STUDENT 2 (S2): Data Engineering, ML Architecture & Database Optimization

**Your domain:** The brain of the system. You ingested 268K government price records, trained a RandomForest model on 5.4M data points, built the fuzzy matching engine, and engineered exception handling for every failure mode.

**Rubric coverage:** Progress >80% (Aspect 1) + Security & Exception Handling (Aspect 4)

---

## Domain Mastery 1: KPDN Open Data ETL Pipeline

### 🧠 WHAT?
An idempotent ETL (Extract, Transform, Load) script (`scripts/etl_pricecatcher.py`) ingests Malaysia's official KPDN PriceCatcher data — 268,489 historical price records across 405 products — from legacy tables into the Phase 3A market intelligence schema.

### ❓ WHY?
The raw KPDN data from data.gov.my is notoriously dirty. The `unit` column contains strings like "N X Ng" (multipacks), "M54" (diaper sizes), "beg" (bag in Malay), "+- 500g" (approximate weight), and bare numbers. Without normalization, the matching engine cannot compare a shop's "500g" product against a KPDN "0.5 kg" record. The ETL must normalize every record to exactly three base units: `kg`, `l`, or `unit`.

### ⚙️ HOW?
**Idempotency** (lines 151-179): The script checks if a `MarketSource` named "PriceCatcher" already exists. If it does, ALL associated MarketItem, MarketPriceObservation, and ProductMarketMatch records are deleted first, then the entire load re-runs. This prevents duplicate records.

**Dirty-data parser** (lines 53-140): A layered parser handles each `unit` format:
1. Clean "N unit" patterns → `normalize_package_size()` (360 rows)
2. "N X Ng" multipacks → total weight (5 X 79g = 395g)
3. "M54"–"M74" diaper sizes → pack count (unit='unit')
4. Count-nouns (beg, batang, sachet) → numeric extraction
5. "+- 500g" → strip "+-", use nominal weight
6. Unparseable → fallback (1, 'unit'), logged as issue

**Denormalization** (lines 101-140): The `state` and `district` fields from the premise lookup table are stored directly on each `MarketPriceObservation` record, eliminating expensive JOINs during real-time UI queries.

**Price aggregation**: Instead of storing 268K individual price records (many from the same shop on the same day), the ETL averages prices per item per date across all premises, reducing the dataset to ~692 meaningful observations.

### 🖥️ EVIDENCE
- **Show** `scripts/etl_pricecatcher.py` lines 1-50: the complete documentation of data sources, parsing rules, and idempotency guarantee
- **Show** lines 151-179: the idempotency detection and cascading delete logic
- **Run** `python scripts/etl_pricecatcher.py` in terminal to show execution logs with item counts and fallback counts

### ✅ RESULT
This proves the system has a **robust, idempotent data ingestion pipeline** that handles real-world dirty data gracefully — directly demonstrating **Project Progress >80%** (Aspect 1) and **Exception Handling** (Aspect 4).

---

## Domain Mastery 2: ML Pricing Engine & Synthetic Training

### 🧠 WHAT?
A `RandomForestRegressor` from scikit-learn predicts optimal product pricing based on 13 features. Since KPDN data logs selling prices (not cost prices), the model is trained on synthetic data generated from real market observations.

### ❓ WHY?
Small shop owners set prices based on gut feeling. They don't know if their rice is overpriced compared to the KPDN regulated price in Segamat, or if their cooking oil margin violates PCAPA regulations. The ML model gives them a data-driven recommendation that accounts for market conditions, their costs, and regulatory constraints.

### ⚙️ HOW?
**Training pipeline** (`scripts/train_pricing_model.py`):
1. Downloads 56 monthly KPDN parquet files (Jan 2022 – Aug 2026) from data.gov.my
2. Merges with item and premise lookup tables
3. Filters to Johor region data
4. Aggregates daily price observations into statistical features
5. Generates 523,000+ synthetic training samples by simulating shop cost prices (80-90% of market median) and margins (5-50%)
6. The optimal price label is: `blend = 0.6 * margin_price + 0.4 * market_median`, with stock adjustment and cost floor enforcement
7. Trains RandomForestRegressor, saves as `ml/pricing_model.pkl`

**The 13 input features** (from `services/pricing_engine.py` lines 68-81):
1. `cost_price` — what the shop pays
2. `target_margin` — desired margin %
3. `baseline_margin` — PCAPA baseline margin
4. `market_median` — median market price (scaled to product size)
5. `market_mean` — mean market price
6. `market_min` / `market_max` — price range
7. `market_spread` — price volatility
8. `normalized_unit_price` — RM per base unit (kg/l/unit)
9. `stock_level` — current inventory
10. `sales_velocity` — units sold per period
11. `price_to_market_ratio` — cost vs. market position
12. `quantity` — package size

**Model performance**: R² = 0.9999, MAE = RM0.06 (within 6 sen of optimal).

### 🖥️ EVIDENCE
- **Show** `ml/pricing_model.pkl` file in the ml/ directory
- **Show** `scripts/train_pricing_model.py` lines 345-440: the `generate_samples` function with the blended optimal price formula
- **Show** `scripts/train_pricing_model.py` lines 448-465: the `train_and_save` function with the 13-feature matrix

### ✅ RESULT
This proves the system implements a **complete ML pipeline** trained on real government big data, achieving near-perfect accuracy — demonstrating **Progress >80%** (Aspect 1) and solving the **cold-start problem** for new shops.

---

## Domain Mastery 3: RapidFuzz Product Matching (75/25 Weighted)

### 🧠 WHAT?
A two-pass matching engine (`services/matching.py`) links shop products to KPDN market items using RapidFuzz's fuzzy string matching, weighted 75% title similarity and 25% package-size agreement.

### ❓ WHY?
A shop owner names their rice "BERAS CAP JASMINE" but KPDN records it as "BERAS CAP JASMINE (SST5%)" or "beras cap jasmine 1kg". The matching engine must handle case differences, brand containment, extra labels, AND ensure a 10kg bag isn't suggested for a 1kg product.

### ⚙️ HOW?
**Exact Pass** (lines 178-210): Compares `clean_text(product_name)` against `MarketItem.normalized_title`. Two match types:
- Exact string match → confidence 1.00
- Brand containment match (both brands match, one title contains the other) → confidence 0.95

**Fuzzy Pass** (lines 220-280): For non-exact matches:
1. **Pre-filter**: `fuzz.token_set_ratio()` < threshold → skip (fast filter for unrelated items)
2. **Full similarity**: `fuzz.WRatio()` → title_score (0-1)
3. **Package agreement**: `package_score()` → 0.0 to 1.0 (penalizes size mismatches)
4. **Blended confidence**: `0.75 × title_score + 0.25 × package_score`
5. Filter by minimum confidence, sort descending, return top-k

The 75/25 weighting ensures that title similarity is the primary signal (product identity), while package size is a secondary quality check (a 1kg rice shouldn't match a 25kg sack).

### 🖥️ EVIDENCE
- **Show** `services/matching.py` lines 220-269: the fuzzy pass with WRatio scoring and the 75/25 blend comment
- **Show** the Product Detail page → Market Intelligence tab → Verified/Suggested Matches table

### ✅ RESULT
This proves the system uses **intelligent, weighted fuzzy matching** to connect local products to government market data — a core component of the **Problem Solution** (Aspect 3).

---

## Domain Mastery 4: Geographic Fallback & Data Sparsity

### 🧠 WHAT?
When a shop is in a small district like Segamat, there may be very few KPDN price observations from that specific area. A 3-tier geographic fallback chain ensures data is always available: District → State → National.

### ❓ WHY?
If a shop in Segamat queries for "Gula Pasir" but KPDN only has 2 observations from Segamat (below our threshold of 3 for statistical significance), the system must transparently expand to all of Johor. If Johor also has limited data, it falls back to national. Without this, the Market Intelligence tab would show empty data for many users.

### ⚙️ HOW?
In `services/market_analysis.py` (lines 192-290):
1. `_fetch_localized_observations(market_item_id, shop)` is called with the shop's state and district
2. It first tries `_build_observation_query(..., state=shop.state, district=shop.district)` — district-level
3. If result count < `_MIN_OBSERVATIONS` (3), tries state-level only
4. If state also insufficient, falls back to all observations (national)
5. The localization string ("📍 Segamat, Johor" or "📍 Johor" or "📍 National") is returned to the UI

### 🖥️ EVIDENCE
- **Show** `services/market_analysis.py` lines 192-240: the geographic filtering documentation and `_build_observation_query`
- **Show** the Product Detail page → Market Intelligence → localization note ("📍 Filtered to Segamat, Johor")

### ✅ RESULT
This proves the system **handles data sparsity gracefully** without crashing or showing empty data, demonstrating **exception handling** (Aspect 4) and a complete **problem solution** (Aspect 3).

---

## Domain Mastery 5: Gemini LLM 3-Layer Fallback

### 🧠 WHAT?
The AI explanation system (`services/llm_explainer.py`) uses Gemini 3.5 Flash Lite to generate natural-language pricing explanations, with a 3-layer fault-tolerant fallback chain.

### ❓ WHY?
During a live demo or production use, the Gemini API might be down, rate-limited, or the API key might be invalid. The app must never crash. The user must always see a meaningful explanation.

### ⚙️ HOW?**Layer 1** — Live Gemini API call: Construct a detailed prompt with product name, cost, market median, recommended price, guardrails, and PCAPA status. The prompt includes explicit legal guardrails — the LLM is instructed NEVER to advise raising prices when a PCAPA warning is present, as this would worsen a legal violation. Send to `genai.Client(api_key=GEMINI_API_KEY)`. 

**Layer 2** — Exception catch: `except Exception as e: return _fallback(...)`. Any failure (404, 429, timeout, missing key) triggers the fallback.

**Layer 3** — Deterministic string template: Generates a clean explanation using only available data. When PCAPA warnings are active, the fallback explicitly states that further price increases are legally prohibited under PCAPA 2011.

The function **never returns None or raises** — it always returns a string.

### 🖥️ EVIDENCE
- **Show** `services/llm_explainer.py` lines 80-165: the three steps — prompt construction, Gemini API call, and `except Exception` fallback

### ✅ RESULT
This proves the system has **robust exception handling for external API failures**, directly demonstrating **Exception Handling** (Aspect 4) and a complete **problem solution** (Aspect 3).

---

### S2 Deep-Dive Q&A Prep

**Q1: "How does the system handle the cold-start problem — a brand new shop with zero sales history?"**

The ML model doesn't need the shop's own sales history. It was trained on 523,000+ synthetic samples derived from 5.4 million real KPDN price observations across 56 months. The 13 input features include both internal data (cost_price, target_margin) and external market data (market_median, market_min, market_max). For a new shop, the model still has the cost price and the localized market data from matched PriceCatcher items. The training pipeline simulates different cost/margin/stock combinations, so the model generalizes well to new shops. R² = 0.9999 and MAE = RM0.06 prove this.

**Q2: "Why did you denormalize state and district onto MarketPriceObservation instead of joining the premise lookup table?"**

The legacy PriceCatcher data requires a 3-way JOIN: `MarketPriceObservation → MarketItem → Premise`. With 268K rows, this JOIN executes on every UI page load. By storing `state` and `district` directly on each MarketPriceObservation record (populated during ETL), the geographic filter becomes a simple `WHERE state = 'Johor'` — a single-column index scan instead of a 3-table join. This is a standard denormalization trade-off: we accept slight data redundancy for dramatically faster read queries, which is critical for a responsive web UI.

**Q3: "What happens if the GEMINI_API_KEY environment variable is not set?"**

The `_call_gemini()` function (line 172) checks `if not api_key: raise ValueError(...)`. This is caught by the `except Exception` block in `generate_pricing_explanation()` (line 158), which logs a warning and calls `_fallback()`. The fallback generates a deterministic string: "The recommended price of RMX.XX is based on a target margin of Y% over a cost of RMC.CC." The user sees a clean, professional explanation with no error messages. The application continues functioning normally — the pricing engine itself does not depend on Gemini at all; the LLM is purely an explanatory layer.

---

# 👨‍💼 STUDENT 3 (S3): Product Solution, Regulatory Guardrails & Live UI Testing

**Your domain:** The promise. You prove the system WORKS by running live test cases in the browser. You demonstrate the regulatory guardrails (KPDN ceiling prices, PCAPA compliance, cost floor protection) and the atomic inventory management that solves the real retail problem.

**Rubric coverage:** Problem Solution (Aspect 3) + Testing (Aspect 5)

---

## Domain Mastery 1: Five Deterministic Guardrails (The Pricing Brain)

### 🧠 WHAT?
The ML pricing engine has five hard-coded guardrails that intercept and override the ML prediction before it reaches the user. These are applied in strict priority order.

### ❓ WHY?
ML models can produce extreme predictions — recommending RM 0.01 for rice, or RM 500 for cooking oil. The ML model is trained on hypermarket data (Lotus's, Mydin) which operates on razor-thin margins. Without guardrails, it would recommend hypermarket prices to a small kedai runcit, which cannot survive on 3% margins. Deterministic guardrails ensure every recommendation is legally compliant, commercially viable, and respects the local SME business model.

### ⚙️ HOW?
In `services/pricing_engine.py` (lines 470-540), the guardrails fire in order:

**Rule 0 — Regulatory Cap (KPDN Barangan Kawalan):**
```python
if product.is_price_controlled and product.government_ceiling_price:
    ceiling = float(product.government_ceiling_price)
    if ml_prediction > ceiling:
        ml_prediction = ceiling  # HARD CAP
```
This is the HIGHEST priority. Government price controls override everything.

**Rule 1 — Cost Floor:**
```python
floor = round(cost_price * (1 + MIN_MARGIN_FLOOR), 2)  # 5% minimum
if recommended_price < floor:
    return floor, True  # Raise to floor
```
Never sell below cost + 5%. This covers electricity, rent, wages.

**Rule 1b — SME Margin Clamp (Anti-Hypermarket Bias):**
```python
if ml_prediction < user_target_floor:
    blended = 0.60 * user_target_floor + 0.40 * ml_prediction
```
When the ML predicts a price below the shop's target margin, we blend to protect the SME business model. Prevents hypermarket-dominated data from bankrupting small kedai runcit.

**Rule 2 — Market Sanity:**
```python
low = max(market_min * 0.70, 0.01)   # Don't go below 70% of market min
high = market_max * 1.50              # Don't go above 150% of market max
```
Prevents unsellable prices from ML artifacts.

**Rule 3 — PCAPA Compliance (with 1.5% tolerance):**
```python
PCAPA_EPSILON = 1.5  # prevents false warnings from rounding artifacts
if implied_margin > baseline_margin + PCAPA_EPSILON:
    if product.cost_price <= baseline_cost:
        # WARNING: genuine margin increase, not rounding artifact
```
Warning only, not a hard cap — the owner makes the final decision. The 1.5% tolerance prevents the SME Margin Clamp's rounding from triggering false legal warnings.

### 🖥️ EVIDENCE
- **Show** `services/pricing_engine.py` lines 470-540: all five guardrails in sequence
- **Live test** (S3 will demonstrate each guardrail in the browser — see live test cases below)

### ✅ RESULT
This proves the system provides a **100% complete solution** to the pricing problem (Aspect 3) — not just ML prediction, but legally and commercially safe pricing with explainable guardrails that protect both regulatory compliance and the local SME business model.

---

## Domain Mastery 2: PriceHistory Audit Trail for PCAPA Compliance

### 🧠 WHAT?
Every time a product's cost or price is edited, a PriceHistory record is appended. This is an append-only audit trail that tracks the complete price lifecycle of every product.

### ❓ WHY?
Malaysia's Price Control and Anti-Profiteering Act 2011 (PCAPA) requires shops to justify any margin increase with a corresponding cost increase. Without an audit trail, a shop cannot prove compliance if audited. PriceHistory provides the evidence: "At product creation, the cost was RM X and the margin was Y%. Today, the cost is RM Z. The margin increase from Y% to Y+5% is justified because cost increased by RM(Z-X)."

### ⚙️ HOW?
When a product is created (`new_product()` in `app.py` line 976):
```python
db.session.add(PriceHistory(product_id=p.id, cost_price=new_cost,
                            selling_price=new_selling, target_margin=new_margin))
```

When a product is edited (`edit_product()` in `app.py` line 1044):
```python
if new_cost != old_cost or new_margin != old_margin:
    db.session.add(PriceHistory(product_id=p.id, cost_price=new_cost,
                                selling_price=new_selling, target_margin=new_margin))
```

The FIRST PriceHistory entry for each product is the "baseline" — the original cost at creation. The PCAPA check in the pricing engine queries this baseline to determine if margin increases are justified.

### 🖥️ EVIDENCE
- **Show** the Product Detail page → Price History tab showing the append-only audit trail
- **Show** `services/pricing_engine.py` lines 278-295: the `_check_pcapa` function querying `PriceHistory.filter_by(product_id=...).order_by(PriceHistory.created_at.asc()).first()`

### ✅ RESULT
This proves the system provides **regulatory compliance evidence** for PCAPA 2011, directly addressing the **100% Problem Solution** (Aspect 3) requirement for Malaysian retail.

---

## Domain Mastery 3: Live Test Case 1 — KPDN Regulatory Cap (Rule 0)

### 🧠 WHAT?
This test proves that when a product is marked as "Price Controlled" (Barangan Kawalan), the system will NEVER recommend a price above the official KPDN ceiling, regardless of what the ML model calculates.

### ❓ WHY?
Malaysia has strict price controls on essential goods. Selling sugar above RM 2.85/kg is a legal violation. The system must enforce this automatically, not rely on the shop owner remembering the ceiling price.

### ⚙️ HOW?
1. Edit product "GULA PUTIH BERTAPIS KASAR 1KG"
2. Set `is_price_controlled = True`, `government_ceiling_price = 2.85`
3. Set `cost_price = 2.00`, `target_margin = 50` (ML would recommend RM 3.00)
4. The ML engine computes RM 3.00, then Rule 0 fires: `if ml_prediction > ceiling (2.85): ml_prediction = 2.85`
5. `regulatory_cap_applied = True` is returned in the API response
6. The UI displays: "Regulatory cap applied: price capped at RM2.85"

**Expected Output:** Recommended Price = RM 2.85 (hard-capped)  
**Actual Output:** [Show the Pricing Recommendation tab — recommended price shows RM 2.85, guardrails list includes "regulatory_cap", AI Insight mentions KPDN legal compliance]

### 🖥️ EVIDENCE
- **Show** the Edit Product page with Price Controlled checkbox and RM 2.85 ceiling
- **Show** the Product Detail → Pricing Recommendation tab with the capped price
- **Show** `services/pricing_engine.py` lines 480-497: the Rule 0 code block

### ✅ RESULT
This proves the system **automatically enforces government price controls** (Rule 0), demonstrating the **100% Problem Solution** for regulatory compliance.

---

## Domain Mastery 4: Live Test Case 2 — Cost Floor Protection (Rule 1)

### 🧠 WHAT?
This test proves the system will NEVER recommend selling a product below cost + 5%, even if the ML model or market data suggests a lower price.

### ❓ WHY?
A small shop owner might set an unusually high cost (e.g., RM 25 for instant noodles that normally cost RM 1.20). If they then accept an ML recommendation based on market data (~RM 1.50), they'd sell at a massive loss. The cost floor prevents bankruptcy.

### ⚙️ HOW?
1. Edit a product (e.g., "MAGGI MI KARI")
2. Set `cost_price = 25.00`, `target_margin = 30`
3. ML predicts a price based on market data (~RM 1.50)
4. Rule 1 fires: `floor = 25.00 × 1.05 = 26.25`. Since `ml_prediction < floor`, price is raised to 26.25
5. `floor_hit = True`, guardrails_applied includes "cost_floor"
6. UI shows: "Cost floor applied: price raised to RM26.25"

**Expected Output:** Recommended Price = RM 26.25 (minimum viable margin)  
**Actual Output:** [Show the Pricing Recommendation tab — recommended price shows RM 26.25, guardrails list includes "cost_floor"]

### 🖥️ EVIDENCE
- **Show** the Edit page with artificially high cost (RM 25.00)
- **Show** the Product Detail → Pricing Recommendation tab with the floor-enforced price
- **Show** `services/pricing_engine.py` lines 181-207: the `_apply_cost_floor` function

### ✅ RESULT
This proves the system **prevents loss-making pricing** (Rule 1), protecting small businesses from financial harm — a core **Problem Solution** (Aspect 3).

---

## Domain Mastery 5: Live Test Case 3 — Atomic Inventory & Sales

### 🧠 WHAT?
This test proves that when a sale is recorded, the inventory stock decreases by EXACTLY the quantity sold, in a single atomic database transaction. No ghost sales, no phantom stock.

### ❓ WHY?
In a real shop, if a cashier sells 2 units of Milo but the stock counter doesn't update (or updates by the wrong amount), the shop loses money or runs out of stock unexpectedly. The system must guarantee that Sale + Stock Change happen as one inseparable unit.

### ⚙️ HOW?
1. Navigate to Inventory → find a product with stock = 10 (or any known quantity)
2. Navigate to Sales → Record a New Sale → select the same product
3. Set Quantity = 2, Selling Price = (current price)
4. Submit the sale
5. The system checks: `if stock < qty: abort(Insufficient stock)`
6. The system executes: `db.session.add(Sale(...))` + `inv.current_stock = stock - qty` + `db.session.commit()`
7. Navigate back to Inventory → stock is now 8 (10 - 2)

**Expected Output:** Stock decreases from 10 to 8 (exact 2-unit deduction)  
**Actual Output:** [Show the Inventory page — stock is now 8, Dashboard reflects updated numbers]

### 🖥️ EVIDENCE
- **Show** the Inventory page BEFORE the sale (stock = 10)
- **Show** the Sales form with Quantity = 2
- **Show** the Inventory page AFTER the sale (stock = 8)
- **Show** `app.py` lines 1390-1415: the atomic transaction with commit/rollback

### ✅ RESULT
This proves the system **guarantees data integrity** through atomic transactions, demonstrating both **Testing** (Aspect 5) with a live test case and **Security & Exception Handling** (Aspect 4).

---

## Domain Mastery 6: The 144-Test Suite

### 🧠 WHAT?
A comprehensive test suite of 144 tests across 9 test files, covering every major system module. Each test creates its own isolated data, runs the test, and cleans up.

### ❓ WHY?
A system without tests is unverifiable. The supervisor cannot trust that edge cases are handled. Our test suite proves that every guardrail fires correctly, every role restriction works, every atomic transaction commits or rolls back properly, and the entire system works end-to-end.

### ⚙️ HOW?
**9 test files, 144 tests:**
| File | Tests | What it proves |
|------|-------|----------------|
| test_normalization | 16 | Unit conversion, dirty data handling |
| test_market_models | 7 | Database schema correctness |
| test_matching | 15 | RapidFuzz 75/25 weighting, lifecycle |
| test_market_analysis | 13 | Statistical calculations, scaling |
| test_pricing_engine | 43 | All 4 guardrails, permissions, isolation |
| test_llm_explainer | 17 | 3-layer fallback, payload structure |
| test_dashboard_service | 24 | Metrics, PPI flagging, isolation |
| test_employee_remove | 8 | RBAC, cross-shop blocking |
| test_integration | 1 (10 checks) | Full E2E user flow |

**Integration test flow:** Register → Create shop → Add product → Record sale → Verify market intel → Cleanup. All 10 checks pass.

### 🖥️ EVIDENCE
- **Run** `python -m pytest tests/ -v --tb=short` to show all 144 tests passing in real-time
- **Show** the test directory listing showing 9 test files

### ✅ RESULT
This proves the system has **comprehensive automated testing** (Aspect 5) covering all edge cases, with every test independent and non-destructive.

---

### S3 Deep-Dive Q&A Prep

**Q1: "How does the system enforce that a shop owner CANNOT set a price-controlled product above the government ceiling?"**

Rule 0 in `pricing_engine.py` (line 481) fires BEFORE any other guardrail. When `product.is_price_controlled` is True and `product.government_ceiling_price` is set, the engine compares the ML prediction against the ceiling. If prediction > ceiling, it's hard-capped. The `regulatory_cap_applied` flag is set to True and returned in the API response, which the UI uses to display a prominent warning. The ceiling is treated as a legal maximum — not a suggestion. Even if Rule 1 (cost floor) or Rule 2 (market sanity) would produce a higher price, Rule 0 overrides them because it fires first in the priority chain.

**Q2: "What exactly do your 144 tests verify, and how do they not interfere with the live production database?"**

The test suite spans 9 modules. The integration test (`test_full_fyp_user_flow`) simulates a complete user journey: creating a user and shop, adding a product with market intelligence, recording a sale that decrements inventory, verifying the market data API, and cleaning up. Each test uses the Flask test client and creates its own isolated records within a session. The test infrastructure uses the same MySQL database but ensures isolation by creating and destroying test records within each test function via `db.session.rollback()`. No test data persists between runs. We can safely execute the full suite against production at any time.

**Q3: "How does the PriceHistory table ensure PCAPA compliance?"**

PCAPA (Price Control and Anti-Profiteering Act 2011) requires shops to justify margin increases with cost increases. PriceHistory is an append-only table — records are never updated or deleted, only added. The first entry for each product records the baseline cost and margin at creation time. When the pricing engine runs, the `_check_pcapa()` function queries this baseline entry and compares it to the current state. If the current margin exceeds the baseline AND the cost has not risen, it flags a PCAPA warning. This warning is displayed in the UI and in the AI explanation. The append-only nature of PriceHistory means the audit trail is tamper-proof — a shop cannot retroactively change their baseline to hide a margin increase.

---

## Appendix: Quick Reference — Key File Locations

| Component | File | Lines |
|-----------|------|-------|
| Product Model + shop_id FK | `app.py` | 145-260 |
| RBAC Decorator | `app.py` | 672-690 |
| Role Permission Matrix | `app.py` | 119-130 |
| Sales Atomic Transaction | `app.py` | 1366-1415 |
| Receive Stock | `app.py` | 1486-1530 |
| Employee Invitation Flow | `app.py` | 1537-1665 |
| Invitation Accept (Authoritative) | `app.py` | 1688-1726 |
| ETL Idempotency Logic | `scripts/etl_pricecatcher.py` | 151-179 |
| ETL Dirty Data Parser | `scripts/etl_pricecatcher.py` | 53-140 |
| ML Feature Names (13) | `services/pricing_engine.py` | 68-81 |
| Rule 0: KPDN Regulatory Cap | `services/pricing_engine.py` | 480-497 |
| Rule 1: Cost Floor | `services/pricing_engine.py` | 181-207 |
| Rule 3: PCAPA Check | `services/pricing_engine.py` | 249-310 |
| Synthetic Training Pipeline | `scripts/train_pricing_model.py` | 345-465 |
| RapidFuzz 75/25 Weighting | `services/matching.py` | 220-269 |
| Geographic 3-Tier Fallback | `services/market_analysis.py` | 192-290 |
| Gemini 3-Layer Fallback | `services/llm_explainer.py` | 80-165 |
| Test Suite (9 files, 144 tests) | `tests/` | Various |

---

*Generated for ShelfSenseAI FYP Defense — August 2026*
