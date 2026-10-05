from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

from model_control_plane import drift_reference_cohort, evaluate_outcomes  # noqa: E402
from ml.model_bundle import verify_outcome_receipt  # noqa: E402


def test_insufficient_outcomes_are_retained_and_signed(tmp_path, monkeypatch) -> None:
    private = Ed25519PrivateKey.generate()
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    monkeypatch.setenv("MODEL_BUNDLE_SIGNING_KEY", private_pem.decode())
    monkeypatch.setenv("MODEL_BUNDLE_PUBLIC_KEY", public_pem.decode())
    models_root = tmp_path / "models"
    report_path = tmp_path / "validation" / "model_outcomes.json"
    args = argparse.Namespace(
        models_root=models_root,
        training_dir=tmp_path / "training",
        min_common_rows=30,
        report=report_path,
        monitoring_report=None,
        history=None,
        history_limit=52,
    )

    assert evaluate_outcomes(args) == 0

    monitoring = models_root / "monitoring"
    latest = monitoring / "latest_outcomes.json"
    history = monitoring / "outcome_history.json"
    receipt_path = monitoring / "latest_outcomes.receipt.json"
    payload = json.loads(latest.read_text())
    retained = json.loads(history.read_text())
    receipt = json.loads(receipt_path.read_text())
    assert payload["status"] == "insufficient_data"
    assert retained["evaluations"][0]["status"] == "insufficient_data"
    assert report_path.read_bytes() == latest.read_bytes()
    verify_outcome_receipt(
        receipt,
        report_path=latest,
        history_path=history,
        public_key=public_pem,
    )


def test_drift_reference_cohort_uses_only_strict_option_rows() -> None:
    import pandas as pd

    forecasts = pd.DataFrame(
        {
            "model_horizon": [7, 7, 14, 14],
            "em_math_pct": [0.08, None, 0.06, float("nan")],
            "feature_vector": ["{}", "{}", "{}", "{}"],
        }
    )

    cohort, diagnostics = drift_reference_cohort(forecasts)

    assert cohort.index.tolist() == [0, 2]
    assert diagnostics["rows"] == 4
    assert diagnostics["strict_option_rows"] == 2
    assert diagnostics["optionless_rows"] == 2
    assert diagnostics["optionless_share"] == 0.5
    assert diagnostics["by_horizon"] == {
        "7": {"rows": 2, "strict_option_rows": 1, "optionless_rows": 1},
        "14": {"rows": 2, "strict_option_rows": 1, "optionless_rows": 1},
    }


def test_register_keeps_frozen_challenger_and_champion_pointer(tmp_path, monkeypatch):
    import shutil
    from test_model_control import _keys, _bundle
    import model_control_plane_impl as plane
    from ml.model_bundle import create_signed_control_pointer, verify_registry

    private, public = _keys(tmp_path)
    monkeypatch.setenv("MODEL_BUNDLE_PUBLIC_KEY", str(public))
    monkeypatch.setenv("MODEL_BUNDLE_SIGNING_KEY", private.decode())
    root = tmp_path / "models"
    control = root / "control"
    control.mkdir(parents=True)
    champion = _bundle(tmp_path / "champion", private, target_shift=0.0)
    candidates = [
        _bundle(
            tmp_path / name,
            private,
            target_shift=shift,
            metadata_overrides={
                "feature_protocol": "quantiv.earnings-causal.v2",
                "target_protocol": "quantiv.session-reaction.v2",
            },
        )
        for name, shift in [("first", 0.001), ("second", 0.002)]
    ]
    for b in candidates:
        _archive_fixture(root, b, private)
    for b in [champion, *candidates]:
        shutil.copytree(b, root / "bundles" / b.name)
    pointer = control / "champion.json"
    pointer.write_text(
        json.dumps(
            create_signed_control_pointer(
                bundle_id=champion.name, previous_bundle_id=None, decision={}
            )
        )
    )
    pointer_bytes = pointer.read_bytes()
    for b in candidates:
        record = tmp_path / "candidate.json"
        record.write_text(
            json.dumps(
                {"bundle_id": b.name, "bundle_dir": str(root / "bundles" / b.name)}
            )
        )
        args = argparse.Namespace(
            candidate_manifest=record,
            models_root=root,
            report=tmp_path / "register.json",
        )
        assert plane.register_candidate(args) == 0
    report = json.loads(args.report.read_text())
    registry = verify_registry(json.loads((control / "registry.json").read_text()))
    assert registry["challenger_bundle_id"] == candidates[0].name
    assert report["candidate_bundle_id"] == candidates[1].name
    assert report["evaluation_bundle_dir"] == str(root / "bundles" / candidates[0].name)
    assert pointer.read_bytes() == pointer_bytes


