# Task 12 report — final independent-review corrections

Date: 2026-10-29
Base: `a87141005796327d17a258df6549929e7a5625df`
Branch: `arch`
Status: implementation complete; independent review and exact-candidate evidence regeneration remain controller-owned.

## Scope and safety

Task 12 corrects the four Important findings in `task-12-brief.md`. Production effects remained disabled. The test target was a disposable local Compose Postgres/Temporal project (`salience-task12-20261028-final-corr-9241`); schedule targets and alert providers used injected test doubles. No live/provider/production database, remote schedule, alert target, or external service was contacted. RG0/full RG1 remain `HELD`; P2 remains `BLOCKED`. No launch lock, evidence package, documentation-status wrapper, or `docs/v4/evidence-lock.json` was regenerated.

## Design decisions

1. **Stop-first schedule pause/readback.** The action boundary opts into committed-stop handling only for `schedule_pause` and `schedule_readback`. It still checks the current immutable source binding and exact approval context, policy ID/workspace/hash, decided approval identity, current schedule operator and `cycles:schedule` grant, current stop actor and `cycles:stop` grant, exact current stop receipts/revisions, route fence/readback, target, and namespace. A stop-only safety path may continue if the bound workload policy is revoked or its decided approval is revoked, expired, or past its expiry. Rejected/undecided approval identities remain invalid. With no stop, and on all activation/poll/resume paths, the code requires an approved unexpired approval, an active policy, and both stop scopes clear. Pause command identity includes current stop revisions so a later stop revision produces a different stable target command key. Both pending cutover pause and active-cutover out-of-band unpause/re-pause are covered.
2. **Rollback issue and claim decisions.** Migration 0051 validates the issue decision against its exact issue event, validates each claim decision against its exact immutable claim event, and validates the current pointer against that exact claim. The claim decision UUID may differ from the issue decision UUID. Corruption still creates the corresponding fail-closed blocker. Migration 0051 remains in place and its downgrade remains fail-before-mutation.
3. **Stop receipt cardinality.** Historical revision 1 accepts zero or more false no-op commands. A later historical unstopped bound revision requires one or more false receipts and no true receipt. Current active rollback fencing requires a current stopped scope, no false receipt at that revision, and at least one true receipt by the scope's changed-by actor; additional same-state no-ops do not invalidate proof.
4. **Persisted retry on restart.** Before a new send, retry history is matched to the exact alert/event key, sequential attempt numbers, event payload, policy decision, and configured retry interval. A local receipt or provider readback is reconciled first. Otherwise, restart waits until the persisted `retry_at`, verifies the injected wait did not return early, reauthorizes, and retries with the same idempotency key. Missing, malformed, conflicting, or policy-inconsistent history fails closed.

## Red-first evidence

All commands below used this exact environment prefix (the focused test suffix follows it):

```sh
PYTHONPATH=src COMPOSE_PROJECT_NAME=salience-task12-20261028-final-corr-9241 \
TEST_DATABASE_URL=postgresql://salience:salience@172.21.0.2:5432/salience \
TEST_TEMPORAL_TARGET=172.21.0.3:7233 \
DATABASE_URL=postgresql+asyncpg://salience:salience@172.21.0.2:5432/salience \
.venv/bin/pytest -q
```

- `tests/integration/test_p1_production_schedule_fencing.py -k 'committed_stop_preserves_revision_bound_pause or active_cutover_stop_allows_exact_out_of_band_unpause_repause'`: **2 failed, 37 deselected**, exit 1, 14.36s. Reproduced the unreachable pending-stop pause and rejection of active-cutover re-pause.
- `tests/integration/test_p1_production_schedule_fencing.py -k committed_stop_preserves_revision_bound_pause_after_policy_revocation`: **2 failed, 2 passed, 38 deselected**, exit 1, 29.46s. Approval status `revoked` and `expired` were rejected by the pre-correction stop path; policy revocation and an elapsed expiry already exercised the accepted policy-only/elapsed branch.
- `tests/integration/test_p1_production_recovery.py -k 'alert_restart_waits_for_persisted_retry_at_before_sending or alert_restart_rejects_invalid_persisted_retry_evidence'`: **5 failed, 49 deselected**, exit 1, 35.84s. Restart sent before the persisted deadline and did not reject the invalid persisted retry cases.
- `tests/integration/test_p1_production_recovery.py -k 'alert_restart_rejects_invalid_persisted_retry_evidence and conflicting'`: **1 failed, 53 deselected**, exit 1, 7.57s. A conflicting duplicate retry event did not fail closed.
- `tests/integration/test_p1_production_recovery.py -k 'rollback_v2_accepts_revision_one_false_stop_noops or rollback_v2_accepts_historical_and_active_stop_noops'`: **2 failed, 52 deselected**, exit 1, 17.44s. Valid canonical stop command histories were rejected by the rollback inventory.
- `tests/integration/test_p1_production_policy_revocation.py -k '0051_rollback_inventory_binds_distinct_issue_and_claim_decisions'`: **1 failed, 124 deselected**, exit 1, 1.49s. Correctly distinct issue/claim decision IDs produced the claim/pointer blocker.

## Green verification

All DB-heavy tests ran serially against the disposable local project.

