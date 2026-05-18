"""
Gate the daily-refresh auto-commit by comparing the freshly built
data/earnings_calendar.csv against the version at git HEAD.

Always prints a structured diff summary (visible in CI logs); exits
non-zero if any integrity guardrail trips. The workflow's commit step
runs only on success, so a tripped gate aborts the commit without
shipping a regressed CSV.

History: shipped 2026-05-18 after a sync_dolthub.py left-join bug
wiped ~960 Finnhub-discovered upcoming earnings rows (MARA, CYBR,
CLSK, MOS, FSK, etc.) silently across multiple CI runs.
"""
from __future__ import annotations

import argparse
import io
import subprocess
import sys
from pathlib import Path

import pandas as pd

CSV_PATH = Path("data/earnings_calendar.csv")


def read_csv(buf_or_path) -> pd.DataFrame:
    df = pd.read_csv(buf_or_path, keep_default_na=False)
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    return df.dropna(subset=["date", "act_symbol"])


def head_csv() -> pd.DataFrame | None:
    """Return the CSV at git HEAD, or None if we can't read it (e.g.
    first run, file not tracked yet, repo not a git checkout)."""
    try:
        blob = subprocess.run(
            ["git", "show", f"HEAD:{CSV_PATH}"],
            capture_output=True,
            check=True,
        ).stdout
    except subprocess.CalledProcessError:
        return None
    if not blob:
        return None
    return read_csv(io.BytesIO(blob))


