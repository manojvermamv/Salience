# Task 32 exact-candidate local qualification

Status: **PASSED**

Artifact HEAD: `cc9d55fd67f10f7f8d831e21b9331ecae08cf039`
Supplied source reference: `8c31e84c9d71d0043b1ca736d9bc4db05cfeee7d`
Compose project: `salience-p1-task32-q-9b9a51dae757`
Evidence directory: `/tmp/salience-task32-exact-qualification-20261029T152227Z-9b9a51dae757`
Started: `2026-10-29T15:22:30+00:00`
Updated at: `2026-10-29T16:28:13Z`

The exact artifact was required clean at start and final checks. Only local Compose `postgres` and `temporal` services were started with `--pull never`; no provider, production, live schedule, alert, or external acceptance service was contacted. Migration and pytest subprocesses used a clean environment and the same unique `COMPOSE_PROJECT_NAME` plus `DATABASE_URL`, `TEST_DATABASE_URL`, `TEMPORAL_TARGET`, and `TEST_TEMPORAL_TARGET`, all pointing to this disposable local stack. No provider or production credentials were passed.

Expected fresh migration/current revision: `0051_p1_recovery_rollback_v2`. External provider/acceptance rows remain **NOT RUN**; group 07 is the local external-acceptance unit test. Schedule/readback included the complete current `tests/unit/test_temporal_schedule_readback.py` file and ran once. External effects were disabled by excluding `live` tests and using local service endpoints.

Environment keys passed to migration and test subprocesses: `COMPOSE_PROJECT_NAME`, `DATABASE_URL`, `TEST_DATABASE_URL`, `TEMPORAL_TARGET`, `TEST_TEMPORAL_TARGET`, `DOCKER_CONTEXT`, `HOME`, `PATH`. Endpoint and credential values are omitted/redacted.

## Command evidence

### preflight-head

Command: `git rev-parse HEAD`

Started: `2026-10-29T15:22:30Z`; ended: `2026-10-29T15:22:30Z`; exit: `0`; elapsed: `0.002s`.

Environment keys: `HOME, PATH`.

Log SHA256: `347aae9b744aabd95131fbd2cdfbe4e68ad7b4d6b0980b7654b31604bde1bf9f` (41 bytes) at `/tmp/salience-task32-exact-qualification-20261029T152227Z-9b9a51dae757/verification/preflight-head.log`.

### preflight-parent

Command: `git rev-parse HEAD^`

Started: `2026-10-29T15:22:30Z`; ended: `2026-10-29T15:22:30Z`; exit: `0`; elapsed: `0.002s`.

Environment keys: `HOME, PATH`.

Log SHA256: `2acd0938017259dff861b455f22129e2b30aabae9eda891dd7bd78cc0d7d04c7` (41 bytes) at `/tmp/salience-task32-exact-qualification-20261029T152227Z-9b9a51dae757/verification/preflight-parent.log`.

### preflight-worktree

Command: `git status --porcelain=v1 --untracked-files=all`

Started: `2026-10-29T15:22:30Z`; ended: `2026-10-29T15:22:30Z`; exit: `0`; elapsed: `0.009s`.

Environment keys: `HOME, PATH`.

