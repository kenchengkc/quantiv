# Fresh published forecast retention

A refreshed option chain can move a ticker from the validated optionless cohort to
an unadmitted strict-options head. The scorer may retain an earlier **actually
published** optionless forecast for an otherwise absent event while its original
source session remains within the existing one-session grace. It never masks
new option data, changes cohorts, or substitutes another lead model.

The proof chain is the Git-pinned frontend manifest, verified production manifest
and screener bytes, the inventoried `evidence/forecast.json` receipt ID, the full
content-addressed validation receipt, and its sole exact eligible-Parquet digest.
The reader intersects authenticated rows with ML forecasts actually displayed in
production and checks their public identity, session, as-of date, lead, scoring
time, feature hash and six-decimal output projection. Forecast IDs alone do not
authenticate outputs. Signed or validated rows that were never displayed cannot
be retained. Missing, stale-pipeline or altered proof admits no retained rows.

For a recovery whose latest deployment already omitted valid forecasts, an
explicit `retention_smoke_run_id` may identify one earlier production publication.
The recovery authenticates the successful smoke run, exact job and verification
step through GitHub, then checks the actual checkout revision and verified release
in its job log. That revision must be an ancestor with the same production
verifier and workflow, and its Git-pinned manifest must match the restored archive.
The historical proof lives separately from current production readback and is
cleared on the next capture; every recovery must authenticate it again. It never
rolls back the frontend or admits unconfirmed calendar sessions. The same full
receipt, displayed-row projection, freshness, source and monitoring checks apply.

Eligible bytes are preserved at `forecasts/published_snapshots/<sha256>.parquet`.
R2 uploads use immutable copy, and ordinary forecast sync excludes this prefix
so another partial checkout cannot delete it. Reads copy only the last 14 days,
which exceeds the admitted two-session source window. The existing immutable
receipt files remain under `forecasts/receipts/`; immutable `*.receipt.json` files
receive the same copy and deletion protection. No feature vectors are exposed
in new public files. For migration, an old eligible file can be seeded only after
its complete digest matches the receipt authenticated by the deployed frontend.

Retention preserves every original forecast value and timestamp. It requires the
same authenticated champion and admitted head, unchanged canonical date **and
known session**, the original prediction deadline, current corporate-action
coverage and the original session's positive OHLCV close. All originally finite
model inputs must match freshly reconstructed inputs for that same event and
original snapshot, including volatility-history features. Later-session data is
not compared against the old snapshot. Original missing quote inputs stay null;
new quote availability only affects newly scored rows. A same-snapshot correction
or previously unsupported source change fails this conservative check.

Only optionless rows without strict quote evidence use this retention path;
strict-options rows continue through ordinary two-session rescoring. Existing
current candidates win. Retained rows enter the normal candidate file **before**
live validation, rolling monitoring, signed publication selection, and activation
checks. Retention never supplies an allowlist or overrides a hold or rollback.
