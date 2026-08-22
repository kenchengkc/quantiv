from __future__ import annotations

import json
from pathlib import Path

from ml.evidence_receipt import build_evidence_receipt, publish_evidence_receipt


def _report(validated_at: str) -> dict:
    return {
        "status": "passed",
        "validated_at": validated_at,
        "issues": [],
        "stages": {
            "forecasts": {
                "status": "passed",
                "stage": "forecasts",
                "artifact": "/temporary/runner/data/forecasts/example.parquet",
                "rows": 12,
                "symbols": 4,
                "events": 4,
                "horizons": [1],
                "reconciliation": {"duplicate_serving_keys": 0},
            }
        },
    }


def _build(tmp_path: Path, report: dict) -> dict:
    data_dir = tmp_path / "data"
    models_dir = data_dir / "models"
    forecast_path = data_dir / "forecasts" / "forecasts_2026-08-22.parquet"
    models_dir.mkdir(parents=True, exist_ok=True)
    forecast_path.parent.mkdir(parents=True, exist_ok=True)
    (models_dir / "metadata_T1.json").write_text(
        json.dumps({"feature_cols": ["atm_iv"]})
    )
    (models_dir / "lgbm_T1.joblib").write_bytes(b"point-model")
    for quantile in (10, 25, 50, 75, 90):
        (models_dir / f"lgbm_T1_q{quantile:02d}.joblib").write_bytes(
            f"quantile-{quantile}".encode()
        )
    forecast_path.write_bytes(b"forecast-snapshot")

    return build_evidence_receipt(
        report,
        scope="forecasts",
        repo_root=tmp_path,
        data_dir=data_dir,
        training_dir=data_dir / "ml_training",
        models_dir=models_dir,
        forecast_path=forecast_path,
        horizons=[1],
    )


def test_receipt_fingerprints_one_artifact_bundle_per_pipeline_stage(
    tmp_path: Path,
) -> None:
    receipt = _build(tmp_path, _report("2026-08-22T12:00:00+00:00"))

    assert receipt["schema"] == "quantiv.evidence-receipt.v1"
    assert receipt["quality"] == {
        "status": "passed",
        "issue_count": 0,
        "issue_codes": [],
    }
    assert [artifact["name"] for artifact in receipt["artifacts"]] == [
        "model_bundle",
        "forecast_snapshot",
    ]
    assert receipt["artifacts"][0]["member_count"] == 7
    assert all(
        not member["path"].startswith("/")
        for artifact in receipt["artifacts"]
        for member in artifact["members"]
    )
    assert receipt["reconciliation"]["forecasts"]["rows"] == 12


def test_receipt_id_is_reproducible_and_changes_with_artifact_content(
    tmp_path: Path,
) -> None:
    first = _build(tmp_path, _report("2026-08-22T12:00:00+00:00"))
    second = _build(tmp_path, _report("2026-08-22T13:00:00+00:00"))
    assert first["receipt_id"] == second["receipt_id"]

    forecast_path = tmp_path / "data" / "forecasts" / "forecasts_2026-08-22.parquet"
    forecast_path.write_bytes(b"changed-forecast-snapshot")
    changed = build_evidence_receipt(
        _report("2026-08-22T13:00:00+00:00"),
        scope="forecasts",
        repo_root=tmp_path,
        data_dir=tmp_path / "data",
        training_dir=tmp_path / "data" / "ml_training",
        models_dir=tmp_path / "data" / "models",
        forecast_path=forecast_path,
        horizons=[1],
    )
    assert changed["receipt_id"] != first["receipt_id"]


def test_publish_writes_immutable_receipt_and_latest_pointer(tmp_path: Path) -> None:
    report = _report("2026-08-22T12:00:00+00:00")
    report["evidence_receipt"] = _build(tmp_path, report)
    forecast_path = tmp_path / "data" / "forecasts" / "forecasts_2026-08-22.parquet"

    immutable, latest = publish_evidence_receipt(
        report,
        receipt_dir=tmp_path / "published",
        scope="forecasts",
        forecast_path=forecast_path,
    )

    assert immutable.name.startswith("forecasts_2026-08-22.")
    assert immutable.name.endswith(".receipt.json")
    assert latest.name == "latest_forecasts.json"
    immutable_receipt = json.loads(immutable.read_text())
    latest_receipt = json.loads(latest.read_text())
    assert latest_receipt.pop("validated_at") == "2026-08-22T12:00:00+00:00"
    assert latest_receipt.pop("receipt_file") == immutable.name
    assert immutable_receipt == latest_receipt

    rerun = _report("2026-08-22T13:00:00+00:00")
    rerun["evidence_receipt"] = _build(tmp_path, rerun)
    rerun_immutable, rerun_latest = publish_evidence_receipt(
        rerun,
        receipt_dir=tmp_path / "published",
        scope="forecasts",
        forecast_path=forecast_path,
    )

    assert rerun_immutable == immutable
    assert json.loads(rerun_immutable.read_text()) == immutable_receipt
    assert json.loads(rerun_latest.read_text())["validated_at"] == (
        "2026-08-22T13:00:00+00:00"
    )
