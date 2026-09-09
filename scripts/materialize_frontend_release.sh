#!/usr/bin/env bash
# Materialize the exact Git-pinned frontend publication from private R2.
#
# Transition behavior: while generated JSON is still source-controlled, builds
# without R2 read credentials use that checked-in corpus. Once the generated
# corpus is removed, the fallback disappears automatically and missing R2 access
# becomes fail-closed. Set FRONTEND_RELEASE_REQUIRED=1 to force fail-closed mode
# before source-control eviction (useful for staging verification).

set -euo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PUBLIC_DIR="${PUBLIC_DIR:-$REPO_ROOT/apps/frontend/public}"
DEPLOYMENT_POINTER="${FRONTEND_DEPLOYMENT_POINTER:-$REPO_ROOT/apps/frontend/frontend-release.json}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
R2_BUCKET="${R2_BUCKET:-quantiv-data}"
FRONTEND_R2_REMOTE="${FRONTEND_R2_REMOTE:-r2:${R2_BUCKET}/frontend}"
REQUIRED="${FRONTEND_RELEASE_REQUIRED:-0}"
RCLONE_BIN="${RCLONE_BIN:-}"

fallback_available() {
  [ -f "$PUBLIC_DIR/weekly.json" ] && [ -d "$PUBLIC_DIR/symbols" ]
}

fallback_or_fail() {
  local reason="$1"
  if [ "$REQUIRED" != "1" ] && fallback_available; then
    # A local fallback is not proof that the Git-pinned release was restored.
    # Do not carry a previous build's attestation into an unverified build.
    rm -f "$PUBLIC_DIR/frontend-release-manifest.json"
    echo "Frontend R2 materialization skipped: $reason; using source-controlled publication fallback."
    return 0
  fi
  echo "Frontend R2 materialization required but unavailable: $reason" >&2
  exit 1
}

if [ ! -f "$DEPLOYMENT_POINTER" ]; then
  fallback_or_fail "deployment pointer missing at $DEPLOYMENT_POINTER"
  exit 0
fi

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  fallback_or_fail "Python interpreter '$PYTHON_BIN' is unavailable"
  exit 0
fi

# Tests and controlled environments may inject an already-configured rclone
# implementation. Hosted builds use dedicated read-only R2 credentials.
if [ -z "$RCLONE_BIN" ]; then
  if [ -z "${R2_ACCOUNT_ID:-}" ] || [ -z "${R2_ACCESS_KEY_ID:-}" ] || [ -z "${R2_SECRET_ACCESS_KEY:-}" ]; then
    fallback_or_fail "R2 read credentials are not configured"
    exit 0
  fi
fi

# Validate the Git control object before making any remote request. Object paths
# are deterministic functions of release_id, preventing the pointer from being
# abused as an arbitrary R2 path selector.
eval "$("$PYTHON_BIN" - "$DEPLOYMENT_POINTER" <<'PY'
import json
from pathlib import Path
import re
import shlex
import sys

pointer_path = Path(sys.argv[1])
pointer = json.loads(pointer_path.read_text())
if pointer.get("schema") != "quantiv.frontend-deployment.v1":
    raise SystemExit("unsupported frontend deployment pointer schema")
release_id = str(pointer.get("release_id") or "")
if not re.fullmatch(r"[0-9a-f]{64}", release_id):
    raise SystemExit("invalid frontend release_id")
manifest = pointer.get("manifest") or {}
archive = pointer.get("archive") or {}
manifest_path = str(manifest.get("path") or "")
archive_path = str(archive.get("path") or "")
if manifest_path != f"manifests/{release_id}.json":
    raise SystemExit("frontend manifest path does not match release_id")
if archive_path != f"releases/{release_id}.tar.gz":
    raise SystemExit("frontend archive path does not match release_id")
