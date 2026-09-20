"""Resolve one canonical user-facing earnings expected move.

This module is deliberately separate from the strict research option-quality
contract. It may use structurally sane lower-quality option evidence for
presentation, or point-in-time historical fallbacks, but those values never
feed ML training/scoring or decision-eligible options coverage.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Literal

import duckdb

from .shared import DATA_DIR


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY_PATH = REPO_ROOT / "config" / "option_display_quality.json"

DisplayMethod = Literal[
    "ml",
    "options_math",
    "options_indicative",
    "historical",
    "historical_prior",
]
MLStatus = Literal[
    "available",
    "unavailable_inputs",
    "unavailable_model",
    "unavailable_event",
]
OptionsStatus = Literal["decision_eligible", "indicative", "unavailable"]
FallbackReason = Literal[
    "quote_quality",
    "no_same_strike_pair",
    "no_event_expiry",
    "insufficient_ticker_history",
] | None


class DisplayForecastError(RuntimeError):
    """Raised when the presentation hierarchy cannot produce a safe number."""


@dataclass(frozen=True)
class DisplayPolicy:
    max_leg_relative_spread: float
    max_straddle_relative_spread: float
    max_atm_delta_distance: float
    max_post_event_expiry_days: int
    min_ticker_history_events: int
    ticker_history_window_events: int
    universe_prior_window_days: int


@dataclass(frozen=True)
class DisplayForecast:
    pct: float
    method: DisplayMethod
    as_of: str | None
    ml_status: MLStatus
    options_status: OptionsStatus
    fallback_reason: FallbackReason
    historical_event_count: int | None = None
    selected_options_details: dict[str, Any] | None = None

    def public_fields(self) -> dict[str, Any]:
        return {
            "display_forecast_pct": self.pct,
            "display_forecast_method": self.method,
            "display_forecast_as_of": self.as_of,
            "ml_status": self.ml_status,
            "options_status": self.options_status,
            "fallback_reason": self.fallback_reason,
            "historical_event_count": self.historical_event_count,
        }

    def diagnostic_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_display_policy(path: Path | None = None) -> DisplayPolicy:
    payload = json.loads((path or DEFAULT_POLICY_PATH).read_text(encoding="utf-8"))
    if payload.get("schema") != "quantiv.option-display-quality.v1":
        raise DisplayForecastError("unsupported option display-quality policy")
    return DisplayPolicy(
        max_leg_relative_spread=float(payload["max_leg_relative_spread"]),
        max_straddle_relative_spread=float(payload["max_straddle_relative_spread"]),
        max_atm_delta_distance=float(payload["max_atm_delta_distance"]),
        max_post_event_expiry_days=int(payload["max_post_event_expiry_days"]),
        min_ticker_history_events=int(payload["min_ticker_history_events"]),
        ticker_history_window_events=int(payload["ticker_history_window_events"]),
        universe_prior_window_days=int(payload["universe_prior_window_days"]),
    )


def _finite_positive(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _is_after_close(timing: str | None) -> bool:
    normalized = str(timing or "").strip().lower()
    return normalized in {"after_market_close", "amc", "after_close"} or "after" in normalized


def _relation_exists(conn: duckdb.DuckDBPyConnection, name: str) -> bool:
    try:
        conn.execute(f"SELECT 1 FROM {name} LIMIT 0")
        return True
    except duckdb.Error:
        return False


def _raw_options_source(conn: duckdb.DuckDBPyConnection) -> str | None:
    """Return provider-level option rows suitable for display-only estimates."""

    if _relation_exists(conn, "v_options"):
        return "v_options"
    if _relation_exists(conn, "v_options_raw"):
        return "v_options_raw"

    root = DATA_DIR / "parquet" / "options_chain"
    if root.exists() and any(root.glob("year=*/month=*/*.parquet")):
        pattern = str(root / "year=*" / "month=*" / "*.parquet").replace("'", "''")
        return f"read_parquet('{pattern}', hive_partitioning=true, union_by_name=true)"
    return None


def _eod_spot(
    conn: duckdb.DuckDBPyConnection,
    *,
    ticker: str,
    as_of_date: date,
) -> float | None:
    """Return the latest point-in-time EOD close available at the option cutoff."""

    if not _relation_exists(conn, "v_ohlcv"):
        return None
    try:
        row = conn.execute(
            """
            SELECT close
            FROM v_ohlcv
            WHERE UPPER(CAST(act_symbol AS VARCHAR)) = ?
              AND CAST(date AS DATE) <= ?
              AND TRY_CAST(close AS DOUBLE) > 0
              AND isfinite(TRY_CAST(close AS DOUBLE))
            ORDER BY CAST(date AS DATE) DESC
            LIMIT 1
            """,
            [ticker.upper(), as_of_date],
        ).fetchone()
    except duckdb.Error:
        return None
    return _finite_positive(row[0]) if row else None


def _pair_rows(
    conn: duckdb.DuckDBPyConnection,
    *,
    ticker: str,
    as_of_date: date,
    earnings_date: date,
    timing: str | None,
    policy: DisplayPolicy,
) -> tuple[list[dict[str, Any]], FallbackReason]:
    source = _raw_options_source(conn)
    if not source:
        return [], "no_event_expiry"

    expiry_cmp = ">" if _is_after_close(timing) else ">="
    max_expiry = earnings_date + timedelta(days=policy.max_post_event_expiry_days)
    try:
        rows = conn.execute(
            f"""
            WITH raw AS (
                SELECT
                    CAST(date AS DATE) AS as_of_date,
                    UPPER(CAST(act_symbol AS VARCHAR)) AS ticker,
                    CAST(expiration AS DATE) AS expiry_date,
                    TRY_CAST(strike AS DOUBLE) AS strike,
                    CASE
                        WHEN UPPER(CAST(call_put AS VARCHAR)) IN ('C', 'CALL') THEN 'C'
                        WHEN UPPER(CAST(call_put AS VARCHAR)) IN ('P', 'PUT') THEN 'P'
                        ELSE NULL
                    END AS side,
                    TRY_CAST(bid AS DOUBLE) AS bid,
                    TRY_CAST(ask AS DOUBLE) AS ask,
                    TRY_CAST(delta AS DOUBLE) AS delta
                FROM {source}
                WHERE UPPER(CAST(act_symbol AS VARCHAR)) = ?
                  AND CAST(date AS DATE) = ?
            ), structurally_valid AS (
                SELECT *
                FROM raw
                WHERE strike IS NOT NULL AND strike > 0
                  AND side IS NOT NULL
                  AND bid IS NOT NULL AND ask IS NOT NULL
                  AND isfinite(bid) AND isfinite(ask)
                  AND bid >= 0 AND ask >= 0 AND ask >= bid
                  AND (bid + ask) > 0
            ), spanning AS (
                SELECT *
                FROM structurally_valid
                WHERE expiry_date {expiry_cmp} ?
                  AND expiry_date <= ?
            ), calls AS (
                SELECT * FROM spanning WHERE side = 'C'
            ), puts AS (
                SELECT * FROM spanning WHERE side = 'P'
            )
            SELECT
                c.expiry_date,
                c.strike,
                c.bid AS call_bid,
                c.ask AS call_ask,
                c.delta AS call_delta,
                p.bid AS put_bid,
                p.ask AS put_ask,
                p.delta AS put_delta
            FROM calls c
            JOIN puts p USING (as_of_date, ticker, expiry_date, strike)
            ORDER BY c.expiry_date, c.strike
            """,
            [ticker.upper(), as_of_date, earnings_date, max_expiry],
        ).fetchall()

        expiry_exists = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM {source}
            WHERE UPPER(CAST(act_symbol AS VARCHAR)) = ?
              AND CAST(date AS DATE) = ?
              AND CAST(expiration AS DATE) {expiry_cmp} ?
              AND CAST(expiration AS DATE) <= ?
            """,
            [ticker.upper(), as_of_date, earnings_date, max_expiry],
        ).fetchone()[0]
    except duckdb.Error:
        return [], "no_event_expiry"

    if not expiry_exists:
        return [], "no_event_expiry"
    if not rows:
        return [], "no_same_strike_pair"

    pairs: list[dict[str, Any]] = []
    for expiry, strike, call_bid, call_ask, call_delta, put_bid, put_ask, put_delta in rows:
        call_mid = (float(call_bid) + float(call_ask)) / 2.0
        put_mid = (float(put_bid) + float(put_ask)) / 2.0
        straddle_mid = call_mid + put_mid
        if call_mid <= 0 or put_mid <= 0 or straddle_mid <= 0:
            continue
        call_spread = (float(call_ask) - float(call_bid)) / call_mid
        put_spread = (float(put_ask) - float(put_bid)) / put_mid
        straddle_spread = (
            float(call_ask) + float(put_ask) - float(call_bid) - float(put_bid)
        ) / straddle_mid
        valid_call_delta = (
            call_delta is not None
            and math.isfinite(float(call_delta))
            and 0 <= float(call_delta) <= 1
        )
        valid_put_delta = (
            put_delta is not None
            and math.isfinite(float(put_delta))
            and -1 <= float(put_delta) <= 0
        )
        delta_distance = (
            abs(float(call_delta) - 0.5) + abs(float(put_delta) + 0.5)
            if valid_call_delta and valid_put_delta
            else None
        )
        pairs.append(
            {
                "expiry_date": expiry,
                "strike": float(strike),
                "call_bid": float(call_bid),
                "call_ask": float(call_ask),
                "call_mid": call_mid,
                "call_delta": float(call_delta) if valid_call_delta else None,
                "call_relative_spread": call_spread,
                "put_bid": float(put_bid),
                "put_ask": float(put_ask),
                "put_mid": put_mid,
                "put_delta": float(put_delta) if valid_put_delta else None,
                "put_relative_spread": put_spread,
                "straddle_mid": straddle_mid,
                "straddle_relative_spread": straddle_spread,
                "atm_delta_distance": delta_distance,
            }
        )
    return pairs, "quote_quality"


