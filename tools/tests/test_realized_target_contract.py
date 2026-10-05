from datetime import date
import hashlib

import duckdb
import pandas as pd
import pytest

from frontend_data.realized_moves import (
    _compute_realized_from_closes,
    enrich_realized_moves_from_ohlcv,
    realized_move_from_ohlcv,
)
from ml.causal_features import extract_reaction_labels
from ml.model_protocol import TARGET_PROTOCOL_CAUSAL


def production_reaction_database(tmp_path, *, receipt=True):
    """Use the frontend's actual lake-view builder and content-addressed actions."""
    from build_earnings_events import create_duckdb_views
    from scripts import sync_dolthub
    from scripts.tests.historical_admission_fixtures import (
        publish_action_receipt,
        write_action_receipt,
    )
    import pyarrow as pa
    import pyarrow.parquet as pq

    report = date(2026, 9, 15)
    action_receipt = write_action_receipt(
        tmp_path, source_date=str(report), splits=[["TEST", report, 2.0, 1.0]]
    )
    dividend = pd.DataFrame([["TEST", report, 1.0]], columns=["act_symbol", "ex_date", "amount"])
    digest = sync_dolthub._action_content_digest(dividend, list(dividend))
    path = tmp_path / f"parquet/corporate_actions/dividends/{digest}.parquet"
    pq.write_table(pa.Table.from_pandas(dividend, schema=sync_dolthub.DIVIDEND_SCHEMA,
                                       preserve_index=False), path)
    action_receipt["datasets"]["dividends"].update(
        rows=1, partition=path.relative_to(tmp_path).as_posix(), content_sha256=digest,
        partition_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        batches=[{"symbols": 1, "rows": 1, "pages": 1, "completion": "short_page"}],
    )
    publish_action_receipt(tmp_path, action_receipt)
    options = tmp_path / "parquet/options_chain/year=2026/month=09/2026-09-15.parquet"
    pd.DataFrame([{
        "date": report, "act_symbol": "TEST", "expiration": date(2026, 9, 18),
        "strike": 100.0, "call_put": side, "bid": 4.0, "ask": 4.2, "vol": .4,
        "delta": delta, "gamma": .1, "theta": -.1, "vega": .1,
    } for side, delta in [("Call", .5), ("Put", -.5)]]).to_parquet(options, index=False)
    ohlcv = tmp_path / "parquet/ohlcv/year=2026/month=09/history.parquet"
    ohlcv.parent.mkdir(parents=True)
    pd.DataFrame([{
        "date": day, "act_symbol": symbol, "open": close, "high": close,
        "low": close, "close": close, "volume": 100,
    } for symbol in ["TEST", "OTHER"] for day, close in [
        (date(2026, 9, 14), 100.), (report, 52.), (date(2026, 9, 16), 54.),
    ]]).to_parquet(ohlcv, index=False)
    if not receipt:
        (tmp_path / "control/ingestion/corporate_actions/latest.json").unlink()
    conn = duckdb.connect()
    create_duckdb_views(conn, tmp_path)
    return conn


def test_production_frontend_views_apply_verified_actions_and_coverage(tmp_path):
    conn = production_reaction_database(tmp_path)
    assert realized_move_from_ohlcv(conn, "TEST", date(2026, 9, 15), "bmo") == pytest.approx(.06)
    assert realized_move_from_ohlcv(conn, "OTHER", date(2026, 9, 15), "bmo") is None
    # The receipt ends on the 15th, so a following-session AMC label is uncovered.
    assert realized_move_from_ohlcv(conn, "TEST", date(2026, 9, 15), "amc") is None


def test_production_frontend_missing_receipt_withholds_raw_reactions(tmp_path):
    conn = production_reaction_database(tmp_path, receipt=False)
    assert realized_move_from_ohlcv(conn, "TEST", date(2026, 9, 15), "bmo") is None


