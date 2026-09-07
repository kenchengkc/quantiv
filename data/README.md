# Data workspace

`data/` is Quantiv's local materialization and pipeline workspace. It is not the canonical production datastore and new mutable artifacts must not be added to Git.

Production jobs restore the currently published release from R2, write candidate/provider output locally, run reconciliation and model/data gates, and publish verified immutable artifacts back to R2. The canonical earnings calendar is materialized as `data/earnings_calendar.csv`; the exact prior-release copy is retained as `data/validation/earnings_calendar_baseline.csv` for the regression gate.

## Directory contract

```text
data/
├── earnings_calendar.csv          # R2-materialized production input; not tracked
├── validation/                    # per-run gates, receipts, and baselines
├── parquet/                       # materialized analytical release members
├── forecasts/                     # generated forecast artifacts
├── models/                        # materialized model/control state
├── quarantine/                    # rejected candidate evidence
├── research/                      # isolated research/probe output
└── ...                            # transitional operational caches
```

Rules:

- R2/release manifests are authoritative for mutable production datasets, model bundles, forecasts, and control evidence.
- Git is authoritative for source, schemas, stable configuration, documentation, and small deterministic test fixtures.
- Research artifacts must never enter publication or model admission unless a reviewed production pipeline explicitly consumes them.
- Do not `git add -f` a new file under `data/`. If a generator needs cross-run state, give that state an explicit artifact-store owner and materialization path.
- A small number of historical operational caches remain tracked during migration; treat them as transitional debt rather than precedent for new files.

See [`docs/REPOSITORY_STATE_POLICY.md`](../docs/REPOSITORY_STATE_POLICY.md), [`docs/runbooks/ARTIFACT_MATERIALIZATION.md`](../docs/runbooks/ARTIFACT_MATERIALIZATION.md), and [`scripts/README.md`](../scripts/README.md).
