# Task 29 exact-candidate local qualification

Status: **PASSED**

Artifact HEAD: `33cac5cffff973e9cba5e03be6b493065e5f817d`
Supplied source reference: `cf070328d035f9010b760431173845c6afdfedee`
Compose project: `salience-p1-task29-q-291ea23ae3c0`
Evidence directory: `/tmp/salience-task29-exact-qualification-20261029T124413Z-291ea23ae3c0`
Started: `2026-10-29T12:44:13+00:00`
Updated at: `2026-10-29T13:49:22+00:00`

The exact candidate was required clean at start and final checks. Only local Compose `postgres` and `temporal` services were started, using `--pull never`; no provider, production, live schedule, alert, or external acceptance service was contacted. No provider or production credentials were passed. All migration and pytest subprocesses received the same `COMPOSE_PROJECT_NAME`, `DATABASE_URL`, `TEST_DATABASE_URL`, `TEST_TEMPORAL_TARGET`, and `TEMPORAL_TARGET`, with the database and Temporal endpoints resolving to this disposable stack. The 0051 downgrade schema-readback test therefore targeted this exact Compose project.

Expected fresh migration/current revision: `0051_p1_recovery_rollback_v2`. External provider/acceptance rows remain **NOT RUN**; group 07 is the local external-acceptance unit test. The schedule/readback group was executed once against the current tree; its actual count is recorded below.

Environment keys passed to migration and test subprocesses: `PATH`, `HOME`, `DOCKER_CONTEXT`, `DATABASE_URL`, `TEST_DATABASE_URL`, `TEST_TEMPORAL_TARGET`, `TEMPORAL_TARGET`, `COMPOSE_PROJECT_NAME`. Endpoint values are omitted/redacted in this report and recorded only in the live subprocess environment.

## Command evidence

### preflight-head

Command: `git rev-parse HEAD`

Started: `2026-10-29T12:44:13+00:00`; ended: `2026-10-29T12:44:13+00:00`; exit: `0`; elapsed: `0.004s`.

Environment keys: `CODEX_CI, CODEX_HOME, CODEX_SESSION_ID, CODEX_SQLITE_HOME, CODEX_THREAD_ID, CODEX_VERSION, COLORTERM, DBUS_SESSION_BUS_ADDRESS, GH_PAGER, GIT_PAGER, GPG_AGENT_INFO, HOME, INVOCATION_ID, JOURNAL_STREAM, LANG, LC_ALL, LC_CTYPE, LOGNAME, MANAGERPID, MEMORY_PRESSURE_WATCH, MEMORY_PRESSURE_WRITE, MYCODEX_HOME, MYCODEX_PROFILE, MYCODEX_ROLE, NO_COLOR, PAGER, PATH, PWD, PYTHONUNBUFFERED, SHELL, SHLVL, SSH_AUTH_SOCK, SYSTEMD_EXEC_PID, TERM, USER, XDG_RUNTIME_DIR, _`.

Log SHA256: `851b98ca2807d43f1ace32a592d72eff7ba0c92faf8c0590ada8ac8e7c2daffb` (41 bytes) at `/tmp/salience-task29-exact-qualification-20261029T124413Z-291ea23ae3c0/verification/preflight-head.log`.
### preflight-parent

Command: `git show -s --format=%P HEAD`

Started: `2026-10-29T12:44:13+00:00`; ended: `2026-10-29T12:44:13+00:00`; exit: `0`; elapsed: `0.004s`.

Environment keys: `CODEX_CI, CODEX_HOME, CODEX_SESSION_ID, CODEX_SQLITE_HOME, CODEX_THREAD_ID, CODEX_VERSION, COLORTERM, DBUS_SESSION_BUS_ADDRESS, GH_PAGER, GIT_PAGER, GPG_AGENT_INFO, HOME, INVOCATION_ID, JOURNAL_STREAM, LANG, LC_ALL, LC_CTYPE, LOGNAME, MANAGERPID, MEMORY_PRESSURE_WATCH, MEMORY_PRESSURE_WRITE, MYCODEX_HOME, MYCODEX_PROFILE, MYCODEX_ROLE, NO_COLOR, PAGER, PATH, PWD, PYTHONUNBUFFERED, SHELL, SHLVL, SSH_AUTH_SOCK, SYSTEMD_EXEC_PID, TERM, USER, XDG_RUNTIME_DIR, _`.

