# Task35 exact-candidate local qualification report

Status: **PASSED**

Artifact HEAD: `fe6b3795ce4453beb6c6bbafcc20cd3be91b3b87`<br>
Supplied source reference: `546ac4ed794afc123ea02586a37d4a4457590582`<br>
Compose project: `salience-p1-task35-q-35a80dc40a2a`<br>
Evidence directory: `/tmp/salience-task35-exact-qualification-20261029T173910Z-35a80dc40a2a`<br>
Started: `2026-10-29T17:39:10Z`<br>
Updated at: `2026-10-29T18:46:08Z`

The checkout was required clean at start and final checks, with HEAD exactly equal to the artifact and its direct parent exactly equal to the supplied source. Only the local disposable Compose `postgres` and `temporal` services were started, using `--pull never`; a bounded local TCP readiness loop checked PostgreSQL port 5432 and Temporal port 7233 after Compose reported the containers running. Migration and every pytest subprocess received the same unique `COMPOSE_PROJECT_NAME` and exact local database and Temporal endpoints through a cleared environment. Alembic used the local async SQLAlchemy URL; integration tests used the same PostgreSQL service through the libpq `postgresql://` URL required by direct psycopg fixtures. No provider, production, live schedule, or external acceptance service was contacted. External provider/acceptance rows remain **NOT RUN**; group 07 is the local external-acceptance unit test. Live tests were excluded and external effects remained disabled by use of only the disposable local services.

Environment keys passed to migration and test subprocesses: `COMPOSE_PROJECT_NAME, DATABASE_URL, DOCKER_CONTEXT, HOME, PATH, TEMPORAL_TARGET, TEST_DATABASE_URL, TEST_TEMPORAL_TARGET`. Endpoint and credential values are omitted/redacted.

Fresh Alembic revision: `0051_p1_recovery_rollback_v2` (expected `0051_p1_recovery_rollback_v2`).

The first isolated harness attempt is retained separately because its `TEST_DATABASE_URL` incorrectly used the async SQLAlchemy scheme for direct psycopg fixtures. It ran seven focused commands once, then cleaned with zero labeled resources; its complete command evidence is retained at `/tmp/salience-task35-exact-qualification-20261029T173417Z-50400cc11b6f/attempt-1-report.md` (SHA-256 `3f0f5366963de446ae1887ef6b1310c0e605afded6790bf45e2bde2e5f869c05`). This final attempt uses a new project and the corrected libpq test URL.

## Command evidence

### preflight-head

Command: `git rev-parse HEAD`

Started: `2026-10-29T17:39:10Z`; ended: `2026-10-29T17:39:10Z`; exit: `0`; elapsed: `0.002s`.

Environment keys: `DOCKER_CONTEXT, HOME, PATH`.

Log SHA256: `c0a54dddafd7ac68e276a8550771ca1b73ae816b4cffc1aab1188e31e2a99435` (41 bytes) at `/tmp/salience-task35-exact-qualification-20261029T173910Z-35a80dc40a2a/verification/preflight-head.log`.

### preflight-parent

Command: `git rev-parse 'HEAD^'`

Started: `2026-10-29T17:39:10Z`; ended: `2026-10-29T17:39:10Z`; exit: `0`; elapsed: `0.002s`.

Environment keys: `DOCKER_CONTEXT, HOME, PATH`.

Log SHA256: `b0254da624bf38ca66b66e5a1bed5edaddb08a0c7eebcca6040c50afdbb1da11` (41 bytes) at `/tmp/salience-task35-exact-qualification-20261029T173910Z-35a80dc40a2a/verification/preflight-parent.log`.

### preflight-worktree

Command: `git status --porcelain=v1 --untracked-files=all`

Started: `2026-10-29T17:39:10Z`; ended: `2026-10-29T17:39:10Z`; exit: `0`; elapsed: `0.010s`.

Environment keys: `DOCKER_CONTEXT, HOME, PATH`.

