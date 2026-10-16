#!/usr/bin/env python3
"""Reserve historical test events and admit exact native models before promotion."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps/ml"))
sys.path.insert(0, str(ROOT / "scripts"))

from ml.historical_admission import (  # noqa: E402
    DEFAULT_TEST_DAYS,
    claim_historical_candidate,
    evaluate_historical_admission,
    prepare_historical_admission,
    verify_historical_admission_receipt,
)
from ml.model_bundle import DAILY_HORIZONS  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="copy purged development rows and seal a temporal test")
    prepare.add_argument("--source-training-dir", type=Path, required=True)
    prepare.add_argument("--training-dir", type=Path, required=True)
    prepare.add_argument("--staging-dir", type=Path, required=True)
    prepare.add_argument("--source-admission-report", type=Path, required=True)
    prepare.add_argument("--source-data-dir", type=Path, required=True)
    prepare.add_argument("--reservation-registry", type=Path, required=True)
    prepare.add_argument("--horizons", type=int, nargs="+", default=list(DAILY_HORIZONS))
    prepare.add_argument("--as-of")
    prepare.add_argument("--test-days", type=int, default=DEFAULT_TEST_DAYS)
    prepare.add_argument("--min-development-rows", type=int, default=1000)
    evaluate = commands.add_parser("evaluate", help="score all six frozen native heads and sign passing cohort cells")
    evaluate.add_argument("--bundle-dir", type=Path, required=True)
    evaluate.add_argument("--reservation", type=Path, required=True)
    evaluate.add_argument("--model-receipt", type=Path, required=True)
    evaluate.add_argument("--training-dir", type=Path, required=True)
    evaluate.add_argument("--output-dir", type=Path, required=True)
    evaluate.add_argument("--reservation-registry", type=Path, required=True)
    claim = commands.add_parser("claim", help="commit the exact candidate before reading reserved outcomes")
    claim.add_argument("--bundle-dir", type=Path, required=True)
    claim.add_argument("--reservation", type=Path, required=True)
    claim.add_argument("--reservation-registry", type=Path, required=True)
    verify = commands.add_parser("verify", help="verify retained admission evidence and replay native predictions")
    verify.add_argument("--receipt", type=Path, required=True)
    verify.add_argument("--bundle-dir", type=Path, required=True)
    verify.add_argument("--evidence-root", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "prepare":
        path, reservation = prepare_historical_admission(
            source_training_dir=args.source_training_dir.resolve(),
            training_dir=args.training_dir.resolve(), staging_dir=args.staging_dir.resolve(),
            horizons=args.horizons, as_of=args.as_of, test_days=args.test_days,
            min_development_rows=args.min_development_rows,
            source_admission_report=args.source_admission_report.resolve(),
            source_data_dir=args.source_data_dir.resolve(), reservation_registry=args.reservation_registry.resolve())
        result = {"reservation": str(path), "reservation_id": reservation["reservation_id"],
                  "test_days": reservation["test_days"], "horizons": reservation["horizons"]}
    elif args.command == "claim":
        path, claim = claim_historical_candidate(args.bundle_dir.resolve(), args.reservation.resolve(),
            args.reservation_registry.resolve())
        result = {"claim": str(path), "panel_id": claim["panel_id"],
                  "native_candidate_id": claim["native_candidate_id"]}
    elif args.command == "evaluate":
        path, receipt = evaluate_historical_admission(
            bundle_dir=args.bundle_dir.resolve(), reservation_path=args.reservation.resolve(),
            model_receipt=json.loads(args.model_receipt.read_text()),
            training_dir=args.training_dir.resolve(), output_dir=args.output_dir.resolve(),
            reservation_registry=args.reservation_registry.resolve())
        result = {"receipt": str(path), "admission_id": receipt["admission_id"],
                  "status": receipt["status"], "admitted_cohorts": receipt["admitted_cohorts"]}
    else:
        receipt = verify_historical_admission_receipt(json.loads(args.receipt.read_text()),
            bundle_dir=args.bundle_dir.resolve(),
            evidence_root=(args.evidence_root or args.receipt.parent).resolve())
        result = {"admission_id": receipt["admission_id"], "status": "verified",
                  "admitted_cohorts": receipt["admitted_cohorts"]}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
