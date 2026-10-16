"""Live scoring observes current admitted inputs without reconstructing old scores."""

from datetime import datetime, timezone
import json

import duckdb
import lightgbm as lgb
import numpy as np
import pandas as pd
import pytest

from ml.model_protocol import FEATURE_PROTOCOL_CAUSAL, FEATURE_PROTOCOL_LEGACY
from ml.pipeline_validation import FORECAST_REQUIRED_COLUMNS
from scripts import daily_score


PROTOCOLS = [FEATURE_PROTOCOL_LEGACY, FEATURE_PROTOCOL_CAUSAL]
NOW = datetime(2026, 10, 8, 6, tzinfo=timezone.utc)


@pytest.fixture
def market_clock(monkeypatch):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)

    monkeypatch.setattr(daily_score, "datetime", FrozenDatetime)


def _connection(*, quote_date="2026-10-07", quote_pct=0.10, with_current_price=True):
    conn = duckdb.connect()
    conn.execute("CREATE TABLE v_earnings (act_symbol VARCHAR, date DATE, timing VARCHAR)")
    conn.execute("INSERT INTO v_earnings VALUES ('COVERED', DATE '2026-10-14', 'bmo'), ('OFF_HEAD', DATE '2026-10-15', 'amc')")
    conn.execute("CREATE TABLE prices_input (act_symbol VARCHAR, date DATE, close DOUBLE)")
    prices = [(symbol, day, 100.) for symbol in ("COVERED", "OFF_HEAD", "SPY", "TLT")
              for day in ("2026-09-30", "2026-10-08")]
    if with_current_price:
        prices += [(symbol, "2026-10-07", 100.) for symbol in ("COVERED", "OFF_HEAD", "SPY", "TLT")]
    conn.executemany("INSERT INTO prices_input VALUES (?, ?, ?)", prices)
    conn.execute("CREATE VIEW v_ohlcv AS SELECT * FROM prices_input")
    numeric = "atm_iv atm_strike straddle_mid em_iv dte em_straddle call_bid call_ask call_mid call_relative_spread call_volume call_open_interest put_bid put_ask put_mid put_relative_spread put_volume put_open_interest straddle_bid straddle_ask straddle_relative_spread".split()
    text = "quote_timestamp_precision market_data_mode quote_quality_status liquidity_tier liquidity_tier_method quote_rejection_reason".split()
    fields = ["NULL::VARCHAR act_symbol", "NULL::DATE date", "NULL::DATE expiration"]
    fields += [f"NULL::DOUBLE {column}" for column in numeric]
    fields += [f"NULL::VARCHAR {column}" for column in text]
    fields += ["NULL::TIMESTAMP call_quote_timestamp", "NULL::TIMESTAMP put_quote_timestamp"]
    conn.execute("CREATE TABLE v_straddle_features AS SELECT " + ", ".join(fields) + " WHERE FALSE")
    conn.execute("INSERT INTO v_straddle_features (act_symbol,date,expiration,atm_iv,atm_strike,straddle_mid,em_straddle,em_iv,dte,quote_quality_status,market_data_mode) VALUES ('COVERED', ?, DATE '2026-10-16', 0.5, 100., ?, ?, 10., 9., 'passed', 'end_of_day')", [quote_date, 100. * quote_pct, 100. * quote_pct])
    rv = "close volume parkinson_rv_10d parkinson_rv_20d parkinson_rv_60d cc_rv_10d cc_rv_20d vol_of_vol_20d volume_ratio_20d drift_5d".split()
    conn.execute("CREATE VIEW v_realized_vol AS SELECT act_symbol,date," + ",".join("close" if c == "close" else f"1.0 AS {c}" for c in rv) + " FROM v_ohlcv")
    vh = "iv_current iv_week_ago iv_month_ago iv_year_high iv_year_low hv_current hv_year_high hv_year_low".split()
    conn.execute("CREATE VIEW v_volhist AS SELECT NULL::VARCHAR act_symbol,NULL::DATE date," + ",".join(f"NULL::DOUBLE {c}" for c in vh) + " WHERE FALSE")
    conn.execute("CREATE VIEW v_vix AS SELECT DISTINCT date,20.0 AS vix_close FROM v_ohlcv")
    return conn


