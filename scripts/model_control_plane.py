#!/usr/bin/env python3
"""Operate Quantiv's file-backed model control plane without Redis or Neon."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
ML_PACKAGE_ROOT = REPO_ROOT / "apps" / "ml"
if str(ML_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(ML_PACKAGE_ROOT))

from ml.model_bundle import (  # noqa: E402
    create_signed_control_pointer,
    create_signed_monitor_receipt,
    create_signed_outcome_receipt,
    create_signed_registry,
    verify_bundle_dir,
    verify_control_pointer,
    verify_monitor_receipt,
    verify_registry,
)
from ml.model_control import (  # noqa: E402
    append_prediction_ledger,
    compare_on_common_holdout,
    evaluate_realized_outcomes,
    feature_drift_report,
    monitoring_rows,
    shadow_score_report,
    update_outcome_history,
)
from ml.pipeline_validation import latest_forecast_path  # noqa: E402


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"expected a JSON object at {path}")
    return payload


def _publish_outcome_evidence(
    args: argparse.Namespace,
    report: dict[str, Any],
    monitoring_dir: Path,
) -> None:
    """Atomically persist and sign the latest outcome check plus bounded history."""
    latest_path = args.monitoring_report or monitoring_dir / "latest_outcomes.json"
    history_path = args.history or monitoring_dir / "outcome_history.json"
    receipt_path = monitoring_dir / "latest_outcomes.receipt.json"
    existing_history = _read_json(history_path) if history_path.exists() else {}
    history = update_outcome_history(
        existing_history,
        report,
        limit=args.history_limit,
    )
    _atomic_json(args.report, report)
    _atomic_json(latest_path, report)
    _atomic_json(history_path, history)
    _atomic_json(
        receipt_path,
        create_signed_outcome_receipt(
            report_path=latest_path,
            history_path=history_path,
        ),
    )


def _promote_forecast(candidate_path: Path, forecast_dir: Path) -> Path:
    frame = pd.read_parquet(candidate_path, columns=["snapshot_date"])
    snapshot = pd.to_datetime(frame["snapshot_date"], errors="raise").max().date().isoformat()
    forecast_dir.mkdir(parents=True, exist_ok=True)
    destination = forecast_dir / f"forecasts_{snapshot}.parquet"
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    shutil.copy2(candidate_path, temporary)
    temporary.replace(destination)
    return destination


def decide(args: argparse.Namespace) -> int:
    candidate_record = _read_json(args.candidate_manifest)
    candidate_id = str(candidate_record["bundle_id"])
    candidate_dir = Path(candidate_record["bundle_dir"])
    candidate_manifest = verify_bundle_dir(candidate_dir)
    if candidate_manifest["bundle_id"] != candidate_id:
        raise ValueError("candidate record and signed manifest disagree")
    candidate_forecasts = pd.read_parquet(args.candidate_forecast)

    control_dir = args.models_root / "control"
    pointer_path = control_dir / "champion.json"
    registry_path = control_dir / "registry.json"
    champion_id: str | None = None
    champion_dir: Path | None = None
    previous_id: str | None = None
    history: list[dict[str, Any]] = []
    if pointer_path.exists():
        pointer = verify_control_pointer(_read_json(pointer_path))
        champion_id = str(pointer["champion_bundle_id"])
        previous_id = pointer.get("previous_bundle_id")
        champion_dir = args.models_root / "bundles" / champion_id
        verify_bundle_dir(champion_dir)
    if registry_path.exists():
        registry = verify_registry(_read_json(registry_path))
        history = list(registry.get("history") or [])

    drift = feature_drift_report(candidate_forecasts, candidate_dir)
    comparison: dict[str, Any] | None = None
    shadow: dict[str, Any] | None = None
    reasons: list[str] = []
    if drift["status"] == "critical":
        reasons.append("candidate inputs show critical feature drift")
    if champion_dir is not None:
        comparison = compare_on_common_holdout(
            candidate_dir, champion_dir, args.training_dir
        )
        shadow = shadow_score_report(candidate_forecasts, champion_dir)
        if comparison["status"] != "passed":
            reasons.extend(comparison["issues"])
        if shadow["status"] != "passed":
            reasons.extend(shadow["issues"])

    promoted = not reasons
    action = "bootstrap" if champion_id is None and promoted else (
        "promote" if promoted else "retain_champion"
    )
    evaluated_at = datetime.now(timezone.utc).isoformat()
    decision_summary = {
        "action": action,
        "candidate_bundle_id": candidate_id,
        "evaluated_at": evaluated_at,
        "reasons": reasons,
    }
    production_forecast: Path | None = None
    if promoted:
        pointer = create_signed_control_pointer(
            bundle_id=candidate_id,
            previous_bundle_id=champion_id,
            decision=decision_summary,
        )
        _atomic_json(pointer_path, pointer)
        new_champion = candidate_id
        new_previous = champion_id
        challenger = None
        production_forecast = _promote_forecast(
            args.candidate_forecast, args.production_forecast_dir
        )
    else:
        if champion_id is None:
            raise RuntimeError("bootstrap candidate failed safety gates; no champion can be published")
        new_champion = champion_id
        new_previous = str(previous_id) if previous_id is not None else None
        challenger = candidate_id

    history.append(
        {
            "action": action,
            "candidate_bundle_id": candidate_id,
            "champion_bundle_id": new_champion,
            "evaluated_at": evaluated_at,
        }
    )
    registry = create_signed_registry(
        champion_bundle_id=new_champion,
        challenger_bundle_id=challenger,
        previous_bundle_id=new_previous,
        decision=decision_summary,
        history=history,
    )
    _atomic_json(registry_path, registry)

    report = {
        "schema": "quantiv.model-decision.v1",
        "status": "passed",
        "evaluated_at": evaluated_at,
        "action": action,
        "promoted": promoted,
        "candidate_bundle_id": candidate_id,
        "champion_bundle_id": new_champion,
        "previous_bundle_id": new_previous,
        "challenger_bundle_id": challenger,
        "production_forecast": str(production_forecast) if production_forecast else None,
        "reasons": reasons,
        "common_holdout": comparison,
        "shadow_scoring": shadow,
        "feature_drift": drift,
    }
    _atomic_json(args.report, report)
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


def monitor(args: argparse.Namespace) -> int:
    control_dir = args.models_root / "control"
    pointer = verify_control_pointer(_read_json(control_dir / "champion.json"))
    champion_id = str(pointer["champion_bundle_id"])
    champion_dir = args.models_root / "bundles" / champion_id
    verify_bundle_dir(champion_dir)
    registry = (
        verify_registry(_read_json(control_dir / "registry.json"))
        if (control_dir / "registry.json").exists()
        else {
            "champion_bundle_id": champion_id,
            "challenger_bundle_id": None,
            "previous_bundle_id": pointer.get("previous_bundle_id"),
        }
    )
    forecast_path = args.forecast_path or latest_forecast_path(args.forecast_dir)
    if forecast_path is None:
        raise FileNotFoundError("no production forecast snapshot exists")
    forecasts = pd.read_parquet(forecast_path)
    served_ids = set(forecasts["model_bundle_id"].dropna().astype(str))
    if served_ids != {champion_id}:
        raise ValueError(
            f"production forecast bundle {sorted(served_ids)} does not match champion {champion_id}"
        )

    ledger_rows = [
        monitoring_rows(
            forecasts,
            champion_dir,
            bundle_id=champion_id,
            role="champion",
            use_served_predictions=True,
        )
    ]
    shadows: dict[str, Any] = {}
    shadow_ids = {
        "challenger": registry.get("challenger_bundle_id"),
        "previous": registry.get("previous_bundle_id"),
    }
    for role, bundle_id in shadow_ids.items():
        if not bundle_id or bundle_id == champion_id:
            continue
        bundle_dir = args.models_root / "bundles" / str(bundle_id)
        verify_bundle_dir(bundle_dir)
        shadows[role] = shadow_score_report(forecasts, bundle_dir)
        ledger_rows.append(
            monitoring_rows(
                forecasts,
                bundle_dir,
                bundle_id=str(bundle_id),
                role=role,
            )
        )

    monitoring_dir = args.models_root / "monitoring"
    ledger_path = monitoring_dir / "prediction_ledger.parquet"
    ledger = append_prediction_ledger(ledger_path, ledger_rows)
    drift = feature_drift_report(forecasts, champion_dir)
    report = {
        "schema": "quantiv.model-monitoring.v1",
        "status": "failed" if drift["status"] == "critical" else "passed",
        "monitored_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_date": str(pd.to_datetime(forecasts["snapshot_date"]).max().date()),
        "champion_bundle_id": champion_id,
        "forecast_path": str(forecast_path),
        "ledger_rows": len(ledger),
        "feature_drift": drift,
        "shadow_scoring": shadows,
    }
    report_path = monitoring_dir / "latest_monitoring.json"
    _atomic_json(report_path, report)
    receipt = create_signed_monitor_receipt(
        ledger_path=ledger_path,
        report_path=report_path,
        snapshot_date=report["snapshot_date"],
    )
    _atomic_json(monitoring_dir / "latest_monitoring.receipt.json", receipt)
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0 if report["status"] == "passed" else 1


def evaluate_outcomes(args: argparse.Namespace) -> int:
    control_dir = args.models_root / "control"
    monitoring_dir = args.models_root / "monitoring"
    ledger_path = monitoring_dir / "prediction_ledger.parquet"
    monitoring_report_path = monitoring_dir / "latest_monitoring.json"
    receipt_path = monitoring_dir / "latest_monitoring.receipt.json"
    evaluated_at = datetime.now(timezone.utc).isoformat()
    if not (ledger_path.exists() and monitoring_report_path.exists() and receipt_path.exists()):
        report = {
            "schema": "quantiv.model-outcome-monitor.v1",
            "evaluated_at": evaluated_at,
            "status": "insufficient_data",
            "rolled_back": False,
            "reason": "signed production prediction ledger is not available yet",
        }
        _publish_outcome_evidence(args, report, monitoring_dir)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    verify_monitor_receipt(
        _read_json(receipt_path),
        ledger_path=ledger_path,
        report_path=monitoring_report_path,
    )
    pointer = verify_control_pointer(_read_json(control_dir / "champion.json"))
    registry = verify_registry(_read_json(control_dir / "registry.json"))
    champion_id = str(pointer["champion_bundle_id"])
    comparison_id = registry.get("previous_bundle_id") or registry.get("challenger_bundle_id")
    if not comparison_id or comparison_id == champion_id:
        report = {
            "schema": "quantiv.model-outcome-monitor.v1",
            "evaluated_at": evaluated_at,
            "status": "insufficient_data",
            "rolled_back": False,
            "reason": "no distinct previous or challenger bundle is available",
        }
        _publish_outcome_evidence(args, report, monitoring_dir)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    champion_dir = args.models_root / "bundles" / champion_id
    comparison_dir = args.models_root / "bundles" / str(comparison_id)
    verify_bundle_dir(champion_dir)
    verify_bundle_dir(comparison_dir)
    result = evaluate_realized_outcomes(
        pd.read_parquet(ledger_path),
        args.training_dir,
        champion_dir,
        comparison_dir,
        champion_id=champion_id,
        comparison_id=str(comparison_id),
        min_common_rows=args.min_common_rows,
    )
    rolled_back = bool(result.get("rollback_recommended"))
    if rolled_back:
        now = datetime.now(timezone.utc).isoformat()
        decision = {
            "action": "automatic_rollback",
            "evaluated_at": now,
            "from_bundle_id": champion_id,
            "to_bundle_id": comparison_id,
            "outcome_metrics": {
                "champion": result.get("champion"),
                "comparison": result.get("comparison"),
                "reasons": result.get("rollback_reasons"),
            },
        }
        _atomic_json(
            control_dir / "champion.json",
            create_signed_control_pointer(
                bundle_id=str(comparison_id),
                previous_bundle_id=champion_id,
                decision=decision,
            ),
        )
        history = list(registry.get("history") or [])
        history.append(decision)
        _atomic_json(
            control_dir / "registry.json",
            create_signed_registry(
                champion_bundle_id=str(comparison_id),
                challenger_bundle_id=champion_id,
                previous_bundle_id=champion_id,
                decision=decision,
                history=history,
            ),
        )
    report = {
        "schema": "quantiv.model-outcome-monitor.v1",
        "evaluated_at": evaluated_at,
        "rolled_back": rolled_back,
        **result,
    }
    _publish_outcome_evidence(args, report, monitoring_dir)
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    decision = subparsers.add_parser("decide", help="Evaluate and register a candidate bundle")
    decision.add_argument("--models-root", type=Path, default=REPO_ROOT / "data" / "models")
    decision.add_argument("--training-dir", type=Path, default=REPO_ROOT / "data" / "ml_training")
    decision.add_argument("--candidate-manifest", type=Path, required=True)
    decision.add_argument("--candidate-forecast", type=Path, required=True)
    decision.add_argument(
        "--production-forecast-dir",
        type=Path,
        default=REPO_ROOT / "data" / "forecasts",
    )
    decision.add_argument(
        "--report",
        type=Path,
        default=REPO_ROOT / "data" / "validation" / "model_decision.json",
    )
    decision.set_defaults(handler=decide)
    monitoring = subparsers.add_parser("monitor", help="Record champion and shadow predictions")
    monitoring.add_argument("--models-root", type=Path, default=REPO_ROOT / "data" / "models")
    monitoring.add_argument("--forecast-dir", type=Path, default=REPO_ROOT / "data" / "forecasts")
    monitoring.add_argument("--forecast-path", type=Path, default=None)
    monitoring.set_defaults(handler=monitor)
    outcomes = subparsers.add_parser(
        "evaluate-outcomes", help="Evaluate realized residuals and roll back a bad champion"
    )
    outcomes.add_argument("--models-root", type=Path, default=REPO_ROOT / "data" / "models")
    outcomes.add_argument("--training-dir", type=Path, default=REPO_ROOT / "data" / "ml_training")
    outcomes.add_argument("--min-common-rows", type=int, default=30)
    outcomes.add_argument("--monitoring-report", type=Path, default=None)
    outcomes.add_argument("--history", type=Path, default=None)
    outcomes.add_argument("--history-limit", type=int, default=52)
    outcomes.add_argument(
        "--report",
        type=Path,
        default=REPO_ROOT / "data" / "validation" / "model_outcomes.json",
    )
    outcomes.set_defaults(handler=evaluate_outcomes)
    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
