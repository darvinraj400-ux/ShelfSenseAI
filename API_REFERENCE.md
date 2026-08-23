# API Endpoints Reference

ShelfSenseAI utilizes standard server-side rendering for its core views, supplemented by a RESTful internal API for dynamic asynchronous UI components (e.g., AI fetching, Market Matching).

### Internal API Endpoints (JSON)

| Method | Endpoint | Purpose |
| :--- | :--- | :--- |
| `GET` | `/autocomplete` | Fetches historical price/product name suggestions. |
| `POST` | `/api/product/<pid>/match` | Triggers the RapidFuzz engine to find market candidates. |
| `POST` | `/api/market-match/<mid>/verify` | Confirms a fuzzy match as an exact match. |
| `POST` | `/api/market-match/<mid>/reject` | Flags a match as incorrect to hide it. |
| `DELETE` | `/api/market-match/<mid>` | Removes an existing market linkage. |
| `GET` | `/api/product/<pid>/market` | Fetches verified market items linked to a product. |
| `GET` | `/api/product/<pid>/market-stats` | Calculates statistical market summaries (PPI, median). |
| `GET` | `/api/product/<pid>/pricing` | Runs the ML model + Gemini LLM explainer and returns the JSON payload. |
| `POST` | `/api/product/<pid>/apply-price` | Applies the AI-recommended price and logs the audit trail. |

*(Note: Standard HTML views (e.g., `/dashboard`, `/inventory`) are documented in the main routing structure of `app.py` and are protected by `@login_required`).*
