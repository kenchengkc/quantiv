#!/usr/bin/env python3
"""Publish a validated model set as an immutable signed bundle."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ML_PACKAGE_ROOT = REPO_ROOT / "apps" / "ml"
if str(ML_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(ML_PACKAGE_ROOT))

from ml.model_bundle import (  # noqa: E402 - standalone script path setup
    create_signed_bundle,
)


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-dir", type=Path, default=REPO_ROOT / "data" / "models")
    parser.add_argument(
        "--validation-report",
        type=Path,
        default=REPO_ROOT / "data" / "validation" / "retrain_models.json",
    )
    parser.add_argument(
        "--receipt",
        type=Path,
        default=REPO_ROOT / "data" / "models" / "receipts" / "latest_models.json",
    )
    parser.add_argument("--source-revision", default=os.getenv("GITHUB_SHA", "local"))
    parser.add_argument(
        "--output-manifest",
        type=Path,
        default=None,
        help="Write the verified bundle id and local path for later workflow stages.",
    )
    args = parser.parse_args()

    bundle_dir, manifest = create_signed_bundle(
        args.models_dir,
        args.models_dir / "bundles",
        receipt_path=args.receipt,
        validation_report_path=args.validation_report,
        source_revision=args.source_revision,
    )
    print(f"Packaged signed model bundle: {manifest['bundle_id']}")
    if args.output_manifest is not None:
        _atomic_json(
            args.output_manifest,
            {
                "bundle_id": manifest["bundle_id"],
                "bundle_dir": str(bundle_dir),
                "receipt_id": manifest["receipt_id"],
            },
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
