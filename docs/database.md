# Canonical PostgreSQL Data

PostgreSQL is the system of record. Temporal is a durable execution engine, and Garage
stores immutable artifact bytes; neither replaces a canonical database row.

## Migration

The first migration is a static schema snapshot at
`migrations/versions/0001_canonical_foundation.py`. The implemented history
reaches `0005_strategy_idempotency.py`; `0004_intelligence_loop.py` adds the
source-to-brief lineage tables. Historical migrations do not import application
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

Those fields remain extension hooks. The deterministic Phase 1–6 implementation
enforces its present scope, policy, approval, trust, workflow, protocol-gateway,
and callable-agent boundaries without treating a future tenant, publisher, or
provider deployment as already configured.
