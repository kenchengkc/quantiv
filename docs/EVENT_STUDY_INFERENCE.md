# Event-study inference

A chart of historical earnings moves is descriptive. A research-grade event study should also state how uncertain the estimated edge is and whether the observed effect is distinguishable from a zero-effect null.

`scripts/research/event_study_inference.py` adds two deliberately simple, auditable tools:

- a **circular moving-block bootstrap** for the mean realized-minus-priced move, preserving short-range ordering dependence instead of treating every event as iid;
- a **two-sided sign-flip randomization test** for a zero-mean symmetric null.

The report also includes sample size, mean and median excess move, realized/priced ratio, and the fraction of events whose realized move exceeded the option-implied threshold.

```bash
python scripts/research/event_study_inference.py artifacts/events.csv \
  --realized-column realized_abs_move \
  --priced-column straddle_pct \
  --block-size 4 \
  --out artifacts/event-study-inference.json
```

The defaults are intended for compact earnings-event panels, not high-frequency returns. Results should be reported alongside the exact sample definition and point-in-time data manifest. A small p-value does not turn an event study into a tradable strategy; spreads, liquidity, post-event volatility changes, and selection effects remain outside this statistic.
