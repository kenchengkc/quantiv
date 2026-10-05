from __future__ import annotations

import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
import pytest

import model_trainer
import ml.training_split as splits


def test_trainer_import_does_not_require_the_optional_tuning_dependency():
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c", "\n".join([
            "import sys",
            f"sys.path.insert(0, {str(Path(model_trainer.__file__).parent)!r})",
            "sys.modules['optuna'] = None",
            "import model_trainer",
            "assert callable(model_trainer.run_training)",
        ])],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def test_time_decay_uses_a_true_half_life():
    dates = pd.Series(pd.to_datetime(["2024-01-02", "2025-01-01"]))
    weights = splits.half_life_weights(dates, pd.Timestamp("2025-01-01"), 1.)
    assert weights.tolist() == pytest.approx([.5, 1.])


def test_fit_row_digest_ignores_order_but_binds_labels():
    frame = pd.DataFrame({"__symbol": ["B", "A"], "target": [.1, .2]})
    original = splits.training_row_digest(frame)
    assert splits.training_row_digest(frame.iloc[::-1]) == original
    frame.loc[0, "target"] = .3
    assert splits.training_row_digest(frame) != original


def test_fit_digest_binds_exact_floating_point_labels():
    first = pd.DataFrame({"target": [.04]})
    second = pd.DataFrame({"target": [np.nextafter(.04, .05)]})
    assert splits.training_row_digest(first) != splits.training_row_digest(second)


def test_optionless_selection_evidence_uses_available_historical_baseline():
    actual = np.linspace(.02, .12, 200)
    frame = pd.DataFrame({"straddle_pct": [np.nan] * 200, "hist_move_med_4q": [.05] * 200})
    quantiles = np.tile([.03, .045, .07, .095, .11], (200, 1))
    report = model_trainer._cohort_validation_report(frame, actual, actual, quantiles)
    assert report["optionless"]["baseline_name"] == "historical_median"
    assert report["optionless"]["baseline_rows"] == 200
    assert report["optionless"]["status"] == "passed"
    frame["hist_move_med_4q"] = np.nan
    missing = model_trainer._cohort_validation_report(frame, actual, actual, quantiles)
    assert missing["optionless"]["status"] == "withheld"
    assert missing["optionless"]["baseline_mae"] is None


@pytest.mark.parametrize("corruption,expected", [("crossing", .25), ("negative", .10)])
def test_supported_cohort_cannot_hide_raw_quantile_failures_after_rearrangement(corruption, expected):
    actual = np.linspace(.02, .12, 200)
    frame = pd.DataFrame({"straddle_pct": [np.nan] * 200, "hist_move_med_4q": [.05] * 200})
    served = np.tile([.03, .045, .07, .095, .11], (200, 1))
    raw = served.copy()
    if corruption == "crossing":
        raw[:50, [1, 2]] = raw[:50, [2, 1]]
        key = "quantile_crossing_rate_raw"
    else:
        raw[-20:, 0] = -.001
        key = "quantile_negative_rate_raw"
    report = model_trainer._cohort_validation_report(frame, actual, actual, served, raw_quantiles=raw)
    assert report["optionless"][key] == pytest.approx(expected)
    assert report["optionless"]["status"] == "withheld"
    assert any(corruption in issue for issue in report["optionless"]["issues"])


def test_cohort_raw_quantile_rates_at_existing_limits_remain_allowed():
    actual = np.linspace(.02, .12, 200)
    frame = pd.DataFrame({"straddle_pct": [np.nan] * 200, "hist_move_med_4q": [.05] * 200})
    served = np.tile([.03, .045, .07, .095, .11], (200, 1))
    raw = served.copy()
    raw[:40, [1, 2]] = raw[:40, [2, 1]]
    raw[-10:, 0] = -.001
    report = model_trainer._cohort_validation_report(frame, actual, actual, served, raw_quantiles=raw)
    assert report["optionless"]["quantile_crossing_rate_raw"] == .20
    assert report["optionless"]["quantile_negative_rate_raw"] == .05
    assert report["optionless"]["status"] == "passed"


def test_saved_heads_refit_every_mature_row_with_frozen_selected_counts(tmp_path: Path, monkeypatch):
    dates = pd.date_range("2025-01-01", periods=200)
    frame = pd.DataFrame({
        "feature": np.arange(200, dtype=float), "straddle_pct": [.05] * 200,
        "target": [.04] * 200, "__symbol": [f"SYM{i}" for i in range(200)],
        "__earnings_date": dates, "__snapshot_date": dates - pd.Timedelta(days=7),
        "__label_available_at": dates + pd.Timedelta(days=1),
    })
    frame.loc[199, "__label_available_at"] = pd.Timestamp("2026-01-02")
    training_dir = tmp_path / "ml_training"
    training_dir.mkdir()
    frame.to_parquet(training_dir / "training_T7.parquet", index=False)
    (training_dir / "metadata_T7.json").write_text(json.dumps({
        "feature_protocol": "quantiv.earnings-causal.v2",
        "target_protocol": "quantiv.session-reaction.v2",
    }))
    monkeypatch.setattr(model_trainer, "get_data_dir", lambda: tmp_path)
    monkeypatch.setattr(model_trainer, "DEFAULT_PARAMS", {
        "n_estimators": 12, "learning_rate": .03, "num_leaves": 3,
        "min_child_samples": 2, "n_jobs": 1,
    })
    result = model_trainer.run_training([7], as_of="2026-01-01")[7]
    assert result["final_fit"]["rows"] == 199
    assert result["final_fit"]["end"] == "2025-07-18"
    assert result["final_fit"]["label_available_end"] == "2025-07-19"
    assert result["selection_exposure"]["through_date"] == "2025-07-19"
    assert result["metric_scope"] == "selection_only"
    assert result["feature_reference"]["feature"]["rows"] == 199
    assert result["feature_protocol"] == "quantiv.earnings-causal.v2"
    for head in ["point", "q10", "q25", "q50", "q75", "q90"]:
        suffix = "" if head == "point" else "_" + head
        saved = lgb.Booster(model_file=str(tmp_path / "models" / f"lgbm_T7{suffix}.txt"))
        assert saved.num_trees() == result["selected_iterations"][head]
        tree = saved.dump_model()["tree_info"][0]["tree_structure"]
        assert tree.get("internal_count", tree.get("leaf_count")) == 199