def test_product_canonical_reactions_withhold_future_inferred_timing(tmp_path):
    from build_earnings_events import build_earnings_events_table
    from frontend_data.payloads import build_symbol_detail, screener_extras

    conn = production_reaction_database(tmp_path)
    csv = tmp_path / "earnings_calendar.csv"
    pd.DataFrame([
        ("TEST", "2026-09-15", "unknown"),
        ("TEST", "2026-12-15", "BMO"),
        ("TEST", "2027-03-15", "BMO"),
        ("TEST", "2027-06-15", "BMO"),
    ], columns=["act_symbol", "date", "timing"]).to_csv(csv, index=False)
    build_earnings_events_table(conn, csv)
    inferred = conn.execute("SELECT timing_source FROM earnings_events WHERE earnings_dt=DATE '2026-09-15'").fetchone()[0]
    assert inferred.startswith("inferred_")
    assert realized_move_from_ohlcv(conn, "TEST", date(2026, 9, 15), "bmo") is None
    detail = build_symbol_detail(conn, "TEST", date(2026, 9, 15), None)
    report = next(row for row in detail["earnings_history"] if row["date"] == "2026-09-15")
    assert report["actual"] is None
    assert screener_extras(conn, "TEST", date(2026, 10, 15), date(2026, 9, 15))["hist_move_med_4q"] is None
    events = [{"ticker": "TEST", "earnings_date": "2026-09-15", "timing": "bmo", "realized_move_pct": -.48}]
    enrich_realized_moves_from_ohlcv(conn, events)
    assert events[0]["realized_move_pct"] is None


def reaction_database(timing, report, prices):
    conn = duckdb.connect()
    frame = pd.DataFrame(prices, columns=["date", "close"])
    frame["date"] = pd.to_datetime(frame["date"])
    conn.register("prices", frame)
    conn.execute(
        "CREATE VIEW v_ohlcv AS SELECT 'A' act_symbol,CAST(date AS DATE) date,close FROM prices"
    )
    conn.execute(
        "CREATE TABLE v_earnings (act_symbol VARCHAR,date DATE,timing VARCHAR)"
    )
    conn.execute("INSERT INTO v_earnings VALUES ('A',?,?)", [report, timing])
    return conn


def test_display_and_training_share_corporate_action_normalized_target():
    report = date(2026, 9, 15)
    conn = reaction_database(
        "bmo", report, [(date(2026, 9, 14), 100.0), (report, 52.0)]
    )
    conn.execute(
        "CREATE TABLE v_splits AS SELECT 'A' act_symbol,DATE '2026-09-15' ex_date,2.0 to_factor,1.0 for_factor"
    )
    conn.execute(
        "CREATE TABLE v_dividends AS SELECT 'A' act_symbol,DATE '2026-09-15' ex_date,1.0 amount"
    )
    training = extract_reaction_labels(conn, as_of_date=report).iloc[0]
    displayed = realized_move_from_ohlcv(conn, "A", report, "bmo")
    assert displayed == pytest.approx(0.06)
    assert abs(displayed) == pytest.approx(training["realized_move_pct"])
    events = [
        {
            "ticker": "A",
            "earnings_date": str(report),
            "timing": "bmo",
            "realized_move_pct": -0.48,
        }
    ]
    assert enrich_realized_moves_from_ohlcv(conn, events) == 1
    assert events[0]["realized_move_pct"] == pytest.approx(0.06)
    assert events[0]["realized_target_protocol"] == TARGET_PROTOCOL_CAUSAL
    assert events[0]["realized_label_source"] == "ohlcv_session_close"


