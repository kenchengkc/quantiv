from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts.frontend_release import (
    DEPLOYMENT_POINTER_SCHEMA,
    PUBLIC_MANIFEST,
    build_release,
    materialize_release,
    verify_release,
    write_deployment_pointer,
)


def _write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)


def test_release_is_content_addressed_deterministic_and_excludes_brand(tmp_path: Path) -> None:
    public = tmp_path / "public"
    output = tmp_path / "release"
    _write(public / "brand/logo.webp", b"source-controlled-brand")
    _write(public / "weekly.json", b'{"week":1}\n')
    _write(public / "symbols/AAPL.json", b'{"ticker":"AAPL"}\n')

    archive1, manifest1, pointer1, release1 = build_release(
        public, output, source_revision="abc123"
    )
    archive_bytes = archive1.read_bytes()
    os.utime(public / "weekly.json", (1_800_000_000, 1_800_000_000))
    archive2, manifest2, pointer2, release2 = build_release(
        public, output, source_revision="def456"
    )

    assert release1["release_id"] == release2["release_id"]
    assert archive1 == archive2
    assert archive2.read_bytes() == archive_bytes
    assert manifest1.read_bytes() == manifest2.read_bytes()
    assert pointer1 == pointer2
    assert all(not item["path"].startswith("brand/") for item in release1["files"])
    assert verify_release(output)["status"] == "passed"


def test_deployment_pointer_pins_verified_manifest_archive_and_source_revision(
    tmp_path: Path,
) -> None:
    public = tmp_path / "public"
    output = tmp_path / "release"
    deployment_pointer = tmp_path / "frontend-release.json"
    _write(public / "weekly.json", b'{"week":1}\n')
    archive, manifest, _current, release = build_release(
        public, output, source_revision="wrong-event-sha"
    )

    pinned = write_deployment_pointer(
        output,
        deployment_pointer,
        source_revision="actual-checkout-sha",
    )
    on_disk = json.loads(deployment_pointer.read_text())

    assert pinned == on_disk
    assert pinned["schema"] == DEPLOYMENT_POINTER_SCHEMA
    assert pinned["release_id"] == release["release_id"]
    assert pinned["source_revision"] == "actual-checkout-sha"
    assert pinned["manifest"]["path"] == f"manifests/{release['release_id']}.json"
    assert pinned["manifest"]["bytes"] == manifest.stat().st_size
    assert len(pinned["manifest"]["sha256"]) == 64
    assert pinned["archive"]["path"] == f"releases/{release['release_id']}.tar.gz"
    assert pinned["archive"]["bytes"] == archive.stat().st_size
    assert len(pinned["archive"]["sha256"]) == 64


def test_verify_rejects_tampered_archive(tmp_path: Path) -> None:
    public = tmp_path / "public"
    output = tmp_path / "release"
    _write(public / "weekly.json", b'{"week":1}\n')
    archive, _manifest, _pointer, _release = build_release(public, output)
    archive.write_bytes(archive.read_bytes() + b"tamper")

    with pytest.raises(RuntimeError, match="archive size mismatch|archive SHA-256 mismatch"):
        verify_release(output)


def test_materialize_replaces_generated_state_and_preserves_brand(tmp_path: Path) -> None:
    source = tmp_path / "source-public"
    output = tmp_path / "release"
    target = tmp_path / "target-public"
    _write(source / "weekly.json", b'{"fresh":true}\n')
    _write(source / "weeks/2026-09-07.json", b'{"events":[]}\n')
    _, manifest, _, release = build_release(source, output)

    _write(target / "brand/logo.webp", b"keep-me")
    _write(target / "weekly.json", b'{"stale":true}\n')
    _write(target / "symbols/STALE.json", b"{}\n")

    result = materialize_release(output, target)

    assert result["status"] == "passed"
    assert (target / "brand/logo.webp").read_bytes() == b"keep-me"
    assert (target / "weekly.json").read_bytes() == b'{"fresh":true}\n'
    assert (target / "weeks/2026-09-07.json").exists()
    assert not (target / "symbols/STALE.json").exists()
    assert (target / PUBLIC_MANIFEST).read_bytes() == manifest.read_bytes()
    # Repackaging materialized output must not recursively inventory its marker.
    _, _, _, rebuilt = build_release(target, tmp_path / "rebuilt")
    assert rebuilt["release_id"] == release["release_id"]


def test_failed_materialization_does_not_attest_unverified_release(tmp_path: Path) -> None:
    source = tmp_path / "source"
    output = tmp_path / "release"
    target = tmp_path / "target"
    _write(source / "weekly.json", b"{}\n")
    archive, _, _, _ = build_release(source, output)
    archive.write_bytes(b"corrupt")
    with pytest.raises(RuntimeError, match="archive size mismatch"):
        materialize_release(output, target)
    assert not (target / PUBLIC_MANIFEST).exists()
