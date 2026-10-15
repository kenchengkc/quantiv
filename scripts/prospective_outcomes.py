"""Collect verified session outcomes independently of model training features."""

from __future__ import annotations

from datetime import date
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

from ml.causal_features import canonical_earnings_events, extract_reaction_labels
from ml.model_protocol import TARGET_PROTOCOL_CAUSAL


def extract_prospective_labels(
    conn: duckdb.DuckDBPyConnection,
    ledger: pd.DataFrame,
    *,
    as_of_date: date,
) -> pd.DataFrame:
    """Label ledger event identities with current, action-adjusted close evidence.

    An event need not have an options chain or a usable training feature row.
    Revised dates and conflicting/unknown timings cannot establish an outcome
    for an earlier prediction. The shared label builder retains exact-session,
    maturity and corporate-action coverage checks.
    """
    columns = [
        "__symbol",
        "__earnings_date",
        "target",
        "__label_available_at",
        "__target_protocol",
    ]
    if ledger.empty:
        return pd.DataFrame(columns=columns)
    events = ledger[["act_symbol", "earnings_date", "timing"]].copy()
    events["earnings_date"] = pd.to_datetime(events["earnings_date"])
    events["timing"] = events["timing"].fillna("").astype(str).str.strip().str.lower()
    known = events.loc[events["timing"].isin(["bmo", "amc"])].drop_duplicates()
    conflicting = known.loc[
        known.duplicated(["act_symbol", "earnings_date"], keep=False),
        ["act_symbol", "earnings_date"],
    ].drop_duplicates()
    events = events.merge(
        conflicting.assign(_conflicting=True),
        how="left",
        on=["act_symbol", "earnings_date"],
    )
    observed = canonical_earnings_events(
        events.loc[events._conflicting.isna()].drop(columns="_conflicting")
    )
    current = canonical_earnings_events(
        conn.execute(
            "SELECT act_symbol, CAST(date AS DATE) earnings_date, timing FROM v_earnings"
        ).fetchdf()
    )
    events = observed.merge(
        current, on=["act_symbol", "earnings_date", "timing"], how="inner"
    )
    labels = extract_reaction_labels(conn, as_of_date=as_of_date, events=events)
    labels = labels.rename(
        columns={
            "act_symbol": "__symbol",
            "earnings_date": "__earnings_date",
            "realized_move_pct": "target",
            "label_available_at": "__label_available_at",
            "target_protocol": "__target_protocol",
        }
    )
    labels["__target_protocol"] = TARGET_PROTOCOL_CAUSAL
    return (
        labels[columns]
        .sort_values(["__earnings_date", "__symbol"])
        .reset_index(drop=True)
    )


def read_verified_labels(data_dir: Path, ledger: pd.DataFrame):
    """Bind independent labels to the admitted price/action release."""
    from verify_historical_training_gate import verify_historical_training_gate
    from market_sessions import latest_completed_us_market_session
    from setup_duckdb_from_parquet import setup_views

    admission = verify_historical_training_gate(data_dir=data_dir)
    # Rebuild from the verified release rather than trusting mutable cached DB
    # views, which can reference older prices or a different coverage receipt.
    with duckdb.connect() as conn:
        setup_views(conn, data_dir)
        labels = extract_prospective_labels(
            conn,
            ledger,
            as_of_date=latest_completed_us_market_session(datetime.now(timezone.utc)),
        )
    return labels, {
        "source": "verified_session_outcomes",
        "rows": len(labels),
        "release_id": admission["release_id"],
        "corporate_actions": admission["corporate_actions"],
        "calendar_source": admission["calendar_source"],
    }
