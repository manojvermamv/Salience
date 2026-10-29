# Task 28 exact-candidate local qualification

Status: **FAILED**

Artifact HEAD: `33cac5cffff973e9cba5e03be6b493065e5f817d`
Supplied source reference: `cf070328d035f9010b760431173845c6afdfedee`
Compose project: `salience-p1-task28-q-276c0a98819e`
Evidence directory: `/tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e`
Updated at: `2026-10-29T12:41:15.980171+00:00`

The checkout was required to be clean at the exact artifact HEAD, directly parented by the supplied source reference. Only the local `postgres` and `temporal` Compose services were started with image pulls disabled. The test and migration processes used an isolated environment containing only PATH/HOME plus local disposable database and Temporal endpoints. No provider, production, live schedule, alert, or external acceptance service was contacted, and no provider or production credentials were passed.

Expected migration revision: `0051_p1_recovery_rollback_v2`. External provider/acceptance rows: **NOT RUN**; the external acceptance unit test was run as a focused unit group.

## Command evidence

### preflight-head

Command: `git rev-parse HEAD`
Started: `2026-10-29T12:15:27.316721+00:00`; ended: `2026-10-29T12:15:28.317422+00:00`; exit: `0`; elapsed: `1.001s`.
Log SHA-256: `851b98ca2807d43f1ace32a592d72eff7ba0c92faf8c0590ada8ac8e7c2daffb` (41 bytes) at `/tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/verification/preflight-head.log`.

Tail:
```text
33cac5cffff973e9cba5e03be6b493065e5f817d
```

### preflight-parent

Command: `git show -s --format=%P HEAD`
Started: `2026-10-29T12:15:28.317602+00:00`; ended: `2026-10-29T12:15:29.318303+00:00`; exit: `0`; elapsed: `1.001s`.
Log SHA-256: `6eb414c2dbfb9dfd8b8b4d78df46a4ddfe05b141c90a60ffe3ef940bdafa3ea7` (41 bytes) at `/tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/verification/preflight-parent.log`.

Tail:
```text
cf070328d035f9010b760431173845c6afdfedee
```

### preflight-worktree

Command: `git status --porcelain=v1 --untracked-files=all`
Started: `2026-10-29T12:15:29.318462+00:00`; ended: `2026-10-29T12:15:30.319121+00:00`; exit: `0`; elapsed: `1.001s`.
Log SHA-256: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` (0 bytes) at `/tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/verification/preflight-worktree.log`.

### compose-up-postgres-temporal

Command: `docker --context default compose --env-file /tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/empty-compose.env --project-name salience-p1-task28-q-276c0a98819e up -d --pull never --wait --wait-timeout 180 postgres temporal`
Started: `2026-10-29T12:15:30.319426+00:00`; ended: `2026-10-29T12:15:37.324937+00:00`; exit: `0`; elapsed: `7.006s`.
Log SHA-256: `19fccdbd0135dce1da60ddbb8c78d1583bbc0ae7887b88d9234a6a459d669d03` (1418 bytes) at `/tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/verification/compose-up-postgres-temporal.log`.

Tail:
```text
 Network salience-p1-task28-q-276c0a98819e_default Created
 Container salience-p1-task28-q-276c0a98819e-postgres-1 Creating
 Container salience-p1-task28-q-276c0a98819e-postgres-1 Created
 Container salience-p1-task28-q-276c0a98819e-temporal-1 Creating
 Container salience-p1-task28-q-276c0a98819e-temporal-1 Created
 Container salience-p1-task28-q-276c0a98819e-postgres-1 Starting
 Container salience-p1-task28-q-276c0a98819e-postgres-1 Started
 Container salience-p1-task28-q-276c0a98819e-postgres-1 Waiting
 Container salience-p1-task28-q-276c0a98819e-postgres-1 Healthy
 Container salience-p1-task28-q-276c0a98819e-temporal-1 Starting
 Container salience-p1-task28-q-276c0a98819e-temporal-1 Started
 Container salience-p1-task28-q-276c0a98819e-temporal-1 Waiting
 Container salience-p1-task28-q-276c0a98819e-postgres-1 Waiting
 Container salience-p1-task28-q-276c0a98819e-temporal-1 Healthy
 Container salience-p1-task28-q-276c0a98819e-postgres-1 Healthy
