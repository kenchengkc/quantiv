#!/usr/bin/env python3
"""Build and verify content-addressed research manifests.

A manifest pins the exact local inputs that produced a research result. It is
small enough to commit, attach to an experiment, or ship next to an exported
research bundle, while still detecting any byte-level input drift.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = "quantiv.research-manifest.v1"


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _sha256_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _resolve_repo_path(repo_root: Path, raw_path: str | Path) -> tuple[Path, str]:
    root = repo_root.resolve()
    candidate = Path(raw_path)
    resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"path escapes repository root: {raw_path}") from exc
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    return resolved, relative.as_posix()


def _detect_git_commit(repo_root: Path) -> str | None:
    if value := os.environ.get("GITHUB_SHA"):
        return value
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def build_manifest(
    paths: list[str | Path],
    *,
    repo_root: Path,
    as_of: str | None = None,
    git_commit: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a deterministic manifest for the declared input files."""
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_path in paths:
        resolved, relative = _resolve_repo_path(repo_root, raw_path)
        if relative in seen:
            continue
        seen.add(relative)
        records.append(
            {
                "path": relative,
                "sha256": sha256_file(resolved),
                "bytes": resolved.stat().st_size,
            }
        )
    records.sort(key=lambda row: row["path"])
    body: dict[str, Any] = {
        "schema": SCHEMA,
        "as_of": as_of or datetime.now(UTC).isoformat(),
        "git_commit": git_commit if git_commit is not None else _detect_git_commit(repo_root),
        "inputs": records,
        "metadata": metadata or {},
    }
    body["manifest_id"] = _sha256_bytes(_canonical_json(body))
    return body


def verify_manifest(manifest: dict[str, Any], *, repo_root: Path) -> dict[str, Any]:
    """Verify both manifest identity and every declared input hash."""
    if manifest.get("schema") != SCHEMA:
        return {"ok": False, "errors": ["unsupported schema"], "files": []}

    expected_id = manifest.get("manifest_id")
    body = {key: value for key, value in manifest.items() if key != "manifest_id"}
    actual_id = _sha256_bytes(_canonical_json(body))
    errors: list[str] = []
    if expected_id != actual_id:
        errors.append("manifest_id mismatch")

    files: list[dict[str, Any]] = []
    for record in manifest.get("inputs", []):
        relative = record.get("path")
        try:
            resolved, normalized = _resolve_repo_path(repo_root, relative)
        except (FileNotFoundError, ValueError):
            files.append({"path": relative, "ok": False, "reason": "missing_or_unsafe"})
            continue
        actual_hash = sha256_file(resolved)
        actual_bytes = resolved.stat().st_size
        ok = actual_hash == record.get("sha256") and actual_bytes == record.get("bytes")
        files.append(
            {
                "path": normalized,
                "ok": ok,
                "expected_sha256": record.get("sha256"),
                "actual_sha256": actual_hash,
                "expected_bytes": record.get("bytes"),
                "actual_bytes": actual_bytes,
            }
        )
    return {"ok": not errors and all(row["ok"] for row in files), "errors": errors, "files": files}


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("manifest must be a JSON object")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="write a content-addressed manifest")
    build.add_argument("paths", nargs="+", help="input files relative to --repo-root")
    build.add_argument("--repo-root", type=Path, default=Path.cwd())
    build.add_argument("--out", type=Path, required=True)
    build.add_argument("--as-of")

    verify = subparsers.add_parser("verify", help="verify a manifest and its inputs")
    verify.add_argument("manifest", type=Path)
    verify.add_argument("--repo-root", type=Path, default=Path.cwd())

    args = parser.parse_args()
    if args.command == "build":
        payload = build_manifest(args.paths, repo_root=args.repo_root, as_of=args.as_of)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        print(payload["manifest_id"])
        return

    report = verify_manifest(_load_json(args.manifest), repo_root=args.repo_root)
    print(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()
