from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from scripts.frontend_release import PUBLIC_MANIFEST, build_release, write_deployment_pointer


REPO_ROOT = Path(__file__).resolve().parents[2]
MATERIALIZER = REPO_ROOT / "scripts" / "materialize_frontend_release.sh"
FRONTEND_WORKSPACE = REPO_ROOT / "apps" / "frontend"


def _write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)


def _fake_rclone(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env python3
import os
from pathlib import Path
import shutil
import sys

args = sys.argv[1:]
if args[:1] == [\"--config\"]:
    args = args[2:]
if len(args) != 3 or args[0] != \"copyto\":
    raise SystemExit(f\"unsupported fake-rclone args: {args!r}\")
source, target = args[1], args[2]
if source.startswith(\"fake:\"):
    source = str(Path(os.environ[\"FAKE_R2_ROOT\"]) / source.split(\":\", 1)[1])
Path(target).parent.mkdir(parents=True, exist_ok=True)
shutil.copyfile(source, target)
"""
    )
    path.chmod(0o755)


@pytest.mark.parametrize("existing_publication", [False, True])
def test_materializer_downloads_verified_pinned_release_and_preserves_brand(
    tmp_path: Path, existing_publication: bool,
) -> None:
    source_public = tmp_path / "source-public"
    target_public = tmp_path / "target-public"
    release_output = tmp_path / "release-output"
    deployment_pointer = tmp_path / "frontend-release.json"
    fake_r2 = tmp_path / "fake-r2"
    fake_rclone = tmp_path / "rclone"

    _write(source_public / "weekly.json", b'{"fresh":true}\n')
    _write(source_public / "symbols/AAPL.json", b'{"ticker":"AAPL"}\n')
    archive, manifest, _current, release = build_release(source_public, release_output)
    write_deployment_pointer(
        release_output,
        deployment_pointer,
        source_revision="fixture-source-revision",
    )

    remote_manifest = fake_r2 / "frontend" / "manifests" / f"{release['release_id']}.json"
    remote_archive = fake_r2 / "frontend" / "releases" / f"{release['release_id']}.tar.gz"
    remote_manifest.parent.mkdir(parents=True, exist_ok=True)
    remote_archive.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(manifest, remote_manifest)
    shutil.copyfile(archive, remote_archive)
    _fake_rclone(fake_rclone)

    _write(target_public / "brand/logo.webp", b"keep-brand")
    if existing_publication:
        _write(target_public / "weekly.json", b'{"stale":true}\n')
        _write(target_public / "symbols/STALE.json", b"{}\n")

    env = os.environ.copy()
    env.update(
        {
            "PUBLIC_DIR": str(target_public),
            "FRONTEND_DEPLOYMENT_POINTER": str(deployment_pointer),
            "FRONTEND_R2_REMOTE": "fake:frontend",
            "RCLONE_BIN": str(fake_rclone),
            "FAKE_R2_ROOT": str(fake_r2),
            "PYTHON_BIN": sys.executable,
            "FRONTEND_RELEASE_REQUIRED": "1",
        }
    )
    # npm workspace lifecycle scripts run with cwd=apps/frontend. Keep this
    # integration test at the same cwd so repo-relative script regressions fail.
    result = subprocess.run(
        ["bash", str(MATERIALIZER)],
        cwd=FRONTEND_WORKSPACE,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert release["release_id"] in result.stdout
    assert (target_public / "brand/logo.webp").read_bytes() == b"keep-brand"
    assert (target_public / "weekly.json").read_bytes() == b'{"fresh":true}\n'
    assert (target_public / "symbols/AAPL.json").exists()
    assert not (target_public / "symbols/STALE.json").exists()
    assert (target_public / PUBLIC_MANIFEST).read_bytes() == manifest.read_bytes()


def test_default_paths_work_from_frontend_workspace_without_r2_credentials() -> None:
    env = os.environ.copy()
    for key in (
        "PUBLIC_DIR",
        "FRONTEND_DEPLOYMENT_POINTER",
        "R2_ACCOUNT_ID",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "RCLONE_BIN",
        "FRONTEND_RELEASE_REQUIRED",
    ):
        env.pop(key, None)
    env["PYTHON_BIN"] = sys.executable

    result = subprocess.run(
        ["bash", str(MATERIALIZER)],
        cwd=FRONTEND_WORKSPACE,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "source-controlled publication fallback" in result.stdout


def test_materializer_uses_checked_in_fallback_when_credentials_are_absent(
    tmp_path: Path,
) -> None:
    target_public = tmp_path / "public"
    pointer = tmp_path / "frontend-release.json"
    _write(target_public / "weekly.json", b'{"fallback":true}\n')
    _write(target_public / "symbols/AAPL.json", b"{}\n")
    _write(target_public / PUBLIC_MANIFEST, b'{"old":"attestation"}\n')
    pointer.write_text('{"schema":"quantiv.frontend-deployment.v1"}\n')

    env = os.environ.copy()
    for key in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "RCLONE_BIN"):
        env.pop(key, None)
    env.update(
        {
            "PUBLIC_DIR": str(target_public),
            "FRONTEND_DEPLOYMENT_POINTER": str(pointer),
            "PYTHON_BIN": sys.executable,
        }
    )
    result = subprocess.run(
        ["bash", str(MATERIALIZER)],
        cwd=FRONTEND_WORKSPACE,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "source-controlled publication fallback" in result.stdout
    assert (target_public / "weekly.json").read_bytes() == b'{"fallback":true}\n'
    assert not (target_public / PUBLIC_MANIFEST).exists()


def test_materializer_fails_closed_without_remote_access_or_fallback(tmp_path: Path) -> None:
    target_public = tmp_path / "public"
    target_public.mkdir()
    pointer = tmp_path / "frontend-release.json"
    pointer.write_text('{"schema":"quantiv.frontend-deployment.v1"}\n')

    env = os.environ.copy()
    for key in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "RCLONE_BIN"):
        env.pop(key, None)
    env.update(
        {
            "PUBLIC_DIR": str(target_public),
            "FRONTEND_DEPLOYMENT_POINTER": str(pointer),
            "PYTHON_BIN": sys.executable,
        }
    )
    result = subprocess.run(
        ["bash", str(MATERIALIZER)],
        cwd=FRONTEND_WORKSPACE,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "materialization required but unavailable" in result.stderr
