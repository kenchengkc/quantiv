from __future__ import annotations

import importlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


def _engine():
    return importlib.import_module("ml.historical_admission")


def _fixture_source_report():
    return {"schema": "quantiv.historical-training-admission.v1", "status": "passed",
        "release_id": "a" * 64, "earnings_calendar_sha256": "b" * 64,
        "calendar_source": {"path": "earnings_calendar.csv", "sha256": "b" * 64, "events": 500},
        "corporate_actions": {"receipt_id": "c" * 64}}


def _install_fixture_trust(monkeypatch, tmp_path, source_report=None):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    monkeypatch.setenv("MODEL_BUNDLE_SIGNING_KEY", private.decode())
    monkeypatch.setenv("MODEL_BUNDLE_PUBLIC_KEY", public.decode())
    def verified_source(*, data_dir):
        assert data_dir == tmp_path / "source_data"
        return _fixture_source_report() if source_report is None else source_report
    for name in ("verify_historical_training_gate", "scripts.verify_historical_training_gate"):
        module = importlib.import_module(name)
        monkeypatch.setattr(module, "verify_historical_training_gate", verified_source)
    return private, public


@pytest.fixture(autouse=True)
def _explicit_fixture_trust(monkeypatch, tmp_path):
    _install_fixture_trust(monkeypatch, tmp_path)


def _source(root: Path, horizon: int = 7) -> Path:
    directory = root / "source"
    directory.mkdir(exist_ok=True)
    dates = pd.date_range("2023-01-01", periods=500)
    frame = pd.DataFrame({
        "feature": np.arange(500, dtype=float), "straddle_pct": [.2] * 500,
        "hist_move_med_4q": [.15] * 500,
        "target": np.tile(np.linspace(.01, .10, 100), 5),
        "__symbol": [f"S{i % 20}" for i in range(500)],
        "__earnings_date": dates,
        "__snapshot_date": dates - pd.Timedelta(days=horizon),
        "__label_available_at": dates + pd.Timedelta(days=1),
        "__pre_price_date": dates - pd.Timedelta(days=1), "__post_price_date": dates,
        "__target_protocol": "quantiv.session-reaction.v2",
    })
    for index in range(7):
        frame[f"extra_feature_{index}"] = np.arange(500, dtype=float) + index
    frame.to_parquet(directory / f"training_T{horizon}.parquet", index=False)
    (directory / f"metadata_T{horizon}.json").write_text(json.dumps({
        "feature_protocol": "quantiv.earnings-causal.v2",
        "target_protocol": "quantiv.session-reaction.v2", "horizon": horizon,
    }))
    return directory


def test_prepare_preserves_source_and_purges_by_first_test_snapshot(tmp_path):
    engine = _engine()
    source = _source(tmp_path)
    original = (source / "training_T7.parquet").read_bytes()
    path, reservation = engine.prepare_historical_admission(
        source_training_dir=source, source_data_dir=tmp_path / "source_data", reservation_registry=tmp_path / "registry", training_dir=tmp_path / "development",
        staging_dir=tmp_path / "sealed", horizons=[7], test_days=220,
        min_development_rows=100, as_of="2025-01-01",
    )
    assert path.is_file()
    assert (source / "training_T7.parquet").read_bytes() == original
    development = pd.read_parquet(tmp_path / "development/training_T7.parquet")
    row = reservation["horizons"]["7"]
    assert len(development) == row["development_rows"]
    assert pd.to_datetime(development["__label_available_at"]).max() < pd.Timestamp(row["first_test_snapshot"])
    assert reservation["protocol"]["historical_model_family_exposure_status"] == "not_established"
    from ml.pipeline_validation import validate_training_artifacts
    validation = validate_training_artifacts(tmp_path / "development", horizons=[7],
        min_rows=100, min_symbols=20, min_history_days=100)
    assert validation["status"] == "passed", validation["issues"]


