# P1 Remaining Production Development — Final Evidence Report


> **Current authoritative refresh — Task25/26/29 exact candidate:** source `cf070328d035f9010b760431173845c6afdfedee` / tree `d9f57a83364422b2062fdfbe1cf03a87c9af71a0`; exact development artifact `33cac5cffff973e9cba5e03be6b493065e5f817d` / tree `d324a3257c9149ee65b52a95333ebf9a57ceb8f2`, whose sole parent is the source. Task25 closed the async plan/preflight DNS timeout escape and received a clean independent review. Task29 passed 413 serial focused tests and one full non-live suite (1,203 passed, 26 skipped, 3 warnings). See [`progress.md`](progress.md) and [`task-29-report.md`](task-29-report.md) for exact run evidence. Task11–23 results below retain their historical scopes; Task13's separate result remains 1,150 passed, 26 skipped, and 3 warnings.

Date: 2026-10-29

## Historical result — Task11 candidate, superseded by later candidates

The exact local development candidate completed the final serial non-live test run successfully: 1,120 passed, 26 skipped, and 3 Alembic deprecation warnings; pytest exited 0 after 2,346.13 seconds. This is local development evidence only. The acceptance package is structurally valid and the read-only checker correctly returns exit 1 with `NO_GO` and `UNCHANGED`. No external production acceptance is claimed. The log path `salience-p1-task11-final-full-1793217725-2697832.log` and tested artifact `3e7f679` identify this as Task11; Task13 is the separate `8d26aa1` / `e805c27` candidate with 1,150 passed, 26 skipped, and 3 warnings.

## Historical candidate and artifact identity

- Implementation source: commit `1ded0219c13fa478e3850956a30841d89cd80a38`, tree `732f865bdfec789bbd407d3b7965611223132395`. This source pin excludes only the generated deployment lock artifacts.
- Generated artifact commit measured by the final full suite: `3e7f6798c652412d2992f83ed7f65974532a91c9`, tree `61fae364ab5f99edef47916db4d0a10d8c318c01`. The full suite ran on this artifact commit.
- Development wheel SHA-256: `5f6680065cf84e8d30df298b342af093ef578422a6f922b2dae1a53e0d4072ed`.
- Development v2 lock JSON SHA-256: `04514285da583fe4b32feb0ae2e710a55da6c9bdbf40d853c9454c0105fd8221`; companion file contains this digest. Companion-file SHA-256: `5cee4a5ebceb280123d7243f52263375f405307af5aaadab7041a8b3a581ecc2`.
- Tracked v1 lock SHA-256 remains `1c8ac4ff92e589e27dc0af2c6dd0bc69c4295435df5a318174a2c77f3b1f2c9c` (byte-identical).
- Migration head: `0051_p1_recovery_rollback_v2`.
- The external production trust digest remains unprovisioned; the v2 artifact is development-only.

## Historical local test record

Commands were serial and used an isolated Compose project with only Postgres and Temporal, the database migrated to 0051, credentials unset, and the pytest paths set to the repository source and tests directories. The final full-run project was `salience-p1-task11-final-full-1793217725-2697832`.

The final full command was:

```bash
.venv/bin/python -m pytest -p no:cacheprovider tests/contracts tests/unit tests/integration tests/e2e tests/evals -m 'not live' -q -rs --tb=short
```

Result: exit 0; 1,120 passed, 26 skipped, 3 warnings in 2,346.13 seconds. The warnings were Alembic `path_separator` deprecations in migration tests. Log: `/tmp/salience-p1-task11-final-full-1793217725-2697832.log`.

The exact route/launcher focused command passed (exit 0):

```bash
.venv/bin/python -m pytest -p no:cacheprovider tests/unit/test_production_routing.py tests/integration/test_p1_production_routing.py tests/integration/test_v4_publication_recovery_floor.py::test_launcher_v2_lock_builder_binds_exact_final_migration_head_0051_and_verifier_abi -q --tb=short
```

The initial focused migration command exited 1 with 38 passed and one failure because its `docker compose exec` schema-dump helper selected the default Compose project rather than the isolated project. It attempted to dump `task9_downgrade_1a8a45a5357d`; this was a harness/project-selection failure, not a migration assertion. The full suite later exercised the migration tests under the unique project, including the 0051 preservation guard.

