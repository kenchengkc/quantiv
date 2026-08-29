from __future__ import annotations

import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
PUSH_HELPER = REPO_ROOT / "scripts" / "git_push_with_retry.sh"


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _configure(repo: Path) -> None:
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Quantiv test")


def test_retry_rebases_when_main_advanced(tmp_path: Path) -> None:
    origin = tmp_path / "origin.git"
    first = tmp_path / "first"
    second = tmp_path / "second"
    _git(tmp_path, "init", "--bare", str(origin))
    _git(tmp_path, "clone", str(origin), str(first))
    _configure(first)
    _git(first, "checkout", "-b", "main")
    _git(first, "commit", "--allow-empty", "-m", "initial")
    _git(first, "push", "-u", "origin", "main")

    _git(tmp_path, "clone", "--branch", "main", str(origin), str(second))
    _configure(second)
    _git(second, "commit", "--allow-empty", "-m", "remote-update")
    _git(second, "push", "origin", "main")

    _git(first, "commit", "--allow-empty", "-m", "local-refresh")
    env = os.environ.copy()
    env.update({"GIT_PUSH_MAX_ATTEMPTS": "2", "GIT_PUSH_SLEEP_BASE_S": "0"})
    subprocess.run(
        ["bash", str(PUSH_HELPER), "origin", "main"],
        cwd=first,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    messages = _git(origin, "--git-dir", str(origin), "log", "--format=%s", "--all")
    assert "remote-update" in messages
    assert "local-refresh" in messages
