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
**Last Updated:** 2026-08-25

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

Aggregates price observations for verified market matches:

1. Fetches all `MarketPriceObservation` rows for verified `ProductMarketMatch` entries.
2. Scales each observation's `normalized_unit_price` to the shop product's package size (e.g., RM/kg × 10 for a 10 kg bag).
3. Filters invalid prices (≤0, None).
4. Computes: N, min, max, mean, median, spread, and **Price Position Index (PPI)**.

**PPI Formula:**
```
PPI = (shop_selling_price / market_median) × 100
```
- PPI = 100: exactly at market median
- PPI = 110: 10% above median (overpriced)
- PPI = 90: 10% below median (underpriced)

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

### 4.4 Deterministic Guardrails (4 Rules in Strict Order)

The ML prediction is intercepted by four sequential safety rules:

| Rule | Name | Logic | Behavior |
|---|---|---|---|
| **Rule 0** | Regulatory Cap | If `is_price_controlled` and price > `government_ceiling_price` → cap at ceiling | **Hard cap** — overrides ML output |
| **Rule 1** | Cost Floor | If price < `cost_price × 1.05` → raise to floor | **Hard cap** — prevents selling at loss |
| **Rule 2** | Market Sanity | If price < 70% of market_min or > 150% of market_max → clamp | **Hard cap** — prevents extreme outliers |
| **Rule 3** | PCAPA Check | If margin > baseline AND cost hasn't risen → warning | **Soft warning** — informational, not forced |

**Key guarantee:** The recommended price is ALWAYS ≥ cost × 1.05 (Rule 1) and NEVER exceeds the KPDN ceiling for controlled goods (Rule 0).

### 4.5 Gemini LLM Integration (`services/llm_explainer.py`)

**Three-Layer Fault-Tolerant Fallback:**

| Layer | Trigger | Behavior |
|---|---|---|
| **Layer 1** | Normal operation | Live Gemini 3.5 Flash Lite API call with structured prompt |
| **Layer 2** | API failure | try/except catches network errors, rate limits, missing API key |
| **Layer 3** | Fallback | Deterministic string template using same data, no external calls |

**Prompt structure:** Injects product name, cost, market median, recommended price, guardrails triggered, PCAPA status, and regulatory context. LLM is instructed to explain in 2–3 concise sentences under 100 words.

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

### 5.6 Documentation Suite

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
