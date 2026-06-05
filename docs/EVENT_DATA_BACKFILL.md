# Event-data backfill — scope

Goal: break the point-MAE wall (realized↔implied corr ≈ 0.26; transforms of
existing implied/vol features keep testing null) by adding **new, event-specific
signals** to the ML model: analyst-estimate dispersion, put/call & options
volume/OI, and short interest.

## What already exists (don't rebuild)

`scripts/sync_provider_enrichments.py` already collects all three raw signals and
writes them into the per-event `provider_enrichment` block on `screener.json`:

| Signal | Source | Endpoint | Fields already captured |
|---|---|---|---|
| Options flow | Massive | `massive_options_snapshot` | call/put counts, call/put **volume**, call/put **open interest** |
| Short interest | Massive | `massive_short_interest` | shares, avg_daily_volume, **days_to_cover**, settlement_date |
| Analyst estimates | FMP | `fmp_analyst_estimates` | `epsAvg/epsLow/epsHigh`, `revenueAvg/Low/High`, `numAnalystsEps/Revenue` |

Dispersion is therefore **derivable today**: `eps_dispersion = (epsHigh − epsLow)/|epsAvg|`,
`rev_dispersion = (revHigh − revLow)/|revAvg|`. Put/call ratio = `put_vol/call_vol`;
VOI = `total_volume / total_open_interest`.

## The two real gaps

### Gap A — historical depth (the hard part)
The enrichment is a **current snapshot** (`collected_at = today`). The ML trains on
earnings events back to ~2023, so each signal must be aligned to its value *as of
the pre-earnings snapshot* for every historical event. Two paths:

1. **Provider history** (preferred where free):
   - Short interest: bi-monthly FINRA settlement history — Massive likely serves
     past settlements; pull the settlement on/before each earnings date.
   - Analyst estimates: FMP `analyst-estimates` is forward-dated per fiscal period;
     limited true history on free tier — **verify depth** (likely the binding
     constraint).
   - Options flow (put/call, VOI): historical EOD OI/volume is the least likely to
     be free-backfillable. Fallback: derive a put/call & VOI proxy from the
     `options_chain` parquet we already store (2023+) — it has per-strike `vol`
     (IV) but **not** volume/OI, so this proxy is only partial. **Verify** whether
     the chain parquet or Massive has historical contract volume/OI.
2. **Forward-accumulate**: snapshot every upcoming reporter now and grow history
   going forward. Zero backfill risk, but no signal until ~4–8 quarters accrue.
   Use as the fallback for any source whose history isn't free.

Decision rule: for each signal, spend ≤1 day probing free historical depth
(`scripts/probe_provider_capabilities.py`); if <~2yr usable history, forward-
accumulate instead of blocking.

### Gap B — ML feature integration
The enrichment lives in frontend JSON, **not** in DuckDB/`feature_engineering_v3`.
To test these as model features:
1. Land the historical panel as a parquet keyed `(act_symbol, snapshot_date)` →
   new view `v_event_signals`.
2. As-of LEFT JOIN into the snapshots CTE in `feature_engineering_v3.py` (same
   pattern as `v_volhist`), exposing columns:
   - `eps_dispersion`, `rev_dispersion`, `num_analysts_eps`
   - `put_call_vol_ratio`, `put_call_oi_ratio`, `options_voi`
   - `short_days_to_cover`, `short_pct_float` (if float available)
3. Add the names to `feature_cols`; rebuild to a temp dir; **paired-test** with
   `experiment_model_improvements.py` (new `events` round: baseline = drop the new
   cols, variant = keep) on both OOS windows. Ship only if ΔMAE<0 **and** |t|≥2.

## Expected value & order
Run the paired test per signal group so we learn which (if any) actually moves
MAE rather than bundling:
1. **Analyst-estimate dispersion** — strongest prior (genuinely new info about
   outcome uncertainty, orthogonal to implied vol). Data is already collected;
   only history is missing. **Start here.**
2. **Put/call & VOI** — directional/positioning signal; medium prior.
3. **Short interest / days-to-cover** — squeeze risk; weakest prior, but free and
   already flowing.