Before the final full run, targeted fixture-repair checks passed: 17 helper-caller cases in 10.04 seconds and the wait-restart pg_dump/restore test in 13.27 seconds. The fixture correction seeds the exact valid `v4_production_intent_enrollments` parent for Task 7 no-effect recovery-work rows and asserts the work-to-enrollment join; product and migration code were unchanged. The database query reported no orphan work rows.

### Prior full-run failures and corrections

- The first full run exposed ten deterministic stale downgrade expectations after migration 0051 became irreversible. Six historical migration rollback tests were corrected to pin their pre-downgrade upgrade to `0050_p1_production_dispatch_reissue`; four current-head preservation checks retained `head` and accepted the exact 0051 irreversible refusal while preserving schema/version/row assertions. The test-only correction is `ec3f9c2`. The original run's durable aggregate summary was not retained; this report records only the ten known deterministic failures and does not infer an aggregate count.
- The isolated wait-restart symptom passed on a separate rerun and was not patched absent deterministic evidence.
- The next full run, on pre-fixture-fix artifact `effb78f`, had 1 failed, 1,119 passed, and 26 skipped in 2,356.11 seconds. The failure was a pg_restore foreign-key violation because the shared recovery-work test fixture omitted its valid enrollment parent. The failure was reproduced with a preserved database dump, fixed in test-only commit `1ded021`, and verified by the targeted helper-caller and wait-restart tests above.
- After refreshing the artifact for `1ded021`, the single final full run on `3e7f679` passed. No additional full suite was run after that pass.

## Historical documentation and static verification

All commands below ran serially after the final full suite. Exact exit codes are recorded; the docs checker failures were left intact because they are tied to frozen historical evidence.

| Command | Exit | Result |
|---|---:|---|
| `.venv/bin/python -m compileall -q src tests` | 0 | Passed |
| `.venv/bin/python docs/v4/check.py --self-test` | 1 | Frozen source-digest and application-inventory evidence drift; six negative controls ran |
| `.venv/bin/python docs/v4/check.py` | 1 | Same frozen source-digest and application-inventory evidence drift |
| `bash tests/scripts/test_browser_documentation.sh` | 0 | Passed |
| `bash tests/scripts/test_verify_browser_evidence.sh` | 0 | Passed |
| `bash tests/scripts/test_phase_9_documentation.sh` | 0 | Passed |
| `bash tests/scripts/test_verify_phase_9_cleanup.sh` | 0 | Passed |
| `bash tests/scripts/test_phase_7_8_documentation.sh` | 0 | Passed |
| `node .agents/skills/archify/bin/archify.mjs validate architecture docs/salience-phase-1-9.architecture.json --quality showcase --repo-root . --json` | 0 | Nine architecture checks passed; zero composition errors or warnings |
| `git diff --check` | 0 | Passed |

Both `docs/v4/check.py` modes reported 75 requirements, 376 evidence files, 360 inventory files, 111 Markdown files, and 4 HTML files. They identify changed source digests for the launch-lock builder/launcher, production dispatch/recovery/schedule/workflow modules, and multiple existing integration tests, as well as the added 0047–0051 migrations, Task 10 acceptance checker and modules, and Task 7–9 recovery/routing modules and tests. The active frozen initial, Slice 2, and 0043 evidence was not rewritten to manufacture a green result.

## Historical synthetic package and gates

Package: [`docs/v4/p1-remaining-production-development-evidence.json`](../../../docs/v4/p1-remaining-production-development-evidence.json)

- Package scope is `SYNTHETIC`; package digest is `fc5b1d107fd84493450103519d3ec905e44bae8e7a57dafe21db2f96bd78a865`; manifest digest is `3a9c3c1dbe6c59b1beb03e71b8000fd6906f8783a45ce4facf0927dd7476e235`.
- The route inventory contains exactly one actual regenerated development route, UUID `00000000-0000-0000-0000-000000000953`, reused by all 22 RG0/RG1 rows. Tuple SHA-256: `3ab3efdaa3c7b79ac74352da2f2d4a4990014cf07c8195c1bf08e7a6256eecbe`; authenticated route-manifest SHA-256: `3294c1020ef0313c5d6c4200696a3d22120eb2f34ed2176af781146ea31f29d3`.
- Five RG0 rows and seventeen RG1 rows remain `NOT RUN`. Every row has explicit missing prerequisites. Owner, operator, reviewer, and final auditor identities are labeled synthetic fixtures with `verified_identity: false`; none represents real assignment or authorization.
- `production_effects_enabled` is false, publication budget is zero, final audit is `NOT RUN`, final disposition is `NO_GO`, and no gate mutation is asserted.
- Checker command:

