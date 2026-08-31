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
  # Existing release objects may never be overwritten. vix/vix.parquet is a
  # local compatibility alias; the content-addressed VIX snapshot beside it is
  # the immutable object included in the release manifest.
  rclone copy "$DATA_DIR/parquet" "$REMOTE/parquet" --immutable \
    --exclude "/vix/vix.parquet" \
    --fast-list --transfers=16 --checkers=16 \
    --progress
  rclone check "$DATA_DIR/parquet" "$REMOTE/parquet" \
    --exclude "/vix/vix.parquet" \
    --one-way --checkers=16
}

push_models() {
  # Upload immutable/versioned bundles and supporting state first. The signed
  # champion pointer is promoted separately after every other upload succeeds.
  rclone sync "$DATA_DIR/models" "$REMOTE/models" \
    --exclude "/control/**" \
    --fast-list --transfers=8 --progress
  if [ -d "$DATA_DIR/models/control" ]; then
    rclone sync "$DATA_DIR/models/control" "$REMOTE/models/control" \
      --exclude "/champion.json" \
      --fast-list --transfers=4 --progress
  fi
}

promote_model_champion() {
  local pointer="$DATA_DIR/models/control/champion.json"
  if [ -f "$pointer" ]; then
    # This is deliberately the final R2 mutation in a full push. A model
    # reader can never observe a new champion before its bundle, validation
    # receipts, forecasts, and data-release pointer are durable.
    rclone copyto "$pointer" "$REMOTE/models/control/champion.json"
    echo "✅ Promoted atomic model champion pointer"
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
      --exclude "ingestion/corporate_actions/latest.json" \
      --fast-list --transfers=4 --progress
    local action_pointer="$DATA_DIR/control/ingestion/corporate_actions/latest.json"
    if [ -f "$action_pointer" ]; then
      rclone copyto \
        "$action_pointer" \
        "$REMOTE/control/ingestion/corporate_actions/latest.json"
    fi
  fi
  if [ -d "$DATA_DIR/quarantine" ]; then
    # Quarantine is derived evidence keyed by source date. A rerun can add
    # newly rejected rows for that same date, so the dated Parquet may change.
    # Keep the core parquet release and control manifests immutable, but allow
    # this refreshable ledger to replace its same-date object in R2.
    rclone copy "$DATA_DIR/quarantine" "$REMOTE/quarantine" \
      --fast-list --transfers=4 --progress
  fi
  if [ -d "$DATA_DIR/validation" ]; then
    # Validation evidence is produced by multiple jobs. A daily runner does
    # not restore every weekly report, so sync would delete valid outcome and
    # retraining evidence that is absent from that runner's checkout.
    rclone copy "$DATA_DIR/validation" "$REMOTE/validation" \
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
  promote_model_champion
else
  "$PYTHON_BIN" scripts/data_release.py build --data-dir "$DATA_DIR"
  push_parquet
  push_models
  push_forecasts
  push_small_files
  push_controls
  promote_data_release
  promote_model_champion
fi

echo "✅ Push complete ($MODE)"
