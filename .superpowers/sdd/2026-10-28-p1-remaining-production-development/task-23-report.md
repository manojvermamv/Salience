# Task 23 exact-candidate local qualification

Status: **DONE**

Supplied report SHA-256: `656e73d345eaa15c4a93abd9080878c8dd1829d1be76149e6014bc85df395019` (repository copy trims trailing whitespace only).

Artifact HEAD: `c1c78dd25e467d0ae48e9c949de6aeb555875ea8`
Supplied source reference: `db3613482ac78cd708f6aa62e64854626ba7a906`
Compose project: `salience-p1-task23-q-a1dc00a2cc82`
Evidence directory: `/tmp/salience-p1-task23-qualification-a1dc00a2cc82`
Updated at: `2026-10-29T10:02:38.751181+00:00`

The worktree started clean and remained clean at each recorded check. Only the local `postgres` and `temporal` Compose services were started. No provider, production, live schedule, alert, or external acceptance service was contacted. Subprocesses received only the recorded minimal environment keys; no provider or production credentials were passed. PostgreSQL/Temporal used the disposable Compose stack credentials locally.

## Stack and migration checks

### compose-up-postgres-temporal

Command: `docker compose --env-file /tmp/salience-p1-task23-qualification-a1dc00a2cc82/empty-compose.env --project-name salience-p1-task23-q-a1dc00a2cc82 up -d --pull never --wait --wait-timeout 180 postgres temporal`
Started: `2026-10-29T08:55:41.352712+00:00`; ended: `2026-10-29T08:55:48.214469+00:00`; exit: `0`; elapsed: `6.862s`.
Log SHA256: `73d877d8d946f08e213cafb9231dc77c74807b7a76aabfb71e7788c94081860d` (1418 bytes) at `/tmp/salience-p1-task23-qualification-a1dc00a2cc82/verification/compose-up.log`.
Tail:
```text
 Container salience-p1-task23-q-a1dc00a2cc82-temporal-1 Creating
 Container salience-p1-task23-q-a1dc00a2cc82-temporal-1 Created
 Container salience-p1-task23-q-a1dc00a2cc82-postgres-1 Starting
 Container salience-p1-task23-q-a1dc00a2cc82-postgres-1 Started
 Container salience-p1-task23-q-a1dc00a2cc82-postgres-1 Waiting
 Container salience-p1-task23-q-a1dc00a2cc82-postgres-1 Healthy
 Container salience-p1-task23-q-a1dc00a2cc82-temporal-1 Starting
 Container salience-p1-task23-q-a1dc00a2cc82-temporal-1 Started
 Container salience-p1-task23-q-a1dc00a2cc82-postgres-1 Waiting
 Container salience-p1-task23-q-a1dc00a2cc82-temporal-1 Waiting
 Container salience-p1-task23-q-a1dc00a2cc82-temporal-1 Healthy
 Container salience-p1-task23-q-a1dc00a2cc82-postgres-1 Healthy
```

### compose-ps-postgres

Command: `docker compose --env-file /tmp/salience-p1-task23-qualification-a1dc00a2cc82/empty-compose.env --project-name salience-p1-task23-q-a1dc00a2cc82 ps -q postgres`
Started: `2026-10-29T08:55:48.214841+00:00`; ended: `2026-10-29T08:55:48.362452+00:00`; exit: `0`; elapsed: `0.148s`.
Log SHA256: `6735a319a17d28934e90cb37dc7c8bffcd40f2c4068c260b9348ceffec2ed62d` (65 bytes) at `/tmp/salience-p1-task23-qualification-a1dc00a2cc82/verification/compose-ps-postgres.log`.
Tail:
```text
924be46f2b09eeefe4e869633dfdd3cbff5a057c488a2ba393beda41ee116554
```

### inspect-ip-postgres

Command: `docker inspect -f '{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}' 924be46f2b09eeefe4e869633dfdd3cbff5a057c488a2ba393beda41ee116554`
Started: `2026-10-29T08:55:48.362671+00:00`; ended: `2026-10-29T08:55:48.394698+00:00`; exit: `0`; elapsed: `0.032s`.
Log SHA256: `870abc46b1af55506101fb91ed5852682d5374ffd5d796b7b527760e62240dea` (11 bytes) at `/tmp/salience-p1-task23-qualification-a1dc00a2cc82/verification/inspect-ip-postgres.log`.
Tail:
```text
172.20.0.2
```

### compose-ps-temporal

