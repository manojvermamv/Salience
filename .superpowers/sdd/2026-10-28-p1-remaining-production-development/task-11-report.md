# Task 11 report — exact-candidate development acceptance


> **Superseded by Task 13:** the latest exact-candidate evidence binds source `8d26aa15ef8d6bf7c9248bdd1899cf01a352cb2a` / tree `435f7b6cea818c96790e890da9485ce08fd18ac6` to artifact commit `e805c277577da8491dca51933dadb5c36acc805b` / tree `2e7213cf989f8b7dcfd489afb076be54c4858a8b`. Task 11 records below remain historical; see [`task-13-report.md`](task-13-report.md) for complete current evidence.

Date: 2026-10-28

## Historical outcome — superseded by the exact-candidate refresh

The exact candidate's single final serial non-live suite passed: 1,120 passed, 26 skipped, 3 warnings, pytest exit 0 (2,346.13 seconds). A synthetic 22-row external-acceptance package was generated and accepted structurally; its checker exits 1 as required for `VALID` / `NO_GO` / `UNCHANGED`. Production acceptance is not established. The final evidence package and detailed report are in `docs/v4/p1-remaining-production-development-evidence.json` and `final-evidence-report.md`.

## Historical source and generated-artifact pins

- Implementation source commit/tree: `1ded0219c13fa478e3850956a30841d89cd80a38` / `732f865bdfec789bbd407d3b7965611223132395`.
- Artifact refresh commit/tree: `3e7f6798c652412d2992f83ed7f65974532a91c9` / `61fae364ab5f99edef47916db4d0a10d8c318c01`.
- Development wheel SHA-256: `5f6680065cf84e8d30df298b342af093ef578422a6f922b2dae1a53e0d4072ed`.
- Development v2 lock SHA-256: `04514285da583fe4b32feb0ae2e710a55da6c9bdbf40d853c9454c0105fd8221`. Companion content agrees; v1 lock remains byte-identical at SHA-256 `1c8ac4ff92e589e27dc0af2c6dd0bc69c4295435df5a318174a2c77f3b1f2c9c`.
- Exact migration head: `0051_p1_recovery_rollback_v2`.

The lock was regenerated after all test-only source corrections. The source pin excludes generated deployment artifacts; the separately recorded artifact commit is the tree on which the full test run was performed. Evidence avoids self-hashing by referring to source and artifact commits only, not a commit containing itself.

## Historical corrections and test history

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

## Historical documentation and architecture verification

- `.venv/bin/python -m compileall -q src tests`: exit 0.
- `.venv/bin/python docs/v4/check.py --self-test`: exit 1.
- `.venv/bin/python docs/v4/check.py`: exit 1.
- Five shell contracts all exited 0: browser docs, browser evidence, Phase 9 docs, Phase 9 cleanup, and Phase 7–8 docs.
- `node .agents/skills/archify/bin/archify.mjs validate architecture docs/salience-phase-1-9.architecture.json --quality showcase --repo-root . --json`: exit 0; nine checks passed; zero composition errors/warnings.
- `git diff --check`: exit 0.

Both docs checker modes report frozen evidence digest/inventory drift (75 requirements; 376 evidence files; 360 inventory files; 111 Markdown files; 4 HTML files). The self-test ran six negative controls. The failures include 16 changed/missing file pins and the newly added 0047–0051 migrations, acceptance checker/modules, and recovery/routing sources/tests. Historical initial, Slice 2, and 0043 evidence was not rewritten to force green.

## Historical synthetic acceptance package

The package is schema-valid and binds exactly one authenticated route from the regenerated development lock, shared across all 22 rows:

- Route UUID `00000000-0000-0000-0000-000000000953`.
- Tuple SHA-256 `3ab3efdaa3c7b79ac74352da2f2d4a4990014cf07c8195c1bf08e7a6256eecbe`.
- Authenticated manifest SHA-256 `3294c1020ef0313c5d6c4200696a3d22120eb2f34ed2176af781146ea31f29d3`.

All 5 RG0 and 17 RG1 rows are `NOT RUN`; all synthetic owners/operators/reviewers/final auditor have `verified_identity: false`. Effects are disabled, fixture publication budget is zero, final audit is `NOT RUN`, and disposition is `NO_GO`. The package checker returned exit 1, `structural_status: VALID`, and `gate_state: UNCHANGED`; this is the expected valid no-go result. See `final-evidence-report.md` for all digests and exact checker command.

