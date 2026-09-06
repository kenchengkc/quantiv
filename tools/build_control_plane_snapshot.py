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
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
ML_PACKAGE_ROOT = REPO_ROOT / "apps" / "ml"
if str(ML_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(ML_PACKAGE_ROOT))

from ml.model_bundle import ModelBundleError, verify_outcome_receipt  # noqa: E402

try:  # package import under pytest / repo-root execution
    from tools.build_public_validation import build_validation as build_public_validation  # noqa: E402
except ModuleNotFoundError:  # direct `python tools/build_control_plane_snapshot.py`
    from build_public_validation import build_validation as build_public_validation  # type: ignore[no-redef]  # noqa: E402


OUTPUT_PATH = REPO_ROOT / "apps" / "frontend" / "public" / "control-plane.json"
HISTORY_OUTPUT_PATH = (
    REPO_ROOT / "apps" / "frontend" / "public" / "control-plane-history.json"
)
PUBLIC_VALIDATION_OUTPUT_PATH = (
    REPO_ROOT / "apps" / "frontend" / "public" / "evidence" / "model-validation.json"
)
DEFAULT_HISTORY_LIMIT = 30


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


def _read_verified_outcomes(
    report_path: Path,
    history_path: Path,
    receipt_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not report_path.exists():
        return {}, {}
    if not history_path.exists() or not receipt_path.exists():
        return {"status": "unverified"}, {}
    try:
        receipt = _read(receipt_path)
        verify_outcome_receipt(
            receipt,
            report_path=report_path,
            history_path=history_path,
        )
    except (ModelBundleError, OSError, ValueError):
        return {"status": "unverified"}, {}
    return _read(report_path), _read(history_path)


def _status(*values: str | None) -> str:
    normalized = {value for value in values if value}
    if "failed" in normalized or "critical" in normalized:
        return "failed"
    if "degraded" in normalized or "warning" in normalized:
        return "degraded"
    if "passed" in normalized or "ok" in normalized:
        return "passed"
    return "unavailable"


def _stage_status(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"passed", "success", "succeeded", "completed"}:
        return "passed"
    if normalized in {"failed", "failure", "cancelled", "timed_out"}:
        return "failed"
    if normalized in {"warning", "degraded"}:
        return "degraded"
    return "unavailable"


def build_release_status(environment: dict[str, str]) -> dict[str, str]:
    """Summarize publication stages reached before the control snapshot."""
    data_status = _stage_status(environment.get("CONTROL_DATA_R2_STATUS"))
    forecast_status = _stage_status(environment.get("CONTROL_FORECAST_R2_STATUS"))
    frontend_status = _stage_status(environment.get("CONTROL_FRONTEND_PAYLOAD_STATUS"))
    core = [data_status, forecast_status, frontend_status]
    if "failed" in core:
        promotion_status = "failed"
    elif all(status == "passed" for status in core):
        promotion_status = "passed"
    elif any(status == "degraded" for status in core):
        promotion_status = "degraded"
    else:
        promotion_status = "unavailable"
    return {
        "artifact_promotion_status": promotion_status,
        "data_r2_status": data_status,
        "forecast_r2_status": forecast_status,
        "frontend_payload_status": frontend_status,
        "neon_import_status": _stage_status(
            environment.get("CONTROL_NEON_IMPORT_STATUS")
        ),
    }


def build_snapshot(
    reconciliation: dict[str, Any],
    monitoring: dict[str, Any],
    registry: dict[str, Any],
    outcomes: dict[str, Any],
    outcome_history: dict[str, Any] | None = None,
    *,
    generated_at: str,
    release: dict[str, Any] | None = None,
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
    outcome_evaluations = [
        item
        for item in ((outcome_history or {}).get("evaluations") or [])
        if isinstance(item, dict)
    ]

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
            "quote_quality_errors": [
                error for error in (quote.get("errors") or []) if isinstance(error, str)
            ],
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
            "outcome_evaluated_at": outcomes.get("evaluated_at"),
            "outcome_evaluations": len(outcome_evaluations),
            "rollback_recorded": bool(outcomes.get("rolled_back")),
        },
        "release": {
            "artifact_promotion_status": (release or {}).get(
                "artifact_promotion_status", "unavailable"
            ),
            "data_r2_status": (release or {}).get(
                "data_r2_status", "unavailable"
            ),
            "forecast_r2_status": (release or {}).get(
                "forecast_r2_status", "unavailable"
            ),
            "frontend_payload_status": (release or {}).get(
                "frontend_payload_status", "unavailable"
            ),
            "neon_import_status": (release or {}).get(
                "neon_import_status", "unavailable"
            ),
        },
        "exceptions": exceptions,
    }


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def build_workflow_reference(
    environment: dict[str, str],
    *,
    completed_at: str | None = None,
) -> dict[str, Any] | None:
    run_id = environment.get("GITHUB_RUN_ID")
    if not run_id:
        return None
    reference = {
        "run_id": run_id,
        "run_number": environment.get("GITHUB_RUN_NUMBER", ""),
        "run_attempt": environment.get("GITHUB_RUN_ATTEMPT", ""),
        "event_name": environment.get("GITHUB_EVENT_NAME", ""),
        "started_at": environment.get("REFRESH_STARTED_AT", ""),
    }
    server_url = environment.get("GITHUB_SERVER_URL")
    repository = environment.get("GITHUB_REPOSITORY")
    reference["url"] = (
        f"{server_url}/{repository}/actions/runs/{run_id}"
        if server_url and repository
        else ""
    )
    started_at = _parse_datetime(reference["started_at"])
    completed = _parse_datetime(completed_at)
    reference["control_ready_seconds"] = (
        max(0, int((completed - started_at).total_seconds()))
        if started_at and completed
        else None
    )
    return reference


