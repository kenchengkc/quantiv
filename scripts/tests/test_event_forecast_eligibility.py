"""Publication selects healthy events without weakening signed model gates."""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts import daily_score, model_publication
import model_control_plane_impl as plane
from scripts.tests.test_accepted_options_publication import (
    _monitor_fixture_rows, _signed_champion,
)

pytest_plugins = ["scripts.tests.test_accepted_options_publication"]


def test_publication_identity_rejects_unadmitted_cohort(accepted_options, monkeypatch):
    import event_forecast_eligibility as eligibility
    store = accepted_options
    frame = daily_score.get_bundle_forecasts(store["conn"], store["bundle"], 21)
    monkeypatch.setattr(eligibility, "verify_bundle_dir", lambda directory: {
        "bundle_id": store["bundle"].name, "horizons": [1, 2, 3, 7, 14, 21],
        "serving_capabilities": {str(h): ["optionless"] for h in (1, 2, 3, 7, 14, 21)},
    }, raising=False)
    with pytest.raises(ValueError, match="(admitted|capability)"):
        eligibility.verify_candidate_identity(frame, store["bundle"], store["bundle"].name,
                                             [1, 2, 3, 7, 14, 21], require_admitted_cohorts=True)


def test_same_session_candidate_cannot_republish_a_quarantined_issuer(accepted_options):
    store = accepted_options
    good = daily_score.get_bundle_forecasts(store["conn"], store["bundle"], 21)
    quarantined = good.iloc[:1].assign(act_symbol="QUARANTINED")
    _, policy, report, _ = _monitor_fixture_rows(store, pd.concat([good, quarantined]))
    assert policy["coverage"]["eligible_rows"] == len(good)
    assert policy["coverage"]["held_rows"] == 1
    assert report["publication_eligibility"]["held_events"][0]["reasons"] == ["corporate_action_source_unavailable"]


@pytest.mark.parametrize("mutation", ["prediction", "stale", "vector", "quantiles"])
def test_healthy_events_publish_when_one_row_fails_local_checks(accepted_options, mutation):
    store = accepted_options
    good = daily_score.get_bundle_forecasts(store["conn"], store["bundle"], 21)
    bad = good.iloc[:1].copy()
    bad["act_symbol"] = "HELD"
    if mutation == "prediction":
        bad["em_ml_pct"] = np.nan
    elif mutation == "stale":
        bad["snapshot_date"] = pd.Timestamp(store["previous"])
        bad["scored_at"] = (pd.Timestamp(store["previous"]).tz_localize("UTC") + pd.Timedelta(hours=21)).isoformat()
    elif mutation == "vector":
        bad["feature_vector"] = "{invalid"
    else:
        bad["p10"] = .2
    result, policy, report, candidate = _monitor_fixture_rows(store, pd.concat([good, bad]))
    assert result == 1
    assert policy["state"] == "partial" and policy["can_publish_ml"]
    assert report["status"] == "partial"
    assert report["feature_drift"]["status"] not in {"passed", "warning"}
    assert policy["coverage"] == {"candidate_rows": 101, "eligible_rows": 100,
                                  "held_rows": 1, "candidate_events": 101,
                                  "eligible_events": 100, "held_events": 1}
    assert policy["held_event_keys"] == [f"HELD|{store['event']}"]
    eligible = pd.read_parquet(policy["eligible_forecast_path"])
    assert set(eligible["act_symbol"]) == set(store["symbols"])
    assert set(policy["eligible_forecast_ids"]) == set(eligible["forecast_id"])
    signed = report["publication_eligibility"]
    assert signed["schema"] == "quantiv.event-forecast-eligibility.v1"
    assert signed["eligible_forecast_path"].startswith("forecasts/eligible/")
    assert signed["candidate_sha256"] == report["forecast_sha256"]


