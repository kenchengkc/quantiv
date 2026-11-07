"""Price and option histories must respect the MSGS spin-off boundary."""

from datetime import date

import duckdb
import pandas as pd
import pytest

from scripts.setup_duckdb_from_parquet import setup_views
from build_earnings_events import create_duckdb_views
from ml.causal_features import build_causal_features, extract_reaction_labels
from frontend_data.realized_moves import _compute_realized_from_closes, reaction_label_lookup


@pytest.fixture
def market_data(tmp_path):
    # Include provider backfills under MSGK and stale MSGS rows after the
    # distribution. Neither is evidence of a comparable price history.
    prices = [
        (symbol, day, close)
        for symbol in ("MSGS", "MSGK", "MSGR", "MSGE")
        for day, close in (
            (date(2026, 10, 23), 100.0),
            (date(2026, 10, 26), 105.0),
            (date(2026, 10, 27), 60.0),
            (date(2026, 10, 28), 66.0),
        )
    ]
    ohlcv = tmp_path / "parquet/ohlcv/year=2026/month=10/prices.parquet"
    ohlcv.parent.mkdir(parents=True)
    pd.DataFrame([
        dict(act_symbol=symbol, date=day, open=close, high=close, low=close,
             close=close, volume=100)
        for symbol, day, close in prices
    ]).to_parquet(ohlcv, index=False)
    options = tmp_path / "parquet/options_chain/year=2026/month=10/options.parquet"
    options.parent.mkdir(parents=True)
    pd.DataFrame([
        dict(act_symbol=symbol, date=day, expiration=date(2026, 10, 30),
             strike=close, call_put=side, bid=2.0, ask=2.1, vol=.4,
             delta=delta, gamma=.1, theta=-.1, vega=.1, rho=.01)
        for symbol, day, close in prices
        for side, delta in (("Call", .5), ("Put", -.5))
    ]).to_parquet(options, index=False)
    volhist = tmp_path / "parquet/volatility_history/year=2026/month=10/history.parquet"
    volhist.parent.mkdir(parents=True)
    pd.DataFrame([
        dict(act_symbol=symbol, date=day, hv_current=.3, hv_week_ago=.2,
             hv_month_ago=.2, hv_year_high=.5, hv_year_low=.1,
             iv_current=.4, iv_week_ago=.3, iv_month_ago=.2,
             iv_year_high=.6, iv_year_low=.1)
        for symbol in ("MSGS", "MSGK", "MSGR", "MSGE")
        for day in (date(2026, 10, 26), date(2026, 10, 27), date(2026, 11, 3),
                    date(2026, 11, 27), date(2027, 10, 27))
    ]).to_parquet(volhist, index=False)
    pd.DataFrame([
        ("MSGS", "2026-10-26", "bmo"),
        ("MSGS", "2026-10-26", "amc"),
        ("MSGK", "2026-10-27", "bmo"),
        ("MSGK", "2026-10-28", "bmo"),
    ], columns=["act_symbol", "date", "timing"]).to_csv(
        tmp_path / "earnings_calendar.csv", index=False
    )
    return tmp_path


def _build_views(builder, data_dir):
    conn = duckdb.connect()
    builder(conn, data_dir)
    return conn


@pytest.mark.parametrize("builder,option_view,symbol_column,date_column", [
    (setup_views, "v_options_raw", "act_symbol", "date"),
    (create_duckdb_views, "v_options_chain", "ticker", "as_of_date"),
])
def test_market_views_preserve_only_each_securitys_valid_dates(
    market_data, builder, option_view, symbol_column, date_column
):
    with _build_views(builder, market_data) as conn:
        prices = conn.execute("SELECT act_symbol,date FROM v_ohlcv ORDER BY 1,2").fetchall()
        options = conn.execute(
            f"SELECT DISTINCT {symbol_column},{date_column} FROM {option_view} ORDER BY 1,2"
        ).fetchall()
        expected = [
            (symbol, day)
            for symbol in ("MSGE", "MSGK", "MSGR", "MSGS")
            for day in (date(2026, 10, 23), date(2026, 10, 26),
                        date(2026, 10, 27), date(2026, 10, 28))
            if (symbol != "MSGS" or day < date(2026, 10, 27))
            and (symbol != "MSGK" or day >= date(2026, 10, 27))
        ]
        assert prices == expected
        assert options == expected


