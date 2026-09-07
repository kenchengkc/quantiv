# Rollback boundary

Model rollback is a privileged recovery operation and should remain isolated from ordinary refresh/retrain execution.

The rollback path must require an exact expected current bundle, an exact signed target bundle, a reason, read-back verification, and preservation of recovery evidence. Normal refresh/retrain workflows must not expose a bypass that can accidentally enter the rollback path.