def test_held_gate_retains_candidate_without_loading_forecasts(tmp_path, monkeypatch):
    import shutil
    from test_model_control import _keys, _bundle
    import model_control_plane_impl as plane
    from ml.model_bundle import create_signed_control_pointer

    private, public = _keys(tmp_path)
    monkeypatch.setenv("MODEL_BUNDLE_PUBLIC_KEY", str(public))
    monkeypatch.setenv("MODEL_BUNDLE_SIGNING_KEY", private.decode())
    champion = _bundle(tmp_path / "champion", private, target_shift=0.0)
    candidate = _bundle(tmp_path / "candidate", private, target_shift=0.001)
    root = tmp_path / "models"
    (root / "control").mkdir(parents=True)
    for b in [champion, candidate]:
        shutil.copytree(b, root / "bundles" / b.name)
    pointer = root / "control/champion.json"
    pointer.write_text(
        json.dumps(
            create_signed_control_pointer(
                bundle_id=champion.name, previous_bundle_id=None, decision={}
            )
        )
    )
    original = pointer.read_bytes()
    record = tmp_path / "candidate.json"
    record.write_text(
        json.dumps(
            {
                "bundle_id": candidate.name,
                "bundle_dir": str(root / "bundles" / candidate.name),
            }
        )
    )
    gate = tmp_path / "gate.json"
    gate.write_text(
        json.dumps({"status": "held", "reason": "current reconciliation is held"})
    )
    args = argparse.Namespace(
        candidate_manifest=record,
        models_root=root,
        activation_gate_report=gate,
        candidate_forecast=None,
        champion_forecast=None,
        report=tmp_path / "decision.json",
        training_dir=tmp_path / "training",
        production_forecast_dir=tmp_path / "forecasts",
    )
    assert plane.decide(args) == 0
    result = json.loads(args.report.read_text())
    assert result["action"] == "retain_champion"
    assert result["promoted"] is False
    assert result["challenger_bundle_id"] == candidate.name
    assert pointer.read_bytes() == original


def test_passed_gate_report_does_not_bypass_current_release_check(tmp_path):
    import model_control_plane_impl as plane

    gate = tmp_path / "passed.json"
    gate.write_text(json.dumps({"status": "passed"}))
    result = plane._activation_gate(
        argparse.Namespace(activation_gate_report=gate, models_root=tmp_path / "models")
    )
    assert result["status"] == "held"
    assert "unavailable" in result["reason"]


