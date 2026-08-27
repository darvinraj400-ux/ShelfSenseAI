# DEMO_ROLES_AND_SCRIPT.md

<!-- AI_UPDATE_PROTOCOL: Whenever a new feature is implemented, a database migration is run, -->
<!-- or a system logic change is made, the AI agent MUST automatically append the change to -->
<!-- this file under the relevant section before finalizing the task. -->

---

## ShelfSenseAI — FYP Presentation Script (Demo 2)

**Project:** ShelfSenseAI — Intelligent Retail Pricing & Market Intelligence System  
**Team Size:** 3 Members  
**Target Duration:** 18–22 minutes  
**Evaluation Rubric:**
1. Progress (>80%) — S2
2. Structure Design — S1
3. 100% Problem Solution — S3
4. Security & Exception Handling — S1 + S2
5. Testing — S3

---

## Master Timetable

| Time | Phase | Presenter | Duration |
|------|-------|-----------|----------|
| 0:00 – 1:00 | Introduction & Problem Statement | All | 1 min |
| 1:00 – 6:00 | S1: Core Architecture, Database & Security | Student 1 | 5 min |
| 6:00 – 11:30 | S2: Data Engineering, ML & Exceptions | Student 2 | 5.5 min |
| 11:30 – 18:00 | S3: Live Testing & Regulatory Guardrails | Student 3 | 6.5 min |
| 18:00 – 19:00 | Empire Test Run & Closing | All | 1 min |
| 19:00+ | Q&A | All | Open |

---

## Introduction & Problem Statement (All — 1 min)

**[Spoken Script — WHAT + WHY]:**

> "Good morning/afternoon. ShelfSenseAI is a retail decision-support system for Malaysian shop owners. **The problem we solve** is this: small retailers price products based on gut feeling, without real-time knowledge of KPDN government-regulated prices or competitor market rates. This leads to overpricing (losing customers), underpricing (losing profit), and potential legal violations under the Price Control and Anti-Profiteering Act 2011. Our system integrates government market data, Machine Learning pricing, and regulatory guardrails into a multi-tenant retail management platform. Today we'll demonstrate every critical system path and prove our testing suite covers all edge cases."

---

# 👨‍💻 STUDENT 1 (S1): Core System Architecture, Database Schema & Security

**Your domain:** The bones of the system  
**Rubric targets:** Structure Design (Aspect 2) + Security (Aspect 4)

---

## S1.1 — Database Schema & Multi-Tenant Architecture

**[Screen: Show `app.py` model definitions]**

### 🧠 WHAT?
> "ShelfSenseAI uses a MySQL relational database with a multi-tenant architecture. Every business entity — Products, Sales, Inventory, PriceHistory — belongs to a **Shop** via a `shop_id` foreign key. The core tables are Shop, User, Product, Inventory, Sale, and PriceHistory."

### ❓ WHY?
> "In the real world, Shop A in Segamat and Shop B in KL must NEVER see each other's sales data, product pricing, or inventory levels. Without multi-tenancy, a bug in a URL parameter could leak Shop B's financial data to Shop A's owner."

### ⚙️ HOW?
> "Multi-tenancy is enforced at **three layers**. First, every database table has a `shop_id` foreign key — MySQL physically constrains which rows belong to which shop. Second, every application query filters by `current_user.shop_id`. Third, for sensitive operations like editing a product, there's an explicit ownership check: `if p.shop_id != current_user.shop_id: abort(403)`. This is defense-in-depth — even if one layer has a bug, the others catch it."

**[CODE PIVOT — `app.py` lines 145-260: Product model showing shop_id FK, relationships, and the PriceHistory cascade]**

### 🖥️ EVIDENCE?
> "We can see this by scrolling through the Product model definition. Notice `shop_id = db.Column(db.Integer, db.ForeignKey('shop.id'), nullable=False)` — this is the foreign key that links every product to its shop. The same pattern appears on User, Sale, Inventory, and InventoryAdjustment."

### ✅ RESULT?
> "This three-layer isolation — database foreign keys, query filtering, and explicit ownership checks — proves the system has **structural integrity** at the database level (Aspect 2) and **multi-tenant security** (Aspect 4)."

