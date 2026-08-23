"""Corporate-action normalization shared by training and daily scoring."""

from __future__ import annotations

import duckdb


def ensure_corporate_action_views(conn: duckdb.DuckDBPyConnection) -> None:
    """Install typed empty controls for older/local databases.

    Production databases receive these views from
    ``scripts/setup_duckdb_from_parquet.py``. The empty views keep local
    feature extraction backward compatible while the reconciliation gate still
    rejects publication when the signed ingestion receipt is absent.
    """
    names = {
        row[0]
        for row in conn.execute(
            "SELECT table_name FROM information_schema.tables"
        ).fetchall()
    }
    if "v_splits" not in names:
        conn.execute(
            """
            CREATE OR REPLACE TEMP VIEW v_splits AS
            SELECT NULL::VARCHAR AS act_symbol, NULL::DATE AS ex_date,
                   NULL::DOUBLE AS to_factor, NULL::DOUBLE AS for_factor
            WHERE FALSE
            """
        )
    if "v_dividends" not in names:
        conn.execute(
            """
            CREATE OR REPLACE TEMP VIEW v_dividends AS
            SELECT NULL::VARCHAR AS act_symbol, NULL::DATE AS ex_date,
                   NULL::DOUBLE AS amount
            WHERE FALSE
            """
        )


def adjusted_post_price_sql(
    *,
    symbol: str,
    pre_date: str,
    post_date: str,
    post_price: str,
) -> str:
    """Return SQL that makes a post-event price comparable to the pre-event price.

    Split factors convert post-action shares back to the pre-event share basis.
    Cash dividends are added back on that same basis so an ex-dividend price
    drop is not mislabeled as an earnings shock.
    """
    split_factor = f"""
        COALESCE((
            SELECT EXP(SUM(LN(s.to_factor / s.for_factor)))
            FROM v_splits s
            WHERE s.act_symbol = {symbol}
              AND s.ex_date > {pre_date}
              AND s.ex_date <= {post_date}
        ), 1.0)
    """
    dividend_cash = f"""
        COALESCE((
            SELECT SUM(
                d.amount * COALESCE((
                    SELECT EXP(SUM(LN(sd.to_factor / sd.for_factor)))
                    FROM v_splits sd
                    WHERE sd.act_symbol = d.act_symbol
                      AND sd.ex_date > {pre_date}
                      AND sd.ex_date <= d.ex_date
                ), 1.0)
            )
            FROM v_dividends d
            WHERE d.act_symbol = {symbol}
              AND d.ex_date > {pre_date}
              AND d.ex_date <= {post_date}
        ), 0.0)
    """
    return f"(({post_price}) * ({split_factor}) + ({dividend_cash}))"
