#!/usr/bin/env python3
"""Publish the source-level historical research universe."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parent.parent
ML_PACKAGE_ROOT = REPO_ROOT / "apps" / "ml"
for candidate in (REPO_ROOT / "tools", REPO_ROOT / "scripts", ML_PACKAGE_ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from build_earnings_events import build_earnings_events_table, create_duckdb_views  # noqa: E402
from delisted import delisted_tickers  # noqa: E402
from frontend_data.research_history import (  # noqa: E402
    build_historical_event_universe,
    write_historical_event_universe,
)
from frontend_data.research_sources import (  # noqa: E402
    bind_retired_membership,
    fetch_retired_research_sources,
    install_research_corporate_actions,
    install_retired_earnings,
)
from frontend_data.shared import DATA_DIR, EARNINGS_CSV, PUBLIC_DIR  # noqa: E402
from sync_dolthub import EARNINGS_API, STOCKS_API, query  # noqa: E402
from validate_public_contracts import validate_research_history  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--earnings-csv", type=Path, default=EARNINGS_CSV)
    parser.add_argument(
        "--output",
        type=Path,
        default=PUBLIC_DIR / "research-history.json",
    )
    parser.add_argument("--as-of-date", type=date.fromisoformat, default=None)
    parser.add_argument("--source-revision", default=os.getenv("GITHUB_SHA"))
    args = parser.parse_args()

    conn = duckdb.connect()
    conn.execute("PRAGMA memory_limit='4GB'")
    conn.execute("PRAGMA threads=4")
    try:
        build_earnings_events_table(conn, args.earnings_csv)
        create_duckdb_views(conn, args.data_dir)
        as_of_date = args.as_of_date
        if as_of_date is None:
            row = conn.execute("SELECT MAX(as_of_date) FROM v_options_chain").fetchone()
            as_of_date = row[0]
        if as_of_date is None:
            raise RuntimeError("cannot build research history without an options as-of date")

        # The production calendar deliberately removes confirmed delistings.
        # Historical research must not inherit that active-universe filter.
        # Re-query only the explicit retirement ledger and retain the exact
        # normalized source rows inside the content-addressed universe.
        retired_sources = fetch_retired_research_sources(
            query,
            earnings_api=EARNINGS_API,
            stocks_api=STOCKS_API,
            retired_tickers=delisted_tickers(),
            action_end=as_of_date,
        )
        installed_retired = install_retired_earnings(
            conn, retired_sources["earnings"]["rows"]
        )
        action_evidence = install_research_corporate_actions(
            conn,
            data_dir=args.data_dir,
            supplemental_splits=retired_sources["corporate_actions"]["splits"]["rows"],
            supplemental_dividends=retired_sources["corporate_actions"]["dividends"]["rows"],
        )

        payload = build_historical_event_universe(
            conn,
            as_of_date,
            source_revision=args.source_revision,
        )
        payload = bind_retired_membership(
            payload,
            retired_sources,
            installed_event_rows=installed_retired,
            corporate_action_evidence=action_evidence,
        )
        write_historical_event_universe(args.output, payload)

        # Publication's default artifact must pass the same semantic contract
        # consumed by the API. A caller writing a fixture/custom output can
        # validate that payload separately without mutating the repository path.
        if args.output.resolve() == (PUBLIC_DIR / "research-history.json").resolve():
            validate_research_history()
    finally:
        conn.close()

    print(
        "Research history: "
        f"{payload['event_count']} eligible / {payload['audit']['candidate_event_count']} canonical events "
        f"({payload['audit']['excluded_event_count']} excluded) -> {args.output}"
    )
    membership = payload["source"]["retired_membership"]
    print(
        "Retired membership: "
        f"{len(membership['configured_tickers'])} configured tickers, "
        f"{membership['earnings']['row_count']} source rows, "
        f"{membership['installed_event_rows']} research-only rows installed"
    )
    print(f"Universe id: {payload['universe_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
