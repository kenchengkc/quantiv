## What changed

<!-- Describe the problem and the smallest coherent solution. -->

## Invariants / control boundaries

<!-- List data, research, security, publication, model, or serving invariants that must remain true. -->

- [ ] No production control boundary was weakened or bypassed.
- [ ] New external identifiers/paths/inputs are validated before use.
- [ ] Workflow permissions remain least-privilege.

## Reproducibility and quantitative integrity

- [ ] Not applicable, or data/model outputs are reproducible from pinned inputs and dependencies.
- [ ] Not applicable, or temporal/as-of availability is preserved and tested.
- [ ] Not applicable, or model-selection/multiple-testing implications are documented.
- [ ] Not applicable, or immutable artifact/receipt identities are preserved through promotion.

## Generated data / storage migration

- [ ] This PR does not commit incidental generated production data.
- [ ] Not applicable, or migration/fallback behavior has an explicit removal condition.
- [ ] Not applicable, or immutable objects are verified before mutable pointers are promoted.

## Validation

<!-- Paste commands/results or link CI artifacts. -->

- [ ] Relevant lint/type-check/tests pass.
- [ ] Production build/container checks pass when affected.
- [ ] Failure/rollback behavior is tested when changing a control plane.

## Operations / rollback

<!-- How would an operator detect failure, recover, or roll this back? -->

- [ ] No operator/runbook change required, or the corresponding docs are updated.

## Security / secrets

- [ ] No credentials, tokens, private keys, cookies, production database URLs, or sensitive datasets are included in the diff/log examples.
- [ ] `SECURITY.md` considerations were reviewed for security-sensitive changes.