## Historical review and release state

The exact committed diff is ready for independent review. External owner/operator identity, route/target qualification, credentials, named provider/database/Temporal evidence, alert destinations, and independent final audit remain absent. Therefore all five RG0 and seventeen RG1 procedures stay `NOT RUN`/`HELD`, aggregate status remains `NO_GO`, production effects remain disabled, publication budget remains zero, P2 stays blocked, and no push or merge occurred. The frozen normal docs checker status is the sole current verification limitation; its evidence was left unchanged.

## Historical focused evidence follow-up — review fix round 1

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

## Historical evidence commit reference — append-only

evidence_commit: `f0e7de0681ab7d18531e65ffc1337380e3b58fbc`

This separately identifies the preceding focused-results/report commit; it does not alter the evidence JSON.

## Exact-candidate refresh — source `b4865d1`, artifact `3c62a4f`

This is the current Task 11 evidence. The prior exact-candidate records in this file refer to source `1ded0219c13fa478e3850956a30841d89cd80a38` and artifact commit `3e7f6798c652412d2992f83ed7f65974532a91c9`; they remain historical and are superseded for claims about the current candidate.

### Source, artifact, and route pins

- Clean implementation source: commit `b4865d115c225c828e54777024f1de726fa4cd86`, tree `2d34ef60e603eda8582aabc18ed21284e961d1b3`. The source pin covers application sources, migrations, `alembic.ini`, and the launcher; it excludes generated deployment locks.
- Generated artifact commit measured by all tests: `3c62a4f01e974d97225100bb2afc3a3665ef601d`, tree `512aaa7046232973e6725c3e10de2c721aef06ca`. It changes only the two development v2 lock files.
- Wheel SHA-256: `1905451b9f795575930fdf75c2dc39671a4e42e59a659cc5c94be4eaab096041` (452,710 bytes). Two clean `git archive` source exports and isolated PEP 517 builds produced byte-identical wheels (`cmp` exit 0).
- Locally built OCI image digest: `b36bfb3e026abe41ff0c8045a98bb39ec85d38a049b9078bcbf7b476cbc96c8c`. Its route use is development-only and is not signed or externally qualified.
- Source manifest digest: `8fcb78033e94b4ca2aa987e1c743ff5878a46d7f27216ed9acb3235aae553f3b`.
- Development v2 launch-lock JSON and its companion content: `d8d8762c59d46a184e55c8b8533f5578d004211e0bb28e3cf986d0f5e6406319`. Companion-file SHA-256: `31e8e744bb0d511bff8383f90cf066e20e481dcbd166c70d15a8d20d80e79b26`. Two lock-builder invocations over the two wheel builds emitted byte-identical JSON and companion files.
- The v1 lock `deployment/v4/publication-worker-launch-lock.json` remains byte-identical: SHA-256 `1c8ac4ff92e589e27dc0af2c6dd0bc69c4295435df5a318174a2c77f3b1f2c9c`. The external production trust digest remains unprovisioned.
- Migration head is `0051_p1_recovery_rollback_v2`; rollback verifier ABI is `salience.p1-recovery-rollback-verifier.v2`.
- One development route is shared across the synthetic package: UUID `00000000-0000-0000-0000-000000000953`, tuple SHA-256 `3ab3efdaa3c7b79ac74352da2f2d4a4990014cf07c8195c1bf08e7a6256eecbe`, authenticated route-manifest SHA-256 `0ebe9f3c9fc4cee6cc190098c9bffcc943dae764240e83f91916824ab320c0d0`.

The wheel build command, run on each of two independently extracted source trees, was:

```bash
SOURCE_DATE_EPOCH="$(git show -s --format=%ct b4865d115c225c828e54777024f1de726fa4cd86)" \
  .venv/bin/python -m pip wheel --no-deps --wheel-dir "$TASK_TMP/wheels-N" "$TASK_TMP/source-N"
```

