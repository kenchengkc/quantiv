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

# Forecasts (small, but keeping them in sync means daily_score finds the
# previous run's parquet and build_frontend_data has them on hand).
rclone sync "$REMOTE/forecasts" "$DATA_DIR/forecasts" \
  --fast-list --transfers=8 --progress 2>/dev/null || true

# Small files
#
# Note: earnings_calendar.csv is intentionally NOT pulled from R2.
# It's git-canonical (committed at the end of each successful CI run
# via `git add -f data/earnings_calendar.csv`), so the checkout already
# contains the authoritative version. Pulling from R2 here would
# silently overwrite manual git commits with whatever the last CI run
# pushed — which masked a real bug on 2026-05-19 where a user's local
# Finnhub sweep + universe trim were committed to git but invisible
# to CI because R2 hadn't been updated. r2_push.sh still uploads the
# CSV as a backup / fast-pull source for local devs.
# rclone copy "$REMOTE/earnings_calendar.csv" "$DATA_DIR/" 2>/dev/null || true
rclone copy "$REMOTE/bias_curves.parquet"   "$DATA_DIR/" 2>/dev/null || true

echo "✅ Pull complete"
du -sh "$DATA_DIR/parquet" "$DATA_DIR/models" 2>/dev/null || true