Command: `docker compose --env-file /tmp/salience-p1-task23-qualification-a1dc00a2cc82/empty-compose.env --project-name salience-p1-task23-q-a1dc00a2cc82 ps -q temporal`
Started: `2026-10-29T08:55:48.395406+00:00`; ended: `2026-10-29T08:55:48.520012+00:00`; exit: `0`; elapsed: `0.125s`.
Log SHA256: `7ad2a1e176ce67aee462439b9deb600110f5e318943cab184c81e8b47c5d23a4` (65 bytes) at `/tmp/salience-p1-task23-qualification-a1dc00a2cc82/verification/compose-ps-temporal.log`.
Tail:
```text
cdaddcbc7ac1b8958b0ec62fe20a887147d884e1ad4de13ba069cb3fe9b6e1bf
```

### inspect-ip-temporal

Command: `docker inspect -f '{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}' cdaddcbc7ac1b8958b0ec62fe20a887147d884e1ad4de13ba069cb3fe9b6e1bf`
Started: `2026-10-29T08:55:48.520223+00:00`; ended: `2026-10-29T08:55:48.563387+00:00`; exit: `0`; elapsed: `0.043s`.
Log SHA256: `d6d6b03795a1c1774e97c843e6542c6de18b34b3f219d45f38bb675a9f70dfeb` (11 bytes) at `/tmp/salience-p1-task23-qualification-a1dc00a2cc82/verification/inspect-ip-temporal.log`.
Tail:
```text
172.20.0.3
```

### local-endpoint-readiness

Command: `TCP connect to local Temporal container 172.x.x.x:7233`
Started: `2026-10-29T08:55:48.563551+00:00`; ended: `2026-10-29T08:55:49.565901+00:00`; exit: `0`; elapsed: `1.002s`.
Log SHA256: `6778f366b7129b8c83ab1a2ffbf28a6a8dba9d3eb8a7ba7d2bb26f1f8bfc75d8` (305 bytes) at `/tmp/salience-p1-task23-qualification-a1dc00a2cc82/local-endpoints.json`.
Tail:
```text
{"database_url": "<local disposable endpoint; credential redacted>", "migration_database_url": "<local disposable endpoint; credential redacted>", "postgres_ip": "172.20.0.2", "project": "salience-p1-task23-q-a1dc00a2cc82", "temporal_ip": "172.20.0.3", "temporal_target": "172.20.0.3:7233"}
```

### alembic-upgrade-head

