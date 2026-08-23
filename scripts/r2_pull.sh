#!/usr/bin/env bash
# Pull parquet + models from Cloudflare R2 into the local data/ dir.
# Used at the start of each GitHub Actions run to restore pipeline inputs.
#
# Requires rclone with an `r2` remote configured (see scripts/r2_setup.md).
# When running in GHA the remote is configured on the fly from env vars.

set -euo pipefail

DATA_DIR="${DATA_DIR:-data}"
REMOTE="${R2_REMOTE:-r2:${R2_BUCKET:-quantiv-data}}"

echo "📥 Pulling from $REMOTE → $DATA_DIR/"

# Parquet trees — options chain, ohlcv, volatility history
rclone sync "$REMOTE/parquet" "$DATA_DIR/parquet" \
  --fast-list --transfers=16 --checkers=16 \
  --progress

# Models + bias curves
rclone sync "$REMOTE/models" "$DATA_DIR/models" \
  --fast-list --transfers=8 --progress

# Compact control evidence is needed to verify source revisions and replay
# equivalence before the next promotion. Missing paths are expected on the
# first controlled run.
rclone sync "$REMOTE/control" "$DATA_DIR/control" \
  --fast-list --transfers=4 --progress 2>/dev/null || true
rclone sync "$REMOTE/quarantine" "$DATA_DIR/quarantine" \
  --fast-list --transfers=4 --progress 2>/dev/null || true

# Forecasts (small, but keeping them in sync means daily_score finds the
# previous run's parquet and build_frontend_data has them on hand).
rclone sync "$REMOTE/forecasts" "$DATA_DIR/forecasts" \
  --fast-list --transfers=8 --progress 2>/dev/null || true

# Small files
#
# By default, earnings_calendar.csv remains git-canonical. Weekly retrain
# can opt into R2 via R2_PULL_EARNINGS=1 because that run may be queued before
# the daily refresh auto-commit lands, but should train/score against the
# just-refreshed calendar that daily pushed to R2.
if [ "${R2_PULL_EARNINGS:-0}" = "1" ]; then
  rclone copy "$REMOTE/earnings_calendar.csv"     "$DATA_DIR/" 2>/dev/null || true
  rclone copy "$REMOTE/earnings_calendar.parquet" "$DATA_DIR/" 2>/dev/null || true
fi
rclone copy "$REMOTE/bias_curves.parquet"   "$DATA_DIR/" 2>/dev/null || true

echo "✅ Pull complete"
du -sh "$DATA_DIR/parquet" "$DATA_DIR/models" 2>/dev/null || true
