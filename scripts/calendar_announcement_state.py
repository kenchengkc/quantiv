"""Rotate bounded announcement lookups using persisted attempt metadata only.

This state influences retrieval order, never earnings dates, sessions, or model
admission. Confirmed announcement evidence remains in the canonical calendar.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping, Sequence
import warnings


SCHEMA = "quantiv.calendar-announcement-state.v1"
RETENTION_DAYS = 120
PROVIDERS = {
    "finnhub_company_news", "twelvedata_press_releases", "alphavantage_news",
    "public_press_releases",
}
STATUSES = {"ok", "partial", "error", "missing_key", "empty", "blocked", "rate_limited"}
State = dict[str, dict[str, str]]


def _key(provider: str, symbol: str, event_date: str | date) -> str:
    symbol = str(symbol).strip().upper()
    day = str(event_date)
    if provider not in PROVIDERS or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", symbol):
        raise ValueError("invalid announcement provider or symbol")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise ValueError("announcement event requires an exact date")
    date.fromisoformat(day)
    return f"{provider}|{symbol}|{day}"


def _entry(observed_at: str | datetime, status: str) -> dict[str, str]:
    observed = (datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
                if isinstance(observed_at, str) else observed_at)
    if not isinstance(observed, datetime) or observed.utcoffset() is None:
        raise ValueError("announcement attempt requires a timezone-aware timestamp")
    if status not in STATUSES:
        raise ValueError("invalid announcement attempt status")
    return {"observed_at": observed.astimezone(timezone.utc).isoformat(), "status": status}


def _sanitize(attempts: Mapping[str, Any], *, today: date) -> State:
    clean: State = {}
    invalid = 0
    for key, value in attempts.items():
        try:
            provider, symbol, event_date = key.split("|")
            normalized_key = _key(provider, symbol, event_date)
            entry = _entry(value["observed_at"], value["status"])
            observed_day = datetime.fromisoformat(entry["observed_at"]).date()
            # A UTC attempt may fall on the following date of the market's
            # local calendar. More distant future timestamps are invalid.
            if observed_day > today + timedelta(days=1):
                raise ValueError("future announcement attempt")
            if not today <= date.fromisoformat(event_date) <= today + timedelta(days=RETENTION_DAYS):
                continue
            if observed_day < today - timedelta(days=RETENTION_DAYS):
                continue
            clean[normalized_key] = entry
        except (AttributeError, KeyError, TypeError, ValueError):
            invalid += 1
    if invalid:
        warnings.warn(f"Discarded {invalid} invalid announcement attempt records", RuntimeWarning)
    return clean


def load_state(path: Path, *, today: date) -> State:
    """Return sanitized attempts; missing/corrupt state leaves lookups eligible."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if (not isinstance(payload, dict) or payload.get("schema") != SCHEMA
                or not isinstance(payload.get("attempts"), dict)):
            raise ValueError("invalid announcement-state schema")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError, ValueError):
        warnings.warn("Unable to load announcement attempt state; starting with fresh lookup order", RuntimeWarning)
        return {}
    return _sanitize(payload["attempts"], today=today)


def update_state(
    state: Mapping[str, dict[str, str]], *, provider: str, symbol: str,
    event_date: str | date, observed_at: str | datetime, status: str,
) -> State:
    """Return updated attempt metadata without mutating the supplied mapping."""
    return {**state, _key(provider, symbol, event_date): _entry(observed_at, status)}


def prioritize_symbols(
    symbols: Sequence[str], *, state: Mapping[str, dict[str, str]], provider: str,
    candidate_dates: Mapping[str, str] | None = None,
) -> list[str]:
    """Try unvisited exact-date events first, then oldest attempts; retain ties."""
    def last_attempt(symbol: str) -> str:
        try:
            key = _key(provider, symbol, (candidate_dates or {}).get(symbol, ""))
            attempt = state[key]
            return _entry(attempt["observed_at"], attempt["status"])["observed_at"]
        except (KeyError, TypeError, ValueError):
            return ""

    # Python's stable sort retains the caller's event/risk rank for equal ages.
    return sorted(symbols, key=last_attempt)


def write_state(path: Path, state: Mapping[str, Any], *, today: date) -> None:
    """Atomically persist only recent attempts for current/future events."""
    path = Path(path)
    payload = {"schema": SCHEMA, "attempts": _sanitize(state, today=today)}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
