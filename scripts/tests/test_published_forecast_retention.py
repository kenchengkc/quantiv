"""Retention requires authenticated published values, then current source gates."""
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

import duckdb
import pandas as pd
import pytest

from ml.evidence_receipt import build_evidence_receipt, publish_evidence_receipt
from ml.model_artifact import sha256_file
from scripts.frontend_release import build_release, materialize_release, write_deployment_pointer
from scripts import published_forecast_retention as retention


NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)


def row(symbol="AAA"):
    return {"act_symbol": symbol, "earnings_date": pd.Timestamp("2026-10-27"),
            "timing": "bmo", "snapshot_date": pd.Timestamp("2026-10-23"),
            "model_horizon": 4, "model_bundle_id": "champion", "spot_price": 10.,
            "em_math_pct": None, "__cohort": "optionless", "em_ml_pct": .023456789,
            "em_ml_abs": .23456789, "p10": .01, "p25": .015, "p50": .023,
            "p75": .03, "p90": .04, "scored_at": "2026-10-25T09:00:00+00:00",
            "forecast_id": f"forecast-{symbol}", "feature_hash": "original-hash",
            "feature_vector": '{"spot":10}', "freeze_eligible": True}


def projected(raw):
    return {"ticker": raw["act_symbol"], "earnings_date": "2026-10-27", "timing": "bmo",
            "display_forecast_method": "ml", "forecast_id": raw["forecast_id"],
            "display_forecast_pct": round(raw["em_ml_pct"], 6), "display_forecast_as_of": "2026-10-23",
            "forecast_model_bundle_id": raw["model_bundle_id"], "ml_snapshot_date": "2026-10-23",
            "forecast_scored_at": raw["scored_at"], "model_horizon": 4,
            "forecast_feature_hash": raw["feature_hash"],
            **{key: round(raw[key], 6) for key in ("em_ml_pct", "em_ml_abs", "p10", "p25", "p50", "p75", "p90")}}


@pytest.fixture
def proof(tmp_path):
    data = tmp_path / "data"
    source = tmp_path / "source"
    source.mkdir()
    eligible = data / "forecasts/eligible/forecasts_2026-10-25.parquet"
    eligible.parent.mkdir(parents=True)
    frame = pd.DataFrame([row(), row("UNPUBLISHED")])
    frame.to_parquet(eligible, index=False)
    report = {"status": "passed", "stages": {"forecasts": {"rows": 2}}}
    report["evidence_receipt"] = build_evidence_receipt(
        report, scope="forecasts", repo_root=tmp_path, data_dir=data,
        training_dir=tmp_path / "training", models_dir=data / "models", forecast_path=eligible, horizons=[4],
    )
    receipt, _ = publish_evidence_receipt(report, receipt_dir=data / "forecasts/receipts",
                                          scope="forecasts", forecast_path=eligible)
    snapshot = data / "forecasts/published_snapshots" / f"{sha256_file(eligible)}.parquet"
    snapshot.parent.mkdir()
    shutil.copyfile(eligible, snapshot)
    (source / "evidence").mkdir()
    (source / "evidence/forecast.json").write_text(json.dumps({
        "receipt_id": report["evidence_receipt"]["receipt_id"], "receipt_file": receipt.name,
    }))
    (source / "screener.json").write_text(json.dumps({"events": [projected(row())]}))
    release, public, pointer = tmp_path / "release", tmp_path / "public", tmp_path / "deployment.json"

    def publish():
        build_release(source, release)
        write_deployment_pointer(release, pointer)
        materialize_release(release, public)
        retention.capture_deployed_publication(
            data_dir=data, public_dir=public, deployment_pointer=pointer,
            fetcher=lambda name: (public / name).read_bytes(),
        )

    publish()
    return {"data_dir": data, "public_dir": public, "deployment_pointer": pointer}, frame, source, snapshot, receipt, publish


def test_only_actually_displayed_rows_are_loaded_with_original_precision(proof):
    args, frame, *_ = proof
    actual = retention.load_published_forecasts(**args)
    pd.testing.assert_frame_equal(actual.reset_index(drop=True), frame.iloc[:1].reset_index(drop=True))


