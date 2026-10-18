"""Load forecast/provider inputs and publish compact evidence manifests."""

from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

from ml.provider_signal_policy import (
    DEFAULT_POLICY_PATH,
    load_provider_signal_policy,
    permitted_signals,
)

from .shared import (
    EVENT_FORECAST_ARCHIVE_PATH,
    EVENT_PREDICTION_LEDGER_PATH,
    EVENT_PREDICTION_PUBLICATIONS_PATH,
    FORECAST_RECEIPT_PATH,
    FORECASTS_DIR,
    PROVIDER_ENRICHMENTS_DIR,
    PUBLIC_DIR,
    jsonable,
    write_to_public,
)

def load_ml_forecasts(
    *, publication_held: bool = False,
    eligible_forecast_ids: set[str] | None = None,
    eligible_event_keys: set[str] | None = None,
    forecast_path: Path | None = None,
) -> dict[tuple[str, str], dict]:
    """Return the latest *eligible* point-in-time forecast per event.

    Once the immutable ledger exists it is authoritative. This prevents a
    post-deadline rescore from becoming the homepage/ticker headline merely
    because it lives in the newest dated forecast file. Legacy installations
    without a ledger retain the prior newest-file behavior until the next score
    seeds the ledger.
    """
    if publication_held:
        print('ML publication held: omitting upcoming model forecasts')
        return {}
    if eligible_forecast_ids is not None and not eligible_forecast_ids:
        return {}
    try:
        import pandas as pd  # local import — only needed when forecasts exist
    except Exception as exc:
        print(f"⚠️  Could not import pandas for forecast loading: {exc}")
        return {}

    def authenticated_rows(frame):
        if eligible_forecast_ids is None:
            return frame
        if 'forecast_id' not in frame.columns:
            return frame.iloc[0:0]
        selected = frame['forecast_id'].isin(eligible_forecast_ids)
        if eligible_event_keys is not None:
            dates = pd.to_datetime(frame['earnings_date'], errors='coerce').dt.strftime('%Y-%m-%d')
            keys = frame['act_symbol'].astype(str).str.upper().str.strip() + '|' + dates
            selected &= keys.isin(eligible_event_keys)
        return frame[selected].copy()

    if forecast_path is None and EVENT_PREDICTION_LEDGER_PATH.exists():
        try:
            df = pd.read_parquet(EVENT_PREDICTION_LEDGER_PATH)
        except Exception as exc:
            print(f"⚠️  Could not read {EVENT_PREDICTION_LEDGER_PATH.name}: {exc}")
            df = pd.DataFrame()
        df = authenticated_rows(df)
        if not df.empty and "freeze_eligible" in df.columns:
            df = df[df["freeze_eligible"].fillna(False).astype(bool)].copy()
            if not df.empty:
                df["earnings_date"] = pd.to_datetime(
                    df["earnings_date"], errors="coerce"
                ).dt.date
                df["_snapshot"] = pd.to_datetime(
                    df.get("feature_snapshot_at"), errors="coerce", utc=True
                )
                df["_scored"] = pd.to_datetime(
                    df.get("scored_at"), errors="coerce", utc=True
                )
                df = df.dropna(subset=["earnings_date"])
                df = df.sort_values(
                    ["act_symbol", "earnings_date", "_snapshot", "_scored"],
                    ascending=[True, True, False, False],
                    kind="mergesort",
                )
                df = df.drop_duplicates(
                    subset=["act_symbol", "earnings_date"], keep="first"
                )
                out: dict[tuple[str, str], dict] = {}
                for row in df.drop(columns=["_snapshot", "_scored"]).to_dict(
                    orient="records"
                ):
                    key = (
                        str(row["act_symbol"]).upper(),
                        row["earnings_date"].isoformat(),
                    )
                    out[key] = row
                print(
                    f"🤖 Loaded {len(out)} eligible ML forecasts from "
                    f"{EVENT_PREDICTION_LEDGER_PATH.name}"
                )
                return out
        if eligible_forecast_ids is not None:
            return {}

    if forecast_path is None and not FORECASTS_DIR.exists():
        return {}
    if forecast_path is None:
        files = sorted(FORECASTS_DIR.glob("forecasts_*.parquet"))
        if not files:
            return {}
        latest = files[-1]
    else:
        latest = forecast_path
    try:
        df = pd.read_parquet(latest)
    except Exception as exc:
        print(f"⚠️  Could not read {latest.name}: {exc}")
        return {}
    if df.empty:
        return {}

    df = authenticated_rows(df)
    if df.empty:
        return {}

    df = df.copy()
    df['act_symbol'] = df['act_symbol'].astype(str).str.upper().str.strip()
    if "freeze_eligible" in df.columns:
        df = df[df["freeze_eligible"].fillna(False).astype(bool)].copy()
        if df.empty:
            return {}
    df["earnings_date"] = pd.to_datetime(df["earnings_date"]).dt.date
    df["snapshot_date"] = pd.to_datetime(df["snapshot_date"]).dt.date
    df["lead_days"] = (df["earnings_date"] - df["snapshot_date"]).apply(
        lambda d: d.days
    )
    df["horizon_gap"] = (df["model_horizon"] - df["lead_days"]).abs()
    df = df.sort_values(
        ["act_symbol", "earnings_date", "horizon_gap", "snapshot_date"],
        ascending=[True, True, True, False],
    )
    df = df.drop_duplicates(subset=["act_symbol", "earnings_date"], keep="first")

    out: dict[tuple[str, str], dict] = {}
    for row in df.to_dict(orient="records"):
        key = (row["act_symbol"], row["earnings_date"].isoformat())
        out[key] = row
    print(f"🤖 Loaded {len(out)} ML forecasts from {latest.name}")
    return out


