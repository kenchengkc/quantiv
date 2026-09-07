"""
Gate the daily refresh by comparing the freshly built earnings calendar against
an explicitly materialized prior-release baseline.

Production uses ``data/validation/earnings_calendar_baseline.csv``, written by
``scripts/r2_pull.sh`` from the previously published R2 calendar before any
provider mutation occurs. This keeps the regression gate independent of Git
working-tree state. For local/backward-compatible use only, the gate can still
fall back to the CSV at git HEAD when no explicit baseline exists.

Always prints a structured diff summary and exits non-zero if a guardrail trips.

History: shipped 2026-05-18 after a sync_dolthub.py left-join bug wiped ~960
Finnhub-discovered upcoming earnings rows silently across multiple CI runs.
"""
from __future__ import annotations

import argparse
import io
import subprocess
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sync_finnhub_earnings import is_us_symbol  # noqa: E402
from delisted import is_retired  # noqa: E402

CSV_PATH = Path("data/earnings_calendar.csv")
DEFAULT_BASELINE_PATH = Path("data/validation/earnings_calendar_baseline.csv")


def read_csv(buf_or_path) -> pd.DataFrame:
    df = pd.read_csv(buf_or_path, keep_default_na=False)
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    return df.dropna(subset=["date", "act_symbol"])


