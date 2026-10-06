# P1 Publication Recovery Hold Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct parallel-team parent status and add one durable, immutable, non-resumable publication recovery hold that preserves the original receipt and liabilities under both qualified hold reasons.

**Architecture:** Keep the existing cycle/publication architecture and signed inspection route. Add an append-only `0042` sidecar plus a focused `publication_recovery` module that derives all hold facts from canonical rows, serializes by operation, stores an immutable liability snapshot, and supports a pre-restart rollback verifier. Publication activities record the hold before raising the existing typed failure; no new effect, recovery command, endpoint, queue, or service is introduced.

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
- Before prior-code restart, verify one retained hold for each reason and unchanged receipt/effect/job/reservation/cycle/event/output/outbox liabilities.
- For every unchanged-liability assertion, capture and compare the exact receipt row, canonical effect row, job row, reservation row, cycle row, ordered publication/status-event rows, output row or absence, and ordered outbox rows before and after; the recovery-hold row itself is the only allowed addition.
- Use `apply_patch` for manual edits. Keep the unrelated pre-existing `.gitignore` `/temp/` change out of commits.

## Review Focus

- Two processes insert the same exact hold concurrently: `test_concurrent_exact_hold_insert_returns_one_stable_hold` in Task 3 proves one row/ID and no liability change.
- The process dies after commit but before returning the typed failure: `test_hold_commit_then_process_death_replays_without_liability_change` in Task 6 proves exact recovery.
- A second reason or changed receipt is presented for an already-held operation: `test_hold_replay_conflicting_reason_or_evidence_denies` in Task 3 proves fail-closed conflict.
- Revoked or stale authority tries to turn readback into renewed execution: `test_expired_receipt_hold_requires_current_reconcile_scope_and_never_resubmits` in Task 4 proves separate read authority and zero new effect.
- Rollback is attempted with one reason missing or liabilities changed: `test_rollback_verifier_requires_both_reasons_and_unchanged_liabilities` in Task 7 blocks restart.

## Planned file responsibilities

- `src/salience/agents/parallel_store.py`: derive the parent team status from child progress without changing child states or schema.
- `src/salience/cycles/publication_recovery.py`: typed hold projection; serialized create/exact-replay; read projection; immutable liability snapshot; both-reason rollback verification.
- `src/salience/cycles/legacy_publication_runtime.py`: invoke hold creation at the two existing typed-failure boundaries and include the hold ID in safe Temporal failure details.
- `src/salience/cycles/legacy_dispatch.py`: add the safe `recovery_hold` member to existing run inspection.
- `migrations/versions/0042_legacy_publication_recovery_holds.py`: additive table, reason/state/fingerprint/scope constraints, insert validation, immutability, narrow grants, and preservation-safe downgrade.
- `scripts/verify-v4-publication-recovery-rollback.py`: offline pre-restart verifier; no HTTP or mutation capability.
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

- [ ] **Step 1: Write deterministic failing status tests**

Add `test_started_team_with_completed_or_unknown_child_and_queued_siblings_is_running`. Use `max_concurrency=1`; assert all-queued is `queued`, succeeded-plus-queued is `running`, unknown-plus-queued is `running`, the parent `agent_runs.state` remains `delegating`, and no `team.finished` event exists. Extend the existing `test_process_death_keeps_completed_and_unknown_and_resumes_queued` path to kill the worker, observe `running` while unknown and queued siblings coexist, reclaim only queued siblings with their original run IDs, and reach the existing terminal aggregate. Extend the signed interface case to assert the same store status passes through API, SDK, and CLI output.

- [ ] **Step 2: Prove the red behavior**

Run: `.venv/bin/pytest -q tests/integration/test_durable_parallel_teams.py -k 'started_team or process_death_keeps_completed_and_unknown_and_resumes_queued or signed_api_sdk_cli'`

Expected: FAIL because the parent becomes `queued` after the active child leaves `running`.

