# ShelfSenseAI

ShelfSenseAI is a Flask-based retail management and market intelligence application developed as a Final Year Project (FYP) for Malaysian small retail (kedai runcit). It helps local retailers understand their product pricing relative to verified market observations and make better, human-approved pricing decisions. **AI recommends and explains; business guardrails remain authoritative; human approval remains part of the decision process.** There is no autonomous repricing.

## 1. Project Overview
Decision Support System (DSS) for dry-goods retail. The shop sets cost, target margin, and selling price; the system shows where that price sits versus the local market and explains a guardrailed recommendation. The retailer explicitly applies or dismisses.

## 2. Problem Statement
Small retailers lack visibility into hyper-local market pricing (district → state → national) and risk either underpricing (margin loss) or overpricing (lost sales) or breaching KPDN price controls / PCAPA. ShelfSenseAI makes the market visible and the recommendation traceable.

## 3. Project Scope
- Malaysian local retail, dry goods (PriceCatcher `BARANGAN SEGAR` and `MAKANAN SIAP MASAK` excluded)
- Products do NOT need to exist in PriceCatcher — a product stands alone even with no market match
- PriceCatcher is one market source, not the only source (ManaMurah/FAMA is supplementary)
- Fresh goods / ready-to-eat are outside core scope
- No real-time PriceCatcher API; data is via monthly Parquet archive + ETL

## 4. Key Features
- Shop isolation, Owner/Manager/Staff RBAC, product CRUD with brand/category/quantity/unit
- Sales & inventory (atomic sale + stock decrease, adjustments, no negative stock)
- Employee invitations and notifications, shop create/join
- PriceCatcher autocomplete, RapidFuzz matching (exact/fuzzy/manual, verified/rejected)
- Market intelligence: min/max/mean/median/spread, quartiles, premise/observation counts, market position (Below/Near/Above, 5% band), competitor snapshot (latest date only), historical trend (server-side `PERCENTILE_CONT`), district→state→national fallback
- Intelligent pricing: ML candidate → KPDN cap → cost floor (×1.05) → SME target-margin floor → market sanity → PCAPA informational warning → final price; status (MAINTAIN/REDUCE/INCREASE), evidence, trend
- Human decision workflow: PENDING snapshot → Apply (atomic price+PriceHistory+APPLIED, stale protection, server-side recomputation) / Dismiss (reason/note) — no automatic repricing, idempotent, CSRF, shop isolation
- Shop-wide pricing intelligence dashboard (`/pricing-dashboard`, read-only, `skip_llm`)
- Market data automation: unified refresh service, persistent audit, health/freshness, monitoring UI, OS scheduling

## 5. Technology Stack
- **Backend:** Python 3.9+, Flask, Flask-SQLAlchemy, Flask-Migrate/Alembic, MySQL/MariaDB, PyMySQL
- **ML/AI:** scikit-learn `RandomForestRegressor`, Google GenAI SDK `Gemini 3.5 Flash Lite` (3-layer fallback)
- **Market:** Pandas, PyArrow, RapidFuzz, `mcp` SDK (ManaMurah streamable HTTP)
- **Frontend:** HTML5, Bootstrap 5, Vanilla JS (Fetch API), Jinja2
- **Testing/Security:** Pytest, Flask-Login, Flask-WTF CSRF, Werkzeug bcrypt
- **No Celery/Redis/Kafka/APScheduler** — scheduling is OS-level (cron / Task Scheduler) invoking the Flask CLI

## 6. Architecture
```
Market Sources (PriceCatcher monthly Parquet / ManaMurah FAMA daily)
        ↓
Market Data Refresh Service (reuses import_pricecatcher + ETL + mcp_client/market_ingestion)
        ↓
MarketRefreshRun (persistent audit: inserted/updated/duplicates/rejected/errors, latest_observed_at, status, triggered_by)
        ↓
Health / Freshness (source-specific thresholds, conservative multi-source)
        ↓
MarketPriceObservation (premise-level, 1 raw → 1 observation, ~1.97M rows, indexed)
        ↓
Market Intelligence (geographic 3-tier fallback, server-side aggregation)
        ↓
Pricing Recommendation (ML candidate → guardrails → final price → decision-support signals → Gemini explanation)
        ↓
Human Decision (Apply/Dismiss) → PriceHistory + PricingRecommendationDecision
```
- **Pricing hierarchy (authoritative):** ML/Rule Candidate → KPDN Regulatory Cap → Cost Floor (×1.05) → SME Target-Margin Floor → Market Sanity → **FINAL PRICE** → Decision-support metadata → Gemini explanation → Human Apply/Dismiss. Freshness never overrides guardrails.
- **Market freshness:** `fresh/aging/stale/unavailable` per source (ManaMurah 3/7d, PriceCatcher 35/60d), multi-source conservative (fresh+stale→stale), evidence (`Strong/Moderate/Limited/Unavailable`) and freshness are separate dimensions.