@pytest.mark.parametrize("target", ["snapshot", "receipt", "manifest", "public"])
def test_tampered_proof_is_rejected(proof, target):
    args, _, _, snapshot, receipt, _ = proof
    path = {"snapshot": snapshot, "receipt": receipt,
            "manifest": args["public_dir"] / "frontend-release-manifest.json",
            "public": args["public_dir"] / "screener.json"}[target]
    path.write_bytes(path.read_bytes() + b" ")
    if target == "receipt":
        content = json.loads(path.read_text())
        content["quality"]["status"] = "failed"
        path.write_text(json.dumps(content))
    with pytest.raises((ValueError, RuntimeError)):
        retention.load_published_forecasts(**args)


@pytest.mark.parametrize("field,value", [("timing", "amc"), ("em_ml_pct", .99),
                                        ("model_horizon", 5), ("ml_snapshot_date", "2026-10-22"),
                                        ("display_forecast_pct", .99),
                                        ("display_forecast_as_of", "2026-10-22"), ("ml_status", "unavailable")])
def test_even_authenticated_public_projection_must_match_full_row(proof, field, value):
    args, _, source, _, _, publish = proof
    public_row = projected(row())
    public_row[field] = value
    (source / "screener.json").write_text(json.dumps({"events": [public_row]}))
    publish()
    with pytest.raises((ValueError, RuntimeError)):
        retention.load_published_forecasts(**args)


@pytest.fixture
def sources(proof, monkeypatch):
    args, frame, *_ = proof
    bundle = args["data_dir"] / "models/bundles/champion"
    bundle.mkdir(parents=True)
    control = args["data_dir"] / "models/control/champion.json"
    control.parent.mkdir()
    control.write_text('{"champion_bundle_id":"champion"}')
    monkeypatch.setattr(retention, "verify_control_pointer", lambda p: p)
    monkeypatch.setattr(retention, "verify_bundle_dir", lambda p: {"bundle_id": "champion", "horizons": [4]})
    monkeypatch.setattr(retention, "verify_candidate_identity", lambda f, *a, **k: f.copy())
    conn = duckdb.connect()
    conn.execute("CREATE TABLE v_ohlcv AS SELECT 'AAA' act_symbol, DATE '2026-10-23' date, 10.0 AS close")
    conn.execute("CREATE TABLE v_earnings AS SELECT 'AAA' act_symbol, DATE '2026-10-27' date, 'bmo' timing")
    conn.execute("CREATE TABLE v_corporate_action_coverage AS SELECT 'AAA' act_symbol, DATE '2026-01-01' window_start, DATE '2026-10-23' window_end")
    yield {**args, "bundle_dir": bundle, "now": NOW, "conn": conn,
           "source_features": frame.iloc[:1].assign(spot=10.)}, frame.iloc[:1].copy()
    conn.close()


def test_original_fields_survive_and_new_current_row_is_preferred(sources):
    args, prior = sources
    retained = retention.retain_published_forecasts(prior.iloc[:0], **args)
    pd.testing.assert_frame_equal(retained.reset_index(drop=True), prior.reset_index(drop=True))
    current = prior.assign(em_ml_pct=.03, forecast_id="new-current")
    pd.testing.assert_frame_equal(retention.retain_published_forecasts(current, **args), current)


@pytest.mark.parametrize("change", ["session", "unknown", "date", "price", "actions", "champion", "expired", "feature"])
def test_changed_or_expired_inputs_cannot_retain(sources, change):
    args, prior = sources
    conn = args["conn"]
    if change in {"session", "unknown"}:
        conn.execute("UPDATE v_earnings SET timing = ?", ["amc" if change == "session" else "unknown"])
    elif change == "date":
        conn.execute("UPDATE v_earnings SET date=DATE '2026-10-28'")
    elif change == "price":
        conn.execute("UPDATE v_ohlcv SET close=11")
    elif change == "actions":
        conn.execute("DELETE FROM v_corporate_action_coverage")
    elif change == "champion":
        (args["data_dir"] / "models/control/champion.json").write_text('{"champion_bundle_id":"other"}')
    elif change == "feature":
        args["source_features"]["spot"] = 11.
    else:
        args = {**args, "now": datetime(2026, 10, 27, 10, tzinfo=timezone.utc)}
    assert retention.retain_published_forecasts(prior.iloc[:0], **args).empty


