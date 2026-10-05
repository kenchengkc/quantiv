from datetime import date, datetime
import json
from zoneinfo import ZoneInfo

import duckdb
import numpy as np
import pandas as pd
import pytest

from ml.model_protocol import FEATURE_PROTOCOL_CAUSAL, FEATURE_PROTOCOL_LEGACY
from scripts.daily_score import get_upcoming_features, save_forecasts, score


def market_connection(events, prices):
    conn = duckdb.connect()
    earnings = pd.DataFrame(events, columns=["act_symbol", "date", "timing"])
    earnings["date"] = pd.to_datetime(earnings["date"])
    px = pd.DataFrame(prices, columns=["act_symbol", "date", "close"])
    px["date"] = pd.to_datetime(px["date"])
    conn.register("earnings_input", earnings)
    conn.register("prices_input", px)
    conn.execute(
        "CREATE VIEW v_earnings AS SELECT act_symbol,CAST(date AS DATE) date,timing FROM earnings_input"
    )
    conn.execute(
        "CREATE VIEW v_ohlcv AS SELECT act_symbol,CAST(date AS DATE) date,close FROM prices_input"
    )
    return conn


def test_bmo_and_amc_reactions_use_distinct_report_session_prices():
    from ml.causal_features import extract_reaction_labels

    conn = market_connection(
        [
            ("B", "2026-09-15", " BMO "),
            ("A", "2026-09-15", "AMC"),
            ("U", "2026-09-15", "unknown"),
        ],
        [
            (s, d, p)
            for s in ("B", "A", "U")
            for d, p in [
                ("2026-09-14", 100.0),
                ("2026-09-15", 110.0),
                ("2026-09-16", 121.0),
            ]
        ],
    )
    labels = extract_reaction_labels(conn, as_of_date=date(2026, 9, 16)).set_index(
        "act_symbol"
    )
    assert set(labels.index) == {"A", "B"}
    assert labels.loc["B", "realized_move_pct"] == pytest.approx(0.1)
    assert labels.loc["A", "realized_move_pct"] == pytest.approx(0.1)
    assert str(labels.loc["B", "pre_price_date"])[:10] == "2026-09-14"
    assert str(labels.loc["B", "post_price_date"])[:10] == "2026-09-15"
    assert str(labels.loc["A", "pre_price_date"])[:10] == "2026-09-15"
    assert str(labels.loc["A", "label_available_at"])[:10] == "2026-09-16"


def test_targets_require_exact_session_prices_and_mature_labels():
    from ml.causal_features import extract_reaction_labels

    conn = market_connection(
        [("A", "2026-01-16", "amc"), ("MISSING", "2026-01-16", "amc")],
        [
            ("A", "2026-01-16", 100.0),
            ("A", "2026-01-20", 104.0),
            ("MISSING", "2026-01-16", 100.0),
            ("MISSING", "2026-01-21", 120.0),
        ],
    )
    assert extract_reaction_labels(conn, as_of_date=date(2026, 1, 19)).empty
    labels = extract_reaction_labels(conn, as_of_date=date(2026, 1, 21))
    assert labels["act_symbol"].tolist() == ["A"]
    assert labels.iloc[0]["realized_move_pct"] == pytest.approx(0.04)


def test_targets_exclude_years_outside_verified_session_calendar():
    from ml.causal_features import extract_reaction_labels

    conn = market_connection(
        [
            ("OLD", "2019-07-03", "amc"),
            ("KNOWN", "2026-09-15", "bmo"),
            ("FUTURE", "2029-07-03", "amc"),
        ],
        [
            ("OLD", "2019-07-03", 100.0),
            ("OLD", "2019-07-04", 110.0),
            ("KNOWN", "2026-09-14", 100.0),
            ("KNOWN", "2026-09-15", 105.0),
            ("FUTURE", "2029-07-03", 100.0),
            ("FUTURE", "2029-07-04", 110.0),
        ],
    )
    labels = extract_reaction_labels(conn, as_of_date=date(2029, 7, 5))
    assert labels["act_symbol"].tolist() == ["KNOWN"]


