#!/usr/bin/env python3
"""Run the mandatory walk-forward model gate and attach it to metadata."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ML_PACKAGE_ROOT = REPO_ROOT / "apps" / "ml"
if str(ML_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(ML_PACKAGE_ROOT))

from ml.pipeline_validation import DEFAULT_HORIZONS  # noqa: E402
from ml.walk_forward_validation import run_walk_forward_gate  # noqa: E402


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    data_dir = Path(os.getenv("DATA_DIR", REPO_ROOT / "data"))
    parser.add_argument("--training-dir", type=Path, default=data_dir / "ml_training")
    parser.add_argument("--models-dir", type=Path, default=data_dir / "models")
    parser.add_argument("--horizons", nargs="+", type=int, default=list(DEFAULT_HORIZONS))
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--test-days", type=int, default=60)
    parser.add_argument("--purge-days", type=int, default=5)
    parser.add_argument("--min-train-rows", type=int, default=1_000)
    parser.add_argument("--min-validation-rows", type=int, default=30)
    parser.add_argument("--max-worst-fold-ratio", type=float, default=1.50)
    parser.add_argument(
        "--report",
        type=Path,
        default=data_dir / "validation" / "retrain_walk_forward.json",
    )
    args = parser.parse_args()

    report = {
        "schema": "quantiv.walk-forward-validation.v1",
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "status": "passed",
        "horizons": {},
        "issues": [],
    }
    for horizon in args.horizons:
        training_path = args.training_dir / f"training_T{horizon}.parquet"
        metadata_path = args.models_dir / f"metadata_T{horizon}.json"
        try:
            import pandas as pd

            frame = pd.read_parquet(training_path)
            metadata = json.loads(metadata_path.read_text())
            result = run_walk_forward_gate(
                frame,
                metadata,
                folds=args.folds,
                test_days=args.test_days,
                purge_days=args.purge_days,
                min_train_rows=args.min_train_rows,
                min_validation_rows=args.min_validation_rows,
                max_worst_fold_ratio=args.max_worst_fold_ratio,
            )
            metadata["walk_forward_validation"] = result
            _atomic_json(metadata_path, metadata)
            report["horizons"][str(horizon)] = result
            if result["status"] != "passed":
                report["status"] = "failed"
                report["issues"].append(
                    {"horizon": horizon, "issues": result["issues"]}
                )
        except Exception as exc:
            report["status"] = "failed"
            report["issues"].append({"horizon": horizon, "issues": [str(exc)]})
    _atomic_json(args.report, report)
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
