"""
====================================================================
 ShelfSenseAI - ManaMurah MCP Client Layer
====================================================================

Connects to the ManaMurah MCP server (https://mcp.manamurah.com/mcp)
and converts its tool responses into SOURCE-AGNOSTIC market records
that services/market_ingestion.py can persist.

ARCHITECTURAL ROLE (Phase 5):
    ManaMurah MCP  ->  mcp_client.py  ->  normalized records
                                          ->  market_ingestion.py
                                                ->  Phase 3A tables

This module contains ONLY MCP protocol logic. It must never import
Flask, SQLAlchemy, or any ShelfSenseAI model — the database layer
(market_ingestion.py) depends on plain dicts produced here, not on
MCP protocol objects.

VERIFIED SERVER FACTS (probed live, 2026-09-08; see README of
github.com/manamurah/mcp-server):
  - Transport: streamable HTTP, JSON-RPC 2.0, POST /mcp.
  - 15 read-only tools, no credentials, no client-side rate limits
    (upstream has a 12h KV edge cache).
  - 11 KPDN PriceCatcher tools (weekly averages) — these expose the
    SAME underlying data our PriceCatcher ETL already ingests, so we
    deliberately do NOT ingest them (would duplicate observations).
  - 3 FAMA Panduan Harga Harian tools (daily cadence) — FAMA's
    catalogue is INDEPENDENT of PriceCatcher (item_id 1..46 is
    FAMA's own id, not the KPDN item_code). This is the genuinely
    independent dataset and the only thing we ingest.

Response envelope (verified from live calls):
    {"item_id": 46, "item_name": "TELUR AYAM", "unit": "Biji",
     "level": "RUNCIT", "grain": "national", ...
     "series": [{"date": "2026-09-03", "harga": 0.5202}, ...],
     "days_requested": 5, "days_returned": 4,
     "missing_dates": [...], "status": "ok", "reason": null,
     "warnings": []}

Business-level "no data" comes back with HTTP 200 and
status="no_data"; only transport failures raise. The client maps
both to safe return values (None / empty list) so the sync script
never crashes on an empty catalog.

Usage (sync script / future scheduled job):
    from services.mcp_client import ManaMurahClient
    client = ManaMurahClient()          # reads MANAMURAH_MCP_URL env
    records = client.fetch_fama_records(level="RUNCIT", days=30)
"""
import asyncio
import logging
import os
from datetime import date, datetime

log = logging.getLogger(__name__)

# Default remote endpoint (public, read-only, no credentials needed).
DEFAULT_MCP_URL = 'https://mcp.manamurah.com/mcp'

# Valid FAMA price levels (verified from the tool's JSON-Schema enum).
FAMA_LEVELS = ('RUNCIT', 'BORONG', 'LADANG')

# Geographic grains (verified enum). 'daerah' is sparse; we default
# to state-level with national fallback, mirroring the Phase 4
# geographic localization strategy in market_analysis.py.
FAMA_GRAINS = ('national', 'state', 'daerah')