```bash
.venv/bin/python scripts/check-p1-external-acceptance.py \
  --package docs/v4/p1-remaining-production-development-evidence.json \
  --schema docs/v4/p1-rg0-rg1-external-acceptance.schema.json
```

  Expected and observed exit: 1, with `structural_status: VALID`, `disposition: NO_GO`, and `gate_state: UNCHANGED`. Exit 2 would indicate invalid evidence. The package file SHA-256 is `7802433872dd87dd2a18fe4a79a1e9c2de49945b08b6c738d35d11eee3451230`.

The unique Compose project was removed with its containers, volume, and network by the cleanup trap. A subsequent `docker compose --project-name salience-p1-task11-final-full-1793217725-2697832 ps -a` returned no services. No live/provider path, remote schedule, production database, alert target, push, or merge was used. Independent review of the exact committed diff remains the next handoff; it does not change the local `NO_GO` or held external status.

## Historical Task18 exact-candidate local evidence — 2026-10-29

Task18’s reviewed candidate is source `c23c4cca72021ce6621eaacd66f2bfc23f4cc129` / tree `d587cda6011bb0b974346353cec7a4856fbaf778` and artifact `be6f713b255f6b654b73ae4343db1db5c07e3840` / tree `419a33459a022d96c843313ffe5112c4e863c862`. Task17 reproduced two identical PEP517 wheels (SHA-256 `b6d48ce34584cc38ad946d4503ff3b3ad04783f6da38b2198d58b75f70d143e2`), local OCI image (`sha256:b5745f9b890da2ea5205a482de3238b2a907fa4d741107024663982119985d25`), source manifest (`25ddd9ea50638e014205bd1a6f82af557e29ffd0be712e12166ccf1ef9a7e99c`), v2 JSON/companion digest (`6095fe2f1a7c02666c1824e916bcb8dc7f91e598234276adef62af83d0026f00`), and v2 companion-file digest (`acf0080aa7a19dc73f2687e94d4a1d793425818d14265d2988315d5f9f05f0f4`). The v1 lock remains byte-identical at `1c8ac4ff92e589e27dc0af2c6dd0bc69c4295435df5a318174a2c77f3b1f2c9c`.

The exact artifact passed seven serial focused groups totaling **399 passed** and one full non-live command totaling **1,189 passed, 26 skipped, and 3 warnings**. The full log SHA-256 is `fd55c83d541c0f9778b3a557fa1c76355e431dfd944c0ca81caa5add4fe9d705`; exact focused commands, timings, and hashes are recorded in [`task-15-report.md`](task-15-report.md). The disposable Task18 project cleanup exited 0 with zero remaining containers, volumes, and networks (log SHA-256 `8bf3662c57698217b8f716c07d0ec717881bc7b930aec012ce0cc77c8c4efe32`).

This is local development evidence. All 22 external acceptance rows remain `NOT RUN`; identities remain synthetic and unverified; production effects remain disabled and budget zero; final audit remains `NOT RUN`; package disposition remains `NO_GO`; external production trust is unprovisioned; RG0/full RG1 remain `HELD`; P2 remains `BLOCKED`. No provider or external production system was contacted, and this report does not claim external qualification.

## Historical focused evidence follow-up — review fix round 1

The review identified a reporting gap: the seven focused categories were not all captured against the final exact candidate. I reran them serially on documentation-only `HEAD 8c85d7efd10906dfddf96cf1342116f74861ddba`. Source/artifact pins remained implementation source `1ded0219c13fa478e3850956a30841d89cd80a38` and generated lock commit `3e7f6798c652412d2992f83ed7f65974532a91c9`; no application, migration, or checker code changed. The exact Compose project was `salience-p1-task11-review-20261028210012-2771713`. It used only Postgres and Temporal, a local `TEST_DATABASE_URL`/`TEST_TEMPORAL_TARGET`, `PYTHONPATH=/home/admin/Salience/src:/home/admin/Salience/tests`, base migration head 0051, and no environment variable matching the credential/production/live/provider/alert/effect filter. Setup and cleanup results are in `/tmp/salience-p1-task11-review-20261028210012-2771713.log` and `...-cleanup.log`.