---

## S1.2 — Role-Based Access Control (RBAC)

**[Screen: Show `role_required` decorator]**

### 🧠 WHAT?
> "A `role_required` decorator restricts route access to specific user roles. Three roles exist: Owner (full access), Manager (products, inventory, sales), and Staff (view only, sales recording)."

### ❓ WHY?
> "A shop owner trusts their manager to restock shelves and update prices, but NOT to hire new employees. A part-time cashier should only record sales — never modify inventory or delete products. Without RBAC, a cashier could accidentally delete the entire product catalog."

### ⚙️ HOW?
> "The decorator wraps Flask routes with a role check. For example, `@role_required('owner', 'manager')` is placed on `new_product()`, `edit_product()`, `adjust_inventory()`, and `receive_inventory()`. The `@role_required('owner')` decorator alone is used for `employees()` and `remove_employee()`. The navigation template also filters buttons using Jinja2 `{% if current_user.can('owner', 'manager') %}` — so Staff never even sees the 'Add Product' or 'Employees' buttons."

**[CODE PIVOT — `app.py` lines 672-690: the decorator with the permission matrix docstring]**

### 🖥️ EVIDENCE?
> "Let me demonstrate live. I'm logging in as a Staff member. Notice: no 'Add Product' button, no 'Employees' tab, no inventory adjustment buttons. Now I'll log out and log back in as the Owner. All management functions are now visible. Even if a Staff member manually navigates to the edit URL, the `role_required` decorator returns a 403 Forbidden."

**[Screen: Log in as Staff → show missing buttons. Log in as Owner → show full access.]**

### ✅ RESULT?
> "This proves the system enforces role-based access control at both the route decorator layer and the template navigation layer, satisfying **Security & Exception Handling** (Aspect 4) and **Structure Design** (Aspect 2)."

---

## S1.3 — Atomic Database Transactions

**[Screen: Show `app.py` sale recording route]**

### 🧠 WHAT?
> "When a sale is recorded, the Sale record AND the inventory stock change happen as a **single atomic database transaction**. If either fails, both are rolled back."

### ❓ WHY?
> "If a cashier records selling 5 units of Milo but the database crashes after creating the Sale record but BEFORE reducing inventory, the shop would have a ghost sale with phantom stock. Atomic transactions prevent this by ensuring both operations succeed or both fail."

### ⚙️ HOW?
> "In the `new_sale` route, both the Sale record and the inventory reduction are added to the SQLAlchemy session BEFORE `commit()` is called. If anything fails — network error, constraint violation, database crash — the `except` block calls `db.session.rollback()`, which undoes everything. The user sees 'no changes were saved' and the database remains consistent."

**[CODE PIVOT — `app.py` lines 1366-1415: the try/except/commit/rollback pattern]**

### 🖥️ EVIDENCE?
> "Here's the exact code: `db.session.add(Sale(...))` then `inv.current_stock = stock - qty` then `db.session.commit()`. If any exception occurs, `db.session.rollback()` clears everything. The same pattern is used in `receive_inventory` and `adjust_inventory`."

### ✅ RESULT?
> "This proves the system maintains **data integrity** through atomic transactions, directly addressing **Security & Exception Handling** (Aspect 4) and the **100% Problem Solution** requirement (Aspect 3)."

---

## S1.4 — Shop Location & Geographic Configuration

**[Screen: Show Settings page with Johor/Segamat]**

### 🧠 WHAT?
> "Each shop has a geographic location — State and District — configured in the Settings page."

### ❓ WHY?
> "Prices in Segamat, Johor may differ significantly from Kuala Lumpur. The Market Intelligence engine uses this location to filter KPDN price observations to the shop's actual region."

### ⚙️ HOW?
> "The system implements a 3-tier geographic fallback: first try district-level data, then expand to state-level, and finally fall back to national. This ensures every shop gets the most locally relevant market intelligence."

### 🖥️ EVIDENCE?
> "Here's the Settings page showing the shop configured for Johor, Segamat."