## 7. User Roles / RBAC
- **Owner:** full access (products, sales, inventory, employees, invitations, pricing decisions, market-data monitoring, pricing dashboard)
- **Manager:** products, sales, inventory, pricing decisions, monitoring, dashboard (no employee invite/revoke)
- **Staff:** view dashboard/product pages, record sales (no product add/edit/delete, no pricing apply/dismiss, no monitoring)
- **Unassigned:** created via “Join Shop”, must accept invitation to gain shop linkage; enforced via `@login_required` + `@role_required` + `shop_id` checks (shop isolation on every query, 403 on cross-shop).

## 8. Product and Inventory Functionality
- Product identity: name, brand, category, quantity, unit, cost_price, selling_price, target_margin, baseline_margin (PCAPA anchor), `is_price_controlled` + `government_ceiling_price`; `size_label` (package) vs `Inventory.current_stock` (shelf count).
- Sales: per-unit price snapshot at sale time, atomic sale + stock decrease, revenue = qty×price (not stored).
- Inventory: `minimum_stock`, manual adjustments with reason/user, no negative stock.

## 9. Market Data Architecture
- **Models:** `MarketSource` (PriceCatcher/ManaMurah), `MarketItem` (external_id, raw/normalized title, package size), `MarketPriceObservation` (premise_code, regular/promo/effective, normalized_unit_price, state/district, observed_at, unique `uq_market_obs_premise`), `ProductMarketMatch` (shop_product ↔ market_item, confidence, `is_verified/is_rejected`).
- **Normalization:** `utils/normalization.clean_text`, `normalize_package_size` (g→kg, ml→l), `calculate_unit_price` (RM/base unit, pure, tested).
- **Indexes:** `ix_market_item_normalized_title`, `ix_observed_at`, `ix_market_obs_item_geo(market_item_id,state,district,observed_at)`, `uq_market_obs_premise`, `ix_market_item_external`, `uq_price_natural(date,premise_code,item_code)`.

## 10. PriceCatcher
- Monthly Parquet from `storage.data.gov.my/pricecatcher` → raw archive (`lookup_item`, `lookup_premise`, `price`) with natural-key unique index; additive, never deletes months; `import_pricecatcher.py --month/--start-month/--end-month --local-dir --dry-run` + chunked upserts.
- `scripts/etl_pricecatcher.py` rebuilds premise-level observations (1 raw → 1 observation, exact price, no averaging), stable `MarketItem` ids (so `ProductMarketMatch` survives), source-isolated (`WHERE source_id=:sid`), unique index prevents duplicates.
- Denormalized `price_catcher_item` for autocomplete (all 405 items, not only those with observations).

## 11. ManaMurah / FAMA
- Public read-only MCP endpoint `https://mcp.manamurah.com/mcp` (no credentials) via `services/mcp_client.py` (streamable HTTP, JSON-RPC, defensive parsing, plain-dict records). Only FAMA `fama_price_history` (daily, independent catalogue `1..46`) is ingested — KPDN weekly tools are intentionally ignored to avoid duplicating PriceCatcher. `FAMA_SCOPE_ITEMS` (5 dry-goods: TELUR AYAM, BERAS SAWAH/IMPORT, SANTAN BERSARIKAT, UBI KENTANG) is hard-wired; `services/market_ingestion.py` validates, normalizes via `utils`, upserts by `(source_id,external_id)` + `(market_item_id,observed_at,state,district)`, per-item commit/rollback.
- `scripts/sync_market_data.py --days 30 --state johor` — idempotent, source-isolated, never purges real ManaMurah demo data.

## 12. Product-Market Matching
- RapidFuzz `WRatio` + `token_set_ratio` 75/25 blend on `clean_text` titles, filtered by `ProductMarketMatch.is_verified` (owner/manager only) and `is_rejected`. Suggestions are unverified until confirmed; adding a product auto-suggests candidates.