def _archive_fixture(root, bundle, private):
    import shutil
    import pandas as pd
    from pytest import MonkeyPatch
    import model_promotion_receipts as receipts
    from ml.candidate_evidence import sign_candidate_evidence
    from scripts.data_release import build_release
    from scripts.tests.historical_admission_fixtures import write_action_receipt
    from scripts.verify_historical_training_gate import verify_historical_training_gate

    evidence = root / "candidates" / bundle.name
    original = bundle.parent.parent
    shutil.copytree(original / "training", evidence / "training")
    shutil.copyfile(original / "receipt.json", evidence / "model_validation_receipt.json")
    fixture_repo = root.parent
    for relative in (
        "apps/ml/feature_engineering.py", "apps/ml/ml/causal_features.py",
        "apps/ml/ml/corporate_actions.py", "apps/ml/ml/model_protocol.py",
        "config/market_sessions.json",
    ):
        destination = fixture_repo / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_ROOT / relative, destination)
        source = evidence / "source" / destination.name
        source.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(destination, source)
    inputs = fixture_repo / "fixture_inputs" / bundle.name
    prices = inputs / "parquet/ohlcv/history.parquet"
    prices.parent.mkdir(parents=True)
    pd.DataFrame({"act_symbol": ["TEST"], "date": ["2026-09-01"], "close": [100.0]}).to_parquet(prices)
    write_action_receipt(inputs)
    build_release(inputs)
    events = pd.DataFrame({"act_symbol": ["TEST"], "date": ["2026-09-01"], "timing": ["bmo"]})
    events.to_csv(inputs / "earnings_calendar.csv", index=False)
    events.to_parquet(inputs / "earnings_calendar.parquet", index=False)
    admission = verify_historical_training_gate(data_dir=inputs)
    (evidence / "historical_admission.json").write_text(json.dumps(admission))
    (evidence / "inputs").mkdir()
    for name in ("earnings_calendar.csv", "earnings_calendar.parquet"):
        shutil.copyfile(inputs / name, evidence / "inputs" / name)
    candidate = evidence / "candidate.json"
    candidate.write_text(json.dumps({"bundle_id": bundle.name, "bundle_dir": str(bundle)}))
    with MonkeyPatch.context() as patch:
        patch.setattr(receipts, "REPO_ROOT", fixture_repo)
        patch.setattr(receipts, "FEATURE_ENGINEERING_PATH", fixture_repo / "apps/ml/feature_engineering.py")
        receipts.build_promotion_receipts(
            candidate, training_dir=evidence / "training",
            temporal_path=evidence / "promotion/temporal_integrity.json",
            statistical_path=evidence / "promotion/statistical_selection.json",
        )
    sign_candidate_evidence(evidence, bundle.name, private_key=private)


def test_register_migrates_legacy_challenger_without_champion_change(
    tmp_path, monkeypatch
):
    import shutil
    from test_model_control import _keys, _bundle
    import model_control_plane_impl as plane
    from ml.model_bundle import create_signed_control_pointer, create_signed_registry

    private, public = _keys(tmp_path)
    monkeypatch.setenv("MODEL_BUNDLE_PUBLIC_KEY", str(public))
    monkeypatch.setenv("MODEL_BUNDLE_SIGNING_KEY", private.decode())
    legacy = _bundle(tmp_path / "legacy", private, target_shift=0.0)
    repaired = _bundle(
        tmp_path / "repaired",
        private,
        target_shift=0.01,
        metadata_overrides={
            "feature_protocol": "quantiv.earnings-causal.v2",
            "target_protocol": "quantiv.session-reaction.v2",
        },
    )
    root = tmp_path / "models"
    (root / "control").mkdir(parents=True)
    for b in [legacy, repaired]:
        shutil.copytree(b, root / "bundles" / b.name)
    _archive_fixture(root, repaired, private)
    pointer = root / "control/champion.json"
    pointer.write_text(
        json.dumps(
            create_signed_control_pointer(
                bundle_id=legacy.name, previous_bundle_id=None, decision={}
            )
        )
    )
    original = pointer.read_bytes()
    (root / "control/registry.json").write_text(
        json.dumps(
            create_signed_registry(
                champion_bundle_id=legacy.name,
                challenger_bundle_id=legacy.name,
                previous_bundle_id=None,
                decision={},
            )
        )
    )
    record = tmp_path / "candidate.json"
    record.write_text(
        json.dumps(
            {
                "bundle_id": repaired.name,
                "bundle_dir": str(root / "bundles" / repaired.name),
            }
        )
    )
    args = argparse.Namespace(
        candidate_manifest=record, models_root=root, report=tmp_path / "register.json"
    )
    assert plane.register_candidate(args) == 0
    report = json.loads(args.report.read_text())
    assert report["challenger_bundle_id"] == repaired.name
    assert report["challenger_migration_reason"]
    assert pointer.read_bytes() == original


