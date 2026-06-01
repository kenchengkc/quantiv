#!/usr/bin/env python3
"""Derive each company's fiscal-year-end month from Polygon/Massive financials.

The earnings calendar mislabels fiscal quarters/years for non-December filers
(see tools/fiscal_calendar.py). The first step of a backfill is knowing, per
ticker, the month its fiscal year ends. Polygon's ``vX/reference/financials``
returns ``fiscal_period`` + ``end_date`` per filing, from which the year-end
month is unambiguous.

This writes two artifacts:

  * ``config/fiscal_year_end.json`` — ``{ "fye_month": { "DG": 1, "UVV": 3, ... } }``
    The durable, reviewable map of fiscal-year-end months.
  * ``data/fiscal_calendar_derived.json`` — full detail incl. recent
    ``fiscal_period`` → ``end_date`` samples (for a later fiscal_q recompute).

Non-December filers (``fye_month != 12``) are flagged as ``needs_naming_review``:
the begin-vs-end-year naming convention is per-company (NVDA/WMT end in January
but name by END year; DG/LULU/FIVE/PVH name by BEGIN year) and must be verified
against the issuer's 10-K before adding an offset to
``config/fiscal_year_naming.json``.

Rate-limited through the shared provider ledger (Polygon = 5/min by default),
resumable via ``--refresh-after-days`` against the existing output.

Run: ``python scripts/derive_fiscal_calendar.py --symbols DG,UVV,LULU,NVDA``
     ``python scripts/derive_fiscal_calendar.py --from-calendar --limit 250``
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from provider_utils import (
    ProviderQuotaError,
    ProviderUsageLedger,
    api_keys_for_provider,
    default_data_dir,
    load_local_env,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config" / "fiscal_year_end.json"
DETAIL_PATH = default_data_dir() / "fiscal_calendar_derived.json"
EARNINGS_CSV = default_data_dir() / "earnings_calendar.csv"
POLYGON_FINANCIALS = "https://api.polygon.io/vX/reference/financials"

MONTH_NAME = {1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
              7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec"}


def calendar_tickers(limit: int | None) -> list[str]:
    if not EARNINGS_CSV.exists():
        return []
    seen: dict[str, int] = {}
    with EARNINGS_CSV.open() as fh:
        header = fh.readline().rstrip("\n").split(",")
        try:
            i_sym, i_date = header.index("act_symbol"), header.index("date")
        except ValueError:
            return []
        for line in fh:
            parts = line.split(",")
            if len(parts) <= max(i_sym, i_date):
                continue
            sym, dt = parts[i_sym].strip().upper(), parts[i_date]
            if sym:
                seen[sym] = max(seen.get(sym, ""), dt)  # type: ignore[arg-type]
    # Most-recently-seen tickers first (active universe).
    ordered = sorted(seen, key=lambda s: seen[s], reverse=True)
    return ordered[:limit] if limit else ordered


def fetch_financials(symbol: str, api_key: str) -> list[dict[str, Any]]:
    resp = requests.get(
        POLYGON_FINANCIALS,
        params={"ticker": symbol, "limit": 8, "apiKey": api_key,
                "sort": "period_of_report_date", "order": "desc"},
        timeout=45,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}")
    return resp.json().get("results", []) or []


def fye_month_from_results(results: list[dict[str, Any]]) -> tuple[int | None, list[dict[str, str]]]:
    """Return (fiscal_year_end_month, period samples). The FYE month is the
    end_date month of an annual (FY) filing, else inferred from the latest Q4."""
    samples: list[dict[str, str]] = []
    fy_end_month: int | None = None
    q4_end_month: int | None = None
    for r in results:
        period = str(r.get("fiscal_period") or "").upper()
        end = str(r.get("end_date") or "")
        samples.append({"fiscal_period": period, "fiscal_year": str(r.get("fiscal_year") or ""),
                        "end_date": end})
        if len(end) >= 7:
            month = int(end[5:7])
            if period == "FY" and fy_end_month is None:
                fy_end_month = month
            if period == "Q4" and q4_end_month is None:
                q4_end_month = month
    return (fy_end_month if fy_end_month is not None else q4_end_month), samples[:6]


def main() -> int:
    load_local_env()
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbols", default="", help="Comma/space-separated tickers")
    parser.add_argument("--from-calendar", action="store_true",
                        help="Use distinct tickers from earnings_calendar.csv")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--detail", type=Path, default=DETAIL_PATH)
    parser.add_argument("--refresh-after-days", type=int, default=180,
                        help="Skip tickers derived within this many days")
    parser.add_argument("--respect-minute-limits", action="store_true", default=True)
    parser.add_argument("--allow-missing-key", action="store_true")
    args = parser.parse_args()

    keys = api_keys_for_provider("massive")  # POLYGON_API_KEY
    if not keys:
        msg = "POLYGON_API_KEY missing"
        print(msg, file=sys.stderr)
        return 0 if args.allow_missing_key else 1
    api_key = keys[0]

    manual = [s for s in args.symbols.replace(",", " ").split() if s]
    symbols = [s.strip().upper() for s in manual]
    if args.from_calendar and not symbols:
        symbols = calendar_tickers(args.limit)
    if args.limit:
        symbols = symbols[: args.limit]
    if not symbols:
        print("no symbols selected (use --symbols or --from-calendar)", file=sys.stderr)
        return 1

    # Load existing artifacts (resume / merge).
    existing_cfg: dict[str, Any] = {}
    if args.config.exists():
        try:
            existing_cfg = json.loads(args.config.read_text())
        except json.JSONDecodeError:
            existing_cfg = {}
    fye_month: dict[str, int] = dict(existing_cfg.get("fye_month", {}))
    detail: dict[str, Any] = {}
    if args.detail.exists():
        try:
            detail = json.loads(args.detail.read_text())
        except json.JSONDecodeError:
            detail = {}

    now = datetime.now(timezone.utc)
    ledger = ProviderUsageLedger()
    derived, skipped, failed = 0, 0, 0

    for i, sym in enumerate(symbols, 1):
        prev = detail.get(sym)
        if prev and prev.get("derived_at"):
            try:
                age = (now - datetime.fromisoformat(prev["derived_at"])).days
                if age < args.refresh_after_days:
                    skipped += 1
                    continue
            except ValueError:
                pass
        try:
            ledger.reserve_pooled("massive", "polygon_financials", ["k0"],
                                  wait_for_minute=args.respect_minute_limits)
        except ProviderQuotaError as exc:
            print(f"  stop: {exc}")
            break
        try:
            results = fetch_financials(sym, api_key)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  {sym}: fetch failed ({exc})")
            continue

        month, samples = fye_month_from_results(results)
        rec: dict[str, Any] = {"derived_at": now.isoformat(), "fye_month": month,
                               "samples": samples}
        detail[sym] = rec
        if month is not None:
            fye_month[sym] = month
            tag = "" if month == 12 else "  ← non-December (needs naming review)"
            print(f"  {sym}: FYE {MONTH_NAME[month]}{tag}  ({i}/{len(symbols)})")
            derived += 1
        else:
            print(f"  {sym}: no fiscal data ({i}/{len(symbols)})")

    # Write outputs.
    non_dec = sorted(s for s, m in fye_month.items() if m != 12)
    out_cfg = {
        "_comment": ("Fiscal-year-end month per ticker, derived from Polygon "
                     "financials by scripts/derive_fiscal_calendar.py. Non-December "
                     "filers may need an entry in config/fiscal_year_naming.json — "
                     "verify begin-vs-end-year convention against the 10-K."),
        "needs_naming_review": non_dec,
        "fye_month": dict(sorted(fye_month.items())),
    }
    args.config.parent.mkdir(parents=True, exist_ok=True)
    args.config.write_text(json.dumps(out_cfg, indent=2) + "\n")
    args.detail.parent.mkdir(parents=True, exist_ok=True)
    args.detail.write_text(json.dumps(detail, indent=2, sort_keys=True) + "\n")

    dist = Counter(fye_month.values())
    print(f"\nderived={derived} skipped={skipped} failed={failed}")
    print(f"FYE-month distribution: "
          + ", ".join(f"{MONTH_NAME[m]}={n}" for m, n in sorted(dist.items())))
    print(f"non-December filers needing naming review: {len(non_dec)} -> {non_dec[:20]}"
          + (" ..." if len(non_dec) > 20 else ""))
    print(f"wrote {args.config} and {args.detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
