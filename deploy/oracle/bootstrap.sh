#!/usr/bin/env bash
# One-shot bring-up on a fresh VM. Idempotent: safe to re-run.
#
#   1. sanity-check Docker + .env
#   2. reconstruct ./data from R2 (models + forecasts)
#   3. build the image and start api + worker + caddy
#   4. install a daily cron that re-pulls forecasts so the DuckDB history view
#      keeps advancing (recent data already comes from Neon, so this is parity
#      insurance, not a hard requirement)
#
# Run from this directory:  ./bootstrap.sh

set -euo pipefail
cd "$(dirname "$0")"
HERE="$(pwd)"

command -v docker >/dev/null || { echo "❌ Docker not installed. See RUNBOOK.md step 3."; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "❌ 'docker compose' plugin missing. See RUNBOOK.md step 3."; exit 1; }
command -v rclone >/dev/null || { echo "❌ rclone not installed:  sudo apt-get install -y rclone"; exit 1; }
[ -f .env ] || { echo "❌ .env missing. Copy .env.example → .env and fill in (or scp it from your laptop)."; exit 1; }

echo "==> 1/3  Reconstructing ./data from R2"
./pull-data.sh

echo "==> 2/3  Building image + starting api, worker, caddy"
docker compose up -d --build

echo "==> 3/3  Installing daily forecasts re-pull cron (07:15 UTC)"
CRON_LINE="15 7 * * * cd $HERE && ./pull-data.sh >> $HERE/pull-data.log 2>&1"
( crontab -l 2>/dev/null | grep -vF "$HERE/pull-data.sh" ; echo "$CRON_LINE" ) | crontab -

echo
echo "✅ Up. Watch boot:    docker compose logs -f api worker"
echo "   Local health:      curl -s localhost:8000/health    (api publishes only to caddy; use 'docker compose exec api ...' if no host port)"
echo "   Once DNS points here: curl -s https://\${API_DOMAIN:-api.usequantiv.com}/health"
