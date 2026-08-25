"""
ShelfSenseAI - Business Services Package
========================================

This package contains the core business logic services that power
the Market Intelligence and Pricing Recommendation engines:

  - matching.py:         Product-to-market-item matching (Phase 3C)
  - market_analysis.py:  Statistical market data aggregation (Phase 3D)
  - pricing_engine.py:   ML-powered pricing with guardrails (Phase 3E)
  - llm_explainer.py:    Natural-language pricing explanations (Phase 3F)
  - dashboard_service.py: Dashboard metrics and action items (Phase 4A)

Each service is designed to be independently testable and follows
the convention of separating PURE scoring/math functions from
DATABASE-AWARE query functions.
"""
