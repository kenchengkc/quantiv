"""Tests for the read-only earnings provider benchmark."""

from __future__ import annotations

from benchmark_earnings_providers import (
    normalize_session,
    parse_alphavantage_csv,
    parse_finnhub,
    parse_fmp,
    parse_massive,
    parse_twelvedata,
    score_provider,
)


def test_normalize_session_handles_vendor_labels_and_times() -> None:
    assert normalize_session("bmo") == "bmo"
    assert normalize_session("Pre Market") == "bmo"
    assert normalize_session("After Hours") == "amc"
    assert normalize_session("07:00:00") == "bmo"
    assert normalize_session("16:05:00") == "amc"
    assert normalize_session("12:00:00") == "unknown"
    assert normalize_session(None) == "unknown"


def test_provider_parsers_normalize_date_session_and_confirmation() -> None:
    finnhub = parse_finnhub(
        {"earningsCalendar": [{"symbol": "AIR", "date": "2026-09-29", "hour": "amc"}]}
    )
    assert finnhub == [
        {
            "ticker": "AIR",
            "date": "2026-09-29",
            "session": "amc",
            "confirmation": "unknown",
        }
    ]

    twelve = parse_twelvedata(
        {
            "earnings": {
                "2026-09-30": [
                    {"symbol": "CAG", "time": "Pre Market", "country": "United States"}
                ]
            }
        }
    )
    assert twelve == [
        {
            "ticker": "CAG",
            "date": "2026-09-30",
            "session": "bmo",
            "confirmation": "unknown",
        }
    ]

    fmp = parse_fmp(
        [
            {
                "symbol": "NKE",
                "date": "2026-10-01",
                "time": "16:15:00",
                "dateStatus": "confirmed",
            }
        ]
    )
    assert fmp == [
        {
            "ticker": "NKE",
            "date": "2026-10-01",
            "session": "amc",
            "confirmation": "confirmed",
        }
    ]

    massive = parse_massive(
        {
            "results": [
                {
                    "ticker": "NKE",
                    "date": "2026-10-01",
                    "time": "16:15:00",
                    "date_status": "confirmed",
                }
            ]
        }
    )
    assert massive == [
        {
            "ticker": "NKE",
            "date": "2026-10-01",
            "session": "amc",
            "confirmation": "confirmed",
        }
    ]

    av_csv = (
        "symbol,name,reportDate,fiscalDateEnding,estimate,currency\n"
        "ACN,Accenture plc,2026-10-01,2026-08-31,3.0,USD\n"
    )
    assert parse_alphavantage_csv(av_csv) == [
        {
            "ticker": "ACN",
            "date": "2026-10-01",
            "session": "unknown",
            "confirmation": "unknown",
        }
    ]


def test_score_provider_penalizes_wrong_dates_false_positives_and_sessions() -> None:
    truth = {
        "AIR": {"date": "2026-09-29", "session": "amc"},
        "NKE": {"date": "2026-10-01", "session": "amc"},
        "CAG": {"date": "2026-09-30", "session": "bmo"},
        "FERG": None,
    }
    records = [
        {"ticker": "AIR", "date": "2026-09-29", "session": "amc", "confirmation": "unknown"},
        {"ticker": "NKE", "date": "2026-09-28", "session": "amc", "confirmation": "unknown"},
        {"ticker": "CAG", "date": "2026-09-30", "session": "amc", "confirmation": "unknown"},
        {"ticker": "FERG", "date": "2026-09-22", "session": "unknown", "confirmation": "unknown"},
    ]

    result = score_provider(records, truth)

    assert result["truth_events"] == 3
    assert result["date_hits"] == 2
    assert result["date_accuracy"] == 2 / 3
    assert result["session_known_truth"] == 3
    assert result["session_hits"] == 1
    assert result["session_accuracy"] == 1 / 3
    assert result["wrong_dates"] == {"NKE": ["2026-09-28"]}
    assert result["wrong_sessions"] == {"CAG": ["amc"]}
    assert result["false_positives"] == {"FERG": ["2026-09-22"]}
    assert result["missing"] == []
