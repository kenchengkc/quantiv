"""Historical learning cannot depend on downloading current activation evidence."""

import os
from pathlib import Path
import subprocess


def _run_pull(tmp_path, *, historical):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    rclone = bin_dir / "rclone"
    rclone.write_text('''#!/bin/bash
if [[ "$2" == *data_reconciliation.json ]]; then exit 4; fi
if [[ "$2" == *runtime-state/current.json ]]; then exit 3; fi
if [[ "$2" == *earnings_calendar.csv ]]; then
  printf 'act_symbol,date,timing\\nTEST,2026-09-01,bmo\\n' > "$3"
fi
exit 0
''')
    rclone.chmod(0o755)
    python = bin_dir / "fake-python"
    python.write_text('''#!/bin/bash
printf '%s\\n' "$*" >> "$PULL_PYTHON_LOG"
exit 0
''')
    python.chmod(0o755)
    data = tmp_path / "data"
    (data / "validation").mkdir(parents=True)
    stale = data / "validation/data_reconciliation.json"
    stale.write_text('{"quality":{"decision_safe":true}}')
    log = tmp_path / "python.log"
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
           "PYTHON_BIN": str(python), "PULL_PYTHON_LOG": str(log),
           "DATA_DIR": str(data), "R2_PULL_EARNINGS": "1",
           "R2_HISTORICAL_TRAINING": "1" if historical else "0"}
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(["bash", "scripts/r2_pull.sh"], cwd=root, env=env,
                            text=True, capture_output=True)
    return result, stale, log


def test_missing_current_reconciliation_does_not_block_verified_historical_admission(tmp_path):
    result, stale, log = _run_pull(tmp_path, historical=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "verify_historical_training_gate.py" in log.read_text()
    assert not stale.exists()


def test_nonhistorical_pull_still_fails_without_current_reconciliation(tmp_path):
    result, _, log = _run_pull(tmp_path, historical=False)
    assert result.returncode != 0
    assert not log.exists()
