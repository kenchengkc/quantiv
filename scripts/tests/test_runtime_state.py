from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import runtime_state


def seed_state(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "market_caps.json").write_text('{"AAPL": 1}\n', encoding="utf-8")
    (data_dir / "popular_snapshot.json").write_text('{"AAPL": 0.9}\n', encoding="utf-8")
    (data_dir / "popular_changes.jsonl").write_text('{"added":["AAPL"]}\n', encoding="utf-8")
    (data_dir / "fmp_earnings_metadata.json").write_text('{"status":"ok"}\n', encoding="utf-8")
    (data_dir / "delisting_watch.json").write_text('{"watch":{}}\n', encoding="utf-8")


def test_release_is_content_addressed_and_deterministic(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    seed_state(data_dir)
    output_a = tmp_path / "release-a"
    output_b = tmp_path / "release-b"

    archive_a, manifest_a, pointer_a, payload_a = runtime_state.build_release(
        data_dir, output_a, source_revision="a" * 40
    )
    archive_b, manifest_b, pointer_b, payload_b = runtime_state.build_release(
        data_dir, output_b, source_revision="b" * 40
    )

    assert payload_a["release_id"] == payload_b["release_id"]
    assert archive_a.read_bytes() == archive_b.read_bytes()
    assert json.loads(manifest_a.read_text()) == json.loads(manifest_b.read_text())
    assert json.loads(pointer_a.read_text())["source_revision"] == "a" * 40
    assert json.loads(pointer_b.read_text())["source_revision"] == "b" * 40


def test_verify_rejects_tampered_archive(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    output_dir = tmp_path / "release"
    seed_state(data_dir)
    archive, _, _, _ = runtime_state.build_release(data_dir, output_dir)
    archive.write_bytes(archive.read_bytes() + b"tamper")

    with pytest.raises(RuntimeError, match="archive size mismatch"):
        runtime_state.verify_release(output_dir)


def test_materialize_restores_release_and_removes_stale_known_state(tmp_path: Path) -> None:
    source = tmp_path / "source"
    output_dir = tmp_path / "release"
    target = tmp_path / "target"
    seed_state(source)
    runtime_state.build_release(source, output_dir)

    target.mkdir()
    (target / "market_caps.json").write_text('{"STALE": 1}\n', encoding="utf-8")
    # This supported file is absent from the release and must not survive.
    (target / "fmp_earnings_backfill_state.json").write_text(
        '{"stale": true}\n', encoding="utf-8"
    )
    # Unrelated local data is outside the runtime-state ownership boundary.
    (target / "unrelated.txt").write_text("keep\n", encoding="utf-8")

    runtime_state.materialize_release(target, output_dir)

    for relative in (
        "market_caps.json",
        "popular_snapshot.json",
        "popular_changes.jsonl",
        "fmp_earnings_metadata.json",
        "delisting_watch.json",
    ):
        assert (target / relative).read_bytes() == (source / relative).read_bytes()
    assert not (target / "fmp_earnings_backfill_state.json").exists()
    assert (target / "unrelated.txt").read_text() == "keep\n"


def test_build_fails_when_no_owned_state_exists(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="no runtime-state files"):
        runtime_state.build_release(tmp_path / "empty", tmp_path / "release")
