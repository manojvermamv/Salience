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
deduplicated under the content program. Every non-dry variant records a planned
effect and explicit budget reservation before submission, then settles known
actual cost once before it can cross the final gate. Verified provider webhooks
are duplicate-safe by provider delivery identity and converge the same provider
job lifecycle as polling. The full Phase 7 recovery fixture interrupts after
provider acceptance, resumes on a replacement worker, and requires one provider
submission, one reservation, one actual-cost settlement, a duplicate-safe
webhook receipt, and complete script/asset/distribution/ready-package lineage.
Ready-package lineage cannot be directly rewritten after approval; an altered
governed decision needs a new version. No recovery path converts a ready package
into live publishing.

The Phase 9 publication fixture applies the same interruption rule after a
publisher accepts the request. It persists a request/plan/attempt/effect before
the remote boundary, crashes after acceptance, and resumes by reconciling the
same provider idempotency key. It proves one remote receipt, bounded polling,
duplicate-safe signed webhook convergence, reservation/actual settlement,
policy/approval denial before submit, cancellation signalling, and canonical
audit/provenance/trace records. Ambiguous outcomes become reconciliation work;
the workflow never blindly repeats a publisher create.

The opt-in YouTube adapter currently starts only a private resumable session.
It keeps the session URI in transient edge memory and persists no video transfer
or public post, so it does not weaken the fixture recovery guarantee.