Command: `.venv/bin/python -m alembic upgrade head`
Started: `2026-10-29T08:55:49.566003+00:00`; ended: `2026-10-29T08:55:51.304890+00:00`; exit: `0`; elapsed: `1.739s`.
Log SHA256: `5f4e10c1dccc9962e7f52f3cf4f6dc8dd61f6ce7d529531918923889aa1b4b3d` (9165 bytes) at `/tmp/salience-p1-task23-qualification-a1dc00a2cc82/verification/alembic-upgrade.log`.
Tail:
```text
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

Command: `.venv/bin/python -m alembic current`
Started: `2026-10-29T08:55:51.305721+00:00`; ended: `2026-10-29T08:55:52.167106+00:00`; exit: `0`; elapsed: `0.861s`.
Log SHA256: `b4695e95e313ffb6440fb361ffee38640aec70511b35c2d6cf2c7968e2ada401` (164 bytes) at `/tmp/salience-p1-task23-qualification-a1dc00a2cc82/verification/alembic-current.log`.
Tail:
```text
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
0051_p1_recovery_rollback_v2 (head)
```

## Focused groups

### 01-routing-files-lock-builder

Command: `.venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/unit/test_production_routing.py tests/integration/test_p1_production_routing.py tests/integration/test_v4_publication_recovery_floor.py::test_launcher_v2_lock_builder_binds_exact_final_migration_head_0051_and_verifier_abi`
Started: `2026-10-29T08:55:52.326027+00:00`; ended: `2026-10-29T08:55:54.877014+00:00`; exit: `0`; elapsed: `2.551s`.
Test counts: `{"summary": "38 passed in 2.05s", "passed": 38, "failed": 0, "error": 0, "errors": 0, "skipped": 0, "deselected": 0, "xfailed": 0, "xpassed": 0}`.
Log SHA256: `1e2f03e2c7f7898678530c1434fbacb0dd85bd230bc1f8e269e1076b4848906c` (99 bytes) at `/tmp/salience-p1-task23-qualification-a1dc00a2cc82/verification/01-routing-files-lock-builder.log`.
Tail:
```text
......................................                                   [100%]
38 passed in 2.05s
```

### 02-migrations-0047-0051-schema-safety

Command: `.venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/integration/test_v4_migrations.py tests/integration/test_p1_production_recovery.py -k '0047 or 0048 or 0049 or 0050 or 0051 or schema_and_migration_safety'`
Started: `2026-10-29T08:55:54.895914+00:00`; ended: `2026-10-29T09:00:13.025496+00:00`; exit: `0`; elapsed: `258.13s`.
Test counts: `{"summary": "39 passed, 113 deselected in 257.76s (0:04:17)", "passed": 39, "failed": 0, "error": 0, "errors": 0, "skipped": 0, "deselected": 113, "xfailed": 0, "xpassed": 0}`.
Log SHA256: `17d975604e32040b266ca345452e5587e0475f91478573be629d3f606d3d38b7` (127 bytes) at `/tmp/salience-p1-task23-qualification-a1dc00a2cc82/verification/02-migrations-0047-0051-schema-safety.log`.
Tail:
```text
.......................................                                  [100%]
39 passed, 113 deselected in 257.76s (0:04:17)
```

### 03-schedule-native-readback

Command: `.venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/integration/test_p1_production_schedule_fencing.py tests/integration/test_v4_native_schedules.py tests/integration/test_v4_native_publication_schedules.py tests/unit/test_temporal_schedule_readback.py`
Started: `2026-10-29T09:00:13.044981+00:00`; ended: `2026-10-29T09:04:12.734013+00:00`; exit: `0`; elapsed: `239.689s`.
Test counts: `{"summary": "64 passed in 238.63s (0:03:58)", "passed": 64, "failed": 0, "error": 0, "errors": 0, "skipped": 0, "deselected": 0, "xfailed": 0, "xpassed": 0}`.
Log SHA256: `d624d348256670eee3945e6cae22e470ef93c645f5676657312ac2a64c500044` (111 bytes) at `/tmp/salience-p1-task23-qualification-a1dc00a2cc82/verification/03-schedule-native-readback.log`.
Tail:
```text
................................................................         [100%]
64 passed in 238.63s (0:03:58)
```

### 04-complete-production-recovery

Command: `.venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/integration/test_p1_production_recovery.py`
Started: `2026-10-29T09:04:12.752024+00:00`; ended: `2026-10-29T09:13:33.492823+00:00`; exit: `0`; elapsed: `560.741s`.
Test counts: `{"summary": "79 passed in 560.21s (0:09:20)", "passed": 79, "failed": 0, "error": 0, "errors": 0, "skipped": 0, "deselected": 0, "xfailed": 0, "xpassed": 0}`.
Log SHA256: `63c0a23d0807d1eba71750a13b9c95998ad9a659279eb3b2d73b741e1877584a` (191 bytes) at `/tmp/salience-p1-task23-qualification-a1dc00a2cc82/verification/04-complete-production-recovery.log`.
Tail:
```text
........................................................................ [ 91%]
.......                                                                  [100%]
79 passed in 560.21s (0:09:20)
```

### 05-rollback-v2-handoff

Command: `.venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/integration/test_p1_production_routing.py::test_launcher_v2_runs_pre_and_post_readback_before_prior_code tests/integration/test_p1_production_routing.py::test_rollback_v2_hands_off_authenticated_deployable_only_after_verification tests/integration/test_p1_production_routing.py::test_rollback_v2_fails_closed_without_authenticated_deployable_executor tests/integration/test_p1_production_routing.py::test_launcher_v2_rejects_caller_supplied_hold_lists`
Started: `2026-10-29T09:13:33.512456+00:00`; ended: `2026-10-29T09:13:34.974108+00:00`; exit: `0`; elapsed: `1.462s`.
Test counts: `{"summary": "4 passed in 0.93s", "passed": 4, "failed": 0, "error": 0, "errors": 0, "skipped": 0, "deselected": 0, "xfailed": 0, "xpassed": 0}`.
Log SHA256: `54d23ef0b56f99d2cbe2d50ca07723596b53070e1dcae86069f33fb176336fed` (98 bytes) at `/tmp/salience-p1-task23-qualification-a1dc00a2cc82/verification/05-rollback-v2-handoff.log`.
Tail:
```text
....                                                                     [100%]
4 passed in 0.93s
```

### 06-complete-policy-revocation

Command: `.venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/integration/test_p1_production_policy_revocation.py`
Started: `2026-10-29T09:13:34.993573+00:00`; ended: `2026-10-29T09:14:53.082374+00:00`; exit: `0`; elapsed: `78.089s`.
Test counts: `{"summary": "137 passed in 77.62s (0:01:17)", "passed": 137, "failed": 0, "error": 0, "errors": 0, "skipped": 0, "deselected": 0, "xfailed": 0, "xpassed": 0}`.
Log SHA256: `474620bd421e188a028928a72ee6623c9410d1e2476bd6be3e9759699383a5e5` (191 bytes) at `/tmp/salience-p1-task23-qualification-a1dc00a2cc82/verification/06-complete-policy-revocation.log`.
Tail:
```text
........................................................................ [ 52%]
.................................................................        [100%]
137 passed in 77.62s (0:01:17)
```

### 07-external-acceptance-unit

Command: `.venv/bin/python -m pytest -p no:cacheprovider -m 'not live' -q -rs --tb=short tests/unit/test_p1_external_acceptance.py`
Started: `2026-10-29T09:14:53.102049+00:00`; ended: `2026-10-29T09:18:03.633653+00:00`; exit: `0`; elapsed: `190.532s`.
Test counts: `{"summary": "45 passed in 190.20s (0:03:10)", "passed": 45, "failed": 0, "error": 0, "errors": 0, "skipped": 0, "deselected": 0, "xfailed": 0, "xpassed": 0}`.
Log SHA256: `a5a14ddee7e0936ab0472d927680395144b73883794f8faac009c9b88fc78f5b` (111 bytes) at `/tmp/salience-p1-task23-qualification-a1dc00a2cc82/verification/07-external-acceptance-unit.log`.
Tail:
```text
.............................................                            [100%]
45 passed in 190.20s (0:03:10)
```

## Full non-live suite

Command: `.venv/bin/python -m pytest -p no:cacheprovider tests/contracts tests/unit tests/integration tests/e2e tests/evals -m 'not live' -q -rs --tb=short`
Started: `2026-10-29T09:18:03.653134+00:00`; ended: `2026-10-29T10:02:35.789527+00:00`; exit: `0`; elapsed: `2672.136s`.
Test counts: `{"summary": "1196 passed, 26 skipped, 3 warnings in 2668.51s (0:44:28)", "passed": 1196, "failed": 0, "error": 0, "errors": 0, "skipped": 26, "deselected": 0, "xfailed": 0, "xpassed": 0}`.
Log SHA256: `228705c455ab4dad8f0216bc67bef1ac31563dcae9871a3d1252a5d27b928111` (3394 bytes) at `/tmp/salience-p1-task23-qualification-a1dc00a2cc82/verification/full-non-live-suite.log`.
Tail:
```text
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
=========================== short test summary info ============================
SKIPPED [1] tests/integration/test_v4_legacy_dispatch.py:655: Task 8 exact candidate state is wholly absent
SKIPPED [1] tests/integration/test_v4_publication_recovery_floor.py:822: Task 8 exact candidate state is wholly absent
SKIPPED [1] tests/integration/test_v4_publication_recovery_floor.py:3051: Task 8 exact candidate state is wholly absent
SKIPPED [1] tests/integration/test_v4_publication_recovery_floor.py:3339: Task 8 exact candidate state is wholly absent
SKIPPED [3] tests/integration/test_v4_publication_recovery_floor.py:3367: Task 8 exact candidate state is wholly absent
SKIPPED [1] tests/integration/test_v4_publication_recovery_floor.py:3428: Task 8 exact candidate state is wholly absent
SKIPPED [16] tests/integration/test_v4_publication_recovery_floor.py:4231: Task 8 candidate fixture metadata is absent: candidate state is wholly absent
SKIPPED [1] tests/integration/test_v4_publication_recovery_floor.py:5916: Task 8 exact candidate state is wholly absent
SKIPPED [1] tests/e2e/test_compose_worker_restart.py:8: Task 8 exact candidate state is wholly absent
1196 passed, 26 skipped, 3 warnings in 2668.51s (0:44:28)
```

## Cleanup

Command: `docker compose --env-file /tmp/salience-p1-task23-qualification-a1dc00a2cc82/empty-compose.env --project-name salience-p1-task23-q-a1dc00a2cc82 down --volumes --remove-orphans`
Started: `2026-10-29T10:02:35.804772+00:00`; ended: `2026-10-29T10:02:38.685716+00:00`; exit: `0`.
Remaining project-labeled resources: `{"containers": 0, "networks": 0, "volumes": 0}`.
Log SHA256: `54801e153aa3141f9684c5491140e4b15c68569091eacf3847e787277dd9c2b6` at `/tmp/salience-p1-task23-qualification-a1dc00a2cc82/verification/compose-cleanup.log`.
Tail:
```text
 Container salience-p1-task23-q-a1dc00a2cc82-temporal-1 Stopping
 Container salience-p1-task23-q-a1dc00a2cc82-temporal-1 Stopped
 Container salience-p1-task23-q-a1dc00a2cc82-temporal-1 Removing
 Container salience-p1-task23-q-a1dc00a2cc82-temporal-1 Removed
 Container salience-p1-task23-q-a1dc00a2cc82-postgres-1 Stopping
 Container salience-p1-task23-q-a1dc00a2cc82-postgres-1 Stopped
 Container salience-p1-task23-q-a1dc00a2cc82-postgres-1 Removing
 Container salience-p1-task23-q-a1dc00a2cc82-postgres-1 Removed
 Volume salience-p1-task23-q-a1dc00a2cc82_postgres-data Removing
 Network salience-p1-task23-q-a1dc00a2cc82_default Removing
 Volume salience-p1-task23-q-a1dc00a2cc82_postgres-data Removed
 Network salience-p1-task23-q-a1dc00a2cc82_default Removed
```

## Failure details

None recorded.
