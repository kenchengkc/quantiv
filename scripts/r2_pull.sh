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

# Older releases briefly accumulated one immutable VIX snapshot per day. Keep
# the latest verified object plus the mutable compatibility alias locally so
# the next release heals itself instead of perpetuating that historical set.
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

# Earnings calendar stays in git unless R2_PULL_EARNINGS=1. That flag is used
# by the weekly/manual retrain path, which must also restore and verify the
# latest reconciliation decision before it is allowed to train or mutate models.
if [ "${R2_PULL_EARNINGS:-0}" = "1" ]; then
  rclone copy "$REMOTE/earnings_calendar.csv"     "$DATA_DIR/" 2>/dev/null || true
  rclone copy "$REMOTE/earnings_calendar.parquet" "$DATA_DIR/" 2>/dev/null || true
  mkdir -p "$DATA_DIR/validation"
  rclone copyto \
    "$REMOTE/validation/data_reconciliation.json" \
    "$DATA_DIR/validation/data_reconciliation.json"
  "$PYTHON_BIN" scripts/verify_retrain_data_gate.py --data-dir "$DATA_DIR"
fi
rclone copy "$REMOTE/bias_curves.parquet"   "$DATA_DIR/" 2>/dev/null || true

echo "✅ Pull complete"
du -sh "$DATA_DIR/parquet" "$DATA_DIR/models" 2>/dev/null || true
