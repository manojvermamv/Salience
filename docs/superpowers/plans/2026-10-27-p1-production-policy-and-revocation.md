# P1 Production Policy and Cross-Service Revocation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fail-closed, primary-database production policy and revocation boundary, with durable production-only permits and claims, while keeping all production effects disabled.

**Architecture:** Load policy, identity, grants, approvals, operation, budgets, account, and stop state from the canonical PostgreSQL primary at every authorization boundary. Add production-only permit storage after migration 0043 and keep the existing fixture permit path unchanged; a route-fence decision is required at issue, claim, and each later effect action. Local tests use disposable PostgreSQL/Temporal fixtures and a route-fence test double; they do not qualify a production target.

**Tech Stack:** Python 3.13, Pydantic, psycopg, PostgreSQL, Alembic, Temporal, pytest.

**Spec:** `docs/superpowers/specs/2026-10-13-p1-production-policy-and-revocation-design.md`

## Global Constraints

- Production remains disabled; RG0/RG1 remain `HELD`; P2 remains `BLOCKED`.
- Read the current canonical authority from one strongly consistent primary. Do not use positive authorization caches, replicas, JWT scopes, Temporal history, or invalidation signals as authority.
- Keep `CycleGovernance`, fixture permit literals, fixture DDL, fixture triggers, fixture permissions, and fixture history behavior unchanged.
- Do not add a policy mirror, duplicate grant table, policy microservice, provider substitution, adaptive routing, or P2 behavior.
- Do not implement worker routing, live schedule changes, recovery workers, external reconciliation, alert delivery, or terminal liability resolution in this slice.
- A permit lasts no more than 60 seconds and no longer than any current approval, policy, identity, grant, goal, route, or execution deadline.
- A committed stop or revocation denies the next serialized authorization boundary. A prior claim remains tied to its original operation and unresolved external outcomes remain liabilities.
- Unknown, malformed, expired, missing, cross-workspace, or hash-mismatched policy/approval/authority data fails closed. Do not backfill missing authority from current state.
- Permit/request snapshots and durable decisions contain no token, credential, secret, raw provider response, or sensitive request text. Resolve a scoped credential lease only after the final current route/policy barrier.
- Persist redacted allow/deny decisions on the canonical event path. Metric labels are limited to bounded action, effect, outcome, and reason values; never use workspace, subject, operation, policy, account, or provider IDs as labels.
- Migration 0044 is additive after `0043_complete_publication_recovery_liabilities`; populated authority history is never downgraded or deleted.
- Production qualification requires a named environment, exact candidate, named accountable owners, and current no-effects evidence. Fixture evidence never transfers to production acceptance.

## Review Focus

- Missing, unknown-key, legacy-shaped, malformed-expiry, or hash-mismatched policy documents must deny before permit issue. Pin this in Task 1 tests `test_policy_document_v1_is_strict_and_hash_bound` and `test_policy_loader_fails_closed_on_unknown_or_legacy_shapes`.
- Revoke-then-regrant must not revive a frozen authority snapshot or issued permit. Pin this in Task 2 test `test_0044_grant_mutation_advances_subject_revision` and Task 3 test `test_revoke_then_regrant_cannot_reuse_old_permit`.
- Stop/revocation versus issue/claim races and duplicate workers must have one durable order and at most one claim. Pin this in Task 2 test `test_0044_production_claim_is_unique_and_mode_separated` and Task 3 test `test_stop_revoke_claim_race_and_duplicate_workers`.
- A route decision that is stale, unavailable, expired, or ordered after owner transfer must deny the next action; a local test double cannot prove the external platform contract. Pin the local behavior in Task 3 test `test_each_action_requires_a_fresh_route_decision`; retain target qualification as `HELD` until the platform proves linearizable ordering.
- A primary outage or crash after claim/possible acceptance must preserve the original operation and liability and must not resubmit or substitute a provider. Pin this in Task 3 test `test_primary_outage_denies_and_preserves_claimed_liability` and retain the existing publication crash regression in Task 4.

## Entry Blockers

Resolve these before implementing production integration or claiming qualification. The approved design lists them as prerequisites, and the inspected repository does not supply them:

- Name the human policy, authority/revocation, effect-dispatch, safety/reconciliation, admission, baseline, context, and production-environment owners.
- Identify the production issuer/audience, primary database, runtime roles/connectivity, policy provisioning/revocation operator, read-consistency and failover guarantees, measurable revocation-latency objective, and allowed exact operation/effect scope.
- Confirm the strict policy document schema and the approval command’s ability to preserve the immutable operation binding. This plan standardizes new production documents as `schema_version: "salience.p1-production-policy.v1"` and production effect approvals as `request_context.schema_version: "salience.p1-effect-approval.v1"`; existing fixture documents remain on their current path.
- Supply the named platform `RouteFenceAuthority.authorize_action` implementation and evidence that its order is shared with claim, renewal, expiry, termination, and owner transfer. No such adapter or authority is present in this repository.
- Supply the platform’s exact versioned `authorize_action` request/decision tag and wire format; the approved design gives the tuple but does not name a wire schema.
- Name the existing typed recovery command/API that records authoritative no-effect proof for a same-operation permit generation advance. The inspected repository contains no production-shaped no-effect reissue API; do not create a recovery path in this slice.
- Identify the production effect-dispatch entry point to receive the new permit gate. `src/salience/workflows/worker.py` currently rejects non-fixture mode, and `src/salience/cycles/governance.py` is fixture-only. Keep deployment wiring disabled until this separate boundary is established.

If any blocker remains open, local schema and fake-authority tests may be reviewed as fixture evidence only; production integration and qualification remain stopped.

---

### Task 1: Parse strict canonical policy and approval rows

**Files:**

- Create: `src/salience/governance/production_policy.py`
- Test: `tests/unit/test_production_policy.py`
- Reference only: `src/salience/governance/policy.py`, `src/salience/cycles/authority.py`, `src/salience/db/models.py`

**Interfaces:**

- Produce `ProductionPolicyVersion` with `id`, `workspace_id`, `status`, `document_hash`, `effective_at`, `expires_at`, `allowed_effects`, `approval_required_effects`, `jurisdiction_refs`, and optional strict `publication_scope`.
- Produce `ProductionApprovalBinding` with `approval_request_id`, `workspace_id`, `program_id`, `goal_id`, `goal_revision`, `intent_id`, `cycle_id`, `context_id`, `operation_id`, `subject_id`, `policy_version_id`, `policy_document_hash`, `effect_type`, `purpose`, `artifact_sha256`, `account_id`, `provider_id`, and timezone-aware `expires_at`; publication bindings also include `publication_request_id`, `job_id`, `workflow_id`, `workflow_run_id`, `publication_plan_id`, `publication_attempt_id`, and the applicable immutable external-effect/remote-receipt identity.
- `parse_policy_row(row: Mapping[str, object], *, workspace_id: UUID, database_time: datetime) -> ProductionPolicyVersion` validates row scope/status/time, exact document keys, recognized `EffectClass` values, explicit expiry, and SHA-256 of canonical sorted compact JSON. The v1 document keys are `schema_version`, `allowed_effects`, `approval_required_effects`, `expires_at`, `jurisdiction_refs`, plus `publication_scope` only when present; that object has exactly `platform`, `destination`, `locale`, `territory`, and `visibility`.
- `load_policy(connection, *, workspace_id: UUID, policy_version_id: UUID) -> ProductionPolicyVersion` reads the canonical row and database `clock_timestamp()`; no cache or caller-supplied parsed policy is accepted.
- `parse_approval_row(row, *, expected: ProductionApprovalBinding, database_time: datetime) -> ProductionApprovalBinding` checks the exact `salience.p1-effect-approval.v1` tag, row/JSON agreement, current `approved` status, original decision actor/time, and all operation bindings.

