<!--
AI_UPDATE_PROTOCOL:
Whenever a new feature is implemented, a database migration is run, or a system
logic change is made, the AI agent MUST automatically append the change to this
file under the "Recent Updates" section at the bottom before finalizing the task.
This ensures the development history remains a living, accurate record of the
project's evolution. Do NOT modify previous phase sections — only append new
entries to "Recent Updates" and update the "Last Updated" timestamp.
-->

# ShelfSenseAI — Development Journey

**Project:** ShelfSenseAI — Intelligent Retail Pricing Decision Support System
**Type:** Final Year Project (FYP)
**Team:** 3 Members (Backend, Database, Frontend)
**Repository:** [github.com/darvinraj400-ux/ShelfSenseAI](https://github.com/darvinraj400-ux/ShelfSenseAI)
**Last Updated:** 2026-09-09

---

## Table of Contents

1. [Project Overview & Architectural Paradigm](#1-project-overview--architectural-paradigm)
2. [Phase 1: Core Foundation & Operations](#2-phase-1-core-foundation--operations)
3. [Phase 2: Market Intelligence & ETL](#3-phase-2-market-intelligence--etl)
4. [Phase 3: Machine Learning & Generative AI](#4-phase-3-machine-learning--generative-ai)
5. [Phase 4: Hardening & Legal Compliance](#5-phase-4-hardening--legal-compliance)
6. [Phase 5 & Future Roadmap](#6-phase-5--future-roadmap)
7. [Recent Updates](#7-recent-updates)

---

## 1. Project Overview & Architectural Paradigm

### 1.1 Purpose

ShelfSenseAI is a web-based **Decision Support System (DSS)** designed for small and medium retail businesses (kedai runcit) in Malaysia. It helps shop owners understand how their product pricing compares to official government market data and provides AI-assisted pricing recommendations that comply with Malaysian regulations.

### 1.2 Three-Tier Architecture

The system follows a **decoupled three-tier architecture**, separating concerns into distinct operational layers:

```
┌─────────────────────────────────────────────────┐
│  TIER 3: AI/ML ENGINE                           │
│  Random Forest Regressor (pricing prediction)   │
│  Gemini 3.5 Flash Lite (natural-language        │
│  explanations with 3-layer fallback)            │
├─────────────────────────────────────────────────┤
│  TIER 2: MARKET INTELLIGENCE                    │
│  KPDN PriceCatcher ETL pipeline                 │
│  RapidFuzz exact/fuzzy matching (75/25 blend)   │
│  Statistical analysis (PPI, median, spread)     │
│  Regulatory guardrails (KPDN ceiling caps)      │
├─────────────────────────────────────────────────┤
│  TIER 1: CORE RETAIL MANAGEMENT                 │
│  Authentication (Flask-Login + bcrypt)           │
│  RBAC (Owner / Manager / Staff / Unassigned)    │
│  Multi-tenant shop isolation (shop_id)          │
│  Product management + Inventory + Sales          │
│  Price history audit trail                       │
│  Employee invitations + in-app notifications     │
└─────────────────────────────────────────────────┘
```

Each tier is designed to function independently: the core retail layer operates without market data, the market intelligence layer processes data without requiring the ML engine, and the AI layer gracefully degrades when external APIs are unavailable.

### 1.3 Multi-Tenant Shop Isolation

Every data entity (Product, Sale, Inventory, PriceHistory, MarketMatch) is scoped to a `shop_id`. The system enforces strict data isolation through:

- **Database-level:** Foreign keys referencing `shop.id` on all tenant-scoped tables.
- **Application-level:** Every route queries with `shop_id=current_user.shop_id` as a mandatory filter.
- **Authorization-level:** Role-based decorators (`@role_required`) restrict write operations.

A user from Shop A can never access, modify, or even see Shop B's data through any route, API endpoint, or URL manipulation.

### 1.4 Technology Stack

| Layer | Technology | Purpose |
|---|---|---|
| Backend | Python 3, Flask, SQLAlchemy, MySQL | Web framework, ORM, database |
| ML/AI | Scikit-learn (RandomForestRegressor), Google GenAI (Gemini 3.5 Flash Lite) | Price prediction, natural-language explanations |
| Data Matching | RapidFuzz (WRatio, token_set_ratio) | Fuzzy product-to-market matching |
| Data Ingestion | Pandas, Parquet, PyMySQL | Government open-data ETL pipeline |
| Frontend | HTML5, Bootstrap 5, Chart.js, Vanilla JS | UI with AJAX-driven intelligence tabs |
| Testing | Pytest (144 tests, 9 suites) | Unit, integration, and E2E validation |
| Auth/Security | Flask-Login, Flask-WTF (CSRF), Werkzeug (bcrypt) | Session management, form protection, password hashing |

### 1.5 Key Architectural Decisions

1. **Synchronous ML inference:** With only ~405 market items, async overhead is unnecessary. RapidFuzz and RandomForest predictions execute synchronously.
2. **Guardrails override ML:** The ML model proposes; deterministic rules dispose. Four sequential guardrails ensure regulatory compliance, cost protection, market sanity, and PCAPA compliance.
3. **3-layer LLM fallback:** The Gemini API call is wrapped in a try/except that falls back to a deterministic string template, guaranteeing the UI never crashes.
4. **Idempotent data pipelines:** Both `import_pricecatcher.py` and `scripts/etl_pricecatcher.py` are safe to re-run without creating duplicates.
5. **Package Size ≠ Inventory Stock:** `Product.quantity/unit` describes the product (e.g., "1 kg Milo"); `Inventory.current_stock` describes the shelf count (e.g., "20 packages").
6. **Geographic Market Localization:** Shop location (state/district) drives a 3-tier fallback chain (district → state → national) for market data filtering, ensuring hyper-local pricing.
7. **Explainable AI Transparency:** Raw competitor observations from KPDN open data are exposed in the UI, giving users full visibility into the data driving AI recommendations.

---

## 2. Phase 1: Core Foundation & Operations

**Timeline:** August 6–12, 2026
**Commits:** `23adf9d` through `e676040`
**Goal:** Establish the core retail management platform with authentication, multi-tenant isolation, and atomic inventory/sales workflows.

### 2.1 Authentication & User Management

- **Framework:** Flask-Login with session-based authentication.
- **Password Security:** Werkzeug `generate_password_hash` (salted PBKDF2) — passwords are never stored in plain text.
- **Model:** `User` with fields: id, email, password_hash, role, shop_id.
- **Four Roles:**
  - `owner` — Full shop administration (CRUD products, manage employees, apply prices)
  - `manager` — Operational management (adjust inventory, record sales, view intelligence)
  - `staff` — Day-to-day operations (record sales, view products)
  - `unassigned` — Registered employee awaiting owner invitation

### 2.2 Multi-Tenant Shop Isolation

- **Model:** `Shop` with fields: id, name, address, created_at.
- **Enforcement:** Every data query includes `shop_id=current_user.shop_id`.
- **Cross-shop blocking:** Routes verify `product.shop_id == current_user.shop_id` before any data access.
- **URL manipulation protection:** Even if a user manually crafts a URL with another shop's product ID, the shop_id check prevents access.

### 2.3 Role-Based Access Control (RBAC)

Implemented via a custom `role_required` decorator in `app.py`:

```python
def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(403)
            if not current_user.can(*roles):
                flash('You do not have permission.', 'danger')
                abort(403)
            return f(*args, **kwargs)
        return wrapper
    return decorator
```

**Permission Matrix:**

| Route | Owner | Manager | Staff |
|---|---|---|---|
| Create/Edit/Delete Product | ✅ | ✅ | ❌ |
| Receive Stock | ✅ | ✅ | ❌ |
| Adjust Inventory | ✅ | ✅ | ❌ |
| Record Sale | ✅ | ✅ | ✅ |
| View Dashboard | ✅ | ✅ | ✅ |
| View Product Detail | ✅ | ✅ | ✅ |
| Manage Employees | ✅ | ❌ | ❌ |
| Apply AI Price | ✅ | ✅ | ❌ |

### 2.4 Product Management

- **Model:** `Product` with fields: id, shop_id, name, category, brand, quantity (package size), unit, cost_price, selling_price, suggested_price, target_margin, baseline_margin, is_price_controlled, government_ceiling_price.
- **PriceHistory:** Audit trail recording every price change (cost_price, selling_price, target_margin, created_at). Used by PCAPA compliance checks.
- **Product Form:** Validated with Flask-WTF. Quantity is `type="number" step="any" min="0"`.

### 2.5 Inventory & Sales (Atomic Transactions)

**Inventory Model:** `Inventory` with product_id (unique), current_stock, unit, last_updated.
**Sale Model:** `Sale` with product_id, quantity, selling_price, sold_at.

**Critical: Atomic Transaction Pattern**

Every sale and stock adjustment is wrapped in a `try/except` block:

```python
try:
    sale = Sale(product_id=pid, quantity=qty, selling_price=price)
    db.session.add(sale)
    inv.current_stock = stock - qty
    db.session.commit()    # Both succeed or both fail
except Exception:
    db.session.rollback()  # Rollback prevents partial state
    flash('Error recording sale', 'danger')
```

This guarantees that a sale row is never created without the corresponding stock decrease, and vice versa.

### 2.6 Employee Invitation System (Phase 2C)

- **Model:** `ShopInvitation` with cryptographic tokens, email-based matching, expiration, and status (pending/accepted/rejected/revoked).
- **Flow:** Owner creates invitation → token generated → employee receives notification → employee accepts → shop membership created.
- **In-App Notifications:** `Notification` model with user_id, type, title, message, invitation_id, is_read, created_at.
- **Accept/Reject:** Server-side validation of token authenticity, email matching, expiry, and status — no client-side trust.

### 2.7 Registration Architecture

Two distinct paths:

1. **Create New Shop (Owner):** Register → enter shop details → user.role = "owner" → user.shop_id = new shop.
2. **Join Existing Shop (Employee):** Register → role = "unassigned" → shop_id = NULL → wait for owner invitation → accept → role and shop_id set from invitation.

---

## 3. Phase 2: Market Intelligence & ETL

**Timeline:** August 18–22, 2026
**Commits:** `26c3af6` through `1cd06ab`
**Goal:** Build the data foundation for connecting shop products to official Malaysian government market data.

### 3.1 Data Source: KPDN PriceCatcher

The Malaysian government publishes monthly PriceCatcher data on `storage.data.gov.my`, containing:
- `lookup_item.parquet` — Product catalog (item_code, item name, unit, category)
- `lookup_premise.parquet` — Retail premise directory (~2,000 premises)
- `pricecatcher_YYYY-MM.parquet` — Price observations (item_code, premise_code, date, price)

### 3.2 Raw Data Ingestion (`import_pricecatcher.py`)

Downloads Parquet files from the government portal and loads them into MySQL:
- Normalizes item_code/premise_code (strips float artifacts like "1000.0" → "1000")
- Drops rows with missing/blank codes
- Excludes item groups: BARANGAN SEGAR (fresh produce), MAKANAN SIAP MASAK (ready-to-cook)
- Filters orphan price rows (item/premise not in lookup tables)
- Creates denormalized `price_catcher_item` table with surrogate integer IDs
- Sets up foreign keys between `price`, `lookup_item`, and `lookup_premise`

### 3.3 Market Data Models (Phase 3A)

Four new models added to support the intelligence layer:

| Model | Purpose | Key Fields |
|---|---|---|
| `MarketSource` | Data provider identity | name, source_type (government/online_retailer/manual), is_active |
| `MarketItem` | Normalized market product | source_id, external_id, raw_title, normalized_title, brand, category, package_quantity, package_unit |
| `MarketPriceObservation` | Time-series price point | market_item_id, regular_price, promo_price, effective_price, normalized_unit_price, observed_at |
| `ProductMarketMatch` | Shop-to-market linkage | shop_product_id, market_item_id, confidence_score, match_type (exact/fuzzy/manual), is_verified, is_rejected |

### 3.4 Text Normalization (`utils/normalization.py`)

Three core functions form the normalization pipeline:

- **`clean_text(text)`** — Lowercase, strip special characters (™, ®, *), remove extra spaces.
- **`normalize_package_size(quantity, unit)`** — Convert to base units: g→kg (/1000), ml→l (/1000), pcs→unit.
- **`calculate_unit_price(price, quantity, unit)`** — Price per base unit (e.g., RM per kg).

### 3.5 ETL Pipeline (`scripts/etl_pricecatcher.py`)

Transforms legacy `price_catcher_item` + `price` tables into the Phase 3A schema:

- **Idempotent:** If MarketSource "PriceCatcher" exists, purges all associated records via raw SQL (bypassing ORM identity map) before re-inserting.
- **Dirty unit parser:** Handles 12+ edge cases: "N X Ng" multipacks, "M54" diaper sizes, count-nouns (beg, batang, biji), bare "paket" with count extraction from item name, "+-" prefixes.
- **AVG price rollup:** Computes average price across ~2,000 premises per item per date, resulting in one `MarketPriceObservation` per item+date.
- **Final stats:** 405 MarketItems, 692 MarketPriceObservations (the full historical dataset).

---

## 4. Phase 3: Machine Learning & Generative AI

**Timeline:** August 22–23, 2026
**Commits:** `0cd7ee8` through `aba6fbd`
**Goal:** Build the intelligent pricing engine combining statistical analysis, machine learning, and generative AI.

### 4.1 Market Analysis Engine (`services/market_analysis.py`)

Aggregates price observations for verified market matches with **geographic localization**:

1. Fetches all `MarketPriceObservation` rows for verified `ProductMarketMatch` entries.
2. Applies the **3-tier geographic fallback chain** based on the shop's location:
   - **Tier 1 (District):** Filter by shop's district (e.g., Segamat). If ≥3 observations, use these.
   - **Tier 2 (State):** If insufficient district data, fall back to state (e.g., Johor).
   - **Tier 3 (National):** If no state data, use all observations as fallback.
3. Scales each observation's `normalized_unit_price` to the shop product's package size (e.g., RM/kg × 10 for a 10 kg bag).
4. Filters invalid prices (≤0, None).
5. Computes: N, min, max, mean, median, spread, and **Price Position Index (PPI)**.
6. Collects **recent observations** (up to 15, newest first) for the Explainable AI transparency table.

**PPI Formula:**
```
PPI = (shop_selling_price / market_median) × 100
```
- PPI = 100: exactly at market median
- PPI = 110: 10% above median (overpriced)
- PPI = 90: 10% below median (underpriced)

**Geographic Data Model:** The `MarketPriceObservation` table includes `state` and `district` columns populated by the ETL pipeline via JOIN with `lookup_premise`. This enables filtering observations by the shop's actual geographic region.

### 4.2 Product Matching Algorithm (`services/matching.py`)

Two-pass matching over ~405 MarketItems:

**Pass 1 — Exact (deterministic):**
- Normalized title equality → confidence = 1.00
- Brand containment match → confidence = 0.95

**Pass 2 — Fuzzy (RapidFuzz):**
```python
confidence = 0.75 × title_score + 0.25 × package_score
```

Where:
- `title_score` = WRatio(product_name, market_title) / 100
- `package_score` = 1.0 (same size), 0.6 (same unit, different size), 0.3 (different unit), 0.0 (no package)

**Design decisions:**
- `FUZZY_GATE = 55` (token_set_ratio pre-filter skips obviously unrelated items)
- `MIN_CONFIDENCE = 0.55` (below this, suggestion is not shown)
- `TOP_K = 3` (maximum suggestions per product)
- Category-filtered candidate pool with full-catalogue fallback

### 4.3 Machine Learning Pricing Engine (`services/pricing_engine.py`)

**Training Pipeline (`scripts/train_pricing_model.py`):**
- **Data source:** Real historical KPDN PriceCatcher data spanning **January 2022 to August 2026** (56 monthly parquet files from `data.gov.my`).
- **Localization:** Filtered for **Johor** state (~325 unique items, 5.4M+ raw price observations).
- **Memory-efficient processing:** Each month is downloaded, merged with lookups, filtered, and aggregated individually — never loading all 56 files into memory.
- **Feature engineering:** Aggregated by `item_code + date` across ~2,000 premises per month. Computes market_median, min, max, spread, and normalized unit price.
- **Target simulation:** Simulated shop cost prices (80-90% of market price) generate 523,590 training samples with varying margin, stock, and velocity scenarios.
- **Model:** `RandomForestRegressor` (200 trees, max_depth=15, min_samples_split=5)
- **Performance:** MAE = RM0.0639 (6.4 sen average error), R² = 0.9999
- **Output:** `ml/pricing_model.pkl` (excluded from git via `.gitignore`)

**13 Features fed to the model:**

| # | Feature | Description |
|---|---|---|
| 1 | cost_price | What the shop pays (RM) |
| 2 | target_margin | Desired margin % |
| 3 | baseline_margin | Margin at product creation (PCAPA baseline) |
| 4 | market_median | Median market price (scaled to product size) |
| 5 | market_mean | Mean market price |
| 6 | market_min | Minimum market price |
| 7 | market_max | Maximum market price |
| 8 | market_spread | max − min (volatility) |
| 9 | normalized_unit_price | RM per base unit |
| 10 | stock_level | Current inventory quantity |
| 11 | sales_velocity | Estimated daily units sold |
| 12 | price_to_market_ratio | cost_price / market_median |
| 13 | quantity | Product package quantity |

### 4.4 Deterministic Guardrails (5 Rules in Strict Order)

The ML prediction is intercepted by five sequential safety rules:

| Rule | Name | Logic | Behavior |
|---|---|---|---|
| **Rule 0** | Regulatory Cap | If `is_price_controlled` and price > `government_ceiling_price` → cap at ceiling | **Hard cap** — overrides ML output |
| **Rule 1** | Cost Floor | If price < `cost_price × 1.05` → raise to floor | **Hard cap** — prevents selling at loss |
| **Rule 1b** | SME Target-Margin Floor | If ML prediction < user's target floor (cost × (1 + target_margin/100)) → raise to the floor | **Hard minimum** — prevents hypermarket bias from bankrupting SMEs |
| **Rule 2** | Market Sanity | If price < 70% of market_min or > 150% of market_max → clamp | **Hard cap** — prevents extreme outliers |
| **Rule 3** | PCAPA Check | If margin > baseline + 1.5% tolerance AND cost hasn't risen → warning | **Soft warning** — informational, not forced |

**Key guarantee:** The recommended price is ALWAYS ≥ cost × 1.05 (Rule 1), NEVER exceeds the KPDN ceiling for controlled goods (Rule 0), and respects the SME business model (Rule 1b, a hard target-margin floor). The 1.5% PCAPA tolerance prevents false warnings from rounding artifacts.

### 4.5 Gemini LLM Integration (`services/llm_explainer.py`)

**Three-Layer Fault-Tolerant Fallback:**

| Layer | Trigger | Behavior |
|---|---|---|
| **Layer 1** | Normal operation | Live Gemini 3.5 Flash Lite API call with structured prompt |
| **Layer 2** | API failure | try/except catches network errors, rate limits, missing API key |
| **Layer 3** | Fallback | Deterministic string template using same data, no external calls |

**Prompt structure:** Injects product name, cost, market median, recommended price, guardrails triggered, PCAPA status, and regulatory context. The system prompt includes explicit legal guardrails: if a PCAPA warning is present, the LLM MUST NOT advise raising the price (this prevents dangerous business advice). LLM is instructed to explain in 2–3 concise sentences under 100 words.

**Critical guarantee:** The function NEVER raises exceptions. A broken API key returns a sensible fallback string rather than crashing the dashboard.

### 4.6 Confidence Scoring

| Level | Score | Condition |
|---|---|---|
| High | 0.85 | ML model + market data + no guardrails fired |
| Medium | 0.55 | Guardrails modified the ML output, or no ML model but market data exists |
| Low | 0.20 | No market data — pure cost-based rule |

---

## 5. Phase 4: Hardening & Legal Compliance

**Timeline:** August 23–25, 2026
**Commits:** `3aa0fa9` through `997dd8d`
**Goal:** Dashboard analytics, data quality, documentation, testing, and Malaysian regulatory compliance.

### 5.1 Dashboard Analytics (`services/dashboard_service.py`)

**Metrics computed in real-time:**

| Metric | Calculation |
|---|---|
| Total Products | `Product.query.filter_by(shop_id=...)` count |
| Low Stock Alerts | Products where `Inventory.current_stock < 10` |
| Inventory Valuation | `SUM(cost_price × current_stock)` across all products |

**Action Required Panel flags:**
- **Low Stock:** `current_stock < 10`
- **Cost Floor Violation:** `selling_price < cost_price × 1.05`
- **PCAPA Warning:** Margin > baseline without cost increase
- **Overpriced:** PPI > 110% (price significantly above market median)
- **Underpriced:** PPI < 90% (price significantly below market median)

### 5.2 Data Quality (Phase 4B)

- **Product Form Validation:** Quantity field is `type="number" step="any" min="0"` with placeholder text.
- **Backend Guardrails:** Quantity safely cast to float; unit stripped of whitespace.
- **Seed Demo Cleanup:** Removed dirty demo products ("100 PLUS", "MILO PAKET") and replaced with 5 clean products with mathematically correct package sizes.
- **Inventory Seeding:** Each demo product gets realistic stock levels (including one low-stock item to trigger dashboard alerts).

### 5.3 KPDN Price Control Guardrails (Rule 0)

Malaysian **Barangan Kawalan** (Price-Controlled Goods) implementation:

- **Model fields:** `is_price_controlled` (Boolean), `government_ceiling_price` (Float)
- **Official KPDN Peninsular Malaysia ceiling prices:**
  - Gula Putih Bertapis Kasar (1 kg): RM 2.85
  - Gula Putih Bertapis Halus (1 kg): RM 2.95
  - Minyak Masak Polybag (1 kg): RM 2.50
  - Tepung Gandum (1 kg): RM 1.35
- **UI:** Checkbox in product form → conditional ceiling price input (JavaScript toggle)
- **Enforcement:** Rule 0 fires first in the guardrail chain — price is hard-capped at the KPDN ceiling.
- **Gemini context:** When cap applies, LLM prompt includes "WARNING: Barangan Kawalan — legally capped at RM X.XX"

### 5.4 Test Suite (144 Tests, 9 Suites)

| Suite | Tests | What It Covers |
|---|---|---|
| test_normalization | 16 | clean_text, normalize_package_size, calculate_unit_price |
| test_market_models | 7 | MarketSource, MarketItem, MarketPriceObservation, ProductMarketMatch CRUD |
| test_employee_remove | 8 | Owner/Manager/Staff permission enforcement for employee removal |
| test_dashboard_service | 24 | Metrics calculation, action items, shop isolation |
| test_matching | 15 | Exact pass, fuzzy pass, package scoring, category filtering |
| test_market_analysis | 13 | compute_metrics, PPI calculation, unit scaling |
| test_llm_explainer | 17 | 3-layer fallback chain, prompt construction, mock API |
| test_pricing_engine | 43 | All 4 guardrails, feature engineering, confidence scoring |
| test_integration | 1 | Full E2E flow: Register → Login → Product → Stock → Sale → Market Intel → AI Pricing |
| **Total** | **144** | All passing ✅ |

### 5.5 Production Hardening

- **Error Pages:** Custom 403, 404, 500 templates with Bootstrap card layouts and "Back to Dashboard" buttons.
- **500 Handler:** Logs full traceback via `app.logger.error(..., exc_info=True)` before rendering.
- **Logging:** `logging.basicConfig` configured at app startup with timestamp, level, and module name.
- **Environment:** `.env.example` documenting SECRET_KEY, DATABASE_URL, and GEMINI_API_KEY.

### 5.6 Geographic Localization & Explainable AI

**Geographic Market Intelligence (2026-08-26):**
- **Shop Location:** Added `state` (SelectField with 16 Malaysian states) and `district` (StringField) to Shop model and registration form.
- **Market Data Enrichment:** ETL now JOINs `lookup_premise` to store `state` and `district` on each `MarketPriceObservation` row.
- **3-Tier Geographic Fallback:** `get_market_stats()` filters observations by the shop's location with fallback chain: district → state → national. Minimum 3 observations required at each tier before falling back.
- **UI Integration:** Product Detail page shows localization scope (e.g., "📍 Segamat, Johor") and the `scaling_note` explains whether data is local or national.
- **Migration:** `f7e8d9c0b1a2` adds 4 columns (shop.state, shop.district, market_price_observation.state, market_price_observation.district).
- **Seed Update:** Demo shop set to Johor/Segamat.

**Competitor Observation Transparency (2026-08-26):**
- **Raw Data Exposure:** `get_market_stats()` now returns `recent_observations` — up to 15 raw KPDN price observations (date, product, package, location, price) sorted newest-first.
- **UI Table:** New "Recent Competitor Pricing (KPDN Open Data)" card in Market Intelligence tab shows the actual data points driving the market summary.
- **Explainable AI:** Users can verify the underlying government data, building trust in the AI's pricing recommendations.
- **Graceful Fallback:** Shows placeholder message when no observations are available.

### 5.7 Documentation Suite

| File | Purpose |
|---|---|
| `README.md` | Architecture, tech stack, setup instructions, phase overview |
| `DEMO.md` | 5-step FYP presentation walkthrough script |
| `API_REFERENCE.md` | 9 JSON API endpoints with methods and purposes |
| `SCREENSHOT_GUIDE.md` | 6 specific views to capture for the academic report |
| `DEVELOPMENT_JOURNEY.md` | This file — complete project history and roadmap |

---

## 6. Phase 5 & Future Roadmap

### 6.1 Real-World Sales Retraining Loop

**Current state:** The ML model is trained on real KPDN PriceCatcher data (2022-2026, 5.4M+ observations from Johor). Cost prices are simulated (80-90% of market price) to generate training labels.

**Planned enhancement:**
- After accumulating 6+ months of real shop sales data, retrain the model using actual transaction records instead of simulated costs.
- Implement a periodic retraining schedule (e.g., monthly) that blends real market data + real sales data.
- Add model versioning and A/B testing to compare prediction accuracy.

### 6.2 Time-Series Demand Forecasting

**Current state:** Sales velocity is estimated as a simple average of the last 30 sales.

**Planned enhancement:**
- Implement ARIMA or Prophet for seasonal demand forecasting.
- Factor in seasonal KPDN trends (e.g., Ramadan demand spikes for sugar and cooking oil).
- Use demand forecasts as an additional feature in the pricing model.

### 6.3 Receipt OCR for Automated Cost-Price Updating

**Current state:** Cost prices are manually entered by the shop owner.

**Planned enhancement:**
- Integrate OCR (e.g., Tesseract or Google Vision) to extract cost prices from supplier receipts.
- Automatically update `cost_price` and create a `PriceHistory` entry.
- Flag unusual cost changes for owner review.

### 6.4 Online Retailer Scraping

**Current state:** Only government PriceCatcher data is ingested.

**Planned enhancement:**
- Add scrapers for online retailers (Lazada, Shopee, Lotus's Online) as additional `MarketSource` entries.
- Implement rate limiting and robots.txt compliance.
- Expand the `source_type` enum to include 'online_retailer'.

### 6.5 Multi-Category Expansion

**Current state:** Excludes BARANGAN SEGAR (fresh produce) and MAKANAN SIAP MASAK (ready-to-cook).

**Planned enhancement:**
- Add category-specific matching algorithms (e.g., weight-based for fresh produce, piece-count for ready-to-cook).
- Partner with local wet market associations for fresh-produce pricing data.

---

## 7. Recent Updates

<!-- New entries are appended below this line. Do not modify previous entries. -->

### 2026-09-09 — Phase 9: Shop-wide Pricing Intelligence Dashboard & Reporting

- **Why:** Phases 6–8 deliver strong PRODUCT-level pricing intelligence, but a retailer with dozens of products had no way to see where the pricing OPPORTUNITIES are across the whole catalogue. Phase 9 progresses ShelfSenseAI from individual product-level pricing recommendations to shop-level pricing decision support, allowing retailers to identify and prioritize pricing opportunities across their entire product catalogue.
- **Service (`services/pricing_dashboard.py`, read-only reporting layer):** `get_shop_pricing_opportunities()` builds one row per product by REUSING the real Phase 7 engine (`get_price_recommendation(..., skip_llm=True)`) — the same status, market tier, evidence, trend, and recommended price shown on the product page; it never recomputes anything differently for the dashboard. Latest Phase 8 decision per product comes from ONE grouped `MAX(id)` query (no N+1). Filtering (status / decision / evidence / tier / priority / name search) and sorting (priority / above-market / below-market / largest diff / name / pending-first) happen server-side on the filtered row set, then pagination slices — page 1 always shows the true top matches. `get_shop_decision_summary()` aggregates Phase 8 decisions in SQL (distinct products per decision state, plus generated/applied/dismissed this month).
- **Deterministic priority (transparent, price-neutral):** high = Strong/Moderate evidence AND REDUCE/INCREASE; medium = REDUCE/INCREASE with Limited evidence, or Strong/Moderate evidence with MAINTAIN; low = MAINTAIN with Limited/Unavailable evidence; none = INSUFFICIENT_DATA. Priority only orders the table — it can never modify a recommended price, and it introduces no new pricing logic.
- **Route + template (`/pricing-dashboard`, `templates/pricing_dashboard.html`):** `@login_required @role_required('owner','manager')` (management pricing intelligence — staff excluded, matching the existing product-mutation permission model); nav link "Pricing Intelligence" for owner/manager only. Six summary cards (total products, market-covered, above market, below market, pending reviews, applied/dismissed), three lightweight distribution panels (recommendation status, retailer decisions incl. this-month counts, market position with the documented 5% band), a filter bar (search, status, decision, evidence, tier, sort), the paginated opportunity table with per-row Review drill-down into the existing product page, preserved filter/sort params across pages, and explicit empty states (no products → add-product CTA; no decision history; no market comparison; no filter matches → clear-filters).
- **No writes, ever (`#23/#24/#28` compliance):** the dashboard is a pure GET view. It creates NO PricingRecommendationDecision rows (the Phase 8 snapshot is still written only during product review), never touches selling_price, and never calls Gemini — `skip_llm=True` skips only the descriptive explanation; every deterministic field is identical to the product page. Apply/Dismiss remain exclusive to the Phase 8 workflow; the dashboard's only action is "Review" → product detail.
- **Performance:** no Python-side scans of the 1.97M-row observation table — market data flows only through the existing market-analysis service (geo fallback, indexed queries). Decision aggregates are SQL GROUP BYs; the engine's per-product cost is the same read path the product page already uses (ML cached, one bounded trend query per product).
- **Shop isolation:** every query filters by the logged-in user's shop_id (products, decisions, summaries); verified by tests that Shop B's owner never sees Shop A's rows and vice versa. Unassigned users get a friendly info page, not a 500.
- **Tests (`tests/test_pricing_dashboard.py`, 10 tests / 45+ checks):** priority function, summary metrics (incl. distinct-product pending counting), filters (status/evidence/tier/decision/search + bogus-value), sorting (diff asc/desc, name), pagination (page size, page-2 remainder), authorization (anonymous 302, staff 403, cross-shop isolation both directions), GET side-effect freedom (price/PriceHistory/decision counts unchanged after filtered+panginated reads), drill-down + Phase 8 apply/dismiss authority, empty states, unassigned user.
- **No migration needed:** Phase 9 uses only existing tables (Product, Shop, PricingRecommendationDecision, PriceHistory, market models) — zero schema changes.

### 2026-09-09 — Phase 8: Pricing Decision Workflow & Auditability

- **Objective:** turn the Phase 7 recommendation screen into a full decision-support workflow. Phase 7 answers "what price does the system recommend?"; Phase 8 answers "what can the retailer do with that recommendation, and how is the decision recorded?". The pricing hierarchy, guardrails, ML model, and Gemini's explanation-only role are completely unchanged.
- **Separation of recommendation and decision (academic core):** ShelfSenseAI separates automated pricing recommendation from human pricing decisions. The system generates a deterministic recommendation using market intelligence, machine learning and business constraints; the retailer retains final authority to accept or reject it. `recommendation_status` (MAINTAIN/REDUCE/INCREASE/INSUFFICIENT_DATA) describes the SYSTEM's suggestion; `decision` (PENDING/APPLIED/DISMISSED) records the RETAILER's choice. The two are stored side-by-side but never conflated — a REDUCE recommendation never changes a price by itself.
- **New model — `PricingRecommendationDecision` (`app.py`, migration `f9e8d7c6b5a4`):** one row per recommendation snapshot shown to a retailer. Snapshot columns (written once, never recalculated): recommended_price, current_price at generation time, recommendation_status, market tier/median, evidence rating, trend classification + percent, guardrails fired, generated_at. Decision columns: decision state, decision_reason, decided_by (FK user.id), decided_at. FKs to product/shop/user; indexes on product_id (history + dedupe lookup) and shop_id (cross-shop authorization); deliberately separate from `price_history`, which continues to answer "what happened to the actual selling price?".
- **Decision workflow (`services/pricing_workflow.py`):** `record_decision()` snapshots the current recommendation as PENDING when an owner/manager opens the pricing pane (identical pending snapshots are reused, not duplicated; staff page views never write rows). `apply_decision()` and `dismiss_decision()` implement the retailer's two explicit actions — nothing anywhere changes a price automatically.
- **Apply workflow (atomic + stale-safe):** the client posts only a decision id — NO price. The server (1) re-checks auth/role/shop ownership, (2) rejects non-PENDING decisions (double-apply is an idempotent no-op), (3) STALE CHECK: rejects the apply with HTTP 409 and a clear message if the live selling price no longer matches the snapshot's current_price (another employee changed the price in between), (4) RECOMPUTES the recommendation with the deterministic engine and applies THAT value — a client can never inject RM0.01 in place of RM1.88, (5) commits price update + existing PriceHistory row + decision APPLIED as ONE transaction (rollback on any failure leaves all three untouched).
- **Dismiss workflow:** owner/manager picks a reason from a fixed list (market not representative / supplier cost changed / demand / promotion / competitor temporary / management decision / Other) with an optional short note; server-side validation (invalid reasons rejected, "Other" requires a note, length-capped). Dismissal never touches the price.
- **Routes (`app.py`, all POST + CSRF-protected + `role_required('owner','manager')` + shop-ownership 403):** `/api/product/<pid>/decision` (record snapshot), `/api/decision/<did>/apply`, `/api/decision/<did>/dismiss`. The pricing GET endpoints remain strictly read-only.
- **UI (`templates/product_detail.html`):** the pricing pane gains Apply/Dismiss actions bound to the persisted decision record, a "Decision record #N — generated <date>" line, a Bootstrap dismiss modal with reason select + optional note, a **Pricing Recommendation History** card (date, current price, recommended price, engine status, decision badge, deciding user + reason — limited to the 10 most recent), and stale-apply feedback. Phase 6 Market Intelligence and the Phase 7 badges are untouched.
- **Tests (`tests/test_pricing_workflow.py`, 14 tests / 60+ checks):** all 15 mandated cases — decision recording + snapshot, apply (price + PriceHistory + APPLIED), forced-failure rollback (IntegrityError → nothing partially written), dismissal (user/timestamp/reason + invalid-reason + Other-requires-note), stale rejection after a price change, cross-shop 403 (record/apply/dismiss), staff + anonymous denial, injected client price ignored, status-vs-decision separation, snapshot immutability, PriceHistory integration, decision shop ownership, duplicate-apply idempotency (service + HTTP), dismissed-cannot-apply, and the full HTTP flow with CSRF.
- **Gemini:** untouched. The decision workflow never calls it; the snapshot stores deterministic fields only (the LLM explanation is not recorded as authority data). Gemini remains explanation-only.
- **Migration:** hand-written `f9e8d7c6b5a4` (project convention) adding one table + two indexes; `flask db upgrade` verified (e5d4c3b2a1f0 → f9e8d7c6b5a4) with a matching clean downgrade.

### 2026-09-09 — Phase 7.1: Targeted Fix Pass (SME Floor Semantics, Shop-Aware Pricing API, Badge Wording)

- **Background:** a full read-only Phase 7 audit (159/159 tests passing) verified the architecture and found 5 non-blocking concerns; 2 plus one cosmetic item were selected for this corrective pass. The pricing hierarchy, guardrail order, ML model, and Gemini's explanation-only role are unchanged.
- **Fix 1 — SME target-margin floor semantics (`services/pricing_engine.py`):** the audit proved the earlier "60/40 blend" formulation was mathematically a no-op — `max(0.6×floor + 0.4×candidate, floor)` always equals the floor when the candidate is at or below it. The dead blend arithmetic was removed and Rule 1b is now accurately named and documented: **SME target-margin protection acts as a deterministic hard minimum price floor based on the retailer's configured target margin** (`cost × (1 + target_margin/100)`). No pricing behavior changed — every validated recommendation is identical; only comments, the reasoning string, the `guardrail_effect` wording (now shows the exact floor amount), and documentation were corrected. Verified by `test_sme_floor_hard_minimum` (Case A: candidate 110 with cost 100 / margin 25% → raised to ≥ 125; Case B: candidate above the floor is untouched and the guardrail does not fire).
- **Fix 2 — shop-aware pricing API (`app.py`):** `/api/product/<pid>/pricing` now passes the authenticated user's shop into `get_price_recommendation(pid, shop=p.shop)`, so the JS-rendered pricing pane resolves the SAME geographic market tier (district → state → national), evidence rating, and market context as the server-rendered product detail page. No geographic values are hardcoded. The existing ownership check (`p.shop_id != current_user.shop_id → 403`) is preserved — verified by `test_pricing_api_shop_context` (Case A: API and server-rendered calls produce identical tier/median/n/price/evidence under district data; Case B: consistent fallback when no district data exists; Case C: cross-shop access still 403).
- **Fix 3 — badge wording (`templates/product_detail.html`):** the recommendation-difference badge previously labeled a positive diff as "below your price" and a negative diff as "above your price" — inverted. Now: `+x% (above your price)` / `−x% (below your price)`, in both the server-rendered and JS-rendered panes. No calculation changed.
- **Known limitations deliberately documented, not fixed:** (1) the ML model's training script simulates shop cost at 80–90% of the market median, so products sourced much cheaper than market (e.g. the audit's RM1.50-cost / RM2.60-median case) get a low ML candidate that is then raised by the target-margin floor — this is transparent and explained in the UI; retraining with real shop cost data is a future research task. (2) The pricing pane makes one synchronous Gemini call per page load (latency/cost acceptable for the FYP prototype; caching is a future optimization). (3) Verified market coverage is still limited to a handful of products. (4) Trend classification requires ≥2 points over ≥7 days, so short histories report `insufficient_data` honestly.
- **Tests:** 161 tests total (159 prior + 2 new regression suites for the fixes), all passing.

### 2026-09-09 — Phase 7: Intelligent Pricing Recommendation & Decision Support

- **Objective:** turn Phase 6 market intelligence into an explainable, constraint-aware pricing decision-support layer on top of the existing pricing engine — which was NOT rewritten. All five guardrails (KPDN cap, cost floor cost×1.05, SME target-margin floor, market sanity, PCAPA informational warning), the ML model, and the rule order are untouched and remain authoritative. Market intelligence informs the recommendation; it never determines it, and Gemini stays explanation-only.
- **Decision-support signals (`services/pricing_engine.py`, pure documented constants + functions):** `_classify_trend()` classifies the Phase 6 trend as Rising/Falling/Stable/insufficient_data from the first-vs-last median change (±`TREND_STABLE_BAND_PCT` = 1.0% band; <`TREND_MIN_SPAN_DAYS` = 7 days or <2 points = insufficient, so noisy one-day moves never count); `_market_evidence()` rates the market benchmark Strong/Moderate/Limited/Unavailable from premise count, observation count, and tier (documented bands, e.g. Strong = ≥5 premises, ≥20 observations, district/state tier); `recommendation_status()` derives MAINTAIN/REDUCE/INCREASE from the final guardrailed price vs current price with a ±`STATUS_TOLERANCE_PCT` = 0.5% band (INSUFFICIENT_DATA when no current price), matching the UI's existing ±0.5% badge logic.
- **Integration:** STEP 12b enriches the existing pipeline (after guardrails, before the LLM): the trend reuses `get_market_trend()` (server-side aggregation, date-bounded — no duplicated market SQL), evidence is rated from the same `get_market_stats()` payload, and a human-readable `guardrail_effect` explains when a constraint changed the candidate (cost floor prioritized over a below-cost market median; KPDN cap; SME margin protection; sanity clamp). The reasoning list is appended in traceable order: prediction → constraints → market evidence → trend. The return payload and Gemini payload gain `status`, `market_evidence`, `trend_direction`, `trend_change_percent`, `guardrail_effect`, etc. — context only.
- **Gemini (`services/llm_explainer.py`):** the prompt now also receives the suggested action, evidence strength, trend, and any guardrail effect, with an explicit "do not suggest a different price" instruction. The deterministic recommendation is computed before Gemini is called and is never modified by it; the 3-layer fallback guarantees an explanation when Gemini is unavailable.
- **UI (`templates/product_detail.html`, both server-rendered and JS-rendered pricing panes):** suggested-action badge (MAINTAIN/REDUCE/INCREASE in decision-support wording — "Suggested action", never a command), Market Evidence badge, Trend badge with signed %, and an info alert exposing the guardrail effect in plain language. Phase 6 Market Intelligence section unchanged.
- **Test-source hygiene (Phase 6 audit follow-up):** removed the leftover active `TestPriceCatcher_ingest`/`TestSource_ingest` rows from the database (FK-safe, children first); only `PriceCatcher` (1,970,887 obs) and `ManaMurah` (110 obs) remain, and `tests/test_market_ingestion.py` purge now deletes its fixture PriceCatcher source as well (implemented in the Phase 7 session; the fixture still has 0 verified matches so it can never contaminate production analysis).
- **Tests (`tests/test_pricing_decision.py`, 9 tests):** status math (REDUCE/INCREASE/MAINTAIN/tolerance/edge cases), trend classification (rising/falling/stable/insufficient incl. one-day noise rejection and zero medians), evidence bands, payload keys, status-vs-final-price consistency, cost floor ≥ RM2.10 under a low market, KPDN ceiling enforced, no-market-data graceful degradation (no fake median), and the Gemini explanation-only contract.
- **Live verification (real DB):** SABUN PENCUCI KUAT HARIMAU (LEMON) — cost RM1.50, target margin 25%, current RM3.00; district tier Segamat (7 premises, 93 obs, median RM2.60, range RM2.55–2.90, position +15.38% Above Market, evidence Strong, trend Stable 0.0%); ML candidate RM1.85 → SME-margin guardrail → final RM1.88 (status REDUCE, −37.3% vs current, SME margin protection explained). Empty-market MILO (PAKET) still returns a valid RM5.20 recommendation with evidence Unavailable, trend insufficient_data, and no fabricated median; a no-match product also recommends normally (status INCREASE). No schema changes were needed for Phase 7.
- **Test suite:** **159/159 passed** (150 baseline + 9 new Phase 7 tests).

### 2026-09-09 — Phase 6: Market Intelligence & Visualization

- **Objective:** make the premise-level PriceCatcher dataset (Phase A/B) *useful* — the retailer can now see what market stores charge, where their price sits, and how the market moves, all as an input to (never a replacement for) the existing guardrailed pricing engine.
- **Market summary (`services/market_analysis.py`):** `get_market_stats()` now returns full summary statistics (min/max/mean/median/spread), `premise_count` (distinct stores), `latest_observed_at`/`earliest_observed_at`, a structured `market_tier` (`district`/`state`/`national`) with an honest `market_tier_label` (e.g. "Segamat, Johor" — national fallback is never passed off as local), price distribution quartiles (Q1/median/Q3 for the range visualization), and `market_position()` output (difference, % vs median, Below/Near/Above Market with a documented ±2% "Near Market" tolerance).
- **Latest-snapshot competitor table (`_competitor_snapshot`):** store-level prices resolved through `premise_code` → `lookup_premise` (no metadata duplication), restricted to the **latest market snapshot date** so stale prices never mix with fresh ones in the "current market" view. Premise names now show in the paginated Explainable AI observations table too.
- **Historical trend (`get_market_trend()`):** server-side daily aggregation over verified market items with the same 3-tier geographic fallback as the snapshot, bounded to the last 90 days. Medians/percentiles computed **in MariaDB** via `PERCENTILE_CONT` window functions — no multi-million-row Python processing. The UI renders it as a dependency-free inline SVG chart (median line + shaded min–max band, tooltips with observation/premise counts).
- **Pricing integration (`services/pricing_engine.py`):** the pricing payload now carries market median/mean/min/max/spread/premise count/market tier/price-to-market ratio as **informational context**. All five guardrails (KPDN cap, cost floor, SME target-margin floor, market sanity, PCAPA) are untouched and remain authoritative; market data never sets the price.
- **LLM explanation (`services/llm_explainer.py`):** the Gemini prompt now receives market context (tier label, median, range, premise count, retailer position) so explanations can mention local market standing. Gemini remains explanation-only.
- **UI (`templates/product_detail.html`):** new Market Intelligence section — tier benchmark badge, stat cards (min/median/mean/max/spread/premise count), Your Market Position block (RM difference + % + Below/Near/Above badge), min→max distribution bar with median marker and "your price" marker, latest-snapshot competitor table (store, location, price, date, difference vs shop price), and the SVG market trend chart. Every block degrades to a clean empty state ("No sufficient market observations…" / "Market data unavailable") — **no fake zeros**; pricing works unchanged without market data.
- **Source isolation:** stats/trend/snapshot filter strictly by active source and verified matches; leftover test sources were already purged in the Phase A/B entry. Verified PriceCatcher statistics are not contaminated by ManaMurah/FAMA observations.
- **Performance:** all aggregation is database-side against existing indexes (`ix_mpo_item_state_district_obs`, `uq_premise_obs`, `ix_market_item_source_external`); the trend query uses date bounds (90 days) and item-id filters; medians use MariaDB `PERCENTILE_CONT`; no new indexes were needed. Verified live: snapshot + trend for a 93-observation product returns in well under a second.
- **Tests (`tests/test_market_intelligence.py`, 12 new):** summary statistics (min/max/mean/median/spread/premise/observation counts), market position (below/near/above, percentage math, zero/edge cases), geographic fallback (district → state → national → none), store-level resolution (premise name/code, exact price, dates, multiple premises), source isolation (unmatched/inactive sources excluded), historical trend (dates, per-date stats, bounds), and missing-data safety (nulls not zeros; pricing still functional). No schema migration was required — Phase 6 is analysis/UI only.
- **Manual verification (live DB):** SABUN PENCUCI KUAT HARIMAU (LEMON) — shop price RM 3.00, district tier **Segamat, Johor**, 93 observations across 7 premises, min RM 2.55 / median RM 2.60 / mean RM 2.69 / max RM 2.90 / spread RM 0.35, distribution Q1 2.59 / Q3 2.80, position **+15.4% Above Market**; latest snapshot 2026-08-27 with named stores (GIANT KEMUNING UTAMA RM 2.29 …); 12 trend points from 2026-06-15 → 2026-08-27. Products with unmatched items (e.g. MILO (PAKET)) correctly show nulls and an empty state.
- **Test suite:** **150/150 passed** (138 baseline + 12 new Phase 6 tests).

### 2026-09-08 — Phase 6: Historical Archive & Premise-Level Market Observations

- **Forensic audit finding:** the previous ETL aggregated 1,838,501 raw PriceCatcher records down to 311,543 observations via `GROUP BY item_code, date, state, district AVG(price)` — only **16.94% retention**, destroying all store-level information. The hardcoded `PRICE_MONTHS = ["2026-06","2026-07","2026-08"]` with `if_exists="replace"` also deleted the raw archive on every import.
- **Phase A — Non-destructive monthly import:** rewrote `import_pricecatcher.py` with `--month YYYY-MM` / `--start-month` / `--end-month` / `--local-dir` CLI flags (default: last 3 closed months). The raw tables (`price`, `lookup_item`, `lookup_premise`) are now an append-only historical archive — months coexist and older months are never deleted. Deduplication via a `uq_price_natural` unique key on `(date, premise_code, item_code)` plus in-file dedup; verified: re-importing 2026-06/07 inserted **0** rows, the refreshed 2026-08 file added 132,386 new records (new dates 2026-08-27 → 2026-08-30) with **0 duplicates**.
- **Phase B — Premise-level ETL:** rewrote `scripts/etl_pricecatcher.py`. One raw price record → one `MarketPriceObservation` with the **exact** price (no averaging), `premise_code` (premise metadata stays normalized in `lookup_premise`), state/district resolved from the premise lookup, and per-observation normalized unit price (reusing `utils/normalization.py`). `MarketItem` ids are upserted (stable), so all 19 `ProductMarketMatch` rows survived the rebuild. The ETL only rebuilds the PriceCatcher source — ManaMurah's 110 FAMA observations were untouched — and is safely repeatable (unique index `uq_premise_obs` on `market_item_id, premise_code, observed_at`). Runs month-by-month in separate transactions (resumable) at ~5.5K rows/s (full rebuild: 519s for 1.97M rows).
- **Migration `e5d4c3b2a1f0`:** added `market_price_observation.premise_code` (VARCHAR(20), indexed), `uq_premise_obs` unique index, `ix_mpo_item_state_district_obs` composite index for the geographic fallback queries, and `ix_market_item_source_external` on `market_item(source_id, external_id)` (the per-row join bottleneck).
- **Market analysis:** `get_market_stats()` now reports `premise_count` (distinct premises behind the statistics) and the Explainable AI observations table gained a **Premise** column (server-rendered + paginated), so the shop owner sees exactly which store reported each price.
- **Data cleanup:** deactivated/removed the leftover test sources (`TestPriceCatcher_ingest` ×4, `TestSource_ingest` ×1); only `PriceCatcher` (1,970,887 obs) and `ManaMurah` (110 obs) remain active.
- **Measured results (before → after):** raw archive 1,838,501 → 1,970,887 (2026-06-02 → **2026-08-30**); MarketPriceObservation 311,543 → 1,970,997 (PriceCatcher 1,970,887 + ManaMurah 110); raw retention **16.94% → 100.00%** (0 raw rows unrepresented); Segamat observations 7,077 → 21,787 across **21 named premises**; items with ≥3 Segamat observations 55 → 147; distinct premises represented 2,145 → 2,155.
- **Store-level capability (live DB, not hardcoded):** BAWANG BESAR KUNING/HOLLAND, Segamat, 2026-08-30 — 10 premises from RM 2.50 (PASARAYA NIRWANA) to RM 4.00 (PASAR AWAM), median RM 3.09, mean RM 3.22, spread RM 1.50.
- **Tests:** added `tests/test_import_pricecatcher.py` (15 checks: new month, repeat month, multiple months, historical preservation, duplicate prevention, invalid month, missing file, DB failure) and `tests/test_premise_level.py` (10 checks: exact price, premise identity, multi-premise separation, state/district, unit normalization, ETL idempotency, min/median/max/spread/premise_count, district/state/national fallback, source isolation). Suite: **138/138 passing**.

### 2026-09-08 — Phase 5: ManaMurah MCP Market Data Integration

- **Architectural finding:** The ManaMurah MCP server (`https://mcp.manamurah.com/mcp`, 15 tools) is a protocol shim over the SAME data.gov.my PriceCatcher dataset — NOT an independent source. Its 11 KPDN tools were deliberately NOT ingested (would duplicate observations). Its genuinely independent dataset — **FAMA Panduan Harga Harian** daily prices (46-item catalogue, RUNCIT/BORONG/LADANG levels) — IS ingested as a second MarketSource.
- **Added:** `services/mcp_client.py` — async MCP client (official `mcp` SDK, streamable HTTP transport) with defensive parsing; converts tool responses into source-agnostic market-record dicts. Zero MCP protocol knowledge leaks into the database layer.
- **Added:** `services/market_ingestion.py` — source-agnostic validation + idempotent upsert layer. MarketItem identity: `(source_id, external_id)`; MarketPriceObservation identity: `(market_item_id, observed_at, state, district)`. Per-item batch commits; invalid records are rejected and logged, never inserted.
- **Added:** `scripts/sync_market_data.py` — synchronization script with FYP-scope FAMA item selection (TELUR AYAM, BERAS SAWAH, BERAS IMPORT, SANTAN, UBI KENTANG) and a full sync summary + PriceCatcher-intactness check. Supports `--days` and `--state` flags.
- **Added:** `tests/test_mcp_client.py` (25 checks, fully mocked) and `tests/test_market_ingestion.py` (47 checks, real DB, dual pytest/standalone compatible).
- **Verified live:** first sync inserted 5 items + 24 observations; second sync inserted **0** new rows (24 duplicates skipped) — idempotency proven. PriceCatcher's 311,543 observations untouched.
- **Dependencies:** added `mcp==1.19.0` to requirements.txt; added `MANAMURAH_MCP_URL` to `.env.example`.
- **Test suite:** **113/113 passing** (86 pre-existing + 27 new).

### 2026-08-27 — Unit Normalization Fix, PCAPA Tolerance & LLM Legal Guardrails

- **Fixed: Unit Normalization Scaling Bug** — Products without a `unit` field caused the market median to pass through unscaled (per-kg instead of per-package), causing the ML to hallucinate absurd prices (e.g., RM 28 for toothpaste when the real per-tube price is RM 13.82). Added `_infer_base_qty_from_market()` helper that uses the matched `MarketItem`'s package info as a fallback reference. Three-layer fallback chain: product unit → market item package → default 1.0.
- **Fixed: PCAPA Rounding Collision** — Added `PCAPA_EPSILON = 1.5` tolerance threshold to `_check_pcapa()`. The SME target-margin floor's rounding artifacts (e.g., 25.3% vs 25.0%) no longer trigger false legal warnings. Only genuine margin spikes (> 1.5% above baseline) fire the warning.
- **Fixed: LLM Hallucination** — Updated the Gemini prompt template with explicit legal guardrails: "If a PCAPA warning is present, you MUST NOT advise the user to raise the price closer to the market median." Updated `_fallback()` deterministic template to include "Do not increase the price further" when PCAPA warnings are active. Prevents the LLM from giving dangerous business advice that would worsen PCAPA violations.
- **Files changed:** `services/market_analysis.py`, `services/pricing_engine.py`, `services/llm_explainer.py`, `tests/test_market_analysis.py`
- **Tests:** 86/86 passing — zero regressions.


### 2026-08-26 — Remove 4-Language Localization System

- **Removed:** Entire `translations.py` module (1,109 lines of EN/MS/ZH/TA translation dictionaries).
- **Removed:** `preferred_language` column from `User` model via reverse migration `c8b7e2f1d0a3`.
- **Removed:** `PreferencesForm` class from `forms.py`.
- **Removed:** Language preferences tab from Settings page.
- **Removed:** Language display row from Profile page.
- **Removed:** Translation context processor (`_t()`, `LANGUAGE_NAMES`, `LANGUAGE_OPTIONS`) from `app.py`.
- **Updated:** All 20 HTML templates reverted from `_t('KEY')` calls to hardcoded English text (270 replacements across all templates).
- **Decision:** Language preferences feature was removed to keep the UI simple and avoid translation maintenance overhead for the FYP submission. The application is English-only.
- **Commit:** `fdbe9f2`

### 2026-08-26 — Geographic Market Localization & Explainable AI

- **Added:** Shop `state` and `district` columns for geographic market intelligence.
- **Added:** `MarketPriceObservation` `state` and `district` columns populated by ETL via JOIN with `lookup_premise`.
- **Added:** 3-tier geographic fallback chain in `get_market_stats()`: district → state → national (minimum 3 observations per tier).
- **Added:** `recent_observations` key in market stats return dict — up to 15 raw KPDN price observations for Explainable AI transparency.
- **Added:** "Recent Competitor Pricing (KPDN Open Data)" table in Market Intelligence tab showing date, product, package, location, and price.
- **Updated:** Registration form with Malaysian state dropdown (16 states) and district field.
- **Updated:** Pricing engine passes shop context for geo-filtered market statistics.
- **Updated:** ETL script updated to JOIN `lookup_premise` and store state/district per observation.
- **Added:** Hand-written migration `f7e8d9c0b1a2` (4 new columns).
- **Updated:** Demo shop set to Johor/Segamat.
- **Commits:** `b5de145`, `2637d6c`

### 2026-08-25 — ML Training on KPDN Big Data

- **Upgraded:** ML training pipeline now uses real historical KPDN PriceCatcher data (January 2022 - August 2026) instead of synthetic data.
- **Data processed:** 56 monthly parquet files, 5,438,940 raw price observations from 3,893 Johor premises, 325 unique items.
- **Memory-efficient:** Rewrote training script to process each month individually (download, merge, filter, aggregate) instead of loading all 56 files into memory.
- **Fixed:** premise_code type mismatch (float64 in premise lookup vs int64 in transactions) causing zero-row merges.
- **Fixed:** Date column dtype conflicts causing MemoryError during pd.concat.
- **Model performance:** MAE = RM0.0639 (6.4 sen), R² = 0.9999, 523,590 training samples.
- **Top features:** cost_price (88.1%), market_median (11.1%), target_margin (0.7%).
- **Commit:** `8350e5f`

### 2026-08-25 — Documentation Fix

- **Fixed:** `README.md` and `DEMO.md` now include the missing `import_pricecatcher.py` step in the setup instructions. Previously, new users following the README would hit errors because the ETL pipeline depends on legacy tables that only exist after the import script runs.
- **Fixed:** Updated Gemini model version in README from "Gemini 1.5 Flash" to "Gemini 3.5 Flash Lite".
- **Fixed:** Corrected script path in DEMO.md from `python etl_pricecatcher.py` to `python scripts/etl_pricecatcher.py`.
- **Commit:** `d778611`

### 2026-08-25 — Exhaustive Academic Commenting

- **Added:** Comprehensive docstrings and inline comments across all 12 backend files (1,658 lines added).
- **Files updated:** `utils/normalization.py`, `services/matching.py`, `services/market_analysis.py`, `services/pricing_engine.py`, `services/llm_explainer.py`, `services/dashboard_service.py`, `services/__init__.py`, `scripts/train_pricing_model.py`, `app.py`, `tests/test_pricing_engine.py`, `tests/test_llm_explainer.py`, `tests/test_dashboard_service.py`.
- **Tests:** 144/144 passing — zero regressions.
- **Commit:** `842642d`

### 2026-08-24 — KPDN Price Control Guardrails

- **Added:** `is_price_controlled` and `government_ceiling_price` columns to Product model.
- **Added:** Rule 0 (Regulatory Cap) in pricing engine — hard-caps ML price at KPDN ceiling.
- **Added:** Gemini prompt injection for Barangan Kawalan warning context.
- **Added:** Official KPDN ceiling prices for Gula Putih (RM 2.85) and Minyak Masak (RM 2.50).
- **Added:** Product form checkbox with JavaScript toggle for ceiling price input.
- **Added:** Hand-written Flask-Migrate migration `a1b2c3d4e5f6`.
- **Commit:** `997dd8d`

### 2026-08-26 — FYP Presentation Prep & Test Fix

- **DEMO_ROLES_AND_SCRIPT.md:** Comprehensive presentation script with WHAT/WHY/HOW/EVIDENCE/RESULT 5-part communication framework for all 3 team members.
- **TEAM_ROLE_MASTERY_GUIDE.md:** Deep-comprehension knowledge base with domain mastery breakdowns, 5-part mental model, and Q&A prep per student.
- **Test fix:** Resolved 7 `test_market_models` failures under pytest caused by missing Flask app context. Removed `conftest.py` autouse fixture (which interfered with other tests' own app context management) and wrapped each test function in `with app.app_context():` instead. Result: **84/84 tests passing**.
- **Commits:** `566e79b`, `14fa66a`

### 2026-08-23 — Phase 4 Complete

- **Phase 4A:** Dashboard analytics with metrics cards and Action Required panel.
- **Phase 4B:** Data quality cleanup, demo seed overhaul with correct KPDN products.
- **Phase 4C:** Complete documentation suite (README, DEMO, API_REFERENCE, SCREENSHOT_GUIDE).
- **Phase 4D:** Production hardening — logging configuration, .env.example, error handler improvements.
- **Phase 4E:** Integration test (full E2E flow), pytest.ini configuration.
- **Gemini model:** Updated through 2.5-flash → 3.6-flash → 2.5-flash-lite → **3.5-flash-lite** (current).
- **Commits:** `3aa0fa9` through `aba6fbd`

### 2026-08-22 — Phase 3D–3F (ML & AI)

- **Phase 3D:** Market Analysis Engine with PPI calculation and unit scaling.
- **Phase 3E:** ML Pricing Engine with RandomForestRegressor, synthetic training pipeline, and 4 guardrails.
- **Phase 3F:** Gemini LLM integration with 3-layer fault-tolerant fallback.
- **Commits:** `0cd7ee8` through `51c7974`

### 2026-08-18 — Phase 3A–3B (Market Data Foundation)

- **Phase 3A:** MarketSource, MarketItem, MarketPriceObservation, ProductMarketMatch models; normalization utilities.
- **Phase 3B:** Idempotent PriceCatcher ETL script with dirty unit parser.
- **Commits:** `26c3af6` through `1cd06ab`

### 2026-08-06–12 — Phase 1–2C (Core Foundation)

- **Phase 1:** Flask app, SQLAlchemy models, authentication, product management.
- **Phase 2A:** Product identity (PriceHistory, PriceCatcherItem).
- **Phase 2B:** Sales, Inventory, InventoryAdjustment with atomic transactions.
- **Phase 2C:** Employee invitation system, in-app notifications, shop membership.
- **Commits:** `23adf9d` through `e676040`