@pytest.mark.parametrize(
    "protocol,expected_start",
    [
        (FEATURE_PROTOCOL_CAUSAL, "2023-01-01"),
        (FEATURE_PROTOCOL_LEGACY, "2019-06-01"),
    ],
)
def test_training_cli_default_start_respects_protocol_calendar(
    protocol, expected_start, monkeypatch
):
    import feature_engineering as features

    observed = []
    monkeypatch.setattr(
        "sys.argv", ["feature_engineering.py", "--feature-protocol", protocol]
    )
    monkeypatch.setattr(features, "connect_duckdb", duckdb.connect)
    monkeypatch.setattr(
        features,
        "extract_training_data",
        lambda conn, start, end, **kwargs: observed.append(start) or {},
    )
    features.main()
    assert observed == [expected_start]


def test_session_target_normalizes_split_and_cash_dividend():
    from ml.causal_features import extract_reaction_labels

    conn = market_connection(
        [("A", "2026-09-15", "bmo")],
        [("A", "2026-09-14", 100.0), ("A", "2026-09-15", 52.0)],
    )
    conn.execute(
        "CREATE TABLE v_splits AS SELECT 'A' act_symbol, DATE '2026-09-15' ex_date, 2.0 to_factor, 1.0 for_factor"
    )
    conn.execute(
        "CREATE TABLE v_dividends AS SELECT 'A' act_symbol, DATE '2026-09-15' ex_date, 1.0 amount"
    )
    labels = extract_reaction_labels(conn, as_of_date=date(2026, 9, 15))
    assert labels.iloc[0]["realized_move_pct"] == pytest.approx(0.06)


@pytest.mark.parametrize("window_start,window_end,expected", [
    ("2026-09-14", "2026-09-15", ["COVERED"]),
    ("2026-09-15", "2026-09-15", []),
    ("2026-09-14", "2026-09-14", []),
])
def test_causal_targets_require_symbol_and_full_reaction_action_coverage(window_start, window_end, expected):
    from ml.causal_features import extract_reaction_labels

    conn = market_connection([(symbol, "2026-09-15", "bmo") for symbol in ["COVERED", "UNCOVERED"]],
        [(symbol, day, close) for symbol in ["COVERED", "UNCOVERED"]
         for day, close in [("2026-09-14", 100.0), ("2026-09-15", 110.0)]])
    conn.execute("CREATE TABLE v_corporate_action_coverage (act_symbol VARCHAR,window_start DATE,window_end DATE)")
    conn.execute("INSERT INTO v_corporate_action_coverage VALUES ('COVERED',?,?)", [window_start, window_end])
    labels = extract_reaction_labels(conn, as_of_date=date(2026, 9, 15))
    assert labels["act_symbol"].tolist() == expected


def test_empty_action_coverage_withholds_all_causal_labels():
    from ml.causal_features import extract_reaction_labels

    conn = market_connection([("A", "2026-09-15", "bmo")], [("A", "2026-09-14", 100.), ("A", "2026-09-15", 110.)])
    conn.execute("CREATE TABLE v_corporate_action_coverage (act_symbol VARCHAR,window_start DATE,window_end DATE)")
    assert extract_reaction_labels(conn, as_of_date=date(2026, 9, 15)).empty


def test_file_backed_database_cannot_assume_missing_action_coverage_is_complete(tmp_path):
    from ml.causal_features import extract_reaction_labels

    conn = duckdb.connect(str(tmp_path / "production.duckdb"))
    conn.execute("CREATE TABLE v_earnings AS SELECT 'A' act_symbol,DATE '2026-09-15' date,'bmo' timing")
    conn.execute("CREATE TABLE v_ohlcv AS SELECT 'A' act_symbol,DATE '2026-09-14' date,100.0 AS close UNION ALL SELECT 'A',DATE '2026-09-15',110.0")
    with pytest.raises(ValueError, match="corporate.action coverage"):
        extract_reaction_labels(conn, as_of_date=date(2026, 9, 15))


