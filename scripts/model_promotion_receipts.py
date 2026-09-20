#!/usr/bin/env python3
"""Build and verify fail-closed evidence for model promotion.

The temporal receipt proves the training tables are bound to the declared T-N
pre-event feature contract and records the exact bytes used by retraining.  It
also states the provider-history limitation explicitly: Quantiv does not retain
a complete point-in-time history of every earnings announcement/revision.

The statistical-selection receipt describes the production selection rule.  The
champion/challenger control plane is metric/gate based, not a null-hypothesis
significance procedure, so the truthful multiple-testing disposition is
``not_applicable`` rather than manufactured p-values.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
FEATURE_ENGINEERING_PATH = REPO_ROOT / "apps" / "ml" / "feature_engineering.py"
DEFAULT_TRAINING_DIR = REPO_ROOT / "data" / "ml_training"
DEFAULT_TEMPORAL_RECEIPT = (
    REPO_ROOT / "data" / "validation" / "promotion" / "temporal_integrity.json"
)
DEFAULT_STATISTICAL_RECEIPT = (
    REPO_ROOT / "data" / "validation" / "promotion" / "statistical_selection.json"
)
SUPPORTED_HORIZONS = frozenset({1, 2, 3, 7, 14, 21})
_FORBIDDEN_FEATURE_PATTERNS = (
    re.compile(r"^post_", re.IGNORECASE),
    re.compile(r"future", re.IGNORECASE),
    re.compile(r"realized_move", re.IGNORECASE),
    re.compile(r"realized_source", re.IGNORECASE),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _with_receipt_id(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["receipt_id"] = _canonical_digest(payload)
    return result


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def _read_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object at {path}")
    return payload


def _repo_relative(path: Path) -> str:
    """Return a stable repository-relative path for absolute or CLI-relative input."""
    resolved = path.resolve()
    try:
        return resolved.relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError(f"{resolved} is outside repository root {REPO_ROOT.resolve()}") from exc


def _verify_receipt_identity(payload: dict[str, Any], *, label: str) -> None:
    receipt_id = payload.get("receipt_id")
    if not isinstance(receipt_id, str) or not re.fullmatch(r"[0-9a-f]{64}", receipt_id):
        raise ValueError(f"{label} has no valid receipt_id")
    unsigned = dict(payload)
    unsigned.pop("receipt_id", None)
    if _canonical_digest(unsigned) != receipt_id:
        raise ValueError(f"{label} receipt_id does not match its contents")


def _feature_engineering_contract() -> dict[str, str]:
    source = FEATURE_ENGINEERING_PATH.read_text()
    required = (
        "sf.date AS snapshot_date",
        "(e.earnings_date - sf.date) AS lead_days",
        'hdf = df[df["lead_days"] == horizon].copy()',
        'training_df["target"] = fs.target',
        'training_df["__earnings_date"] = fs.earnings_date.values',
    )
    missing = [token for token in required if token not in source]
    if missing:
        raise ValueError(
            "feature-engineering temporal contract changed without receipt support: "
            + ", ".join(missing)
        )
    return {
        "path": FEATURE_ENGINEERING_PATH.relative_to(REPO_ROOT).as_posix(),
        "sha256": _sha256(FEATURE_ENGINEERING_PATH),
        "snapshot_rule": "snapshot_date < earnings_date",
        "horizon_rule": "calendar lead_days == declared T-N horizon",
        "label_rule": "realized target is stored only in target and is not a model feature",
    }


def _training_artifacts(training_dir: Path) -> list[tuple[int, Path, Path]]:
    rows: list[tuple[int, Path, Path]] = []
    for path in sorted(training_dir.glob("training_T*.parquet")):
        match = re.fullmatch(r"training_T(\d+)\.parquet", path.name)
        if not match:
            continue
        horizon = int(match.group(1))
        if horizon not in SUPPORTED_HORIZONS:
            raise ValueError(f"unsupported training horizon T-{horizon}")
        metadata = training_dir / f"metadata_T{horizon}.json"
        if not metadata.is_file():
            raise ValueError(f"missing metadata for T-{horizon}: {metadata}")
        rows.append((horizon, path, metadata))
    if not rows:
        raise ValueError(f"no training_T*.parquet artifacts found under {training_dir}")
    return rows


def _inspect_training(horizon: int, path: Path, metadata: Path) -> dict[str, Any]:
    frame = pd.read_parquet(path)
    if frame.empty:
        raise ValueError(f"T-{horizon} training artifact is empty")
    if "target" not in frame.columns or "__earnings_date" not in frame.columns:
        raise ValueError(f"T-{horizon} is missing target/__earnings_date temporal metadata")

    feature_columns = [
        str(column)
        for column in frame.columns
        if column != "target" and not str(column).startswith("__")
    ]
    leaked = sorted(
        column
        for column in feature_columns
        if any(pattern.search(column) for pattern in _FORBIDDEN_FEATURE_PATTERNS)
    )
    if leaked:
        raise ValueError(f"T-{horizon} contains future/label-like feature columns: {leaked}")

    earnings = pd.to_datetime(frame["__earnings_date"], errors="coerce")
    if earnings.isna().any():
        raise ValueError(f"T-{horizon} has invalid __earnings_date values")

    snapshot_present = "__snapshot_date" in frame.columns
    if snapshot_present:
        snapshots = pd.to_datetime(frame["__snapshot_date"], errors="coerce")
        if snapshots.isna().any():
            raise ValueError(f"T-{horizon} has invalid __snapshot_date values")
        lead_days = (earnings - snapshots).dt.days
        bad = (lead_days != horizon) | (snapshots >= earnings)
        if bool(bad.any()):
            raise ValueError(
                f"T-{horizon} contains future or horizon-inconsistent feature snapshots"
            )
        decision_min = snapshots.min().date().isoformat()
        decision_max = snapshots.max().date().isoformat()
        proof = "row_level_snapshot_date"
    else:
        # Current training files retain event date but not the source snapshot
        # date.  The builder selected rows with lead_days == horizon, so derive
        # the decision dates from that exact source contract and bind its hash.
        derived = earnings.dt.date.map(lambda value: value - timedelta(days=horizon))
        decision_min = min(derived).isoformat()
        decision_max = max(derived).isoformat()
        proof = "feature_engineering_lead_days_contract"

    return {
        "horizon": horizon,
        "rows": int(len(frame)),
        "training": {
            "path": path.relative_to(REPO_ROOT).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        },
        "metadata": {
            "path": metadata.relative_to(REPO_ROOT).as_posix(),
            "bytes": metadata.stat().st_size,
            "sha256": _sha256(metadata),
        },
        "event_date_min": earnings.min().date().isoformat(),
        "event_date_max": earnings.max().date().isoformat(),
        "decision_date_min": decision_min,
        "decision_date_max": decision_max,
        "snapshot_proof": proof,
        "snapshot_metadata_present": snapshot_present,
        "feature_count": len(feature_columns),
        "violations": 0,
    }


def build_promotion_receipts(
    candidate_record_path: Path,
    *,
    training_dir: Path = DEFAULT_TRAINING_DIR,
    temporal_path: Path = DEFAULT_TEMPORAL_RECEIPT,
    statistical_path: Path = DEFAULT_STATISTICAL_RECEIPT,
    source_revision: str = "local",
) -> tuple[dict[str, Any], dict[str, Any]]:
    candidate = _read_object(candidate_record_path)
    bundle_id = str(candidate.get("bundle_id") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", bundle_id):
        raise ValueError("candidate record has no valid bundle_id")

    contract = _feature_engineering_contract()
    artifacts = [
        _inspect_training(horizon, path, metadata)
        for horizon, path, metadata in _training_artifacts(training_dir)
    ]
    generated_at = datetime.now(timezone.utc).isoformat()
    temporal = _with_receipt_id(
        {
            "schema": "quantiv.temporal-integrity.v1",
            "status": "passed",
            "generated_at": generated_at,
            "source_revision": source_revision,
            "candidate_bundle_id": bundle_id,
            "candidate_record": {
                "path": _repo_relative(candidate_record_path),
                "sha256": _sha256(candidate_record_path),
            },
            "feature_contract": contract,
            "horizons": artifacts,
            "row_count": sum(int(item["rows"]) for item in artifacts),
            "violations": 0,
            "point_in_time_limitations": [
                "The retained training artifacts prove feature snapshots precede the event by the declared T-N horizon, but Quantiv does not retain complete point-in-time provider announcement/revision history for every historical earnings event. The receipt therefore does not claim that later provider revisions could never affect historical event metadata."
            ],
        }
    )

    statistical = _with_receipt_id(
        {
            "schema": "quantiv.statistical-selection.v1",
            "status": "passed",
            "generated_at": generated_at,
            "source_revision": source_revision,
            "candidate_bundle_id": bundle_id,
            "candidate_record_sha256": _sha256(candidate_record_path),
            "selection_policy": "fixed_predictive_and_control_gates_v1",
            "uses_p_values": False,
            "multiple_testing": {
                "disposition": "not_applicable",
                "reason": "Production champion/challenger selection uses predeclared predictive/control gates rather than null-hypothesis p-value selection; manufacturing p-values would be methodologically incorrect.",
                "correction_method": None,
                "alpha": None,
                "hypothesis_family": [],
            },
            "gates": [
                "validated signed candidate bundle",
                "mandatory purged walk-forward validation",
                "feature drift must not be critical",
                "common-holdout comparison must pass when a champion exists",
                "shadow scoring must pass when a champion exists",
            ],
            "research_policy": "Any separate experiment that selects from a family of p-values must preserve raw statistics and Holm/BH correction evidence; that is not the production champion-selection rule.",
        }
    )
    _atomic_json(temporal_path, temporal)
    _atomic_json(statistical_path, statistical)
    return temporal, statistical


def verify_promotion_receipts(
    candidate_bundle_id: str,
    *,
    training_dir: Path = DEFAULT_TRAINING_DIR,
    temporal_path: Path = DEFAULT_TEMPORAL_RECEIPT,
    statistical_path: Path = DEFAULT_STATISTICAL_RECEIPT,
) -> dict[str, Any]:
    temporal = _read_object(temporal_path)
    statistical = _read_object(statistical_path)
    _verify_receipt_identity(temporal, label="temporal integrity")
    _verify_receipt_identity(statistical, label="statistical selection")

    for label, payload in (("temporal", temporal), ("statistical", statistical)):
        if payload.get("status") != "passed":
            raise ValueError(f"{label} promotion receipt did not pass")
        if payload.get("candidate_bundle_id") != candidate_bundle_id:
            raise ValueError(f"{label} promotion receipt is bound to another candidate")

    contract = _feature_engineering_contract()
    if (temporal.get("feature_contract") or {}).get("sha256") != contract["sha256"]:
        raise ValueError("temporal receipt feature-engineering source digest is stale")

    current = {
        horizon: (path, metadata)
        for horizon, path, metadata in _training_artifacts(training_dir)
    }
    receipt_rows = temporal.get("horizons")
    if not isinstance(receipt_rows, list) or not receipt_rows:
        raise ValueError("temporal receipt has no horizon evidence")
    seen: set[int] = set()
    for row in receipt_rows:
        if not isinstance(row, dict):
            raise ValueError("temporal receipt horizon evidence is malformed")
        horizon = int(row.get("horizon"))
        if horizon in seen or horizon not in current:
            raise ValueError("temporal receipt horizon set does not match training artifacts")
        seen.add(horizon)
        path, metadata = current[horizon]
        if (row.get("training") or {}).get("sha256") != _sha256(path):
            raise ValueError(f"T-{horizon} training bytes changed after temporal receipt")
        if (row.get("metadata") or {}).get("sha256") != _sha256(metadata):
            raise ValueError(f"T-{horizon} metadata changed after temporal receipt")
        if int(row.get("violations", -1)) != 0:
            raise ValueError(f"T-{horizon} temporal receipt records violations")
    if seen != set(current):
        raise ValueError("temporal receipt does not cover every training horizon")

    if statistical.get("uses_p_values") is not False:
        raise ValueError("production statistical-selection receipt must declare uses_p_values=false")
    multiple = statistical.get("multiple_testing") or {}
    if multiple.get("disposition") != "not_applicable":
        raise ValueError("metric-based production selection must record not_applicable correction")

    return {
        "temporal_integrity": {
            "receipt_id": temporal["receipt_id"],
            "sha256": _sha256(temporal_path),
            "path": temporal_path.relative_to(REPO_ROOT).as_posix(),
        },
        "statistical_selection": {
            "receipt_id": statistical["receipt_id"],
            "sha256": _sha256(statistical_path),
            "path": statistical_path.relative_to(REPO_ROOT).as_posix(),
        },
    }


def attach_decision_evidence(report_path: Path, evidence: dict[str, Any]) -> None:
    report = _read_object(report_path)
    report["promotion_evidence"] = evidence
    _atomic_json(report_path, report)


__all__ = [
    "DEFAULT_STATISTICAL_RECEIPT",
    "DEFAULT_TEMPORAL_RECEIPT",
    "attach_decision_evidence",
    "build_promotion_receipts",
    "verify_promotion_receipts",
]
