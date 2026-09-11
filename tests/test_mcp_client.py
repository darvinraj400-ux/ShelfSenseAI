"""
Mock-based tests for the ManaMurah MCP client layer
(services/mcp_client.py).

These tests NEVER touch the network — every MCP session is mocked —
so they run fast and deterministically in any environment.

Coverage:
  - successful response parsing (well-formed FAMA series)
  - malformed response (non-JSON text block)
  - connection failure (exception inside the session)
  - empty response (no content blocks)
  - business-level "no_data" envelope
  - per-point validation (bad dates, zero/negative prices)
  - invalid level argument rejected before any network call

Run standalone:
    ./venv/Scripts/python.exe tests/test_mcp_client.py
"""
import os
import sys
from datetime import date
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.mcp_client import (ManaMurahClient,      # noqa: E402
                                 _parse_json_object,
                                 _parse_observation_point)

PASSED = FAILED = 0


def check(label, cond):
    global PASSED, FAILED
    if cond:
        PASSED += 1; print(f"  [PASS] {label}")
    else:
        FAILED += 1; print(f"  [FAIL] {label}")


def _fake_mcp_result(payload_text):
    """Build a MagicMock MCP CallToolResult with one text block."""
    block = MagicMock()
    block.text = payload_text
    result = MagicMock()
    result.content = [block]
    return result


# Well-formed fama_price_history payload (verified live shape).
_GOOD_PAYLOAD = """
{
  "item_id": 46, "item_name": "TELUR AYAM", "unit": "Biji",
  "level": "RUNCIT", "grain": "national",
  "series": [
    {"date": "2026-09-03", "harga": 0.5202},
    {"date": "2026-09-04", "harga": 0.4962}
  ],
  "days_requested": 5, "days_returned": 2,
  "status": "ok", "reason": null, "warnings": []
}
"""


# ======================================================== PARSING TESTS
def test_parse_json_object_valid():
    check('valid JSON object parses', _parse_json_object('{"a": 1}') == {'a': 1})


def test_parse_json_object_malformed():
    check('malformed JSON returns None',
          _parse_json_object('<html>gateway error</html>') is None)


def test_parse_json_object_empty():
    check('empty text returns None', _parse_json_object('') is None)
    check('None text returns None', _parse_json_object(None) is None)


def test_parse_json_object_non_object():
    # A JSON array or scalar is not a valid tool envelope.
    check('JSON array returns None', _parse_json_object('[1,2,3]') is None)


def test_parse_observation_point_valid():
    obs = _parse_observation_point({'date': '2026-09-03', 'harga': 0.5202})
    check('valid point parsed', obs is not None and
          obs['date'] == date(2026, 9, 3) and obs['price'] == 0.5202)


def test_parse_observation_point_bad_date():
    check('bad date rejected',
          _parse_observation_point({'date': '03/09/2026', 'harga': 1.0}) is None)


def test_parse_observation_point_bad_price():
    check('zero price rejected',
          _parse_observation_point({'date': '2026-09-03', 'harga': 0}) is None)
    check('negative price rejected',
          _parse_observation_point({'date': '2026-09-03', 'harga': -1.5}) is None)
    check('non-numeric price rejected',
          _parse_observation_point({'date': '2026-09-03', 'harga': 'cheap'}) is None)


def test_parse_observation_point_not_dict():
    check('non-dict point rejected', _parse_observation_point([1, 2]) is None)


# ======================================================== CLIENT TESTS
def test_call_tool_success():
    """A well-formed tool response is parsed into records."""
    client = ManaMurahClient(url='https://mock.invalid/mcp')
    with patch.object(client, '_call_tool_async',
                      return_value={'status': 'ok',
                                    'item_name': 'TELUR AYAM',
                                    'unit': 'Biji',
                                    'series': [{'date': '2026-09-03',
                                                'harga': 0.52}]}):
        records = client.fetch_fama_records([(46, 'TELUR AYAM')],
                                            level='RUNCIT', days=5)
    check('one record returned', len(records) == 1)
    rec = records[0]
    check('external_id format', rec['external_id'] == 'fama-46-runcit')
    check('raw_title from server', rec['raw_title'] == 'TELUR AYAM')
    check('observation price parsed',
          rec['observations'][0]['price'] == 0.52)
    check('observation date parsed',
          rec['observations'][0]['date'] == date(2026, 9, 3))


