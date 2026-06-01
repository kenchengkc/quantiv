from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

import build_frontend_data as frontend
from twelvedata_basic import (
    TwelveDataConfig,
    TwelveDataUsageLedger,
    fetch_daily_closes,
    parse_daily_closes_payload,
    plan_credit_use,
)


def test_reaction_close_dates_skip_weekends_and_holidays():
    assert frontend.earnings_reaction_close_date(
        date(2026, 5, 22),
        "after_market_close",
    ) == date(2026, 5, 26)
    assert frontend.earnings_reaction_close_date(
        date(2026, 5, 29),
        "amc",
    ) == date(2026, 6, 1)
    assert frontend.earnings_reaction_close_date(
        date(2026, 5, 28),
        "before_market_open",
    ) == date(2026, 5, 28)


def test_realization_window_complete_for_bmo_and_amc():
    et = ZoneInfo("America/New_York")
    assert not frontend.realization_window_complete(
        date(2026, 5, 28),
        "bmo",
        datetime(2026, 5, 28, 15, 59, tzinfo=et),
    )
    assert frontend.realization_window_complete(
        date(2026, 5, 28),
        "bmo",
        datetime(2026, 5, 28, 16, 0, tzinfo=et),
    )
    assert not frontend.realization_window_complete(
        date(2026, 5, 29),
        "amc",
        datetime(2026, 5, 30, 12, 0, tzinfo=et),
    )


def test_compute_realized_from_closes_bmo_and_amc():
    closes = [
        (date(2026, 5, 27), 100.0),
        (date(2026, 5, 28), 110.0),
        (date(2026, 5, 29), 104.5),
    ]
    assert frontend._compute_realized_from_closes(
        closes,
        date(2026, 5, 28),
        "before_market_open",
    ) == pytest.approx(0.10)
    assert frontend._compute_realized_from_closes(
        closes,
        date(2026, 5, 28),
        "after_market_close",
    ) == pytest.approx(-0.05)


def test_parse_single_symbol_time_series_payload():
    payload = {
        "meta": {"symbol": "AAPL"},
        "values": [
            {"datetime": "2026-05-28", "close": "100.5"},
            {"datetime": "2026-05-29", "close": "102.0"},
        ],
    }
    closes, errors = parse_daily_closes_payload(payload, ["AAPL"])
    assert errors == []
    assert closes == {"AAPL": [(date(2026, 5, 28), 100.5), (date(2026, 5, 29), 102.0)]}


def test_parse_batch_payload_with_partial_error_and_empty_values():
    payload = {
        "AAPL": {
            "values": [{"datetime": "2026-05-29", "close": "102.0"}],
        },
        "BAD": {"status": "error", "message": "invalid symbol"},
        "EMPTY": {"values": []},
    }
    closes, errors = parse_daily_closes_payload(payload, ["AAPL", "BAD", "EMPTY"])
    assert closes == {"AAPL": [(date(2026, 5, 29), 102.0)]}
    assert "BAD: invalid symbol" in errors
    assert "EMPTY: no usable daily closes" in errors


def test_plan_credit_use_respects_remaining_daily_ledger(tmp_path):
    ledger = TwelveDataUsageLedger(
        tmp_path / "ledger.json",
        3,
        today_fn=lambda: date(2026, 5, 30),
    )
    ledger.reserve(["AAPL"], purpose="test")
    config = TwelveDataConfig(
        api_key="key",
        daily_credit_limit=3,
        batch_size=2,
        batch_delay_sec=0,
        ledger_path=tmp_path / "ledger.json",
        realized_fallback_enabled=True,
    )
    plan = plan_credit_use(["MSFT", "AAPL", "NVDA"], config, ledger=ledger)
    assert plan["remaining_credits"] == 2
    assert plan["planned_symbols"] == ["AAPL", "MSFT"]
    assert plan["skipped_symbols"] == ["NVDA"]


