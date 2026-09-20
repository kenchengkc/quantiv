from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

import scripts.restore_reported_forecasts as restore


def _event(ticker: str, earnings_date: str, **extra) -> dict:
    return {"ticker": ticker, "earnings_date": earnings_date, **extra}


def test_forecast_rank_prefers_ml_over_iv_when_both_are_pre_event():
    ml = _event(
        "ADBE",
        "2026-09-10",
        em_ml_pct=0.054358,
        ml_snapshot_date="2026-09-09",
        forecast_frozen_eligible=True,
    )
    options = _event(
        "ADBE",
        "2026-09-10",
        timing="amc",
        em_straddle_pct=0.108039,
        em_iv_pct=0.134685,
        as_of_date="2026-09-10",
    )

    assert restore._forecast_rank(ml) > restore._forecast_rank(options)


def test_normalize_legacy_row_prefers_ml_but_preserves_options_provenance():
    row = restore._normalize_forecast(
        _event(
            "FDX",
            "2026-09-16",
            timing="amc",
            as_of_date="2026-09-14",
            em_ml_pct=0.036898,
            ml_snapshot_date="2026-09-14",
            forecast_frozen_eligible=True,
            em_straddle_pct=0.042,
            em_iv_pct=0.052634,
        )
    )

    assert row["display_forecast_pct"] == pytest.approx(0.036898)
    assert row["display_forecast_method"] == "ml"
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
    monkeypatch.setattr(
        restore,
        "_commit_at",
        lambda _commit: restore.datetime.fromisoformat("2026-09-15T20:00:00+00:00"),
    )
    monkeypatch.setattr(restore, "_symbol_history_candidate", lambda _key: None)
    monkeypatch.setattr(restore, "_symbol_expected_move_candidate", lambda _key: None)
    monkeypatch.setattr(restore, "_symbol_historical_candidate", lambda _key: None)

    repaired = restore.recover_week(
        path,
        {("FDX", "2026-09-16")},
        date(2026, 9, 19),
        apply=True,
    )

    assert ("FDX", "2026-09-16") in repaired
    payload = json.loads(path.read_text(encoding="utf-8"))
    event = payload["events"][0]
    assert event["display_forecast_method"] == "ml"
    assert event["display_forecast_pct"] == pytest.approx(0.036898)
    assert event["realized_move_pct"] == pytest.approx(0.013788)


def test_symbol_expected_move_maps_week_fields_to_symbol_contract():
    event = restore._symbol_expected_move(
        _event(
            "COO",
            "2026-09-09",
            timing="after_market_close",
            as_of_date="2026-09-08",
            em_straddle_pct=0.081714,
            em_iv_pct=0.097601,
            expiry_date="2026-09-18",
            days_to_expiry=10,
            lead_time_days=1,
        )
    )

    assert event["display_forecast_method"] == "options_math"
    assert event["display_forecast_pct"] == pytest.approx(0.097601)
    assert event["expiration"] == "2026-09-18"
    assert event["dte"] == 10
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
        forecast_frozen_eligible=True,
    )

    assert restore._forecast_rank(options)[0] == 0
    assert restore._forecast_rank(ml)[0] > 0


def test_symbol_history_candidate_computes_prior_session_event_iv_forecast(
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
                        "implied_as_of": "2026-09-15",
                        "implied_expiration": "2026-10-02",
                        "implied_dte": 17,
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
        0.29635 * (17 / 365.0) ** 0.5
    )
    assert restore._normalize_forecast(candidate)["display_forecast_method"] == "options_math"




def test_symbol_expected_move_candidate_recovers_legacy_pre_event_iv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    symbols = tmp_path / "symbols"
    symbols.mkdir()
    (symbols / "ABM.json").write_text(
        json.dumps(
            {
                "as_of_date": "2026-08-27",
                "expected_move": {
                    "earnings_date": "2026-09-08",
                    "timing": "before_market_open",
                    "expiration": "2026-09-18",
                    "dte": 22,
                    "lead_time_days": 12,
                    "atm_strike": 50.0,
                    "atm_iv": 0.545013,
                    "straddle_abs": 3.775,
                    "straddle_pct": 0.0755,
                    "iv_pct": 0.133805,
                    "em_method": "options_math",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(restore, "SYMBOLS_DIR", symbols)

    candidate = restore._symbol_expected_move_candidate(("ABM", "2026-09-08"))

    assert candidate is not None
    assert candidate["em_iv_pct"] == pytest.approx(0.133805)
    assert candidate["em_straddle_pct"] == pytest.approx(0.0755)
    normalized = restore._normalize_forecast(candidate)
    assert normalized["display_forecast_method"] == "options_math"
    assert normalized["display_forecast_pct"] == pytest.approx(0.133805)


def test_symbol_historical_candidate_matches_ticker_page_four_prior_event_median(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    symbols = tmp_path / "symbols"
    symbols.mkdir()
    (symbols / "OXM.json").write_text(
        json.dumps(
            {
                "earnings_history": [
                    {"date": "2026-06-10", "actual": -0.170055},
                    {"date": "2026-06-09", "actual": 0.002780},
                    {"date": "2026-03-26", "actual": 0.086861},
                    {"date": "2025-12-10", "actual": -0.212361},
                    {"date": "2025-09-10", "actual": 0.276417},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(restore, "SYMBOLS_DIR", symbols)

    candidate = restore._symbol_historical_candidate(("OXM", "2026-09-08"))

    assert candidate is not None
    assert candidate["hist_move_med_4q"] == pytest.approx(0.128458)
    assert candidate["display_forecast_pct"] == pytest.approx(0.128458)
    assert candidate["display_forecast_method"] == "historical"
    assert restore._forecast_rank(candidate)[0] == 1


def test_post_deadline_published_ml_does_not_outrank_pre_event_iv():
    candidate = _event(
        "M",
        "2026-09-10",
        timing="before_market_open",
        em_ml_pct=0.055115,
        ml_snapshot_date="2026-09-09",
        forecast_published_at="2026-09-10T15:42:00+00:00",
        as_of_date="2026-09-09",
        em_iv_pct=0.124093,
        em_straddle_pct=0.10087,
    )

    assert restore._forecast_rank(candidate)[0] == 3
    normalized = restore._normalize_forecast(candidate)
    # Normalization is presentation-only; ranking is the safety gate. The
    # recovery path therefore selects IV when the ML publication missed cutoff.
    candidate["em_ml_pct"] = None
    normalized = restore._normalize_forecast(candidate)
    assert normalized["display_forecast_method"] == "options_math"


def test_pre_deadline_published_ml_is_recoverable():
    candidate = _event(
        "M",
        "2026-09-10",
        timing="before_market_open",
        em_ml_pct=0.061359,
        ml_snapshot_date="2026-09-08",
        forecast_published_at="2026-09-09T15:35:05+00:00",
    )

    assert restore._forecast_rank(candidate)[0] == 4