def test_prospective_promotion_rejects_unsigned_prediction_ledger(tmp_path):
    import pandas as pd
    import model_control_plane_impl as plane

    monitoring = tmp_path / "models" / "monitoring"
    monitoring.mkdir(parents=True)
    pd.DataFrame({"prediction": [0.05]}).to_parquet(
        monitoring / "prediction_ledger.parquet"
    )
    args = argparse.Namespace(
        models_root=tmp_path / "models", training_dir=tmp_path / "training"
    )
    result = plane._prospective_comparison(
        args, tmp_path, tmp_path, [1], candidate_id="candidate", champion_id="champion"
    )
    assert result["status"] == "insufficient_data"
    assert "signed" in result["issues"][0]


def test_prospective_promotion_verifies_exact_prediction_ledger_bytes(
    tmp_path, monkeypatch
):
    import pandas as pd
    import pytest
    import model_control_plane_impl as plane
    from test_model_control import _keys
    from ml.model_bundle import create_signed_monitor_receipt, ModelBundleError

    private, public = _keys(tmp_path)
    monkeypatch.setenv("MODEL_BUNDLE_SIGNING_KEY", private.decode())
    monkeypatch.setenv("MODEL_BUNDLE_PUBLIC_KEY", str(public))
    monitoring = tmp_path / "models" / "monitoring"
    monitoring.mkdir(parents=True)
    ledger = monitoring / "prediction_ledger.parquet"
    pd.DataFrame({"prediction": [0.05]}).to_parquet(ledger)
    report = monitoring / "latest_monitoring.json"
    report.write_text("{}")
    receipt = create_signed_monitor_receipt(
        ledger_path=ledger, report_path=report, snapshot_date="2026-10-02"
    )
    (monitoring / "latest_monitoring.receipt.json").write_text(json.dumps(receipt))
    pd.DataFrame({"prediction": [0.09]}).to_parquet(ledger)
    args = argparse.Namespace(
        models_root=tmp_path / "models", training_dir=tmp_path / "training"
    )
    with pytest.raises(ModelBundleError, match="ledger"):
        plane._prospective_comparison(
            args,
            tmp_path,
            tmp_path,
            [1],
            candidate_id="candidate",
            champion_id="champion",
        )


def test_wrapper_resolves_frozen_bundle_before_validating_receipts(
    tmp_path, monkeypatch
):
    import shutil
    import model_control_plane as entry
    import model_control_plane_impl as plane
    from test_model_control import _keys, _bundle

    private, public = _keys(tmp_path)
    monkeypatch.setenv("MODEL_BUNDLE_PUBLIC_KEY", str(public))
    monkeypatch.setenv("MODEL_BUNDLE_SIGNING_KEY", private.decode())
    root = tmp_path / "models"
    candidates = [
        _bundle(
            tmp_path / str(index),
            private,
            target_shift=index / 1000,
            metadata_overrides={
                "feature_protocol": "quantiv.earnings-causal.v2",
                "target_protocol": "quantiv.session-reaction.v2",
            },
        )
        for index in (1, 2)
    ]
    for bundle in candidates:
        shutil.copytree(bundle, root / "bundles" / bundle.name)
        _archive_fixture(root, bundle, private)
        candidate = tmp_path / "candidate.json"
        candidate.write_text(
            json.dumps(
                {
                    "bundle_id": bundle.name,
                    "bundle_dir": str(root / "bundles" / bundle.name),
                }
            )
        )
        plane.register_candidate(
            argparse.Namespace(
                candidate_manifest=candidate,
                models_root=root,
                report=tmp_path / "register.json",
            )
        )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "model_control_plane.py",
            "decide",
            "--models-root",
            str(root),
            "--candidate-manifest",
            str(candidate),
            "--report",
            str(tmp_path / "decision.json"),
        ],
    )
    entry._resolve_registered_candidate()
    resolved = json.loads(Path(entry._option("--candidate-manifest")).read_text())
    assert resolved["bundle_id"] == candidates[0].name
    assert entry._option("--archived-evidence-dir") == str(
        root / "candidates" / candidates[0].name
    )