- Focused schedule regressions: `tests/integration/test_p1_production_schedule_fencing.py -k committed_stop_preserves_revision_bound_pause_after_policy_revocation`: **4 passed, 38 deselected**, exit 0, 29.86s.
- Full schedule fencing: `tests/integration/test_p1_production_schedule_fencing.py`: **42 passed**, exit 0, 151.91s.
- Alert restart regressions: `tests/integration/test_p1_production_recovery.py -k 'alert_restart_waits_for_persisted_retry_at_before_sending or alert_restart_rejects_invalid_persisted_retry_evidence'`: **5 passed, 49 deselected**, exit 0, 35.12s.
- Stop receipt regressions: `tests/integration/test_p1_production_recovery.py -k 'rollback_v2_accepts_revision_one_false_stop_noops or rollback_v2_accepts_historical_and_active_stop_noops'`: **2 passed, 52 deselected**, exit 0, 17.36s.
- Distinct issue/claim regression: `tests/integration/test_p1_production_policy_revocation.py -k '0051_rollback_inventory_binds_distinct_issue_and_claim_decisions'`: **1 passed, 124 deselected**, exit 0, 2.70s. The test upgrades a disposable clone from 0050 to 0051 and drops it afterward; it does not downgrade or mutate the shared test database.
- Full recovery suite: `tests/integration/test_p1_production_recovery.py`: **54 passed**, exit 0, 378.89s.
- Full policy/revocation suite: `tests/integration/test_p1_production_policy_revocation.py`: **125 passed**, exit 0, 58.58s.
- Migration selection: `tests/integration/test_v4_migrations.py -k '0051'`: **1 passed, 72 deselected**, exit 0, 7.28s. Covers fresh 0050→0051 upgrade and irreversible downgrade fingerprint/version preservation.
- Static checks: `PYTHONPATH=src .venv/bin/python -m compileall -q src migrations tests/integration/test_p1_production_recovery.py tests/integration/test_p1_production_schedule_fencing.py tests/integration/test_p1_production_policy_revocation.py`: exit 0. `git diff --check`: exit 0.

## Cleanup and handoff

The unique Compose project was removed with exactly:

```sh
docker compose -p salience-task12-20261028-final-corr-9241 down --volumes --remove-orphans
```

No host-wide Docker prune or unrelated resource cleanup was used. The regression helpers drop their temporary database clones in `finally` blocks. Controller-owned lock/evidence regeneration and final independent review remain outstanding. No push or merge was performed.

## Fix round 1/5 — complete 0051 claim-event binding

Review finding: the initial 0051 correction matched a claim decision UUID to a `production_permit_claimed` event but only compared part of the dispatch-emitted payload. An event with a changed idempotency key, workspace/goal stop revisions, budget IDs, approval expiry, or required scopes could avoid both the claim and current-pointer blockers.

The red-first regression mutates each omitted field in an isolated 0050→0051 database clone while preserving the claim row and current pointer. All seven cases failed against the pre-fix validator because neither blocker was returned. The fix adds `v4_p1_rollback_claim_event_matches_v1`, which compares exact JSONB payload equality against the immutable permit columns/request snapshot for every canonical `_write_event` field, including structured required scopes and optional publication binding, and validates event envelope identities and database-time shape. The helper requires exactly one event for the claim and is used by both the claim inventory check and current-pointer check. Claim decision IDs remain distinct from issue decision IDs.

Red command (pytest session startup created the named disposable Compose project and upgraded its fresh database):

```sh
COMPOSE_PROJECT_NAME=salience-task12-fix1-20261029 PYTHONPATH=src \
.venv/bin/pytest -q tests/integration/test_p1_production_policy_revocation.py \
-k '0051_rollback_inventory_rejects_claim_event_payload_drift'
```

Result: **7 failed, 125 deselected**, exit 1, 14.15s. Each failure was the intended missing `production_permit_claim_mismatch` assertion; the pre-fix validator also omitted `production_current_pointer_claim_mismatch` for all seven corruptions.

Green verification used this exact prefix for the existing disposable project:

```sh
PYTHONPATH=src COMPOSE_PROJECT_NAME=salience-task12-fix1-20261029 \
TEST_DATABASE_URL=postgresql://salience:salience@172.21.0.2:5432/salience \
TEST_TEMPORAL_TARGET=172.21.0.3:7233 \
DATABASE_URL=postgresql+asyncpg://salience:salience@172.21.0.2:5432/salience \
.venv/bin/pytest -q
```

- `tests/integration/test_p1_production_policy_revocation.py -k '0051_rollback_inventory_binds_distinct_issue_and_claim_decisions or 0051_rollback_inventory_rejects_claim_event_payload_drift'`: **8 passed, 124 deselected**, exit 0, 13.40s.
- `tests/integration/test_v4_migrations.py -k '0051'`: **1 passed, 72 deselected**, exit 0, 6.97s.
- `tests/integration/test_p1_production_recovery.py -k 'rollback_v2'`: **12 passed, 42 deselected**, exit 0, 95.95s.
- `tests/integration/test_p1_production_policy_revocation.py`: **132 passed**, exit 0, 69.80s.
- `PYTHONPATH=src .venv/bin/python -m compileall -q src migrations tests/integration/test_p1_production_recovery.py tests/integration/test_p1_production_schedule_fencing.py tests/integration/test_p1_production_policy_revocation.py`: exit 0. `git diff --check`: exit 0.

The project was removed with `docker compose -p salience-task12-fix1-20261029 down --volumes --remove-orphans` (exit 0); follow-up container, volume, and network label queries returned no resources. No host-wide prune, artifact/evidence regeneration, push, or merge was performed.

Pre-commit fresh verification: `COMPOSE_PROJECT_NAME=salience-task12-fix1-verify-20261029 PYTHONPATH=src .venv/bin/pytest -q tests/integration/test_p1_production_policy_revocation.py -k '0051_rollback_inventory_binds_distinct_issue_and_claim_decisions or 0051_rollback_inventory_rejects_claim_event_payload_drift'` passed **8 tests, 124 deselected** in 14.91s (exit 0) after a separate fresh full migration to 0051. Exact project cleanup completed with `docker compose -p salience-task12-fix1-verify-20261029 down --volumes --remove-orphans` (exit 0); its container, volume, and network label queries were empty.