def test_sparse_horizon_cannot_hold_healthy_horizon(accepted_options, monkeypatch):
    import event_forecast_eligibility as eligibility

    store = accepted_options
    coverage = eligibility._current_source_coverage(store["bundle"])
    coverage["SPARSE"] = ("2019-01-01", store["latest"].isoformat())
    monkeypatch.setattr(eligibility, "_current_source_coverage", lambda _bundle: coverage)
    good = daily_score.get_bundle_forecasts(store["conn"], store["bundle"], 21)
    sparse = good.iloc[:1].copy()
    sparse["act_symbol"] = "SPARSE"
    sparse["model_horizon"] = 14
    result, policy, report, _ = _monitor_fixture_rows(store, pd.concat([good, sparse]))
    assert result == 1 and policy["state"] == "partial"
    assert policy["coverage"]["eligible_rows"] == 100
    assert report["publication_eligibility"]["cohort_checks"]["14|strict_options"]["status"] == "insufficient_data"


@pytest.mark.parametrize("mutation", ["eligible", "candidate", "backup", "identity", "path", "resigned_row", "resigned_tiny_change"])
def test_partial_publication_rejects_tampered_or_unbound_artifacts(accepted_options, mutation):
    store = accepted_options
    good = daily_score.get_bundle_forecasts(store["conn"], store["bundle"], 21)
    bad = good.iloc[:1].copy()
    bad["act_symbol"] = "HELD"
    bad["em_ml_pct"] = np.nan
    _, policy, report, candidate = _monitor_fixture_rows(store, pd.concat([good, bad]))
    monitoring = store["data"] / "models/monitoring"
    if mutation == "eligible":
        Path(policy["eligible_forecast_path"]).write_bytes(b"tampered")
    elif mutation == "candidate":
        candidate.write_bytes(b"tampered")
    elif mutation == "backup":
        (model_publication.backup_dir(store["data"]) / "event_forecast_archive.parquet").write_bytes(b"tampered")
    else:
        from ml.model_bundle import create_signed_monitor_receipt
        if mutation == "identity":
            report["publication_eligibility"]["eligible_forecast_ids"] = ["wrong"]
        elif mutation == "path":
            report["publication_eligibility"]["eligible_forecast_path"] = "../outside.parquet"
        else:
            from ml.model_artifact import sha256_file
            eligible_path = Path(policy["eligible_forecast_path"])
            rows = pd.read_parquet(eligible_path)
            rows.loc[0, "em_ml_pct"] = rows.loc[0, "em_ml_pct"] + 1e-9 if mutation == "resigned_tiny_change" else .99
            rows.to_parquet(eligible_path, index=False)
            report["publication_eligibility"]["eligible_forecast_sha256"] = sha256_file(eligible_path)
        report_path = monitoring / "latest_monitoring.json"
        report_path.write_text(json.dumps(report))
        receipt = create_signed_monitor_receipt(ledger_path=monitoring / "prediction_ledger.parquet",
                                               report_path=report_path, snapshot_date=report["snapshot_date"])
        (monitoring / "latest_monitoring.receipt.json").write_text(json.dumps(receipt))
    with pytest.raises((RuntimeError, ValueError)):
        model_publication.verify_publication(store["data"], forecast_path=candidate)


@pytest.mark.parametrize("mutation", ["schema", "blank_bundle", "duplicate", "forecast_id",
                                      "feature_hash", "freeze_eligible", "freeze_ineligible_reason",
                                      "feature_cutoff_at", "prediction_deadline_at"])
def test_global_candidate_integrity_failures_remain_fatal(accepted_options, mutation):
    store = accepted_options
    frame = daily_score.get_bundle_forecasts(store["conn"], store["bundle"], 21)
    candidate = daily_score.save_forecasts(frame, store["data"], output_path=store["data"] / "validation/candidate.parquet")
    frame = pd.read_parquet(candidate)
    if mutation == "schema":
        frame = frame.drop(columns="p90")
    elif mutation == "blank_bundle":
        frame.loc[0, "model_bundle_id"] = None
    elif mutation == "duplicate":
        frame = pd.concat([frame, frame.iloc[:1]])
    elif mutation == "forecast_id":
        frame.loc[0, "forecast_id"] = "wrong"
    elif mutation == "feature_hash":
        frame.loc[0, "feature_hash"] = "wrong"
    elif mutation == "freeze_eligible":
        frame.loc[0, "freeze_eligible"] = False
    elif mutation == "freeze_ineligible_reason":
        frame.loc[0, "freeze_ineligible_reason"] = "source_held"
    else:
        frame.loc[0, mutation] = "2099-01-01T00:00:00+00:00"
    frame.to_parquet(candidate, index=False)
    with pytest.raises(ValueError):
        plane.monitor(argparse.Namespace(models_root=store["data"] / "models",
                                         forecast_dir=store["data"] / "forecasts", forecast_path=candidate))