@pytest.mark.parametrize(
    "timing,report,prices,expected",
    [
        (
            "amc",
            date(2026, 1, 16),
            [(date(2026, 1, 16), 100.0), (date(2026, 1, 20), 95.0)],
            -0.05,
        ),
        (
            "bmo",
            date(2026, 1, 20),
            [(date(2026, 1, 16), 100.0), (date(2026, 1, 20), 105.0)],
            0.05,
        ),
        (
            "amc",
            date(2026, 1, 16),
            [(date(2026, 1, 16), 100.0), (date(2026, 1, 21), 120.0)],
            None,
        ),
        (
            "bmo",
            date(2026, 1, 20),
            [(date(2026, 1, 15), 100.0), (date(2026, 1, 20), 105.0)],
            None,
        ),
        (
            "unknown",
            date(2026, 1, 20),
            [
                (date(2026, 1, 16), 100.0),
                (date(2026, 1, 20), 105.0),
                (date(2026, 1, 21), 110.0),
            ],
            None,
        ),
    ],
)
def test_display_and_close_list_use_exact_known_reaction_sessions(
    timing, report, prices, expected
):
    conn = reaction_database(timing, report, prices)
    displayed = realized_move_from_ohlcv(conn, "A", report, timing)
    external = _compute_realized_from_closes(prices, report, timing)
    labels = extract_reaction_labels(conn, as_of_date=date(2026, 2, 1))
    if expected is None:
        assert displayed is None
        assert external is None
        assert labels.empty
    else:
        assert displayed == pytest.approx(expected)
        assert external == pytest.approx(expected)
        assert labels.iloc[0]["realized_move_pct"] == pytest.approx(abs(expected))


def test_unknown_timing_cannot_preserve_old_unverified_product_outcome():
    report = date(2026, 1, 20)
    conn = reaction_database(
        "unknown", report, [(date(2026, 1, 16), 100.0), (date(2026, 1, 21), 110.0)]
    )
    events = [
        {
            "ticker": "A",
            "earnings_date": str(report),
            "timing": "unknown",
            "realized_move_pct": 0.1,
        }
    ]
    assert enrich_realized_moves_from_ohlcv(conn, events) == 1
    assert events[0]["realized_move_pct"] is None


def add_product_views(conn):
    conn.execute("""CREATE VIEW earnings_events AS SELECT act_symbol ticker,date earnings_dt,timing,
        2026 fiscal_year,'Q3' fiscal_q, NULL::DOUBLE eps_actual,NULL::DOUBLE eps_estimate,
        NULL::DOUBLE revenue_actual,NULL::DOUBLE revenue_estimate,'calendar' AS source FROM v_earnings""")
    conn.execute("""CREATE VIEW v_eligible_straddles AS SELECT NULL::VARCHAR ticker,NULL::DATE as_of_date,
        NULL::DATE expiry_date,NULL::INTEGER dte,NULL::DOUBLE atm_strike,NULL::DOUBLE atm_iv,
        NULL::DOUBLE call_iv,NULL::DOUBLE put_iv,NULL::DOUBLE straddle_mid,NULL::DOUBLE straddle_pct,
        NULL::DOUBLE call_delta,NULL::DOUBLE call_gamma,NULL::DOUBLE call_vega,NULL::DOUBLE call_theta,
        NULL::VARCHAR quote_quality_status WHERE FALSE""")


def test_symbol_history_and_published_median_use_normalized_shared_reactions(
    monkeypatch,
):
    from frontend_data.payloads import build_symbol_detail, screener_extras

    report = date(2026, 9, 15)
    conn = reaction_database(
        "bmo", report, [(date(2026, 9, 14), 100.0), (report, 52.0)]
    )
    conn.execute(
        "CREATE TABLE v_splits AS SELECT 'A' act_symbol,DATE '2026-09-15' ex_date,2.0 to_factor,1.0 for_factor"
    )
    conn.execute(
        "CREATE TABLE v_dividends AS SELECT 'A' act_symbol,DATE '2026-09-15' ex_date,1.0 amount"
    )
    add_product_views(conn)
    detail = build_symbol_detail(conn, "A", report, None)
    assert detail["earnings_history"][0]["actual"] == pytest.approx(0.06)
    assert (
        detail["earnings_history"][0]["realized_target_protocol"]
        == TARGET_PROTOCOL_CAUSAL
    )
    extras = screener_extras(conn, "A", date(2026, 10, 20), report)
    assert extras["hist_move_avg_4q"] == pytest.approx(0.06)
    assert extras["hist_move_med_4q"] == pytest.approx(0.06)
    before_maturity = screener_extras(conn, "A", date(2026, 10, 20), date(2026, 9, 14))
    assert before_maturity["hist_move_med_4q"] is None
    from frontend_data import payloads

    monkeypatch.setattr(payloads, "compute_em_math", lambda *args: None)
    events = payloads.build_week_events(
        conn,
        report,
        report,
        report,
        {},
        require_ml=False,
        published={("A", str(report)): "bmo"},
    )
    assert events[0]["realized_move_pct"] == pytest.approx(0.06)
    assert events[0]["realized_target_protocol"] == TARGET_PROTOCOL_CAUSAL


