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
python import_pricecatcher.py
python scripts/etl_pricecatcher.py
python seed_demo.py
```

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

### Step 4 — Run the Application
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
| **4** | Hardening | Dashboard analytics, KPDN price control guardrails, Explainable AI, 84 tests |

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
