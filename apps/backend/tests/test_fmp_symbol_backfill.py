from datetime import date, datetime, timedelta, timezone
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from backfill_fmp_earnings_by_symbol import (  # noqa: E402
    FMPRequestError,
    is_symbol_endpoint_unavailable,
    normalize_fmp,
    recent_symbol_endpoint_unavailable,
    request_symbol,
    select_symbols,
)
from sync_fmp_earnings import merge_overlay  # noqa: E402


def test_select_symbols_prioritizes_missing_and_skips_fresh_state():
    existing = pd.DataFrame(
        [
            {
                "act_symbol": "AAPL",
                "date": date(2026, 5, 28),
                "timing": "amc",
                "fiscal_year": 2026,
                "fiscal_q": "Q2",
                "eps_actual": None,
                "eps_estimate": None,
                "revenue_actual": None,
                "revenue_estimate": None,
                "source": "dolthub",
            },
            {
                "act_symbol": "MSFT",
                "date": date(2026, 5, 28),
                "timing": "amc",
                "fiscal_year": 2026,
                "fiscal_q": "Q2",
                "eps_actual": None,
                "eps_estimate": None,
                "revenue_actual": None,
                "revenue_estimate": None,
                "source": "dolthub",
            },
        ]
    )
    now = datetime(2026, 5, 27, tzinfo=timezone.utc)
    state = {
        "symbols": {
            "MSFT": {
                "checked_at": (now - timedelta(days=1)).isoformat(),
                "ok": True,
            }
        }
    }

    selected, pending = select_symbols(
        existing,
        state,
        manual_symbols=[],
        today=date(2026, 5, 27),
        now=now,
        refresh_after_days=90,
        force=False,
        max_symbols=10,
        max_calls=10,
    )

    assert selected == ["AAPL"]
    assert pending == 1


def test_fmp_symbol_rows_merge_fill_missing_only():
    existing = pd.DataFrame(
        [
            {
                "act_symbol": "AAPL",
                "date": date(2026, 7, 30),
                "timing": "amc",
                "fiscal_year": 2026,
                "fiscal_q": "Q3",
                "eps_actual": None,
                "eps_estimate": None,
                "revenue_actual": None,
                "revenue_estimate": None,
                "source": "dolthub",
            }
        ]
    )
    overlay = normalize_fmp(
        [
            {
                "symbol": "AAPL",
                "date": "2026-07-30",
                "epsActual": None,
                "epsEstimated": 1.86,
                "revenueActual": None,
                "revenueEstimated": 108414000000,
            },
            {
                "symbol": "AAPL",
                "date": "2026-10-29",
                "epsActual": None,
                "epsEstimated": 2.01,
                "revenueActual": None,
                "revenueEstimated": 111000000000,
            },
        ]
    )

    merged, stats = merge_overlay(existing, overlay)
    row = merged[merged["act_symbol"].eq("AAPL")].iloc[0]

    assert stats["updated"] == 1
    assert stats["skipped_new_events"] == 1
    assert row["eps_estimate"] == 1.86
    assert row["revenue_estimate"] == 108414000000
    assert row["source"] == "dolthub+fmp"


def test_symbol_endpoint_402_stops_for_plan_entitlement(monkeypatch):
    class Response:
        status_code = 402
        text = (
            "Premium Query Parameter: 'Special Endpoint : This value set for "
            "'symbol' is not available under your current subscription"
        )
        ok = False

    monkeypatch.setattr(
        "backfill_fmp_earnings_by_symbol.requests.get",
        lambda *args, **kwargs: Response(),
    )

    try:
        request_symbol("AAPL", "test-token")
    except FMPRequestError as exc:
        assert exc.stop is True
        assert is_symbol_endpoint_unavailable(str(exc))
    else:
        raise AssertionError("expected FMPRequestError")


def test_recent_symbol_endpoint_unavailable_detects_existing_state():
    now = datetime(2026, 5, 27, tzinfo=timezone.utc)
    state = {
        "symbols": {
            "REX": {
                "checked_at": (now - timedelta(days=1)).isoformat(),
                "ok": False,
                "error": (
                    "FMP HTTP 402: Premium Query Parameter: "
                    "value set for 'symbol' is not available under your "
                    "current subscription"
                ),
            }
        }
    }

    reason = recent_symbol_endpoint_unavailable(
        state,
        now=now,
        refresh_after_days=90,
    )

    assert reason is not None
    assert "symbol" in reason
