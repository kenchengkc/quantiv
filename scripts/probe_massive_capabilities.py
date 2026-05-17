#!/usr/bin/env python3
"""
Phase 0 probe: validate what Massive.com Starter actually exposes
before writing any production sync code.

This script is read-only. It does NOT write to data/parquet, does NOT
touch DuckDB views, and does NOT modify R2. It hits the live Massive
REST option-chain snapshot endpoint with the user's credentials and
records a small sample of raw responses plus a schema report.

Why a probe first:
  • The original migration plan assumed Massive flat files contain
    historical daily IV/Greeks rows. That's not what Massive sells —
    flat files are OPRA trades / quotes / aggregates. Greeks/IV/OI
    live in the REST snapshot endpoint, which is current-state, not
    a historical chain archive.
  • Before writing scripts/sync_massive_snapshots.py against assumed
    field names, capture the actual response shape on Starter creds.
  • Find out which Greeks are populated near ATM for liquid vs
    illiquid names. If Starter omits Greeks below a certain plan,
    we'll need either internal Greeks computation or a fallback
    provider for the historical path.

Usage:
  export MASSIVE_API_KEY=...
  python scripts/probe_massive_capabilities.py
  # writes data/ref/provider_samples/massive/YYYY-MM-DD/

Pass criteria (manually verified by reviewing the output):
  • Snapshot returns latest_quote with bid/ask AND non-null IV near ATM
    for the test underlyings.
  • greeks.delta / gamma / theta / vega all present near ATM.
  • Pagination works for a high-OI underlying (next_url chain to >250 rows).
  • contract_type values map cleanly to {Call, Put} (matching the
    canonical schema expected by setup_duckdb_from_parquet.py).

Fail criteria:
  • Snapshot lacks IV or Greeks for ATM contracts.
  • Pagination caps before the full chain is delivered.
  • Field names disagree with the documented schema such that a static
    mapper can't normalize them reliably.
"""

import json
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import requests

# ─── Config ─────────────────────────────────────────────────────────────
# Per Massive docs, the option-chain snapshot endpoint is:
#   GET /v3/snapshot/options/{underlyingAsset}
# Override with MASSIVE_BASE_URL if the user's account lives on a
# different region/host.
BASE_URL = os.getenv("MASSIVE_BASE_URL", "https://api.massive.com")
SNAPSHOT_PATH = "/v3/snapshot/options/{underlying}"

# A few representative tickers — one mega-cap, one mid-cap, one
# small/recent. Adjust if any of these become non-liquid.
TEST_TICKERS = ["AAPL", "NVDA", "TTD"]

# Limit per page (Massive caps at 250). We follow next_url to paginate.
PAGE_LIMIT = 250
# Hard cap on total pages followed per ticker, defense against runaway
# loops if pagination chains accidentally cycle.
MAX_PAGES_PER_TICKER = 50

# Default sample dir under the repo's data/ref/ tree.
REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_ROOT = REPO_ROOT / "data" / "ref" / "provider_samples" / "massive"


def api_key() -> str:
    key = os.getenv("MASSIVE_API_KEY")
    if not key:
        print(
            "✗ MASSIVE_API_KEY not set.\n"
            "  Sign up at https://massive.com/ → copy API key from the\n"
            "  dashboard → export MASSIVE_API_KEY=...",
            file=sys.stderr,
        )
        sys.exit(1)
    return key


def headers() -> dict:
    return {
        "Authorization": f"Bearer {api_key()}",
        "Accept": "application/json",
        "User-Agent": "Quantiv massive-probe (ken@quantiv.app)",
    }


def fetch_snapshot_pages(underlying: str) -> list[dict]:
    """Follow next_url pagination, capped at MAX_PAGES_PER_TICKER.
    Returns the list of raw page envelopes (not flattened) so the
    sample on disk preserves Massive's exact response shape."""
    url = f"{BASE_URL}{SNAPSHOT_PATH.format(underlying=underlying)}?limit={PAGE_LIMIT}"
    pages: list[dict] = []
    seen_urls: set[str] = set()
    retry_counts: dict[str, int] = {}
    while url and len(pages) < MAX_PAGES_PER_TICKER:
        if url in seen_urls:
            print(f"  ⚠ pagination loop detected at {url} — stopping")
            break
        print(f"  GET {url}")
        try:
            resp = requests.get(url, headers=headers(), timeout=60)
        except requests.RequestException as exc:
            print(f"  ✗ transport error: {exc}")
            break
        if resp.status_code == 429:
            retries = retry_counts.get(url, 0)
            if retries >= 3:
                print("  ✗ rate-limited (429) after 3 retries — stopping")
                break
            retry_counts[url] = retries + 1
            print("  ⚠ rate-limited (429), sleeping 5s and retrying")
            time.sleep(5)
            continue
        if not resp.ok:
            print(f"  ✗ HTTP {resp.status_code}: {resp.text[:300]}")
            break
        seen_urls.add(url)
        body = resp.json()
        pages.append(body)
        next_url = body.get("next_url")
        if not next_url:
            break
        # Massive returns next_url as a full URL; some vendors return
        # path-only — normalize either way.
        url = next_url if next_url.startswith("http") else f"{BASE_URL}{next_url}"
        time.sleep(0.2)  # gentle pacing
    return pages


