"""Exercise independent rollback sequencing and remote control coherence."""

import json
import os
from pathlib import Path
import subprocess
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest
import yaml

from ml.model_bundle import create_signed_control_pointer, create_signed_registry

ROOT = Path(__file__).resolve().parents[2]


def _steps():
    return yaml.safe_load((ROOT / ".github/workflows/model-retrain.yml").read_text())["jobs"]["retrain"]["steps"]


def test_automatic_rollback_is_published_and_imported_before_training():
    steps = _steps()
    ids = [step.get("id") for step in steps]
    required = ["outcome-control", "rollback-score", "rollback-apply", "rollback-publication", "rollback-serving", "rollback-import", "fit-models"]
    assert all(name in ids for name in required)
    offsets = [ids.index(name) for name in required]
    assert offsets == sorted(offsets)
    for name in required[1:-1]:
        step = steps[ids.index(name)]
        flag = "outcome-control.outputs.rollback_staged" if name in {"rollback-score", "rollback-apply"} else "rollback-apply.outputs.rolled_back"
        assert f"steps.{flag} == 'true'" in step["if"]
        assert "steps.model-decision" not in step["if"]
    outcome = steps[ids.index("outcome-control")]
    assert "--activation-report data/validation/rollback_decision.json" in outcome["run"]
    assert "--stage-rollback" in outcome["run"]
    apply = steps[ids.index("rollback-apply")]["run"]
    assert "model_control_plane.py apply-rollback" in apply
    publication = steps[ids.index("rollback-publication")]["run"]
    assert publication.rindex("verify_retrain_data_gate.py") < publication.index("r2_push.sh --model-recovery")
    assert "--allow-hold" not in publication
    serving = steps[ids.index("rollback-serving")]["run"]
    assert "--decision data/validation/rollback_applied.json" in serving
    imported = steps[ids.index("rollback-import")]["run"]
    assert "--expected-model-bundle-id" in imported and "--require-database" in imported


def test_late_candidate_publication_never_reuses_an_earlier_rollback_flag():
    steps = _steps()
    push = next(step for step in steps if step["name"] == "Push activated models to R2")
    assert "outcome-control" not in push["if"]
    assert "verify_retrain_data_gate.py" in push["run"]
    assert "--allow-hold" not in push["run"]
    assert push["run"].index("verify_retrain_data_gate.py") < push["run"].index("r2_push.sh")
    retained = next(i for i, step in enumerate(steps) if step["name"] == "Persist retained candidate control and outcome evidence")
    imported = next(i for i, step in enumerate(steps) if step["name"] == "Import exact retrain forecast into Neon")
    assert retained > imported


@pytest.mark.parametrize("name", ["Push activated models to R2", "Publish validated automatic rollback before training"])
def test_late_held_data_prevents_every_remote_model_mutation(tmp_path, name):
    steps = _steps()
    push = next(step for step in steps if step["name"] == name)
    (tmp_path / "scripts").symlink_to(ROOT / "scripts", target_is_directory=True)
    validation = tmp_path / "data/validation"
    validation.mkdir(parents=True)
    (validation / "retrain_activation.json").write_text(json.dumps({"status": "passed"}))
    (validation / "data_reconciliation.json").write_text(json.dumps({
        "schema": "quantiv.data-reconciliation.v2",
        "quality": {"decision_safe": False, "critical_exceptions": 1},
        "exceptions": [{"severity": "critical", "code": "late_hold"}],
    }))
    executable = tmp_path / "rclone"
    executable.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALL_LOG"\n')
    executable.chmod(0o755)
    log = tmp_path / "calls"
    result = subprocess.run(["bash", "-euc", push["run"]], cwd=tmp_path,
        env={**os.environ, "PATH": f"{Path(sys.executable).parent}:{tmp_path}:{os.environ['PATH']}",
             "CALL_LOG": str(log)}, capture_output=True, text=True)
    assert result.returncode != 0
    assert "late_hold" in result.stderr
    assert not log.exists()