Log SHA256: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` (0 bytes) at `/tmp/salience-task35-exact-qualification-20261029T173910Z-35a80dc40a2a/verification/preflight-worktree.log`.

### preflight-changed-paths

Command: `git diff-tree --no-commit-id --name-only -r HEAD`

Started: `2026-10-29T17:39:10Z`; ended: `2026-10-29T17:39:10Z`; exit: `0`; elapsed: `0.002s`.

Environment keys: `DOCKER_CONTEXT, HOME, PATH`.

Log SHA256: `4b3e0acada355e08ee7a61e9343b95d6b3677390e9b0da46975c05bafbc6fae5` (132 bytes) at `/tmp/salience-task35-exact-qualification-20261029T173910Z-35a80dc40a2a/verification/preflight-changed-paths.log`.

### compose-config

Command: `docker --context default compose --env-file /tmp/salience-task35-exact-qualification-20261029T173910Z-35a80dc40a2a/empty-compose.env --project-name salience-p1-task35-q-35a80dc40a2a -f compose.yaml config -q`

Started: `2026-10-29T17:39:10Z`; ended: `2026-10-29T17:39:10Z`; exit: `0`; elapsed: `0.074s`.

Environment keys: `COMPOSE_PROJECT_NAME, DOCKER_CONTEXT, HOME, PATH`.

Log SHA256: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` (0 bytes) at `/tmp/salience-task35-exact-qualification-20261029T173910Z-35a80dc40a2a/verification/compose-config.log`.

### compose-up-postgres-temporal

Command: `docker --context default compose --env-file /tmp/salience-task35-exact-qualification-20261029T173910Z-35a80dc40a2a/empty-compose.env --project-name salience-p1-task35-q-35a80dc40a2a -f compose.yaml up -d --pull never --wait --wait-timeout 180 postgres temporal`

Started: `2026-10-29T17:39:10Z`; ended: `2026-10-29T17:39:17Z`; exit: `0`; elapsed: `6.808s`.

Environment keys: `COMPOSE_PROJECT_NAME, DOCKER_CONTEXT, HOME, PATH`.

Log SHA256: `2ffedd28dad10ef7d86b494c8a89e41eb05c056e550f724a545443c5f3087436` (1418 bytes) at `/tmp/salience-task35-exact-qualification-20261029T173910Z-35a80dc40a2a/verification/compose-up-postgres-temporal.log`.

### compose-ps-postgres

Command: `docker --context default compose --env-file <empty env file> --project-name salience-p1-task35-q-35a80dc40a2a ps -q postgres`

Started: `2026-10-29T17:39:17Z`; ended: `2026-10-29T17:39:17Z`; exit: `0`; elapsed: `0.120s`.

Environment keys: `COMPOSE_PROJECT_NAME, DOCKER_CONTEXT, HOME, PATH`.

Log SHA256: `a1431a4877cfc77d6618d5f4567fbfb8d07e77b23c5b75fc257b9729394eb6aa` (65 bytes) at `/tmp/salience-task35-exact-qualification-20261029T173910Z-35a80dc40a2a/verification/compose-ps-postgres.log`.

### inspect-ip-postgres

Command: `docker --context default inspect <postgres container> local IP template`

Started: `2026-10-29T17:39:17Z`; ended: `2026-10-29T17:39:17Z`; exit: `0`; elapsed: `0.039s`.

Environment keys: `DOCKER_CONTEXT, HOME, PATH`.

Log SHA256: `870abc46b1af55506101fb91ed5852682d5374ffd5d796b7b527760e62240dea` (11 bytes) at `/tmp/salience-task35-exact-qualification-20261029T173910Z-35a80dc40a2a/verification/inspect-ip-postgres.log`.

### compose-ps-temporal

Command: `docker --context default compose --env-file <empty env file> --project-name salience-p1-task35-q-35a80dc40a2a ps -q temporal`

Started: `2026-10-29T17:39:17Z`; ended: `2026-10-29T17:39:17Z`; exit: `0`; elapsed: `0.152s`.

Environment keys: `COMPOSE_PROJECT_NAME, DOCKER_CONTEXT, HOME, PATH`.

