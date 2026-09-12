# Implementation Progress

## Active Context

- Branch: `phases-1-4-foundation`
- Plan: `docs/superpowers/plans/2026-09-12-phases-1-4-foundation.md`
- Active task: 5 — durable jobs, effects, and recovery
- Current TDD step: checkpoint the completed Task 5 verifier, then begin Task 6 control-plane API coverage.
- Last verified state: live Temporal tests pass for in-process restart and terminal states; the isolated Compose verifier hard-exits a real worker after remote acceptance, restarts it, and proves one accepted provider effect plus reconciliation.

## Checkpoints

- 2026-09-12: Created the isolated worktree and installed the local test runner.
- 2026-09-12: Added and verified the failing configuration-security test; implementation has not started.
- 2026-09-12: Completed dependency research and recorded ADRs; SeaweedFS was rejected for an unpatched high advisory and Garage selected as the isolated S3 service.
- 2026-09-12: Implemented the validated settings model, pinned initial dependencies/images, passed the configuration test and Compose validation, and built the Python 3.13 image.
- 2026-09-12: Completed and committed the deployable runtime scaffold, then observed the focused PostgreSQL migration test fail before migration implementation.
- 2026-09-12: Implemented immutable canonical PostgreSQL migration and matching typed models; verified required identity, governance, provenance, scheduling, idempotency, trace, and secret-reference schema invariants against live PostgreSQL.
- 2026-09-12: Completed and committed the canonical database Task 2 checkpoint, then created the four focused red governance contracts for policy, costs, secrets, and tracing.
- 2026-09-12: Implemented deterministic governance controls and repaired an installed OpenTelemetry trace-flag compatibility mismatch; all six focused governance tests pass.
- 2026-09-12: Completed and committed the Task 3 governance checkpoint, then created red conformance tests for memory/S3 object stores and versioned plugin registration.
- 2026-09-12: Implemented and verified owned storage, workflow, and plugin contracts with memory and mocked-S3 adapter coverage; Garage v2.3 command/configuration surface was inspected before wiring a real runtime adapter.
- 2026-09-12: Completed and committed Task 4; started the pinned Temporal runtime and observed the red process-restart verifier fail before any workflow implementation.
- 2026-09-12: Implemented a real Temporal worker restart scenario with independent idempotent mock-effect reconciliation; focused e2e verifier passes against the pinned Compose Temporal service.
- 2026-09-12: Expanded the restart verifier with canonical checkpoint/effect/audit/provenance assertions and observed the expected red database-boundary failure.
- 2026-09-12: Root-caused a native asyncpg/Python 3.13 crash to PostgreSQL I/O inside Temporal activities. Plain repository I/O is stable, but three activity-boundary variants (original connection, explicit non-TLS connection, and pre-opened connection) segfaulted. The unsafe uncommitted prototype was removed. Psycopg 3.3.5 is the selected next experiment: its official metadata supports Python 3.13, but its LGPL-3.0-only license must remain recorded in the dependency decision before adoption.
- 2026-09-12: Adopted Psycopg 3.3.5 for the worker-side repository after its checked live Temporal restart scenario persisted canonical checkpoints, reconciled effect state, audit events, and provenance without the asyncpg crash.
- 2026-09-12: Added and verified durable terminal-state handling: three-attempt retry exhaustion to a canonical dead letter, activity timeout, cooperative cancellation, approval and budget denial before effects, and dry-run execution with no provider call.
- 2026-09-12: Added a deployable worker, independent HTTP mock provider, Temporal schedule adapter, and isolated Compose recovery verifier. The verifier builds a unique stack, migrates it, hard-exits the worker after provider acceptance, restarts it, verifies reconciliation, and removes only that stack.

## Resume Instructions

1. Commit the Task 5 durable-worker/recovery checkpoint, then write the red Task 6 authenticated control API test.
2. Keep external effects idempotent at both local and provider boundaries, with durable checkpointing before every boundary.
3. Update this file and the checked task steps after each verified slice, then commit the checkpoint.