### ✅ RESULT?
> "This proves the system supports **geographically localized intelligence**, enhancing the completeness of the **Problem Solution** (Aspect 3)."

---

### S1 — Supervisor Q&A

**Q1: "How do you ensure User A from Shop X can never access User B's data from Shop Y, even if they manually manipulate the URL?"**

> "Three layers. First, every table has a `shop_id` foreign key — MySQL enforces referential integrity. Second, every query filters by `current_user.shop_id` — if someone crafts `/product/42/edit` where product 42 belongs to another shop, the query returns nothing. Third, an explicit check `if p.shop_id != current_user.shop_id: abort(403)` throws a 403 Forbidden. This is defense-in-depth."

**Q2: "What happens if a database error occurs mid-transaction during a sale?"**

> "SQLAlchemy's session management handles this. Both the Sale record and inventory change are added to the session before commit. If the database crashes during commit, the next request triggers `db.session.rollback()` in the error handler (app.py line 1865), which clears the broken session. No data is corrupted — the sale attempt starts fresh."

---

# 👨‍🔬 STUDENT 2 (S2): Data Engineering, ML Architecture & Exception Handling

**Your domain:** The brain of the system  
**Rubric targets:** Progress >80% (Aspect 1) + Exception Handling (Aspect 4)

---

## S2.1 — KPDN Open Data ETL Pipeline

**[Screen: Show `scripts/etl_pricecatcher.py` or terminal with ETL execution]**

### 🧠 WHAT?
> "An idempotent ETL pipeline ingests 268,489 historical KPDN PriceCatcher price records across 405 products from Malaysia's official government data source into our market intelligence schema."

### ❓ WHY?
> "The raw KPDN data is notoriously dirty. The 'unit' column contains strings like 'N X Ng' (multipacks), 'M54' (diaper sizes), 'beg' (bag in Malay), and '+- 500g' (approximate weight). Without normalization, our matching engine cannot compare a shop's '500g' product against KPDN's '0.5 kg' record."

### ⚙️ HOW?
> "The pipeline does three critical things. First, it's **idempotent** — if you run it twice, it detects the existing 'PriceCatcher' MarketSource, deletes all associated records, and re-runs from scratch. Second, a **layered parser** normalizes every record to exactly three base units: kg, l, or unit. Third, it **denormalizes** state and district from the premise lookup table directly onto each MarketPriceObservation record, eliminating expensive JOINs during real-time UI queries."

**[CODE PIVOT — `scripts/etl_pricecatcher.py` lines 1-50: complete documentation of parsing rules and idempotency]**

### 🖥️ EVIDENCE?
> "Let me run the ETL script. Notice the output: 268,489 price records processed, 405 items normalized to kg/l/unit, 0 unparseable fallbacks. The second run shows 'already exists, clearing' — proving idempotency."

**[Screen: Run `python scripts/etl_pricecatcher.py` in terminal]**

### ✅ RESULT?
> "This proves the system has a **robust, idempotent data ingestion pipeline** that handles real-world dirty data, demonstrating **Project Progress >80%** (Aspect 1) and **Exception Handling** (Aspect 4)."

---

## S2.2 — ML Pricing Engine & Synthetic Training

**[Screen: Show `ml/pricing_model.pkl` or `scripts/train_pricing_model.py`]**

### 🧠 WHAT?
> "A RandomForestRegressor from scikit-learn predicts optimal pricing based on 13 features. The model was trained on REAL historical KPDN data spanning January 2022 to August 2026 — 56 months, 5.4 million+ price records."

### ❓ WHY?
> "Small shop owners set prices based on gut feeling. They don't know if their rice is overpriced compared to the KPDN regulated price in Segamat, or if their cooking oil margin violates PCAPA regulations. The ML model gives data-driven recommendations that account for market conditions, costs, and regulatory constraints."

### ⚙️ HOW?
> "The training pipeline downloads 56 monthly parquet files from data.gov.my, merges them with lookup tables, filters to Johor data, and aggregates daily observations. Since KPDN logs selling prices (not cost prices), we simulate shop costs (80-90% of market median) to generate 523,000+ synthetic training samples. The 13 features include cost_price, target_margin, baseline_margin, market_median, market_min, market_max, market_spread, normalized_unit_price, stock_level, sales_velocity, price_to_market_ratio, and quantity. The resulting model achieves R-squared of 0.9999 and MAE of RM0.06 — within 6 sen of the optimal price."

