"""
============================================================
 ShelfSenseAI - Text & package-size normalization utilities
============================================================

This module provides the foundational data normalization layer for the
Market Intelligence engine. External market data (PriceCatcher government
records, online retailer SKUs, manual observations) arrives in inconsistent
formats — mixed units, decorative characters, varying capitalization — and
this module transforms them into a single, comparable form.

The normalization pipeline enables three critical downstream operations:
  1. TEXT COMPARISON: Matching shop products to market items requires that
     "BERAS 5KG\u2122" and "beras 5 kg" are treated as the same text.
  2. UNIT STANDARDIZATION: Package sizes are collapsed to a small set of
     base units (kg, l, unit) so that 500 g == 0.5 kg and 250 ml == 0.25 l.
  3. PRICE NORMALIZATION: Unit prices are expressed per BASE unit
     (e.g. RM per 1 kg), which is the only fair way to compare prices
     across differently-sized packages (e.g. a 1 kg bag vs a 5 kg bag).

Architectural decision: These functions are PURE (no database access,
no side effects) and therefore fully unit-testable in isolation.
"""

# -------------------------------------------------
# BASE-UNIT TABLES
#
# Every recognized unit in the PriceCatcher / online-retailer catalogues
# is mapped to one of three canonical base units. This is the authoritative
# mapping that governs how all downstream comparisons work.
#
# Design rationale: We chose kg/l/unit because they cover the three
# fundamental measurement dimensions (mass, volume, count) that appear
# in Malaysian retail. Compound units like "kg/L" are handled by the
# ETL parser at ingestion time, not here.
# -------------------------------------------------

# Weight units: everything collapses to kilograms (kg).
# The numeric factor converts the source quantity to kg.
_WEIGHT_TO_KG = {
    'kg': 1.0,          # already in kg — identity transform
    'kilogram': 1.0,    # full word variant
    'kilograms': 1.0,   # plural variant
    'g': 0.001,         # grams: divide by 1000
    'gram': 0.001,      # singular gram
    'grams': 0.001,     # plural grams
}

# Volume units: everything collapses to litres (l).
# The numeric factor converts the source quantity to litres.
_VOLUME_TO_L = {
    'l': 1.0,           # already in litres — identity transform
    'liter': 1.0,       # American spelling
    'liters': 1.0,      # American plural
    'litre': 1.0,       # British spelling (common in Malaysia)
    'litres': 1.0,      # British plural
    'ml': 0.001,        # millilitres: divide by 1000
    'milliliter': 0.001,# full word singular
    'milliliters': 0.001,# full word plural
}

# Count units: everything collapses to 'unit'.
# The quantity is preserved as-is (no conversion needed for discrete counts).
_COUNT_TO_UNIT = {
    'unit': 1.0,        # generic count unit
    'units': 1.0,       # plural
    'pcs': 1.0,         # "pieces" abbreviation (common in Malaysian retail)
    'pc': 1.0,          # singular abbreviation
    'piece': 1.0,       # full word
    'pieces': 1.0,      # plural full word
    'pack': 1.0,        # pack of items
    'packs': 1.0,       # plural packs
    'roll': 1.0,        # roll (e.g. toilet paper)
    'rolls': 1.0,       # plural rolls
}


def clean_text(text: str) -> str:
    """Normalize a free-text string for comparison.

    This function strips decorative/trademark characters, lowercases,
    and collapses whitespace so that two semantically identical strings
    produce the same output regardless of formatting differences.

    Processing steps:
      1. Lowercase the entire string (BERAS -> beras)
      2. Remove trademark/decorative characters (\u2122, \u00ae, \u00a9, *)
         that carry no meaning for product matching
      3. Collapse runs of whitespace (spaces, tabs) into a single space
      4. Trim leading/trailing whitespace

    Args:
        text: The raw string to clean (may be None, empty, or whitespace).

    Returns:
        A cleaned, lowercased, trimmed string. Returns '' for None/blank input.

    Examples:
        >>> clean_text('  BERAS 5KG\u2122  * ')
        'beras 5kg'
        >>> clean_text('Milo\u00ae 1kg')
        'milo 1kg'
        >>> clean_text(None)
        ''
    """
    # Guard clause: None or empty input returns immediately.
    # This prevents AttributeError on .translate() / .lower() calls.
    if not text:
        return ''

    # Build a translation table that removes all decorative/trademark characters.
    # translate() is faster than regex for single-character removal because
    # Python executes the deletion in C code rather than interpreting a pattern.
    table = str.maketrans('', '', '\u2122\u00ae\u00a9*\"\'`~#%^&()[]{}<>')
    cleaned = text.translate(table)

    # Lowercase + split/join collapses all whitespace runs into single spaces
    # and trims leading/trailing whitespace in one operation.
    return ' '.join(cleaned.lower().split())