def test_all_requested_leads_share_calendar_and_earliest_snapshot_label_cutoff(tmp_path):
    engine = _engine()
    source = _source(tmp_path, horizon=1)
    _source(tmp_path, horizon=25)
    long_path = source / "training_T25.parquet"
    pd.read_parquet(long_path).iloc[:-10].to_parquet(long_path, index=False)
    originals = {horizon: (source / f"training_T{horizon}.parquet").read_bytes() for horizon in (1, 25)}
    _, reservation = engine.prepare_historical_admission(source_training_dir=source,
        source_data_dir=tmp_path / "source_data", reservation_registry=tmp_path / "registry",
        training_dir=tmp_path / "dev", staging_dir=tmp_path / "sealed", horizons=[1, 25],
        min_development_rows=100, as_of="2025-01-01")
    test_frames = {h: pd.read_parquet(tmp_path / f"sealed/final_test_T{h}.parquet") for h in (1, 25)}
    global_snapshot = min(pd.to_datetime(frame["__snapshot_date"]).min() for frame in test_frames.values())
    for horizon in (1, 25):
        development = pd.read_parquet(tmp_path / f"dev/training_T{horizon}.parquet")
        assert pd.to_datetime(development["__label_available_at"]).max() < global_snapshot
        assert test_frames[horizon]["__earnings_date"].min() == test_frames[1]["__earnings_date"].min()
        assert (source / f"training_T{horizon}.parquet").read_bytes() == originals[horizon]
    assert reservation["global_temporal_boundary"]["first_test_snapshot"] == global_snapshot.isoformat()


def test_re_signed_reservation_cannot_relax_global_cutoff_past_longest_lead(tmp_path, monkeypatch):
    from ml.model_bundle import _sign

    engine = _engine()
    _, reservation_path, _, private, public = _candidate(tmp_path, monkeypatch,
        horizons=(1, 25), selected_horizons=(1,))
    reservation = json.loads(reservation_path.read_text())
    relaxed = (pd.Timestamp(reservation["global_temporal_boundary"]["first_test_snapshot"])
               + pd.Timedelta(days=1)).isoformat()
    reservation["global_temporal_boundary"].update(first_test_snapshot=relaxed, selection_label_cutoff=relaxed)
    for row in reservation["horizons"].values():
        row["selection_label_cutoff"] = relaxed
    core = {key: value for key, value in reservation.items() if key not in {"signature", "reservation_id"}}
    forged = _sign({**core, "reservation_id": engine._identity(core)}, private)
    with pytest.raises(ValueError, match="global"):
        engine._verify_reservation(forged, public_key=public)


def _predictions(rows=300):
    actual = np.linspace(.01, .10, rows)
    return pd.DataFrame({
        "event_id": [f"S{i % 20}|{i}" for i in range(rows)],
        "issuer": [f"S{i % 20}" for i in range(rows)],
        "earnings_date": pd.date_range("2025-01-01", periods=rows),
        "actual": actual, "point": actual, "baseline": [.20] * rows,
        **{f"q{q:02d}": [float(np.quantile(actual, q / 100))] * rows for q in (10, 25, 50, 75, 90)},
        **{f"raw_q{q:02d}": [float(np.quantile(actual, q / 100))] * rows for q in (10, 25, 50, 75, 90)},
    })


def test_cell_requires_all_quantiles_and_clustered_baseline_superiority():
    engine = _engine()
    frame = _predictions()
    report = engine.assess_historical_cell(frame, multiplicity=2)
    assert report["status"] == "passed"
    assert report["mae_difference_95_ci"]["conservative_envelope"][1] < 0
    assert report["familywise_mae_difference_ci"]["conservative_envelope"][1] < 0
    assert len(report["blocks"]) == 3
    assert all(set(block["pinball"]) == {"q10", "q25", "q50", "q75", "q90"}
               and "coverage_80" in block["coverage"] for block in report["blocks"])
    frame["q90"] = .03
    assert engine.assess_historical_cell(frame, multiplicity=2)["status"] == "failed"


def test_cell_requires_200_unique_events_and_matched_baselines():
    engine = _engine()
    frame = _predictions()
    frame.loc[150:, "baseline"] = np.nan
    assert engine.assess_historical_cell(frame)["status"] == "insufficient_data"
    frame = _predictions()
    frame["event_id"] = "repeated"
    with pytest.raises(ValueError, match="duplicate"):
        engine.assess_historical_cell(frame)