Compose startup exited 0 in 6.82 seconds. Alembic upgrade to `0051_p1_recovery_rollback_v2` exited 0 in 1.78 seconds; `alembic current` exited 0 in 0.79 seconds and reported 0051 as head. Each pytest command used `-p no:cacheprovider -m 'not live' -q -rs --tb=short` and exited 0:

1. Route manifest/launcher, keeping rollback-v2 cases in their own group:

   ```bash
   /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/unit/test_production_routing.py tests/integration/test_p1_production_routing.py tests/integration/test_v4_publication_recovery_floor.py::test_launcher_v2_lock_builder_binds_exact_final_migration_head_0051_and_verifier_abi -k 'not rollback_v2 and not runs_pre_and_post_readback and not caller_supplied_hold_lists'
   ```

   Exit 0; 26 passed, 4 deselected in 2.36 seconds (runner elapsed 2.99 seconds).

2. Migrations 0047–0051:

   ```bash
   /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/integration/test_v4_migrations.py tests/integration/test_p1_production_recovery.py -k '0047 or 0048 or 0049 or 0050 or 0051 or schema_and_migration_safety'
   ```

   Exit 0; 39 passed, 76 deselected in 258.18 seconds (runner elapsed 258.62 seconds).

3. Schedule fakes and native schedule regressions:

   ```bash
   /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/integration/test_p1_production_schedule_fencing.py tests/integration/test_v4_native_schedules.py tests/integration/test_v4_native_publication_schedules.py
   ```

   Exit 0; 46 passed in 207.29 seconds (runner elapsed 208.26 seconds).

4. Recovery fakes and runtime waits, excluding migration-only cases already in group 2:

   ```bash
   /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/integration/test_p1_production_recovery.py tests/integration/test_v4_runtime_waits.py -k 'not 0047 and not 0049 and not schema_and_migration_safety'
   ```

   Exit 0; 65 passed, 2 deselected in 316.07 seconds (runner elapsed 316.63 seconds).

5. Rollback-v2 handoff/readback cases:

   ```bash
   /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/integration/test_p1_production_routing.py::test_launcher_v2_runs_pre_and_post_readback_before_prior_code tests/integration/test_p1_production_routing.py::test_rollback_v2_hands_off_authenticated_deployable_only_after_verification tests/integration/test_p1_production_routing.py::test_rollback_v2_fails_closed_without_authenticated_deployable_executor tests/integration/test_p1_production_routing.py::test_launcher_v2_rejects_caller_supplied_hold_lists
   ```

   Exit 0; 4 passed in 0.93 seconds (runner elapsed 1.47 seconds).

6. Policy and revocation:

   ```bash
   /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/integration/test_p1_production_policy_revocation.py
   ```

   Exit 0; 124 passed in 57.05 seconds (runner elapsed 57.62 seconds).

7. External acceptance checker:

   ```bash
   /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/unit/test_p1_external_acceptance.py
   ```

   Exit 0; 45 passed in 189.82 seconds (runner elapsed 190.16 seconds).

After the focused run, the package CLI again exited 1 with `structural_status: VALID`, `disposition: NO_GO`, and `gate_state: UNCHANGED` for 22 rows; this is the expected synthetic no-go result. The v1 lock SHA-256 remained `1c8ac4ff92e589e27dc0af2c6dd0bc69c4295435df5a318174a2c77f3b1f2c9c`. The v2 lock SHA-256 remained `04514285da583fe4b32feb0ae2e710a55da6c9bdbf40d853c9454c0105fd8221`, and the evidence JSON was unchanged. The cleanup trap exited 0, removed the exact Compose project and temporary tree, and a follow-up `compose ps -a` showed no services. No full suite was rerun.

## Historical evidence commit reference — append-only

evidence_commit: `f0e7de0681ab7d18531e65ffc1337380e3b58fbc`

This is the report/results commit that records the seven focused verification groups. The evidence JSON remains unchanged and does not contain a self-referential commit hash.

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

## Task 13 exact-candidate refresh — 2026-10-29

