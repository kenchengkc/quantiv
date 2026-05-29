"""Single source of truth for ticker lifecycle events: delistings and renames.

Two config files drive this:

  * ``config/delisted_tickers.json`` — companies that stopped trading entirely
    (M&A, bankruptcy, going-private). Deliberately dropped from
    ``data/earnings_calendar.csv`` and the frontend universe.
  * ``config/ticker_renames.json`` — same company, new symbol (rebrand or
    exchange change), e.g. BK -> BNY. The old symbol is rewritten to the new
    one so earnings history carries over.

Consumers:
  * sync_dolthub.py filters delisted tickers and rewrites renamed ones before
    writing the canonical CSV.
  * The daily-refresh integrity gates treat a delisted or renamed-away symbol's
    disappearance as expected rather than a silent-drop regression.

The loaders never raise: a missing or malformed file yields an empty result so
a bad edit can't break the nightly pipeline.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DELISTED_PATH = REPO_ROOT / "config" / "delisted_tickers.json"
RENAMES_PATH = REPO_ROOT / "config" / "ticker_renames.json"


def _normalize(value: object) -> str | None:
    s = str(value or "").strip().upper()
    return s or None


@lru_cache(maxsize=1)
def delisted_tickers() -> frozenset[str]:
    """Uppercased set of delisted tickers from config/delisted_tickers.json."""
    try:
        payload = json.loads(DELISTED_PATH.read_text())
    except (OSError, ValueError):
        return frozenset()
    table = payload.get("tickers") if isinstance(payload, dict) else None
    if not isinstance(table, dict):
        return frozenset()
    return frozenset(sym for key in table if (sym := _normalize(key)))


def is_delisted(value: object) -> bool:
    """True iff ``value`` names a ticker in the delisting list."""
    sym = _normalize(value)
    return sym is not None and sym in delisted_tickers()


@lru_cache(maxsize=1)
def ticker_renames() -> dict[str, str]:
    """Mapping of OLD -> NEW symbol from config/ticker_renames.json (uppercased)."""
    try:
        payload = json.loads(RENAMES_PATH.read_text())
    except (OSError, ValueError):
        return {}
    table = payload.get("renames") if isinstance(payload, dict) else None
    if not isinstance(table, dict):
        return {}
    out: dict[str, str] = {}
    for old, spec in table.items():
        old_sym = _normalize(old)
        new_sym = _normalize(spec.get("new")) if isinstance(spec, dict) else None
        if old_sym and new_sym and old_sym != new_sym:
            out[old_sym] = new_sym
    return out


def canonical_ticker(value: object) -> str:
    """Return the current symbol for ``value``, applying any rename (one hop)."""
    sym = _normalize(value) or ""
    return ticker_renames().get(sym, sym)


def is_renamed_from(value: object) -> bool:
    """True iff ``value`` is an OLD symbol that has been renamed to a new one."""
    sym = _normalize(value)
    return sym is not None and sym in ticker_renames()


def is_retired(value: object) -> bool:
    """True iff ``value`` is gone from the live universe — delisted OR renamed
    away. Used by the integrity gates: either way the old symbol disappearing
    is expected, not a silent-drop regression."""
    return is_delisted(value) or is_renamed_from(value)