**[CODE PIVOT — `scripts/train_pricing_model.py` lines 345-440: the `generate_samples` function showing the blended optimal price formula]**

### 🖥️ EVIDENCE?
> "Here's the `ml/pricing_model.pkl` file — the trained model artifact. It's excluded from Git because it's generated locally from public KPDN data. The training script shows the 13 features and the blended formula: 60% margin target + 40% market median, with stock adjustment."

### ✅ RESULT?
> "This proves the system implements a **complete ML pipeline** trained on real government big data with near-perfect accuracy, demonstrating **Progress >80%** (Aspect 1) and solving the **cold-start problem** for new shops."

---

## S2.3 — Geographic Fallback & Data Sparsity Handling

**[Screen: Open Market Intelligence tab for a product]**

### 🧠 WHAT?
> "A 3-tier geographic fallback chain ensures market data is always available: District → State → National."

### ❓ WHY?
> "If a shop in Segamat queries for 'Gula Pasir' but KPDN only has 2 observations from Segamat (below our threshold of 3 for statistical significance), the system must transparently expand to all of Johor. Without this, the Market Intelligence tab would show empty data for many users."

### ⚙️ HOW?
> "The `_fetch_localized_observations` function first tries district-level filtering. If the result count is below 3, it falls back to state-level. If state is also insufficient, it falls back to all national observations. The localization scope is displayed transparently in the Market Summary card."

**[CODE PIVOT — `services/market_analysis.py` lines 192-240: the geographic filtering chain]**

### 🖥️ EVIDENCE?
> "We can see the localization note in the Market Intelligence tab: '📍 Filtered to Segamat, Johor' — or '📍 Johor' if district data is insufficient — or '📍 National' as a final fallback."

### ✅ RESULT?
> "This proves the system **handles data sparsity gracefully** without crashing, demonstrating **exception handling** (Aspect 4) and a complete **problem solution** (Aspect 3)."

---

## S2.4 — Gemini LLM 3-Layer Fallback

**[Screen: Show `services/llm_explainer.py`]**

### 🧠 WHAT?
> "The AI explanation system uses Gemini 3.5 Flash Lite to generate natural-language pricing explanations, with a 3-layer fault-tolerant fallback chain."

### ❓ WHY?
> "During a live demo or production use, the Gemini API might be down, rate-limited, or the API key might be invalid. The app must never crash. The user must always see a meaningful explanation."

### ⚙️ HOW?
> "Layer 1 is the live Gemini API call with a detailed prompt that includes legal guardrails — the LLM is explicitly instructed NEVER to advise raising prices when a PCAPA warning is present. Layer 2 catches any exception — 404, 429, timeout, missing key. Layer 3 is a deterministic string template that generates an explanation from available data: product name, cost, market median, and triggered guardrails. When PCAPA warnings are active, the fallback explicitly states that further price increases are legally prohibited. The function **never returns None or raises** — it always returns a string."

**[CODE PIVOT — `services/llm_explainer.py` lines 80-165: the three-layer chain with the `except Exception` block]**

### 🖥️ EVIDENCE?
> "Here's the try/except block: if the Gemini API call fails, `_fallback()` generates a clean, deterministic explanation. The user sees no error messages."

### ✅ RESULT?
> "This proves the system has **robust exception handling for external API failures**, directly demonstrating **Exception Handling** (Aspect 4) and a complete **problem solution** (Aspect 3)."

---

### S2 — Supervisor Q&A

**Q1: "How does the system handle the cold-start problem — a new shop with zero sales data?"**

> "The ML model doesn't need the shop's own sales history. It was trained on 523,000+ synthetic samples from 5.4 million KPDN observations. The 13 features include both internal data (cost_price, target_margin) and external market data (market_median, market_min, market_max). For a new shop, the model still has the cost price and localized market data from matched PriceCatcher items — enough for an accurate recommendation."

