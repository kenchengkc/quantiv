from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

import scripts.restore_reported_forecasts as restore


def _event(ticker: str, earnings_date: str, **extra) -> dict:
    return {"ticker": ticker, "earnings_date": earnings_date, **extra}


def test_forecast_rank_prefers_ml_over_later_options():
    ml = _event(
        "ADBE",
        "2026-09-10",
        em_ml_pct=0.054358,
        ml_snapshot_date="2026-09-09",
    )
    options = _event(
        "ADBE",
        "2026-09-10",
        em_straddle_pct=0.108039,
        as_of_date="2026-09-10",
    )

    assert restore._forecast_rank(ml) > restore._forecast_rank(options)


def test_normalize_legacy_ml_row_adds_canonical_display_fields():
    row = restore._normalize_forecast(
        _event(
            "FDX",
            "2026-09-16",
            em_ml_pct=0.036898,
            ml_snapshot_date="2026-09-14",
            em_straddle_pct=0.042,
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
            as_of_date="2026-09-09",
            em_straddle_pct=0.081714,
            em_iv_pct=0.097601,
            expiry_date="2026-09-18",
            days_to_expiry=9,
            lead_time_days=0,
        )
    )

    assert event["display_forecast_method"] == "options_math"
    assert event["display_forecast_pct"] == pytest.approx(0.081714)
    assert event["expiration"] == "2026-09-18"
    assert event["dte"] == 9
    assert event["forecast_frozen"] is True
