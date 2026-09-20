#!/usr/bin/env python3
"""Recover point-in-time forecasts for already-reported earnings events.

The frontend rebuild cannot recompute an earnings forecast after the event. This
tool walks prior committed week bundles and restores the strongest pre-event
forecast that Quantiv actually published for each reported event:

    frozen ML forecast > IV-based options forecast > strict straddle.

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
from datetime import date, datetime, timezone
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
from event_forecast_ledger import EASTERN, event_cutoffs  # noqa: E402

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
    "forecast_id",
    "forecast_scored_at",
    "forecast_published_at",
    "forecast_feature_cutoff_at",
    "forecast_prediction_deadline_at",
    "forecast_feature_snapshot_at",
    "forecast_feature_hash",
    "forecast_frozen_eligible",
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


def _commit_at(commit: str) -> datetime | None:
    try:
        raw = _git("show", "-s", "--format=%cI", commit).decode().strip()
        parsed = datetime.fromisoformat(raw)
    except (subprocess.CalledProcessError, UnicodeDecodeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _published_before_deadline(event: dict) -> bool:
    published = event.get("forecast_published_at")
    if not published:
        return False
    try:
        published_at = datetime.fromisoformat(str(published).replace("Z", "+00:00"))
        earnings_date = date.fromisoformat(str(event.get("earnings_date") or "")[:10])
    except ValueError:
        return False
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    _feature_cutoff, prediction_deadline = event_cutoffs(
        earnings_date, event.get("timing")
    )
    return published_at.astimezone(EASTERN) <= prediction_deadline


def _positive(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _is_after_close(timing: Any) -> bool:
    normalized = str(timing or "").strip().lower()
    return normalized in {"after_market_close", "amc", "after_close"} or "after" in normalized


def _option_evidence_is_pre_event(event: dict) -> bool:
    earnings_date = str(event.get("earnings_date") or "")[:10]
    as_of = str(event.get("as_of_date") or "")[:10]
    if not earnings_date or not as_of:
        return False
    if _is_after_close(event.get("timing")):
        return as_of <= earnings_date
    return as_of < earnings_date


def _forecast_rank(event: dict) -> tuple[int, str]:
    earnings_date = str(event.get("earnings_date") or "")[:10]
    ml = _positive(event.get("em_ml_pct"))
    ml_as_of = str(event.get("ml_snapshot_date") or "")[:10]
    ml_audited = (
        event.get("forecast_frozen_eligible") is True
        or _published_before_deadline(event)
    )
    if (
        ml is not None
        and ml_as_of
        and (not earnings_date or ml_as_of < earnings_date)
        and ml_audited
    ):
        return (4, str(event.get("forecast_scored_at") or event.get("forecast_published_at") or ml_as_of))

    as_of = str(event.get("as_of_date") or "")[:10]
    options_are_pre_event = _option_evidence_is_pre_event(event)
    iv = _positive(event.get("em_iv_pct"))
    if iv is not None and options_are_pre_event:
        return (3, as_of)

    strict = _positive(event.get("em_straddle_pct"))
    if strict is not None and options_are_pre_event:
        return (2, as_of)
    return (0, "")


def _normalize_forecast(event: dict) -> dict:
    row = dict(event)
    ml = _positive(row.get("em_ml_pct"))
    strict = _positive(row.get("em_straddle_pct"))
    iv = _positive(row.get("em_iv_pct"))
    options_are_pre_event = _option_evidence_is_pre_event(row)
    option_available = options_are_pre_event and (iv is not None or strict is not None)

    if ml is not None:
        row.update(
            {
                "em_method": "ml_lightgbm",
                "display_forecast_pct": ml,
                "display_forecast_method": "ml",
                "display_forecast_as_of": row.get("ml_snapshot_date")
                or row.get("as_of_date"),
                "ml_status": "available",
                "options_status": "decision_eligible" if option_available else "unavailable",
                "fallback_reason": None,
                "forecast_frozen": True,
            }
        )
    elif iv is not None and options_are_pre_event:
        decision_eligible = strict is not None
        row.update(
            {
                "em_method": "options_math" if decision_eligible else row.get("em_method"),
                "display_forecast_pct": iv,
                "display_forecast_method": (
                    "options_math" if decision_eligible else "options_indicative"
                ),
                "display_forecast_as_of": row.get("as_of_date"),
                "ml_status": "unavailable_event",
                "options_status": "decision_eligible" if decision_eligible else "indicative",
                "fallback_reason": None if decision_eligible else "no_same_strike_pair",
                "forecast_frozen": True,
            }
        )
    elif strict is not None and options_are_pre_event:
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
    return row


def _symbol_history_candidate(key: EventKey) -> dict | None:
    """Build point-in-time option evidence from the generated symbol history.

    Symbol history often retains the final event-day IV/straddle observation
    even after the compact week row has been rebuilt without forecast fields.
    Reusing that exact observation keeps the overview and symbol dashboard on
    the same frozen number without inventing a post-event estimate.
    """

    ticker, event_date = key
    path = SYMBOLS_DIR / f"{ticker}.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    row = next(
        (
            item
            for item in payload.get("earnings_history") or []
            if str(item.get("date") or "")[:10] == event_date
        ),
        None,
    )
    if row is None:
        return None

    implied_as_of = str(row.get("implied_as_of") or "")[:10]
    if not implied_as_of:
        return None
    timing = row.get("timing")
    candidate: dict[str, Any] = {
        "ticker": ticker,
        "earnings_date": event_date,
        "timing": timing,
        "as_of_date": implied_as_of,
        "expiry_date": row.get("implied_expiration"),
        "days_to_expiry": row.get("implied_dte"),
        "lead_time_days": row.get("implied_lead_days"),
        "atm_strike": row.get("implied_atm_strike"),
        "atm_iv": row.get("implied_atm_iv"),
        "em_straddle_abs": row.get("implied_straddle_abs"),
        "em_straddle_pct": row.get("implied"),
    }

    atm_iv = _positive(row.get("implied_atm_iv"))
    dte = _positive(row.get("implied_dte"))
    if atm_iv is not None and dte is not None:
        candidate["em_iv_pct"] = atm_iv * math.sqrt(dte / 365.0)

    for field in ML_HISTORY_FIELDS:
        value = row.get(field)
        if value is not None:
            candidate[field] = value
    for field in (
        "forecast_id",
        "forecast_scored_at",
        "forecast_feature_cutoff_at",
        "forecast_prediction_deadline_at",
        "forecast_feature_snapshot_at",
        "forecast_feature_hash",
        "forecast_frozen_eligible",
    ):
        value = row.get(field)
        if value is not None:
            candidate[field] = value

    quality = row.get("implied_quality_status")
    if quality not in {None, "decision_eligible_eod"}:
        candidate["em_straddle_pct"] = None
        candidate["em_iv_pct"] = None

    return candidate if _forecast_rank(candidate)[0] > 0 else None


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
        published_at = _commit_at(commit)
        for raw_event in historical.get("events") or []:
            event = dict(raw_event)
            if published_at is not None:
                event["forecast_published_at"] = published_at.astimezone(
                    timezone.utc
                ).isoformat()
            key = (event.get("ticker"), event.get("earnings_date"))
            if key not in targets or _forecast_rank(event)[0] == 0:
                continue
            previous = best.get(key)
            if previous is None or _forecast_rank(event) > _forecast_rank(previous):
                best[key] = event

    # The symbol history can retain a later, exact pre-event IV observation
    # than the compact week bundle. Prefer it when it has stronger/newer
    # point-in-time evidence.
    for key in targets:
        symbol_candidate = _symbol_history_candidate(key)
        if symbol_candidate is None:
            continue
        previous = best.get(key)
        if previous is None or _forecast_rank(symbol_candidate) > _forecast_rank(previous):
            best[key] = symbol_candidate

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
        "forecast_id": event.get("forecast_id"),
        "forecast_scored_at": event.get("forecast_scored_at"),
        "forecast_published_at": event.get("forecast_published_at"),
        "forecast_feature_cutoff_at": event.get("forecast_feature_cutoff_at"),
        "forecast_prediction_deadline_at": event.get("forecast_prediction_deadline_at"),
        "forecast_feature_snapshot_at": event.get("forecast_feature_snapshot_at"),
        "forecast_feature_hash": event.get("forecast_feature_hash"),
        "forecast_frozen_eligible": event.get("forecast_frozen_eligible"),
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
    current_week = week_payloads.get(this_monday)
    if current_week is not None:
        (PUBLIC_DIR / "weekly.json").write_text(
            json.dumps(current_week, indent=2, default=str), encoding="utf-8"
        )
    print(
        f"  republished weekly.json, weeks/manifest.json and screener.json "
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