Log SHA256: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` (0 bytes) at `/tmp/salience-task32-exact-qualification-20261029T152227Z-9b9a51dae757/verification/preflight-worktree.log`.

### compose-config

Command: `docker --context default compose --env-file <empty env file> --project-name salience-p1-task32-q-9b9a51dae757 config --format json (resolved values suppressed)`

Started: `2026-10-29T15:22:30Z`; ended: `2026-10-29T15:22:30Z`; exit: `0`; elapsed: `0.086s`.

Environment keys: `COMPOSE_PROJECT_NAME, DOCKER_CONTEXT, HOME, PATH`.

Log SHA256: `6abf6441353bd3bf3163cba4b2c5a61f9939740f47f2589071722f350a370d95` (148 bytes) at `/tmp/salience-task32-exact-qualification-20261029T152227Z-9b9a51dae757/verification/compose-config.log`.

### compose-up-postgres-temporal

Command: `docker --context default compose --env-file <empty env file> --project-name salience-p1-task32-q-9b9a51dae757 up -d --pull never --wait --wait-timeout 180 postgres temporal`

Started: `2026-10-29T15:22:30Z`; ended: `2026-10-29T15:22:37Z`; exit: `0`; elapsed: `6.874s`.

Environment keys: `COMPOSE_PROJECT_NAME, DOCKER_CONTEXT, HOME, PATH`.

Log SHA256: `374c2f0893cc8117f9efbfed6c3f3eeecbf38697a88b2674a2b0ae99599d9e3c` (1418 bytes) at `/tmp/salience-task32-exact-qualification-20261029T152227Z-9b9a51dae757/verification/compose-up-postgres-temporal.log`.

### compose-ps-postgres

Command: `docker --context default compose --env-file /tmp/salience-task32-exact-qualification-20261029T152227Z-9b9a51dae757/empty-compose.env --project-name salience-p1-task32-q-9b9a51dae757 ps -q postgres`

Started: `2026-10-29T15:22:37Z`; ended: `2026-10-29T15:22:37Z`; exit: `0`; elapsed: `0.096s`.

Environment keys: `COMPOSE_PROJECT_NAME, DOCKER_CONTEXT, HOME, PATH`.

Log SHA256: `26044d3af6780cab9483422b0ca8994f067761427e2a4b3e4b4affd3833b4015` (61 bytes) at `/tmp/salience-task32-exact-qualification-20261029T152227Z-9b9a51dae757/verification/compose-ps-postgres.log`.

### inspect-ip-postgres

Command: `docker --context default inspect <postgres container> local IP template`

Started: `2026-10-29T15:22:37Z`; ended: `2026-10-29T15:22:37Z`; exit: `0`; elapsed: `0.023s`.

Environment keys: `DOCKER_CONTEXT, HOME, PATH`.

Log SHA256: `6f48ff599ac61190f412d4bd7f8b54f8b5a0c4ac8723c996180978d6b004c771` (55 bytes) at `/tmp/salience-task32-exact-qualification-20261029T152227Z-9b9a51dae757/verification/inspect-ip-postgres.log`.

### compose-ps-temporal

Command: `docker --context default compose --env-file /tmp/salience-task32-exact-qualification-20261029T152227Z-9b9a51dae757/empty-compose.env --project-name salience-p1-task32-q-9b9a51dae757 ps -q temporal`

Started: `2026-10-29T15:22:37Z`; ended: `2026-10-29T15:22:37Z`; exit: `0`; elapsed: `0.100s`.

Environment keys: `COMPOSE_PROJECT_NAME, DOCKER_CONTEXT, HOME, PATH`.

Log SHA256: `7fcb4d052da25826b9d895aede82b48672b69e26ba4656720b484fc3f9b8a8ca` (61 bytes) at `/tmp/salience-task32-exact-qualification-20261029T152227Z-9b9a51dae757/verification/compose-ps-temporal.log`.

### inspect-ip-temporal

Command: `docker --context default inspect <temporal container> local IP template`

Started: `2026-10-29T15:22:37Z`; ended: `2026-10-29T15:22:37Z`; exit: `0`; elapsed: `0.029s`.

Environment keys: `DOCKER_CONTEXT, HOME, PATH`.

Log SHA256: `6f48ff599ac61190f412d4bd7f8b54f8b5a0c4ac8723c996180978d6b004c771` (55 bytes) at `/tmp/salience-task32-exact-qualification-20261029T152227Z-9b9a51dae757/verification/inspect-ip-temporal.log`.

### local-endpoint-readiness-postgres

Command: `/home/admin/Salience/.venv/bin/python -c <local TCP readiness probe with 30s condition wait> <postgres container IP> 5432`

Started: `2026-10-29T15:22:37Z`; ended: `2026-10-29T15:22:37Z`; exit: `0`; elapsed: `0.001s`.

Environment keys: `DOCKER_CONTEXT, HOME, PATH`.

Log SHA256: `99a00fd487e92d30c80fc3cf01628feb1acd3853a0a019a81996ed087dabadea` (52 bytes) at `/tmp/salience-task32-exact-qualification-20261029T152227Z-9b9a51dae757/verification/local-endpoint-readiness-postgres.log`.

### local-endpoint-readiness-temporal

Command: `/home/admin/Salience/.venv/bin/python -c <local TCP readiness probe with 30s condition wait> <temporal container IP> 7233`

Started: `2026-10-29T15:22:37Z`; ended: `2026-10-29T15:22:37Z`; exit: `0`; elapsed: `0.200s`.

Environment keys: `DOCKER_CONTEXT, HOME, PATH`.

Log SHA256: `99a00fd487e92d30c80fc3cf01628feb1acd3853a0a019a81996ed087dabadea` (52 bytes) at `/tmp/salience-task32-exact-qualification-20261029T152227Z-9b9a51dae757/verification/local-endpoint-readiness-temporal.log`.

### alembic-upgrade-head

Command: `env -i COMPOSE_PROJECT_NAME=salience-p1-task32-q-9b9a51dae757 DATABASE_URL=<local disposable endpoint; credential redacted> DOCKER_CONTEXT=default HOME=/home/admin PATH=<minimal> TEMPORAL_TARGET=<local disposable endpoint> TEST_DATABASE_URL=<local disposable endpoint; credential redacted> TEST_TEMPORAL_TARGET=<local disposable endpoint> /home/admin/Salience/.venv/bin/python -m alembic upgrade head`

Started: `2026-10-29T15:22:37Z`; ended: `2026-10-29T15:22:39Z`; exit: `0`; elapsed: `1.923s`.

Environment keys: `COMPOSE_PROJECT_NAME, DATABASE_URL, DOCKER_CONTEXT, HOME, PATH, TEMPORAL_TARGET, TEST_DATABASE_URL, TEST_TEMPORAL_TARGET`.

Log SHA256: `5f4e10c1dccc9962e7f52f3cf4f6dc8dd61f6ce7d529531918923889aa1b4b3d` (9165 bytes) at `/tmp/salience-task32-exact-qualification-20261029T152227Z-9b9a51dae757/verification/alembic-upgrade-head.log`.

### alembic-current

Command: `env -i COMPOSE_PROJECT_NAME=salience-p1-task32-q-9b9a51dae757 DATABASE_URL=<local disposable endpoint; credential redacted> DOCKER_CONTEXT=default HOME=/home/admin PATH=<minimal> TEMPORAL_TARGET=<local disposable endpoint> TEST_DATABASE_URL=<local disposable endpoint; credential redacted> TEST_TEMPORAL_TARGET=<local disposable endpoint> /home/admin/Salience/.venv/bin/python -m alembic current`

Started: `2026-10-29T15:22:39Z`; ended: `2026-10-29T15:22:40Z`; exit: `0`; elapsed: `0.809s`.

Environment keys: `COMPOSE_PROJECT_NAME, DATABASE_URL, DOCKER_CONTEXT, HOME, PATH, TEMPORAL_TARGET, TEST_DATABASE_URL, TEST_TEMPORAL_TARGET`.

Log SHA256: `b4695e95e313ffb6440fb361ffee38640aec70511b35c2d6cf2c7968e2ada401` (164 bytes) at `/tmp/salience-task32-exact-qualification-20261029T152227Z-9b9a51dae757/verification/alembic-current.log`.

### focused-01-routing-files-lock-builder

Command: `env -i COMPOSE_PROJECT_NAME=salience-p1-task32-q-9b9a51dae757 DATABASE_URL=<local disposable endpoint; credential redacted> DOCKER_CONTEXT=default HOME=/home/admin PATH=<minimal> TEMPORAL_TARGET=<local disposable endpoint> TEST_DATABASE_URL=<local disposable endpoint; credential redacted> TEST_TEMPORAL_TARGET=<local disposable endpoint> /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider -m not live -q -rs --tb=short tests/unit/test_production_routing.py tests/integration/test_p1_production_routing.py tests/integration/test_v4_publication_recovery_floor.py::test_launcher_v2_lock_builder_binds_exact_final_migration_head_0051_and_verifier_abi`

Started: `2026-10-29T15:22:40Z`; ended: `2026-10-29T15:22:43Z`; exit: `0`; elapsed: `2.998s`.

Environment keys: `COMPOSE_PROJECT_NAME, DATABASE_URL, DOCKER_CONTEXT, HOME, PATH, TEMPORAL_TARGET, TEST_DATABASE_URL, TEST_TEMPORAL_TARGET`.

Log SHA256: `06c5db13b07d190b9016715651b9a3fc0900178a56cea32c53355e6660e2f4d5` (99 bytes) at `/tmp/salience-task32-exact-qualification-20261029T152227Z-9b9a51dae757/focused/focused-01-routing-files-lock-builder.log`.

Test counts: `{"passed": 38}`.

Summary: `38 passed in 2.26s`.

### focused-02-migrations-0047-0051-schema-safety

Command: `env -i COMPOSE_PROJECT_NAME=salience-p1-task32-q-9b9a51dae757 DATABASE_URL=<local disposable endpoint; credential redacted> DOCKER_CONTEXT=default HOME=/home/admin PATH=<minimal> TEMPORAL_TARGET=<local disposable endpoint> TEST_DATABASE_URL=<local disposable endpoint; credential redacted> TEST_TEMPORAL_TARGET=<local disposable endpoint> /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider -m not live -q -rs --tb=short tests/integration/test_v4_migrations.py tests/integration/test_p1_production_recovery.py -k 0047 or 0048 or 0049 or 0050 or 0051 or schema_and_migration_safety`

Started: `2026-10-29T15:22:43Z`; ended: `2026-10-29T15:26:54Z`; exit: `0`; elapsed: `251.398s`.

Environment keys: `COMPOSE_PROJECT_NAME, DATABASE_URL, DOCKER_CONTEXT, HOME, PATH, TEMPORAL_TARGET, TEST_DATABASE_URL, TEST_TEMPORAL_TARGET`.

Log SHA256: `1abccb36a37eacf2657c6b65d82413e7bde1d96747e70df68e716fdbac1ddb66` (127 bytes) at `/tmp/salience-task32-exact-qualification-20261029T152227Z-9b9a51dae757/focused/focused-02-migrations-0047-0051-schema-safety.log`.

Test counts: `{"deselected": 113, "passed": 39}`.

Summary: `39 passed, 113 deselected in 251.04s (0:04:11)`.

### focused-03-schedule-native-readback

Command: `env -i COMPOSE_PROJECT_NAME=salience-p1-task32-q-9b9a51dae757 DATABASE_URL=<local disposable endpoint; credential redacted> DOCKER_CONTEXT=default HOME=/home/admin PATH=<minimal> TEMPORAL_TARGET=<local disposable endpoint> TEST_DATABASE_URL=<local disposable endpoint; credential redacted> TEST_TEMPORAL_TARGET=<local disposable endpoint> /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider -m not live -q -rs --tb=short tests/integration/test_p1_production_schedule_fencing.py tests/integration/test_v4_native_schedules.py tests/integration/test_v4_native_publication_schedules.py tests/unit/test_temporal_schedule_readback.py`

Started: `2026-10-29T15:26:54Z`; ended: `2026-10-29T15:31:12Z`; exit: `0`; elapsed: `257.885s`.

Environment keys: `COMPOSE_PROJECT_NAME, DATABASE_URL, DOCKER_CONTEXT, HOME, PATH, TEMPORAL_TARGET, TEST_DATABASE_URL, TEST_TEMPORAL_TARGET`.

Log SHA256: `46542dfd4ebba489a59995402a0b106a5ede1dcb51618041df84101a003a6230` (191 bytes) at `/tmp/salience-task32-exact-qualification-20261029T152227Z-9b9a51dae757/focused/focused-03-schedule-native-readback.log`.

Test counts: `{"passed": 79}`.

Summary: `79 passed in 256.94s (0:04:16)`.

### focused-04-complete-production-recovery

Command: `env -i COMPOSE_PROJECT_NAME=salience-p1-task32-q-9b9a51dae757 DATABASE_URL=<local disposable endpoint; credential redacted> DOCKER_CONTEXT=default HOME=/home/admin PATH=<minimal> TEMPORAL_TARGET=<local disposable endpoint> TEST_DATABASE_URL=<local disposable endpoint; credential redacted> TEST_TEMPORAL_TARGET=<local disposable endpoint> /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider -m not live -q -rs --tb=short tests/integration/test_p1_production_recovery.py`

Started: `2026-10-29T15:31:12Z`; ended: `2026-10-29T15:40:22Z`; exit: `0`; elapsed: `549.295s`.

Environment keys: `COMPOSE_PROJECT_NAME, DATABASE_URL, DOCKER_CONTEXT, HOME, PATH, TEMPORAL_TARGET, TEST_DATABASE_URL, TEST_TEMPORAL_TARGET`.

Log SHA256: `bf39dec771c447be680d1ee1e221d17982bd765d46eca58ca9cdc9aad216a85e` (191 bytes) at `/tmp/salience-task32-exact-qualification-20261029T152227Z-9b9a51dae757/focused/focused-04-complete-production-recovery.log`.

Test counts: `{"passed": 79}`.

Summary: `79 passed in 548.74s (0:09:08)`.

### focused-05-rollback-v2-handoff

Command: `env -i COMPOSE_PROJECT_NAME=salience-p1-task32-q-9b9a51dae757 DATABASE_URL=<local disposable endpoint; credential redacted> DOCKER_CONTEXT=default HOME=/home/admin PATH=<minimal> TEMPORAL_TARGET=<local disposable endpoint> TEST_DATABASE_URL=<local disposable endpoint; credential redacted> TEST_TEMPORAL_TARGET=<local disposable endpoint> /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider -m not live -q -rs --tb=short tests/integration/test_p1_production_routing.py::test_launcher_v2_runs_pre_and_post_readback_before_prior_code tests/integration/test_p1_production_routing.py::test_rollback_v2_hands_off_authenticated_deployable_only_after_verification tests/integration/test_p1_production_routing.py::test_rollback_v2_fails_closed_without_authenticated_deployable_executor tests/integration/test_p1_production_routing.py::test_launcher_v2_rejects_caller_supplied_hold_lists`

Started: `2026-10-29T15:40:22Z`; ended: `2026-10-29T15:40:23Z`; exit: `0`; elapsed: `1.495s`.

Environment keys: `COMPOSE_PROJECT_NAME, DATABASE_URL, DOCKER_CONTEXT, HOME, PATH, TEMPORAL_TARGET, TEST_DATABASE_URL, TEST_TEMPORAL_TARGET`.

Log SHA256: `54d23ef0b56f99d2cbe2d50ca07723596b53070e1dcae86069f33fb176336fed` (98 bytes) at `/tmp/salience-task32-exact-qualification-20261029T152227Z-9b9a51dae757/focused/focused-05-rollback-v2-handoff.log`.

Test counts: `{"passed": 4}`.

Summary: `4 passed in 0.93s`.

### focused-06-complete-policy-revocation

Command: `env -i COMPOSE_PROJECT_NAME=salience-p1-task32-q-9b9a51dae757 DATABASE_URL=<local disposable endpoint; credential redacted> DOCKER_CONTEXT=default HOME=/home/admin PATH=<minimal> TEMPORAL_TARGET=<local disposable endpoint> TEST_DATABASE_URL=<local disposable endpoint; credential redacted> TEST_TEMPORAL_TARGET=<local disposable endpoint> /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider -m not live -q -rs --tb=short tests/integration/test_p1_production_policy_revocation.py`

Started: `2026-10-29T15:40:23Z`; ended: `2026-10-29T15:41:40Z`; exit: `0`; elapsed: `77.290s`.

Environment keys: `COMPOSE_PROJECT_NAME, DATABASE_URL, DOCKER_CONTEXT, HOME, PATH, TEMPORAL_TARGET, TEST_DATABASE_URL, TEST_TEMPORAL_TARGET`.

Log SHA256: `39116698e2245ec9965c94c2a8dda1450de40dfc14be9fa7f8e8075bf0b2495c` (191 bytes) at `/tmp/salience-task32-exact-qualification-20261029T152227Z-9b9a51dae757/focused/focused-06-complete-policy-revocation.log`.

Test counts: `{"passed": 137}`.

Summary: `137 passed in 76.85s (0:01:16)`.

### focused-07-external-acceptance-unit

Command: `env -i COMPOSE_PROJECT_NAME=salience-p1-task32-q-9b9a51dae757 DATABASE_URL=<local disposable endpoint; credential redacted> DOCKER_CONTEXT=default HOME=/home/admin PATH=<minimal> TEMPORAL_TARGET=<local disposable endpoint> TEST_DATABASE_URL=<local disposable endpoint; credential redacted> TEST_TEMPORAL_TARGET=<local disposable endpoint> /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider -m not live -q -rs --tb=short tests/unit/test_p1_external_acceptance.py`

Started: `2026-10-29T15:41:40Z`; ended: `2026-10-29T15:44:50Z`; exit: `0`; elapsed: `189.311s`.

Environment keys: `COMPOSE_PROJECT_NAME, DATABASE_URL, DOCKER_CONTEXT, HOME, PATH, TEMPORAL_TARGET, TEST_DATABASE_URL, TEST_TEMPORAL_TARGET`.

Log SHA256: `13ab501ab5fde914398d1befa6c431d69c8bc400893a76415093350cc6d2c670` (111 bytes) at `/tmp/salience-task32-exact-qualification-20261029T152227Z-9b9a51dae757/focused/focused-07-external-acceptance-unit.log`.

Test counts: `{"passed": 45}`.

Summary: `45 passed in 188.99s (0:03:08)`.

### full-non-live-suite

Command: `env -i COMPOSE_PROJECT_NAME=salience-p1-task32-q-9b9a51dae757 DATABASE_URL=<local disposable endpoint; credential redacted> DOCKER_CONTEXT=default HOME=/home/admin PATH=<minimal> TEMPORAL_TARGET=<local disposable endpoint> TEST_DATABASE_URL=<local disposable endpoint; credential redacted> TEST_TEMPORAL_TARGET=<local disposable endpoint> /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider tests/contracts tests/unit tests/integration tests/e2e tests/evals -m not live -q -rs --tb=short`

Started: `2026-10-29T15:44:50Z`; ended: `2026-10-29T16:28:10Z`; exit: `0`; elapsed: `2600.014s`.

Environment keys: `COMPOSE_PROJECT_NAME, DATABASE_URL, DOCKER_CONTEXT, HOME, PATH, TEMPORAL_TARGET, TEST_DATABASE_URL, TEST_TEMPORAL_TARGET`.

Log SHA256: `0e6e0f882833c338e653056cb4b85fc73fe3adfc671ccd0d4088e12453eb01b8` (3474 bytes) at `/tmp/salience-task32-exact-qualification-20261029T152227Z-9b9a51dae757/full/full-non-live-suite.log`.

Test counts: `{"passed": 1211, "skipped": 26, "warning": 3}`.

Summary: `1211 passed, 26 skipped, 3 warnings in 2596.44s (0:43:16)`.

### compose-cleanup

Command: `docker --context default compose --env-file <empty env file> --project-name salience-p1-task32-q-9b9a51dae757 down --volumes --remove-orphans`

Started: `2026-10-29T16:28:10Z`; ended: `2026-10-29T16:28:12Z`; exit: `0`; elapsed: `2.841s`.

Environment keys: `COMPOSE_PROJECT_NAME, DOCKER_CONTEXT, HOME, PATH`.

Log SHA256: `c702a2463241a5fc6f7b10c511ac8176e27067441c8ec69f817e610aec4970d6` (776 bytes) at `/tmp/salience-task32-exact-qualification-20261029T152227Z-9b9a51dae757/verification/compose-cleanup.log`.

### cleanup-verify-containers

Command: `docker --context default ps -aq --filter label=com.docker.compose.project=salience-p1-task32-q-9b9a51dae757`

Started: `2026-10-29T16:28:12Z`; ended: `2026-10-29T16:28:12Z`; exit: `0`; elapsed: `0.020s`.

Environment keys: `DOCKER_CONTEXT, HOME, PATH`.

Log SHA256: `d950b4e86f37941c3520e2f6072e72fac7dd04015e53cf27644030cfef1c1216` (8 bytes) at `/tmp/salience-task32-exact-qualification-20261029T152227Z-9b9a51dae757/verification/cleanup-verify-containers.log`.

### cleanup-verify-networks

Command: `docker --context default network ls -q --filter label=com.docker.compose.project=salience-p1-task32-q-9b9a51dae757`

Started: `2026-10-29T16:28:12Z`; ended: `2026-10-29T16:28:13Z`; exit: `0`; elapsed: `0.020s`.

Environment keys: `DOCKER_CONTEXT, HOME, PATH`.

Log SHA256: `d950b4e86f37941c3520e2f6072e72fac7dd04015e53cf27644030cfef1c1216` (8 bytes) at `/tmp/salience-task32-exact-qualification-20261029T152227Z-9b9a51dae757/verification/cleanup-verify-networks.log`.

### cleanup-verify-volumes

Command: `docker --context default volume ls -q --filter label=com.docker.compose.project=salience-p1-task32-q-9b9a51dae757`

Started: `2026-10-29T16:28:13Z`; ended: `2026-10-29T16:28:13Z`; exit: `0`; elapsed: `0.020s`.

Environment keys: `DOCKER_CONTEXT, HOME, PATH`.

Log SHA256: `d950b4e86f37941c3520e2f6072e72fac7dd04015e53cf27644030cfef1c1216` (8 bytes) at `/tmp/salience-task32-exact-qualification-20261029T152227Z-9b9a51dae757/verification/cleanup-verify-volumes.log`.

### final-head

Command: `git rev-parse HEAD`

Started: `2026-10-29T16:28:13Z`; ended: `2026-10-29T16:28:13Z`; exit: `0`; elapsed: `0.009s`.

Environment keys: `HOME, PATH`.

Log SHA256: `347aae9b744aabd95131fbd2cdfbe4e68ad7b4d6b0980b7654b31604bde1bf9f` (41 bytes) at `/tmp/salience-task32-exact-qualification-20261029T152227Z-9b9a51dae757/verification/final-head.log`.

### final-parent

Command: `git rev-parse HEAD^`

Started: `2026-10-29T16:28:13Z`; ended: `2026-10-29T16:28:13Z`; exit: `0`; elapsed: `0.005s`.

Environment keys: `HOME, PATH`.

Log SHA256: `2acd0938017259dff861b455f22129e2b30aabae9eda891dd7bd78cc0d7d04c7` (41 bytes) at `/tmp/salience-task32-exact-qualification-20261029T152227Z-9b9a51dae757/verification/final-parent.log`.

### final-worktree

Command: `git status --porcelain=v1 --untracked-files=all`

Started: `2026-10-29T16:28:13Z`; ended: `2026-10-29T16:28:13Z`; exit: `0`; elapsed: `0.100s`.

Environment keys: `HOME, PATH`.

Log SHA256: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` (0 bytes) at `/tmp/salience-task32-exact-qualification-20261029T152227Z-9b9a51dae757/verification/final-worktree.log`.

