#!/usr/bin/env python3
"""Build the public institutional-research validation artifact.

The public page should never depend on hand-entered performance numbers. This
projection verifies the active signed model control pointer and immutable bundle,
then verifies the immutable content-addressed model-validation receipt authenticated
by the bundle manifest. Local/preview environments with no champion pointer may
still fall back to the checked-in model metadata under ``apps/ml/models``.

Only compact due-diligence fields are published. Absolute filesystem paths,
model hyperparameters, feature vectors, and operational secrets stay out of
the frontend artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parent.parent
ML_PACKAGE_ROOT = REPO_ROOT / "apps" / "ml"
if str(ML_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(ML_PACKAGE_ROOT))

from ml.evidence_receipt import verify_evidence_receipt  # noqa: E402
from ml.model_bundle import (  # noqa: E402
    ModelBundleError,
    verify_bundle_dir,
    verify_control_pointer,
)


OUTPUT_PATH = REPO_ROOT / "apps" / "frontend" / "public" / "evidence" / "model-validation.json"
HORIZONS = (1, 2, 3, 7, 14, 21)
EVALUATION_RECEIPT_SCHEMA = "quantiv.model-evaluation-receipt.v1"


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _read_required(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelBundleError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise ModelBundleError(f"{label} must be a JSON object: {path}")
    return value


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _int(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _artifact(receipt: Mapping[str, Any], name: str) -> dict[str, Any]:
    matches = [
        item
        for item in (receipt.get("artifacts") or [])
        if isinstance(item, dict) and item.get("name") == name
    ]
    if len(matches) != 1:
        raise ModelBundleError(
            f"model validation receipt must contain exactly one {name!r} artifact bundle"
        )
    return matches[0]


def _verify_receipt_model_members(
    receipt_model_bundle: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> None:
    receipt_members = receipt_model_bundle.get("members")
    manifest_artifacts = manifest.get("artifacts")
    if not isinstance(receipt_members, list) or not isinstance(manifest_artifacts, list):
        raise ModelBundleError("model validation evidence is missing artifact members")

    by_name: dict[str, Mapping[str, Any]] = {}
    for member in receipt_members:
        if not isinstance(member, Mapping):
            raise ModelBundleError("model validation receipt has an invalid model member")
        name = Path(str(member.get("path", ""))).name
        if not name or name in by_name:
            raise ModelBundleError(
                "model validation receipt has duplicate or unnamed model members"
            )
        by_name[name] = member

    signed_by_name: dict[str, Mapping[str, Any]] = {}
    for artifact in manifest_artifacts:
        if not isinstance(artifact, Mapping):
            raise ModelBundleError("signed model manifest has an invalid artifact member")
        name = str(artifact.get("name", ""))
        if not name or name in signed_by_name:
            raise ModelBundleError(
                "signed model manifest has duplicate or unnamed artifacts"
            )
        signed_by_name[name] = artifact

    if set(by_name) != set(signed_by_name):
        raise ModelBundleError(
            "model validation receipt artifact set does not match the signed model bundle"
        )
    for name, signed in signed_by_name.items():
        receipt_member = by_name[name]
        if receipt_member.get("sha256") != signed.get("sha256"):
            raise ModelBundleError(
                f"model validation receipt digest does not match signed artifact: {name}"
            )
        try:
            receipt_bytes = int(receipt_member.get("bytes", -1))
            signed_bytes = int(signed.get("bytes", -2))
        except (TypeError, ValueError) as exc:
            raise ModelBundleError(f"invalid artifact size evidence for {name}") from exc
        if receipt_bytes != signed_bytes:
            raise ModelBundleError(
                f"model validation receipt size does not match signed artifact: {name}"
            )


def _forecast_stage_model_bundle_sha(manifest: Mapping[str, Any]) -> str:
    """Reproduce the forecast evidence receipt's path-sensitive model digest.

    Evidence-receipt bundle hashes intentionally include member paths. Model
    validation hashes files at ``data/models/<name>`` while forecast validation
    hashes the same authenticated bytes from
    ``data/models/bundles/<bundle_id>/<name>``. Reconstruct the latter from the
    signed manifest so a valid forecast is compared to the correct identity.
    """
    bundle_id = str(manifest.get("bundle_id") or "")
    artifacts = manifest.get("artifacts")
    if not bundle_id or not isinstance(artifacts, list):
        raise ModelBundleError("signed model manifest cannot identify forecast model bundle")

    members: list[dict[str, Any]] = []
    for artifact in sorted(
        artifacts,
        key=lambda item: str(item.get("name", "")) if isinstance(item, Mapping) else "",
    ):
        if not isinstance(artifact, Mapping):
            raise ModelBundleError("signed model manifest has an invalid artifact member")
        name = str(artifact.get("name") or "")
        sha256 = str(artifact.get("sha256") or "")
        try:
            size = int(artifact.get("bytes"))
        except (TypeError, ValueError) as exc:
            raise ModelBundleError(f"signed artifact has invalid size: {name}") from exc
        if not name or not sha256:
            raise ModelBundleError("signed model manifest has incomplete artifact evidence")
        members.append(
            {
                "path": f"data/models/bundles/{bundle_id}/{name}",
                "bytes": size,
                "sha256": sha256,
            }
        )
    canonical = json.dumps(members, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def _immutable_model_receipt_path(
    models_root: Path,
    manifest: Mapping[str, Any],
) -> Path:
    receipt_id = str(manifest.get("receipt_id", ""))
    if not receipt_id.startswith("sha256:"):
        raise ModelBundleError("signed model bundle has no content-addressed validation receipt")
    digest = receipt_id.removeprefix("sha256:")
    if len(digest) != 64:
        raise ModelBundleError("signed model bundle has an invalid validation receipt id")
    try:
        int(digest, 16)
    except ValueError as exc:
        raise ModelBundleError("signed model bundle has an invalid validation receipt id") from exc
    return models_root / "receipts" / f"models.{digest[:12]}.receipt.json"


def _model_source(
    repo_root: Path,
) -> tuple[Path, str, str | None, dict[str, Any] | None, dict[str, Any] | None]:
    """Return metadata source plus verified champion manifest/evidence when active.

    Presence of a production champion pointer changes the trust boundary: a bad
    pointer, bad signature, altered bundle, or mismatched validation receipt is
    an error. It must never be converted into a plausible-looking baked fallback.
    """

    models_root = repo_root / "data" / "models"
    pointer_path = models_root / "control" / "champion.json"
    if pointer_path.exists():
        pointer = verify_control_pointer(
            _read_required(pointer_path, "signed champion pointer")
        )
        champion_id = str(pointer["champion_bundle_id"])
        candidate = models_root / "bundles" / champion_id
        manifest = verify_bundle_dir(candidate)
        if manifest.get("bundle_id") != champion_id:
            raise ModelBundleError("champion pointer and signed bundle manifest disagree")
        try:
            manifest_horizons = sorted(int(value) for value in manifest.get("horizons") or [])
        except (TypeError, ValueError) as exc:
            raise ModelBundleError("signed champion has invalid horizon declarations") from exc
        if manifest_horizons != list(HORIZONS):
            raise ModelBundleError(
                "signed champion horizons do not match the public validation contract"
            )

        receipt_path = _immutable_model_receipt_path(models_root, manifest)
        try:
            receipt = verify_evidence_receipt(
                _read_required(receipt_path, "immutable model validation receipt"),
                expected_scope="models",
            )
        except ValueError as exc:
            raise ModelBundleError(str(exc)) from exc
        if receipt.get("quality", {}).get("status") != "passed":
            raise ModelBundleError("champion model validation receipt is not passed")
        if receipt.get("receipt_id") != manifest.get("receipt_id"):
            raise ModelBundleError(
                "champion bundle and model validation receipt identities disagree"
            )
        try:
            receipt_horizons = sorted(int(value) for value in receipt.get("horizons") or [])
        except (TypeError, ValueError) as exc:
            raise ModelBundleError("model validation receipt has invalid horizons") from exc
        if receipt_horizons != list(HORIZONS):
            raise ModelBundleError(
                "model validation receipt horizons do not match the public validation contract"
            )

        training_bundle = _artifact(receipt, "training_bundle")
        model_bundle = _artifact(receipt, "model_bundle")
        if not isinstance(training_bundle.get("sha256"), str):
            raise ModelBundleError("model validation receipt has no training bundle digest")
        if not isinstance(model_bundle.get("sha256"), str):
            raise ModelBundleError("model validation receipt has no model bundle digest")
        _verify_receipt_model_members(model_bundle, manifest)
        return candidate, "signed_champion", champion_id, dict(manifest), receipt

    baked = repo_root / "apps" / "ml" / "models"
    return baked, "baked_fallback", None, None, None


def _horizon_row(metadata: dict[str, Any], horizon: int) -> dict[str, Any]:
    actual_horizon = _int(metadata.get("horizon"))
    if actual_horizon != horizon:
        raise ValueError(f"metadata_T{horizon}.json declares horizon={actual_horizon!r}")

    model_mae = _number(metadata.get("val_mae"))
    baseline_mae = _number(metadata.get("baseline_straddle_mae"))
    n_val = _int(metadata.get("n_val"))
    if model_mae is None or baseline_mae is None or baseline_mae <= 0 or not n_val:
        raise ValueError(f"metadata_T{horizon}.json lacks required validation metrics")

    return {
        "horizon_days": horizon,
        "n_train": _int(metadata.get("n_train")),
        "n_validation": n_val,
        "model_mae": model_mae,
        "straddle_baseline_mae": baseline_mae,
        "relative_mae_improvement": 1.0 - model_mae / baseline_mae,
        "model_rmse": _number(metadata.get("val_rmse")),
        "model_r2": _number(metadata.get("val_r2")),
        "coverage": {
            "p10": _number(metadata.get("q10_coverage")),
            "p25": _number(metadata.get("q25_coverage")),
            "p50": _number(metadata.get("q50_coverage")),
            "p75": _number(metadata.get("q75_coverage")),
            "p90": _number(metadata.get("q90_coverage")),
            "interval_50": _number(metadata.get("coverage_50")),
            "interval_80": _number(metadata.get("coverage_80")),
        },
        "interval_width": {
            "interval_50_mean": _number(metadata.get("interval_width_50_mean")),
            "interval_80_mean": _number(metadata.get("interval_width_80_mean")),
        },
        "feature_count": len(metadata.get("feature_cols") or []),
        "quantiles": [
            float(value)
            for value in (metadata.get("quantiles") or [])
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        ],
        "model_version": metadata.get("version"),
        "trained_at": metadata.get("trained_at"),
    }


def _holdout_split(metadata: Mapping[str, Any], horizon: int) -> dict[str, Any]:
    split = metadata.get("validation_split")
    if not isinstance(split, Mapping):
        raise ModelBundleError(
            f"verified metadata_T{horizon}.json has no validation_split audit"
        )
    required = (
        "train_start",
        "train_end",
        "validation_start",
        "validation_end",
        "purge_days",
        "rows_total",
        "rows_train",
        "rows_purged",
        "rows_validation",
    )
    if any(split.get(key) is None for key in required):
        raise ModelBundleError(
            f"verified metadata_T{horizon}.json has an incomplete validation_split audit"
        )
    return {"horizon_days": horizon, **{key: split[key] for key in required}}


def _walk_forward(metadata: Mapping[str, Any], horizon: int) -> dict[str, Any]:
    result = metadata.get("walk_forward_validation")
    if not isinstance(result, Mapping) or result.get("status") != "passed":
        raise ModelBundleError(
            f"verified metadata_T{horizon}.json has no passing walk-forward audit"
        )
    required = (
        "method",
        "purge_days",
        "test_days",
        "requested_folds",
        "fold_count",
        "validation_rows",
        "model_mae",
        "baseline_straddle_mae",
        "improvement_vs_straddle",
        "folds_beating_baseline",
        "worst_fold_ratio",
    )
    if any(result.get(key) is None for key in required):
        raise ModelBundleError(
            f"verified metadata_T{horizon}.json has an incomplete walk-forward audit"
        )
    return {
        "horizon_days": horizon,
        "status": "passed",
        **{key: result[key] for key in required},
    }


def _verified_protocol(
    metadata_by_horizon: Mapping[int, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    holdouts = [
        _holdout_split(metadata_by_horizon[horizon], horizon) for horizon in HORIZONS
    ]
    walk_forwards = [
        _walk_forward(metadata_by_horizon[horizon], horizon) for horizon in HORIZONS
    ]
    protocol_keys = ("method", "purge_days", "test_days", "requested_folds")
    first = walk_forwards[0]
    for row in walk_forwards[1:]:
        if any(row[key] != first[key] for key in protocol_keys):
            raise ModelBundleError(
                "walk-forward protocol differs across horizons; refusing one public protocol claim"
            )
    summary = {
        "expanding_windows": int(first["requested_folds"]),
        "validation_window_days": int(first["test_days"]),
        "purge_days": int(first["purge_days"]),
        "method": first["method"],
        "source": "verified_model_metadata",
        "horizons": walk_forwards,
    }
    return holdouts, walk_forwards, summary


def _weighted(rows: list[dict[str, Any]], field: str) -> float | None:
    pairs = [
        (row.get(field), row.get("n_validation"))
        for row in rows
        if _number(row.get(field)) is not None and _int(row.get("n_validation"))
    ]
    if not pairs:
        return None
    numerator = sum(float(value) * int(weight) for value, weight in pairs)
    denominator = sum(int(weight) for _, weight in pairs)
    return numerator / denominator if denominator else None


def _weighted_coverage(rows: list[dict[str, Any]], field: str) -> float | None:
    pairs: list[tuple[float, int]] = []
    for row in rows:
        coverage = row.get("coverage") or {}
        value = _number(coverage.get(field)) if isinstance(coverage, dict) else None
        weight = _int(row.get("n_validation"))
        if value is not None and weight:
            pairs.append((value, weight))
    if not pairs:
        return None
    denominator = sum(weight for _, weight in pairs)
    return sum(value * weight for value, weight in pairs) / denominator


def _evaluation_receipt(
    *,
    manifest: Mapping[str, Any],
    model_receipt: Mapping[str, Any],
    rows: list[dict[str, Any]],
    holdout_splits: list[dict[str, Any]],
    walk_forwards: list[dict[str, Any]],
) -> dict[str, Any]:
    training_bundle = _artifact(model_receipt, "training_bundle")
    model_bundle = _artifact(model_receipt, "model_bundle")
    metadata_sha256 = {
        str(horizon): next(
            str(item["sha256"])
            for item in manifest.get("artifacts") or []
            if isinstance(item, Mapping)
            and item.get("name") == f"metadata_T{horizon}.json"
        )
        for horizon in HORIZONS
    }
    core = {
        "schema": EVALUATION_RECEIPT_SCHEMA,
        "bundle_id": manifest.get("bundle_id"),
        "source_revision": manifest.get("source_revision"),
        "model_validation_receipt_id": model_receipt.get("receipt_id"),
        "training_bundle_sha256": training_bundle.get("sha256"),
        "model_bundle_sha256": model_bundle.get("sha256"),
        "metadata_sha256": metadata_sha256,
        "holdout_splits": holdout_splits,
        "walk_forward": walk_forwards,
        "metrics": rows,
    }
    canonical = json.dumps(
        core, sort_keys=True, separators=(",", ":"), default=str
    ).encode()
    return {
        "receipt_id": f"sha256:{hashlib.sha256(canonical).hexdigest()}",
        **core,
    }


def build_validation(repo_root: Path, *, generated_at: str | None = None) -> dict[str, Any]:
    metadata_dir, source_kind, bundle_id, manifest, model_receipt = _model_source(repo_root)
    rows: list[dict[str, Any]] = []
    metadata_by_horizon: dict[int, dict[str, Any]] = {}
    for horizon in HORIZONS:
        path = metadata_dir / f"metadata_T{horizon}.json"
        metadata = _read(path)
        if not metadata:
            raise FileNotFoundError(f"missing model validation metadata: {path}")
        metadata_by_horizon[horizon] = metadata
        rows.append(_horizon_row(metadata, horizon))

    weighted_model = _weighted(rows, "model_mae")
    weighted_baseline = _weighted(rows, "straddle_baseline_mae")
    improvements = [float(row["relative_mae_improvement"]) for row in rows]

    forecast = _read(
        repo_root / "apps" / "frontend" / "public" / "evidence" / "forecast.json"
    )
    control = _read(
        repo_root / "apps" / "frontend" / "public" / "control-plane.json"
    )
    forecast_model_bundle = next(
        (
            item
            for item in (forecast.get("artifact_bundles") or [])
            if isinstance(item, dict) and item.get("name") == "model_bundle"
        ),
        {},
    )

    evaluation_status = "preview_unverified"
    evaluation_receipt: dict[str, Any] | None = None
    validation_receipt_id: str | None = None
    source_revision: str | None = None
    public_model_artifact_sha: str | None = None
    forecast_model_matches_evaluation: bool | None = None

    if source_kind == "signed_champion":
        if manifest is None or model_receipt is None:
            raise ModelBundleError("signed champion source is missing verified provenance")
        holdout_splits, walk_forwards, walk_forward_summary = _verified_protocol(
            metadata_by_horizon
        )
        evaluation_receipt = _evaluation_receipt(
            manifest=manifest,
            model_receipt=model_receipt,
            rows=rows,
            holdout_splits=holdout_splits,
            walk_forwards=walk_forwards,
        )
        evaluation_status = "verified"
        validation_receipt_id = str(model_receipt["receipt_id"])
        source_revision = str(manifest.get("source_revision") or "") or None
        expected_forecast_model_sha = _forecast_stage_model_bundle_sha(manifest)
        public_model_artifact_sha = expected_forecast_model_sha

        forecast_sha = forecast_model_bundle.get("sha256")
        if isinstance(forecast_sha, str) and forecast_sha:
            forecast_model_matches_evaluation = (
                forecast_sha == expected_forecast_model_sha
            )
            if (
                forecast.get("quality", {}).get("status") == "passed"
                and not forecast_model_matches_evaluation
            ):
                raise ModelBundleError(
                    "passed forecast evidence does not match the verified champion model bundle"
                )
    else:
        holdout_splits = []
        walk_forward_summary = {
            "expanding_windows": 4,
            "validation_window_days": 60,
            "purge_days": 5,
            "method": "preview_default",
            "source": "preview_unverified",
            "horizons": [],
        }

    return {
        "schema": "quantiv.public-model-validation.v1",
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "model_source": {
            "kind": source_kind,
            "bundle_id": bundle_id,
            "artifact_sha256": public_model_artifact_sha,
            "verification_status": evaluation_status,
            "source_revision": source_revision,
            "model_validation_receipt_id": validation_receipt_id,
        },
        "evaluation_evidence": {
            "status": evaluation_status,
            "receipt": evaluation_receipt,
        },
        "summary": {
            "supported_horizons": list(HORIZONS),
            "validation_row_observations": sum(
                int(row["n_validation"]) for row in rows
            ),
            "weighted_model_mae": weighted_model,
            "weighted_straddle_mae": weighted_baseline,
            "weighted_relative_mae_improvement": (
                1.0 - weighted_model / weighted_baseline
                if weighted_model is not None and weighted_baseline
                else None
            ),
            "min_relative_mae_improvement": min(improvements),
            "max_relative_mae_improvement": max(improvements),
            "weighted_coverage": {
                name: _weighted_coverage(rows, name)
                for name in (
                    "p10",
                    "p25",
                    "p50",
                    "p75",
                    "p90",
                    "interval_50",
                    "interval_80",
                )
            },
        },
        "horizons": rows,
        "validation_protocol": {
            "target": "absolute earnings move magnitude",
            "baseline": "market straddle expected move",
            "chronological_holdout": (
                bool(holdout_splits) if source_kind == "signed_champion" else True
            ),
            "holdout_splits": holdout_splits,
            "walk_forward": walk_forward_summary,
            "promotion_controls": [
                "point and interval validation",
                "quantile calibration",
                "straddle-baseline comparison",
                "common-holdout champion comparison",
                "upcoming-event shadow scoring",
                "feature-drift checks",
            ],
            "decision_scope": "end_of_day_research",
            "live_trading_eligible": False,
        },
        "current_evidence": {
            "forecast_receipt_id": forecast.get("receipt_id"),
            "forecast_validated_at": forecast.get("validated_at"),
            "forecast_quality": (forecast.get("quality") or {}).get("status"),
            "forecast_control_exceptions": (forecast.get("controls") or {}).get(
                "exceptions"
            ),
            "forecast_rows": (forecast.get("coverage") or {}).get("rows"),
            "forecast_events": (forecast.get("coverage") or {}).get("events"),
            "forecast_model_matches_evaluation": forecast_model_matches_evaluation,
            "control_plane_status": control.get("status"),
            "publication_eligible": control.get("publication_eligible"),
            "data_status": (control.get("data") or {}).get("status"),
            "model_status": (control.get("model") or {}).get("status"),
            "drift_status": (control.get("model") or {}).get("drift_status"),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    output = (
        args.out
        or repo_root
        / "apps"
        / "frontend"
        / "public"
        / "evidence"
        / "model-validation.json"
    )
    payload = build_validation(repo_root)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