def build_history_entry(
    snapshot: dict[str, Any],
    *,
    workflow: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = snapshot.get("data") or {}
    model = snapshot.get("model") or {}
    release = snapshot.get("release") or {}
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
        "decision_groups": _number(data.get("decision_groups")),
        "eligible_decision_groups": _number(
            data.get("eligible_decision_groups")
        ),
        "quarantine_records": _number(data.get("quarantine_records")),
        "quarantine_status": data.get("quarantine_status", "unavailable"),
        "replay_status": data.get("replay_status", "unavailable"),
        "corporate_action_status": data.get(
            "corporate_action_status", "unavailable"
        ),
        "duplicate_rows": _number(data.get("duplicate_rows")),
        "model_snapshot_date": model.get("snapshot_date"),
        "model_status": model.get("status", "unavailable"),
        "drift_status": model.get("drift_status", "unavailable"),
        "critical_features": _number(model.get("critical_features")),
        "warning_features": _number(model.get("warning_features")),
        "hard_missing_features": _number(model.get("hard_missing_features")),
        "challenger_present": bool(model.get("challenger_present")),
        "outcome_status": model.get("outcome_status", "unavailable"),
        "outcome_common_rows": _number(model.get("outcome_common_rows")),
        "outcome_minimum_rows": _number(model.get("outcome_minimum_rows")),
        "outcome_evaluations": _number(model.get("outcome_evaluations")),
        "rollback_recorded": bool(model.get("rollback_recorded")),
        "artifact_promotion_status": release.get(
            "artifact_promotion_status", "unavailable"
        ),
        "data_r2_status": release.get("data_r2_status", "unavailable"),
        "forecast_r2_status": release.get(
            "forecast_r2_status", "unavailable"
        ),
        "frontend_payload_status": release.get(
            "frontend_payload_status", "unavailable"
        ),
        "neon_import_status": release.get(
            "neon_import_status", "unavailable"
        ),
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
    workflow: dict[str, Any] | None = None,
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
    parser.add_argument("--outcomes", type=Path, default=REPO_ROOT / "data/models/monitoring/latest_outcomes.json")
    parser.add_argument("--outcome-history", type=Path, default=REPO_ROOT / "data/models/monitoring/outcome_history.json")
    parser.add_argument("--outcome-receipt", type=Path, default=REPO_ROOT / "data/models/monitoring/latest_outcomes.receipt.json")
    parser.add_argument("--history-output", type=Path, default=HISTORY_OUTPUT_PATH)
    parser.add_argument("--history-limit", type=int, default=DEFAULT_HISTORY_LIMIT)
    args = parser.parse_args()

    outcomes, outcome_history = _read_verified_outcomes(
        args.outcomes,
        args.outcome_history,
        args.outcome_receipt,
    )
    generated_at = datetime.now(timezone.utc).isoformat()
    environment = dict(os.environ)
    snapshot = build_snapshot(
        _read(args.reconciliation),
        _read(args.monitoring),
        _read(args.registry),
        outcomes,
        outcome_history,
        generated_at=generated_at,
        release=build_release_status(environment),
    )
    history = update_history(
        _read(args.history_output),
        snapshot,
        workflow=build_workflow_reference(
            environment,
            completed_at=generated_at,
        ),
        limit=args.history_limit,
    )
    _write_json_atomic(args.output, snapshot)
    _write_json_atomic(args.history_output, history)

    # Publish the public due-diligence projection only after the control-plane
    # snapshot exists, so its current-evidence fields read the same status that
    # will be committed to the frontend. The builder prefers the active signed
    # champion bundle pulled from R2 and falls back only in local/preview runs.
    public_validation = build_public_validation(REPO_ROOT, generated_at=generated_at)
    _write_json_atomic(PUBLIC_VALIDATION_OUTPUT_PATH, public_validation)

    print(
        f"Control plane: {snapshot['status']} · "
        f"{len(snapshot['exceptions'])} exceptions · "
        f"{len(history['runs'])} retained runs → {args.output}; "
        f"public validation → {PUBLIC_VALIDATION_OUTPUT_PATH}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
