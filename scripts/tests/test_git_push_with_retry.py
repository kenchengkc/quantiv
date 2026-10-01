from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
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


@pytest.mark.parametrize("dirty_state", [None, "local-only", "conflicting"])
def test_retry_rebases_when_main_advanced(tmp_path: Path, dirty_state: str | None) -> None:
    origin = tmp_path / "origin.git"
    first = tmp_path / "first"
    second = tmp_path / "second"
    _git(tmp_path, "init", "--bare", str(origin))
    _git(tmp_path, "clone", str(origin), str(first))
    _configure(first)
    _git(first, "checkout", "-b", "main")
    (first / "runtime.json").write_text("original runtime\n")
    _git(first, "add", "runtime.json")
    _git(first, "commit", "-m", "initial")
    _git(first, "push", "-u", "origin", "main")

    _git(tmp_path, "clone", "--branch", "main", str(origin), str(second))
    _configure(second)
    if dirty_state == "conflicting":
        (second / "runtime.json").write_text("remote runtime\n")
        _git(second, "add", "runtime.json")
    _git(second, "commit", "--allow-empty", "-m", "remote-update")
    _git(second, "push", "origin", "main")

    (first / "publication.json").write_text("validated publication\n")
    _git(first, "add", "publication.json")
    _git(first, "commit", "-m", "local-refresh")
    if dirty_state:
        (first / "runtime.json").write_text("unpublished runtime\n")
    (first / "untracked-cache.json").write_text("local cache\n")
    env = os.environ.copy()
    env.update({"GIT_PUSH_MAX_ATTEMPTS": "2", "GIT_PUSH_SLEEP_BASE_S": "0"})
    result = subprocess.run(
        ["bash", str(PUSH_HELPER), "origin", "main"],
        cwd=first,
        env=env,
        capture_output=True,
        text=True,
    )

    assert (first / "untracked-cache.json").read_text() == "local cache\n"
    if dirty_state == "conflicting":
        assert result.returncode != 0
        assert "local-refresh" not in _git(origin, "log", "main", "--format=%s")
        assert _git(first, "show", "stash@{0}:runtime.json") == "unpublished runtime\n"
        assert _git(first, "ls-files", "--unmerged")
        return

    assert result.returncode == 0, result.stdout + result.stderr
    assert _git(first, "show", "origin/main:publication.json") == "validated publication\n"
    assert _git(first, "show", "origin/main:runtime.json") == "original runtime\n"
    if dirty_state:
        assert (first / "runtime.json").read_text() == "unpublished runtime\n"
        assert not _git(first, "stash", "list")
    messages = _git(origin, "--git-dir", str(origin), "log", "--format=%s", "--all")
    assert "remote-update" in messages
    assert "local-refresh" in messages