def field_presence(rows: list[dict], path: str) -> dict:
    """For a dotted path like `greeks.delta`, return {present, total,
    coverage_pct} across rows. Helps spot fields that are documented
    but missing in practice (e.g. Greeks on illiquid contracts)."""
    parts = path.split(".")
    present = 0
    for row in rows:
        cur: object = row
        ok = True
        for p in parts:
            if not isinstance(cur, dict) or p not in cur or cur[p] is None:
                ok = False
                break
            cur = cur[p]
        if ok:
            present += 1
    total = len(rows)
    pct = (100.0 * present / total) if total else 0.0
    return {"present": present, "total": total, "coverage_pct": round(pct, 1)}


def flatten_results(pages: list[dict]) -> list[dict]:
    out: list[dict] = []
    for p in pages:
        out.extend(p.get("results") or [])
    return out


def analyze(underlying: str, pages: list[dict]) -> dict:
    rows = flatten_results(pages)
    if not rows:
        return {"underlying": underlying, "rows": 0, "error": "no_results"}

    # Sample one ATM-ish row and one ITM/OTM row to surface field
    # naming. We use the first row blindly — the user reviews the
    # sample on disk to confirm typical near-ATM shape.
    sample_row = rows[len(rows) // 2]

    # Fields we expect to consume in sync_massive_snapshots.py based
    # on Massive's documented option-chain-snapshot schema. The probe
    # measures actual coverage so the production sync can be defensive.
    checked_paths = [
        "details.contract_type",
        "details.exercise_style",
        "details.expiration_date",
        "details.shares_per_contract",
        "details.strike_price",
        "details.ticker",
        "day.close",
        "day.high",
        "day.low",
        "day.open",
        "day.volume",
        "day.vwap",
        "greeks.delta",
        "greeks.gamma",
        "greeks.theta",
        "greeks.vega",
        "implied_volatility",
        "last_quote.ask",
        "last_quote.bid",
        "last_quote.timestamp",
        "last_trade.price",
        "last_trade.timestamp",
        "open_interest",
        "underlying_asset.ticker",
        "underlying_asset.price",
    ]
    coverage = {p: field_presence(rows, p) for p in checked_paths}

    # contract_type value distribution — confirms 'call'/'put' vs
    # 'Call'/'Put' vs 'C'/'P'. Critical for the call_put column that
    # setup_duckdb_from_parquet.py joins on (expects 'Call'/'Put').
    ct_counts: dict[str, int] = {}
    for r in rows:
        v = (r.get("details") or {}).get("contract_type")
        if v is None:
            v = "<missing>"
        ct_counts[str(v)] = ct_counts.get(str(v), 0) + 1

    return {
        "underlying": underlying,
        "rows": len(rows),
        "pages": len(pages),
        "field_coverage": coverage,
        "contract_type_values": ct_counts,
        "sample_row": sample_row,
    }


def write_samples(sample_dir: Path, underlying: str, pages: list[dict]) -> None:
    sample_dir.mkdir(parents=True, exist_ok=True)
    raw_path = sample_dir / f"{underlying}_pages.json"
    raw_path.write_text(json.dumps(pages, indent=2, default=str) + "\n")
    print(f"  ✓ wrote raw pages → {raw_path}")


def main() -> None:
    today = date.today()
    sample_dir = SAMPLE_ROOT / today.isoformat()
    print(f"Probe started {datetime.now().isoformat()}")
    print(f"Sample dir: {sample_dir}")

    summary: dict = {
        "probed_at": datetime.utcnow().isoformat() + "Z",
        "base_url": BASE_URL,
        "tickers": {},
    }

    for ticker in TEST_TICKERS:
        print(f"\n📡 {ticker}")
        pages = fetch_snapshot_pages(ticker)
        if not pages:
            summary["tickers"][ticker] = {"error": "no_pages"}
            continue
        write_samples(sample_dir, ticker, pages)
        summary["tickers"][ticker] = analyze(ticker, pages)

    # Write the summary at the top level (no nested ticker dirs) so a
    # reviewer can scan one file to make the migration decision.
    sample_dir.mkdir(parents=True, exist_ok=True)
    summary_path = sample_dir / "probe_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str) + "\n")
    print(f"\n✓ summary → {summary_path}")
    print(
        "\nNext step: review the summary's `field_coverage` (any < 80% near-ATM\n"
        "  for IV / Greeks is a red flag for using snapshots in production), and\n"
        "  `contract_type_values` (must be mappable to 'Call' / 'Put' — NOT 'C' / 'P'\n"
        "  — to match the canonical DuckDB-view schema)."
    )


if __name__ == "__main__":
    main()
