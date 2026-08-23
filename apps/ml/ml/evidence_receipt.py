"""Deterministic, run-level provenance receipts for validated ML artifacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Sequence

from ml.model_artifact import point_model_name, quantile_model_name


RECEIPT_SCHEMA = "quantiv.evidence-receipt.v1"


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _display_path(path: Path, *, repo_root: Path, data_dir: Path) -> str:
    resolved = path.resolve()
    for root, prefix in ((repo_root.resolve(), ""), (data_dir.resolve(), "DATA_DIR")):
        try:
            relative = resolved.relative_to(root)
        except ValueError:
            continue
        return str(Path(prefix) / relative) if prefix else relative.as_posix()
    return f"external/{path.name}"


def _artifact_bundle(
    name: str,
    producer: str,
    paths: Iterable[Path],
    *,
    repo_root: Path,
    data_dir: Path,
) -> dict[str, Any] | None:
    members = []
    for path in sorted({candidate.resolve() for candidate in paths}):
        if not path.is_file():
            continue
        members.append(
            {
                "path": _display_path(path, repo_root=repo_root, data_dir=data_dir),
                "bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    if not members:
        return None

    bundle_payload = json.dumps(
        members,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return {
        "name": name,
        "producer": producer,
        "member_count": len(members),
        "bytes": sum(member["bytes"] for member in members),
        "sha256": _sha256_bytes(bundle_payload),
        "members": members,
    }


def _model_paths(models_dir: Path, horizons: Sequence[int]) -> list[Path]:
    paths: list[Path] = []
    for horizon in sorted(set(horizons)):
        paths.append(models_dir / f"metadata_T{horizon}.json")
        paths.append(models_dir / point_model_name(horizon))
        paths.extend(
            models_dir / quantile_model_name(horizon, quantile)
            for quantile in (10, 25, 50, 75, 90)
        )
    return paths


def _training_paths(training_dir: Path, horizons: Sequence[int]) -> list[Path]:
    return [
        path
        for horizon in sorted(set(horizons))
        for path in (
            training_dir / f"training_T{horizon}.parquet",
            training_dir / f"metadata_T{horizon}.json",
        )
    ]


def _receipt_horizons(report: dict[str, Any], fallback: Sequence[int]) -> list[int]:
    values: set[int] = set()
    for result in (report.get("stages") or {}).values():
        horizons = result.get("horizons") if isinstance(result, dict) else None
        iterable = horizons.keys() if isinstance(horizons, dict) else horizons or []
        for horizon in iterable:
            try:
                values.add(int(horizon))
            except (TypeError, ValueError):
                continue
    return sorted(values or set(fallback))


def _reconciliation_summary(report: dict[str, Any]) -> dict[str, Any]:
    summaries: dict[str, Any] = {}
    for stage, result in (report.get("stages") or {}).items():
        if not isinstance(result, dict):
            continue
        summaries[stage] = {
            key: value
            for key, value in result.items()
            if key not in {"artifact", "artifact_dir", "stage", "status"}
        }
    return summaries


def build_evidence_receipt(
    report: dict[str, Any],
    *,
    scope: str,
    repo_root: Path,
    data_dir: Path,
    training_dir: Path,
    models_dir: Path,
    forecast_path: Path | None,
    horizons: Sequence[int],
) -> dict[str, Any]:
    """Build one receipt for a validation run, not one record per metric."""
    validated_stages = set((report.get("stages") or {}).keys())
    receipt_horizons = _receipt_horizons(report, horizons)
    artifacts: list[dict[str, Any]] = []

    if "training" in validated_stages or "models" in validated_stages:
        bundle = _artifact_bundle(
            "training_bundle",
            "apps/ml/feature_engineering_v3.py",
            _training_paths(training_dir, receipt_horizons),
            repo_root=repo_root,
            data_dir=data_dir,
        )
        if bundle:
            artifacts.append(bundle)

    if "models" in validated_stages or "forecasts" in validated_stages:
        bundle = _artifact_bundle(
            "model_bundle",
            "apps/ml/model_trainer_v3.py",
            _model_paths(models_dir, receipt_horizons),
            repo_root=repo_root,
            data_dir=data_dir,
        )
        if bundle:
            artifacts.append(bundle)

    if "forecasts" in validated_stages and forecast_path is not None:
        bundle = _artifact_bundle(
            "forecast_snapshot",
            "scripts/daily_score.py",
            [forecast_path],
            repo_root=repo_root,
            data_dir=data_dir,
        )
        if bundle:
            artifacts.append(bundle)

    issue_codes = sorted(
        {
            (str(issue.get("stage", "unknown")), str(issue.get("code", "unknown")))
            for issue in report.get("issues", [])
            if isinstance(issue, dict)
        }
    )
    core = {
        "schema": RECEIPT_SCHEMA,
        "scope": scope,
        "quality": {
            "status": report.get("status", "failed"),
            "issue_count": len(report.get("issues", [])),
            "issue_codes": [
                {"stage": stage, "code": code} for stage, code in issue_codes
            ],
        },
        "horizons": receipt_horizons,
        "artifacts": artifacts,
        "reconciliation": _reconciliation_summary(report),
    }
    canonical = json.dumps(
        core, sort_keys=True, separators=(",", ":"), default=str
    ).encode()
    return {
        "receipt_id": f"sha256:{_sha256_bytes(canonical)}",
        **core,
    }


def publish_evidence_receipt(
    report: dict[str, Any],
    *,
    receipt_dir: Path,
    scope: str,
    forecast_path: Path | None,
) -> tuple[Path, Path]:
    """Atomically write an immutable receipt and its tiny latest pointer."""
    receipt = report["evidence_receipt"]
    receipt_id = str(receipt["receipt_id"]).removeprefix("sha256:")
    if scope in {"forecasts", "all"} and forecast_path is not None:
        filename = f"{forecast_path.stem}.{receipt_id[:12]}.receipt.json"
    else:
        filename = f"{scope}.{receipt_id[:12]}.receipt.json"

    receipt_dir.mkdir(parents=True, exist_ok=True)
    immutable_path = receipt_dir / filename
    latest_path = receipt_dir / f"latest_{scope}.json"
    immutable_payload = (
        json.dumps(receipt, indent=2, sort_keys=True, default=str) + "\n"
    )
    if immutable_path.exists():
        if immutable_path.read_text() != immutable_payload:
            raise ValueError(
                f"immutable receipt collision for {receipt['receipt_id']}: {immutable_path}"
            )
    else:
        temporary = immutable_path.with_suffix(immutable_path.suffix + ".tmp")
        temporary.write_text(immutable_payload)
        temporary.replace(immutable_path)

    latest_receipt = {
        **receipt,
        "validated_at": report.get("validated_at"),
        "receipt_file": immutable_path.name,
    }
    latest_payload = (
        json.dumps(latest_receipt, indent=2, sort_keys=True, default=str) + "\n"
    )
    temporary = latest_path.with_suffix(latest_path.suffix + ".tmp")
    temporary.write_text(latest_payload)
    temporary.replace(latest_path)
    return immutable_path, latest_path
