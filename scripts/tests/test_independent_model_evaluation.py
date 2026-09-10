from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from research.independent_model_evaluation import prepare_independent_test
from research.research_manifest import sha256_file


def _write_training(root: Path, *, horizon: int = 1, days: int = 220) -> pd.DataFrame:
    training_dir = root / "data/ml_training"
    training_dir.mkdir(parents=True, exist_ok=True)
    dates = pd.date_range("2025-01-01", periods=days, freq="D")
    rows = []
    for index, date in enumerate(dates):
        for suffix in ("A", "B"):
            rows.append(
                {
                    "feature": float(index),
                    "straddle_pct": 0.05 + (index % 7) * 0.001,
                    "target": 0.04 + (index % 5) * 0.002,
                    "__earnings_date": date,
                    "__symbol": f"{suffix}{index:03d}",
                }
            )
    frame = pd.DataFrame(rows)
    frame.to_parquet(training_dir / f"training_T{horizon}.parquet", index=False)
    (training_dir / f"metadata_T{horizon}.json").write_text(
        json.dumps(
            {
                "horizon": horizon,
                "n_samples": len(frame),
                "feature_cols": ["feature", "straddle_pct"],
            }
        )
    )
    return frame


def test_prepare_physically_removes_final_test_from_development_training(
    tmp_path: Path,
) -> None:
    original = _write_training(tmp_path)
    training_path = tmp_path / "data/ml_training/training_T1.parquet"
    source_sha = sha256_file(training_path)

    reservation = prepare_independent_test(
        repo_root=tmp_path,
        training_dir=tmp_path / "data/ml_training",
        staging_dir=tmp_path / "data/independent_evaluation/pending",
        horizons=[1],
        test_days=30,
        purge_days=2,
        label_availability_days=5,
        min_development_rows=100,
        min_test_rows=20,
        source_revision="test-revision",
    )

    row = reservation["horizons"][0]
    final_test = pd.read_parquet(tmp_path / row["final_test_path"])
    development = pd.read_parquet(training_path)
    final_start = pd.Timestamp(row["reservation_window_start"])
    development_end = pd.Timestamp(row["development_end"])

    assert reservation["protocol"]["effective_embargo_days"] == 5
    assert reservation["protocol"]["operational_outcome_exclusion"] == {
        "method": "exclude_prediction_ledger_symbol_date_horizon_keys",
        "prediction_ledger_present": False,
        "prediction_ledger_path": "data/models/monitoring/prediction_ledger.parquet",
        "prediction_ledger_sha256": None,
        "prediction_ledger_rows": 0,
        "prediction_ledger_event_keys": 0,
    }
    assert row["source_sha256"] == source_sha
    assert row["source_rows"] == len(original)
    assert len(final_test) == row["final_test_rows"]
    assert len(development) == row["development_rows"]
    assert row["purged_rows"] > 0
    assert row["operational_excluded_rows"] == 0
    assert development_end < final_start - pd.Timedelta(days=5)
    assert pd.to_datetime(development["__earnings_date"]).max() == development_end
    assert pd.to_datetime(final_test["__earnings_date"]).min() == pd.Timestamp(
        row["final_test_start"]
    )
    assert set(
        zip(
            development["__symbol"].astype(str),
            pd.to_datetime(development["__earnings_date"]).dt.date.astype(str),
        )
    ).isdisjoint(
        set(
            zip(
                final_test["__symbol"].astype(str),
                pd.to_datetime(final_test["__earnings_date"]).dt.date.astype(str),
            )
        )
    )

    metadata = json.loads(
        (tmp_path / "data/ml_training/metadata_T1.json").read_text()
    )
    assert metadata["source_n_samples_before_independent_test"] == len(original)
    assert metadata["n_samples"] == len(development)
    assert metadata["independent_test_reservation"]["reservation_id"] == (
        reservation["reservation_id"]
    )


def test_prepare_excludes_every_event_seen_by_operational_outcome_ledger(
    tmp_path: Path,
) -> None:
    original = _write_training(tmp_path, days=100)
    observed = original.iloc[-3]
    ledger_path = tmp_path / "data/models/monitoring/prediction_ledger.parquet"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "act_symbol": [observed["__symbol"]],
            "earnings_date": [observed["__earnings_date"]],
            "model_horizon": [1],
            "irrelevant": ["retained"],
        }
    ).to_parquet(ledger_path, index=False)

    reservation = prepare_independent_test(
        repo_root=tmp_path,
        training_dir=tmp_path / "data/ml_training",
        staging_dir=tmp_path / "data/independent_evaluation/pending",
        horizons=[1],
        test_days=20,
        min_development_rows=50,
        min_test_rows=20,
        source_revision="test-revision",
    )

    row = reservation["horizons"][0]
    final_test = pd.read_parquet(tmp_path / row["final_test_path"])
    final_keys = set(
        zip(
            final_test["__symbol"].astype(str),
            pd.to_datetime(final_test["__earnings_date"]).dt.date.astype(str),
        )
    )
    observed_key = (
        str(observed["__symbol"]),
        pd.Timestamp(observed["__earnings_date"]).date().isoformat(),
    )
    evidence = reservation["protocol"]["operational_outcome_exclusion"]

    assert observed_key not in final_keys
    assert row["operational_excluded_rows"] == 1
    assert evidence["prediction_ledger_present"] is True
    assert evidence["prediction_ledger_rows"] == 1
    assert evidence["prediction_ledger_event_keys"] == 1
    assert evidence["prediction_ledger_sha256"] == sha256_file(ledger_path)


def test_prepare_keeps_all_symbols_on_final_test_boundary_together(
    tmp_path: Path,
) -> None:
    _write_training(tmp_path, days=80)

    reservation = prepare_independent_test(
        repo_root=tmp_path,
        training_dir=tmp_path / "data/ml_training",
        staging_dir=tmp_path / "data/independent_evaluation/pending",
        horizons=[1],
        test_days=10,
        purge_days=0,
        label_availability_days=5,
        min_development_rows=20,
        min_test_rows=10,
        source_revision="test-revision",
    )

    row = reservation["horizons"][0]
    final_test = pd.read_parquet(tmp_path / row["final_test_path"])
    final_dates = pd.to_datetime(final_test["__earnings_date"])
    boundary = pd.Timestamp(row["reservation_window_start"])
    boundary_rows = final_test.loc[final_dates == boundary]
    assert len(boundary_rows) == 2


def test_prepare_fails_when_test_period_is_too_small(tmp_path: Path) -> None:
    _write_training(tmp_path, days=20)

    with pytest.raises(ValueError, match="final-test rows"):
        prepare_independent_test(
            repo_root=tmp_path,
            training_dir=tmp_path / "data/ml_training",
            staging_dir=tmp_path / "data/independent_evaluation/pending",
            horizons=[1],
            test_days=2,
            min_development_rows=10,
            min_test_rows=10,
            source_revision="test-revision",
        )