def test_decide_rechecks_forecast_quality_before_any_activation(tmp_path, monkeypatch):
    import pandas as pd
    import model_control_plane_impl as plane
    from test_model_control import _keys, _bundle

    private, public = _keys(tmp_path)
    monkeypatch.setenv("MODEL_BUNDLE_PUBLIC_KEY", str(public))
    monkeypatch.setenv("MODEL_BUNDLE_SIGNING_KEY", private.decode())
    candidate = _bundle(tmp_path / "candidate", private, target_shift=0.0)
    record = tmp_path / "candidate.json"
    record.write_text(
        json.dumps({"bundle_id": candidate.name, "bundle_dir": str(candidate)})
    )
    forecast = tmp_path / "forecast.parquet"
    pd.DataFrame({"model_bundle_id": [candidate.name]}).to_parquet(forecast)
    monkeypatch.setattr(plane, "_activation_gate", lambda _: {"status": "passed"})
    args = argparse.Namespace(
        candidate_manifest=record,
        models_root=tmp_path / "models",
        candidate_forecast=forecast,
        report=tmp_path / "decision.json",
        training_dir=tmp_path / "training",
        production_forecast_dir=tmp_path / "forecasts",
    )
    assert plane.decide(args) == 0
    result = json.loads(args.report.read_text())
    assert result["promoted"] is False
    assert result["candidate_forecast_validation"]["status"] == "failed"
    assert not (args.models_root / "control/champion.json").exists()


def test_register_requires_complete_archive_for_a_new_current_protocol_candidate(
    tmp_path, monkeypatch
):
    import pytest
    import model_control_plane_impl as plane
    from test_model_control import _keys, _bundle

    private, public = _keys(tmp_path)
    monkeypatch.setenv("MODEL_BUNDLE_PUBLIC_KEY", str(public))
    monkeypatch.setenv("MODEL_BUNDLE_SIGNING_KEY", private.decode())
    candidate = _bundle(
        tmp_path / "candidate",
        private,
        target_shift=0.0,
        metadata_overrides={
            "feature_protocol": "quantiv.earnings-causal.v2",
            "target_protocol": "quantiv.session-reaction.v2",
        },
    )
    record = tmp_path / "candidate.json"
    record.write_text(
        json.dumps({"bundle_id": candidate.name, "bundle_dir": str(candidate)})
    )
    args = argparse.Namespace(
        candidate_manifest=record,
        models_root=tmp_path / "models",
        report=tmp_path / "report.json",
    )
    with pytest.raises(ValueError, match="archived evidence"):
        plane.register_candidate(args)
    assert not (args.models_root / "control/registry.json").exists()


