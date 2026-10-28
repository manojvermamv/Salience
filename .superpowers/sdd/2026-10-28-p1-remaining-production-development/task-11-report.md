# Task 11 report — exact-candidate development acceptance

Date: 2026-10-28

## Outcome

The exact candidate's single final serial non-live suite passed: 1,120 passed, 26 skipped, 3 warnings, pytest exit 0 (2,346.13 seconds). A synthetic 22-row external-acceptance package was generated and accepted structurally; its checker exits 1 as required for `VALID` / `NO_GO` / `UNCHANGED`. Production acceptance is not established. The final evidence package and detailed report are in `docs/v4/p1-remaining-production-development-evidence.json` and `final-evidence-report.md`.

## Source and generated-artifact pins

- Implementation source commit/tree: `1ded0219c13fa478e3850956a30841d89cd80a38` / `732f865bdfec789bbd407d3b7965611223132395`.
- Artifact refresh commit/tree: `3e7f6798c652412d2992f83ed7f65974532a91c9` / `61fae364ab5f99edef47916db4d0a10d8c318c01`.
- Development wheel SHA-256: `5f6680065cf84e8d30df298b342af093ef578422a6f922b2dae1a53e0d4072ed`.
- Development v2 lock SHA-256: `04514285da583fe4b32feb0ae2e710a55da6c9bdbf40d853c9454c0105fd8221`. Companion content agrees; v1 lock remains byte-identical at SHA-256 `1c8ac4ff92e589e27dc0af2c6dd0bc69c4295435df5a318174a2c77f3b1f2c9c`.
- Exact migration head: `0051_p1_recovery_rollback_v2`.

The lock was regenerated after all test-only source corrections. The source pin excludes generated deployment artifacts; the separately recorded artifact commit is the tree on which the full test run was performed. Evidence avoids self-hashing by referring to source and artifact commits only, not a commit containing itself.

## Corrections and test history

Task 11 surfaced stale migration-downgrade tests and a recovery-work fixture omission. For historical rollback tests, pre-downgrade upgrades now pin `0050_p1_production_dispatch_reissue` so those tests continue exercising the intended historical guards. Current-head preservation checks remain at `head` and include the precise 0051 irreversible refusal while retaining schema, version, and row-preservation assertions. This test-only correction is commit `ec3f9c2`.

The remaining wait-restart restore failure was traced to the shared fixture: each Task 7 no-effect recovery-work row needs its exact valid parent in `v4_production_intent_enrollments`. Commit `1ded021` changes only test fixture setup and assertions: it seeds that parent from existing valid cycle/case/request authority facts, uses canonical synthetic route identities, and asserts the recovery-work-to-enrollment join. Product code, migrations, and event semantics did not change. The helper-caller suite passed 17 tests in 10.04 seconds, the pg_dump/restore wait-restart test passed in 13.27 seconds, and the orphan query returned no rows.

Historical full-run record:

- Initial full run: ten deterministic stale downgrade expectation failures were identified. Its aggregate durable summary was not retained; no total is inferred. The wait-restart symptom passed on isolated rerun and was not patched without reproducible evidence.
- Full run on pre-fixture-fix artifact `effb78f`: 1 failed, 1,119 passed, 26 skipped, 3 warnings in 2,356.11 seconds. The sole failure was pg_restore rejecting the missing enrollment parent. The preserved dump reproduced it; the shared fixture correction above fixed the root cause.
- Required final full run on artifact commit `3e7f679`: 1,120 passed, 26 skipped, 3 warnings in 2,346.13 seconds; exit 0. No further full suite was run.

The final command was:

```bash
.venv/bin/python -m pytest -p no:cacheprovider tests/contracts tests/unit tests/integration tests/e2e tests/evals -m 'not live' -q -rs --tb=short
```

Run environment used unique Compose project `salience-p1-task11-final-full-1793217725-2697832`, Postgres and Temporal only, `PYTHONPATH=$PWD/src:$PWD/tests`, Alembic head 0051, and credentials unset. The cleanup trap removed its containers, volume, and network; follow-up `compose ps -a` was empty.

## Documentation and architecture verification

- `.venv/bin/python -m compileall -q src tests`: exit 0.
- `.venv/bin/python docs/v4/check.py --self-test`: exit 1.
- `.venv/bin/python docs/v4/check.py`: exit 1.
- Five shell contracts all exited 0: browser docs, browser evidence, Phase 9 docs, Phase 9 cleanup, and Phase 7–8 docs.
- `node .agents/skills/archify/bin/archify.mjs validate architecture docs/salience-phase-1-9.architecture.json --quality showcase --repo-root . --json`: exit 0; nine checks passed; zero composition errors/warnings.
- `git diff --check`: exit 0.

Both docs checker modes report frozen evidence digest/inventory drift (75 requirements; 376 evidence files; 360 inventory files; 111 Markdown files; 4 HTML files). The self-test ran six negative controls. The failures include 16 changed/missing file pins and the newly added 0047–0051 migrations, acceptance checker/modules, and recovery/routing sources/tests. Historical initial, Slice 2, and 0043 evidence was not rewritten to force green.

## Synthetic acceptance package

The package is schema-valid and binds exactly one authenticated route from the regenerated development lock, shared across all 22 rows:

