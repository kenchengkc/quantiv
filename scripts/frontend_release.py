#!/usr/bin/env python3
"""Build, verify, pin, and materialize immutable frontend publication releases.

The release contains generated browser-safe files beneath ``apps/frontend/public``
except ``brand/``, which is source-controlled static design material. A logical
release ID is the SHA-256 of the sorted file inventory, not a timestamp. The
archive is deterministic, immutable, and safe to reproduce from any checkout.

``current.json`` is the mutable R2 discovery pointer. The source-controlled
``apps/frontend/frontend-release.json`` is a separate deployment pointer: it
pins an exact immutable manifest/archive pair (including SHA-256 digests) so a
Git revision cannot silently resolve to different frontend bytes later.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PUBLIC_DIR = REPO_ROOT / "apps" / "frontend" / "public"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data" / "frontend_publication"
DEFAULT_DEPLOYMENT_POINTER = REPO_ROOT / "apps" / "frontend" / "frontend-release.json"
RELEASE_SCHEMA = "quantiv.frontend-release.v1"
POINTER_SCHEMA = "quantiv.current-frontend-release.v1"
DEPLOYMENT_POINTER_SCHEMA = "quantiv.frontend-deployment.v1"
SOURCE_CONTROLLED_PREFIXES = ("brand/",)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_id(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def _is_generated(relative: str) -> bool:
    return not any(
        relative == prefix.rstrip("/") or relative.startswith(prefix)
        for prefix in SOURCE_CONTROLLED_PREFIXES
    )


def _inventory(public_dir: Path) -> list[dict[str, Any]]:
    if not public_dir.is_dir():
        raise RuntimeError(f"frontend public directory does not exist: {public_dir}")
    files: list[dict[str, Any]] = []
    for path in sorted(public_dir.rglob("*")):
        if path.is_symlink():
            raise RuntimeError(f"symlinks are not allowed in frontend releases: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(public_dir).as_posix()
        if not _is_generated(relative):
            continue
        files.append(
            {
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    if not files:
        raise RuntimeError(f"no generated frontend publication files found in {public_dir}")
    return files


def _write_deterministic_archive(
    public_dir: Path, files: list[dict[str, Any]], archive_path: Path
) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = archive_path.with_name(f".{archive_path.name}.{os.getpid()}.tmp")
    with temporary.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
                for item in files:
                    relative = str(item["path"])
                    source = public_dir / relative
                    data = source.read_bytes()
                    info = tarfile.TarInfo(name=relative)
                    info.size = len(data)
                    info.mtime = 0
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.mode = 0o644
                    tar.addfile(info, io.BytesIO(data))
    os.replace(temporary, archive_path)


def build_release(
    public_dir: Path = DEFAULT_PUBLIC_DIR,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    *,
    source_revision: str | None = None,
) -> tuple[Path, Path, Path, dict[str, Any]]:
    files = _inventory(public_dir)
    core = {
        "schema": RELEASE_SCHEMA,
        "files": files,
        "file_count": len(files),
        "total_bytes": sum(int(item["bytes"]) for item in files),
    }
    release_id = _canonical_id(core)
    archive_rel = f"releases/{release_id}.tar.gz"
    archive_path = output_dir / archive_rel
    if not archive_path.exists():
        _write_deterministic_archive(public_dir, files, archive_path)
    archive_sha = _sha256_file(archive_path)

    manifest_rel = f"manifests/{release_id}.json"
    manifest_path = output_dir / manifest_rel
    manifest = {
        "release_id": release_id,
        **core,
        "archive": {
            "path": archive_rel,
            "bytes": archive_path.stat().st_size,
            "sha256": archive_sha,
        },
    }
    if manifest_path.exists():
        existing = json.loads(manifest_path.read_text())
        if existing != manifest:
            raise RuntimeError(
                f"frontend release manifest collision at {manifest_path}; refusing overwrite"
            )
    else:
        _atomic_json(manifest_path, manifest)

    pointer = {
        "schema": POINTER_SCHEMA,
        "release_id": release_id,
        "manifest": manifest_rel,
        "promoted_at": datetime.now(timezone.utc).isoformat(),
    }
    if source_revision:
        pointer["source_revision"] = source_revision
    pointer_path = output_dir / "current.json"
    _atomic_json(pointer_path, pointer)
    return archive_path, manifest_path, pointer_path, manifest


def _load_release(
    output_dir: Path, pointer_path: Path | None = None
) -> tuple[dict, dict, Path]:
    pointer_path = pointer_path or output_dir / "current.json"
    pointer = json.loads(pointer_path.read_text())
    if pointer.get("schema") != POINTER_SCHEMA:
        raise RuntimeError("frontend release pointer schema is unsupported")
    manifest_path = output_dir / str(pointer.get("manifest"))
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema") != RELEASE_SCHEMA:
        raise RuntimeError("frontend release manifest schema is unsupported")
    identity = {
        "schema": manifest.get("schema"),
        "files": [
            {key: item[key] for key in ("path", "bytes", "sha256")}
            for item in manifest.get("files") or []
        ],
        "file_count": manifest.get("file_count"),
        "total_bytes": manifest.get("total_bytes"),
    }
    calculated = _canonical_id(identity)
    if calculated != pointer.get("release_id") or calculated != manifest.get("release_id"):
        raise RuntimeError("frontend release pointer/manifest identity mismatch")
    archive_path = output_dir / str(manifest["archive"]["path"])
    return pointer, manifest, archive_path


def write_deployment_pointer(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    deployment_pointer: Path = DEFAULT_DEPLOYMENT_POINTER,
    *,
    source_revision: str | None = None,
) -> dict[str, Any]:
    """Pin the locally verified immutable release for deterministic deployment."""
    pointer, manifest, archive_path = _load_release(output_dir)
    manifest_path = output_dir / str(pointer["manifest"])
    if not manifest_path.is_file() or not archive_path.is_file():
        raise RuntimeError("frontend release must be fully materialized before it can be pinned")

    resolved_source_revision = source_revision or pointer.get("source_revision")
    pinned: dict[str, Any] = {
        "schema": DEPLOYMENT_POINTER_SCHEMA,
        "release_id": pointer["release_id"],
        "manifest": {
            "path": str(pointer["manifest"]),
            "bytes": manifest_path.stat().st_size,
            "sha256": _sha256_file(manifest_path),
        },
        "archive": {
            "path": str(manifest["archive"]["path"]),
            "bytes": int(manifest["archive"]["bytes"]),
            "sha256": str(manifest["archive"]["sha256"]),
        },
        "file_count": int(manifest["file_count"]),
        "total_bytes": int(manifest["total_bytes"]),
    }
    if resolved_source_revision:
        pinned["source_revision"] = str(resolved_source_revision)
    _atomic_json(deployment_pointer, pinned)
    return pinned


def _safe_member_name(name: str) -> str:
    pure = PurePosixPath(name)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        raise RuntimeError(f"unsafe frontend archive member: {name!r}")
    normalized = pure.as_posix()
    if not _is_generated(normalized):
        raise RuntimeError(f"source-controlled path present in frontend archive: {normalized}")
    return normalized


def verify_release(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    pointer_path: Path | None = None,
) -> dict[str, Any]:
    pointer, manifest, archive_path = _load_release(output_dir, pointer_path)
    archive = manifest["archive"]
    if not archive_path.is_file():
        raise RuntimeError(f"frontend release archive missing: {archive_path}")
    if archive_path.stat().st_size != int(archive["bytes"]):
        raise RuntimeError("frontend release archive size mismatch")
    if _sha256_file(archive_path) != archive["sha256"]:
        raise RuntimeError("frontend release archive SHA-256 mismatch")

    expected = {str(item["path"]): item for item in manifest.get("files") or []}
    seen: set[str] = set()
    with tarfile.open(archive_path, mode="r:gz") as tar:
        for member in tar:
            name = _safe_member_name(member.name)
            if not member.isfile():
                raise RuntimeError(f"non-file member in frontend archive: {name}")
            if name in seen:
                raise RuntimeError(f"duplicate member in frontend archive: {name}")
            seen.add(name)
            item = expected.get(name)
            if item is None:
                raise RuntimeError(f"unexpected member in frontend archive: {name}")
            handle = tar.extractfile(member)
            if handle is None:
                raise RuntimeError(f"unable to read frontend archive member: {name}")
            data = handle.read()
            if len(data) != int(item["bytes"]):
                raise RuntimeError(f"frontend archive size mismatch: {name}")
            if hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise RuntimeError(f"frontend archive SHA-256 mismatch: {name}")
    missing = sorted(set(expected) - seen)
    if missing:
        raise RuntimeError(f"frontend archive is missing {len(missing)} files: {missing[:10]}")
    return {
        "status": "passed",
        "release_id": pointer["release_id"],
        "files": int(manifest["file_count"]),
        "bytes": int(manifest["total_bytes"]),
        "archive_bytes": int(archive["bytes"]),
    }


def materialize_release(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    public_dir: Path = DEFAULT_PUBLIC_DIR,
    pointer_path: Path | None = None,
) -> dict[str, Any]:
    result = verify_release(output_dir, pointer_path)
    _, manifest, archive_path = _load_release(output_dir, pointer_path)
    expected = {str(item["path"]) for item in manifest.get("files") or []}
    public_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="quantiv-frontend-release-") as tmp:
        temp_root = Path(tmp)
        with tarfile.open(archive_path, mode="r:gz") as tar:
            for member in tar:
                name = _safe_member_name(member.name)
                handle = tar.extractfile(member)
                if handle is None:
                    raise RuntimeError(f"unable to extract frontend archive member: {name}")
                target = temp_root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(handle.read())

        # Remove only generated publication state. Static source-controlled brand
        # assets remain untouched throughout materialization.
        for path in sorted(public_dir.rglob("*"), reverse=True):
            if path.is_file() and _is_generated(path.relative_to(public_dir).as_posix()):
                path.unlink()
        for source in sorted(temp_root.rglob("*")):
            if not source.is_file():
                continue
            relative = source.relative_to(temp_root)
            target = public_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)

    materialized = {
        path.relative_to(public_dir).as_posix()
        for path in public_dir.rglob("*")
        if path.is_file() and _is_generated(path.relative_to(public_dir).as_posix())
    }
    if materialized != expected:
        raise RuntimeError("materialized frontend publication does not match release inventory")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build", "verify", "materialize", "pin"))
    parser.add_argument("--public-dir", type=Path, default=DEFAULT_PUBLIC_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--pointer", type=Path, default=None)
    parser.add_argument("--deployment-pointer", type=Path, default=DEFAULT_DEPLOYMENT_POINTER)
    parser.add_argument("--source-revision", default=os.getenv("FRONTEND_SOURCE_REVISION"))
    args = parser.parse_args()

    if args.command == "build":
        archive, manifest, pointer, release = build_release(
            args.public_dir,
            args.output_dir,
            source_revision=args.source_revision,
        )
        print(
            f"Frontend release {release['release_id']}: {release['file_count']:,} files, "
            f"{release['total_bytes']:,} bytes"
        )
        print(f"Archive: {archive}")
        print(f"Manifest: {manifest}")
        print(f"Pending pointer: {pointer}")
    elif args.command == "verify":
        result = verify_release(args.output_dir, args.pointer)
        print(
            f"Verified frontend release {result['release_id']}: "
            f"{result['files']:,} files, {result['bytes']:,} bytes"
        )
    elif args.command == "pin":
        result = write_deployment_pointer(
            args.output_dir,
            args.deployment_pointer,
            source_revision=args.source_revision,
        )
        print(
            f"Pinned frontend release {result['release_id']} -> {args.deployment_pointer}"
        )
    else:
        result = materialize_release(args.output_dir, args.public_dir, args.pointer)
        print(
            f"Materialized frontend release {result['release_id']}: "
            f"{result['files']:,} files"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