def test_raw_quantile_corruption_cannot_hide_after_rearrangement():
    engine = _engine()
    frame = _predictions()
    frame.loc[:100, "raw_q10"] = -.01
    report = engine.assess_historical_cell(frame)
    assert report["status"] == "failed"
    assert any("negative" in issue for issue in report["issues"])


def test_losing_chronological_block_cannot_hide_in_aggregate():
    engine = _engine()
    frame = _predictions()
    frame.loc[200:, "point"] = 1.
    report = engine.assess_historical_cell(frame)
    assert report["status"] == "failed"
    assert any("block" in issue for issue in report["issues"])


def test_sparse_losing_third_block_cannot_be_admitted_on_aggregate_strength():
    engine = _engine()
    frame = _predictions(600)
    days = np.concatenate([np.linspace(0, 59, 288), np.linspace(60, 119, 283), np.linspace(120, 179, 29)]).astype(int)
    frame["earnings_date"] = pd.Timestamp("2025-01-01") + pd.to_timedelta(days, unit="D")
    frame.loc[571:, "point"] = .5
    report = engine.assess_historical_cell(frame)
    assert [block["baseline_rows"] for block in report["blocks"]] == [288, 283, 29]
    assert report["status"] in {"insufficient_data", "failed"}
    assert any("block 3" in issue and "30" in issue for issue in report["issues"])


def test_preparation_rejects_label_features_and_wrong_lead_time(tmp_path):
    engine = _engine()
    source = _source(tmp_path)
    path = source / "training_T7.parquet"
    frame = pd.read_parquet(path)
    frame["future_move"] = frame["target"]
    frame.to_parquet(path, index=False)
    with pytest.raises(ValueError, match="label|future"):
        engine.prepare_historical_admission(source_training_dir=source, source_data_dir=tmp_path / "source_data", reservation_registry=tmp_path / "registry",
            training_dir=tmp_path / "dev", staging_dir=tmp_path / "sealed",
            horizons=[7], min_development_rows=100, as_of="2025-01-01")


def test_preparation_rejects_target_protocol_sidecar_mismatch(tmp_path):
    engine = _engine()
    source = _source(tmp_path)
    path = source / "training_T7.parquet"
    frame = pd.read_parquet(path)
    frame["__target_protocol"] = "quantiv.generic-reaction.v1"
    frame.to_parquet(path, index=False)
    with pytest.raises(ValueError, match="target|protocol"):
        engine.prepare_historical_admission(source_training_dir=source, source_data_dir=tmp_path / "source_data", reservation_registry=tmp_path / "registry",
            training_dir=tmp_path / "dev", staging_dir=tmp_path / "sealed",
            horizons=[7], min_development_rows=100, as_of="2025-01-01")