def test_missing_snapshot_does_not_prevent_current_scoring(proof):
    args, _, _, snapshot, *_ = proof
    snapshot.unlink()
    current = pd.DataFrame([row()])
    # Proof failure must return before opening model/source controls.
    result = retention.retain_published_forecasts(current, **args, bundle_dir=Path("absent"), conn=None, now=NOW)
    pd.testing.assert_frame_equal(result, current)


def test_git_pin_without_observed_production_publication_cannot_retain(proof):
    args, *_ = proof
    shutil.rmtree(args["data_dir"] / ".forecast_retention/publication")
    with pytest.raises(OSError):
        retention.load_published_forecasts(**args)


def test_production_mismatch_clears_previous_readback_proof(proof):
    args, *_ = proof
    with pytest.raises(ValueError, match="production frontend"):
        retention.capture_deployed_publication(**args, fetcher=lambda name: b"different deployment")
    assert not (args["data_dir"] / ".forecast_retention/publication").exists()


def test_verified_historical_publication_recovers_only_previously_displayed_rows(proof, monkeypatch):
    import historical_forecast_publication as historical
    args, frame, source, _, _, publish = proof
    archived = source.parent / "historical-public"
    shutil.copytree(args["public_dir"], archived)
    pointer = source.parent / "historical-pointer.json"
    shutil.copyfile(args["deployment_pointer"], pointer)
    # The latest deployment legitimately lacks the old forecast. Historical
    # smoke verification is tested separately; it never impersonates readback.
    (source / "screener.json").write_text('{"events":[]}')
    publish()
    assert retention.load_published_forecasts(**args).empty
    monkeypatch.setattr(historical, "historical_publication_paths", lambda _: (archived, pointer))
    actual = retention.load_published_forecasts(**args)
    pd.testing.assert_frame_equal(actual.reset_index(drop=True), frame.iloc[:1].reset_index(drop=True))
    (archived / "screener.json").write_text('{"events":[]}')
    with pytest.raises(ValueError, match="published file has changed"):
        retention.load_published_forecasts(**args)


def test_current_capture_clears_prior_historical_authorization(proof):
    args, *_, publish = proof
    historical = args["data_dir"] / ".forecast_retention/historical"
    historical.mkdir()
    (historical / "verified-smoke.json").write_text('{}')
    publish()
    assert not historical.exists()


@pytest.mark.parametrize("duplicate_current", [False, True])
def test_current_publication_wins_over_historical_rescore_of_same_source(proof, monkeypatch, duplicate_current):
    import historical_forecast_publication as historical
    args, frame, source, snapshot, receipt_path, publish = proof
    archived = source.parent / "historical-public"
    shutil.copytree(args["public_dir"], archived)
    pointer = source.parent / "historical-pointer.json"
    shutil.copyfile(args["deployment_pointer"], pointer)
    newer = frame.copy()
    newer.loc[0, "forecast_id"] = "later-scoring-run"
    newer.loc[0, "scored_at"] = "2026-10-25T10:00:00+00:00"
    if duplicate_current:
        newer.loc[1] = newer.loc[0]
        newer.loc[1, "forecast_id"] = "duplicate-source-different-id"
    # Independently publish a new receipt/snapshot for the same observed input.
    eligible = args["data_dir"] / "forecasts/eligible/forecasts_2026-10-25.parquet"
    newer.to_parquet(eligible, index=False)
    report = {"status": "passed", "stages": {"forecasts": {"rows": len(newer)}}}
    report["evidence_receipt"] = build_evidence_receipt(
        report, scope="forecasts", repo_root=source.parent, data_dir=args["data_dir"],
        training_dir=source.parent / "training", models_dir=args["data_dir"] / "models",
        forecast_path=eligible, horizons=[4],
    )
    receipt, _ = publish_evidence_receipt(report, receipt_dir=args["data_dir"] / "forecasts/receipts",
                                         scope="forecasts", forecast_path=eligible)
    retention.preserve_published_snapshot(eligible, report["evidence_receipt"], forecasts_dir=args["data_dir"] / "forecasts")
    (source / "evidence/forecast.json").write_text(json.dumps({
        "receipt_id": report["evidence_receipt"]["receipt_id"], "receipt_file": receipt.name,
    }))
    (source / "screener.json").write_text(json.dumps({"events": [projected(newer.iloc[0].to_dict())]}))
    publish()
    monkeypatch.setattr(historical, "historical_publication_paths", lambda _: (archived, pointer))
    if duplicate_current:
        with pytest.raises(ValueError, match="duplicate serving keys"):
            retention.load_published_forecasts(**args)
        return
    actual = retention.load_published_forecasts(**args)
    pd.testing.assert_frame_equal(actual.reset_index(drop=True), newer.iloc[:1].reset_index(drop=True))


