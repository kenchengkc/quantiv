#!/usr/bin/env bash
# Push the local data/ dir back to Cloudflare R2. Only changed files transfer
# (rclone sync compares size + mtime). Used at the end of each run to persist
# new parquet partitions synced from DoltHub.

set -euo pipefail

DATA_DIR="${DATA_DIR:-data}"
REMOTE="${R2_REMOTE:-r2:${R2_BUCKET:-quantiv-data}}"

echo "📤 Pushing $DATA_DIR/ → $REMOTE"

# Parquet (delta upload)
rclone sync "$DATA_DIR/parquet" "$REMOTE/parquet" \
  --fast-list --transfers=16 --checkers=16 \
  --progress

# Models (only changes when we retrain — usually no-op for daily runs)
rclone sync "$DATA_DIR/models" "$REMOTE/models" \
  --fast-list --transfers=8 --progress

# Forecasts — daily_score writes a new forecasts_YYYY-MM-DD.parquet each run.
# Cheap to sync (a few MB) and lets local devs pull the latest without rerunning.
if [ -d "$DATA_DIR/forecasts" ]; then
  rclone sync "$DATA_DIR/forecasts" "$REMOTE/forecasts" \
    --fast-list --transfers=8 --progress
fi

# Small refreshable files
[ -f "$DATA_DIR/earnings_calendar.csv" ] && \
  rclone copy "$DATA_DIR/earnings_calendar.csv" "$REMOTE/"
[ -f "$DATA_DIR/bias_curves.parquet" ] && \
  rclone copy "$DATA_DIR/bias_curves.parquet" "$REMOTE/"

echo "✅ Push complete"
