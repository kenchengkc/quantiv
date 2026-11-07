"""Single source of truth for ticker lifecycle events: delistings and renames.

Two config files drive this:

  * ``config/delisted_tickers.json`` — companies that stopped trading entirely
    (M&A, bankruptcy, going-private). Deliberately dropped from
    ``data/earnings_calendar.csv`` and the frontend universe.
  * ``config/ticker_renames.json`` — same company, new symbol (rebrand or
    exchange change), e.g. BK -> BNY. The old symbol is rewritten to the new
    one so earnings history carries over. Spin-offs explicitly preserve the
    original historical symbol because identity continuity is not price continuity.

Consumers:
  * sync_dolthub.py filters delisted tickers and rewrites renamed ones before
    writing the canonical CSV.
  * The daily-refresh integrity gates treat a delisted or renamed-away symbol's
    disappearance as expected rather than a silent-drop regression.

The legacy identity loaders tolerate missing/malformed files. The economic
history guard validates discontinuity metadata and fails closed on bad edits.
"""
from __future__ import annotations

import json
from datetime import date
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


@lru_cache(maxsize=1)
def ticker_history_cutovers() -> dict[str, tuple[str, str]]:
    """OLD -> (NEW, first new-regime date) for non-comparable price histories.

    These entries are identity transitions, not aliases for historical market
    data. Invalid metadata must not silently turn them into ordinary renames.
    """
    payload = json.loads(RENAMES_PATH.read_text())
    table = payload.get("renames")
    if not isinstance(table, dict):
        raise ValueError("ticker rename configuration requires a renames object")
    out = {}
    for old, spec in table.items():
        if not isinstance(spec, dict):
            raise ValueError(f"invalid ticker transition: {old}")
        policy = spec.get("history_policy")
        continuous = spec.get("price_history_continuous", True)
        if policy is None and continuous is True:
            continue
        if policy != "preserve_pre_transition" or continuous is not False:
            raise ValueError(f"invalid price-history policy for {old}")
        old_sym, new_sym = _normalize(old), _normalize(spec.get("new"))
        cutoff = date.fromisoformat(str(spec.get("renamed_on", ""))).isoformat()
        if not old_sym or not new_sym or old_sym == new_sym:
            raise ValueError(f"invalid discontinuous ticker transition: {old}")
        out[old_sym] = (new_sym, cutoff)
    return out


def preserves_earnings_history(value: object, event_date: object) -> bool:
    """Whether this historical event must retain its original ticker."""
    sym = _normalize(value) or ""
    cutover = ticker_history_cutovers().get(sym)
    if not cutover:
        return False
    observed = date.fromisoformat(str(event_date)[:10]).isoformat()
    return observed < cutover[1]


def canonical_earnings_ticker(value: object, event_date: object) -> str:
    """Preserve pre-spin earnings under their original economic identity."""
    sym = _normalize(value) or ""
    return sym if preserves_earnings_history(sym, event_date) else canonical_ticker(sym)


def canonical_action_ticker(value: object) -> str:
    """Keep historical split/dividend coverage aligned to unspliced prices."""
    sym = _normalize(value) or ""
    return sym if sym in ticker_history_cutovers() else canonical_ticker(sym)


def is_renamed_from(value: object) -> bool:
    """True iff ``value`` is an OLD symbol that has been renamed to a new one."""
    sym = _normalize(value)
    return sym is not None and sym in ticker_renames()


def is_retired(value: object) -> bool:
    """True iff ``value`` is gone from the live universe — delisted OR renamed
    away. Used by the integrity gates: either way the old symbol disappearing
    is expected, not a silent-drop regression."""
    return is_delisted(value) or is_renamed_from(value)