Log SHA256: `6eb414c2dbfb9dfd8b8b4d78df46a4ddfe05b141c90a60ffe3ef940bdafa3ea7` (41 bytes) at `/tmp/salience-task29-exact-qualification-20261029T124413Z-291ea23ae3c0/verification/preflight-parent.log`.
### preflight-worktree

Command: `git status --porcelain=v1 --untracked-files=all`

Started: `2026-10-29T12:44:13+00:00`; ended: `2026-10-29T12:44:13+00:00`; exit: `0`; elapsed: `0.018s`.

Environment keys: `CODEX_CI, CODEX_HOME, CODEX_SESSION_ID, CODEX_SQLITE_HOME, CODEX_THREAD_ID, CODEX_VERSION, COLORTERM, DBUS_SESSION_BUS_ADDRESS, GH_PAGER, GIT_PAGER, GPG_AGENT_INFO, HOME, INVOCATION_ID, JOURNAL_STREAM, LANG, LC_ALL, LC_CTYPE, LOGNAME, MANAGERPID, MEMORY_PRESSURE_WATCH, MEMORY_PRESSURE_WRITE, MYCODEX_HOME, MYCODEX_PROFILE, MYCODEX_ROLE, NO_COLOR, PAGER, PATH, PWD, PYTHONUNBUFFERED, SHELL, SHLVL, SSH_AUTH_SOCK, SYSTEMD_EXEC_PID, TERM, USER, XDG_RUNTIME_DIR, _`.

