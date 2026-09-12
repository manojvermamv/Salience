# Durable workflows

Salience owns the `DummyWorkflowRequest` contract and uses Temporal only as its
durable execution engine. The workflow records a canonical PostgreSQL
checkpoint before and after an external boundary, applies a three-attempt
bounded retry policy, and records terminal timeout, cancellation, denial, and
dead-letter states in PostgreSQL.

`IntelligenceLoopWorkflow` is the implemented Phase 5–6 durable path. Under a
single canonical job and trace, it performs idempotent source collection,
normalization, signal ranking, Research/Strategy agent runs, strategy proposal,
strategic package generation and evaluation, claim/evidence checks, and
immutable `ContentBrief@v1` assembly. A recovered worker resumes from the last
canonical checkpoint and reuses the same fetch, proposal, and brief identities.

`TemporalScheduleService` maps canonical schedule IDs and positive intervals to
Temporal schedules. The derived Temporal execution ID is a mapping detail; the
canonical job/workspace/program identities remain PostgreSQL UUIDs.

The deployed worker resolves a workflow ID back to its canonical job before an
activity runs. It uses the HTTP mock provider only as a test fixture and calls
it through `ExternalEffectService`; future providers must implement the same
idempotency and reconciliation contract.

Read-only source connectors are not external-effect providers. They retain
untrusted source and fetch provenance, honor allowlists and byte/timeout bounds,
and never turn source content into authority. Phase 7 starts only from the
selected immutable brief; scripting, media, publishing, analytics, and learning
are not activities in the current workflow.