Log SHA256: `86f03d9457b49c3e6b2e57ce360d85b8bf5be4066dbeb8dd6a92ca04e4a20802` (65 bytes) at `/tmp/salience-task35-exact-qualification-20261029T173910Z-35a80dc40a2a/verification/compose-ps-temporal.log`.

### inspect-ip-temporal

Command: `docker --context default inspect <temporal container> local IP template`

Started: `2026-10-29T17:39:17Z`; ended: `2026-10-29T17:39:17Z`; exit: `0`; elapsed: `0.028s`.

Environment keys: `DOCKER_CONTEXT, HOME, PATH`.

Log SHA256: `d6d6b03795a1c1774e97c843e6542c6de18b34b3f219d45f38bb675a9f70dfeb` (11 bytes) at `/tmp/salience-task35-exact-qualification-20261029T173910Z-35a80dc40a2a/verification/inspect-ip-temporal.log`.

### local-endpoint-readiness-postgres

Command: `/home/admin/Salience/.venv/bin/python -c <bounded local TCP readiness loop> <postgres container IP> 5432`

Started: `2026-10-29T17:39:17Z`; ended: `2026-10-29T17:39:17Z`; exit: `0`; elapsed: `0.033s`.

Environment keys: `DOCKER_CONTEXT, HOME, PATH`.

Log SHA256: `a0a96e962938d7f8de27e58d9800cf471d57cd07b2117c086c1e5d00064ea1bd` (38 bytes) at `/tmp/salience-task35-exact-qualification-20261029T173910Z-35a80dc40a2a/verification/local-endpoint-readiness-postgres.log`.

### local-endpoint-readiness-temporal

Command: `/home/admin/Salience/.venv/bin/python -c <bounded local TCP readiness loop> <temporal container IP> 7233`

Started: `2026-10-29T17:39:17Z`; ended: `2026-10-29T17:39:17Z`; exit: `0`; elapsed: `0.031s`.

Environment keys: `DOCKER_CONTEXT, HOME, PATH`.

Log SHA256: `a0a96e962938d7f8de27e58d9800cf471d57cd07b2117c086c1e5d00064ea1bd` (38 bytes) at `/tmp/salience-task35-exact-qualification-20261029T173910Z-35a80dc40a2a/verification/local-endpoint-readiness-temporal.log`.

### alembic-upgrade-head

Command: `env -i COMPOSE_PROJECT_NAME=salience-p1-task35-q-35a80dc40a2a DATABASE_URL=<local disposable endpoint; credential redacted> DOCKER_CONTEXT=default HOME=/home/admin PATH=<minimal> TEMPORAL_TARGET=<local disposable endpoint> TEST_DATABASE_URL=<local disposable endpoint; credential redacted> TEST_TEMPORAL_TARGET=<local disposable endpoint> /home/admin/Salience/.venv/bin/python -m alembic upgrade head`

Started: `2026-10-29T17:39:17Z`; ended: `2026-10-29T17:39:19Z`; exit: `0`; elapsed: `1.805s`.

Environment keys: `COMPOSE_PROJECT_NAME, DATABASE_URL, DOCKER_CONTEXT, HOME, PATH, TEMPORAL_TARGET, TEST_DATABASE_URL, TEST_TEMPORAL_TARGET`.

Log SHA256: `5f4e10c1dccc9962e7f52f3cf4f6dc8dd61f6ce7d529531918923889aa1b4b3d` (9165 bytes) at `/tmp/salience-task35-exact-qualification-20261029T173910Z-35a80dc40a2a/verification/alembic-upgrade-head.log`.

### alembic-current

Command: `env -i COMPOSE_PROJECT_NAME=salience-p1-task35-q-35a80dc40a2a DATABASE_URL=<local disposable endpoint; credential redacted> DOCKER_CONTEXT=default HOME=/home/admin PATH=<minimal> TEMPORAL_TARGET=<local disposable endpoint> TEST_DATABASE_URL=<local disposable endpoint; credential redacted> TEST_TEMPORAL_TARGET=<local disposable endpoint> /home/admin/Salience/.venv/bin/python -m alembic current`

