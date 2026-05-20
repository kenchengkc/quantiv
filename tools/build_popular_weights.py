"""
Generate apps/frontend/lib/popular.ts from a composite popularity score.

Score = 0.5 × dv_rank_pct + 0.5 × mcap_rank_pct

Each component is the ticker's percentile rank in the candidate
universe (1.0 = top, 0.0 = bottom). Rank-based normalization avoids
two pitfalls:
- Log(mcap) gave foreign mega-cap ADRs (TSM, HDB, BABA) an
  artificially huge size signal, pushing them above NVDA despite far
  smaller US trading flow.
- Log(dv) compressed the long tail so that "rank 200 vs rank 500"
  looked nearly identical in the score.

With ranks, both signals carry equal weight and a marginal name
either trips one threshold or the other, but doesn't get rocket-
shipped by an outlier value on either axis.

Why composite vs pure dollar volume:
- Pure DV rewards activity × size mixed together. AXTI ($7B mcap,
  intense news-day flow) ends up in the same bucket as ABNB ($81B
  mcap, steady household name). Adding market cap as the size
  signal lets brand-recognized large-caps surface even when their
  trading is calmer.
- Retail favorites (MSTR/HOOD/PLTR/COIN/CRWV/RKLB) have BOTH solid
  market caps AND strong flow, so both halves support them.

Inputs:
- v_ohlcv from DuckDB → 90d dollar volume per ticker
- data/market_caps.json from tools/pull_market_caps.py → market caps
  (in $M, refreshed weekly via Finnhub /stock/profile2)
- v_earnings → filters out ETFs (SPY/QQQ/etc.)

Output: top 200 → apps/frontend/lib/popular.ts, weights 76..100 linear
by rank. Frontend filter is `POPULAR_WEIGHT[ticker] >= 76`, so all 200
pass and sort order respects rank.

Fallback: tickers without market-cap data get mcap_rank_pct=0, so
score = 0.5 × dv_rank_pct (max 0.5). They naturally fall below tickers
with both signals; the system degrades gracefully if market_caps.json
goes stale.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "data" / "quantiv.duckdb"
MCAP_PATH = REPO_ROOT / "data" / "market_caps.json"
OUT_PATH = REPO_ROOT / "apps" / "frontend" / "lib" / "popular.ts"
# Yesterday's top-N snapshot. Compared against today to produce the
# added/removed/movers diff written to CHANGES_PATH. Atomically overwritten
# at the end of each successful run.
SNAPSHOT_PATH = REPO_ROOT / "data" / "popular_snapshot.json"
# Append-only JSONL audit log of every rebuild's diff. One line per build.
# Reasonable to tail in CI logs or pipe into a Slack/Discord webhook.
CHANGES_PATH = REPO_ROOT / "data" / "popular_changes.jsonl"

TOP_N = 400
WINDOW_DAYS = 90
MIN_WEIGHT = 76  # must match the >=76 filter in EarningsGrid.tsx
MAX_WEIGHT = 100

W_DV = 0.7    # weight on dollar-volume half of composite
W_MCAP = 0.3  # weight on market-cap half of composite

# Threshold for highlighting a rank change in the diff log. With TOP_N=400,
# 25 places is ~6% of the list — small enough to surface real movement,
# large enough to filter out daily jitter at the edges of the universe.
MOVER_DELTA = 25
# Tuned by sweep — 50/50 over-weighted size and pushed retail favorites
# (MSTR/COIN/CRWV) out in favor of mid-cap S&P names. 0.7/0.3 keeps DV
# as the primary signal (activity = popularity) while mcap acts as a
# tiebreaker that penalizes high-flow penny stocks (AXTI/CLS/AAOI).


def weight_for_rank(rank: int, total: int) -> int:
    if total <= 1:
        return MAX_WEIGHT
    span = MAX_WEIGHT - MIN_WEIGHT
    return round(MAX_WEIGHT - (rank - 1) * span / (total - 1))


def load_market_caps() -> dict[str, float]:
    if not MCAP_PATH.exists():
        print(f"⚠ {MCAP_PATH} missing — falling back to dollar-volume-only")
        return {}
    try:
        data = json.loads(MCAP_PATH.read_text())
        return data.get("market_caps", {})
    except Exception as exc:
        print(f"⚠ could not parse {MCAP_PATH}: {exc} — falling back to DV-only")
        return {}


def rank_pct(rank: int, n: int) -> float:
    """Percentile: rank 1 → ~1.0, rank n → ~0.0."""
    if n <= 1:
        return 1.0
    return 1.0 - (rank - 1) / (n - 1)


def load_snapshot() -> dict | None:
    """Load yesterday's snapshot if it exists. Returns None on missing or
    unreadable file — the diff just treats that as a fresh baseline."""
    if not SNAPSHOT_PATH.exists():
        return None
    try:
        return json.loads(SNAPSHOT_PATH.read_text())
    except Exception as exc:
        print(f"⚠ could not parse {SNAPSHOT_PATH}: {exc} — treating as fresh baseline")
        return None


def compute_diff(
    today_ranks: dict[str, int],
    prev_snapshot: dict | None,
) -> dict:
    """Compare today's rank map to the previous snapshot.

    Returns a dict with `added`, `removed`, `movers_up`, `movers_down`.
    `movers_*` only contain tickers whose absolute rank change clears
    MOVER_DELTA — without that floor the list is dominated by churn at the
    bottom of the universe where one bad volume day shifts a name 5-10
    places.
    """
    prev_ranks: dict[str, int] = {}
    if prev_snapshot and isinstance(prev_snapshot.get("ranks"), dict):
        for sym, rank in prev_snapshot["ranks"].items():
            if isinstance(rank, int) and rank > 0:
                prev_ranks[sym] = rank

    today_set = set(today_ranks)
    prev_set = set(prev_ranks)

    added = sorted(
        (
            {"ticker": s, "rank": today_ranks[s]}
            for s in today_set - prev_set
        ),
        key=lambda r: r["rank"],
    )
    removed = sorted(
        (
            {"ticker": s, "prev_rank": prev_ranks[s]}
            for s in prev_set - today_set
        ),
        key=lambda r: r["prev_rank"],
    )

    movers_up: list[dict] = []
    movers_down: list[dict] = []
    for sym in today_set & prev_set:
        delta = today_ranks[sym] - prev_ranks[sym]
        # delta < 0 means the ticker moved UP the leaderboard (rank 50 → 10).
        if delta <= -MOVER_DELTA:
            movers_up.append(
                {
                    "ticker": sym,
                    "prev_rank": prev_ranks[sym],
                    "rank": today_ranks[sym],
                    "delta": delta,
                }
            )
        elif delta >= MOVER_DELTA:
            movers_down.append(
                {
                    "ticker": sym,
                    "prev_rank": prev_ranks[sym],
                    "rank": today_ranks[sym],
                    "delta": delta,
                }
            )
    movers_up.sort(key=lambda r: r["delta"])  # most negative (biggest climb) first
    movers_down.sort(key=lambda r: -r["delta"])  # biggest drop first

    return {
        "added": added,
        "removed": removed,
        "movers_up": movers_up,
        "movers_down": movers_down,
    }


def write_snapshot(today_ranks: dict[str, int], as_of: str) -> None:
    """Atomically replace the snapshot file. Writes to a sibling .tmp and
    renames so a crash mid-write never leaves a half-baked snapshot that
    would corrupt tomorrow's diff."""
    payload = {"as_of": as_of, "ranks": today_ranks}
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SNAPSHOT_PATH.with_suffix(SNAPSHOT_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")))
    os.replace(tmp, SNAPSHOT_PATH)


def append_changes(
    diff: dict,
    as_of: str,
    prev_as_of: str | None,
    universe_size: int,
) -> None:
    """Append one JSONL entry per build. JSONL is easier to tail/grep
    than a daily-keyed JSON object and lets us preserve same-day re-runs."""
    entry = {
        "ts": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        "as_of": as_of,
        "previous_as_of": prev_as_of,
        "universe_size": universe_size,
        "summary": {
            "added": len(diff["added"]),
            "removed": len(diff["removed"]),
            "movers_up": len(diff["movers_up"]),
            "movers_down": len(diff["movers_down"]),
        },
        **diff,
    }
    CHANGES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CHANGES_PATH.open("a") as fh:
        fh.write(json.dumps(entry, separators=(",", ":")) + "\n")


def print_diff_summary(diff: dict, prev_as_of: str | None) -> None:
    """Render the diff for the CI / shell operator. Kept short on purpose —
    the JSONL log has the full record if you want to look later."""
    if prev_as_of is None:
        print("📓 No previous snapshot found — establishing baseline (no diff to report).")
        return
    print(f"📓 Diff vs {prev_as_of}:")
    print(
        f"   +{len(diff['added'])} added · −{len(diff['removed'])} removed · "
        f"↑{len(diff['movers_up'])} climbers · ↓{len(diff['movers_down'])} fallers "
        f"(|Δrank| ≥ {MOVER_DELTA})"
    )
    if diff["added"]:
        sample = ", ".join(f"{r['ticker']} (#{r['rank']})" for r in diff["added"][:8])
        more = "" if len(diff["added"]) <= 8 else f" … +{len(diff['added']) - 8} more"
        print(f"   ➕ {sample}{more}")
    if diff["removed"]:
        sample = ", ".join(
            f"{r['ticker']} (was #{r['prev_rank']})" for r in diff["removed"][:8]
        )
        more = "" if len(diff["removed"]) <= 8 else f" … +{len(diff['removed']) - 8} more"
        print(f"   ➖ {sample}{more}")
    if diff["movers_up"]:
        sample = ", ".join(
            f"{r['ticker']} #{r['prev_rank']}→#{r['rank']}"
            for r in diff["movers_up"][:5]
        )
        print(f"   ↑  {sample}")
    if diff["movers_down"]:
        sample = ", ".join(
            f"{r['ticker']} #{r['prev_rank']}→#{r['rank']}"
            for r in diff["movers_down"][:5]
        )
        print(f"   ↓  {sample}")


def main() -> int:
    if not DB_PATH.exists():
        print(f"❌ DuckDB not found at {DB_PATH}", file=sys.stderr)
        return 1

    mcaps = load_market_caps()

    conn = duckdb.connect(str(DB_PATH), read_only=True)
    # Filter to tickers that ACTUALLY file earnings — exclude preferred
    # stock vehicles (STRC/STRK/STRF for MSTR's BTC raises), delisted
    # remnants, and other zombie symbols that have high residual flow but
    # no real corporate reporting. "Active reporter" = at least one
    # earnings event in past 365 days OR future. The OR-of-sides catches:
    #   - Established quarterly reporters (past entries) ✓
    #   - Fresh IPOs whose first earnings is scheduled but not yet filed
    #     (e.g. SpaceX IPO — popular by trading flow + market cap but no
    #     past 10-Q yet). Future entry alone is enough. ✓
    #   - Recently delisted: past entries age out within 365d → naturally
    #     filtered as time passes.
    # Both-sides-empty (no past in window, no future projection) = zombie.
    dv_rows = conn.execute(
        f"""
        WITH dv AS (
            SELECT act_symbol, SUM(volume * close) AS dv
            FROM v_ohlcv
            WHERE date >= CURRENT_DATE - INTERVAL '{WINDOW_DAYS}' DAY
              AND volume IS NOT NULL AND close IS NOT NULL
            GROUP BY act_symbol
        ),
        active_reporters AS (
            SELECT DISTINCT act_symbol
            FROM v_earnings
            WHERE date >= CURRENT_DATE - INTERVAL '365' DAY
               OR date > CURRENT_DATE
        )
        SELECT d.act_symbol, d.dv
        FROM dv d JOIN active_reporters e USING (act_symbol)
        ORDER BY d.dv DESC
        """
    ).fetchall()

    if not dv_rows:
        print("❌ No dollar-volume rows returned", file=sys.stderr)
        return 1

    # Rank by dollar volume (already sorted DESC above).
    dv_rank = {sym: i + 1 for i, (sym, _dv) in enumerate(dv_rows)}
    n_universe = len(dv_rows)

    # Rank by market cap among tickers we have data for. Tickers without
    # mcap data get a sentinel rank past the end → mcap_pct = 0.
    mcap_subset = sorted(
        ((sym, mcaps[sym]) for sym in dv_rank if sym in mcaps and mcaps[sym] > 0),
        key=lambda x: -x[1],
    )
    mcap_rank = {sym: i + 1 for i, (sym, _) in enumerate(mcap_subset)}

    scored: list[tuple[str, float, int, int, float]] = []
    for sym, dv in dv_rows:
        dv_pct = rank_pct(dv_rank[sym], n_universe)
        if sym in mcap_rank:
            mc_pct = rank_pct(mcap_rank[sym], len(mcap_subset))
            score = W_DV * dv_pct + W_MCAP * mc_pct
        else:
            mc_pct = 0.0
            score = W_DV * dv_pct
        scored.append((sym, score, dv_rank[sym], mcap_rank.get(sym, 0), mcaps.get(sym, 0.0)))

    scored.sort(key=lambda x: x[1], reverse=True)
    top = scored[:TOP_N]
    total = len(top)

    entries: list[str] = []
    for rank, (sym, *_rest) in enumerate(top, 1):
        w = weight_for_rank(rank, total)
        entries.append(f'  "{sym}": {w},')

    now_iso = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    mcap_coverage = sum(1 for _, _, _, mr, _ in top if mr > 0)
    content = (
        "// AUTO-GENERATED by tools/build_popular_weights.py — DO NOT EDIT BY HAND.\n"
        "// Composite popularity score (refreshed nightly):\n"
        f"//   {W_DV} × dollar-volume rank percentile (90d, from v_ohlcv)\n"
        f"// + {W_MCAP} × market-cap rank percentile (Finnhub /stock/profile2)\n"
        "// Rank-based normalization caps outlier influence — foreign mega-cap\n"
        "// ADRs (TSM, HDB) can't dominate via log(mcap), high-flow penny semis\n"
        "// (AXTI, CLS) can't ride a small-base log(dv) gain into the top.\n"
        "// Filtered to v_earnings tickers; top 200 mapped to weights 76..100.\n"
        f"// Last generated: {now_iso}; market-cap coverage: {mcap_coverage}/{total}.\n"
        "\n"
        "export const POPULAR_WEIGHT: Record<string, number> = {\n"
        + "\n".join(entries)
        + "\n};\n"
    )
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(content)
    print(f"✅ Wrote {total} popular tickers → {OUT_PATH.relative_to(REPO_ROOT)}")
    print(f"   Market-cap coverage: {mcap_coverage}/{total} ({mcap_coverage*100/total:.0f}%)")
    print(f"   Top 10: {', '.join(s for s, *_ in top[:10])}")
    print(f"   Last 5: {', '.join(s for s, *_ in top[-5:])}")

    # Diff vs the previous snapshot, then atomically replace the snapshot.
    # On the very first run we just establish the baseline — no diff entry
    # is written so the log doesn't start with a misleading "everything
    # added" blast.
    today_ranks = {sym: rank for rank, (sym, *_) in enumerate(top, 1)}
    prev_snapshot = load_snapshot()
    prev_as_of = prev_snapshot.get("as_of") if prev_snapshot else None
    diff = compute_diff(today_ranks, prev_snapshot)
    print_diff_summary(diff, prev_as_of)
    if prev_snapshot is not None:
        append_changes(
            diff,
            as_of=now_iso,
            prev_as_of=prev_as_of,
            universe_size=n_universe,
        )
    write_snapshot(today_ranks, as_of=now_iso)

    return 0


if __name__ == "__main__":
    sys.exit(main())