```

### compose-ps-postgres

Command: `docker --context default compose --env-file /tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/empty-compose.env --project-name salience-p1-task28-q-276c0a98819e ps -q postgres`
Started: `2026-10-29T12:15:37.325085+00:00`; ended: `2026-10-29T12:15:38.326888+00:00`; exit: `0`; elapsed: `1.002s`.
Log SHA-256: `eb47db1516bb1706afd28d3c98051ec8b9e609b3599a881aea7992dd2f7660dd` (65 bytes) at `/tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/verification/compose-ps-postgres.log`.

Tail:
```text
da4a6cbe34c49e47b4efdb8fa1e0abc0ead72105c98f228301f73238fb3f1f45
```

### inspect-ip-postgres

Command: `docker --context default inspect -f {{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}} da4a6cbe34c49e47b4efdb8fa1e0abc0ead72105c98f228301f73238fb3f1f45`
Started: `2026-10-29T12:15:38.328040+00:00`; ended: `2026-10-29T12:15:39.331651+00:00`; exit: `0`; elapsed: `1.004s`.
Log SHA-256: `870abc46b1af55506101fb91ed5852682d5374ffd5d796b7b527760e62240dea` (11 bytes) at `/tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/verification/inspect-ip-postgres.log`.

Tail:
```text
172.20.0.2
```

### compose-ps-temporal

Command: `docker --context default compose --env-file /tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/empty-compose.env --project-name salience-p1-task28-q-276c0a98819e ps -q temporal`
Started: `2026-10-29T12:15:39.331872+00:00`; ended: `2026-10-29T12:15:40.332725+00:00`; exit: `0`; elapsed: `1.001s`.
Log SHA-256: `e2e6d6641aba79c346956f493632cf1e950855d1913be3d676b6ab9fc701d554` (65 bytes) at `/tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/verification/compose-ps-temporal.log`.

Tail:
```text
ce414ddbb51e7f979ea1f58cd948f49148406916f08ae3f0901c003276aeb41d
```

### inspect-ip-temporal

Command: `docker --context default inspect -f {{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}} ce414ddbb51e7f979ea1f58cd948f49148406916f08ae3f0901c003276aeb41d`
Started: `2026-10-29T12:15:40.332929+00:00`; ended: `2026-10-29T12:15:41.333626+00:00`; exit: `0`; elapsed: `1.001s`.
Log SHA-256: `d6d6b03795a1c1774e97c843e6542c6de18b34b3f219d45f38bb675a9f70dfeb` (11 bytes) at `/tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/verification/inspect-ip-temporal.log`.

Tail:
```text
172.20.0.3
```

### local-endpoint-readiness-postgres

Command: `/home/admin/Salience/.venv/bin/python -c <local TCP readiness probe> 172.20.0.2 5432`
Started: `2026-10-29T12:15:41.334084+00:00`; ended: `2026-10-29T12:15:42.334692+00:00`; exit: `0`; elapsed: `1.001s`.
Log SHA-256: `2032f1dfa29eb1fb75f12dd2faba45eb90588f9b88315a7d3c614b4c2368956e` (10 bytes) at `/tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/verification/local-endpoint-readiness-postgres.log`.

Tail:
```text
TCP ready
```

### local-endpoint-readiness-temporal

Command: `/home/admin/Salience/.venv/bin/python -c <local TCP readiness probe> 172.20.0.3 7233`
Started: `2026-10-29T12:15:42.334841+00:00`; ended: `2026-10-29T12:15:43.335650+00:00`; exit: `0`; elapsed: `1.001s`.
Log SHA-256: `2032f1dfa29eb1fb75f12dd2faba45eb90588f9b88315a7d3c614b4c2368956e` (10 bytes) at `/tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/verification/local-endpoint-readiness-temporal.log`.

Tail:
```text
TCP ready
```

### alembic-upgrade-head

Command: `env -i PATH=<minimal> HOME=/home/admin DATABASE_URL=<local disposable endpoint; credential redacted> /home/admin/Salience/.venv/bin/python -m alembic upgrade head`
Started: `2026-10-29T12:15:43.336149+00:00`; ended: `2026-10-29T12:15:45.337300+00:00`; exit: `0`; elapsed: `2.001s`.
Log SHA-256: `5f4e10c1dccc9962e7f52f3cf4f6dc8dd61f6ce7d529531918923889aa1b4b3d` (9165 bytes) at `/tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/verification/alembic-upgrade-head.log`.

Tail:
```text
INFO  [alembic.runtime.migration] Running upgrade 0036_legacy_cycle_dispatch -> 0037_native_schedule_sources, Preserve original native schedules and separately retain cutover progress.
INFO  [alembic.runtime.migration] Running upgrade 0037_native_schedule_sources -> 0038_legacy_dummy_stage, Retain legacy intelligence bindings while qualifying no-send dummy starts.
INFO  [alembic.runtime.migration] Running upgrade 0038_legacy_dummy_stage -> 0039_legacy_creative_stage, Qualify fixed dry creative starts without rewriting original legacy history.
INFO  [alembic.runtime.migration] Running upgrade 0039_legacy_creative_stage -> 0040_legacy_publication_stage, Original publication identity with explicit fixture account approval and durable simulation.
INFO  [alembic.runtime.migration] Running upgrade 0040_legacy_publication_stage -> 0041_native_publication_sources, Preserve original governed-publication schedule identities and cutover progress.
INFO  [alembic.runtime.migration] Running upgrade 0041_native_publication_sources -> 0042_publication_recovery_holds, Record immutable evidence when a legacy publication must remain held.
INFO  [alembic.runtime.migration] Running upgrade 0042_publication_recovery_holds -> 0043_complete_publication_recovery_liabilities, Add the complete immutable publication recovery liability baseline.
INFO  [alembic.runtime.migration] Running upgrade 0043_complete_publication_recovery_liabilities -> 0044_production_policy_revocation, Add production effect permit history and terminal authority revisions.
INFO  [alembic.runtime.migration] Running upgrade 0044_production_policy_revocation -> 0045_production_policy_approval_guard_repair, Repair the strict production effect approval transition guard.
INFO  [alembic.runtime.migration] Running upgrade 0045_production_policy_approval_guard_repair -> 0046_production_policy_approval_id_immutability, Keep decided tagged effect approval identity and history immutable.
INFO  [alembic.runtime.migration] Running upgrade 0046_production_policy_approval_id_immutability -> 0047_p1_production_recovery_work_events, Add immutable production intent enrollment, recovery work, and event evidence.
INFO  [alembic.runtime.migration] Running upgrade 0047_p1_production_recovery_work_events -> 0048_p1_production_schedule_bindings, Persist complete production Temporal schedule inventories and bindings.
INFO  [alembic.runtime.migration] Running upgrade 0048_p1_production_schedule_bindings -> 0049_p1_production_recovery_decision_evidence, Retain split stop and decision evidence in production recovery events.
INFO  [alembic.runtime.migration] Running upgrade 0049_p1_production_recovery_decision_evidence -> 0050_p1_production_dispatch_reissue, Add one-use typed no-effect proof to production dispatch generations.
INFO  [alembic.runtime.migration] Running upgrade 0050_p1_production_dispatch_reissue -> 0051_p1_recovery_rollback_v2, Add the self-discovered, read-only production recovery rollback inventory.
```

### alembic-current

Command: `env -i PATH=<minimal> HOME=/home/admin DATABASE_URL=<local disposable endpoint; credential redacted> /home/admin/Salience/.venv/bin/python -m alembic current`
Started: `2026-10-29T12:15:45.337528+00:00`; ended: `2026-10-29T12:15:46.338226+00:00`; exit: `0`; elapsed: `1.001s`.
Log SHA-256: `b4695e95e313ffb6440fb361ffee38640aec70511b35c2d6cf2c7968e2ada401` (164 bytes) at `/tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/verification/alembic-current.log`.

Tail:
```text
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
0051_p1_recovery_rollback_v2 (head)
```

### 01-routing-files-lock-builder

Command: `env -i PATH=<minimal> HOME=/home/admin DATABASE_URL=<local disposable endpoint; credential redacted> TEST_DATABASE_URL=<local disposable endpoint; credential redacted> TEST_TEMPORAL_TARGET=<local disposable endpoint> TEMPORAL_TARGET=<local disposable endpoint> /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/unit/test_production_routing.py tests/integration/test_p1_production_routing.py tests/integration/test_v4_publication_recovery_floor.py::test_launcher_v2_lock_builder_binds_exact_final_migration_head_0051_and_verifier_abi`
Started: `2026-10-29T12:15:46.338476+00:00`; ended: `2026-10-29T12:15:49.339364+00:00`; exit: `0`; elapsed: `3.001s`.
Log SHA-256: `af6b6b638a4b9f272b9607931ffc4bfe672f69cd2d963ab1030e3b7029e38b26` (99 bytes) at `/tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/focused/01-routing-files-lock-builder.log`.
Test counts: `{"deselected": 0, "errors": 0, "failed": 0, "passed": 38, "skipped": 0, "summary": "38 passed in 2.19s", "warnings": 0}`.

Tail:
```text
......................................                                   [100%]
38 passed in 2.19s
```

### 02-migrations-0047-0051-schema-safety

Command: `env -i PATH=<minimal> HOME=/home/admin DATABASE_URL=<local disposable endpoint; credential redacted> TEST_DATABASE_URL=<local disposable endpoint; credential redacted> TEST_TEMPORAL_TARGET=<local disposable endpoint> TEMPORAL_TARGET=<local disposable endpoint> /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/integration/test_v4_migrations.py tests/integration/test_p1_production_recovery.py -k 0047 or 0048 or 0049 or 0050 or 0051 or schema_and_migration_safety`
Started: `2026-10-29T12:15:49.339954+00:00`; ended: `2026-10-29T12:20:03.393845+00:00`; exit: `1`; elapsed: `254.054s`.
Log SHA-256: `8bf25bf6e15bd72835a80bef1d3eac63b134900e22562cfdb2329f6df8f3c9b4` (1071 bytes) at `/tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/focused/02-migrations-0047-0051-schema-safety.log`.
Test counts: `{"deselected": 113, "errors": 0, "failed": 1, "passed": 38, "skipped": 0, "summary": "1 failed, 38 passed, 113 deselected in 253.19s (0:04:13)", "warnings": 0}`.

Tail:
```text
F......................................                                  [100%]
=================================== FAILURES ===================================
_____ test_0051_downgrade_raises_without_schema_or_alembic_version_change ______
tests/integration/test_v4_migrations.py:1489: in test_0051_downgrade_raises_without_schema_or_alembic_version_change
    fingerprint_before = schema_fingerprint()
                         ^^^^^^^^^^^^^^^^^^^^
