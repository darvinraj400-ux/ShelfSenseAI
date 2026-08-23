# ShelfSenseAI

ShelfSenseAI is a Flask-based retail management and market intelligence application developed as a Final Year Project (FYP). It helps local retailers understand their product pricing relative to market conditions and make better, data-driven pricing decisions using deterministic calculations, Machine Learning (Random Forest), and Generative AI (Google Gemini).

## System Architecture

ShelfSenseAI follows a decoupled architecture, separating core retail management from the market intelligence engine.

*   **Core Retail:** Shop management, Auth, Employee roles, Inventory, Sales, Price History.
*   **Market Intelligence:** Idempotent ETL pipeline (PriceCatcher), exact/fuzzy matching (RapidFuzz), statistical market analysis (PPI).
*   **AI/ML Engine:** Random Forest Regressor (pricing prediction with deterministic guardrails) and Gemini LLM (3-layer fallback for natural-language explanations).

## Technology Stack

*   **Backend:** Python 3, Flask, SQLAlchemy, MySQL
*   **Machine Learning:** Scikit-learn (RandomForestRegressor)
*   **AI Integration:** Google GenAI SDK (Gemini 1.5 Flash)
*   **Data Matching:** RapidFuzz
*   **Frontend:** HTML5, Bootstrap 5, Chart.js, Vanilla JS (Fetch API)
*   **Testing:** Pytest

## Setup & Installation

1.  **Clone & Environment Setup:**
    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows: venv\Scripts\activate
    pip install -r requirements.txt
    ```
2.  **Environment Variables:**
    Create a `.env` file based on `.env.example`. Include your database URI, Flask Secret Key, and `GEMINI_API_KEY`.
3.  **Database Migration & Seeding:**
    ```bash
    flask db upgrade
    python seed_demo.py        # Generates shops, users, and clean demo products
    python etl_pricecatcher.py # Runs the idempotent Market Data ETL pipeline
    ```
4.  **Run the Application:**
    ```bash
    flask run
    ```

## Development Phases
*   **Phase 1-2:** Core Foundation, Identity, Inventory, Roles & Registration.
*   **Phase 3:** Market Intelligence, ETL, Matching, ML Pricing, and AI Explainer.
*   **Phase 4:** Dashboard Analytics, Data Quality, Production Hardening, Testing.
