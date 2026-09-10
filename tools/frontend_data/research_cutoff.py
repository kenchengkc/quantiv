"""Cutoff controls for historical research inputs."""

from __future__ import annotations

from datetime import date

import duckdb


def restrict_ohlcv_to_as_of(
    conn: duckdb.DuckDBPyConnection, as_of_date: date
) -> dict[str, object]:
    """Make future OHLCV physically inaccessible to the research builder.

    ``tools.build_earnings_events.create_duckdb_views`` always installs
    ``v_ohlcv`` as a view. Copy the eligible prefix to an in-memory temporary
    table, then replace the source view with that sealed prefix. This is stronger
    than merely checking event dates: lateral post-event selection and corporate
    action normalization cannot accidentally consume a close after the declared
    research as-of date.
    """
    before = conn.execute(
        "SELECT COUNT(*), MIN(date), MAX(date) FROM v_ohlcv"
    ).fetchone()
    conn.execute(
        """
        CREATE OR REPLACE TEMP TABLE _research_ohlcv_as_of AS
        SELECT * FROM v_ohlcv WHERE date <= ?
        """,
        [as_of_date],
    )
    conn.execute("DROP VIEW v_ohlcv")
    conn.execute(
        "CREATE OR REPLACE TEMP VIEW v_ohlcv AS SELECT * FROM _research_ohlcv_as_of"
    )
    after = conn.execute(
        "SELECT COUNT(*), MIN(date), MAX(date) FROM v_ohlcv"
    ).fetchone()
    if after[2] is not None and after[2] > as_of_date:
        raise RuntimeError("research OHLCV cutoff failed closed")
    return {
        "as_of_date": as_of_date.isoformat(),
        "source_rows": int(before[0] or 0),
        "retained_rows": int(after[0] or 0),
        "dropped_post_cutoff_rows": int((before[0] or 0) - (after[0] or 0)),
        "source_min": before[1].isoformat() if before[1] is not None else None,
        "source_max": before[2].isoformat() if before[2] is not None else None,
        "retained_min": after[1].isoformat() if after[1] is not None else None,
        "retained_max": after[2].isoformat() if after[2] is not None else None,
    }