def test_call_tool_connection_failure():
    """A transport exception must yield zero records, never a crash."""
    client = ManaMurahClient(url='https://mock.invalid/mcp')
    with patch.object(client, '_call_tool_async',
                      side_effect=ConnectionError('boom')):
        records = client.fetch_fama_records([(46, 'TELUR AYAM')])
    check('connection failure -> empty records', records == [])


def test_call_tool_malformed_payload():
    """A non-JSON text block must yield zero records, never a crash."""
    client = ManaMurahClient(url='https://mock.invalid/mcp')
    with patch.object(client, '_call_tool_async',
                      return_value='<html>502 Bad Gateway</html>'):
        records = client.fetch_fama_records([(46, 'TELUR AYAM')])
    check('malformed payload -> empty records', records == [])


def test_call_tool_empty_content():
    """An empty content list must yield zero records."""
    client = ManaMurahClient(url='https://mock.invalid/mcp')
    with patch.object(client, '_call_tool_async', return_value=None):
        records = client.fetch_fama_records([(46, 'TELUR AYAM')])
    check('empty response -> empty records', records == [])


def test_no_data_envelope():
    """status=no_data (HTTP 200 business error) yields zero records."""
    client = ManaMurahClient(url='https://mock.invalid/mcp')
    with patch.object(client, '_call_tool_async',
                      return_value={'status': 'no_data',
                                    'reason': 'no observations in window'}):
        records = client.fetch_fama_records([(46, 'TELUR AYAM')])
    check('no_data envelope -> empty records', records == [])


def test_one_bad_point_does_not_kill_series():
    """A single invalid point is dropped; valid siblings survive."""
    client = ManaMurahClient(url='https://mock.invalid/mcp')
    payload = {'status': 'ok', 'item_name': 'TELUR AYAM', 'unit': 'Biji',
               'series': [
                   {'date': '2026-09-03', 'harga': 0.52},
                   {'date': 'garbage', 'harga': 0.52},
                   {'date': '2026-09-04', 'harga': 0},
                   {'date': '2026-09-05', 'harga': 0.51},
               ]}
    with patch.object(client, '_call_tool_async', return_value=payload):
        records = client.fetch_fama_records([(46, 'TELUR AYAM')])
    check('valid points survive bad neighbours',
          len(records) == 1 and len(records[0]['observations']) == 2)


def test_invalid_level_rejected_locally():
    """An invalid level must be rejected before any network call."""
    client = ManaMurahClient(url='https://mock.invalid/mcp')
    with patch.object(client, '_call_tool_async') as mock_call:
        records = client.fetch_fama_records([(46, 'TELUR AYAM')],
                                            level='WHOLESALE')
    check('invalid level -> no records, no call',
          records == [] and not mock_call.called)


def test_state_slug_sets_grain():
    """Passing a state slug switches the tool to state grain."""
    client = ManaMurahClient(url='https://mock.invalid/mcp')
    captured = {}

    def fake_call(tool_name, arguments):
        captured.update(arguments)
        return {'status': 'ok', 'item_name': 'TELUR AYAM', 'unit': 'Biji',
                'series': [{'date': '2026-09-03', 'harga': 0.49}]}

    with patch.object(client, '_call_tool_async', side_effect=fake_call):
        records = client.fetch_fama_records([(46, 'TELUR AYAM')],
                                            state_slug='johor')
    check('grain=state requested', captured.get('grain') == 'state')
    check('state_slug forwarded', captured.get('state_slug') == 'johor')
    check('state attributed to record',
          records and records[0]['observation_state'] == 'Johor')


# -------------------------------------------------
# runner (works without pytest)
# -------------------------------------------------
def main():
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith('test_') and callable(fn)]
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:                        # noqa: BLE001
            global FAILED
            FAILED += 1
            print(f"  [FAIL] {name}: {type(exc).__name__}: {exc}")
    print(f"test_mcp_client: {PASSED}/{PASSED + FAILED} checks passed")
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