def test_signed_prospective_pairs_use_current_mature_labels_and_require_200(
    tmp_path, monkeypatch
):
    import numpy as np
    import pandas as pd
    import model_control_plane_impl as plane
    from test_model_control import _keys
    from ml.model_bundle import create_signed_monitor_receipt

    private, public = _keys(tmp_path)
    monkeypatch.setenv("MODEL_BUNDLE_PUBLIC_KEY", str(public))
    monkeypatch.setenv("MODEL_BUNDLE_SIGNING_KEY", private.decode())
    metadata = {
        "selection_exposure": {"through_date": "2026-06-01"},
        "feature_protocol": "quantiv.earnings-causal.v2",
        "target_protocol": "quantiv.session-reaction.v2",
    }
    candidate, champion = tmp_path / "candidate", tmp_path / "champion"
    for bundle, payload in (
        (candidate, metadata),
        (champion, {"selection_exposure": {"through_date": "2026-06-01"}}),
    ):
        bundle.mkdir()
        (bundle / "metadata_T1.json").write_text(json.dumps(payload))
    training = tmp_path / "current_training"
    training.mkdir()
    (training / "metadata_T1.json").write_text(json.dumps(metadata))
    actual = np.linspace(0.001, 0.10, 200)
    rows = pd.DataFrame(
        {
            "act_symbol": [f"S{i}" for i in range(200)],
            "earnings_date": pd.Timestamp("2026-07-02"),
            "snapshot_date": pd.Timestamp("2026-07-01"),
            "model_horizon": 1,
                "recorded_at": "2026-07-01T20:00:00Z",
                "timing": "bmo",
                "prediction": actual,
            "em_math_pct": 0.2,
            **{f"p{q:02d}": q / 1000 for q in (10, 25, 50, 75, 90)},
        }
    )
    pd.DataFrame(
        {
            "__symbol": rows.act_symbol,
            "__earnings_date": rows.earnings_date,
            "__label_available_at": pd.Timestamp("2026-07-03"),
            "target": actual,
        }
    ).to_parquet(training / "training_T1.parquet")
    monitoring = tmp_path / "models" / "monitoring"
    monitoring.mkdir(parents=True)
    report_path = monitoring / "latest_monitoring.json"
    report_path.write_text("{}")
    ledger_path = monitoring / "prediction_ledger.parquet"
    for count, expected in ((199, "insufficient_data"), (200, "passed")):
        pd.concat(
            [
                rows.iloc[:count].assign(
                    bundle_id="candidate", feature_protocol="quantiv.earnings-causal.v2"
                ),
                rows.iloc[:count].assign(
                    bundle_id="champion",
                    feature_protocol="quantiv.earnings-legacy.v1",
                    prediction=actual[:count] + 0.001,
                ),
            ]
        ).to_parquet(ledger_path)
        receipt = create_signed_monitor_receipt(
            ledger_path=ledger_path, report_path=report_path, snapshot_date="2026-07-01"
        )
        (monitoring / "latest_monitoring.receipt.json").write_text(json.dumps(receipt))
        result = plane._prospective_comparison(
            argparse.Namespace(models_root=tmp_path / "models", training_dir=training),
            candidate,
            champion,
            [1],
            candidate_id="candidate",
            champion_id="champion",
        )
        assert result["status"] == expected
        assert result["horizons"]["1"]["rows"] == count


def _monitor_fixture(tmp_path, monkeypatch):
    import shutil
    from datetime import datetime, timezone
    import pandas as pd
    from market_sessions import latest_completed_us_market_session
    from ml.model_bundle import create_signed_control_pointer
    from test_model_control import _keys, _bundle

    private, public = _keys(tmp_path)
    monkeypatch.setenv("MODEL_BUNDLE_PUBLIC_KEY", str(public))
    monkeypatch.setenv("MODEL_BUNDLE_SIGNING_KEY", private.decode())
    bundle = _bundle(tmp_path / "champion", private, target_shift=0.0)
    root = tmp_path / "models"
    shutil.copytree(bundle, root / "bundles" / bundle.name)
    (root / "control").mkdir()
    (root / "control/champion.json").write_text(
        json.dumps(
            create_signed_control_pointer(
                bundle_id=bundle.name, previous_bundle_id=None, decision={}
            )
        )
    )
    forecast_dir = tmp_path / "forecasts"
    forecast_dir.mkdir()
    forecast = forecast_dir / "forecasts.parquet"
    pd.DataFrame(
        {
            "act_symbol": [f"S{i}" for i in range(200)],
            "earnings_date": pd.Timestamp.now().normalize() + pd.Timedelta(days=30),
            "snapshot_date": latest_completed_us_market_session(
                datetime.now(timezone.utc)
            ),
            "model_horizon": 1,
            "model_bundle_id": bundle.name,
            "em_math_pct": 0.08,
            "em_ml_pct": 0.05,
            "timing": "bmo",
            "scored_at": datetime.now(timezone.utc).isoformat(),
            "feature_vector": json.dumps({"feature": 0.5, "straddle_pct": 0.08}),
            **{f"p{q:02d}": q / 1000 for q in (10, 25, 50, 75, 90)},
        }
    ).to_parquet(forecast)
    return argparse.Namespace(
        models_root=root, forecast_dir=forecast_dir, forecast_path=forecast
    )


