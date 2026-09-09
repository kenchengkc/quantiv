#!/usr/bin/env bash
# Push the local data/ dir back to Cloudflare R2. Raw partitions are immutable;
# versioned pointers are copied last and are the only promotion steps.
# CI calls this in bounded modes so each producer publishes only state it owns.

set -euo pipefail

DATA_DIR="${DATA_DIR:-data}"
REMOTE="${R2_REMOTE:-r2:${R2_BUCKET:-quantiv-data}}"
PYTHON_BIN="${PYTHON_BIN:-python}"

MODE="${1:-all}"
case "$MODE" in
  all|--skip-forecasts|--forecasts-only|--model-recovery|--runtime-state-only) ;;
  *)
    echo "Usage: r2_push.sh [all| --skip-forecasts | --forecasts-only | --model-recovery | --runtime-state-only]" >&2
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

push_runtime_state() {
  local output_dir="$DATA_DIR/runtime_state_release"
  local source_revision="${RUNTIME_STATE_SOURCE_REVISION:-}"
  local readback
  local release_id manifest_rel archive_rel

  if [ -z "$source_revision" ]; then
    source_revision="$(git rev-parse HEAD 2>/dev/null || true)"
  fi
  rm -rf "$output_dir"
  "$PYTHON_BIN" scripts/runtime_state.py build \
    --data-dir "$DATA_DIR" \
    --output-dir "$output_dir" \
    ${source_revision:+--source-revision "$source_revision"}
  "$PYTHON_BIN" scripts/runtime_state.py verify --output-dir "$output_dir"

  readarray -t release_paths < <(
    "$PYTHON_BIN" - "$output_dir" <<'PY'
import json
from pathlib import Path
import sys

root = Path(sys.argv[1])
pointer = json.loads((root / "current.json").read_text())
manifest_rel = str(pointer["manifest"])
manifest = json.loads((root / manifest_rel).read_text())
print(pointer["release_id"])
print(manifest_rel)
print(manifest["archive"]["path"])
PY
  )
  release_id="${release_paths[0]}"
  manifest_rel="${release_paths[1]}"
  archive_rel="${release_paths[2]}"

  # Immutable objects first. Existing content-addressed objects may never be
  # replaced; a new mutable discovery pointer is only promoted after readback.
  rclone copyto \
    "$output_dir/$archive_rel" \
    "$REMOTE/runtime-state/$archive_rel" \
    --immutable
  rclone copyto \
    "$output_dir/$manifest_rel" \
    "$REMOTE/runtime-state/$manifest_rel" \
    --immutable

  readback="$(mktemp -d)"
  trap 'rm -rf "$readback"' RETURN
  mkdir -p "$readback/$(dirname "$manifest_rel")" "$readback/$(dirname "$archive_rel")"
  cp "$output_dir/current.json" "$readback/current.json"
  rclone copyto "$REMOTE/runtime-state/$manifest_rel" "$readback/$manifest_rel"
  rclone copyto "$REMOTE/runtime-state/$archive_rel" "$readback/$archive_rel"
  "$PYTHON_BIN" scripts/runtime_state.py verify --output-dir "$readback"

  # Pointer-last promotion: readers only observe a release whose immutable
  # manifest/archive have already survived a remote readback verification.
  rclone copyto "$output_dir/current.json" "$REMOTE/runtime-state/current.json"
  rclone copyto "$REMOTE/runtime-state/current.json" "$readback/current.remote.json"
  cmp "$output_dir/current.json" "$readback/current.remote.json"
  rm -rf "$readback"
  trap - RETURN
  echo "✅ Promoted runtime-state release $release_id after R2 readback verification"
}

if [ "$MODE" = "--model-recovery" ]; then
  # Controlled provenance recovery: never build/promote a data release or
  # rewrite reconciliation. The publication hold remains intact.
  push_models
  push_forecasts
  promote_model_champion
elif [ "$MODE" = "--forecasts-only" ]; then
  push_forecasts
elif [ "$MODE" = "--runtime-state-only" ]; then
  push_runtime_state
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
