# Phase 9 Independent-Review Repairs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close every critical and important governed-publication review finding before integrating Phase 9.

**Architecture:** Add an explicit, current publication approval separate from creative approval; hydrate each schedule from one canonical request/plan/budget record; and repeat database-backed authorization in the submission activity. The YouTube edge requires an injected durable session-store contract, while canonical DTOs retain no opaque session URI.

**Tech Stack:** Python 3.13, PostgreSQL 17, Psycopg 3.3, Alembic, Temporal 1.32, Pydantic 2.12, HTTPX 0.28, pytest.

**Spec:** `docs/superpowers/specs/2026-09-13-phase-7-8-release-gate-phase-9-publishing-design.md`

## Global Constraints

- Add revision `0010_publication_hardening` (the canonical Alembic version column is `VARCHAR(32)`), retain additive `0011_publication_plan_lifecycle` for bounded lifecycle transitions, and use additive `0012_publication_profile_scope` for account-scoped capability identity; never modify a migration that may already be applied.
- A `ReadyToPublishPackage` approval is not a publish approval. New requests require an approved `approval_requests` row with `effect_type = 'publication'` and exact package/account context.
- Missing policy references, package assets, or asset-rights links deny authorization.
- A scheduled Temporal payload names a canonical publication schedule only; all effect fields load from PostgreSQL.
- Reauthorization runs immediately before the first `adapter.submit` call.
- Raw OAuth values, resumable session locations, and media bytes never enter canonical records, logs, checkpoints, DTOs, or API output.
- A non-fixture YouTube adapter has no process-local session-store default.

---

### Task 1: Add canonical approval, budget, and immutability schema invariants

**Files:**
- Create: `migrations/versions/0010_publication_governance_hardening.py` (revision `0010_publication_hardening`)
- Modify: `src/salience/db/models.py`
- Modify: `tests/integration/test_publication_migrations.py`

**Interfaces:** Adds `publication_requests.publication_approval_request_id` and `publication_schedules.budget_id`. Plan, schedule, and attempt decision identities are immutable; the additive lifecycle trigger permits only the explicit, bounded plan cost/effect transitions needed for durable reservation and settlement.

- [x] **Step 1: Write failing direct-SQL tests**

```python
def test_publication_hardening_columns_exist(connection):
    assert _column_names(connection, "publication_requests") >= {"publication_approval_request_id"}
    assert _column_names(connection, "publication_schedules") >= {"budget_id"}

def test_publication_plan_rejects_changed_publisher(connection, persisted):
    with pytest.raises(psycopg.errors.RaiseException, match="immutable publication plan"):
        connection.execute("UPDATE publication_plans SET publisher_id = 'other' WHERE id = %s", (persisted.plan_id,))
```

- [x] **Step 2: Confirm the tests fail**

Run: `PYTHONPATH=src .venv/bin/pytest tests/integration/test_publication_migrations.py -q`

Expected: failure because revision `0010` is absent.

- [x] **Step 3: Implement the additive migration**

```sql
ALTER TABLE publication_requests ADD COLUMN publication_approval_request_id UUID REFERENCES approval_requests(id) ON DELETE RESTRICT;
ALTER TABLE publication_schedules ADD COLUMN budget_id UUID REFERENCES budgets(id) ON DELETE RESTRICT;
```

Install a dedicated trigger that rejects changes/deletions to plan, schedule, and attempt decision fields, except a single null-to-value operational binding for a plan external effect or reservation.

- [x] **Step 4: Verify and checkpoint**

Run: `PYTHONPATH=src .venv/bin/pytest tests/integration/test_publication_migrations.py -q && .venv/bin/python -m compileall -q src && git diff --check`

Expected: PASS. Commit: `feat: harden publication governance schema`.

### Task 2: Bind request and schedule execution to current canonical authority

**Files:**
- Modify: `src/salience/publication/contracts.py`
- Modify: `src/salience/publication/repository.py`
- Modify: `src/salience/api/schemas.py`
- Modify: `src/salience/api/dependencies.py`
- Modify: `src/salience/api/routes/publication.py`
- Modify: `tests/integration/test_publication_repository.py`
- Modify: `tests/integration/test_publication_control_api.py`
- Modify: `tests/integration/test_creative_release_gate_migration.py`

**Interfaces:** `PublicationRequest` and `PublicationWorkflowRequest` carry `publication_approval_request_id`. `load_scheduled_execution(schedule_id)` returns one active request, plan, and budget from a canonical schedule.

- [x] **Step 1: Write failing authority tests**

```python
async def test_request_rejects_creative_or_unbound_publish_approval(repository, ready, account):
    with pytest.raises(KeyError, match="publication approval"):
        await repository.create_request(..., publication_approval_request_id=ready["creative_approval_id"])

async def test_current_authorization_denies_empty_policy_and_missing_rights(repository, request):
    current = await repository.load_current_authorization(request.id)
    assert current.policy_allowed is False
    assert current.rights_allowed is False
```

- [x] **Step 2: Confirm the focused suite fails**

Run: `PYTHONPATH=src .venv/bin/pytest tests/integration/test_publication_repository.py tests/integration/test_publication_control_api.py -q`