Started: `2026-10-29T17:39:19Z`; ended: `2026-10-29T17:39:20Z`; exit: `0`; elapsed: `0.782s`.

Environment keys: `COMPOSE_PROJECT_NAME, DATABASE_URL, DOCKER_CONTEXT, HOME, PATH, TEMPORAL_TARGET, TEST_DATABASE_URL, TEST_TEMPORAL_TARGET`.

Log SHA256: `b4695e95e313ffb6440fb361ffee38640aec70511b35c2d6cf2c7968e2ada401` (164 bytes) at `/tmp/salience-task35-exact-qualification-20261029T173910Z-35a80dc40a2a/verification/alembic-current.log`.

### focused-01-routing-files-lock-builder

Command: `env -i COMPOSE_PROJECT_NAME=salience-p1-task35-q-35a80dc40a2a DATABASE_URL=<local disposable endpoint; credential redacted> DOCKER_CONTEXT=default HOME=/home/admin PATH=<minimal> TEMPORAL_TARGET=<local disposable endpoint> TEST_DATABASE_URL=<local disposable endpoint; credential redacted> TEST_TEMPORAL_TARGET=<local disposable endpoint> /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/unit/test_production_routing.py tests/integration/test_p1_production_routing.py tests/integration/test_v4_publication_recovery_floor.py::test_launcher_v2_lock_builder_binds_exact_final_migration_head_0051_and_verifier_abi`

Started: `2026-10-29T17:39:20Z`; ended: `2026-10-29T17:39:22Z`; exit: `0`; elapsed: `2.580s`.

Environment keys: `COMPOSE_PROJECT_NAME, DATABASE_URL, DOCKER_CONTEXT, HOME, PATH, TEMPORAL_TARGET, TEST_DATABASE_URL, TEST_TEMPORAL_TARGET`.

Log SHA256: `1e2f03e2c7f7898678530c1434fbacb0dd85bd230bc1f8e269e1076b4848906c` (99 bytes) at `/tmp/salience-task35-exact-qualification-20261029T173910Z-35a80dc40a2a/focused/focused-01-routing-files-lock-builder.log`.

Test counts: `{"passed": 38}`.

Summary: `38 passed in 2.05s`.

### focused-02-migrations-0047-0051-schema-safety

Command: `env -i COMPOSE_PROJECT_NAME=salience-p1-task35-q-35a80dc40a2a DATABASE_URL=<local disposable endpoint; credential redacted> DOCKER_CONTEXT=default HOME=/home/admin PATH=<minimal> TEMPORAL_TARGET=<local disposable endpoint> TEST_DATABASE_URL=<local disposable endpoint; credential redacted> TEST_TEMPORAL_TARGET=<local disposable endpoint> /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/integration/test_v4_migrations.py tests/integration/test_p1_production_recovery.py -k '0047 or 0048 or 0049 or 0050 or 0051 or schema_and_migration_safety'`

Started: `2026-10-29T17:39:22Z`; ended: `2026-10-29T17:43:35Z`; exit: `0`; elapsed: `253.133s`.

Environment keys: `COMPOSE_PROJECT_NAME, DATABASE_URL, DOCKER_CONTEXT, HOME, PATH, TEMPORAL_TARGET, TEST_DATABASE_URL, TEST_TEMPORAL_TARGET`.

Log SHA256: `63bbfaebe88ce53e5813f1a610853bbe3d6877927b298fbd9e9ae2f1f45aa7cf` (127 bytes) at `/tmp/salience-task35-exact-qualification-20261029T173910Z-35a80dc40a2a/focused/focused-02-migrations-0047-0051-schema-safety.log`.

Test counts: `{"deselected": 113, "passed": 39}`.

Summary: `39 passed, 113 deselected in 252.78s (0:04:12)`.

### focused-03-schedule-native-readback

