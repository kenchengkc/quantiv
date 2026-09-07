#!/usr/bin/env bash
# Materialize the current production data release from R2 at the start of CI.
# Needs rclone pointed at R2 (docs/R2_SETUP.md). Actions builds that from secrets.

set -euo pipefail

DATA_DIR="${DATA_DIR:-data}"
REMOTE="${R2_REMOTE:-r2:${R2_BUCKET:-quantiv-data}}"
PYTHON_BIN="${PYTHON_BIN:-python}"
ALLOW_MISSING_EARNINGS_BASELINE="${R2_ALLOW_MISSING_EARNINGS_BASELINE:-0}"

mkdir -p "$DATA_DIR" "$DATA_DIR/validation"
echo "📥 Pulling from $REMOTE → $DATA_DIR/"

# Which data version to use. Fine if this is the first run.
rclone copy "$REMOTE/control" "$DATA_DIR/control" \
  --fast-list --transfers=4 --progress 2>/dev/null || true

materialize_earnings_calendar() {
  # The earnings calendar is mutable production state, not source code. Resolve
  # the last published copy before provider mutation and retain those exact
  # bytes as the integrity-gate baseline for this run.
  local target="$DATA_DIR/earnings_calendar.csv"
  local baseline="$DATA_DIR/validation/earnings_calendar_baseline.csv"
  local tmp="$DATA_DIR/.earnings_calendar.csv.$$.tmp"

  rm -f "$tmp"
  if rclone copyto "$REMOTE/earnings_calendar.csv" "$tmp"; then
    if [ ! -s "$tmp" ]; then
      echo "Downloaded earnings calendar is empty; refusing materialization" >&2
      rm -f "$tmp"
      exit 1
    fi
    mv "$tmp" "$target"
    cp "$target" "$baseline"
    echo "✅ Materialized prior-release earnings calendar and integrity baseline"
  elif [ "$ALLOW_MISSING_EARNINGS_BASELINE" = "1" ] && [ -s "$target" ]; then
    # Explicit bootstrap escape hatch only. Normal production CI leaves this
    # disabled and therefore fails closed when the canonical R2 object is absent.
    cp "$target" "$baseline"
    echo "⚠️  R2 earnings calendar missing; using explicit bootstrap local baseline"
  else
    rm -f "$tmp"
    echo "Missing canonical R2 earnings calendar; refusing to run without a prior-release baseline" >&2
    exit 1
  fi

  # Parquet is a compatibility/analytical derivative and may not exist in older
  # releases; the CSV remains the canonical provider-merge input.
  rclone copy "$REMOTE/earnings_calendar.parquet" "$DATA_DIR/" 2>/dev/null || true
}

materialize_earnings_calendar

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

materialize_vix_alias() {
  "$PYTHON_BIN" - "$DATA_DIR" <<'PY'
import json
import os
from pathlib import Path
import re
import shutil
import sys

data_dir = Path(sys.argv[1])
pointer = json.loads((data_dir / "control" / "current_data_release.json").read_text())
manifest = json.loads((data_dir / str(pointer["manifest"])).read_text())
paths = [
    str(item.get("path", ""))
    for item in manifest.get("files") or []
    if str(item.get("path", "")).startswith("parquet/vix/")
    and str(item.get("path", "")) != "parquet/vix/vix.parquet"
]
if not paths:
    raise SystemExit(0)

pattern = re.compile(
    r"^parquet/vix/vix-through-(\d{4}-\d{2}-\d{2})-[0-9a-f]{64}\.parquet$"
)
dated_paths = []
for value in paths:
    match = pattern.fullmatch(value)
    if match is None:
        raise RuntimeError(f"active data release contains an invalid VIX snapshot: {value}")
    dated_paths.append((match.group(1), value))
latest_date = max(item[0] for item in dated_paths)
latest = [value for snapshot_date, value in dated_paths if snapshot_date == latest_date]
if len(latest) != 1:
    raise RuntimeError(
        f"active data release contains ambiguous latest VIX snapshots: {latest}"
    )

selected = data_dir / latest[0]
alias = data_dir / "parquet" / "vix" / "vix.parquet"
alias.parent.mkdir(parents=True, exist_ok=True)
temporary = alias.with_name(f".{alias.name}.{os.getpid()}.tmp")
shutil.copyfile(selected, temporary)
os.replace(temporary, alias)

for snapshot in alias.parent.glob("vix-through-*.parquet"):
    if snapshot != selected:
        snapshot.unlink()
print(f"✅ Restored local VIX alias from {latest[0]}")
if len(paths) > 1:
    print(f"✅ Pruned {len(paths) - 1} superseded VIX snapshots from the local release")
PY
}

if [ -f "$DATA_DIR/control/current_data_release.json" ]; then
  "$PYTHON_BIN" scripts/data_release.py verify --data-dir "$DATA_DIR"
  materialize_vix_alias
else
  echo "⚠️  No published data version yet; the first successful push will create it"
fi

# Forecasts from the last run (scoring and the frontend build).
rclone sync "$REMOTE/forecasts" "$DATA_DIR/forecasts" \
  --fast-list --transfers=8 --progress 2>/dev/null || true

# Weekly/manual retraining additionally restores the latest reconciliation
# decision and verifies that the materialized release is eligible for training.
if [ "${R2_PULL_EARNINGS:-0}" = "1" ]; then
  rclone copyto \
    "$REMOTE/validation/data_reconciliation.json" \
    "$DATA_DIR/validation/data_reconciliation.json"
  "$PYTHON_BIN" scripts/verify_retrain_data_gate.py --data-dir "$DATA_DIR"
fi
rclone copy "$REMOTE/bias_curves.parquet" "$DATA_DIR/" 2>/dev/null || true

echo "✅ Pull complete"
du -sh "$DATA_DIR/parquet" "$DATA_DIR/models" 2>/dev/null || true
