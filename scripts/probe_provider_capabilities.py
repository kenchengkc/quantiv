#!/usr/bin/env python3
"""Probe provider entitlements and response shapes for additive data sources."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from provider_probe import CAPABILITIES_PATH, probe_spec, select_specs, write_capabilities
from provider_utils import DEFAULT_LEDGER_PATH, ProviderUsageLedger, load_local_env


def parse_providers(raw: str) -> set[str] | None:
    if not raw or raw.lower() == "all":
        return None
    out = {part.strip().lower() for part in raw.replace(",", " ").split() if part.strip()}
    allowed = {"fmp", "alphavantage", "massive", "twelvedata"}
    unknown = out - allowed
    if unknown:
        raise argparse.ArgumentTypeError(f"unknown provider(s): {', '.join(sorted(unknown))}")
    return out


def main() -> int:
    load_local_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--providers", type=parse_providers, default=None)
    parser.add_argument("--sample-symbol", default="AAPL")
    parser.add_argument("--include-heavy", action="store_true")
    parser.add_argument("--max-calls", type=int, default=35)
    parser.add_argument("--delay", type=float, default=0.0)
    parser.add_argument("--output", type=Path, default=CAPABILITIES_PATH)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER_PATH)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--respect-minute-limits",
        action="store_true",
        help="Sleep as needed for providers with per-minute limits, notably Massive and TwelveData.",
    )
    parser.add_argument(
        "--allow-missing-key",
        action="store_true",
        help="Exit 0 when all planned endpoints only fail because provider keys are absent.",
    )
    args = parser.parse_args()

    if args.max_calls < 1:
        parser.error("--max-calls must be at least 1")

    specs = select_specs(
        providers=args.providers,
        include_heavy=args.include_heavy,
        sample_symbol=args.sample_symbol,
    )[: args.max_calls]

    print(
        "Provider capability probe: "
        f"{len(specs)} endpoint(s), sample={args.sample_symbol.upper()}, "
        f"heavy={'yes' if args.include_heavy else 'no'}"
    )
    if args.dry_run:
        by_provider: dict[str, int] = {}
        for spec in specs:
            by_provider[spec.provider] = by_provider.get(spec.provider, 0) + spec.credit_cost
            print(f"would probe {spec.provider}:{spec.id} -> {spec.category}")
        print(f"planned calls/credits by provider: {by_provider}")
        print("dry run: no API calls made and no files written")
        return 0

    ledger = ProviderUsageLedger(args.ledger)
    results = []
    for idx, spec in enumerate(specs, start=1):
        print(f"probing {spec.provider}:{spec.id} ({idx}/{len(specs)})", flush=True)
        result = probe_spec(
            spec,
            ledger=ledger,
            wait_for_minute=args.respect_minute_limits,
        )
        results.append(result)
        print(f"  {result['status']}")
        if idx < len(specs) and args.delay > 0:
            time.sleep(args.delay)

    payload = write_capabilities(results, output=args.output, sample_symbol=args.sample_symbol.upper())
    status_counts: dict[str, int] = {}
    for result in results:
        status_counts[str(result["status"])] = status_counts.get(str(result["status"]), 0) + 1
    print(f"wrote {args.output}")
    print(f"status counts: {status_counts}")

    if all(result.get("status") == "missing_key" for result in results):
        if args.allow_missing_key:
            return 0
        print("all provider capability probes skipped because keys are missing", file=sys.stderr)
        return 1
    if not payload.get("endpoints"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