Command: `env -i COMPOSE_PROJECT_NAME=salience-p1-task35-q-35a80dc40a2a DATABASE_URL=<local disposable endpoint; credential redacted> DOCKER_CONTEXT=default HOME=/home/admin PATH=<minimal> TEMPORAL_TARGET=<local disposable endpoint> TEST_DATABASE_URL=<local disposable endpoint; credential redacted> TEST_TEMPORAL_TARGET=<local disposable endpoint> /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/integration/test_p1_production_schedule_fencing.py tests/integration/test_v4_native_schedules.py tests/integration/test_v4_native_publication_schedules.py tests/unit/test_temporal_schedule_readback.py`

Started: `2026-10-29T17:43:35Z`; ended: `2026-10-29T17:47:12Z`; exit: `0`; elapsed: `217.020s`.

Environment keys: `COMPOSE_PROJECT_NAME, DATABASE_URL, DOCKER_CONTEXT, HOME, PATH, TEMPORAL_TARGET, TEST_DATABASE_URL, TEST_TEMPORAL_TARGET`.

Log SHA256: `2f27aeb500a20727249b8d9546a906d57702d9d76deeb4638ff21de650791634` (191 bytes) at `/tmp/salience-task35-exact-qualification-20261029T173910Z-35a80dc40a2a/focused/focused-03-schedule-native-readback.log`.

Test counts: `{"passed": 80}`.

Summary: `80 passed in 216.08s (0:03:36)`.

### focused-04-complete-production-recovery

Command: `env -i COMPOSE_PROJECT_NAME=salience-p1-task35-q-35a80dc40a2a DATABASE_URL=<local disposable endpoint; credential redacted> DOCKER_CONTEXT=default HOME=/home/admin PATH=<minimal> TEMPORAL_TARGET=<local disposable endpoint> TEST_DATABASE_URL=<local disposable endpoint; credential redacted> TEST_TEMPORAL_TARGET=<local disposable endpoint> /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/integration/test_p1_production_recovery.py`

Started: `2026-10-29T17:47:12Z`; ended: `2026-10-29T17:56:21Z`; exit: `0`; elapsed: `548.871s`.

Environment keys: `COMPOSE_PROJECT_NAME, DATABASE_URL, DOCKER_CONTEXT, HOME, PATH, TEMPORAL_TARGET, TEST_DATABASE_URL, TEST_TEMPORAL_TARGET`.

Log SHA256: `ebbd669b06d1549b315800e00c0027fe700121c50753607aab726d396eeb5153` (191 bytes) at `/tmp/salience-task35-exact-qualification-20261029T173910Z-35a80dc40a2a/focused/focused-04-complete-production-recovery.log`.

Test counts: `{"passed": 79}`.

Summary: `79 passed in 548.30s (0:09:08)`.

### focused-05-rollback-v2-handoff

Command: `env -i COMPOSE_PROJECT_NAME=salience-p1-task35-q-35a80dc40a2a DATABASE_URL=<local disposable endpoint; credential redacted> DOCKER_CONTEXT=default HOME=/home/admin PATH=<minimal> TEMPORAL_TARGET=<local disposable endpoint> TEST_DATABASE_URL=<local disposable endpoint; credential redacted> TEST_TEMPORAL_TARGET=<local disposable endpoint> /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/integration/test_p1_production_routing.py::test_launcher_v2_runs_pre_and_post_readback_before_prior_code tests/integration/test_p1_production_routing.py::test_rollback_v2_hands_off_authenticated_deployable_only_after_verification tests/integration/test_p1_production_routing.py::test_rollback_v2_fails_closed_without_authenticated_deployable_executor tests/integration/test_p1_production_routing.py::test_launcher_v2_rejects_caller_supplied_hold_lists`

Started: `2026-10-29T17:56:21Z`; ended: `2026-10-29T17:56:23Z`; exit: `0`; elapsed: `1.441s`.

Environment keys: `COMPOSE_PROJECT_NAME, DATABASE_URL, DOCKER_CONTEXT, HOME, PATH, TEMPORAL_TARGET, TEST_DATABASE_URL, TEST_TEMPORAL_TARGET`.

