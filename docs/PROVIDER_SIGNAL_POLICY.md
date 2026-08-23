# Provider signal promotion policy

Supplemental vendor signals are research candidates, not production inputs by
default. The fail-closed policy in
[`config/provider_signal_policy.json`](../config/provider_signal_policy.json)
controls three separate capabilities for every signal family:

- scheduled collection;
- publication into ticker/screener JSON;
- inclusion in an ML feature schema.

All three are currently frozen. The nightly workflow no longer probes or
refreshes these datasets, and the former afternoon Alpha Vantage schedule is a
manual workflow that uploads an expiring research artifact instead of
committing data to `main`.

## Promotion evidence

Enabling any capability requires a committed `quantiv.provider-paired-test.v1`
report whose SHA-256 digest is pinned in the policy. The policy validator
requires:

- identical paired event keys and a split-audit receipt;
- at least 250 events and three purged walk-forward folds;
- a measurable MAE improvement over the control;
- a result that preserves or improves performance relative to the straddle;
- no material cohort regression; and
- no incremental monthly infrastructure or provider cost.

The evidence file and policy change are reviewed together. Changing a report
without updating its pinned digest fails closed.

## Research collection

Use the `Manual provider-signal research` workflow or run:

```bash
python scripts/sync_provider_enrichments.py --research-override ...
```

The override is for isolated research only. Scheduled production jobs must
never pass it. Production collection without the override filters every
endpoint through the policy and leaves existing research artifacts unchanged
when nothing is approved.

The ML training gate separately rejects any feature whose configured prefix
belongs to a signal family without approved paired evidence. This prevents a
future feature-engineering edit from silently bypassing the workflow freeze.
