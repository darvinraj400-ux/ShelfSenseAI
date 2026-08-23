# ShelfSenseAI - FYP Presentation Walkthrough

This script is designed for the Final Year Project demonstration. It follows a logical flow to showcase the system's progression from basic management to AI decision support.

## Preparations Before Demo
1. Run `python seed_demo.py` to ensure clean products and realistic stock levels.
2. Run `python etl_pricecatcher.py` to ensure market data is populated.
3. Log in as the Owner of the seeded demo shop.

## Step 1: The Dashboard (Phase 4A)
*   **Action:** Navigate to `Dashboard`.
*   **Talking Points:** Point out the top metrics cards (Total Products, Low Stock, Inventory Value). Highlight the **Action Required** panel showing automated warnings (Low Stock, Cost Floor Violations). This demonstrates immediate business value.

## Step 2: Product & Inventory Management (Phase 1 & 2)
*   **Action:** Click on `Inventory` -> `Adjust` for a product, then record a sale via `Sales`.
*   **Talking Points:** Show how atomic stock decreases work. Mention that "Package Size" (e.g., 1kg) is correctly decoupled from "Inventory Stock" (e.g., 20 units).

## Step 3: Market Intelligence & Matching (Phase 3A & 3C)
*   **Action:** Navigate to a Product Detail page -> Click the `Market Intelligence` tab.
*   **Talking Points:** Explain the RapidFuzz fuzzy matching. Show how the retailer can accept/reject market links. Emphasize that the system *never* invents market data—it relies on the decoupled ETL pipeline.

## Step 4: ML Pricing & AI Explainer (Phase 3D, 3E, 3F) **[Climax]**
*   **Action:** Click the `Pricing Recommendation` tab.
*   **Talking Points:** 
    1. **The Math:** Show the statistical metrics (Price Position Index, Market Median).
    2. **The ML:** Point out the Random Forest recommended price. Emphasize the **deterministic guardrails** (it won't recommend a price that causes a loss).
    3. **The AI:** Read the Gemini LLM explanation. Explain the architecture: DB -> Math -> AI. The AI explains the data; it does not hallucinate the math. Mention the 3-layer fallback for API rate limits.

## Step 5: Roles & Security (Phase 2C)
*   **Action:** Navigate to `Team / Employees`.
*   **Talking Points:** Show the cryptographically secure invitation links. Demonstrate that a Manager can adjust stock, but only an Owner can delete products or remove employees.
