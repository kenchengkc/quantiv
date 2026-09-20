from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from event_forecast_ledger import (  # noqa: E402
    append_event_prediction_ledger,
    audit_forecast_row,
    event_cutoffs,
    latest_eligible_predictions,
)

ET = ZoneInfo("America/New_York")


def _row(**overrides):
    row = {
        "act_symbol": "M",
        "earnings_date": "2026-09-10",
        "timing": "before_market_open",
        "snapshot_date": "2026-09-09",
        "model_horizon": 1,
        "model_bundle_id": "bundle-1",
        "feature_vector": '{"x":1}',
        "scored_at": "2026-09-09T21:00:00+00:00",  # 17:00 ET
        "em_ml_pct": 0.06,
    }
    row.update(overrides)
    return row


def test_bmo_uses_previous_session_close_and_prior_evening_deadline():
    feature_cutoff, prediction_deadline = event_cutoffs(
        date(2026, 9, 8), "before_market_open"
    )
    # Monday Sep 7 is Labor Day; previous NYSE session is Fri Sep 4.
    assert feature_cutoff == datetime(2026, 9, 4, 16, 0, tzinfo=ET)
    assert prediction_deadline == datetime(2026, 9, 4, 18, 0, tzinfo=ET)


def test_amc_cutoff_is_five_minutes_before_canonical_close():
    feature_cutoff, prediction_deadline = event_cutoffs(
        date(2026, 11, 27), "after_market_close"
    )
    # Canonical early close is 13:00 ET.
    expected = datetime(2026, 11, 27, 12, 55, tzinfo=ET)
    assert feature_cutoff == expected
    assert prediction_deadline == expected


def test_morning_of_bmo_score_is_not_eligible_even_with_prior_close_features():
    audited = audit_forecast_row(
        _row(scored_at="2026-09-10T15:30:00+00:00")
    )
    assert audited["freeze_eligible"] is False
    assert audited["freeze_ineligible_reason"] == "prediction_created_after_deadline"


def test_prior_evening_bmo_score_is_eligible():
    audited = audit_forecast_row(_row())
    assert audited["freeze_eligible"] is True
    assert audited["freeze_ineligible_reason"] is None
    assert audited["forecast_id"]
    assert len(audited["feature_hash"]) == 64


def test_same_day_date_only_amc_snapshot_fails_closed():
    audited = audit_forecast_row(
        _row(
            act_symbol="ADBE",
            earnings_date="2026-09-10",
            timing="after_market_close",
            snapshot_date="2026-09-10",
            scored_at="2026-09-10T19:45:00+00:00",  # 15:45 ET
            atm_iv=0.5,
        )
    )
    assert audited["freeze_eligible"] is False
    assert audited["freeze_ineligible_reason"] == "feature_snapshot_after_event_cutoff"


def test_prior_session_amc_forecast_is_safe_until_intraday_inputs_exist():
    audited = audit_forecast_row(
        _row(
            act_symbol="ADBE",
            earnings_date="2026-09-10",
            timing="after_market_close",
            snapshot_date="2026-09-09",
            scored_at="2026-09-10T15:00:00+00:00",  # 11:00 ET
        )
    )
    assert audited["freeze_eligible"] is True


def test_ledger_is_append_only_and_materialized_archive_uses_latest_eligible(tmp_path):
    forecast_dir = tmp_path / "forecasts"
    forecast_dir.mkdir()

    first = pd.DataFrame([_row(em_ml_pct=0.061359)])
    later_but_post_deadline = pd.DataFrame(
        [
            _row(
                em_ml_pct=0.055115,
                scored_at="2026-09-10T15:30:00+00:00",
            )
        ]
    )

    path = append_event_prediction_ledger(first, forecast_dir)
    assert path is not None
    append_event_prediction_ledger(first, forecast_dir)
    append_event_prediction_ledger(later_but_post_deadline, forecast_dir)

    ledger = pd.read_parquet(path)
    assert len(ledger) == 2  # duplicate forecast_id was not rewritten/appended

    frozen = latest_eligible_predictions(ledger)
    assert len(frozen) == 1
    assert frozen.iloc[0]["em_ml_pct"] == 0.061359