Log SHA256: `6a8ee765aa85750ad3157354082112da148050828ccd0a44a85396fbcd87f37c` (98 bytes) at `/tmp/salience-task35-exact-qualification-20261029T173910Z-35a80dc40a2a/focused/focused-05-rollback-v2-handoff.log`.

Test counts: `{"passed": 4}`.

Summary: `4 passed in 0.94s`.

### focused-06-complete-policy-revocation

Command: `env -i COMPOSE_PROJECT_NAME=salience-p1-task35-q-35a80dc40a2a DATABASE_URL=<local disposable endpoint; credential redacted> DOCKER_CONTEXT=default HOME=/home/admin PATH=<minimal> TEMPORAL_TARGET=<local disposable endpoint> TEST_DATABASE_URL=<local disposable endpoint; credential redacted> TEST_TEMPORAL_TARGET=<local disposable endpoint> /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/integration/test_p1_production_policy_revocation.py`

Started: `2026-10-29T17:56:23Z`; ended: `2026-10-29T17:57:41Z`; exit: `0`; elapsed: `78.065s`.

Environment keys: `COMPOSE_PROJECT_NAME, DATABASE_URL, DOCKER_CONTEXT, HOME, PATH, TEMPORAL_TARGET, TEST_DATABASE_URL, TEST_TEMPORAL_TARGET`.

Log SHA256: `474620bd421e188a028928a72ee6623c9410d1e2476bd6be3e9759699383a5e5` (191 bytes) at `/tmp/salience-task35-exact-qualification-20261029T173910Z-35a80dc40a2a/focused/focused-06-complete-policy-revocation.log`.

Test counts: `{"passed": 137}`.

Summary: `137 passed in 77.62s (0:01:17)`.

### focused-07-external-acceptance-unit

Command: `env -i COMPOSE_PROJECT_NAME=salience-p1-task35-q-35a80dc40a2a DATABASE_URL=<local disposable endpoint; credential redacted> DOCKER_CONTEXT=default HOME=/home/admin PATH=<minimal> TEMPORAL_TARGET=<local disposable endpoint> TEST_DATABASE_URL=<local disposable endpoint; credential redacted> TEST_TEMPORAL_TARGET=<local disposable endpoint> /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/unit/test_p1_external_acceptance.py`

Started: `2026-10-29T17:57:41Z`; ended: `2026-10-29T18:00:50Z`; exit: `0`; elapsed: `189.769s`.

Environment keys: `COMPOSE_PROJECT_NAME, DATABASE_URL, DOCKER_CONTEXT, HOME, PATH, TEMPORAL_TARGET, TEST_DATABASE_URL, TEST_TEMPORAL_TARGET`.

Log SHA256: `955ca05367e7f02636472f108cb875842db80fd885188674f587a692e2694c68` (111 bytes) at `/tmp/salience-task35-exact-qualification-20261029T173910Z-35a80dc40a2a/focused/focused-07-external-acceptance-unit.log`.

Test counts: `{"passed": 45}`.

Summary: `45 passed in 189.45s (0:03:09)`.

### full-non-live-suite

Command: `env -i COMPOSE_PROJECT_NAME=salience-p1-task35-q-35a80dc40a2a DATABASE_URL=<local disposable endpoint; credential redacted> DOCKER_CONTEXT=default HOME=/home/admin PATH=<minimal> TEMPORAL_TARGET=<local disposable endpoint> TEST_DATABASE_URL=<local disposable endpoint; credential redacted> TEST_TEMPORAL_TARGET=<local disposable endpoint> /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider tests/contracts tests/unit tests/integration tests/e2e tests/evals -m 'not live' -q -rs --tb=short`

Started: `2026-10-29T18:00:50Z`; ended: `2026-10-29T18:44:17Z`; exit: `0`; elapsed: `2606.357s`.