@pytest.mark.parametrize("published", [True, False])
def test_registry_only_push_requires_the_remote_champion_to_match(tmp_path, published):
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    data = tmp_path / "data"
    control = data / "models/control"
    control.mkdir(parents=True)
    selected, old = "b" * 64, "a" * 64
    pointer = create_signed_control_pointer(bundle_id=selected, previous_bundle_id=old, decision={}, private_key=private)
    registry = create_signed_registry(champion_bundle_id=selected, challenger_bundle_id="c" * 64,
        previous_bundle_id=old, decision={}, private_key=private)
    (control / "champion.json").write_text(json.dumps(pointer))
    (control / "registry.json").write_text(json.dumps(registry))
    remote = tmp_path / "remote.json"
    remote.write_text(json.dumps(pointer if published else create_signed_control_pointer(
        bundle_id=old, previous_bundle_id=None, decision={}, private_key=private)))
    executable = tmp_path / "rclone"
    executable.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALL_LOG"\n'
        'if [ "$1" = copyto ] && [ "$2" = test:bucket/models/control/champion.json ]; then cp "$REMOTE_CHAMPION" "$3"; fi\n')
    executable.chmod(0o755)
    log = tmp_path / "calls"
    result = subprocess.run(["bash", str(ROOT / "scripts/r2_push.sh"), "--candidate-control-only"], cwd=ROOT,
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "PYTHON_BIN": sys.executable,
             "DATA_DIR": str(data), "R2_REMOTE": "test:bucket", "MODEL_BUNDLE_PUBLIC_KEY": public.decode(),
             "REMOTE_CHAMPION": str(remote), "CALL_LOG": str(log)}, capture_output=True, text=True)
    writes = [line for line in log.read_text().splitlines() if " test:bucket/models/control/registry.json" in line]
    if published:
        assert result.returncode == 0, result.stderr
        assert len(writes) == 1
    else:
        assert result.returncode != 0
        assert not writes


def test_model_publication_never_uploads_registry_before_champion(tmp_path):
    data = tmp_path / "data"
    control = data / "models/control"
    control.mkdir(parents=True)
    for name in ("champion.json", "registry.json"):
        (control / name).write_text("{}")
    evaluations = data / "models/evaluations"
    evaluations.mkdir()
    (evaluations / "learning_123.json").write_text("{}")
    executable = tmp_path / "rclone"
    executable.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALL_LOG"\n')
    executable.chmod(0o755)
    log = tmp_path / "calls"
    subprocess.run(["bash", str(ROOT / "scripts/r2_push.sh"), "--model-recovery"], check=True, cwd=ROOT,
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "DATA_DIR": str(data),
             "R2_REMOTE": "test:bucket", "CALL_LOG": str(log)})
    calls = log.read_text().splitlines()
    directory = next(line for line in calls if " copy " in f" {line} " and "/models/control " in line)
    assert '--exclude /registry.json' in directory
    champion = next(i for i, line in enumerate(calls) if "copyto" in line and "test:bucket/models/control/champion.json" in line)
    registry = next(i for i, line in enumerate(calls) if "copyto" in line and "test:bucket/models/control/registry.json" in line)
    assert champion < registry
    evaluation_copy = next(line for line in calls if "copy " in line and "test:bucket/models/evaluations" in line)
    assert "--immutable" in evaluation_copy


