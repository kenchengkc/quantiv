# Secret Rotation

Use this runbook for planned credential rotation and suspected credential exposure. For a suspected vulnerability or compromise, also follow `SECURITY.md` and [`INCIDENT_RESPONSE.md`](INCIDENT_RESPONSE.md).

## Principles

- Revoke/rotate first when continued credential use could expand exposure.
- Deleting a secret from Git/logs is not a substitute for rotation.
- Rotate the narrowest credential set that safely closes the exposure, but account for credentials derived from or reachable through the compromised one.
- Never print replacement secrets into workflow logs, issue comments, screenshots, or runbook evidence.
- Validate the new credential before deleting the last known-good credential only when overlap does not prolong an active compromise.

## Inventory

The current environment-variable inventory and ownership notes live in `.env.example` and deployment docs. Typical production credentials include:

- Cloudflare R2 account/access credentials;
- Neon `DATABASE_URL`;
- Upstash Redis credentials;
- Clerk publishable/secret keys as applicable;
- `ADMIN_API_KEY` and HMAC/service authentication material;
- model-bundle signing key material;
- Finnhub/FMP/TwelveData/other provider API keys;
- deployment/service tokens maintained outside the repository.

Treat model-signing material as higher impact than an ordinary provider read key because it participates in artifact authenticity.

## Planned rotation

1. Identify every consumer: GitHub Actions environment/secrets, Railway, Vercel, Cloudflare worker configuration, local operator configuration, and any external scheduled service.
2. Create the replacement credential with least-privilege scope.
3. Update consumers through the provider/platform secret store; do not commit the value.
4. Run the smallest safe verification for each consumer.
5. Verify production smoke/control paths.
6. Revoke the old credential.
7. Record rotation date, credential identifier/scope (never value), systems updated, and verification result.

## Suspected exposure

1. Revoke/disable the exposed credential immediately when safe.
2. Record the exposure window and where the secret may have appeared.
3. Review provider/platform audit logs for unexpected access during that window.
4. Rotate dependent credentials if the exposed credential could read them, alter deployment configuration, replace artifacts, or mint additional credentials.
5. Remove the exposed value from active files/log surfaces where practical after revocation.
6. Deploy/verify replacement configuration.
7. Preserve evidence needed to determine impact.

## R2 credential rotation

After rotating R2 credentials:

- verify read access to existing immutable releases;
- verify write/publish access only in workflows that require it;
- ensure frontend/build readers do not receive write credentials;
- run publication readback verification before considering the writer credential healthy;
- do not create a new artifact merely to prove a read-only credential works when an existing immutable manifest/archive can be verified.

## Model signing key rotation

Signing-key rotation changes the artifact trust root and therefore requires an explicit migration plan.

Do not simply replace `MODEL_BUNDLE_SIGNING_KEY` if existing production bundles/pointers cannot be verified under the new trust contract.

A safe rotation must define:

- whether the verification scheme supports key identifiers/multiple trusted keys;
- which existing bundles remain trusted during the transition;
- how a new champion/control pointer is signed/promoted;
- when the old key is removed from verification;
- how rollback to an older bundle works after cutover.

If exposure is suspected, freeze model promotion, restore/retain the last independently verified serving state when possible, and treat artifacts signed during the exposure window as suspect until provenance is established.

## Database/Redis/auth provider rotation

For Neon, Upstash, Clerk, or similar managed services:

- rotate through the provider's supported mechanism;
- update server-side consumers first where overlap is supported;
- verify authentication/read/write paths relevant to Quantiv;
- revoke the old credential;
- check logs for continued use of the retired credential, which indicates a missed consumer.

## Provider API-key rotation

A data-provider key generally should not change data semantics. After rotation, verify request authorization and the normal ingestion/reconciliation path. Do not waive reconciliation because the new key successfully returns HTTP 200.

## Verification

Rotation is complete only when:

- all intended consumers use the replacement credential;
- retired credentials are revoked/inactive;
- normal CI/workflows/runtime health pass;
- no fallback consumer still depends on the retired credential;
- production artifact/model identities remain unchanged unless the rotation intentionally required a trust-root release;
- incident evidence contains identifiers/timestamps but no secret values.