The lock builder ran twice with `--application-commit b4865d115c225c828e54777024f1de726fa4cd86`, the respective wheel, the extracted pinned Docker runtime tree, the regular `/usr/local/bin/python3.13` bytes as `--interpreter-source`, and the route bundle containing the measured wheel and image digests. Both returned exit 0 and printed source-manifest digest `8fcb78033e94b4ca2aa987e1c743ff5878a46d7f27216ed9acb3235aae553f3b`; lock JSON and companion `cmp` checks returned 0. An initial wheel attempt with build isolation disabled stopped before building because `.venv` does not include setuptools; the successful two builds used pip's isolated build environment. An initial temporary runtime extraction was rejected because the staging path did not preserve the target installation hierarchy; rebuilding the staging tree to match `/usr/local/lib/python3.13/site-packages` and its console-script RECORD path made both validations pass. Neither setup correction changed tracked source.

### Isolated environment and focused acceptance

All tests ran serially on artifact commit `3c62a4f01e974d97225100bb2afc3a3665ef601d`. Unique Compose project: `salience-p1-task11-refresh-20261028-2827037`. The project started only `postgres` and `temporal`; local addresses were Postgres `172.21.0.2:5432` and Temporal `172.21.0.3:7233`. The database was upgraded and checked at head `0051_p1_recovery_rollback_v2`. Test and migration processes used `env -i` with only the local database/Temporal addresses, project name, repository `PYTHONPATH`, `PATH`, `HOME`, and `DOCKER_CONTEXT=default`; provider, production, alert, and live credentials were unset.

Each focused command used the shared prefix `.venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short`; all exited 0:

1. Complete routing, unit route contracts, and exact-head launcher lock:

   ```bash
   .venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/unit/test_production_routing.py tests/integration/test_p1_production_routing.py tests/integration/test_v4_publication_recovery_floor.py::test_launcher_v2_lock_builder_binds_exact_final_migration_head_0051_and_verifier_abi
   ```

   Exit 0; 34 passed in 2.02 seconds; runner elapsed 3 seconds.

2. Migrations 0047–0051 and migration safety/current-head cases:

   ```bash
   .venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/integration/test_v4_migrations.py tests/integration/test_p1_production_recovery.py -k '0047 or 0048 or 0049 or 0050 or 0051 or schema_and_migration_safety'
   ```

   Exit 0; 39 passed, 81 deselected in 253.23 seconds; runner elapsed 254 seconds.

3. Schedule fakes and native schedule regressions:

   ```bash
   .venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/integration/test_p1_production_schedule_fencing.py tests/integration/test_v4_native_schedules.py tests/integration/test_v4_native_publication_schedules.py
   ```

   Exit 0; 46 passed in 188.71 seconds; runner elapsed 189 seconds.

4. Complete production recovery module, including the two clock-rollback replay regressions:

   ```bash
   .venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/integration/test_p1_production_recovery.py
   ```

   Exit 0; 47 passed in 326.63 seconds; runner elapsed 327 seconds.

5. Rollback-v2 readback and handoff:

   ```bash
   .venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/integration/test_p1_production_routing.py::test_launcher_v2_runs_pre_and_post_readback_before_prior_code tests/integration/test_p1_production_routing.py::test_rollback_v2_hands_off_authenticated_deployable_only_after_verification tests/integration/test_p1_production_routing.py::test_rollback_v2_fails_closed_without_authenticated_deployable_executor tests/integration/test_p1_production_routing.py::test_launcher_v2_rejects_caller_supplied_hold_lists
   ```

   Exit 0; 4 passed in 0.93 seconds; runner elapsed 2 seconds.

6. Policy and revocation:

   ```bash
   .venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/integration/test_p1_production_policy_revocation.py
   ```

   Exit 0; 124 passed in 57.33 seconds; runner elapsed 58 seconds.

7. External-acceptance checker unit suite:

   ```bash
   .venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/unit/test_p1_external_acceptance.py
   ```

   Exit 0; 45 passed in 189.90 seconds; runner elapsed 190 seconds.

The complete durable log, including setup, commands, outputs, and cleanup, is `/tmp/salience-p1-task11-refresh.t40dU7/development-suite.log`; per-command logs are beside it. There were no focused-suite skips or warnings. The one `alembic current` command exited 0 and reported `0051_p1_recovery_rollback_v2 (head)`.

### One final full non-live suite

The required full suite ran exactly once on the exact artifact commit. Command:

```bash
.venv/bin/python -m pytest -p no:cacheprovider tests/contracts tests/unit tests/integration tests/e2e tests/evals -m 'not live' -q -rs --tb=short
```