def _git_head_csv() -> pd.DataFrame | None:
    """Compatibility fallback for local checkouts during the migration."""
    try:
        blob = subprocess.run(
            ["git", "show", f"HEAD:{CSV_PATH}"],
            capture_output=True,
            check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    if not blob:
        return None
    return read_csv(io.BytesIO(blob))


def baseline_csv(path: Path) -> tuple[pd.DataFrame | None, str | None]:
    if path.exists():
        return read_csv(path), str(path)
    fallback = _git_head_csv()
    if fallback is not None:
        return fallback, f"git HEAD:{CSV_PATH} (compatibility fallback)"
    return None, None


def fmt_delta(new: int, old: int) -> str:
    delta = new - old
    pct = (delta / old * 100) if old else 0.0
    sign = "+" if delta >= 0 else ""
    return f"{old:,} → {new:,}  ({sign}{delta:,}, {sign}{pct:.2f}%)"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--baseline",
        type=Path,
        default=DEFAULT_BASELINE_PATH,
        help=f"Prior verified calendar snapshot (default: {DEFAULT_BASELINE_PATH})",
    )
    p.add_argument(
        "--require-baseline",
        action="store_true",
        help="Fail closed when no explicit baseline file exists. Production CI should set this.",
    )
    # Thresholds were tuned against ordinary daily churn. The anchor gate is
    # the strongest silent-drop protection; event-count thresholds are
    # secondary backstops sized below the 2026-05-18 regression signature.
    p.add_argument(
        "--max-row-drop-pct",
        type=float,
        default=2.5,
        help="Fail if US-only row count drops more than this percent (default 2.5)",
    )
    p.add_argument(
        "--max-ticker-drop",
        type=int,
        default=50,
        help="Fail if more than this many US tickers vanish from the universe (default 50)",
    )
    p.add_argument(
        "--max-past-vanished",
        type=int,
        default=400,
        help="Fail if more than this many US (ticker, date) events from the past 30 days vanish vs baseline (default 400)",
    )
    p.add_argument(
        "--max-future-vanished",
        type=int,
        default=150,
        help="Fail if more than this many US (ticker, date) events in the next 60 days vanish vs baseline (default 150)",
    )
    p.add_argument(
        "--anchor-min-history",
        type=int,
        default=10,
        help="Tickers with ≥ this many historical rows are anchors; any anchor losing all rows fails the gate",
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

    if args.require_baseline and not args.baseline.exists():
        print(f"❌ Required prior-release baseline missing: {args.baseline}")
        return 1

    new = read_csv(CSV_PATH)
    old, baseline_source = baseline_csv(args.baseline)

    if old is None:
        print("ℹ No prior calendar baseline available — skipping comparative integrity gates")
        print(f"  current: {len(new):,} rows across {new['act_symbol'].nunique():,} tickers")
        return 0

    print("=" * 60)
    print("EARNINGS CALENDAR INTEGRITY CHECK")
    print("=" * 60)
    print(f"Baseline: {baseline_source}")
    print(f"Rows:    {fmt_delta(len(new), len(old))}")
    print(
        f"Tickers: {fmt_delta(new['act_symbol'].nunique(), old['act_symbol'].nunique())}"
    )

    src_new = new["source"].value_counts().to_dict() if "source" in new.columns else {}
    src_old = old["source"].value_counts().to_dict() if "source" in old.columns else {}
    if src_new or src_old:
        print("By source:")
        for k in sorted(set(src_new) | set(src_old)):
            print(f"  {k:24} {fmt_delta(int(src_new.get(k, 0)), int(src_old.get(k, 0)))}")

    k_new = set(zip(new["act_symbol"], new["date"]))
    k_old = set(zip(old["act_symbol"], old["date"]))
    added = k_new - k_old
    removed = k_old - k_new
    print(f"Events: +{len(added):,} added, −{len(removed):,} removed")

    old_counts_by_ticker = old["act_symbol"].value_counts()
    old_sources_by_ticker = (
        old.groupby("act_symbol")["source"].agg(set).to_dict()
        if "source" in old.columns
        else {}
    )

    def _is_low_confidence_provider_churn(sym: str) -> bool:
        if int(old_counts_by_ticker.get(sym, 0)) >= args.anchor_min_history:
            return False
        sources = old_sources_by_ticker.get(sym, ())
        return bool(sources) and all(
            "dolthub" not in str(source).lower() for source in sources
        )

    # Date-drift rescue: estimated earnings dates can shift as the event
    # approaches. A vanished key is rescued when the same ticker still has a
    # nearby event in the new calendar; complete ticker loss remains visible.
    DATE_DRIFT_DAYS = 14

    today = pd.Timestamp.today().date()
    past_start = today - pd.Timedelta(days=30)
    future_end = today + pd.Timedelta(days=60)
    past_old = {k for k in k_old if past_start <= k[1] <= today and is_us_symbol(k[0])}
    past_new = {k for k in k_new if past_start <= k[1] <= today and is_us_symbol(k[0])}
    future_old = {k for k in k_old if today < k[1] <= future_end and is_us_symbol(k[0])}
    future_new = {k for k in k_new if today < k[1] <= future_end and is_us_symbol(k[0])}

    new_dates_by_ticker: dict[str, list] = {}
    for sym, d in k_new:
        if is_us_symbol(sym):
            new_dates_by_ticker.setdefault(sym, []).append(d)

    def _rescued(sym, d) -> bool:
        nearby = new_dates_by_ticker.get(sym)
        if not nearby:
            return False
        return any(abs((d - nd).days) <= DATE_DRIFT_DAYS for nd in nearby)

    past_vanished_raw = past_old - past_new
    future_vanished_raw = future_old - future_new
    past_vanished_base = {
        k for k in past_vanished_raw if not _rescued(*k) and not is_retired(k[0])
    }
    future_vanished_base = {
        k for k in future_vanished_raw if not _rescued(*k) and not is_retired(k[0])
    }
    past_churn_trimmed = {
        k for k in past_vanished_base if _is_low_confidence_provider_churn(k[0])
    }
    future_churn_trimmed = {
        k for k in future_vanished_base if _is_low_confidence_provider_churn(k[0])
    }
    past_vanished = past_vanished_base - past_churn_trimmed
    future_vanished = future_vanished_base - future_churn_trimmed
    past_drifted = len(past_vanished_raw) - len(past_vanished_base)
    future_drifted = len(future_vanished_raw) - len(future_vanished_base)
    print(
        f"Past 30d:  {len(past_new):,} events  "
        f"({len(past_vanished):,} vanished vs baseline, "
        f"{past_drifted:,} date-drifted, {len(past_new - past_old):,} new)"
    )
    print(
        f"Next 60d:  {len(future_new):,} events  "
        f"({len(future_vanished):,} vanished vs baseline, "
        f"{future_drifted:,} date-drifted, {len(future_new - future_old):,} new)"
    )
    if past_churn_trimmed or future_churn_trimmed:
        print(
            "Provider-only event churn excluded from event gates: "
            f"{len(past_churn_trimmed):,} past-30d, "
            f"{len(future_churn_trimmed):,} next-60d "
            f"(<{args.anchor_min_history} prior rows, no DoltHub backing)"
        )

    print()
    tripped: list[str] = []

    old_us = old[old["act_symbol"].map(is_us_symbol)]
    new_us = new[new["act_symbol"].map(is_us_symbol)]
    if len(old_us) > 0:
        row_drop_pct = (len(old_us) - len(new_us)) / len(old_us) * 100
        print(
            f"  (US-only rows: {len(old_us):,} → {len(new_us):,}; "
            f"foreign trimmed: {len(old) - len(old_us):,} → {len(new) - len(new_us):,})"
        )
        if row_drop_pct > args.max_row_drop_pct:
            tripped.append(
                f"US row count dropped {row_drop_pct:.2f}% "
                f"(threshold: {args.max_row_drop_pct}%, "
                f"{len(old_us):,} → {len(new_us):,})"
            )

    vanished_all = set(old["act_symbol"]) - set(new["act_symbol"])
    foreign_trimmed = {t for t in vanished_all if not is_us_symbol(t)}
    retired_trimmed = {t for t in vanished_all if is_retired(t)}
    vanished = vanished_all - foreign_trimmed - retired_trimmed
    churn_trimmed = {t for t in vanished if _is_low_confidence_provider_churn(t)}
    vanished = vanished - churn_trimmed
    if foreign_trimmed:
        print(f"  ({len(foreign_trimmed):,} foreign-symbol trims excluded from gate)")
    if retired_trimmed:
        print(
            f"  ({len(retired_trimmed):,} delisted/renamed trims excluded from gate: "
            f"{sorted(retired_trimmed)})"
        )
    if churn_trimmed:
        print(
            f"  ({len(churn_trimmed):,} low-confidence provider-only churn trims "
            f"excluded from gate: <{args.anchor_min_history} prior rows, no DoltHub backing)"
        )
    if len(vanished) > args.max_ticker_drop:
        sample = sorted(vanished)[:15]
        tripped.append(
            f"{len(vanished):,} US tickers vanished from universe "
            f"(threshold: {args.max_ticker_drop}). Sample: {sample}"
        )

    if len(past_vanished) > args.max_past_vanished:
        sample = sorted(past_vanished, key=lambda x: (x[1], x[0]), reverse=True)[:15]
        tripped.append(
            f"{len(past_vanished):,} events from the past 30 days vanished vs baseline "
            f"(threshold: {args.max_past_vanished}). Sample: {sample}"
        )

    if len(future_vanished) > args.max_future_vanished:
        sample = sorted(future_vanished, key=lambda x: (x[1], x[0]))[:15]
        tripped.append(
            f"{len(future_vanished):,} events in the next 60 days vanished vs baseline "
            f"(threshold: {args.max_future_vanished}). Sample: {sample}"
        )

    blank_new = new[new["act_symbol"].astype(str).str.strip() == ""]
    if len(blank_new):
        sample_dates = sorted(blank_new["date"].unique())[:5]
        tripped.append(
            f"{len(blank_new):,} rows in new CSV have blank act_symbol "
            f"(sample dates: {sample_dates})"
        )

    anchors = {
        sym
        for sym in old_counts_by_ticker[
            old_counts_by_ticker >= args.anchor_min_history
        ].index
        if str(sym or "").strip()
    }
    anchors_in_new = set(new["act_symbol"].unique())
    lost_anchors = {a for a in (anchors - anchors_in_new) if not is_retired(a)}
    if lost_anchors:
        sample = sorted(lost_anchors)[:20]
        tripped.append(
            f"{len(lost_anchors):,} anchor tickers (≥{args.anchor_min_history} prior rows) "
            f"lost all rows: {sample}"
        )

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

    print("Aborting publication to prevent shipping a regressed earnings calendar.")
    print("If this is a legitimate large change, re-run with --warn-only or")
    print("widen the threshold flags (--max-row-drop-pct / --max-ticker-drop / ...).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