The current local candidate binds source `8d26aa15ef8d6bf7c9248bdd1899cf01a352cb2a` / tree `435f7b6cea818c96790e890da9485ce08fd18ac6` to artifact commit `e805c277577da8491dca51933dadb5c36acc805b` / tree `2e7213cf989f8b7dcfd489afb076be54c4858a8b`. Two wheel builds match at SHA-256 `267c793ea403f79fe35bcb5f6a33edca32d5affa0dcf41d14eb633ef47cddc6a`; the local development image digest is `24ff7e27e99cb978307bf0b16390749536a29ad30fe36bc47150de67ac61e13d`; the v2 lock JSON and companion content SHA-256 is `c788d439807855822b3e23091545f31109c333fab79fcca319c4ca9c71c26ab1`. The v1 lock remains byte-identical.

The seven focused non-live groups passed with counts 34, 39, 51, 54, 4, 133, and 45. The one full non-live run passed 1,150, skipped 26 absent Task 8 candidate fixtures, and produced 3 Alembic deprecation warnings in 2,414.18 seconds. The synthetic package checker returned its expected exit 1 with `VALID` / `NO_GO` / `UNCHANGED` for 22 rows that remain `NOT RUN`. Production effects are disabled, RG0/full RG1 remain `HELD`, and P2 remains `BLOCKED`; no external acceptance is claimed.

The active source lock now covers 416 paths: 409 application/source paths plus the seven established boundary files. The normal and self-test documentation checker modes pass; remaining documentation/static check outputs, exact commands, hashes, and cleanup details are in [`task-13-report.md`](task-13-report.md). The unique local Compose cleanup found zero project resources. No push or merge occurred; final independent reviews remain controller-owned.

The current documentation metadata was reconciled after the initial `docs/v4/test_checks.py` run found a 291-versus-300 artifact inventory mismatch and a stale progress digest in the append-only review wrapper. The refreshed active audit covers 300 documentation artifacts and 125 Markdown files; the contract now passes 25/25. Compileall, all five documentation shell contracts, Archify validation, and `git diff --check` passed. `docs/v4/check.py --self-test` and its normal mode each returned 1 because the preserved application evidence lock has 376 evidence files against 360 inventory entries and predates the exact source candidate's changes/additions. No frozen historical application inventory was rewritten. The detailed commands, exits, durations, and distinction between current metadata and frozen evidence are recorded in [`task-11-report.md`](task-11-report.md).

## Task19 documentation and evidence refresh — 2026-10-29

The Task19 local evidence refresh bound its records to historical Task18 source `c23c4cca72021ce6621eaacd66f2bfc23f4cc129` / tree `d587cda6011bb0b974346353cec7a4856fbaf778` and artifact `be6f713b255f6b654b73ae4343db1db5c07e3840` / tree `419a33459a022d96c843313ffe5112c4e863c862`. Its 410-source/seven-boundary lock and Task18 package digests remain in dated records; Task29 is the latest candidate and is documented above. The synthetic package continues to preserve all 22 rows as `NOT RUN`, synthetic/unverified identities, effects disabled, zero budget, final audit `NOT RUN`, `NO_GO`, RG0/full RG1 `HELD`, and P2 `BLOCKED`.

Task19 documentation verification passed: AgentTube reference tests (17), AgentTube verifier, `docs/v4/test_checks.py` (25), `docs/v4/check.py` and self-test (75 requirements; six negative controls), required browser/phase shell contracts, `compileall src/tests`, Archify validation (nine checks, zero errors/warnings), and `git diff --check`. The synthetic acceptance checker exited 1 as expected with `VALID` / `NO_GO` / `UNCHANGED`. Exact commands, durations, and log hashes are in [`task-15-report.md`](task-15-report.md).


## Historical Task22 build and Task23 exact-candidate local qualification — 2026-10-29