def test_causal_vix_percentile_uses_chronological_window_and_ignores_future():
    from ml.causal_features import causal_macro_features

    dates = pd.bdate_range("2025-01-02", periods=254)
    conn = market_connection([], [("SPY", d, 100.0) for d in dates])
    vix = pd.DataFrame(
        {"date": dates, "vix_close": [999.0, 998.0] + [10.0] * 251 + [20.0]}
    )
    conn.register("vix_input", vix)
    conn.execute("CREATE VIEW v_vix AS SELECT * FROM vix_input")
    before = causal_macro_features(conn, as_of_date=dates[-1].date())
    assert before.iloc[-1]["vix_pct_252d"] == pytest.approx(1.0)
    extended = pd.concat(
        [vix, pd.DataFrame({"date": [pd.Timestamp("2027-01-01")], "vix_close": [1.0]})]
    )
    conn.register("vix_input", extended)
    after = causal_macro_features(conn, as_of_date=date(2027, 1, 2))
    pd.testing.assert_frame_equal(
        before, after.loc[after["date"] <= dates[-1]].reset_index(drop=True)
    )


def test_empty_candidate_is_durable_without_overwriting_production(tmp_path):
    production = tmp_path / "forecasts" / "forecasts_2026-09-15.parquet"
    production.parent.mkdir()
    production.write_bytes(b"prior-production")
    candidate = tmp_path / "validation" / "candidate.parquet"
    save_forecasts(pd.DataFrame(), tmp_path, output_path=candidate)
    assert candidate.exists()
    assert pd.read_parquet(candidate).empty
    assert (
        json.loads(candidate.with_suffix(".research.json").read_text())["status"]
        == "no_upcoming_events"
    )
    assert production.read_bytes() == b"prior-production"


def test_production_save_migrates_legacy_duckdb_table_and_inserts_by_name(
    tmp_path, monkeypatch
):
    from datetime import timedelta
    from ml.pipeline_validation import FORECAST_REQUIRED_COLUMNS

    row = {column: 1.0 for column in FORECAST_REQUIRED_COLUMNS}
    row.update(
        {
            "act_symbol": "A",
            "earnings_date": (date.today() + timedelta(days=1)).isoformat(),
            "snapshot_date": date.today().isoformat(),
            "timing": "bmo",
            "feature_vector": "{}",
            "model_bundle_id": "a" * 64,
            "model_horizon": 1,
            "feature_protocol": FEATURE_PROTOCOL_CAUSAL,
            "target_protocol": "quantiv.session-reaction.v2",
            "__cohort": "strict_options",
            "timing_confidence": "reported",
            "scored_at": "2026-10-01T22:00:00+00:00",
            "call_quote_timestamp": "2026-10-01T20:00:00+00:00",
            "put_quote_timestamp": "2026-10-01T20:00:00+00:00",
        }
    )
    database = tmp_path / "quantiv.duckdb"
    conn = duckdb.connect(str(database))
    conn.execute("CREATE TABLE ml_forecasts (legacy_note VARCHAR, act_symbol VARCHAR)")
    conn.execute("INSERT INTO ml_forecasts VALUES ('retained', 'OLD')")
    conn.close()
    monkeypatch.setenv("DUCKDB_PATH", str(database))
    save_forecasts(pd.DataFrame([row]), tmp_path)
    conn = duckdb.connect(str(database))
    observed = conn.execute(
        "SELECT act_symbol,legacy_note,feature_protocol,target_protocol FROM ml_forecasts ORDER BY act_symbol"
    ).fetchall()
    assert observed == [
        ("A", None, FEATURE_PROTOCOL_CAUSAL, "quantiv.session-reaction.v2"),
        ("OLD", "retained", None, None),
    ]
    artifact = next((tmp_path / "forecasts").glob("forecasts_*.parquet"))
    assert (
        pd.read_parquet(artifact).iloc[0]["feature_protocol"] == FEATURE_PROTOCOL_CAUSAL
    )
    conn.close()