def _select_indicative_pair(
    conn: duckdb.DuckDBPyConnection,
    *,
    ticker: str,
    as_of_date: date,
    earnings_date: date,
    timing: str | None,
    policy: DisplayPolicy,
) -> tuple[dict[str, Any] | None, FallbackReason]:
    pairs, failure_reason = _pair_rows(
        conn,
        ticker=ticker,
        as_of_date=as_of_date,
        earnings_date=earnings_date,
        timing=timing,
        policy=policy,
    )
    if not pairs:
        return None, failure_reason

    actual_spot = _eod_spot(conn, ticker=ticker, as_of_date=as_of_date)
    expiries = sorted({row["expiry_date"] for row in pairs})
    for expiry in expiries:
        expiry_pairs = [row for row in pairs if row["expiry_date"] == expiry]
        delta_spot_candidates = [
            row for row in expiry_pairs if row["call_delta"] is not None
        ]
        if actual_spot is not None:
            estimated_spot = actual_spot
            spot_source = "eod_close"
        elif delta_spot_candidates:
            spot_row = min(
                delta_spot_candidates,
                key=lambda row: (abs(row["call_delta"] - 0.5), row["strike"]),
            )
            estimated_spot = float(spot_row["strike"])
            spot_source = "call_delta_strike_proxy"
        else:
            estimated_spot = float(median(row["strike"] for row in expiry_pairs))
            spot_source = "median_strike_proxy"

        eligible: list[dict[str, Any]] = []
        for row in expiry_pairs:
            atm_metric = row["atm_delta_distance"]
            if atm_metric is None:
                atm_metric = abs(row["strike"] / estimated_spot - 1.0)
            worst_leg = max(row["call_relative_spread"], row["put_relative_spread"])
            if worst_leg > policy.max_leg_relative_spread:
                continue
            if row["straddle_relative_spread"] > policy.max_straddle_relative_spread:
                continue
            if atm_metric > policy.max_atm_delta_distance:
                continue
            enriched = dict(row)
            enriched["estimated_spot"] = estimated_spot
            enriched["spot_source"] = spot_source
            enriched["atm_metric"] = atm_metric
            enriched["worst_leg_relative_spread"] = worst_leg
            eligible.append(enriched)

        if not eligible:
            continue

        selected = min(
            eligible,
            key=lambda row: (
                row["atm_metric"],
                row["straddle_relative_spread"],
                row["worst_leg_relative_spread"],
                abs(row["strike"] / row["estimated_spot"] - 1.0),
                row["strike"],
            ),
        )
        return selected, "quote_quality"

    return None, "quote_quality"


