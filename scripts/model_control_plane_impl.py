#!/usr/bin/env python3
"""Operate Quantiv's file-backed model control plane without Redis or Neon."""

from __future__ import annotations

import argparse
import json
import os
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
    verify_outcome_receipt,
    verify_registry,
)
from ml.model_control import (  # noqa: E402
    append_prediction_ledger,
    compare_on_common_holdout,
    compare_prospective_outcomes,
    rolling_cohort_drift_report,
    evaluate_realized_outcomes,
    monitoring_rows,
    shadow_score_report,
    update_outcome_history,
)
from ml.pipeline_validation import (  # noqa: E402
    latest_forecast_path,
    validate_forecast_artifact,
    PipelineValidationError,
)
from ml.model_artifact import sha256_file  # noqa: E402
from ml.model_protocol import feature_protocol, target_protocol  # noqa: E402


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n"
    )
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
    snapshot = (
        pd.to_datetime(frame["snapshot_date"], errors="raise").max().date().isoformat()
    )
    forecast_dir.mkdir(parents=True, exist_ok=True)
    destination = forecast_dir / f"forecasts_{snapshot}.parquet"
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    shutil.copy2(candidate_path, temporary)
    temporary.replace(destination)
    return destination


def _control_state(models_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    control = models_root / "control"
    pointer = (
        verify_control_pointer(_read_json(control / "champion.json"))
        if (control / "champion.json").exists()
        else {}
    )
    registry = (
        verify_registry(_read_json(control / "registry.json"))
        if (control / "registry.json").exists()
        else {}
    )
    if (
        pointer
        and registry
        and pointer["champion_bundle_id"] != registry["champion_bundle_id"]
    ):
        raise ValueError("signed registry and champion pointer disagree")
    return pointer, registry


def _has_complete_candidate_evidence(
    models_root: Path, bundle_id: str, horizons: list[int]
) -> bool:
    from ml.candidate_evidence import verify_candidate_evidence, verify_candidate_bindings

    root = models_root / "candidates" / bundle_id
    try:
        payload = verify_candidate_evidence(root, bundle_id)
        bundle = verify_bundle_dir(models_root / "bundles" / bundle_id)
        verify_candidate_bindings(root, bundle)
        observed = {row["path"] for row in payload["members"]}
        required = {
            "promotion/temporal_integrity.json",
            "promotion/statistical_selection.json",
            "source/feature_engineering.py",
            "source/causal_features.py",
            "source/model_protocol.py",
            "source/corporate_actions.py",
            "source/market_sessions.json",
            "model_validation_receipt.json",
            "historical_admission.json",
            "inputs/earnings_calendar.csv",
        }
        for horizon in horizons:
            required.update(
                {
                    f"training/training_T{horizon}.parquet",
                    f"training/metadata_T{horizon}.json",
                }
            )
        return required.issubset(observed)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError):
        return False