- [ ] **Step 1: Write failing tests** named `test_policy_document_v1_is_strict_and_hash_bound` (valid projection, unsupported effect, malformed/naive/expired expiry, future effective time, wrong workspace/status, and hash mismatch), `test_policy_loader_fails_closed_on_unknown_or_legacy_shapes` (missing/extra keys and legacy version), and `test_approval_binding_matches_exact_operation_scope_and_expiry` (approval ID/hash/workspace/operation/effect/purpose/artifact/account/expiry mismatch).
- [ ] **Step 2: Run the RED tests** with a disposable DB/Temporal environment supplied so `tests/conftest.py` does not start its own services:

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" TEST_TEMPORAL_TARGET="$TEST_TEMPORAL_TARGET" .venv/bin/python -m pytest -p no:cacheprovider tests/unit/test_production_policy.py -q
```

Expected: import/attribute failures for `production_policy.py` and all new tests fail.

- [ ] **Step 3: Implement** the immutable row projections and strict parser in `src/salience/governance/production_policy.py`; reuse `EffectClass` and leave `PolicyEngine` and fixture `current_authority()` semantics unchanged.
- [ ] **Step 4: Run the GREEN tests** with the same command; expect every strict-shape, hash, database-time, and binding assertion to pass.
- [ ] **Step 5: Commit** only the two Task 1 files after the focused suite passes.

### Task 2: Add migration 0044 and database enforcement

**Files:**

- Create: `migrations/versions/0044_production_policy_revocation.py`
- Test: `tests/integration/test_v4_migrations.py`
- Preserve unchanged: migrations `0013_identity_boundary.py`, `0021_cycle_policy.py`, `0026_cycle_governance.py`, and `0043_complete_publication_recovery_liabilities.py`

**Interfaces:**

- Migration identity: `revision = "0044_production_policy_revocation"`, `down_revision = "0043_complete_publication_recovery_liabilities"`.
- Create immutable `v4_production_effect_permits` with `permit_id`, `operation_id`, `generation`, workspace/goal/goal-revision/program/intent/cycle/context/subject IDs, `subject_revision`, JSON `grant_ids`, `policy_version_id`, `policy_document_hash`, `approval_request_id`, `approval_expires_at`, `effect_type`, `purpose`, `account_id`, `provider_id`, `artifact_sha256`, all eight `RouteBinding` values, `route_fence_decision_id`, `issued_at`, `expires_at`, and `request_snapshot` tagged `salience.p1-production-effect-permit.v1`. The snapshot binds any publication request/job/workflow/run/plan/attempt/effect/receipt IDs present at the boundary. Create append-only `v4_production_effect_permit_claims` with `claim_id`, `permit_id`, `operation_id`, `generation`, `route_fence_decision_id`, and `claimed_at`; create `v4_production_current_dispatch` with `operation_id` primary key, `generation`, `current_permit_id`, nullable `current_claim_id`, and the original `idempotency_key`. Enforce `UNIQUE(operation_id,generation)` on permits, `UNIQUE(permit_id)` and `UNIQUE(operation_id,generation)` on claims, and production-only foreign keys/pointer updates that cannot reference fixture permit/claim rows.
- Add restricted function `public.v4_advance_production_current_dispatch_v1(operation_id uuid, expected_generation integer, permit_id uuid, claim_id uuid, idempotency_key text, recovery_command_id uuid DEFAULT NULL)`. Revoke direct pointer writes; issue/claim calls must use this function after taking canonical authority locks. Until the existing typed no-effect recovery command is identified, the function must reject every generation advance after generation 1 with `typed_no_effect_command_required`; do not create a substitute command table.
- Add an `AFTER INSERT/UPDATE/DELETE` permission-grant trigger. For identity grants, advance each affected `identity_subjects.revision` in the same transaction when workspace, principal, scope, effect, constraints, or expiry changes; never lower a revision. Retain the existing `v4_grant_edit` serialization trigger.
- Add terminal transition guards for `policy_versions`: draft fields may be authored before activation; once non-draft, policy identity/document/hash/effective/jurisdiction fields are immutable, and a non-draft version cannot return to draft/active. Guard only `approval_requests` tagged `salience.p1-effect-approval.v1`. A decided binding/actor/time is immutable; terminal decisions cannot return to pending/approved; approved may transition once to revoked only with its immutable owning cycle revocation event in the same transaction. Unrelated approvals are unaffected.
- Preserve fixture DDL, trigger/function behavior, literals, roles, and rows. Revoke `PUBLIC` writes to the new history/pointer relations and trigger functions; grant only the named least-privilege runtime/governance/rollback roles after those roles are supplied. Add read-only SQL function `public.v4_production_policy_rollback_inventory_v1(workspace_id uuid) RETURNS jsonb`, returning `{schema_version: "salience.p1-production-policy-rollback-inventory.v1", workspace_id, inventory, inventory_sha256}`. `inventory` is a deterministic complete workspace inventory of fixture and production permits/claims/pointers plus cycles, contexts, outbox/events, publication jobs/requests/plans/attempts, effects, budget reservations, receipts, recovery cases/work items/holds, policy/grant/approval/stop revisions, and unresolved/missing links; rows sort by source, relation, and primary identity before hashing. Expose it only to the authenticated rollback-reader role. Downgrade refuses populated authority history (including identity/grant/policy/approval/stop state) and any state whose safe emptiness cannot be proven; operational rollback preserves 0044 and all revisions.

- [ ] **Step 1: Write failing migration tests** named `test_0044_upgrade_preserves_0043_fixture_rows`, `test_0044_grant_mutation_advances_subject_revision`, `test_0044_policy_and_tagged_approval_transitions_are_terminal`, `test_0044_production_claim_is_unique_and_mode_separated`, `test_0044_rollback_inventory_is_complete_and_read_only`, `test_0044_populated_downgrade_refuses_without_mutation`, and `test_0044_empty_downgrade_preserves_0043_fixture_contract`. Each test creates a disposable migration database, upgrades to 0043, captures pre-upgrade fixture rows/contracts, then targets 0044. Downgrade tests use a separate empty authority database and a separately populated authority database.
- [ ] **Step 2: Run each RED test** against the new target revision:

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" TEST_TEMPORAL_TARGET="$TEST_TEMPORAL_TARGET" .venv/bin/python -m pytest -p no:cacheprovider tests/integration/test_v4_migrations.py::test_0044_upgrade_preserves_0043_fixture_rows tests/integration/test_v4_migrations.py::test_0044_grant_mutation_advances_subject_revision tests/integration/test_v4_migrations.py::test_0044_policy_and_tagged_approval_transitions_are_terminal tests/integration/test_v4_migrations.py::test_0044_production_claim_is_unique_and_mode_separated tests/integration/test_v4_migrations.py::test_0044_rollback_inventory_is_complete_and_read_only tests/integration/test_v4_migrations.py::test_0044_populated_downgrade_refuses_without_mutation tests/integration/test_v4_migrations.py::test_0044_empty_downgrade_preserves_0043_fixture_contract -q
```

