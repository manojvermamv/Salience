# P1 Publication Recovery Hold Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct parallel-team parent status, add one durable non-resumable publication recovery hold, and qualify the first pinned hold-aware rollback floor without allowing older workers to mutate retained liabilities.

**Architecture:** Keep the existing cycle/publication architecture and signed inspection route. The append-only `0042` sidecar and `publication_recovery` module derive hold facts from canonical rows, preserve immutable liability snapshots, and verify both reasons before restart. A candidate-independent launcher rejects the known-unsafe A4/pre-floor line before it starts, while hold-aware application entrypoints recheck the pinned source/artifact/runtime identity before publication-worker registration; publication activities still record or replay the hold and raise without projecting business state.

**Tech Stack:** Python 3.13, Pydantic v2, psycopg 3, PostgreSQL/Alembic, Temporal Python SDK, FastAPI, pytest/pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-10-06-p1-publication-recovery-hold-design.md`

## Global Constraints

- Start from the actual feature HEAD and reuse all completed P0/P1 work; do not rewrite the architecture or historical migrations.
- `SALIENCE_EFFECTS_ENABLED=false`; fixture publication budget remains exactly zero.
- RG0 and full RG1 remain `HELD`; P2–P7 remain gated; P6 remains disabled; `main` remains unmerged.
- Migration revision is `0042_publication_recovery_holds` with `down_revision = "0041_native_publication_sources"`; the descriptive filename remains `0042_legacy_publication_recovery_holds.py`.
- The two hold reasons are exactly `expired_permit_receipt` and `published_during_cancel`; state is exactly `held`.
- One operation can own one stable hold. Exact replay returns it; changed reason or evidence conflicts.
- Keep the cycle closed, retain the current permit/liability rules, and do not insert a generic `v4_recovery_cases` row.
- Retain `GET /v1/publications/runs/{job_id}` and existing SDK/CLI inspection; add no hold mutation surface.
- Before any supported prior application restarts, verify one retained hold for each reason and unchanged full canonical liabilities. The supported target is the first independently reviewed and qualified application at `69d7ab5da02cc9b8013f17b3e9aed3f7edb758a6` or a Git descendant; `039ddf`/A4 remains prohibited.
- For every unchanged-liability assertion, capture and compare the exact receipt, publication-attempt, effect, job, reservation, cycle, ordered publication/status-event, output/absence, outbox, checkpoint, audit, and provenance rows before and after. The first hold insertion is the only allowed addition; exact replay changes nothing.
- Migration `0042` and its rows remain installed. An empty hold table does not waive the floor; populated downgrade remains prohibited and no hold-retirement path is added.
- Bind a qualified floor to its full Git SHA, source manifest, built artifact SHA-256, and runtime import paths/hashes. Unknown, unpinned, unrelated-history, pre-floor, and mismatched artifacts fail before publication-worker registration.
- Use `apply_patch` for manual edits. Keep the unrelated pre-existing `.gitignore` `/temp/` change out of commits.

## Review Focus

- Concurrent first inserts race for either reason: the two reason-specific concurrency tests in Task 10 prove one stable ID, one row, and unchanged full liabilities.
- The worker dies after either reason's hold commit: the two reason-specific hard-exit tests in Task 10 prove replay of the same row/ID and byte-identical full liabilities.
- A known-unsafe, unknown, unpinned, unrelated-history, pre-floor, mismatched, path-substituted, or candidate-controlled artifact/lock is proposed: Task 10's launch-gate matrix proves denial before process start/worker registration, including an empty `0042` table.
- The actual floor artifact sees both retained reasons: Task 10's artifact rehearsal proves typed held outcomes, zero provider calls, stable holds/liabilities, and a second both-reason verifier pass.
- The verifier sees one missing/duplicated/rewritten reason or any liability change: the existing verifier test plus Task 10 launch tests block every restart.

## Planned file responsibilities

- `src/salience/agents/parallel_store.py`: derive the parent team status from child progress without changing child states or schema.
- `src/salience/cycles/publication_recovery.py`: typed hold projection; serialized create/exact-replay; read projection; immutable liability snapshot; both-reason rollback verification.
- `src/salience/cycles/legacy_publication_runtime.py`: invoke hold creation at the two existing typed-failure boundaries and include the hold ID in safe Temporal failure details.
- `src/salience/cycles/legacy_dispatch.py`: add the safe `recovery_hold` member to existing run inspection.
- `migrations/versions/0042_legacy_publication_recovery_holds.py`: additive table, reason/state/fingerprint/scope constraints, insert validation, immutability, narrow grants, and preservation-safe downgrade.
- `scripts/verify-v4-publication-recovery-rollback.py`: offline pre-restart verifier; no HTTP or mutation capability.
- `src/salience/cycles/publication_recovery_compatibility.py`: validate the applied migration, trusted worker pin, source/artifact/runtime identity, explicit start mode, and mandatory both-reason rollback proof for rollback restarts.
- `scripts/launch-v4-publication-worker.py`: candidate-independent pre-launch gate with fixed trusted-control paths and a closed worker-kind-to-entrypoint mapping; it never accepts an arbitrary executable, module, runtime root, pin, or expected digest from the candidate.
- `deployment/v4/publication-worker-launch-lock.json`: reviewed source for the exact launcher hash, target interpreter/runtime, worker entrypoints, application/artifact/source/import pins, and eligible floor; deployment publishes this file plus its digest through a separately owned read-only control mount.
- `Dockerfile` and `compose.yaml`: install one externally built exact wheel, retain that wheel for runtime hashing, and route the supported deployable worker start through the trusted launcher and read-only control mount instead of invoking the application module directly.
- `tests/integration/test_v4_publication_recovery_floor.py`: launch denial and exact floor-artifact qualification.
- `tests/fixtures/v4_publication_floor_worker.py`: execute the installed candidate artifact against retained local fixture workflows without importing the shared checkout.
- `tests/integration/test_durable_parallel_teams.py`: deterministic intermediate parent-state behavior.
- `tests/integration/test_v4_publication_recovery_holds.py`: migration, helper, concurrency, conflict, inspection, crash, and rollback-verifier acceptance.
- `tests/integration/test_v4_legacy_publication.py`: activity-level assertions for the two existing hold triggers and signed API/SDK/CLI parity.
- Current `docs/v4/*` status/evidence files: durable rereview closure, requirement coverage, executed commands/results, source hashes, and retained production gates.

---

### Task 1: Correct parallel-team parent status semantics

**Files:**
- Modify: `src/salience/agents/parallel_store.py:100-108`
- Modify: `tests/integration/test_durable_parallel_teams.py`

**Interfaces:**
- Consumes: persisted child states in `ParallelTeamStore._inspect(connection, team_id)`.
- Produces: the existing snapshot dictionary with corrected `status`; no new public type or database state.

- [x] **Step 1: Write deterministic failing status tests**

Add `test_started_team_with_completed_or_unknown_child_and_queued_siblings_is_running`. Use `max_concurrency=1`; assert all-queued is `queued`, succeeded-plus-queued is `running`, unknown-plus-queued is `running`, the parent `agent_runs.state` remains `delegating`, and no `team.finished` event exists. Extend the existing `test_process_death_keeps_completed_and_unknown_and_resumes_queued` path to kill the worker, observe `running` while unknown and queued siblings coexist, reclaim only queued siblings with their original run IDs, and reach the existing terminal aggregate. Extend the signed interface case to assert the same store status passes through API, SDK, and CLI output.

- [x] **Step 2: Prove the red behavior**

Run: `.venv/bin/pytest -q tests/integration/test_durable_parallel_teams.py -k 'started_team or process_death_keeps_completed_and_unknown_and_resumes_queued or signed_api_sdk_cli'`

Expected: FAIL because the parent becomes `queued` after the active child leaves `running`.

- [x] **Step 3: Implement the status fold**

In `_inspect`, make `queued` require `all(state == "queued")`; return `running` whenever any child is queued/running after any child has progressed; preserve terminal precedence and the ordered task projection.

- [x] **Step 4: Verify the focused parallel suite**

Run: `.venv/bin/pytest -q tests/unit/test_parallel_agent_teams.py tests/integration/test_durable_parallel_teams.py`

Expected: all tests PASS; no migration change.

- [x] **Step 5: Commit**

```bash
git add src/salience/agents/parallel_store.py tests/integration/test_durable_parallel_teams.py
git commit -m "fix(agents): preserve progressed parallel team status"
```

**Recorded result:** Commit `3be94979d0d05b68a748ff5ef47735077f06e7c1`; the requested focused parallel suite passed 34 tests.

### Task 2: Add the immutable recovery-hold schema

**Files:**
- Create: `migrations/versions/0042_legacy_publication_recovery_holds.py`
- Create: `tests/integration/test_v4_publication_recovery_holds.py`

**Interfaces:**
- Consumes: `v4_legacy_dispatches`, `v4_legacy_publication_fixture_receipts`, `v4_legacy_publication_fixture_progress`, `v4_permit_claims`, `v4_dispatch_permits`, publication attempt/request, job, context, cycle, effect, reservation, event, and outbox rows.
- Produces: `v4_legacy_publication_recovery_holds` keyed uniquely by `operation_id` and protected from update/delete.

- [x] **Step 1: Write red migration and direct-SQL tests**

Add tests that upgrade `0041 -> 0042`, inspect exact columns/constraints/revision, reject cross-workspace/cross-operation/fingerprint/reason/state mismatches, reject update/delete, pass empty downgrade/re-upgrade, and refuse populated downgrade without changing `alembic_version` or deleting the row. Use two direct SQL transactions racing the same `operation_id` to prove the database admits one row only. Assert `PUBLIC` has no table access. Following the repository's restricted-role convention, create a per-test fixture-runtime role during test/deployment provisioning, grant it only schema `USAGE` plus table `SELECT`/`INSERT`, and prove it lacks `UPDATE`/`DELETE`/`TRUNCATE`; the migration does not invent or discover a named application role.

- [x] **Step 2: Prove the migration is absent**

Run: `.venv/bin/pytest -q tests/integration/test_v4_publication_recovery_holds.py -k 'migration or direct_sql'`

Expected: FAIL because revision `0042_publication_recovery_holds` and its table do not exist.

- [x] **Step 3: Implement revision `0042_publication_recovery_holds`**

Create an additive table with stable `id`, unique `operation_id`, all spec identities, `publication_attempt_id`, `permit_id`, `owner_id`, frozen execution deadline, permit expiry, request/receipt/liability fingerprints, remote identity/state, reason, fixed `held` state, immutable liability snapshot, and timestamps. Add composite scope FKs where current schema permits them, an insert trigger that derives/compares the original canonical rows and reason-specific state, an update/delete rejection trigger, explicit revocation from `PUBLIC`, a three-second downgrade lock, and populated downgrade refusal. Runtime role provisioning owns the explicit per-principal `SELECT`/`INSERT` grants.

- [x] **Step 4: Verify schema and preservation behavior**

Run: `.venv/bin/pytest -q tests/integration/test_v4_publication_recovery_holds.py -k 'migration or direct_sql'`

Expected: PASS for valid insert, one-row concurrent uniqueness, narrow role privileges, hostile SQL denial, empty rollback, and populated preservation.

- [x] **Step 5: Commit**

```bash
git add migrations/versions/0042_legacy_publication_recovery_holds.py tests/integration/test_v4_publication_recovery_holds.py
git commit -m "feat(v4): add immutable publication recovery holds"
```

**Recorded result:** Commit `c1865b7b293dd9a7ec744d4823c266da8dc14f9d`; the migration/direct-SQL selection passed 2 tests.

### Task 3: Implement serialized hold creation and exact replay

**Files:**
- Create: `src/salience/cycles/publication_recovery.py`
- Modify: `tests/integration/test_v4_publication_recovery_holds.py`

**Interfaces:**
- Produces: `PublicationRecoveryReason = Literal["expired_permit_receipt", "published_during_cancel"]`.
- Produces: frozen `PublicationRecoveryHoldView(id: UUID, workspace_id: UUID, operation_id: UUID, goal_id: UUID, intent_id: UUID, cycle_id: UUID, context_id: UUID, job_id: UUID, publication_attempt_id: UUID, owner_id: UUID, reason: PublicationRecoveryReason, state: Literal["held"], execution_deadline: datetime, permit_expires_at: datetime, created_at: datetime)`.
- Produces: `PublicationRecoveryConflictError(ValueError)` for every non-exact replay.
- Produces: `record_publication_recovery_hold(database_url: str, *, workspace_id: UUID, operation_id: UUID, reason: PublicationRecoveryReason) -> PublicationRecoveryHoldView`.
- Produces: `load_publication_recovery_hold(connection, *, workspace_id: UUID, operation_id: UUID) -> PublicationRecoveryHoldView | None`.

- [x] **Step 1: Write red helper tests**

Add `test_exact_hold_replay_returns_stable_id`, `test_hold_replay_conflicting_reason_or_evidence_denies`, and `test_concurrent_exact_hold_insert_returns_one_stable_hold`. Assert an advisory-lock/unique-key race yields one row and identical views. Compare the exact receipt, effect, job, reservation, cycle, ordered status events, output/absence, and ordered outbox rows before and after; only the one hold row may be added.

- [x] **Step 2: Prove the helper is absent**

Run: `.venv/bin/pytest -q tests/integration/test_v4_publication_recovery_holds.py -k 'exact_hold or conflicting_reason or concurrent_exact'`

Expected: collection/import FAIL for the missing module or functions.

- [x] **Step 3: Implement typed projection and record/load functions**

Derive all identities, fingerprints, current remote progress, permit dates, and liability snapshot inside one PostgreSQL transaction. Serialize with an operation-scoped advisory transaction lock. Return the stored view and stable ID only after every immutable binding, reason, fingerprint, and evidence field matches. Raise `PublicationRecoveryConflictError` for any differing replay and `PermissionError` when required evidence/authority is unavailable. Do not accept caller-supplied authority, fingerprints, liability, owner, or deadline.

- [x] **Step 4: Verify idempotency, concurrency, and conflict**

Run: `.venv/bin/pytest -q tests/integration/test_v4_publication_recovery_holds.py -k 'exact_hold or conflicting_reason or concurrent_exact'`

Expected: PASS with one stable hold and unchanged liabilities.

- [x] **Step 5: Commit**

```bash
git add src/salience/cycles/publication_recovery.py tests/integration/test_v4_publication_recovery_holds.py
git commit -m "feat(v4): record publication recovery holds"
```

**Recorded result:** Commit `78383f27a3caf368f5cefd55f9aff0a9dd6a4c84`; the exact replay/conflict/concurrency selection passed 3 tests (2 deselected).

### Task 4: Persist expired-permit receipt recovery holds

**Files:**
- Modify: `src/salience/cycles/legacy_publication_runtime.py`
- Modify: `tests/integration/test_v4_legacy_publication.py`

**Interfaces:**
- Consumes: `record_publication_recovery_hold(..., reason="expired_permit_receipt")` from Task 3.
- Produces: existing nonretryable `LegacyPublicationRecoveryHeld` with safe `hold_id` details.

- [x] **Step 1: Extend the expired-permit test red**

Add `test_expired_receipt_hold_requires_current_reconcile_scope_and_never_resubmits` assertions: one typed row, matching stable ID in failure details, one durable accepted receipt, no second submit/status progress, and unchanged canonical liabilities. Cover missing receipt, altered authorization, and revoked reconcile grant creating no misleading hold. Task 6 owns the signed inspection assertion after that projection exists.

- [x] **Step 2: Prove no hold is persisted**

Run the named test with `.venv/bin/pytest -q tests/integration/test_v4_legacy_publication.py::test_expired_receipt_hold_requires_current_reconcile_scope_and_never_resubmits`

Expected: FAIL on missing hold/failure details while existing fail-closed behavior remains.

- [x] **Step 3: Record the hold at the validated boundary**

After `_expired_permit_receipt_recovery` has validated the exact effect, receipt, current publication facts, and separate reconciliation grant, record/exact-replay the hold before raising the typed failure. Do not move provider readback earlier or add status polling.

- [x] **Step 4: Verify expired-permit recovery**

Run the named test again plus `test_publication_revoked_reconcile_scope_holds_durable_acceptance`.

Expected: PASS; revoked reconciliation creates no new hold/effect, exact recovery creates one hold and no resubmit.

- [x] **Step 5: Commit**

```bash
git add src/salience/cycles/legacy_publication_runtime.py tests/integration/test_v4_legacy_publication.py
git commit -m "feat(v4): hold expired publication receipt recovery durably"
```

**Recorded result:** Commits `42242b6` and `75ef794`; the final expiry, exact replay, revoked-scope and missing-receipt regressions passed on the current schema.

### Task 5: Persist post-close published-cancellation holds

**Files:**
- Modify: `src/salience/cycles/legacy_publication_runtime.py`
- Modify: `tests/integration/test_v4_legacy_publication.py`

**Interfaces:**
- Consumes: `record_publication_recovery_hold(..., reason="published_during_cancel")` from Task 3.
- Produces: the same typed failure with stable hold ID, before base cancellation writes.

- [x] **Step 1: Extend the direct cancellation regression red**

Update `test_post_close_cancel_reads_published_then_holds_original_liability` to require one `published_during_cancel` row and stable failure detail while retaining all existing zero-terminal-write and unchanged-liability assertions.

- [x] **Step 2: Prove the new record is missing**

Run: `.venv/bin/python -m pytest -q tests/integration/test_v4_legacy_publication.py::test_post_close_cancel_reads_published_then_holds_original_liability`

Expected: FAIL only on recovery-hold assertions.

- [x] **Step 3: Record before the existing typed failure**

When the exact cancellation readback is `published`, call the Task 3 recorder before raising. Do not call the base cancellation projector or change the confirmed-cancelled branch.

- [x] **Step 4: Verify both cancellation branches**

Run: `.venv/bin/pytest -q tests/integration/test_v4_legacy_publication.py -k 'post_close_cancel or cancellation'`

Expected: PASS; published holds, confirmed cancelled retains its existing projection.

- [x] **Step 5: Commit**

```bash
git add src/salience/cycles/legacy_publication_runtime.py tests/integration/test_v4_legacy_publication.py
git commit -m "feat(v4): hold published cancellation readback durably"
```

**Recorded result:** Commit `5ce27ab47f230f91f35b02c1e02d95a2f557c1d8`; post-close cancellation and confirmed-cancel regressions passed (2 tests).

### Task 6: Prove crash-after-commit recovery and inspection parity

**Files:**
- Modify: `src/salience/cycles/legacy_dispatch.py`
- Modify: `tests/integration/test_v4_publication_recovery_holds.py`
- Modify: `tests/integration/test_v4_legacy_publication.py`
- Test only: `src/salience/sdk/client.py`, `src/salience/cli.py`

**Interfaces:**
- Consumes: `load_publication_recovery_hold` from Task 3.
- Produces: additive `recovery_hold: dict | None` in the existing run inspection response.

- [x] **Step 1: Write red crash and inspection tests**

Add `test_hold_commit_then_process_death_replays_without_liability_change`: terminate the activity/worker after the hold commit but before its typed failure is acknowledged, restart a compatible worker, and assert one stable row/ID. Compare the exact receipt, effect, job, reservation, cycle, ordered status events, output/absence, and ordered outbox rows before commit, after process death, and after retry. Extend signed route, `LegacyPublicationClient.inspect`, and `content legacy-publication inspect` assertions for both hold reasons, the same safe projection, and absence of any hold mutation command.

- [x] **Step 2: Prove inspection is missing and crash recovery is incomplete**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/integration/test_v4_publication_recovery_holds.py::test_hold_commit_then_process_death_replays_without_liability_change \
  tests/integration/test_v4_legacy_publication.py::test_signed_publication_routes_sdk_cli_and_safe_default_denial
```

Expected: FAIL on missing `recovery_hold` and stable failure detail; no duplicate effect is allowed even in red.

- [x] **Step 3: Add the bounded inspection projection**

Join/load by the already scoped operation in `LegacyDispatch.inspect`; return only the model's safe fields or `None`. Keep SDK/CLI passthrough code unchanged unless a failing typed contract proves an additive field declaration is required.

- [x] **Step 4: Verify crash recovery and all three clients**

Repeat the exact two-test command from Step 2.

Expected: PASS; one hold survives process death, retry reuses it, liabilities remain unchanged, and no mutation route exists.

- [x] **Step 5: Commit**

```bash
git add src/salience/cycles/legacy_dispatch.py tests/integration/test_v4_publication_recovery_holds.py tests/integration/test_v4_legacy_publication.py
git commit -m "feat(v4): expose immutable publication recovery holds"
```

**Recorded result:** Commit `931840ddfc78314b93d306b8c130e9f39ed7ec4f`; process-death replay/inspection passed (2 tests), and both hold-reason projections passed (2 tests).

### Task 7: Add the both-reason pre-restart rollback verifier

**Files:**
- Modify: `src/salience/cycles/publication_recovery.py`
- Create: `scripts/verify-v4-publication-recovery-rollback.py`
- Modify: `tests/integration/test_v4_publication_recovery_holds.py`

**Interfaces:**
- Produces: `verify_publication_recovery_rollback(database_url: str, *, workspace_id: UUID, operation_ids: Mapping[PublicationRecoveryReason, UUID]) -> tuple[PublicationRecoveryHoldView, PublicationRecoveryHoldView]`.
- Produces: offline script arguments `--database-url`, `--workspace-id`, `--expired-operation-id`, `--cancel-operation-id`; exit 0 only after both holds and liabilities match.

- [x] **Step 1: Write the red rollback test**

Add `test_rollback_verifier_requires_both_reasons_and_unchanged_liabilities`. Assert missing reason, duplicate operation, changed reason/evidence, or any changed receipt/effect/job/reservation/cycle/event/output/outbox value raises and yields nonzero script exit. Assert exact retained rows return both stable IDs and exit 0.

- [x] **Step 2: Prove no verifier exists**

Run: `.venv/bin/python -m pytest -q tests/integration/test_v4_publication_recovery_holds.py::test_rollback_verifier_requires_both_reasons_and_unchanged_liabilities`

Expected: import/script FAIL.

- [x] **Step 3: Implement read-only verification**

Require the mapping keys to equal both exact reasons and operations to be distinct. Reload each immutable hold and recompute its current liability snapshot/fingerprint in one repeatable-read transaction; compare with stored snapshots. The script prints bounded JSON containing only workspace, reasons, operation IDs, hold IDs, and `verified: true`.

- [x] **Step 4: Verify the mandatory rollback gate**

Run the named pytest command from Step 2, then invoke the script against the disposable fixture database populated by the rehearsal harness:

```bash
.venv/bin/python scripts/verify-v4-publication-recovery-rollback.py \
  --database-url "$TEST_DATABASE_URL" \
  --workspace-id "$WORKSPACE_ID" \
  --expired-operation-id "$EXPIRED_OPERATION_ID" \
  --cancel-operation-id "$CANCEL_OPERATION_ID"
```

Expected: exact both-reason state PASS; every missing/mutated case blocks prior-code restart.

- [x] **Step 5: Commit**

```bash
git add src/salience/cycles/publication_recovery.py scripts/verify-v4-publication-recovery-rollback.py tests/integration/test_v4_publication_recovery_holds.py
git commit -m "feat(v4): gate publication hold rollback on both reasons"
```

**Recorded result:** Commit `7878208e7a353f93bb70279ad0dd7f0760f93ec4`; the both-reason rollback verifier test passed.

### Task 8: Run the complete focused application qualification

**Files:**
- Modify only if a focused failure proves an implementation defect in Tasks 1–7.

**Interfaces:**
- Consumes: all implementation and tests above.
- Produces: a clean application commit and captured focused command/JUnit/log/source manifest for evidence.

- [x] **Step 1: Run migration and focused suites**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/unit/test_parallel_agent_teams.py \
  tests/integration/test_durable_parallel_teams.py \
  tests/integration/test_v4_publication_recovery_holds.py \
  tests/integration/test_v4_legacy_publication.py \
  tests/integration/test_v4_native_publication_schedules.py \
  tests/integration/test_v4_migrations.py \
  tests/integration/test_v4_cycle_governance.py \
  tests/integration/test_v4_item7_governance.py \
  tests/e2e/test_v4_item7_delivery_recovery.py
.venv/bin/python -m compileall -q src tests scripts
```

Expected: all PASS, zero failures/errors/skips; record exact counts and duration rather than predicting them.

- [x] **Step 2: Run rollback rehearsal for both reasons**

Populate one exact hold per reason, execute the offline verifier, stop/drain the compatible fixture worker, re-run the verifier, and exercise the retained prior-code fail-closed path without downgrading `0042`.

Expected: both verification passes report the same hold IDs; all liability snapshots remain identical; prior-code restart is not attempted on a failed verifier.

- [x] **Step 3: Repair only evidenced failures and rerun affected scope**

Use `superpowers:systematic-debugging` for any failure. Add a regression before changing behavior. Repeat the complete focused command after the last repair.

- [x] **Step 4: Commit the final application candidate**

If Step 3 produced repairs, stage only the exact reviewed files named by `git diff --name-only`, inspect `git diff --cached`, and commit `feat(v4): qualify durable publication recovery holds`. If no repair was needed, create no empty commit. Record the resulting application HEAD as the qualified application commit.

**Recorded result:** No application commit; candidate remains `7878208e7a353f93bb70279ad0dd7f0760f93ec4`. Focused suite: 116 passed; compileall passed; rollback rehearsal: 1 passed; Task 8 evidence review: PASS with zero findings. Three Alembic deprecation warnings were reported.

### Task 9: Reconcile active documentation and durable evidence

**Files:**
- Create: `docs/v4/publication-recovery-hold-evidence.json`
- Modify: `docs/v4/IMPLEMENTATION-BLUEPRINT.md`
- Modify: `docs/v4/progress.md`
- Modify: `docs/v4/validation.md`
- Modify: `docs/v4/p1-qualification-matrix.json`
- Modify: `docs/v4/legacy-publication-evidence.json`
- Modify: `docs/v4/p0-release.json`
- Modify: `docs/v4/documentation-status-audit.json`
- Modify: `docs/v4/evidence-lock.json`
- Modify the five audited active references: `docs/v4/p1-qualification-matrix.json`, `docs/v4/progress.md`, `docs/v4/IMPLEMENTATION-BLUEPRINT.md`, `docs/README.md`, and `docs-new-arch/README.md`.
- Modify current checkpoint aliases that still say the rereview is pending: `README.md`, `docs/limitations.md`, `docs/memory.md`, `docs/implementation-progress.md`, `docs/v4/validation.md`, `docs/v4/p0-release.json`, `docs/v4/documentation-status-audit.json`, `docs/v4/evidence-lock.json`, `docs/v4/legacy-publication-evidence.json`, and other non-historical current-banner paths returned by the same exact stale-text search. Exclude archived snapshots, dated evidence bodies, old plans/specs/reviews, and historical captures.

**Interfaces:**
- Consumes: actual Task 8 SHA/results; rereview findings hash `483b57e8ae1aee0993dea01d8e9c5b50f5add3bed0f9bb04887ecc2e013117d5`; rereview result hash `3ed1e9052513a850a878a802f0d5137f69275c34d9b25e16f73d5baccbe6d992`; meta-review report hash `507d7c5422f43f28cb953ffa34d472ad7933def6da6b2dce1a30119624c4b96e`; and meta-review result hash `169129ec9673cd98c0545b17152d3246921b8bee34b45e9a257c60295b0dee3e`.
- Produces: source-bound current evidence with R18/R32 local advancement and every retained production gate explicit.

- [x] **Step 1: Capture actual full non-live and documentation results**

Run:

```bash
.venv/bin/python -m pytest -p no:cacheprovider tests/contracts tests/unit tests/integration tests/e2e tests/evals -m 'not live' -q -rs
.venv/bin/python docs/v4/test_checks.py
.venv/bin/python docs/v4/check.py --self-test
.venv/bin/python docs/v4/check.py
for script in tests/scripts/*.sh; do bash "$script"; done
.venv/bin/python -m pip check
.venv/bin/python -m compileall -q src tests scripts docs/v4
git diff --check
```

Parse every modified JSON file with Python's `json` module. Render the six Mermaid sources with `.venv/bin/python docs/v4/verify_diagrams.py --node-modules /tmp/salience-v4-mermaid/node_modules --browser /usr/bin/chromium --output artifacts/v4-next-p1/qualification-diagrams`, using the repository's already qualified Mermaid 12.0.0 environment. Store JUnit/log/command/environment/source manifests under ignored artifacts, with hashes in tracked evidence.

Expected: PASS with actual counts; do not reuse 604 as the new total.

**Recorded result:** The authoritative clean disposable-DB run passed 614 tests, zero failures/errors/skips, with three Alembic deprecation warnings (917.367s pytest; 920.214s runner). It used a fresh `template0` database with zero relevant baseline, remained at `0042_publication_recovery_holds`, dropped the disposable database, and preserved the original database. Final JUnit SHA-256 is `ed97fc0aaf5c1b2f89c17f7c77f9aaf08679feb22634933632e5b7a3757c605a`; final log SHA-256 is `64985ba84b268d8056ebb56bec3951e79c086b84d995901d33e39ad791d379f4`.

- [x] **Step 2: Update only current status and evidence**

Record completed 2026-10-06 rereview/meta-review, targeted 5/5 rerun, Task 8 focused results, new full results, both-reason rollback proof, application SHA, and source hashes. Correct the five audited stale references, remove the repeated R18 sentence, update all active “rereview pending” checkpoint aliases now made stale, and preserve historical captures verbatim.

**Recorded result:** All 54 active exact stale-banner Markdown paths, root `README.md`, the active legacy dispatch contract, the five current JSON records, and the new recovery-hold evidence record are reconciled. The corrected-publication rereview remains explicitly scoped to application `a4b2368cdb463939d093020224a5b5667392aee6` with documentation/observed HEAD `d0311725686d81b7b4747c2d6225986e5ea3a524`; its four supplied artifact hashes and the bounded 5/5 orchestrator attribution are recorded. It does not review application `7878208`. Four duplicate R18 acceptance sentences were removed; one remains.

- [x] **Step 3: Preserve requirement and gate accuracy**

Advance only local fixture R18/R32 hold/ownership coverage. Keep production-wide revocation, general policy, routing/live schedule inventory, production recovery workers, R33 resume, R34 external alerts, RG0/RG1, and P2–P7 open/held.

**Recorded result:** Current R18/R32 mapped verifier counts were derived from distinct test cases in the authoritative JUnit as 76 and 81. Only local no-effects fixture hold/ownership coverage advances. Production effects remain disabled; production-wide revocation, general policy, routing/live schedule inventory, production recovery workers, R33/R34, RG0/RG1, full RG1, and P2–P7 remain open or held; P6 remains disabled and `main` remains unmerged.

- [x] **Step 4: Run documentation verification after tracked updates**

Repeat `.venv/bin/python docs/v4/test_checks.py`, `.venv/bin/python docs/v4/check.py --self-test`, `.venv/bin/python docs/v4/check.py`, all four `tests/scripts/*.sh`, JSON parsing for every modified JSON, Mermaid rendering, `.venv/bin/python -m pip check`, `.venv/bin/python -m compileall -q src tests scripts docs/v4`, and `git diff --check`.

Expected: all PASS and every referenced hash resolves to the final tracked content or named ignored artifact.

**Recorded result:** `docs/v4/test_checks.py` passed 15 tests; the consolidated checker and self-test passed 75 requirements with 356 locked source files, 360 inventory entries, and 6 negative controls; all 4 documentation shell contracts passed; all 6 Mermaid diagrams rendered with Mermaid 12.0.0; all 6 modified JSON files parsed; `pip check`, compileall, historical 14-path SHA verification, 356-file source-lock verification, and `git diff --check` passed. The tracked documentation audit was recomputed as 88 Markdown paths and 246 documentation artifacts.

- [x] **Step 5: Commit documentation/evidence**

Stage only the exact reviewed paths listed in this task and any current-banner aliases proven stale by the recorded search. Inspect `git diff --cached --name-only` and `git diff --cached` before committing `docs(v4): qualify publication recovery hold increment`; never stage the unrelated `.gitignore` change.

### Task 10: Qualify the hold-aware publication-worker rollback floor

**Files:**
- Create: `src/salience/cycles/publication_recovery_compatibility.py`
- Create: `scripts/launch-v4-publication-worker.py`
- Create: `deployment/v4/publication-worker-launch-lock.json`
- Create: `tests/integration/test_v4_publication_recovery_floor.py`
- Create: `tests/fixtures/v4_publication_floor_worker.py`
- Modify: `Dockerfile`
- Modify: `compose.yaml`
- Modify: `src/salience/cycles/legacy_runtime.py`
- Modify: `src/salience/workflows/worker.py`
- Modify: `tests/integration/test_v4_publication_recovery_holds.py`
- Modify: `tests/integration/test_v4_legacy_publication.py`
- Modify after actual qualification: `README.md`, `docs/README.md`, `docs/implementation-progress.md`, `docs/limitations.md`, `docs/memory.md`, `docs/v4/IMPLEMENTATION-BLUEPRINT.md`, `docs/v4/progress.md`, `docs/v4/validation.md`, `docs/v4/p1-qualification-matrix.json`, `docs/v4/p0-release.json`, `docs/v4/documentation-status-audit.json`, `docs/v4/evidence-lock.json`, and `docs/v4/publication-recovery-hold-evidence.json`
- Preserve unchanged: migration `0042`, the retained `039ddf` negative reproduction, and Tasks 1–9 evidence artifacts

**Interfaces:**
- Produces: frozen `PublicationWorkerSourcePin(path: str, git_blob: str, sha256: str)` and `PublicationWorkerImportPin(module: str, wheel_path: str, observed_path: str, sha256: str)`.
- Produces: frozen `PublicationWorkerPin(schema_version: Literal["salience.publication-worker-pin.v1"], application_commit: str, eligible_anchor: str, artifact_path: str, artifact_sha256: str, distribution_record_path: str, distribution_record_sha256: str, source_manifest_sha256: str, source_files: tuple[PublicationWorkerSourcePin, ...], runtime_imports: tuple[PublicationWorkerImportPin, ...])`. `artifact_path` is exactly `/opt/salience-artifacts/salience.whl`; the pinned installed `RECORD` and every file/hash it names attest the complete installed distribution.
- Produces: `PublicationWorkerStartMode = Literal["current", "rollback-restart"]` and `PublicationWorkerKind = Literal["deployable", "legacy-fixture"]`.
- Produces: frozen `PublicationWorkerEntrypoint(module: str, argv: tuple[str, ...])` and `PublicationWorkerLaunchLock(schema_version: Literal["salience.publication-worker-launch-lock.v1"], launcher_sha256: str, interpreter_path: str, interpreter_sha256: str, runtime_root: str, workers: Mapping[PublicationWorkerKind, PublicationWorkerEntrypoint], pin: PublicationWorkerPin)`.
- Produces: `PublicationWorkerCompatibilityError(RuntimeError)` for every failed migration, ancestry, pin, artifact, import, or rollback check.
- Produces: `require_publication_worker_compatibility(database_url: str, *, start_mode: PublicationWorkerStartMode, launch_lock: PublicationWorkerLaunchLock, observed_launch_lock_sha256: str, rollback_workspace_id: UUID | None = None, rollback_operation_ids: Mapping[PublicationRecoveryReason, UUID] | None = None) -> PublicationWorkerPin | None`; `None` is allowed only when the database is provably before `0042`. `rollback-restart` requires one exact workspace plus one distinct operation ID for each exact reason and always calls the existing verifier with that workspace before returning.
- Produces: launcher arguments `--mode {current,rollback-restart}`, `--worker-kind {deployable,legacy-fixture}`, plus required `--workspace-id`, `--expired-hold-operation-id`, and `--cancel-hold-operation-id` only in `rollback-restart` mode. It reads the same `DATABASE_URL` that is preserved for the worker process and accepts no command separator, executable, module, runtime-root, manifest, lock path, expected digest, or alternate database argument.
- Produces: fixed control paths `/run/salience-control/launch-v4-publication-worker.py`, `/run/salience-control/publication-worker-launch-lock.json`, and `/run/salience-control/publication-worker-launch-lock.sha256`. The digest file is the authoritative deployment-owned trust root; all three are mounted read-only from the reviewed control bundle, outside the target application artifact. The launcher authenticates its own source, the lock, interpreter, runtime root, and target entrypoint before `os.execve` with a scrubbed import environment.
- Produces: in-process configuration keys `SALIENCE_PUBLICATION_WORKER_START_MODE`, `SALIENCE_PUBLICATION_WORKER_LAUNCH_LOCK`, and `SALIENCE_PUBLICATION_WORKER_LAUNCH_LOCK_SHA256`; restart keys `SALIENCE_PUBLICATION_ROLLBACK_WORKSPACE_ID`, `SALIENCE_PUBLICATION_EXPIRED_HOLD_OPERATION_ID`, and `SALIENCE_PUBLICATION_CANCEL_HOLD_OPERATION_ID` are mandatory only and always together in `rollback-restart`. Missing configuration at `0042` fails closed.
- Consumes: existing `verify_publication_recovery_rollback(...)`; it does not replace or weaken the direct canonical read.

- [ ] **Step 1: Add the missing per-reason RED tests**

Keep the existing published-cancellation concurrency case and expired-receipt hard-exit case. Add:

- `test_expired_permit_concurrent_exact_hold_insert_returns_one_stable_hold` in `test_v4_publication_recovery_holds.py`;
- `test_cancel_hold_commit_then_process_death_replays_without_liability_change` in the same file, using the real recorder in a subprocess followed immediately by `os._exit(74)`;
- full before/after snapshot equality for receipt, publication attempt, effect, job, reservation, cycle, status/cycle events, output, outbox, checkpoints, audit, provenance, and the stable hold row in both existing and new reason-specific cases.

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/integration/test_v4_publication_recovery_holds.py::test_concurrent_exact_hold_insert_returns_one_stable_hold \
  tests/integration/test_v4_publication_recovery_holds.py::test_expired_permit_concurrent_exact_hold_insert_returns_one_stable_hold \
  tests/integration/test_v4_publication_recovery_holds.py::test_hold_commit_then_process_death_replays_without_liability_change \
  tests/integration/test_v4_publication_recovery_holds.py::test_cancel_hold_commit_then_process_death_replays_without_liability_change
```

Expected RED: the two new cases are absent; the existing cases continue to prove their prior narrower behavior.

- [ ] **Step 2: Implement only the test/harness support needed for the two-reason matrix**

Reuse `_SNAPSHOT_SQL`, `_liability_snapshot`, `_assert_named_liabilities_unchanged`, `_hard_exit_publication_worker`, and the authentic reason-specific fixture builders. Do not duplicate production hold logic or weaken activity ordering. Extend the snapshot helper if any named canonical collection is missing, then make all four cases assert one row/ID and unchanged full liabilities.

Repeat Step 1's exact command.

Expected GREEN: four PASS; no production source change unless a test exposes an actual defect.

- [ ] **Step 3: Write RED launch-gate tests**

In `test_v4_publication_recovery_floor.py`, add:

- `test_floor_gate_rejects_a4_unknown_unpinned_unrelated_and_pre_floor_before_start`, including an active empty `0042` table and a launch callback that must remain uncalled;
- `test_floor_gate_requires_exact_artifact_install_source_and_runtime_import_pins`, covering missing or mismatched retained-wheel path/hash, installed-distribution `RECORD` path/hash or any recorded file, manifest digest, source file/blob/hash, runtime logical name/path/hash, and a descendant without its own exact pin;
- `test_launcher_binds_valid_floor_lock_to_fixed_interpreter_runtime_and_entrypoint`, proving that a valid floor lock paired with an A4 executable, module, runtime root, `PYTHONPATH`, working-directory shadow, or extra command is rejected before a process starts;
- `test_launcher_rejects_candidate_selected_lock_and_modified_launcher`, proving that a self-generated lock plus matching candidate-supplied digest cannot replace the independently mounted digest and that a launcher source/hash mismatch fails closed;
- `test_floor_gate_requires_workspace_and_both_reason_verification_for_rollback_restart`, covering omitted workspace/both IDs, either ID omitted, duplicated IDs, reason-swapped/mismatched or cross-workspace operations, and changed full liabilities at the launcher boundary;
- `test_current_mode_rejects_rollback_arguments_and_rollback_mode_always_verifies`, proving `current` rejects a workspace or either operation ID, the mode cannot be used to bypass the verifier, and pre-floor/A4 remains rejected in either mode;
- `test_docker_and_compose_worker_use_pinned_wheel_and_trusted_launcher`, parsing both deployment files and proving the image installs/retains only the required external wheel, performs no `pip install .`, and neither supported default invokes `salience.workflows.worker` directly;
- `test_pre_0042_database_returns_no_floor_requirement`, proving the only allowed no-pin result is a known revision before `0042`.

The pin fixture must name at least `salience.cycles.publication_recovery_compatibility`, `salience.cycles.legacy_runtime`, `salience.cycles.legacy_publication_runtime`, `salience.cycles.publication_recovery`, `salience.cycles.legacy_publication`, `salience.workflows.worker`, `salience.workflows.publication`, `salience.workflows.persistence`, `salience.publication.repository`, and `migrations/versions/0042_legacy_publication_recovery_holds.py`. Source pins include repository-relative path, Git blob ID, and SHA-256; runtime pins additionally include logical module, wheel-relative path, observed `__file__`, and SHA-256. The retained wheel hash must equal `artifact_sha256`; the installed `.dist-info/RECORD` hash and every file/hash it enumerates must match under the fixed runtime root. The launch lock also pins the external launcher source, exact interpreter, closed worker entrypoints, and full pin-manifest digest.

Run: `.venv/bin/python -m pytest -q tests/integration/test_v4_publication_recovery_floor.py`

Expected RED: imports/functions/launcher are missing.

- [ ] **Step 4: Implement the read-only external and in-process gates**

Implement the Task 10 interfaces. Read Alembic state with a three-second, read-only transaction. An unreachable database, unknown revision, unknown revision graph, or ambiguous branch fails closed. A known revision before `0042` returns no floor requirement; `0042` or a known descendant in the repository's single migration lineage requires the exact trusted pin. During pin generation in the clean source checkout, establish application ancestry with `git merge-base --is-ancestor 69d7ab5da02cc9b8013f17b3e9aed3f7edb758a6 <candidate>` and record the exact anchor/candidate/result in qualification evidence. The runtime gate does not require `.git` inside the installed artifact: it matches the full application SHA and verifies the externally trusted manifest digest plus artifact/source/import hashes. Ancestry is only eligibility; the candidate still needs its own exact pin. Reject `039ddf39cb9b846c205f352e083293fbec6f9fc5`, A4 `a4b2368cdb463939d093020224a5b5667392aee6`, every pre-floor/unrelated/unknown/unpinned candidate, and every source/artifact/import mismatch.

The external launcher uses only the three fixed `/run/salience-control/` paths. It first compares the launch-lock bytes with the digest in the separately deployed `.sha256` trust-root file, then compares its own bytes with `launcher_sha256` in the authenticated lock. It verifies the fixed interpreter path/hash, hashes the retained `/opt/salience-artifacts/salience.whl` against `artifact_sha256`, verifies the pinned installed `.dist-info/RECORD`, verifies every installed file/hash enumerated by that record under the fixed runtime root, and then checks the exact application/source/import pin and selected closed worker entrypoint. It accepts only `deployable` or `legacy-fixture`, constructs the complete `os.execve` argument vector from the authenticated lock, sets the working directory to the authenticated runtime root, removes `PYTHONPATH`/`PYTHONHOME`, caller-controlled import flags, and any caller-supplied in-process gate variables, then injects the authenticated fixed lock path/digest, mode, and exact rollback IDs for the child recheck. It invokes the pinned interpreter with `-I -m <fixed-module>` and never accepts or appends caller command text. A target artifact therefore cannot pair a valid floor lock with an A4 path, source-built installation, modified installed file, or shadow module.

`rollback-restart` is an explicit mode, not an inference from optional fields. It requires exactly one workspace ID and one distinct operation ID for each exact reason, proves both operation records belong to that workspace, and always calls `verify_publication_recovery_rollback(database_url, workspace_id=rollback_workspace_id, operation_ids=rollback_operation_ids)` before `exec`; missing workspace, missing/duplicate/reason-swapped/mismatched/cross-workspace operations, or changed liabilities fail closed. `current` rejects every rollback workspace/operation argument. Both modes still require the exact eligible pin whenever `0042` is active, so `current` cannot authorize A4 or a pre-floor artifact. An empty hold table never authorizes a rollback restart or a pre-floor worker.

`legacy_runtime.main` and `workflows.worker.run_worker` perform the same mode/pin check before connecting or constructing a publication worker when `0042` is active. This in-process check is defense in depth for qualified descendants; it is not relied on for A4, which lacks it. Replace the Dockerfile's source install with a required `SALIENCE_WHEEL_FILE` build argument: copy that one prebuilt wheel to `/opt/salience-artifacts/salience.whl`, install only that retained file, and never run `pip install .`. Make the Compose application-image build pass the same exact wheel path for API, worker, migrate, and fixture services. Change the Dockerfile default and `compose.yaml` worker command so every supported worker start is exactly `/usr/local/bin/python /run/salience-control/launch-v4-publication-worker.py --mode current --worker-kind deployable`. Compose mounts the reviewed launcher, lock, and independent digest file read-only at the fixed control paths; the target image cannot choose or overwrite them. The launcher's preserved `DATABASE_URL` is the same value checked before exec and consumed afterward by the fixed worker module. Source-level worker builders remain test helpers and are not supported deployment entrypoints.

The tracked `deployment/v4/publication-worker-launch-lock.json` is the reviewed source of the deployed lock. Qualification copies its exact bytes into the control bundle and creates the digest file in a separately owned deployment/evidence step; neither the worker image nor a launcher CLI argument supplies that digest. The lock pins `scripts/launch-v4-publication-worker.py`, `salience.cycles.publication_recovery_compatibility`, and `salience.workflows.worker` in addition to the complete source/runtime set. Add no migration, role, HTTP endpoint, mutable floor record, or hold-retirement path.

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/integration/test_v4_publication_recovery_floor.py \
  tests/integration/test_v4_publication_recovery_holds.py::test_rollback_verifier_requires_both_reasons_and_unchanged_liabilities \
  tests/integration/test_v4_publication_recovery_holds.py::test_migration_0042_schema_role_and_empty_rollback_reupgrade \
  tests/integration/test_v4_publication_recovery_holds.py::test_direct_sql_recovery_hold_guards_race_and_populated_downgrade
```

Expected GREEN: every rejected candidate, path/entrypoint substitution, and trust-root substitution is stopped before launch/registration; rollback mode cannot omit the both-reason proof; Docker/Compose use the gate; exact synthetic pin and both-reason proof pass; migration preservation remains unchanged.

- [ ] **Step 5: Commit the implementation candidate**

```bash
git add \
  Dockerfile \
  compose.yaml \
  src/salience/cycles/publication_recovery_compatibility.py \
  src/salience/cycles/legacy_runtime.py \
  src/salience/workflows/worker.py \
  scripts/launch-v4-publication-worker.py \
  tests/integration/test_v4_publication_recovery_floor.py \
  tests/integration/test_v4_publication_recovery_holds.py \
  tests/integration/test_v4_legacy_publication.py
git commit -m "feat(v4): gate publication workers on recovery floor"
```

Before committing, inspect `git diff --cached`; do not include the independent AgentTube adoption spec or `.superpowers/` research logs in this application commit.

- [ ] **Step 6: Build and execute the actual candidate artifact**

From a clean detached checkout of the exact Step 5 commit, build one wheel once, compute its SHA-256, and place that exact file at the Docker build argument path. Build the application image from that retained wheel; do not rebuild the wheel inside Docker. Generate the source/blob and complete installed-distribution manifests plus `deployment/v4/publication-worker-launch-lock.json` that pins the already-existing Step 5 application SHA, retained wheel path/hash, installed `RECORD` path/hash, and every recorded installed file. This order avoids a self-referential commit hash: the launch lock is never part of the application commit/artifact it authenticates. Verify runtime imports resolve only inside the wheel-derived image installation. Assemble a candidate-independent control bundle containing the external launcher, launch lock, and a separately generated exact lock-digest file; mount all three read-only at their fixed paths. `tests/fixtures/v4_publication_floor_worker.py` must scrub the shared checkout from `sys.path` and orchestrate the real Compose worker image against authentic retained workflows for both reasons; the worker must load only the installation attested by the retained wheel and `RECORD`.

Add `test_floor_worker_executes_both_retained_reasons_and_second_verifier_passes`. It must:

1. create one authentic committed hold for each exact reason and capture full canonical snapshots;
2. run the existing verifier and retain both IDs;
3. stop/drain the current fixture worker;
4. invoke the external launcher in `rollback-restart` mode with the retained holds' exact workspace ID plus the exact expired-receipt and published-during-cancel operation IDs, then run the wheel-derived Compose worker image against both retained workflows;
5. receive typed held outcomes with zero provider submit/status calls;
6. prove exactly one unchanged hold per operation and byte-identical liabilities;
7. rerun the verifier and obtain the same IDs;
8. pass the actual A4, unknown, unpinned, unrelated-history, and pre-floor descriptors through the external launcher and prove no worker process starts;
9. pair the valid floor lock with the actual A4 interpreter/runtime/module path and prove the closed launcher refuses it before process start;
10. present a self-generated lock and matching candidate-supplied digest while the independent digest file remains unchanged, and prove the trust-root mismatch refuses it;
11. inspect the running image's retained wheel, installed `RECORD`, every recorded file, `__file__` paths, and interpreter; prove all match the authenticated lock and that no source-tree path is loaded.

The old direct A4 execution remains an expected-negative reproduction showing why A4 is prohibited; do not rename it as a successful rollback. Capture the exact commit, ancestry result, one-time wheel build command, Docker/Compose image ID and build command, retained-wheel digest, installed `RECORD` and complete file attestation, source manifest, runtime imports, workspace/operation/hold IDs, rollback-restart launcher argv, verifier JSON, before/after snapshot hashes, cross-workspace denial, gate denials, worker logs, and effects-disabled environment under ignored `artifacts/v4-next-p1/agents/task-10-floor-qualification/`.

Run the named test from the clean qualification environment, then repeat Task 10's full focused matrix. Expected: actual installed artifact PASS for both reasons; every unsafe descriptor denied; A4 negative reproduction remains preserved.

- [ ] **Step 7: Commit the artifact-qualification harness and test**

Stage only the exact generated deployment lock, `tests/fixtures/v4_publication_floor_worker.py`, `tests/integration/test_v4_publication_recovery_floor.py`, and the named narrow hold-test helper if Step 6 changed it. Inspect the staged diff and commit:

```bash
git add \
  deployment/v4/publication-worker-launch-lock.json \
  tests/fixtures/v4_publication_floor_worker.py \
  tests/integration/test_v4_publication_recovery_floor.py \
  tests/integration/test_v4_publication_recovery_holds.py
git diff --cached --name-only
git diff --cached
git commit -m "test(v4): qualify publication floor against retained holds"
```

The tested application pin remains the Step 5 commit. The Step 7 test-only commit must not change any runtime source included in that artifact; if it does, return to Step 5 and build a new candidate.

- [ ] **Step 8: Record the candidate pin and qualification evidence**

Update `docs/v4/publication-recovery-hold-evidence.json` and current active status/coverage files with the exact Step 5 application SHA, wheel/source/import pins, qualification artifact hashes, both-reason results, and the explicit boundary that this is a disposable effects-disabled local fixture. Correct the two stale `next_action` fields identified by Task 10 review. Keep production rollback unqualified, RG0/RG1 held, P2–P7 gated, P6 disabled, and `main` unmerged.

Run Task 9's documentation/JSON/hash/Mermaid/compile/whitespace checks and commit only the reviewed evidence/status paths:

```bash
git add \
  README.md \
  docs/README.md \
  docs/implementation-progress.md \
  docs/limitations.md \
  docs/memory.md \
  docs/v4/IMPLEMENTATION-BLUEPRINT.md \
  docs/v4/progress.md \
  docs/v4/validation.md \
  docs/v4/p1-qualification-matrix.json \
  docs/v4/p0-release.json \
  docs/v4/documentation-status-audit.json \
  docs/v4/evidence-lock.json \
  docs/v4/publication-recovery-hold-evidence.json
git diff --cached --name-only
git diff --cached
git commit -m "docs(v4): record publication recovery floor candidate"
```

Do not call the pin the supported floor until Task 11's independent review, meta-review, final artifact rerun, and exact-head checks pass.

### Task 11: Independent review, final verification, push, and exact-head CI

**Files:**
- Modify only for an approved, reproduced reviewer finding.
- Create ignored transparent reports under `artifacts/v4-next-p1/agents/` and retain their hashes/verdicts in tracked evidence.

**Interfaces:**
- Consumes: exact application/documentation candidate SHAs and Task 10 artifact/source/import pins.
- Produces: independently reviewed and qualified rollback floor plus a CI-verified feature head; no main merge.

- [ ] **Step 1: Request independent read-only review**

Dispatch GPT-6.1-Sol read-only against the exact candidate and Task 10 artifacts, covering the parallel fold, snapshot alias/no-projection repairs, schema/trigger, both concurrency/crash matrices, both activity paths, launch gate, exact artifact/import pinning, actual floor execution, verifier, migration preservation, revocation, liabilities, and status/evidence accuracy.

Expected: prioritized findings or explicit no-findings verdict; tracked files unchanged.

- [ ] **Step 2: Run GPT-5.6-Sol meta-review before any reviewer-driven edit**

Validate every finding against source/tests/artifacts and return the result to the user. Routine fixes already inside the approved design may proceed under the user's simplified-approval preference; any scope expansion, production effect, destructive migration, main merge, changed floor policy, or disputed reviewer change returns to the user.

- [ ] **Step 3: Apply accepted findings test-first and requalify the artifact**

For each accepted defect, reproduce RED and repair narrowly. Any application change creates a new candidate: rebuild/re-pin the wheel and source/import manifests, rerun both-reason execution and both verifier passes, refresh evidence, and request clean rereview/meta-review. If there are no findings, make no edit.

- [ ] **Step 4: Run final completion verification**

Rerun Task 8's exact focused command, Task 9's exact full non-live/documentation commands, Task 10's per-reason concurrency/crash and floor suite, the named migration preservation cases, the offline verifier, and the exact installed-artifact rehearsal.

Expected: all PASS; the same reviewed application pin is qualified; production remains disabled; RG0/RG1 held; main unmerged.

- [ ] **Step 5: Commit the exact locally reviewed candidate evidence**

Record the clean review/meta-review and final rerun hashes. Label the exact application SHA and its artifact/source/import/launcher/lock pins **review-qualified local fixture rollback floor candidate, pending exact-candidate CI**. Run documentation checks, stage the exact changed evidence paths, inspect the staged path list and diff, and commit them. Do not call the pin supported yet.

```bash
git add \
  README.md \
  docs/README.md \
  docs/implementation-progress.md \
  docs/limitations.md \
  docs/memory.md \
  docs/v4/IMPLEMENTATION-BLUEPRINT.md \
  docs/v4/progress.md \
  docs/v4/validation.md \
  docs/v4/p1-qualification-matrix.json \
  docs/v4/p0-release.json \
  docs/v4/documentation-status-audit.json \
  docs/v4/evidence-lock.json \
  docs/v4/publication-recovery-hold-evidence.json
git diff --cached --name-only
git diff --cached
git commit -m "docs(v4): record reviewed publication floor candidate"
```

- [ ] **Step 6: Push the feature branch**

```bash
git push origin HEAD:feat/archv4-p0-foundation
```

Expected: remote feature ref equals local candidate SHA. Do not push or merge `main`.

- [ ] **Step 7: Verify exact-candidate CI and protection**

Locate the workflow run whose `headSha` exactly equals the pushed candidate. Require both `verify` and `p0-regression` jobs to finish successfully. Capture run/job IDs, URLs, conclusions, and remote SHA for the promotion evidence; do not promote on a partial, stale-head, or rerun from another SHA.

- [ ] **Step 8: Promote the CI-qualified pin and push the evidence commit**

Add the Step 7 run/job evidence and change the exact pin label to supported **local fixture rollback floor** only when the application SHA, launcher/lock, artifact/source/import pins, both-reason qualification, review/meta-review, local reruns, and exact-candidate CI all match. Keep production rollback unqualified. Run the documentation suite, stage only the exact evidence/status paths from Step 5, inspect the staged path list/diff, commit `docs(v4): promote local publication rollback floor`, and push to `origin/feat/archv4-p0-foundation`.

```bash
git add \
  README.md \
  docs/README.md \
  docs/implementation-progress.md \
  docs/limitations.md \
  docs/memory.md \
  docs/v4/IMPLEMENTATION-BLUEPRINT.md \
  docs/v4/progress.md \
  docs/v4/validation.md \
  docs/v4/p1-qualification-matrix.json \
  docs/v4/p0-release.json \
  docs/v4/documentation-status-audit.json \
  docs/v4/evidence-lock.json \
  docs/v4/publication-recovery-hold-evidence.json
git diff --cached --name-only
git diff --cached
git commit -m "docs(v4): promote local publication rollback floor"
git push origin HEAD:feat/archv4-p0-foundation
```

- [ ] **Step 9: Verify CI for the final evidence head**

Locate the new workflow run whose `headSha` exactly equals the pushed Step 8 evidence commit and require both `verify` and `p0-regression` to succeed. The durable repository evidence names the immediately preceding exact application-bearing candidate CI; GitHub's immutable check run is the durable exact-head proof for this final docs-only promotion commit. Do not create another tracked CI-only commit.

- [ ] **Step 10: Completion audit**

Map every spec requirement to current code, tests, migration, review, artifact pin, durable evidence, remote SHA, and exact-head CI. Confirm no worktree contains unmerged descendant work, migration `0042` and all holds remain, production effects are disabled, RG0/RG1 are held, later phases remain gated, and `main` is unchanged.