for label, payload in (("manifest", manifest), ("archive", archive)):
    sha = str(payload.get("sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise SystemExit(f"invalid {label} SHA-256")
    size = payload.get("bytes")
    if not isinstance(size, int) or size <= 0:
        raise SystemExit(f"invalid {label} byte count")
values = {
    "RELEASE_ID": release_id,
    "MANIFEST_PATH": manifest_path,
    "MANIFEST_SHA": str(manifest["sha256"]),
    "MANIFEST_BYTES": str(manifest["bytes"]),
    "ARCHIVE_PATH": archive_path,
    "ARCHIVE_SHA": str(archive["sha256"]),
    "ARCHIVE_BYTES": str(archive["bytes"]),
    "SOURCE_REVISION": str(pointer.get("source_revision") or ""),
}
for key, value in values.items():
    print(f"{key}={shlex.quote(value)}")
PY
)"

TMP_ROOT="$(mktemp -d -t quantiv-frontend-release.XXXXXX)"
trap 'rm -rf "$TMP_ROOT"' EXIT
STAGE_DIR="$TMP_ROOT/release"
mkdir -p "$STAGE_DIR/$(dirname "$MANIFEST_PATH")" "$STAGE_DIR/$(dirname "$ARCHIVE_PATH")"

RCLONE_CONFIG=""
if [ -z "$RCLONE_BIN" ]; then
  RCLONE_INSTALL_DIR="$TMP_ROOT/bin" GITHUB_PATH=/dev/null bash "$REPO_ROOT/scripts/install_rclone.sh"
  RCLONE_BIN="$TMP_ROOT/bin/rclone"
  RCLONE_CONFIG="$TMP_ROOT/rclone.conf"
  cat > "$RCLONE_CONFIG" <<EOF
[r2]
type = s3
provider = Cloudflare
access_key_id = $R2_ACCESS_KEY_ID
secret_access_key = $R2_SECRET_ACCESS_KEY
endpoint = https://$R2_ACCOUNT_ID.r2.cloudflarestorage.com
region = auto
EOF
fi

rclone_copyto() {
  if [ -n "$RCLONE_CONFIG" ]; then
    "$RCLONE_BIN" --config "$RCLONE_CONFIG" copyto "$1" "$2"
  else
    "$RCLONE_BIN" copyto "$1" "$2"
  fi
}

MANIFEST_LOCAL="$STAGE_DIR/$MANIFEST_PATH"
ARCHIVE_LOCAL="$STAGE_DIR/$ARCHIVE_PATH"
rclone_copyto "$FRONTEND_R2_REMOTE/$MANIFEST_PATH" "$MANIFEST_LOCAL"
rclone_copyto "$FRONTEND_R2_REMOTE/$ARCHIVE_PATH" "$ARCHIVE_LOCAL"

"$PYTHON_BIN" - "$MANIFEST_LOCAL" "$MANIFEST_SHA" "$MANIFEST_BYTES" "$ARCHIVE_LOCAL" "$ARCHIVE_SHA" "$ARCHIVE_BYTES" <<'PY'
from pathlib import Path
import hashlib
import sys


def verify(path_s: str, expected_sha: str, expected_bytes: str, label: str) -> None:
    path = Path(path_s)
    data = path.read_bytes()
    if len(data) != int(expected_bytes):
        raise SystemExit(f"{label} byte count mismatch")
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected_sha:
        raise SystemExit(f"{label} SHA-256 mismatch")

verify(sys.argv[1], sys.argv[2], sys.argv[3], "frontend manifest")
verify(sys.argv[4], sys.argv[5], sys.argv[6], "frontend archive")
PY

# Reconstruct the mutable-pointer shape expected by the existing release verifier,
# but only from the immutable Git control object already validated above.
"$PYTHON_BIN" - "$STAGE_DIR/current.json" "$RELEASE_ID" "$MANIFEST_PATH" "$SOURCE_REVISION" <<'PY'
from pathlib import Path
import json
import sys

payload = {
    "schema": "quantiv.current-frontend-release.v1",
    "release_id": sys.argv[2],
    "manifest": sys.argv[3],
}
if sys.argv[4]:
    payload["source_revision"] = sys.argv[4]
Path(sys.argv[1]).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
PY

"$PYTHON_BIN" "$REPO_ROOT/scripts/frontend_release.py" verify --output-dir "$STAGE_DIR"
"$PYTHON_BIN" "$REPO_ROOT/scripts/frontend_release.py" materialize \
  --output-dir "$STAGE_DIR" \
  --public-dir "$PUBLIC_DIR"

echo "Materialized Git-pinned frontend release $RELEASE_ID from $FRONTEND_R2_REMOTE"
