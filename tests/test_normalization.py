"""
Unit tests for utils/normalization.py (Phase 3A market-data foundation).

Pure function tests - no database required.

Run standalone:
    ./venv/Scripts/python.exe tests/test_normalization.py

or collect with pytest (if ever added) since each case is a
`test_*` function using plain asserts.
"""
import os
import sys

# Make the project root importable when run as a plain script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.normalization import (          # noqa: E402
    clean_text,
    normalize_package_size,
    calculate_unit_price,
)


# -------------------------------------------------
# clean_text
# -------------------------------------------------
def test_clean_text_lowercases_and_trims():
    assert clean_text('  BERAS CAP JASMINE  ') == 'beras cap jasmine'


def test_clean_text_strips_trademark_and_decorative_chars():
    assert clean_text('Milo\u2122 1kg') == 'milo 1kg'
    assert clean_text('Beras\u00ae *5KG') == 'beras 5kg'
    assert clean_text('SPRITE\u00a9 1.5L') == 'sprite 1.5l'


def test_clean_text_collapses_multiple_spaces():
    assert clean_text('  beras    cap   jasmine\t5kg ') == 'beras cap jasmine 5kg'


def test_clean_text_empty_and_none():
    assert clean_text('') == ''
    assert clean_text(None) == ''
    assert clean_text('   ') == ''


# -------------------------------------------------
# normalize_package_size
# -------------------------------------------------
def test_grams_to_kg():
    assert normalize_package_size(500, 'g') == (0.5, 'kg')
    assert normalize_package_size(1000, 'g') == (1.0, 'kg')
    assert normalize_package_size(250, 'gram') == (0.25, 'kg')
    assert normalize_package_size(750, 'grams') == (0.75, 'kg')


def test_kilograms_stay_kg():
    assert normalize_package_size(1, 'kg') == (1.0, 'kg')
    assert normalize_package_size(2.5, 'kg') == (2.5, 'kg')
    assert normalize_package_size(3, 'kilogram') == (3.0, 'kg')


def test_milliliters_to_litres():
    assert normalize_package_size(250, 'ml') == (0.25, 'l')
    assert normalize_package_size(1500, 'ML') == (1.5, 'l')


def test_litres_stay_litres():
    assert normalize_package_size(1, 'L') == (1.0, 'l')
    assert normalize_package_size(1.5, 'l') == (1.5, 'l')
    assert normalize_package_size(2, 'liter') == (2.0, 'l')
    assert normalize_package_size(3, 'litre') == (3.0, 'l')
    assert normalize_package_size(2, 'litres') == (2.0, 'l')


def test_counts_become_unit():
    assert normalize_package_size(5, 'pcs') == (5, 'unit')
    assert normalize_package_size(1, 'pack') == (1, 'unit')
    assert normalize_package_size(10, 'packs') == (10, 'unit')
    assert normalize_package_size(12, 'rolls') == (12, 'unit')
    assert normalize_package_size(24, 'units') == (24, 'unit')


def test_case_and_space_insensitive_units():
    assert normalize_package_size(500, '  G ') == (0.5, 'kg')
    assert normalize_package_size(250, 'Ml') == (0.25, 'l')
    assert normalize_package_size(5, 'PCS') == (5, 'unit')


def test_unknown_unit_passes_through():
    assert normalize_package_size(3, 'carton') == (3, 'carton')
    assert normalize_package_size(2, '') == (2, '')


def test_zero_quantity():
    assert normalize_package_size(0, 'kg') == (0.0, 'kg')
    assert normalize_package_size(0, 'g') == (0.0, 'kg')


# -------------------------------------------------
# calculate_unit_price
# -------------------------------------------------
def test_price_per_kg():
    assert calculate_unit_price(12.00, 1, 'kg') == 12.0
    assert calculate_unit_price(6.00, 500, 'g') == 12.0      # 500 g = 0.5 kg
    assert calculate_unit_price(30.00, 2, 'kg') == 15.0


def test_price_per_litre():
    assert calculate_unit_price(2.50, 1, 'L') == 2.5
    assert calculate_unit_price(5.00, 2.5, 'L') == 2.0
    assert calculate_unit_price(1.25, 250, 'ml') == 5.0      # 250 ml = 0.25 l


def test_price_per_unit():
    assert calculate_unit_price(10.00, 5, 'pcs') == 2.0
    assert calculate_unit_price(24.00, 12, 'packs') == 2.0


def test_unit_price_zero_quantity_returns_zero():
    assert calculate_unit_price(10.00, 0, 'kg') == 0.0
    assert calculate_unit_price(10.00, -2, 'g') == 0.0


# -------------------------------------------------
# runner (works without pytest)
# -------------------------------------------------
def _all_tests():
    return [(name, fn) for name, fn in sorted(globals().items())
            if name.startswith('test_') and callable(fn)]


def main():
    tests = _all_tests()
    passed = 0
    failed = []
    for name, fn in tests:
        try:
            fn()
            passed += 1
        except Exception as exc:                                    # noqa: BLE001
            failed.append((name, exc))
    print(f"test_normalization: {passed}/{len(tests)} passed")
    for name, exc in failed:
        print(f"  FAIL {name}: {exc}")
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
