import pandas as pd

from scripts.daily_score import get_upcoming_features


class _CapturingConnection:
    def __init__(self):
        self.sql = ""

    def execute(self, sql: str):
        self.sql = sql
        return self

    def fetchall(self):
        return []

    def fetchdf(self) -> pd.DataFrame:
        return pd.DataFrame()


def test_upcoming_feature_spine_does_not_require_strict_straddle():
    connection = _CapturingConnection()

    get_upcoming_features(connection, 21)

    assert "snapshot_spine AS" in connection.sql
    assert "LEFT JOIN v_straddle_features sf" in connection.sql
    assert "WHERE sf.atm_iv > 0" not in connection.sql
    assert (
        "COALESCE(rv.close, sp.snapshot_close, sf.atm_strike) AS spot_price"
        in connection.sql
    )