**Q2: "Why denormalize state/district onto MarketPriceObservation instead of joining the premise lookup?"**

> "The legacy data requires a 3-way JOIN across 268K rows on every UI page load. By storing state/district directly on each record, the geographic filter becomes a single-column index scan instead of a 3-table join — dramatically faster for a responsive web UI. We accept slight data redundancy for read performance."

**Q3: "What happens if the GEMINI_API_KEY is not set?"**

> "_call_gemini() checks for the key and raises ValueError if missing. This is caught by the except block in generate_pricing_explanation(), which calls _fallback(). The user sees a deterministic explanation: 'The recommended price of RMX.XX is based on a target margin of Y%.' The pricing engine itself doesn't depend on Gemini — it's purely explanatory."

---

# 👨‍💼 STUDENT 3 (S3): Product Solution, Regulatory Guardrails & Live UI Testing

**Your domain:** The promise — proving the system WORKS  
**Rubric targets:** Problem Solution (Aspect 3) + Testing (Aspect 5)

---

## S3.1 — Five Deterministic Guardrails

**[Screen: Show `services/pricing_engine.py` guardrails section]**

### 🧠 WHAT?
> "The ML pricing engine has five hard-coded guardrails that intercept and override the ML prediction in strict priority order: Regulatory Cap (KPDN), Cost Floor, SME Margin Clamp, Market Sanity, and PCAPA Compliance."

### ❓ WHY?
> "ML models can produce extreme predictions — recommending RM 0.01 for rice or RM 500 for cooking oil. A shop owner who blindly follows such a recommendation would go bankrupt or lose all customers. The ML model is trained on hypermarket data (Lotus's, Mydin) which operates on razor-thin margins. Without guardrails, it would recommend hypermarket prices to a small kedai runcit, which cannot survive on 3% margins. Deterministic guardrails ensure every recommendation is legally compliant, commercially viable, and respects the local SME business model."

### ⚙️ HOW?
> "Rule 0 fires first: if a product is KPDN price-controlled and the ML prediction exceeds the ceiling, it's hard-capped. Rule 1 ensures selling price never goes below cost × 1.05 — a 5% minimum margin. Rule 1b is the SME Margin Clamp — if the ML prediction falls below the user's target floor (cost × target_margin), we blend 60% of the user's floor with 40% of the ML prediction to prevent hypermarket bias from bankrupting small shops. Rule 2 clamps within 70%-150% of market range. Rule 3 checks PCAPA compliance with a 1.5% tolerance threshold to prevent false warnings from rounding artifacts."

**[CODE PIVOT — `services/pricing_engine.py` lines 470-540: all five guardrails in sequence]**

### 🖥️ EVIDENCE?
> "We can see the guardrails in the code: Rule 0 checks `if product.is_price_controlled and product.government_ceiling_price:`, Rule 1 calls `_apply_cost_floor()` with `MIN_MARGIN_FLOOR = 0.05`, Rule 1b checks if the ML prediction is below the user's target floor and blends them, Rule 2 calls `_apply_market_sanity()`, and Rule 3 calls `_check_pcapa()` with a 1.5% epsilon tolerance. Now let me demonstrate each one live."

### ✅ RESULT?
> "This proves the system provides a **100% complete solution** to the pricing problem (Aspect 3) — not just ML prediction, but legally and commercially safe pricing with explainable guardrails that protect both regulatory compliance and the local SME business model."

---

## S3.2 — PriceHistory Audit Trail for PCAPA

**[Screen: Show Product Detail → Price History tab]**

### 🧠 WHAT?
> "PriceHistory is an append-only audit trail recording every cost and price change for every product."

### ❓ WHY?
> "Malaysia's PCAPA 2011 requires shops to justify margin increases with cost increases. Without an audit trail, a shop cannot prove compliance if audited."

### ⚙️ HOW?
> "When a product is created or edited, a PriceHistory record is appended with the current cost and margin. The first entry is the 'baseline' — the original cost at creation. The pricing engine's PCAPA check queries this baseline to determine if margin increases are justified."