@pytest.mark.parametrize(
    "timing,prices",
    [
        ("amc", [(date(2026, 1, 16), 100.0), (date(2026, 1, 21), 120.0)]),
        (
            "unknown",
            [
                (date(2026, 1, 15), 100.0),
                (date(2026, 1, 16), 120.0),
                (date(2026, 1, 20), 150.0),
            ],
        ),
    ],
)
def test_symbol_history_and_weekly_product_withhold_unverified_reactions(
    timing, prices, monkeypatch
):
    from frontend_data import payloads

    report = date(2026, 1, 16)
    conn = reaction_database(timing, report, prices)
    add_product_views(conn)
    detail = payloads.build_symbol_detail(conn, "A", date(2026, 1, 21), None)
    assert detail["earnings_history"][0]["actual"] is None
    monkeypatch.setattr(payloads, "compute_em_math", lambda *args: None)
    events = payloads.build_week_events(
        conn,
        date(2026, 1, 21),
        report,
        report,
        {},
        require_ml=False,
        published={("A", str(report)): timing},
    )
    assert events[0]["realized_move_pct"] is None


def test_weekly_product_collapses_known_and_unknown_duplicates_consistently(
    monkeypatch,
):
    from frontend_data import payloads

    report = date(2026, 9, 15)
    conn = reaction_database(
        "unknown", report, [(date(2026, 9, 14), 100.0), (report, 110.0)]
    )
    conn.execute("INSERT INTO v_earnings VALUES ('A',?, 'bmo')", [report])
    add_product_views(conn)
    monkeypatch.setattr(payloads, "compute_em_math", lambda *args: None)
    events = payloads.build_week_events(
        conn,
        report,
        report,
        report,
        {},
        require_ml=False,
        published={("A", str(report)): "bmo"},
    )
    assert len(events) == 1
    assert events[0]["timing"] == "bmo"
    assert events[0]["realized_move_pct"] == pytest.approx(0.1)


def test_unverified_external_reaction_stays_out_of_canonical_product_fields(
    monkeypatch, tmp_path
):
    from frontend_data import realized_moves
    from twelvedata_basic import TwelveDataConfig, TwelveDataFetchResult

    config = TwelveDataConfig(
        api_key="fixture",
        daily_credit_limit=1,
        batch_size=1,
        batch_delay_sec=0,
        ledger_path=tmp_path / "credits.json",
        realized_fallback_enabled=True,
    )
    monkeypatch.setattr(realized_moves, "load_twelvedata_config", lambda *args: config)
    # Split-adjusted external closes omit the dividend needed by the v2 target.
    result = TwelveDataFetchResult(
        closes={"A": [(date(2026, 9, 14), 50.0), (date(2026, 9, 15), 52.0)]}
    )
    monkeypatch.setattr(
        realized_moves, "fetch_daily_closes", lambda *args, **kwargs: result
    )
    events = [
        {
            "ticker": "A",
            "earnings_date": "2026-09-15",
            "timing": "bmo",
            "realized_move_pct": None,
            "realized_move_abs": 0.04,
        }
    ]
    assert realized_moves.enrich_realized_moves_from_twelvedata(events) == 1
    assert events[0]["realized_move_pct"] is None
    assert events[0]["realized_move_abs"] is None
    assert events[0]["realized_external_fallback_pct"] == pytest.approx(0.04)
    assert (
        events[0]["realized_external_fallback_source"]
        == "external_split_adjusted_close_unverified"
    )
    assert events[0]["realized_target_protocol"] is None
