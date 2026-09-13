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

The intelligence workflow applies the same recovery rules to its canonical
stages. A source fetch is keyed by its source identity and request fingerprint;
ranking, strategy proposal, package selection, claim checks, and brief assembly
reuse their natural identities after a retry or worker restart. Read-only source
collection can be resumed safely, but it never grants permission for a write
effect or live publication.

The creative workflow applies the same rules to the provider boundary. It
persists the creative request and `provider_jobs` reconciliation state before
remote submission; a restart asks the provider for the same external job rather
than generating another asset. Imported asset bytes are content-hashed and
deduplicated under the content program. The full Phase 7 recovery fixture
interrupts after provider acceptance, resumes on a replacement worker, and
requires one provider submission plus complete script/asset/distribution/ready
package lineage. No recovery path converts a ready package into live publishing.