@pytest.mark.parametrize("held", [True, False])
def test_rollback_handoff_report_is_bound_to_a_gated_previous_champion(tmp_path, monkeypatch, held):
    from argparse import Namespace
    import pandas as pd
    import model_control_plane_impl as plane
    from ml.model_bundle import create_signed_monitor_receipt, verify_control_pointer, verify_registry
    from scripts.activate_model_bundle import expected_bundle_id

    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    monkeypatch.setenv("MODEL_BUNDLE_SIGNING_KEY", private.decode())
    monkeypatch.setenv("MODEL_BUNDLE_PUBLIC_KEY", public.decode())
    models = tmp_path / "models"
    control, monitoring = models / "control", models / "monitoring"
    control.mkdir(parents=True)
    monitoring.mkdir()
    old, previous = "a" * 64, "b" * 64
    (control / "champion.json").write_text(json.dumps(create_signed_control_pointer(
        bundle_id=old, previous_bundle_id=previous, decision={})))
    (control / "registry.json").write_text(json.dumps(create_signed_registry(
        champion_bundle_id=old, previous_bundle_id=previous, challenger_bundle_id=None, decision={})))
    originals = {path: path.read_bytes() for path in control.iterdir()}
    ledger = monitoring / "prediction_ledger.parquet"
    pd.DataFrame({"prediction": [.1]}).to_parquet(ledger)
    report = monitoring / "latest_monitoring.json"
    report.write_text("{}")
    (monitoring / "latest_monitoring.receipt.json").write_text(json.dumps(create_signed_monitor_receipt(
        ledger_path=ledger, report_path=report, snapshot_date="2026-10-02")))
    monkeypatch.setattr(plane, "verify_bundle_dir", lambda *args, **kwargs: {})
    monkeypatch.setattr(plane, "evaluate_realized_outcomes", lambda *args, **kwargs: {
        "status": "failed", "rollback_recommended": True, "rollback_reasons": ["MAE regression"],
        "champion": {"mae": .1}, "comparison": {"mae": .05},
    })
    monkeypatch.setattr(plane, "_activation_gate", lambda _: {"status": "held" if held else "passed", "reason": "late hold"})
    args = Namespace(models_root=models, training_dir=tmp_path / "training", min_common_rows=30,
        report=tmp_path / "outcomes.json", monitoring_report=None, history=None, history_limit=52,
        activation_report=tmp_path / "rollback_decision.json", stage_rollback=True)
    assert plane.evaluate_outcomes(args) == 0
    outcome = json.loads(args.report.read_text())
    assert outcome["rolled_back"] is False
    assert outcome["rollback_staged"] is (not held)
    assert {path: path.read_bytes() for path in control.iterdir()} == originals
    if held:
        assert not args.activation_report.exists()
        assert {path: path.read_bytes() for path in control.iterdir()} == originals
    else:
        proposal = json.loads(args.activation_report.read_text())
        assert proposal["status"] == "held"
        candidate = tmp_path / "rollback_forecast.parquet"
        pd.DataFrame({"model_bundle_id": [], "snapshot_date": []}).to_parquet(candidate)
        apply_args = Namespace(models_root=models, rollback_decision=args.activation_report,
            candidate_forecast=candidate, activation_gate_report=tmp_path / "gate.json",
            production_forecast_dir=tmp_path / "forecasts", report=tmp_path / "applied.json")
        assert plane.apply_rollback(apply_args) == 0
        assert json.loads(apply_args.report.read_text())["rolled_back"] is False
        assert {path: path.read_bytes() for path in control.iterdir()} == originals
        assert not apply_args.production_forecast_dir.exists()
        (control / "champion.json").write_text(json.dumps(create_signed_control_pointer(
            bundle_id=old, previous_bundle_id="c" * 64, decision={})))
        divergent = {path: path.read_bytes() for path in control.iterdir()}
        with pytest.raises(ValueError, match="current signed previous champion"):
            plane.apply_rollback(apply_args)
        assert {path: path.read_bytes() for path in control.iterdir()} == divergent
        (control / "champion.json").write_bytes(originals[control / "champion.json"])
        original_outcome = (monitoring / "latest_outcomes.json").read_bytes()
        (monitoring / "latest_outcomes.json").write_text("{}")
        with pytest.raises(RuntimeError, match="outcome"):
            plane.apply_rollback(apply_args)
        assert {path: path.read_bytes() for path in control.iterdir()} == originals
        (monitoring / "latest_outcomes.json").write_bytes(original_outcome)
        pd.DataFrame({"model_bundle_id": [previous], "snapshot_date": ["2026-10-02"]}).to_parquet(candidate)
        monkeypatch.setattr(plane, "validate_forecast_artifact", lambda *args, **kwargs: {"status": "passed"})
        gates = iter([{"status": "passed"}, {"status": "held", "reason": "late data hold"}])
        monkeypatch.setattr(plane, "_activation_gate", lambda _: next(gates))
        assert plane.apply_rollback(apply_args) == 0
        assert json.loads(apply_args.report.read_text())["rolled_back"] is False
        assert {path: path.read_bytes() for path in control.iterdir()} == originals
        assert not apply_args.production_forecast_dir.exists()
        monkeypatch.setattr(plane, "_activation_gate", lambda _: {"status": "passed"})
        assert plane.apply_rollback(apply_args) == 0
        assert expected_bundle_id(apply_args.report) == previous
        assert verify_control_pointer(json.loads((control / "champion.json").read_text()))["champion_bundle_id"] == previous
        assert verify_registry(json.loads((control / "registry.json").read_text()))["champion_bundle_id"] == previous
        from ml.model_artifact import sha256_file
        assert json.loads(apply_args.report.read_text())["outcome_report_sha256"] == sha256_file(args.report)