def load_event_forecast_archive(*, forecasts_dir: Path | None = None) -> dict[tuple[str, str], dict]:
    """Return the durable final pre-event ML forecast for each earnings event."""
    path = (forecasts_dir / EVENT_FORECAST_ARCHIVE_PATH.name
            if forecasts_dir is not None else EVENT_FORECAST_ARCHIVE_PATH)
    publications_path = (forecasts_dir / EVENT_PREDICTION_PUBLICATIONS_PATH.name
                         if forecasts_dir is not None else EVENT_PREDICTION_PUBLICATIONS_PATH)
    if not path.exists():
        return {}
    try:
        import pandas as pd
        frame = pd.read_parquet(path)
    except Exception as exc:
        print(f"⚠️  Could not read {path.name}: {exc}")
        return {}
    if frame.empty:
        return {}

    required = {"act_symbol", "earnings_date", "snapshot_date"}
    if not required <= set(frame.columns):
        print(
            f"⚠️  Ignoring {path.name}; missing columns "
            f"{sorted(required - set(frame.columns))}"
        )
        return {}

    frame = frame.copy()
    frame["earnings_date"] = pd.to_datetime(
        frame["earnings_date"], errors="coerce"
    ).dt.date
    frame["snapshot_date"] = pd.to_datetime(
        frame["snapshot_date"], errors="coerce"
    ).dt.date
    frame = frame.dropna(subset=["act_symbol", "earnings_date", "snapshot_date"])
    frame["act_symbol"] = frame["act_symbol"].astype(str).str.upper().str.strip()

    if publications_path.exists() and "forecast_id" in frame.columns:
        try:
            publications = pd.read_parquet(publications_path)
        except Exception as exc:
            print(
                f"⚠️  Could not read {EVENT_PREDICTION_PUBLICATIONS_PATH.name}: {exc}"
            )
        else:
            if {"forecast_id", "published_at"} <= set(publications.columns):
                publications = publications.drop_duplicates(
                    subset=["forecast_id"], keep="first"
                )
                frame = frame.merge(
                    publications[["forecast_id", "published_at"]],
                    on="forecast_id",
                    how="left",
                )

    out: dict[tuple[str, str], dict] = {}
    for row in frame.to_dict(orient="records"):
        key = (str(row["act_symbol"]), row["earnings_date"].isoformat())
        out[key] = row
    print(f"🧊 Loaded {len(out)} frozen event ML forecasts from {path.name}")
    return out