def test_failed_duckdb_insert_does_not_publish_partial_forecast(tmp_path, monkeypatch):
    from datetime import datetime, timedelta
    from ml.pipeline_validation import FORECAST_REQUIRED_COLUMNS

    row = {column: 1.0 for column in FORECAST_REQUIRED_COLUMNS}
    row.update(
        {
            "act_symbol": "A",
            "earnings_date": (date.today() + timedelta(days=1)).isoformat(),
            "snapshot_date": date.today().isoformat(),
            "timing": "bmo",
            "feature_vector": "{}",
            "model_bundle_id": "a" * 64,
            "model_horizon": 1,
            "scored_at": "2026-10-01T22:00:00+00:00",
            "call_quote_timestamp": "2026-10-01T20:00:00+00:00",
            "put_quote_timestamp": "2026-10-01T20:00:00+00:00",
        }
    )
    database = tmp_path / "quantiv.duckdb"
    conn = duckdb.connect(str(database))
    conn.execute(
        "CREATE TABLE ml_forecasts (act_symbol VARCHAR CHECK (act_symbol='OLD'))"
    )
    conn.execute("INSERT INTO ml_forecasts VALUES ('OLD')")
    conn.close()
    artifact = (
        tmp_path
        / "forecasts"
        / f"forecasts_{datetime.now().strftime('%Y-%m-%d')}.parquet"
    )
    artifact.parent.mkdir()
    artifact.write_bytes(b"prior-production")
    monkeypatch.setenv("DUCKDB_PATH", str(database))
    with pytest.raises(duckdb.ConstraintException):
        save_forecasts(pd.DataFrame([row]), tmp_path)
    assert artifact.read_bytes() == b"prior-production"
    assert not artifact.with_suffix(".parquet.tmp").exists()
    conn = duckdb.connect(str(database))
    assert conn.execute("SELECT * FROM ml_forecasts").fetchall() == [("OLD",)]
    assert conn.execute("DESCRIBE ml_forecasts").fetchall()[0][0] == "act_symbol"
    conn.close()


def test_unknown_feature_protocol_fails_before_querying_database():
    with pytest.raises(ValueError, match="protocol"):
        get_upcoming_features(duckdb.connect(), 21, feature_protocol="future-unknown")


class ConstantModel:
    def predict(self, frame):
        return np.repeat(0.05, len(frame))


def test_causal_model_withholds_unrepresented_optionless_cohort():
    row = {
        "act_symbol": "A",
        "earnings_date": "2026-09-15",
        "snapshot_date": "2026-09-14",
        "lead_days": 1,
        "spot_price": 100.0,
        "timing": "bmo",
        "straddle_pct": np.nan,
    }
    model = {
        "feature_cols": ["straddle_pct"],
        "model": ConstantModel(),
        "residual_std": 0.03,
        "metadata": {
            "feature_protocol": FEATURE_PROTOCOL_CAUSAL,
            "cohort_reference": {
                "strict_options": {"rows": 100},
                "optionless": {"rows": 500},
            },
            "supported_cohorts": ["strict_options"],
        },
    }
    assert score(pd.DataFrame([row]), {1: model}).empty
    model["metadata"]["supported_cohorts"].append("optionless")
    result = score(pd.DataFrame([row]), {1: model})
    assert len(result) == 1
    assert result.iloc[0]["feature_protocol"] == FEATURE_PROTOCOL_CAUSAL
    model["metadata"] = {}
    assert (
        score(pd.DataFrame([row]), {1: model}).iloc[0]["feature_protocol"]
        == FEATURE_PROTOCOL_LEGACY
    )


def test_causal_scoring_withholds_unknown_timing_but_legacy_keeps_its_contract():
    row = {
        "act_symbol": "A",
        "earnings_date": "2026-09-15",
        "snapshot_date": "2026-09-14",
        "lead_days": 1,
        "spot_price": 100.0,
        "timing": "unknown",
        "straddle_pct": 0.04,
    }
    model = {
        "feature_cols": ["straddle_pct"],
        "model": ConstantModel(),
        "residual_std": 0.03,
        "metadata": {
            "feature_protocol": FEATURE_PROTOCOL_CAUSAL,
            "supported_cohorts": ["strict_options"],
        },
    }
    assert score(pd.DataFrame([row]), {1: model}).empty
    model["metadata"] = {}
    assert len(score(pd.DataFrame([row]), {1: model})) == 1


