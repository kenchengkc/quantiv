# Data

`data/` contains repository-tracked state and small artifacts that support the data pipeline. Large local databases, Parquet partitions, downloads, and caches remain gitignored and are synchronized through the configured artifact stores instead.

## Directory contract

```text
data/
├── earnings_calendar.csv          # canonical tracked earnings calendar
├── provider_enrichments/          # operational derived provider summaries
├── provider_capabilities.json     # operational entitlement/capability cache
├── provider_usage_ledger.json     # shared production/provider call ledger
├── research/                      # artifacts that cannot affect publication by default
│   ├── event_signals/             # paused/manual forward-accumulation panel
│   ├── provider_probes/           # provider coverage and entitlement evidence
│   └── provider_signals/          # isolated manual provider-signal samples
└── ...                            # other small operational snapshots/metadata
```

Rules:

- Keep production inputs, operational ledgers, and publication metadata at `data/` or in an explicitly owned operational subdirectory.
- Put manual experiments, probes, and evidence collection under `data/research/`.
- Research artifacts must not enter frontend publication or ML admission unless an explicit reviewed pipeline consumes them and the corresponding control policy permits it.
- Do not use `data/` as a general source-code directory.
- Git ignores `/data/` by default because most local artifacts are large. Existing tracked files continue to update normally; adding a brand-new tracked artifact under `data/` may require `git add -f` plus an explicit review of why it belongs in Git.

See [`scripts/README.md`](../scripts/README.md) for production data commands and [`docs/EVENT_DATA_BACKFILL.md`](../docs/EVENT_DATA_BACKFILL.md) for the paused event-signal research history.