class _FakeResponse:
    def __init__(self, payload, headers=None):
        self.payload = payload
        self.headers = headers or {}

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_fetch_daily_closes_reserves_before_request_and_caps_quota(tmp_path, monkeypatch):
    monkeypatch.setenv("TWELVEDATA_SHARE_PROVIDER_LEDGER", "0")
    ledger = TwelveDataUsageLedger(
        tmp_path / "ledger.json",
        3,
        today_fn=lambda: date(2026, 5, 30),
    )
    config = TwelveDataConfig(
        api_key="key",
        daily_credit_limit=3,
        batch_size=2,
        batch_delay_sec=0,
        ledger_path=tmp_path / "ledger.json",
        realized_fallback_enabled=True,
    )
    calls = []

    def fake_get(_url, *, params, timeout):
        calls.append((params, timeout))
        return _FakeResponse({
            symbol: {"values": [{"datetime": "2026-05-29", "close": "100"}]}
            for symbol in params["symbol"].split(",")
        })

    result = fetch_daily_closes(
        ["AAPL", "MSFT", "NVDA", "TSLA"],
        date(2026, 5, 28),
        date(2026, 5, 29),
        config,
        ledger=ledger,
        http_get=fake_get,
    )

    assert result.used_credits == 3
    assert ledger.used() == 3
    assert result.skipped_symbols == ["TSLA"]
    assert [call[0]["symbol"] for call in calls] == ["AAPL,MSFT", "NVDA"]
    assert set(result.closes) == {"AAPL", "MSFT", "NVDA"}


def test_fetch_daily_closes_records_provider_credit_headers(tmp_path, monkeypatch):
    monkeypatch.setenv("TWELVEDATA_SHARE_PROVIDER_LEDGER", "0")
    ledger = TwelveDataUsageLedger(
        tmp_path / "ledger.json",
        2,
        today_fn=lambda: date(2026, 5, 30),
    )
    config = TwelveDataConfig(
        api_key="key",
        daily_credit_limit=2,
        batch_size=2,
        batch_delay_sec=0,
        ledger_path=tmp_path / "ledger.json",
        realized_fallback_enabled=True,
    )

    def fake_get(_url, *, params, timeout):
        return _FakeResponse(
            {"AAPL": {"values": [{"datetime": "2026-05-29", "close": "100"}]}},
            headers={"api-credits-used": "1", "api-credits-left": "799"},
        )

    result = fetch_daily_closes(
        ["AAPL"],
        date(2026, 5, 28),
        date(2026, 5, 29),
        config,
        ledger=ledger,
        http_get=fake_get,
    )

    assert result.provider_credits_used == 1
    assert result.provider_credits_left == 799


def test_fetch_daily_closes_skips_partial_quota_batch_once(tmp_path, monkeypatch):
    monkeypatch.setenv("TWELVEDATA_SHARE_PROVIDER_LEDGER", "0")
    ledger = TwelveDataUsageLedger(
        tmp_path / "ledger.json",
        3,
        today_fn=lambda: date(2026, 5, 30),
    )
    config = TwelveDataConfig(
        api_key="key",
        daily_credit_limit=3,
        batch_size=8,
        batch_delay_sec=0,
        ledger_path=tmp_path / "ledger.json",
        realized_fallback_enabled=True,
    )
    symbols = [f"S{i:02d}" for i in range(12)]

    def fake_get(_url, *, params, timeout):
        return _FakeResponse({
            symbol: {"values": [{"datetime": "2026-05-29", "close": "100"}]}
            for symbol in params["symbol"].split(",")
        })

    result = fetch_daily_closes(
        symbols,
        date(2026, 5, 28),
        date(2026, 5, 29),
        config,
        ledger=ledger,
        http_get=fake_get,
    )

    assert result.requested_symbols == ["S00", "S01", "S02"]
    assert result.skipped_symbols == symbols[3:]
    assert len(result.skipped_symbols) == len(set(result.skipped_symbols))