Log SHA256: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` (0 bytes) at `/tmp/salience-task29-exact-qualification-20261029T124413Z-291ea23ae3c0/verification/preflight-worktree.log`.
### compose-up-postgres-temporal

Command: `docker --context default compose --env-file /tmp/salience-task29-exact-qualification-20261029T124413Z-291ea23ae3c0/empty-compose.env --project-name salience-p1-task29-q-291ea23ae3c0 up -d --pull never --wait --wait-timeout 180 postgres temporal`

Started: `2026-10-29T12:44:13+00:00`; ended: `2026-10-29T12:44:20+00:00`; exit: `0`; elapsed: `6.783s`.

Environment keys: `COMPOSE_PROJECT_NAME, DOCKER_CONTEXT, HOME, PATH`.

Log SHA256: `482b505c7bd098f75936c4f21c1ac81e5d0f499d2ba34c16daa944899a559109` (1418 bytes) at `/tmp/salience-task29-exact-qualification-20261029T124413Z-291ea23ae3c0/verification/compose-up-postgres-temporal.log`.
### compose-ps-postgres

Command: `docker --context default compose --env-file /tmp/salience-task29-exact-qualification-20261029T124413Z-291ea23ae3c0/empty-compose.env --project-name salience-p1-task29-q-291ea23ae3c0 ps -q postgres`

Started: `2026-10-29T12:44:20+00:00`; ended: `2026-10-29T12:44:20+00:00`; exit: `0`; elapsed: `0.114s`.

Environment keys: `COMPOSE_PROJECT_NAME, DOCKER_CONTEXT, HOME, PATH`.

Log SHA256: `9e783bdf88b5d894288f585ebe99228fe4afe2dfbcc4896454f8a9467031cac5` (65 bytes) at `/tmp/salience-task29-exact-qualification-20261029T124413Z-291ea23ae3c0/verification/compose-ps-postgres.log`.
### inspect-ip-postgres

Command: `docker --context default inspect -f '{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}' 76af62486f20738ff4a147d56a75a387f5c6ca60a66559b7df7edcb4696e885e`

Started: `2026-10-29T12:44:20+00:00`; ended: `2026-10-29T12:44:20+00:00`; exit: `0`; elapsed: `0.033s`.

Environment keys: `DOCKER_CONTEXT, HOME, PATH`.

Log SHA256: `870abc46b1af55506101fb91ed5852682d5374ffd5d796b7b527760e62240dea` (11 bytes) at `/tmp/salience-task29-exact-qualification-20261029T124413Z-291ea23ae3c0/verification/inspect-ip-postgres.log`.
### compose-ps-temporal

Command: `docker --context default compose --env-file /tmp/salience-task29-exact-qualification-20261029T124413Z-291ea23ae3c0/empty-compose.env --project-name salience-p1-task29-q-291ea23ae3c0 ps -q temporal`

Started: `2026-10-29T12:44:20+00:00`; ended: `2026-10-29T12:44:20+00:00`; exit: `0`; elapsed: `0.119s`.

Environment keys: `COMPOSE_PROJECT_NAME, DOCKER_CONTEXT, HOME, PATH`.

Log SHA256: `a853144549ef91c83f5e37cc36aa0cddab44ef13ae08a4dc56a8e87553b582c5` (65 bytes) at `/tmp/salience-task29-exact-qualification-20261029T124413Z-291ea23ae3c0/verification/compose-ps-temporal.log`.
### inspect-ip-temporal

Command: `docker --context default inspect -f '{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}' 7a15ec967af32028ae13c1c529b9075c467ddfbf948026c2de873deaa2df6b54`

Started: `2026-10-29T12:44:20+00:00`; ended: `2026-10-29T12:44:20+00:00`; exit: `0`; elapsed: `0.033s`.

Environment keys: `DOCKER_CONTEXT, HOME, PATH`.

Log SHA256: `d6d6b03795a1c1774e97c843e6542c6de18b34b3f219d45f38bb675a9f70dfeb` (11 bytes) at `/tmp/salience-task29-exact-qualification-20261029T124413Z-291ea23ae3c0/verification/inspect-ip-temporal.log`.
### local-endpoint-readiness-postgres

Command: `/home/admin/Salience/.venv/bin/python -c <local TCP readiness probe> 172.20.0.2 5432`

Started: `2026-10-29T12:44:20+00:00`; ended: `2026-10-29T12:44:20+00:00`; exit: `0`; elapsed: `0.034s`.

Environment keys: `DOCKER_CONTEXT, HOME, PATH`.

Log SHA256: `2032f1dfa29eb1fb75f12dd2faba45eb90588f9b88315a7d3c614b4c2368956e` (10 bytes) at `/tmp/salience-task29-exact-qualification-20261029T124413Z-291ea23ae3c0/verification/local-endpoint-readiness-postgres.log`.
### local-endpoint-readiness-temporal

Command: `/home/admin/Salience/.venv/bin/python -c <local TCP readiness probe> 172.20.0.3 7233`

Started: `2026-10-29T12:44:20+00:00`; ended: `2026-10-29T12:44:20+00:00`; exit: `0`; elapsed: `0.033s`.

Environment keys: `DOCKER_CONTEXT, HOME, PATH`.

Log SHA256: `2032f1dfa29eb1fb75f12dd2faba45eb90588f9b88315a7d3c614b4c2368956e` (10 bytes) at `/tmp/salience-task29-exact-qualification-20261029T124413Z-291ea23ae3c0/verification/local-endpoint-readiness-temporal.log`.
### alembic-upgrade-head

Command: `env -i COMPOSE_PROJECT_NAME=salience-p1-task29-q-291ea23ae3c0 DATABASE_URL=<local disposable endpoint; credential redacted> DOCKER_CONTEXT=default HOME=/home/admin PATH=<minimal> TEMPORAL_TARGET=<local disposable endpoint> TEST_DATABASE_URL=<local disposable endpoint; credential redacted> TEST_TEMPORAL_TARGET=<local disposable endpoint> /home/admin/Salience/.venv/bin/python -m alembic upgrade head`

Started: `2026-10-29T12:44:20+00:00`; ended: `2026-10-29T12:44:22+00:00`; exit: `0`; elapsed: `1.721s`.

Environment keys: `COMPOSE_PROJECT_NAME, DATABASE_URL, DOCKER_CONTEXT, HOME, PATH, TEMPORAL_TARGET, TEST_DATABASE_URL, TEST_TEMPORAL_TARGET`.

Log SHA256: `5f4e10c1dccc9962e7f52f3cf4f6dc8dd61f6ce7d529531918923889aa1b4b3d` (9165 bytes) at `/tmp/salience-task29-exact-qualification-20261029T124413Z-291ea23ae3c0/verification/alembic-upgrade-head.log`.
### alembic-current

Command: `env -i COMPOSE_PROJECT_NAME=salience-p1-task29-q-291ea23ae3c0 DATABASE_URL=<local disposable endpoint; credential redacted> DOCKER_CONTEXT=default HOME=/home/admin PATH=<minimal> TEMPORAL_TARGET=<local disposable endpoint> TEST_DATABASE_URL=<local disposable endpoint; credential redacted> TEST_TEMPORAL_TARGET=<local disposable endpoint> /home/admin/Salience/.venv/bin/python -m alembic current`

Started: `2026-10-29T12:44:22+00:00`; ended: `2026-10-29T12:44:23+00:00`; exit: `0`; elapsed: `0.817s`.

Environment keys: `COMPOSE_PROJECT_NAME, DATABASE_URL, DOCKER_CONTEXT, HOME, PATH, TEMPORAL_TARGET, TEST_DATABASE_URL, TEST_TEMPORAL_TARGET`.

Log SHA256: `b4695e95e313ffb6440fb361ffee38640aec70511b35c2d6cf2c7968e2ada401` (164 bytes) at `/tmp/salience-task29-exact-qualification-20261029T124413Z-291ea23ae3c0/verification/alembic-current.log`.
### focused-01-routing-files-lock-builder

Command: `env -i COMPOSE_PROJECT_NAME=salience-p1-task29-q-291ea23ae3c0 DATABASE_URL=<local disposable endpoint; credential redacted> DOCKER_CONTEXT=default HOME=/home/admin PATH=<minimal> TEMPORAL_TARGET=<local disposable endpoint> TEST_DATABASE_URL=<local disposable endpoint; credential redacted> TEST_TEMPORAL_TARGET=<local disposable endpoint> /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/unit/test_production_routing.py tests/integration/test_p1_production_routing.py tests/integration/test_v4_publication_recovery_floor.py::test_launcher_v2_lock_builder_binds_exact_final_migration_head_0051_and_verifier_abi`

Started: `2026-10-29T12:44:23+00:00`; ended: `2026-10-29T12:44:26+00:00`; exit: `0`; elapsed: `2.622s`.

Environment keys: `COMPOSE_PROJECT_NAME, DATABASE_URL, DOCKER_CONTEXT, HOME, PATH, TEMPORAL_TARGET, TEST_DATABASE_URL, TEST_TEMPORAL_TARGET`.

Log SHA256: `dbe21ec38a639959269b867e919325e811dafb00e01842c7279067b6e0dca4bf` (99 bytes) at `/tmp/salience-task29-exact-qualification-20261029T124413Z-291ea23ae3c0/focused/01-routing-files-lock-builder.log`.

Test counts: `{"passed": 38}`.

Summary: `38 passed in 2.08s`.
### focused-02-migrations-0047-0051-schema-safety

Command: `env -i COMPOSE_PROJECT_NAME=salience-p1-task29-q-291ea23ae3c0 DATABASE_URL=<local disposable endpoint; credential redacted> DOCKER_CONTEXT=default HOME=/home/admin PATH=<minimal> TEMPORAL_TARGET=<local disposable endpoint> TEST_DATABASE_URL=<local disposable endpoint; credential redacted> TEST_TEMPORAL_TARGET=<local disposable endpoint> /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/integration/test_v4_migrations.py tests/integration/test_p1_production_recovery.py -k '0047 or 0048 or 0049 or 0050 or 0051 or schema_and_migration_safety'`

Started: `2026-10-29T12:44:26+00:00`; ended: `2026-10-29T12:48:38+00:00`; exit: `0`; elapsed: `252.768s`.

Environment keys: `COMPOSE_PROJECT_NAME, DATABASE_URL, DOCKER_CONTEXT, HOME, PATH, TEMPORAL_TARGET, TEST_DATABASE_URL, TEST_TEMPORAL_TARGET`.

Log SHA256: `5908c70966914c8fcbc60f76b5ad964d2cee44cdbfc4a92861a130bce7bf40b1` (127 bytes) at `/tmp/salience-task29-exact-qualification-20261029T124413Z-291ea23ae3c0/focused/02-migrations-0047-0051-schema-safety.log`.

Test counts: `{"deselected": 113, "passed": 39}`.

Summary: `39 passed, 113 deselected in 252.34s (0:04:12)`.
### focused-03-schedule-native-readback

Command: `env -i COMPOSE_PROJECT_NAME=salience-p1-task29-q-291ea23ae3c0 DATABASE_URL=<local disposable endpoint; credential redacted> DOCKER_CONTEXT=default HOME=/home/admin PATH=<minimal> TEMPORAL_TARGET=<local disposable endpoint> TEST_DATABASE_URL=<local disposable endpoint; credential redacted> TEST_TEMPORAL_TARGET=<local disposable endpoint> /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/integration/test_p1_production_schedule_fencing.py tests/integration/test_v4_native_schedules.py tests/integration/test_v4_native_publication_schedules.py tests/unit/test_temporal_schedule_readback.py`

Started: `2026-10-29T12:48:38+00:00`; ended: `2026-10-29T12:52:13+00:00`; exit: `0`; elapsed: `214.191s`.

Environment keys: `COMPOSE_PROJECT_NAME, DATABASE_URL, DOCKER_CONTEXT, HOME, PATH, TEMPORAL_TARGET, TEST_DATABASE_URL, TEST_TEMPORAL_TARGET`.

Log SHA256: `6e381adb96f62e05aadd5dbcd86921e67f276145b2e3b434d868f7a2107f819a` (111 bytes) at `/tmp/salience-task29-exact-qualification-20261029T124413Z-291ea23ae3c0/focused/03-schedule-native-readback.log`.

Test counts: `{"passed": 71}`.

Summary: `71 passed in 213.21s (0:03:33)`.
### focused-04-complete-production-recovery

Command: `env -i COMPOSE_PROJECT_NAME=salience-p1-task29-q-291ea23ae3c0 DATABASE_URL=<local disposable endpoint; credential redacted> DOCKER_CONTEXT=default HOME=/home/admin PATH=<minimal> TEMPORAL_TARGET=<local disposable endpoint> TEST_DATABASE_URL=<local disposable endpoint; credential redacted> TEST_TEMPORAL_TARGET=<local disposable endpoint> /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/integration/test_p1_production_recovery.py`

Started: `2026-10-29T12:52:13+00:00`; ended: `2026-10-29T13:01:25+00:00`; exit: `0`; elapsed: `551.983s`.

Environment keys: `COMPOSE_PROJECT_NAME, DATABASE_URL, DOCKER_CONTEXT, HOME, PATH, TEMPORAL_TARGET, TEST_DATABASE_URL, TEST_TEMPORAL_TARGET`.

Log SHA256: `b19311ffb7ef98dfd7e119105099997d4400a9b28779170dd9fd16bd39172def` (191 bytes) at `/tmp/salience-task29-exact-qualification-20261029T124413Z-291ea23ae3c0/focused/04-complete-production-recovery.log`.

Test counts: `{"passed": 79}`.

Summary: `79 passed in 551.36s (0:09:11)`.
### focused-05-rollback-v2-handoff

Command: `env -i COMPOSE_PROJECT_NAME=salience-p1-task29-q-291ea23ae3c0 DATABASE_URL=<local disposable endpoint; credential redacted> DOCKER_CONTEXT=default HOME=/home/admin PATH=<minimal> TEMPORAL_TARGET=<local disposable endpoint> TEST_DATABASE_URL=<local disposable endpoint; credential redacted> TEST_TEMPORAL_TARGET=<local disposable endpoint> /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/integration/test_p1_production_routing.py::test_launcher_v2_runs_pre_and_post_readback_before_prior_code tests/integration/test_p1_production_routing.py::test_rollback_v2_hands_off_authenticated_deployable_only_after_verification tests/integration/test_p1_production_routing.py::test_rollback_v2_fails_closed_without_authenticated_deployable_executor tests/integration/test_p1_production_routing.py::test_launcher_v2_rejects_caller_supplied_hold_lists`

Started: `2026-10-29T13:01:25+00:00`; ended: `2026-10-29T13:01:26+00:00`; exit: `0`; elapsed: `1.468s`.

Environment keys: `COMPOSE_PROJECT_NAME, DATABASE_URL, DOCKER_CONTEXT, HOME, PATH, TEMPORAL_TARGET, TEST_DATABASE_URL, TEST_TEMPORAL_TARGET`.

Log SHA256: `54d23ef0b56f99d2cbe2d50ca07723596b53070e1dcae86069f33fb176336fed` (98 bytes) at `/tmp/salience-task29-exact-qualification-20261029T124413Z-291ea23ae3c0/focused/05-rollback-v2-handoff.log`.

Test counts: `{"passed": 4}`.

Summary: `4 passed in 0.93s`.
### focused-06-complete-policy-revocation

Command: `env -i COMPOSE_PROJECT_NAME=salience-p1-task29-q-291ea23ae3c0 DATABASE_URL=<local disposable endpoint; credential redacted> DOCKER_CONTEXT=default HOME=/home/admin PATH=<minimal> TEMPORAL_TARGET=<local disposable endpoint> TEST_DATABASE_URL=<local disposable endpoint; credential redacted> TEST_TEMPORAL_TARGET=<local disposable endpoint> /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/integration/test_p1_production_policy_revocation.py`

Started: `2026-10-29T13:01:26+00:00`; ended: `2026-10-29T13:02:45+00:00`; exit: `0`; elapsed: `78.835s`.

Environment keys: `COMPOSE_PROJECT_NAME, DATABASE_URL, DOCKER_CONTEXT, HOME, PATH, TEMPORAL_TARGET, TEST_DATABASE_URL, TEST_TEMPORAL_TARGET`.

Log SHA256: `dc8426427f1c203aacdc13e6e393f1ceb9c8c031ed90fcc3256101ef4adb3cb0` (191 bytes) at `/tmp/salience-task29-exact-qualification-20261029T124413Z-291ea23ae3c0/focused/06-complete-policy-revocation.log`.

Test counts: `{"passed": 137}`.

Summary: `137 passed in 78.29s (0:01:18)`.
### focused-07-external-acceptance-unit

Command: `env -i COMPOSE_PROJECT_NAME=salience-p1-task29-q-291ea23ae3c0 DATABASE_URL=<local disposable endpoint; credential redacted> DOCKER_CONTEXT=default HOME=/home/admin PATH=<minimal> TEMPORAL_TARGET=<local disposable endpoint> TEST_DATABASE_URL=<local disposable endpoint; credential redacted> TEST_TEMPORAL_TARGET=<local disposable endpoint> /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/unit/test_p1_external_acceptance.py`

Started: `2026-10-29T13:02:45+00:00`; ended: `2026-10-29T13:05:53+00:00`; exit: `0`; elapsed: `188.498s`.

Environment keys: `COMPOSE_PROJECT_NAME, DATABASE_URL, DOCKER_CONTEXT, HOME, PATH, TEMPORAL_TARGET, TEST_DATABASE_URL, TEST_TEMPORAL_TARGET`.

Log SHA256: `ba392f1cf9c8e697e920aae6220c06e0500b35a531192e5d3e1d69c4285bbe23` (111 bytes) at `/tmp/salience-task29-exact-qualification-20261029T124413Z-291ea23ae3c0/focused/07-external-acceptance-unit.log`.

Test counts: `{"passed": 45}`.

Summary: `45 passed in 188.17s (0:03:08)`.
### full-non-live-suite

Command: `env -i COMPOSE_PROJECT_NAME=salience-p1-task29-q-291ea23ae3c0 DATABASE_URL=<local disposable endpoint; credential redacted> DOCKER_CONTEXT=default HOME=/home/admin PATH=<minimal> TEMPORAL_TARGET=<local disposable endpoint> TEST_DATABASE_URL=<local disposable endpoint; credential redacted> TEST_TEMPORAL_TARGET=<local disposable endpoint> /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider tests/contracts tests/unit tests/integration tests/e2e tests/evals -m 'not live' -q -rs --tb=short`

Started: `2026-10-29T13:05:53+00:00`; ended: `2026-10-29T13:49:19+00:00`; exit: `0`; elapsed: `2605.827s`.

Environment keys: `COMPOSE_PROJECT_NAME, DATABASE_URL, DOCKER_CONTEXT, HOME, PATH, TEMPORAL_TARGET, TEST_DATABASE_URL, TEST_TEMPORAL_TARGET`.

Log SHA256: `d7d1d0ffe497665fb5be923cfd2a5022cf283b11a36610e3abcd7d35e5a1dc74` (3474 bytes) at `/tmp/salience-task29-exact-qualification-20261029T124413Z-291ea23ae3c0/full/full-non-live-suite.log`.

Test counts: `{"passed": 1203, "skipped": 26, "warnings": 3}`.

Summary: `1203 passed, 26 skipped, 3 warnings in 2602.15s (0:43:22)`.
### compose-cleanup

Command: `docker --context default compose --env-file /tmp/salience-task29-exact-qualification-20261029T124413Z-291ea23ae3c0/empty-compose.env --project-name salience-p1-task29-q-291ea23ae3c0 down --volumes --remove-orphans`

Started: `2026-10-29T13:49:19+00:00`; ended: `2026-10-29T13:49:22+00:00`; exit: `0`; elapsed: `2.872s`.

Environment keys: `COMPOSE_PROJECT_NAME, DOCKER_CONTEXT, HOME, PATH`.

Log SHA256: `d3b92ee4fc832d4b066c1be4f56f9de5ea664985f4dd1db56b05e8ae75e95b7e` (776 bytes) at `/tmp/salience-task29-exact-qualification-20261029T124413Z-291ea23ae3c0/verification/compose-cleanup.log`.
### cleanup-verify-containers

Command: `docker --context default ps -aq --filter label=com.docker.compose.project=salience-p1-task29-q-291ea23ae3c0`

Started: `2026-10-29T13:49:22+00:00`; ended: `2026-10-29T13:49:22+00:00`; exit: `0`; elapsed: `0.032s`.

Environment keys: `DOCKER_CONTEXT, HOME, PATH`.

Log SHA256: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` (0 bytes) at `/tmp/salience-task29-exact-qualification-20261029T124413Z-291ea23ae3c0/verification/cleanup-verify-containers.log`.
### cleanup-verify-networks

