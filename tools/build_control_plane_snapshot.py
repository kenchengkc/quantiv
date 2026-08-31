#!/usr/bin/env python3
"""Publish a compact, hash-free production-control snapshot for the UI.

The source manifests stay in the validation/R2 audit paths. This projection is
deliberately small: engineers get the full receipts there, while the protected
status page gets the exceptions and publication-control signals a person can act on.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = REPO_ROOT / "apps" / "frontend" / "public" / "control-plane.json"
HISTORY_OUTPUT_PATH = (
    REPO_ROOT / "apps" / "frontend" / "public" / "control-plane-history.json"
)
DEFAULT_HISTORY_LIMIT = 14


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
            "decision_group_rejection_rate": _number(
                quote.get("decision_group_rejection_rate")
            ),
            "decision_groups": _number(quote.get("decision_groups")),
            "eligible_decision_groups": _number(
                quote.get("eligible_decision_groups")
            ),
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


def build_workflow_reference(environment: dict[str, str]) -> dict[str, str] | None:
    run_id = environment.get("GITHUB_RUN_ID")
    if not run_id:
        return None
    reference = {
        "run_id": run_id,
        "run_number": environment.get("GITHUB_RUN_NUMBER", ""),
        "run_attempt": environment.get("GITHUB_RUN_ATTEMPT", ""),
    }
    server_url = environment.get("GITHUB_SERVER_URL")
    repository = environment.get("GITHUB_REPOSITORY")
    reference["url"] = (
        f"{server_url}/{repository}/actions/runs/{run_id}"
        if server_url and repository
        else ""
    )
    return reference


def build_history_entry(
    snapshot: dict[str, Any],
    *,
    workflow: dict[str, str] | None = None,
) -> dict[str, Any]:
    data = snapshot.get("data") or {}
    model = snapshot.get("model") or {}
    exceptions = [
        item
        for item in (snapshot.get("exceptions") or [])
        if isinstance(item, dict)
    ]
    return {
        "generated_at": snapshot.get("generated_at"),
        "status": snapshot.get("status", "unavailable"),
        "publication_eligible": bool(
            snapshot.get("publication_eligible", snapshot.get("decision_safe", False))
        ),
        "source_date": data.get("source_date"),
        "source_session_lag": _number(data.get("source_session_lag")),
        "event_coverage_pct": _number(data.get("event_coverage_pct")),
        "expected_events": _number(data.get("expected_events")),
        "covered_events": _number(data.get("covered_events")),
        "missing_events": _number(data.get("missing_events")),
        "contract_rejection_rate": _number(data.get("contract_rejection_rate")),
        "pair_rejection_rate": _number(data.get("pair_rejection_rate")),
        "decision_group_rejection_rate": _number(
            data.get("decision_group_rejection_rate")
        ),
        "duplicate_rows": _number(data.get("duplicate_rows")),
        "model_snapshot_date": model.get("snapshot_date"),
        "model_status": model.get("status", "unavailable"),
        "drift_status": model.get("drift_status", "unavailable"),
        "critical_features": _number(model.get("critical_features")),
        "warning_features": _number(model.get("warning_features")),
        "challenger_present": bool(model.get("challenger_present")),
        "outcome_status": model.get("outcome_status", "unavailable"),
        "critical_exceptions": sum(
            1 for item in exceptions if item.get("severity") == "critical"
        ),
        "warning_exceptions": sum(
            1 for item in exceptions if item.get("severity") == "warning"
        ),
        "exception_codes": sorted(
            str(item["code"]) for item in exceptions if item.get("code")
        ),
        "workflow": workflow,
    }


def _history_identity(entry: dict[str, Any]) -> tuple[str, ...]:
    workflow = entry.get("workflow")
    if isinstance(workflow, dict) and workflow.get("run_id"):
        return (
            "workflow",
            str(workflow["run_id"]),
            str(workflow.get("run_attempt") or "1"),
        )
    return ("generated_at", str(entry.get("generated_at") or ""))


def update_history(
    existing: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    workflow: dict[str, str] | None = None,
    limit: int = DEFAULT_HISTORY_LIMIT,
) -> dict[str, Any]:
    if limit < 1:
        raise ValueError("history limit must be at least 1")
    current = build_history_entry(snapshot, workflow=workflow)
    current_identity = _history_identity(current)
    previous = [
        entry
        for entry in (existing.get("runs") or [])
        if isinstance(entry, dict) and _history_identity(entry) != current_identity
    ]
    return {
        "schema": "quantiv.control-plane-history.v1",
        "generated_at": snapshot.get("generated_at"),
        "runs": [current, *previous][:limit],
    }


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    parser.add_argument("--reconciliation", type=Path, default=REPO_ROOT / "data/validation/data_reconciliation.json")
    parser.add_argument("--monitoring", type=Path, default=REPO_ROOT / "data/models/monitoring/latest_monitoring.json")
    parser.add_argument("--registry", type=Path, default=REPO_ROOT / "data/models/control/registry.json")
    parser.add_argument("--outcomes", type=Path, default=REPO_ROOT / "data/validation/model_outcomes.json")
    parser.add_argument("--history-output", type=Path, default=HISTORY_OUTPUT_PATH)
    parser.add_argument("--history-limit", type=int, default=DEFAULT_HISTORY_LIMIT)
    args = parser.parse_args()

    snapshot = build_snapshot(
        _read(args.reconciliation),
        _read(args.monitoring),
        _read(args.registry),
        _read(args.outcomes),
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
    history = update_history(
        _read(args.history_output),
        snapshot,
        workflow=build_workflow_reference(dict(os.environ)),
        limit=args.history_limit,
    )
    _write_json_atomic(args.output, snapshot)
    _write_json_atomic(args.history_output, history)
    print(
        f"Control plane: {snapshot['status']} · "
        f"{len(snapshot['exceptions'])} exceptions · "
        f"{len(history['runs'])} retained runs → {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