def test_fetch_daily_closes_records_provider_quota_error(tmp_path, monkeypatch):
    monkeypatch.setenv("TWELVEDATA_SHARE_PROVIDER_LEDGER", "0")
    ledger = TwelveDataUsageLedger(
        tmp_path / "ledger.json",
        2,
        today_fn=lambda: date(2026, 5, 30),
    )
    config = TwelveDataConfig(
        api_key="key",
        daily_credit_limit=2,
        batch_size=2,
        batch_delay_sec=0,
        ledger_path=tmp_path / "ledger.json",
        realized_fallback_enabled=True,
    )

    def fake_get(_url, *, params, timeout):
        return _FakeResponse({"status": "error", "message": "API credits exceeded"})

    result = fetch_daily_closes(
        ["AAPL", "MSFT"],
        date(2026, 5, 28),
        date(2026, 5, 29),
        config,
        ledger=ledger,
        http_get=fake_get,
    )

    assert result.closes == {}
    assert result.used_credits == 2
    assert ledger.used() == 2
    assert result.errors == ["API credits exceeded"]


def test_twelvedata_realized_candidate_stats():
    events = [
        {"ticker": "DONE", "earnings_date": "2026-05-20", "timing": "bmo", "realized_move_pct": 0.1},
        {"ticker": "MISS", "earnings_date": "2026-05-20", "timing": "bmo"},
        {"ticker": "FUTR", "earnings_date": "2027-05-20", "timing": "bmo"},
        {"ticker": "", "earnings_date": "bad-date", "timing": "bmo"},
    ]
    missing, stats = frontend.twelvedata_realized_candidates(events)
    assert [(ticker, dt) for _, ticker, dt, _ in missing] == [("MISS", date(2026, 5, 20))]
    assert stats == {"already_realized": 1, "not_complete": 1, "invalid": 1}


def test_hist_move_avg_from_twelvedata_updates_only_derived_field(monkeypatch):
    class FakeConn:
        def execute(self, _query, _params):
            return self

        def fetchall(self):
            return [
                (date(2026, 2, 20), "before_market_open"),
                (date(2025, 11, 20), "after_market_close"),
            ]

    events = [{"ticker": "MISS", "earnings_date": "2026-05-20", "timing": "bmo", "hist_move_avg_4q": None}]

    def fake_fetch(_symbols, _start, _end, _config, *, purpose):
        assert purpose == "hist_move_avg_4q"

        class Result:
            skipped_symbols = []
            errors = []
            closes = {
                "MISS": [
                    (date(2025, 11, 20), 100.0),
                    (date(2025, 11, 21), 90.0),
                    (date(2026, 2, 19), 50.0),
                    (date(2026, 2, 20), 55.0),
                ],
            }

        return Result()

    monkeypatch.setenv("TWELVEDATA_API_KEY", "key")
    monkeypatch.setattr(frontend, "fetch_daily_closes", fake_fetch)
    updated = frontend.enrich_hist_move_avg_from_twelvedata(FakeConn(), events)
    assert updated == 1
    assert events == [{
        "ticker": "MISS",
        "earnings_date": "2026-05-20",
        "timing": "bmo",
        "hist_move_avg_4q": 0.1,
    }]


def test_validation_sample_logs_material_delta(monkeypatch, capsys):
    events = [{"ticker": "MISS", "earnings_date": "2026-05-20", "timing": "bmo"}]

    def fake_fetch(_symbols, _start, _end, _config, *, purpose):
        assert purpose == "validation_sample"

        class Result:
            skipped_symbols = []
            errors = []
            closes = {
                "MISS": [
                    (date(2026, 5, 19), 100.0),
                    (date(2026, 5, 20), 120.0),
                ],
            }

        return Result()

    monkeypatch.setenv("TWELVEDATA_API_KEY", "key")
    monkeypatch.setenv("TWELVEDATA_VALIDATION_SAMPLE_SIZE", "1")
    monkeypatch.setattr(frontend, "fetch_daily_closes", fake_fetch)
    monkeypatch.setattr(frontend, "realized_move_from_ohlcv", lambda *_args: 0.10)

    compared = frontend.validate_twelvedata_against_ohlcv(object(), events, label="test")
    out = capsys.readouterr().out

    assert compared == 1
    assert "material delta" in out
    assert "MISS 2026-05-20" in out