Environment keys: `COMPOSE_PROJECT_NAME, DATABASE_URL, DOCKER_CONTEXT, HOME, PATH, TEMPORAL_TARGET, TEST_DATABASE_URL, TEST_TEMPORAL_TARGET`.

Log SHA256: `10f3722e943361a0323311b5be06816f40faa0dde805736182ea6aef748b023c` (3474 bytes) at `/tmp/salience-task35-exact-qualification-20261029T173910Z-35a80dc40a2a/full/full-non-live-suite.log`.

Test counts: `{"passed": 1212, "skipped": 26, "warning": 3}`.

Summary: `1212 passed, 26 skipped, 3 warnings in 2602.63s (0:43:22)`.

### compose-cleanup

Command: `docker --context default compose --env-file /tmp/salience-task35-exact-qualification-20261029T173910Z-35a80dc40a2a/empty-compose.env --project-name salience-p1-task35-q-35a80dc40a2a -f compose.yaml down --volumes --remove-orphans`

Started: `2026-10-29T18:44:17Z`; ended: `2026-10-29T18:44:20Z`; exit: `0`; elapsed: `3.251s`.

Environment keys: `DOCKER_CONTEXT, HOME, PATH`.

Log SHA256: `d972190b218ef6a2a272c249f759834564698de67e25badbf3ac44fe3ce9f2cf` (776 bytes) at `/tmp/salience-task35-exact-qualification-20261029T173910Z-35a80dc40a2a/verification/compose-cleanup.log`.

### cleanup-verify-containers

Command: `docker --context default ps -aq --filter label=com.docker.compose.project=salience-p1-task35-q-35a80dc40a2a`

Started: `2026-10-29T18:44:20Z`; ended: `2026-10-29T18:44:20Z`; exit: `0`; elapsed: `0.022s`.

Environment keys: `DOCKER_CONTEXT, HOME, PATH`.

Log SHA256: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` (0 bytes) at `/tmp/salience-task35-exact-qualification-20261029T173910Z-35a80dc40a2a/verification/cleanup-verify-containers.log`.

### cleanup-verify-networks

Command: `docker --context default network ls -q --filter label=com.docker.compose.project=salience-p1-task35-q-35a80dc40a2a`

Started: `2026-10-29T18:44:20Z`; ended: `2026-10-29T18:44:20Z`; exit: `0`; elapsed: `0.021s`.

Environment keys: `DOCKER_CONTEXT, HOME, PATH`.

Log SHA256: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` (0 bytes) at `/tmp/salience-task35-exact-qualification-20261029T173910Z-35a80dc40a2a/verification/cleanup-verify-networks.log`.

### cleanup-verify-volumes

Command: `docker --context default volume ls -q --filter label=com.docker.compose.project=salience-p1-task35-q-35a80dc40a2a`

Started: `2026-10-29T18:44:20Z`; ended: `2026-10-29T18:44:20Z`; exit: `0`; elapsed: `0.021s`.

Environment keys: `DOCKER_CONTEXT, HOME, PATH`.

Log SHA256: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` (0 bytes) at `/tmp/salience-task35-exact-qualification-20261029T173910Z-35a80dc40a2a/verification/cleanup-verify-volumes.log`.

### final-head

Command: `git rev-parse HEAD`

Started: `2026-10-29T18:44:20Z`; ended: `2026-10-29T18:44:20Z`; exit: `0`; elapsed: `0.008s`.

Environment keys: `DOCKER_CONTEXT, HOME, PATH`.

Log SHA256: `c0a54dddafd7ac68e276a8550771ca1b73ae816b4cffc1aab1188e31e2a99435` (41 bytes) at `/tmp/salience-task35-exact-qualification-20261029T173910Z-35a80dc40a2a/verification/final-head.log`.

### final-parent

Command: `git rev-parse 'HEAD^'`

Started: `2026-10-29T18:44:20Z`; ended: `2026-10-29T18:44:20Z`; exit: `0`; elapsed: `0.008s`.

Environment keys: `DOCKER_CONTEXT, HOME, PATH`.

Log SHA256: `b0254da624bf38ca66b66e5a1bed5edaddb08a0c7eebcca6040c50afdbb1da11` (41 bytes) at `/tmp/salience-task35-exact-qualification-20261029T173910Z-35a80dc40a2a/verification/final-parent.log`.

### final-worktree

Command: `git status --porcelain=v1 --untracked-files=all`

Started: `2026-10-29T18:44:20Z`; ended: `2026-10-29T18:44:20Z`; exit: `0`; elapsed: `0.111s`.

Environment keys: `DOCKER_CONTEXT, HOME, PATH`.

Log SHA256: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` (0 bytes) at `/tmp/salience-task35-exact-qualification-20261029T173910Z-35a80dc40a2a/verification/final-worktree.log`.