## 13. Market Evidence
- `_market_evidence` in `pricing_engine.py`: `Unavailable n<3 or premises<1`, `Limited <5 premises or <20 obs`, `Moderate ≥3 premises & ≥5 obs`, `Strong ≥5 premises & ≥20 obs & district/state tier`. Premise/observation counts and tier come from `get_market_stats`.

## 14. Market Freshness
- Centralized `services/market_freshness.py` reusing 10C thresholds: ManaMurah `fresh 0-3d, aging 4-7d, stale >7d`; PriceCatcher `fresh 0-35d, aging 36-60d, stale >60d`; generic `7/14d`. `classify_market_freshness(source, latest_observed_at)` and `get_product_market_freshness(product_id, shop)` (same `district→state→national` filtering as `get_market_stats`, per-source latest, most conservative when multiple sources contribute: `fresh+stale→stale`). Freshness is derived, not persisted; historical `PricingRecommendationDecision` rows remain immutable.

## 15. Pricing Recommendation Pipeline
- **Candidate:** ML `RandomForestRegressor` (523k Johor samples, MAE RM0.0639, R² 0.9999) or rule fallback `cost×(1+margin)`.
- **Guardrails (strict order):** KPDN cap (if `is_price_controlled`), cost floor, SME target-margin floor (hard minimum), market sanity (0.7×min to 1.5×max), PCAPA informational warning (tolerance 1.5pp).
- **Signals:** `status` (±0.5% band), `market_evidence`, `trend` (`_classify_trend` on 90-day `get_market_trend` medians), `guardrail_effect`, `market_freshness`/`freshness_warning` (10F, explanatory only), confidence (`high/medium/low`).
- **No automatic repricing** — `Product.selling_price` only changes via explicit Phase 8 Apply.

## 16. AI / Gemini Role
- `services/llm_explainer.py` — Gemini **explanation-only**: builds a prompt from deterministic facts (cost, margins, median, guardrails, evidence, trend, freshness) and asks for 2-3 sentences <100 words. Three-layer fallback: live Gemini → exception catch → deterministic `_fallback` string. Gemini never sets `freshness`, `evidence`, `status`, `price`, or guardrails.

## 17. Human Approval Workflow
- `services/pricing_workflow.py` + `PricingRecommendationDecision` + migration `f9e8d7c6b5a4` (PENDING→APPLIED/DISMISSED, `current_price` staleness check, server-side recomputation, atomic `Product`+`PriceHistory`+decision, `DISMISS_REASONS`, idempotent apply, `shop_id` FK for cross-shop 403).
- Routes: `POST /api/product/<pid>/decision` (snapshot, reuse identical PENDING), `POST /api/decision/<did>/apply` (stale 409, recomputed price), `POST /api/decision/<did>/dismiss` (reason/note, 200). Product page records snapshot lazily for owner/manager only.

## 18. Market Monitoring
- `GET /market-data` (Owner/Manager, Staff 403) — `services/market_refresh_status` + `market_refresh_health`: per-source cards (Healthy/Warning/Unhealthy badge, latest refresh `status/finished_at` or `Never refreshed`, latest successful, latest observation or `No observations`, metrics `inserted/updated/duplicates/rejected/errors`), health details (fresh/aging/stale, partial/failed, error messages sanitized), and recent history table (30 rows, Date/Time, Source, Status, Inserted, Updated, Duplicates, Rejected, Errors, Latest Observation, Triggered By). Read-only, never triggers refresh or creates decisions.

## 19. Refresh History
- `MarketRefreshRun` + migration `33b959a98288` (`source_name, started_at, finished_at, status success/partial/failed, inserted/updated/duplicates_skipped/rejected/errors, latest_observed_at, error_message (sanitized), triggered_by manual/scheduled/test`, index `source_name,started_at`). One row per source per invocation, so `source=all` with one success + one fail preserves both truths.

## 20. Health Monitoring
- `services/market_refresh_health.py` — per-source health from `MarketRefreshRun` + live `COUNT(*)/MAX(observed_at)`: checks refresh status, observation availability, freshness (source-specific thresholds), recency, metrics; derives `healthy/warning/unhealthy`. CLI `flask market-data health --source all|pricecatcher|manamurah` (read-only).