def test_consistent_audit_timestamps_preserve_original_timezone_values(accepted_options):
    store = accepted_options
    frame = daily_score.get_bundle_forecasts(store["conn"], store["bundle"], 21)
    model_publication.snapshot_published_forecasts(store["data"])
    candidate = daily_score.save_forecasts(frame, store["data"], output_path=store["data"] / "validation/candidate.parquet")
    source = pd.read_parquet(candidate)
    for column in ("feature_cutoff_at", "prediction_deadline_at", "feature_snapshot_at"):
        source[column] = pd.to_datetime(source[column], utc=True).dt.tz_convert("America/New_York").map(lambda value: value.isoformat())
    source.to_parquet(candidate, index=False)
    assert plane.monitor(argparse.Namespace(models_root=store["data"] / "models",
                                            forecast_dir=store["data"] / "forecasts", forecast_path=candidate)) == 0
    policy = model_publication.verify_publication(store["data"], forecast_path=candidate)
    selected = pd.read_parquet(policy["eligible_forecast_path"])
    pd.testing.assert_frame_equal(source.reset_index(drop=True), selected.reset_index(drop=True))


def test_ninety_nine_current_observations_do_not_pass_drift_gate(accepted_options):
    store = accepted_options
    frame = daily_score.get_bundle_forecasts(store["conn"], store["bundle"], 21).iloc[:99]
    result, policy, report, _ = _monitor_fixture_rows(store, frame)
    assert result == 1 and policy["state"] == "held" and not policy["can_publish_ml"]
    assert policy["coverage"]["eligible_rows"] == 0
    assert report["publication_eligibility"]["cohort_checks"]["7|strict_options"]["status"] == "insufficient_data"


def test_partial_monitor_wrapper_emits_exact_selected_artifact(accepted_options, monkeypatch, tmp_path):
    store = accepted_options
    good = daily_score.get_bundle_forecasts(store["conn"], store["bundle"], 21)
    bad = good.iloc[:1].copy()
    bad["act_symbol"] = "HELD"
    bad["em_ml_pct"] = np.nan
    _, _, _, candidate = _monitor_fixture_rows(store, pd.concat([good, bad]))
    output = tmp_path / "outputs"
    monkeypatch.setattr("sys.argv", ["model_publication.py", "monitor", "--data-dir", str(store["data"]),
                                    "--forecast-path", str(candidate), "--github-output", str(output)])
    assert model_publication.main() == 0
    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert values["can_publish_ml"] == values["has_forecasts"] == "true"
    assert values["eligible_rows"] == "100" and values["held_events"] == "1"
    assert len(pd.read_parquet(values["eligible_forecast_path"])) == 100