def normalize_package_size(quantity: float, unit: str) -> tuple[float, str]:
    """Convert a (quantity, unit) package size to a standard base unit.

    This is the core normalization function that enables fair price
    comparisons across differently-sized packages. A 500g packet of
    rice and a 10kg bag of rice are expressed in the same unit (kg)
    so their per-kg prices can be directly compared.

    Conversion rules:
      - g / gram / grams  -> kg  (quantity / 1000)
      - ml / milliliter   -> l   (quantity / 1000)
      - l / liter / litre -> l   (quantity unchanged)
      - kg / kilogram     -> kg  (quantity unchanged)
      - pcs / packs / rolls / units -> unit (quantity unchanged)
      - Unknown units     -> pass through unchanged (lowercased)

    Matching is case-insensitive and tolerates surrounding whitespace
    (e.g. '  G ' matches 'g' -> kg).

    Args:
        quantity: The numeric amount in the source unit (e.g. 500 for 500g).
        unit: The unit string (e.g. 'g', 'kg', 'ml', 'pcs').

    Returns:
        A tuple of (converted_quantity, base_unit):
        - (0.5, 'kg') for 500g
        - (0.25, 'l') for 250ml
        - (5, 'unit') for 5 pcs
        - (3, 'carton') for unknown units (passed through)

    Examples:
        >>> normalize_package_size(500, 'g')
        (0.5, 'kg')
        >>> normalize_package_size(250, 'ML')
        (0.25, 'l')
        >>> normalize_package_size(5, 'pcs')
        (5, 'unit')
    """
    # Convert to float for arithmetic safety (handles int/float/Decimal inputs).
    q = float(quantity)

    # Normalize the unit string: strip whitespace and lowercase for matching.
    # This handles inputs like '  G ', 'KG', 'Ml' uniformly.
    u = (unit or '').strip().lower()

    # Check weight units first: if the unit is in _WEIGHT_TO_KG, convert to kg.
    if u in _WEIGHT_TO_KG:
        return (round(q * _WEIGHT_TO_KG[u], 6), 'kg')

    # Check volume units: if the unit is in _VOLUME_TO_L, convert to litres.
    if u in _VOLUME_TO_L:
        return (round(q * _VOLUME_TO_L[u], 6), 'l')

    # Check count units: discrete counts (pcs, packs, rolls) become 'unit'.
    if u in _COUNT_TO_UNIT:
        return (q, 'unit')

    # Unknown unit: preserve the quantity and lowercased unit as-is.
    # This ensures no data is silently dropped — the caller decides
    # how to handle truly unrecognized units.
    return (q, u)


def calculate_unit_price(price: float, quantity: float, unit: str) -> float:
    """Return the price per standard base unit (e.g. RM per 1 kg).

    This function is the foundation for fair cross-product price
    comparisons. It first normalizes the package size using
    normalize_package_size(), then divides the price by the base-unit
    quantity to produce a per-unit price.

    For example, a RM12.00 bag of 500g rice has a unit price of
    RM24.00/kg (12.00 / 0.5), while a RM25.00 bag of 10kg rice
    has a unit price of RM2.50/kg (25.00 / 10). The per-unit
    comparison reveals the true value difference.

    Args:
        price: The total price for the package (e.g. 12.00 for RM12.00).
        quantity: The numeric amount in the source unit.
        unit: The unit string (e.g. 'g', 'kg', 'L', 'pcs').

    Returns:
        The price per base unit, rounded to 4 decimal places.
        Returns 0.0 for zero/negative quantities (avoids division by zero).

    Examples:
        >>> calculate_unit_price(12.00, 1, 'kg')    # RM12 per 1 kg
        12.0
        >>> calculate_unit_price(6.00, 500, 'g')    # RM6 per 500g = RM12/kg
        12.0
        >>> calculate_unit_price(5.00, 2.5, 'L')    # RM5 per 2.5L = RM2/L
        2.0
        >>> calculate_unit_price(10.00, 5, 'pcs')   # RM10 per 5 pcs = RM2/ea
        2.0
    """
    # Step 1: Normalize the quantity to base units (e.g. 500g -> 0.5kg).
    q, _ = normalize_package_size(quantity, unit)

    # Step 2: Guard against division by zero or negative quantities.
    # A package with no meaningful size cannot have a per-unit price.
    if q <= 0:
        return 0.0

    # Step 3: Divide price by the base-unit quantity and round to 4 decimals
    # for consistent precision across all unit price calculations.
    return round(float(price) / q, 4)
