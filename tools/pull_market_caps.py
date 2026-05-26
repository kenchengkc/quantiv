"""
Pull market capitalization from Finnhub for the top N tickers by
90-day dollar volume. Caches to data/market_caps.json.

Used by build_popular_weights.py to compute a composite popularity
score that captures BOTH trading activity (dollar volume) AND company
size (market cap). Pure dollar volume alone misclassifies — e.g.,
ABNB ($80B mcap, household name) and AXTI ($0.5B mcap, volatile
penny semi) can land in the same dollar-volume bucket because AXTI's
flow is driven by news-day spikes rather than steady investor demand.

Cache TTL: MAX_AGE_DAYS (7). Market caps change slowly day-to-day;
weekly refresh keeps the popular set current without burning the
Finnhub free-tier budget. If the cache is fresh, the script is a
no-op — fine to run on every CI cycle.

Finnhub free-tier: 60 calls/min. 500 tickers ≈ 9 minutes one-time.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(REPO_ROOT / "scripts"))
from provider_market_hours import block_finnhub_reserved_window  # noqa: E402

DB_PATH = REPO_ROOT / "data" / "quantiv.duckdb"
CACHE_PATH = REPO_ROOT / "data" / "market_caps.json"
LOGO_CACHE_PATH = REPO_ROOT / "apps" / "frontend" / "public" / "ticker-logos.json"

TOP_N = 500
MAX_AGE_DAYS = 7
REQUEST_DELAY = 1.05  # 60/min free tier with safety margin
PROFILE_URL = "https://finnhub.io/api/v1/stock/profile2"

# Finnhub returns marketCapitalization in the company's reporting
# currency, NOT USD. For ADRs (TSM=TWD, HDB=INR, NVO=DKK, BABA=CNY,
# CVE=CAD, GSK=GBP) this gives 10-50× inflated values that wreck the
# popularity composite. We fetch live FX rates from open.er-api.com
# (free, no auth, hourly updates) and convert. If the FX call fails,
# we fall back to FX_FALLBACK below — captured 2026-05-19, refresh
# annually as a safety net but the live fetch should make this
# unnecessary 99% of the time.
FX_API_URL = "https://open.er-api.com/v6/latest/USD"
FX_FALLBACK: dict[str, float] = {
    "USD": 1.0,
    "TWD": 0.031, "INR": 0.012, "EUR": 1.08, "GBP": 1.26, "JPY": 0.0067,
    "CNY": 0.14,  "HKD": 0.128, "KRW": 0.00072, "BRL": 0.18, "MXN": 0.058,
    "CAD": 0.73,  "AUD": 0.66,  "CHF": 1.13, "SEK": 0.094, "NOK": 0.092,
    "DKK": 0.145, "ZAR": 0.054, "ARS": 0.001,
}


def fetch_fx_rates() -> dict[str, float]:
    """Fetch live USD → currency rates and invert to currency → USD.
    Returns FX_FALLBACK on any failure so the script stays functional
    even if the FX API is down.
    """
    try:
        resp = requests.get(FX_API_URL, timeout=15)
        resp.raise_for_status()
        body = resp.json()
        # API returns {result: 'success', base_code: 'USD', rates: {EUR: 0.92, ...}}
        # rates[X] = 1 USD in X. We need X→USD = 1 / rates[X].
        rates = body.get("rates") or {}
        if not rates or body.get("result") != "success":
            raise ValueError(f"unexpected response: {body!r}")
        inverted = {"USD": 1.0}
        for code, rate in rates.items():
            try:
                if float(rate) > 0:
                    inverted[code.upper()] = 1.0 / float(rate)
            except (TypeError, ValueError):
                continue
        print(f"  live FX: {len(inverted)} currencies fetched (open.er-api.com)")
        return inverted
    except Exception as exc:
        print(f"  ⚠ FX API failed ({exc}); using hardcoded FX_FALLBACK")
        return dict(FX_FALLBACK)


def load_local_env() -> None:
    for path in [
        REPO_ROOT / "config" / ".env.local",
        REPO_ROOT / "config" / ".env.production",
    ]:
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def cache_age_days() -> float | None:
    if not CACHE_PATH.exists():
        return None
    try:
        data = json.loads(CACHE_PATH.read_text())
        ts = data.get("generated_at")
        if not ts:
            return None
        gen = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - gen).total_seconds() / 86400
    except Exception:
        return None


def fetch_profile(
    symbol: str, token: str, fx: dict[str, float]
) -> tuple[float | None, str | None, str | None]:
    """Return (market_cap_usd_millions, currency, logo_url) on failure.

    Finnhub returns marketCapitalization in the company's reporting
    currency. We convert to USD using the live `fx` dict fetched at
    script start. Unmapped currencies pass through unmodified and the
    currency string is returned so the caller can log them.
    """
    for attempt in range(3):
        try:
            resp = requests.get(
                PROFILE_URL,
                params={"symbol": symbol, "token": token},
                timeout=20,
            )
        except requests.RequestException:
            if attempt == 2:
                return None, None
            time.sleep(2 ** attempt)
            continue
        if resp.status_code == 429 and attempt < 2:
            time.sleep(5 * (attempt + 1))
            continue
        if not resp.ok:
            return None, None, None
        body = resp.json()
        logo = body.get("logo")
        logo_url = logo.strip() if isinstance(logo, str) and logo.strip() else None
        mcap = body.get("marketCapitalization")
        currency = (body.get("currency") or "USD").strip().upper()
        if mcap is None:
            return None, currency, logo_url
        try:
            mcap_local = float(mcap)
        except (TypeError, ValueError):
            return None, currency, logo_url
        rate = fx.get(currency, 1.0)
        return mcap_local * rate, currency, logo_url
    return None, None, None


def write_logo_cache(logos: dict[str, str]) -> None:
    LOGO_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing_logos: dict[str, str] = {}
    existing_profiles: dict[str, object] = {}
    profile_sweep_at: str | None = None
    if LOGO_CACHE_PATH.exists():
        try:
            existing = json.loads(LOGO_CACHE_PATH.read_text())
            profile_sweep_at_raw = existing.get("profile_sweep_at")
            if isinstance(profile_sweep_at_raw, str) and profile_sweep_at_raw:
                profile_sweep_at = profile_sweep_at_raw
            existing_logos = {
                k: v
                for k, v in dict(existing.get("logos", {})).items()
                if isinstance(k, str) and isinstance(v, str) and v.startswith("http")
            }
            existing_profiles = {
                k: v
                for k, v in dict(existing.get("profiles", {})).items()
                if isinstance(k, str) and isinstance(v, dict)
            }
        except Exception:
            existing_logos = {}
            existing_profiles = {}
    merged_logos = {**existing_logos, **logos}
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "finnhub /stock/profile2",
        "ticker_count": len(merged_logos),
        "profile_count": len(existing_profiles),
        "logos": dict(sorted(merged_logos.items())),
        "profiles": dict(sorted(existing_profiles.items())),
    }
    if profile_sweep_at:
        payload["profile_sweep_at"] = profile_sweep_at
    LOGO_CACHE_PATH.write_text(json.dumps(payload, indent=2) + "\n")


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Refetch even if cache is fresh.",
    )
    parser.add_argument(
        "--allow-missing-key",
        action="store_true",
        help="Exit cleanly if FINNHUB_API_KEY is missing (for CI runs without secrets).",
    )
    parser.add_argument(
        "--allow-market-hours",
        action="store_true",
        help="Allow non-price Finnhub calls during the 09:25-16:45 ET quote refresh window.",
    )
    args = parser.parse_args()

    age = cache_age_days()
    if age is not None and age < MAX_AGE_DAYS and not args.force:
        try:
            cached = json.loads(CACHE_PATH.read_text())
            cached_logos = {
                k: v for k, v in dict(cached.get("logos", {})).items()
                if isinstance(k, str) and isinstance(v, str) and v.startswith("http")
            }
        except Exception:
            cached_logos = {}
        if cached_logos:
            write_logo_cache(cached_logos)
            print(
                f"✅ Cache age {age:.1f}d < {MAX_AGE_DAYS}d — "
                f"skipping fetch, refreshed {LOGO_CACHE_PATH.relative_to(REPO_ROOT)}"
            )
            return 0
        print(
            f"Cache age {age:.1f}d < {MAX_AGE_DAYS}d but no logo cache is present; "
            "refreshing profile2 data once."
        )

    load_local_env()
    token = os.getenv("FINNHUB_API_KEY")
    if not token:
        msg = "FINNHUB_API_KEY missing"
        if args.allow_missing_key:
            print(f"⚠ {msg}; skipping market cap pull")
            return 0
        print(f"✗ {msg}", file=sys.stderr)
        return 1
    block_finnhub_reserved_window(args.allow_market_hours)

    if not DB_PATH.exists():
        print(f"❌ DuckDB not found at {DB_PATH}", file=sys.stderr)
        return 1

    conn = duckdb.connect(str(DB_PATH), read_only=True)
    symbols = [
        r[0]
        for r in conn.execute(
            f"""
            WITH dv AS (
                SELECT act_symbol, SUM(volume * close) AS dv
                FROM v_ohlcv
                WHERE date >= CURRENT_DATE - INTERVAL '90' DAY
                  AND volume IS NOT NULL AND close IS NOT NULL
                GROUP BY act_symbol
            ),
            earners AS (SELECT DISTINCT act_symbol FROM v_earnings)
            SELECT d.act_symbol
            FROM dv d JOIN earners e USING (act_symbol)
            ORDER BY d.dv DESC
            LIMIT {TOP_N}
            """
        ).fetchall()
    ]

    # Preserve prior cache entries so partial failures don't lose data.
    caps: dict[str, float] = {}
    logos: dict[str, str] = {}
    if CACHE_PATH.exists():
        try:
            prior = json.loads(CACHE_PATH.read_text())
            caps = dict(prior.get("market_caps", {}))
            logos = {
                k: v for k, v in dict(prior.get("logos", {})).items()
                if isinstance(k, str) and isinstance(v, str) and v.startswith("http")
            }
        except Exception:
            caps = {}
            logos = {}

    print("Fetching live FX rates...")
    fx = fetch_fx_rates()

    fetched = 0
    skipped = 0
    non_usd_count = 0
    unknown_currencies: dict[str, int] = {}
    print(f"Pulling market caps for top {len(symbols)} tickers (delay {REQUEST_DELAY}s)…")
    for i, sym in enumerate(symbols, 1):
        if i == 1 or i % 50 == 0 or i == len(symbols):
            print(
                f"  [{i}/{len(symbols)}] {sym}  fetched={fetched} "
                f"non_usd={non_usd_count} skipped={skipped}",
                flush=True,
            )
        mcap_usd, currency, logo_url = fetch_profile(sym, token, fx)
        if mcap_usd is not None and mcap_usd > 0:
            caps[sym] = mcap_usd
            fetched += 1
            if currency and currency != "USD":
                non_usd_count += 1
                if currency not in fx:
                    unknown_currencies[currency] = unknown_currencies.get(currency, 0) + 1
        else:
            skipped += 1
        if logo_url:
            logos[sym] = logo_url
        time.sleep(REQUEST_DELAY)

    if unknown_currencies:
        print(
            f"  ⚠ unmapped currencies (passed through 1:1 to USD): "
            f"{unknown_currencies}"
        )

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "finnhub /stock/profile2",
        "unit": "USD millions",
        "ticker_count": len(caps),
        "fx_source": "open.er-api.com (live, refreshed each fetch)",
        "fx_rates": {k: v for k, v in sorted(fx.items())},
        "notes": (
            "Market capitalization converted to USD millions using live FX "
            "rates from open.er-api.com. Refreshed weekly alongside the "
            "mcap fetch. Used by build_popular_weights.py as the size "
            "half of the composite popularity score (other half is 90d "
            "dollar volume)."
        ),
        "market_caps": dict(sorted(caps.items())),
        "logos": dict(sorted(logos.items())),
    }
    CACHE_PATH.write_text(json.dumps(payload, indent=2))
    write_logo_cache(logos)
    print(
        f"✅ Wrote {len(caps):,} market caps "
        f"({fetched} fresh, {skipped} skipped) → "
        f"{CACHE_PATH.relative_to(REPO_ROOT)}"
    )
    print(f"✅ Wrote {len(logos):,} logos → {LOGO_CACHE_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