Exit 0; 1,129 passed, 26 skipped, and 3 warnings in 2,303.57 seconds (38:23); runner elapsed 2,307 seconds. The three warnings are Alembic `path_separator` deprecations in `test_owner_binding_upgrade_preserves_or_holds_existing_history` for the `original`, `foreign`, and `concurrent_foreign` cases. All 26 skips state that the historical Task 8 exact candidate or its candidate fixture is absent; no production or live acceptance case was run. No full-suite rerun occurred. Full captured output: `/tmp/salience-p1-task11-refresh.t40dU7/final_full_non_live.log`.

### Synthetic evidence and gate state

`docs/v4/p1-remaining-production-development-evidence.json` now binds the source, artifact, route, lock, and migration values above. Its file SHA-256 is `a6e97ad749a121c0498057dae82f3a8dbfbe8f70aecab8a43de2c57314021f64`; package digest is `df780f00c484fcf10a9f1d6a4ef59d9e8eddd374a56eebf769dc3fb6ff54c095`; manifest SHA-256 is `daf990e4ba3b610c75fa65ed4790c90c4d54acbdf80ac1dfb6f7934cc5bda1a8`.

The package checker command was:

```bash
.venv/bin/python scripts/check-p1-external-acceptance.py --package docs/v4/p1-remaining-production-development-evidence.json --schema docs/v4/p1-rg0-rg1-external-acceptance.schema.json
```

It returned the required exit 1 with `structural_status: VALID`, `disposition: NO_GO`, and `gate_state: UNCHANGED`. The package is `SYNTHETIC`, contains exactly 22 external rows (five RG0 and seventeen RG1), and every row is `NOT RUN`. All owner/operator/reviewer/auditor identities have `verified_identity: false`; verified identity count is zero. Production effects remain false, publication budget is zero, final audit is `NOT RUN`, final disposition remains `NO_GO`, and no gate mutation is claimed.

### Cleanup and limits

The EXIT cleanup trap returned 0 and removed the exact Compose project's containers, Postgres volume, and network; follow-up counts were zero containers, zero volumes, and zero networks. The temporary image-build container and image were also removed by exact name. No prune command ran, and shared host images or other Compose projects were left untouched. Production effects stayed disabled; no provider, production database, remote schedule, alert destination, RG0/RG1 procedure, push, or merge was used. This is local synthetic development evidence, not external qualification.

A machine-readable scoped consistency check found all source/artifact/route fields in the package consistent with the generated lock and all external rows/gates still held. Independent scoped rereview is pending the parent agent; this report does not claim that a self-check is independent review.

### Documentation and static verification for this refresh

The active documentation inventory and its append-only digest wrapper were refreshed after the first contract run exposed two stale current-metadata entries: 291 inventory rows versus 300 tracked artifact paths, and a missing current `docs/v4/progress.md` digest. The audit now covers 300 artifact paths and 125 Markdown files. This updates current documentation metadata only; the frozen initial audit and its historical review chain remain unchanged.

The final command results were:

| Command | Exit | Result | Elapsed |
| --- | ---: | --- | ---: |
| `.venv/bin/python -m compileall -q src tests` | 0 | Passed | 0 sec |
| `.venv/bin/python docs/v4/test_checks.py` | 0 | 25 tests passed | 1 sec |
| `.venv/bin/python docs/v4/check.py --self-test` | 1 | Six negative controls ran; frozen evidence pins do not match this later source candidate | 0 sec |
| `.venv/bin/python docs/v4/check.py` | 1 | Same frozen evidence/inventory mismatch | 1 sec |
| Five documentation shell contracts listed below | 0 each | Passed | 0 sec each |
| Archify validation listed below | 0 | Nine checks passed; zero composition errors or warnings | 0 sec |
| `git diff --check` | 0 | Passed | 0 sec |

The two checker modes report 376 frozen evidence files against 360 inventory paths, changed/missing pins for 16 paths, and the later candidate's added migrations 0047–0051, acceptance/recovery modules, and tests. Those checks remain failed against the preserved historical lock; it was not rewritten to manufacture a pass.

The shell contracts were run separately and serially:

```bash
bash tests/scripts/test_browser_documentation.sh
bash tests/scripts/test_verify_browser_evidence.sh
bash tests/scripts/test_phase_9_documentation.sh
bash tests/scripts/test_verify_phase_9_cleanup.sh
bash tests/scripts/test_phase_7_8_documentation.sh
```