def _native_model(protocol, *, supported=("strict_options",)):
    values = np.tile([0.02, 0.04, 0.06, 0.08, 0.10], 8)
    model = lgb.train(
        {"objective": "regression", "verbosity": -1, "num_threads": 1,
         "min_data_in_leaf": 1, "num_leaves": 5, "learning_rate": 0.5},
        lgb.Dataset(pd.DataFrame({"straddle_pct": values}), label=values),
        num_boost_round=8,
    )
    return {"feature_cols": ["straddle_pct"], "model": model, "residual_std": 0.03,
            "bundle_id": "same-model", "metadata": {"feature_protocol": protocol,
            "supported_cohorts": list(supported)}}


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_live_features_use_only_latest_completed_session(market_clock, protocol):
    with _connection() as conn:
        features = daily_score.get_upcoming_features(conn, 21, feature_protocol=protocol)
    assert set(features["snapshot_date"].dt.date.astype(str)) == {"2026-10-07"}
    assert len(features) == 2


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_live_features_withhold_issuers_without_snapshot_action_coverage(market_clock, protocol):
    with _connection() as conn:
        conn.execute("""CREATE TABLE v_corporate_action_coverage AS SELECT 'COVERED' AS act_symbol,
            DATE '2020-01-01' AS window_start, DATE '2026-10-07' AS window_end""")
        features = daily_score.get_upcoming_features(conn, 21, feature_protocol=protocol)
        assert features["act_symbol"].tolist() == ["COVERED"]
        conn.execute("UPDATE v_corporate_action_coverage SET window_end = DATE '2026-10-06'")
        assert daily_score.get_upcoming_features(conn, 21, feature_protocol=protocol).empty


def test_file_backed_legacy_scoring_cannot_assume_missing_action_coverage(tmp_path, market_clock):
    with _connection() as conn:
        database = str(tmp_path / "source.duckdb").replace("'", "''")
        conn.execute(f"ATTACH '{database}' AS persisted")
        assert daily_score.get_upcoming_features(conn, 21, feature_protocol=FEATURE_PROTOCOL_LEGACY).empty


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_missing_current_price_does_not_fall_back_or_use_future_price(market_clock, protocol):
    with _connection(with_current_price=False) as conn:
        features = daily_score.get_upcoming_features(conn, 21, feature_protocol=protocol)
    assert features.empty


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_one_session_grace_preserves_snapshot_features_and_exact_head(market_clock, protocol):
    model = _native_model(protocol, supported=("strict_options", "optionless"))
    with _connection(quote_date="2026-10-06", with_current_price=False) as conn:
        conn.execute("INSERT INTO prices_input SELECT act_symbol, DATE '2026-10-06', close FROM prices_input WHERE date=DATE '2026-09-30'")
        features = daily_score.get_upcoming_features(conn, 21, feature_protocol=protocol)
        assert set(features["snapshot_date"].dt.date.astype(str)) == {"2026-10-06"}
        forecasts = daily_score.score_upcoming_models(conn, {7: model, 8: model}, 21)
    assert forecasts["act_symbol"].tolist() == ["COVERED"]
    assert forecasts["model_horizon"].tolist() == [8]
    assert str(forecasts.iloc[0]["snapshot_date"])[:10] == "2026-10-06"
    assert json.loads(forecasts.iloc[0]["feature_vector"])["straddle_pct"] == .1


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_live_scoring_excludes_events_past_prediction_deadline(market_clock, protocol):
    model = _native_model(protocol, supported=("strict_options", "optionless"))
    with _connection() as conn:
        conn.execute("UPDATE v_earnings SET date=DATE '2026-10-08'")
        forecasts = daily_score.score_upcoming_models(conn, {1: model}, 21)
    assert forecasts["act_symbol"].tolist() == ["OFF_HEAD"]


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_current_strict_quotes_reach_recorded_vector_and_native_prediction(market_clock, protocol):
    model = _native_model(protocol)
    predicted = []
    for quote_pct in (0.02, 0.10):
        with _connection(quote_pct=quote_pct) as conn:
            forecasts = daily_score.score_upcoming_models(conn, {7: model, 14: model}, 21)
        assert len(forecasts) == 1
        row = forecasts.iloc[0]
        assert str(row["snapshot_date"])[:10] == "2026-10-07"
        assert row["model_horizon"] == 7
        assert row["model_bundle_id"] == "same-model"
        assert row["feature_protocol"] == protocol
        assert json.loads(row["feature_vector"])["straddle_pct"] == pytest.approx(quote_pct)
        predicted.append(row["em_ml_pct"])
    assert predicted[1] > predicted[0]


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_stale_quotes_are_not_relabelled_to_current_price(market_clock, protocol):
    with _connection(quote_date="2026-09-30") as conn:
        features = daily_score.get_upcoming_features(conn, 21, feature_protocol=protocol)
        assert features["straddle_pct"].isna().all()
        forecasts = daily_score.score(features, {7: _native_model(protocol)})
    assert forecasts.empty


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_live_scoring_uses_exact_trained_head_without_nearest_head(market_clock, protocol):
    model = _native_model(protocol, supported=("strict_options", "optionless"))
    with _connection() as conn:
        forecasts = daily_score.score_upcoming_models(conn, {7: model, 14: model}, 21)
    assert forecasts["act_symbol"].tolist() == ["COVERED"]
    assert forecasts["model_horizon"].tolist() == [7]


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_model_omits_unrepresented_optionless_cohort(protocol):
    frame = pd.DataFrame({"act_symbol": ["YES", "NO"], "earnings_date": ["2026-10-14"] * 2,
                          "snapshot_date": ["2026-10-07"] * 2, "lead_days": [7] * 2,
                          "spot_price": [100.] * 2, "timing": ["bmo"] * 2,
                          "straddle_pct": [0.1, np.nan]})
    forecasts = daily_score.score(frame, {7: _native_model(protocol)})
    assert forecasts["act_symbol"].tolist() == ["YES"]
    assert forecasts["__cohort"].tolist() == ["strict_options"]


