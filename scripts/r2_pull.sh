#!/usr/bin/env bash
# Restore data/ from R2 at the start of GitHub Actions.
# Needs rclone pointed at R2 (docs/R2_SETUP.md). Actions builds that from secrets.

set -euo pipefail

DATA_DIR="${DATA_DIR:-data}"
REMOTE="${R2_REMOTE:-r2:${R2_BUCKET:-quantiv-data}}"
PYTHON_BIN="${PYTHON_BIN:-python}"

echo "📥 Pulling from $REMOTE → $DATA_DIR/"

# Which data version to use. Fine if this is the first run.
rclone copy "$REMOTE/control" "$DATA_DIR/control" \
  --fast-list --transfers=4 --progress 2>/dev/null || true

# Options, daily prices, and vol history.
rclone copy "$REMOTE/parquet" "$DATA_DIR/parquet" \
  --fast-list --transfers=16 --checkers=16 \
  --progress

# Models + bias curves
rclone sync "$REMOTE/models" "$DATA_DIR/models" \
  --fast-list --transfers=8 --progress

# Rejected-quote logs. Missing on the first run is fine.
rclone sync "$REMOTE/quarantine" "$DATA_DIR/quarantine" \
  --fast-list --transfers=4 --progress 2>/dev/null || true

if [ -f "$DATA_DIR/control/current_data_release.json" ]; then
  "$PYTHON_BIN" scripts/data_release.py verify --data-dir "$DATA_DIR"
else
  echo "⚠️  No published data version yet; the first successful push will create it"
fi

# Forecasts from the last run (scoring and the frontend build).
rclone sync "$REMOTE/forecasts" "$DATA_DIR/forecasts" \
  --fast-list --transfers=8 --progress 2>/dev/null || true

# Earnings calendar stays in git unless R2_PULL_EARNINGS=1 (weekly retrain
# may start before the daily commit lands).
if [ "${R2_PULL_EARNINGS:-0}" = "1" ]; then
  rclone copy "$REMOTE/earnings_calendar.csv"     "$DATA_DIR/" 2>/dev/null || true
  rclone copy "$REMOTE/earnings_calendar.parquet" "$DATA_DIR/" 2>/dev/null || true
fi
rclone copy "$REMOTE/bias_curves.parquet"   "$DATA_DIR/" 2>/dev/null || true

echo "✅ Pull complete"
du -sh "$DATA_DIR/parquet" "$DATA_DIR/models" 2>/dev/null || true