Expected: Alembic cannot resolve `0044_production_policy_revocation` until the migration is added; each test must fail for that missing revision.

- [ ] **Step 3: Implement** the additive DDL/guards in 0044. Do not backfill historical records. Make the downgrade lock the affected relations, prove all new authority history is empty before dropping anything, and raise before changing schema or `alembic_version` when populated.
- [ ] **Step 4: Run the GREEN tests** with the same command. Verify upgrade from 0043, empty downgrade/re-upgrade, populated refusal with unchanged rows/head, grant revision advancement for INSERT/decision-relevant UPDATE/DELETE, active-policy terminality, tagged approval guards, both cross-mode denial directions, and unchanged fixture table contracts.
- [ ] **Step 5: Commit** migration and its migration tests.

### Task 3: Implement production permit issue, claim, and per-action authorization

**Files:**

- Create: `src/salience/cycles/production_dispatch.py`
- Create: `src/salience/observability/production_authorization.py`
- Test: `tests/integration/test_p1_production_policy_revocation.py`
- Use: `src/salience/governance/production_policy.py`, `src/salience/cycles/authority.py`, `src/salience/cycles/baselines.py`, and migration 0044 relations

**Interfaces:**

- `RouteBinding` carries `deployment_id`, `environment_id`, `route_id`, `slot_id`, `owner_id`, `fencing_epoch`, `route_manifest_sha256`, and `artifact_lock_sha256`.
- `RouteFenceRequest` adds `operation_id`, `permit_id`, and `action_kind`; `RouteFenceDecision` returns `authorized`, a unique one-use decision ID when allowed, a bounded `reason_code` when denied, and the exact full request tuple.
- `RouteFenceAuthority.authorize_action(request: RouteFenceRequest) -> RouteFenceDecision` is the injected, versioned platform contract; pin the version tag and serialization only after the platform owner supplies them. The local fake is test-only.
- `PublicationEffectBinding` carries `publication_request_id`, `job_id`, `workflow_id`, `workflow_run_id`, `publication_plan_id`, `publication_attempt_id`, `external_effect_id`, and nullable `remote_receipt_id` where available.
- `ProductionPermitRequest` carries only proposed operation/effect fields: `workspace_id`, `goal_id`, `goal_revision`, `program_id`, `intent_id`, `cycle_id`, `context_id`, `operation_id`, `subject_id`, `policy_version_id`, `approval_request_id`, `required_scopes`, `effect_type`, `purpose`, `artifact_sha256`, `account_id`, `provider_id`, `idempotency_key`, `route`, `execution_deadline`, `ttl_seconds` constrained to 1–60, and optional `publication_binding`. The service reloads all authority fields and verifies every proposed ID against canonical rows.
- `ProductionPermitResult` is immutable and returns `permit_id`, `operation_id`, `generation`, `expires_at`, nullable `claim_id`, and `dispatch_allowed`.
- `ProductionEffectDispatch(database_url, route_fence).issue_permit(request) -> ProductionPermitResult`, `.claim_permit(operation_id, permit_id) -> ProductionPermitResult`, and `.authorize_action(operation_id, permit_id, action_kind) -> RouteFenceDecision` synchronously reload and lock current primary rows. Inputs are revalidated against canonical operation/context/baseline/grant/policy/approval/budget/account/stop data; payload snapshots are never authority. It calls only `public.v4_advance_production_current_dispatch_v1` for pointer changes; later generations remain denied until the typed recovery-command blocker is resolved.
- Issue and claim persist their route decision, current policy/subject revision, approval binding, and both stop revisions with immutable cycle/publication audit evidence in the same transaction. Claim serializes on `v4_production_current_dispatch`; one claim can authorize one exact action. Every later effect action obtains a fresh route decision and current canonical read.
- `ProductionAuthorizationMetrics(meter)` in `src/salience/observability/production_authorization.py` exposes `record_decision(*, action, effect, outcome, reason_code)`, `record_stop_to_denial(seconds)`, and bounded incident methods for stale revision, expiry, primary outage, signal lag/failure, duplicate claim, unknown outcome, and unresolved liability. Metric attributes never contain identifiers; this adds metrics only, not alert delivery.