def test_legacy_metadata_defaults_to_strict_options_only():
    model = _native_model(FEATURE_PROTOCOL_LEGACY)
    model["metadata"] = {}
    frame = pd.DataFrame({"act_symbol": ["NO"], "earnings_date": ["2026-10-14"],
                          "lead_days": [7], "spot_price": [100.], "timing": ["bmo"],
                          "straddle_pct": [np.nan]})
    assert daily_score.score(frame, {7: model}).empty


def test_empty_live_score_writes_current_artifact_and_preserves_prior_predictions(
    market_clock, tmp_path,
):
    forecasts = tmp_path / "forecasts"
    forecasts.mkdir()
    prior = {
        "forecasts_2026-10-07.parquet": b"previous-daily-forecast",
        "event_forecast_archive.parquet": b"previous-event-archive",
        "event_prediction_ledger.parquet": b"previous-event-ledger",
    }
    for name, value in prior.items():
        (forecasts / name).write_bytes(value)
    database = tmp_path / "quantiv.duckdb"
    database.write_bytes(b"previous-database")

    saved = daily_score.save_forecasts(pd.DataFrame(), tmp_path)

    assert saved == forecasts / "forecasts_2026-10-08.parquet"
    empty = pd.read_parquet(saved)
    assert empty.empty
    assert set(empty.columns) == FORECAST_REQUIRED_COLUMNS
    assert database.read_bytes() == b"previous-database"
    for name, value in prior.items():
        assert (forecasts / name).read_bytes() == value


def test_empty_main_reports_exact_new_artifact_and_false_workflow_output(
    market_clock, tmp_path, monkeypatch,
):
    output = tmp_path / "github-output"
    conn = _connection(quote_date="2026-09-30")
    model = _native_model(FEATURE_PROTOCOL_LEGACY)
    monkeypatch.setattr("sys.argv", [
        "daily_score.py", "--models-dir", str(tmp_path / "models"),
        "--github-output", str(output),
    ])
    monkeypatch.setattr(daily_score, "get_data_dir", lambda: tmp_path)
    monkeypatch.setattr(daily_score, "load_models", lambda models_dir: {7: model})
    monkeypatch.setattr(daily_score.duckdb, "connect", lambda *args, **kwargs: conn)

    daily_score.main()

    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert values == {
        "forecast_path": str(tmp_path / "forecasts/forecasts_2026-10-08.parquet"),
        "has_forecasts": "false", "forecast_rows": "0",
    }
    assert pd.read_parquet(values["forecast_path"]).empty


def test_nonempty_main_reports_exact_candidate_and_true_workflow_output(
    market_clock, tmp_path, monkeypatch,
):
    output = tmp_path / "github-output"
    candidate = tmp_path / "validation/current.parquet"
    conn = _connection()
    model = _native_model(FEATURE_PROTOCOL_LEGACY)
    monkeypatch.setattr("sys.argv", [
        "daily_score.py", "--models-dir", str(tmp_path / "models"),
        "--output-path", str(candidate), "--github-output", str(output),
    ])
    monkeypatch.setattr(daily_score, "get_data_dir", lambda: tmp_path)
    monkeypatch.setattr(daily_score, "load_models", lambda models_dir: {7: model})
    monkeypatch.setattr(daily_score.duckdb, "connect", lambda *args, **kwargs: conn)

    daily_score.main()

    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert values == {
        "forecast_path": str(candidate), "has_forecasts": "true", "forecast_rows": "1",
    }
    assert pd.read_parquet(candidate)["act_symbol"].tolist() == ["COVERED"]
    assert not (tmp_path / "forecasts/event_prediction_ledger.parquet").exists()


