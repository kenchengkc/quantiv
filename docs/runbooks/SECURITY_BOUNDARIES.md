# Security boundaries

Production administrative and recovery paths should be explicit, narrowly scoped, and independently auditable.

- Normal scheduled refreshes use read-mostly repository permissions and only acquire write permission in the job that publishes a reviewed/generated source update.
- Model activation and rollback require exact bundle identity verification.
- HMAC protects the frontend-to-backend inference boundary; administrative API-key authorization is separate.
- CI must pin third-party actions and downloaded executables by immutable version and verify checksums where practical.
