"""Apply authenticated corporate-action coverage to current feature snapshots."""

from __future__ import annotations

import duckdb
import pandas as pd
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


def completed_market_session(now: datetime) -> date:
    candidates = (Path(__file__).resolve().parents[3] / "config/market_sessions.json",
                  Path("/app/config/market_sessions.json"))
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if path is None or now.tzinfo is None:
        raise ValueError("current market-session contract is unavailable")
    contract = json.loads(path.read_text())
    if (contract.get("schema") != "quantiv.market-sessions.v1"
            or now.date().isoformat() > contract["source"]["verified_through"]):
        raise ValueError("current market-session contract is unavailable")
    eastern = now.astimezone(ZoneInfo("America/New_York"))
    candidate = eastern.date()
    close = contract.get("early_closes", {}).get(candidate.isoformat(), "16:00")
    hour, minute = (int(part) for part in close.split(":"))
    if eastern.hour * 60 + eastern.minute < hour * 60 + minute:
        candidate -= timedelta(days=1)
    holidays = set(contract["holidays"])
    while candidate.weekday() >= 5 or candidate.isoformat() in holidays:
        candidate -= timedelta(days=1)
    return candidate


def filter_source_covered_snapshots(
    conn: duckdb.DuckDBPyConnection, frame: pd.DataFrame, *, require_coverage: bool = False,
) -> pd.DataFrame:
    if frame.empty:
        return frame
    tables = {row[0] for row in conn.execute(
        "SELECT table_name FROM information_schema.tables"
    ).fetchall()}
    if "v_corporate_action_coverage" not in tables:
        return frame.iloc[:0].copy() if require_coverage else frame
    conn.register("_source_covered_snapshots", frame)
    try:
        return conn.execute("""
            SELECT snapshots.* FROM _source_covered_snapshots snapshots
            WHERE EXISTS (
                SELECT 1 FROM v_corporate_action_coverage coverage
                WHERE coverage.act_symbol = snapshots.act_symbol
                  AND snapshots.snapshot_date BETWEEN coverage.window_start AND coverage.window_end
            )
        """).fetchdf()
    finally:
        conn.unregister("_source_covered_snapshots")
