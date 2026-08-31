#!/usr/bin/env python3
"""Publish a compact, hash-free production-control snapshot for the UI.

The source manifests stay in the validation/R2 audit paths. This projection is
deliberately small: engineers get the full receipts there, while the protected
status page gets the exceptions and publication-control signals a person can act on.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = REPO_ROOT / "apps" / "frontend" / "public" / "control-plane.json"


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def _status(*values: str | None) -> str:
    normalized = {value for value in values if value}
    if "failed" in normalized or "critical" in normalized:
        return "failed"
    if "degraded" in normalized or "warning" in normalized:
        return "degraded"
    if "passed" in normalized or "ok" in normalized:
        return "passed"
    return "unavailable"


def build_snapshot(
    reconciliation: dict[str, Any],
    monitoring: dict[str, Any],
    registry: dict[str, Any],
    outcomes: dict[str, Any],
    *,
    generated_at: str,
) -> dict[str, Any]:
    quality = reconciliation.get("quality") or {}
    source = reconciliation.get("source_reconciliation") or {}
    quote = reconciliation.get("quote_quality") or {}
    events = reconciliation.get("event_coverage") or {}
    controls = reconciliation.get("pipeline_controls") or {}
    quarantine = controls.get("quarantine") or {}
    replay = controls.get("idempotent_replay") or {}
    actions = reconciliation.get("corporate_actions") or {}
    duplicates = reconciliation.get("duplicates") or {}
    exceptions = [
        {
            key: item[key]
            for key in ("code", "severity", "summary", "count")
            if key in item
        }
        for item in (reconciliation.get("exceptions") or [])
        if isinstance(item, dict) and item.get("code") and item.get("severity")
    ]

    drift = monitoring.get("feature_drift") or {}
    shadow_scoring = monitoring.get("shadow_scoring") or {}
    model_status = _status(monitoring.get("status"), drift.get("status"))
    if not monitoring:
        model_status = "unavailable"
    data_status = _status(quality.get("status"))
    if data_status == "unavailable" and model_status == "unavailable":
        overall_status = "unavailable"
    elif "failed" in {data_status, model_status}:
        overall_status = "failed"
    elif "unavailable" in {data_status, model_status}:
        overall_status = "degraded"
    else:
        overall_status = _status(data_status, model_status)

    total_duplicates = sum(
        int((value or {}).get("duplicate_rows") or 0)
        for value in duplicates.values()
        if isinstance(value, dict)
    )
    active_champion = bool(registry.get("champion_bundle_id"))
    challenger = bool(registry.get("challenger_bundle_id"))

    publication_eligible = bool(quality.get("decision_safe")) and model_status in {
        "passed",
        "degraded",
    }

    return {
        "schema": "quantiv.control-plane.v2",
        "generated_at": generated_at,
        "status": overall_status,
        "publication_eligible": publication_eligible,
        "data": {
            "status": data_status,
            "source_date": source.get("source_date") or quote.get("source_date"),
            "expected_source_date": quote.get("expected_source_date"),
            "source_session_lag": _number(quote.get("source_session_lag")),
            "event_coverage_pct": _number(events.get("coverage_pct")),
            "expected_events": _number(events.get("expected_events")),
            "covered_events": _number(events.get("covered_events")),
            "missing_events": _number(events.get("missing_events")),
            "contract_rejection_rate": _number(quote.get("contract_rejection_rate")),
            "pair_rejection_rate": _number(quote.get("pair_rejection_rate")),
            "contracts": _number(quote.get("contracts")),
            "eligible_contracts": _number(quote.get("eligible_contracts")),
            "live_trading_eligible": bool(quote.get("live_trading_eligible")),
            "decision_scope": quote.get("decision_scope"),
            "quarantine_records": _number(quarantine.get("records")),
            "quarantine_status": quarantine.get("status", "unavailable"),
            "replay_status": replay.get("status", "unavailable"),
            "corporate_action_status": actions.get("continuity_status", "unavailable"),
            "corporate_action_rows": sum(
                int((value or {}).get("rows") or 0)
                for value in (actions.get("datasets") or {}).values()
                if isinstance(value, dict)
            ),
            "duplicate_rows": total_duplicates,
        },
        "model": {
            "status": model_status,
            "monitored_at": monitoring.get("monitored_at"),
            "snapshot_date": monitoring.get("snapshot_date"),
            "champion_active": active_champion,
            "challenger_present": challenger,
            "shadow_roles": sorted(str(role) for role in shadow_scoring),
            "drift_status": drift.get("status", "unavailable"),
            "critical_features": _number(drift.get("critical_features")),
            "hard_missing_features": _number(drift.get("hard_missing_features")),
            "warning_features": sum(
                int((value or {}).get("warning_features") or 0)
                for value in (drift.get("horizons") or {}).values()
                if isinstance(value, dict)
            ),
            "fallback_bundle_available": bool(registry.get("previous_bundle_id")),
            "outcome_status": outcomes.get("status", "unavailable"),
            "outcome_common_rows": _number(outcomes.get("common_rows")),
            "outcome_minimum_rows": _number(outcomes.get("minimum_common_rows")),
            "rollback_recorded": bool(outcomes.get("rolled_back")),
        },
        "exceptions": exceptions,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    parser.add_argument("--reconciliation", type=Path, default=REPO_ROOT / "data/validation/data_reconciliation.json")
    parser.add_argument("--monitoring", type=Path, default=REPO_ROOT / "data/models/monitoring/latest_monitoring.json")
    parser.add_argument("--registry", type=Path, default=REPO_ROOT / "data/models/control/registry.json")
    parser.add_argument("--outcomes", type=Path, default=REPO_ROOT / "data/validation/model_outcomes.json")
    args = parser.parse_args()

    snapshot = build_snapshot(
        _read(args.reconciliation),
        _read(args.monitoring),
        _read(args.registry),
        _read(args.outcomes),
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
    temporary.replace(args.output)
    print(
        f"Control plane: {snapshot['status']} · "
        f"{len(snapshot['exceptions'])} exceptions → {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
