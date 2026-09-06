#!/usr/bin/env python3
"""Roll back a champion whose promotion provenance was invalid.

This is intentionally narrower than performance-based automatic rollback. The target
must be the signed current pointer's exact ``previous_bundle_id`` and callers must also
name the expected current champion, so an operator cannot select an arbitrary model or
race a newer promotion.
"""

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
    create_signed_registry,
    verify_bundle_dir,
    verify_control_pointer,
    verify_registry,
)


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"expected a JSON object at {path}")
    return payload


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def _promote_forecast(
    candidate_path: Path,
    forecast_dir: Path,
    *,
    expected_bundle_id: str,
) -> Path:
    frame = pd.read_parquet(candidate_path)
    if frame.empty:
        raise ValueError("provenance rollback forecast is empty")
    if "model_bundle_id" not in frame.columns or "snapshot_date" not in frame.columns:
        raise ValueError("provenance rollback forecast lacks bundle or snapshot identity")
    bundle_ids = set(frame["model_bundle_id"].dropna().astype(str))
    if bundle_ids != {expected_bundle_id}:
        raise ValueError(
            f"rollback forecast bundles {sorted(bundle_ids)} do not match target "
            f"{expected_bundle_id}"
        )
    snapshots = pd.to_datetime(frame["snapshot_date"], errors="raise")
    if snapshots.isna().any():
        raise ValueError("provenance rollback forecast contains a blank snapshot date")
    snapshot = snapshots.max().date().isoformat()
    forecast_dir.mkdir(parents=True, exist_ok=True)
    destination = forecast_dir / f"forecasts_{snapshot}.parquet"
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    shutil.copy2(candidate_path, temporary)
    temporary.replace(destination)
    return destination


def provenance_rollback(
    *,
    models_root: Path,
    expected_current_bundle_id: str,
    target_bundle_id: str,
    candidate_forecast: Path,
    production_forecast_dir: Path,
    report_path: Path,
    reason: str,
    private_key: str | bytes | None = None,
    public_key: str | bytes | None = None,
) -> dict[str, Any]:
    if not reason.strip():
        raise ValueError("provenance rollback requires a non-empty reason")

    control_dir = models_root / "control"
    pointer_path = control_dir / "champion.json"
    registry_path = control_dir / "registry.json"
    pointer = verify_control_pointer(_read_json(pointer_path), public_key=public_key)
    registry = verify_registry(_read_json(registry_path), public_key=public_key)

    current_id = str(pointer["champion_bundle_id"])
    previous_id = pointer.get("previous_bundle_id")
    if current_id != expected_current_bundle_id:
        raise RuntimeError(
            "current champion changed since the rollback was authorized: "
            f"expected={expected_current_bundle_id}, actual={current_id}"
        )
    if target_bundle_id == current_id:
        raise RuntimeError("rollback target is already the champion")
    if previous_id != target_bundle_id:
        raise RuntimeError(
            "rollback target is not the signed previous champion: "
            f"target={target_bundle_id}, signed_previous={previous_id}"
        )
    if registry.get("champion_bundle_id") != current_id:
        raise RuntimeError("signed registry champion disagrees with the control pointer")
    if registry.get("previous_bundle_id") != target_bundle_id:
        raise RuntimeError("signed registry previous bundle disagrees with the control pointer")

    current_dir = models_root / "bundles" / current_id
    target_dir = models_root / "bundles" / target_bundle_id
    verify_bundle_dir(current_dir, public_key=public_key)
    verify_bundle_dir(target_dir, public_key=public_key)

    production_forecast = _promote_forecast(
        candidate_forecast,
        production_forecast_dir,
        expected_bundle_id=target_bundle_id,
    )
    evaluated_at = datetime.now(timezone.utc).isoformat()
    decision_summary = {
        "action": "operator_provenance_rollback",
        "evaluated_at": evaluated_at,
        "from_bundle_id": current_id,
        "to_bundle_id": target_bundle_id,
        "reason": reason.strip(),
    }

    new_pointer = create_signed_control_pointer(
        bundle_id=target_bundle_id,
        previous_bundle_id=current_id,
        decision=decision_summary,
        private_key=private_key,
    )
    history = list(registry.get("history") or [])
    history.append(
        {
            **decision_summary,
            "champion_bundle_id": target_bundle_id,
        }
    )
    new_registry = create_signed_registry(
        champion_bundle_id=target_bundle_id,
        challenger_bundle_id=current_id,
        previous_bundle_id=current_id,
        decision=decision_summary,
        history=history,
        private_key=private_key,
    )

    _atomic_json(pointer_path, new_pointer)
    _atomic_json(registry_path, new_registry)
    report = {
        "schema": "quantiv.model-decision.v1",
        "status": "passed",
        "evaluated_at": evaluated_at,
        "action": "operator_provenance_rollback",
        "promoted": False,
        "candidate_bundle_id": None,
        "champion_bundle_id": target_bundle_id,
        "previous_bundle_id": current_id,
        "challenger_bundle_id": current_id,
        "production_forecast": str(production_forecast),
        "reasons": [reason.strip()],
    }
    _atomic_json(report_path, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-root", type=Path, default=REPO_ROOT / "data" / "models")
    parser.add_argument("--expected-current-bundle-id", required=True)
    parser.add_argument("--target-bundle-id", required=True)
    parser.add_argument("--candidate-forecast", type=Path, required=True)
    parser.add_argument(
        "--production-forecast-dir",
        type=Path,
        default=REPO_ROOT / "data" / "forecasts",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=REPO_ROOT / "data" / "validation" / "model_decision.json",
    )
    parser.add_argument("--reason", required=True)
    args = parser.parse_args()

    report = provenance_rollback(
        models_root=args.models_root,
        expected_current_bundle_id=args.expected_current_bundle_id,
        target_bundle_id=args.target_bundle_id,
        candidate_forecast=args.candidate_forecast,
        production_forecast_dir=args.production_forecast_dir,
        report_path=args.report,
        reason=args.reason,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
