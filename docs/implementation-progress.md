# Implementation Progress

## Active Context

- Branch: `main`
- Plan: `docs/superpowers/plans/2026-09-12-phases-1-4-foundation.md`
- Active task: verify, commit, and push the Archify-backed master documentation.
- Current TDD step: validate the frozen architecture artifact and documentation without invoking a storage-heavy Docker rebuild.
- Last verified state: `scripts/verify-phases-1-4.sh` passed in an isolated Compose project (five e2e tests, including real worker hard-exit/restart reconciliation). The full local suite additionally exposed and fixed automatic test endpoint provisioning plus neutral-URL migration handling; the focused migration suite passes. A subsequent full rerun exhausted host disk space in Temporal's test volume after 38 tests, so no further Docker rebuild is attempted until storage is available.

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
- 2026-09-12: Completed Phase 1 control-plane coverage. The FastAPI adapter enforces token and scope checks, creates canonical workspace/program identities, starts idempotent dry-run jobs through Temporal, exposes inspection records, and supplies an HTTP-only CLI. The live Phase 1 verifier passes.
- 2026-09-12: Added Phase 2 canonical callable-agent entities: immutable provider-neutral manifests, version registry with disabled-history preservation, and PostgreSQL agent/team/run/delegation/event records.
- 2026-09-12: Added deterministic native agent execution for the lead, research, and strategy fixture manifests. Direct/delegated equivalence, schema rejection, and team composition are verified without an AI/model provider.
- 2026-09-12: Completed Phase 2 public callable-agent surfaces. The control API, SDK, and CLI share the same agent schemas; the Phase 2 verifier confirms direct, delegated, and team fixture calls remain equivalent.
- 2026-09-12: Added Phase 3 model gateway contracts with deterministic static fixtures and an optional schema-validating OpenAI-compatible HTTP adapter. Agent runtime selection remains outside agent manifests.
- 2026-09-12: Researched the current official MCP specification and SDK. Implemented an SDK-free `2025-11-25` fixture gateway so the project owns its compatibility boundary while the breaking v2 SDK remains deferred.
- 2026-09-12: Researched official A2A v0.3.0 and its Apache-2.0 Python SDK. Added an SDK-free version-gated descriptor/task/artifact fixture and completed the Phase 3 verifier suite.
- 2026-09-12: Added scoped PostgreSQL memory plus versioned research-evidence and strategy persistence. Cross-program scope isolation is verified and no vector database is used.
- 2026-09-12: Completed fixture-backed niche bootstrap. A niche creates canonical workspace/program identities, direct research/strategy agent runs, evidence, scoped memory, and immutable explainable strategy records without browser or model-provider access.
- 2026-09-12: Completed the clean Phases 1–4 verification script and operational runbooks. Its isolated Compose run applied every migration and passed Phase 1 recovery/control, Phase 2 agents, Phase 3 protocols, and Phase 4 bootstrap checks.
- 2026-09-12: Merged the verified `phases-1-4-foundation` branch into local `main`, preserving the user-owned README product line for the requested master README refresh.
- 2026-09-12: Installed the project-local Archify 2.17 skill, verified its Node 24.19.0 runtime with `doctor`, authored a repository-grounded Phase 1–4 architecture specification, and delivered a standalone interactive HTML artifact. The final Archify showcase receipt reports 9/9 checks, zero composition errors, and zero warnings. Browser evidence is skipped because Chrome/Chromium is unavailable in this environment; the deterministic delivery artifact remains valid.
- 2026-09-12: Replaced the minimal README with an end-user master guide covering the shipped scope, safe in-container demo, public API, validation, architecture, extension points, and deliberate exclusions. The README links the checked Archify viewer and retains the product line.
- 2026-09-12: Re-ran `git diff --check`, verified all README repository references, and re-checked the frozen Archify HTML. The artifact remains a 9/9 showcase pass with zero errors and warnings; no Docker workload was started because the host still has insufficient free space for a safe Temporal test rerun.
- 2026-09-12: The `main` checkout intentionally has no duplicate virtual environment. Reused the retained Python 3.13 test environment from the verified worktree against the already-running local PostgreSQL/Temporal services; `tests/integration/test_migrations.py` passed 2/2 in 1.65 seconds. This adds fresh migration evidence without creating a new Temporal volume.

## Resume Instructions

1. Inspect the final documentation and generated Archify artifacts.
2. Run lightweight documentation/Archify verification; do not retry storage-heavy Compose tests while host disk space is constrained.
3. Commit the merged Phases 1–4 foundation and documentation, then push `main` without force.