def has_reported_actuals(row: dict) -> bool:
    """EPS/revenue actuals prove reporting; timing and freeze flags do not."""
    for field in ('eps_actual', 'revenue_actual'):
        value = row.get(field)
        if value is None or isinstance(value, bool):
            continue
        try:
            if math.isfinite(float(value)):
                return True
        except (TypeError, ValueError):
            continue
    return False


def load_reported_event_keys(public_dir: Path, *, today: date) -> set[str]:
    """Find same-day reported evidence across calendar and symbol history."""
    paths = [public_dir / 'weekly.json', public_dir / 'screener.json',
             *sorted((public_dir / 'weeks').glob('*.json')),
             *sorted((public_dir / 'symbols').glob('*.json'))]
    keys = set()
    for path in paths:
        if not path.is_file() or path.name == 'manifest.json':
            continue
        payload = json.loads(path.read_text())
        nodes = [*(payload.get('events') or []), *(payload.get('earnings_history') or [])]
        if isinstance(payload.get('expected_move'), dict):
            nodes.append(payload['expected_move'])
        for node in nodes:
            event_iso = str(node.get('earnings_date') or node.get('date') or '')[:10]
            if event_iso != today.isoformat() or not has_reported_actuals(node):
                continue
            symbol = str(node.get('ticker') or node.get('act_symbol') or payload.get('symbol') or path.stem).upper().strip()
            keys.add(f'{symbol}|{event_iso}')
    return keys


def filter_event_forecast_archive_for_publication(
    archive: dict[tuple[str, str], dict], *, today: date,
    eligible_forecast_ids: set[str], eligible_event_keys: set[str],
    reported_event_keys: set[str],
) -> dict[tuple[str, str], dict]:
    """Preserve reported history and admit only current eligible unreported rows."""
    return {key: row for key, row in archive.items()
            if date.fromisoformat(key[1]) < today
            or date.fromisoformat(key[1]) == today and f'{key[0]}|{key[1]}' in reported_event_keys
            or row.get('forecast_id') in eligible_forecast_ids and f'{key[0]}|{key[1]}' in eligible_event_keys}