def _valid_leg_quote(
    bid: Any,
    ask: Any,
    max_relative_spread: float,
) -> tuple[bool, float | None]:
    """Validate a quote only when quote fields exist; IV itself can be provider-supplied."""

    if bid is None and ask is None:
        return True, None
    if bid is None or ask is None:
        return False, None
    try:
        bid_value = float(bid)
        ask_value = float(ask)
    except (TypeError, ValueError):
        return False, None
    if not math.isfinite(bid_value) or not math.isfinite(ask_value):
        return False, None
    if bid_value < 0 or ask_value < 0 or ask_value < bid_value:
        return False, None
    midpoint = (bid_value + ask_value) / 2.0
    if midpoint <= 0:
        return False, None
    relative_spread = (ask_value - bid_value) / midpoint
    return relative_spread <= max_relative_spread, relative_spread


def _select_indicative_iv(
    conn: duckdb.DuckDBPyConnection,
    *,
    ticker: str,
    as_of_date: date,
    earnings_date: date,
    timing: str | None,
    policy: DisplayPolicy,
    pair_failure_reason: FallbackReason,
) -> tuple[dict[str, Any] | None, FallbackReason]:
    """Build an IV expected move without requiring a same-strike call/put pair."""

    source = _raw_options_source(conn)
    if not source or pair_failure_reason == "no_event_expiry":
        return None, pair_failure_reason or "no_event_expiry"

    expiry_cmp = ">" if _is_after_close(timing) else ">="
    max_expiry = earnings_date + timedelta(days=policy.max_post_event_expiry_days)
    try:
        rows = conn.execute(
            f"""
            SELECT
                CAST(expiration AS DATE) AS expiry_date,
                TRY_CAST(strike AS DOUBLE) AS strike,
                CASE
                    WHEN UPPER(CAST(call_put AS VARCHAR)) IN ('C', 'CALL') THEN 'C'
                    WHEN UPPER(CAST(call_put AS VARCHAR)) IN ('P', 'PUT') THEN 'P'
                    ELSE NULL
                END AS side,
                TRY_CAST(bid AS DOUBLE) AS bid,
                TRY_CAST(ask AS DOUBLE) AS ask,
                TRY_CAST(iv AS DOUBLE) AS iv,
                TRY_CAST(delta AS DOUBLE) AS delta
            FROM {source}
            WHERE UPPER(CAST(act_symbol AS VARCHAR)) = ?
              AND CAST(date AS DATE) = ?
              AND CAST(expiration AS DATE) {expiry_cmp} ?
              AND CAST(expiration AS DATE) <= ?
              AND TRY_CAST(strike AS DOUBLE) > 0
              AND TRY_CAST(iv AS DOUBLE) > 0
              AND TRY_CAST(iv AS DOUBLE) <= 5
              AND isfinite(TRY_CAST(iv AS DOUBLE))
            ORDER BY expiry_date, strike, side
            """,
            [ticker.upper(), as_of_date, earnings_date, max_expiry],
        ).fetchall()
    except duckdb.Error:
        # Some compact/unit-test views intentionally omit IV. In that case the
        # hierarchy simply continues to historical evidence.
        return None, pair_failure_reason

    if not rows:
        return None, pair_failure_reason

    actual_spot = _eod_spot(conn, ticker=ticker, as_of_date=as_of_date)
    expiries = sorted({row[0] for row in rows if row[0] is not None})
    for expiry in expiries:
        expiry_rows = [row for row in rows if row[0] == expiry]
        if not expiry_rows:
            continue
        proxy_spot = actual_spot or float(median(float(row[1]) for row in expiry_rows))
        spot_source = "eod_close" if actual_spot is not None else "median_strike_proxy"

        candidates: dict[str, list[dict[str, Any]]] = {"C": [], "P": []}
        for _, strike_raw, side, bid, ask, iv_raw, delta_raw in expiry_rows:
            if side not in candidates:
                continue
            strike = _finite_positive(strike_raw)
            iv = _finite_positive(iv_raw)
            if strike is None or iv is None or iv > 5:
                continue
            quote_ok, relative_spread = _valid_leg_quote(
                bid,
                ask,
                policy.max_leg_relative_spread,
            )
            if not quote_ok:
                continue

            target_delta = 0.5 if side == "C" else -0.5
            delta: float | None = None
            if delta_raw is not None:
                try:
                    candidate_delta = float(delta_raw)
                except (TypeError, ValueError):
                    candidate_delta = math.nan
                if math.isfinite(candidate_delta) and (
                    (side == "C" and 0 <= candidate_delta <= 1)
                    or (side == "P" and -1 <= candidate_delta <= 0)
                ):
                    delta = candidate_delta

            atm_metric = (
                abs(delta - target_delta)
                if delta is not None
                else abs(strike / proxy_spot - 1.0)
            )
            if atm_metric > policy.max_atm_delta_distance:
                continue
            candidates[side].append(
                {
                    "side": side,
                    "strike": strike,
                    "iv": iv,
                    "delta": delta,
                    "atm_metric": atm_metric,
                    "relative_spread": relative_spread,
                    "moneyness_distance": abs(strike / proxy_spot - 1.0),
                }
            )

        selected: dict[str, dict[str, Any]] = {}
        for side in ("C", "P"):
            if not candidates[side]:
                continue
            selected[side] = min(
                candidates[side],
                key=lambda row: (
                    row["atm_metric"],
                    row["relative_spread"] is None,
                    row["relative_spread"]
                    if row["relative_spread"] is not None
                    else math.inf,
                    row["moneyness_distance"],
                    row["strike"],
                ),
            )

        if not selected:
            continue

        dte = (expiry - as_of_date).days
        if dte <= 0:
            continue
        sides_used = [side for side in ("C", "P") if side in selected]
        avg_iv = sum(selected[side]["iv"] for side in sides_used) / len(sides_used)
        iv_em_pct = avg_iv * math.sqrt(dte / 365.0)
        if not math.isfinite(iv_em_pct) or iv_em_pct <= 0:
            continue

        details: dict[str, Any] = {
            "estimator": "atm_iv",
            "expiry_date": expiry,
            "dte": dte,
            "avg_iv": avg_iv,
            "iv_em_pct": iv_em_pct,
            "sides_used": sides_used,
            "estimated_spot": proxy_spot,
            "spot_source": spot_source,
        }
        for side, prefix in (("C", "call"), ("P", "put")):
            row = selected.get(side)
            if row is None:
                continue
            details[f"{prefix}_strike"] = row["strike"]
            details[f"{prefix}_iv"] = row["iv"]
            details[f"{prefix}_delta"] = row["delta"]
            details[f"{prefix}_atm_metric"] = row["atm_metric"]
            details[f"{prefix}_relative_spread"] = row["relative_spread"]
        return details, pair_failure_reason

    return None, pair_failure_reason


