# Task25 implementation report — DNS/default-executor timeout

## Status

Implemented and committed as `cf07032` (`Bound temporal plan reads to pinned database endpoint`). The commit changes only `src/salience/cycles/temporal_schedule_control.py` and `tests/unit/test_temporal_schedule_readback.py`. The worktree was clean after the commit.

## RED evidence

The first regression exercised synchronous public `describe()` with a controlled `socket.getaddrinfo` implementation that blocks on `fixture.invalid`. Against the pre-fix code, the command

```text
TEST_DATABASE_URL=postgresql://127.0.0.1:1/unused TEST_TEMPORAL_TARGET=127.0.0.1:1 .venv/bin/python -m pytest -p no:cacheprovider tests/unit/test_temporal_schedule_readback.py::test_public_describe_does_not_wait_for_default_executor_dns -q
```

failed at the intended assertion: the fake resolver entered the default executor during the hostname-only async connect. The test released the resolver in `finally`, so it did not strand the worker thread.

I then expanded that public-boundary case to `describe`, `pause`, and resume preflight and ran it against the pre-fix implementation. All three cases failed on the same expected resolver-entry assertion (`3 failed in 1.36s`). This demonstrated the escape through each affected public path before changing production code.

## GREEN and compatibility evidence

After the fix, the new endpoint, fail-closed, Unix-socket, TLS-option, and public-boundary cases passed. The final serial run of the complete temporal readback unit file was:

```text
TEST_DATABASE_URL=postgresql://127.0.0.1:1/unused TEST_TEMPORAL_TARGET=127.0.0.1:1 .venv/bin/python -m pytest -p no:cacheprovider tests/unit/test_temporal_schedule_readback.py -q
20 passed in 1.62s
```

`git diff --check` passed. A search of `tests/unit` found no other unit tests calling this private pin/plan chain.

`_pin()` now captures the active connection's `info.host` and `info.hostaddr` before closing that connection and returns them in a frozen private endpoint value. The public describe, pause, and resume paths pass that value explicitly to every timed plan read. Resume preflight uses its first pin's endpoint before the durable local rollback fence; the later remote resume uses the endpoint from its second pin. No endpoint is stored on mutable control-instance state.

Async psycopg connects receive the captured `host` and numeric `hostaddr`, while retaining the original conninfo, including `sslmode=verify-full` and other URL parameters. Missing or invalid numeric `hostaddr` for a TCP host fails closed before async connect with a generic error. A Unix-socket path remains supported without `hostaddr`. No DSN or credential is logged. Existing plan/preflight deadlines, connect and statement timeout values, close-before-cancel cleanup, retry behavior, and resume fencing were retained; the complete relevant unit file passes those existing regressions as well.

## Environment and scope notes

The first attempted command used `uv run pytest`; repository `tests/conftest.py` started the default `salience` Compose project before the test ran. I stopped after the expected RED result, then removed only that exact project with the documented command:

```text
docker compose --project-name salience down --volumes --remove-orphans
```

Label-filtered checks then showed zero remaining containers, networks, and volumes for `com.docker.compose.project=salience`. The `uv run` invocation also created an untracked `uv.lock`; I removed it. Every subsequent pytest run used `.venv/bin/python` with dummy endpoint overrides, which made `tests/conftest.py` skip service startup. No Docker command was used afterward, and no live or external service was contacted.

The 44-minute full suite, artifact rebuild, evidence JSON/docs updates, and downstream exact-candidate qualification were intentionally not run; they are explicitly deferred by the Task25 requirements. This change addresses async plan/preflight hostname resolution only. The initial synchronous `_pin()` connection and inherited synchronous fixture-control database calls remain outside those coroutine deadlines, as scoped in the design.

## Fix round 1 — Unix socket and multi-host port compatibility

A final rereview found two compatibility gaps in the endpoint contract: psycopg reports a Unix socket's `ConnectionInfo.hostaddr` as the empty string, and a multi-host DSN retains its comma-separated port list unless the selected active port is also overridden.

### RED evidence

I added the driver-shaped regressions before changing production code and ran them with the no-Compose unit invocation. The expected RED result was `10 failed, 3 passed in 0.91s`:

```text
TEST_DATABASE_URL=postgresql://127.0.0.1:1/unused TEST_TEMPORAL_TARGET=127.0.0.1:1 .venv/bin/python -m pytest -p no:cacheprovider \
  tests/unit/test_temporal_schedule_readback.py::test_async_plan_uses_bounded_connection_and_shared_query \
  tests/unit/test_temporal_schedule_readback.py::test_pin_captures_active_endpoint_and_normalizes_empty_unix_address \
  tests/unit/test_temporal_schedule_readback.py::test_async_plan_fails_closed_without_numeric_address_for_hostname \
  tests/unit/test_temporal_schedule_readback.py::test_async_plan_keeps_local_unix_socket_endpoint_without_hostaddr \
  tests/unit/test_temporal_schedule_readback.py::test_async_plan_rejects_invalid_pinned_port_without_logging_conninfo \
  tests/unit/test_temporal_schedule_readback.py::test_public_describe_uses_normalized_unix_pin_endpoint -q
```

The multi-host case failed inside psycopg's real `split_attempts()` with `could not match 2 port numbers to 1 hosts`. The public describe case pinned a fake successful psycopg connection whose `info.hostaddr` returned `""` and failed when the plan rejected that address. Pin capture also showed the Unix address was not normalized and the active port was not retained. Invalid port cases reached async connect rather than failing closed. The three pre-existing hostname fail-closed cases passed. A first post-change focused run exposed only a missing `recent_actions` attribute in the new public test fixture; after correcting that fixture, the same focused set passed.

### GREEN and compatibility evidence

The immutable `_DatabaseEndpoint` now includes the active scalar port. `_pin()` captures `host`, `hostaddr`, and `port` from the open synchronous connection; it normalizes an empty address to `None` only when the captured host is a Unix socket path. Async plan connects validate a numeric port in the TCP/port range, reject bools and other types with a generic endpoint error, and pass the selected port as a scalar string alongside the pinned host and optional numeric address.

The multi-host regression feeds the resulting conninfo and overrides through psycopg's actual conninfo parser. It confirms one selected host/address/port remains, `sslmode=verify-full` keeps the hostname for TLS identity, and `application_name` and `target_session_attrs` survive. A public `describe()` regression runs the actual `_pin()` and plan path with the driver-shaped Unix empty address. TCP endpoints with absent, empty, or invalid numeric addresses still fail closed. Invalid ports fail before connect and no DSN or credential appears in errors or captured logs.

The focused regression set passed `13 passed in 0.55s`. The complete serial readback unit file passed:

```text
TEST_DATABASE_URL=postgresql://127.0.0.1:1/unused TEST_TEMPORAL_TARGET=127.0.0.1:1 .venv/bin/python -m pytest -p no:cacheprovider tests/unit/test_temporal_schedule_readback.py -q
28 passed in 1.60s
```

A repository search found no other unit tests using `_DatabaseEndpoint`, `_pin()`, or `_plan_async`; the remaining references are integration tests requiring external fixture services and were not run. `git diff --check` passed. Self-review found changes limited to the production endpoint capture/validation/connect options and the readback unit tests. Existing timeout, close-before-cancel, retry, one-call pause/update, and resume-fencing regressions all pass in the complete file. No Docker, build, live endpoint, or external system was used in this fix round.
