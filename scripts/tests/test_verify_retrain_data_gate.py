from __future__ import annotations

import json
import hashlib
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import pandas as pd

from scripts.verify_retrain_data_gate import verify_retrain_data_gate
from scripts import verify_retrain_data_gate as gate_script

EASTERN = ZoneInfo("America/New_York")


def test_standalone_scoped_gate_imports_local_ml_without_editable_install(tmp_path):
    import subprocess
    import sys
    import site

    entry = Path(__file__).resolve().parents[1] / "verify_retrain_data_gate.py"
    report = tmp_path / "held.json"
    code = """
import importlib.util, json, runpy, sys
sys.path.extend(json.loads(sys.argv[5]))
assert importlib.util.find_spec('ml') is None
sys.path.insert(0, sys.argv[1])
entry = sys.argv[2]
sys.argv = [entry, '--data-dir', sys.argv[3], '--forecast-path', 'missing.parquet',
            '--allow-hold', '--report', sys.argv[4]]
runpy.run_path(entry, run_name='__main__')
"""
    result = subprocess.run([sys.executable, "-I", "-S", "-c", code,
        str(entry.parent), str(entry), str(tmp_path / "data"), str(report), json.dumps(site.getsitepackages())],
        cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(report.read_text())["status"] == "held"


def _write_options_partition(data_dir: Path, source_date: str) -> None:
    year, month, _ = source_date.split("-")
    path = (
        data_dir
        / "parquet"
        / "options_chain"
        / f"year={year}"
        / f"month={month}"
        / f"{source_date}.parquet"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"test")


def _write_manifest(
    data_dir: Path,
    *,
    source_date: str,
    generated_at: str,
    decision_safe: bool = True,
    critical_codes: tuple[str, ...] = (),
) -> None:
    path = data_dir / "validation" / "data_reconciliation.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "manifest_id": "sha256:test-reconciliation",
                "schema": "quantiv.data-reconciliation.v2",
                "generated_at": generated_at,
                "quality": {
                    "status": "passed" if decision_safe else "failed",
                    "decision_safe": decision_safe,
                    "critical_exceptions": len(critical_codes),
                    "warnings": 0,
                },
                "source_reconciliation": {
                    "status": "passed",
                    "source_date": source_date,
                },
                "quote_quality": {
                    "status": "passed",
                    "source_date": source_date,
                },
                "exceptions": [
                    {
                        "code": code,
                        "severity": "critical",
                        "summary": code,
                    }
                    for code in critical_codes
                ],
            }
        )
    )


