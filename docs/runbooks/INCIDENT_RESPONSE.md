# Production Incident Response

Use this runbook when Quantiv is serving incorrect/stale state, a production dependency is failing, a release/control boundary may have been bypassed, or the impact is not yet understood.

The first goal is **containment without destroying evidence**. Do not overwrite an immutable release, model bundle, receipt, or retained baseline merely to make the current state look healthy.

## 1. Establish the production identity

Record, before changing anything:

- UTC timestamp and operator;
- current Git revision/deployment shown by Vercel/Railway;
- current frontend release ID and materialization attestation, if available;
- current R2 runtime/frontend pointer identities;
- current champion model bundle ID;
- most recent successful data-refresh and model-retrain workflow runs;
- current `/health` and `/ml-status` / control-plane status;
- provider/dependency status relevant to the incident;
- screenshots/log excerpts only after redacting credentials and user data.

If two surfaces disagree about a release/model identity, treat that as a control-plane incident rather than choosing whichever value appears newest.

## 2. Classify the failure

### Availability

The service is unavailable or materially degraded but the last known-good data/model identity remains internally consistent.

Examples: Railway crash loop, Vercel outage, provider timeout, Redis/Neon connectivity failure.

### Freshness

The service responds but one or more research inputs/outputs exceed their documented freshness budget.

Examples: stale quotes, old options snapshot, earnings calendar not refreshed, forecast publication did not advance.

### Integrity / provenance

A displayed or promoted artifact cannot be tied to the expected immutable release, source evidence, digest, signature, or temporal contract.

Examples: manifest mismatch, model signature failure, pointer references unexpected release, future-information audit failure.

**Integrity/provenance failures take precedence over availability. Do not restore availability by weakening verification.**

### Security

Authentication/authorization, secret exposure, arbitrary path/object access, workflow tampering, or other malicious activity may be involved. Follow `SECURITY.md`; rotate affected credentials before attempting normal recovery when continued use could expand exposure.

## 3. Contain

Choose the smallest action that prevents bad state from advancing:

- stop/reject the candidate publication rather than deleting the previous validated release;
- disable or pause a failing scheduled workflow only if repeated execution could corrupt state or exhaust a provider budget;
- preserve the last validated R2 pointer/model champion;
- keep degraded/held status visible rather than fabricating freshness;
- revoke leaked credentials immediately;
- if necessary, roll back the application deployment while retaining current evidence for diagnosis.

Do **not**:

- force-push `main`;
- rewrite immutable R2 release keys;
- edit signed bundle contents in place;
- promote an artifact because it is the newest;
- bypass a freshness/reconciliation/signature gate to clear an alert;
- delete failed workflow artifacts/logs needed for diagnosis.

## 4. Recover through the owning runbook

- Data refresh / options / Neon: [`DATA_PIPELINE_RECOVERY.md`](DATA_PIPELINE_RECOVERY.md)
- Model identity / serving rollback: [`MODEL_ROLLBACK.md`](MODEL_ROLLBACK.md)
- Frontend publication / R2: [`FRONTEND_R2_RECOVERY.md`](FRONTEND_R2_RECOVERY.md)
- Credential compromise or planned rotation: [`SECRET_ROTATION.md`](SECRET_ROTATION.md)
- Artifact materialization internals: [`ARTIFACT_MATERIALIZATION.md`](ARTIFACT_MATERIALIZATION.md)

For a provider outage, prefer retaining the last validated state with an explicit stale/degraded marker when the product contract allows it. Do not silently substitute a semantically different provider or field without reconciliation and documented provenance.

## 5. Verify recovery

A recovery is complete only when:

1. the externally served application is healthy;
2. current release/model identifiers match the expected control objects;
3. freshness/integrity checks pass or the system is explicitly degraded/held by design;
4. the relevant production smoke workflow passes;
5. no temporary bypass, credential, debug route, or elevated permission remains;
6. any scheduled workflow paused during containment is intentionally re-enabled.

A green HTTP health check alone is not sufficient for a quantitative-data incident.

## 6. Preserve evidence and close out

Record:

- impact window;
- customer/research surfaces affected;
- root cause and contributing factors;
- exact release/model/data identities involved;
- containment and recovery actions;
- checks proving recovery;
- any credentials rotated;
- follow-up issue(s) with owner and acceptance criteria.

If the failure escaped an existing gate, add a regression test/control receipt so the same class of failure is rejected automatically in the future.