def test_monitor_cannot_resign_a_tampered_previously_signed_ledger(
    tmp_path, monkeypatch
):
    import pandas as pd
    import pytest
    import model_control_plane_impl as plane
    from ml.model_bundle import create_signed_monitor_receipt, ModelBundleError

    args = _monitor_fixture(tmp_path, monkeypatch)
    monitoring = args.models_root / "monitoring"
    monitoring.mkdir()
    ledger = monitoring / "prediction_ledger.parquet"
    pd.DataFrame({"prediction": [0.05]}).to_parquet(ledger)
    report = monitoring / "latest_monitoring.json"
    report.write_text("{}")
    receipt = monitoring / "latest_monitoring.receipt.json"
    receipt.write_text(
        json.dumps(
            create_signed_monitor_receipt(
                ledger_path=ledger, report_path=report, snapshot_date="2026-10-02"
            )
        )
    )
    original_receipt = receipt.read_bytes()
    pd.DataFrame({"prediction": [0.09]}).to_parquet(ledger)
    tampered_bytes = ledger.read_bytes()
    with pytest.raises(ModelBundleError, match="ledger"):
        plane.monitor(args)
    assert ledger.read_bytes() == tampered_bytes
    assert receipt.read_bytes() == original_receipt


def test_monitor_quarantines_unsigned_legacy_ledger_before_recording_new_pairs(
    tmp_path, monkeypatch
):
    import pandas as pd
    import model_control_plane_impl as plane
    from ml.model_bundle import verify_monitor_receipt

    args = _monitor_fixture(tmp_path, monkeypatch)
    monitoring = args.models_root / "monitoring"
    monitoring.mkdir()
    ledger = monitoring / "prediction_ledger.parquet"
    pd.DataFrame({"prediction": [0.05]}).to_parquet(ledger)
    original = ledger.read_bytes()
    assert plane.monitor(args) == 0
    payload = json.loads((monitoring / "latest_monitoring.json").read_text())
    assert payload["ledger_migration"]["action"] == "quarantine_unsigned_legacy_ledger"
    assert Path(payload["ledger_migration"]["path"]).read_bytes() == original
    assert len(pd.read_parquet(ledger)) == 200
    verify_monitor_receipt(
        json.loads((monitoring / "latest_monitoring.receipt.json").read_text()),
        ledger_path=ledger,
        report_path=monitoring / "latest_monitoring.json",
    )


