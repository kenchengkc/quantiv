#!/usr/bin/env bash
# One-time upload of local data/ to R2. Set up rclone first (docs/R2_SETUP.md).
# Later uploads: scripts/r2_push.sh.

set -euo pipefail

DATA_DIR="${DATA_DIR:-data}"
BUCKET="${R2_BUCKET:-quantiv-data}"
REMOTE="r2:$BUCKET"

echo "🚀 Bootstrapping $DATA_DIR/ → $REMOTE"
echo
echo "Will upload:"
du -sh "$DATA_DIR/parquet" "$DATA_DIR/models" 2>/dev/null || true
echo "plus small files: earnings_calendar.csv, bias_curves.parquet"
echo
read -r -p "Continue? [y/N] " ans
[[ "$ans" == "y" || "$ans" == "Y" ]] || exit 1

rclone copy "$DATA_DIR/parquet" "$REMOTE/parquet" \
  --fast-list --transfers=16 --progress

rclone copy "$DATA_DIR/models" "$REMOTE/models" \
  --fast-list --transfers=8 --progress

[ -f "$DATA_DIR/earnings_calendar.csv" ] && \
  rclone copy "$DATA_DIR/earnings_calendar.csv" "$REMOTE/"
[ -f "$DATA_DIR/bias_curves.parquet" ] && \
  rclone copy "$DATA_DIR/bias_curves.parquet" "$REMOTE/"

echo
echo "✅ Bootstrap complete. Verify with:"
echo "    rclone size $REMOTE"
echo "    rclone lsd  $REMOTE"
