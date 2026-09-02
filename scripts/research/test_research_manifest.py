from pathlib import Path

import pytest

from research.research_manifest import build_manifest, verify_manifest


def test_manifest_is_order_independent_and_content_addressed(tmp_path: Path) -> None:
    (tmp_path / "a.json").write_text('{"x":1}\n')
    (tmp_path / "b.csv").write_text("x,y\n1,2\n")

    first = build_manifest(
        ["b.csv", "a.json"],
        repo_root=tmp_path,
        as_of="2026-09-02T00:00:00+00:00",
        git_commit="abc123",
    )
    second = build_manifest(
        ["a.json", "b.csv"],
        repo_root=tmp_path,
        as_of="2026-09-02T00:00:00+00:00",
        git_commit="abc123",
    )

    assert first == second
    assert first["manifest_id"].startswith("sha256:")
    assert [row["path"] for row in first["inputs"]] == ["a.json", "b.csv"]
    assert verify_manifest(first, repo_root=tmp_path)["ok"] is True


def test_verify_detects_byte_level_input_drift(tmp_path: Path) -> None:
    target = tmp_path / "features.parquet"
    target.write_bytes(b"version-1")
    manifest = build_manifest(
        [target],
        repo_root=tmp_path,
        as_of="2026-09-02T00:00:00+00:00",
        git_commit="abc123",
    )

    target.write_bytes(b"version-2")
    report = verify_manifest(manifest, repo_root=tmp_path)

    assert report["ok"] is False
    assert report["files"][0]["ok"] is False
    assert report["files"][0]["expected_sha256"] != report["files"][0]["actual_sha256"]


def test_manifest_rejects_paths_outside_repo_root(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("nope")
    try:
        with pytest.raises(ValueError, match="escapes repository root"):
            build_manifest([outside], repo_root=tmp_path, git_commit="abc123")
    finally:
        outside.unlink(missing_ok=True)


def test_verify_fails_closed_on_malformed_input_record(tmp_path: Path) -> None:
    manifest = {
        "schema": "quantiv.research-manifest.v1",
        "as_of": "2026-09-02T00:00:00+00:00",
        "git_commit": "abc123",
        "inputs": [{"path": None}],
        "metadata": {},
        "manifest_id": "invalid",
    }

    report = verify_manifest(manifest, repo_root=tmp_path)

    assert report["ok"] is False
    assert "manifest_id mismatch" in report["errors"]
    assert report["files"] == [
        {"path": None, "ok": False, "reason": "malformed_record"}
    ]
