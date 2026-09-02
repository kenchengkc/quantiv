# Look-ahead audit

Point-in-time research requires every feature to be available no later than the decision timestamp used for that observation.

`scripts/research/lookahead_audit.py` checks a flat CSV containing one row per feature observation. It fails when a feature timestamp is after its decision timestamp or when either timestamp is missing.

```bash
python scripts/research/lookahead_audit.py artifacts/feature_timestamps.csv \
  --decision-column decision_at \
  --available-column available_at \
  --feature-column feature \
  --id-column event_id \
  --out artifacts/lookahead-audit.json
```

The report includes the number of violations, the exact offending rows, the amount of future lead time, and per-feature counts. A zero exit code means every checked row is timestamp-complete and point-in-time safe under the supplied timestamps.