def test_snapshot_preservation_is_exact_and_collision_safe(proof):
    args, _, _, snapshot, receipt_path, _ = proof
    original = args["data_dir"] / "forecasts/eligible/forecasts_2026-10-25.parquet"
    receipt = json.loads(receipt_path.read_text())
    assert retention.preserve_published_snapshot(original, receipt, forecasts_dir=args["data_dir"] / "forecasts") == snapshot
    snapshot.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="collision"):
        retention.preserve_published_snapshot(original, receipt, forecasts_dir=args["data_dir"] / "forecasts")


def test_new_quote_inputs_do_not_mutate_original_optionless_vector(sources):
    args, prior = sources
    args["source_features"]["atm_iv"] = .6
    args["source_features"]["straddle_pct"] = .04
    retained = retention.retain_published_forecasts(prior.iloc[:0], **args)
    pd.testing.assert_frame_equal(retained.reset_index(drop=True), prior.reset_index(drop=True))


def test_original_session_grace_expires_even_for_later_event(sources, monkeypatch):
    args, prior = sources
    prior = prior.assign(earnings_date=pd.Timestamp("2026-11-02"), model_horizon=10)
    monkeypatch.setattr(retention, "load_published_forecasts", lambda **kwargs: prior)
    args["conn"].execute("UPDATE v_earnings SET date=DATE '2026-11-02'")
    args["source_features"] = prior.assign(spot=10.)
    args["now"] = datetime(2026, 10, 27, 21, tzinfo=timezone.utc)
    assert retention.retain_published_forecasts(prior.iloc[:0], **args).empty


@pytest.mark.parametrize("field", ["atm_iv", "em_math_pct"])
def test_quote_bearing_prior_rows_are_not_retained(sources, monkeypatch, field):
    args, prior = sources
    prior[field] = .05
    monkeypatch.setattr(retention, "load_published_forecasts", lambda **kwargs: prior)
    assert retention.retain_published_forecasts(prior.iloc[:0], **args).empty


def test_retention_precedes_monitoring_and_storage_never_sync_deletes_snapshots():
    import yaml
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((root / ".github/workflows/data-refresh.yml").read_text())
    for name in ("refresh", "recovery"):
        commands = [step.get("run", "") for step in workflow["jobs"][name]["steps"]]
        def locate(value):
            return next(i for i, command in enumerate(commands) if value in command)
        assert locate("published_forecast_retention.py verify-production") < locate("daily_score.py") < locate("model_publication.py monitor")
    push = (root / "scripts/r2_push.sh").read_text()
    pull = (root / "scripts/r2_pull.sh").read_text()
    assert "--exclude '/published_snapshots/**'" in push and "--exclude '/published_snapshots/**'" in pull
    assert '--immutable --fast-list --transfers=4' in push
    steps = workflow["jobs"]["recovery"]["steps"]
    historical = next(step for step in steps if "historical_forecast_publication.py" in step.get("run", ""))
    assert historical["if"] == "inputs.retention_smoke_run_id != ''"
    assert historical["env"]["RETENTION_SMOKE_RUN_ID"] == "${{ inputs.retention_smoke_run_id }}"
    assert workflow["jobs"]["recovery"]["permissions"]["actions"] == "read"
