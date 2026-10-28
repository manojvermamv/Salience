# P1 Recovery Rollback Verifier v2 Implementation Plan

> **For agentic workers:** Use the inline execution method selected by the task owner. Keep the work serial and do not spawn agents.

**Goal:** Add a complete workspace-discovered recovery inventory and read-only rollback verifier, then bind v2 rollback launch to migration 0051 and an unchanged pre/post digest.

**Architecture:** Migration 0051 owns the complete SQL inventory function and is irreversible. A focused Python verifier validates that inventory and checks current route and Temporal readbacks through the existing typed interfaces. The launcher v2 path accepts workspace scope only, runs the verifier before prior-code execution and after it exits, and compares digests; the v1 fixture path stays intact.

**Tech Stack:** PostgreSQL/Alembic, Python, psycopg, pytest integration tests, canonical JSON/SHA-256 launch-lock builder.

**Spec:** `.superpowers/sdd/2026-10-28-p1-remaining-production-development/task-9-brief.md`

## Global Constraints

- Migration revision is `0051_p1_recovery_rollback_v2`, down revision is `0050_p1_production_dispatch_reissue`.
- `public.v4_p1_recovery_rollback_inventory_v2(workspace_id uuid) RETURNS jsonb` discovers all scoped records itself and always queries both hold reasons with explicit zero counts.
- SQL is split into a self-scoped workspace inventory (enrollments, production work/events, production permit/claim/pointer evidence, schedules, and liabilities) and a Python readback verifier pinned by one ABI constant. The inventory reuses `v4_validate_production_schedule_inventory_v1(uuid)` for the full page/member/history/binding/approval authority. Migration 0051 replaces that function from `pg_get_functiondef`, removing only its inventory-header `FOR UPDATE`; `v4_finalize_production_schedule_inventory_v1(uuid)` retains its explicit header lock before validation, so finalization remains serialized while verifier reads work under `READ ONLY`.
- The verifier ABI is `verify_p1_recovery_rollback_v2(database_url: str, *, workspace_id: UUID, route_authority: RouteLeaseAuthority, temporal_readback: TemporalWorkerReadback) -> RecoveryRollbackInventoryV2`.
- The canonical v2 lock payload adds exactly `migration_head=0051_p1_recovery_rollback_v2`, `rollback_verifier_abi=salience.p1-recovery-rollback-verifier.v2`, and `qualification=development-only`; the tracked fixture v1 lock remains byte-identical.
- Migration 0051 downgrade raises before schema or version mutation.
- The v2 rollback launcher accepts workspace scope only; it verifies before and after prior-code execution and requires identical inventory digests.
- The v2 candidate lock pins exactly migration head `0051_p1_recovery_rollback_v2` and verifier ABI v2; its external trust digest remains unprovisioned and development-only.
- v1 fixture lock, v1 launcher, and 0043 fixture behavior remain unchanged where applicable; production remains disabled and gates held.

## Review Focus

- Missing rows, omitted reason counts, and incomplete SQL reads must fail closed; cover with the five named verifier inventory tests.
- Incomplete page roots, history roots, or mismatched typed approvals must block; cover complete inventory cases against the 0048 validator.
- Snapshot or liability changes between observations must block; cover `test_rollback_v2_rejects_snapshot_or_liability_change` and the pre/post launcher test.
- Unknown, stale, or unavailable route and Temporal readbacks must block; cover `test_rollback_v2_rejects_unknown_route_claim_and_unavailable_readback` and routing integration.
- Caller-selected hold or operation IDs must be rejected before prior code imports; cover `test_launcher_v2_rejects_caller_supplied_hold_lists`.

---

### Task 1: Pin the migration and verifier failure cases

**Files:**
- Create: `migrations/versions/0051_p1_recovery_rollback_v2.py`
- Create: `src/salience/cycles/p1_recovery_rollback.py`
- Test: `tests/integration/test_p1_production_recovery.py`
- Test: `tests/integration/test_v4_migrations.py`

- [ ] Add the five named `test_rollback_v2_*` cases and `test_0051_downgrade_raises_without_schema_or_alembic_version_change`.
- [ ] Run the named cases serially against disposable PostgreSQL databases and record the expected missing-ABI/migration RED results.
- [ ] Add migration 0051 with the self-discovering complete inventory function, exact two-reason counts, blockers, and an unconditional first-statement downgrade exception.
- [ ] Implement the inventory result model and fail-closed verifier ABI using read-only repeatable-read database access, `RouteLeaseAuthority.read_slot`, and `TemporalWorkerReadback.read_worker`.
- [ ] Re-run the named recovery and migration cases; verify equal deterministic digests for unchanged complete inventory.

### Task 2: Bind compatibility and launcher v2 rollback

**Files:**
- Modify: `src/salience/cycles/publication_recovery_compatibility.py`
- Modify: `scripts/launch-v4-publication-worker.py`
- Modify: `tests/integration/test_p1_production_routing.py`

- [ ] Add `test_launcher_v2_rejects_caller_supplied_hold_lists` and `test_launcher_v2_runs_pre_and_post_readback_before_prior_code`.
- [ ] Run those tests serially and verify they fail for the current v2 rejection of rollback mode and missing second readback.
- [ ] Add the exact verifier ABI v2 requirement to authenticated v2 rollback compatibility; leave v1 compatibility calls and behavior intact.
- [ ] Make v2 rollback parse workspace scope only, discover route identity from verified inventory, run preflight before worker import, run the worker process, re-read complete inventory, and require matching digests.
- [ ] Re-run the two routing cases and the relevant v1 fixture lock/launcher cases.

### Task 3: Pin final candidate head and ABI

**Files:**
- Modify: `scripts/build_publication_worker_launch_lock.py`
- Modify: `src/salience/cycles/publication_recovery_compatibility.py`
- Modify: `tests/integration/test_v4_publication_recovery_floor.py`
- Modify: `deployment/v4/publication-worker-launch-lock.json` only if it is the development v2 candidate; preserve tracked v1 fixture lock bytes.

- [ ] Add `test_launcher_v2_lock_builder_binds_exact_final_migration_head_0051_and_verifier_abi`.
- [ ] Run it serially and verify the current builder rejects 0051 as a production head and omits the verifier ABI.
- [ ] Extend the canonical v2 payload with exact 0051/verifier-v2 pins and an explicit development-only marker; do not provision an external trust digest.
- [ ] Regenerate the deterministic development candidate payload and SHA-256; confirm v1 fixture lock bytes remain unchanged.
- [ ] Re-run the four named test files, review all changed files, and commit as `feat: add complete recovery rollback verifier v2`.