Command: `docker --context default network ls -q --filter label=com.docker.compose.project=salience-p1-task29-q-291ea23ae3c0`

Started: `2026-10-29T13:49:22+00:00`; ended: `2026-10-29T13:49:22+00:00`; exit: `0`; elapsed: `0.032s`.

Environment keys: `DOCKER_CONTEXT, HOME, PATH`.

Log SHA256: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` (0 bytes) at `/tmp/salience-task29-exact-qualification-20261029T124413Z-291ea23ae3c0/verification/cleanup-verify-networks.log`.
### cleanup-verify-volumes

Command: `docker --context default volume ls -q --filter label=com.docker.compose.project=salience-p1-task29-q-291ea23ae3c0`

Started: `2026-10-29T13:49:22+00:00`; ended: `2026-10-29T13:49:22+00:00`; exit: `0`; elapsed: `0.033s`.

Environment keys: `DOCKER_CONTEXT, HOME, PATH`.

Log SHA256: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` (0 bytes) at `/tmp/salience-task29-exact-qualification-20261029T124413Z-291ea23ae3c0/verification/cleanup-verify-volumes.log`.
### final-head

Command: `git rev-parse HEAD`

Started: `2026-10-29T13:49:22+00:00`; ended: `2026-10-29T13:49:22+00:00`; exit: `0`; elapsed: `0.009s`.

