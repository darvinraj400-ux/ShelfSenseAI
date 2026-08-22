"""
============================================================
 ShelfSenseAI — Phase 3F LLM Pricing Explainer
============================================================
 Feeds deterministic rules and ML outputs into Gemini 2.5 Flash
 to generate a natural-language explanation for the shop owner.

 CRITICAL: The entire module is designed to never crash the app.
 If GEMINI_API_KEY is missing, the API is down, or any exception
 occurs, a sensible fallback string is returned.
============================================================
"""
import os
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are a pricing assistant for a small retail shop in Malaysia.
Explain the recommended price for a product in 2-3 concise sentences.
Be practical and direct — the shop owner needs a clear, actionable insight.
Mention the market context if available. Keep it under 100 words."""

USER_PROMPT_TEMPLATE = """Product: {product_name}
Cost price: RM{cost_price:.2f}
Current selling price: {current_price}
Target margin: {target_margin}%
Baseline margin: {baseline_margin}%
Recommended price: RM{recommended_price:.2f}
Market median: {market_median}
Market range: {market_range}
Confidence: {confidence}
Guardrails applied: {guardrails}
PCAPA warning: {pcapa_warning}

Explain why this price is recommended. Be concise (2-3 sentences)."""


# ---------------------------------------------------------------------------
# Main function
# ---------------------------------------------------------------------------

def generate_pricing_explanation(product, market_stats, recommendation):
    """Generate a natural-language pricing explanation via Gemini 2.5 Flash.

    Parameters:
        product:          Product ORM object (has name, cost_price, etc.)
        market_stats:     dict from get_market_stats() (may have n=0)
        recommendation:   dict from get_price_recommendation()

    Returns:
        str — a 2-3 sentence explanation, or a fallback string on failure.
    """
    # Build the prompt
    current = (f"RM{float(product.selling_price):.2f}"
               if product.selling_price else "Not set")
    mkt_median = (f"RM{market_stats['median']:.2f}"
                  if market_stats.get("median") else "Not available")
    mkt_range = "N/A"
    if market_stats.get("min") and market_stats.get("max"):
        mkt_range = f"RM{market_stats['min']:.2f} – RM{market_stats['max']:.2f}"

    guardrails = ", ".join(recommendation.get("guardrails_applied", [])) or "None"
    pcapa_warn = "Yes" if recommendation.get("warnings") else "No"

    prompt = USER_PROMPT_TEMPLATE.format(
        product_name=product.name,
        cost_price=float(product.cost_price),
        current_price=current,
        target_margin=product.target_margin,
        baseline_margin=product.baseline_margin or product.target_margin,
        recommended_price=recommendation["recommended_price"],
        market_median=mkt_median,
        market_range=mkt_range,
        confidence=recommendation.get("confidence", "unknown"),
        guardrails=guardrails,
        pcapa_warning=pcapa_warn,
    )

    # Attempt LLM call
    try:
        return _call_gemini(prompt)
    except Exception as e:
        logger.warning("Gemini API call failed: %s", e)
        return _fallback(product, market_stats, recommendation)


def _call_gemini(prompt):
    """Call Gemini 2.5 Flash and return the response text.

    Raises on any failure — caller handles the fallback.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set in environment")

    from google import genai
    client = genai.Client(api_key=api_key)

    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=[
            {"role": "user", "parts": [
                {"text": SYSTEM_PROMPT},
                {"text": prompt},
            ]}
        ],
    )

    text = response.text
    if not text or not text.strip():
        raise RuntimeError("Gemini returned empty response")
    return text.strip()


def _fallback(product, market_stats, recommendation):
    """Generate a deterministic fallback explanation without the LLM."""
    parts = [
        f"Recommended price RM{recommendation['recommended_price']:.2f} "
        f"for {product.name}."
    ]

    if market_stats.get("median"):
        ppi = recommendation.get("diff_pct")
        if ppi is not None:
            direction = "above" if ppi > 0 else "below"
            parts.append(
                f"This is {abs(ppi):.1f}% {direction} your current price "
                f"(market median: RM{market_stats['median']:.2f})."
            )
        else:
            parts.append(
                f"Market median is RM{market_stats['median']:.2f}."
            )
    else:
        parts.append(
            "No verified market data available — "
            "recommendation based on cost structure and target margin."
        )

    if recommendation.get("warnings"):
        parts.append("⚠ " + recommendation["warnings"][0])

    return " ".join(parts)
