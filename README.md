# ShelfSenseAI

ShelfSenseAI is a Flask-based retail management and market intelligence application developed as a Final Year Project (FYP). It helps local retailers understand their product pricing relative to market conditions and make better, data-driven pricing decisions using deterministic calculations, Machine Learning (Random Forest), and Generative AI (Google Gemini).

## System Architecture

ShelfSenseAI follows a decoupled architecture, separating core retail management from the market intelligence engine.

*   **Core Retail:** Shop management, Auth, Employee roles, Inventory, Sales, Price History.
*   **Market Intelligence:** Idempotent ETL pipeline (PriceCatcher), geographic market localization (3-tier fallback), exact/fuzzy matching (RapidFuzz), statistical market analysis (PPI), and Explainable AI (raw competitor observation data).
*   **AI/ML Engine:** Random Forest Regressor trained on 4.5 years of real KPDN PriceCatcher data (2022-2026, 5.4M+ observations) with deterministic guardrails, and Gemini LLM (3-layer fallback for natural-language explanations).

## Technology Stack

*   **Backend:** Python 3, Flask, SQLAlchemy, MySQL
*   **Machine Learning:** Scikit-learn (RandomForestRegressor)
*   **AI Integration:** Google GenAI SDK (Gemini 3.5 Flash Lite)
*   **Data Matching:** RapidFuzz
*   **Frontend:** HTML5, Bootstrap 5, Chart.js, Vanilla JS (Fetch API)
*   **Testing:** Pytest

## Setup & Installation

### Prerequisites
*   Python 3.9+
*   MySQL Server
*   `pip install -r requirements.txt` (includes `pandas`, `pyarrow`, `scikit-learn`, `rapidfuzz`, `google-genai`)

### Step 1 — Environment Setup
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Create a `.env` file based on `.env.example`:
```env
SECRET_KEY=your_secret_key
DATABASE_URL=mysql+pymysql://user:pass@localhost/shelfsenseai
GEMINI_API_KEY=your_gemini_api_key
```

### Step 2 — Database Migration & Data Loading
```bash
flask db upgrade
python import_pricecatcher.py --month 2026-08       # one month
python import_pricecatcher.py --start-month 2026-06 --end-month 2026-08   # backfill range
python scripts/etl_pricecatcher.py
python seed_demo.py
```

Both steps are **idempotent and non-destructive**:

* `import_pricecatcher.py` downloads monthly PriceCatcher Parquet files and appends them to the raw archive (`price`, `lookup_item`, `lookup_premise`). Months coexist — importing a new month never deletes older months, and re-importing the same month inserts zero duplicates (a database unique constraint on `date + premise_code + item_code` guards the archive). `--local-dir ./pc_cache` caches downloaded files for offline re-runs.
* `scripts/etl_pricecatcher.py` rebuilds **premise-level** `MarketPriceObservation` rows — one raw price record becomes one observation with its exact price, `premise_code`, state/district, and per-observation normalized unit price. It only rebuilds the PriceCatcher source's observations (ManaMurah/FAMA are untouched), keeps `MarketItem` ids stable so `ProductMarketMatch` survives, and is safe to rerun (zero duplicates via the `uq_premise_obs` unique index on `market_item_id + premise_code + observed_at`).

### Step 3 — Train the ML Model (IMPORTANT)

The ML pricing engine requires a trained model file (`ml/pricing_model.pkl`). This is **not** checked into Git — you must train it locally using the included training script.

```bash
python scripts/train_pricing_model.py
```

This script will:
1. Download **56 monthly KPDN PriceCatcher datasets** (Jan 2022 – Aug 2026) from Malaysia's [Open Data portal](https://data.gov.my).
2. Merge with item and premise lookup tables.
3. Filter for **Johor state** (customizable via `TARGET_STATE` / `TARGET_DISTRICT` constants in the script).
4. Engineer features: market median, min, max, spread, cost price, target margin.
5. Train a **RandomForestRegressor** using scikit-learn.
6. Save the model to `ml/pricing_model.pkl` (~633 KB).

**Expected output:**
```
Processed 56/56 months (0 failed)
Unique items: 325
Training samples: 523,590
Model MAE: RM0.0639 (6.4 sen average error)
Model R²: 0.9999
Top features: cost_price (88.1%), market_median (11.1%), target_margin (0.7%)
Model saved to ml/pricing_model.pkl (633 KB)
```

> **Note:** The training requires downloading ~300 MB of parquet files from data.gov.my. The script is memory-efficient — it processes each month individually instead of loading all files at once.

### Step 4 — Sync ManaMurah Market Data (Optional, Phase 5)

