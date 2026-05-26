"""Cache Finnhub company profile/logo metadata for the earnings universe.

This is intentionally separate from live quote polling. It uses Finnhub's
slow-moving /stock/profile2 endpoint and refuses to run during the regular
quote refresh window unless explicitly overridden.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(REPO_ROOT / "scripts"))
from provider_market_hours import (  # noqa: E402
    block_finnhub_reserved_window,
    is_finnhub_reserved_window,
)

PROFILE_URL = "https://finnhub.io/api/v1/stock/profile2"
OUTPUT_PATH = REPO_ROOT / "apps" / "frontend" / "public" / "ticker-logos.json"
EARNINGS_CSV = REPO_ROOT / "data" / "earnings_calendar.csv"
PUBLIC_DIR = REPO_ROOT / "apps" / "frontend" / "public"
SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
DEFAULT_DELAY_S = 1.05
DEFAULT_MAX_AGE_DAYS = 7.0


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def normalize_symbol(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    symbol = value.strip().upper()
    return symbol if SYMBOL_RE.match(symbol) else None


def add_symbol(out: set[str], value: object) -> None:
    symbol = normalize_symbol(value)
    if symbol:
        out.add(symbol)


def walk_json_for_symbols(value: object, out: set[str]) -> None:
    if isinstance(value, dict):
        for key in ("ticker", "symbol", "act_symbol"):
            add_symbol(out, value.get(key))
        for child in value.values():
            walk_json_for_symbols(child, out)
    elif isinstance(value, list):
        for child in value:
            walk_json_for_symbols(child, out)


def load_json_symbols(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text())
    except Exception:
        return set()
    out: set[str] = set()
    walk_json_for_symbols(payload, out)
    return out


def load_symbol_page_universe() -> set[str]:
    symbols_dir = PUBLIC_DIR / "symbols"
    if not symbols_dir.exists():
        return set()
    return {
        symbol
        for path in symbols_dir.glob("*.json")
        if (symbol := normalize_symbol(path.stem))
    }


def load_week_universe() -> set[str]:
    out: set[str] = set()
    for path in (PUBLIC_DIR / "weeks").glob("*.json"):
        out.update(load_json_symbols(path))
    return out


def load_frontend_universe() -> set[str]:
    out = load_symbol_page_universe()
    out.update(load_week_universe())
    out.update(load_json_symbols(PUBLIC_DIR / "weekly.json"))
    out.update(load_json_symbols(PUBLIC_DIR / "screener.json"))
    return out


def load_earnings_universe() -> set[str]:
    out = load_frontend_universe()
    if not EARNINGS_CSV.exists():
        return out
    with EARNINGS_CSV.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            add_symbol(out, row.get("act_symbol"))
    return out


def load_ticker_name_universe() -> set[str]:
    path = PUBLIC_DIR / "ticker-names.json"
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text())
    except Exception:
        return set()
    return {
        symbol
        for value in payload.keys()
        if (symbol := normalize_symbol(value))
    }


def load_universe(kind: str) -> list[str]:
    if kind == "frontend":
        symbols = load_frontend_universe()
    elif kind == "earnings":
        symbols = load_earnings_universe()
    elif kind == "all":
        symbols = load_earnings_universe()
        symbols.update(load_ticker_name_universe())
    else:
        raise ValueError(f"unknown universe: {kind}")
    return sorted(symbols)


def load_cache() -> dict[str, Any]:
    if not OUTPUT_PATH.exists():
        return {}
    try:
        payload = json.loads(OUTPUT_PATH.read_text())
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def cache_age_days(payload: dict[str, Any]) -> float | None:
    ts = payload.get("profile_sweep_at") or payload.get("generated_at")
    if not isinstance(ts, str) or not ts:
        return None
    try:
        gen = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - gen).total_seconds() / 86400


def clean_logo_map(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, str] = {}
    for key, url in value.items():
        symbol = normalize_symbol(key)
        if symbol and isinstance(url, str) and url.startswith(("http://", "https://")):
            out[symbol] = url
    return out


def clean_profile_map(value: object) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key, profile in value.items():
        symbol = normalize_symbol(key)
        if symbol and isinstance(profile, dict):
            out[symbol] = profile
    return out


def normalize_profile(symbol: str, body: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "name": body.get("name"),
        "ticker": body.get("ticker") or symbol,
        "exchange": body.get("exchange"),
        "currency": body.get("currency"),
        "country": body.get("country"),
        "ipo": body.get("ipo"),
        "marketCapitalization": body.get("marketCapitalization"),
        "shareOutstanding": body.get("shareOutstanding"),
        "finnhubIndustry": body.get("finnhubIndustry"),
        "weburl": body.get("weburl"),
        "logo": body.get("logo"),
    }
    cleaned: dict[str, Any] = {
        key: value
        for key, value in fields.items()
        if value not in (None, "", [], {})
    }
    cleaned["has_profile"] = bool(cleaned.get("name") or cleaned.get("logo"))
    cleaned["fetched_at"] = utc_now_iso()
    return cleaned


def fetch_profile(symbol: str, token: str) -> dict[str, Any] | None:
    for attempt in range(3):
        try:
            resp = requests.get(
                PROFILE_URL,
                params={"symbol": symbol, "token": token},
                timeout=20,
            )
        except requests.RequestException:
            if attempt == 2:
                return None
            time.sleep(2**attempt)
            continue
        if resp.status_code == 429 and attempt < 2:
            time.sleep(5 * (attempt + 1))
            continue
        if not resp.ok:
            return None
        try:
            body = resp.json()
        except ValueError:
            return None
        if not isinstance(body, dict):
            return None
        return normalize_profile(symbol, body)
    return None


def write_cache(
    logos: dict[str, str],
    profiles: dict[str, dict[str, Any]],
    *,
    mode: str,
    universe: str,
    attempted: int,
    fetched: int,
    failed: int,
    stopped_reason: str | None,
) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    now = utc_now_iso()
    payload = {
        "generated_at": now,
        "profile_sweep_at": now,
        "source": "finnhub /stock/profile2",
        "mode": mode,
        "universe": universe,
        "ticker_count": len(logos),
        "profile_count": len(profiles),
        "attempted": attempted,
        "fetched": fetched,
        "failed": failed,
        "stopped_reason": stopped_reason,
        "logos": dict(sorted(logos.items())),
        "profiles": dict(sorted(profiles.items())),
    }
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--full",
        action="store_true",
        help="Refetch every symbol in the selected universe.",
    )
    parser.add_argument(
        "--missing-only",
        action="store_true",
        help="Fetch only symbols not already present in the profile cache. This is the default.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Run even if the profile cache is newer than --max-age-days.",
    )
    parser.add_argument(
        "--universe",
        choices=["frontend", "earnings", "all"],
        default="earnings",
        help="Symbol universe to sweep. Default: earnings.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY_S,
        help="Delay between Finnhub calls. Default: 1.05s.",
    )
    parser.add_argument(
        "--max-age-days",
        type=float,
        default=DEFAULT_MAX_AGE_DAYS,
        help="Skip missing-only runs when the cache is fresher than this many days.",
    )
    parser.add_argument(
        "--max-calls",
        type=int,
        default=None,
        help="Maximum Finnhub calls for this run. Useful for phased backfills.",
    )
    parser.add_argument(
        "--allow-missing-key",
        action="store_true",
        help="Exit cleanly if FINNHUB_API_KEY is missing.",
    )
    parser.add_argument(
        "--allow-market-hours",
        action="store_true",
        help="Allow non-price Finnhub calls during the 09:25-16:45 ET quote refresh window.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the planned work without calling Finnhub or writing the cache.",
    )
    args = parser.parse_args()

    if args.full and args.missing_only:
        print("Choose only one of --full or --missing-only.", file=sys.stderr)
        return 2

    mode = "full" if args.full else "missing-only"
    cache = load_cache()
    logos = clean_logo_map(cache.get("logos"))
    profiles = clean_profile_map(cache.get("profiles"))

    age = cache_age_days(cache)
    if (
        mode == "missing-only"
        and age is not None
        and profiles
        and age < args.max_age_days
        and not args.force
    ):
        print(
            f"✅ Profile cache age {age:.1f}d < {args.max_age_days:.1f}d; "
            "skipping missing-only sweep."
        )
        return 0

    symbols = load_universe(args.universe)
    if mode == "missing-only":
        targets = [symbol for symbol in symbols if symbol not in profiles]
    else:
        targets = symbols
    if args.max_calls is not None:
        targets = targets[: max(args.max_calls, 0)]

    print(
        f"Finnhub profile sweep: mode={mode} universe={args.universe} "
        f"symbols={len(symbols):,} targets={len(targets):,} "
        f"cached_profiles={len(profiles):,} cached_logos={len(logos):,}"
    )
    if args.dry_run:
        print("Dry run only; no Finnhub calls or writes.")
        return 0
    if not targets:
        write_cache(
            logos,
            profiles,
            mode=mode,
            universe=args.universe,
            attempted=0,
            fetched=0,
            failed=0,
            stopped_reason=None,
        )
        print(f"✅ No missing profiles. Refreshed {OUTPUT_PATH.relative_to(REPO_ROOT)}")
        return 0

    load_local_env()
    token = os.getenv("FINNHUB_API_KEY")
    if not token:
        msg = "FINNHUB_API_KEY missing"
        if args.allow_missing_key:
            print(f"⚠ {msg}; skipping profile sweep.")
            return 0
        print(f"✗ {msg}", file=sys.stderr)
        return 1

    block_finnhub_reserved_window(args.allow_market_hours)

    attempted = 0
    fetched = 0
    failed = 0
    stopped_reason: str | None = None
    for idx, symbol in enumerate(targets, 1):
        if not args.allow_market_hours and is_finnhub_reserved_window():
            stopped_reason = "market_hours_started"
            print("Stopping before the Finnhub quote refresh window starts.")
            break
        if idx == 1 or idx % 50 == 0 or idx == len(targets):
            print(
                f"  [{idx}/{len(targets)}] {symbol} "
                f"fetched={fetched} failed={failed}",
                flush=True,
            )
        profile = fetch_profile(symbol, token)
        attempted += 1
        if profile is None:
            profiles[symbol] = {
                "ticker": symbol,
                "has_profile": False,
                "fetched_at": utc_now_iso(),
                "error": "fetch_failed",
            }
            failed += 1
        else:
            profiles[symbol] = profile
            logo = profile.get("logo")
            if isinstance(logo, str) and logo.startswith(("http://", "https://")):
                logos[symbol] = logo
            fetched += 1
        time.sleep(max(args.delay, 0.0))

    write_cache(
        logos,
        profiles,
        mode=mode,
        universe=args.universe,
        attempted=attempted,
        fetched=fetched,
        failed=failed,
        stopped_reason=stopped_reason,
    )
    print(
        f"✅ Wrote {len(profiles):,} profiles and {len(logos):,} logos "
        f"({attempted:,} attempted, {fetched:,} fetched, {failed:,} failed) → "
        f"{OUTPUT_PATH.relative_to(REPO_ROOT)}"
    )
    if stopped_reason:
        print(f"Stopped early: {stopped_reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
