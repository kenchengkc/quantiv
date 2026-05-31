#!/usr/bin/env python3
"""TwelveData Basic-tier helpers.

Used only for backend data enrichment. The public app should receive derived
fields (for example realized_move_pct), never raw TwelveData market data.
"""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable


TWELVEDATA_TIME_SERIES_URL = "https://api.twelvedata.com/time_series"


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


@dataclass(frozen=True)
class TwelveDataConfig:
    api_key: str | None
    daily_credit_limit: int
    batch_size: int
    batch_delay_sec: float
    ledger_path: Path
    realized_fallback_enabled: bool


@dataclass
class TwelveDataFetchResult:
    closes: dict[str, list[tuple[date, float]]] = field(default_factory=dict)
    requested_symbols: list[str] = field(default_factory=list)
    used_credits: int = 0
    provider_credits_used: int | None = None
    provider_credits_left: int | None = None
    skipped_symbols: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    try:
        return max(minimum, float(os.getenv(name, str(default))))
    except ValueError:
        return default


def twelvedata_api_key() -> str | None:
    return (
        os.getenv("TWELVEDATA_API_KEY")
        or os.getenv("TWELVE_DATA_API_KEY")
        or os.getenv("TWELVEDATA_KEY")
    )


def load_twelvedata_config(data_dir: Path) -> TwelveDataConfig:
    ledger_raw = os.getenv("TWELVEDATA_LEDGER_PATH")
    ledger_path = Path(ledger_raw) if ledger_raw else data_dir / "twelvedata_usage_ledger.json"
    return TwelveDataConfig(
        api_key=twelvedata_api_key(),
        daily_credit_limit=_env_int("TWELVEDATA_DAILY_CREDIT_LIMIT", 792, minimum=0),
        batch_size=_env_int("TWELVEDATA_BATCH_SIZE", 8, minimum=1),
        batch_delay_sec=_env_float("TWELVEDATA_BATCH_DELAY_SEC", 61.0, minimum=0.0),
        ledger_path=ledger_path,
        realized_fallback_enabled=_env_bool("TWELVEDATA_REALIZED_FALLBACK", True),
    )