- [ ] **Step 1: Write failing integration tests** named `test_issue_denies_missing_or_wrong_current_authority` (including missing stop scope), `test_current_expiry_and_missing_facts_deny_at_database_time` (identity, grant, policy, approval, permit, baseline, account, and stop), `test_revoke_then_regrant_cannot_reuse_old_permit`, `test_stop_revoke_claim_race_and_duplicate_workers`, `test_each_action_requires_a_fresh_route_decision`, `test_crash_before_claim_reloads_current_authority`, `test_primary_outage_denies_and_preserves_claimed_liability`, `test_provider_substitution_and_cross_workspace_are_denied`, `test_issue_claim_event_is_atomic_with_permit_history`, and `test_production_authorization_metrics_use_bounded_redacted_labels`. Assert zero adapter/lease calls on every denial, no new operation, no rewritten context, one current pointer/claim, same-transaction decision evidence, bounded metric labels without identifiers, and the original operation/idempotency key on any retained unknown liability. Add `test_no_effect_generation_advance_uses_typed_recovery_proof` only after the blocked existing recovery API is identified; it must advance one generation for the same operation/key only after exact authoritative no-effect proof, and deny unknown/accepted/unresolved prior generations.
- [ ] **Step 2: Run the RED tests** against migration 0044 and a disposable PostgreSQL/Temporal pair:

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" TEST_TEMPORAL_TARGET="$TEST_TEMPORAL_TARGET" .venv/bin/python -m pytest -p no:cacheprovider tests/integration/test_p1_production_policy_revocation.py -q
```

Expected: imports for `production_dispatch.py` fail and no production permit API exists.

- [ ] **Step 3: Implement** one transaction service in `production_dispatch.py`. Lock the canonical subject/grants and policy/approval rows, workspace and goal stop rows, operation and dispatch pointer; evaluate database time; synchronously call the injected route authority; then persist decision/permit/claim atomically. Do not call `CycleGovernance`, renew an unknown claim, issue a second business operation, or use an in-memory allow after restart.
- [ ] **Step 4: Run the GREEN tests** with the same command. Add a test-only fake that can order stop/revoke and route-transfer barriers, and assert each denied action makes zero provider calls. Label these as local fixture contract tests, not route-target proof.
- [ ] **Step 5: Commit** the dispatch module, bounded metrics module, and integration tests.

### Task 4: Reauthorize publication at each effect boundary

**Files:**

- Modify: `src/salience/publication/repository.py`
- Modify: `src/salience/workflows/publication.py`
- Test: `tests/integration/test_p1_production_policy_revocation.py`
- Regression: `tests/integration/test_v4_legacy_publication.py`

**Interfaces:**

- Add canonical policy identity/hash/status/expiry and tagged approval binding to `CurrentPublicationAuthorization`; production authorization uses the repository’s current canonical policy rows and parsed approval binding, never a worker payload or `PublicationWorkflowState.policy_allowed` boolean. The existing state boolean remains a fixture denial input only.
- Add `ProductionEffectDispatch | None` to `PublicationWorkflowState`, default `None`. A non-fixture provider has no forward authorization when this dependency is absent. Do not wire a production adapter into `src/salience/workflows/worker.py` or enable a deployment mode in this task.
- In `salience.publication.authorize`, after canonical request, approval, capability, rights, disclosure, and budget checks, issue one production permit; before provider dispatch in `submit_or_reconcile`, claim that permit. Before `salience.publication.delivery`, every state-progressing `await_publication`, and any provider-specific `cancel`/reconcile action, reload current request and policy/approval/grant/account/rights/disclosure/budget facts and call `authorize_action` with the matching action kind. Invoke synchronous PostgreSQL dispatch methods via `asyncio.to_thread` from Temporal activities. Resolve `CredentialLease` only after the final action barrier and assert denial does not call the lease factory. Missing production dispatch authority denies before adapter I/O. Existing fixture recovery continues to keep unknown liability under its current typed hold path.

- [ ] **Step 1: Write failing tests** named `test_production_publication_revoke_between_activities_never_submits`, `test_production_publication_route_transfer_blocks_next_action`, and `test_missing_production_dispatch_never_calls_nonfixture_adapter`. Assert deny before adapter call and preserve the existing fixture no-resubmit and recovery-hold assertions.
- [ ] **Step 2: Run the RED tests**:

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" TEST_TEMPORAL_TARGET="$TEST_TEMPORAL_TARGET" .venv/bin/python -m pytest -p no:cacheprovider tests/integration/test_p1_production_policy_revocation.py::test_production_publication_revoke_between_activities_never_submits tests/integration/test_p1_production_policy_revocation.py::test_production_publication_route_transfer_blocks_next_action tests/integration/test_p1_production_policy_revocation.py::test_missing_production_dispatch_never_calls_nonfixture_adapter -q
```