def _retain_candidate(args: argparse.Namespace) -> dict[str, Any]:
    record = _read_json(args.candidate_manifest)
    incoming = str(record["bundle_id"])
    manifest = verify_bundle_dir(Path(record["bundle_dir"]))
    if incoming != manifest["bundle_id"]:
        raise ValueError("candidate record and signed manifest disagree")
    incoming_metadata = _read_json(
        Path(record["bundle_dir"]) / f"metadata_T{int(manifest['horizons'][0])}.json"
    )
    if feature_protocol(incoming_metadata) == "quantiv.earnings-causal.v2":
        if not _has_complete_candidate_evidence(
            args.models_root, incoming, manifest["horizons"]
        ):
            raise ValueError(
                "current-protocol candidate requires verified complete archived evidence"
            )
        durable_bundle = args.models_root / "bundles" / incoming
        if verify_bundle_dir(durable_bundle)["bundle_id"] != incoming:
            raise ValueError("durable candidate bundle and incoming record disagree")
    pointer, registry = _control_state(args.models_root)
    frozen = registry.get("challenger_bundle_id")
    migration_reason = None
    if frozen:
        old_dir = args.models_root / "bundles" / str(frozen)
        old = verify_bundle_dir(old_dir)
        horizon = int(old["horizons"][0])
        old_metadata = _read_json(old_dir / f"metadata_T{horizon}.json")
        if feature_protocol(old_metadata) != "quantiv.earnings-causal.v2":
            migration_reason = "existing challenger uses legacy feature protocol"
        elif not _has_complete_candidate_evidence(
            args.models_root, str(frozen), old["horizons"]
        ):
            migration_reason = (
                "existing challenger lacks verified complete archived evidence"
            )
        if migration_reason:
            frozen = None
    frozen = str(frozen or incoming)
    evaluation_dir = (
        Path(record["bundle_dir"])
        if frozen == incoming
        else args.models_root / "bundles" / frozen
    )
    evaluation_manifest = verify_bundle_dir(evaluation_dir)
    evaluated_at = datetime.now(timezone.utc).isoformat()
    decision = {
        "action": "register_candidate",
        "candidate_bundle_id": incoming,
        "challenger_bundle_id": frozen,
        "evaluated_at": evaluated_at,
        "challenger_migration_reason": migration_reason,
    }
    history = list(registry.get("history") or [])
    history.append(decision)
    champion = pointer.get("champion_bundle_id")
    previous = pointer.get("previous_bundle_id")
    _atomic_json(
        args.models_root / "control" / "registry.json",
        create_signed_registry(
            champion_bundle_id=champion,
            challenger_bundle_id=frozen,
            previous_bundle_id=previous,
            decision=decision,
            history=history,
        ),
    )
    evaluation_record = args.report.parent / "evaluation_candidate.json"
    _atomic_json(
        evaluation_record,
        {
            "bundle_id": frozen,
            "bundle_dir": str(evaluation_dir),
            "receipt_id": evaluation_manifest["receipt_id"],
        },
    )
    return {
        "schema": "quantiv.model-decision.v1",
        "status": "passed",
        **decision,
        "promoted": False,
        "champion_bundle_id": champion,
        "previous_bundle_id": previous,
        "evaluation_bundle_dir": str(evaluation_dir),
        "evaluation_manifest": str(evaluation_record),
        "evaluation_evidence_dir": str(args.models_root / "candidates" / frozen),
        "production_forecast": None,
        "reasons": ["frozen challenger retained for unseen paired evidence"],
    }