Task22 built the development-only artifact from frozen source `db3613482ac78cd708f6aa62e64854626ba7a906` / tree `f6ed4a172065985dd13b92994cb781318e0960f9`. Artifact `c1c78dd25e467d0ae48e9c949de6aeb555875ea8` / tree `7212ba6537bcdde0196c55152ac0814c59787073` has that source as its sole parent and changes only `deployment/v4/publication-worker-launch-lock-v2-development.json` and its `.sha256` companion. The reproducible wheel is 459,013 bytes / SHA-256 `7e3073d66ffc6f6d0eb9aa4a27e08e506d82b7ecf58d749558cd6544d805a5b6`; the local image digest is `24084cf14fadcb711bccefb9ab24c72444c0740ad819d79a3756831b2e008fd6`; source manifest is `b4fbe57aacde546f7733f839211cf211d7a4d12ac35cc901f2c74f90e2c33fe8`. The v2 lock JSON and companion content hash is `d7cc873fe32beffcf042db7133b7b134be23d6ad9774b75568f337482a4e7bc1`; companion file hash is `7d30bce4d395dcd3242dff345ecc88f4fe45e2485e8ae6dba43c418d61d6420f`; the v1 lock remains unchanged at `1c8ac4ff92e589e27dc0af2c6dd0bc69c4295435df5a318174a2c77f3b1f2c9c`.

The synthetic development route uses deployment/environment/route/slot UUIDs `00000000-0000-0000-0000-000000000221` / `00000000-0000-0000-0000-000000000222` / `00000000-0000-0000-0000-000000000223` / `00000000-0000-0000-0000-000000000224`; route tuple SHA-256 `6f56661b24a5032deb6f5bfea8459b17d4efdbdee03d6131ac8ff441f47cd25c`; route manifest SHA-256 `baf5884f361937459d47e3d6467565acc54d2f6a28d0584978556b60e37935dc`. The migration head is `0051_p1_recovery_rollback_v2`, rollback verifier ABI is v2, the endpoint is unprovisioned, producer bindings are empty, and qualification remains development-only.

Task23 ran against the exact artifact in disposable Compose project `salience-p1-task23-q-a1dc00a2cc82`; seven serial focused groups passed 38 + 39 + 64 + 79 + 4 + 137 + 45 = **406 tests**. Their log SHA-256 values, in that order, are `1e2f03e2c7f7898678530c1434fbacb0dd85bd230bc1f8e269e1076b4848906c`, `17d975604e32040b266ca345452e5587e0475f91478573be629d3f606d3d38b7`, `d624d348256670eee3945e6cae22e470ef93c645f5676657312ac2a64c500044`, `63c0a23d0807d1eba71750a13b9c95998ad9a659279eb3b2d73b741e1877584a`, `54d23ef0b56f99d2cbe2d50ca07723596b53070e1dcae86069f33fb176336fed`, `474620bd421e188a028928a72ee6623c9410d1e2476bd6be3e9759699383a5e5`, and `a5a14ddee7e0936ab0472d927680395144b73883794f8faac009c9b88fc78f5b`. The one full non-live suite passed 1,196, skipped 26, and produced 3 warnings; its log SHA-256 is `228705c455ab4dad8f0216bc67bef1ac31563dcae9871a3d1252a5d27b928111`. Cleanup exited 0 with zero project-labeled containers, networks, and volumes; cleanup log SHA-256 is `54801e153aa3141f9684c5491140e4b15c68569091eacf3847e787277dd9c2b6`.

This is local development evidence only. All 22 external/live acceptance rows remain `NOT RUN`; identities remain synthetic and unverified; external final audit remains `NOT RUN`; production effects remain disabled; budget remains zero; disposition remains `NO_GO` / `UNCHANGED`; RG0 and full RG1 remain `HELD`; P2 remains `BLOCKED`; `main` remains unmerged. No external/provider/live service was contacted. The exact build and qualification records are [`Task22`](task-22-report.md) and [`Task23`](task-23-report.md).

## Task25 DNS-bounded database endpoint, Task26 final artifact, and Task29 qualification — 2026-10-29

Task25 source `cf070328d035f9010b760431173845c6afdfedee` / tree `d9f57a83364422b2062fdfbe1cf03a87c9af71a0` fixed the async plan/preflight hostname-resolution timeout escape. `_pin()` captures the active connection's host and numeric `hostaddr`; each timed plan read receives them explicitly while preserving TLS hostname checks and other connection settings. The implementation report records red evidence across `describe`, `pause`, and resume preflight and a 20-test green temporal-readback unit file. The separate review verdict is PASS/CLEAN with zero findings. Exact report copies are [`task-25-report.md`](task-25-report.md) and [`task-25-review.md`](task-25-review.md).