Expected: current activity path has no production dispatch gate and tests fail before implementation.

- [ ] **Step 3: Implement** canonical projection in `PublicationRepository` and inject the guard at each listed `PublicationActivities` boundary. Keep `GuardedPublicationActivities` fixture behavior and fixture literals intact.
- [ ] **Step 4: Run the GREEN and fixture regression tests**:

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" TEST_TEMPORAL_TARGET="$TEST_TEMPORAL_TARGET" .venv/bin/python -m pytest -p no:cacheprovider tests/integration/test_p1_production_policy_revocation.py tests/integration/test_v4_legacy_publication.py::test_publication_current_policy_and_rights_are_required tests/integration/test_v4_legacy_publication.py::test_publication_revoked_before_native_start_has_no_temporal_or_business_effect tests/integration/test_v4_legacy_publication.py::test_publication_revoked_between_native_activities_never_submits tests/integration/test_v4_legacy_publication.py::test_publication_subprocess_crash_without_receipt_never_retries_submit -q
```

Expected: production fake-authority denial tests pass; the existing fixture revocation, crash, and hold tests remain green without changed call counts or fixture DDL.

- [ ] **Step 5: Commit** repository/activity changes and their tests.

### Task 5: Add deterministic read-only rollback inventory

**Files:**

- Create: `src/salience/cycles/production_rollback_reader.py`
- Test: `tests/integration/test_p1_production_policy_revocation.py`
- Reference only: `src/salience/cycles/publication_recovery.py`, `scripts/verify-v4-publication-recovery-rollback.py`

**Interfaces:**

- `read_production_policy_rollback_inventory(connection, *, workspace_id: UUID) -> ProductionPolicyRollbackInventoryV1` calls `public.v4_production_policy_rollback_inventory_v1(workspace_id)` and validates a deterministic sorted inventory plus SHA-256 fingerprint for fixture and production permits/claims/pointers and all linked cycles, contexts, outbox/events, publication jobs/requests/plans/attempts, provider effects, budget reservations, receipts, recovery cases/work items/holds, policy, grant, approval, and stop rows in the workspace. Every possibly submitted or unknown operation remains present with its original identity and unresolved state.
- The reader is read-only, requires workspace scope, does not accept a caller-supplied operation list, and never classifies an `unknown` or unmatched liability as safe. It does not replace the routing-owned authenticated launcher or the full publication/recovery liability verifier.

- [ ] **Step 1: Write failing tests** named `test_rollback_inventory_covers_fixture_and_production_modes`, `test_rollback_inventory_is_deterministic_and_read_only`, and `test_rollback_inventory_retains_unknown_and_unmatched_liabilities`. Seed both table families; compare repeat fingerprints; verify no table/row changes; assert unknown, unclaimed/claimed, unmatched reservation, or missing-receipt identities remain visible as blockers.
- [ ] **Step 2: Run the RED tests**:

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" TEST_TEMPORAL_TARGET="$TEST_TEMPORAL_TARGET" .venv/bin/python -m pytest -p no:cacheprovider tests/integration/test_p1_production_policy_revocation.py::test_rollback_inventory_covers_fixture_and_production_modes tests/integration/test_p1_production_policy_revocation.py::test_rollback_inventory_is_deterministic_and_read_only tests/integration/test_p1_production_policy_revocation.py::test_rollback_inventory_retains_unknown_and_unmatched_liabilities -q
```