- [ ] **Step 3: Implement the status fold**

In `_inspect`, make `queued` require `all(state == "queued")`; return `running` whenever any child is queued/running after any child has progressed; preserve terminal precedence and the ordered task projection.

- [ ] **Step 4: Verify the focused parallel suite**

Run: `.venv/bin/pytest -q tests/unit/test_parallel_agent_teams.py tests/integration/test_durable_parallel_teams.py`

Expected: all tests PASS; no migration change.

- [ ] **Step 5: Commit**

```bash
git add src/salience/agents/parallel_store.py tests/integration/test_durable_parallel_teams.py
git commit -m "fix(agents): preserve progressed parallel team status"
```

### Task 2: Add the immutable recovery-hold schema

**Files:**
- Create: `migrations/versions/0042_legacy_publication_recovery_holds.py`
- Create: `tests/integration/test_v4_publication_recovery_holds.py`

**Interfaces:**
- Consumes: `v4_legacy_dispatches`, `v4_legacy_publication_fixture_receipts`, `v4_legacy_publication_fixture_progress`, `v4_permit_claims`, `v4_dispatch_permits`, publication attempt/request, job, context, cycle, effect, reservation, event, and outbox rows.
- Produces: `v4_legacy_publication_recovery_holds` keyed uniquely by `operation_id` and protected from update/delete.

- [ ] **Step 1: Write red migration and direct-SQL tests**

Add tests that upgrade `0041 -> 0042`, inspect exact columns/constraints/revision, reject cross-workspace/cross-operation/fingerprint/reason/state mismatches, reject update/delete, pass empty downgrade/re-upgrade, and refuse populated downgrade without changing `alembic_version` or deleting the row. Use two direct SQL transactions racing the same `operation_id` to prove the database admits one row only. Assert `PUBLIC` has no table access. Following the repository's restricted-role convention, create a per-test fixture-runtime role during test/deployment provisioning, grant it only schema `USAGE` plus table `SELECT`/`INSERT`, and prove it lacks `UPDATE`/`DELETE`/`TRUNCATE`; the migration does not invent or discover a named application role.

- [ ] **Step 2: Prove the migration is absent**

Run: `.venv/bin/pytest -q tests/integration/test_v4_publication_recovery_holds.py -k 'migration or direct_sql'`

Expected: FAIL because revision `0042_publication_recovery_holds` and its table do not exist.

- [ ] **Step 3: Implement revision `0042_publication_recovery_holds`**

Create an additive table with stable `id`, unique `operation_id`, all spec identities, `publication_attempt_id`, `permit_id`, `owner_id`, frozen execution deadline, permit expiry, request/receipt/liability fingerprints, remote identity/state, reason, fixed `held` state, immutable liability snapshot, and timestamps. Add composite scope FKs where current schema permits them, an insert trigger that derives/compares the original canonical rows and reason-specific state, an update/delete rejection trigger, explicit revocation from `PUBLIC`, a three-second downgrade lock, and populated downgrade refusal. Runtime role provisioning owns the explicit per-principal `SELECT`/`INSERT` grants.

- [ ] **Step 4: Verify schema and preservation behavior**

Run: `.venv/bin/pytest -q tests/integration/test_v4_publication_recovery_holds.py -k 'migration or direct_sql'`

Expected: PASS for valid insert, one-row concurrent uniqueness, narrow role privileges, hostile SQL denial, empty rollback, and populated preservation.

- [ ] **Step 5: Commit**

```bash
git add migrations/versions/0042_legacy_publication_recovery_holds.py tests/integration/test_v4_publication_recovery_holds.py
git commit -m "feat(v4): add immutable publication recovery holds"
```

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

- [ ] **Step 1: Write red helper tests**

Add `test_exact_hold_replay_returns_stable_id`, `test_hold_replay_conflicting_reason_or_evidence_denies`, and `test_concurrent_exact_hold_insert_returns_one_stable_hold`. Assert an advisory-lock/unique-key race yields one row and identical views. Compare the exact receipt, effect, job, reservation, cycle, ordered status events, output/absence, and ordered outbox rows before and after; only the one hold row may be added.