def test_causal_candidate_main_saves_empty_artifact_without_optional_rv_view(
    tmp_path, monkeypatch
):
    from scripts import daily_score

    conn = market_connection([], [])
    add_optional_feature_views(conn)
    conn.execute("DROP VIEW v_realized_vol")
    model = {
        "feature_cols": ["straddle_pct"],
        "model": ConstantModel(),
        "residual_std": 0.03,
        "metadata": {
            "feature_protocol": FEATURE_PROTOCOL_CAUSAL,
            "supported_cohorts": ["strict_options"],
        },
    }
    output = tmp_path / "validation/candidate.parquet"
    monkeypatch.setattr(
        "sys.argv",
        [
            "daily_score.py",
            "--models-dir",
            str(tmp_path / "models"),
            "--output-path",
            str(output),
        ],
    )
    monkeypatch.setattr(daily_score, "get_data_dir", lambda: tmp_path)
    monkeypatch.setattr(daily_score, "load_models", lambda path: {1: model})
    monkeypatch.setattr(daily_score.duckdb, "connect", lambda *args, **kwargs: conn)
    daily_score.main()
    assert pd.read_parquet(output).empty


def test_conflicting_reaction_timing_is_rejected_instead_of_picking_a_label():
    from ml.causal_features import extract_reaction_labels

    conn = market_connection(
        [("A", "2026-09-15", "bmo"), ("A", "2026-09-15", "amc")],
        [
            ("A", "2026-09-14", 100.0),
            ("A", "2026-09-15", 110.0),
            ("A", "2026-09-16", 100.0),
        ],
    )
    with pytest.raises(ValueError, match="conflicting"):
        extract_reaction_labels(conn, as_of_date=date(2026, 9, 16))


@pytest.mark.parametrize("timings", [("bmo", "unknown"), ("unknown", "bmo")])
def test_duplicate_unknown_calendar_rows_cannot_inherit_a_known_timing_label(timings):
    from ml.causal_features import build_causal_features

    conn = market_connection(
        [("A", "2026-09-15", timing) for timing in timings],
        [("A", "2026-09-14", 100.0), ("A", "2026-09-15", 110.0)],
    )
    add_optional_feature_views(conn)
    rows = build_causal_features(
        conn,
        start_date=date(2026, 9, 15),
        end_date=date(2026, 9, 15),
        as_of_date=date(2026, 9, 15),
        require_labels=True,
    )
    assert len(rows) == 1
    assert rows.iloc[0]["timing"] == "bmo"
    assert rows.iloc[0]["timing_confidence"] == "reported"
    assert rows.iloc[0]["realized_move_pct"] == pytest.approx(0.1)


def add_optional_feature_views(conn):
    # These are the actual nullable EOD feature-view contracts.
    option_numeric = "atm_iv atm_strike straddle_mid em_iv dte em_straddle call_bid call_ask call_mid call_relative_spread call_volume call_open_interest put_bid put_ask put_mid put_relative_spread put_volume put_open_interest straddle_bid straddle_ask straddle_relative_spread".split()
    option_text = "quote_timestamp_precision market_data_mode quote_quality_status liquidity_tier liquidity_tier_method quote_rejection_reason".split()
    option_fields = [
        "NULL::VARCHAR act_symbol",
        "NULL::DATE date",
        "NULL::DATE expiration",
    ]
    option_fields += [f"NULL::DOUBLE {column}" for column in option_numeric]
    option_fields += [f"NULL::VARCHAR {column}" for column in option_text]
    option_fields += [
        "NULL::TIMESTAMP call_quote_timestamp",
        "NULL::TIMESTAMP put_quote_timestamp",
    ]
    conn.execute(
        "CREATE VIEW v_straddle_features AS SELECT "
        + ", ".join(option_fields)
        + " WHERE FALSE"
    )
    rv_numeric = "close volume parkinson_rv_10d parkinson_rv_20d parkinson_rv_60d cc_rv_10d cc_rv_20d vol_of_vol_20d volume_ratio_20d drift_5d".split()
    conn.execute(
        "CREATE VIEW v_realized_vol AS SELECT act_symbol,date,"
        + ",".join(
            f"close AS {c}" if c == "close" else f"1.0 AS {c}" for c in rv_numeric
        )
        + " FROM v_ohlcv"
    )
    vh_columns = "iv_current iv_week_ago iv_month_ago iv_year_high iv_year_low hv_current hv_year_high hv_year_low".split()
    conn.execute(
        "CREATE VIEW v_volhist AS SELECT NULL::VARCHAR act_symbol,NULL::DATE date,"
        + ",".join(f"NULL::DOUBLE {c}" for c in vh_columns)
        + " WHERE FALSE"
    )
    conn.execute(
        "CREATE VIEW v_vix AS SELECT DISTINCT date, 20.0 AS vix_close FROM v_ohlcv"
    )