## Group 03 readback count reconciliation

The corrected group 03 command was run once and its exact log records `80 passed in 216.08s`; log SHA-256 is `2f27aeb500a20727249b8d9546a906d57702d9d76deeb4638ff21de650791634`. The command included the full current `tests/unit/test_temporal_schedule_readback.py` alongside the same three schedule integration files used by Task32.

A separate pytest collection-only metadata command ran against that current file with the same unique Compose project and local database/Temporal environment; no test bodies ran. It exited `0` and collected `29 tests` in `0.54s`. Environment keys: `COMPOSE_PROJECT_NAME, DATABASE_URL, DOCKER_CONTEXT, HOME, PATH, TEMPORAL_TARGET, TEST_DATABASE_URL, TEST_TEMPORAL_TARGET`. Command: `env -i COMPOSE_PROJECT_NAME=salience-p1-task35-q-35a80dc40a2a DATABASE_URL=<local disposable endpoint; credential redacted> DOCKER_CONTEXT=default HOME=/home/admin PATH=<minimal> TEMPORAL_TARGET=<local disposable endpoint> TEST_DATABASE_URL=<local disposable endpoint; credential redacted> TEST_TEMPORAL_TARGET=<local disposable endpoint> /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider -m 'not live' --collect-only -q tests/unit/test_temporal_schedule_readback.py`. Its log is `/tmp/salience-task35-exact-qualification-20261029T173910Z-35a80dc40a2a/verification/readback-unit-collect-only.log` (SHA-256 `916694c44a1df8287ef4e0d284d391584db91c830475c7d558db8c96c203f9c6`, 3,364 bytes); metadata is in `/tmp/salience-task35-exact-qualification-20261029T173910Z-35a80dc40a2a/verification/readback-unit-collect-only-metadata.json`.

Task25's final fix-round report records `29 passed` for the complete readback unit file; its immediately prior fix-round records `28 passed`. Task32's group 03 command used the same four paths and logged `79 passed in 256.94s` (log SHA-256 `46542dfd4ebba489a59995402a0b106a5ede1dcb51618041df84101a003a6230`). The Task32 artifact copy of the readback file is blob-identifiable at SHA-256 `68692750db02df64eaa9860fae699e9274874adeb8e5272933a5bb14163f29f` (35,018 bytes; 19 test functions); the current file is SHA-256 `3691e275ef60440e82c8851b181b35499350f8a7a86bb665646db2206905e31f` (37,591 bytes; 20 test functions). A read-only diff of all group 03 inputs from Task32 artifact `cc9d55fd67f10f7f8d831e21b9331ecae08cf039` to Task35 artifact `fe6b3795ce4453beb6c6bbafcc20cd3be91b3b87` found only this unit file changed; the three integration files are unchanged. The current 29-item collection is one case larger than Task32's 28-item file, matching the group count moving from 79 to 80.

## Focused groups and full suite

Focused groups run: `7/7` exactly once each, serially.
Focused groups passed: `7/7`.
Full non-live suite run: `True`.
Full non-live suite exit: `0`.
Fresh migration current revision: `0051_p1_recovery_rollback_v2`.
Compose cleanup counts by project label: `{"containers": 0, "networks": 0, "volumes": 0}`.

No tracked source files were edited or committed. Logs redact local endpoint and credential values; no provider or production credentials were passed.