def test_malformed_candidate_row_does_not_crash_registered_shadow(accepted_options, tmp_path):
    import duckdb
    import shutil
    from ml.model_bundle import create_signed_registry
    from scripts.setup_duckdb_from_parquet import setup_views

    store = accepted_options
    shadow_data = tmp_path / "shadow-data"
    shadow_data.mkdir()
    fixture_root = tmp_path / "shadow-fixture"
    fixture_root.mkdir()
    shadow = _signed_champion(shadow_data, fixture_root, metadata_overrides={"shadow_fixture": True})
    shutil.copytree(shadow, store["data"] / "models/bundles" / shadow.name)
    registry = create_signed_registry(champion_bundle_id=store["bundle"].name,
                                      challenger_bundle_id=shadow.name, previous_bundle_id=None,
                                      decision={"reason": "shadow only"})
    (store["data"] / "models/control/registry.json").write_text(json.dumps(registry))
    database = store["data"] / "shadow.duckdb"
    with duckdb.connect(str(database)) as conn:
        setup_views(conn, store["data"])
        conn.execute("""CREATE VIEW v_volhist AS SELECT NULL::DATE AS date, NULL::VARCHAR AS act_symbol,
             NULL::DOUBLE AS hv_current, NULL::DOUBLE AS hv_year_high, NULL::DOUBLE AS hv_year_low,
             NULL::DOUBLE AS iv_current, NULL::DOUBLE AS iv_week_ago, NULL::DOUBLE AS iv_month_ago,
             NULL::DOUBLE AS iv_year_high, NULL::DOUBLE AS iv_year_low WHERE FALSE""")
    good = daily_score.get_bundle_forecasts(store["conn"], store["bundle"], 21)
    bad = good.iloc[:1].copy()
    bad["act_symbol"] = "HELD"
    bad["feature_vector"] = "{invalid"
    result, policy, report, _ = _monitor_fixture_rows(store, pd.concat([good, bad]), database=database)
    assert result == 1 and policy["state"] == "partial"
    assert report["shadow_scoring"]["challenger"]["status"] in {"passed", "warning"}
    ledger = pd.read_parquet(store["data"] / "models/monitoring/prediction_ledger.parquet")
    assert (ledger["role"] == "challenger").sum() == 100


@pytest.mark.parametrize("accepted_options", ["2026-10-13T07:00:00+00:00"], indirect=True)
def test_registered_daily_head_collects_evidence_beyond_public_window(accepted_options, tmp_path):
    import duckdb
    import shutil
    from ml.model_bundle import create_signed_registry
    from scripts.setup_duckdb_from_parquet import setup_views

    store = accepted_options
    data = tmp_path / "daily-shadow-data"
    data.mkdir()
    fixture = tmp_path / "daily-shadow-fixture"
    fixture.mkdir()
    shadow = _signed_champion(data, fixture, horizons=(25,),
        metadata_overrides={"supported_cohorts": ["strict_options", "optionless"]})
    shutil.copytree(shadow, store["data"] / "models/bundles" / shadow.name)
    registry = create_signed_registry(champion_bundle_id=store["bundle"].name,
        challenger_bundle_id=shadow.name, previous_bundle_id=None, decision={})
    (store["data"] / "models/control/registry.json").write_text(json.dumps(registry))
    database = store["data"] / "daily-shadow.duckdb"
    with duckdb.connect(str(database)) as conn:
        setup_views(conn, store["data"])
        conn.execute("DROP VIEW v_earnings")
        conn.execute("CREATE OR REPLACE TABLE v_earnings AS SELECT act_symbol, CAST(? AS DATE) date, 'bmo' timing FROM v_ohlcv GROUP BY act_symbol", [store["latest"] + pd.Timedelta(days=25)])
        conn.execute("""CREATE VIEW v_volhist AS SELECT NULL::DATE AS date, NULL::VARCHAR AS act_symbol,
             NULL::DOUBLE AS hv_current, NULL::DOUBLE AS hv_year_high, NULL::DOUBLE AS hv_year_low,
             NULL::DOUBLE AS iv_current, NULL::DOUBLE AS iv_week_ago, NULL::DOUBLE AS iv_month_ago,
             NULL::DOUBLE AS iv_year_high, NULL::DOUBLE AS iv_year_low WHERE FALSE""")
    good = daily_score.get_bundle_forecasts(store["conn"], store["bundle"], 21)
    _monitor_fixture_rows(store, good, database=database)
    ledger = pd.read_parquet(store["data"] / "models/monitoring/prediction_ledger.parquet")
    observed = ledger.loc[ledger.bundle_id.eq(shadow.name)]
    assert len(observed) > 0
    assert set(observed.model_horizon) == {25}
