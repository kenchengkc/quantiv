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