def register_candidate(args: argparse.Namespace) -> int:
    report = _retain_candidate(args)
    _atomic_json(args.report, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def _activation_gate(args: argparse.Namespace) -> dict[str, Any]:
    path = getattr(args, "activation_gate_report", None)
    if path is None:
        return {
            "status": "held",
            "reason": "current activation gate evidence is required",
        }
    report = _read_json(path)
    if report.get("status") != "passed":
        return {
            "status": "held",
            "reason": report.get("reason") or "current activation gate is held",
        }
    # Re-read the current immutable release contract immediately before activation.
    from verify_retrain_data_gate import verify_retrain_data_gate

    try:
        return verify_retrain_data_gate(data_dir=args.models_root.parent)
    except RuntimeError as exc:
        return {"status": "held", "reason": str(exc)}


def _prospective_comparison(
    args: argparse.Namespace,
    candidate_dir: Path,
    champion_dir: Path,
    horizons: list[int],
    *,
    candidate_id: str,
    champion_id: str,
) -> dict[str, Any]:
    path = args.models_root / "monitoring" / "prediction_ledger.parquet"
    report: dict[str, Any] = {
        "status": "passed",
        "method": "prospective_paired_evidence",
        "horizons": {},
        "issues": [],
    }
    monitoring_report = path.parent / "latest_monitoring.json"
    receipt_path = path.parent / "latest_monitoring.receipt.json"
    if not all(item.is_file() for item in (path, monitoring_report, receipt_path)):
        return {
            **report,
            "status": "insufficient_data",
            "issues": ["signed prospective prediction ledger is unavailable"],
        }
    verify_monitor_receipt(
        _read_json(receipt_path), ledger_path=path, report_path=monitoring_report
    )
    ledger = pd.read_parquet(path)
    for horizon in horizons:
        training = args.training_dir / f"training_T{horizon}.parquet"
        labels = pd.read_parquet(training) if training.exists() else pd.DataFrame()
        # Only the corrected externally defined operational target is comparable.
        label_metadata = args.training_dir / f"metadata_T{horizon}.json"
        if (
            not label_metadata.exists()
            or target_protocol(_read_json(label_metadata))
            != "quantiv.session-reaction.v2"
        ):
            result = {
                "status": "insufficient_data",
                "rows": 0,
                "issues": ["mature session-reaction labels are unavailable"],
            }
        else:
            result = compare_prospective_outcomes(
                ledger,
                labels,
                candidate_dir,
                champion_dir,
                candidate_id=candidate_id,
                champion_id=champion_id,
                horizon=horizon,
            )
        report["horizons"][str(horizon)] = result
        if result["status"] != "passed":
            report["status"] = (
                "failed"
                if result["status"] == "failed" or report["status"] == "failed"
                else "insufficient_data"
            )
            report["issues"].extend(
                f"T-{horizon}: {issue}" for issue in result["issues"]
            )
    report["evaluation_complete"] = bool(report["horizons"] and all(row.get("evaluation_complete") is True for row in report["horizons"].values()))
    report["failure_kind"] = "quality" if report["evaluation_complete"] and report["status"] == "failed" else None
    return report


def decide(args: argparse.Namespace) -> int:
    retained = _retain_candidate(args)
    candidate_id = retained["challenger_bundle_id"]
    candidate_dir = Path(retained["evaluation_bundle_dir"])
    manifest = verify_bundle_dir(candidate_dir)
    horizons = [int(h) for h in manifest["horizons"]]
    champion_id = retained["champion_bundle_id"]
    champion_dir = args.models_root / "bundles" / champion_id if champion_id else None
    gate = _activation_gate(args)
    reasons = []
    drift = comparison = shadow = prospective = None
    forecast_validation = None
    candidate_forecasts = None
    if gate["status"] != "passed":
        reasons.append(gate["reason"])
    else:
        path = getattr(args, "candidate_forecast", None)
        if path is not None and path.exists():
            try:
                forecast_validation = validate_forecast_artifact(
                    path, models_dir=candidate_dir
                )
            except PipelineValidationError as exc:
                forecast_validation = exc.as_dict()
            if forecast_validation["status"] == "passed":
                candidate_forecasts = pd.read_parquet(path)
            else:
                reasons.append("current candidate forecast validation failed")
        if candidate_forecasts is None or candidate_forecasts.empty:
            reasons.append(
                "no upcoming candidate forecasts; frozen challenger retained"
            )
        else:
            served = set(candidate_forecasts["model_bundle_id"].dropna().astype(str))
            if served != {candidate_id}:
                raise ValueError(
                    "candidate forecast does not target the frozen challenger"
                )
            drift = _rolling_drift_assessment(
                candidate_forecasts, candidate_dir, bundle_id=candidate_id,
                models_root=args.models_root, horizons=horizons,
            )
            if drift["status"] not in {"passed", "warning"}:
                reasons.append(f"candidate cohort drift evidence is {drift['status']}")
            if champion_dir is not None:
                comparison = compare_on_common_holdout(
                    candidate_dir, champion_dir, args.training_dir, horizons=horizons
                )
                prospective = _prospective_comparison(
                    args,
                    candidate_dir,
                    champion_dir,
                    horizons,
                    candidate_id=candidate_id,
                    champion_id=champion_id,
                )
                evidence = (
                    comparison if comparison["status"] == "passed" else prospective
                )
                if evidence["status"] != "passed":
                    reasons.extend(evidence["issues"])
                champion_path = getattr(args, "champion_forecast", None)
                champion_forecasts = (
                    pd.read_parquet(champion_path)
                    if champion_path is not None and champion_path.exists()
                    else None
                )
                if champion_forecasts is None:
                    reasons.append(
                        "protocol-specific champion shadow forecast is unavailable"
                    )
                else:
                    shadow = shadow_score_report(
                        candidate_forecasts,
                        champion_dir,
                        champion_forecasts=champion_forecasts,
                    )
                    if shadow["status"] != "passed":
                        reasons.extend(shadow["issues"])
            else:
                reasons.append(
                    "bootstrap requires independent paired activation evidence"
                )
    promoted = not reasons
    action = "promote" if promoted else "retain_champion"
    if promoted:
        # Reconciliation/freshness may have changed during scoring and evaluation.
        gate = _activation_gate(args)
        if gate["status"] != "passed":
            promoted, action = False, "retain_champion"
            reasons.append(gate["reason"])
    summary = {
        "action": action,
        "candidate_bundle_id": candidate_id,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "reasons": reasons,
    }
    production_forecast = None
    rejected_evidence = None
    if (not promoted and gate["status"] == "passed" and forecast_validation
            and forecast_validation["status"] == "passed" and drift
            and drift["status"] in {"passed", "warning"} and champion_dir is not None
            and not any(result and result["status"] == "passed" for result in (comparison, prospective))):
        rejected_evidence = next((result for result in (prospective, comparison)
                                  if result and result["status"] == "failed"
                                  and result.get("evaluation_complete") is True
                                  and result.get("failure_kind") == "quality"), None)
    if rejected_evidence:
        action = summary["action"] = "reject_challenger"
        summary["rejection"] = {"reason": "complete independent panel failed numeric quality gates",
                                "evidence": rejected_evidence}
        _, registry = _control_state(args.models_root)
        _atomic_json(args.models_root / "control/registry.json", create_signed_registry(
            champion_bundle_id=champion_id, challenger_bundle_id=None,
            previous_bundle_id=retained["previous_bundle_id"], decision=summary,
            history=[*(registry.get("history") or []), summary],
        ))
    if promoted:
        production_forecast = _promote_forecast(
            args.candidate_forecast, args.production_forecast_dir
        )
        _atomic_json(
            args.models_root / "control" / "champion.json",
            create_signed_control_pointer(
                bundle_id=candidate_id, previous_bundle_id=champion_id, decision=summary
            ),
        )
        _, registry = _control_state_after_promotion(args.models_root)
        _atomic_json(
            args.models_root / "control" / "registry.json",
            create_signed_registry(
                champion_bundle_id=candidate_id,
                challenger_bundle_id=None,
                previous_bundle_id=champion_id,
                decision=summary,
                history=[*(registry.get("history") or []), summary],
            ),
        )
    report = {
        **retained,
        **summary,
        "promoted": promoted,
        "activation_gate": gate,
        "champion_bundle_id": candidate_id if promoted else champion_id,
        "previous_bundle_id": champion_id
        if promoted
        else retained["previous_bundle_id"],
        "challenger_bundle_id": None if promoted or rejected_evidence else candidate_id,
        "challenger_rejected": rejected_evidence is not None,
        "rejection_evidence": rejected_evidence,
        "production_forecast": str(production_forecast)
        if production_forecast
        else None,
        "candidate_forecast_validation": forecast_validation,
        "common_holdout": comparison,
        "prospective_evidence": prospective,
        "shadow_scoring": shadow,
        "feature_drift": drift,
    }
    _atomic_json(args.report, report)
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


def _control_state_after_promotion(
    models_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    # During the two atomic pointer writes the old registry intentionally precedes the new pointer.
    return {}, verify_registry(_read_json(models_root / "control" / "registry.json"))


def drift_reference_cohort(
    forecasts: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Select rows comparable to the champion's strict-option training reference.

    Optionless ML rows are valid live forecasts after the independent-scoring
    change, but their intentionally missing strict-option features are not
    comparable to a training reference built from decision-eligible straddles.
    Keep those rows in shadow scoring while excluding them from PSI/missingness
    drift against that reference.
    """
    if "em_math_pct" not in forecasts.columns:
        raise ValueError(
            "forecast artifact lacks em_math_pct for drift cohort selection"
        )

    em_math = pd.to_numeric(forecasts["em_math_pct"], errors="coerce")
    strict_mask = em_math.notna() & (em_math > 0) & (em_math < float("inf"))
    cohort = forecasts.loc[strict_mask].copy()

    by_horizon: dict[str, dict[str, int]] = {}
    horizons = pd.to_numeric(forecasts["model_horizon"], errors="coerce")
    for horizon in sorted(horizons.dropna().astype(int).unique()):
        horizon_mask = horizons == horizon
        rows = int(horizon_mask.sum())
        strict_rows = int((horizon_mask & strict_mask).sum())
        by_horizon[str(horizon)] = {
            "rows": rows,
            "strict_option_rows": strict_rows,
            "optionless_rows": rows - strict_rows,
        }

    total_rows = len(forecasts)
    strict_rows = int(strict_mask.sum())
    optionless_rows = total_rows - strict_rows
    diagnostics = {
        "rows": total_rows,
        "strict_option_rows": strict_rows,
        "optionless_rows": optionless_rows,
        "optionless_share": optionless_rows / total_rows if total_rows else 0.0,
        "by_horizon": by_horizon,
    }
    return cohort, diagnostics


def monitor(args: argparse.Namespace) -> int:
    control_dir = args.models_root / "control"
    pointer = verify_control_pointer(_read_json(control_dir / "champion.json"))
    champion_id = str(pointer["champion_bundle_id"])
    champion_dir = args.models_root / "bundles" / champion_id
    champion_manifest = verify_bundle_dir(champion_dir)
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
        database = getattr(args, "database", None) or Path(
            os.getenv("DUCKDB_PATH", str(args.models_root.parent / "quantiv.duckdb"))
        )
        if not database.exists():
            shadows[role] = {
                "status": "insufficient_data",
                "issues": ["protocol-specific shadow source database is unavailable"],
            }
            continue
        import duckdb
        from daily_score import get_bundle_forecasts

        with duckdb.connect(str(database), read_only=True) as connection:
            shadow_forecasts = get_bundle_forecasts(
                connection, bundle_dir, getattr(args, "days_ahead", 21)
            )
        if shadow_forecasts.empty:
            shadows[role] = {
                "status": "insufficient_data",
                "issues": ["no protocol-specific shadow forecasts"],
            }
            continue
        shadows[role] = shadow_score_report(
            shadow_forecasts, champion_dir, champion_forecasts=forecasts
        )
        ledger_rows.append(
            monitoring_rows(
                shadow_forecasts,
                bundle_dir,
                bundle_id=str(bundle_id),
                role=role,
                use_served_predictions=True,
            )
        )

    monitoring_dir = args.models_root / "monitoring"
    ledger_path = monitoring_dir / "prediction_ledger.parquet"
    migration = _verify_prior_prediction_ledger(monitoring_dir)
    drift = _rolling_drift_assessment(
        forecasts, champion_dir, bundle_id=champion_id,
        models_root=args.models_root, horizons=champion_manifest["horizons"],
    )
    ledger = append_prediction_ledger(ledger_path, ledger_rows)
    report = {
        "schema": "quantiv.model-monitoring.v1",
        "status": "passed" if drift["status"] in {"passed", "warning"} else "failed",
        "monitored_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_date": str(pd.to_datetime(forecasts["snapshot_date"]).max().date()),
        "champion_bundle_id": champion_id,
        "forecast_path": str(forecast_path),
        "forecast_sha256": sha256_file(forecast_path),
        "published_forecasts_sha256": (
            sha256_file(args.forecast_dir.parent / ".published_forecasts/manifest.json")
            if (
                args.forecast_dir.parent / ".published_forecasts/manifest.json"
            ).is_file()
            else None
        ),
        "ledger_rows": len(ledger),
        "ledger_migration": migration,
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


def _rolling_drift_assessment(
    forecasts: pd.DataFrame, bundle_dir: Path, *, bundle_id: str,
    models_root: Path, horizons: list[int],
) -> dict[str, Any]:
    """Use only already authenticated observations before this run appends rows."""
    monitoring = models_root / "monitoring"
    ledger = monitoring / "prediction_ledger.parquet"
    report_path = monitoring / "latest_monitoring.json"
    receipt_path = monitoring / "latest_monitoring.receipt.json"
    history = None
    source = None
    if receipt_path.is_file():
        if not ledger.is_file() or not report_path.is_file():
            raise ValueError("previously signed monitoring state is incomplete")
        verified = verify_monitor_receipt(_read_json(receipt_path), ledger_path=ledger, report_path=report_path)
        history = pd.read_parquet(ledger)
        source = {"receipt_sha256": sha256_file(receipt_path),
                  "ledger_sha256": verified["ledger"]["sha256"],
                  "report_sha256": verified["report"]["sha256"]}
    report = rolling_cohort_drift_report(
        forecasts, bundle_dir, bundle_id=bundle_id, history=history, horizons=horizons,
    )
    report["rolling_evidence"]["history_authentication"] = "verified_prior_monitoring_receipt" if history is not None else "unavailable"
    report["rolling_evidence"]["history_source"] = source
    return report


def _verify_prior_prediction_ledger(monitoring_dir: Path) -> dict[str, Any] | None:
    """Never launder unverified historical predictions into a new signed receipt."""
    ledger = monitoring_dir / "prediction_ledger.parquet"
    report = monitoring_dir / "latest_monitoring.json"
    receipt = monitoring_dir / "latest_monitoring.receipt.json"
    if receipt.is_file():
        if not ledger.is_file() or not report.is_file():
            raise ValueError("previously signed monitoring state is incomplete")
        verify_monitor_receipt(
            _read_json(receipt), ledger_path=ledger, report_path=report
        )
        return None
    if not ledger.is_file():
        return None
    digest = sha256_file(ledger)
    quarantine = monitoring_dir / f"unverified_prediction_ledger_{digest}.parquet"
    ledger.replace(quarantine)
    return {
        "action": "quarantine_unsigned_legacy_ledger",
        "path": str(quarantine),
        "sha256": digest,
        "reason": "historical rows lack a verified monitoring receipt and are excluded from prospective evidence",
    }


def evaluate_outcomes(args: argparse.Namespace) -> int:
    control_dir = args.models_root / "control"
    monitoring_dir = args.models_root / "monitoring"
    ledger_path = monitoring_dir / "prediction_ledger.parquet"
    monitoring_report_path = monitoring_dir / "latest_monitoring.json"
    receipt_path = monitoring_dir / "latest_monitoring.receipt.json"
    evaluated_at = datetime.now(timezone.utc).isoformat()
    if not (
        ledger_path.exists()
        and monitoring_report_path.exists()
        and receipt_path.exists()
    ):
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
    comparison_id = registry.get("previous_bundle_id") or registry.get(
        "challenger_bundle_id"
    )
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
    gate = _activation_gate(args) if result.get("rollback_recommended") else None
    previously_served = (comparison_id == registry.get("previous_bundle_id")
                         and comparison_id == pointer.get("previous_bundle_id"))
    authorized = bool(
        result.get("rollback_recommended")
        and previously_served
        and gate
        and gate["status"] == "passed"
    )
    staged = authorized and bool(getattr(args, "stage_rollback", False))
    rolled_back = authorized and not staged
    if result.get("rollback_recommended") and not authorized:
        result["activation_hold"] = (
            gate
            if previously_served
            else {
                "status": "held",
                "reason": "challenger requires full promotion evidence",
            }
        )
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
        "rollback_staged": staged,
        "rollback_from_bundle_id": champion_id,
        "rollback_target_bundle_id": comparison_id,
        **result,
    }
    _publish_outcome_evidence(args, report, monitoring_dir)
    if (rolled_back or staged) and getattr(args, "activation_report", None):
        _atomic_json(args.activation_report, {
            "schema": "quantiv.model-decision.v1",
            "status": "held" if staged else "passed",
            "action": "proposed_automatic_rollback" if staged else "automatic_rollback",
            "promoted": False, "rolled_back": rolled_back,
            "rollback_staged": staged,
            "expected_current_bundle_id": champion_id,
            "champion_bundle_id": comparison_id,
            "previous_bundle_id": champion_id,
            "production_forecast": None,
            "activation_gate": gate,
            "outcome_report_sha256": sha256_file(args.report),
        })
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


def apply_rollback(args: argparse.Namespace) -> int:
    """Validate a staged signed recommendation before changing production state."""
    proposal = _read_json(args.rollback_decision)
    monitoring = args.models_root / "monitoring"
    outcome_path, history_path = monitoring / "latest_outcomes.json", monitoring / "outcome_history.json"
    verify_outcome_receipt(_read_json(monitoring / "latest_outcomes.receipt.json"),
                           report_path=outcome_path, history_path=history_path)
    outcome = _read_json(outcome_path)
    pointer, registry = _control_state(args.models_root)
    current, target = proposal.get("expected_current_bundle_id"), proposal.get("champion_bundle_id")
    if (proposal.get("rollback_staged") is not True or outcome.get("rollback_staged") is not True
            or proposal.get("outcome_report_sha256") != sha256_file(outcome_path)
            or outcome.get("rollback_from_bundle_id") != current
            or outcome.get("rollback_target_bundle_id") != target
            or pointer.get("champion_bundle_id") != current
            or pointer.get("previous_bundle_id") != target
            or registry.get("champion_bundle_id") != current
            or registry.get("previous_bundle_id") != target or not target or target == current):
        raise ValueError("rollback proposal is not bound to the current signed previous champion")
    target_dir = args.models_root / "bundles" / str(target)
    verify_bundle_dir(target_dir)
    forecast = pd.read_parquet(args.candidate_forecast)
    report = {**proposal, "rolled_back": False, "rollback_staged": True,
              "status": "held", "action": "retain_champion", "production_forecast": None,
              "champion_bundle_id": current, "previous_bundle_id": pointer.get("previous_bundle_id"),
              "rollback_target_bundle_id": target}
    gate = _activation_gate(args)
    report["activation_gate"] = gate
    if gate["status"] != "passed":
        report["reason"] = gate["reason"]
    else:
        try:
            validation = validate_forecast_artifact(
                args.candidate_forecast, models_dir=target_dir)
            if validation["status"] != "passed":
                report["reason"] = "rollback forecast validation held"
            elif (forecast.empty or "model_bundle_id" not in forecast
                  or forecast["model_bundle_id"].isna().any()
                  or not forecast["model_bundle_id"].eq(target).all()):
                report["reason"] = "rollback forecast does not contain exact target-bundle rows"
            else:
                gate = _activation_gate(args)
                report["activation_gate"] = gate
                if gate["status"] != "passed":
                    report["reason"] = gate["reason"]
                else:
                    decision = {"action": "automatic_rollback", "from_bundle_id": current,
                                "to_bundle_id": target, "outcome_report_sha256": proposal["outcome_report_sha256"]}
                    signed_pointer = create_signed_control_pointer(bundle_id=target, previous_bundle_id=current, decision=decision)
                    signed_registry = create_signed_registry(champion_bundle_id=target, previous_bundle_id=current,
                        challenger_bundle_id=current, decision=decision, history=[*(registry.get("history") or []), decision])
                    production = _promote_forecast(args.candidate_forecast, args.production_forecast_dir)
                    _atomic_json(args.models_root / "control/champion.json", signed_pointer)
                    _atomic_json(args.models_root / "control/registry.json", signed_registry)
                    report.update(status="passed", action="automatic_rollback", rolled_back=True,
                                  rollback_staged=False, champion_bundle_id=target, previous_bundle_id=current,
                                  production_forecast=str(production))
        except PipelineValidationError as exc:
            report["reason"] = f"rollback forecast validation held: {exc}"
    _atomic_json(args.report, report)
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    decision = subparsers.add_parser(
        "decide", help="Evaluate and register a candidate bundle"
    )
    decision.add_argument(
        "--models-root", type=Path, default=REPO_ROOT / "data" / "models"
    )
    decision.add_argument(
        "--training-dir", type=Path, default=REPO_ROOT / "data" / "ml_training"
    )
    decision.add_argument("--candidate-manifest", type=Path, required=True)
    decision.add_argument("--candidate-forecast", type=Path, default=None)
    decision.add_argument("--champion-forecast", type=Path, default=None)
    decision.add_argument("--activation-gate-report", type=Path, required=True)
    decision.add_argument("--archived-evidence-dir", type=Path, default=None)
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
    registration = subparsers.add_parser(
        "register", help="Retain a signed candidate without activating production"
    )
    registration.add_argument(
        "--models-root", type=Path, default=REPO_ROOT / "data" / "models"
    )
    registration.add_argument("--candidate-manifest", type=Path, required=True)
    registration.add_argument("--report", type=Path, required=True)
    registration.set_defaults(handler=register_candidate)
    monitoring = subparsers.add_parser(
        "monitor", help="Record champion and shadow predictions"
    )
    monitoring.add_argument(
        "--models-root", type=Path, default=REPO_ROOT / "data" / "models"
    )
    monitoring.add_argument(
        "--forecast-dir", type=Path, default=REPO_ROOT / "data" / "forecasts"
    )
    monitoring.add_argument("--forecast-path", type=Path, default=None)
    monitoring.add_argument("--database", type=Path, default=None)
    monitoring.add_argument("--days-ahead", type=int, default=21)
    monitoring.set_defaults(handler=monitor)
    outcomes = subparsers.add_parser(
        "evaluate-outcomes",
        help="Evaluate realized residuals and roll back a bad champion",
    )
    outcomes.add_argument(
        "--models-root", type=Path, default=REPO_ROOT / "data" / "models"
    )
    outcomes.add_argument(
        "--training-dir", type=Path, default=REPO_ROOT / "data" / "ml_training"
    )
    outcomes.add_argument("--min-common-rows", type=int, default=30)
    outcomes.add_argument("--activation-gate-report", type=Path, default=None)
    outcomes.add_argument("--activation-report", type=Path, default=None,
                          help="Write the exact automatic rollback decision for the independent serving handoff")
    outcomes.add_argument("--stage-rollback", action="store_true",
                          help="Record an authenticated recommendation without changing production controls")
    outcomes.add_argument("--monitoring-report", type=Path, default=None)
    outcomes.add_argument("--history", type=Path, default=None)
    outcomes.add_argument("--history-limit", type=int, default=52)
    outcomes.add_argument(
        "--report",
        type=Path,
        default=REPO_ROOT / "data" / "validation" / "model_outcomes.json",
    )
    outcomes.set_defaults(handler=evaluate_outcomes)
    rollback = subparsers.add_parser("apply-rollback", help="Apply a validated staged automatic rollback")
    rollback.add_argument("--models-root", type=Path, default=REPO_ROOT / "data/models")
    rollback.add_argument("--rollback-decision", type=Path, required=True)
    rollback.add_argument("--candidate-forecast", type=Path, required=True)
    rollback.add_argument("--activation-gate-report", type=Path, required=True)
    rollback.add_argument("--production-forecast-dir", type=Path, default=REPO_ROOT / "data/forecasts")
    rollback.add_argument("--report", type=Path, required=True)
    rollback.set_defaults(handler=apply_rollback)
    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