- Route UUID `00000000-0000-0000-0000-000000000953`.
- Tuple SHA-256 `3ab3efdaa3c7b79ac74352da2f2d4a4990014cf07c8195c1bf08e7a6256eecbe`.
- Authenticated manifest SHA-256 `3294c1020ef0313c5d6c4200696a3d22120eb2f34ed2176af781146ea31f29d3`.

All 5 RG0 and 17 RG1 rows are `NOT RUN`; all synthetic owners/operators/reviewers/final auditor have `verified_identity: false`. Effects are disabled, fixture publication budget is zero, final audit is `NOT RUN`, and disposition is `NO_GO`. The package checker returned exit 1, `structural_status: VALID`, and `gate_state: UNCHANGED`; this is the expected valid no-go result. See `final-evidence-report.md` for all digests and exact checker command.

## Remaining review and release state

The exact committed diff is ready for independent review. External owner/operator identity, route/target qualification, credentials, named provider/database/Temporal evidence, alert destinations, and independent final audit remain absent. Therefore all five RG0 and seventeen RG1 procedures stay `NOT RUN`/`HELD`, aggregate status remains `NO_GO`, production effects remain disabled, publication budget remains zero, P2 stays blocked, and no push or merge occurred. The frozen normal docs checker status is the sole current verification limitation; its evidence was left unchanged.

## Focused evidence follow-up — review fix round 1

To close the focused-evidence reporting gap, all seven approved non-live groups were run serially on documentation-only `HEAD 8c85d7efd10906dfddf96cf1342116f74861ddba`. Source pin remained `1ded0219c13fa478e3850956a30841d89cd80a38`; artifact pin remained `3e7f6798c652412d2992f83ed7f65974532a91c9`. One fresh project, `salience-p1-task11-review-20261028210012-2771713`, ran only Postgres and Temporal; `COMPOSE_PROJECT_NAME`, source/test `PYTHONPATH`, local database and Temporal URLs were explicit, the base DB was upgraded and checked at 0051, and credentials were unset. All commands used `-m 'not live'`; no full suite was rerun.

Commands and exact pytest results (all exit 0):

1. Route/launcher, with four rollback-v2 cases reserved for group 5:

   ```bash
   /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/unit/test_production_routing.py tests/integration/test_p1_production_routing.py tests/integration/test_v4_publication_recovery_floor.py::test_launcher_v2_lock_builder_binds_exact_final_migration_head_0051_and_verifier_abi -k 'not rollback_v2 and not runs_pre_and_post_readback and not caller_supplied_hold_lists'
   ```

   26 passed, 4 deselected in 2.36s (runner 2.99s).

2. Migration 0047–0051 selection:

   ```bash
   /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/integration/test_v4_migrations.py tests/integration/test_p1_production_recovery.py -k '0047 or 0048 or 0049 or 0050 or 0051 or schema_and_migration_safety'
   ```

   39 passed, 76 deselected in 258.18s (runner 258.62s).

3. Schedule fakes/native schedule regression:

   ```bash
   /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/integration/test_p1_production_schedule_fencing.py tests/integration/test_v4_native_schedules.py tests/integration/test_v4_native_publication_schedules.py
   ```

   46 passed in 207.29s (runner 208.26s).

4. Recovery fakes/runtime waits, excluding migration-only selections:

   ```bash
   /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/integration/test_p1_production_recovery.py tests/integration/test_v4_runtime_waits.py -k 'not 0047 and not 0049 and not schema_and_migration_safety'
   ```

   65 passed, 2 deselected in 316.07s (runner 316.63s).

5. Rollback-v2 readback/handoff:

   ```bash
   /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/integration/test_p1_production_routing.py::test_launcher_v2_runs_pre_and_post_readback_before_prior_code tests/integration/test_p1_production_routing.py::test_rollback_v2_hands_off_authenticated_deployable_only_after_verification tests/integration/test_p1_production_routing.py::test_rollback_v2_fails_closed_without_authenticated_deployable_executor tests/integration/test_p1_production_routing.py::test_launcher_v2_rejects_caller_supplied_hold_lists
   ```

   4 passed in 0.93s (runner 1.47s).

6. Policy/revocation:

   ```bash
   /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/integration/test_p1_production_policy_revocation.py
   ```

   124 passed in 57.05s (runner 57.62s).

7. Checker unit suite:

   ```bash
   /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/unit/test_p1_external_acceptance.py
   ```

   45 passed in 189.82s (runner 190.16s).

Compose startup exited 0 in 6.82s; Alembic upgrade to 0051 exited 0 in 1.78s; Alembic current exited 0 in 0.79s and returned 0051 as head. Full per-command output is retained in `/tmp/salience-p1-task11-review-20261028210012-2771713.log`. Cleanup exited 0, removed the unique Compose project and temp tree, and follow-up `compose ps -a` was empty.

The package checker was rerun after these tests and returned the expected exit 1 with `VALID` / `NO_GO` / `UNCHANGED`, 22 rows. The v1 lock hash remains `1c8ac4ff92e589e27dc0af2c6dd0bc69c4295435df5a318174a2c77f3b1f2c9c`; the v2 lock remains `04514285da583fe4b32feb0ae2e710a55da6c9bdbf40d853c9454c0105fd8221`. No package/evidence JSON, product, migration, or checker changes were made for this follow-up.
