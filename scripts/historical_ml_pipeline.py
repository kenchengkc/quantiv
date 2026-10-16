#!/usr/bin/env python3
"""Run the reviewed historical admission, keeping fitting and activation separate."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps/ml"), str(ROOT / "scripts")]


def _run(*args: str, env: dict | None = None, check: bool = True, **kwargs):
    return subprocess.run(list(args), cwd=ROOT, env=env, check=check, **kwargs)


def _python(script: str, *args: str, **kwargs):
    return _run(sys.executable, script, *map(str, args), **kwargs)


def _write(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")


def _bundle_id(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("bundle identity must be exactly 64 lowercase hexadecimal characters")
    return value


def restore_reservation_registry(data: Path, remote: str) -> Path:
    registry = data / "models/historical_reservations"
    listing = _run("rclone", "lsf", f"{remote}/models/", "--dirs-only",
                   capture_output=True, text=True)
    if "historical_reservations/" in listing.stdout.splitlines():
        _run("rclone", "copy", f"{remote}/models/historical_reservations", str(registry))
    registry.mkdir(parents=True, exist_ok=True)
    return registry


def publish_reservation_registry(registry: Path, remote: str):
    destination = f"{remote}/models/historical_reservations"
    _run("rclone", "copy", str(registry), destination, "--immutable", "--checksum")
    _run("rclone", "check", str(registry), destination, "--one-way", "--download")


def prepare_data(data: Path):
    from market_sessions import latest_completed_us_market_session

    # This job shares the daily execution lock; neither fitting nor evaluation
    # holds that lock while the normal refresh needs it.
    _python("scripts/sync_dolthub.py", "--ohlcv")
    _python("scripts/sync_dolthub.py", "--corporate-actions", "--end-date",
            latest_completed_us_market_session().isoformat())
    _python("scripts/sync_dolthub.py", "--volhist")
    _python("scripts/sync_vix.py")
    _python("scripts/data_release.py", "build", "--data-dir", data)
    _python("scripts/verify_historical_training_gate.py", "--data-dir", data,
            "--report", data / "validation/historical_training_admission.json")
    _python("scripts/setup_duckdb_from_parquet.py")
    _python("scripts/build_data_reconciliation.py", "--max-lag-days", "5", "--days-ahead", "21",
            "--report", data / "validation/data_reconciliation.json")
    _run("bash", "scripts/r2_push.sh", "--skip-forecasts")


def fit(data: Path, revision: str):
    from ml.model_bundle import DAILY_HORIZONS, create_historically_admitted_bundle
    from ml.pipeline_validation import PipelineValidationError, validate_model_artifacts
    from ml.historical_admission import (
        claim_historical_candidate, prepare_historical_admission, evaluate_historical_admission,
    )
    from archive_model_candidate import prepare_candidate_evidence, archive_candidate

    _python("scripts/verify_historical_training_gate.py", "--data-dir", data,
            "--report", data / "validation/historical_training_admission.json")
    _python("scripts/setup_duckdb_from_parquet.py")
    _python("apps/ml/feature_engineering.py", "--start-date", "2023-01-01",
            "--end-date", datetime.now(timezone.utc).date().isoformat())
    remote = f"r2:{os.environ.get('R2_BUCKET') or 'quantiv-data'}"
    registry = restore_reservation_registry(data, remote)
    workspace = data / "historical_admission/workspace"
    reservation, _ = prepare_historical_admission(
        source_training_dir=data / "ml_training", training_dir=workspace / "ml_training",
        staging_dir=data / "historical_admission/sealed", horizons=list(DAILY_HORIZONS),
        source_admission_report=data / "validation/historical_training_admission.json",
        source_data_dir=data, reservation_registry=registry,
    )
    # Persist the signed test membership before the trainer can start.
    publish_reservation_registry(registry, remote)
    environment = {**os.environ, "DATA_DIR": str(workspace)}
    _python("scripts/validate_ml_pipeline.py", "training", "--report", workspace / "validation/training.json", env=environment)
    _python("apps/ml/model_trainer.py", "--tune", "--time-decay", "0.5", env=environment)
    # Development-only head selection happens before any sealed test labels are read.
    _python("scripts/validate_walk_forward.py", "--training-dir", workspace / "ml_training",
            "--models-dir", workspace / "models", "--report", workspace / "validation/walk_forward.json",
            env=environment, check=False)
    selected, development = [], {}
    for horizon in DAILY_HORIZONS:
        try:
            result = validate_model_artifacts(workspace / "models", horizons=[horizon],
                                              training_dir=workspace / "ml_training")
            selected.append(horizon)
        except PipelineValidationError as exc:
            result = exc.as_dict()
        development[str(horizon)] = result
    _write(data / "validation/historical_development.json", development)
    if not selected:
        _write(data / "validation/historical_fit.json", {"status": "held", "reason": "no head passed development validation"})
        return
    models = workspace / "models"
    _python("scripts/validate_ml_pipeline.py", "models", "--horizons", *map(str, selected),
            "--report", workspace / "validation/models.json", "--publish-receipt-dir", models / "receipts", env=environment)
    _python("scripts/package_model_bundle.py", "--models-dir", models,
            "--training-dir", workspace / "ml_training", "--horizons", *map(str, selected),
            "--validation-report", workspace / "validation/models.json",
            "--receipt", models / "receipts/latest_models.json", "--source-revision", revision,
            "--output-manifest", data / "validation/historical_source_bundle.json")
    record = json.loads((data / "validation/historical_source_bundle.json").read_text())
    source = Path(record["bundle_dir"])
    admission_dir = data / "models/historical_admissions" / source.name
    # Retain the exact fitted candidate before any reserved outcome is read.
    # An interrupted evaluation must be able to recover these same weights.
    (workspace / "validation").mkdir(parents=True, exist_ok=True)
    shutil.copy2(data / "validation/historical_training_admission.json", workspace / "validation/historical_training_admission.json")
    shutil.copytree(data / "validation/promotion", workspace / "validation/promotion", dirs_exist_ok=True)
    for name in ("earnings_calendar.csv", "earnings_calendar.parquet"):
        if (data / name).exists():
            shutil.copy2(data / name, workspace / name)
    evidence = prepare_candidate_evidence(bundle_dir=source, data_dir=workspace)
    archive_candidate(bundle_dir=source, remote=remote, evidence_dir=evidence)
    claim_historical_candidate(source, reservation, registry)
    # A durable exact-artifact claim must precede the first test prediction.
    publish_reservation_registry(registry, remote)
    receipt_path, receipt = evaluate_historical_admission(
        bundle_dir=source, reservation_path=reservation,
        model_receipt=json.loads((models / "receipts/latest_models.json").read_text()),
        training_dir=workspace / "ml_training", output_dir=admission_dir,
        reservation_registry=registry)
    _run("rclone", "copy", str(admission_dir), f"{remote}/models/historical_admissions/{source.name}", "--immutable")
    _run("rclone", "check", str(admission_dir), f"{remote}/models/historical_admissions/{source.name}", "--one-way", "--download")
    result = {"status": receipt["status"], "source_bundle_id": source.name,
              "receipt": str(receipt_path), "admission_id": receipt["admission_id"],
              "admitted_cohorts": receipt["admitted_cohorts"]}
    if receipt["status"] == "passed":
        served, manifest = create_historically_admitted_bundle(source, data / "models/bundles",
            admission_receipt=receipt, source_revision=revision)
        archive_candidate(bundle_dir=served, remote=remote)
        result["bundle_id"] = manifest["bundle_id"]
    _write(data / "validation/historical_fit.json", result)


def activate(data: Path, bundle_id: str):
    from ml.model_bundle import verify_bundle_dir

    bundle_id = _bundle_id(bundle_id)
    remote = f"r2:{os.environ.get('R2_BUCKET') or 'quantiv-data'}"
    bundle = data / "models/bundles" / bundle_id
    _run("rclone", "copy", f"{remote}/models/bundles/{bundle_id}", str(bundle))
    manifest = verify_bundle_dir(bundle)
    source_id = _bundle_id(manifest["historical_admission"]["source_manifest"]["bundle_id"])
    for relative in (f"models/bundles/{source_id}", f"models/candidates/{source_id}", f"models/historical_admissions/{source_id}"):
        _run("rclone", "copy", f"{remote}/{relative}", str(data / relative))
    candidate = data / "validation/historical_serving_candidate.json"
    _write(candidate, {"bundle_id": bundle_id, "bundle_dir": str(bundle), "receipt_id": manifest["receipt_id"]})
    _python("scripts/setup_duckdb_from_parquet.py")
    forecast = data / "validation/historical_candidate_forecasts.parquet"
    _python("scripts/daily_score.py", "--days-ahead", "21", "--models-dir", bundle, "--output-path", forecast)
    champion_forecast = data / "validation/historical_champion_forecasts.parquet"
    pointer = data / "models/control/champion.json"
    champion_args = []
    if pointer.is_file():
        from ml.model_bundle import verify_control_pointer

        champion_id = _bundle_id(verify_control_pointer(json.loads(pointer.read_text()))["champion_bundle_id"])
        _python("scripts/daily_score.py", "--days-ahead", "21",
                "--models-dir", data / "models/bundles" / champion_id,
                "--output-path", champion_forecast)
        champion_args = ["--champion-forecast", champion_forecast]
    gate = data / "validation/historical_activation.json"
    _python("scripts/verify_retrain_data_gate.py", "--forecast-path", forecast, "--report", gate, "--allow-hold")
    # Retain this exact qualified candidate even if current prices are still held.
    _python("scripts/model_control_plane.py", "register", "--candidate-manifest", candidate,
            "--report", data / "validation/historical_registration.json")
    _run("bash", "scripts/r2_push.sh", "--candidate-control-only")
    decision = data / "validation/historical_decision.json"
    _python("scripts/model_control_plane.py", "decide", "--candidate-manifest", candidate,
            "--candidate-forecast", forecast, *champion_args,
            "--activation-gate-report", gate, "--report", decision)
    result = json.loads(decision.read_text())
    if not result["promoted"]:
        _run("bash", "scripts/r2_push.sh", "--candidate-control-only")
        return
    production = result["production_forecast"]
    _python("scripts/validate_ml_pipeline.py", "forecasts", "--models-dir", bundle,
            "--forecast-path", production, "--report", data / "validation/historical_production.json",
            "--publish-receipt-dir", data / "forecasts/receipts")
    _python("scripts/verify_retrain_data_gate.py", "--forecast-path", production, "--report", gate)
    _run("bash", "scripts/r2_push.sh", "--model-recovery")
    activation = data / "validation/historical_serving_activation.json"
    _python("scripts/activate_model_bundle.py", "--decision", decision, "--receipt", activation)
    _python("scripts/import_recent_to_postgres.py", "--file", production, "--full", "--require-database",
            "--expected-model-bundle-id", bundle_id, "--activation-receipt", activation,
            "--receipt", data / "validation/historical_forecast_import.json")
    # A fresh daily-style monitor issues the publication authorization and preserves rollback evidence.
    _python("scripts/model_publication.py", "snapshot")
    _python("scripts/model_publication.py", "monitor", "--forecast-path", production)
    _run("bash", "scripts/r2_push.sh", "--monitoring-only")
    _run("bash", "scripts/r2_push.sh", "--forecasts-only")
    environment = {**os.environ, "MODEL_PUBLICATION_FORECAST_PATH": production}
    _run("bash", "scripts/run_provider_free.sh", sys.executable, "tools/build_frontend_data.py", "--verify-model-publication", env=environment)
    _python("scripts/restore_reported_forecasts.py", "--apply")
    _python("tools/build_control_plane_snapshot.py")
    _python("tools/validate_public_contracts.py")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare-data", "fit", "activate"))
    parser.add_argument("--source-revision", default=os.environ.get("GITHUB_SHA", "local"))
    parser.add_argument("--bundle-id")
    args = parser.parse_args()
    data = Path(os.environ.get("DATA_DIR", ROOT / "data")).resolve()
    if args.phase == "prepare-data":
        prepare_data(data)
    elif args.phase == "fit":
        fit(data, args.source_revision)
    else:
        activate(data, _bundle_id(args.bundle_id or ""))


if __name__ == "__main__":
    main()
