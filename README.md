# ShelfSenseAI

**Decision support for kedai runcit pricing — market intelligence, guardrailed AI recommendations, human approval.**

**Live demo:** https://app.shelfsenseai.online

<!-- ![ShelfSenseAI Pricing Dashboard](docs/screenshots/pricing-dashboard.png) -->

> **Want to run it locally?** See [Local Development Setup](#local-development-setup).
> **Want to understand the architecture?** See [Architecture](#architecture).
> **Want to know how it's deployed?** See [Production Deployment](#production-deployment-cloudflare-tunnel).

ShelfSenseAI is a Flask-based retail management and market intelligence application developed as a Final Year Project (FYP) for Malaysian small retail (kedai runcit). It helps local retailers understand their product pricing relative to verified market observations and make better, human-approved pricing decisions. **AI recommends and explains; business guardrails remain authoritative; human approval remains part of the decision process.** There is no autonomous repricing.

## Problem Statement

Small retailers lack visibility into hyper-local market pricing (district → state → national) and risk underpricing (margin loss), overpricing (lost sales), or breaching KPDN price controls / PCAPA. KPDN publishes PriceCatcher market data openly, but it is hard for a shop owner to use. ShelfSenseAI makes the market visible and every recommendation traceable.

## Key Features

All features below are deployed and working on the live site.

- **Authentication & RBAC** (live) — register, login, logout; Owner / Manager / Staff roles with shop isolation on every query.
- **Shop Management** (live) — create shop with state/district, employee invitations and notifications, join-shop flow, employee removal.
- **Product & Inventory** (live) — product CRUD with brand, category, quantity, unit, cost/selling price, target margin and PCAPA baseline margin; inventory receive/adjust with minimum stock and no-negative-stock protection.
- **Sales** (live) — record sales with per-unit price snapshots; atomic sale-plus-stock-decrease; over-selling blocked.
- **Market Data** (live) — PriceCatcher autocomplete, RapidFuzz matching (exact/fuzzy/manual, verify/reject), premise-level observations.
- **Pricing Recommendations** (live) — ML candidate → KPDN cap → cost floor (×1.05) → SME target-margin floor → market sanity → PCAPA informational warning → final price, with status (MAINTAIN/REDUCE/INCREASE), evidence, trend, freshness, and Gemini explanation.
- **Dashboard** (live) — shop-wide pricing intelligence table with filters, sorting, priority ranking (read-only); market-data monitoring page with per-source health and refresh history.

## Architecture

```text
User (browser, HTTPS)
  → Cloudflare Edge (TLS, shelfsenseai.online)
  → Cloudflare Tunnel (cloudflared, HTTP/2)
  → Local laptop — waitress serving Flask on 127.0.0.1:5000
  → Flask app → TiDB Cloud (MySQL-compatible, TLS)
```

- **Market data sources:** PriceCatcher (KPDN, monthly Parquet archive + ETL into premise-level observations) and ManaMurah/FAMA (live MCP HTTP sync, dry-goods scope only).
- **ML model:** scikit-learn `RandomForestRegressor` (~5 MB pickle, loaded locally from `ml/pricing_model.pkl`, cached after first load; rule-based fallback if absent).
- **Gemini:** Google GenAI `Gemini 3.5 Flash Lite`, explanation-only with deterministic fallback — it never sets prices, evidence, or guardrails.
- **Stack:** Python 3.12, Flask, Flask-SQLAlchemy, Flask-Migrate/Alembic, PyMySQL, Bootstrap 5 + Jinja2, pytest. No Celery/Redis/Kafka — scheduling is OS-level.

## Local Development Setup

For a fresh machine:

```bash
python -m venv venv
# Windows: venv\Scripts\activate  # Linux: source venv/bin/activate
pip install -r requirements.txt
```

Create `.env` from `.env.example`:
```env
SECRET_KEY=your_secret_key
DATABASE_URL=mysql+pymysql://user:pass@localhost/shelfsense_db
GEMINI_API_KEY=your_gemini_api_key   # optional; fallback works without it
```

Then:
```bash
flask db upgrade
python import_pricecatcher.py --month 2026-08
python scripts/etl_pricecatcher.py
python seed_demo.py
python scripts/train_pricing_model.py
flask run
```

## Production Deployment (Cloudflare Tunnel)

The app runs on the developer's Windows laptop (waitress, port 5000) and is exposed through a Cloudflare Tunnel at https://app.shelfsenseai.online. The database is TiDB Cloud Starter; DNS for `shelfsenseai.online` (Exabytes) is managed by Cloudflare.

**Prerequisites:** a Cloudflare account, the domain's nameservers pointed to Cloudflare, and `cloudflared` installed on Windows.

**Setup steps:**
1. Install cloudflared: `winget install Cloudflare.cloudflared`
2. `cloudflared tunnel login`
3. `cloudflared tunnel create shelfsenseai`
4. Create `%USERPROFILE%\.cloudflared\config.yml` with the tunnel ID, credentials-file path, `protocol: http2`, and ingress rules routing `app.shelfsenseai.online` to `http://127.0.0.1:5000`
5. `cloudflared tunnel route dns shelfsenseai app.shelfsenseai.online`
6. Start Flask: `waitress-serve --host=127.0.0.1 --port=5000 --threads=10 app:app` (install waitress locally first: `pip install waitress`)
7. Start the tunnel: `cloudflared tunnel run shelfsenseai`

**Important:** the laptop must stay on and connected, and both processes (waitress + cloudflared) must keep running — otherwise the site goes down.

**Why HTTP/2:** many Malaysian ISPs block outbound QUIC/UDP, so `protocol: http2` in `config.yml` keeps the tunnel reliable.

**Alternative:** Render free tier (`render.yaml` and `Procfile` are committed as a fallback), but it is slower and memory-constrained.

## CLI Commands

```bash
flask market-data refresh --source manamurah --dry-run
flask market-data refresh --source pricecatcher --dry-run
flask market-data refresh --source all --dry-run
flask market-data refresh --source manamurah --days 14 --state johor
flask market-data refresh --source manamurah --scheduled   # for Task Scheduler / cron
flask market-data health --source all
flask market-data health --source pricecatcher
```

## Testing

```bash
pytest -q
# Expected: 256 passed, 0 failed
```

Coverage includes normalisation, market models/matching/analysis, pricing engine (guardrails, SME floor, KPDN cap, PCAPA), LLM fallback, dashboard, market refresh service/status/health/freshness, monitoring UI, and scheduling. Formal acceptance testing is documented separately (4.1 Testing Form, 4.2 Unit Testing Plan, 4.3 Integration Testing Plan, 4.4 User Acceptance Test), prepared for testing with a kedai runcit owner.

## Project Structure

```text
app.py                  # Flask app, routes, all database models
services/               # market_analysis, pricing_engine, llm_explainer,
                        # market_ingestion, mcp_client, market refresh
                        # service/status/health, market_freshness, matching,
                        # pricing_dashboard, pricing_workflow
scripts/                # etl_pricecatcher, sync_market_data, train_pricing_model
templates/              # Jinja2 pages (dashboard, product, pricing, market-data)
utils/                  # normalisation helpers
tests/                  # pytest suite (256 tests)
migrations/             # Alembic migrations (head: 33b959a98288)
ml/                     # pricing_model.pkl (local build artefact, gitignored)
docs/                   # MARKET_DATA_SCHEDULING.md, VIDEO_DEMO_DATA.md
import_pricecatcher.py  # PriceCatcher Parquet archive importer
seed_demo.py            # demo shop seed
requirements.txt        # web/runtime dependencies
requirements-etl.txt    # ETL-only dependencies (pandas, pyarrow)
```

## Deployment History (for FYP report)

| Stage | Platform | Outcome | Lesson |
|---|---|---|---|
| 1 | Local dev (XAMPP + `flask run`) | Worked for development | Baseline; not shareable |
| 2 | Render free tier | Deployed, but too slow (0.1 vCPU, 512 MB RAM; worker OOM on heavy imports) | Split web/ETL dependencies, guarded heavy imports, shrank model |
| 3 | Leapcell free tier | Migration prepared (port 8080, `/tmp` model path), platform shut down before use | Kept the deployment portable; reverted Leapcell-specific paths |
| 4 | Cloudflare Tunnel + local waitress | **Final:** fast, custom domain, full laptop resources | Laptop-as-server fits a demo with a real database |

## Limitations

- **Laptop-as-server:** no redundancy; a single point of failure.
- **Uptime:** no cold start while the laptop is on, but laptop sleep or disconnects take the site down.
- **ML model:** constrained hyperparameters (~5 MB vs the original 638 MB) — minor accuracy tradeoff for deployability.
- **Scheduling:** no in-app scheduler; market refreshes run locally via Task Scheduler/cron.
- **Market data:** PriceCatcher is a monthly archive (fresh ≤35 days is expected); ManaMurah covers 5 dry-goods items only.

## Future Work

- Persistent cloud deployment with adequate CPU/RAM.
- In-app job scheduler for market refreshes.
- Mobile-optimised UI.
- Additional market data sources.

## Credits

- **Developers:** [Team member names]
- **Supervisor:** [Supervisor name]
- **Institution:** [Politeknik name]
- **External tester:** kedai runcit owner (acceptance testing)

> **AI recommends and explains. Business guardrails remain authoritative. Human approval remains part of the decision process.**