@pytest.mark.parametrize("builder", [setup_views, create_duckdb_views])
def test_valid_earnings_reactions_survive_but_spin_off_windows_have_no_label(
    market_data, builder
):
    with _build_views(builder, market_data) as conn:
        # Isolate lifecycle protection from the separately tested receipt gate:
        # even complete action coverage cannot authorize an unadjusted spin-off.
        conn.execute("""
            CREATE OR REPLACE VIEW v_corporate_action_coverage AS
            SELECT symbol AS act_symbol, DATE '2026-10-01' AS window_start,
                   DATE '2026-10-31' AS window_end, 'test' AS receipt_id
            FROM (VALUES ('MSGS'), ('MSGK')) symbols(symbol)
        """)
        for symbol, day, timing, expected in (
            ("MSGS", date(2026, 10, 26), "bmo", .05),
            ("MSGS", date(2026, 10, 26), "amc", None),
            ("MSGK", date(2026, 10, 27), "bmo", None),
            ("MSGK", date(2026, 10, 28), "bmo", .10),
        ):
            events = pd.DataFrame([(symbol, day, timing)],
                                  columns=["act_symbol", "earnings_date", "timing"])
            labels = extract_reaction_labels(conn, as_of_date=date(2026, 10, 29), events=events)
            displayed = reaction_label_lookup(conn, [(symbol, day, timing)],
                                              as_of_date=date(2026, 10, 29))
            if expected is None:
                assert labels.empty
                assert displayed == {}
            else:
                assert labels.iloc[0].realized_move_pct == pytest.approx(expected)
                assert displayed[(symbol, day)].signed_realized_move_pct == pytest.approx(expected)


def test_ml_cannot_use_pre_spin_off_grace_snapshot_or_price_drift(market_data):
    with _build_views(setup_views, market_data) as conn:
        conn.execute("""
            CREATE OR REPLACE VIEW v_earnings AS
            SELECT 'MSGK' AS act_symbol, DATE '2026-10-28' AS date, 'bmo' AS timing;
            CREATE VIEW v_vix AS
            SELECT NULL::DATE AS date, NULL::DOUBLE AS vix_close WHERE FALSE;
            CREATE VIEW v_volhist AS
            SELECT NULL::VARCHAR AS act_symbol, NULL::DATE AS date,
                NULL::DOUBLE AS iv_current, NULL::DOUBLE AS iv_week_ago,
                NULL::DOUBLE AS iv_month_ago, NULL::DOUBLE AS iv_year_high,
                NULL::DOUBLE AS iv_year_low, NULL::DOUBLE AS hv_current,
                NULL::DOUBLE AS hv_year_high, NULL::DOUBLE AS hv_year_low WHERE FALSE
        """)
        kwargs = dict(start_date=date(2026, 10, 28), end_date=date(2026, 10, 28))
        # On October 27 the publication grace window may allow October 26,
        # but a provider's backfilled MSGK close cannot create such a forecast.
        assert build_causal_features(conn, as_of_date=date(2026, 10, 26), **kwargs).empty
        rows = build_causal_features(conn, as_of_date=date(2026, 10, 27), **kwargs)
        assert len(rows) == 1
        assert pd.Timestamp(rows.iloc[0].snapshot_date).date() == date(2026, 10, 27)
        assert rows.iloc[0].lead_days == 1
        assert pd.isna(rows.iloc[0].drift_5d)
        assert pd.isna(rows.iloc[0].cc_rv_20d)