def freeze_market_clock(monkeypatch, instant):
    from scripts import market_sessions

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

    monkeypatch.setattr(market_sessions, "datetime", FrozenDatetime)


def reaction_training_connection(post_day, prior_session, amc_snapshot):
    symbols = {timing: [f"{timing}{i}" for i in range(40)] for timing in ("bmo", "amc")}
    conn = market_connection(
        [(s, post_day, "bmo") for s in symbols["bmo"]]
        + [(s, prior_session, "amc") for s in symbols["amc"]],
        [
            (s, day, price)
            for cohort in symbols.values()
            for s in cohort
            for day, price in [(amc_snapshot, 100.0), (prior_session, 100.0), (post_day, 110.0)]
        ],
    )
    add_optional_feature_views(conn)
    return conn


@pytest.mark.parametrize(
    "clock,post_day,prior_session,amc_snapshot,expected_horizons",
    [
        ("2026-10-05T15:59:00", "2026-10-05", "2026-10-02", "2026-10-01", set()),
        ("2026-10-05T16:00:00", "2026-10-05", "2026-10-02", "2026-10-01", {1, 3}),
        ("2026-11-27T12:59:00", "2026-11-27", "2026-11-25", "2026-11-24", set()),
        ("2026-11-27T13:00:00", "2026-11-27", "2026-11-25", "2026-11-24", {1, 2, 3}),
    ],
)
def test_default_training_waits_for_the_reaction_sessions_actual_close(
    monkeypatch, clock, post_day, prior_session, amc_snapshot, expected_horizons
):
    from feature_engineering import extract_training_data

    instant = datetime.fromisoformat(clock).replace(tzinfo=ZoneInfo("America/New_York"))
    freeze_market_clock(monkeypatch, instant)
    conn = reaction_training_connection(post_day, prior_session, amc_snapshot)
    try:
        features = extract_training_data(conn, amc_snapshot, post_day)
        assert set(features) == expected_horizons
        if features:
            assert features[1].symbol.str.startswith("amc").all()
            bmo_horizon = 3 if post_day == "2026-10-05" else 2
            assert features[bmo_horizon].symbol.str.startswith("bmo").all()
            for frame in features.values():
                assert frame.target.tolist() == pytest.approx([0.1] * len(frame.target))
                assert frame.observation_metadata["__label_available_at"].eq(instant.date()).all()
    finally:
        conn.close()


def test_default_training_preserves_an_earlier_historical_end_date(monkeypatch):
    from feature_engineering import extract_training_data

    freeze_market_clock(
        monkeypatch, datetime(2026, 10, 5, 17, tzinfo=ZoneInfo("America/New_York"))
    )
    conn = reaction_training_connection("2026-10-05", "2026-10-02", "2026-10-01")
    try:
        assert extract_training_data(conn, "2026-10-01", "2026-10-02") == {}
        features = extract_training_data(conn, "2026-10-01", "2026-10-05")
        assert features[1].target.tolist() == pytest.approx([0.1] * 40)
    finally:
        conn.close()


def test_explicit_training_asof_preserves_eod_replay_semantics(monkeypatch):
    from feature_engineering import extract_training_data

    freeze_market_clock(
        monkeypatch, datetime(2026, 10, 5, 12, tzinfo=ZoneInfo("America/New_York"))
    )
    conn = reaction_training_connection("2026-10-05", "2026-10-02", "2026-10-01")
    try:
        features = extract_training_data(
            conn, "2026-10-01", "2026-10-05", as_of_date=date(2026, 10, 5)
        )
        assert set(features) == {1, 3}
        assert features[1].symbol.str.startswith("amc").all()
        assert features[3].symbol.str.startswith("bmo").all()
    finally:
        conn.close()