ShelfSenseAI can augment its KPDN PriceCatcher data with **FAMA Panduan Harga Harian** daily prices via the [ManaMurah MCP server](https://github.com/manamurah/mcp-server) — a public, read-only MCP endpoint (no API key required):

```bash
python scripts/sync_market_data.py              # national grain, last 30 days
python scripts/sync_market_data.py --days 14 --state johor   # state grain
```

The script is fully idempotent — run it as many times as you like; a second run inserts zero duplicates. It ingests only FAMA's independent daily catalogue (eggs, rice, staples) and never duplicates PriceCatcher data.

### Step 5 — Run the Application
```bash
flask run
```

## Development Phases

| Phase | Focus | Key Deliverables |
|-------|-------|------------------|
| **1–2** | Core Foundation | Auth, RBAC, Shop isolation, Products, Inventory, Sales, Employee invitations, Notifications |
| **3A–B** | Market Data Foundation | MarketSource/MarketItem/MarketPriceObservation models, idempotent ETL pipeline |
| **3C–D** | Market Intelligence | RapidFuzz 75/25 matching, geographic localization, statistical analysis (PPI) |
| **3E** | ML Pricing Engine | RandomForestRegressor trained on 5.4M+ real KPDN observations (2022–2026) |
| **3F** | AI Explainer | Gemini 3.5 Flash Lite with 3-layer fault-tolerant fallback |
| **4** | Hardening | Dashboard analytics, KPDN price control guardrails, Explainable AI, 84/84 tests passing |
| **5** | Multi-Source Market Data | ManaMurah MCP client + source-agnostic ingestion layer, idempotent FAMA daily-price sync |
| **6** | Historical Archive + Premise-Level Observations | Configurable monthly PriceCatcher import (no `replace`), raw archive with unique natural key, premise-level `MarketPriceObservation` (100% raw retention), premise column in Explainable AI table |
| **6 (MI)** | Market Intelligence & Visualization | Market summary stats (min/median/mean/max/spread/premise count), geographic tier labeling, latest-snapshot competitor table with store names, market position (Below/Near/Above vs median), min→max distribution bar, SVG historical trend chart (server-side `PERCENTILE_CONT` medians), pricing-engine market context (guardrails untouched) |
| **7** | Intelligent Pricing Decision Support | Explainable recommendation status (MAINTAIN/REDUCE/INCREASE with documented tolerance), market trend classification (Rising/Stable/Falling/insufficient), market evidence rating (Strong/Moderate/Limited/Unavailable), guardrail-effect explanations, decision-support badges in the pricing pane; existing guardrails/ML/Gemini roles unchanged, Gemini explains but never sets prices, 159 tests passing |
| **7.1** | Validation & Corrective Fixes | Full read-only audit (all scenarios validated); SME margin "blend" corrected to an accurately-documented hard target-margin floor (behaviour unchanged), pricing API now passes the shop context so the JS pane uses the same district→state→national tier as the page, recommendation-difference badge wording fixed, 161 tests passing |
| **8** | Pricing Decision Workflow & Auditability | Every reviewed recommendation is snapshotted as a `PricingRecommendationDecision` (recommended price, current price, engine status, market tier/median, evidence, trend, guardrails, timestamp). Retailer explicitly **Apply**s (atomic: price update + existing PriceHistory + decision APPLIED) or **Dismiss**es with a recorded reason. Stale-snapshot protection rejects applies when the product price changed after the snapshot; the applied price is always recomputed server-side by the deterministic engine (client-supplied prices are ignored). Recommendation-decision history card on the product page; staff role read-only, cross-shop 403, CSRF-protected POST-only actions, 175 tests passing |
| **9** | Shop-wide Pricing Intelligence Dashboard & Reporting | `/pricing-dashboard` (owner/manager): summary cards (total / market-covered / above-below-market / pending / applied-dismissed), pricing opportunity table with deterministic priority sorting (engine status + evidence — never moves a price), filters (status, decision, evidence, tier, search), server-side sorting + pagination, lightweight distribution panels (status / decisions / market position), proper empty states, product drill-down into the Phase 8 workflow. Read-only GET: no price changes, no decision records created by dashboard views; reuses engine + market services with `skip_llm` (Gemini never involved), 185 tests passing |
| **FYP** | Presentation | `DEMO_ROLES_AND_SCRIPT.md` (5-part speaking framework), `TEAM_ROLE_MASTERY_GUIDE.md` (deep comprehension) |

## ML Training Details

The pricing model is trained on **real government data** from [data.gov.my/pricecatcher](https://storage.data.gov.my/pricecatcher/):

*   **Data source:** KPDN (Kementerian Perdagangan Dalam Negeri dan Kos Sara Hidup) PriceCatcher dataset
*   **Coverage:** 56 monthly files (January 2022 – August 2026)
*   **Raw observations:** 5,438,940 price records from ~3,900 premises
*   **Localization:** Filtered for Johor state (325 unique items)
*   **Training samples:** 523,590 (aggregated by item × date)
*   **Model:** RandomForestRegressor (scikit-learn)
*   **Performance:** MAE = RM0.0639 (6.4 sen), R² = 0.9999

The training script (`scripts/train_pricing_model.py`) downloads data from `storage.data.gov.my` automatically. It uses a memory-efficient pipeline that processes each month individually rather than loading all 56 files into memory at once.

## FYP Presentation Documents

| Document | Purpose |
|----------|---------|
| `DEMO_ROLES_AND_SCRIPT.md` | Verbatim speaking scripts with WHAT/WHY/HOW/EVIDENCE/RESULT framework for all 3 team members |
| `TEAM_ROLE_MASTERY_GUIDE.md` | Deep-comprehension knowledge base with domain mastery breakdowns and Q&A prep |
| `DEMO.md` | Quick demo walkthrough for the live demonstration |
| `API_REFERENCE.md` | Complete API endpoint documentation |
| `SCREENSHOT_GUIDE.md` | Step-by-step screenshot guide for the FYP report |

## Testing

```bash
# Run all 84 tests under pytest
python -m pytest tests/ -v

# Run standalone tests (also 84 tests)
python tests/test_market_models.py
python tests/test_normalization.py
```

All 84 tests pass with zero failures. Tests cover: normalization (16), market models (7), matching (15), market analysis (13), pricing engine (10), LLM explainer (7), dashboard metrics (7), employee removal (8), and end-to-end integration (1).
