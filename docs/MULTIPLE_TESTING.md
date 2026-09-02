# Multiple-testing correction

Testing many candidate signals increases the chance of finding an apparently significant result by chance.

`scripts/research/multiple_testing.py` applies either Benjamini–Hochberg false-discovery-rate control or Holm family-wise-error control to a CSV of research results. Both methods are standard, dependency-free here, and preserve the original row order.

```bash
python scripts/research/multiple_testing.py artifacts/signal-tests.csv \
  --p-value-column p_value \
  --method benjamini-hochberg \
  --alpha 0.05 \
  --out artifacts/signal-tests-adjusted.csv
```

The output adds `adjusted_p_value` and `reject_null`. The correction should be applied to the full family of hypotheses chosen before reviewing results, not only to the most promising rows.
