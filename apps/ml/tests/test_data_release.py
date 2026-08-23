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
    first_path, _, first = build_release(tmp_path)
    first_bytes = first_path.read_bytes()
    _, _, second = build_release(tmp_path)
    assert first["release_id"] == second["release_id"]
    assert first_path.read_bytes() == first_bytes
    assert "generated_at" not in first
    assert "mtime_ns" not in first["files"][0]


def test_existing_release_manifest_is_never_rewritten(tmp_path: Path) -> None:
    partition = tmp_path / "parquet" / "options_chain" / "one.parquet"
    partition.parent.mkdir(parents=True)
    partition.write_bytes(b"same-data")
    manifest_path, _, manifest = build_release(tmp_path)
    legacy = {
        **manifest,
        "generated_at": "2026-08-22T00:00:00+00:00",
        "files": [{**manifest["files"][0], "mtime_ns": 123}],
    }
    manifest_path.write_text(json.dumps(legacy, indent=2, sort_keys=True) + "\n")
    legacy_bytes = manifest_path.read_bytes()

    rebuilt_path, _, rebuilt = build_release(tmp_path)

    assert rebuilt_path.read_bytes() == legacy_bytes
    assert rebuilt["generated_at"] == "2026-08-22T00:00:00+00:00"
