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
