#!/usr/bin/env python3
"""Apply the canonical ticker-lifecycle ledger to earnings artifacts.

Provider syncs run before the exchange-directory lifecycle detector. A ticker
can therefore be promoted to ``config/delisted_tickers.json`` after the
earnings CSV and Parquet have already been written. This stage closes that
same-run gap: renames are canonicalized, retired symbols are removed, and the
two artifacts are required to describe the same event keys before publication.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

from delisted import delisted_tickers, ticker_renames  # noqa: E402


def _normalize_symbol(value: object) -> str:
    return str(value or "").strip().upper()


def apply_lifecycle(
    frame: pd.DataFrame,
    *,
    renames: dict[str, str],
    retired: frozenset[str],
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Return a lifecycle-normalized frame and mutation counts."""
    if "act_symbol" not in frame.columns or "date" not in frame.columns:
        missing = sorted({"act_symbol", "date"} - set(frame.columns))
        raise ValueError(f"earnings artifact missing required columns: {missing}")

    out = frame.copy()
    symbols = out["act_symbol"].map(_normalize_symbol)
    blank = int(symbols.eq("").sum())
    if blank:
        raise ValueError(f"earnings artifact contains {blank} blank ticker(s)")

    rename_mask = symbols.isin(renames)
    out["act_symbol"] = symbols.map(lambda symbol: renames.get(symbol, symbol))
    retired_mask = out["act_symbol"].isin(retired)
    retired_rows = int(retired_mask.sum())
    out = out.loc[~retired_mask].copy()

    before_dedup = len(out)
    out = out.drop_duplicates(subset=["act_symbol", "date"], keep="last")
    out = out.sort_values(["date", "act_symbol"], kind="stable").reset_index(drop=True)
    return out, {
        "renamed_rows": int(rename_mask.sum()),
        "retired_rows": retired_rows,
        "deduplicated_rows": before_dedup - len(out),
    }


def _event_keys(frame: pd.DataFrame) -> set[tuple[str, str]]:
    return {
        (_normalize_symbol(symbol), str(date_value)[:10])
        for symbol, date_value in frame[["act_symbol", "date"]].itertuples(index=False)
    }


def _atomic_write_csv(frame: pd.DataFrame, path: Path) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    frame.to_csv(temporary, index=False)
    temporary.replace(path)


def _atomic_write_parquet(frame: pd.DataFrame, path: Path) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    frame.to_parquet(temporary, index=False)
    temporary.replace(path)


def apply_artifacts(data_dir: Path) -> dict[str, dict[str, int]]:
    csv_path = data_dir / "earnings_calendar.csv"
    parquet_path = data_dir / "earnings_calendar.parquet"
    missing = [str(path) for path in (csv_path, parquet_path) if not path.exists()]
    if missing:
        raise FileNotFoundError(f"missing earnings artifact(s): {missing}")

    renames = ticker_renames()
    retired = delisted_tickers()
    csv_frame, csv_counts = apply_lifecycle(
        pd.read_csv(csv_path, keep_default_na=False),
        renames=renames,
        retired=retired,
    )
    parquet_frame, parquet_counts = apply_lifecycle(
        pd.read_parquet(parquet_path),
        renames=renames,
        retired=retired,
    )

    csv_keys = _event_keys(csv_frame)
    parquet_keys = _event_keys(parquet_frame)
    if csv_keys != parquet_keys:
        csv_only = sorted(csv_keys - parquet_keys)[:10]
        parquet_only = sorted(parquet_keys - csv_keys)[:10]
        raise ValueError(
            "lifecycle-normalized earnings artifacts disagree: "
            f"csv_only={csv_only}, parquet_only={parquet_only}"
        )

    _atomic_write_csv(csv_frame, csv_path)
    _atomic_write_parquet(parquet_frame, parquet_path)
    return {"csv": csv_counts, "parquet": parquet_counts}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=REPO_ROOT / "data",
        help="Directory containing earnings_calendar.csv and .parquet",
    )
    args = parser.parse_args()

    try:
        results = apply_artifacts(args.data_dir)
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(f"Ticker lifecycle normalization failed: {exc}", file=sys.stderr)
        return 1

    print("TICKER LIFECYCLE NORMALIZATION")
    for artifact, counts in results.items():
        print(
            f"  {artifact}: renamed={counts['renamed_rows']:,} "
            f"retired={counts['retired_rows']:,} "
            f"deduplicated={counts['deduplicated_rows']:,}"
        )
    print("  CSV/Parquet event keys: aligned")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
