#!/usr/bin/env bash
# Push the local data/ dir back to Cloudflare R2. Only changed files transfer
# (rclone sync compares size + mtime). CI calls this twice:
#   1. After DoltHub sync (--skip-forecasts) — parquet + models
#   2. After daily_score (--forecasts-only) — forecasts_YYYY-MM-DD.parquet

set -euo pipefail

DATA_DIR="${DATA_DIR:-data}"
REMOTE="${R2_REMOTE:-r2:${R2_BUCKET:-quantiv-data}}"

MODE="${1:-all}"
case "$MODE" in
  all|--skip-forecasts|--forecasts-only) ;;
  *)
    echo "Usage: r2_push.sh [all| --skip-forecasts | --forecasts-only]" >&2
    exit 2
    ;;
esac

echo "📤 Pushing $DATA_DIR/ → $REMOTE (mode=$MODE)"

push_parquet() {
  rclone sync "$DATA_DIR/parquet" "$REMOTE/parquet" \
    --fast-list --transfers=16 --checkers=16 \
    --progress
}

push_models() {
  rclone sync "$DATA_DIR/models" "$REMOTE/models" \
    --fast-list --transfers=8 --progress
}

push_forecasts() {
  if [ -d "$DATA_DIR/forecasts" ]; then
    rclone sync "$DATA_DIR/forecasts" "$REMOTE/forecasts" \
      --fast-list --transfers=8 --progress
  else
    echo "⚠️  $DATA_DIR/forecasts missing — skipping forecast sync"
  fi
  if [ -d "$DATA_DIR/models/monitoring" ]; then
    rclone sync "$DATA_DIR/models/monitoring" "$REMOTE/models/monitoring" \
      --fast-list --transfers=4 --progress
  fi
}

push_small_files() {
  [ -f "$DATA_DIR/earnings_calendar.csv" ] && \
    rclone copy "$DATA_DIR/earnings_calendar.csv" "$REMOTE/"
  [ -f "$DATA_DIR/earnings_calendar.parquet" ] && \
    rclone copy "$DATA_DIR/earnings_calendar.parquet" "$REMOTE/"
  [ -f "$DATA_DIR/bias_curves.parquet" ] && \
    rclone copy "$DATA_DIR/bias_curves.parquet" "$REMOTE/"
}

push_controls() {
  if [ -d "$DATA_DIR/control" ]; then
    rclone sync "$DATA_DIR/control" "$REMOTE/control" \
      --fast-list --transfers=4 --progress
  fi
  if [ -d "$DATA_DIR/quarantine" ]; then
    rclone sync "$DATA_DIR/quarantine" "$REMOTE/quarantine" \
      --fast-list --transfers=4 --progress
  fi
  if [ -d "$DATA_DIR/validation" ]; then
    rclone sync "$DATA_DIR/validation" "$REMOTE/validation" \
      --fast-list --transfers=4 --progress
  fi
}

if [ "$MODE" = "--forecasts-only" ]; then
  push_forecasts
elif [ "$MODE" = "--skip-forecasts" ]; then
  push_parquet
  push_models
  push_small_files
  push_controls
else
  push_parquet
  push_models
  push_forecasts
  push_small_files
  push_controls
fi

echo "✅ Push complete ($MODE)"