def withhold_upcoming_ml(
    public_dir: Path, *, today: date,
    eligible_forecast_ids: set[str] | None = None,
    eligible_event_keys: set[str] | None = None,
    held_event_reasons: dict[str, str] | None = None,
    align_components: bool = True,
    reported_event_keys: set[str] | None = None,
    conn=None, as_of_date: date | None = None, options_available: bool = True,
) -> None:
    """Apply current event eligibility to retained files, preserving reported rows."""
    paths = [public_dir / 'weekly.json', public_dir / 'screener.json',
             *sorted((public_dir / 'weeks').glob('*.json')),
             *sorted((public_dir / 'symbols').glob('*.json'))]
    reported_keys = set(reported_event_keys or ()) | load_reported_event_keys(public_dir, today=today)
    ml_keys = {'em_ml_pct', 'em_ml_abs', 'correction_factor', 'model_horizon',
               'ml_snapshot_date', 'p10', 'p25', 'p50', 'p75', 'p90'}
    def clear_ml(node: dict) -> None:
        for key in list(node):
            if key in ml_keys or key.startswith('forecast_'):
                node.pop(key)
        if node.get('display_forecast_method') == 'ml':
            for key in ('display_forecast_pct', 'display_forecast_method', 'display_forecast_as_of'):
                node.pop(key, None)
        if node.get('em_method') == 'ml_lightgbm':
            node.pop('em_method', None)

    def eligible(node: dict, payload: dict, path: Path) -> bool:
        symbol = str(node.get('ticker') or node.get('act_symbol') or payload.get('symbol') or path.stem).upper().strip()
        event_key = symbol + '|' + str(node.get('earnings_date') or node.get('date') or '')[:10]
        return (eligible_forecast_ids is not None
                and node.get('forecast_id') in eligible_forecast_ids
                and (eligible_event_keys is None or event_key in eligible_event_keys))

    def protected_history(node: dict, payload: dict, path: Path) -> bool:
        event_iso = str(node.get('earnings_date') or node.get('date') or '')[:10]
        if not event_iso:
            return True
        event_date = date.fromisoformat(event_iso)
        symbol = str(node.get('ticker') or node.get('act_symbol') or payload.get('symbol') or path.stem).upper().strip()
        return event_date < today or event_date == today and f'{symbol}|{event_iso}' in reported_keys

    for path in paths:
        if not path.is_file() or path.name == 'manifest.json':
            continue
        payload = json.loads(path.read_text())
        nodes = [*(payload.get('events') or []), *(payload.get('earnings_history') or [])]
        if isinstance(payload.get('expected_move'), dict):
            nodes.append(payload['expected_move'])
        changed = False
        for node in nodes:
            event_date = node.get('earnings_date') or node.get('date')
            if protected_history(node, payload, path):
                continue
            changed = True
            ml_pct = node.get('em_ml_pct')
            valid_ml = (isinstance(ml_pct, (int, float)) and not isinstance(ml_pct, bool)
                        and math.isfinite(ml_pct) and ml_pct > 0)
            if eligible(node, payload, path) and valid_ml:
                node.update(ml_publication_status='eligible', ml_status='available',
                            display_forecast_pct=ml_pct, display_forecast_method='ml',
                            display_forecast_as_of=node.get('ml_snapshot_date')
                            or node.get('display_forecast_as_of') or today.isoformat(),
                            em_method='ml_lightgbm', fallback_reason=None)
                node.setdefault('options_status', 'unavailable')
                node.pop('ml_publication_reason', None)
                continue
            symbol = str(node.get('ticker') or node.get('act_symbol') or payload.get('symbol') or path.stem).upper().strip()
            event_key = symbol + '|' + str(event_date)[:10]
            clear_ml(node)
            node['ml_status'] = 'unavailable_model'
            node['ml_publication_status'] = 'held'
            node['ml_publication_reason'] = (held_event_reasons or {}).get(
                event_key, 'feature_drift' if eligible_forecast_ids is None else 'no_eligible_current_forecast')
            method = node.get('display_forecast_method')
            independent = method in {'options_math', 'options_indicative', 'straddle_implied',
                                     'historical', 'historical_prior'}
            if independent:
                from .display_payloads import _validate_display_provenance
                errors = []
                _validate_display_provenance(node, identity=event_key, errors=errors)
                source_date = (node.get('options_publication_source_date')
                               if node.get('options_publication_status') == 'held'
                               else node.get('as_of_date') or payload.get('as_of_date')
                               or (payload.get('metadata') or {}).get('as_of_date'))
                if (not errors and source_date and node.get('display_forecast_as_of') == source_date
                        and source_date < str(event_date)[:10]
                        and (method in {'historical', 'historical_prior'}
                             or options_available and node.get('options_publication_status') != 'held'
                             and (as_of_date is None or source_date == as_of_date.isoformat()))):
                    node['em_method'] = method
                    continue
            if conn is not None and as_of_date is not None and date.fromisoformat(str(event_date)[:10]) > today:
                from .display_forecast import resolve_upcoming_display_forecast
                from .display_payloads import _clear_held_option_inputs, _strict_options_from_event
                source_iso = (node.get('as_of_date') or payload.get('as_of_date')
                              or (payload.get('metadata') or {}).get('as_of_date'))
                # Retained option values may only represent the build cutoff.
                # Re-query current evidence when the row came from an older release.
                if source_iso != as_of_date.isoformat():
                    _clear_held_option_inputs(node)
                strict_options = _strict_options_from_event(node)
                result = resolve_upcoming_display_forecast(
                    conn, ticker=symbol, earnings_date=date.fromisoformat(str(event_date)[:10]),
                    timing=node.get('timing'), as_of_date=as_of_date,
                    ml_forecast={'ml_status': 'unavailable_model'},
                    strict_options=strict_options,
                    options_available=options_available and node.get('options_publication_status') != 'held',
                )
                node.update(result.public_fields())
                node['em_method'] = result.method
            else:
                from .display_payloads import _clear_held_option_inputs
                _clear_held_option_inputs(node)
                node['options_status'] = 'unavailable'
                node.update(display_forecast_pct=None, display_forecast_method='ml_unavailable',
                            display_forecast_as_of=node.get('as_of_date') or payload.get('as_of_date')
                            or (payload.get('metadata') or {}).get('as_of_date') or today.isoformat(),
                            em_method='ml_unavailable', fallback_reason=None)
                node.setdefault('options_status', 'unavailable')
        if changed:
            path.write_text(json.dumps(payload, indent=2) + '\n')


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
    out["forecast_id"] = pick("forecast_id")
    out["forecast_model_bundle_id"] = pick("model_bundle_id")
    out["forecast_scored_at"] = pick("scored_at")
    out["forecast_published_at"] = pick("published_at", "forecast_published_at")
    out["forecast_feature_cutoff_at"] = pick("feature_cutoff_at")
    out["forecast_prediction_deadline_at"] = pick("prediction_deadline_at")
    out["forecast_feature_snapshot_at"] = pick("feature_snapshot_at")
    out["forecast_feature_hash"] = pick("feature_hash")
    out["forecast_frozen_eligible"] = pick("freeze_eligible")
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


