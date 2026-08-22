import numpy as np
import pandas as pd

from scripts.daily_score import score


class _ConstantModel:
    def __init__(self, value: float):
        self.value = value

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        return np.full(len(frame), self.value)


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
