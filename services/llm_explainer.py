"""
============================================================
 ShelfSenseAI — Phase 3F LLM Pricing Explainer
============================================================

This module feeds deterministic rules and ML outputs into Google's
Gemini 3.5 Flash Lite to generate a natural-language explanation
for the shop owner.

CRITICAL DESIGN PRINCIPLE: The entire module is designed to NEVER
crash the application. If GEMINI_API_KEY is missing, the API is
down, rate limits are hit, or any exception occurs, a sensible
fallback string is returned instead.

THREE-LAYER FALLBACK CHAIN
---------------------------
Layer 1 — Live Gemini API call:
  The ideal path. Sends a structured prompt with product data,
  market context, and guardrail status to Gemini 3.5 Flash Lite.

Layer 2 — Exception handling:
  Any failure in Layer 1 (network error, API key missing, rate limit,
  empty response) is caught by the try/except in generate_pricing_explanation.
  The error is logged and the deterministic fallback is used.

Layer 3 — Deterministic fallback (_fallback function):
  A pure-Python string template that generates a sensible explanation
  without any external API call. Uses the same data as the LLM prompt
  but formats it deterministically. Always returns a non-empty string.

PROMPT STRUCTURE
----------------
The prompt sent to Gemini contains:
  - Product name, cost price, current selling price
  - Target and baseline margins
  - Recommended price and confidence level
  - Market median and range
  - Which guardrails were applied
  - Whether PCAPA warning was triggered
  - Regulatory context (KPDN Barangan Kawalan, if applicable)

The LLM is asked to explain in 2-3 concise sentences, under 100 words.
============================================================
"""
import os
import logging

# Create a module-level logger for debugging and error tracking.
logger = logging.getLogger(__name__)


# -------------------------------------------------
# PROMPT TEMPLATE
#
# The system prompt sets the persona: a practical pricing assistant
# for Malaysian retail. The user prompt injects the specific data.
# -------------------------------------------------

