# Railway runtime security and base-image provenance

## Decision

Quantiv **retains the Railway `/data` persistent volume and therefore runs the backend container as UID/GID `0:0` deliberately**. Railway mounts attached volumes as root-owned storage; switching the image to an arbitrary non-root UID would make signed model-bundle activation unreliable unless the volume architecture is removed first. `USER 0:0` is explicit in the Dockerfile so this exception cannot be mistaken for an accidental default.

This is a platform exception, not a general permission model. The application has no shell/upload endpoint, production ML/admin routes are HMAC/API-key protected, generated frontend data is not copied into the backend image, Python bytecode writes are disabled, and `/app` is marked read-only at image build time. Application-owned persistent writes are restricted by convention to `/data`; ephemeral scratch belongs in `/tmp`. Model downloads are signature/digest verified and atomically activated before the serving path changes.

If Railway later supports a mounted-volume owner compatible with a fixed non-root UID, or if serving models move entirely to ephemeral verified R2 materialization, this exception should be removed and CI should assert a non-zero runtime UID.

## Base-image provenance policy

The production image uses `python:3.11-slim-bookworm`, fixing the Python minor and Debian family rather than the floating `python:3.11-slim` alias. Quantiv intentionally uses an update policy instead of freezing a digest forever:

1. Review the resolved upstream image **at least monthly** and immediately for a critical Python/Debian/OpenSSL/glibc CVE.
2. Rebuild through the normal PR CI lane; the Railway image job must pass the real `/health` smoke test.
3. Keep Python on the supported 3.11 security line until an intentional runtime upgrade is reviewed.
4. Record any Python-minor or Debian-family change in the PR; do not silently change both application code and the runtime family during an incident.
5. If a supply-chain incident requires exact rollback, use the previously built Railway deployment/image, not an unreviewed floating rebuild.

This policy is executable evidence through `apps/backend/tests/test_runtime_policy.py`, which fails if the explicit root exception, Debian family, or policy documentation disappears.
