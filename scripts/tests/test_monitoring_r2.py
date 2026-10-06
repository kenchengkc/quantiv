"""Persist authenticated learning observations independently of ML publication."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pandas as pd
import pytest
import yaml

from ml.model_bundle import create_signed_monitor_receipt, verify_monitor_receipt

ROOT = Path(__file__).resolve().parents[2]
MONITOR_FILES = (
    "prediction_ledger.parquet",
    "latest_monitoring.json",
    "latest_monitoring.receipt.json",
)


@pytest.fixture
def monitoring_store(tmp_path):
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    data = tmp_path / "data"
    monitoring = data / "models/monitoring"
    monitoring.mkdir(parents=True)
    pd.DataFrame({"act_symbol": ["TEST"], "prediction": [0.1]}).to_parquet(
        monitoring / MONITOR_FILES[0], index=False,
    )
    report = {
        "schema": "quantiv.model-monitoring.v1",
        "status": "failed",
        "feature_drift": {"status": "critical"},
        "monitored_at": "2026-10-05T22:00:00+00:00",
        "snapshot_date": "2026-10-05",
        "champion_bundle_id": "a" * 64,
        "ledger_rows": 1,
    }

    def sign_report(drift="critical"):
        report["status"] = "passed" if drift == "passed" else "failed"
        report["feature_drift"]["status"] = drift
        (monitoring / MONITOR_FILES[1]).write_text(json.dumps(report))
        receipt = create_signed_monitor_receipt(
            ledger_path=monitoring / MONITOR_FILES[0],
            report_path=monitoring / MONITOR_FILES[1],
            snapshot_date=report["snapshot_date"], private_key=private,
        )
        (monitoring / MONITOR_FILES[2]).write_text(json.dumps(receipt))

    sign_report()
    # Adjacent producer-owned state must never leak through this upload mode.
    for relative in (
        "models/control/champion.json", "models/control/registry.json",
        "models/bundles/active/model.txt", "models/candidates/candidate.json",
        "models/monitoring/latest_outcomes.json",
        "models/monitoring/unverified_prediction_ledger_old.parquet",
        "forecasts/forecasts_2026-10-05.parquet",
        "control/current_data_release.json", "frontend/recent.json",
    ):
        path = data / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("unrelated local state")
    remote = tmp_path / "remote"
    remote.mkdir()
    remote_forecast = remote / "forecasts/served.parquet"
    remote_forecast.parent.mkdir()
    remote_forecast.write_bytes(b"previously published forecast")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_rclone = bin_dir / "rclone"
    fake_rclone.write_text(f"#!{sys.executable}\n" + '''
import json
import os
from pathlib import Path
import shutil
import sys

args = sys.argv[1:]
with open(os.environ["RCLONE_LOG"], "a") as output:
    output.write(json.dumps(args) + "\\n")
source, destination = args[1:3]
prefix = "test:bucket/"
if not destination.startswith(prefix):
    raise SystemExit("unexpected remote path")
relative = destination[len(prefix):]
if relative == os.environ.get("FAIL_UPLOAD_OBJECT"):
    raise SystemExit(23)
target = Path(os.environ["FAKE_R2_ROOT"]) / relative
target.parent.mkdir(parents=True, exist_ok=True)
if args[0] == "copyto":
    shutil.copyfile(source, target)
elif args[0] in {"copy", "sync"}:
    import fnmatch
    excludes = [args[index + 1] for index, value in enumerate(args) if value == "--exclude"]
    includes = [args[index + 1] for index, value in enumerate(args) if value == "--include"]
    root = Path(source)
    paths = root.rglob("*") if root.is_dir() else [root]
    for path in paths:
        if not path.is_file():
            continue
        local = path.relative_to(root) if root.is_dir() else Path(path.name)
        if any(fnmatch.fnmatch("/" + local.as_posix(), pattern) or fnmatch.fnmatch(local.as_posix(), pattern)
               for pattern in excludes):
            continue
        if includes and not any(fnmatch.fnmatch(local.as_posix(), pattern) for pattern in includes):
            continue
        output = target / local
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, output)
else:
    raise SystemExit("unexpected rclone operation")
if os.environ.get("MUTATE_LOCAL_AFTER_UPLOAD") == "1":
    source_root = Path(os.environ["DATA_DIR"]) / "models/monitoring"
    (source_root / "latest_monitoring.json").write_text("{}");
    (source_root / "prediction_ledger.parquet").write_bytes(b"new local ledger")
''')
    fake_rclone.chmod(0o755)
    log = tmp_path / "rclone.jsonl"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "PYTHON_BIN": sys.executable,
        "DATA_DIR": str(data),
        "R2_REMOTE": "test:bucket",
        "MODEL_BUNDLE_PUBLIC_KEY": public.decode(),
        "FAKE_R2_ROOT": str(remote),
        "RCLONE_LOG": str(log),
    }
    return data, remote, log, env, public, sign_report


def run_push(store, *, command=None, extra_env=None):
    _, _, _, env, _, _ = store
    return subprocess.run(
        ["bash", "-euc", command] if command else
        ["bash", str(ROOT / "scripts/r2_push.sh"), "--monitoring-only"],
        cwd=ROOT, env={**env, **(extra_env or {})}, capture_output=True, text=True,
    )


@pytest.mark.parametrize("drift", ["critical", "insufficient_data", "unsupported_cohort", "passed"])
def test_signed_monitoring_persists_on_hold_without_publishing_other_state(monitoring_store, drift):
    data, remote, log, _, public, sign = monitoring_store
    sign(drift)
    expected = {name: (data / "models/monitoring" / name).read_bytes() for name in MONITOR_FILES}
    result = run_push(monitoring_store)
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert all(call[0] == "copyto" for call in calls)
    assert [call[2] for call in calls] == [f"test:bucket/models/monitoring/{name}" for name in MONITOR_FILES]
    assert set(path.relative_to(remote).as_posix() for path in remote.rglob("*") if path.is_file()) == {
        "forecasts/served.parquet", *(f"models/monitoring/{name}" for name in MONITOR_FILES),
    }
    assert (remote / "forecasts/served.parquet").read_bytes() == b"previously published forecast"
    for name, content in expected.items():
        assert (remote / "models/monitoring" / name).read_bytes() == content
    verify_monitor_receipt(
        json.loads((remote / "models/monitoring" / MONITOR_FILES[2]).read_text()),
        ledger_path=remote / "models/monitoring" / MONITOR_FILES[0],
        report_path=remote / "models/monitoring" / MONITOR_FILES[1], public_key=public,
    )


@pytest.mark.parametrize("mutation", ["ledger", "report", "receipt", "missing", "wrong_key"])
def test_monitoring_upload_authenticates_all_files_before_first_remote_operation(monitoring_store, mutation):
    data, _, log, _, _, _ = monitoring_store
    monitoring = data / "models/monitoring"
    extra = {}
    if mutation == "ledger":
        (monitoring / MONITOR_FILES[0]).write_bytes(b"tampered ledger")
    elif mutation == "report":
        (monitoring / MONITOR_FILES[1]).write_text("{}")
    elif mutation == "receipt":
        receipt = json.loads((monitoring / MONITOR_FILES[2]).read_text())
        receipt["snapshot_date"] = "2026-10-02"
        (monitoring / MONITOR_FILES[2]).write_text(json.dumps(receipt))
    elif mutation == "missing":
        (monitoring / MONITOR_FILES[2]).unlink()
    else:
        extra["MODEL_BUNDLE_PUBLIC_KEY"] = Ed25519PrivateKey.generate().public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()
    result = run_push(monitoring_store, extra_env=extra)
    assert result.returncode != 0
    assert not log.exists()


def test_monitoring_upload_uses_verified_snapshot_despite_later_local_mutation(monitoring_store):
    _, remote, _, _, public, _ = monitoring_store
    result = run_push(monitoring_store, extra_env={"MUTATE_LOCAL_AFTER_UPLOAD": "1"})
    assert result.returncode == 0, result.stderr
    monitoring = remote / "models/monitoring"
    verify_monitor_receipt(
        json.loads((monitoring / MONITOR_FILES[2]).read_text()),
        ledger_path=monitoring / MONITOR_FILES[0], report_path=monitoring / MONITOR_FILES[1],
        public_key=public,
    )
    assert pd.read_parquet(monitoring / MONITOR_FILES[0])["act_symbol"].tolist() == ["TEST"]
    assert json.loads((monitoring / MONITOR_FILES[1]).read_text())["status"] == "failed"


def test_upload_failure_never_publishes_a_receipt_for_missing_monitoring_bytes(monitoring_store):
    _, remote, log, _, _, _ = monitoring_store
    result = run_push(monitoring_store, extra_env={
        "FAIL_UPLOAD_OBJECT": "models/monitoring/latest_monitoring.json",
    })
    assert result.returncode != 0
    assert not (remote / "models/monitoring/latest_monitoring.receipt.json").exists()
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(calls) == 2


@pytest.mark.parametrize("mode", ["--forecasts-only", "--model-recovery"])
def test_other_producers_cannot_overwrite_the_authenticated_prediction_trio(monitoring_store, mode):
    _, remote, _, _, _, _ = monitoring_store
    result = run_push(monitoring_store, command=f"bash scripts/r2_push.sh {mode}")
    assert result.returncode == 0, result.stderr
    assert not (remote / "models/monitoring").exists()
    assert (remote / "forecasts/forecasts_2026-10-05.parquet").exists()


def test_candidate_control_push_preserves_separately_owned_outcome_upload(monitoring_store):
    data, remote, _, _, _, _ = monitoring_store
    (data / "models/control/registry.json").unlink()
    result = run_push(monitoring_store, command="bash scripts/r2_push.sh --candidate-control-only")
    assert result.returncode == 0, result.stderr
    monitoring = remote / "models/monitoring"
    assert (monitoring / "latest_outcomes.json").read_text() == "unrelated local state"
    assert not any((monitoring / name).exists() for name in MONITOR_FILES)


def monitoring_condition_runs(expression, monitor_id, outcome):
    # Exercise workflow admission with ML publication held and only the monitor
    # outcome varied, rather than asserting the spelling of a YAML condition.
    values = {
        f"steps.{monitor_id}.outcome": outcome,
        f"steps.{monitor_id}.outputs.can_publish_ml": "false",
        "steps.options_gate.outputs.can_refresh": "true",
        "steps.input_gate.outputs.ready": "true",
    }
    expression = expression.removeprefix("${{").removesuffix("}}")
    expression = re.sub(r"steps\.[\w-]+\.[\w.]+", lambda match: repr(values[match[0]]), expression)
    return eval(expression.replace("&&", " and ").replace("||", " or "),
                {"__builtins__": {}, "always": lambda: True, "success": lambda: outcome == "success"})


@pytest.mark.parametrize("workflow,job,monitor_id", [
    ("data-refresh.yml", "refresh", "model_publication"),
    ("data-refresh.yml", "recovery", "model_publication"),
    ("event-forecast-freeze.yml", "freeze", "model-publication"),
])
def test_workflow_persists_verified_monitoring_even_when_ml_publication_is_held(
    monitoring_store, workflow, job, monitor_id,
):
    steps = yaml.safe_load((ROOT / ".github/workflows" / workflow).read_text())["jobs"][job]["steps"]
    monitor_offset = next(index for index, step in enumerate(steps) if step.get("id") == monitor_id)
    uploads = [(index, step) for index, step in enumerate(steps) if "--monitoring-only" in step.get("run", "")]
    assert len(uploads) == 1
    offset, upload = uploads[0]
    assert offset == monitor_offset + 1
    assert monitoring_condition_runs(upload["if"], monitor_id, "success") is True
    for outcome in ("failure", "skipped"):
        assert monitoring_condition_runs(upload["if"], monitor_id, outcome) is False
    for step in steps:
        if "--forecasts-only" in step.get("run", ""):
            assert monitoring_condition_runs(step["if"], monitor_id, "success") is False
    result = run_push(monitoring_store, command=upload["run"])
    assert result.returncode == 0, result.stderr
    _, remote, _, _, _, _ = monitoring_store
    assert (remote / "models/monitoring/prediction_ledger.parquet").exists()
    assert not (remote / "models/control/champion.json").exists()


def test_prediction_writers_share_the_complete_refresh_execution_lock():
    daily = yaml.safe_load((ROOT / ".github/workflows/data-refresh.yml").read_text())
    freeze = yaml.safe_load((ROOT / ".github/workflows/event-forecast-freeze.yml").read_text())
    locks = [daily["jobs"][job]["concurrency"] for job in ("refresh", "recovery", "options_recovery")]
    locks.append(freeze["concurrency"])
    assert len({lock["group"] for lock in locks}) == 1
    assert locks[0]["group"]
    assert all(lock["cancel-in-progress"] is False for lock in locks)
