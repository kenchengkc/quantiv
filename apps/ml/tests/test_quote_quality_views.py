from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
import sys

import duckdb
import pandas as pd
import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

from setup_duckdb_from_parquet import setup_views  # noqa: E402
from build_data_reconciliation import _event_coverage  # noqa: E402


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

    assert conn.execute("SELECT COUNT(*) FROM v_corporate_action_coverage").fetchone()[0] == 0

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


@pytest.mark.parametrize(
    ("covered", "total", "status"),
    [(13, 20, "passed"), (12, 20, "failed"),
     (93, 143, "passed"), (92, 143, "failed")],
)
def test_event_coverage_admission_preserves_rejected_pairs(
    tmp_path: Path, covered: int, total: int, status: str,
) -> None:
    snapshot = date.today()
    earnings_date = snapshot + timedelta(days=3)
    expiration = snapshot + timedelta(days=7)
    options = []
    for index in range(total):
        for side, delta in [("Call", 0.5), ("Put", -0.5)]:
            option = _option(
                f"TEST{index:03d}", 100, side,
                bid=1.0, ask=1.1 if index < covered else 4.0, delta=delta,
            )
            option.update(date=snapshot, expiration=expiration)
            options.append(option)
    partition = (
        tmp_path / "parquet/options_chain" / f"year={snapshot.year}"
        / f"month={snapshot.month:02d}" / f"{snapshot}.parquet"
    )
    partition.parent.mkdir(parents=True)
    frame = pd.DataFrame(options)
    frame["quote_timestamp"] = pd.to_datetime(frame["quote_timestamp"])
    frame.to_parquet(partition, index=False)
    pd.DataFrame([
        {"act_symbol": f"TEST{index:03d}", "date": earnings_date, "timing": "bmo"}
        for index in range(total)
    ]).to_parquet(tmp_path / "earnings_calendar.parquet", index=False)

    with duckdb.connect() as conn:
        setup_views(conn, tmp_path)
        coverage = _event_coverage(conn, days_ahead=21)
        selected = conn.execute(
            "SELECT act_symbol FROM v_straddle_features ORDER BY act_symbol"
        ).fetchall()
        rejected = conn.execute(
            "SELECT COUNT(*) FROM v_straddle_quote_quarantine"
        ).fetchone()[0]

    assert coverage["status"] == status
    assert coverage["expected_events"] == total
    assert coverage["covered_events"] == covered
    assert selected == [(f"TEST{index:03d}",) for index in range(covered)]
    assert rejected == total - covered
    assert coverage["missing_reason_counts"] == [
        {"reason": "noncommercial_leg_quotes", "events": total - covered}
    ]
    # Aggregate admission must not silently lower the separate horizon warning.
    assert coverage["horizon_coverage"]["status"] == "failed"
