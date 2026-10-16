"""A build-local cache must preserve the complete analytical input rows."""
import duckdb
import pandas as pd

from build_earnings_events import materialize_frontend_queries


def test_cache_preserves_duplicate_null_and_future_rows():
    conn = duckdb.connect()
    conn.execute("CREATE TABLE prices(act_symbol VARCHAR, date DATE, close DOUBLE)")
    conn.execute("INSERT INTO prices VALUES ('A', '2026-10-15', 10), ('A', '2026-10-15', 10), ('B', '2026-12-01', NULL)")
    conn.execute("CREATE VIEW v_ohlcv AS SELECT * FROM prices")
    conn.execute("CREATE TABLE pairs(ticker VARCHAR, as_of_date DATE, expiry_date DATE, straddle_pct DOUBLE)")
    conn.execute("INSERT INTO pairs VALUES ('A','2026-10-15','2026-10-16',.04), ('B','2026-12-01','2026-12-18',NULL)")
    conn.execute("CREATE VIEW v_eligible_straddles AS SELECT * FROM pairs")
    before = {name: conn.execute(f'SELECT * FROM {name} ORDER BY ALL').fetchdf()
              for name in ('v_ohlcv', 'v_eligible_straddles')}
    materialize_frontend_queries(conn)
    # Queries can reuse these exact rows without revisiting the source scan.
    conn.execute('DELETE FROM prices')
    conn.execute('DELETE FROM pairs')
    for name, expected in before.items():
        actual = conn.execute(f'SELECT * FROM {name} ORDER BY ALL').fetchdf()
        pd.testing.assert_frame_equal(actual, expected)
    conn.close()
