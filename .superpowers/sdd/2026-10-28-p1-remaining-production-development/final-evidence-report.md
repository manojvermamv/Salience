# P1 Remaining Production Development — Final Evidence Report

Date: 2026-10-28

## Result

The exact local development candidate completed the final serial non-live test run successfully: 1,120 passed, 26 skipped, and 3 Alembic deprecation warnings; pytest exited 0 after 2,346.13 seconds. This is local development evidence only. The acceptance package is structurally valid and the read-only checker correctly returns exit 1 with `NO_GO` and `UNCHANGED`. No external production acceptance is claimed.

## Candidate and artifact identity

- Implementation source: commit `1ded0219c13fa478e3850956a30841d89cd80a38`, tree `732f865bdfec789bbd407d3b7965611223132395`. This source pin excludes only the generated deployment lock artifacts.
- Generated artifact commit measured by the final full suite: `3e7f6798c652412d2992f83ed7f65974532a91c9`, tree `61fae364ab5f99edef47916db4d0a10d8c318c01`. The full suite ran on this artifact commit.
- Development wheel SHA-256: `5f6680065cf84e8d30df298b342af093ef578422a6f922b2dae1a53e0d4072ed`.
- Development v2 lock JSON SHA-256: `04514285da583fe4b32feb0ae2e710a55da6c9bdbf40d853c9454c0105fd8221`; companion file contains this digest. Companion-file SHA-256: `5cee4a5ebceb280123d7243f52263375f405307af5aaadab7041a8b3a581ecc2`.
- Tracked v1 lock SHA-256 remains `1c8ac4ff92e589e27dc0af2c6dd0bc69c4295435df5a318174a2c77f3b1f2c9c` (byte-identical).
- Migration head: `0051_p1_recovery_rollback_v2`.
- The external production trust digest remains unprovisioned; the v2 artifact is development-only.

## Local test record

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

## Documentation and static verification

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

## Synthetic package and gates

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

## Focused evidence follow-up — review fix round 1

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

## Evidence commit reference — append-only

evidence_commit: `f0e7de0681ab7d18531e65ffc1337380e3b58fbc`

This is the report/results commit that records the seven focused verification groups. The evidence JSON remains unchanged and does not contain a self-referential commit hash.
