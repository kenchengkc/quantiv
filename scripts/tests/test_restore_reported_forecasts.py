from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

import scripts.restore_reported_forecasts as restore


def _event(ticker: str, earnings_date: str, **extra) -> dict:
    return {"ticker": ticker, "earnings_date": earnings_date, **extra}


def test_forecast_rank_prefers_iv_over_ml_when_both_are_pre_event():
    ml = _event(
        "ADBE",
        "2026-09-10",
        em_ml_pct=0.054358,
        ml_snapshot_date="2026-09-09",
    )
    options = _event(
        "ADBE",
        "2026-09-10",
        timing="amc",
        em_straddle_pct=0.108039,
        em_iv_pct=0.134685,
        as_of_date="2026-09-10",
    )

    assert restore._forecast_rank(options) > restore._forecast_rank(ml)


def test_normalize_legacy_row_prefers_iv_but_preserves_ml_provenance():
    row = restore._normalize_forecast(
        _event(
            "FDX",
            "2026-09-16",
            timing="amc",
            as_of_date="2026-09-14",
            em_ml_pct=0.036898,
            ml_snapshot_date="2026-09-14",
            em_straddle_pct=0.042,
            em_iv_pct=0.052634,
        )
    )

    assert row["display_forecast_pct"] == pytest.approx(0.052634)
    assert row["display_forecast_method"] == "options_math"
    assert row["display_forecast_as_of"] == "2026-09-14"
    assert row["ml_status"] == "available"
    assert row["options_status"] == "decision_eligible"
    assert row["forecast_frozen"] is True


def test_recover_week_repairs_present_row_instead_of_only_missing_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    path = tmp_path / "2026-09-14.json"
    path.write_text(
        json.dumps(
            {
                "window": {"start": "2026-09-14", "end": "2026-09-18"},
                "events": [
                    _event(
                        "FDX",
                        "2026-09-16",
                        realized_move_pct=0.013788,
                        em_method=None,
                        em_ml_pct=None,
                    )
                ],
                "summary": {
                    "total_events": 1,
                    "avg_em_straddle_pct": 0.0,
                    "avg_em_iv_pct": 0.0,
                },
            }
        ),
        encoding="utf-8",
    )

    historical = {
        "events": [
            _event(
                "FDX",
                "2026-09-16",
                timing="amc",
                as_of_date="2026-09-14",
                em_method="ml_lightgbm",
                em_ml_pct=0.036898,
                ml_snapshot_date="2026-09-14",
                model_horizon=2,
                em_straddle_pct=0.042,
                em_iv_pct=0.052634,
                p50=0.0396,
            )
        ]
    }
    monkeypatch.setattr(restore, "_history", lambda _path: ["abc"])
    monkeypatch.setattr(restore, "_bundle_at", lambda _commit, _path: historical)
    monkeypatch.setattr(restore, "_symbol_history_candidate", lambda _key: None)

    repaired = restore.recover_week(
        path,
        {("FDX", "2026-09-16")},
        date(2026, 9, 19),
        apply=True,
    )

    assert ("FDX", "2026-09-16") in repaired
    payload = json.loads(path.read_text(encoding="utf-8"))
    event = payload["events"][0]
    assert event["display_forecast_method"] == "options_math"
    assert event["display_forecast_pct"] == pytest.approx(0.052634)
    assert event["realized_move_pct"] == pytest.approx(0.013788)


def test_symbol_expected_move_maps_week_fields_to_symbol_contract():
    event = restore._symbol_expected_move(
        _event(
            "COO",
            "2026-09-09",
            timing="after_market_close",
            as_of_date="2026-09-09",
            em_straddle_pct=0.081714,
            em_iv_pct=0.097601,
            expiry_date="2026-09-18",
            days_to_expiry=9,
            lead_time_days=0,
        )
    )

    assert event["display_forecast_method"] == "options_math"
    assert event["display_forecast_pct"] == pytest.approx(0.097601)
    assert event["expiration"] == "2026-09-18"
    assert event["dte"] == 9
    assert event["forecast_frozen"] is True



def test_same_day_bmo_options_are_rejected_as_post_event_evidence():
    options = _event(
        "KR",
        "2026-09-11",
        timing="bmo",
        as_of_date="2026-09-11",
        em_straddle_pct=0.05,
        em_iv_pct=0.06,
    )
    ml = _event(
        "KR",
        "2026-09-11",
        timing="bmo",
        em_ml_pct=0.046961,
        ml_snapshot_date="2026-09-10",
    )

    assert restore._forecast_rank(options)[0] == 0
    assert restore._forecast_rank(ml)[0] > 0


def test_symbol_history_candidate_computes_event_iv_forecast(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    symbols = tmp_path / "symbols"
    symbols.mkdir()
    (symbols / "FDX.json").write_text(
        json.dumps(
            {
                "earnings_history": [
                    {
                        "date": "2026-09-16",
                        "timing": "after_market_close",
                        "implied": 0.049818,
                        "implied_as_of": "2026-09-16",
                        "implied_expiration": "2026-10-02",
                        "implied_dte": 16,
                        "implied_atm_iv": 0.29635,
                        "implied_quality_status": "decision_eligible_eod",
                        "em_ml_pct": 0.036898,
                        "ml_snapshot_date": "2026-09-14",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(restore, "SYMBOLS_DIR", symbols)

    candidate = restore._symbol_history_candidate(("FDX", "2026-09-16"))

    assert candidate is not None
    assert candidate["em_straddle_pct"] == pytest.approx(0.049818)
    assert candidate["em_iv_pct"] == pytest.approx(
        0.29635 * (16 / 365.0) ** 0.5
    )
    assert restore._normalize_forecast(candidate)["display_forecast_method"] == "options_math"