This is the only remaining lever expected to move *point* accuracy; coverage/band
quality is already addressed by the calibrated ML quantile bands.

## Probe results (executed 2026-06-04)

Built `scripts/backfill_analyst_dispersion.py` (resumable) and ran it. Hard
free-tier walls on FMP:
- **`period=quarter` is premium** → only **annual** dispersion is free. Annual is
  one value per fiscal year (shared by that year's 4 prints) → weak per-event
  signal. Prior: likely null for point MAE.
- **`limit` ≤ 10**, but **pagination works** (page 1 → 2011-2013), so annual
  history depth back to ~2007 is reachable.
- **~250 requests/day**, and the nightly `sync_provider_enrichments` cron already
  spends FMP quota — the backfill 429'd ("Limit Reach") after ~22 symbols. Only
  **one** FMP key configured (no key-pool). So a full S&P-500 annual backfill is
  ~4 days of daily-quota accumulation; the 7.6k-symbol universe is ~2 months.
- AlphaVantage `EARNINGS_ESTIMATES` is the quarterly-granular alternative, but
  free tier is ~25 req/day — even more rate-bound.

**Verdict / recommendation:** the high-signal version (quarterly dispersion) is
paywalled; the free annual proxy is coarse AND multi-day to backfill, so its EV
is low. Two honest paths instead of grinding it:
1. **Forward-accumulate** the already-collected event-specific signals (put/call,
   VOI, short interest are snapshotted per upcoming reporter today) into a dated
   panel — best event-specificity, zero backfill cost, matures over ~4-8 quarters.
2. **Paid data** (FMP quarterly estimates / a contract OI history feed) if point
   accuracy is a priority — the free plan has likely hit its point-MAE ceiling
   (realized↔implied corr ≈ 0.26; every free-feature transform tests null).
The de-bias/calibrated band (shipped) was the realistic free-tier win.
`backfill_analyst_dispersion.py` stays as the resumable tool if we choose to
accumulate annual dispersion for an eventual paired test anyway.

## Implemented: forward-accumulation (2026-06-04)

Chose path (1). `scripts/accumulate_event_signals.py` snapshots, for every
UPCOMING reporter (within `--lead-days`, from `weeks/*.json`), the Massive
options snapshot (put/call vol & OI ratios, VOI, ATM IV) + short interest, and
appends to the append-only `data/event_signals_panel.jsonl`. Targeting upcoming
reporters — not the popular-symbol set the enrichment cron uses — is the key:
Massive has the headroom (58 calls in seconds; the `5/min` budget is self-imposed
elsewhere). Wired into `daily-refresh.yml` (best-effort step) and `git add -f`'d
so the panel persists across the stateless CI and grows daily.

Panel row: `{snapshot_date, act_symbol, earnings_date, timing, lead_days,
put_call_oi_ratio, put_call_vol_ratio, options_voi, total_{call,put}_{oi,vol},
atm_iv_snap, short_days_to_cover, short_interest, short_avg_vol, short_settlement}`.
Note: the nightly run is ~7am ET (pre-market), so `*_vol`/VOI reflect the prior
session (or are sparse) — OI ratios and short interest are the stable signals;
the per-event snapshot closest before the print is the one to use.

**When mature (~4-8 quarters):** write `experiment_event_signals.py` (mirrors
`experiment_garch_feature.py`): for each training event take the last panel
snapshot before its `earnings_date`, as-of join the columns, and paired-test L1
±signals on both OOS windows (ship only if ΔMAE<0 and |t|≥2). Until then the
panel just accumulates; no model change.

### Effectiveness read so far (2026-06-04)

Short interest is the one signal with free *historical* depth (Massive serves
~15 months of FINRA settlements), so it could be tested now without waiting.
`scripts/probe_signal_effectiveness.py` aligned 796 historical events to the
settlement before each print: **days-to-cover is NULL** for both magnitude
(Spearman rho=−0.02, p=0.61) and direction (rho=−0.02, p=0.64), with a flat
quintile table. So short interest is unlikely to help the model — deprioritize
it. put/call & options VOI still have no stored history and remain UNTESTED; they
are the reason the forward panel is worth accumulating, since the one positioning
signal we *could* test (short interest) came up empty.