class ManaMurahClient:
    """Thin async MCP client for the ManaMurah PriceCatcher/FAMA server.

    Responsibilities:
      - Establish a streamable-HTTP MCP session (initialize handshake).
      - Call verified tools with verified parameters.
      - Parse JSON responses defensively (missing keys, bad types, and
        business-level "no_data" envelopes must never raise).
      - Convert responses into plain-dict "market records":
            {external_id, raw_title, package_quantity, package_unit,
             category, observations: [{date, price, state, district}]}
        which market_ingestion.py upserts into the Phase 3A tables.
    """

    def __init__(self, url=None):
        # URL comes from the environment so deployments can point at a
        # self-hosted copy (see "Self-host / fork" in the MCP README).
        # No credentials exist for this server — documented, not hidden.
        self.url = url or os.environ.get('MANAMURAH_MCP_URL', DEFAULT_MCP_URL)

    # -------------------------------------------------------------
    # Low-level session plumbing
    # -------------------------------------------------------------
    def _run(self, coro):
        """Run an async coroutine to completion from sync code.

        The official `mcp` SDK is asyncio-native; the sync script and
        Flask routes are synchronous, so this bridge keeps call sites
        simple. A fresh event loop per call is acceptable here because
        a sync run performs a handful of calls at most."""
        return asyncio.run(coro)

    async def _call_tool_async(self, tool_name, arguments):
        """Open a session, initialize, and invoke one tool.

        Returns the parsed JSON payload (dict) from the tool's text
        content, or None on any transport/parse failure. All failures
        are logged, never raised, so a flaky network cannot crash a
        synchronization run midway."""
        # Imported lazily so importing this module never fails on
        # machines where the `mcp` package is not installed (tests
        # mock the client instead).
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        try:
            async with streamablehttp_client(self.url) as (read, write, _):
                async with ClientSession(read, write) as session:
                    # MCP handshake — required before any tools/call.
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments)
                    # The server returns JSON inside a text content block
                    # (and mirrors it in structuredContent). Parse the
                    # text block so we depend on the documented contract.
                    if not result.content:
                        log.warning('ManaMurah %s returned empty content',
                                    tool_name)
                        return None
                    text = result.content[0].text
                    return _parse_json_object(text)
        except Exception as exc:  # transport, timeout, JSON-RPC error
            log.error('ManaMurah MCP call %s failed: %s', tool_name, exc)
            return None

    def call_tool(self, tool_name, arguments):
        """Synchronously call one MCP tool and return the parsed dict.

        Returns None on connection failure or unparseable response.
        The try/except here is the outer safety net: even if an
        exception escapes the async session block, the caller (sync
        script) must never see it."""
        try:
            return self._run(self._call_tool_async(tool_name, arguments))
        except Exception as exc:                        # noqa: BLE001
            log.error('ManaMurah MCP transport failure for %s: %s',
                      tool_name, exc)
            return None

    # -------------------------------------------------------------
    # FAMA record retrieval (the only data we ingest)
    # -------------------------------------------------------------
    def fetch_fama_records(self, items, level='RUNCIT', days=30,
                           state_slug=None):
        """Fetch daily FAMA retail/wholesale/farm-gate prices as
        source-agnostic market records.

        Args:
            items: iterable of (item_id, item_name) pairs — the FAMA
                catalogue ids 1..46 the caller wants to track. The
                caller decides scope (the sync script filters to the
                dry-goods/egg items relevant to ShelfSenseAI).
            level: 'RUNCIT' (retail) | 'BORONG' | 'LADANG'.
            days: trailing window, 1..90 (server-verified maximum).
            state_slug: optional FAMA state slug (e.g. 'johor') for
                state-grain data; None = national grain.

        Returns:
            list of market-record dicts (possibly empty). Each record:
            {
              'external_id': 'fama-46-runcit',
              'raw_title': 'TELUR AYAM',
              'category': 'FAMA',
              'package_quantity': 1.0,
              'package_unit': 'unit',        # FAMA prices are per unit
              'observations': [
                  {'date': date(2026, 9, 3), 'price': 0.5202,
                   'state': None, 'district': None}, ...
              ]
            }
        """
        if level not in FAMA_LEVELS:
            log.error('Invalid FAMA level %r (must be one of %s)',
                      level, FAMA_LEVELS)
            return []

        records = []
        for item_id, item_name in items:
            arguments = {'item_id': int(item_id), 'level': level,
                         'days': int(days)}
            # State grain requires the lowercase FAMA state slug.
            if state_slug:
                arguments['grain'] = 'state'
                arguments['state_slug'] = state_slug

            payload = self.call_tool('fama_price_history', arguments)
            # Transport failure / malformed payload -> skip this item,
            # keep syncing the rest (never crash the whole run).
            # isinstance check guards against non-dict payloads (e.g. a
            # proxy returning a plain string error page).
            if not isinstance(payload, dict):
                log.warning('No payload for FAMA item %s (%s)',
                            item_id, item_name)
                continue
            # Business-level "no data for this window" envelope.
            if payload.get('status') != 'ok':
                log.info('FAMA item %s: status=%s reason=%s',
                         item_id, payload.get('status'),
                         payload.get('reason'))
                continue

            series = payload.get('series') or []
            observations = []
            for point in series:
                # Defensive per-point parsing: one bad row must not
                # discard the whole item's series.
                obs = _parse_observation_point(point)
                if obs is not None:
                    observations.append(obs)

            if not observations:
                log.info('FAMA item %s: series empty after validation',
                         item_id)
                continue

            # Geographic attribution: state-grain responses carry the
            # requested slug; national grain carries none (falls back
            # to national tier in market_analysis, matching Phase 4).
            state = (state_slug.title().replace('-', ' ')
                     if state_slug else None)

            records.append({
                'external_id': f'fama-{item_id}-{level.lower()}',
                'raw_title': (item_name or
                              payload.get('item_name') or
                              f'FAMA item {item_id}'),
                'category': 'FAMA',
                # FAMA prices are quoted per unit of the item's own
                # unit (Biji / Kilogram). We keep the server-provided
                # unit string normalized by the ingestion layer.
                'package_quantity': 1.0,
                'package_unit': payload.get('unit') or 'unit',
                'observations': observations,
                'observation_state': state,
            })
        return records


# -------------------------------------------------------------
# Pure parsing helpers (unit-testable without any MCP connection)
# -------------------------------------------------------------
def _parse_json_object(text):
    """Parse a JSON object out of an MCP text content block.

    Returns a dict, or None when the payload is empty, malformed, or
    not a JSON object (e.g. an HTML error page from a proxy)."""
    import json
    if not text or not isinstance(text, str):
        return None
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        log.warning('ManaMurah returned non-JSON payload (%.80r)', text)
        return None
    return data if isinstance(data, dict) else None


def _parse_observation_point(point):
    """Validate one {'date': 'YYYY-MM-DD', 'harga': float} series point.

    Returns {'date': date, 'price': float} or None when invalid.
    Validation rules (documented in the implementation report):
      - date parses as an ISO date
      - price is numeric and strictly positive
    """
    if not isinstance(point, dict):
        return None
    raw_date = point.get('date')
    price = point.get('harga')
    # Parse the date first; a bad date invalidates the row entirely.
    if isinstance(raw_date, str):
        try:
            parsed = datetime.strptime(raw_date, '%Y-%m-%d').date()
        except ValueError:
            return None
    elif isinstance(raw_date, date):
        parsed = raw_date
    else:
        return None
    # Price must be a real number > 0 (zero/negative prices are data
    # errors, not real promotions).
    if not isinstance(price, (int, float)) or isinstance(price, bool):
        return None
    if price <= 0:
        return None
    return {'date': parsed, 'price': float(price)}
