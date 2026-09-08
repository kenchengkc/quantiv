#!/usr/bin/env bash
# Publish the generated frontend corpus as an immutable, content-addressed R2 release.
# The immutable archive and manifest are uploaded first; current.json is the only
# mutable R2 object and is written last after local verification. After R2 readback
# succeeds, write a tiny source-controlled deployment pointer that pins the exact
# immutable manifest/archive digests for deterministic Vercel materialization.

set -euo pipefail

PUBLIC_DIR="${PUBLIC_DIR:-apps/frontend/public}"
STAGE_DIR="${FRONTEND_RELEASE_DIR:-data/frontend_publication}"
DEPLOYMENT_POINTER="${FRONTEND_DEPLOYMENT_POINTER:-apps/frontend/frontend-release.json}"
REMOTE="${R2_REMOTE:-r2:${R2_BUCKET:-quantiv-data}}"
PYTHON_BIN="${PYTHON_BIN:-python}"
SOURCE_REVISION="${FRONTEND_SOURCE_REVISION:-}"

if [ -z "$SOURCE_REVISION" ]; then
  SOURCE_REVISION="$(git rev-parse HEAD 2>/dev/null || true)"
fi
if [ -z "$SOURCE_REVISION" ]; then
  SOURCE_REVISION="local"
fi

"$PYTHON_BIN" scripts/frontend_release.py build \
  --public-dir "$PUBLIC_DIR" \
  --output-dir "$STAGE_DIR" \
  --source-revision "$SOURCE_REVISION"
"$PYTHON_BIN" scripts/frontend_release.py verify --output-dir "$STAGE_DIR"

RELEASE_ID=$(jq -er .release_id "$STAGE_DIR/current.json")
MANIFEST_REL=$(jq -er .manifest "$STAGE_DIR/current.json")
ARCHIVE_REL=$(jq -er .archive.path "$STAGE_DIR/$MANIFEST_REL")

# Immutable content first. --immutable makes a same-key/different-byte collision
# fail instead of silently replacing history.
rclone copyto "$STAGE_DIR/$ARCHIVE_REL" "$REMOTE/frontend/$ARCHIVE_REL" --immutable
rclone copyto "$STAGE_DIR/$MANIFEST_REL" "$REMOTE/frontend/$MANIFEST_REL" --immutable

# Read back the immutable control object before pointer promotion.
READBACK_MANIFEST="$STAGE_DIR/.manifest-readback.json"
rclone copyto "$REMOTE/frontend/$MANIFEST_REL" "$READBACK_MANIFEST"
cmp "$STAGE_DIR/$MANIFEST_REL" "$READBACK_MANIFEST"
rm -f "$READBACK_MANIFEST"

# Pointer last: consumers can only discover a release after every immutable
# object has been uploaded and verified.
rclone copyto "$STAGE_DIR/current.json" "$REMOTE/frontend/current.json"
READBACK_POINTER="$STAGE_DIR/.pointer-readback.json"
rclone copyto "$REMOTE/frontend/current.json" "$READBACK_POINTER"
cmp "$STAGE_DIR/current.json" "$READBACK_POINTER"
rm -f "$READBACK_POINTER"

# Git carries only this compact immutable deployment reference, never the corpus.
# Write it only after the remote pointer readback passes so a commit cannot refer
# to an R2 release that was not fully promoted.
"$PYTHON_BIN" scripts/frontend_release.py pin \
  --output-dir "$STAGE_DIR" \
  --deployment-pointer "$DEPLOYMENT_POINTER" \
  --source-revision "$SOURCE_REVISION"

echo "Published frontend release $RELEASE_ID to $REMOTE/frontend"
echo "Pinned frontend deployment pointer at $DEPLOYMENT_POINTER (source $SOURCE_REVISION)"
