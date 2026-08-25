from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys

import pandas as pd
import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import sync_vix  # noqa: E402
from scripts.data_release import build_release, verify_release  # noqa: E402


VIX_CSV = """DATE,OPEN,HIGH,LOW,CLOSE
08/21/2026,15.00,16.00,14.00,15.50
08/24/2026,15.20,16.20,14.20,15.85
"""


def _fake_rclone(tmp_path: Path) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_path = tmp_path / "rclone.log"
    executable = bin_dir / "rclone"
    executable.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$RCLONE_LOG"\n'
        "exit 0\n"
    )
    executable.chmod(0o755)
    return bin_dir, log_path


def test_vix_sync_creates_one_immutable_snapshot_and_local_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    out_dir = tmp_path / "parquet" / "vix"
    out_dir.mkdir(parents=True)
    (out_dir / "vix.parquet").write_bytes(b"legacy")
    (out_dir / "vix-through-2026-08-21-deadbeef.parquet").write_bytes(b"stale")
    monkeypatch.setattr(sync_vix, "fetch_csv", lambda _: VIX_CSV)

    sync_vix.main()

    alias = out_dir / "vix.parquet"
    snapshots = [path for path in out_dir.glob("*.parquet") if path != alias]
    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert re.fullmatch(
        r"vix-through-2026-08-24-[0-9a-f]{64}\.parquet", snapshot.name
    )
    assert alias.read_bytes() == snapshot.read_bytes()
    frame = pd.read_parquet(snapshot)
    assert frame["date"].astype(str).tolist() == ["2026-08-21", "2026-08-24"]
    assert frame["vix_close"].tolist() == [15.5, 15.85]

    _, _, manifest = build_release(tmp_path)
    assert [item["path"] for item in manifest["files"]] == [
        f"parquet/vix/{snapshot.name}"
    ]
    assert verify_release(tmp_path)["files"] == 1


def test_r2_push_keeps_vix_alias_out_of_immutable_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    out_dir = tmp_path / "parquet" / "vix"
    out_dir.mkdir(parents=True)
    snapshot = out_dir / ("vix-through-2026-08-24-" + "a" * 64 + ".parquet")
    snapshot.write_bytes(b"immutable-vix")
    (out_dir / "vix.parquet").write_bytes(b"immutable-vix")

    bin_dir, log_path = _fake_rclone(tmp_path)
    env = {
        **os.environ,
        "DATA_DIR": str(tmp_path),
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "PYTHON_BIN": sys.executable,
        "RCLONE_LOG": str(log_path),
    }
    subprocess.run(
        ["bash", "scripts/r2_push.sh", "--skip-forecasts"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    calls = log_path.read_text().splitlines()
    parquet_copy = next(line for line in calls if line.startswith("copy ") and "/parquet " in line)
    parquet_check = next(line for line in calls if line.startswith("check ") and "/parquet " in line)
    assert "--immutable" in parquet_copy
    assert "--exclude /vix/vix.parquet" in parquet_copy
    assert "--exclude /vix/vix.parquet" in parquet_check


def test_r2_pull_restores_vix_alias_from_active_release(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "parquet" / "vix"
    out_dir.mkdir(parents=True)
    snapshot = out_dir / ("vix-through-2026-08-24-" + "b" * 64 + ".parquet")
    snapshot.write_bytes(b"active-vix")
    build_release(tmp_path)

    bin_dir, log_path = _fake_rclone(tmp_path)
    env = {
        **os.environ,
        "DATA_DIR": str(tmp_path),
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "PYTHON_BIN": sys.executable,
        "RCLONE_LOG": str(log_path),
    }
    subprocess.run(
        ["bash", "scripts/r2_pull.sh"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert (out_dir / "vix.parquet").read_bytes() == snapshot.read_bytes()