Task26 built the final development-only artifact `33cac5cffff973e9cba5e03be6b493065e5f817d` / tree `d324a3257c9149ee65b52a95333ebf9a57ceb8f2`, directly parented by the source and changing only the v2 development lock JSON and companion. The source archive hash is `b92d7cf53ad9fe597de01ebb119ac6bc7f4085ecbf85c7562a8d6e74bbdb7f25` at `SOURCE_DATE_EPOCH=1793272876`; its 250-pin canonical source manifest is `6d5c6e5b30487a01fb15e61345774b124d5d7db9f43eebd1e7babefd7c83be37`. Independently built wheels match at 459,327 bytes / `0c44f28534e1976e13429b3ae14df02acb4f7329e46ff824861489f574c078c6`. The route bundle, tuple, and authenticated route manifest hashes are `55618bd0f2515421407e1ce1e2723952d9261efdf9c5bafe9c48bbe2e034ccac`, `a472211d2bd22d735c711cf99b9d296de15921368a893ba96dc89e29ec89c9cc`, and `88fc34cc722d05f6c160a4842f09b4525aebd8f09bb205adcbcefb4eeb25e883`. The v2 lock content digest is `c9cdea795fe6342c5bad052a445c5b9459434df58e7fabb93f13bc83e1109fbd`, companion-file hash is `5cc49a4edcb158d3abdd397c62e1a65d5f0b449908fbff852cf915f27a9c66de`, and v1 lock remains byte-identical at `1c8ac4ff92e589e27dc0af2c6dd0bc69c4295435df5a318174a2c77f3b1f2c9c`.

The route image is the typed `linux/amd64` OCI image manifest `ccf73a53169b1e775036afda13b858114b2201905bab155e1bd8036ca49078e0` (media type `application/vnd.oci.image.manifest.v1+json`, 2,951 bytes); its config is `03ad31fde9e2a0ed843a79639a01a5baf8d1a22d53673790c7df4e6ccff28ecd`, with 14 layers. Two provenance-disabled OCI outputs share a byte-identical selected manifest, config, layers, and referenced blobs. Root `index.json` descriptor `created` annotations differ (`2026-10-29T11:51:24Z` and `2026-10-29T11:51:33Z`), and the archives differ (`3bf087d70d6a18d72af0413e554e3592ed854968757c8e4ba533e54ea5c65fed`; `869095059b7afb8a7ec24823c4a420bb3b3fe7db7285d9052adb52ab237f9760`); archive-level reproducibility is therefore not claimed. This offline derivative uses the cached Task15 runtime image `sha256:51f38f1ae8c31c86601eda50116f4e19a0b68915dcbdfcdd708bb2e6f62cd79d`, and does not establish source-Dockerfile fidelity, production image qualification, or production trust.

Task28's first exact-candidate runner attempt is preserved as a superseded harness failure. Its group 02 migration test failed when `docker compose exec` selected the wrong project because `COMPOSE_PROJECT_NAME` was omitted; the focused summary was 412 passed, 1 failed, 113 deselected, the full suite was NOT RUN, and the group was not rerun. Its corrected continuation groups passed, but they do not replace the authoritative complete run. [`task-28-report.md`](task-28-report.md) preserves the run details.

Task29 then passed all seven serial focused groups once: 38, 39, 71, 79, 4, 137, and 45 tests (413 total). The group log SHA-256 values in that order are `dbe21ec38a639959269b867e919325e811dafb00e01842c7279067b6e0dca4bf`, `5908c70966914c8fcbc60f76b5ad964d2cee44cdbfc4a92861a130bce7bf40b1`, `6e381adb96f62e05aadd5dbcd86921e67f276145b2e3b434d868f7a2107f819a`, `b19311ffb7ef98dfd7e119105099997d4400a9b28779170dd9fd16bd39172def`, `54d23ef0b56f99d2cbe2d50ca07723596b53070e1dcae86069f33fb176336fed`, `dc8426427f1c203aacdc13e6e393f1ceb9c8c031ed90fcc3256101ef4adb3cb0`, and `ba392f1cf9c8e697e920aae6220c06e0500b35a531192e5d3e1d69c4285bbe23`. The single full non-live run passed 1,203, skipped 26, and emitted 3 warnings; its log SHA-256 is `d7d1d0ffe497665fb5be923cfd2a5022cf283b11a36610e3abcd7d35e5a1dc74`. Task29 ran from `2026-10-29T12:44:13+00:00` to `2026-10-29T13:49:22+00:00` in disposable project `salience-p1-task29-q-291ea23ae3c0`; the report contains exact serial commands/times. Cleanup exited 0 with zero containers, networks, and volumes; cleanup log SHA-256 is `d3b92ee4fc832d4b066c1be4f56f9de5ea664985f4dd1db56b05e8ae75e95b7e`. No provider, production, live schedule, alert, or external acceptance service was contacted.

