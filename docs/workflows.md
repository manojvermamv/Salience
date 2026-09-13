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

`CreativeProductionWorkflow` is the implemented Phase 7–8 durable path. It
accepts only a selected immutable brief, then checkpoints load, script,
verification, creative direction, authorization, provider submit/reconcile,
provider await, asset import/validation, distribution, and final governance.
Every bounded activity has a 30-second start-to-close timeout and a three-attempt
Temporal retry policy with 100ms-to-1s backoff. Cancellation, policy denial,
budget denial, timeout, provider failure, and retry exhaustion remain canonical
terminal states; a recovered worker reuses the canonical provider job and asset
identities rather than submitting duplicate work.

Before submit, the workflow resolves a versioned provider capability manifest and
creates one to three durable variant plans. A non-dry variant needs an explicit
canonical budget, planned external effect, and reservation; known actual cost
settles before asset/distribution finalization. The workflow records provider
lifecycle transitions conditionally, accepts only credential-free verified
webhook projections, and supports cancellation/dead-letter handling without
resubmitting an already accepted request. Its final distribution and ready
package are immutable versioned decisions: exact replays converge, while changed
governed inputs create a fresh version.

Read-only source connectors are not external-effect providers. They retain
untrusted source and fetch provenance, honor allowlists and byte/timeout bounds,
and never turn source content into authority. Creative work starts only from the
selected immutable brief. The ready package is an immutable Phase-9 handoff,
not a workflow activity that publishes; analytics, experiments, and learning
remain outside the current workflow.
