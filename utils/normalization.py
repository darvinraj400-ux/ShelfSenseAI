"""
============================================================
 ShelfSenseAI - Text & package-size normalization utilities
============================================================
 Phase 3A "Market Data Foundation" helpers.

 These functions turn messy external market data (PriceCatcher
 titles, online-retailer SKUs, mixed units) into a single,
 comparable form so that later phases can match market items to
 shop products and compute a meaningful "price per kg / per L /
 per unit" for comparisons.

 Key idea:
   - Text is cleaned so "BERAS 5KG™  " and "beras 5 kg" can be
     compared.
   - Package sizes are normalized to a SMALL set of base units
     (kg, l, unit) so 500 g == 0.5 kg and 250 ml == 0.25 l.
   - Unit prices are expressed per BASE unit (e.g. RM per 1 kg),
     which is what a fair price comparison needs.
============================================================
"""

# -------------------------------------------------
# Base-unit table: every recognized unit collapses to
# one of these three canonical units.
# -------------------------------------------------
_WEIGHT_TO_KG = {
    'kg': 1.0,
    'kilogram': 1.0,
    'kilograms': 1.0,
    'g': 0.001,
    'gram': 0.001,
    'grams': 0.001,
}
_VOLUME_TO_L = {
    'l': 1.0,
    'liter': 1.0,
    'liters': 1.0,
    'litre': 1.0,
    'litres': 1.0,
    'ml': 0.001,
    'milliliter': 0.001,
    'milliliters': 0.001,
}
_COUNT_TO_UNIT = {
    'unit': 1.0,
    'units': 1.0,
    'pcs': 1.0,
    'pc': 1.0,
    'piece': 1.0,
    'pieces': 1.0,
    'pack': 1.0,
    'packs': 1.0,
    'roll': 1.0,
    'rolls': 1.0,
}


def clean_text(text: str) -> str:
    """Normalize a free-text string for comparison.

    - lowercases the input
    - strips decorative/trademark characters (™ ® © * etc.)
    - collapses runs of whitespace into a single space
    - trims leading/trailing whitespace

    Example: ``"  BERAS 5KG™  * " -> "beras 5kg"``

    Returns an empty string for ``None`` or blank input.
    """
    if not text:
        return ''
    # Remove characters that carry no meaning for matching.
    # translate() is faster than a regex for single-char removal.
    table = str.maketrans('', '', '\u2122\u00ae\u00a9*"\'`~#%^&()[]{}<>')
    cleaned = text.translate(table)
    return ' '.join(cleaned.lower().split())


def normalize_package_size(quantity: float, unit: str) -> tuple[float, str]:
    """Convert a (quantity, unit) package size to a standard base unit.

    Returns ``(quantity_in_base_unit, base_unit)``:

    - g / gram / grams  -> kg          (quantity / 1000)
    - ml / milliliter   -> l           (quantity / 1000)
    - l / liter / litre -> l           (quantity unchanged)
    - kg / kilogram     -> kg          (quantity unchanged)
    - pcs / packs / rolls / units -> unit (quantity unchanged)

    Matching is case-insensitive and tolerates surrounding spaces.
    Unrecognized units pass through unchanged (lowercased) so no
    data is ever silently dropped.

    Examples::

        normalize_package_size(500, 'g')   -> (0.5, 'kg')
        normalize_package_size(250, 'ML')  -> (0.25, 'l')
        normalize_package_size(5, 'pcs')   -> (5, 'unit')
    """
    q = float(quantity)
    u = (unit or '').strip().lower()

    if u in _WEIGHT_TO_KG:
        return (round(q * _WEIGHT_TO_KG[u], 6), 'kg')
    if u in _VOLUME_TO_L:
        return (round(q * _VOLUME_TO_L[u], 6), 'l')
    if u in _COUNT_TO_UNIT:
        return (q, 'unit')
    # Unknown unit: keep quantity, keep (lowercased) unit.
    return (q, u)


def calculate_unit_price(price: float, quantity: float, unit: str) -> float:
    """Return the price per standard base unit (e.g. RM per 1 kg).

    Uses :func:`normalize_package_size` first, then divides.

    Examples::

        calculate_unit_price(12.00, 1, 'kg')    -> 12.0   (RM/kg)
        calculate_unit_price(6.00, 500, 'g')    -> 12.0   (500 g -> 0.5 kg)
        calculate_unit_price(5.00, 2.5, 'L')    -> 2.0    (RM/L)
        calculate_unit_price(10.00, 5, 'pcs')   -> 2.0    (RM/unit)

    A zero or negative quantity (no meaningful package size) returns
    ``0.0`` instead of dividing by zero.
    """
    q, _ = normalize_package_size(quantity, unit)
    if q <= 0:
        return 0.0
    return round(float(price) / q, 4)
