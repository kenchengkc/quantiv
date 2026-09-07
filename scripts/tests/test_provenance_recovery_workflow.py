"""Executable upload-scope and deployment-order regression checks."""

import os
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_recovery_push_never_promotes_or_uploads_data_release(tmp_path):
    data = tmp_path / "data"
    (data / "models" / "control").mkdir(parents=True)
    (data / "models" / "control" / "champion.json").write_text("{}")
    (data / "forecasts").mkdir()
    executable = tmp_path / "rclone"
    executable.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALL_LOG"\n')
    executable.chmod(0o755)
    log = tmp_path / "calls"
    subprocess.run(
        ["bash", str(ROOT / "scripts/r2_push.sh"), "--model-recovery"],
        check=True,
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "DATA_DIR": str(data),
            "R2_REMOTE": "test:bucket",
            "CALL_LOG": str(log),
            "PYTHON_BIN": "/usr/bin/false",
        },
    )
    calls = log.read_text().splitlines()
    assert len(calls) == 4
    assert "models/control/champion.json" in calls[-1]
    assert all(
        "test:bucket/models" in call or "test:bucket/forecasts" in call
        for call in calls
    )
    assert not any(
        "current_data_release" in call or "reconciliation" in call for call in calls
    )


def test_recovery_is_isolated_serialized_and_uses_existing_deployment_tools():
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/model-rollback.yml").read_text()
    )
    assert workflow["concurrency"] == {
        "group": "model-provenance-rollback",
        "cancel-in-progress": False,
    }
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["rollback"]
    assert job["environment"] == "production-recovery"
    assert job["env"]["R2_BUCKET"] == "${{ secrets.R2_BUCKET || 'quantiv-data' }}"

    steps = job["steps"]
    commands = "\n".join(step.get("run", "") for step in steps)
    assert 'test "$GITHUB_REF" = refs/heads/main' in commands
    assert 'test "$EXPECTED_CURRENT" != "$ROLLBACK_TARGET"' in commands
    assert "bash scripts/install_rclone.sh" in commands

    sequence = [
        "--check-only",
        "scripts/daily_score.py",
        "scripts/validate_ml_pipeline.py",
        "scripts/verify_model_recovery.py --preflight",
        "champion-prepush.json",
        "scripts/r2_push.sh --model-recovery",
        "scripts/activate_model_bundle.py",
        "scripts/import_recent_to_postgres.py",
        "scripts/verify_model_recovery.py \\",
    ]
    offsets = [commands.index(item) for item in sequence]
    assert offsets == sorted(offsets)
    assert "model_trainer" not in commands
    assert "--require-database" in commands
    assert "--expected-model-bundle-id" in commands