def test_historical_spine_retains_optionless_rows_and_auditable_target_sidecars(
    tmp_path,
):
    from feature_engineering import extract_training_data, save_training_data

    symbols = [f"S{i}" for i in range(40)]
    conn = market_connection(
        [(s, "2026-09-15", "bmo") for s in symbols],
        [
            (s, d, p)
            for s in symbols
            for d, p in [("2026-09-14", 100.0), ("2026-09-15", 110.0)]
        ],
    )
    add_optional_feature_views(conn)
    features = extract_training_data(conn, "2026-09-01", "2026-09-15")
    assert len(features[1].features) == 40
    assert features[1].target.tolist() == pytest.approx([0.1] * 40)
    save_training_data(features, tmp_path)
    rows = pd.read_parquet(tmp_path / "training_T1.parquet")
    assert rows["straddle_pct"].isna().all()
    assert rows["__cohort"].eq("optionless").all()
    assert rows["__snapshot_date"].astype(str).eq("2026-09-14").all()
    assert rows["__label_available_at"].astype(str).eq("2026-09-15").all()
    assert rows["__pre_price_date"].astype(str).eq("2026-09-14").all()
    assert rows["__post_price_date"].astype(str).eq("2026-09-15").all()
    assert rows["__label_source"].eq("ohlcv_session_close").all()
    assert rows["__target_protocol"].eq("quantiv.session-reaction.v2").all()
    assert features[1].metadata["feature_protocol"] == FEATURE_PROTOCOL_CAUSAL


def test_past_earnings_features_respect_each_prediction_snapshot():
    from ml.causal_features import build_causal_features

    conn = market_connection(
        [("A", "2026-09-01", "amc"), ("A", "2026-09-15", "bmo")],
        [
            ("A", "2026-08-25", 90.0),
            ("A", "2026-09-01", 100.0),
            ("A", "2026-09-02", 110.0),
            ("A", "2026-09-14", 100.0),
            ("A", "2026-09-15", 105.0),
        ],
    )
    add_optional_feature_views(conn)
    rows = build_causal_features(
        conn,
        start_date=date(2026, 9, 15),
        end_date=date(2026, 9, 15),
        as_of_date=date(2026, 9, 15),
        require_labels=True,
    ).set_index("lead_days")
    assert rows.loc[21, "hist_event_count"] == 0
    assert pd.isna(rows.loc[21, "hist_move_last"])
    assert rows.loc[1, "hist_event_count"] == 1
    assert rows.loc[1, "hist_move_last"] == pytest.approx(0.1)


def test_live_unknown_timing_is_explicit_but_has_no_verified_reaction():
    from ml.causal_features import build_causal_features

    conn = market_connection(
        [("A", "2026-09-15", "other")], [("A", "2026-09-14", 100.0)]
    )
    add_optional_feature_views(conn)
    rows = build_causal_features(
        conn,
        start_date=date(2026, 9, 15),
        end_date=date(2026, 9, 15),
        as_of_date=date(2026, 9, 14),
    )
    assert rows.iloc[0]["timing_confidence"] == "unknown"
    assert pd.isna(rows.iloc[0]["realized_move_pct"])
    assert build_causal_features(
        conn,
        start_date=date(2026, 9, 15),
        end_date=date(2026, 9, 15),
        as_of_date=date(2026, 9, 14),
        require_labels=True,
    ).empty


