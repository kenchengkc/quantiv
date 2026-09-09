#!/usr/bin/env python3
"""Build, verify, and materialize Quantiv cross-run runtime state.

These files are operational restart/cursor/cache state. They are not source code
and must not be committed to Git. A content-addressed archive and manifest are
published immutably to R2; ``runtime-state/current.json`` is promoted last.
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
DEFAULT_DATA_DIR = REPO_ROOT / "data"
DEFAULT_OUTPUT_DIR = DEFAULT_DATA_DIR / "runtime_state_release"
RELEASE_SCHEMA = "quantiv.runtime-state-release.v1"
POINTER_SCHEMA = "quantiv.current-runtime-state.v1"
STATE_FILES = (
    "market_caps.json",
    "popular_snapshot.json",
    "popular_changes.jsonl",
    "fmp_earnings_metadata.json",
    "fmp_earnings_backfill_state.json",
    "delisting_watch.json",
)


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


def _inventory(data_dir: Path) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for relative in STATE_FILES:
        path = data_dir / relative
        if not path.exists():
            continue
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"runtime state must be a regular file: {path}")
        files.append(
            {
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    if not files:
        raise RuntimeError(f"no runtime-state files found beneath {data_dir}")
    return files


def _write_deterministic_archive(
    data_dir: Path, files: list[dict[str, Any]], archive_path: Path
) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = archive_path.with_name(f".{archive_path.name}.{os.getpid()}.tmp")
    with temporary.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
                for item in files:
                    relative = str(item["path"])
                    payload = (data_dir / relative).read_bytes()
                    info = tarfile.TarInfo(name=relative)
                    info.size = len(payload)
                    info.mtime = 0
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.mode = 0o644
                    tar.addfile(info, io.BytesIO(payload))
    os.replace(temporary, archive_path)


def build_release(
    data_dir: Path = DEFAULT_DATA_DIR,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    *,
    source_revision: str | None = None,
) -> tuple[Path, Path, Path, dict[str, Any]]:
    files = _inventory(data_dir)
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
        _write_deterministic_archive(data_dir, files, archive_path)

    manifest_rel = f"manifests/{release_id}.json"
    manifest_path = output_dir / manifest_rel
    manifest = {
        "release_id": release_id,
        **core,
        "archive": {
            "path": archive_rel,
            "bytes": archive_path.stat().st_size,
            "sha256": _sha256_file(archive_path),
        },
    }
    if manifest_path.exists():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if existing != manifest:
            raise RuntimeError(f"runtime-state manifest collision: {manifest_path}")
    else:
        _atomic_json(manifest_path, manifest)

    pointer: dict[str, Any] = {
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


def _safe_member_name(name: str) -> str:
    pure = PurePosixPath(name)
    if pure.is_absolute() or ".." in pure.parts or len(pure.parts) != 1:
        raise RuntimeError(f"unsafe runtime-state archive member: {name!r}")
    normalized = pure.as_posix()
    if normalized not in STATE_FILES:
        raise RuntimeError(f"unexpected runtime-state archive member: {normalized}")
    return normalized


def _load_release(
    output_dir: Path, pointer_path: Path | None = None
) -> tuple[dict[str, Any], dict[str, Any], Path]:
    pointer_path = pointer_path or output_dir / "current.json"
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    if pointer.get("schema") != POINTER_SCHEMA:
        raise RuntimeError("runtime-state pointer schema is unsupported")
    manifest_path = output_dir / str(pointer.get("manifest"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != RELEASE_SCHEMA:
        raise RuntimeError("runtime-state manifest schema is unsupported")
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
        raise RuntimeError("runtime-state pointer/manifest identity mismatch")
    archive_path = output_dir / str(manifest["archive"]["path"])
    return pointer, manifest, archive_path


def verify_release(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    pointer_path: Path | None = None,
) -> dict[str, Any]:
    pointer, manifest, archive_path = _load_release(output_dir, pointer_path)
    archive = manifest["archive"]
    if not archive_path.is_file():
        raise RuntimeError(f"runtime-state archive missing: {archive_path}")
    if archive_path.stat().st_size != int(archive["bytes"]):
        raise RuntimeError("runtime-state archive size mismatch")
    if _sha256_file(archive_path) != str(archive["sha256"]):
        raise RuntimeError("runtime-state archive SHA-256 mismatch")

    expected = {str(item["path"]): item for item in manifest.get("files") or []}
    if set(expected) - set(STATE_FILES):
        raise RuntimeError("runtime-state manifest contains unsupported paths")
    if int(manifest.get("file_count", -1)) != len(expected):
        raise RuntimeError("runtime-state manifest file count mismatch")
    if int(manifest.get("total_bytes", -1)) != sum(
        int(item["bytes"]) for item in expected.values()
    ):
        raise RuntimeError("runtime-state manifest byte count mismatch")

    seen: set[str] = set()
    with tarfile.open(archive_path, mode="r:gz") as tar:
        for member in tar:
            name = _safe_member_name(member.name)
            if name in seen or name not in expected:
                raise RuntimeError(f"unexpected/duplicate runtime-state member: {name}")
            if not member.isfile():
                raise RuntimeError(f"runtime-state archive member is not a file: {name}")
            extracted = tar.extractfile(member)
            if extracted is None:
                raise RuntimeError(f"unable to read runtime-state member: {name}")
            payload = extracted.read()
            item = expected[name]
            if len(payload) != int(item["bytes"]):
                raise RuntimeError(f"runtime-state member size mismatch: {name}")
            if hashlib.sha256(payload).hexdigest() != str(item["sha256"]):
                raise RuntimeError(f"runtime-state member SHA-256 mismatch: {name}")
            seen.add(name)
    if seen != set(expected):
        raise RuntimeError("runtime-state archive inventory mismatch")
    return pointer


def materialize_release(
    data_dir: Path = DEFAULT_DATA_DIR,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    pointer_path: Path | None = None,
) -> dict[str, Any]:
    pointer = verify_release(output_dir, pointer_path)
    _, manifest, archive_path = _load_release(output_dir, pointer_path)
    expected = {str(item["path"]) for item in manifest.get("files") or []}
    data_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="quantiv-runtime-state-") as tmp_name:
        tmp = Path(tmp_name)
        with tarfile.open(archive_path, mode="r:gz") as tar:
            for member in tar:
                name = _safe_member_name(member.name)
                extracted = tar.extractfile(member)
                if extracted is None:
                    raise RuntimeError(f"unable to materialize runtime-state member: {name}")
                (tmp / name).write_bytes(extracted.read())

        for relative in STATE_FILES:
            target = data_dir / relative
            if relative in expected:
                source = tmp / relative
                temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
                shutil.copyfile(source, temporary)
                os.replace(temporary, target)
            elif target.exists():
                target.unlink()
    return pointer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("build", "verify", "materialize"):
        child = subparsers.add_parser(name)
        child.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
        child.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
        child.add_argument("--pointer", type=Path)
        if name == "build":
            child.add_argument("--source-revision")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "build":
            archive, manifest, pointer, payload = build_release(
                args.data_dir,
                args.output_dir,
                source_revision=args.source_revision,
            )
            print(
                json.dumps(
                    {
                        "release_id": payload["release_id"],
                        "archive": str(archive),
                        "manifest": str(manifest),
                        "pointer": str(pointer),
                        "file_count": payload["file_count"],
                        "total_bytes": payload["total_bytes"],
                    },
                    sort_keys=True,
                )
            )
        elif args.command == "verify":
            pointer = verify_release(args.output_dir, args.pointer)
            print(f"verified runtime-state release {pointer['release_id']}")
        else:
            pointer = materialize_release(
                args.data_dir,
                args.output_dir,
                args.pointer,
            )
            print(f"materialized runtime-state release {pointer['release_id']}")
        return 0
    except (OSError, RuntimeError, KeyError, ValueError, json.JSONDecodeError) as exc:
        print(f"runtime-state release failed: {exc}", file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