def _seed_signed_drift_history(args, count):
    import pandas as pd
    from market_sessions import latest_completed_us_market_session
    from ml.model_protocol import LEGACY_FEATURE_PROTOCOL, LEGACY_TARGET_PROTOCOL
    from ml.model_bundle import create_signed_monitor_receipt

    prior = pd.read_parquet(args.forecast_path).iloc[:count].copy()
    stamp = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=7)
    snapshot = latest_completed_us_market_session(stamp.to_pydatetime())
    recorded = pd.Timestamp(snapshot).tz_localize("UTC") + pd.Timedelta(hours=21, minutes=15)
    prior["snapshot_date"] = snapshot
    prior["scored_at"] = prior["recorded_at"] = recorded.isoformat()
    prior["bundle_id"] = prior["model_bundle_id"]
    prior["feature_protocol"] = LEGACY_FEATURE_PROTOCOL
    prior["target_protocol"] = LEGACY_TARGET_PROTOCOL
    prior["__cohort"] = "strict_options"
    monitoring = args.models_root / "monitoring"
    monitoring.mkdir(exist_ok=True)
    ledger = monitoring / "prediction_ledger.parquet"
    prior.to_parquet(ledger, index=False)
    report = monitoring / "latest_monitoring.json"
    report.write_text("{}")
    (monitoring / "latest_monitoring.receipt.json").write_text(json.dumps(
        create_signed_monitor_receipt(ledger_path=ledger, report_path=report, snapshot_date=str(snapshot))
    ))


def test_monitor_uses_verified_observed_vectors_for_sparse_nightly_cohort(tmp_path, monkeypatch):
    import pandas as pd
    import model_control_plane_impl as plane
    from ml.model_artifact import sha256_file

    args = _monitor_fixture(tmp_path, monkeypatch)
    _seed_signed_drift_history(args, 80)
    current = pd.read_parquet(args.forecast_path).iloc[80:100]
    current.to_parquet(args.forecast_path, index=False)
    assert plane.monitor(args) == 0
    report = json.loads((args.models_root / "monitoring/latest_monitoring.json").read_text())
    assert report["feature_drift"]["rolling_evidence"]["historical_rows"] == 80
    assert report["feature_drift"]["horizons"]["1"]["rows"] == 100
    assert report["forecast_sha256"] == sha256_file(args.forecast_path)
    ledger = pd.read_parquet(args.models_root / "monitoring/prediction_ledger.parquet")
    assert ledger.loc[ledger["snapshot_date"].astype(str) == str(current["snapshot_date"].iloc[0]), "feature_vector"].tolist() == current["feature_vector"].tolist()


def test_rolling_assessment_requires_a_verified_history_receipt_in_decisions(tmp_path, monkeypatch):
    import pandas as pd
    import model_control_plane_impl as plane

    args = _monitor_fixture(tmp_path, monkeypatch)
    _seed_signed_drift_history(args, 80)
    current = pd.read_parquet(args.forecast_path).iloc[80:100]
    bundle_id = current["model_bundle_id"].iloc[0]
    kwargs = dict(bundle_id=bundle_id, models_root=args.models_root, horizons=[1])
    bundle = args.models_root / "bundles" / bundle_id
    assert plane._rolling_drift_assessment(current, bundle, **kwargs)["status"] in {"passed", "warning"}
    (args.models_root / "monitoring/latest_monitoring.receipt.json").unlink()
    report = plane._rolling_drift_assessment(current, bundle, **kwargs)
    assert report["status"] == "insufficient_data"
    assert report["rolling_evidence"]["history_authentication"] == "unavailable"


def test_frozen_candidate_archive_requires_relational_binding_even_after_resigning(tmp_path, monkeypatch):
    import shutil
    import model_control_plane_impl as plane
    from test_model_control import _keys, _bundle
    from ml.candidate_evidence import sign_candidate_evidence

    private, public = _keys(tmp_path)
    monkeypatch.setenv("MODEL_BUNDLE_PUBLIC_KEY", str(public))
    candidate = _bundle(tmp_path / "candidate", private, target_shift=0.)
    root = tmp_path / "models"
    shutil.copytree(candidate, root / "bundles" / candidate.name)
    _archive_fixture(root, candidate, private)
    archive = root / "candidates" / candidate.name
    (archive / "training/training_T1.parquet").write_bytes(b"different fitting rows")
    sign_candidate_evidence(archive, candidate.name, private_key=private)
    assert plane._has_complete_candidate_evidence(root, candidate.name, [1]) is False