def _candidate(tmp_path, monkeypatch, horizons=(7,), selected_horizons=None, source_report=None):
    import lightgbm as lgb
    from ml.evidence_receipt import build_evidence_receipt
    from ml.model_artifact import save_native_model
    from ml.model_bundle import create_signed_bundle
    from ml.training_split import training_row_digest

    private, public = _install_fixture_trust(monkeypatch, tmp_path, source_report)
    engine = _engine()
    for horizon in horizons:
        source = _source(tmp_path, horizon=horizon)
        source_path = source / f"training_T{horizon}.parquet"
        full = pd.read_parquet(source_path)
        full["target"] = np.tile(np.linspace(.01, .10, 20), 25)
        full["feature"] = full["target"]
        if horizon == 14:
            full["straddle_pct"] = full["target"]
        full.to_parquet(source_path, index=False)
    source_admission = tmp_path / "historical_admission.json"
    source_admission.write_text(json.dumps(_fixture_source_report() if source_report is None else source_report))
    reservation_path, reservation = engine.prepare_historical_admission(
        source_training_dir=source, source_data_dir=tmp_path / "source_data", reservation_registry=tmp_path / "registry", training_dir=tmp_path / "dev",
        staging_dir=tmp_path / "sealed", horizons=horizons, test_days=220,
        min_development_rows=100, as_of="2025-01-01", source_admission_report=source_admission)
    models = tmp_path / "models"
    models.mkdir()
    features = ["feature", "straddle_pct", "hist_move_med_4q"]
    params = {"n_estimators": 100, "num_leaves": 7, "min_child_samples": 2,
              "n_jobs": 1, "verbosity": -1, "random_state": 42}
    for horizon in horizons:
        dev = pd.read_parquet(tmp_path / f"dev/training_T{horizon}.parquet")
        point = lgb.LGBMRegressor(objective="regression_l1", **params).fit(dev[features], dev["target"])
        save_native_model(point, models / f"lgbm_T{horizon}.txt")
        for q in engine.QUANTILES:
            quantile = lgb.LGBMRegressor(objective="quantile", alpha=q / 100,
                n_estimators=1, learning_rate=.001, num_leaves=2, min_child_samples=2,
                verbosity=-1, n_jobs=1).fit(dev[features], dev["target"])
            save_native_model(quantile, models / f"lgbm_T{horizon}_q{q:02d}.txt")
        exposure = pd.to_datetime(dev["__label_available_at"]).max().isoformat()
        meta = {"feature_cols": features, "feature_protocol": "quantiv.earnings-causal.v2",
                "target_protocol": "quantiv.session-reaction.v2", "horizon": horizon,
                "supported_cohorts": ["strict_options", "optionless"],
                "final_fit": {"rows": len(dev), "row_digest": training_row_digest(dev),
                              "label_available_end": exposure},
                "selection_exposure": {"through_date": exposure, "row_digest": training_row_digest(dev)}}
        (models / f"metadata_T{horizon}.json").write_text(json.dumps(meta))
    selected_horizons = horizons if selected_horizons is None else selected_horizons
    report = {"status": "passed", "issues": [], "stages": {"models": {"horizons": list(selected_horizons)}}}
    receipt = build_evidence_receipt(report, scope="models", repo_root=tmp_path,
        data_dir=tmp_path, training_dir=tmp_path / "dev", models_dir=models,
        forecast_path=None, horizons=selected_horizons)
    report["evidence_receipt"] = receipt
    report_path, receipt_path = tmp_path / "report.json", tmp_path / "model_receipt.json"
    report_path.write_text(json.dumps(report))
    receipt_path.write_text(json.dumps(receipt))
    bundle, _ = create_signed_bundle(models, tmp_path / "bundles", receipt_path=receipt_path,
        validation_report_path=report_path, source_revision="test", horizons=selected_horizons, private_key=private)
    engine.claim_historical_candidate(bundle, reservation_path, tmp_path / "registry", private_key=private, public_key=public)
    return bundle, reservation_path, receipt, private, public


def test_exact_native_candidate_passes_before_promotion_and_only_admits_passing_cells(tmp_path, monkeypatch):
    engine = _engine()
    bundle, reservation, model_receipt, private, public = _candidate(tmp_path, monkeypatch)
    path, receipt = engine.evaluate_historical_admission(bundle, reservation, model_receipt,
        tmp_path / "dev", tmp_path / "evidence", private_key=private, public_key=public, reservation_registry=tmp_path / "registry")
    assert path.is_file()
    assert receipt["status"] == "passed"
    assert receipt["source_admission"]["report"]["release_id"] == "a" * 64
    assert receipt["admitted_cohorts"] == {"7": ["strict_options"]}
    assert receipt["horizons"]["7"]["cohorts"]["optionless"]["status"] == "insufficient_data"
    verified = engine.verify_historical_admission_receipt(receipt, bundle_dir=bundle,
        evidence_root=path.parent, public_key=public)
    assert verified["bundle_id"] == bundle.name
    assert engine.verify_historical_admission_receipt(receipt,
        bundle_manifest=json.loads((bundle / "manifest.json").read_text()), public_key=public)["admitted_cohorts"] == {"7": ["strict_options"]}
    prediction = path.parent / "predictions_T7.parquet"
    prediction.write_bytes(prediction.read_bytes() + b"altered")
    with pytest.raises(RuntimeError, match="evidence|digest"):
        engine.verify_historical_admission_receipt(receipt, bundle_dir=bundle,
            evidence_root=path.parent, public_key=public)