- [ ] **Step 2: Prove the helper is absent**

Run: `.venv/bin/pytest -q tests/integration/test_v4_publication_recovery_holds.py -k 'exact_hold or conflicting_reason or concurrent_exact'`

Expected: collection/import FAIL for the missing module or functions.

- [ ] **Step 3: Implement typed projection and record/load functions**

Derive all identities, fingerprints, current remote progress, permit dates, and liability snapshot inside one PostgreSQL transaction. Serialize with an operation-scoped advisory transaction lock. Return the stored view and stable ID only after every immutable binding, reason, fingerprint, and evidence field matches. Raise `PublicationRecoveryConflictError` for any differing replay and `PermissionError` when required evidence/authority is unavailable. Do not accept caller-supplied authority, fingerprints, liability, owner, or deadline.

- [ ] **Step 4: Verify idempotency, concurrency, and conflict**

Run: `.venv/bin/pytest -q tests/integration/test_v4_publication_recovery_holds.py -k 'exact_hold or conflicting_reason or concurrent_exact'`

Expected: PASS with one stable hold and unchanged liabilities.

- [ ] **Step 5: Commit**

```bash
git add src/salience/cycles/publication_recovery.py tests/integration/test_v4_publication_recovery_holds.py
git commit -m "feat(v4): record publication recovery holds"
```

### Task 4: Persist expired-permit receipt recovery holds

**Files:**
- Modify: `src/salience/cycles/legacy_publication_runtime.py`
- Modify: `tests/integration/test_v4_legacy_publication.py`

**Interfaces:**
- Consumes: `record_publication_recovery_hold(..., reason="expired_permit_receipt")` from Task 3.
- Produces: existing nonretryable `LegacyPublicationRecoveryHeld` with safe `hold_id` details.

- [ ] **Step 1: Extend the expired-permit test red**

Add `test_expired_receipt_hold_requires_current_reconcile_scope_and_never_resubmits` assertions: one typed row, matching stable ID in failure details, one durable accepted receipt, no second submit/status progress, and unchanged canonical liabilities. Cover missing receipt, altered authorization, and revoked reconcile grant creating no misleading hold. Task 6 owns the signed inspection assertion after that projection exists.

- [ ] **Step 2: Prove no hold is persisted**

Run the named test with `.venv/bin/pytest -q tests/integration/test_v4_legacy_publication.py::test_expired_receipt_hold_requires_current_reconcile_scope_and_never_resubmits`

Expected: FAIL on missing hold/failure details while existing fail-closed behavior remains.

- [ ] **Step 3: Record the hold at the validated boundary**

After `_expired_permit_receipt_recovery` has validated the exact effect, receipt, current publication facts, and separate reconciliation grant, record/exact-replay the hold before raising the typed failure. Do not move provider readback earlier or add status polling.

- [ ] **Step 4: Verify expired-permit recovery**

Run the named test again plus `test_publication_revoked_reconcile_scope_holds_durable_acceptance`.

Expected: PASS; revoked reconciliation creates no new hold/effect, exact recovery creates one hold and no resubmit.

- [ ] **Step 5: Commit**

```bash
git add src/salience/cycles/legacy_publication_runtime.py tests/integration/test_v4_legacy_publication.py
git commit -m "feat(v4): hold expired publication receipt recovery durably"
```

### Task 5: Persist post-close published-cancellation holds

**Files:**
- Modify: `src/salience/cycles/legacy_publication_runtime.py`
- Modify: `tests/integration/test_v4_legacy_publication.py`

**Interfaces:**
- Consumes: `record_publication_recovery_hold(..., reason="published_during_cancel")` from Task 3.
- Produces: the same typed failure with stable hold ID, before base cancellation writes.

- [ ] **Step 1: Extend the direct cancellation regression red**