Expected: import failure for the versioned reader.

- [ ] **Step 3: Implement** a read-only query/projection and deterministic digest. Keep `scripts/verify-v4-publication-recovery-rollback.py` unchanged because it takes caller-provided operation IDs and is not the design’s complete production verifier.
- [ ] **Step 4: Run the GREEN tests** with the same command. Also run migration refusal tests from Task 2; verify populated rollback refuses without changing `alembic_version`, policy/grant revisions, stops, approval rows, permits, claims, or pointers.
- [ ] **Step 5: Commit** the reader and tests.

### Task 6: Record fixture evidence and preserve release gates

**Files:**

- Create: `docs/v4/p1-production-policy-revocation-evidence.json`
- Modify: `docs/v4/test_checks.py`
- Modify: `docs/v4/p1-qualification-matrix.json`
- Modify: `docs/v4/validation.md`
- Modify: `docs/v4/progress.md`
- Modify: `docs/README.md` to link the implementation plan and fixture evidence from the current V4 checkpoint.

- [ ] **Step 1: Write failing test `test_policy_revocation_evidence_is_fixture_scoped_and_gate_held`** in `docs/v4/test_checks.py`. Require the evidence record to carry schema version, exact implementation commit, artifact/image digest when available, migration head, named fixture environment/secret-free manifest digest, issuer/audience identifiers without keys, test commands/results, timestamp, operator/reviewer fields, and immutable log/evidence digests. Reject any fixture record presented as production acceptance and assert production disabled, RG0/RG1 `HELD`, P2 `BLOCKED`.
- [ ] **Step 2: Run the RED documentation test**:

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" TEST_TEMPORAL_TARGET="$TEST_TEMPORAL_TARGET" .venv/bin/python -m pytest -p no:cacheprovider docs/v4/test_checks.py -q
```

Expected: the new evidence record is absent and the schema/gate assertion fails.

- [ ] **Step 3: Record only actual local evidence** in `p1-production-policy-revocation-evidence.json`; label scope `fixture`, include failed/interrupted runs only where needed to explain the final command, and preserve production target fields as `NOT RUN` when absent. In `p1-qualification-matrix.json`, attach the fixture evidence to applicable R13/R15/R17/R18/R05/R07/R10/R23F rows while keeping their production status `HELD` and remaining acceptance explicit. Append current dated checkpoint text to validation/progress; do not rewrite frozen audit records or historical candidate evidence.
- [ ] **Step 4: Run GREEN documentation checks**:

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" TEST_TEMPORAL_TARGET="$TEST_TEMPORAL_TARGET" .venv/bin/python -m pytest -p no:cacheprovider docs/v4/test_checks.py -q
bash tests/scripts/test_phase_9_documentation.sh
bash scripts/verify-phase-9.sh
git diff --check
```