def test_all_history_refit_cannot_admit_itself(tmp_path, monkeypatch):
    engine = _engine()
    bundle, reservation, model_receipt, private, public = _candidate(tmp_path, monkeypatch)
    dev_path = tmp_path / "dev/training_T7.parquet"
    pd.read_parquet(tmp_path / "source/training_T7.parquet").to_parquet(dev_path, index=False)
    with pytest.raises((ValueError, RuntimeError), match="development|training|fit"):
        engine.evaluate_historical_admission(bundle, reservation, model_receipt,
            tmp_path / "dev", tmp_path / "evidence", private_key=private, public_key=public, reservation_registry=tmp_path / "registry")


def test_signed_receipt_cannot_claim_passed_metrics_with_bad_quantile_coverage(tmp_path, monkeypatch):
    from ml.model_bundle import _sign

    engine = _engine()
    bundle, reservation, model_receipt, private, public = _candidate(tmp_path, monkeypatch)
    _, receipt = engine.evaluate_historical_admission(bundle, reservation, model_receipt,
        tmp_path / "dev", tmp_path / "evidence", private_key=private, public_key=public, reservation_registry=tmp_path / "registry")
    receipt["horizons"]["7"]["cohorts"]["strict_options"]["coverage"]["q90_coverage"] = .5
    core = {key: value for key, value in receipt.items() if key not in {"signature", "admission_id"}}
    forged = _sign({**core, "admission_id": engine._identity(core)}, private)
    with pytest.raises(RuntimeError, match="metric|coverage|qualif"):
        engine.verify_historical_admission_receipt(forged, bundle_dir=bundle, public_key=public)


@pytest.mark.parametrize("replay", [False, True])
def test_signed_receipt_requires_every_block_to_meet_minimum_rows(tmp_path, monkeypatch, replay):
    from ml.model_bundle import _sign

    engine = _engine()
    bundle, reservation, model_receipt, private, public = _candidate(tmp_path, monkeypatch)
    _, receipt = engine.evaluate_historical_admission(bundle, reservation, model_receipt,
        tmp_path / "dev", tmp_path / "evidence", private_key=private, public_key=public,
        reservation_registry=tmp_path / "registry")
    block = receipt["horizons"]["7"]["cohorts"]["strict_options"]["blocks"][2]
    block.update(baseline_rows=29, status="low_sample")
    block.pop("mae_ratio", None)
    core = {key: value for key, value in receipt.items() if key not in {"signature", "admission_id"}}
    forged = _sign({**core, "admission_id": engine._identity(core)}, private)
    with pytest.raises(RuntimeError, match="metric|qualif|replay"):
        engine.verify_historical_admission_receipt(forged, bundle_dir=bundle, public_key=public,
            evidence_root=tmp_path / "evidence" if replay else None)


def test_same_native_recipe_can_reverify_after_packaging_revision_change(tmp_path, monkeypatch):
    from ml.model_bundle import _bundle_core, _sign

    engine = _engine()
    bundle, reservation, model_receipt, private, public = _candidate(tmp_path, monkeypatch)
    engine.evaluate_historical_admission(bundle, reservation, model_receipt,
        tmp_path / "dev", tmp_path / "first", private_key=private, public_key=public, reservation_registry=tmp_path / "registry")
    manifest = json.loads((bundle / "manifest.json").read_text())
    different = _bundle_core(artifacts=manifest["artifacts"], receipt_id=manifest["receipt_id"],
        horizons=manifest["horizons"], source_revision="different-attempt", created_at=manifest["created_at"])
    import shutil
    second = bundle.parent / different["bundle_id"]
    shutil.copytree(bundle, second)
    (second / "manifest.json").write_text(json.dumps(_sign(different, private)))
    _, replay = engine.evaluate_historical_admission(second, reservation, model_receipt,
        tmp_path / "dev", tmp_path / "second", private_key=private, public_key=public, reservation_registry=tmp_path / "registry")
    assert replay["admitted_cohorts"] == {"7": ["strict_options"]}


