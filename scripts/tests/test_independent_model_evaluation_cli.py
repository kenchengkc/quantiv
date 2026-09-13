from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "research" / "independent_model_evaluation.py"


def test_independent_model_evaluation_cli_bootstraps_repository_imports(
    tmp_path: Path,
) -> None:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTHONNOUSERSITE"] = "1"

    completed = subprocess.run(
        [sys.executable, "-P", str(SCRIPT), "--help"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "prepare" in completed.stdout
    assert "evaluate" in completed.stdout
    assert "verify" in completed.stdout