Environment keys: `CODEX_CI, CODEX_HOME, CODEX_SESSION_ID, CODEX_SQLITE_HOME, CODEX_THREAD_ID, CODEX_VERSION, COLORTERM, DBUS_SESSION_BUS_ADDRESS, GH_PAGER, GIT_PAGER, GPG_AGENT_INFO, HOME, INVOCATION_ID, JOURNAL_STREAM, LANG, LC_ALL, LC_CTYPE, LOGNAME, MANAGERPID, MEMORY_PRESSURE_WATCH, MEMORY_PRESSURE_WRITE, MYCODEX_HOME, MYCODEX_PROFILE, MYCODEX_ROLE, NO_COLOR, PAGER, PATH, PWD, PYTHONUNBUFFERED, SHELL, SHLVL, SSH_AUTH_SOCK, SYSTEMD_EXEC_PID, TERM, USER, XDG_RUNTIME_DIR, _`.

Log SHA256: `851b98ca2807d43f1ace32a592d72eff7ba0c92faf8c0590ada8ac8e7c2daffb` (41 bytes) at `/tmp/salience-task29-exact-qualification-20261029T124413Z-291ea23ae3c0/verification/final-head.log`.
### final-parent

Command: `git show -s --format=%P HEAD`

Started: `2026-10-29T13:49:22+00:00`; ended: `2026-10-29T13:49:22+00:00`; exit: `0`; elapsed: `0.004s`.