Update `test_post_close_cancel_reads_published_then_holds_original_liability` to require one `published_during_cancel` row and stable failure detail while retaining all existing zero-terminal-write and unchanged-liability assertions.

- [ ] **Step 2: Prove the new record is missing**

Run: `.venv/bin/python -m pytest -q tests/integration/test_v4_legacy_publication.py::test_post_close_cancel_reads_published_then_holds_original_liability`

Expected: FAIL only on recovery-hold assertions.

- [ ] **Step 3: Record before the existing typed failure**

When the exact cancellation readback is `published`, call the Task 3 recorder before raising. Do not call the base cancellation projector or change the confirmed-cancelled branch.

- [ ] **Step 4: Verify both cancellation branches**

Run: `.venv/bin/pytest -q tests/integration/test_v4_legacy_publication.py -k 'post_close_cancel or cancellation'`

Expected: PASS; published holds, confirmed cancelled retains its existing projection.

- [ ] **Step 5: Commit**

```bash
git add src/salience/cycles/legacy_publication_runtime.py tests/integration/test_v4_legacy_publication.py
git commit -m "feat(v4): hold published cancellation readback durably"
```

### Task 6: Prove crash-after-commit recovery and inspection parity

**Files:**
- Modify: `src/salience/cycles/legacy_dispatch.py`
- Modify: `tests/integration/test_v4_publication_recovery_holds.py`
- Modify: `tests/integration/test_v4_legacy_publication.py`
- Test only: `src/salience/sdk/client.py`, `src/salience/cli.py`

**Interfaces:**
- Consumes: `load_publication_recovery_hold` from Task 3.
- Produces: additive `recovery_hold: dict | None` in the existing run inspection response.

- [ ] **Step 1: Write red crash and inspection tests**

Add `test_hold_commit_then_process_death_replays_without_liability_change`: terminate the activity/worker after the hold commit but before its typed failure is acknowledged, restart a compatible worker, and assert one stable row/ID. Compare the exact receipt, effect, job, reservation, cycle, ordered status events, output/absence, and ordered outbox rows before commit, after process death, and after retry. Extend signed route, `LegacyPublicationClient.inspect`, and `content legacy-publication inspect` assertions for both hold reasons, the same safe projection, and absence of any hold mutation command.

- [ ] **Step 2: Prove inspection is missing and crash recovery is incomplete**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/integration/test_v4_publication_recovery_holds.py::test_hold_commit_then_process_death_replays_without_liability_change \
  tests/integration/test_v4_legacy_publication.py::test_signed_publication_routes_sdk_cli_and_safe_default_denial