def test_bundle_queries_reconstruct_each_protocols_own_vectors(tmp_path):
    from datetime import timedelta
    from scripts.daily_score import get_bundle_upcoming_features

    today = date.today()
    event = today + timedelta(days=1)
    future = today + timedelta(days=400)
    conn = market_connection(
        [("A", event, "bmo")],
        [("A", today, 100.0), ("SPY", today, 100.0), ("SPY", future, 100.0)],
    )
    add_optional_feature_views(conn)
    conn.execute(
        "CREATE OR REPLACE VIEW v_vix AS SELECT date,CASE WHEN date=CURRENT_DATE THEN 10.0 ELSE 1.0 END AS vix_close FROM v_ohlcv WHERE act_symbol='SPY'"
    )
    for name, metadata in [
        ("old", {"horizon": 1}),
        ("new", {"horizon": 1, "feature_protocol": FEATURE_PROTOCOL_CAUSAL}),
    ]:
        path = tmp_path / name
        path.mkdir()
        (path / "metadata_T1.json").write_text(json.dumps(metadata))
    old = get_bundle_upcoming_features(conn, tmp_path / "old", 21)
    new = get_bundle_upcoming_features(conn, tmp_path / "new", 21)
    assert old.iloc[0]["vix_pct_252d"] == pytest.approx(1.0)
    assert new.iloc[0]["vix_pct_252d"] == pytest.approx(0.0)


def test_causal_training_and_live_features_use_identical_asof_calculations():
    from ml.causal_features import build_causal_features

    conn = market_connection(
        [("A", "2026-09-15", "bmo")],
        [
            ("A", "2026-09-14", 100.0),
            ("A", "2026-09-15", 110.0),
            ("SPY", "2026-09-14", 100.0),
            ("SPY", "2026-09-15", 101.0),
        ],
    )
    add_optional_feature_views(conn)
    kwargs = dict(start_date=date(2026, 9, 15), end_date=date(2026, 9, 15))
    historical = build_causal_features(
        conn, **kwargs, as_of_date=date(2026, 9, 15), require_labels=True
    )
    live = build_causal_features(conn, **kwargs, as_of_date=date(2026, 9, 14))
    label_columns = [
        "realized_move_pct",
        "pre_price_date",
        "post_price_date",
        "label_available_at",
        "label_source",
        "target_protocol",
    ]
    pd.testing.assert_frame_equal(
        historical.drop(columns=label_columns), live.drop(columns=label_columns)
    )


def test_unknown_timing_requires_expiration_after_report_day():
    from ml.causal_features import build_causal_features

    conn = market_connection(
        [("A", "2026-09-15", "unknown"), ("B", "2026-09-15", "bmo")],
        [("A", "2026-09-14", 100.0), ("B", "2026-09-14", 100.0)],
    )
    add_optional_feature_views(conn)
    conn.execute("CREATE TABLE chain AS SELECT * FROM v_straddle_features")
    conn.execute(
        "INSERT INTO chain (act_symbol,date,expiration,atm_iv,atm_strike,straddle_mid,em_iv,dte) VALUES ('A','2026-09-14','2026-09-15',.3,100,6,5,1),('B','2026-09-14','2026-09-15',.3,100,6,5,1)"
    )
    conn.execute("CREATE OR REPLACE VIEW v_straddle_features AS SELECT * FROM chain")
    rows = build_causal_features(
        conn,
        start_date=date(2026, 9, 15),
        end_date=date(2026, 9, 15),
        as_of_date=date(2026, 9, 14),
    ).set_index("act_symbol")
    assert rows.loc["A", "__cohort"] == "optionless"
    assert rows.loc["B", "__cohort"] == "strict_options"


def test_history_count_retains_all_available_events_beyond_summary_window():
    from datetime import timedelta
    from ml.causal_features import build_causal_features

    past = [date(2026, 6, 3) + timedelta(days=7 * i) for i in range(15)]
    events = [("A", day, "bmo") for day in [*past, date(2026, 10, 5)]]
    prices = [("A", day - timedelta(days=1), 100.0) for day in past]
    prices += [("A", day, 102.0) for day in past]
    prices += [("A", date(2026, 10, 2), 100.0), ("A", date(2026, 10, 5), 103.0)]
    conn = market_connection(events, prices)
    add_optional_feature_views(conn)
    rows = build_causal_features(
        conn,
        start_date=date(2026, 10, 5),
        end_date=date(2026, 10, 5),
        as_of_date=date(2026, 10, 5),
        require_labels=True,
    )
    assert rows.iloc[-1]["hist_event_count"] == 15