tests/integration/test_v4_migrations.py:1467: in schema_fingerprint
    dumped = subprocess.run(
/usr/lib/python3.13/subprocess.py:577: in run
    raise CalledProcessError(retcode, process.args,
E   subprocess.CalledProcessError: Command '['docker', 'compose', 'exec', '-T', '-e', 'PGPASSWORD=salience', 'postgres', 'pg_dump', '--schema-only', '--no-owner', '--no-privileges', '--restrict-key=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', '-h', '127.0.0.1', '-U', 'salience', 'task9_downgrade_be05e665d016']' returned non-zero exit status 1.
1 failed, 38 passed, 113 deselected in 253.19s (0:04:13)
```

### compose-cleanup

Command: `docker --context default compose --env-file /tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/empty-compose.env --project-name salience-p1-task28-q-276c0a98819e down --volumes --remove-orphans`
Started: `2026-10-29T12:20:03.394214+00:00`; ended: `2026-10-29T12:20:06.395101+00:00`; exit: `0`; elapsed: `3.001s`.
Log SHA-256: `d8b0243a3c0f3c7178ec2aac92a78370ccfa6bc3e83dcc8790ba54a08d3c22ec` (776 bytes) at `/tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/verification/compose-cleanup.log`.

Tail:
```text
 Container salience-p1-task28-q-276c0a98819e-temporal-1 Stopping
 Container salience-p1-task28-q-276c0a98819e-temporal-1 Stopped
 Container salience-p1-task28-q-276c0a98819e-temporal-1 Removing
 Container salience-p1-task28-q-276c0a98819e-temporal-1 Removed
 Container salience-p1-task28-q-276c0a98819e-postgres-1 Stopping
 Container salience-p1-task28-q-276c0a98819e-postgres-1 Stopped
 Container salience-p1-task28-q-276c0a98819e-postgres-1 Removing
 Container salience-p1-task28-q-276c0a98819e-postgres-1 Removed
 Volume salience-p1-task28-q-276c0a98819e_postgres-data Removing
 Network salience-p1-task28-q-276c0a98819e_default Removing
 Volume salience-p1-task28-q-276c0a98819e_postgres-data Removed
 Network salience-p1-task28-q-276c0a98819e_default Removed
```

### cleanup-verify-containers

Command: `docker --context default ps -aq --filter label=com.docker.compose.project=salience-p1-task28-q-276c0a98819e`
Started: `2026-10-29T12:20:06.395280+00:00`; ended: `2026-10-29T12:20:07.396143+00:00`; exit: `0`; elapsed: `1.001s`.
Log SHA-256: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` (0 bytes) at `/tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/verification/cleanup-verify-containers.log`.

### cleanup-verify-networks

Command: `docker --context default network ls -q --filter label=com.docker.compose.project=salience-p1-task28-q-276c0a98819e`
Started: `2026-10-29T12:20:07.396597+00:00`; ended: `2026-10-29T12:20:08.397528+00:00`; exit: `0`; elapsed: `1.001s`.
Log SHA-256: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` (0 bytes) at `/tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/verification/cleanup-verify-networks.log`.

### cleanup-verify-volumes

Command: `docker --context default volume ls -q --filter label=com.docker.compose.project=salience-p1-task28-q-276c0a98819e`
Started: `2026-10-29T12:20:08.397973+00:00`; ended: `2026-10-29T12:20:09.398643+00:00`; exit: `0`; elapsed: `1.001s`.
Log SHA-256: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` (0 bytes) at `/tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/verification/cleanup-verify-volumes.log`.

### final-head

Command: `git rev-parse HEAD`
Started: `2026-10-29T12:20:09.399048+00:00`; ended: `2026-10-29T12:20:09.400986+00:00`; exit: `0`; elapsed: `0.002s`.
Log SHA-256: `851b98ca2807d43f1ace32a592d72eff7ba0c92faf8c0590ada8ac8e7c2daffb` (41 bytes) at `/tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/verification/final-head.log`.

Tail:
```text
33cac5cffff973e9cba5e03be6b493065e5f817d
```

### final-parent

Command: `git show -s --format=%P HEAD`
Started: `2026-10-29T12:20:09.401311+00:00`; ended: `2026-10-29T12:20:10.403202+00:00`; exit: `0`; elapsed: `1.002s`.
Log SHA-256: `6eb414c2dbfb9dfd8b8b4d78df46a4ddfe05b141c90a60ffe3ef940bdafa3ea7` (41 bytes) at `/tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/verification/final-parent.log`.

Tail:
```text
cf070328d035f9010b760431173845c6afdfedee
```

### final-worktree

Command: `git status --porcelain=v1 --untracked-files=all`
Started: `2026-10-29T12:20:10.403344+00:00`; ended: `2026-10-29T12:20:11.406355+00:00`; exit: `0`; elapsed: `1.003s`.
Log SHA-256: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` (0 bytes) at `/tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/verification/final-worktree.log`.


## Focused groups

| Group | Exit | Test counts | Log SHA-256 |
| --- | ---: | --- | --- |
| 01-routing-files-lock-builder | 0 | 38 passed | `af6b6b638a4b9f272b9607931ffc4bfe672f69cd2d963ab1030e3b7029e38b26` |
| 02-migrations-0047-0051-schema-safety | 1 | 38 passed, 1 failed, 113 deselected | `8bf25bf6e15bd72835a80bef1d3eac63b134900e22562cfdb2329f6df8f3c9b4` |
| 03-schedule-native-readback | 0 | 71 passed in 231.45s (0:03:51) | `6596432b91a5cc2be4a37893016e3923943bfed8c289ff028cf57ece901f4c46` |
| 04-complete-production-recovery | 0 | 79 passed in 552.52s (0:09:12) | `b04e073d40c066e03f798ba1c7be9ff4c836ce1c4ecb7e99cc245c3f909a8b91` |
| 05-rollback-v2-handoff | 0 | 4 passed in 0.93s | `54d23ef0b56f99d2cbe2d50ca07723596b53070e1dcae86069f33fb176336fed` |
| 06-complete-policy-revocation | 0 | 137 passed in 76.94s (0:01:16) | `e9ebd56ce6d510e99738f35717763c6b33b905e9cdde715c9fc35a37cd1e8875` |
| 07-external-acceptance-unit | 0 | 45 passed in 191.30s (0:03:11) | `438dc97a2dca846990173867d7a2f7108b3a231f5322c7b1a22ceae5f068b7fe` |

Focused total: **412 passed, 1 failed, 113 deselected** across the seven one-time focused invocations.

## Continuation command evidence

The first focused environment omitted `COMPOSE_PROJECT_NAME`. The migration test uses `docker compose exec` without an explicit project flag, so group 02 could not resolve the isolated `postgres` service. The failing test was not repeated because the instruction prohibited duplicate invocations. After that failure, the same unique Compose project was recreated from a fresh volume, migrated again to the exact head, and groups 03–07 were run once each with `COMPOSE_PROJECT_NAME` set to the project. A temporary orchestration wrapper error occurred before any pytest in the first continuation attempt; the exact project was brought down and label filters showed no resources before the corrected continuation was started.

### continuation-preflight-head

Command: `git rev-parse HEAD`
Started: `2026-10-29T12:22:01.353271+00:00`; ended: `2026-10-29T12:22:02.354095+00:00`; exit: `0`; elapsed: `1.001s`.
Log SHA-256: `851b98ca2807d43f1ace32a592d72eff7ba0c92faf8c0590ada8ac8e7c2daffb` (41 bytes) at `/tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/continuation/continuation-preflight-head.log`.

### continuation-preflight-parent

Command: `git show -s --format=%P HEAD`
Started: `2026-10-29T12:22:02.354276+00:00`; ended: `2026-10-29T12:22:03.355024+00:00`; exit: `0`; elapsed: `1.001s`.
Log SHA-256: `6eb414c2dbfb9dfd8b8b4d78df46a4ddfe05b141c90a60ffe3ef940bdafa3ea7` (41 bytes) at `/tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/continuation/continuation-preflight-parent.log`.

### continuation-preflight-worktree

Command: `git status --porcelain=v1 --untracked-files=all`
Started: `2026-10-29T12:22:03.355183+00:00`; ended: `2026-10-29T12:22:04.356147+00:00`; exit: `0`; elapsed: `1.001s`.
Log SHA-256: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` (0 bytes) at `/tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/continuation/continuation-preflight-worktree.log`.

### continuation-compose-up-postgres-temporal

Command: `docker --context default compose --env-file /tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/empty-compose.env --project-name salience-p1-task28-q-276c0a98819e up -d --pull never --wait --wait-timeout 180 postgres temporal`
Started: `2026-10-29T12:22:04.356478+00:00`; ended: `2026-10-29T12:22:11.358158+00:00`; exit: `0`; elapsed: `7.002s`.
Log SHA-256: `19fccdbd0135dce1da60ddbb8c78d1583bbc0ae7887b88d9234a6a459d669d03` (1418 bytes) at `/tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/continuation/continuation-compose-up-postgres-temporal.log`.

### continuation-compose-ps-postgres

Command: `docker --context default compose --env-file /tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/empty-compose.env --project-name salience-p1-task28-q-276c0a98819e ps -q postgres`
Started: `2026-10-29T12:22:11.358327+00:00`; ended: `2026-10-29T12:22:12.359381+00:00`; exit: `0`; elapsed: `1.001s`.
Log SHA-256: `fcffd9aa312411b5bf3f767d5e553a7ee1964d5c51efc9b62474e3a9d5e2bc00` (65 bytes) at `/tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/continuation/continuation-compose-ps-postgres.log`.

### continuation-inspect-ip-postgres

Command: `docker --context default inspect -f {{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}} ae55e305ff911468ec101c5e3e7bd84fba6c70d2c158308429013f14fcc309a2`
Started: `2026-10-29T12:22:12.359870+00:00`; ended: `2026-10-29T12:22:13.362349+00:00`; exit: `0`; elapsed: `1.003s`.
Log SHA-256: `870abc46b1af55506101fb91ed5852682d5374ffd5d796b7b527760e62240dea` (11 bytes) at `/tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/continuation/continuation-inspect-ip-postgres.log`.

### continuation-readiness-postgres

Command: `/home/admin/Salience/.venv/bin/python -c <local TCP readiness probe> 172.20.0.2 5432`
Started: `2026-10-29T12:22:13.362582+00:00`; ended: `2026-10-29T12:22:14.363169+00:00`; exit: `0`; elapsed: `1.001s`.
Log SHA-256: `2032f1dfa29eb1fb75f12dd2faba45eb90588f9b88315a7d3c614b4c2368956e` (10 bytes) at `/tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/continuation/continuation-readiness-postgres.log`.

### continuation-compose-ps-temporal

Command: `docker --context default compose --env-file /tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/empty-compose.env --project-name salience-p1-task28-q-276c0a98819e ps -q temporal`
Started: `2026-10-29T12:22:14.363335+00:00`; ended: `2026-10-29T12:22:15.364027+00:00`; exit: `0`; elapsed: `1.001s`.
Log SHA-256: `221746e3fee71ad3bb6c73185a8d4457c7dd1645f3b4dd567b19e52d60b0d9e2` (65 bytes) at `/tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/continuation/continuation-compose-ps-temporal.log`.

### continuation-inspect-ip-temporal

Command: `docker --context default inspect -f {{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}} af5b7dd30fb0f3f8887ca917a4854f8c559f236b2b25bbdef04011fb6f7ba83b`
Started: `2026-10-29T12:22:15.364284+00:00`; ended: `2026-10-29T12:22:16.364928+00:00`; exit: `0`; elapsed: `1.001s`.
Log SHA-256: `d6d6b03795a1c1774e97c843e6542c6de18b34b3f219d45f38bb675a9f70dfeb` (11 bytes) at `/tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/continuation/continuation-inspect-ip-temporal.log`.

### continuation-readiness-temporal

Command: `/home/admin/Salience/.venv/bin/python -c <local TCP readiness probe> 172.20.0.3 7233`
Started: `2026-10-29T12:22:16.365159+00:00`; ended: `2026-10-29T12:22:17.366172+00:00`; exit: `0`; elapsed: `1.001s`.
Log SHA-256: `2032f1dfa29eb1fb75f12dd2faba45eb90588f9b88315a7d3c614b4c2368956e` (10 bytes) at `/tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/continuation/continuation-readiness-temporal.log`.

### continuation-alembic-upgrade-head

Command: `env -i PATH=<minimal> HOME=/home/admin DATABASE_URL=<local disposable endpoint; credential redacted> /home/admin/Salience/.venv/bin/python -m alembic upgrade head`
Started: `2026-10-29T12:22:17.366346+00:00`; ended: `2026-10-29T12:22:19.367678+00:00`; exit: `0`; elapsed: `2.001s`.
Log SHA-256: `5f4e10c1dccc9962e7f52f3cf4f6dc8dd61f6ce7d529531918923889aa1b4b3d` (9165 bytes) at `/tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/continuation/continuation-alembic-upgrade-head.log`.

### continuation-alembic-current

Command: `env -i PATH=<minimal> HOME=/home/admin DATABASE_URL=<local disposable endpoint; credential redacted> /home/admin/Salience/.venv/bin/python -m alembic current`
Started: `2026-10-29T12:22:19.367899+00:00`; ended: `2026-10-29T12:22:20.368559+00:00`; exit: `0`; elapsed: `1.001s`.
Log SHA-256: `b4695e95e313ffb6440fb361ffee38640aec70511b35c2d6cf2c7968e2ada401` (164 bytes) at `/tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/continuation/continuation-alembic-current.log`.

### 03-schedule-native-readback

Command: `env -i PATH=<minimal> HOME=/home/admin DATABASE_URL=<local disposable endpoint; credential redacted> TEST_DATABASE_URL=<local disposable endpoint; credential redacted> TEST_TEMPORAL_TARGET=<local disposable endpoint> TEMPORAL_TARGET=<local disposable endpoint> COMPOSE_PROJECT_NAME=salience-p1-task28-q-276c0a98819e /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/integration/test_p1_production_schedule_fencing.py tests/integration/test_v4_native_schedules.py tests/integration/test_v4_native_publication_schedules.py tests/unit/test_temporal_schedule_readback.py`
Started: `2026-10-29T12:22:20.368824+00:00`; ended: `2026-10-29T12:26:13.409519+00:00`; exit: `0`; elapsed: `233.041s`.
Log SHA-256: `6596432b91a5cc2be4a37893016e3923943bfed8c289ff028cf57ece901f4c46` (111 bytes) at `/tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/continuation/03-schedule-native-readback.log`.
Test counts: `{"deselected": 0, "errors": 0, "failed": 0, "passed": 71, "skipped": 0, "summary": "71 passed in 231.45s (0:03:51)", "warnings": 0}`.

### 04-complete-production-recovery

Command: `env -i PATH=<minimal> HOME=/home/admin DATABASE_URL=<local disposable endpoint; credential redacted> TEST_DATABASE_URL=<local disposable endpoint; credential redacted> TEST_TEMPORAL_TARGET=<local disposable endpoint> TEMPORAL_TARGET=<local disposable endpoint> COMPOSE_PROJECT_NAME=salience-p1-task28-q-276c0a98819e /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/integration/test_p1_production_recovery.py`
Started: `2026-10-29T12:26:13.410148+00:00`; ended: `2026-10-29T12:35:26.543792+00:00`; exit: `0`; elapsed: `553.134s`.
Log SHA-256: `b04e073d40c066e03f798ba1c7be9ff4c836ce1c4ecb7e99cc245c3f909a8b91` (191 bytes) at `/tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/continuation/04-complete-production-recovery.log`.
Test counts: `{"deselected": 0, "errors": 0, "failed": 0, "passed": 79, "skipped": 0, "summary": "79 passed in 552.52s (0:09:12)", "warnings": 0}`.

### 05-rollback-v2-handoff

Command: `env -i PATH=<minimal> HOME=/home/admin DATABASE_URL=<local disposable endpoint; credential redacted> TEST_DATABASE_URL=<local disposable endpoint; credential redacted> TEST_TEMPORAL_TARGET=<local disposable endpoint> TEMPORAL_TARGET=<local disposable endpoint> COMPOSE_PROJECT_NAME=salience-p1-task28-q-276c0a98819e /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/integration/test_p1_production_routing.py::test_launcher_v2_runs_pre_and_post_readback_before_prior_code tests/integration/test_p1_production_routing.py::test_rollback_v2_hands_off_authenticated_deployable_only_after_verification tests/integration/test_p1_production_routing.py::test_rollback_v2_fails_closed_without_authenticated_deployable_executor tests/integration/test_p1_production_routing.py::test_launcher_v2_rejects_caller_supplied_hold_lists`
Started: `2026-10-29T12:35:26.544061+00:00`; ended: `2026-10-29T12:35:28.544817+00:00`; exit: `0`; elapsed: `2.001s`.
Log SHA-256: `54d23ef0b56f99d2cbe2d50ca07723596b53070e1dcae86069f33fb176336fed` (98 bytes) at `/tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/continuation/05-rollback-v2-handoff.log`.
Test counts: `{"deselected": 0, "errors": 0, "failed": 0, "passed": 4, "skipped": 0, "summary": "4 passed in 0.93s", "warnings": 0}`.

### 06-complete-policy-revocation

Command: `env -i PATH=<minimal> HOME=/home/admin DATABASE_URL=<local disposable endpoint; credential redacted> TEST_DATABASE_URL=<local disposable endpoint; credential redacted> TEST_TEMPORAL_TARGET=<local disposable endpoint> TEMPORAL_TARGET=<local disposable endpoint> COMPOSE_PROJECT_NAME=salience-p1-task28-q-276c0a98819e /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/integration/test_p1_production_policy_revocation.py`
Started: `2026-10-29T12:35:28.545092+00:00`; ended: `2026-10-29T12:36:46.563227+00:00`; exit: `0`; elapsed: `78.018s`.
Log SHA-256: `e9ebd56ce6d510e99738f35717763c6b33b905e9cdde715c9fc35a37cd1e8875` (191 bytes) at `/tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/continuation/06-complete-policy-revocation.log`.
Test counts: `{"deselected": 0, "errors": 0, "failed": 0, "passed": 137, "skipped": 0, "summary": "137 passed in 76.94s (0:01:16)", "warnings": 0}`.

### 07-external-acceptance-unit

Command: `env -i PATH=<minimal> HOME=/home/admin DATABASE_URL=<local disposable endpoint; credential redacted> TEST_DATABASE_URL=<local disposable endpoint; credential redacted> TEST_TEMPORAL_TARGET=<local disposable endpoint> TEMPORAL_TARGET=<local disposable endpoint> COMPOSE_PROJECT_NAME=salience-p1-task28-q-276c0a98819e /home/admin/Salience/.venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/unit/test_p1_external_acceptance.py`
Started: `2026-10-29T12:36:46.563499+00:00`; ended: `2026-10-29T12:39:58.590864+00:00`; exit: `0`; elapsed: `192.027s`.
Log SHA-256: `438dc97a2dca846990173867d7a2f7108b3a231f5322c7b1a22ceae5f068b7fe` (111 bytes) at `/tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/continuation/07-external-acceptance-unit.log`.
Test counts: `{"deselected": 0, "errors": 0, "failed": 0, "passed": 45, "skipped": 0, "summary": "45 passed in 191.30s (0:03:11)", "warnings": 0}`.

### continuation-compose-cleanup

Command: `docker --context default compose --env-file /tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/empty-compose.env --project-name salience-p1-task28-q-276c0a98819e down --volumes --remove-orphans`
Started: `2026-10-29T12:39:58.591116+00:00`; ended: `2026-10-29T12:40:01.591962+00:00`; exit: `0`; elapsed: `3.001s`.
Log SHA-256: `d8b0243a3c0f3c7178ec2aac92a78370ccfa6bc3e83dcc8790ba54a08d3c22ec` (776 bytes) at `/tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/continuation/continuation-compose-cleanup.log`.

### continuation-cleanup-verify-containers

Command: `docker --context default ps -aq --filter label=com.docker.compose.project=salience-p1-task28-q-276c0a98819e`
Started: `2026-10-29T12:40:01.592134+00:00`; ended: `2026-10-29T12:40:02.592815+00:00`; exit: `0`; elapsed: `1.001s`.
Log SHA-256: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` (0 bytes) at `/tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/continuation/continuation-cleanup-verify-containers.log`.

### continuation-cleanup-verify-networks

Command: `docker --context default network ls -q --filter label=com.docker.compose.project=salience-p1-task28-q-276c0a98819e`
Started: `2026-10-29T12:40:02.593041+00:00`; ended: `2026-10-29T12:40:03.593953+00:00`; exit: `0`; elapsed: `1.001s`.
Log SHA-256: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` (0 bytes) at `/tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/continuation/continuation-cleanup-verify-networks.log`.

### continuation-cleanup-verify-volumes

Command: `docker --context default volume ls -q --filter label=com.docker.compose.project=salience-p1-task28-q-276c0a98819e`
Started: `2026-10-29T12:40:03.594201+00:00`; ended: `2026-10-29T12:40:04.594843+00:00`; exit: `0`; elapsed: `1.001s`.
Log SHA-256: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` (0 bytes) at `/tmp/salience-task28-exact-qualification-20261029T121506Z-276c0a98819e/continuation/continuation-cleanup-verify-volumes.log`.

## Full non-live suite

Not run: group 02 exited 1, so the all-focused-pass gate was not met. External provider/acceptance rows remain **NOT RUN**.

## Cleanup and final worktree

Initial exact-project cleanup exited 0; the continuation cleanup also exited 0. Continuation label checks report `{"containers": 0, "networks": 0, "volumes": 0}`. Final read-only label checks at 2026-10-29T12:40:43Z also returned no containers, networks, or volumes.
Final Git checks after all focused groups: HEAD `33cac5cffff973e9cba5e03be6b493065e5f817d`, direct parent `cf070328d035f9010b760431173845c6afdfedee`, clean porcelain status. Their final logs and hashes are under `verification/post-continuation-final-*.log`.

## Failure details

Group 02 failed in `test_0051_downgrade_raises_without_schema_or_alembic_version_change` while the test called `docker compose exec ... postgres pg_dump`; its subprocess returned exit 1 because the initial test environment did not set the unique `COMPOSE_PROJECT_NAME`. This is a local qualification harness configuration failure; group 02 was not rerun. Groups 03–07 passed in the corrected environment. No candidate code was edited, and the tracked worktree remains clean.
