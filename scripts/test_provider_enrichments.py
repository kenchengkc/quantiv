from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from provider_probe import classify_response  # noqa: E402
from provider_specs import endpoint_specs  # noqa: E402
from provider_utils import (  # noqa: E402
    ProviderBudget,
    ProviderQuotaError,
    ProviderUsageLedger,
    api_keys_for_provider,
    massive_api_key,
)
from sync_provider_enrichments import normalize_options_signal  # noqa: E402


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=None, content_type="application/json"):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else ""
        self.headers = {"content-type": content_type}
        self.ok = status_code < 400

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def success_response_for(spec):
    if spec.response_kind == "csv":
        return FakeResponse(
            payload=None,
            text="symbol,name\nAAPL,Apple Inc.\n",
            content_type="text/csv",
        )
    return FakeResponse(payload={"results": [{"field": "value"}], "status": "OK"})


def test_every_planned_endpoint_classifies_success_gated_quota_empty_and_malformed():
    now = datetime(2026, 5, 31, tzinfo=timezone.utc)
    for spec in endpoint_specs("AAPL", today=date(2026, 5, 31)):
        assert classify_response(spec, success_response_for(spec), now=now)["status"] == "ok"
        assert classify_response(
            spec,
            FakeResponse(402, text="Premium endpoint is not available under your current subscription"),
            now=now,
        )["status"] == "entitlement_denied"
        assert classify_response(spec, FakeResponse(429, text="rate limit"), now=now)["status"] == "quota_limited"
        if spec.response_kind == "csv":
            empty = FakeResponse(payload=None, text="symbol,name\n", content_type="text/csv")
        else:
            empty = FakeResponse(payload={"results": []})
        assert classify_response(spec, empty, now=now)["status"] == "empty"
        if spec.response_kind == "json":
            malformed = FakeResponse(payload=ValueError("bad json"))
            assert classify_response(spec, malformed, now=now)["status"] == "malformed"


def test_provider_ledger_reserves_daily_budget_before_calls(tmp_path):
    ledger = ProviderUsageLedger(
        tmp_path / "provider_usage_ledger.json",
        budgets={"fmp": ProviderBudget(daily_limit=2)},
        today_fn=lambda: date(2026, 5, 31),
        now_fn=lambda: datetime(2026, 5, 31, 12, tzinfo=timezone.utc),
    )

    assert ledger.reserve("fmp", "one") == 1
    assert ledger.reserve("fmp", "two") == 1
    try:
        ledger.reserve("fmp", "three")
    except ProviderQuotaError as exc:
        assert "daily budget exhausted" in str(exc)
    else:
        raise AssertionError("expected ProviderQuotaError")

    assert ledger.used("fmp") == 2


def test_provider_ledger_blocks_minute_budget_without_wait(tmp_path):
    now = datetime(2026, 5, 31, 12, tzinfo=timezone.utc)
    ledger = ProviderUsageLedger(
        tmp_path / "provider_usage_ledger.json",
        budgets={"massive": ProviderBudget(daily_limit=None, minute_limit=1, minute_window_sec=60)},
        today_fn=lambda: date(2026, 5, 31),
        now_fn=lambda: now,
    )

    ledger.reserve("massive", "first")
    try:
        ledger.reserve("massive", "second")
    except ProviderQuotaError as exc:
        assert "minute budget exhausted" in str(exc)
    else:
        raise AssertionError("expected ProviderQuotaError")


def test_massive_key_prefers_polygon(monkeypatch):
    monkeypatch.setenv("POLYGON_API_KEY", "polygon-key")
    monkeypatch.setenv("MASSIVE_API_KEY", "massive-key")
    assert massive_api_key() == "polygon-key"


def test_api_keys_for_provider_collects_and_dedupes_stacked_keys(monkeypatch):
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "aaa")
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY_2", "bbb")
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY_3", "aaa")  # duplicate, dropped
    assert api_keys_for_provider("alphavantage") == ["aaa", "bbb"]


def test_key_pool_stacking_extends_daily_budget(tmp_path):
    ledger = ProviderUsageLedger(
        tmp_path / "provider_usage_ledger.json",
        budgets={"alphavantage": ProviderBudget(daily_limit=2)},
        today_fn=lambda: date(2026, 5, 31),
        now_fn=lambda: datetime(2026, 5, 31, 12, tzinfo=timezone.utc),
    )
    accounts = ["k0", "k1"]
    chosen = [ledger.reserve_pooled("alphavantage", "av_x", accounts)[0] for _ in range(4)]
    # Two keys at 2/day each → fills k0 first, then k1: 4 total reservations.
    assert chosen == ["k0", "k0", "k1", "k1"]
    assert ledger.used("alphavantage") == 4
    try:
        ledger.reserve_pooled("alphavantage", "av_x", accounts)
    except ProviderQuotaError as exc:
        assert "daily budget exhausted across 2 key(s)" in str(exc)
    else:
        raise AssertionError("expected ProviderQuotaError once all keys exhausted")


def test_massive_options_signal_is_derived_not_raw_payload():
    spec = [s for s in endpoint_specs("AAPL") if s.id == "massive_options_snapshot"][0]
    payload = {
        "results": [
            {
                "details": {"contract_type": "call", "strike_price": 100, "ticker": "O:AAPL"},
                "underlying_asset": {"price": 101},
                "open_interest": 10,
                "day": {"volume": 4},
                "implied_volatility": 0.4,
                "greeks": {"delta": 0.51},
                "last_quote": {"bid": 1.0, "ask": 1.2, "timestamp": "100"},
            },
            {
                "details": {"contract_type": "put", "strike_price": 100, "ticker": "O:AAPL2"},
                "underlying_asset": {"price": 101},
                "open_interest": 5,
                "day": {"volume": 8},
                "implied_volatility": 0.5,
                "greeks": {"delta": -0.49},
                "last_quote": {"bid": 1.1, "ask": 1.3, "timestamp": "101"},
            },
        ]
    }

    rows = normalize_options_signal(spec, "AAPL", payload, "2026-05-31T12:00:00Z")
    assert rows[0]["contract_count"] == 2
    assert rows[0]["put_call_open_interest_ratio"] == 0.5
    assert rows[0]["put_call_volume_ratio"] == 2.0
    assert "results" not in rows[0]
    assert "details" not in rows[0]