```

Expected: FAIL on missing `recovery_hold` and stable failure detail; no duplicate effect is allowed even in red.

- [ ] **Step 3: Add the bounded inspection projection**

Join/load by the already scoped operation in `LegacyDispatch.inspect`; return only the model's safe fields or `None`. Keep SDK/CLI passthrough code unchanged unless a failing typed contract proves an additive field declaration is required.

- [ ] **Step 4: Verify crash recovery and all three clients**

Repeat the exact two-test command from Step 2.

Expected: PASS; one hold survives process death, retry reuses it, liabilities remain unchanged, and no mutation route exists.

- [ ] **Step 5: Commit**

```bash
git add src/salience/cycles/legacy_dispatch.py tests/integration/test_v4_publication_recovery_holds.py tests/integration/test_v4_legacy_publication.py
git commit -m "feat(v4): expose immutable publication recovery holds"
```

### Task 7: Add the both-reason pre-restart rollback verifier

**Files:**
- Modify: `src/salience/cycles/publication_recovery.py`
- Create: `scripts/verify-v4-publication-recovery-rollback.py`
- Modify: `tests/integration/test_v4_publication_recovery_holds.py`

**Interfaces:**
- Produces: `verify_publication_recovery_rollback(database_url: str, *, workspace_id: UUID, operation_ids: Mapping[PublicationRecoveryReason, UUID]) -> tuple[PublicationRecoveryHoldView, PublicationRecoveryHoldView]`.
- Produces: offline script arguments `--database-url`, `--workspace-id`, `--expired-operation-id`, `--cancel-operation-id`; exit 0 only after both holds and liabilities match.

- [ ] **Step 1: Write the red rollback test**

Add `test_rollback_verifier_requires_both_reasons_and_unchanged_liabilities`. Assert missing reason, duplicate operation, changed reason/evidence, or any changed receipt/effect/job/reservation/cycle/event/output/outbox value raises and yields nonzero script exit. Assert exact retained rows return both stable IDs and exit 0.

- [ ] **Step 2: Prove no verifier exists**

Run: `.venv/bin/python -m pytest -q tests/integration/test_v4_publication_recovery_holds.py::test_rollback_verifier_requires_both_reasons_and_unchanged_liabilities`

Expected: import/script FAIL.

- [ ] **Step 3: Implement read-only verification**

Require the mapping keys to equal both exact reasons and operations to be distinct. Reload each immutable hold and recompute its current liability snapshot/fingerprint in one repeatable-read transaction; compare with stored snapshots. The script prints bounded JSON containing only workspace, reasons, operation IDs, hold IDs, and `verified: true`.

- [ ] **Step 4: Verify the mandatory rollback gate**

Run the named pytest command from Step 2, then invoke the script against the disposable fixture database populated by the rehearsal harness:

```bash
.venv/bin/python scripts/verify-v4-publication-recovery-rollback.py \
  --database-url "$TEST_DATABASE_URL" \
  --workspace-id "$WORKSPACE_ID" \
  --expired-operation-id "$EXPIRED_OPERATION_ID" \
  --cancel-operation-id "$CANCEL_OPERATION_ID"
