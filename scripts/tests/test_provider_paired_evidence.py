from __future__ import annotations

import pandas as pd
import pytest

from research.build_provider_paired_evidence import build_paired_report


def _paired_rows() -> pd.DataFrame:
    rows = []
    for index in range(300):
        fold = index // 100
        test_start = pd.Timestamp("2025-01-01") + pd.Timedelta(days=fold * 120)
        earnings_date = test_start + pd.Timedelta(days=index % 100)
        actual = 0.04 + (index % 20) * 0.001
        control_error = 0.008 + (index % 7) * 0.0007
        candidate_error = 0.004 + (index % 5) * 0.00035
        sign = -1 if index % 2 else 1
        rows.append(
            {
                "act_symbol": f"S{index:03d}",
                "earnings_date": earnings_date,
                "model_horizon": 7,
                "fold": fold,
                "train_end": test_start - pd.Timedelta(days=5),
                "test_start": test_start,
                "test_end": test_start + pd.Timedelta(days=99),
                "actual": actual,
                "straddle": actual + sign * 0.018,
                "control_prediction": actual + sign * control_error,
                "candidate_prediction": actual + sign * candidate_error,
                "sector": ("Technology", "Financials")[index % 2],
                "volatility_regime": ("low", "high")[index % 2],
                "liquidity": ("standard", "tight")[index % 2],
                "dte_bucket": ("4-7", "8-14")[index % 2],
            }
        )
    return pd.DataFrame(rows)


def test_paired_report_passes_only_with_reproducible_incremental_lift() -> None:
    report = build_paired_report(
        _paired_rows(),
        signal="options_flow",
        incremental_monthly_cost_usd=0.0,
        generated_at="2026-08-29T00:00:00+00:00",
        source_sha256="sha256:" + "a" * 64,
    )

    assert report["status"] == "passed"
    assert report["sample"] == {
        "rows": 300,
        "events": 300,
        "walk_forward_folds": 3,
        "horizons": [7],
    }
    assert report["mae_improvement_pct"] > 0.5
    assert report["paired_error_delta"]["t_stat"] < -2
    assert report["candidate"]["straddle_relative_mae"] < 1
    assert report["worst_slice_mae_regression_pct"] < 5
    assert report["paired_keys_sha256"].startswith("sha256:")
    assert report["split_audit_sha256"].startswith("sha256:")


def test_paired_report_rejects_leakage_and_cost() -> None:
    frame = _paired_rows()
    frame.loc[frame["fold"] == 0, "train_end"] = frame.loc[
        frame["fold"] == 0, "test_start"
    ]
    with pytest.raises(ValueError, match="purge"):
        build_paired_report(
            frame,
            signal="options_flow",
            incremental_monthly_cost_usd=0.0,
        )

    report = build_paired_report(
        _paired_rows(),
        signal="options_flow",
        incremental_monthly_cost_usd=1.0,
    )
    assert report["status"] == "failed"
    assert "candidate exceeds the allowed incremental monthly cost" in report["gate_failures"]