@pytest.fixture
def signed_daily_models(tmp_path, monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from ml.evidence_receipt import build_evidence_receipt
    from ml.model_bundle import create_signed_bundle, required_artifact_names
    from ml.model_artifact import save_native_model
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    monkeypatch.setenv("MODEL_BUNDLE_PUBLIC_KEY", key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode())
    models = tmp_path / "models"
    models.mkdir()
    native = _native_model(FEATURE_PROTOCOL_LEGACY)["model"]
    for name in required_artifact_names((4, 25)):
        if name.startswith("metadata"):
            horizon = int(name.removeprefix("metadata_T").removesuffix(".json"))
            (models / name).write_text(json.dumps({"horizon": horizon, "feature_cols": ["straddle_pct"]}))
        else:
            save_native_model(native, models / name)
    report = {"status": "passed", "stages": {"models": {"horizons": [4, 25]}}}
    receipt = build_evidence_receipt(report, scope="models", repo_root=tmp_path, data_dir=tmp_path,
        training_dir=tmp_path / "training", models_dir=models, forecast_path=None, horizons=(4, 25))
    report_path, receipt_path = tmp_path / "report.json", tmp_path / "receipt.json"
    report_path.write_text(json.dumps(report))
    receipt_path.write_text(json.dumps(receipt))
    bundle, _ = create_signed_bundle(models, tmp_path / "bundles", receipt_path=receipt_path,
        validation_report_path=report_path, source_revision="test", horizons=(4, 25), private_key=private)
    return bundle


def test_daily_loading_ignores_undeclared_metadata(signed_daily_models):
    (signed_daily_models / "metadata_T7.json").write_text(json.dumps({"horizon": 7, "feature_protocol": "unknown"}))
    assert sorted(daily_score.load_models(signed_daily_models)) == [4, 25]


def test_daily_loading_rejects_missing_signed_head(signed_daily_models):
    from ml.model_bundle import ModelBundleError
    (signed_daily_models / "lgbm_T25.txt").unlink()
    with pytest.raises(ModelBundleError, match="missing"):
        daily_score.load_models(signed_daily_models)


def test_bundle_feature_construction_ignores_undeclared_metadata(signed_daily_models, market_clock):
    (signed_daily_models / "metadata_T7.json").write_text(json.dumps({"horizon": 7, "feature_protocol": "unknown"}))
    with _connection() as connection:
        frame = daily_score.get_bundle_upcoming_features(connection, signed_daily_models, 21)
    assert len(frame) == 2


def test_default_daily_score_covers_the_twenty_first_upcoming_day(market_clock, tmp_path, monkeypatch):
    output = tmp_path / "github-output"
    connection = _connection()
    connection.execute("INSERT INTO v_earnings VALUES ('TAIL', DATE '2026-10-29', 'bmo')")
    connection.execute("INSERT INTO prices_input VALUES ('TAIL', DATE '2026-10-07', 100.)")
    model = _native_model(FEATURE_PROTOCOL_CAUSAL, supported=("strict_options", "optionless"))
    monkeypatch.setattr("sys.argv", ["daily_score.py", "--models-dir", str(tmp_path / "models"),
        "--github-output", str(output), "--output-path", str(tmp_path / "candidate.parquet")])
    monkeypatch.setattr(daily_score, "get_data_dir", lambda: tmp_path)
    monkeypatch.setattr(daily_score, "load_models", lambda models_dir: {22: model})
    monkeypatch.setattr(daily_score.duckdb, "connect", lambda *args, **kwargs: connection)
    daily_score.main()
    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert pd.read_parquet(values["forecast_path"])["act_symbol"].tolist() == ["TAIL"]


def test_alternate_serving_pipeline_loads_exact_signed_daily_heads(signed_daily_models):
    from ml.serving_pipeline import MLServingPipeline
    pipeline = MLServingPipeline.__new__(MLServingPipeline)
    pipeline.models_dir = signed_daily_models
    pipeline.models = {}
    pipeline._load_models()
    assert sorted(pipeline.models) == [4, 25]
