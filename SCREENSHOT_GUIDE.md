# FYP Report Screenshot Guide

When writing your final academic report, capture the following exact views to prove the completion of the system's objectives:

1.  **Dashboard Analytics:** Capture the whole screen showing the 3 top metric cards and the yellow "Action Required" panel. *(Proves system overview capabilities)*
2.  **Market Intelligence Tab (Product Detail):** Capture the RapidFuzz suggestion table and the verified market links. *(Proves data integration and fuzzy matching)*
3.  **Pricing Recommendation Tab (Product Detail):** This is your most important screenshot. Capture the ML target price, the statistical breakdown, and the Gemini "AI Assistant Insight" card. *(Proves AI/ML integration)*
4.  **Price History / Audit Trail:** Capture a product's price history table showing old vs. new prices. *(Proves transactional correctness)*
5.  **Employee Invitations:** Capture the Team page showing an active generated token link. *(Proves security and RBAC)*
6.  **Form Validation:** Try to type letters into the Quantity field on the Add Product page, or set a price below the cost price, and capture the resulting UI error/guardrail. *(Proves data quality enforcement)*7. **KPDN Price-Controlled Product:** Open the GULA PUTIH BERTAPIS KASAR product detail page. The Pricing Recommendation tab should show "Rule 0 (Regulatory Cap) Fired" with the price hard-capped at RM 2.85. *(Proves Malaysian legal compliance)*
8. **Training Metrics (Terminal):** Run `python scripts/train_pricing_model.py` and capture the final output showing MAE = RM0.0639, R² = 0.9999, and the top 5 feature importances. *(Proves ML model quality trained on real KPDN Big Data)*
9. **Geographic Market Localization:** On the Market Intelligence tab, capture the localization note ("📍 Segamat, Johor") below the Market Summary, showing that the market median is filtered to the shop's geographic region rather than national data. *(Proves hyper-localized market intelligence)*
10. **Competitor Observation Table (Explainable AI):** In the Market Intelligence tab, capture the "Recent Competitor Pricing (KPDN Open Data)" table showing raw price observations with date, product, package, location, and price columns. *(Proves Explainable AI transparency — users can see the actual government data driving the AI recommendations)*
