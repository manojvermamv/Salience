# Recovery and external effects

Every external effect has a workspace-local idempotency key. The canonical
effect record is planned before remote invocation and completed only after a
provider receipt has been recorded. A retry therefore asks the provider to
reconcile the same key instead of creating a second effect.

The Compose recovery verifier starts an independent mock provider and worker,
hard-exits the worker immediately after remote acceptance, restarts that real
worker container, and requires a successful reconciliation. It retains audit,
provenance, checkpoint, trace, and effect records in PostgreSQL for inspection.

Retry exhaustion and timeouts create canonical dead-letter records. Cooperative
cancellation, policy/approval denial, budget denial, and dry-run execution are
terminal states that do not make a remote provider write. Dry-run is the safe
default for new program flows.
