#!/usr/bin/env bash
# Run a recovery command with market-data provider credentials unavailable.
set -euo pipefail

if [ "$#" -eq 0 ]; then
  echo "Usage: scripts/run_provider_free.sh <command> [args...]" >&2
  exit 2
fi

export PROVIDER_FREE_RECOVERY=1

# Recovery may still use GitHub, R2, Neon, and package registries. It may not
# call market-data providers. Remove every provider credential from the child
# environment so an accidental optional fallback cannot consume quota.
for name in   FINNHUB_API_KEY FINNHUB_API_KEY_2   FMP_API_KEY FMP_API_KEY_2   ALPHAVANTAGE_API_KEY ALPHAVANTAGE_API_KEY_2   TWELVEDATA_API_KEY TWELVEDATA_API_KEY_2 TWELVE_DATA_API_KEY TWELVEDATA_KEY   POLYGON_API_KEY POLYGON_API_KEY_2 MASSIVE_API_KEY MASSIVE_API_KEY_2   ALPACA_API_KEY ALPACA_API_SECRET; do
  unset "$name" || true
done

exec "$@"