Archify command:

```bash
node .agents/skills/archify/bin/archify.mjs validate architecture docs/salience-phase-1-9.architecture.json --quality showcase --repo-root . --json
```

The first documentation-test run exited 1 before metadata refresh; the two failures were the stale inventory count and progress digest described above. After the refresh, the full 25-test command passed. Exact outputs and per-command times are in `/tmp/salience-p1-task11-refresh.t40dU7/documentation-checks.log` and its per-command logs. The complete docs/static commands were run in an environment containing only `PATH` and `HOME`.

After the planned checks, a collection-only probe was attempted to inspect the routing module's collected count:

```bash
.venv/bin/python -m pytest --collect-only -q tests/integration/test_p1_production_routing.py
```

It exited 3 before collection. The repository's `tests/conftest.py::pytest_sessionstart` starts the default Compose project whenever either `TEST_DATABASE_URL` or `TEST_TEMPORAL_TARGET` is absent; because this probe did not provide those test variables, it invoked `docker compose up -d postgres temporal` for project `salience`, then Alembic failed while importing `salience` (`ModuleNotFoundError`) before any database connection. Compose reported both services as already running. Read-only inspection afterward showed containers created at 2026-10-28T02:07Z, Postgres started at 02:07Z, Temporal started at 05:29Z, and restart counts of zero, all predating this probe. No migration or database connection occurred, no stop/restart/prune command ran, and the required unique Task 11 Compose project had already been removed. This probe was not part of the candidate test results and is recorded here as a harness limitation.

## Task 13 exact-candidate refresh — source `8d26aa1`, artifact `e805c27`

Task 13 supersedes the preceding Task 11 candidate for current-source claims. Exact source is `8d26aa15ef8d6bf7c9248bdd1899cf01a352cb2a` / tree `435f7b6cea818c96790e890da9485ce08fd18ac6`; the generated-artifact commit measured by every test is `e805c277577da8491dca51933dadb5c36acc805b` / tree `2e7213cf989f8b7dcfd489afb076be54c4858a8b`.

- Two independent wheel builds were byte-identical at SHA-256 `267c793ea403f79fe35bcb5f6a33edca32d5affa0dcf41d14eb633ef47cddc6a` (455,511 bytes). The development image digest is `24ff7e27e99cb978307bf0b16390749536a29ad30fe36bc47150de67ac61e13d`; source-manifest digest is `b1834357f140bef0a855c096eb4b8d5e4b2acc38862d911af4e440d1da743c15`. The v2 lock JSON/companion content SHA-256 is `c788d439807855822b3e23091545f31109c333fab79fcca319c4ca9c71c26ab1`; v1 remains byte-identical at `1c8ac4ff92e589e27dc0af2c6dd0bc69c4295435df5a318174a2c77f3b1f2c9c`.
- Focused groups passed serially: route/launcher 34; migrations 0047–0051 39 (88 deselected); schedule/native 51; complete recovery/alerts 54; rollback-v2 handoff 4; policy/revocation 133; external-acceptance checker unit suite 45. The single full non-live suite passed 1,150, skipped 26 documented absent Task 8 fixtures, and produced 3 Alembic deprecation warnings in 2,414.18 seconds. The full suite ran once, with no rerun.
- Synthetic package digest is `ef310caaa0653e14629d3e87cc72f1e29cdffa7edb52e03263a02a819cf2181d`; manifest SHA-256 is `d9a2f2054b408f8d206bca05b2b226e9c31cd4374e43013704c4ccfc6cb29b84`. Its 22 rows remain `NOT RUN`, identities are synthetic and unverified, effects are disabled, final audit remains `NOT RUN`, and disposition remains `NO_GO`. The acceptance checker returned its expected exit 1 with `VALID` / `NO_GO` / `UNCHANGED`.
- The active `docs/v4/evidence-lock.json` now covers 409 current files under `src`, `tests`, `migrations`, and `scripts` plus the seven established boundary files (416 total), preserving nested historical lock data. Both normal and self-test checker modes pass. Exact commands, results, active-lock digest, and limits are in [`task-13-report.md`](task-13-report.md).
- The unique local Compose project was removed with zero remaining containers, volumes, and networks. No external target, provider, production database, push, or merge was used. RG0/full RG1 remain HELD; P2 remains BLOCKED; independent review remains controller-owned.