def _ticker_historical_moves(
    conn: duckdb.DuckDBPyConnection,
    *,
    ticker: str,
    cutoff: date,
    limit: int,
) -> list[float]:
    if not _relation_exists(conn, "earnings_events") or not _relation_exists(conn, "v_ohlcv"):
        return []
    rows = conn.execute(
        """
        SELECT ABS(post.close / NULLIF(pre.close, 0) - 1.0) AS realized
        FROM earnings_events e
        JOIN LATERAL (
            SELECT close
            FROM v_ohlcv
            WHERE act_symbol = e.ticker
              AND date >= e.earnings_dt - INTERVAL '5' DAY
              AND (
                ((LOWER(COALESCE(e.timing, 'unknown')) IN ('after_market_close','amc','after_close')
                   OR LOWER(COALESCE(e.timing, 'unknown')) LIKE '%after%') AND date <= e.earnings_dt)
                OR
                (NOT (LOWER(COALESCE(e.timing, 'unknown')) IN ('after_market_close','amc','after_close')
                   OR LOWER(COALESCE(e.timing, 'unknown')) LIKE '%after%') AND date < e.earnings_dt)
              )
              AND close > 0
            ORDER BY date DESC
            LIMIT 1
        ) pre ON TRUE
        JOIN LATERAL (
            SELECT close
            FROM v_ohlcv
            WHERE act_symbol = e.ticker
              AND date <= e.earnings_dt + INTERVAL '5' DAY
              AND date <= ?
              AND (
                ((LOWER(COALESCE(e.timing, 'unknown')) IN ('before_market_open','bmo','before_open')
                   OR LOWER(COALESCE(e.timing, 'unknown')) LIKE '%before%') AND date >= e.earnings_dt)
                OR
                (NOT (LOWER(COALESCE(e.timing, 'unknown')) IN ('before_market_open','bmo','before_open')
                   OR LOWER(COALESCE(e.timing, 'unknown')) LIKE '%before%') AND date > e.earnings_dt)
              )
              AND close > 0
            ORDER BY date ASC
            LIMIT 1
        ) post ON TRUE
        WHERE e.ticker = ? AND e.earnings_dt < ?
        ORDER BY e.earnings_dt DESC
        LIMIT ?
        """,
        [cutoff, ticker.upper(), cutoff, limit],
    ).fetchall()
    values = [_finite_positive(row[0]) for row in rows]
    return [value for value in values if value is not None]