## Focused group and suite result

Focused groups run: `7/7` exactly once each, serially.
All seven focused groups passed: `True`.
Full non-live suite run: `True` (gate required all seven focused groups to pass).
Full non-live suite passed: `True`.
Fresh migration current revision: `0051_p1_recovery_rollback_v2`.
Compose cleanup counts by project label: `{"containers": 0, "networks": 0, "volumes": 0}`.

## Pre-test readiness diagnosis

One preliminary bootstrap attempt used project `salience-p1-task32-q-98039f582cae` under `/tmp/salience-task32-exact-qualification-20261029T151755Z-98039f582cae`. It stopped before migrations or tests because its single immediate local Temporal TCP probe received connection-refused; that project was removed and cleanup verified zero labeled containers, networks, and volumes. A separate local timing reproduction under project `salience-p1-task32-diag-425992f46057` confirmed errno 111 at t+0 and successful connections at t+1s; it too was cleaned with zero labeled resources. The first bootstrap report is `/tmp/salience-task32-exact-qualification-20261029T151755Z-98039f582cae/salience-task32-exact-qualification-report.md` (SHA256 `54ebb13cb35902d892aabcebf732314116fb186ce849cf5f47f2aa23f6aebb04`); timing evidence is under `/tmp/salience-task32-temporal-diagnostic-20261029T152114Z-425992f46057`. No migration or test command ran in those preliminary attempts. The successful project above waited for the Temporal port condition before migration and testing.

## Failure details

None in the final qualification.

No source files were edited or committed. Logs contain no provider or production credentials.