def test_only_passing_horizon_artifacts_enter_derived_serving_bundle(tmp_path, monkeypatch):
    from ml.model_bundle import create_historically_admitted_bundle, verify_bundle_dir

    engine = _engine()
    source, reservation, model_receipt, private, public = _candidate(tmp_path, monkeypatch, horizons=(7, 14))
    _, receipt = engine.evaluate_historical_admission(source, reservation, model_receipt,
        tmp_path / "dev", tmp_path / "evidence", private_key=private, public_key=public, reservation_registry=tmp_path / "registry")
    assert receipt["admitted_cohorts"] == {"7": ["strict_options"]}
    assert receipt["horizons"]["14"]["cohorts"]["strict_options"]["status"] == "failed"
    derived, _ = create_historically_admitted_bundle(source, tmp_path / "bundles",
        admission_receipt=receipt, source_revision="admitted", private_key=private)
    serving = verify_bundle_dir(derived, public_key=public)
    assert serving["horizons"] == [7]
    assert len(serving["artifacts"]) == 7
    assert all("T14" not in artifact["name"] for artifact in serving["artifacts"])
    assert all((derived / artifact["name"]).read_bytes() == (source / artifact["name"]).read_bytes()
               for artifact in serving["artifacts"])


def test_development_selected_head_subset_can_use_a_broader_pretest_reservation(tmp_path, monkeypatch):
    engine = _engine()
    source, reservation, model_receipt, private, public = _candidate(tmp_path, monkeypatch,
        horizons=(7, 14), selected_horizons=(7,))
    _, receipt = engine.evaluate_historical_admission(source, reservation, model_receipt,
        tmp_path / "dev", tmp_path / "evidence", private_key=private, public_key=public, reservation_registry=tmp_path / "registry")
    assert receipt["pretest_head_selection"] == {
        "requested_horizons": [7, 14], "selected_horizons": [7],
        "basis": "development_model_validation_and_walk_forward_only", "test_labels_used": False,
    }
    assert engine.verify_historical_admission_receipt(receipt, bundle_dir=source,
        evidence_root=tmp_path / "evidence", public_key=public)["admitted_cohorts"] == {"7": ["strict_options"]}


def test_selected_head_fit_exposure_and_replay_use_unselected_long_lead_boundary(tmp_path, monkeypatch):
    engine = _engine()
    source, reservation_path, model_receipt, private, public = _candidate(tmp_path, monkeypatch,
        horizons=(1, 25), selected_horizons=(1,))
    reservation = json.loads(reservation_path.read_text())
    global_cutoff = pd.Timestamp(reservation["global_temporal_boundary"]["first_test_snapshot"])
    metadata = json.loads((source / "metadata_T1.json").read_text())
    metadata["selection_exposure"]["through_date"] = (global_cutoff + pd.Timedelta(days=1)).isoformat()
    assert pd.Timestamp(metadata["selection_exposure"]["through_date"]) < pd.Timestamp(reservation["horizons"]["1"]["first_test_snapshot"])
    with pytest.raises(ValueError, match="selection exposure|global|snapshot"):
        engine._verify_fit(metadata, pd.read_parquet(tmp_path / "dev/training_T1.parquet"),
            pd.read_parquet(tmp_path / "sealed/final_test_T1.parquet"), reservation["horizons"]["1"], 1,
            global_cutoff=global_cutoff)
    _, receipt = engine.evaluate_historical_admission(source, reservation_path, model_receipt,
        tmp_path / "dev", tmp_path / "evidence", private_key=private, public_key=public,
        reservation_registry=tmp_path / "registry")
    unselected_development = tmp_path / "evidence/development_T25.parquet"
    assert unselected_development.is_file()
    assert receipt["global_temporal_boundary"] == reservation["global_temporal_boundary"]
    engine.verify_historical_admission_receipt(receipt, bundle_dir=source,
        evidence_root=tmp_path / "evidence", public_key=public)
    unselected_development.unlink()
    with pytest.raises((RuntimeError, ValueError), match="evidence|digest"):
        engine.verify_historical_admission_receipt(receipt, bundle_dir=source,
            evidence_root=tmp_path / "evidence", public_key=public)