SYSTEM_PROMPT = """You are a pricing assistant for a small retail shop in Malaysia.
Explain the recommended price for a product in 2-3 concise sentences.
Be practical and direct — the shop owner needs a clear, actionable insight.
Mention the market context if available. Keep it under 100 words.

CRITICAL LEGAL RULES:
- If a PCAPA warning is present, you MUST NOT advise the user to raise the price closer to the market median or any higher price. PCAPA law strictly forbids arbitrary margin increases without a cost increase. Giving such advice would constitute illegal business guidance.
- If the regulatory cap (KPDN Barangan Kawalan) was applied, emphasize that the cap is a strict legal compliance measure and must not be overridden.
- Never suggest pricing strategies that would increase margins beyond the baseline without a documented cost increase."""

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
{pcapa_details}
{regulatory_context}
Explain why this price is recommended. Be concise (2-3 sentences).
If PCAPA warning is Yes, you MUST NOT suggest raising the price. The recommended price is the legal maximum given current constraints."""


# -------------------------------------------------
# MAIN FUNCTION — Entry point for generating explanations
# -------------------------------------------------

def generate_pricing_explanation(product, market_stats, recommendation):
    """Generate a natural-language pricing explanation via Gemini 3.5 Flash Lite.

    This function orchestrates the three-layer fallback chain:
      1. Build the prompt from product/market/recommendation data.
      2. Attempt the Gemini API call.
      3. On failure, fall back to the deterministic string template.

    The function NEVER raises exceptions — it always returns a string.
    This is critical for application stability: a broken API key or
    network issue should degrade gracefully, not crash the dashboard.

    Args:
        product: Product ORM object (has name, cost_price, target_margin,
                 baseline_margin, selling_price).
        market_stats: Dict from get_market_stats() with keys like 'median',
                      'min', 'max', 'n'. May have n=0 (no market data).
        recommendation: Dict from get_price_recommendation() with keys
                        like 'recommended_price', 'confidence',
                        'guardrails_applied', 'warnings', 'diff_pct'.

    Returns:
        A string (2-3 sentences) explaining the price recommendation.
        Never returns None or empty string.
    """
    # --- Step 1: Build the prompt payload ---

    # Format the current selling price for the prompt.
    current = (f"RM{float(product.selling_price):.2f}"
               if product.selling_price else "Not set")

    # Format market median for the prompt.
    mkt_median = (f"RM{market_stats['median']:.2f}"
                  if market_stats.get("median") else "Not available")

    # Format market range for the prompt.
    mkt_range = "N/A"
    if market_stats.get("min") and market_stats.get("max"):
        mkt_range = f"RM{market_stats['min']:.2f} \u2013 RM{market_stats['max']:.2f}"

    # Format guardrails and PCAPA status.
    guardrails = ", ".join(recommendation.get("guardrails_applied", [])) or "None"
    pcapa_warn = "Yes" if recommendation.get("warnings") else "No"

    # Include the full PCAPA warning text so the LLM understands the legal context.
    pcapa_details = ""
    if recommendation.get("warnings"):
        pcapa_details = f"PCAPA details: {recommendation['warnings'][0]}"

    # Build the regulatory context for KPDN Barangan Kawalan products.
    # This is injected into the prompt ONLY when the regulatory cap was applied,
    # ensuring the LLM emphasizes the legal compliance aspect.
    regulatory_context = ""
    if recommendation.get("regulatory_cap_applied"):
        ceiling = recommendation.get("government_ceiling_price", "unknown")
        regulatory_context = (
            f"WARNING: This product is a Malaysian Barangan Kawalan (Price-Controlled Good). "
            f"The recommended price was legally capped at the official KPDN ceiling price of "
            f"RM{ceiling}. Emphasize that this is a strict legal compliance measure."
        )

    # --- Step 2: Format the complete prompt ---
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
        pcapa_details=pcapa_details,
        regulatory_context=regulatory_context,
    )

    # --- Step 3: Attempt the Gemini API call ---
    try:
        return _call_gemini(prompt)
    except Exception as e:
        # Layer 2: Any exception from the API call triggers the fallback.
        # Log the error for debugging but NEVER let it crash the app.
        logger.warning("Gemini API call failed: %s", e)
        return _fallback(product, market_stats, recommendation)


def _call_gemini(prompt):
    """Call Gemini 3.5 Flash Lite and return the response text.

    This function handles the raw API interaction. It:
      1. Checks for the GEMINI_API_KEY environment variable.
      2. Initializes the Google GenAI client.
      3. Sends the prompt with the system prompt.
      4. Returns the response text.

    Raises:
        RuntimeError: If the API key is missing or the response is empty.
        Any Google API exception: Network errors, rate limits, etc.

    The caller (generate_pricing_explanation) handles all exceptions
    via the three-layer fallback chain.
    """
    # Step 1: Verify the API key exists.
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set in environment")

    # Step 2: Initialize the Google GenAI client.
    from google import genai
    client = genai.Client(api_key=api_key)

    # Step 3: Send the prompt to Gemini 3.5 Flash Lite.
    # The prompt includes both the system prompt (persona) and the
    # user prompt (specific data) as two parts of a single user message.
    response = client.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents=[
            {"role": "user", "parts": [
                {"text": SYSTEM_PROMPT},
                {"text": prompt},
            ]}
        ],
    )

    # Step 4: Validate the response.
    text = response.text
    if not text or not text.strip():
        raise RuntimeError("Gemini returned empty response")
    return text.strip()


def _fallback(product, market_stats, recommendation):
    """Generate a deterministic fallback explanation without the LLM.

    This function produces a sensible explanation using pure string
    formatting — no external API calls, no randomness, no failure modes.
    It is the safety net that guarantees the pricing tab always shows
    an explanation, even when Gemini is completely unavailable.

    The fallback covers three scenarios:
      1. Market data available: mentions the price difference from current.
      2. No market data: explains the recommendation is cost-based only.
      3. Warnings present: appends the PCAPA warning.

    Args:
        product: Product ORM object.
        market_stats: Dict with market statistics.
        recommendation: Dict with recommendation details.

    Returns:
        A non-empty string (always — this function never fails).
    """
    # Start with the core recommendation statement.
    parts = [
        f"Recommended price RM{recommendation['recommended_price']:.2f} "
        f"for {product.name}."
    ]

    # Add market context if available.
    if market_stats.get("median"):
        ppi = recommendation.get("diff_pct")
        if ppi is not None:
            # Compute direction (above/below current price) and percentage.
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
        # No market data: explain the recommendation is cost-based only.
        parts.append(
            "No verified market data available \u2014 "
            "recommendation based on cost structure and target margin."
        )

    # Append any compliance warnings (e.g. PCAPA).
    if recommendation.get("warnings"):
        parts.append("\u26a0 " + recommendation["warnings"][0])
        # Legal constraint: when PCAPA warning is active, explicitly state
        # that the price must not be increased further (prevents LLM advice
        # to raise prices toward market median, which would worsen the violation).
        parts.append(
            "Do not increase the price further — "
            "the PCAPA law strictly forbids margin increases without "
            "a documented cost increase."
        )

    return " ".join(parts)
