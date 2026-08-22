#!/usr/bin/env python3
"""
Build frontend data from local parquet files.

Generates:
  public/weekly.json         — current week's earnings + expected moves
  public/screener.json       — merged, deduped earnings rows for /screener (single fetch)
  public/symbols/{SYM}.json  — per-symbol detail with implied moves, Greeks, term structure

Uses the most recent parquet snapshot as the as-of date. Safe to re-run.
"""

import json
import math
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb

sys.path.append(str(Path(__file__).parent))
from build_earnings_events import build_earnings_events_table, create_duckdb_views
from fiscal_calendar import display_fiscal_year, load_fiscal_year_naming
from math_baseline import compute_em_math
from twelvedata_basic import (
    TwelveDataUsageLedger,
    fetch_daily_closes,
    load_twelvedata_config,
    plan_credit_use,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
# Vercel serves from apps/frontend/public only (see vercel.json outputDirectory).
PUBLIC_DIR = REPO_ROOT / "apps" / "frontend" / "public"
EARNINGS_CSV = DATA_DIR / "earnings_calendar.csv"
FORECASTS_DIR = DATA_DIR / "forecasts"
FORECAST_RECEIPT_PATH = FORECASTS_DIR / "receipts" / "latest_forecasts.json"
PROVIDER_ENRICHMENTS_DIR = DATA_DIR / "provider_enrichments"
MODEL_HORIZONS = [1, 2, 3, 7, 14, 21]
MARKET_HOLIDAYS_TS = REPO_ROOT / "apps" / "frontend" / "lib" / "marketHolidays.generated.ts"
ET = ZoneInfo("America/New_York")


def load_ml_forecasts() -> dict[tuple[str, str], dict]:
    """Return {(ticker, earnings_date_iso): forecast_row} from the newest
    daily_score.py output. Picks the row whose model_horizon is closest to the
    actual lead time, and the most recent snapshot_date within that horizon.
    Returns {} when no forecasts exist."""
    if not FORECASTS_DIR.exists():
        return {}
    files = sorted(FORECASTS_DIR.glob("forecasts_*.parquet"))
    if not files:
        return {}
    latest = files[-1]
    try:
        import pandas as pd  # local import — only needed when forecasts exist
        df = pd.read_parquet(latest)
    except Exception as e:
        print(f"⚠️  Could not read {latest.name}: {e}")
        return {}
    if df.empty:
        return {}

    df = df.copy()
    df["earnings_date"] = pd.to_datetime(df["earnings_date"]).dt.date
    df["snapshot_date"] = pd.to_datetime(df["snapshot_date"]).dt.date
    df["lead_days"] = (df["earnings_date"] - df["snapshot_date"]).apply(lambda d: d.days)
    df["horizon_gap"] = (df["model_horizon"] - df["lead_days"]).abs()

    # Per (ticker, earnings_date): smallest horizon_gap, then most recent snapshot.
    df = df.sort_values(["act_symbol", "earnings_date", "horizon_gap", "snapshot_date"],
                        ascending=[True, True, True, False])
    df = df.drop_duplicates(subset=["act_symbol", "earnings_date"], keep="first")

    out: dict[tuple[str, str], dict] = {}
    for row in df.to_dict(orient="records"):
        key = (row["act_symbol"], row["earnings_date"].isoformat())
        out[key] = row
    print(f"🤖 Loaded {len(out)} ML forecasts from {latest.name}")
    return out


def ml_fields(fc: dict | None) -> dict:
    """Flatten a forecast row to the public JSON field names. Prefers p10/p90
    when present (newer daily_score.py output); falls back to band68/band95."""
    if not fc:
        return {}
    out: dict = {}

    def pick(*keys):
        for k in keys:
            v = fc.get(k)
            if v is not None and not (isinstance(v, float) and math.isnan(v)):
                return v
        return None

    out["em_ml_pct"] = jsonable(pick("em_ml_pct"))
    out["em_ml_abs"] = jsonable(pick("em_ml_abs"))
    out["correction_factor"] = jsonable(pick("correction_factor"))
    out["model_horizon"] = pick("model_horizon")
    out["ml_snapshot_date"] = (
        fc["snapshot_date"].isoformat() if fc.get("snapshot_date") else None
    )
    # Prefer true quantiles if the trainer emitted them, else use band endpoints.
    out["p10"] = jsonable(pick("p10", "band95_low_pct"))
    out["p25"] = jsonable(pick("p25", "band68_low_pct"))
    out["p50"] = jsonable(pick("p50", "em_ml_pct"))
    out["p75"] = jsonable(pick("p75", "band68_high_pct"))
    out["p90"] = jsonable(pick("p90", "band95_high_pct"))
    return {k: v for k, v in out.items() if v is not None}


def _read_enrichment_rows(filename: str) -> tuple[str | None, list[dict]]:
    path = PROVIDER_ENRICHMENTS_DIR / filename
    if not path.exists():
        return None, []
    try:
        payload = json.loads(path.read_text())
    except Exception as exc:
        print(f"⚠️  Could not read provider enrichment {filename}: {exc}")
        return None, []
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return payload.get("generated_at") if isinstance(payload, dict) else None, []
    return payload.get("generated_at"), [r for r in rows if isinstance(r, dict)]


def _latest_row(rows: list[dict], *, date_keys: tuple[str, ...] = ("collected_at",)) -> dict | None:
    if not rows:
        return None
    return sorted(
        rows,
        key=lambda r: tuple(str(r.get(k) or "") for k in date_keys),
        reverse=True,
    )[0]


def _row_source(row: dict) -> dict:
    return {
        k: v
        for k, v in {
            "provider": row.get("provider"),
            "endpoint": row.get("source_endpoint"),
            "collected_at": row.get("collected_at"),
        }.items()
        if v is not None
    }


def _provider_signal_flags(enrichment: dict) -> list[str]:
    flags: list[str] = []
    short = enrichment.get("short_interest") or {}
    options = enrichment.get("options_flow") or {}
    actions = enrichment.get("corporate_actions") or {}
    days_to_cover = short.get("days_to_cover")
    if isinstance(days_to_cover, (int, float)):
        if days_to_cover >= 5:
            flags.append("high_short_interest")
        elif days_to_cover >= 3:
            flags.append("elevated_short_interest")
    pcv = options.get("put_call_volume_ratio")
    if isinstance(pcv, (int, float)) and pcv > 0:
        if pcv >= 1.25:
            flags.append("put_heavy_flow")
        elif pcv <= 0.75:
            flags.append("call_heavy_flow")
    pcoi = options.get("put_call_open_interest_ratio")
    if isinstance(pcoi, (int, float)) and pcoi > 0:
        if pcoi >= 1.25:
            flags.append("put_heavy_open_interest")
        elif pcoi <= 0.75:
            flags.append("call_heavy_open_interest")
    if actions.get("split_events"):
        flags.append("recent_split_history")
    if actions.get("dividend_events"):
        flags.append("recent_dividend_history")
    return flags


def _provider_signal_score(enrichment: dict) -> float | None:
    short = enrichment.get("short_interest") or {}
    options = enrichment.get("options_flow") or {}
    score = 0.0
    count = 0
    days_to_cover = short.get("days_to_cover")
    if isinstance(days_to_cover, (int, float)) and days_to_cover > 0:
        score += min(float(days_to_cover) / 10.0, 1.0)
        count += 1
    for key in ("put_call_volume_ratio", "put_call_open_interest_ratio"):
        ratio = options.get(key)
        if isinstance(ratio, (int, float)) and ratio > 0:
            score += min(abs(math.log(float(ratio))), 1.0)
            count += 1
    return round(score / count, 6) if count else None


def load_provider_enrichments() -> dict[str, dict]:
    """Load derived provider tables and compact them into per-symbol signals.

    Raw provider payloads are intentionally not published. This emits only
    normalized fields useful to product surfaces and future ML features.
    """
    generated_at: dict[str, str] = {}
    tables: dict[str, list[dict]] = {}
    for table, filename in {
        "company_facts": "company_facts.json",
        "options_provider_signals": "options_provider_signals.json",
        "corporate_actions": "corporate_actions.json",
        "earnings_news_signals": "earnings_news_signals.json",
        "live_market_signals": "live_market_signals.json",
    }.items():
        ts, rows = _read_enrichment_rows(filename)
        if ts:
            generated_at[table] = ts
        tables[table] = rows

    by_symbol: dict[str, dict] = {}

    def slot(symbol: str) -> dict:
        symbol = symbol.upper()
        return by_symbol.setdefault(symbol, {})

    facts_by_symbol: dict[str, list[dict]] = {}
    for row in tables["company_facts"]:
        symbol = str(row.get("symbol") or "").upper()
        if symbol:
            facts_by_symbol.setdefault(symbol, []).append(row)
    for symbol, rows in facts_by_symbol.items():
        short_rows = [r for r in rows if r.get("days_to_cover") is not None or r.get("short_interest") is not None]
        short = _latest_row(short_rows, date_keys=("settlement_date", "collected_at"))
        if short:
            slot(symbol)["short_interest"] = {
                **_row_source(short),
                "shares": jsonable(short.get("short_interest")),
                "avg_daily_volume": jsonable(short.get("avg_daily_volume")),
                "days_to_cover": jsonable(short.get("days_to_cover")),
                "settlement_date": short.get("settlement_date"),
            }

    option_by_symbol: dict[str, list[dict]] = {}
    for row in tables["options_provider_signals"]:
        symbol = str(row.get("symbol") or "").upper()
        if symbol:
            option_by_symbol.setdefault(symbol, []).append(row)
    for symbol, rows in option_by_symbol.items():
        row = _latest_row(rows)
        if not row:
            continue
        slot(symbol)["options_flow"] = {
            **_row_source(row),
            "contract_count": jsonable(row.get("contract_count")),
            "call_count": jsonable(row.get("call_count")),
            "put_count": jsonable(row.get("put_count")),
            "total_call_volume": jsonable(row.get("total_call_volume")),
            "total_put_volume": jsonable(row.get("total_put_volume")),
            "total_call_open_interest": jsonable(row.get("total_call_open_interest")),
            "total_put_open_interest": jsonable(row.get("total_put_open_interest")),
            "put_call_volume_ratio": jsonable(row.get("put_call_volume_ratio")),
            "put_call_open_interest_ratio": jsonable(row.get("put_call_open_interest_ratio")),
            "iv_coverage_pct": jsonable(row.get("iv_coverage_pct")),
            "greeks_coverage_pct": jsonable(row.get("greeks_coverage_pct")),
        }

    actions_by_symbol: dict[str, list[dict]] = {}
    for row in tables["corporate_actions"]:
        symbol = str(row.get("symbol") or "").upper()
        if symbol:
            actions_by_symbol.setdefault(symbol, []).append(row)
    for symbol, rows in actions_by_symbol.items():
        dividends = [r for r in rows if "dividend" in str(r.get("source_endpoint") or "")]
        splits = [r for r in rows if "split" in str(r.get("source_endpoint") or "")]
        latest_div = _latest_row(dividends, date_keys=("latest_event_date", "collected_at"))
        latest_split = _latest_row(splits, date_keys=("latest_event_date", "collected_at"))
        if latest_div or latest_split:
            slot(symbol)["corporate_actions"] = {
                "dividend_events": jsonable(sum(int(r.get("event_count") or 0) for r in dividends)),
                "latest_dividend_date": latest_div.get("latest_event_date") if latest_div else None,
                "split_events": jsonable(sum(int(r.get("event_count") or 0) for r in splits)),
                "latest_split_date": latest_split.get("latest_event_date") if latest_split else None,
                "sources": [src for src in (_row_source(r) for r in [latest_div, latest_split] if r) if src],
            }

    for symbol, enrichment in list(by_symbol.items()):
        flags = _provider_signal_flags(enrichment)
        score = _provider_signal_score(enrichment)
        if flags:
            enrichment["flags"] = flags
        if score is not None:
            enrichment["signal_score"] = score
        sources = sorted(
            {
                str(v.get("provider"))
                for v in enrichment.values()
                if isinstance(v, dict) and v.get("provider")
            }
        )
        enrichment["sources"] = sources
        enrichment["generated_at"] = generated_at or None
        by_symbol[symbol] = {k: v for k, v in enrichment.items() if v is not None}

    if by_symbol:
        print(f"🧩 Loaded provider enrichment signals for {len(by_symbol)} symbols")
    return by_symbol


def provider_event_fields(enrichment: dict | None) -> dict:
    if not enrichment:
        return {}
    short = enrichment.get("short_interest") or {}
    options = enrichment.get("options_flow") or {}
    total_call_volume = options.get("total_call_volume")
    total_put_volume = options.get("total_put_volume")
    total_options_volume = (
        total_call_volume + total_put_volume
        if isinstance(total_call_volume, (int, float)) and isinstance(total_put_volume, (int, float))
        else None
    )
    flat = {
        "provider_enrichment": enrichment,
        "short_days_to_cover": short.get("days_to_cover"),
        "short_interest_shares": short.get("shares"),
        "put_call_volume_ratio": options.get("put_call_volume_ratio"),
        "put_call_open_interest_ratio": options.get("put_call_open_interest_ratio"),
        "provider_options_volume": total_options_volume,
        "provider_signal_score": enrichment.get("signal_score"),
    }
    return {k: jsonable(v) for k, v in flat.items() if v is not None}


def write_to_public(relpath: str, content: str) -> None:
    path = PUBLIC_DIR / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def build_dashboard_evidence(receipt: dict) -> dict:
    """Compress one pipeline receipt into the single manifest used by the UI."""
    if receipt.get("schema") != "quantiv.evidence-receipt.v1":
        raise ValueError("unsupported or missing forecast evidence schema")
    if not str(receipt.get("receipt_id", "")).startswith("sha256:"):
        raise ValueError("forecast evidence receipt_id must be a SHA-256 identifier")

    forecast = (receipt.get("reconciliation") or {}).get("forecasts")
    quality = receipt.get("quality")
    artifacts = receipt.get("artifacts")
    if not isinstance(forecast, dict) or not isinstance(quality, dict):
        raise ValueError("forecast evidence is missing reconciliation or quality data")
    if not isinstance(artifacts, list):
        raise ValueError("forecast evidence is missing artifact bundles")

    controls = forecast.get("reconciliation") or {}
    artifact_bundles = [
        {
            key: artifact.get(key)
            for key in ("name", "producer", "member_count", "bytes", "sha256")
        }
        for artifact in artifacts
        if isinstance(artifact, dict)
    ]
    return {
        "schema": "quantiv.dashboard-evidence.v1",
        "receipt_id": receipt["receipt_id"],
        "receipt_file": receipt.get("receipt_file"),
        "validated_at": receipt.get("validated_at"),
        "quality": {
            "status": quality.get("status", "failed"),
            "issue_count": int(quality.get("issue_count", 0)),
            "issue_codes": quality.get("issue_codes") or [],
        },
        "coverage": {
            "rows": int(forecast.get("rows", 0)),
            "symbols": int(forecast.get("symbols", 0)),
            "events": int(forecast.get("events", 0)),
            "horizons": forecast.get("horizons") or receipt.get("horizons") or [],
        },
        "observation_window": forecast.get("data_window") or {},
        "controls": {
            "evaluated": len(controls),
            "exceptions": int(quality.get("issue_count", 0)),
            "results": controls,
        },
        "artifact_bundles": artifact_bundles,
    }


def publish_forecast_evidence() -> bool:
    """Publish one small UI manifest; never duplicate receipts into symbol JSON."""
    public_path = PUBLIC_DIR / "evidence" / "forecast.json"
    if not FORECAST_RECEIPT_PATH.exists():
        public_path.unlink(missing_ok=True)
        print("⚠️  No forecast evidence receipt; public trust manifest removed")
        return False
    try:
        receipt = json.loads(FORECAST_RECEIPT_PATH.read_text())
        evidence = build_dashboard_evidence(receipt)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        public_path.unlink(missing_ok=True)
        raise ValueError(f"invalid forecast evidence receipt: {exc}") from exc
    write_to_public("evidence/forecast.json", json.dumps(evidence, indent=2))
    print(
        "🧾 Forecast evidence → "
        f"{evidence['quality']['status']} · "
        f"{evidence['coverage']['rows']} rows · "
        f"{evidence['controls']['exceptions']} exceptions"
    )
    return True


def monday_of_week(d: date) -> date:
    return d - timedelta(days=d.weekday())


def target_week(today: date) -> tuple[date, date]:
    """Return (Mon, Fri) of the trading week for `today`. Weekend rolls to next week."""
    if today.weekday() >= 5:
        base = today + timedelta(days=(7 - today.weekday()))
    else:
        base = monday_of_week(today)
    return base, base + timedelta(days=4)


# Week offsets we expose in the UI: last week, this week, next week, week after next.
WEEK_OFFSETS = [-1, 0, 1, 2]


def load_market_holidays() -> set[date]:
    if not MARKET_HOLIDAYS_TS.exists():
        return set()
    text = MARKET_HOLIDAYS_TS.read_text()
    match = re.search(
        r"MARKET_HOLIDAYS_US\s*=\s*\[(.*?)\]\s*as const",
        text,
        flags=re.S,
    )
    if not match:
        return set()
    return {
        date.fromisoformat(m.group(0))
        for m in re.finditer(r"\d{4}-\d{2}-\d{2}", match.group(1))
    }


MARKET_HOLIDAYS = load_market_holidays()


def timing_bucket(timing: str | None) -> str:
    k = (timing or "").lower()
    if k == "bmo" or "before" in k:
        return "bmo"
    if k == "amc" or "after" in k:
        return "amc"
    return "unknown"


def next_trading_day(d: date) -> date:
    cur = d + timedelta(days=1)
    for _ in range(366):
        if cur.weekday() < 5 and cur not in MARKET_HOLIDAYS:
            return cur
        cur += timedelta(days=1)
    return cur


def earnings_reaction_close_date(earnings_dt: date, timing: str | None) -> date:
    return next_trading_day(earnings_dt) if timing_bucket(timing) == "amc" else earnings_dt


def realization_window_complete(
    earnings_dt: date,
    timing: str | None,
    now: datetime | None = None,
) -> bool:
    now_et = now or datetime.now(ET)
    close_date = earnings_reaction_close_date(earnings_dt, timing)
    if now_et.date() > close_date:
        return True
    if now_et.date() < close_date:
        return False
    return now_et.hour * 60 + now_et.minute >= 16 * 60


def realized_move_from_ohlcv(
    conn,
    ticker: str,
    earnings_dt: date,
    timing: str | None,
) -> float | None:
    """Timing-aware regular-session close-to-close move from local OHLCV."""
    try:
        row = conn.execute(
            """
            WITH event AS (
                SELECT
                    CAST(? AS VARCHAR) AS ticker,
                    CAST(? AS DATE) AS earnings_dt,
                    LOWER(COALESCE(CAST(? AS VARCHAR), 'unknown')) AS timing
            )
            SELECT (post.close / NULLIF(pre.close, 0) - 1.0) AS realized_move
            FROM event e
            LEFT JOIN v_ohlcv pre ON pre.act_symbol = e.ticker
                AND pre.close > 0
                AND pre.date >= e.earnings_dt - INTERVAL '5' DAY
                AND (
                    ((e.timing IN ('after_market_close', 'amc', 'after_close') OR e.timing LIKE '%after%')
                        AND pre.date <= e.earnings_dt)
                    OR (NOT (e.timing IN ('after_market_close', 'amc', 'after_close') OR e.timing LIKE '%after%')
                        AND pre.date < e.earnings_dt)
                )
            LEFT JOIN v_ohlcv post ON post.act_symbol = e.ticker
                AND post.close > 0
                AND post.date <= e.earnings_dt + INTERVAL '5' DAY
                AND (
                    ((e.timing IN ('before_market_open', 'bmo', 'before_open') OR e.timing LIKE '%before%')
                        AND post.date >= e.earnings_dt)
                    OR (NOT (e.timing IN ('before_market_open', 'bmo', 'before_open') OR e.timing LIKE '%before%')
                        AND post.date > e.earnings_dt)
                )
            QUALIFY ROW_NUMBER() OVER (
                ORDER BY pre.date DESC NULLS LAST, post.date ASC NULLS LAST
            ) = 1
            """,
            [ticker, earnings_dt, timing or "unknown"],
        ).fetchone()
    except Exception:
        return None
    if not row or row[0] is None:
        return None
    return float(row[0])


def enrich_realized_moves_from_ohlcv(conn, events: list[dict]) -> int:
    """Backfill/refresh realized_move_pct on both new and preserved week rows."""
    candidates: list[tuple[dict, str, date, str]] = []
    for ev in events:
        try:
            ticker = str(ev.get("ticker") or "").upper()
            earnings_dt = date.fromisoformat(str(ev.get("earnings_date") or "")[:10])
        except ValueError:
            continue
        if not ticker or not realization_window_complete(earnings_dt, ev.get("timing")):
            continue
        candidates.append((ev, ticker, earnings_dt, str(ev.get("timing") or "unknown")))
    if not candidates:
        return 0

    try:
        conn.execute(
            """
            CREATE OR REPLACE TEMP TABLE tmp_earnings_reaction_events (
                ticker VARCHAR,
                earnings_dt DATE,
                timing VARCHAR
            )
            """
        )
        conn.executemany(
            "INSERT INTO tmp_earnings_reaction_events VALUES (?, ?, ?)",
            [(ticker, earnings_dt, timing) for _, ticker, earnings_dt, timing in candidates],
        )
        rows = conn.execute(
            """
            SELECT ticker, earnings_dt, realized_move
            FROM (
                SELECT
                    e.ticker,
                    e.earnings_dt,
                    (post.close / NULLIF(pre.close, 0) - 1.0) AS realized_move,
                    ROW_NUMBER() OVER (
                        PARTITION BY e.ticker, e.earnings_dt
                        ORDER BY pre.date DESC NULLS LAST, post.date ASC NULLS LAST
                    ) AS rn
                FROM tmp_earnings_reaction_events e
                LEFT JOIN v_ohlcv pre ON pre.act_symbol = e.ticker
                    AND pre.close > 0
                    AND pre.date >= e.earnings_dt - INTERVAL '5' DAY
                    AND (
                        ((LOWER(COALESCE(e.timing, 'unknown')) IN ('after_market_close', 'amc', 'after_close')
                            OR LOWER(COALESCE(e.timing, 'unknown')) LIKE '%after%')
                            AND pre.date <= e.earnings_dt)
                        OR (NOT (LOWER(COALESCE(e.timing, 'unknown')) IN ('after_market_close', 'amc', 'after_close')
                            OR LOWER(COALESCE(e.timing, 'unknown')) LIKE '%after%')
                            AND pre.date < e.earnings_dt)
                    )
                LEFT JOIN v_ohlcv post ON post.act_symbol = e.ticker
                    AND post.close > 0
                    AND post.date <= e.earnings_dt + INTERVAL '5' DAY
                    AND (
                        ((LOWER(COALESCE(e.timing, 'unknown')) IN ('before_market_open', 'bmo', 'before_open')
                            OR LOWER(COALESCE(e.timing, 'unknown')) LIKE '%before%')
                            AND post.date >= e.earnings_dt)
                        OR (NOT (LOWER(COALESCE(e.timing, 'unknown')) IN ('before_market_open', 'bmo', 'before_open')
                            OR LOWER(COALESCE(e.timing, 'unknown')) LIKE '%before%')
                            AND post.date > e.earnings_dt)
                    )
            )
            WHERE rn = 1 AND realized_move IS NOT NULL
            """
        ).fetchall()
    except Exception:
        rows = []

    moves = {
        (str(ticker).upper(), earnings_dt): float(move)
        for ticker, earnings_dt, move in rows
        if move is not None
    }
    updated = 0
    for ev, ticker, earnings_dt, _ in candidates:
        move = moves.get((ticker, earnings_dt))
        if move is None:
            continue
        new_value = jsonable(move)
        if ev.get("realized_move_pct") != new_value:
            ev["realized_move_pct"] = new_value
            updated += 1
    return updated


def _compute_realized_from_closes(
    closes: list[tuple[date, float]],
    earnings_dt: date,
    timing: str | None,
) -> float | None:
    if not closes:
        return None
    bucket = timing_bucket(timing)
    if bucket == "amc":
        pre = [row for row in closes if row[0] <= earnings_dt]
        post = [row for row in closes if row[0] > earnings_dt]
    elif bucket == "bmo":
        pre = [row for row in closes if row[0] < earnings_dt]
        post = [row for row in closes if row[0] >= earnings_dt]
    else:
        pre = [row for row in closes if row[0] < earnings_dt]
        post = [row for row in closes if row[0] > earnings_dt]
    if not pre or not post:
        return None
    pre_close = pre[-1][1]
    post_close = post[0][1]
    if pre_close <= 0 or post_close <= 0:
        return None
    return post_close / pre_close - 1.0


def twelvedata_realized_candidates(events: list[dict]) -> tuple[list[tuple[dict, str, date, date]], dict[str, int]]:
    missing: list[tuple[dict, str, date, date]] = []
    stats = {
        "already_realized": 0,
        "not_complete": 0,
        "invalid": 0,
    }
    for ev in events:
        if ev.get("realized_move_pct") is not None:
            stats["already_realized"] += 1
            continue
        try:
            ticker = str(ev.get("ticker") or "").upper()
            earnings_dt = date.fromisoformat(str(ev.get("earnings_date") or "")[:10])
        except ValueError:
            stats["invalid"] += 1
            continue
        if not ticker or not realization_window_complete(earnings_dt, ev.get("timing")):
            stats["not_complete"] += 1
            continue
        close_date = earnings_reaction_close_date(earnings_dt, ev.get("timing"))
        missing.append((ev, ticker, earnings_dt, close_date))
    return missing, stats


def print_twelvedata_dry_run(events: list[dict], *, label: str) -> None:
    missing, stats = twelvedata_realized_candidates(events)
    symbols = sorted({ticker for _, ticker, _, _ in missing})
    config = load_twelvedata_config(DATA_DIR)
    ledger = TwelveDataUsageLedger(config.ledger_path, config.daily_credit_limit)
    plan = plan_credit_use(symbols, config, ledger=ledger)
    print(
        f"    TwelveData dry-run {label}: "
        f"{len(missing)} candidate event(s), "
        f"{plan['needed_credits']} needed credit(s), "
        f"{plan['remaining_credits']} remaining, "
        f"{plan['planned_credits']} would be used; "
        f"skipped: {len(plan['skipped_symbols'])} quota, "
        f"{stats['already_realized']} already realized, "
        f"{stats['not_complete']} not complete, "
        f"{stats['invalid']} invalid",
        flush=True,
    )
    if plan["planned_symbols"]:
        print(f"      planned symbols: {', '.join(plan['planned_symbols'])}", flush=True)
    if plan["skipped_symbols"]:
        print(f"      quota-skipped symbols: {', '.join(plan['skipped_symbols'])}", flush=True)


def enrich_realized_moves_from_twelvedata(events: list[dict], *, dry_run: bool = False, label: str = "") -> int:
    config = load_twelvedata_config(DATA_DIR)
    if dry_run:
        print_twelvedata_dry_run(events, label=label)
        return 0
    if not config.realized_fallback_enabled or not config.api_key:
        return 0

    missing, _stats = twelvedata_realized_candidates(events)

    if not missing:
        return 0

    symbols = sorted({ticker for _, ticker, _, _ in missing})
    start = min(earnings_dt for _, _, earnings_dt, _ in missing) - timedelta(days=7)
    end = max(close_date for _, _, _, close_date in missing) + timedelta(days=7)
    fetch_result = fetch_daily_closes(
        symbols,
        start,
        end,
        config,
        purpose="realized_fallback",
    )
    closes_by_symbol = fetch_result.closes
    if fetch_result.skipped_symbols:
        print(
            f"  ⚠ TwelveData quota skipped {len(fetch_result.skipped_symbols)} symbol(s): "
            f"{', '.join(fetch_result.skipped_symbols)}",
            flush=True,
        )
    if fetch_result.errors:
        for err in fetch_result.errors[:8]:
            print(f"  ⚠ TwelveData fallback: {err}", flush=True)
        if len(fetch_result.errors) > 8:
            print(f"  ⚠ TwelveData fallback: {len(fetch_result.errors) - 8} more error(s)", flush=True)
    if not closes_by_symbol:
        return 0

    updated = 0
    for ev, ticker, earnings_dt, _ in missing:
        move = _compute_realized_from_closes(
            closes_by_symbol.get(ticker, []),
            earnings_dt,
            ev.get("timing"),
        )
        if move is None:
            continue
        ev["realized_move_pct"] = jsonable(move)
        updated += 1
    return updated


def twelvedata_hist_move_candidates(conn, events: list[dict]) -> dict[str, list[tuple[date, str]]]:
    """Return {ticker: last earnings events} for rows missing hist_move_avg_4q."""
    out: dict[str, list[tuple[date, str]]] = {}
    for ev in events:
        if ev.get("hist_move_avg_4q") is not None:
            continue
        ticker = str(ev.get("ticker") or "").upper()
        try:
            earnings_dt = date.fromisoformat(str(ev.get("earnings_date") or "")[:10])
        except ValueError:
            continue
        if not ticker or ticker in out:
            continue
        try:
            rows = conn.execute(
                """
                SELECT earnings_dt, timing
                FROM earnings_events
                WHERE ticker = ? AND earnings_dt < ?
                ORDER BY earnings_dt DESC
                LIMIT 4
                """,
                [ticker, earnings_dt],
            ).fetchall()
        except Exception:
            rows = []
        history = [(d, t or "unknown") for d, t in rows if realization_window_complete(d, t)]
        if history:
            out[ticker] = history
    return out


def print_twelvedata_hist_dry_run(conn, events: list[dict], *, label: str) -> None:
    candidates = twelvedata_hist_move_candidates(conn, events)
    config = load_twelvedata_config(DATA_DIR)
    ledger = TwelveDataUsageLedger(config.ledger_path, config.daily_credit_limit)
    plan = plan_credit_use(sorted(candidates), config, ledger=ledger)
    print(
        f"    TwelveData hist dry-run {label}: "
        f"{len(candidates)} ticker candidate(s), "
        f"{plan['needed_credits']} needed credit(s), "
        f"{plan['remaining_credits']} remaining, "
        f"{plan['planned_credits']} would be used; "
        f"skipped: {len(plan['skipped_symbols'])} quota",
        flush=True,
    )
    if plan["planned_symbols"]:
        print(f"      planned hist symbols: {', '.join(plan['planned_symbols'])}", flush=True)
    if plan["skipped_symbols"]:
        print(f"      quota-skipped hist symbols: {', '.join(plan['skipped_symbols'])}", flush=True)


def enrich_hist_move_avg_from_twelvedata(
    conn,
    events: list[dict],
    *,
    dry_run: bool = False,
    label: str = "",
) -> int:
    config = load_twelvedata_config(DATA_DIR)
    if dry_run:
        print_twelvedata_hist_dry_run(conn, events, label=label)
        return 0
    if not config.realized_fallback_enabled or not config.api_key:
        return 0

    candidates = twelvedata_hist_move_candidates(conn, events)
    if not candidates:
        return 0

    all_events = [item for history in candidates.values() for item in history]
    start = min(d for d, _ in all_events) - timedelta(days=7)
    end = max(earnings_reaction_close_date(d, timing) for d, timing in all_events) + timedelta(days=7)
    fetch_result = fetch_daily_closes(
        sorted(candidates),
        start,
        end,
        config,
        purpose="hist_move_avg_4q",
    )
    if fetch_result.skipped_symbols:
        print(
            f"  ⚠ TwelveData hist quota skipped {len(fetch_result.skipped_symbols)} symbol(s): "
            f"{', '.join(fetch_result.skipped_symbols)}",
            flush=True,
        )
    if fetch_result.errors:
        for err in fetch_result.errors[:8]:
            print(f"  ⚠ TwelveData hist fallback: {err}", flush=True)
        if len(fetch_result.errors) > 8:
            print(f"  ⚠ TwelveData hist fallback: {len(fetch_result.errors) - 8} more error(s)", flush=True)

    hist_avg_by_symbol: dict[str, float] = {}
    for ticker, history in candidates.items():
        closes = fetch_result.closes.get(ticker, [])
        vals = [
            abs(move)
            for d, timing in history
            if (move := _compute_realized_from_closes(closes, d, timing)) is not None
        ]
        if vals:
            hist_avg_by_symbol[ticker] = sum(vals) / len(vals)

    updated = 0
    for ev in events:
        ticker = str(ev.get("ticker") or "").upper()
        avg = hist_avg_by_symbol.get(ticker)
        if avg is None:
            continue
        new_value = jsonable(avg)
        if ev.get("hist_move_avg_4q") != new_value:
            ev["hist_move_avg_4q"] = new_value
            updated += 1
    return updated


def twelvedata_validation_sample_size() -> int:
    try:
        return max(0, int(os.getenv("TWELVEDATA_VALIDATION_SAMPLE_SIZE", "8")))
    except ValueError:
        return 8


def twelvedata_validation_delta_threshold() -> float:
    try:
        return max(0.0, float(os.getenv("TWELVEDATA_VALIDATION_DELTA_PCT", "0.005")))
    except ValueError:
        return 0.005


def twelvedata_validation_candidates(
    conn,
    events: list[dict],
    *,
    limit: int,
) -> list[tuple[str, date, str | None, float, date]]:
    out: list[tuple[str, date, str | None, float, date]] = []
    seen: set[tuple[str, date]] = set()
    for ev in events:
        if len(out) >= limit:
            break
        try:
            ticker = str(ev.get("ticker") or "").upper()
            earnings_dt = date.fromisoformat(str(ev.get("earnings_date") or "")[:10])
        except ValueError:
            continue
        key = (ticker, earnings_dt)
        if not ticker or key in seen:
            continue
        if not realization_window_complete(earnings_dt, ev.get("timing")):
            continue
        local_move = realized_move_from_ohlcv(conn, ticker, earnings_dt, ev.get("timing"))
        if local_move is None:
            continue
        seen.add(key)
        out.append((
            ticker,
            earnings_dt,
            ev.get("timing"),
            local_move,
            earnings_reaction_close_date(earnings_dt, ev.get("timing")),
        ))
    return out


def print_twelvedata_validation_dry_run(conn, events: list[dict], *, label: str) -> None:
    sample_size = twelvedata_validation_sample_size()
    if sample_size <= 0:
        return
    candidates = twelvedata_validation_candidates(conn, events, limit=sample_size)
    config = load_twelvedata_config(DATA_DIR)
    ledger = TwelveDataUsageLedger(config.ledger_path, config.daily_credit_limit)
    plan = plan_credit_use([ticker for ticker, *_ in candidates], config, ledger=ledger)
    print(
        f"    TwelveData validation dry-run {label}: "
        f"{len(candidates)} sample candidate(s), "
        f"{plan['needed_credits']} needed credit(s), "
        f"{plan['remaining_credits']} remaining, "
        f"{plan['planned_credits']} would be used; "
        f"skipped: {len(plan['skipped_symbols'])} quota",
        flush=True,
    )
    if plan["planned_symbols"]:
        print(f"      planned validation symbols: {', '.join(plan['planned_symbols'])}", flush=True)
    if plan["skipped_symbols"]:
        print(f"      quota-skipped validation symbols: {', '.join(plan['skipped_symbols'])}", flush=True)


def validate_twelvedata_against_ohlcv(
    conn,
    events: list[dict],
    *,
    dry_run: bool = False,
    label: str = "",
) -> int:
    sample_size = twelvedata_validation_sample_size()
    if sample_size <= 0:
        return 0
    if dry_run:
        print_twelvedata_validation_dry_run(conn, events, label=label)
        return 0

    config = load_twelvedata_config(DATA_DIR)
    if not config.realized_fallback_enabled or not config.api_key:
        return 0

    candidates = twelvedata_validation_candidates(conn, events, limit=sample_size)
    if not candidates:
        return 0

    symbols = sorted({ticker for ticker, *_ in candidates})
    start = min(earnings_dt for _, earnings_dt, *_ in candidates) - timedelta(days=7)
    end = max(close_date for _, _, _, _, close_date in candidates) + timedelta(days=7)
    fetch_result = fetch_daily_closes(
        symbols,
        start,
        end,
        config,
        purpose="validation_sample",
    )
    if fetch_result.skipped_symbols:
        print(
            f"  ⚠ TwelveData validation quota skipped {len(fetch_result.skipped_symbols)} symbol(s): "
            f"{', '.join(fetch_result.skipped_symbols)}",
            flush=True,
        )
    if fetch_result.errors:
        for err in fetch_result.errors[:8]:
            print(f"  ⚠ TwelveData validation: {err}", flush=True)
        if len(fetch_result.errors) > 8:
            print(f"  ⚠ TwelveData validation: {len(fetch_result.errors) - 8} more error(s)", flush=True)

    threshold = twelvedata_validation_delta_threshold()
    compared = 0
    deltas: list[tuple[str, date, float, float, float]] = []
    for ticker, earnings_dt, timing, local_move, _close_date in candidates:
        td_move = _compute_realized_from_closes(
            fetch_result.closes.get(ticker, []),
            earnings_dt,
            timing,
        )
        if td_move is None:
            continue
        compared += 1
        delta = td_move - local_move
        if abs(delta) >= threshold:
            deltas.append((ticker, earnings_dt, local_move, td_move, delta))

    if deltas:
        print(
            f"  ⚠ TwelveData validation {label}: "
            f"{len(deltas)}/{compared} material delta(s) >= {threshold:.2%}",
            flush=True,
        )
        for ticker, earnings_dt, local_move, td_move, delta in deltas[:8]:
            print(
                f"    {ticker} {earnings_dt.isoformat()}: "
                f"local={local_move:.2%}, twelvedata={td_move:.2%}, delta={delta:.2%}",
                flush=True,
            )
        if len(deltas) > 8:
            print(f"    ... {len(deltas) - 8} more material delta(s)", flush=True)
    elif compared:
        print(
            f"    TwelveData validation {label}: {compared} sample(s), "
            f"no material deltas >= {threshold:.2%}",
            flush=True,
        )
    return compared


def build_screener_payload(
    as_of_date: date,
    this_monday: date,
    week_payloads: dict[date, dict],
) -> dict:
    """Merge week JSON events in calendar order; first row wins on duplicate (ticker, date)."""
    seen: set[tuple[str, str]] = set()
    events: list[dict] = []
    for off in WEEK_OFFSETS:
        wk_start = this_monday + timedelta(days=7 * off)
        payload = week_payloads.get(wk_start)
        if not payload:
            continue
        for ev in payload.get("events", []):
            t = ev.get("ticker") or ""
            ed = ev.get("earnings_date") or ""
            if not t or not ed:
                continue
            key = (t, ed)
            if key in seen:
                continue
            seen.add(key)
            events.append(ev)
    return {
        "metadata": {
            "version": "v1",
            "as_of_date": as_of_date.isoformat(),
            "generated_at": datetime.now().isoformat(),
            "event_count": len(events),
            "week_starts": [
                (this_monday + timedelta(days=7 * off)).isoformat() for off in WEEK_OFFSETS
            ],
        },
        "events": events,
    }


def jsonable(v):
    if v is None:
        return None
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
        return round(v, 6)
    return v


def _surprise_pct(actual, estimate):
    """Surprise % = (actual - estimate) / |estimate|. Signed.
    None when either side is missing or estimate is zero. Uses |estimate|
    so a beat against a negative estimate still reads as positive."""
    if actual is None or estimate is None:
        return None
    try:
        a, e = float(actual), float(estimate)
    except (TypeError, ValueError):
        return None
    if math.isnan(a) or math.isnan(e) or e == 0:
        return None
    return (a - e) / abs(e)


# Curated ticker -> fiscal-year naming offset (see tools/fiscal_calendar.py).
# Loaded once; empty when the config is absent so the build is unaffected.
_FY_NAMING = load_fiscal_year_naming()


def _corrected_fiscal_year(symbol, fiscal_year, source):
    """Vendor labels (Finnhub) name a fiscal year by its END year; some
    retailers name it by the START year. Apply the curated per-ticker offset,
    but only to vendor-end-year rows (source contains 'finnhub'). DoltHub-only
    rows use a different (calendar-ordinal) labeling and are left untouched."""
    if fiscal_year is None:
        return None
    if not (source and "finnhub" in str(source).lower()):
        return fiscal_year
    try:
        return display_fiscal_year(symbol, int(fiscal_year), _FY_NAMING)
    except (TypeError, ValueError):
        return fiscal_year


def _quarter_label(fiscal_year, fiscal_q, fallback_date):
    """Render 'Q3 24' from a (already naming-corrected) fiscal_year/fiscal_q.
    Falls back to a calendar-month inference when either piece is missing —
    correct for ~70% of names, wrong for non-calendar-year filers, but
    no worse than the current frontend default."""
    fy = fiscal_year
    fq = (fiscal_q or "").upper() if isinstance(fiscal_q, str) else None
    if not fq or fq not in {"Q1", "Q2", "Q3", "Q4"}:
        month = fallback_date.month
        fq = f"Q{(month - 1) // 3 + 1}"
    if fy is None:
        fy = fallback_date.year
    try:
        yy = str(int(fy) % 100).zfill(2)
    except (TypeError, ValueError):
        yy = str(fallback_date.year % 100).zfill(2)
    return f"{fq} {yy}"


def _history_row(d, timing, fiscal_year, fiscal_q, eps_actual, eps_estimate,
                 rev_actual, rev_estimate, actual_move, symbol=None, source=None):
    """Shape one earnings_history entry. Implied is null until historical
    option-chain data is wired; everything else is best-effort from the
    Finnhub-overlaid earnings_events table."""
    fy = _corrected_fiscal_year(symbol, fiscal_year, source)
    return {
        "date": d.isoformat(),
        "timing": timing,
        "q": _quarter_label(fy, fiscal_q, d),
        "fiscal_year": int(fy) if fy is not None else None,
        "fiscal_q": (fiscal_q or "").upper() or None,
        # Signed close-to-close realized move (e.g. +0.034 = +3.4%).
        # NULL when OHLCV doesn't bracket the event.
        "actual": jsonable(actual_move),
        # At-the-time implied move. Populated once historical
        # option-chain data is wired into the build; until then
        # the chart shows only the actual dots.
        "implied": None,
        "eps_actual": jsonable(eps_actual),
        "eps_estimate": jsonable(eps_estimate),
        "eps_surprise_pct": jsonable(_surprise_pct(eps_actual, eps_estimate)),
        "revenue_actual": jsonable(rev_actual),
        "revenue_estimate": jsonable(rev_estimate),
        "rev_surprise_pct": jsonable(_surprise_pct(rev_actual, rev_estimate)),
    }


def build_symbol_detail(conn, ticker: str, as_of_date: date, earnings_dt: date | None,
                        ml_lookup: dict[tuple[str, str], dict] | None = None,
                        provider_lookup: dict[str, dict] | None = None) -> dict | None:
    """Build per-symbol detail: implied moves across expiries + term structure."""
    expiries = conn.execute(
        """
        SELECT DISTINCT expiry_date
        FROM v_options_chain
        WHERE ticker = ? AND as_of_date = ?
          AND expiry_date > ?
          AND expiry_date <= ?
        ORDER BY expiry_date
        LIMIT 6
        """,
        [ticker, as_of_date, as_of_date, as_of_date + timedelta(days=120)],
    ).fetchall()

    if not expiries:
        return None

    # Spot estimate from ATM call with delta ~ 0.5 on nearest expiry
    spot_row = conn.execute(
        """
        SELECT strike
        FROM v_options_chain
        WHERE ticker = ? AND as_of_date = ?
          AND expiry_date = ?
          AND call_put = 'C' AND delta IS NOT NULL
          AND delta BETWEEN 0.3 AND 0.7
        ORDER BY ABS(delta - 0.5)
        LIMIT 1
        """,
        [ticker, as_of_date, expiries[0][0]],
    ).fetchone()
    spot = float(spot_row[0]) if spot_row else None

    straddles = []
    for (exp,) in expiries:
        atm_row = conn.execute(
            """
            SELECT strike
            FROM v_options_chain
            WHERE ticker = ? AND as_of_date = ? AND expiry_date = ?
              AND call_put = 'C' AND delta IS NOT NULL
              AND delta BETWEEN 0.3 AND 0.7
            ORDER BY ABS(delta - 0.5)
            LIMIT 1
            """,
            [ticker, as_of_date, exp],
        ).fetchone()
        if not atm_row:
            continue
        atm_strike = float(atm_row[0])

        pair = conn.execute(
            """
            SELECT call_put, mid_price, iv, delta, gamma, vega, theta
            FROM v_options_chain
            WHERE ticker = ? AND as_of_date = ? AND expiry_date = ? AND strike = ?
              AND call_put IN ('C','P')
            """,
            [ticker, as_of_date, exp, atm_strike],
        ).fetchall()
        if len(pair) != 2:
            continue
        call = next((r for r in pair if r[0] == "C"), None)
        put = next((r for r in pair if r[0] == "P"), None)
        if not call or not put:
            continue

        call_mid, call_iv, call_delta, call_gamma, call_vega, call_theta = (
            call[1], call[2], call[3], call[4], call[5], call[6]
        )
        put_mid, put_iv = put[1], put[2]

        dte = (exp - as_of_date).days
        straddle_mid = (call_mid or 0) + (put_mid or 0)
        atm_iv = (call_iv + put_iv) / 2.0 if call_iv and put_iv else None
        em_iv_abs = (spot * atm_iv * math.sqrt(dte / 365.0)) if (spot and atm_iv and dte > 0) else None

        straddles.append({
            "expiration": exp.isoformat(),
            "dte": dte,
            "atm_strike": atm_strike,
            "atm_iv": jsonable(atm_iv),
            "atm_call_iv": jsonable(call_iv),
            "atm_put_iv": jsonable(put_iv),
            "straddle_mid": jsonable(straddle_mid),
            "em_straddle": jsonable(straddle_mid),
            "em_straddle_pct": jsonable(straddle_mid / spot) if spot else None,
            "em_iv": jsonable(em_iv_abs),
            "em_iv_pct": jsonable((em_iv_abs / spot) if (em_iv_abs and spot) else None),
            "call_delta": jsonable(call_delta),
            "call_gamma": jsonable(call_gamma),
            "call_vega": jsonable(call_vega),
            "call_theta": jsonable(call_theta),
        })

    if not straddles:
        return None

    # Earnings history (last 12 events) with signed close-to-close realized
    # moves where OHLCV coverage permits. The LEFT JOIN ensures we still
    # return history rows when v_ohlcv can't bracket the event — those
    # rows just have actual=NULL and the chart's dot won't render. The
    # window is ±5 calendar days so weekends/holidays don't drop events.
    #
    # Bracket is timing-aware (Finnhub-grade timing once overlay has run):
    #   BMO  → pre = last close BEFORE earnings_dt, post = close ON OR AFTER earnings_dt
    #   AMC  → pre = close ON OR BEFORE earnings_dt, post = first close AFTER earnings_dt
    #   DMH / unknown → symmetric (pre <, post >) — original behavior.
    # ROW_NUMBER picks the latest pre and earliest post per earnings_dt
    # (NULLS LAST keeps the bare row if no OHLCV exists).
    #
    # Also pulls EPS/revenue actual+estimate (populated by the Finnhub
    # overlay) so the historical chart can color realized-move dots by
    # surprise and surface fundamentals in the tooltip.
    try:
        history = conn.execute(
            """
            SELECT
                e.earnings_dt,
                e.timing,
                e.fiscal_year,
                e.fiscal_q,
                e.eps_actual,
                e.eps_estimate,
                e.revenue_actual,
                e.revenue_estimate,
                (post.close / NULLIF(pre.close, 0) - 1.0) AS actual,
                e.source
            FROM earnings_events e
            LEFT JOIN v_ohlcv pre  ON pre.act_symbol = e.ticker
                AND pre.date >= e.earnings_dt - INTERVAL '5' DAY
                AND (
                    ((LOWER(COALESCE(e.timing, 'unknown')) IN ('after_market_close', 'amc', 'after_close')
                        OR LOWER(COALESCE(e.timing, 'unknown')) LIKE '%after%')
                        AND pre.date <= e.earnings_dt)
                    OR (NOT (LOWER(COALESCE(e.timing, 'unknown')) IN ('after_market_close', 'amc', 'after_close')
                        OR LOWER(COALESCE(e.timing, 'unknown')) LIKE '%after%')
                        AND pre.date < e.earnings_dt)
                )
            LEFT JOIN v_ohlcv post ON post.act_symbol = e.ticker
                AND post.date <= e.earnings_dt + INTERVAL '5' DAY
                AND (
                    ((LOWER(COALESCE(e.timing, 'unknown')) IN ('before_market_open', 'bmo', 'before_open')
                        OR LOWER(COALESCE(e.timing, 'unknown')) LIKE '%before%')
                        AND post.date >= e.earnings_dt)
                    OR (NOT (LOWER(COALESCE(e.timing, 'unknown')) IN ('before_market_open', 'bmo', 'before_open')
                        OR LOWER(COALESCE(e.timing, 'unknown')) LIKE '%before%')
                        AND post.date > e.earnings_dt)
                )
            WHERE e.ticker = ?
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY e.earnings_dt
                ORDER BY pre.date DESC NULLS LAST, post.date ASC NULLS LAST
            ) = 1
            ORDER BY e.earnings_dt DESC
            LIMIT 12
            """,
            [ticker],
        ).fetchall()
    except Exception:
        # v_ohlcv may not exist for this build; fall back to bare history.
        history = [
            (d, t, fy, fq, epa, epe, rva, rve, None, src)
            for d, t, fy, fq, epa, epe, rva, rve, src in conn.execute(
                """
                SELECT earnings_dt, timing, fiscal_year, fiscal_q,
                       eps_actual, eps_estimate, revenue_actual, revenue_estimate, source
                FROM earnings_events
                WHERE ticker = ?
                ORDER BY earnings_dt DESC
                LIMIT 12
                """,
                [ticker],
            ).fetchall()
        ]

    # Use the closest post-earnings expiry as the headline expected move
    em = None
    if earnings_dt:
        em_math_data = compute_em_math(conn, ticker, as_of_date, earnings_dt)
        if em_math_data:
            em = {
                "earnings_date": earnings_dt.isoformat(),
                "expiration": em_math_data["expiry_date"],
                "dte": em_math_data["days_to_expiry"],
                "lead_time_days": em_math_data["lead_time_days"],
                "atm_strike": jsonable(em_math_data["atm_strike"]),
                "atm_iv": jsonable(em_math_data["avg_iv"]),
                "straddle_abs": jsonable(em_math_data["straddle_price"]),
                "straddle_pct": jsonable(em_math_data["em_baseline_straddle"]),
                "iv_pct": jsonable(em_math_data["em_baseline_iv"]),
                "skew_atm": jsonable(em_math_data.get("skew_atm")),
                "term_slope": jsonable(em_math_data.get("term_slope")),
                "total_vega": jsonable(em_math_data.get("total_vega")),
            }
            fc = (ml_lookup or {}).get((ticker, earnings_dt.isoformat()))
            ml = ml_fields(fc)
            if ml:
                em.update(ml)
                em["em_method"] = "ml_lightgbm"
            else:
                em["em_method"] = "options_math"

    # Fall back to nearest expiry for summary if no earnings event
    if em is None and straddles:
        near = straddles[0]
        em = {
            "expiration": near["expiration"],
            "dte": near["dte"],
            "atm_strike": near["atm_strike"],
            "atm_iv": near["atm_iv"],
            "straddle_abs": near["em_straddle"],
            "straddle_pct": near["em_straddle_pct"],
            "iv_pct": near["em_iv_pct"],
        }

    # Pull volatility-regime snapshot for the current as-of date. May not exist
    # for every ticker (coverage depends on volatility_history parquet).
    vol_regime = None
    try:
        vh = conn.execute(
            """
            SELECT iv_current, iv_year_high, iv_year_low,
                   hv_current, hv_year_high, hv_year_low,
                   iv_week_ago, iv_month_ago
            FROM v_volhist
            WHERE act_symbol = ? AND date = ?
            LIMIT 1
            """,
            [ticker, as_of_date],
        ).fetchone()
        if vh:
            iv_c, iv_hi, iv_lo, hv_c, hv_hi, hv_lo, iv_wk, iv_mo = vh
            def _rank(cur, hi, lo):
                if cur is None or hi is None or lo is None or hi <= lo:
                    return None
                return max(0.0, min(1.0, (cur - lo) / (hi - lo)))
            vol_regime = {
                "iv_current": jsonable(iv_c),
                "iv_rank":    jsonable(_rank(iv_c, iv_hi, iv_lo)),
                "iv_year_high": jsonable(iv_hi),
                "iv_year_low":  jsonable(iv_lo),
                "hv_current": jsonable(hv_c),
                "hv_rank":    jsonable(_rank(hv_c, hv_hi, hv_lo)),
                "iv_mom_week":  jsonable((iv_c - iv_wk) if (iv_c is not None and iv_wk is not None) else None),
                "iv_mom_month": jsonable((iv_c - iv_mo) if (iv_c is not None and iv_mo is not None) else None),
            }
    except Exception:
        # v_volhist may not exist if volatility_history parquet hasn't been synced yet
        pass

    provider_fields = provider_event_fields((provider_lookup or {}).get(ticker))
    return {
        "symbol": ticker,
        "as_of_date": as_of_date.isoformat(),
        "spot_price": jsonable(spot),
        "expected_move": em,
        "straddle_features": straddles,
        "earnings_history": [
            _history_row(d, t, fy, fq, epa, epe, rva, rve, a, symbol=ticker, source=src)
            for d, t, fy, fq, epa, epe, rva, rve, a, src in history
        ],
        "next_earnings": earnings_dt.isoformat() if earnings_dt else None,
        "vol_regime": vol_regime,
        **provider_fields,
    }


def screener_extras(conn, ticker: str, earnings_dt: date, as_of_date: date) -> dict:
    """Extra per-event fields the screener needs but the standard event payload
    doesn't carry: IV rank, last-4-quarter realized average, IV crush proxy.

    All three are best-effort — return None for any field that can't be
    computed. The screener degrades gracefully on missing fields.
    """
    extras: dict = {
        "iv_rank": None,
        "hist_move_avg_4q": None,
        "iv_crush_pct": None,
    }

    # IV rank from v_volhist
    try:
        vh = conn.execute(
            """
            SELECT iv_current, iv_year_high, iv_year_low
            FROM v_volhist
            WHERE act_symbol = ? AND date = ?
            LIMIT 1
            """,
            [ticker, as_of_date],
        ).fetchone()
        if vh:
            cur, hi, lo = vh
            if cur is not None and hi is not None and lo is not None and hi > lo:
                extras["iv_rank"] = jsonable(max(0.0, min(1.0, (cur - lo) / (hi - lo))))
    except Exception:
        pass

    # Hist average realized move — last 4 earnings × OHLCV close-to-close.
    # Uses the canonical `earnings_events` table (built by build_earnings_events_table)
    # joined to v_ohlcv. Both must exist for this to produce a value; the
    # try/except suppresses errors when either is missing.
    try:
        rows = conn.execute(
            """
            SELECT ABS(post.close / NULLIF(pre.close, 0) - 1.0) AS realized
            FROM earnings_events e
            JOIN v_ohlcv pre  ON pre.act_symbol = e.ticker
                AND pre.date >= e.earnings_dt - INTERVAL '5' DAY
                AND (
                    ((LOWER(COALESCE(e.timing, 'unknown')) IN ('after_market_close', 'amc', 'after_close')
                        OR LOWER(COALESCE(e.timing, 'unknown')) LIKE '%after%')
                        AND pre.date <= e.earnings_dt)
                    OR (NOT (LOWER(COALESCE(e.timing, 'unknown')) IN ('after_market_close', 'amc', 'after_close')
                        OR LOWER(COALESCE(e.timing, 'unknown')) LIKE '%after%')
                        AND pre.date < e.earnings_dt)
                )
            JOIN v_ohlcv post ON post.act_symbol = e.ticker
                AND post.date <= e.earnings_dt + INTERVAL '5' DAY
                AND (
                    ((LOWER(COALESCE(e.timing, 'unknown')) IN ('before_market_open', 'bmo', 'before_open')
                        OR LOWER(COALESCE(e.timing, 'unknown')) LIKE '%before%')
                        AND post.date >= e.earnings_dt)
                    OR (NOT (LOWER(COALESCE(e.timing, 'unknown')) IN ('before_market_open', 'bmo', 'before_open')
                        OR LOWER(COALESCE(e.timing, 'unknown')) LIKE '%before%')
                        AND post.date > e.earnings_dt)
                )
            WHERE e.ticker = ? AND e.earnings_dt < ? AND pre.close > 0 AND post.close > 0
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY e.earnings_dt ORDER BY pre.date DESC, post.date ASC
            ) = 1
            ORDER BY e.earnings_dt DESC LIMIT 4
            """,
            [ticker, earnings_dt],
        ).fetchall()
        if rows:
            vals = [r[0] for r in rows if r[0] is not None]
            if vals:
                extras["hist_move_avg_4q"] = jsonable(sum(vals) / len(vals))
    except Exception as e:
        print(f"  ⚠ hist_move_avg_4q failed for {ticker}: {e}", flush=True)

    # IV crush proxy — front ATM IV vs the next-out expiry's ATM IV.
    # Already computed in v_straddle_features as iv_crush_pct upstream;
    # we recompute here from raw options to avoid coupling to that view.
    try:
        front = conn.execute(
            """
            SELECT expiry_date, iv FROM v_options_chain
            WHERE ticker = ? AND as_of_date = ? AND call_put = 'C'
              AND iv IS NOT NULL AND iv > 0
              AND expiry_date >= ?
            ORDER BY expiry_date ASC, ABS(delta - 0.5) ASC
            LIMIT 1
            """,
            [ticker, as_of_date, earnings_dt],
        ).fetchone()
        if front:
            front_expiry = front[0]
            front_iv = float(front[1])
            back = conn.execute(
                """
                SELECT iv FROM v_options_chain
                WHERE ticker = ? AND as_of_date = ? AND call_put = 'C'
                  AND iv IS NOT NULL AND iv > 0
                  AND expiry_date > ?
                ORDER BY expiry_date ASC, ABS(delta - 0.5) ASC
                LIMIT 1
                """,
                [ticker, as_of_date, front_expiry],
            ).fetchone()
            if back:
                back_iv = float(back[0])
                if front_iv > 0:
                    extras["iv_crush_pct"] = jsonable((front_iv - back_iv) / front_iv)
    except Exception:
        pass

    return extras


def collapse_duplicate_earnings(conn, start: date, end: date):
    """Pick one canonical earnings date per ticker across the forward window, so
    a ticker whose estimated date was revised (e.g. PLUS 2026-05-20 → 05-27 →
    05-28) shows on a single day instead of two or three.

    Partitions by ticker only (NOT ticker+fiscal_q): a revised estimate can be
    tagged with a different/blank fiscal quarter (e.g. M's 05-26 vs 06-03), so
    keying on the quarter would fail to merge them. Within this ~25-day window a
    ticker has at most one real print, so per-ticker collapse is safe.

    Rule (matches the confirmed report date 5/5 on known cases):
      1. prefer the row that already has reported actuals (the confirmed date),
      2. otherwise the latest date (the most recent revision).
    Returns (keep, dropped): keep is the set of (ticker, iso_date) to retain;
    dropped is [(ticker, dropped_iso, kept_iso)] for the build log.
    """
    rows = conn.execute(
        """
        WITH base AS (
            SELECT ticker, earnings_dt, eps_actual, revenue_actual
            FROM earnings_events
            WHERE earnings_dt BETWEEN ? AND ?
        ),
        ranked AS (
            SELECT ticker, earnings_dt,
                ROW_NUMBER() OVER (
                    PARTITION BY ticker
                    ORDER BY
                        ((NULLIF(CAST(eps_actual AS VARCHAR), '') IS NOT NULL)
                          OR (NULLIF(CAST(revenue_actual AS VARCHAR), '') IS NOT NULL)) DESC,
                        earnings_dt DESC
                ) AS rn
            FROM base
        )
        SELECT ticker, earnings_dt, rn FROM ranked
        """,
        [start, end],
    ).fetchall()
    keep: set[tuple[str, str]] = set()
    kept_iso: dict[str, str] = {}
    for ticker, earnings_dt, rn in rows:
        if rn == 1:
            keep.add((ticker, earnings_dt.isoformat()))
            kept_iso[ticker] = earnings_dt.isoformat()
    dropped = [
        (ticker, earnings_dt.isoformat(), kept_iso.get(ticker, "?"))
        for ticker, earnings_dt, rn in rows
        if rn != 1
    ]
    return keep, dropped


def build_week_events(conn, as_of_date: date, week_start: date, week_end: date,
                      ml_lookup: dict[tuple[str, str], dict],
                      provider_lookup: dict[str, dict] | None = None,
                      require_ml: bool = True,
                      canonical: set[tuple[str, str]] | None = None) -> list[dict]:
    # realized_move: signed regular-session close-to-close move ACROSS the
    # print, for events already reported. Same timing-aware bracket as
    # build_symbol_detail (BMO → prev close→report-day close; AMC → report-day
    # close→next close). Regular-session OHLCV only, so it excludes the
    # pre/after-hours IEX prints the live quote can include. NULL for upcoming
    # events (no post close yet) — the calendar then shows the live tick.
    try:
        rows = conn.execute(
            """
            SELECT e.ticker, e.earnings_dt, e.timing, e.fiscal_q,
                   e.eps_actual, e.eps_estimate, e.revenue_actual, e.revenue_estimate,
                   (post.close / NULLIF(pre.close, 0) - 1.0) AS realized_move
            FROM earnings_events e
            LEFT JOIN v_ohlcv pre  ON pre.act_symbol = e.ticker
                AND pre.date >= e.earnings_dt - INTERVAL '5' DAY
                AND (
                    ((LOWER(COALESCE(e.timing, 'unknown')) IN ('after_market_close', 'amc', 'after_close')
                        OR LOWER(COALESCE(e.timing, 'unknown')) LIKE '%after%')
                        AND pre.date <= e.earnings_dt)
                    OR (NOT (LOWER(COALESCE(e.timing, 'unknown')) IN ('after_market_close', 'amc', 'after_close')
                        OR LOWER(COALESCE(e.timing, 'unknown')) LIKE '%after%')
                        AND pre.date < e.earnings_dt)
                )
            LEFT JOIN v_ohlcv post ON post.act_symbol = e.ticker
                AND post.date <= e.earnings_dt + INTERVAL '5' DAY
                AND (
                    ((LOWER(COALESCE(e.timing, 'unknown')) IN ('before_market_open', 'bmo', 'before_open')
                        OR LOWER(COALESCE(e.timing, 'unknown')) LIKE '%before%')
                        AND post.date >= e.earnings_dt)
                    OR (NOT (LOWER(COALESCE(e.timing, 'unknown')) IN ('before_market_open', 'bmo', 'before_open')
                        OR LOWER(COALESCE(e.timing, 'unknown')) LIKE '%before%')
                        AND post.date > e.earnings_dt)
                )
            WHERE e.earnings_dt BETWEEN ? AND ?
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY e.ticker, e.earnings_dt
                ORDER BY pre.date DESC NULLS LAST, post.date ASC NULLS LAST
            ) = 1
            ORDER BY e.earnings_dt, e.ticker
            """,
            [week_start, week_end],
        ).fetchall()
    except Exception:
        # v_ohlcv absent (e.g. options-only build) — fall back without moves.
        rows = [
            (*r, None) for r in conn.execute(
                """
                SELECT ticker, earnings_dt, timing, fiscal_q,
                       eps_actual, eps_estimate, revenue_actual, revenue_estimate
                FROM earnings_events
                WHERE earnings_dt BETWEEN ? AND ?
                ORDER BY earnings_dt, ticker
                """,
                [week_start, week_end],
            ).fetchall()
        ]

    events = []
    skipped_no_ml = 0
    skipped_no_options = 0
    skipped_dupe = 0
    today = date.today()
    for i, (
        ticker,
        earnings_dt,
        timing,
        fiscal_q,
        eps_actual,
        eps_estimate,
        revenue_actual,
        revenue_estimate,
        realized_move,
    ) in enumerate(rows, 1):
        # Drop revised/duplicate dates: keep only the canonical date chosen per
        # (ticker, fiscal quarter) by collapse_duplicate_earnings.
        if canonical is not None and (ticker, earnings_dt.isoformat()) not in canonical:
            skipped_dupe += 1
            continue
        fc = ml_lookup.get((ticker, earnings_dt.isoformat()))
        # Require ML only for *future* earnings. Past earnings within the same
        # week (e.g. Mon/Tue when today is Wed) fall back to options_math —
        # daily_score.py only scores upcoming events, so forecasts are absent
        # for past dates by design.
        if require_ml and not fc and earnings_dt >= today:
            skipped_no_ml += 1
            continue
        em = compute_em_math(conn, ticker, as_of_date, earnings_dt)
        if not em:
            skipped_no_options += 1
            continue
        if i % 25 == 0 or i == len(rows):
            print(f"    event {i}/{len(rows)}: {ticker} {earnings_dt}", flush=True)
        ml = ml_fields(fc)
        extras = screener_extras(conn, ticker, earnings_dt, as_of_date)
        provider_fields = provider_event_fields((provider_lookup or {}).get(str(ticker).upper()))
        event = {
            "ticker": ticker,
            "earnings_date": earnings_dt.isoformat(),
            "timing": timing or "unknown",
            "fiscal_q": fiscal_q,
            "eps_actual": jsonable(eps_actual),
            "eps_estimate": jsonable(eps_estimate),
            "revenue_actual": jsonable(revenue_actual),
            "revenue_estimate": jsonable(revenue_estimate),
            # Signed regular-session close-to-close move across the print, for
            # already-reported events. The calendar shows this (clearly marked
            # as the earnings-day reaction) instead of the live tick once a
            # reporter's date has passed. NULL for upcoming events.
            "realized_move_pct": jsonable(realized_move),
            "as_of_date": as_of_date.isoformat(),
            "spot_price": jsonable(em["estimated_spot"]),
            "atm_strike": jsonable(em["atm_strike"]),
            "atm_iv": jsonable(em["avg_iv"]),
            "em_straddle_pct": jsonable(em["em_baseline_straddle"]),
            "em_iv_pct": jsonable(em["em_baseline_iv"]),
            "em_straddle_abs": jsonable(em["straddle_price"]),
            "expiry_date": em["expiry_date"],
            "days_to_expiry": em["days_to_expiry"],
            "lead_time_days": em["lead_time_days"],
            "skew_atm": jsonable(em.get("skew_atm")),
            "term_slope": jsonable(em.get("term_slope")),
            "em_method": "ml_lightgbm" if ml else "options_math",
            "confidence": "high" if ml else "high",
            **extras,
            **provider_fields,
            **ml,
        }
        events.append(event)
    events.sort(key=lambda e: (e["earnings_date"], e["ticker"]))
    if skipped_no_ml or skipped_no_options or skipped_dupe:
        print(
            f"    skipped: {skipped_no_ml} (no ML forecast), "
            f"{skipped_no_options} (no options data), "
            f"{skipped_dupe} (duplicate revised date)",
            flush=True,
        )
    return events


def main():
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--resume",
        action="store_true",
        help=(
            "Skip symbol detail files that were written AFTER weekly.json "
            "in this run. Use when recovering from a crash mid-loop so you "
            "don't re-do hundreds of completed tickers."
        ),
    )
    ap.add_argument(
        "--skip-weeks",
        action="store_true",
        help="Skip the weeks rebuild entirely — jump straight into symbol details. "
             "Implied by --resume.",
    )
    ap.add_argument(
        "--twelvedata-dry-run",
        action="store_true",
        help=(
            "Print TwelveData realized-move fallback candidates, estimated credits, "
            "remaining daily quota, and skipped events without calling TwelveData."
        ),
    )
    args = ap.parse_args()

    (PUBLIC_DIR / "symbols").mkdir(parents=True, exist_ok=True)
    (PUBLIC_DIR / "weeks").mkdir(parents=True, exist_ok=True)
    publish_forecast_evidence()

    conn = duckdb.connect()
    # Cap DuckDB memory so long-running symbol-detail loops don't OOM-segfault.
    # Spills to disk if the working set exceeds this.
    conn.execute("PRAGMA memory_limit='4GB'")
    conn.execute("PRAGMA threads=4")
    build_earnings_events_table(conn, EARNINGS_CSV)
    create_duckdb_views(conn, DATA_DIR)
    ml_lookup = load_ml_forecasts()
    provider_lookup = load_provider_enrichments()

    as_of_row = conn.execute("SELECT MAX(as_of_date) FROM v_options_chain").fetchone()
    as_of_date = as_of_row[0]
    if as_of_date is None:
        print("❌ No options data available")
        sys.exit(1)

    # Staleness guardrail. CI's daily-refresh pulls fresh parquet from R2
    # before building, so as_of_date should be within a few days of today.
    # Running this script with weeks-old local parquet produces empty
    # upcoming-week JSONs and silently regresses production data. Bail
    # loudly instead. Set ALLOW_STALE_OPTIONS=1 to override (e.g. for
    # offline development or testing historical snapshots).
    age_days = (date.today() - as_of_date).days
    stale_threshold = int(os.getenv("STALE_OPTIONS_MAX_DAYS", "7"))
    if age_days > stale_threshold and not os.getenv("ALLOW_STALE_OPTIONS"):
        print(
            f"❌ Options chain is {age_days} days stale "
            f"(as_of={as_of_date}, today={date.today()}, "
            f"threshold={stale_threshold}d).\n"
            "   Run `bash scripts/r2_pull.sh` to refresh local parquet, "
            "or set ALLOW_STALE_OPTIONS=1 to override.\n"
            "   Building with stale data produces empty upcoming-week "
            "JSONs and regresses production. Aborting.",
            file=sys.stderr,
        )
        sys.exit(2)
    if age_days > 1:
        print(f"⚠ Options chain is {age_days} days old (as_of={as_of_date})")

    today = date.today()
    this_monday = monday_of_week(today) if today.weekday() < 5 else today + timedelta(days=(7 - today.weekday()))

    # Build each week (last, this, next, +2) and collect unique tickers for detail pages.
    # On --resume: reuse the existing per-week JSONs on disk — don't rebuild them.
    skip_weeks = args.skip_weeks or args.resume
    week_payloads = {}
    tickers_needing_detail: dict[str, date] = {}

    # Collapse revised/duplicate forward earnings dates to one per ticker across
    # the whole window so weekly/weeks/screener stay consistent (a revised
    # estimate like PLUS 5/20→5/27→5/28 otherwise shows 2-3 times). Prefer the
    # row with reported actuals (the confirmed date), else the latest revision.
    span_start = this_monday + timedelta(days=7 * min(WEEK_OFFSETS))
    span_end = this_monday + timedelta(days=7 * max(WEEK_OFFSETS) + 4)
    canonical_keys, dropped_dupes = collapse_duplicate_earnings(conn, span_start, span_end)
    if dropped_dupes:
        print(
            f"  Earnings dedup: collapsed {len(dropped_dupes)} duplicate date(s) "
            "to one per ticker (actuals first, else latest):"
        )
        for tk, dropped_iso, kept in sorted(dropped_dupes):
            print(f"    {tk}: dropped {dropped_iso} → kept {kept}")

    if skip_weeks:
        print("⏭️  --resume/--skip-weeks: reading existing weeks/*.json from disk", flush=True)
        for offset in WEEK_OFFSETS:
            wk_start = this_monday + timedelta(days=7 * offset)
            wk_path = PUBLIC_DIR / "weeks" / f"{wk_start.isoformat()}.json"
            if not wk_path.exists():
                print(f"  ❌ missing {wk_path.name} — run without --resume to rebuild it")
                sys.exit(1)
            payload = json.loads(wk_path.read_text())
            week_payloads[wk_start] = payload
            events = payload.get("events", [])
            print(f"📅 week {wk_start} (offset {offset:+d}) → {len(events)} events (cached)")
            for ev in events:
                tickers_needing_detail.setdefault(ev["ticker"], date.fromisoformat(ev["earnings_date"]))

    for offset in ([] if skip_weeks else WEEK_OFFSETS):
        wk_start = this_monday + timedelta(days=7 * offset)
        wk_end = wk_start + timedelta(days=4)
        # daily_score.py only scores upcoming earnings within a ~21-day window,
        # so require_ml would leave last/+2 weeks empty. Fall back to the
        # options_math baseline for those; enforce ML on current + next week.
        require_ml = offset in (0, 1)
        events = build_week_events(conn, as_of_date, wk_start, wk_end, ml_lookup, provider_lookup,
                                   require_ml=require_ml, canonical=canonical_keys)

        # Past-week preservation: once a week is fully in the past, the
        # rebuild loses events whose options chains have rolled off
        # (compute_em_math returns None for past-dated expiries). Friday's
        # 45-event bundle for May 11-15 shrank to 8 events by Monday for
        # this reason. Merge new events with the prior committed bundle —
        # new entries win on (ticker, date) collisions so any post-hoc
        # data update (e.g. an EPS actual landing late) replaces the
        # stale entry, but events with expired options are preserved
        # from the original "when this week was current" build.
        if wk_end < today:
            wk_path = PUBLIC_DIR / "weeks" / f"{wk_start.isoformat()}.json"
            if wk_path.exists():
                try:
                    prior = json.loads(wk_path.read_text())
                    prior_events = prior.get("events", [])
                    new_keys = {(e["ticker"], e["earnings_date"]) for e in events}
                    preserved = [
                        e for e in prior_events
                        if (e["ticker"], e["earnings_date"]) not in new_keys
                        # Don't resurrect a date the dedup just collapsed away:
                        # a revised estimate (e.g. WSM 05-20/28 superseded by
                        # 05-21) would otherwise be re-preserved here and
                        # reintroduce the duplicate. Keep only canonical keys.
                        and (canonical_keys is None
                             or (e["ticker"], e["earnings_date"]) in canonical_keys)
                    ]
                    if preserved:
                        print(
                            f"    preserving {len(preserved)} events from prior bundle "
                            f"(options data expired since first build)"
                        )
                        events.extend(preserved)
                        events.sort(key=lambda e: (e["earnings_date"], e["ticker"]))
                except (json.JSONDecodeError, OSError) as exc:
                    print(f"    ⚠ could not read prior bundle: {exc}")

        ohlcv_realized = enrich_realized_moves_from_ohlcv(conn, events)
        twelve_realized = enrich_realized_moves_from_twelvedata(
            events,
            dry_run=args.twelvedata_dry_run,
            label=f"week {wk_start.isoformat()}",
        )
        if ohlcv_realized or twelve_realized:
            print(
                f"    realized moves updated: {ohlcv_realized} from OHLCV"
                + (f", {twelve_realized} from TwelveData" if twelve_realized else ""),
                flush=True,
            )

        print(f"📅 week {wk_start} (offset {offset:+d}) → {len(events)} events")
        em_straddle_vals = [
            e["em_straddle_pct"] for e in events if e["em_straddle_pct"] is not None
        ]
        em_iv_vals = [
            e["em_iv_pct"] for e in events if e["em_iv_pct"] is not None
        ]
        payload = {
            "metadata": {
                "version": "v4_multi_week",
                "generated_at": datetime.now().isoformat(),
                "as_of_date": as_of_date.isoformat(),
                "method": "ATM straddle + IV baseline",
                "offset": offset,
            },
            "window": {"start": wk_start.isoformat(), "end": wk_end.isoformat()},
            "events": events,
            "summary": {
                "total_events": len(events),
                "avg_em_straddle_pct": (
                    sum(em_straddle_vals) / len(em_straddle_vals)
                    if em_straddle_vals else 0
                ),
                "avg_em_iv_pct": (
                    sum(em_iv_vals) / len(em_iv_vals)
                    if em_iv_vals else 0
                ),
            },
        }
        week_payloads[wk_start] = payload
        write_to_public(
            f"weeks/{wk_start.isoformat()}.json",
            json.dumps(payload, indent=2, default=str),
        )
        for ev in events:
            tickers_needing_detail.setdefault(ev["ticker"], date.fromisoformat(ev["earnings_date"]))

    if not skip_weeks:
        # Spend TwelveData on every realized-move gap first. Historical averages
        # only use whatever Basic-tier quota remains after all weekly rows are built.
        twelve_hist_total = 0
        for offset in WEEK_OFFSETS:
            wk_start = this_monday + timedelta(days=7 * offset)
            payload = week_payloads.get(wk_start)
            if not payload:
                continue
            twelve_hist = enrich_hist_move_avg_from_twelvedata(
                conn,
                payload.get("events", []),
                dry_run=args.twelvedata_dry_run,
                label=f"week {wk_start.isoformat()}",
            )
            if twelve_hist:
                twelve_hist_total += twelve_hist
                write_to_public(
                    f"weeks/{wk_start.isoformat()}.json",
                    json.dumps(payload, indent=2, default=str),
                )
        if twelve_hist_total:
            print(
                f"    hist avg updated: {twelve_hist_total} from TwelveData remaining quota",
                flush=True,
            )
        validation_events = [
            ev
            for offset in WEEK_OFFSETS
            for ev in week_payloads.get(
                this_monday + timedelta(days=7 * offset),
                {},
            ).get("events", [])
        ]
        validate_twelvedata_against_ohlcv(
            conn,
            validation_events,
            dry_run=args.twelvedata_dry_run,
            label="weekly calendar",
        )

    # Primary weekly.json points at the current week (back-compat for any old consumers).
    if not skip_weeks:
        write_to_public("weekly.json", json.dumps(week_payloads[this_monday], indent=2, default=str))

    # Manifest of available weeks (used by the UI to render nav state).
    if not skip_weeks:
        manifest = {
            "as_of_date": as_of_date.isoformat(),
            "current_week": this_monday.isoformat(),
            "weeks": [
                {
                    "start": d.isoformat(),
                    "end": (d + timedelta(days=4)).isoformat(),
                    "offset": offset,
                    "count": len(week_payloads[d]["events"]),
                }
                for offset, d in ((off, this_monday + timedelta(days=7 * off)) for off in WEEK_OFFSETS)
            ],
        }
        write_to_public("weeks/manifest.json", json.dumps(manifest, indent=2))

        screener = build_screener_payload(as_of_date, this_monday, week_payloads)
        write_to_public("screener.json", json.dumps(screener, indent=2, default=str))
        print(f"📊 screener.json → {screener['metadata']['event_count']} events", flush=True)

    # Generate per-symbol detail: all tickers seen in any week + a curated popular list.
    popular = [
        "AAPL", "TSLA", "NVDA", "AMZN", "MSFT", "META", "GOOGL", "JPM",
        "BAC", "GS", "NFLX", "JNJ", "UNH", "PG", "V", "MA", "IBM", "INTC",
        "T", "BA", "GE", "RTX", "AXP", "HON", "LMT", "TMO", "MCO",
    ]
    for ticker in popular:
        if ticker not in tickers_needing_detail:
            row = conn.execute(
                """
                SELECT earnings_dt FROM earnings_events
                WHERE ticker = ? AND earnings_dt >= ?
                ORDER BY earnings_dt LIMIT 1
                """,
                [ticker, as_of_date],
            ).fetchone()
            tickers_needing_detail[ticker] = row[0] if row else None

    generated = 0
    skipped_resume = 0
    total_details = len(tickers_needing_detail)
    if args.resume:
        print(
            "⏭️  --resume: skipping any ticker whose symbols/{TICKER}.json already exists. "
            "Files from previous runs stay — run without --resume for a full refresh.",
            flush=True,
        )

    print(f"📝 generating {total_details} symbol detail files", flush=True)
    # Print every ticker so a segfault leaves the exact culprit on the last
    # visible line. (Cheap — ~1000 lines over a 10-min run.)
    for i, (ticker, earn_dt) in enumerate(tickers_needing_detail.items(), 1):
        if args.resume and (PUBLIC_DIR / "symbols" / f"{ticker}.json").exists():
            skipped_resume += 1
            continue
        print(f"  [{i}/{total_details}] {ticker}", flush=True)
        # Recycle the DuckDB connection every 200 tickers to shed any
        # accumulated query-plan memory that could push us toward a segfault.
        if i > 1 and i % 200 == 0:
            conn.close()
            conn = duckdb.connect()
            conn.execute("PRAGMA memory_limit='4GB'")
            conn.execute("PRAGMA threads=4")
            build_earnings_events_table(conn, EARNINGS_CSV)
            create_duckdb_views(conn, DATA_DIR)
        try:
            detail = build_symbol_detail(conn, ticker, as_of_date, earn_dt, ml_lookup, provider_lookup)
            if not detail:
                continue
            # Attach timing (BMO/AMC/unknown) from the earnings row, surfaced on the detail page.
            if earn_dt:
                row = conn.execute(
                    "SELECT timing FROM earnings_events WHERE ticker = ? AND earnings_dt = ? LIMIT 1",
                    [ticker, earn_dt],
                ).fetchone()
                timing = (row[0] if row else None) or "unknown"
                detail["next_earnings_timing"] = timing
                if detail.get("expected_move"):
                    detail["expected_move"]["timing"] = timing
                    # em_method is already set inside build_symbol_detail based
                    # on whether an ML forecast was attached; don't overwrite.
            write_to_public(
                f"symbols/{ticker}.json",
                json.dumps(detail, indent=2, default=str),
            )
            generated += 1
        except Exception as e:
            print(f"  ⚠️  {ticker} detail: {e}")

    print(
        f"✅ {len(week_payloads)} weeks written, {generated} symbol files"
        + (f", {skipped_resume} skipped (resume)" if skipped_resume else "")
    )
    conn.close()


if __name__ == "__main__":
    main()