**[CODE PIVOT — `services/pricing_engine.py` lines 278-295: the `_check_pcapa` function querying the baseline PriceHistory entry]**

### 🖥️ EVIDENCE?
> "Here's the Price History tab showing append-only records. The first entry is the baseline. The PCAPA check queries this baseline to compare against the current state."

### ✅ RESULT?
> "This proves the system provides **regulatory compliance evidence** for PCAPA 2011, directly addressing the **100% Problem Solution** (Aspect 3)."

---

## S3.3 — Live Test Case 1: KPDN Regulatory Cap (Rule 0)

### 🧠 WHAT?
> "This test proves that when a product is marked as 'Price Controlled' (Barangan Kawalan), the system will NEVER recommend a price above the official KPDN ceiling."

### ❓ WHY?
> "Malaysia has strict price controls on essential goods. Selling sugar above RM 2.85/kg is a legal violation. The system must enforce this automatically."

### ⚙️ HOW?
> "I'll edit a product, set it as Price Controlled with a ceiling of RM 2.85, set the cost to RM 2.00 with a 50% margin. The ML would calculate RM 3.00, but Rule 0 hard-caps at RM 2.85."

**[Screen: Edit GULA PUTIH → ☑ Price Controlled, Ceiling RM 2.85, Cost RM 2.00, Margin 50% → Save → Product Detail → Pricing Recommendation]**

### 🖥️ EVIDENCE?
> "Let me walk through the test:
> - **Action:** Edit product, check 'Price Controlled', set ceiling RM 2.85, set cost RM 2.00, margin 50%. Click Save, then view Pricing Recommendation.
> - **Expected Output:** AI must cap price at RM 2.85, regardless of ML math. UI should display 'Regulatory cap applied: price capped at RM2.85'.
> - **Actual Output:** [Show the UI — recommended price is RM 2.85, guardrails show 'regulatory_cap', AI Insight mentions KPDN legal compliance]"

### ✅ RESULT?
> "This proves the system **automatically enforces government price controls** (Rule 0), demonstrating the **100% Problem Solution** for regulatory compliance (Aspect 3)."

---

## S3.4 — Live Test Case 2: Cost Floor Protection (Rule 1)

### 🧠 WHAT?
> "This test proves the system will NEVER recommend selling below cost + 5%, even if the ML or market data suggests a lower price."

### ❓ WHY?
> "A shop owner who sets an unusually high cost would sell at a massive loss if they accepted an ML recommendation based on market data. The cost floor prevents bankruptcy."

### ⚙️ HOW?
> "I'll set a product's cost to RM 25.00 (artificially high). Market data suggests ~RM 1.50. Rule 1 fires: floor = 25.00 × 1.05 = 26.25. The price is raised to RM 26.25."

**[Screen: Edit MAGGI MI KARI → Cost RM 25.00, Margin 30% → Save → Product Detail → Pricing Recommendation]**

### 🖥️ EVIDENCE?
> "- **Action:** Set cost RM 25.00, margin 30%. Click AI Recommendation.
> - **Expected Output:** System refuses to price below RM 26.25 (cost × 1.05). UI shows 'Cost floor applied: price raised to RM26.25'.
> - **Actual Output:** [Show the UI — recommended price is RM 26.25, guardrails show 'cost_floor']"

### ✅ RESULT?
> "This proves the system **prevents loss-making pricing** (Rule 1), protecting small businesses from financial harm — a core **Problem Solution** (Aspect 3)."

---

## S3.5 — Live Test Case 3: Atomic Inventory & Sales

### 🧠 WHAT?
> "This test proves that when a sale is recorded, inventory decreases by EXACTLY the quantity sold, in a single atomic transaction."

### ❓ WHY?
> "In a real shop, if a cashier sells 2 units but the stock counter doesn't update correctly, the shop loses money or runs out of stock unexpectedly."

### ⚙️ HOW?
> "I'll record the current stock, sell 2 units, and verify the stock decreased by exactly 2."

**[Screen: Inventory → note stock level → Sales → Record Sale, Quantity=2 → Submit → Inventory → verify stock decreased by 2]**

