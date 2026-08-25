#!/usr/bin/env python3
"""Build and verify content-addressed manifests for atomic data-lake promotion."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
RELEASE_SCHEMA = "quantiv.data-release.v1"
POINTER_SCHEMA = "quantiv.current-data-release.v1"
MUTABLE_PARQUET_ALIASES = {"parquet/vix/vix.parquet"}


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


def build_release(data_dir: Path) -> tuple[Path, Path, dict[str, Any]]:
    """Snapshot every immutable Parquet object into one versioned manifest."""
    parquet_root = data_dir / "parquet"
    candidates = sorted(parquet_root.rglob("*.parquet")) if parquet_root.exists() else []
    paths = [
        path
        for path in candidates
        if path.relative_to(data_dir).as_posix() not in MUTABLE_PARQUET_ALIASES
    ]
    if not paths:
        raise RuntimeError(f"no immutable Parquet objects found beneath {parquet_root}")
    files: list[dict[str, Any]] = []
    for path in paths:
        relative = path.relative_to(data_dir).as_posix()
        digest = _sha256_file(path)
        files.append(
            {
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": digest,
            }
        )
    core = {
        "schema": RELEASE_SCHEMA,
        "files": files,
        "file_count": len(files),
        "total_bytes": sum(int(item["bytes"]) for item in files),
    }
    release_id = _canonical_id(core)
    manifest_rel = f"control/releases/{release_id}.json"
    manifest_path = data_dir / manifest_rel
    if manifest_path.exists():
        # R2 partitions and their release manifests are immutable. Preserve an
        # already-pulled manifest byte-for-byte when the logical release is
        # unchanged; rewriting timestamps under the same content address makes
        # an idempotent retry look like a supply-chain collision.
        manifest = json.loads(manifest_path.read_text())
        existing_files = [
            {key: item[key] for key in ("path", "bytes", "sha256")}
            for item in manifest.get("files") or []
        ]
        existing_core = {
            "schema": manifest.get("schema"),
            "files": existing_files,
            "file_count": manifest.get("file_count"),
            "total_bytes": manifest.get("total_bytes"),
        }
        if _canonical_id(existing_core) != release_id:
            raise RuntimeError(
                f"release manifest collision at {manifest_path}; refusing overwrite"
            )
    else:
        # Versioned manifests contain only content identity. Operational time
        # belongs on the mutable pointer, not inside an immutable object.
        manifest = {"release_id": release_id, **core}
        _atomic_json(manifest_path, manifest)
    promoted_at = datetime.now(timezone.utc).isoformat()
    pointer = {
        "schema": POINTER_SCHEMA,
        "release_id": release_id,
        "manifest": manifest_rel,
        "promoted_at": promoted_at,
    }
    pointer_path = data_dir / "control" / "current_data_release.json"
    _atomic_json(pointer_path, pointer)
    return manifest_path, pointer_path, manifest


def verify_release(data_dir: Path, pointer_path: Path | None = None) -> dict[str, Any]:
    pointer_path = pointer_path or data_dir / "control" / "current_data_release.json"
    pointer = json.loads(pointer_path.read_text())
    if pointer.get("schema") != POINTER_SCHEMA:
        raise RuntimeError("data-release pointer schema is unsupported")
    manifest_path = data_dir / str(pointer["manifest"])
    manifest = json.loads(manifest_path.read_text())
    identity_files = [
        {key: item[key] for key in ("path", "bytes", "sha256")}
        for item in manifest.get("files") or []
    ]
    core = {
        "schema": manifest.get("schema"),
        "files": identity_files,
        "file_count": manifest.get("file_count"),
        "total_bytes": manifest.get("total_bytes"),
    }
    calculated_release_id = _canonical_id(core)
    if calculated_release_id != pointer.get("release_id"):
        raise RuntimeError("release pointer does not match the manifest contents")
    errors: list[str] = []
    for item in manifest.get("files") or []:
        path = data_dir / str(item["path"])
        if not path.is_file():
            errors.append(f"missing:{item['path']}")
            continue
        if path.stat().st_size != int(item["bytes"]):
            errors.append(f"size:{item['path']}")
            continue
        if _sha256_file(path) != item["sha256"]:
            errors.append(f"sha256:{item['path']}")
    if errors:
        sample = ", ".join(errors[:10])
        raise RuntimeError(f"data release verification failed ({len(errors)}): {sample}")
    return {
        "status": "passed",
        "release_id": calculated_release_id,
        "files": int(manifest["file_count"]),
        "bytes": int(manifest["total_bytes"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build", "verify"))
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--pointer", type=Path, default=None)
    args = parser.parse_args()
    data_dir = args.data_dir or Path(os.getenv("DATA_DIR", REPO_ROOT / "data"))
    if args.command == "build":
        manifest_path, pointer_path, manifest = build_release(data_dir)
        print(
            f"Data release {manifest['release_id']}: {manifest['file_count']:,} files, "
            f"{manifest['total_bytes']:,} bytes"
        )
        print(f"Manifest: {manifest_path}")
        print(f"Pending pointer: {pointer_path}")
    else:
        result = verify_release(data_dir, args.pointer)
        print(
            f"Verified data release {result['release_id']}: "
            f"{result['files']:,} files, {result['bytes']:,} bytes"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
