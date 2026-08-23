"""Load forecast/provider inputs and publish compact evidence manifests."""

from __future__ import annotations

import json
import math

from .shared import (
    FORECAST_RECEIPT_PATH,
    FORECASTS_DIR,
    PROVIDER_ENRICHMENTS_DIR,
    PUBLIC_DIR,
    jsonable,
    write_to_public,
)

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