Environment keys: `CODEX_CI, CODEX_HOME, CODEX_SESSION_ID, CODEX_SQLITE_HOME, CODEX_THREAD_ID, CODEX_VERSION, COLORTERM, DBUS_SESSION_BUS_ADDRESS, GH_PAGER, GIT_PAGER, GPG_AGENT_INFO, HOME, INVOCATION_ID, JOURNAL_STREAM, LANG, LC_ALL, LC_CTYPE, LOGNAME, MANAGERPID, MEMORY_PRESSURE_WATCH, MEMORY_PRESSURE_WRITE, MYCODEX_HOME, MYCODEX_PROFILE, MYCODEX_ROLE, NO_COLOR, PAGER, PATH, PWD, PYTHONUNBUFFERED, SHELL, SHLVL, SSH_AUTH_SOCK, SYSTEMD_EXEC_PID, TERM, USER, XDG_RUNTIME_DIR, _`.

Log SHA256: `6eb414c2dbfb9dfd8b8b4d78df46a4ddfe05b141c90a60ffe3ef940bdafa3ea7` (41 bytes) at `/tmp/salience-task29-exact-qualification-20261029T124413Z-291ea23ae3c0/verification/final-parent.log`.
### final-worktree

Command: `git status --porcelain=v1 --untracked-files=all`