def build_universe_historical_prior(
    conn: duckdb.DuckDBPyConnection,
    *,
    cutoff: date,
    window_days: int = 730,
) -> dict[str, Any]:
    start = cutoff - timedelta(days=window_days)
    if not _relation_exists(conn, "earnings_events") or not _relation_exists(conn, "v_ohlcv"):
        return {
            "as_of_date": cutoff.isoformat(),
            "window_start": start.isoformat(),
            "window_end": cutoff.isoformat(),
            "median_abs_move": None,
            "event_count": 0,
            "symbol_count": 0,
        }
    rows = conn.execute(
        """
        SELECT e.ticker, ABS(post.close / NULLIF(pre.close, 0) - 1.0) AS realized
        FROM earnings_events e
        JOIN LATERAL (
            SELECT close
            FROM v_ohlcv
            WHERE act_symbol = e.ticker
              AND date >= e.earnings_dt - INTERVAL '5' DAY
              AND (
                ((LOWER(COALESCE(e.timing, 'unknown')) IN ('after_market_close','amc','after_close')
                   OR LOWER(COALESCE(e.timing, 'unknown')) LIKE '%after%') AND date <= e.earnings_dt)
                OR
                (NOT (LOWER(COALESCE(e.timing, 'unknown')) IN ('after_market_close','amc','after_close')
                   OR LOWER(COALESCE(e.timing, 'unknown')) LIKE '%after%') AND date < e.earnings_dt)
              )
              AND close > 0
            ORDER BY date DESC
            LIMIT 1
        ) pre ON TRUE
        JOIN LATERAL (
            SELECT close
            FROM v_ohlcv
            WHERE act_symbol = e.ticker
              AND date <= e.earnings_dt + INTERVAL '5' DAY
              AND date <= ?
              AND (
                ((LOWER(COALESCE(e.timing, 'unknown')) IN ('before_market_open','bmo','before_open')
                   OR LOWER(COALESCE(e.timing, 'unknown')) LIKE '%before%') AND date >= e.earnings_dt)
                OR
                (NOT (LOWER(COALESCE(e.timing, 'unknown')) IN ('before_market_open','bmo','before_open')
                   OR LOWER(COALESCE(e.timing, 'unknown')) LIKE '%before%') AND date > e.earnings_dt)
              )
              AND close > 0
            ORDER BY date ASC
            LIMIT 1
        ) post ON TRUE
        WHERE e.earnings_dt >= ? AND e.earnings_dt < ?
        """,
        [cutoff, start, cutoff],
    ).fetchall()
    values: list[float] = []
    symbols: set[str] = set()
    for ticker, raw in rows:
        value = _finite_positive(raw)
        if value is None:
            continue
        values.append(value)
        symbols.add(str(ticker))
    return {
        "as_of_date": cutoff.isoformat(),
        "window_start": start.isoformat(),
        "window_end": cutoff.isoformat(),
        "median_abs_move": float(median(values)) if values else None,
        "event_count": len(values),
        "symbol_count": len(symbols),
    }


