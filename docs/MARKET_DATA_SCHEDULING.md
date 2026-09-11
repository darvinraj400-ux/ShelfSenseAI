# Market Data Scheduling — ShelfSenseAI (Phase 10E)

This document explains how to automate market-data refresh using the
existing Flask CLI and the host operating system's scheduler.
It does **not** describe a background worker — the Flask app remains a
monolith and the OS invokes the CLI periodically.

```
Operating System Scheduler (cron / Task Scheduler)
        ↓
flask market-data refresh  (MarketDataRefreshService)
        ↓
PriceCatcher / ManaMurah  →  MarketRefreshRun  →  MarketPriceObservation
        ↓
Market Intelligence → Pricing Recommendation → Human Review → APPLY / DISMISS
```

---

## A. What scheduling does

* Periodically executes `flask market-data refresh` via the OS scheduler.
* Reuses `MarketDataRefreshService.refresh()` — no duplicate ingestion logic.
* For **ManaMurah** (FAMA daily): fetches the configured `FAMA_SCOPE_ITEMS`
  (5 dry-goods ids) via `ManaMurahClient` (streamable HTTP, defensive parsing)
  and upserts through `market_ingestion` (`(source_id,external_id)` +
  `(market_item_id,observed_at,state,district)` idempotency).
* For **PriceCatcher**: reuses the existing ETL (`_get_or_create_source`,
  `_upsert_market_items`, `_rebuild_observations`) — premise-level,
  source-isolated (`WHERE source_id=:sid`), stable `MarketItem` ids.
* Records one `MarketRefreshRun` per source with `inserted/updated/
  duplicates_skipped/rejected/errors`, `latest_observed_at`,
  `started_at/finished_at`, `status` (`success`/`partial`/`failed`),
  `triggered_by` (`manual` vs `scheduled`), and sanitized `error_message`.
* Makes newer `MarketPriceObservation` rows available to
  `market_analysis` (min/max/median, recent observations) and therefore to
  the pricing engine's next recommendation — **without** changing any
  `Product.selling_price`.

## B. What scheduling does NOT do

* **Does NOT** automatically change `Product.selling_price`.
* **Does NOT** automatically create `PriceHistory` or
  `PricingRecommendationDecision`, nor apply/dismiss recommendations.
* **Does NOT** replace human review (Phase 8 `POST /api/decision/<did>/apply`
  remains the only mutation path).
* **Does NOT** provide real-time pricing — ManaMurah is daily, PriceCatcher
  is monthly; freshness thresholds are 3/7 days (ManaMurah) and 35/60 days
  (PriceCatcher).
* **Does NOT** require Celery/Redis/Kafka/APScheduler — the OS scheduler is
  sufficient.

## C. Windows Task Scheduler

The project is typically run on Windows with XAMPP/MySQL, VS Code, and Git
Bash. The Flask CLI is launched via the project's venv, not a global Flask.

**1. Find your Python:**
```
<project-root>\venv\Scripts\python.exe
# e.g. C:\Users\User\ShelfSenseAI\venv\Scripts\python.exe
```
Verify:
```
.\venv\Scripts\python.exe -m flask --app app market-data refresh --help
```

**2. Create a task (Task Scheduler GUI):**
* Open *Task Scheduler* → *Create Task…* (not Basic Task).
* **General:** Name `ShelfSenseAI — ManaMurah refresh`, *Run whether user is logged on or not*, *Do not store password* if MySQL is local.
* **Triggers:** Daily, e.g. `06:30` (after FAMA publishes). For PriceCatcher, monthly on the 5th at `07:00`.
* **Actions → New:**
  * **Program/script:** `C:\Users\User\ShelfSenseAI\venv\Scripts\python.exe`
  * **Add arguments:** `-m flask --app app market-data refresh --source manamurah --scheduled`
    * For PriceCatcher: `-m flask --app app market-data refresh --source pricecatcher --scheduled`
    * For both: `-m flask --app app market-data refresh --source all --scheduled`
  * **Start in:** `C:\Users\User\ShelfSenseAI`
* **Conditions/Settings:** Uncheck *Stop if the computer switches to battery*, check *If the task fails, restart every 10 minutes* (optional).

**3. Task Scheduler (command line, alternative):**
```powershell
# ManaMurah daily 06:30
schtasks /create /tn "ShelfSenseAI-ManaMurah" /tr "C:\Users\User\ShelfSenseAI\venv\Scripts\python.exe -m flask --app app market-data refresh --source manamurah --scheduled" /sc daily /st 06:30 /ru "%USERNAME%"

# PriceCatcher monthly on the 5th
schtasks /create /tn "ShelfSenseAI-PriceCatcher" /tr "C:\Users\User\ShelfSenseAI\venv\Scripts\python.exe -m flask --app app market-data refresh --source pricecatcher --scheduled" /sc monthly /d 5 /st 07:00 /ru "%USERNAME%"
```