All 22 external/live rows remain `NOT RUN`; identities are synthetic and unverified; provider acceptance and external final audit remain `NOT RUN`; production trust is unprovisioned; production effects are disabled; publication budget is zero; the package remains `NO_GO` / `UNCHANGED`; RG0 and full RG1 remain `HELD`; P2 and later phases remain gated; `main` remains unmerged. No exact-head hosted CI or formal PR approval is asserted for this documentation refresh.

## Task30 documentation and evidence binding — 2026-10-29

The current source lock was recomputed from Git blobs for exact source `cf070328d035f9010b760431173845c6afdfedee` and contains 417 paths: 410 under `src/`, `tests/`, `migrations/`, and `scripts/`, plus seven established boundary files. The lock file SHA-256 is `bcb4c35b748307933bb375b90c71acf13bd127df103c5f7f7da44a273e1a607b`. The exact development artifact remains `33cac5cffff973e9cba5e03be6b493065e5f817d`; no application/source/test/migration/submodule bytes were changed in this documentation refresh.

The current synthetic acceptance package file SHA-256 is `8c8764119ca6d99d00216896bdc2e9e4d66a7e7c7eadbfc73638c401f1ae1f09` and its canonical package digest is `6036f18b5654c832526560e4f7b9fa799f7e24374f26bca9376aa18e28036290`. The checker returned the required exit 1 with `structural_status: VALID`, `NO_GO`, and `UNCHANGED`; all 22 external rows remain `NOT RUN`, production effects are disabled, and RG0/full RG1 remain `HELD`. Task30 results for the 14 serial documentation/evidence checks are recorded under `verification.task30_documentation_checks` in [`documentation-status-audit.json`](../../../docs/v4/documentation-status-audit.json). No exact-head hosted CI or formal PR approval is asserted for this refresh.

Authoritative report sources were copied to the SDD directory. To make the staged diff check pass, only trailing spaces/tabs were removed from three copied Markdown reports. Their source and durable-copy hashes are both recorded below; the other three remain byte-identical to their authoritative sources. No wording or result data was otherwise changed.

| Report | Authoritative source SHA-256 | Durable copy SHA-256 | Copy handling |
|---|---|---|---|
| `task-25-report.md` | `d9a9c4bf5394c7e3042bf0911e8c2c62b7b1992e271ed0b9a3659b9c08c71ee8` | `d9a9c4bf5394c7e3042bf0911e8c2c62b7b1992e271ed0b9a3659b9c08c71ee8` | Byte-identical |
| `task-25-review.md` | `07d0f0288a4e13473f1ec02c3788d65bd1d19b5f426a24f72c5b88c547a2571b` | `48dfefbda13e7cf448736b9f9e7e49ca5474a96dcfca5a0dfa31aa129d735f54` | Trailing spaces/tabs normalized |
| `task-26-build-report.md` | `da464f968a5e7c53420b372b527118453f52cf6aa54dd4e4b35e503501cb4c6b` | `da464f968a5e7c53420b372b527118453f52cf6aa54dd4e4b35e503501cb4c6b` | Byte-identical |
| `task-26-build-review.md` | `0f6d93d74769560657572789416efd838ec8bcff31db3e06826894d2e20601af` | `0f6d93d74769560657572789416efd838ec8bcff31db3e06826894d2e20601af` | Byte-identical |
| `task-28-report.md` | `e7b4b136fcb7936cc856187ee2d91066e292c5afa0e85b2146bc2f3f6cd0d512` | `277e5f65953a5f19dcc327a5062211fc3abcaceae2955c14ff952b46f05ef3dc` | Trailing spaces/tabs normalized |
| `task-29-report.md` | `4952f95a5dc9ab01e29102edbe39fbded56917ea697ac0550d54c1675ae36fa6` | `f954f25db4be2e3eb4e0e2e59da85ac525c90f84ba719399c1b5bd646470638c` | Trailing spaces/tabs normalized |