Expected: failure because the request currently inherits ready-package approval and empty proof sets allow publication.

- [x] **Step 3: Implement exact approval/proof/schedule bindings**

```python
async def load_scheduled_execution(self, publication_schedule_id: str) -> PersistedPublicationExecution:
    """Load one active canonical schedule, its immutable request/plan, and its bound budget."""
```

Require an active workspace policy and a valid rights link for every distributed asset. Persist and exact-match the schedule budget in both `job_schedules.payload` and `publication_schedules`. Extend fixture ready packages with active policy, active asset license link, and a separate approved publication approval whose JSON context names the package/account.

- [x] **Step 4: Verify and checkpoint**

Run: `PYTHONPATH=src .venv/bin/pytest tests/integration/test_publication_repository.py tests/integration/test_publication_control_api.py tests/e2e/test_phase9_governed_publishing.py -q && .venv/bin/python -m compileall -q src && git diff --check`

Expected: PASS. Commit: `feat: bind governed publication authority`.

### Task 3: Hydrate schedules canonically and reauthorize at external-write time

**Files:**
- Modify: `src/salience/workflows/publication.py`
- Modify: `src/salience/api/dependencies.py`
- Modify: `tests/unit/test_publication_schedule.py`
- Modify: `tests/e2e/test_phase9_governed_publishing.py`
- Modify: `tests/e2e/test_phase9_publication_recovery.py`

**Interfaces:** Scheduled workflow payloads contain `scheduled_publication_schedule_id` only. The request activity hydrates a canonical normal workflow payload. The submission activity reuses `_reauthorize_current(request, plan_id, budget_id, adapter)` immediately before a first effect submission.

- [x] **Step 1: Write failing schedule and revocation-race tests**

```python
def test_schedule_payload_contains_only_its_canonical_identity(request):
    assert _workflow_request_payload(request) == {
        "contract_version": "PublicationWorkflowRequest@v1",
        "scheduled_publication_schedule_id": request.scheduled_publication_schedule_id,
    }

async def test_connection_revoked_after_delivery_denies_before_submit(workflow):
    workflow.state.before_submit_hook = revoke_connection
    assert (await workflow.run()).publication_state == "denied"
    assert workflow.provider.submit_count == 0
```

- [x] **Step 2: Confirm the workflow suite fails**

Run: `PYTHONPATH=src .venv/bin/pytest tests/unit/test_publication_schedule.py tests/e2e/test_phase9_governed_publishing.py tests/e2e/test_phase9_publication_recovery.py -q`

Expected: failure because schedule payload fields and final submit are currently trusted without a same-activity recheck.

- [x] **Step 3: Implement hydration and final authorization**

```python
if workflow_request.scheduled_publication_schedule_id:
    execution = await repository.load_scheduled_execution(workflow_request.scheduled_publication_schedule_id)
    effective_request = PublicationWorkflowRequest.from_persisted_execution(execution)

await self._reauthorize_current(request, plan_id, budget_id, adapter)
submitting = await self._state.store.begin_effect_submission(run, effect_key)
```

The schedule identity is the sole trusted scheduled input. A test-only hook runs before the final check to prove a current connection revocation prevents `adapter.submit`; post-acceptance reconciliation remains duplicate-safe.

- [x] **Step 4: Verify and checkpoint**

Run: `PYTHONPATH=src .venv/bin/pytest tests/unit/test_publication_schedule.py tests/e2e/test_phase9_governed_publishing.py tests/e2e/test_phase9_publication_recovery.py -q && .venv/bin/python -m compileall -q src && git diff --check`

Expected: PASS. Commit: `fix: close governed publication write race`.

### Task 4: Require durable YouTube edge storage and close the release gate

**Files:**
- Modify: `src/salience/publication/youtube.py`
- Modify: `tests/integration/test_youtube_publisher_contract.py`
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/governance.md`
- Modify: `docs/database.md`
- Modify: `docs/limitations.md`
- Modify: `docs/phase-9-handoff.md`
- Modify: `docs/verification.md`
- Modify: `docs/implementation-progress.md`
- Modify: `docs/superpowers/plans/2026-09-13-phase-7-8-release-gate-phase-9-publishing.md`
- Create: `docs/superpowers/reviews/2026-09-13-phase-9-independent-review.md`

**Interfaces:** `YouTubePublisherAdapter` rejects absent/nondurable stores. `DurableYouTubeSessionStore` is edge-only and retains opaque locations; canonical DTOs retain only a hash identity.

- [x] **Step 1: Write failing durable-store tests**

```python
def test_youtube_adapter_requires_explicit_durable_store(client):
    with pytest.raises(ValueError, match="durable edge session store"):
        YouTubePublisherAdapter(client=client, connection_reference="secret-ref")
```

- [x] **Step 2: Confirm the contract fails**

Run: `PYTHONPATH=src .venv/bin/pytest tests/integration/test_youtube_publisher_contract.py -q`

Expected: failure because a process-local store is silently created.

- [x] **Step 3: Implement the durable-store contract and truthful docs**

```python
@runtime_checkable
class DurableYouTubeSessionStore(Protocol):
    @property
    def is_durable(self) -> bool: ...
