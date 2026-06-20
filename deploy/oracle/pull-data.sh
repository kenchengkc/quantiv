#!/usr/bin/env bash
# Reconstruct ./data from Cloudflare R2 (the source of truth).
#
#   data/models/    — LightGBM joblibs + metadata (the container ALSO re-syncs
#                     these on boot via services/r2_models.py; this is belt-and-
#                     suspenders so the volume is warm before first request)
#   data/forecasts/ — forecasts_<date>.parquet, feeds the DuckDB `em_forecasts`
#                     view that /em/forecast and /em/history read in hybrid mode
#
# Safe to run repeatedly (rclone sync). Run from this directory.
# Used by bootstrap.sh on first boot and by the daily refresh cron.

set -euo pipefail
cd "$(dirname "$0")"

# Read R2 creds literally from .env (no `source`, so a `$` in a secret value
# can't be shell-expanded).
[ -f .env ] || { echo "❌ .env not found in $(pwd)"; exit 1; }
getenv() { grep -E "^$1=" .env | head -1 | cut -d= -f2-; }
R2_ACCOUNT_ID="$(getenv R2_ACCOUNT_ID)"
R2_BUCKET="$(getenv R2_BUCKET)"
R2_ACCESS_KEY_ID="$(getenv R2_ACCESS_KEY_ID)"
R2_SECRET_ACCESS_KEY="$(getenv R2_SECRET_ACCESS_KEY)"

: "${R2_ACCOUNT_ID:?set in .env}"
: "${R2_BUCKET:?set in .env}"
: "${R2_ACCESS_KEY_ID:?set in .env}"
: "${R2_SECRET_ACCESS_KEY:?set in .env}"

# Configure an rclone "r2" remote purely from env (no rclone.conf needed).
export RCLONE_CONFIG_R2_TYPE=s3
export RCLONE_CONFIG_R2_PROVIDER=Cloudflare
export RCLONE_CONFIG_R2_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID"
export RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY"
export RCLONE_CONFIG_R2_ENDPOINT="https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
export RCLONE_CONFIG_R2_REGION=auto

mkdir -p data/forecasts data/models

echo "📥 R2 → ./data (forecasts + models)"
rclone sync "r2:${R2_BUCKET}/forecasts" data/forecasts --transfers=8 --checkers=8
rclone sync "r2:${R2_BUCKET}/models"    data/models    --transfers=8 --checkers=8

echo "✅ pull complete"
ls -1 data/forecasts | tail -3
du -sh data 2>/dev/null || true