Expected: the evidence parses and links resolve; docs scripts and whitespace checks pass; release rows remain held.

- [ ] **Step 5: Commit** only the new fixture evidence and scoped documentation updates.

## Final focused verification

After Tasks 1–6, run the exact new tests plus the unchanged fixture governance/publication regressions on a disposable PostgreSQL/Temporal pair:

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" TEST_TEMPORAL_TARGET="$TEST_TEMPORAL_TARGET" .venv/bin/python -m pytest -p no:cacheprovider tests/unit/test_policy.py tests/unit/test_production_policy.py tests/integration/test_v4_migrations.py::test_0044_upgrade_preserves_0043_fixture_rows tests/integration/test_v4_migrations.py::test_0044_grant_mutation_advances_subject_revision tests/integration/test_v4_migrations.py::test_0044_policy_and_tagged_approval_transitions_are_terminal tests/integration/test_v4_migrations.py::test_0044_production_claim_is_unique_and_mode_separated tests/integration/test_v4_migrations.py::test_0044_rollback_inventory_is_complete_and_read_only tests/integration/test_v4_migrations.py::test_0044_populated_downgrade_refuses_without_mutation tests/integration/test_v4_migrations.py::test_0044_empty_downgrade_preserves_0043_fixture_contract tests/integration/test_p1_production_policy_revocation.py tests/integration/test_v4_cycle_governance.py tests/integration/test_v4_legacy_publication.py -q -rs
```

Before any restart rehearsal, run the versioned reader against a disposable copied database at 0044 and verify it enumerates fixture and production relations with a deterministic digest. Test Alembic downgrade refusal only on disposable databases. A rollback rehearsal on a named production environment, prior-artifact compatibility, full liability inventory through the routing-owned authenticated launcher, route-transfer ordering, failover consistency, and independent reviewer readback are separate `HELD` qualification obligations; the existing caller-scoped publication verifier is insufficient. No migration downgrade, worker start, schedule mutation, provider credential, provider call, live spend, or production effect is authorized by this plan.

## Spec coverage and unresolved blockers

- R13: Tasks 1–4 test current grant/policy revocation and revision advancement.
- R15: Tasks 1, 3, and 4 test canonical versioned policy and exact approval/effect binding.
- R17: Tasks 2–4 test serialized issue/claim, stop ordering, unique claims, and per-activity route checks.
- R18: Tasks 3–5 test authority outage, restart, no resubmit, and liability retention.
- R05/R07/R10: Task 3 binds current approved baseline, admission, context, and goal revision into issue/claim checks.
- R23F: Tasks 1, 3, and 4 retain default deny on missing policy/route/account and provider substitution.
- Local acceptance remains fixture-only. Unresolved named owners, production identity/DB/failover guarantees, policy/approval producer support, the platform route-fence implementation, the production effect-dispatch entry point, and the typed no-effect reissue command prevent production integration/qualification. Keep production disabled, RG0/RG1 held, and P2 blocked until those dependencies and external acceptance are complete.
