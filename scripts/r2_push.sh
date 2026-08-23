#!/usr/bin/env bash
# Push the local data/ dir back to Cloudflare R2. Raw partitions are immutable;
# a versioned manifest pointer is copied last and is the only promotion step.
# CI calls this twice:
#   1. After DoltHub sync (--skip-forecasts) — parquet + models
#   2. After daily_score (--forecasts-only) — forecasts_YYYY-MM-DD.parquet

set -euo pipefail

DATA_DIR="${DATA_DIR:-data}"
REMOTE="${R2_REMOTE:-r2:${R2_BUCKET:-quantiv-data}}"
PYTHON_BIN="${PYTHON_BIN:-python}"

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
  # Existing dated partitions may never be overwritten. New files remain
  # inactive until current_data_release.json is promoted below.
  rclone copy "$DATA_DIR/parquet" "$REMOTE/parquet" --immutable \
    --fast-list --transfers=16 --checkers=16 \
    --progress
  rclone check "$DATA_DIR/parquet" "$REMOTE/parquet" \
    --one-way --checkers=16
}

push_models() {
  # Upload immutable/versioned bundles and supporting state first. The signed
  # champion pointer is the only serving promotion step and is replaced last,
  # so an R2 reader never observes a pointer to a partially uploaded bundle.
  rclone sync "$DATA_DIR/models" "$REMOTE/models" \
    --exclude "/control/**" \
    --fast-list --transfers=8 --progress
  if [ -d "$DATA_DIR/models/control" ]; then
    rclone sync "$DATA_DIR/models/control" "$REMOTE/models/control" \
      --exclude "/champion.json" \
      --fast-list --transfers=4 --progress
    if [ -f "$DATA_DIR/models/control/champion.json" ]; then
      rclone copyto \
        "$DATA_DIR/models/control/champion.json" \
        "$REMOTE/models/control/champion.json"
      echo "✅ Promoted atomic model champion pointer"
    fi
  fi
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
    rclone copy "$DATA_DIR/control" "$REMOTE/control" --immutable \
      --exclude "current_data_release.json" \
      --fast-list --transfers=4 --progress
  fi
  if [ -d "$DATA_DIR/quarantine" ]; then
    rclone copy "$DATA_DIR/quarantine" "$REMOTE/quarantine" --immutable \
      --fast-list --transfers=4 --progress
  fi
  if [ -d "$DATA_DIR/validation" ]; then
    rclone sync "$DATA_DIR/validation" "$REMOTE/validation" \
      --fast-list --transfers=4 --progress
  fi
}

promote_data_release() {
  local pointer="$DATA_DIR/control/current_data_release.json"
  if [ ! -f "$pointer" ]; then
    echo "Missing data-release pointer; refusing R2 promotion" >&2
    exit 1
  fi
  "$PYTHON_BIN" scripts/data_release.py verify --data-dir "$DATA_DIR"
  # R2 object replacement is atomic. Consumers either see the prior complete
  # release or this complete release, never the upload in between.
  rclone copyto "$pointer" "$REMOTE/control/current_data_release.json"
  echo "✅ Promoted atomic data-release pointer"
}

if [ "$MODE" = "--forecasts-only" ]; then
  push_forecasts
elif [ "$MODE" = "--skip-forecasts" ]; then
  "$PYTHON_BIN" scripts/data_release.py build --data-dir "$DATA_DIR"
  push_parquet
  push_models
  push_small_files
  push_controls
  promote_data_release
else
  "$PYTHON_BIN" scripts/data_release.py build --data-dir "$DATA_DIR"
  push_parquet
  push_models
  push_forecasts
  push_small_files
  push_controls
  promote_data_release
fi

echo "✅ Push complete ($MODE)"