**4. Recommended frequencies:**
* **ManaMurah (FAMA daily):** daily 06:30–07:00 (fresh ≤3d, aging ≤7d). Example above.
* **PriceCatcher (monthly archive):** monthly, 5th of month 07:00 (fresh ≤35d, aging ≤60d). Weekly would be wasteful; the ETL rebuilds 1.97M rows (~60s).

**5. Test manually (before scheduling):**
```powershell
.\venv\Scripts\python.exe -m flask --app app market-data refresh --source manamurah --dry-run
.\venv\Scripts\python.exe -m flask --app app market-data refresh --source manamurah --scheduled --dry-run
.\venv\Scripts\python.exe -m flask --app app market-data health --source all
```

**6. Inspect failures:**
* CLI exit code: `0` success, non-zero if any source failed (Task Scheduler *Last Run Result*).
* History: `SELECT source_name, status, inserted, rejected, errors, error_message, started_at FROM market_refresh_run ORDER BY started_at DESC LIMIT 10;`
* Health: `flask market-data health --source all` or `/market-data` page (Owner/Manager).

Do **not** create the task automatically — configure it manually as above.

## D. Linux cron

Use the project's venv Python, not the system Flask.

```bash
# Open crontab
crontab -e

# ManaMurah daily 06:30
30 6 * * * cd /home/user/ShelfSenseAI && /home/user/ShelfSenseAI/venv/bin/python -m flask --app app market-data refresh --source manamurah --scheduled >> /var/log/shelfsense-manamurah.log 2>&1

# PriceCatcher monthly on the 5th 07:00
0 7 5 * * cd /home/user/ShelfSenseAI && /home/user/ShelfSenseAI/venv/bin/python -m flask --app app market-data refresh --source pricecatcher --scheduled >> /var/log/shelfsense-pricecatcher.log 2>&1

# All sources (alternative)
0 7 * * * cd /home/user/ShelfSenseAI && /home/user/ShelfSenseAI/venv/bin/python -m flask --app app market-data refresh --source all --scheduled >> /var/log/shelfsense-all.log 2>&1
```

Verify cron is running (`systemctl status cron`) and check logs.

## E. Recommended frequencies (summary)

| Source | Cadence | Rationale |
|---|---|---|
| **ManaMurah/FAMA** | **Daily** 06:30 | Daily feed; 10C thresholds fresh 3d/aging 7d; missed weekend tolerated |
| **PriceCatcher** | **Monthly** (5th) | Monthly file; fresh 35d/aging 60d; ETL is bulk (1.97M rows) |

Do not claim real-time data — health page will show `age` and `latest_observed_at`.

## F. Monitoring

After scheduling, inspect:

* **Web:** `GET /market-data` (Owner/Manager) — per-source cards (Healthy/Warning/Unhealthy), latest refresh `status/finished_at`, `latest observation`, metrics `inserted/updated/duplicates/rejected/errors`, and `Recent Refresh History` table (30 rows, `triggered_by` badge distinguishes `manual`/`scheduled`/`test`). No auto-refresh is triggered by viewing.
* **CLI:** `flask market-data health --source all` — same health logic, read-only.
* **DB:** `market_refresh_run` — authoritative audit:
  ```sql
  SELECT source_name, status, triggered_by, inserted, duplicates_skipped, rejected, errors, latest_observed_at, error_message, started_at
  FROM market_refresh_run ORDER BY started_at DESC LIMIT 20;
  ```
  Check `latest_observation_date`, `inserted` vs `rejected`/`errors`, `failed` vs `partial`.

## Concurrency

Overlapping executions are unlikely (daily vs monthly, each <2 min for ManaMurah, ~60s for PriceCatcher ETL). No Redis/distributed lock is used. Protection is:

* **Per-source isolation:** PriceCatcher `DELETE … WHERE source_id=:sid` and ManaMurah upsert `WHERE source_id=:sid` never cross.
* **DB indexes/uniqueness:** `uq_price_natural` and `uq_market_obs_premise` make concurrent inserts idempotent; MySQL row locks serialize overlapping ETL deletes/inserts.
* **Per-source try/except:** `MarketDataRefreshService.refresh(sources='all')` runs pricecatcher then manamurah; if one fails the other is still recorded and not rolled back, CLI exits non-zero.
* If two scheduled jobs overlap, the second will either wait on the DB lock or be recorded as `failed` with a sanitized `error_message` — previous `MarketPriceObservation` rows remain, because ManaMurah never deletes and PriceCatcher raw `price` archive is never cleared (only `market_price_observation` for that source is rebuilt, and a failed rebuild leaves the previous successful observation set intact until the next successful run; the raw `price` table is additive).

For 10E, **no additional lock is added** — the simplest reliable mechanism is the existing DB constraints plus OS scheduler spacing (daily vs monthly). Documented here instead of code.
