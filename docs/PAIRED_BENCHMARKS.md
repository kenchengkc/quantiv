# Paired benchmark comparison

Model and baseline forecasts should be compared on the same observations.

`scripts/research/paired_benchmark.py` reports model and baseline MAE/RMSE, the mean paired difference in absolute error, a bootstrap 95% confidence interval for that difference, and the model win rate. A negative error difference means the model has lower absolute error.

```bash
python scripts/research/paired_benchmark.py artifacts/holdout.csv \
  --actual realized_abs_move \
  --model model_forecast \
  --baseline straddle_pct \
  --group-column sector \
  --min-group-size 20 \
  --out artifacts/paired-benchmark.json
```

Grouping is optional. It is useful for checking whether an aggregate improvement is consistent across standard cohorts such as sector or forecast horizon. Small groups are skipped rather than reported with unstable estimates.
