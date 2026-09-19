#!/usr/bin/env python3
"""Recover point-in-time forecasts for already-reported earnings events.

The frontend rebuild cannot recompute an earnings forecast after the event. This
tool walks prior committed week bundles and restores the strongest pre-event
forecast that Quantiv actually published for each reported event:

    ML forecast > strict options math > IV-only indicative options.

Fresh post-event facts (realized move, EPS/revenue actuals, corrected timing)
remain authoritative. Only forecast/pricing fields are frozen from history.

Dry-run by default; pass --apply to rewrite week bundles, screener, and matching
symbol payloads.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_DIR = REPO_ROOT / "apps" / "frontend" / "public"
WEEKS_DIR = PUBLIC_DIR / "weeks"
SYMBOLS_DIR = PUBLIC_DIR / "symbols"
CALENDAR_REFERENCE = PUBLIC_DIR / "calendar-reference.json"

if str(REPO_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "tools"))
from frontend_data.payloads import build_screener_payload  # noqa: E402

EventKey = tuple[str, str]

FORECAST_FIELDS = {
    "as_of_date",
    "spot_price",
    "atm_strike",
    "atm_iv",
    "em_straddle_pct",
    "em_iv_pct",
    "em_straddle_abs",
    "expiry_date",
    "days_to_expiry",
    "lead_time_days",
    "skew_atm",
    "term_slope",
    "em_method",
    "confidence",
    "em_ml_pct",
    "em_ml_abs",
    "correction_factor",
    "model_horizon",
    "ml_snapshot_date",
    "p10",
    "p25",
    "p50",
    "p75",
    "p90",
    "display_forecast_pct",
    "display_forecast_method",
    "display_forecast_as_of",
    "ml_status",
    "options_status",
    "fallback_reason",
    "historical_event_count",
    "forecast_frozen",
}

ML_HISTORY_FIELDS = {
    "em_ml_pct",
    "em_ml_abs",
    "correction_factor",
    "model_horizon",
    "ml_snapshot_date",
    "p10",
    "p25",
    "p50",
    "p75",
    "p90",
}


def _git(*args: str) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, check=True
    ).stdout


def _load_reference_membership() -> tuple[set[EventKey], dict[str, str]]:
    payload = json.loads(CALENDAR_REFERENCE.read_text(encoding="utf-8"))
    events = payload.get("events") or []
    membership = {(e["ticker"], e["earnings_date"]) for e in events}
    return membership, payload["window"]


def _history(path: Path) -> list[str]:
    rel = path.relative_to(REPO_ROOT).as_posix()
    return _git("log", "--format=%H", "--", rel).decode().split()


def _bundle_at(commit: str, path: Path) -> dict | None:
    rel = path.relative_to(REPO_ROOT).as_posix()
    try:
        return json.loads(_git("show", f"{commit}:{rel}"))
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return None


def _positive(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _forecast_rank(event: dict) -> tuple[int, str]:
    earnings_date = str(event.get("earnings_date") or "")[:10]
    ml = _positive(event.get("em_ml_pct"))
    ml_as_of = str(event.get("ml_snapshot_date") or "")[:10]
    if ml is not None and ml_as_of and (not earnings_date or ml_as_of < earnings_date):
        return (3, ml_as_of)

    strict = _positive(event.get("em_straddle_pct"))
    as_of = str(event.get("as_of_date") or "")[:10]
    if strict is not None:
        return (2, as_of)

    iv = _positive(event.get("em_iv_pct"))
    if iv is not None:
        return (1, as_of)
    return (0, "")


def _normalize_forecast(event: dict) -> dict:
    row = dict(event)
    ml = _positive(row.get("em_ml_pct"))
    strict = _positive(row.get("em_straddle_pct"))
    iv = _positive(row.get("em_iv_pct"))

    if ml is not None:
        row.update(
            {
                "em_method": "ml_lightgbm",
                "display_forecast_pct": ml,
                "display_forecast_method": "ml",
                "display_forecast_as_of": row.get("ml_snapshot_date")
                or row.get("as_of_date"),
                "ml_status": "available",
                "options_status": "decision_eligible" if strict is not None else "unavailable",
                "fallback_reason": None,
                "forecast_frozen": True,
            }
        )
    elif strict is not None:
        row.update(
            {
                "em_method": "options_math",
                "display_forecast_pct": strict,
                "display_forecast_method": "options_math",
                "display_forecast_as_of": row.get("as_of_date"),
                "ml_status": "unavailable_event",
                "options_status": "decision_eligible",
                "fallback_reason": None,
                "forecast_frozen": True,
            }
        )
    elif iv is not None:
        row.update(
            {
                "display_forecast_pct": iv,
                "display_forecast_method": "options_indicative",
                "display_forecast_as_of": row.get("as_of_date"),
                "ml_status": "unavailable_event",
                "options_status": "indicative",
                "fallback_reason": "no_same_strike_pair",
                "forecast_frozen": True,
            }
        )
    return row


def _merge_forecast(current: dict, historical: dict) -> dict:
    source = _normalize_forecast(historical)
    merged = dict(current)
    for field in FORECAST_FIELDS:
        value = source.get(field)
        if value is not None:
            merged[field] = value
    return merged


def _summary(events: list[dict]) -> dict:
    straddle = [
        e["em_straddle_pct"]
        for e in events
        if _positive(e.get("em_straddle_pct")) is not None
    ]
    iv = [
        e["em_iv_pct"]
        for e in events
        if _positive(e.get("em_iv_pct")) is not None
    ]
    return {
        "total_events": len(events),
        "avg_em_straddle_pct": sum(straddle) / len(straddle) if straddle else 0,
        "avg_em_iv_pct": sum(iv) / len(iv) if iv else 0,
    }


def recover_week(
    path: Path,
    membership: set[EventKey],
    today: date,
    apply: bool,
) -> dict[EventKey, dict]:
    bundle = json.loads(path.read_text(encoding="utf-8"))
    events = bundle.get("events") or []
    by_key = {(e["ticker"], e["earnings_date"]): e for e in events}

    targets = {
        key
        for key in membership
        if key[1] <= today.isoformat()
        and bundle["window"]["start"] <= key[1] <= bundle["window"]["end"]
    }
    if not targets:
        return {}

    best: dict[EventKey, dict] = {}
    for commit in _history(path):
        historical = _bundle_at(commit, path)
        if not historical:
            continue
        for event in historical.get("events") or []:
            key = (event.get("ticker"), event.get("earnings_date"))
            if key not in targets or _forecast_rank(event)[0] == 0:
                continue
            previous = best.get(key)
            if previous is None or _forecast_rank(event) > _forecast_rank(previous):
                best[key] = event

    changed: dict[EventKey, dict] = {}
    for key in sorted(targets):
        candidate = best.get(key)
        if candidate is None:
            continue
        current = by_key.get(key)
        if current is None:
            repaired = _normalize_forecast(candidate)
        elif _forecast_rank(candidate) > _forecast_rank(current):
            repaired = _merge_forecast(current, candidate)
        elif _forecast_rank(current)[0] == 0:
            repaired = _merge_forecast(current, candidate)
        else:
            # Even when the numerical forecast is already present, old bundles
            # may predate canonical display provenance. Normalize those fields.
            normalized = _normalize_forecast(current)
            if normalized == current:
                continue
            repaired = normalized

        by_key[key] = repaired
        changed[key] = repaired

    if not changed:
        return {}

    merged = sorted(by_key.values(), key=lambda e: (e["earnings_date"], e["ticker"]))
    bundle["events"] = merged
    if "summary" in bundle:
        bundle["summary"] = {**bundle["summary"], **_summary(merged)}

    if apply:
        path.write_text(json.dumps(bundle, indent=2, default=str), encoding="utf-8")
    return changed


def _symbol_expected_move(event: dict) -> dict:
    event = _normalize_forecast(event)
    mapped = {
        "earnings_date": event.get("earnings_date"),
        "timing": event.get("timing"),
        "expiration": event.get("expiry_date"),
        "dte": event.get("days_to_expiry"),
        "lead_time_days": event.get("lead_time_days"),
        "atm_strike": event.get("atm_strike"),
        "atm_iv": event.get("atm_iv"),
        "straddle_abs": event.get("em_straddle_abs"),
        "straddle_pct": event.get("em_straddle_pct"),
        "iv_pct": event.get("em_iv_pct"),
        "em_ml_pct": event.get("em_ml_pct"),
        "em_ml_abs": event.get("em_ml_abs"),
        "correction_factor": event.get("correction_factor"),
        "model_horizon": event.get("model_horizon"),
        "ml_snapshot_date": event.get("ml_snapshot_date"),
        "p10": event.get("p10"),
        "p25": event.get("p25"),
        "p50": event.get("p50"),
        "p75": event.get("p75"),
        "p90": event.get("p90"),
        "em_method": event.get("em_method"),
        "display_forecast_pct": event.get("display_forecast_pct"),
        "display_forecast_method": event.get("display_forecast_method"),
        "display_forecast_as_of": event.get("display_forecast_as_of"),
        "ml_status": event.get("ml_status"),
        "options_status": event.get("options_status"),
        "fallback_reason": event.get("fallback_reason"),
        "historical_event_count": event.get("historical_event_count"),
        "forecast_frozen": True,
    }
    return {key: value for key, value in mapped.items() if value is not None}


def _repair_symbol_payloads(recovered: dict[EventKey, dict], apply: bool) -> int:
    changed_files = 0
    for (ticker, event_date), event in sorted(recovered.items()):
        path = SYMBOLS_DIR / f"{ticker}.json"
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        changed = False
        for row in payload.get("earnings_history") or []:
            if str(row.get("date") or "")[:10] != event_date:
                continue
            normalized = _normalize_forecast(event)
            if _positive(normalized.get("em_ml_pct")) is not None:
                for field in ML_HISTORY_FIELDS:
                    value = normalized.get(field)
                    if value is not None and row.get(field) != value:
                        row[field] = value
                        changed = True
                if row.get("forecast_frozen") is not True:
                    row["forecast_frozen"] = True
                    changed = True

        current_expected = payload.get("expected_move") or {}
        expected_date = str(current_expected.get("earnings_date") or "")[:10]
        next_date = str(payload.get("next_earnings") or "")[:10]
        if event_date in {expected_date, next_date}:
            frozen = _symbol_expected_move(event)
            if frozen.get("display_forecast_pct") is not None and frozen != current_expected:
                payload["expected_move"] = frozen
                changed = True

        if changed:
            changed_files += 1
            if apply:
                path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return changed_files


def _republish_manifest_and_screener() -> None:
    manifest_path = WEEKS_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    week_payloads: dict[date, dict] = {}
    for week in manifest["weeks"]:
        payload = json.loads(
            (WEEKS_DIR / f"{week['start']}.json").read_text(encoding="utf-8")
        )
        week_payloads[date.fromisoformat(week["start"])] = payload
        week["count"] = len(payload.get("events") or [])
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    as_of_date = date.fromisoformat(manifest["as_of_date"])
    this_monday = date.fromisoformat(manifest["current_week"])
    screener = build_screener_payload(as_of_date, this_monday, week_payloads)
    (PUBLIC_DIR / "screener.json").write_text(
        json.dumps(screener, indent=2, default=str), encoding="utf-8"
    )
    print(
        f"  republished weeks/manifest.json and screener.json "
        f"({screener['metadata']['event_count']} events)"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write repaired public payloads")
    ap.add_argument("--today", help="override the reported cutoff (YYYY-MM-DD)")
    args = ap.parse_args()

    today = date.fromisoformat(args.today) if args.today else date.today()
    membership, window = _load_reference_membership()
    print(
        f"calendar-reference window {window['start']}..{window['end']} "
        f"({len(membership)} events), reported cutoff {today.isoformat()}"
    )

    recovered: dict[EventKey, dict] = {}
    for path in sorted(WEEKS_DIR.glob("*.json")):
        if path.name == "manifest.json":
            continue
        repaired = recover_week(path, membership, today, args.apply)
        if repaired:
            recovered.update(repaired)
            print(f"  {path.name}: repaired {len(repaired)} reported forecast(s)")
            for ticker, earnings_date in repaired:
                method = _normalize_forecast(repaired[(ticker, earnings_date)]).get(
                    "display_forecast_method"
                )
                print(f"    {earnings_date}  {ticker}  {method}")

    if not recovered:
        print("nothing to restore")
        return 0
    if not args.apply:
        print(
            f"\ndry run — {len(recovered)} reported forecasts would be repaired; "
            "re-run with --apply"
        )
        return 0

    symbol_count = _repair_symbol_payloads(recovered, apply=True)
    _republish_manifest_and_screener()
    print(
        f"\nrepaired {len(recovered)} reported forecasts "
        f"and {symbol_count} symbol payload(s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
