"""Attach canonical display forecasts to frontend research payloads."""

from __future__ import annotations

import math
from collections import Counter
from datetime import date
from typing import Any

import duckdb

from fiscal_calendar import (
    load_fiscal_year_ends,
    load_fiscal_year_naming,
    reporting_fiscal_period,
    reporting_quarter_label,
)

from .display_forecast import DisplayForecastError, resolve_display_forecast
from .shared import jsonable


DISPLAY_METHODS = {
    "ml",
    "options_math",
    "options_indicative",
    "historical",
    "historical_prior",
}
ML_STATUSES = {
    "available",
    "unavailable_inputs",
    "unavailable_model",
    "unavailable_event",
}
OPTIONS_STATUSES = {"decision_eligible", "indicative", "unavailable"}
FALLBACK_REASONS = {
    None,
    "quote_quality",
    "no_same_strike_pair",
    "no_event_expiry",
    "insufficient_ticker_history",
}

_FY_NAMING = load_fiscal_year_naming()
_FYE_MONTHS = load_fiscal_year_ends()


def _finite_positive(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _strict_options_from_event(event: dict[str, Any]) -> dict[str, Any] | None:
    iv_pct = _finite_positive(event.get("em_iv_pct"))
    straddle_pct = _finite_positive(event.get("em_straddle_pct"))
    if iv_pct is None and straddle_pct is None:
        return None
    return {
        "em_baseline_iv": iv_pct,
        "em_baseline_straddle": straddle_pct,
        "expiry_date": event.get("expiry_date"),
        "dte": event.get("days_to_expiry"),
        "atm_iv": event.get("atm_iv"),
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
        ml_forecast={
            "em_ml_pct": event.get("em_ml_pct"),
            "ml_snapshot_date": event.get("ml_snapshot_date"),
            "ml_status": event.get("ml_status"),
        },
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


def _event_identity_map(
    week_payloads: dict[date, dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for payload in week_payloads.values():
        for event in payload.get("events") or []:
            ticker = str(event.get("ticker") or "").upper()
            earnings_iso = str(event.get("earnings_date") or "")[:10]
            if ticker and earnings_iso:
                out[(ticker, earnings_iso)] = event
    return out


def _validate_display_provenance(
    event: dict[str, Any],
    *,
    identity: str,
    errors: list[str],
) -> None:
    pct = _finite_positive(event.get("display_forecast_pct"))
    method = event.get("display_forecast_method")
    ml_status = event.get("ml_status")
    options_status = event.get("options_status")
    fallback_reason = event.get("fallback_reason")
    as_of = event.get("display_forecast_as_of")

    if pct is None:
        errors.append(f"{identity}: invalid display_forecast_pct")
    if method not in DISPLAY_METHODS:
        errors.append(f"{identity}: invalid display_forecast_method={method!r}")
        return
    if ml_status not in ML_STATUSES:
        errors.append(f"{identity}: invalid ml_status={ml_status!r}")
    if options_status not in OPTIONS_STATUSES:
        errors.append(f"{identity}: invalid options_status={options_status!r}")
    if fallback_reason not in FALLBACK_REASONS:
        errors.append(f"{identity}: invalid fallback_reason={fallback_reason!r}")
    if not isinstance(as_of, str) or not as_of:
        errors.append(f"{identity}: missing display_forecast_as_of")

    if method == "ml":
        if ml_status != "available":
            errors.append(f"{identity}: ML method without available ML status")
        if options_status not in {"decision_eligible", "indicative", "unavailable"}:
            errors.append(
                f"{identity}: ML method has incoherent options status={options_status!r}"
            )
        if fallback_reason is not None:
            errors.append(f"{identity}: ML method must not carry a fallback reason")
        return

    if method in {"historical", "historical_prior"} and ml_status == "available":
        errors.append(f"{identity}: historical fallback with available ML status")

    if method == "options_math":
        if options_status != "decision_eligible":
            errors.append(
                f"{identity}: strict options method without decision-eligible options"
            )
        if fallback_reason is not None:
            errors.append(
                f"{identity}: strict options method must not carry a fallback reason"
            )
    elif method == "options_indicative":
        if options_status != "indicative":
            errors.append(
                f"{identity}: indicative options method without indicative options status"
            )
        if fallback_reason not in {"quote_quality", "no_same_strike_pair"}:
            errors.append(
                f"{identity}: indicative options method has incoherent "
                f"fallback_reason={fallback_reason!r}"
            )
    elif method == "historical":
        if options_status != "unavailable":
            errors.append(
                f"{identity}: historical method must have unavailable options status"
            )
        count = event.get("historical_event_count")
        if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
            errors.append(
                f"{identity}: historical method requires positive historical_event_count"
            )
        if fallback_reason not in {
            "quote_quality",
            "no_same_strike_pair",
            "no_event_expiry",
        }:
            errors.append(
                f"{identity}: historical method has incoherent "
                f"fallback_reason={fallback_reason!r}"
            )
    elif method == "historical_prior":
        if options_status != "unavailable":
            errors.append(
                f"{identity}: historical prior must have unavailable options status"
            )
        count = event.get("historical_event_count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            errors.append(
                f"{identity}: historical prior requires nonnegative historical_event_count"
            )
        if fallback_reason != "insufficient_ticker_history":
            errors.append(
                f"{identity}: historical prior must identify insufficient_ticker_history"
            )


def validate_upcoming_display_forecasts(
    published_events: list[tuple[str, date, str]],
    week_payloads: dict[date, dict[str, Any]],
    *,
    today: date,
) -> None:
    """Fail publication when an upcoming published row would render a dash."""

    rows = _event_identity_map(week_payloads)
    windows = [
        (
            date.fromisoformat(payload["window"]["start"]),
            date.fromisoformat(payload["window"]["end"]),
        )
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
        identity = f"{ticker} {earnings_date}"
        event = rows.get((ticker.upper(), earnings_date.isoformat()))
        if event is None:
            errors.append(f"{identity}: missing week row")
            continue
        _validate_display_provenance(event, identity=identity, errors=errors)
    if errors:
        raise DisplayForecastError(
            "display forecast invariant failed:\n  " + "\n  ".join(errors)
        )


def build_display_forecast_status(
    published_events: list[tuple[str, date, str]],
    week_payloads: dict[date, dict[str, Any]],
    *,
    today: date,
    generated_at: str,
) -> dict[str, Any]:
    rows = _event_identity_map(week_payloads)
    windows = [
        (
            date.fromisoformat(payload["window"]["start"]),
            date.fromisoformat(payload["window"]["end"]),
        )
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
    covered = sum(
        _finite_positive(event.get("display_forecast_pct")) is not None
        for event in selected
    )
    expected = len(selected)
    return {
        "schema": "quantiv.display-forecast-status.v1",
        "generated_at": generated_at,
        "published_upcoming_events": expected,
        "with_display_forecast": covered,
        "coverage_pct": covered / expected if expected else 1.0,
        "method_mix": {
            method: int(mix.get(method, 0)) for method in sorted(DISPLAY_METHODS)
        },
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


def _symbol_history(
    conn: duckdb.DuckDBPyConnection,
    ticker: str,
) -> list[dict[str, Any]]:
    try:
        rows = conn.execute(
            """
            SELECT earnings_dt, timing, fiscal_year, fiscal_q,
                   eps_actual, eps_estimate, revenue_actual, revenue_estimate, source
            FROM earnings_events
            WHERE ticker = ?
            ORDER BY earnings_dt DESC
            LIMIT 20
            """,
            [ticker],
        ).fetchall()
    except duckdb.Error:
        return []

    best: dict[tuple[int, str], tuple[tuple[int, date], dict[str, Any]]] = {}
    for (
        earnings_dt,
        timing,
        _fiscal_year,
        _fiscal_q,
        eps_actual,
        eps_estimate,
        revenue_actual,
        revenue_estimate,
        source,
    ) in rows:
        fiscal_year, fiscal_q = reporting_fiscal_period(
            ticker,
            earnings_dt,
            fiscal_year_ends=_FYE_MONTHS,
            naming=_FY_NAMING,
        )
        item = {
            "date": earnings_dt.isoformat(),
            "timing": timing or "unknown",
            "q": reporting_quarter_label(
                ticker,
                earnings_dt,
                fiscal_year_ends=_FYE_MONTHS,
                naming=_FY_NAMING,
            ),
            "fiscal_year": fiscal_year,
            "fiscal_q": fiscal_q,
            "actual": None,
            "implied": None,
            "eps_actual": jsonable(eps_actual),
            "eps_estimate": jsonable(eps_estimate),
            "revenue_actual": jsonable(revenue_actual),
            "revenue_estimate": jsonable(revenue_estimate),
        }
        key = (fiscal_year, fiscal_q)
        source_priority = 1 if source and "finnhub" in str(source).lower() else 0
        score = (source_priority, earnings_dt)
        current = best.get(key)
        if current is None or score > current[0]:
            best[key] = (score, item)

    history = [item[1] for item in best.values()]
    history.sort(key=lambda item: item["date"], reverse=True)
    return history[:12]


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
