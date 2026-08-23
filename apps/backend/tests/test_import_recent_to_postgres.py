"""Unit tests for forecast import bookkeeping."""

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from scripts import import_recent_to_postgres as importer  # noqa: E402


def _forecast_row(**overrides):
    row = {col: None for col in importer.COLUMNS}
    row.update(
        {
            "act_symbol": "CRM",
            "earnings_date": date(2026, 5, 27),
            "timing": "amc",
            "snapshot_date": date(2026, 5, 20),
            "model_horizon": 7,
            "model_bundle_id": "bundle-2026-05-24",
            "spot_price": 180.0,
            "em_ml_pct": 0.07,
            "em_ml_abs": 12.6,
            "scored_at": datetime(2026, 5, 24, tzinfo=timezone.utc),
            "feature_vector": json.dumps({"log_spot": 5.19}),
        }
    )
    row.update(overrides)
    return row


def test_load_and_filter_records_dedupe_stats(tmp_path):
    parquet_path = tmp_path / "forecasts_2026-05-24.parquet"
    pd.DataFrame([
        _forecast_row(spot_price=180.0),
        _forecast_row(spot_price=181.0),
    ]).to_parquet(parquet_path, index=False)

    df, stats = importer.load_and_filter(parquet_path, days=7, full=True)

    assert len(df) == 1
    assert float(df.iloc[0]["spot_price"]) == 180.0
    assert stats.source_rows == 2
    assert stats.selected_rows == 2
    assert stats.duplicate_rows == 2
    assert stats.duplicate_keys == 1
    assert importer._feature_vector_count(df) == 1
    assert importer._single_model_bundle_id(df) == "bundle-2026-05-24"
    assert importer._horizon_counts(df) == {"7": 1}


def test_load_and_filter_backfills_legacy_audit_columns(tmp_path):
    parquet_path = tmp_path / "forecasts_2026-05-24.parquet"
    legacy_columns = [
        col for col in importer.COLUMNS
        if col not in {"feature_vector", "model_bundle_id"}
    ]
    pd.DataFrame([
        {col: _forecast_row()[col] for col in legacy_columns}
    ]).to_parquet(parquet_path, index=False)

    df, stats = importer.load_and_filter(parquet_path, days=7, full=True)

    assert len(df) == 1
    assert stats.source_rows == 1
    assert df.iloc[0]["feature_vector"] is None
    assert df.iloc[0]["model_bundle_id"] is None
    assert importer._feature_vector_count(df) == 0
    assert importer._single_model_bundle_id(df) is None


def test_load_and_filter_rejects_mixed_model_bundles(tmp_path):
    parquet_path = tmp_path / "forecasts_2026-05-24.parquet"
    pd.DataFrame([
        _forecast_row(model_bundle_id="bundle-a"),
        _forecast_row(model_bundle_id="bundle-b", model_horizon=14),
    ]).to_parquet(parquet_path, index=False)

    try:
        importer.load_and_filter(parquet_path, days=7, full=True)
    except ValueError as exc:
        assert "multiple model bundle IDs" in str(exc)
    else:
        raise AssertionError("mixed-model import should fail closed")


def test_import_bundle_must_match_activated_serving_bundle(tmp_path):
    parquet_path = tmp_path / "forecasts_2026-05-24.parquet"
    pd.DataFrame([_forecast_row(model_bundle_id="bundle-a")]).to_parquet(
        parquet_path, index=False
    )
    df, _stats = importer.load_and_filter(parquet_path, days=7, full=True)

    importer.verify_expected_model_bundle(df, "bundle-a")
    try:
        importer.verify_expected_model_bundle(df, "bundle-b")
    except ValueError as exc:
        assert "does not match the activated serving bundle" in str(exc)
    else:
        raise AssertionError("mismatched serving/import bundles must fail closed")
