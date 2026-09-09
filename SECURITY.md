# Security Policy

Quantiv handles market-data ingestion, model artifacts, deployment pointers, and authenticated service-to-service operations. Security reports should therefore preserve confidentiality and enough evidence to reproduce the issue without exposing production credentials or user data.

## Reporting a vulnerability

Do **not** open a public GitHub issue for a suspected vulnerability, leaked credential, authentication bypass, data-integrity weakness, or exploit chain.

Use GitHub's private vulnerability reporting / Security Advisory flow for this repository when available. If private reporting is unavailable, contact the repository owner privately through the contact method listed on the owner's GitHub profile and include only the minimum information needed to establish contact. Do not paste live credentials, private keys, tokens, cookies, database URLs, or full production datasets into an initial report.

A useful report includes:

- the affected component and commit/release identifier;
- prerequisites and a minimal reproduction;
- expected versus observed behavior;
- security or data-integrity impact;
- whether exploitation requires authentication or privileged configuration;
- suggested remediation, if known.

## Scope

High-priority reports include:

- authentication/authorization bypasses;
- HMAC or admin-endpoint weaknesses;
- secret disclosure or unsafe logging;
- arbitrary object/path access through R2 publication controls;
- signature, digest, manifest, or deployment-pointer bypasses;
- model/artifact substitution or rollback-control bypasses;
- SQL injection or unsafe database access;
- cross-site scripting, request forgery, or session compromise;
- workflow/supply-chain paths that permit unreviewed production code or artifact replacement;
- look-ahead/provenance failures that allow future or unverified data to masquerade as valid production research evidence.

Normal model error, provider outages, stale third-party data that is correctly surfaced as stale/degraded, and documented research limitations are not security vulnerabilities unless they can be used to bypass a control boundary.

## Credential exposure response

If a credential is accidentally committed or disclosed:

1. Revoke or rotate the credential immediately; deleting it from Git history is not sufficient.
2. Identify every environment/service that could use the credential and rotate dependent credentials if compromise could have propagated.
3. Review GitHub Actions, Railway/Vercel/R2/Neon/provider logs as applicable for unexpected use.
4. Remove the secret from active source/history where practical after rotation.
5. Verify replacement credentials have least-privilege scopes.
6. Preserve an incident record describing the exposure window, affected systems, evidence reviewed, and remediation.

## Supported code

Security fixes target the current `main` branch and the currently deployed production release. Historical commits and archived design documents are not maintained as supported releases.

## Disclosure

Please allow reasonable time to reproduce, remediate, test, and deploy a fix before public disclosure. Coordinated disclosure is preferred, especially when a finding affects credentials, production data integrity, or third-party services.