def test_unsigned_reservation_rehash_cannot_authenticate_changed_test_targets(tmp_path):
    engine = _engine()
    source = _source(tmp_path)
    _, reservation = engine.prepare_historical_admission(source_training_dir=source, source_data_dir=tmp_path / "source_data", reservation_registry=tmp_path / "registry",
        training_dir=tmp_path / "dev", staging_dir=tmp_path / "sealed", horizons=[7],
        min_development_rows=100, as_of="2025-01-01")
    reservation.pop("signature", None)
    reservation["horizons"]["7"]["test_sha256"] = "a" * 64
    core = {key: value for key, value in reservation.items() if key != "reservation_id"}
    reservation["reservation_id"] = engine._identity(core)
    with pytest.raises(RuntimeError, match="sign|authentic"):
        engine._verify_reservation(reservation)


def test_event_timestamp_variants_cannot_duplicate_calendar_day_identity(tmp_path):
    engine = _engine()
    source = _source(tmp_path)
    frame = pd.read_parquet(source / "training_T7.parquet")
    duplicate = frame.iloc[[0]].copy()
    for column in ("__earnings_date", "__snapshot_date"):
        duplicate[column] += pd.Timedelta(hours=1)
    frame = pd.concat([frame, duplicate], ignore_index=True)
    metadata = json.loads((source / "metadata_T7.json").read_text())
    with pytest.raises(ValueError, match="duplicate|identity"):
        engine._validate_frame(frame, metadata, 7)


def test_stable_panel_identity_rejects_fresh_directory_and_asof_reuse(tmp_path):
    engine = _engine()
    source = _source(tmp_path)
    _, first = engine.prepare_historical_admission(source_training_dir=source,
        source_data_dir=tmp_path / "source_data", reservation_registry=tmp_path / "registry",
        training_dir=tmp_path / "dev", staging_dir=tmp_path / "sealed", horizons=[7],
        min_development_rows=100, as_of="2025-01-01")
    _, second = engine.prepare_historical_admission(source_training_dir=source,
        source_data_dir=tmp_path / "source_data", reservation_registry=tmp_path / "other_registry",
        training_dir=tmp_path / "other_dev", staging_dir=tmp_path / "other_sealed", horizons=[7],
        min_development_rows=100, as_of="2026-01-01")
    assert first["panel_id"] == second["panel_id"]
    with pytest.raises(ValueError, match="durable|reservation"):
        engine.prepare_historical_admission(source_training_dir=source,
            source_data_dir=tmp_path / "source_data", reservation_registry=tmp_path / "registry",
            training_dir=tmp_path / "third_dev", staging_dir=tmp_path / "third_sealed", horizons=[7],
            min_development_rows=100, as_of="2026-01-01")


def test_changed_native_candidate_cannot_claim_previously_seen_event_panel(tmp_path, monkeypatch):
    import hashlib
    import shutil
    from ml.model_bundle import _bundle_core, _sign

    engine = _engine()
    bundle, reservation, _, private, public = _candidate(tmp_path, monkeypatch)
    signed = json.loads((bundle / "manifest.json").read_text())
    artifact = next(item for item in signed["artifacts"] if item["name"] == "lgbm_T7.txt")
    changed = (bundle / artifact["name"]).read_bytes() + b"\n"
    artifact.update(bytes=len(changed), sha256=hashlib.sha256(changed).hexdigest())
    core = _bundle_core(artifacts=signed["artifacts"], receipt_id=signed["receipt_id"],
        horizons=signed["horizons"], source_revision="different", created_at=signed["created_at"])
    second = bundle.parent / core["bundle_id"]
    shutil.copytree(bundle, second)
    (second / artifact["name"]).write_bytes(changed)
    (second / "manifest.json").write_text(json.dumps(_sign(core, private)))
    with pytest.raises(ValueError, match="already claimed|different candidate"):
        engine.claim_historical_candidate(second, reservation, tmp_path / "registry", private_key=private, public_key=public)
