from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ml.live_forecast_validation import validate_live_forecast_artifact
from ml.pipeline_validation import FORECAST_REQUIRED_COLUMNS


def test_forecast_gate_accepts_ml_inference_without_strict_options(tmp_path: Path) -> None:
    now = datetime(2026, 9, 17, 12, tzinfo=timezone.utc)
    forecast_path = tmp_path / "forecasts" / "forecasts_2026-09-17.parquet"
    models_dir = tmp_path / "models"
    models_dir.mkdir(parents=True)
    feature_cols = [
        "atm_iv",
        "dte",
        "em_iv_pct",
        "straddle_pct",
        "hist_move_avg_4q",
    ]
    (models_dir / "metadata_T7.json").write_text(
        json.dumps({"feature_cols": feature_cols})
    )

    row = {column: None for column in FORECAST_REQUIRED_COLUMNS}
    row.update(
        {
            "act_symbol": "PAYX",
            "earnings_date": "2026-09-24",
            "snapshot_date": "2026-09-17",
            "model_horizon": 7,
            "model_bundle_id": "test-bundle",
            "spot_price": 122.0,
            "em_ml_pct": 0.07,
            "em_ml_abs": 8.54,
            "p10": 0.04,
            "p25": 0.055,
            "p50": 0.07,
            "p75": 0.09,
            "p90": 0.12,
            "scored_at": now.isoformat(),
            "feature_vector": json.dumps(
                {
                    "atm_iv": None,
                    "dte": None,
                    "em_iv_pct": None,
                    "straddle_pct": None,
                    "hist_move_avg_4q": 0.06,
                },
                allow_nan=False,
            ),
        }
    )
    frame = pd.DataFrame([row])
    forecast_path.parent.mkdir(parents=True)
    frame.to_parquet(forecast_path, index=False)

    report = validate_live_forecast_artifact(
        forecast_path,
        models_dir=models_dir,
        now=now,
    )

    assert report["status"] == "passed"
    assert report["rows"] == 1
    assert report["reconciliation"]["optionless_ml_rows"] == 1