def fmt_delta(new: int, old: int) -> str:
    delta = new - old
    pct = (delta / old * 100) if old else 0.0
    sign = "+" if delta >= 0 else ""
    return f"{old:,} → {new:,}  ({sign}{delta:,}, {sign}{pct:.2f}%)"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--max-row-drop-pct",
        type=float,
        default=1.0,
        help="Fail if total row count drops more than this percent (default 1.0)",
    )
    p.add_argument(
        "--max-ticker-drop",
        type=int,
        default=50,
        help="Fail if more than this many tickers vanish from the universe (default 50)",
    )
    p.add_argument(
        "--max-past-vanished",
        type=int,
        default=30,
        help="Fail if more than this many (ticker, date) events from the past 30 days vanish vs HEAD (default 30)",
    )
    p.add_argument(
        "--max-future-vanished",
        type=int,
        default=30,
        help="Fail if more than this many (ticker, date) events in the next 60 days vanish vs HEAD (default 30)",
    )
    p.add_argument(
        "--anchor-min-history",
        type=int,
        default=10,
        help="Tickers with ≥ this many historical rows are 'anchors' — any anchor losing all rows fails the gate (default 10)",
    )
    p.add_argument(
        "--warn-only",
        action="store_true",
        help="Print the summary + tripped gates but always exit 0",
    )
    args = p.parse_args()

    if not CSV_PATH.exists():
        print(f"❌ {CSV_PATH} does not exist — nothing to check")
        return 1

    new = read_csv(CSV_PATH)
    old = head_csv()

    if old is None:
        print("ℹ No prior CSV at HEAD — first commit, skipping integrity gate")
        print(f"  current: {len(new):,} rows across {new['act_symbol'].nunique():,} tickers")
        return 0

    # --- Always-on diff summary --------------------------------------
    print("=" * 60)
    print("EARNINGS CALENDAR INTEGRITY CHECK")
    print("=" * 60)
    print(f"Rows:    {fmt_delta(len(new), len(old))}")
    print(
        f"Tickers: {fmt_delta(new['act_symbol'].nunique(), old['act_symbol'].nunique())}"
    )

    # Source breakdown (where the new rows came from)
    src_new = new["source"].value_counts().to_dict() if "source" in new.columns else {}
    src_old = old["source"].value_counts().to_dict() if "source" in old.columns else {}
    if src_new or src_old:
        print("By source:")
        for k in sorted(set(src_new) | set(src_old)):
            print(f"  {k:24} {fmt_delta(int(src_new.get(k, 0)), int(src_old.get(k, 0)))}")

    # Per-(ticker,date) key set diff
    k_new = set(zip(new["act_symbol"], new["date"]))
    k_old = set(zip(old["act_symbol"], old["date"]))
    added = k_new - k_old
    removed = k_old - k_new
    print(f"Events: +{len(added):,} added, −{len(removed):,} removed")

    # Past 30d / next 60d windows. We compare SETS of (ticker, date) keys
    # across CSVs — not bare counts — so events naturally rolling out of
    # the window don't look like a regression. Only events that existed
    # in HEAD's window AND are missing from the new CSV count as
    # "vanished" (the actual silent-drop signature).
    today = pd.Timestamp.today().date()
    past_start = today - pd.Timedelta(days=30)
    future_end = today + pd.Timedelta(days=60)
    past_old = {k for k in k_old if past_start <= k[1] <= today}
    past_new = {k for k in k_new if past_start <= k[1] <= today}
    future_old = {k for k in k_old if today < k[1] <= future_end}
    future_new = {k for k in k_new if today < k[1] <= future_end}
    past_vanished = past_old - past_new
    future_vanished = future_old - future_new
    print(
        f"Past 30d:  {len(past_new):,} events  "
        f"({len(past_vanished):,} vanished vs HEAD, {len(past_new - past_old):,} new)"
    )
    print(
        f"Next 60d:  {len(future_new):,} events  "
        f"({len(future_vanished):,} vanished vs HEAD, {len(future_new - future_old):,} new)"
    )

    print()

    # --- Guardrails --------------------------------------------------
    tripped: list[str] = []

    # 1. Total row count drop
    if len(old) > 0:
        row_drop_pct = (len(old) - len(new)) / len(old) * 100
        if row_drop_pct > args.max_row_drop_pct:
            tripped.append(
                f"Row count dropped {row_drop_pct:.2f}% "
                f"(threshold: {args.max_row_drop_pct}%, "
                f"{len(old):,} → {len(new):,})"
            )

    # 2. Vanished tickers
    vanished = set(old["act_symbol"]) - set(new["act_symbol"])
    if len(vanished) > args.max_ticker_drop:
        sample = sorted(vanished)[:15]
        tripped.append(
            f"{len(vanished):,} tickers vanished from universe "
            f"(threshold: {args.max_ticker_drop}). Sample: {sample}"
        )

    # 3. Past 30d events vanishing (regressing already-happened earnings).
    if len(past_vanished) > args.max_past_vanished:
        sample = sorted(past_vanished, key=lambda x: (x[1], x[0]), reverse=True)[:15]
        tripped.append(
            f"{len(past_vanished):,} events from the past 30 days vanished vs HEAD "
            f"(threshold: {args.max_past_vanished}). Sample: {sample}"
        )

    # 4. Next 60d events vanishing (regressing upcoming earnings — the
    #    signature of the 2026-05-18 bug, where 712 of the 961 dropped
    #    rows were future-dated upcoming events).
    if len(future_vanished) > args.max_future_vanished:
        sample = sorted(future_vanished, key=lambda x: (x[1], x[0]))[:15]
        tripped.append(
            f"{len(future_vanished):,} events in the next 60 days vanished vs HEAD "
            f"(threshold: {args.max_future_vanished}). Sample: {sample}"
        )

    # 4. Anchor tickers losing all rows. "Anchor" = had ≥N historical rows
    #    in the prior CSV — established names whose disappearance signals
    #    a universe-wide drop, not a delisting.
    ticker_counts_old = old["act_symbol"].value_counts()
    anchors = set(ticker_counts_old[ticker_counts_old >= args.anchor_min_history].index)
    anchors_in_new = set(new["act_symbol"].unique())
    lost_anchors = anchors - anchors_in_new
    if lost_anchors:
        sample = sorted(lost_anchors)[:20]
        tripped.append(
            f"{len(lost_anchors):,} anchor tickers (≥{args.anchor_min_history} prior rows) "
            f"lost all rows: {sample}"
        )

    # --- Result ------------------------------------------------------
    if not tripped:
        print("✅ All integrity gates passed")
        return 0

    print("❌ Integrity gate(s) TRIPPED:")
    for t in tripped:
        print(f"  • {t}")
    print()

    if args.warn_only:
        print("⚠ --warn-only set; exiting 0 despite tripped gate(s)")
        return 0

    print("Aborting auto-commit to prevent shipping a regressed CSV.")
    print("If this is a legitimate large change, re-run with --warn-only or")
    print("widen the threshold flags (--max-row-drop-pct / --max-ticker-drop / ...).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
