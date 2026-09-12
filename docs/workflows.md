# Durable workflows

Salience owns the `DummyWorkflowRequest` contract and uses Temporal only as its
durable execution engine. The workflow records a canonical PostgreSQL
checkpoint before and after an external boundary, applies a three-attempt
bounded retry policy, and records terminal timeout, cancellation, denial, and
dead-letter states in PostgreSQL.

`TemporalScheduleService` maps canonical schedule IDs and positive intervals to
Temporal schedules. The derived Temporal execution ID is a mapping detail; the
canonical job/workspace/program identities remain PostgreSQL UUIDs.

The deployed worker resolves a workflow ID back to its canonical job before an
activity runs. It uses the HTTP mock provider only as a test fixture and calls
it through `ExternalEffectService`; future providers must implement the same
idempotency and reconciliation contract.