```

Reject missing/nondurable stores. Keep the in-memory store test-only with `is_durable = False`, update docs to describe session-start/reconciliation but not byte streaming or completed video publishing, and commit the original/repaired review evidence.

- [ ] **Step 4: Complete verification and independent review**

Run: `bash scripts/verify-phase-9.sh && PYTHONPATH=src .venv/bin/pytest tests/contracts tests/unit tests/integration tests/e2e tests/evals -m 'not live' -q && .venv/bin/python -m compileall -q src && git diff --check`

Expected: all fixture checks pass, live status is `NOT RUN` without explicit private configuration, and a fresh review reports no critical/important finding. Commit: `docs: complete governed publishing phase`.

Current evidence: executable verification is green, but the fresh-review portion
is externally blocked. Two reviewer requests failed with the reviewer service's
usage limit; no clean independent-review result is claimed. The local audit and
the exact remaining limitation are recorded in
`docs/superpowers/reviews/2026-09-13-phase-9-independent-review.md`.

### Task 5: Bind approval, policy, rights, connection, and capability scope

**Files:**
- Create: `migrations/versions/0012_publication_profile_scope.py`
- Modify: `src/salience/publication/repository.py`
- Modify: `src/salience/workflows/publication.py`
- Modify: `tests/integration/test_creative_release_gate_migration.py`
- Modify: `tests/integration/test_publication_repository.py`
- Modify: `tests/e2e/test_phase9_governed_publishing.py`

**Interfaces:** A publication approval names the exact package, account, destination, locale, territory, visibility, and capability-profile revision. Active referenced policy versions must independently contain the same publication scope. Every distributed asset must have a current, commercial, channel-and-territory-permitting consent or license path. The selected immutable capability profile is account-scoped, verified, and unexpired; the current connection must be unrevoked and unexpired.

- [x] **Step 1: Write failing scope and current-fact tests**
- [x] **Step 2: Confirm the new tests fail because the current request supplies its own authority facts**
- [x] **Step 3: Add additive profile-identity migration and fail-closed scope evaluation**
- [x] **Step 4: Verify focused persistence/workflow scope coverage and checkpoint**

### Task 6: Materialize canonical jobs for scheduler-fired executions

**Files:**
- Modify: `src/salience/workflows/persistence.py`
- Modify: `src/salience/workflows/publication.py`
- Modify: `src/salience/publication/repository.py`
- Modify: `tests/integration/test_publication_repository.py`
- Modify: `tests/e2e/test_phase9_governed_publishing.py`

**Interfaces:** A `job_schedules` payload must exact-match the canonical request, plan, budget, version, and contract. A scheduler fire creates or retrieves one canonical run using Temporal's execution run ID as its unique idempotency identity before the first publication activity accesses a run.

- [x] **Step 1: Write failing payload-mismatch and unseeded scheduler-fire tests**
- [x] **Step 2: Confirm failures identify absent payload validation and canonical run creation**
- [x] **Step 3: Implement exact schedule matching and scheduler-run materialization**
- [x] **Step 4: Verify real Temporal schedule trigger and recovery coverage**

### Task 7: Validate normal starts before creating durable jobs

**Files:**
- Modify: `src/salience/api/dependencies.py`
- Modify: `tests/integration/test_publication_control_api.py`

**Interfaces:** Production control validates the canonical publication request before it creates a running job or starts Temporal; an invalid approval cannot leave an orphaned nonterminal job.

- [x] **Step 1: Write a failing invalid-start persistence test**
- [x] **Step 2: Confirm the test exposes a prematurely created job**
- [x] **Step 3: Prevalidate through the canonical repository and verify idempotent normal start**
- [x] **Step 4: Run focused control coverage and checkpoint**

### Task 8: Re-certify the repaired gate

- [x] **Step 1: Update all affected docs, progress evidence, and the Phase 1–9 architecture visual**
- [x] **Step 2: Run the complete Phase 9 verifier, standalone non-live suite, compilation, whitespace, Archify delivery, and Chromium visual inspection**
- [ ] **Step 3: Request a fresh independent review; repair every critical or important finding and repeat this task until clear**
- [ ] **Step 4: Commit only verified Phase 9 code/docs and integrate only after a clean worktree audit**

Task 8 Step 3 is blocked by the external reviewer service usage limit after two
requests. Chromium visual inspection was attempted and reported `NOT RUN`
because no Chrome/Chromium executable exists on this host; deterministic
Archify validation remains green.

## Plan Self-Review

- **Spec coverage:** Tasks 1–4 address all three critical and all three important independent-review findings.
- **Placeholder scan:** Every task contains explicit paths, red tests, commands, implementation rules, and evidence.
- **Type consistency:** Approval flows from control request to canonical request; schedule identity flows from canonical schedule to Temporal payload; bound budget flows from schedule to final authorization.

## Execution Handoff

Execute Task 1 inline using `superpowers:executing-plans`. Do not merge or push while any critical or important review finding remains open.