class TwelveDataUsageLedger:
    """Conservative local credit ledger for Basic-tier daily quota.

    Credits are reserved before the HTTP request is attempted. That means a
    network failure can slightly under-use the allowance, but retries cannot
    accidentally exceed the daily cap.
    """

    def __init__(
        self,
        path: Path,
        daily_credit_limit: int,
        *,
        today_fn: Callable[[], date] = _utc_today,
        now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self.path = path
        self.daily_credit_limit = max(0, daily_credit_limit)
        self.today_fn = today_fn
        self.now_fn = now_fn

    def _today_iso(self) -> str:
        return self.today_fn().isoformat()

    def _fresh_state(self) -> dict[str, Any]:
        return {"date": self._today_iso(), "used": 0, "batches": []}

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._fresh_state()
        try:
            state = json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError):
            return self._fresh_state()
        if state.get("date") != self._today_iso():
            return self._fresh_state()
        if not isinstance(state.get("used"), int):
            state["used"] = 0
        if not isinstance(state.get("batches"), list):
            state["batches"] = []
        return state

    def write(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(f"{self.path.suffix}.tmp")
        tmp.write_text(json.dumps(state, indent=2))
        tmp.replace(self.path)

    def used(self) -> int:
        return int(self.read().get("used", 0))

    def remaining(self) -> int:
        return max(0, self.daily_credit_limit - self.used())

    def reserve(self, symbols: list[str], *, purpose: str) -> int:
        credits = len(symbols)
        if credits <= 0:
            return 0
        state = self.read()
        used = int(state.get("used", 0))
        if used + credits > self.daily_credit_limit:
            raise RuntimeError(
                f"TwelveData quota exceeded: requested {credits}, "
                f"remaining {self.daily_credit_limit - used}"
            )
        state["used"] = used + credits
        state.setdefault("batches", []).append({
            "at": self.now_fn().isoformat(),
            "purpose": purpose,
            "credits": credits,
            "symbols": symbols,
        })
        self.write(state)
        return credits


def unique_symbols(symbols: list[str]) -> list[str]:
    return sorted({s.strip().upper() for s in symbols if s and s.strip()})


def plan_credit_use(
    symbols: list[str],
    config: TwelveDataConfig,
    *,
    ledger: TwelveDataUsageLedger | None = None,
) -> dict[str, Any]:
    clean = unique_symbols(symbols)
    ledger = ledger or TwelveDataUsageLedger(config.ledger_path, config.daily_credit_limit)
    remaining = ledger.remaining()
    planned = clean[:remaining]
    return {
        "requested_symbols": clean,
        "needed_credits": len(clean),
        "remaining_credits": remaining,
        "planned_symbols": planned,
        "planned_credits": len(planned),
        "skipped_symbols": clean[len(planned):],
    }


def _float_or_none(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_daily_closes_payload(
    payload: Any,
    requested_symbols: list[str],
) -> tuple[dict[str, list[tuple[date, float]]], list[str]]:
    errors: list[str] = []
    clean_symbols = unique_symbols(requested_symbols)

    if isinstance(payload, dict) and payload.get("status") == "error":
        return {}, [str(payload.get("message") or "TwelveData error")]

    if isinstance(payload, dict) and "values" in payload and len(clean_symbols) == 1:
        by_symbol = {clean_symbols[0]: payload}
    elif isinstance(payload, dict):
        by_symbol = {sym: payload.get(sym) for sym in clean_symbols}
    else:
        return {}, ["unexpected TwelveData response shape"]

    out: dict[str, list[tuple[date, float]]] = {}
    for sym, item in by_symbol.items():
        if not isinstance(item, dict):
            errors.append(f"{sym}: missing response")
            continue
        if item.get("status") == "error":
            errors.append(f"{sym}: {item.get('message') or 'TwelveData error'}")
            continue
        rows: list[tuple[date, float]] = []
        for raw in item.get("values") or []:
            if not isinstance(raw, dict):
                continue
            try:
                dt = date.fromisoformat(str(raw.get("datetime") or "")[:10])
            except ValueError:
                continue
            close = _float_or_none(raw.get("close"))
            if close is not None:
                rows.append((dt, close))
        if rows:
            rows.sort(key=lambda r: r[0])
            out[sym] = rows
        else:
            errors.append(f"{sym}: no usable daily closes")
    return out, errors


def _chunks(items: list[str], size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def fetch_daily_closes(
    symbols: list[str],
    start: date,
    end: date,
    config: TwelveDataConfig,
    *,
    purpose: str = "realized_fallback",
    ledger: TwelveDataUsageLedger | None = None,
    http_get: Callable[..., Any] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> TwelveDataFetchResult:
    result = TwelveDataFetchResult()
    if not config.realized_fallback_enabled:
        result.errors.append("TwelveData realized fallback disabled")
        return result
    if not config.api_key:
        result.errors.append("TWELVEDATA_API_KEY missing")
        return result

    ledger = ledger or TwelveDataUsageLedger(config.ledger_path, config.daily_credit_limit)
    clean_symbols = unique_symbols(symbols)
    http_get = http_get or _requests_get
    requested_count = 0

    for batch_index, batch in enumerate(_chunks(clean_symbols, config.batch_size), 1):
        remaining = ledger.remaining()
        if remaining <= 0:
            result.skipped_symbols.extend(clean_symbols[requested_count:])
            break

        effective_batch = batch[:remaining]
        if not effective_batch:
            result.skipped_symbols.extend(clean_symbols[requested_count:])
            break

        ledger.reserve(effective_batch, purpose=purpose)
        result.used_credits += len(effective_batch)
        result.requested_symbols.extend(effective_batch)
        requested_count += len(effective_batch)
        partial_quota_batch = len(effective_batch) < len(batch)

        params = {
            "symbol": ",".join(effective_batch),
            "interval": "1day",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "order": "ASC",
            "adjust": "splits",
            "apikey": config.api_key,
        }
        try:
            response = http_get(TWELVEDATA_TIME_SERIES_URL, params=params, timeout=20)
            response.raise_for_status()
            headers = getattr(response, "headers", {}) or {}
            used_header = _int_or_none(headers.get("api-credits-used"))
            left_header = _int_or_none(headers.get("api-credits-left"))
            if used_header is not None:
                result.provider_credits_used = used_header
            if left_header is not None:
                result.provider_credits_left = left_header
            payload = response.json()
        except Exception as exc:
            result.errors.append(
                f"batch {batch_index}/{math.ceil(len(clean_symbols) / config.batch_size)} failed: {exc}"
            )
            if partial_quota_batch:
                result.skipped_symbols.extend(clean_symbols[requested_count:])
                break
            continue

        closes, errors = parse_daily_closes_payload(payload, effective_batch)
        result.closes.update(closes)
        result.errors.extend(errors)

        if partial_quota_batch:
            result.skipped_symbols.extend(clean_symbols[requested_count:])
            break

        if requested_count < len(clean_symbols) and config.batch_delay_sec > 0:
            sleep_fn(config.batch_delay_sec)

    return result


def _requests_get(*args: Any, **kwargs: Any) -> Any:
    import requests

    return requests.get(*args, **kwargs)