@pytest.mark.parametrize("ticker,report,timing,expected", [
    ("MSGS", date(2026, 10, 26), "bmo", .05),
    ("MSGS", date(2026, 10, 26), "amc", None),
    ("MSGK", date(2026, 10, 27), "bmo", None),
    ("MSGK", date(2026, 10, 28), "bmo", .10),
    ("MSGE", date(2026, 10, 27), "bmo", 60 / 105 - 1),
    ("MSGR", date(2026, 10, 28), "bmo", .10),
])
def test_external_price_fallback_respects_security_boundary(ticker, report, timing, expected):
    closes = [(date(2026, 10, 23), 100.), (date(2026, 10, 26), 105.),
              (date(2026, 10, 27), 60.), (date(2026, 10, 28), 66.)]
    actual = _compute_realized_from_closes(closes, report, timing, ticker=ticker)
    assert actual is None if expected is None else actual == pytest.approx(expected)


def _assert_volatility_history_boundary(conn, view):
    rows = conn.execute(f"SELECT * FROM {view}").fetchdf()
    dates = pd.to_datetime(rows.date).dt.date
    assert not ((rows.act_symbol == "MSGS") & (dates >= date(2026, 10, 27))).any()
    assert not ((rows.act_symbol == "MSGK") & (dates < date(2026, 10, 27))).any()
    successor = rows[rows.act_symbol == "MSGK"].set_index("date")
    assert successor.iv_current.eq(.4).all()
    assert successor.filter(regex="^hv_").isna().all().all()
    for column in ("iv_week_ago", "iv_month_ago", "iv_year_high", "iv_year_low"):
        assert successor[column].isna().all()
    others = rows[rows.act_symbol.isin(["MSGS", "MSGE", "MSGR"])]
    assert others.hv_current.eq(.3).all()
    assert others.iv_year_high.eq(.6).all()


@pytest.mark.parametrize("builder,view", [
    (setup_views, "v_volhist_raw"), (create_duckdb_views, "v_volhist"),
])
def test_volatility_summaries_cannot_reintroduce_pre_spin_off_history(market_data, builder, view):
    with _build_views(builder, market_data) as conn:
        _assert_volatility_history_boundary(conn, view)


def test_training_direct_volatility_scan_also_honors_boundary(market_data, monkeypatch):
    from feature_engineering import connect_duckdb

    path = market_data / "training.duckdb"
    with duckdb.connect(str(path)) as conn:
        setup_views(conn, market_data)
    monkeypatch.setenv("DATA_DIR", str(market_data))
    monkeypatch.setenv("DUCKDB_PATH", str(path))
    with connect_duckdb() as conn:
        _assert_volatility_history_boundary(conn, "v_volhist")


def test_refresh_replaces_existing_unprotected_volatility_view(market_data):
    with duckdb.connect() as conn:
        source = market_data / 'parquet/volatility_history/year=2026/month=10/history.parquet'
        conn.execute(f"CREATE VIEW v_volhist AS SELECT * FROM read_parquet('{source}')")
        setup_views(conn, market_data)
        _assert_volatility_history_boundary(conn, 'v_volhist')


@pytest.mark.parametrize('relation', [None, 'v_options', 'v_options_raw'])
def test_display_option_fallback_cannot_bypass_cutover(market_data, monkeypatch, relation):
    from frontend_data import display_forecast

    monkeypatch.setattr(display_forecast, 'DATA_DIR', market_data)
    with duckdb.connect() as conn:
        if relation:
            source = market_data / 'parquet/options_chain/year=2026/month=10/options.parquet'
            conn.execute(f"CREATE VIEW {relation} AS SELECT * FROM read_parquet('{source}')")
        source = display_forecast._raw_options_source(conn)
        assert conn.execute(f"SELECT COUNT(*) FROM {source} WHERE act_symbol='MSGK' AND date < DATE '2026-10-27'").fetchone()[0] == 0
        assert conn.execute(f"SELECT COUNT(*) FROM {source} WHERE act_symbol='MSGS' AND date >= DATE '2026-10-27'").fetchone()[0] == 0
        assert conn.execute(f"SELECT COUNT(*) FROM {source} WHERE act_symbol='MSGE'").fetchone()[0] == 8
