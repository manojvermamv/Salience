# Canonical PostgreSQL Data

PostgreSQL is the system of record. Temporal is a durable execution engine, and Garage
stores immutable artifact bytes; neither replaces a canonical database row.

## Migration

The first migration is a static schema snapshot at
`migrations/versions/0001_canonical_foundation.py`. The implemented history
reaches `0011_publication_plan_lifecycle.py`; `0004_intelligence_loop.py` adds the
source-to-brief lineage tables, `0006_creative_production_distribution.py` adds
the Phase 7–8 canonical production/distribution model, and `0007` anchors
provider and package-asset reconciliation lineage. `0008` adds creative-job
effect/reservation links, verified webhook receipts, canonical asset-rights
links, provider lifecycle/cost fields, and triggers that reject direct mutation
of approved decision lineage. `0009_governed_publication` adds publisher
accounts, secret-reference-only connections, capability profiles, immutable
ready-package-bound requests, idempotent plans/schedules/attempts, append-only
status and webhook receipts, remote receipts, publication records, and their
trace/cost links. `0010` adds the distinct publication-approval and
schedule-budget references plus database-enforced immutable identity fields;
`0011` permits only their bounded durable cost lifecycle transitions.
Historical migrations do not import application
metadata, so future model changes cannot alter an already-applied migration.

Use an SQLAlchemy async URL for Alembic:

```bash
DATABASE_URL=postgresql+asyncpg://salience:salience@postgres:5432/salience \
  alembic upgrade head
```

When running from the host, use the Compose PostgreSQL container's network address or
run Alembic in an application container. The database has no public host port by
default; this avoids accidentally exposing the control-plane store.

## Identity and Isolation

Every durable table has a UUID primary key, timestamps, and a nullable `tenant_id`
hook. The initial single-deployment mode does not enable row-level security. A future
multi-tenant rollout must introduce tenant-scoped authentication and RLS in a new
migration before accepting untrusted tenant traffic.

`workspaces` and `content_programs` establish the root product identities.
`external_identity_mappings` provides a workspace-scoped unique mapping to remote
systems.

## Creative And Distribution Lineage

The creative schema is canonical, provider-neutral, and versioned. It records
`script_versions`, `creative_briefs`, `storyboards`, `shot_plans`, and
`creative_jobs` under the selected immutable `content_brief_versions` record.
`provider_jobs` retains normalized requests, external-job reconciliation state,
verified callback receipt identity, provider/model metadata, estimated/actual
cost facts, cancellation/failure state, and trace context without credential
values. `creative_job_effects` ties each bounded variant to exactly one planned
external effect and budget reservation.

`assets`, variants, transformations, captions, compositions, licenses, consent,
likeness/voice records, usage restrictions, and `asset_provenance` retain
content-hash, object-storage, rights, and C2PA-reference metadata. Platform
profiles and distribution packages are versioned independently. A package must
retain its selected asset roles, title/thumbnail decision, localization,
originality evaluation, and synthetic-media disclosure before the immutable
`ready_to_publish_packages` record can reference an approved governance state.

Once an approved ready package references a distribution decision graph,
PostgreSQL triggers reject direct updates to its package, disclosure, candidate,
localization, or originality rows. Repository writers either replay identical
data or allocate a new version that retains source lineage. The final package
has no publisher account or remote destination. Phase 9 adds a separate
publishing-effect identity and does not mutate this lineage.

## Governed Publication Lineage

`publisher_accounts`, `publisher_connections`, and
`publisher_capability_profiles` retain account identity, secret reference,
scope/profile facts, protocol compatibility, and source timestamps without a
secret value. `publication_requests` binds an approved ready package, a distinct approved
publication authority, and one active workspace-bound account. `publication_plans`, `publication_schedules`,
`publication_attempts`, `publication_status_events`,
`publisher_webhook_receipts`, `remote_publication_receipts`, and `publications`
preserve the governed decision, external-effect/reconciliation state, cost
reservation, safe callback hash, trace, and final receipt relationships.
Requests, schedules, attempts, callback/remote receipts, status history, and
final publication references are database-immutable. Plans preserve their
identity fields and admit only a one-time effect/reservation link plus bounded
cost-settlement lifecycle transitions. Repository writers replay exact
identities or reject a changed immutable fact.

## Durable Runs

`jobs`, `job_checkpoints`, `job_dead_letters`, and `job_schedules` retain
workflow IDs, queues, retry configuration, timeouts, scheduled work, checkpoints and
failure history. A workspace-local job idempotency key is unique.

`external_effects` is separate from a job and has its own unique workspace-local
idempotency key, request fingerprint, remote reference, and reconciliation state. This
is the durable boundary used to prevent duplicate external writes.

The intelligence loop adds canonical sources, source fetches, evidence, signals,
source support, ranked opportunities, strategic packages/evaluations, claims,
claim/evidence links, immutable brief versions, and model-invocation lineage.
Their natural keys make retrying a stage return the existing record instead of
duplicating a source, strategy proposal, or brief.

## Governance and Traceability

- `audit_events` has a unique `(run_id, sequence_no)` and a trigger that rejects
  non-increasing sequences per run.
- `provenance_records` retains source hashes, lineage, verification state, trace
  fields and C2PA-compatible manifests.
- `secret_references` stores only a reference URI and scopes; it has no secret-value
  column.
- `permission_grants`, `policy_versions`, and `approval_requests` provide the
  canonical policy and approval records.
- `budgets`, `budget_reservations`, and `cost_ledger_entries` separate budget
  limits, estimated/reserved amounts, and actual provider costs.

## Extension Hooks

The schema carries data classification, retention/deletion, domain/jurisdiction policy
references, trace/span IDs, trust/delegated-authority metadata, C2PA provenance, and
protocol compatibility metadata. `plugin_versions` and `plugin_capabilities` hold
versioned capability declarations without embedding a provider SDK or protocol object
in canonical rows.

Those fields remain extension hooks. The deterministic Phase 1–9 implementation
enforces its present scope, policy, approval, trust, workflow, protocol-gateway,
and callable-agent boundaries without treating a future tenant, publisher, or
provider deployment as already configured.
