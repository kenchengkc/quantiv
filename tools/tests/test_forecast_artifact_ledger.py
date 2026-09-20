from __future__ import annotations

from pathlib import Path

import pandas as pd

import frontend_data.forecast_artifacts as artifacts


def test_load_ml_forecasts_prefers_latest_eligible_ledger_row(
    tmp_path: Path,
    monkeypatch,
):
    forecast_dir = tmp_path / "forecasts"
    forecast_dir.mkdir()
    ledger_path = forecast_dir / "event_prediction_ledger.parquet"

    pd.DataFrame(
        [
            {
                "act_symbol": "M",
                "earnings_date": "2026-09-10",
                "snapshot_date": "2026-09-08",
                "feature_snapshot_at": "2026-09-08T20:00:00+00:00",
                "scored_at": "2026-09-09T15:35:05+00:00",
                "model_horizon": 2,
                "em_ml_pct": 0.061359,
                "freeze_eligible": True,
            },
            {
                "act_symbol": "M",
                "earnings_date": "2026-09-10",
                "snapshot_date": "2026-09-09",
                "feature_snapshot_at": "2026-09-09T20:00:00+00:00",
                "scored_at": "2026-09-10T15:42:00+00:00",
                "model_horizon": 1,
                "em_ml_pct": 0.055115,
                "freeze_eligible": False,
                "freeze_ineligible_reason": "prediction_created_after_deadline",
            },
        ]
    ).to_parquet(ledger_path, index=False)

    monkeypatch.setattr(artifacts, "FORECASTS_DIR", forecast_dir)
    monkeypatch.setattr(artifacts, "EVENT_PREDICTION_LEDGER_PATH", ledger_path)

    loaded = artifacts.load_ml_forecasts()

    row = loaded[("M", "2026-09-10")]
    assert row["em_ml_pct"] == 0.061359
    assert row["freeze_eligible"] is True
