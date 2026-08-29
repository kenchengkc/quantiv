from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
import sys

import duckdb
import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

from setup_duckdb_from_parquet import setup_views  # noqa: E402


def _option(
    symbol: str,
    strike: float,
    side: str,
    *,
    bid: float,
    ask: float,
    delta: float | None,
    quote_timestamp: datetime | None = None,
    option_volume: int | None = None,
    open_interest: int | None = None,
) -> dict[str, object]:
    return {
        "date": date(2026, 8, 21),
        "act_symbol": symbol,
        "expiration": date(2026, 8, 28),
        "strike": strike,
        "call_put": side,
        "bid": bid,
        "ask": ask,
        "vol": 0.5,
        "delta": delta,
        "gamma": 0.1,
        "theta": -0.1,
        "vega": 0.1,
        "rho": 0.01,
        "quote_timestamp": quote_timestamp,
        "option_volume": option_volume,
        "open_interest": open_interest,
    }


def test_quote_views_pair_same_strike_and_fail_closed(tmp_path: Path) -> None:
    options = [
        # Each independently closest leg is on a different strike. Pair-first
        # selection must still emit one common-strike straddle.
        _option("GOOD", 100, "Call", bid=2.0, ask=2.2, delta=0.50),
        _option("GOOD", 100, "Put", bid=1.8, ask=2.0, delta=-0.40),
        _option("GOOD", 105, "Call", bid=1.8, ask=2.0, delta=0.40),
        _option("GOOD", 105, "Put", bid=2.0, ask=2.2, delta=-0.50),
        _option("CROSS", 100, "Call", bid=2.0, ask=1.0, delta=0.50),
        _option("CROSS", 100, "Put", bid=1.0, ask=1.2, delta=-0.50),
        _option("ORPHAN", 100, "Call", bid=1.0, ask=1.1, delta=0.50),
        _option("NODELTA", 100, "Call", bid=1.0, ask=1.1, delta=None),
        _option("NODELTA", 100, "Put", bid=1.0, ask=1.1, delta=-0.50),
        _option(
            "PARTIALILLIQ", 100, "Call", bid=1.0, ask=1.1, delta=0.50,
            option_volume=0,
        ),
        _option(
            "PARTIALILLIQ", 100, "Put", bid=1.0, ask=1.1, delta=-0.50,
            option_volume=0,
        ),
        _option(
            "NEGATIVE", 100, "Call", bid=1.0, ask=1.1, delta=0.50,
            option_volume=10, open_interest=-1,
        ),
        _option(
            "NEGATIVE", 100, "Put", bid=1.0, ask=1.1, delta=-0.50,
            option_volume=10, open_interest=-1,
        ),
        _option("FAR", 100, "Call", bid=1.0, ask=1.1, delta=0.10),
        _option("FAR", 100, "Put", bid=1.0, ask=1.1, delta=-0.10),
        _option(
            "STALE", 100, "Call", bid=1.0, ask=1.1, delta=0.50,
            quote_timestamp=datetime(2026, 8, 20, 20),
        ),
        _option(
            "STALE", 100, "Put", bid=1.0, ask=1.1, delta=-0.50,
            quote_timestamp=datetime(2026, 8, 20, 20),
        ),
        _option(
            "SKEW", 100, "Call", bid=1.0, ask=1.1, delta=0.50,
            quote_timestamp=datetime(2026, 8, 21, 20, 0),
        ),
        _option(
            "SKEW", 100, "Put", bid=1.0, ask=1.1, delta=-0.50,
            quote_timestamp=datetime(2026, 8, 21, 20, 2),
        ),
    ]
    parquet = (
        tmp_path
        / "parquet"
        / "options_chain"
        / "year=2026"
        / "month=08"
        / "2026-08-21.parquet"
    )
    parquet.parent.mkdir(parents=True)
    pd.DataFrame(options).to_parquet(parquet, index=False)

    conn = duckdb.connect()
    setup_views(conn, tmp_path)

    selected = conn.execute(
        """
        SELECT act_symbol, atm_strike, call_bid, put_bid, quote_quality_status
        FROM v_straddle_features
        ORDER BY act_symbol
        """
    ).fetchall()
    assert selected == [("GOOD", 100.0, 2.0, 1.8, "passed")]

    rejected = dict(
        conn.execute(
            """
            SELECT act_symbol, rejection_reason
            FROM v_option_quote_quarantine
            WHERE act_symbol IN (
                'CROSS', 'ORPHAN', 'NODELTA', 'PARTIALILLIQ', 'NEGATIVE'
            )
            ORDER BY act_symbol, rejection_reason
            """
        ).fetchall()
    )
    assert rejected["CROSS"] == "crossed_market"
    assert rejected["ORPHAN"] == "missing_same_strike_opposite_leg"
    assert rejected["NODELTA"] == "invalid_delta"
    assert rejected["PARTIALILLIQ"] == "illiquid_contract"
    assert rejected["NEGATIVE"] == "invalid_liquidity_evidence"

    rejected_pairs = dict(
        conn.execute(
            """
            SELECT act_symbol, pair_rejection_reason
            FROM v_straddle_quote_quarantine
            WHERE act_symbol IN ('FAR', 'SKEW')
            ORDER BY act_symbol
            """
        ).fetchall()
    )
    assert rejected_pairs == {
        "FAR": "not_atm_by_delta",
        "SKEW": "quote_timestamp_skew",
    }

    lineage = conn.execute(
        """
        SELECT quote_timestamp_precision, market_data_mode,
               call_volume, call_open_interest, liquidity_tier_method
        FROM v_straddle_features
        """
    ).fetchone()
    assert lineage == ("date", "end_of_day", None, None, "quote_spread_proxy")
