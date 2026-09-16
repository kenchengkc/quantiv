"""Attach canonical display forecasts to frontend research payloads."""

from __future__ import annotations

import math
from collections import Counter
from datetime import date
from typing import Any

import duckdb

from .display_forecast import DisplayForecastError, resolve_display_forecast
from .shared import jsonable


DISPLAY_METHODS = {
    "ml",
    "options_math",
    "options_indicative",
    "historical",
    "historical_prior",
}


def _finite_positive(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _strict_options_from_event(event: dict[str, Any]) -> dict[str, Any] | None:
    pct = _finite_positive(event.get("em_straddle_pct"))
    if pct is None:
        return None
    return {
        "em_baseline_straddle": pct,
        "expiry_date": event.get("expiry_date"),
        "atm_strike": event.get("atm_strike"),
        "straddle_price": event.get("em_straddle_abs"),
    }


def enrich_upcoming_event(
    conn: duckdb.DuckDBPyConnection,
    event: dict[str, Any],
    *,
    as_of_date: date,
    today: date,
) -> dict[str, Any]:
    """Attach display provenance to one upcoming event in-place."""

    earnings_iso = str(event.get("earnings_date") or "")[:10]
    if not earnings_iso:
        return event
    earnings_date = date.fromisoformat(earnings_iso)
    if earnings_date < today:
        # Reported rows must preserve the pre-event display forecast carried
        # from the prior bundle; never recompute them with post-event evidence.
        return event

    result = resolve_display_forecast(
        conn,
        ticker=str(event.get("ticker") or "").upper(),
        earnings_date=earnings_date,
        timing=event.get("timing"),
        as_of_date=as_of_date,
        ml_forecast={"em_ml_pct": event.get("em_ml_pct")},
        strict_options=_strict_options_from_event(event),
    )
    event.update(result.public_fields())
    return event


def enrich_upcoming_events(
    conn: duckdb.DuckDBPyConnection,
    events: list[dict[str, Any]],
    *,
    as_of_date: date,
    today: date,
) -> list[dict[str, Any]]:
    for event in events:
        enrich_upcoming_event(conn, event, as_of_date=as_of_date, today=today)
    return events


def _event_identity_map(week_payloads: dict[date, dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for payload in week_payloads.values():
        for event in payload.get("events") or []:
            ticker = str(event.get("ticker") or "").upper()
            earnings_iso = str(event.get("earnings_date") or "")[:10]
            if ticker and earnings_iso:
                out[(ticker, earnings_iso)] = event
    return out


def validate_upcoming_display_forecasts(
    published_events: list[tuple[str, date, str]],
    week_payloads: dict[date, dict[str, Any]],
    *,
    today: date,
) -> None:
    """Fail publication when an upcoming published row would render a dash."""

    rows = _event_identity_map(week_payloads)
    windows = [
        (date.fromisoformat(payload["window"]["start"]), date.fromisoformat(payload["window"]["end"]))
        for payload in week_payloads.values()
        if isinstance(payload.get("window"), dict)
        and payload["window"].get("start")
        and payload["window"].get("end")
    ]
    errors: list[str] = []
    for ticker, earnings_date, _timing in published_events:
        if earnings_date < today:
            continue
        if windows and not any(start <= earnings_date <= end for start, end in windows):
            continue
        event = rows.get((ticker.upper(), earnings_date.isoformat()))
        if event is None:
            errors.append(f"{ticker} {earnings_date}: missing week row")
            continue
        pct = _finite_positive(event.get("display_forecast_pct"))
        method = event.get("display_forecast_method")
        if pct is None:
            errors.append(f"{ticker} {earnings_date}: invalid display_forecast_pct")
        if method not in DISPLAY_METHODS:
            errors.append(f"{ticker} {earnings_date}: invalid display_forecast_method={method!r}")
        if method == "ml" and event.get("ml_status") != "available":
            errors.append(f"{ticker} {earnings_date}: ML method without available ML status")
        if method in {"options_math", "options_indicative", "historical", "historical_prior"} and event.get("ml_status") == "available":
            errors.append(f"{ticker} {earnings_date}: fallback method with available ML status")
    if errors:
        raise DisplayForecastError("display forecast invariant failed:\n  " + "\n  ".join(errors))


def build_display_forecast_status(
    published_events: list[tuple[str, date, str]],
    week_payloads: dict[date, dict[str, Any]],
    *,
    today: date,
    generated_at: str,
) -> dict[str, Any]:
    rows = _event_identity_map(week_payloads)
    windows = [
        (date.fromisoformat(payload["window"]["start"]), date.fromisoformat(payload["window"]["end"]))
        for payload in week_payloads.values()
        if isinstance(payload.get("window"), dict)
        and payload["window"].get("start")
        and payload["window"].get("end")
    ]
    selected: list[dict[str, Any]] = []
    for ticker, earnings_date, _timing in published_events:
        if earnings_date < today:
            continue
        if windows and not any(start <= earnings_date <= end for start, end in windows):
            continue
        event = rows.get((ticker.upper(), earnings_date.isoformat()))
        if event is not None:
            selected.append(event)
    mix = Counter(str(event.get("display_forecast_method")) for event in selected)
    covered = sum(_finite_positive(event.get("display_forecast_pct")) is not None for event in selected)
    expected = len(selected)
    return {
        "schema": "quantiv.display-forecast-status.v1",
        "generated_at": generated_at,
        "published_upcoming_events": expected,
        "with_display_forecast": covered,
        "coverage_pct": covered / expected if expected else 1.0,
        "method_mix": {method: int(mix.get(method, 0)) for method in sorted(DISPLAY_METHODS)},
    }


def display_fields_from_event(event: dict[str, Any] | None) -> dict[str, Any]:
    if not event:
        return {}
    return {
        key: event.get(key)
        for key in (
            "display_forecast_pct",
            "display_forecast_method",
            "display_forecast_as_of",
            "ml_status",
            "options_status",
            "fallback_reason",
            "historical_event_count",
        )
        if key in event
    }


def _symbol_history(conn: duckdb.DuckDBPyConnection, ticker: str) -> list[dict[str, Any]]:
    try:
        rows = conn.execute(
            """
            SELECT earnings_dt, timing, fiscal_year, fiscal_q,
                   eps_actual, eps_estimate, revenue_actual, revenue_estimate
            FROM earnings_events
            WHERE ticker = ?
            ORDER BY earnings_dt DESC
            LIMIT 12
            """,
            [ticker],
        ).fetchall()
    except duckdb.Error:
        return []
    return [
        {
            "date": earnings_dt.isoformat(),
            "timing": timing or "unknown",
            "fiscal_year": int(fiscal_year) if fiscal_year is not None else None,
            "fiscal_q": fiscal_q,
            "actual": None,
            "implied": None,
            "eps_actual": jsonable(eps_actual),
            "eps_estimate": jsonable(eps_estimate),
            "revenue_actual": jsonable(revenue_actual),
            "revenue_estimate": jsonable(revenue_estimate),
        }
        for earnings_dt, timing, fiscal_year, fiscal_q, eps_actual, eps_estimate, revenue_actual, revenue_estimate in rows
    ]


def ensure_symbol_display_detail(
    conn: duckdb.DuckDBPyConnection,
    detail: dict[str, Any] | None,
    *,
    ticker: str,
    as_of_date: date,
    earnings_date: date | None,
    display_event: dict[str, Any] | None,
    provider_fields: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Keep symbol pages buildable when strict options evidence is absent."""

    fields = display_fields_from_event(display_event)
    if detail is not None:
        if earnings_date and fields:
            expected_move = detail.get("expected_move") or {
                "earnings_date": earnings_date.isoformat(),
            }
            expected_move.update(fields)
            detail["expected_move"] = expected_move
        return detail

    if earnings_date is None or not fields:
        return detail

    spot = None
    try:
        row = conn.execute(
            """
            SELECT close FROM v_ohlcv
            WHERE act_symbol = ? AND date <= ? AND close > 0
            ORDER BY date DESC LIMIT 1
            """,
            [ticker, as_of_date],
        ).fetchone()
        spot = jsonable(row[0]) if row else None
    except duckdb.Error:
        pass

    expected_move = {
        "earnings_date": earnings_date.isoformat(),
        **fields,
        "expiration": None,
        "dte": None,
        "atm_strike": None,
        "atm_iv": None,
        "straddle_abs": None,
        "straddle_pct": None,
        "iv_pct": None,
        "em_ml_pct": None,
        "em_ml_abs": None,
        "model_horizon": None,
        "ml_snapshot_date": None,
        "p10": None,
        "p25": None,
        "p50": None,
        "p75": None,
        "p90": None,
    }
    return {
        "symbol": ticker,
        "as_of_date": as_of_date.isoformat(),
        "spot_price": spot,
        "expected_move": expected_move,
        "straddle_features": [],
        "earnings_history": _symbol_history(conn, ticker),
        "next_earnings": earnings_date.isoformat(),
        "vol_regime": None,
        **(provider_fields or {}),
    }