def _forecast_as_of(ml_forecast: dict[str, Any] | None, fallback: date) -> str:
    raw = (ml_forecast or {}).get("ml_snapshot_date") or (ml_forecast or {}).get("snapshot_date")
    if isinstance(raw, date):
        return raw.isoformat()
    if isinstance(raw, str) and raw:
        return raw[:10]
    return fallback.isoformat()


def _missing_ml_status(ml_forecast: dict[str, Any] | None) -> MLStatus:
    status = (ml_forecast or {}).get("ml_status")
    if status in {"unavailable_inputs", "unavailable_model", "unavailable_event"}:
        return status
    return "unavailable_inputs"


def resolve_display_forecast(
    conn: duckdb.DuckDBPyConnection,
    *,
    ticker: str,
    earnings_date: date,
    timing: str | None,
    as_of_date: date,
    ml_forecast: dict[str, Any] | None,
    strict_options: dict[str, Any] | None = None,
    universe_prior: dict[str, Any] | None = None,
    policy: DisplayPolicy | None = None,
) -> DisplayForecast:
    """Resolve the user-facing forecast using ML -> IV/options -> history.

    A validated ML forecast is always the headline when available. Point-in-time
    IV/options evidence is the first fallback, and historical estimates are used
    only when neither model nor option evidence exists.
    """

    active_policy = policy or load_display_policy()
    ml_pct = _finite_positive((ml_forecast or {}).get("em_ml_pct"))
    ml_status: MLStatus = (
        "available" if ml_pct is not None else _missing_ml_status(ml_forecast)
    )

    strict_iv = _finite_positive((strict_options or {}).get("em_baseline_iv"))
    strict_straddle = _finite_positive(
        (strict_options or {}).get("em_baseline_straddle")
        or (strict_options or {}).get("straddle_pct")
    )
    strict_pct = strict_iv or strict_straddle

    # ML is the canonical product forecast whenever a validated point estimate
    # exists. Preserve option availability in provenance instead of pretending
    # the option signal is absent.
    if ml_pct is not None:
        return DisplayForecast(
            pct=ml_pct,
            method="ml",
            as_of=_forecast_as_of(ml_forecast, as_of_date),
            ml_status="available",
            options_status=(
                "decision_eligible" if strict_pct is not None else "unavailable"
            ),
            fallback_reason=None,
            selected_options_details=(
                {
                    "estimator": "atm_iv" if strict_iv is not None else "straddle_mid",
                    "expiry_date": (strict_options or {}).get("expiry_date"),
                    "dte": (strict_options or {}).get("dte"),
                    "atm_iv": (strict_options or {}).get("atm_iv"),
                    "atm_strike": (strict_options or {}).get("atm_strike"),
                    "straddle_price": (strict_options or {}).get("straddle_price"),
                }
                if strict_pct is not None
                else None
            ),
        )

    if strict_pct is not None:
        return DisplayForecast(
            pct=strict_pct,
            method="options_math",
            as_of=as_of_date.isoformat(),
            ml_status=ml_status,
            options_status="decision_eligible",
            fallback_reason=None,
            selected_options_details={
                "estimator": "atm_iv" if strict_iv is not None else "straddle_mid",
                "expiry_date": (strict_options or {}).get("expiry_date"),
                "dte": (strict_options or {}).get("dte"),
                "atm_iv": (strict_options or {}).get("atm_iv"),
                "atm_strike": (strict_options or {}).get("atm_strike"),
                "straddle_price": (strict_options or {}).get("straddle_price"),
            },
        )

    indicative_pair, pair_failure_reason = _select_indicative_pair(
        conn,
        ticker=ticker,
        as_of_date=as_of_date,
        earnings_date=earnings_date,
        timing=timing,
        policy=active_policy,
    )
    iv_details, iv_failure_reason = _select_indicative_iv(
        conn,
        ticker=ticker,
        as_of_date=as_of_date,
        earnings_date=earnings_date,
        timing=timing,
        policy=active_policy,
        pair_failure_reason=pair_failure_reason,
    )
    if iv_details is not None:
        pct = _finite_positive(iv_details.get("iv_em_pct"))
        if pct is not None:
            details = dict(iv_details)
            if isinstance(details.get("expiry_date"), date):
                details["expiry_date"] = details["expiry_date"].isoformat()
            return DisplayForecast(
                pct=pct,
                method="options_indicative",
                as_of=as_of_date.isoformat(),
                ml_status=ml_status,
                options_status="indicative",
                fallback_reason=iv_failure_reason or "quote_quality",
                selected_options_details=details,
            )

    if indicative_pair is not None:
        spot = _finite_positive(indicative_pair.get("estimated_spot"))
        straddle = _finite_positive(indicative_pair.get("straddle_mid"))
        pct = straddle / spot if straddle is not None and spot is not None else None
        if pct is not None and math.isfinite(pct) and pct > 0:
            details = dict(indicative_pair)
            if isinstance(details.get("expiry_date"), date):
                details["expiry_date"] = details["expiry_date"].isoformat()
            details.setdefault("estimator", "straddle_mid")
            return DisplayForecast(
                pct=float(pct),
                method="options_indicative",
                as_of=as_of_date.isoformat(),
                ml_status=ml_status,
                options_status="indicative",
                fallback_reason="quote_quality",
                selected_options_details=details,
            )

    historical = _ticker_historical_moves(
        conn,
        ticker=ticker,
        cutoff=as_of_date,
        limit=active_policy.ticker_history_window_events,
    )
    if len(historical) >= active_policy.min_ticker_history_events:
        return DisplayForecast(
            pct=float(median(historical)),
            method="historical",
            as_of=as_of_date.isoformat(),
            ml_status=ml_status,
            options_status="unavailable",
            fallback_reason=iv_failure_reason,
            historical_event_count=len(historical),
        )

    prior = universe_prior or build_universe_historical_prior(
        conn,
        cutoff=as_of_date,
        window_days=active_policy.universe_prior_window_days,
    )
    prior_pct = _finite_positive(prior.get("median_abs_move"))
    if prior_pct is None:
        raise DisplayForecastError(
            f"no finite display forecast for {ticker} {earnings_date.isoformat()}"
        )
    return DisplayForecast(
        pct=prior_pct,
        method="historical_prior",
        as_of=str(prior.get("as_of_date") or as_of_date.isoformat()),
        ml_status=ml_status,
        options_status="unavailable",
        fallback_reason="insufficient_ticker_history",
        historical_event_count=len(historical),
    )
