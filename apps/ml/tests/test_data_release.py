from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.data_release import build_release, verify_release  # noqa: E402


def test_versioned_release_pointer_verifies_immutable_partitions(tmp_path: Path) -> None:
    first = tmp_path / "parquet" / "options_chain" / "2026-08-21.parquet"
    second = tmp_path / "parquet" / "ohlcv" / "2026-08-21.parquet"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_bytes(b"options")
    second.write_bytes(b"ohlcv")

    manifest_path, pointer_path, manifest = build_release(tmp_path)
    pointer = json.loads(pointer_path.read_text())
    assert pointer["release_id"] == manifest["release_id"]
    assert manifest_path.name == f"{manifest['release_id']}.json"
    assert verify_release(tmp_path)["files"] == 2

    first.write_bytes(b"corrupt")
    with pytest.raises(RuntimeError, match="verification failed"):
        verify_release(tmp_path)


def test_release_id_is_stable_when_data_is_unchanged(tmp_path: Path) -> None:
    partition = tmp_path / "parquet" / "options_chain" / "one.parquet"
    partition.parent.mkdir(parents=True)
    partition.write_bytes(b"same-data")
    _, _, first = build_release(tmp_path)
    _, _, second = build_release(tmp_path)
    assert first["release_id"] == second["release_id"]