def load_provider_enrichments(policy_path=DEFAULT_POLICY_PATH) -> dict[str, dict]:
    """Load derived provider tables and compact them into per-symbol signals.

    Raw provider payloads are intentionally not published. This emits only
    normalized fields useful to product surfaces and future ML features.
    """
    policy = load_provider_signal_policy(policy_path)
    allowed_publication = permitted_signals(policy, "allow_publication")
    if not allowed_publication:
        print("🧊 Provider enrichment publication frozen pending paired evidence")
        return {}

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

    if "short_interest" in allowed_publication:
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

    if "options_flow" in allowed_publication:
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

    if "corporate_actions" in allowed_publication:
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


def publish_forecast_evidence(*, expected_forecast_path: Path | None = None) -> bool:
    """Publish one small UI manifest; never duplicate receipts into symbol JSON."""
    public_path = PUBLIC_DIR / "evidence" / "forecast.json"
    if not FORECAST_RECEIPT_PATH.exists():
        public_path.unlink(missing_ok=True)
        if expected_forecast_path is not None:
            raise ValueError('eligible forecast evidence receipt is missing')
        print("⚠️  No forecast evidence receipt; public trust manifest removed")
        return False
    try:
        receipt = json.loads(FORECAST_RECEIPT_PATH.read_text())
        if expected_forecast_path is not None:
            from ml.evidence_receipt import verify_evidence_receipt
            from ml.model_artifact import sha256_file
            verify_evidence_receipt(receipt, expected_scope='forecasts')
            expected_digest = sha256_file(expected_forecast_path)
            members = [member for artifact in receipt.get('artifacts', [])
                       if artifact.get('name') == 'forecast_snapshot'
                       for member in artifact.get('members', [])]
            if len(members) != 1 or members[0].get('sha256') != expected_digest:
                raise ValueError('evidence receipt does not match the eligible forecast')
            if (receipt.get('quality') or {}).get('status') != 'passed':
                raise ValueError('eligible forecast evidence must pass validation')
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
