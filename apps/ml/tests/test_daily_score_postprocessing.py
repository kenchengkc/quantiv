import numpy as np
import pandas as pd
import pytest

from ml.pipeline_validation import FORECAST_REQUIRED_COLUMNS
from scripts.daily_score import get_upcoming_features, save_forecasts, score


class _ConstantModel:
    def __init__(self, value: float):
        self.value = value

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        return np.full(len(frame), self.value)


class _CapturingConnection:
    def __init__(self):
        self.sql = ""

    def execute(self, sql: str):
        self.sql = sql
        return self

    def fetchall(self):
        return []

    def fetchdf(self) -> pd.DataFrame:
        return pd.DataFrame()


def test_batch_score_clips_point_and_rearranges_crossed_quantiles():
    frame = pd.DataFrame(
        {
            "act_symbol": ["TEST"],
            "earnings_date": ["2026-09-01"],
            "lead_days": [7],
            "spot_price": [100.0],
            "timing": ["amc"],
            "straddle_pct": [0.06],
            "event_move_implied": [0.05],
        }
    )
    models = {
        7: {
            "feature_cols": ["straddle_pct"],
            "model": _ConstantModel(-0.02),
            "residual_std": 0.03,
            "quantile_models": {
                0.10: _ConstantModel(0.05),
                0.25: _ConstantModel(-0.01),
                0.50: _ConstantModel(0.03),
                0.75: _ConstantModel(0.09),
                0.90: _ConstantModel(0.07),
            },
        }
    }

    result = score(frame, models).iloc[0]

    assert result["em_ml_pct"] == 0.0
    assert result["em_ml_abs"] == 0.0
    assert result[["p10", "p25", "p50", "p75", "p90"]].tolist() == [
        0.0,
        0.03,
        0.05,
        0.07,
        0.09,
    ]


def test_upcoming_features_carries_raw_straddle_mid_into_forecasts():
    connection = _CapturingConnection()

    get_upcoming_features(connection, 21)

    assert "sf.straddle_mid," in connection.sql


def test_upcoming_features_matches_training_only_feature_families():
    connection = _CapturingConnection()

    get_upcoming_features(connection, 21)

    for expression in (
        "ts.hist_move_p75_8q",
        "ts.hist_move_p90_8q",
        "ts.hist_move_max_12q",
        "ts.hist_move_kurt_8q",
        "ts.hist_move_last",
        "mc.vix_current",
        "mc.vix_change_30d",
        "mc.vix_pct_252d",
        "mc.spy_drift_60d",
        "mc.tlt_spy_ratio_30d",
        "END AS iv_rank",
        "END AS hv_rank",
        "AS iv_mom_week",
        "AS iv_mom_month",
    ):
        assert expression in connection.sql


def test_save_forecasts_rejects_incomplete_artifact_before_writing(tmp_path):
    row = {column: 1 for column in FORECAST_REQUIRED_COLUMNS}
    row.pop("straddle_mid")

    with pytest.raises(ValueError, match="straddle_mid"):
        save_forecasts(
            pd.DataFrame([row]),
            tmp_path,
            output_path=tmp_path / "candidate.parquet",
        )

    assert not (tmp_path / "candidate.parquet").exists()
