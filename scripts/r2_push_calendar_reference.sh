#!/usr/bin/env bash
# Publish the validated dates/session-only calendar reference independently of
# options/research artifacts. Immutable release + receipt first, current pointer
# last, then project the verified release into the frontend public corpus.
set -euo pipefail

STAGE_DIR="${CALENDAR_REFERENCE_DIR:-data/calendar_reference}"
REMOTE="${R2_REMOTE:-r2:${R2_BUCKET:-quantiv-data}}"
PREFIX="${CALENDAR_REFERENCE_PREFIX:-calendar-reference}"
PYTHON_BIN="${PYTHON_BIN:-python}"
SOURCE_REVISION="${CALENDAR_REFERENCE_SOURCE_REVISION:-}"
NOT_BEFORE="${CALENDAR_REFERENCE_NOT_BEFORE:-${REFRESH_STARTED_AT:-}}"

if [ -z "$SOURCE_REVISION" ]; then
  SOURCE_REVISION="$(git rev-parse HEAD 2>/dev/null || true)"
fi
if [ -z "$SOURCE_REVISION" ]; then
  SOURCE_REVISION="local"
fi
if [ -z "$NOT_BEFORE" ]; then
  echo "CALENDAR_REFERENCE_NOT_BEFORE or REFRESH_STARTED_AT is required" >&2
  exit 2
fi

"$PYTHON_BIN" scripts/calendar_reference.py build \
  --output-dir "$STAGE_DIR" \
  --source-revision "$SOURCE_REVISION" \
  --not-before "$NOT_BEFORE"
"$PYTHON_BIN" scripts/calendar_reference.py verify --output-dir "$STAGE_DIR"

RELEASE_ID=$(jq -er .release_id "$STAGE_DIR/current.json")
RELEASE_REL=$(jq -er .release "$STAGE_DIR/current.json")
RECEIPT_ID=$(jq -er .receipt_id "$STAGE_DIR/current.json")
RECEIPT_REL=$(jq -er .receipt "$STAGE_DIR/current.json")

# Immutable objects first. A same-key/different-byte collision fails closed.
rclone copyto "$STAGE_DIR/$RELEASE_REL" "$REMOTE/$PREFIX/$RELEASE_REL" --immutable
rclone copyto "$STAGE_DIR/$RECEIPT_REL" "$REMOTE/$PREFIX/$RECEIPT_REL" --immutable

# Read back both immutable control objects before pointer promotion.
READBACK_RELEASE="$STAGE_DIR/.release-readback.json"
READBACK_RECEIPT="$STAGE_DIR/.receipt-readback.json"
rclone copyto "$REMOTE/$PREFIX/$RELEASE_REL" "$READBACK_RELEASE"
rclone copyto "$REMOTE/$PREFIX/$RECEIPT_REL" "$READBACK_RECEIPT"
cmp "$STAGE_DIR/$RELEASE_REL" "$READBACK_RELEASE"
cmp "$STAGE_DIR/$RECEIPT_REL" "$READBACK_RECEIPT"
rm -f "$READBACK_RELEASE" "$READBACK_RECEIPT"

# Pointer last. Consumers cannot discover unverified/incomplete bytes.
rclone copyto "$STAGE_DIR/current.json" "$REMOTE/$PREFIX/current.json"
READBACK_POINTER="$STAGE_DIR/.pointer-readback.json"
rclone copyto "$REMOTE/$PREFIX/current.json" "$READBACK_POINTER"
cmp "$STAGE_DIR/current.json" "$READBACK_POINTER"
rm -f "$READBACK_POINTER"

# Only after R2 readback succeeds do we update the browser-safe projection.
"$PYTHON_BIN" scripts/calendar_reference.py project --output-dir "$STAGE_DIR"

echo "Published calendar reference $RELEASE_ID (receipt $RECEIPT_ID) to $REMOTE/$PREFIX"
