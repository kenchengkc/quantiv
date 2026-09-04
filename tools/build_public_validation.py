#!/usr/bin/env python3
"""Build the public institutional-research validation artifact.

The public page should never depend on hand-entered performance numbers. This
projection reads the model metadata that actually exists in the runner after
R2 synchronization, preferring the signed champion bundle selected by the
model control plane. Local/preview environments fall back to the baked model
metadata under ``apps/ml/models``.

Only compact due-diligence fields are published. Absolute filesystem paths,
model hyperparameters, feature vectors, and operational secrets stay out of
the frontend artifact.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = REPO_ROOT / "apps" / "frontend" / "public" / "evidence" / "model-validation.json"
HORIZONS = (1, 2, 3, 7, 14, 21)


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _int(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _model_source(repo_root: Path) -> tuple[Path, str, str | None]:
    """Return metadata directory, source kind, and active bundle ID.

    The nightly workflow pulls ``data/models`` from R2 before this script runs.
    When a champion pointer and matching immutable bundle are present, that is
    the authoritative source. Preview/local builds can still generate a useful
    artifact from the checked-in fallback models.
    """

    pointer_path = repo_root / "data" / "models" / "control" / "champion.json"
    pointer = _read(pointer_path)
    champion_id = pointer.get("champion_bundle_id")
    if isinstance(champion_id, str) and len(champion_id) == 64:
        candidate = repo_root / "data" / "models" / "bundles" / champion_id
        if candidate.is_dir() and all((candidate / f"metadata_T{h}.json").is_file() for h in HORIZONS):
            return candidate, "signed_champion", champion_id

    baked = repo_root / "apps" / "ml" / "models"
    return baked, "baked_fallback", None


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
        "quantiles": [float(value) for value in (metadata.get("quantiles") or []) if isinstance(value, (int, float))],
        "model_version": metadata.get("version"),
        "trained_at": metadata.get("trained_at"),
    }


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


def build_validation(repo_root: Path, *, generated_at: str | None = None) -> dict[str, Any]:
    metadata_dir, source_kind, bundle_id = _model_source(repo_root)
    rows: list[dict[str, Any]] = []
    for horizon in HORIZONS:
        path = metadata_dir / f"metadata_T{horizon}.json"
        metadata = _read(path)
        if not metadata:
            raise FileNotFoundError(f"missing model validation metadata: {path}")
        rows.append(_horizon_row(metadata, horizon))

    weighted_model = _weighted(rows, "model_mae")
    weighted_baseline = _weighted(rows, "straddle_baseline_mae")
    improvements = [float(row["relative_mae_improvement"]) for row in rows]

    forecast = _read(repo_root / "apps" / "frontend" / "public" / "evidence" / "forecast.json")
    control = _read(repo_root / "apps" / "frontend" / "public" / "control-plane.json")
    model_bundle = next(
        (
            item
            for item in (forecast.get("artifact_bundles") or [])
            if isinstance(item, dict) and item.get("name") == "model_bundle"
        ),
        {},
    )

    return {
        "schema": "quantiv.public-model-validation.v1",
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "model_source": {
            "kind": source_kind,
            "bundle_id": bundle_id,
            "artifact_sha256": model_bundle.get("sha256"),
        },
        "summary": {
            "supported_horizons": list(HORIZONS),
            "validation_row_observations": sum(int(row["n_validation"]) for row in rows),
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
                for name in ("p10", "p25", "p50", "p75", "p90", "interval_50", "interval_80")
            },
        },
        "horizons": rows,
        "validation_protocol": {
            "target": "absolute earnings move magnitude",
            "baseline": "market straddle expected move",
            "chronological_holdout": True,
            "walk_forward": {
                "expanding_windows": 4,
                "validation_window_days": 60,
                "purge_days": 5,
            },
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
            "forecast_control_exceptions": (forecast.get("controls") or {}).get("exceptions"),
            "forecast_rows": (forecast.get("coverage") or {}).get("rows"),
            "forecast_events": (forecast.get("coverage") or {}).get("events"),
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
    output = args.out or repo_root / "apps" / "frontend" / "public" / "evidence" / "model-validation.json"
    payload = build_validation(repo_root)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
