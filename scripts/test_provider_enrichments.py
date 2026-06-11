from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from provider_probe import capability_probe_due, classify_response, select_specs  # noqa: E402
from provider_specs import endpoint_specs  # noqa: E402
from provider_utils import (  # noqa: E402
    ProviderBudget,
    ProviderQuotaError,
    ProviderUsageLedger,
    api_keys_for_provider,
    massive_api_key,
)
from sync_provider_enrichments import (  # noqa: E402
    load_fmp_symbol_blocks,
    normalize_options_signal,
)


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
    # Two keys at 2/day each → least-used balancing alternates keys (k0 wins
    # ties via stable sort): 4 total reservations.
    assert chosen == ["k0", "k1", "k0", "k1"]
    assert ledger.used("alphavantage") == 4
    try:
        ledger.reserve_pooled("alphavantage", "av_x", accounts)
    except ProviderQuotaError as exc:
        assert "daily budget exhausted across 2 key(s)" in str(exc)
    else:
        raise AssertionError("expected ProviderQuotaError once all keys exhausted")


def test_av_throttle_and_daily_cap_messages_are_quota_not_entitlement():
    # AV returns HTTP 200 with an "Information" message that upsells premium
    # plans in both its throttle and daily-cap notices. Neither is an
    # entitlement denial — a 30-day block here starves healthy endpoints.
    now = datetime(2026, 6, 10, tzinfo=timezone.utc)
    spec = [s for s in endpoint_specs("AAPL") if s.id == "av_cpi"][0]

    throttle = FakeResponse(payload={
        "Information": (
            "Thank you for using Alpha Vantage! Please consider spreading out "
            "your free API requests more sparingly (1 request per second). You "
            "may subscribe to any of the premium plans at "
            "https://www.alphavantage.co/premium/ to instantly remove all rate limits."
        ),
    })
    out = classify_response(spec, throttle, now=now)
    assert out["status"] == "quota_limited"
    assert out["retry_after"] == "2026-06-11"

    daily_cap = FakeResponse(payload={
        "Information": (
            "We have detected your API key and our standard API rate limit is "
            "25 requests per day. Please subscribe to any of the premium plans "
            "to instantly remove all daily rate limits."
        ),
    })
    assert classify_response(spec, daily_cap, now=now)["status"] == "quota_limited"

    premium = FakeResponse(payload={
        "Information": (
            "This is a premium endpoint. You may subscribe to any of the "
            "premium plans to instantly unlock all premium endpoints."
        ),
    })
    assert classify_response(spec, premium, now=now)["status"] == "entitlement_denied"


def test_fmp_symbol_blocks_load_prunes_expired_and_malformed(tmp_path):
    path = tmp_path / "fmp_symbol_blocks.json"
    path.write_text(
        '{"OLD": "2026-06-01", "LIVE": "2026-07-05", "BAD": "not-a-date"}'
    )
    blocks = load_fmp_symbol_blocks(path, date(2026, 6, 10))
    assert blocks == {"LIVE": "2026-07-05"}
    assert load_fmp_symbol_blocks(tmp_path / "missing.json", date(2026, 6, 10)) == {}


def test_single_key_and_pooled_reserves_share_the_k0_bucket(tmp_path):
    ledger = ProviderUsageLedger(
        tmp_path / "ledger.json",
        budgets={"alphavantage": ProviderBudget(daily_limit=5)},
    )
    # A single-key caller (e.g. the V/OI probe path) books to k0 by default…
    ledger.reserve("alphavantage", "av_voi_probe", credits=4)
    # …so the pool sees that usage and rotates to k1 instead of overdrawing
    # the same physical key.
    account, _ = ledger.reserve_pooled(
        "alphavantage", "av_earnings", ["k0", "k1"], credits=2
    )
    assert account == "k1"


def test_reserve_pooled_prefers_least_used_key(tmp_path):
    ledger = ProviderUsageLedger(
        tmp_path / "ledger.json",
        budgets={"alphavantage": ProviderBudget(daily_limit=10)},
    )
    ledger.reserve("alphavantage", "seed", credits=3, account="k0")
    account, _ = ledger.reserve_pooled(
        "alphavantage", "av_earnings", ["k0", "k1"], credits=1
    )
    assert account == "k1"
    # Now k1 has 1 used vs k0's 3 — still k1 until it catches up.
    account, _ = ledger.reserve_pooled(
        "alphavantage", "av_earnings", ["k0", "k1"], credits=1
    )
    assert account == "k1"


def test_ok_probe_results_get_weekly_recheck_window():
    now = datetime(2026, 6, 11, tzinfo=timezone.utc)
    spec = [s for s in endpoint_specs("AAPL") if s.id == "av_earnings"][0]
    out = classify_response(spec, success_response_for(spec), now=now)
    assert out["status"] == "ok"
    assert out["retry_after"] == "2026-06-18"
    # And select_specs honors it: a healthy endpoint inside its recheck
    # window is not re-probed.
    capabilities = {"endpoints": {"av_earnings": out}}
    ids = {spec.id for spec in select_specs(capabilities=capabilities, now=now)}
    assert "av_earnings" not in ids


def test_capability_probe_due_skips_future_retry_after():
    now = datetime(2026, 6, 10, tzinfo=timezone.utc)
    blocked = {
        "status": "entitlement_denied",
        "retry_after": "2026-07-10",
    }
    assert capability_probe_due(blocked, now=now) is False
    assert capability_probe_due(blocked, now=datetime(2026, 7, 10, tzinfo=timezone.utc)) is True
    assert capability_probe_due({"status": "missing_key"}, now=now) is True


def test_select_specs_skips_off_cadence_and_future_retry():
    now = datetime(2026, 6, 10, tzinfo=timezone.utc)
    capabilities = {
        "endpoints": {
            "fmp_batch_aftermarket_quote": {
                "status": "entitlement_denied",
                "retry_after": "2026-07-10",
            }
        }
    }
    ids = {spec.id for spec in select_specs(capabilities=capabilities, now=now)}
    assert "fmp_batch_aftermarket_quote" not in ids
    assert "massive_options_snapshot" in ids


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