def test_accepts_current_decision_safe_friday_release_on_sunday(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_options_partition(data_dir, "2026-09-04")
    _write_manifest(
        data_dir,
        source_date="2026-09-04",
        generated_at="2026-09-06T15:00:00+00:00",
    )

    result = verify_retrain_data_gate(
        data_dir=data_dir,
        now=datetime(2026, 9, 6, 12, 0, tzinfo=EASTERN),
    )

    assert result["status"] == "passed"
    assert result["source_date"] == "2026-09-04"
    assert result["expected_source_date"] == "2026-09-04"


def test_labor_day_does_not_require_a_nonexistent_monday_snapshot(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    _write_options_partition(data_dir, "2026-09-04")
    _write_manifest(
        data_dir,
        source_date="2026-09-04",
        generated_at="2026-09-07T14:00:00+00:00",
    )

    result = verify_retrain_data_gate(
        data_dir=data_dir,
        now=datetime(2026, 9, 7, 13, 0, tzinfo=EASTERN),
    )

    assert result["expected_source_date"] == "2026-09-04"


def test_held_reconciliation_blocks_retraining(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_options_partition(data_dir, "2026-09-04")
    _write_manifest(
        data_dir,
        source_date="2026-09-04",
        generated_at="2026-09-06T15:00:00+00:00",
        decision_safe=False,
        critical_codes=("event_quote_coverage_below_limit",),
    )

    with pytest.raises(RuntimeError, match="held by reconciliation"):
        verify_retrain_data_gate(
            data_dir=data_dir,
            now=datetime(2026, 9, 6, 12, 0, tzinfo=EASTERN),
        )


@pytest.mark.parametrize("activation_flag", [None, True, False])
@pytest.mark.parametrize("hold", ["coverage", "rejection_rate", "count_coverage"])
def test_serving_safe_sparse_release_cannot_activate_models(
    tmp_path: Path, activation_flag: bool | None, hold: str,
) -> None:
    _write_options_partition(tmp_path, "2026-09-04")
    _write_manifest(tmp_path, source_date="2026-09-04",
                    generated_at="2026-09-06T15:00:00+00:00")
    path = tmp_path / "validation/data_reconciliation.json"
    manifest = json.loads(path.read_text())
    if activation_flag is not None:
        manifest["quality"]["model_activation_eligible"] = activation_flag
        manifest["quality"]["model_activation_blocking_codes"] = (
            [] if activation_flag else ["event_quote_coverage_below_limit"]
        )
    if hold == "coverage":
        manifest["event_coverage"] = {"status": "failed"}
    elif hold == "rejection_rate":
        manifest["quote_quality"].update({
            "decision_group_rejection_rate": 0.6,
            "max_decision_group_rejection_rate": 0.5,
        })
    else:
        manifest["event_coverage"] = {
            "status": "passed", "expected_events": 10, "covered_events": 6,
            "minimum_coverage_pct": 0.65,
        }
    path.write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError, match="model activation.*blocked"):
        verify_retrain_data_gate(data_dir=tmp_path,
                                now=datetime(2026, 9, 6, 12, 0, tzinfo=EASTERN))


def test_calendar_recent_but_market_session_stale_release_is_rejected(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    _write_options_partition(data_dir, "2026-09-01")
    _write_manifest(
        data_dir,
        source_date="2026-09-01",
        generated_at="2026-09-06T15:00:00+00:00",
    )

    with pytest.raises(RuntimeError, match="latest completed market session"):
        verify_retrain_data_gate(
            data_dir=data_dir,
            now=datetime(2026, 9, 6, 12, 0, tzinfo=EASTERN),
        )


def test_stale_or_mismatched_reconciliation_evidence_is_rejected(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    _write_options_partition(data_dir, "2026-09-04")
    _write_manifest(
        data_dir,
        source_date="2026-09-03",
        generated_at="2026-09-06T15:00:00+00:00",
    )

    with pytest.raises(RuntimeError, match="does not match the published options release"):
        verify_retrain_data_gate(
            data_dir=data_dir,
            now=datetime(2026, 9, 6, 12, 0, tzinfo=EASTERN),
        )

    _write_manifest(
        data_dir,
        source_date="2026-09-04",
        generated_at="2026-09-04T00:00:00+00:00",
    )
    with pytest.raises(RuntimeError, match="current evidence is required"):
        verify_retrain_data_gate(
            data_dir=data_dir,
            now=datetime(2026, 9, 6, 12, 0, tzinfo=EASTERN),
        )


def test_retrain_r2_pull_restores_and_checks_reconciliation() -> None:
    pull_script = (Path(__file__).resolve().parents[1] / "r2_pull.sh").read_text()
    retrain_block = pull_script.split('if [ "${R2_PULL_EARNINGS:-0}" = "1" ]; then', 1)[1]

    assert 'REMOTE/validation/data_reconciliation.json' in retrain_block
    assert 'verify_retrain_data_gate.py --data-dir "$DATA_DIR"' in retrain_block


@pytest.mark.parametrize("section", ["source_reconciliation", "quote_quality"])
def test_malformed_prospective_section_records_hold_without_blocking_learning(
    tmp_path: Path, monkeypatch, section: str,
) -> None:
    data_dir = tmp_path / "data"
    _write_options_partition(data_dir, "2026-09-30")
    _write_manifest(
        data_dir,
        source_date="2026-09-30",
        generated_at=datetime.now(EASTERN).isoformat(),
    )
    path = data_dir / "validation" / "data_reconciliation.json"
    manifest = json.loads(path.read_text())
    manifest[section] = [{}]
    path.write_text(json.dumps(manifest))
    report = tmp_path / "activation.json"
    monkeypatch.setattr(
        "sys.argv",
        ["verify_retrain_data_gate.py", "--data-dir", str(data_dir),
         "--report", str(report), "--allow-hold"],
    )

    assert gate_script.main() == 0
    result = json.loads(report.read_text())
    assert result["status"] == "held"
    assert "invalid source contract" in result["reason"]
    with pytest.raises(RuntimeError, match="invalid source contract"):
        verify_retrain_data_gate(data_dir=data_dir)


@pytest.fixture
def scoped_release(tmp_path: Path, monkeypatch):
    """Build real immutable source and signed handoff evidence for the gate."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from ml.data_reconciliation import build_reconciliation_manifest
    from ml.evidence_receipt import build_evidence_receipt
    from ml.model_artifact import save_native_model
    from ml.model_bundle import create_signed_bundle, required_artifact_names
    from ml.model_protocol import CAUSAL_FEATURE_PROTOCOL, SESSION_TARGET_PROTOCOL
    from ml.pipeline_validation import FORECAST_REQUIRED_COLUMNS
    import lightgbm as lgb
    from scripts.data_release import build_release
    from scripts.tests.historical_admission_fixtures import (
        publish_action_receipt, write_action_receipt,
    )

    current = datetime(2026, 9, 6, 12, tzinfo=EASTERN)
    data_dir = tmp_path / "data"
    prices = data_dir / "parquet/ohlcv/year=2026/month=09/2026-09-04.parquet"
    prices.parent.mkdir(parents=True)
    pd.DataFrame({"act_symbol": ["TEST"], "date": ["2026-09-04"],
                  "close": [100.]}).to_parquet(prices, index=False)
    # The historical options input can be older than the price input. Actions
    # must nevertheless cover every actual live symbol through its price date.
    actions = write_action_receipt(data_dir, source_date="2026-09-03")
    actions["query_end"] = "2026-09-04"
    publish_action_receipt(data_dir, actions)
    (data_dir / "earnings_calendar.csv").write_text(
        "act_symbol,date,timing\nTEST,2026-09-08,bmo\n")
    build_release(data_dir)

    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(serialization.Encoding.PEM,
                                serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.PEM,
                                          serialization.PublicFormat.SubjectPublicKeyInfo)
    monkeypatch.setenv("MODEL_BUNDLE_PUBLIC_KEY", public.decode())
    models = tmp_path / "validated-models"
    models.mkdir()
    metadata = {"feature_cols": ["atm_iv", "dte", "em_iv_pct", "straddle_pct",
                                  "timing_bmo", "timing_amc"],
                "feature_protocol": CAUSAL_FEATURE_PROTOCOL,
                "target_protocol": SESSION_TARGET_PROTOCOL,
                "supported_cohorts": ["strict_options", "optionless"]}
    inputs = pd.DataFrame({name: [0., 1., 2., 3.] for name in metadata["feature_cols"]})
    for horizon in [4, 5]:
        for name in required_artifact_names([horizon]):
            if name.endswith(".json"):
                (models / name).write_text(json.dumps(metadata))
            else:
                output = next((value for quantile, value in [(10, .04), (25, .05), (50, .07),
                                                             (75, .09), (90, .12)]
                               if name.endswith(f"q{quantile}.txt")), .07)
                native = lgb.train({"objective": "regression", "verbosity": -1, "num_threads": 1},
                                   lgb.Dataset(inputs, label=[output] * len(inputs)), num_boost_round=1)
                save_native_model(native, models / name)
    validation = {"status": "passed", "stages": {"models": {"horizons": [4, 5]}}}
    receipt = build_evidence_receipt(validation, scope="models", repo_root=tmp_path,
                                    data_dir=data_dir, training_dir=tmp_path / "training",
                                    models_dir=models, forecast_path=None, horizons=[4, 5])
    report_path = tmp_path / "models-validation.json"
    receipt_path = tmp_path / "models-receipt.json"
    report_path.write_text(json.dumps({**validation, "evidence_receipt": receipt}))
    receipt_path.write_text(json.dumps(receipt))
    bundle, _ = create_signed_bundle(models, data_dir / "models/bundles",
                                     receipt_path=receipt_path,
                                     validation_report_path=report_path,
                                     source_revision="test-current-data-gate",
                                     horizons=[4, 5], private_key=private)
    row = {column: None for column in FORECAST_REQUIRED_COLUMNS}
    row.update(act_symbol="TEST", earnings_date="2026-09-08", timing="bmo",
               snapshot_date="2026-09-04", model_horizon=4, model_bundle_id=bundle.name,
               spot_price=100., em_ml_pct=.07, em_ml_abs=7., p10=.04, p25=.05,
               p50=.07, p75=.09, p90=.12, scored_at=current.isoformat(),
               feature_protocol=CAUSAL_FEATURE_PROTOCOL,
               target_protocol=SESSION_TARGET_PROTOCOL,
               feature_vector=json.dumps({"atm_iv": None, "dte": None,
                                          "em_iv_pct": None, "straddle_pct": None,
                                          "timing_bmo": 1., "timing_amc": 0.}))
    forecast = data_dir / "forecasts/current.parquet"
    forecast.parent.mkdir()
    pd.DataFrame([row]).to_parquet(forecast, index=False)
    manifest = build_reconciliation_manifest(
        generated_at=current.isoformat(),
        datasets={"options": {"status": "passed", "max_date": "2026-09-03",
                              "lag_days": 1, "max_lag_days": 0},
                  "ohlcv": {"status": "passed", "max_date": "2026-09-04", "rows": 1},
                  "earnings": {"status": "passed", "rows": 1}},
        event_coverage={"status": "failed", "expected_events": 10,
                        "covered_events": 5, "minimum_coverage_pct": .65},
        duplicates={"ohlcv": {"duplicate_rows": 0}}, symbol_mappings={},
        corporate_actions={"status": "passed", "continuity_status": "enforced"},
        pipeline_controls={"quarantine": {"status": "enforced"},
                           "idempotent_replay": {"status": "verified"}},
        quote_quality={"status": "failed", "source_date": "2026-09-03",
                       "errors": ["source session is stale"], "contracts": 100,
                       "same_strike_pairs": 50, "max_decision_group_rejection_rate": .5},
        source_reconciliation={"status": "passed", "source_date": "2026-09-03"},
    )
    manifest_path = data_dir / "validation/data_reconciliation.json"
    manifest_path.parent.mkdir()
    manifest_path.write_text(json.dumps(manifest))
    return {"data_dir": data_dir, "forecast": forecast, "bundle": bundle,
            "prices": prices, "manifest_path": manifest_path, "now": current}


def _scoped_gate(release):
    return verify_retrain_data_gate(data_dir=release["data_dir"],
                                    forecast_path=release["forecast"], now=release["now"])


def _save_scoped_manifest(release, manifest):
    core = {key: value for key, value in manifest.items()
            if key not in {"manifest_id", "generated_at"}}
    manifest["manifest_id"] = "sha256:" + hashlib.sha256(
        json.dumps(core, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    release["manifest_path"].write_text(json.dumps(manifest))


def test_optionless_actual_forecast_uses_current_prices_despite_stale_sparse_options(scoped_release):
    result = _scoped_gate(scoped_release)
    assert result["status"] == "passed"
    assert result["cohorts"] == ["optionless"]
    assert result["snapshot_date"] == "2026-09-04"
    assert result["source_evidence"]["ohlcv"]["source_date"] == "2026-09-04"
    assert result["source_evidence"]["data_release"]["release_id"]
    assert "event_quote_coverage_below_limit" in result["advisory_codes"]
    assert result["forecast_validation"]["rows"] == 1


def test_actual_forecast_admission_accepts_verified_one_session_grace(scoped_release):
    from scripts.data_release import build_release

    store = scoped_release
    prices = pd.read_parquet(store["prices"])
    prices["date"] = "2026-09-03"
    prices.to_parquet(store["prices"], index=False)
    frame = pd.read_parquet(store["forecast"])
    frame["snapshot_date"] = "2026-09-03"
    frame["model_horizon"] = 5
    frame.to_parquet(store["forecast"], index=False)
    manifest = json.loads(store["manifest_path"].read_text())
    manifest["datasets"]["ohlcv"]["max_date"] = "2026-09-03"
    _save_scoped_manifest(store, manifest)
    build_release(store["data_dir"])

    result = _scoped_gate(store)
    assert result["status"] == "passed"
    assert result["snapshot_date"] == result["source_date"] == "2026-09-03"
    assert result["expected_source_date"] == "2026-09-04"
    assert result["source_session_lag"] == 1
    assert result["source_evidence"]["ohlcv"]["source_date"] == "2026-09-03"


def test_optionless_scoping_does_not_change_legacy_global_gate(scoped_release):
    with pytest.raises(RuntimeError, match="held by reconciliation"):
        verify_retrain_data_gate(data_dir=scoped_release["data_dir"], now=scoped_release["now"])


@pytest.mark.parametrize("mutation", ["old_prices", "old_symbol_price", "spot_mismatch"])
def test_optionless_scoping_requires_actual_latest_session_price(scoped_release, mutation):
    from scripts.data_release import build_release
    if mutation == "spot_mismatch":
        frame = pd.read_parquet(scoped_release["forecast"])
        frame["spot_price"] = 101.
        frame["em_ml_abs"] = 7.07
        frame.to_parquet(scoped_release["forecast"], index=False)
    else:
        frame = pd.read_parquet(scoped_release["prices"])
        frame["date"] = "2026-09-03"
        if mutation == "old_symbol_price":
            frame = pd.concat([frame, pd.DataFrame({"act_symbol": ["OTHER"],
                                                   "date": ["2026-09-04"], "close": [50.]})])
        frame.to_parquet(scoped_release["prices"], index=False)
        build_release(scoped_release["data_dir"])
    with pytest.raises(RuntimeError, match="(price|OHLCV)"):
        _scoped_gate(scoped_release)


@pytest.mark.parametrize("mutation", ["price_tamper", "model_tamper", "signature_tamper", "missing_ca"])
def test_optionless_scoping_rejects_tampered_or_missing_integrity_evidence(scoped_release, mutation):
    if mutation == "price_tamper":
        scoped_release["prices"].write_bytes(b"changed after release")
    elif mutation == "model_tamper":
        (scoped_release["bundle"] / "metadata_T4.json").write_text("{}")
    elif mutation == "signature_tamper":
        path = scoped_release["bundle"] / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest["source_revision"] = "changed"
        path.write_text(json.dumps(manifest))
    else:
        (scoped_release["data_dir"] / "control/ingestion/corporate_actions/latest.json").unlink()
    with pytest.raises(RuntimeError):
        _scoped_gate(scoped_release)


@pytest.mark.parametrize("critical", ["ohlcv_stale", "earnings_unavailable", "corporate_action_continuity_failed", "record_quarantine_not_instrumented", "unknown_failure"])
def test_optionless_scoping_cannot_ignore_non_options_critical_issues(scoped_release, critical):
    manifest = json.loads(scoped_release["manifest_path"].read_text())
    manifest["exceptions"].append({"code": critical, "severity": "critical"})
    manifest["quality"]["critical_exceptions"] += 1
    _save_scoped_manifest(scoped_release, manifest)
    with pytest.raises(RuntimeError, match=critical):
        _scoped_gate(scoped_release)


def test_shared_critical_hold_fails_before_any_forecast_file_access(scoped_release):
    manifest = json.loads(scoped_release["manifest_path"].read_text())
    manifest["exceptions"].append({"code": "late_hold", "severity": "critical"})
    manifest["quality"]["critical_exceptions"] += 1
    _save_scoped_manifest(scoped_release, manifest)
    scoped_release["forecast"].unlink()
    with pytest.raises(RuntimeError, match="held by reconciliation: late_hold"):
        _scoped_gate(scoped_release)


@pytest.mark.parametrize("mutation", ["empty", "blank_bundle", "one_blank_bundle", "untrusted_bundle", "old_row", "old_score", "future_score", "partial_quote", "invalid_prediction"])
def test_scoped_gate_validates_every_exact_forecast_row(scoped_release, mutation):
    frame = pd.read_parquet(scoped_release["forecast"])
    if mutation == "empty":
        frame = frame.iloc[:0]
    elif mutation == "blank_bundle":
        frame["model_bundle_id"] = ""
    elif mutation == "one_blank_bundle":
        frame = pd.concat([frame, frame.assign(model_bundle_id=None, act_symbol="OTHER")])
    elif mutation == "untrusted_bundle":
        frame["model_bundle_id"] = "../validated-models"
    elif mutation == "old_row":
        frame["snapshot_date"] = "2026-09-03"
    elif mutation == "old_score":
        frame = pd.concat([frame, frame.assign(scored_at="2026-09-03T20:00:00Z", model_horizon=5,
                                               earnings_date="2026-09-09")])
    elif mutation == "future_score":
        frame["scored_at"] = "2026-09-06T17:00:00Z"
    elif mutation == "partial_quote":
        frame["call_bid"] = 1.
    else:
        frame["p10"] = .5
    frame.to_parquet(scoped_release["forecast"], index=False)
    with pytest.raises(RuntimeError):
        _scoped_gate(scoped_release)


def test_scoped_gate_requires_current_reconciliation_report(scoped_release):
    manifest = json.loads(scoped_release["manifest_path"].read_text())
    manifest["generated_at"] = "2026-09-04T00:00:00Z"
    _save_scoped_manifest(scoped_release, manifest)
    with pytest.raises(RuntimeError, match="current evidence is required"):
        _scoped_gate(scoped_release)


def test_scoped_gate_does_not_trust_passed_dataset_flags_over_actual_prices(scoped_release):
    from scripts.data_release import build_release
    frame = pd.read_parquet(scoped_release["prices"])
    frame["act_symbol"] = "OTHER"
    frame.to_parquet(scoped_release["prices"], index=False)
    build_release(scoped_release["data_dir"])
    with pytest.raises(RuntimeError, match="(price|OHLCV)"):
        _scoped_gate(scoped_release)


def _strict_forecast(release, *, mixed=False, strike=100.):
    import math
    frame = pd.read_parquet(release["forecast"])
    strict = frame.copy()
    strict["atm_iv"] = .5
    for column in ("atm_strike", "call_strike", "put_strike"):
        strict[column] = strike
    for side in ("call", "put"):
        strict[f"{side}_bid"] = 2.9
        strict[f"{side}_ask"] = 3.1
        strict[f"{side}_mid"] = 3.
        strict[f"{side}_relative_spread"] = .2 / 3.
    strict["straddle_bid"], strict["straddle_ask"], strict["straddle_mid"] = 5.8, 6.2, 6.
    strict["straddle_relative_spread"] = .4 / 6.
    strict["quote_timestamp_precision"] = "date"
    strict["market_data_mode"] = "end_of_day"
    strict["quote_quality_status"] = "passed"
    strict["liquidity_tier"] = "tight"
    strict["liquidity_tier_method"] = "quote_spread_proxy"
    strict["em_math_pct"] = 6. / strike
    strict["correction_factor"] = .07 / (6. / strike)
    strict["feature_vector"] = json.dumps({"atm_iv": .5, "dte": 7.,
                                           "em_iv_pct": .5 * math.sqrt(7. / 365.),
                                           "straddle_pct": 6. / strike,
                                           "timing_bmo": 1., "timing_amc": 0.})
    if mixed:
        # Distinct serving key, shared current price and independently verified
        # event identity. Both cohorts must pass their own actual row checks.
        frame["earnings_date"] = "2026-09-09"
        frame["model_horizon"] = 5
        strict = pd.DataFrame([*strict.to_dict("records"), *frame.to_dict("records")])
        with (release["data_dir"] / "earnings_calendar.csv").open("a") as handle:
            handle.write("TEST,2026-09-09,bmo\n")
    strict.to_parquet(release["forecast"], index=False)


def _current_options(release, monkeypatch, *, strike=100.):
    from datetime import date
    from scripts import sync_dolthub
    from scripts.data_release import build_release
    monkeypatch.setenv("DATA_DIR", str(release["data_dir"]))
    day = date(2026, 9, 4)
    frame = pd.DataFrame({"act_symbol": ["TEST", "TEST"], "date": [day, day],
                          "expiration": [date(2026, 9, 11)] * 2,
                          "strike": [strike, strike], "call_put": ["Call", "Put"],
                          "bid": [2.9, 2.9], "ask": [3.1, 3.1], "vol": [.5, .5],
                          "delta": [.5, -.5]})
    root = release["data_dir"] / "parquet/options_chain"
    sync_dolthub.write_date(frame, root)
    # Digest the persisted representation, as production replay checking does.
    path = root / "year=2026/month=09/2026-09-04.parquet"
    ingestion = sync_dolthub._write_ingestion_manifest(day, pd.read_parquet(path), root)
    build_release(release["data_dir"])
    manifest = json.loads(release["manifest_path"].read_text())
    manifest["datasets"]["options"].update(max_date="2026-09-04", lag_days=0)
    manifest["source_reconciliation"] = {
        "status": "passed", "source_date": "2026-09-04", "errors": [],
        "expected_rows": 2, "received_rows": 2, "duckdb_rows": 2,
        "partition_sha256": ingestion["partition_sha256"],
        "content_sha256": ingestion["content_sha256"],
        "replay_equivalence": "verified",
        "source_capabilities": ingestion["source_capabilities"],
    }
    manifest["quote_quality"].update(status="degraded", source_date="2026-09-04", errors=[])
    manifest["exceptions"] = [item for item in manifest["exceptions"]
                              if item["severity"] != "critical"]
    manifest["quality"].update(decision_safe=True, critical_exceptions=0,
                               model_activation_eligible=False,
                               model_activation_blocking_codes=["event_quote_coverage_below_limit"])
    _save_scoped_manifest(release, manifest)
    return ingestion


@pytest.mark.parametrize("mixed", [False, True])
def test_strict_rows_retain_stale_options_activation_hold(scoped_release, mixed):
    _strict_forecast(scoped_release, mixed=mixed)
    with pytest.raises(RuntimeError, match="options_stale"):
        _scoped_gate(scoped_release)


@pytest.mark.parametrize("mixed", [False, True])
def test_actual_strict_rows_with_current_source_can_pass_sparse_aggregate_coverage(scoped_release, monkeypatch, mixed):
    _strict_forecast(scoped_release, mixed=mixed)
    _current_options(scoped_release, monkeypatch)
    result = _scoped_gate(scoped_release)
    assert result["status"] == "passed"
    assert result["source_evidence"]["options"]["source_date"] == "2026-09-04"
    assert result["cohorts"] == (["optionless", "strict_options"] if mixed else ["strict_options"])
    assert "event_quote_coverage_below_limit" in result["advisory_codes"]


@pytest.mark.parametrize("mutation", ["missing_receipt", "receipt_hash", "report_hash", "row_quality", "replay_digest", "source_capabilities"])
def test_strict_scoping_keeps_source_proof_and_row_quality_mandatory(scoped_release, monkeypatch, mutation):
    _strict_forecast(scoped_release)
    _current_options(scoped_release, monkeypatch)
    path = scoped_release["data_dir"] / "control/ingestion/options/2026-09-04.json"
    if mutation == "missing_receipt":
        path.unlink()
    elif mutation in {"receipt_hash", "replay_digest", "source_capabilities"}:
        payload = json.loads(path.read_text())
        if mutation == "source_capabilities":
            payload["source_capabilities"]["option_volume"] = True
        else:
            payload["partition_sha256" if mutation == "receipt_hash" else "persisted_content_sha256"] = "0" * 64
        path.write_text(json.dumps(payload))
    elif mutation == "report_hash":
        manifest = json.loads(scoped_release["manifest_path"].read_text())
        manifest["source_reconciliation"]["partition_sha256"] = "0" * 64
        _save_scoped_manifest(scoped_release, manifest)
    else:
        frame = pd.read_parquet(scoped_release["forecast"])
        frame["call_bid"] = 0.
        frame.to_parquet(scoped_release["forecast"], index=False)
    with pytest.raises(RuntimeError):
        _scoped_gate(scoped_release)


def test_strict_rows_cannot_use_passed_flags_to_hide_actual_stale_options(scoped_release):
    _strict_forecast(scoped_release)
    manifest = json.loads(scoped_release["manifest_path"].read_text())
    manifest["exceptions"] = []
    manifest["quality"].update(decision_safe=True, critical_exceptions=0,
                               model_activation_eligible=True, model_activation_blocking_codes=[])
    manifest["quote_quality"].update(status="passed", errors=[], source_date="2026-09-04")
    manifest["source_reconciliation"].update(status="passed", source_date="2026-09-04")
    _save_scoped_manifest(scoped_release, manifest)
    with pytest.raises(RuntimeError, match="latest completed market session"):
        _scoped_gate(scoped_release)


@pytest.mark.parametrize("mutation", ["outside_universe", "short_window"])
def test_scoped_gate_requires_action_coverage_for_every_live_symbol_snapshot(scoped_release, mutation):
    from scripts.tests.historical_admission_fixtures import publish_action_receipt
    if mutation == "outside_universe":
        frame = pd.read_parquet(scoped_release["forecast"])
        frame["act_symbol"] = "OTHER"
        frame.to_parquet(scoped_release["forecast"], index=False)
    else:
        path = scoped_release["data_dir"] / "control/ingestion/corporate_actions/latest.json"
        receipt = json.loads(path.read_text())
        receipt["query_end"] = "2026-09-03"
        publish_action_receipt(scoped_release["data_dir"], receipt)
    with pytest.raises(RuntimeError, match="corporate-action.*(cover|snapshot)"):
        _scoped_gate(scoped_release)


def test_scoped_gate_cli_records_actual_cohort_and_source_evidence(scoped_release, monkeypatch):
    from scripts import forecast_activation_gate

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return scoped_release["now"].astimezone(tz) if tz else scoped_release["now"]

    monkeypatch.setattr(forecast_activation_gate, "datetime", Clock)
    # The standalone entrypoint resolves this module from scripts on PYTHONPATH.
    import forecast_activation_gate as standalone_gate
    monkeypatch.setattr(standalone_gate, "datetime", Clock)
    report = scoped_release["data_dir"] / "validation/scoped-activation.json"
    monkeypatch.setattr("sys.argv", ["verify_retrain_data_gate.py", "--data-dir",
                                    str(scoped_release["data_dir"]), "--forecast-path",
                                    str(scoped_release["forecast"]), "--report", str(report)])
    assert gate_script.main() == 0
    result = json.loads(report.read_text())
    assert result["status"] == "passed"
    assert result["cohorts"] == ["optionless"]
    assert result["source_evidence"]["ohlcv"]["source_date"] == "2026-09-04"


@pytest.mark.parametrize("mutation", ["quarantine", "price_duplicates", "stale_symbols"])
def test_scoped_gate_reconstructs_non_options_failures_independently_of_passed_flags(scoped_release, mutation):
    manifest = json.loads(scoped_release["manifest_path"].read_text())
    if mutation == "quarantine":
        manifest["pipeline_controls"]["quarantine"]["status"] = "failed"
    elif mutation == "price_duplicates":
        manifest["duplicates"]["ohlcv"]["duplicate_rows"] = 2
    else:
        manifest["symbol_mappings"]["stale_source_symbols"] = ["TEST"]
    _save_scoped_manifest(scoped_release, manifest)
    with pytest.raises(RuntimeError):
        _scoped_gate(scoped_release)


def test_actual_forecast_head_must_match_event_calendar_lead(scoped_release):
    frame = pd.read_parquet(scoped_release["forecast"])
    # T5 is signed and loadable, but Sep 8 minus Sep 4 must be T4.
    frame["model_horizon"] = 5
    frame.to_parquet(scoped_release["forecast"], index=False)
    with pytest.raises(RuntimeError, match="(lead|horizon)"):
        _scoped_gate(scoped_release)


@pytest.mark.parametrize("mutation", ["point", "quantile"])
def test_structurally_valid_predictions_must_replay_from_signed_native_models(scoped_release, mutation):
    frame = pd.read_parquet(scoped_release["forecast"])
    if mutation == "point":
        frame["em_ml_pct"], frame["em_ml_abs"] = .075, 7.5
    else:
        frame["p25"] = .055
    frame.to_parquet(scoped_release["forecast"], index=False)
    with pytest.raises(RuntimeError, match="(replay|prediction)"):
        _scoped_gate(scoped_release)


@pytest.mark.parametrize("mutation", ["quote_legs", "baseline"])
def test_plausible_strict_quote_evidence_must_match_immutable_source(scoped_release, monkeypatch, mutation):
    _strict_forecast(scoped_release)
    _current_options(scoped_release, monkeypatch)
    frame = pd.read_parquet(scoped_release["forecast"])
    frame["em_math_pct"] = .08
    frame["correction_factor"] = .07 / .08
    vector = json.loads(frame.iloc[0]["feature_vector"])
    vector["straddle_pct"] = .08
    frame["feature_vector"] = json.dumps(vector)
    if mutation == "quote_legs":
        for side in ("call", "put"):
            frame[f"{side}_bid"], frame[f"{side}_ask"], frame[f"{side}_mid"] = 3.9, 4.1, 4.
            frame[f"{side}_relative_spread"] = .2 / 4.
        frame["straddle_bid"], frame["straddle_ask"], frame["straddle_mid"] = 7.8, 8.2, 8.
        frame["straddle_relative_spread"] = .4 / 8.
    frame.to_parquet(scoped_release["forecast"], index=False)
    with pytest.raises(RuntimeError, match="(source|straddle)"):
        _scoped_gate(scoped_release)


def test_strict_baseline_retains_canonical_strike_denominator_when_spot_differs(scoped_release, monkeypatch):
    _strict_forecast(scoped_release, strike=99.)
    _current_options(scoped_release, monkeypatch, strike=99.)
    assert _scoped_gate(scoped_release)["status"] == "passed"


@pytest.mark.parametrize("field,value", [("call_volume", 10000.), ("put_open_interest", 1000.),
                                         ("call_quote_timestamp", "2026-09-04T19:00:00Z")])
def test_strict_quote_cannot_claim_evidence_absent_from_immutable_source(scoped_release, monkeypatch, field, value):
    _strict_forecast(scoped_release)
    _current_options(scoped_release, monkeypatch)
    frame = pd.read_parquet(scoped_release["forecast"])
    frame[field] = value
    frame.to_parquet(scoped_release["forecast"], index=False)
    with pytest.raises(RuntimeError, match="source"):
        _scoped_gate(scoped_release)


@pytest.mark.parametrize("mutation", ["missing_delta", "invalid_delta", "not_atm", "illiquid", "invalid_iv", "out_of_policy_dte"])
def test_strict_pair_rejected_by_canonical_source_policy_cannot_claim_passed_status(scoped_release, monkeypatch, mutation):
    from datetime import date
    import math
    from scripts import sync_dolthub
    from scripts.data_release import build_release

    _strict_forecast(scoped_release)
    _current_options(scoped_release, monkeypatch)
    path = scoped_release["data_dir"] / "parquet/options_chain/year=2026/month=09/2026-09-04.parquet"
    options = pd.read_parquet(path)
    forecast = pd.read_parquet(scoped_release["forecast"])
    if mutation == "missing_delta":
        options["delta"] = None
    elif mutation == "invalid_delta":
        options["delta"] = [2., -2.]
    elif mutation == "not_atm":
        options["delta"] = [.99, -.99]
    elif mutation == "illiquid":
        options["option_volume"], options["open_interest"] = 0, 0
        for side in ("call", "put"):
            forecast[f"{side}_volume"], forecast[f"{side}_open_interest"] = 0., 0.
        forecast["liquidity_tier_method"] = "spread_volume_open_interest"
    elif mutation == "invalid_iv":
        # The displayed average remains plausible while both raw legs fail.
        options["vol"] = [6., -5.]
    else:
        options["expiration"] = date(2026, 11, 8)
        vector = json.loads(forecast.iloc[0]["feature_vector"])
        vector.update(dte=65., em_iv_pct=.5 * math.sqrt(65. / 365.))
        forecast["feature_vector"] = json.dumps(vector)
    forecast.to_parquet(scoped_release["forecast"], index=False)
    sync_dolthub.write_date(options, scoped_release["data_dir"] / "parquet/options_chain")
    ingestion = sync_dolthub._write_ingestion_manifest(
        date(2026, 9, 4), pd.read_parquet(path), scoped_release["data_dir"] / "parquet/options_chain")
    build_release(scoped_release["data_dir"])
    manifest = json.loads(scoped_release["manifest_path"].read_text())
    manifest["source_reconciliation"].update(
        partition_sha256=ingestion["partition_sha256"], content_sha256=ingestion["content_sha256"])
    _save_scoped_manifest(scoped_release, manifest)
    with pytest.raises(RuntimeError, match="(eligible|policy|source)"):
        _scoped_gate(scoped_release)
