# Quantile calibration

Quantile forecasts should be evaluated as quantiles, not only as point forecasts.

`scripts/research/quantile_calibration.py` reads a flat CSV and reports:

- empirical coverage for each forecast quantile;
- calibration error versus the nominal quantile;
- pinball loss;
- P10–P90 and P25–P75 interval coverage and mean width when those columns are present;
- the fraction of rows with crossed quantiles.

```bash
python scripts/research/quantile_calibration.py artifacts/forecast_holdout.csv \
  --target realized_abs_move \
  --quantile p10=0.10 \
  --quantile p25=0.25 \
  --quantile p50=0.50 \
  --quantile p75=0.75 \
  --quantile p90=0.90 \
  --out artifacts/quantile-calibration.json
```

The script has no training or serving side effects. It is intended for holdout and walk-forward exports so calibration can be reviewed independently of the model training code.