## 21. Scheduling
- **No Flask background scheduler** (no Celery/Redis/Kafka/APScheduler). OS scheduler invokes the existing Flask CLI:
  - Windows Task Scheduler: Program `venv\Scripts\python.exe`, Arguments `-m flask --app app market-data refresh --source manamurah --scheduled` (daily 06:30) and `... --source pricecatcher --scheduled` (monthly 5th), Start in `<project-root>`.
  - Linux cron: `30 6 * * * cd /path && venv/bin/python -m flask --app app market-data refresh --source manamurah --scheduled`
- `--scheduled` sets `triggered_by=scheduled` (vs `manual`/`test`) so history distinguishes manual vs scheduled; exit 0 on success, non-zero if any source failed; failures are persisted as `failed`/`partial` with sanitized `error_message`, previous observations remain, no price/decision mutation. Concurrency: daily vs monthly, <2 min, source-isolated `WHERE source_id=:sid` + unique indexes make overlap idempotent; no distributed lock. See `docs/MARKET_DATA_SCHEDULING.md`.

## 22. Installation
```bash
python -m venv venv
# Windows: venv\Scripts\activate  # Linux: source venv/bin/activate
pip install -r requirements.txt  # pandas, pyarrow, scikit-learn, rapidfuzz, google-genai, mcp
```

## 23. Environment Setup
Create `.env` from `.env.example`:
```env
SECRET_KEY=your_secret_key
DATABASE_URL=mysql+pymysql://user:pass@localhost/shelfsenseai
GEMINI_API_KEY=your_gemini_api_key   # optional; fallback works without it
# Optional override for self-hosted ManaMurah MCP:
# MANAMURAH_MCP_URL=https://mcp.manamurah.com/mcp
```

## 24. Running the Application
```bash
flask db upgrade
python import_pricecatcher.py --month 2026-08
python scripts/etl_pricecatcher.py
python seed_demo.py
python scripts/train_pricing_model.py   # downloads 56 months, ~300MB, outputs 633KB model
flask run
```

## 25. CLI Commands
```bash
flask market-data refresh --source manamurah --dry-run
flask market-data refresh --source pricecatcher --dry-run
flask market-data refresh --source all --dry-run
flask market-data refresh --source manamurah --days 14 --state johor
flask market-data refresh --source manamurah --scheduled   # for cron/Task Scheduler
flask market-data health --source all
flask market-data health --source pricecatcher
```

## 26. Testing
```bash
pytest -q
# Expected at Phase 10F: 256 passed, 0 failed
```
Coverage: normalization, market models/matching/analysis/intelligence, pricing engine (guardrails, SME floor, KPDN cap, PCAPA, confidence), LLM fallback, dashboard, employee removal, integration, market ingestion/MCP, import/ETL, refresh service/status/health/freshness, monitoring UI (RBAC, source isolation, read-only), scheduling (manual/scheduled/partial/dry-run).

## 27. Current Project Status
- **Branch:** `main`, **Head:** `33b959a98288_add_market_refresh_run` (prior `fa7b68d` + uncommitted 10A-F)
- **DB:** `MarketSource 2, MarketItem 410, MarketPriceObservation 1,970,997, Product 17, PriceHistory 21, PricingRecommendationDecision 3 PENDING, MarketRefreshRun 0` after test cleanup (demo product 7 `24.68`)
- **Tests:** `256 passed` at 10F validation point (was 185 at 9F, 197 at 10A, 204 at 10B, 216 at 10C, 228 at 10D, 238 at 10E)
- **Migration head:** `33b959a98288`

## 28. Limitations
- No real-time PriceCatcher API (monthly archive, fresh ≤35d is expected)
- ManaMurah is FAMA daily (5 dry-goods items) — not a full competitor catalogue
- Historical trend is server-side `PERCENTILE_CONT` over 90 days, not a forecast
- Scheduling is OS-driven; no in-app job queue
- Freshness is a qualification of evidence, not a pricing guardrail

## 29. Future Work
- Additional market sources (if KPDN adds APIs), finer premise geocoding, richer trend analytics, scheduled email summaries for Owner/Manager, and further FYP presentation polish — all as human-approved, non-autonomous enhancements.

> **AI recommends and explains. Business guardrails remain authoritative. Human approval remains part of the decision process. Freshness qualifies evidence; it never overrides KPDN cap, cost floor, SME floor, or market sanity.**