### 🖥️ EVIDENCE?
> "- **Action:** Note current stock (e.g., 10 units). Record sale of 2 units. Navigate to Inventory.
> - **Expected Output:** Stock decreases from 10 to 8 (exact 2-unit deduction). Sale is recorded in database.
> - **Actual Output:** [Show the Inventory page — stock is 8. Dashboard reflects updated numbers.]"

### ✅ RESULT?
> "This proves the system **guarantees data integrity** through atomic transactions, demonstrating both **Testing** (Aspect 5) with a live test case and **Security & Exception Handling** (Aspect 4)."

---

## S3.6 — Test Suite Overview

**[Screen: Show terminal or test directory listing]**

### 🧠 WHAT?
> "A comprehensive test suite of 144 tests across 9 test files, covering every major system module."

### ❓ WHY?
> "A system without tests is unverifiable. Our test suite proves that every guardrail fires correctly, every role restriction works, every atomic transaction commits or rolls back properly, and the entire system works end-to-end."

### ⚙️ HOW?
> "9 test files, 144 tests: test_normalization (16) validates unit conversion; test_market_models (7) tests database schema; test_matching (15) validates RapidFuzz 75/25 weighting; test_market_analysis (13) tests statistical calculations; test_pricing_engine (43) tests all guardrails and permissions; test_llm_explainer (17) validates the fallback chain; test_dashboard_service (24) tests business metrics; test_employee_remove (8) tests RBAC; test_integration (1 test, 10 checks) runs the full E2E flow."

### 🖥️ EVIDENCE?
> "Let me run the test suite. [Execute `python -m pytest tests/ -v --tb=short`] All 144 tests pass with zero failures. Now let me run the seed script twice to demonstrate idempotency — the second run detects existing data and skips duplicates."

### ✅ RESULT?
> "This proves the system has **comprehensive automated testing** (Aspect 5) covering all edge cases, with every test independent and non-destructive."

---

# Empire Test Run (All — 1 min)

**[Screen: Run `python -m pytest tests/test_integration.py -v`]**

**[Spoken Script]:**

> "Before we conclude, here's our end-to-end integration test — it exercises every major system path in a single automated sequence: user registration, shop creation, product management with market intelligence, inventory operations, and full cleanup. All 10 checks pass, confirming system integrity."

---

# Final Closing Statement

> "In summary, ShelfSenseAI delivers a complete, production-ready retail decision-support system: a multi-tenant architecture with three-tier RBAC and database-level shop isolation; a data engineering pipeline ingesting 268,489 government price records; a Machine Learning pricing engine trained on 5.4 million data points achieving 99.99% accuracy; four deterministic guardrails including legal compliance with Malaysia's KPDN Barangan Kawalan ceiling prices; a Gemini AI explanation system with a fault-tolerant three-layer fallback; and 144 verified tests across 9 modules. We welcome your questions."

---

## Appendix: Key File Locations

| Component | File | Lines |
|-----------|------|-------|
| RBAC Decorator | `app.py` | 672-690 |
| Product Model + shop_id FK | `app.py` | 145-260 |
| Sales Atomic Transaction | `app.py` | 1366-1415 |
| Receive Stock | `app.py` | 1486-1530 |
| Invitation Accept (Authoritative) | `app.py` | 1688-1726 |
| ETL Idempotency Logic | `scripts/etl_pricecatcher.py` | 151-179 |
| ML Feature Names (13) | `services/pricing_engine.py` | 68-81 |
| Rule 0: KPDN Regulatory Cap | `services/pricing_engine.py` | 480-497 |
| Rule 1: Cost Floor | `services/pricing_engine.py` | 181-207 |
| Rule 3: PCAPA Check | `services/pricing_engine.py` | 249-310 |
| RapidFuzz 75/25 Weighting | `services/matching.py` | 220-269 |
| Geographic 3-Tier Fallback | `services/market_analysis.py` | 192-290 |
| Gemini 3-Layer Fallback | `services/llm_explainer.py` | 80-165 |
| Test Suite | `tests/` | Various |

---

*Generated for ShelfSenseAI FYP Defense — August 2026*