Started: `2026-10-29T13:49:22+00:00`; ended: `2026-10-29T13:49:22+00:00`; exit: `0`; elapsed: `0.016s`.

Environment keys: `CODEX_CI, CODEX_HOME, CODEX_SESSION_ID, CODEX_SQLITE_HOME, CODEX_THREAD_ID, CODEX_VERSION, COLORTERM, DBUS_SESSION_BUS_ADDRESS, GH_PAGER, GIT_PAGER, GPG_AGENT_INFO, HOME, INVOCATION_ID, JOURNAL_STREAM, LANG, LC_ALL, LC_CTYPE, LOGNAME, MANAGERPID, MEMORY_PRESSURE_WATCH, MEMORY_PRESSURE_WRITE, MYCODEX_HOME, MYCODEX_PROFILE, MYCODEX_ROLE, NO_COLOR, PAGER, PATH, PWD, PYTHONUNBUFFERED, SHELL, SHLVL, SSH_AUTH_SOCK, SYSTEMD_EXEC_PID, TERM, USER, XDG_RUNTIME_DIR, _`.

Log SHA256: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` (0 bytes) at `/tmp/salience-task29-exact-qualification-20261029T124413Z-291ea23ae3c0/verification/final-worktree.log`.

## Focused group and suite result

Focused groups run: `7/7` exactly once each, serially.
All seven focused groups passed: `True`.
Full non-live suite run: `True` (gate required all seven focused groups to pass).
Full non-live suite passed: `True`.
Final exact clean worktree: `True`.
Cleanup counts by project label: `{"containers": 0, "networks": 0, "volumes": 0}`.

## Failure details

None.

No source files were edited or committed. Logs contain no provider or production credentials.
