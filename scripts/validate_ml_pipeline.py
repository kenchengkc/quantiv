#!/usr/bin/env python3
"""Run fail-closed gates between ML pipeline stages."""

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

from ml.pipeline_validation import (  # noqa: E402 - standalone script path setup
    DEFAULT_HORIZONS,
    PipelineValidationError,
    latest_forecast_path,
    validate_forecast_artifact,
    validate_model_artifacts,
    validate_training_artifacts,
)
from ml.evidence_receipt import (  # noqa: E402 - standalone script path setup
    build_evidence_receipt,
    publish_evidence_receipt,
)


def _write_report(path: Path | None, report: dict[str, Any]) -> None:
    payload = json.dumps(report, indent=2, sort_keys=True, default=str)
    print(payload)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate Quantiv ML pipeline artifacts"
    )
    parser.add_argument("stage", choices=("training", "models", "forecasts", "all"))
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--training-dir", type=Path, default=None)
    parser.add_argument("--models-dir", type=Path, default=None)
    parser.add_argument("--forecast-path", type=Path, default=None)
    parser.add_argument(
        "--horizons", nargs="+", type=int, default=list(DEFAULT_HORIZONS)
    )
    parser.add_argument("--min-training-rows", type=int, default=1_000)
    parser.add_argument("--min-symbols", type=int, default=20)
    parser.add_argument("--min-history-days", type=int, default=365)
    parser.add_argument("--max-forecast-age-days", type=int, default=2)
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument(
        "--publish-receipt-dir",
        type=Path,
        default=None,
        help="Also publish an immutable evidence receipt and latest pointer here.",
    )
    args = parser.parse_args()

    data_dir = args.data_dir or Path(os.getenv("DATA_DIR", REPO_ROOT / "data"))
    training_dir = args.training_dir or data_dir / "ml_training"
    models_dir = args.models_dir or data_dir / "models"
    forecast_path = args.forecast_path or latest_forecast_path(data_dir / "forecasts")

    stages = (
        [args.stage] if args.stage != "all" else ["training", "models", "forecasts"]
    )
    report: dict[str, Any] = {
        "status": "passed",
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "stages": {},
        "issues": [],
    }
    for stage in stages:
        try:
            if stage == "training":
                result = validate_training_artifacts(
                    training_dir,
                    horizons=args.horizons,
                    min_rows=args.min_training_rows,
                    min_symbols=args.min_symbols,
                    min_history_days=args.min_history_days,
                )
            elif stage == "models":
                result = validate_model_artifacts(
                    models_dir,
                    horizons=args.horizons,
                    training_dir=training_dir,
                    min_train_rows=args.min_training_rows,
                )
            else:
                if forecast_path is None:
                    raise PipelineValidationError([])
                result = validate_forecast_artifact(
                    forecast_path,
                    models_dir=models_dir,
                    max_age_days=args.max_forecast_age_days,
                )
            report["stages"][stage] = result
        except PipelineValidationError as exc:
            report["status"] = "failed"
            if not exc.issues and stage == "forecasts":
                report["issues"].append(
                    {
                        "stage": "forecasts",
                        "artifact": str(data_dir / "forecasts"),
                        "code": "missing_forecast_artifact",
                        "message": "no dated forecast parquet was found",
                    }
                )
            else:
                report["issues"].extend(exc.as_dict()["issues"])

    report["evidence_receipt"] = build_evidence_receipt(
        report,
        scope=args.stage,
        repo_root=REPO_ROOT,
        data_dir=data_dir,
        training_dir=training_dir,
        models_dir=models_dir,
        forecast_path=forecast_path,
        horizons=args.horizons,
    )
    _write_report(args.report, report)
    if args.publish_receipt_dir is not None:
        immutable_path, latest_path = publish_evidence_receipt(
            report,
            receipt_dir=args.publish_receipt_dir,
            scope=args.stage,
            forecast_path=forecast_path,
        )
        print(f"Published evidence receipts: {immutable_path}, {latest_path}")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