```

Expected: exact both-reason state PASS; every missing/mutated case blocks prior-code restart.

- [ ] **Step 5: Commit**

```bash
git add src/salience/cycles/publication_recovery.py scripts/verify-v4-publication-recovery-rollback.py tests/integration/test_v4_publication_recovery_holds.py
git commit -m "feat(v4): gate publication hold rollback on both reasons"
```

### Task 8: Run the complete focused application qualification

**Files:**
- Modify only if a focused failure proves an implementation defect in Tasks 1–7.

**Interfaces:**
- Consumes: all implementation and tests above.
- Produces: a clean application commit and captured focused command/JUnit/log/source manifest for evidence.

- [ ] **Step 1: Run migration and focused suites**

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

- [ ] **Step 2: Run rollback rehearsal for both reasons**

Populate one exact hold per reason, execute the offline verifier, stop/drain the compatible fixture worker, re-run the verifier, and exercise the retained prior-code fail-closed path without downgrading `0042`.

Expected: both verification passes report the same hold IDs; all liability snapshots remain identical; prior-code restart is not attempted on a failed verifier.

- [ ] **Step 3: Repair only evidenced failures and rerun affected scope**

Use `superpowers:systematic-debugging` for any failure. Add a regression before changing behavior. Repeat the complete focused command after the last repair.

- [ ] **Step 4: Commit the final application candidate**

If Step 3 produced repairs, stage only the exact reviewed files named by `git diff --name-only`, inspect `git diff --cached`, and commit `feat(v4): qualify durable publication recovery holds`. If no repair was needed, create no empty commit. Record the resulting application HEAD as the qualified application commit.

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

- [ ] **Step 1: Capture actual full non-live and documentation results**

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

- [ ] **Step 2: Update only current status and evidence**

Record completed 2026-10-06 rereview/meta-review, targeted 5/5 rerun, Task 8 focused results, new full results, both-reason rollback proof, application SHA, and source hashes. Correct the five audited stale references, remove the repeated R18 sentence, update all active “rereview pending” checkpoint aliases now made stale, and preserve historical captures verbatim.

- [ ] **Step 3: Preserve requirement and gate accuracy**

Advance only local fixture R18/R32 hold/ownership coverage. Keep production-wide revocation, general policy, routing/live schedule inventory, production recovery workers, R33 resume, R34 external alerts, RG0/RG1, and P2–P7 open/held.

- [ ] **Step 4: Run documentation verification after tracked updates**

Repeat `.venv/bin/python docs/v4/test_checks.py`, `.venv/bin/python docs/v4/check.py --self-test`, `.venv/bin/python docs/v4/check.py`, all four `tests/scripts/*.sh`, JSON parsing for every modified JSON, Mermaid rendering, `.venv/bin/python -m pip check`, `.venv/bin/python -m compileall -q src tests scripts docs/v4`, and `git diff --check`.

Expected: all PASS and every referenced hash resolves to the final tracked content or named ignored artifact.

- [ ] **Step 5: Commit documentation/evidence**

Stage only the exact reviewed paths listed in this task and any current-banner aliases proven stale by the recorded search. Inspect `git diff --cached --name-only` and `git diff --cached` before committing `docs(v4): qualify publication recovery hold increment`; never stage the unrelated `.gitignore` change.

### Task 10: Independent review, final verification, push, and exact-head CI

**Files:**
- Modify only for an approved, reproduced reviewer finding.
- Create ignored transparent reports under `artifacts/v4-next-p1/agents/` and retain their hashes/verdicts in tracked evidence.

**Interfaces:**
- Consumes: exact application and documentation candidate SHAs.
- Produces: reviewed and CI-verified feature head; no main merge.

- [ ] **Step 1: Request independent read-only review**

Dispatch GPT-6.1-Sol read-only against the exact candidate, covering parallel fold, schema/trigger, concurrency, crash-after-commit, both activity paths, inspection, rollback verifier, migration preservation, revocation, liabilities, and status/evidence accuracy.

Expected: prioritized findings or explicit no-findings verdict; tracked files unchanged.

- [ ] **Step 2: Run GPT-5.6-Sol meta-review**

Validate each finding against source/tests. Routine fixes already inside the approved design may proceed under the user's simplified-approval preference; any scope expansion, production effect, destructive migration, main merge, or disputed reviewer change returns to the user.

- [ ] **Step 3: Apply accepted findings test-first and re-review**

For each accepted defect, reproduce RED, repair narrowly, rerun affected focused/full/docs checks, commit, and request clean rereview/meta-review. If there are no findings, make no edit.

- [ ] **Step 4: Run final completion verification**

Rerun Task 8's exact focused command, Task 9's exact full non-live and documentation commands, the named migration upgrade/downgrade preservation cases, `test_rollback_verifier_requires_both_reasons_and_unchanged_liabilities`, and the offline verifier on the exact candidate.

Expected: all PASS; production remains disabled; RG0/RG1 held; main unmerged.

- [ ] **Step 5: Commit any final evidence-only readback**

If final review/verification hashes require a tracked evidence update, commit only those exact files and rerun documentation checks. Otherwise keep the reviewed commit unchanged.

- [ ] **Step 6: Push the feature branch**

```bash
git push origin HEAD:feat/archv4-p0-foundation
```

Expected: remote feature ref equals local candidate SHA. Do not push or merge `main`.

- [ ] **Step 7: Verify exact-candidate CI and protection**

Locate the workflow run whose `headSha` exactly equals the pushed candidate. Require both `verify` and `p0-regression` jobs to finish successfully; record run/job IDs, URLs, conclusions, and remote SHA in durable evidence. If CI changes tracked evidence, create a final evidence commit, push it, and repeat exact-head CI until the remote feature head itself is green.

- [ ] **Step 8: Completion audit**

Map every spec requirement to current code, tests, migration, review, durable evidence, remote SHA, and exact-head CI. Confirm no worktree contains unmerged descendant work, production effects are disabled, RG0/RG1 are held, later phases remain gated, and `main` is unchanged.
