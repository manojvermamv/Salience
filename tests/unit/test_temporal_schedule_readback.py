import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import threading
import time
from uuid import UUID

import pytest
from temporalio.client import ScheduleActionStartWorkflow
from temporalio.service import RPCError, RPCStatusCode

import salience.cycles.temporal_schedule_control as temporal_schedule_control


TASK_QUEUE = "salience-v4-local-legacy-readback"
WORKSPACE_ID = UUID("e50cb6ae-2c1a-47e1-82e9-6c3fcd72030b")
CONTENT_PROGRAM_ID = UUID("2c677a22-415d-4f08-9b4e-f935d8c58136")


class _ScheduleHandle:
    def __init__(self, results, description=None):
        self.results = list(results)
        self.rpc_timeouts = []
        self.description = description
        self.paused_at_describe = []
        self.pause_calls = []
        self.update_calls = []

    async def describe(self, *, rpc_timeout):
        self.rpc_timeouts.append(rpc_timeout)
        self.paused_at_describe.append(self.description.schedule.state.paused)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def pause(self, *, note, rpc_timeout):
        self.pause_calls.append((note, rpc_timeout))
        self.description.schedule.state.paused = True

    async def update(self, updater, *, rpc_timeout):
        self.update_calls.append(rpc_timeout)
        await asyncio.Future()


class _Client:
    def __init__(self, handle):
        self.handle = handle
        self.schedule_id = None

    def get_schedule_handle(self, schedule_id):
        self.schedule_id = schedule_id
        return self.handle


class _CancellationSensitiveConnection:
    """Model psycopg wait(): cancellation settles only after close wakes I/O."""

    def __init__(self):
        self.query_started = threading.Event()
        self.closed = threading.Event()
        self.events = []
        self.close_calls = 0

    async def execute(self, query, params=None):
        if query != temporal_schedule_control._PLAN_LOOKUP_QUERY:
            return None
        self.query_started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            self.events.append("query_cancelled")
            await asyncio.to_thread(self.closed.wait)
            self.events.append("query_released")
            raise

    async def close(self):
        if not self.closed.is_set():
            self.close_calls += 1
            self.events.append("connection_closed")
            self.closed.set()

    def release_for_test_cleanup(self):
        # Lets a RED test recover the deliberately stuck old implementation.
        self.closed.set()


def _readback_fixture(monkeypatch, outcomes):
    control = temporal_schedule_control.TemporalFixtureLegacyScheduleControl(
        "postgresql://unused", workspace_id=WORKSPACE_ID,
        temporal_target="temporal-fixture", task_queue=TASK_QUEUE,
    )
    async def no_plan(row, *, required=False):
        return None

    monkeypatch.setattr(control, "_plan_async", no_plan)
    monkeypatch.setattr(temporal_schedule_control, "schedule_metadata", lambda row: {
        "interval_seconds": 60,
        "next_run_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
    })
    action = ScheduleActionStartWorkflow(
        workflow="IntelligenceLoopWorkflow",
        args=[{
            "workspace_id": str(WORKSPACE_ID),
            "content_program_id": str(CONTENT_PROGRAM_ID),
            "niche": "Fixture",
            "dry_run": True,
        }],
        id="fixture-action",
        task_queue=TASK_QUEUE,
    )
    description = SimpleNamespace(
        schedule=SimpleNamespace(
            action=action,
            spec=SimpleNamespace(
                intervals=[SimpleNamespace(every=timedelta(seconds=60), offset=None)],
                calendars=[],
                cron_expressions=[],
            ),
            state=SimpleNamespace(paused=False, note=""),
        ),
        info=SimpleNamespace(running_actions=[]),
    )
    handle = _ScheduleHandle([*outcomes, description], description)
    client = _Client(handle)

    async def connect(target):
        assert target == "temporal-fixture"
        return client

    monkeypatch.setattr(temporal_schedule_control.Client, "connect", connect)
    row = {
        "id": UUID("38e45f69-4af2-47ab-a645-11d5e8006fd8"),
        "content_program_id": CONTENT_PROGRAM_ID,
        "native_source": None,
        "native_publication_source": None,
        "payload": {"dry_run": True},
    }
    return control, client, handle, description, row


@pytest.mark.asyncio
async def test_async_plan_uses_bounded_connection_and_shared_query(monkeypatch):
    import psycopg
    from psycopg.rows import dict_row

    control, _, _, _, row = _readback_fixture(monkeypatch, [])
    real_async_plan = temporal_schedule_control.TemporalFixtureLegacyScheduleControl._plan_async.__get__(control)
    monkeypatch.setattr(control, "_plan_async", real_async_plan)
    plan = {"goal_id": UUID("a56ed3ab-4a8a-47db-8073-5d74dd883921"), "task_queue": TASK_QUEUE}
    calls = []
    connection_state = {"closed": False}

    class Cursor:
        async def fetchone(self):
            return plan

    class Connection:
        async def execute(self, query, params=None):
            calls.append((query, params))
            return Cursor()

        async def close(self):
            connection_state["closed"] = True

    connect_calls = []

    async def connect(cls, conninfo, **kwargs):
        connect_calls.append((conninfo, kwargs))
        return Connection()

    monkeypatch.setattr(psycopg.AsyncConnection, "connect", classmethod(connect))

    result = await control._plan_async(row, required=True)

    assert result is plan
    assert connect_calls == [("postgresql://unused", {
        "row_factory": dict_row,
        "connect_timeout": temporal_schedule_control._PLAN_CONNECT_TIMEOUT_SECONDS,
    })]
    assert calls[0] == (temporal_schedule_control._PLAN_STATEMENT_TIMEOUT_COMMAND, None)
    assert calls[1] == (
        temporal_schedule_control._PLAN_LOOKUP_QUERY,
        (row["id"], WORKSPACE_ID, row["id"], WORKSPACE_ID),
    )
    assert connection_state["closed"] is True


@pytest.mark.asyncio
async def test_async_plan_closes_connection_when_cancelled(monkeypatch):
    import psycopg

    control, _, _, _, row = _readback_fixture(monkeypatch, [])
    real_async_plan = temporal_schedule_control.TemporalFixtureLegacyScheduleControl._plan_async.__get__(control)
    monkeypatch.setattr(control, "_plan_async", real_async_plan)
    connection = _CancellationSensitiveConnection()

    async def connect(cls, conninfo, **kwargs):
        return connection

    monkeypatch.setattr(psycopg.AsyncConnection, "connect", classmethod(connect))
    task = asyncio.create_task(control._plan_async(row))
    assert await asyncio.to_thread(connection.query_started.wait, 1)
    started_at = time.monotonic()
    task.cancel()
    try:
        done, _ = await asyncio.wait({task}, timeout=0.25)
        assert task in done, "cancelled plan read must close its socket and drain within the cleanup bound"
        with pytest.raises(asyncio.CancelledError):
            task.result()
        assert time.monotonic() - started_at < 0.25
        assert connection.events == ["connection_closed", "query_cancelled", "query_released"]
    finally:
        connection.release_for_test_cleanup()
        if not task.done():
            task.cancel()
            await asyncio.wait({task}, timeout=0.25)


def test_temporal_operation_deadlines_cover_bounded_read_phases():
    control_module = temporal_schedule_control
    describe_phase = (
        control_module._PLAN_TOTAL_BOUND_SECONDS
        + control_module._TEMPORAL_DESCRIBE_ATTEMPTS
        * control_module._TEMPORAL_DESCRIBE_TIMEOUT_SECONDS
        + (control_module._TEMPORAL_DESCRIBE_ATTEMPTS - 1)
        * control_module._TEMPORAL_DESCRIBE_RETRY_DELAY_SECONDS
    )
    assert control_module._PLAN_IO_BOUND_SECONDS == (
        control_module._PLAN_CONNECT_TIMEOUT_SECONDS
        + control_module._PLAN_STATEMENT_TIMEOUT_SECONDS
    )
    assert control_module._PLAN_OPERATION_TIMEOUT_SECONDS == 6
    assert control_module._PLAN_CLEANUP_TIMEOUT_SECONDS == 0.5
    assert control_module._PLAN_TOTAL_BOUND_SECONDS == 6.5
    assert control_module._PLAN_PREFLIGHT_OPERATION_TIMEOUT_SECONDS == (
        control_module._PLAN_TOTAL_BOUND_SECONDS + 0.5
    )
    assert control_module._PLAN_CONNECT_TIMEOUT_SECONDS == 3
    assert control_module._PLAN_STATEMENT_TIMEOUT_SECONDS == 3
    assert control_module._PLAN_STATEMENT_TIMEOUT_COMMAND == "SET LOCAL statement_timeout='3s'"
    assert control_module._TEMPORAL_DESCRIBE_TIMEOUT_SECONDS == 2
    assert control_module._TEMPORAL_DESCRIBE_ATTEMPTS == 2
    assert control_module._TEMPORAL_UPDATE_TIMEOUT_SECONDS == 2
    assert control_module._DESCRIBE_PLAN_PHASE_BOUND_SECONDS == describe_phase
    assert control_module._DESCRIBE_OPERATION_TIMEOUT_SECONDS >= describe_phase + 1.45
    assert control_module._DESCRIBE_OPERATION_TIMEOUT_SECONDS == 12
    assert control_module._PAUSE_OPERATION_TIMEOUT_SECONDS >= (
        2 * describe_phase + control_module._TEMPORAL_UPDATE_TIMEOUT_SECONDS + 3.9
    )
    assert control_module._PAUSE_OPERATION_TIMEOUT_SECONDS == 27
    resume_phase = (
        3 * control_module._PLAN_TOTAL_BOUND_SECONDS
        + control_module._TEMPORAL_DESCRIBE_TIMEOUT_SECONDS
        + control_module._TEMPORAL_UPDATE_TIMEOUT_SECONDS
        + control_module._TEMPORAL_DESCRIBE_ATTEMPTS
        * control_module._TEMPORAL_DESCRIBE_TIMEOUT_SECONDS
        + (control_module._TEMPORAL_DESCRIBE_ATTEMPTS - 1)
        * control_module._TEMPORAL_DESCRIBE_RETRY_DELAY_SECONDS
    )
    assert resume_phase == 27.55  # Includes the SDK's initial update description.
    assert control_module._RESUME_OPERATION_TIMEOUT_SECONDS >= resume_phase + 7.45
    assert control_module._RESUME_OPERATION_TIMEOUT_SECONDS == 35


@pytest.mark.asyncio
async def test_describe_retries_deadline_exceeded_and_returns_verified_state(monkeypatch):
    transient = RPCError("describe deadline", RPCStatusCode.DEADLINE_EXCEEDED, b"")
    control, client, handle, description, row = _readback_fixture(monkeypatch, [transient])

    actual = await control._describe(row, "fixture-schedule")

    assert actual is description
    assert client.schedule_id == "fixture-schedule"
    assert handle.rpc_timeouts == [timedelta(seconds=2), timedelta(seconds=2)]


@pytest.mark.asyncio
async def test_describe_does_not_retry_non_deadline_rpc_errors(monkeypatch):
    error = RPCError("invalid describe request", RPCStatusCode.INVALID_ARGUMENT, b"")
    control, _, handle, _, row = _readback_fixture(monkeypatch, [error])

    with pytest.raises(RPCError, match="invalid describe request"):
        await control._describe(row, "fixture-schedule")

    assert handle.rpc_timeouts == [timedelta(seconds=2)]


@pytest.mark.asyncio
async def test_verified_plan_lookup_does_not_block_its_async_deadline(monkeypatch):
    control, client, handle, _, row = _readback_fixture(monkeypatch, [])
    plan_started = asyncio.Event()
    plan_cancelled = asyncio.Event()

    async def slow_plan(_row, *, required=False):
        plan_started.set()
        try:
            await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            plan_cancelled.set()
            raise
        return None

    monkeypatch.setattr(control, "_plan_async", slow_plan, raising=False)
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.03):
            await control._verified(client, handle, row)

    assert plan_started.is_set()
    assert plan_cancelled.is_set()


@pytest.mark.asyncio
async def test_resume_plan_lookup_does_not_exceed_async_deadline(monkeypatch):
    control, _, handle, _, row = _readback_fixture(monkeypatch, [])
    plan_started = asyncio.Event()
    plan_cancelled = asyncio.Event()

    async def slow_plan(_row, *, required=False):
        plan_started.set()
        try:
            await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            plan_cancelled.set()
            raise
        return {"goal_id": "fixture-goal"}

    monkeypatch.setattr(control, "_plan_async", slow_plan, raising=False)
    loop = asyncio.get_running_loop()
    started_at = loop.time()
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.03):
            await control._resume(row, "fixture-schedule", datetime(2025, 1, 1, tzinfo=timezone.utc))
    elapsed = loop.time() - started_at

    assert elapsed < 0.3
    assert plan_started.is_set()
    assert plan_cancelled.is_set()
    assert handle.update_calls == []


def test_public_describe_deadline_closes_driver_connection_before_draining(monkeypatch):
    import psycopg

    control, _, _, _, row = _readback_fixture(monkeypatch, [])
    connection = _CancellationSensitiveConnection()

    async def connect(cls, conninfo, **kwargs):
        return connection

    monkeypatch.setattr(psycopg.AsyncConnection, "connect", classmethod(connect))
    real_async_plan = temporal_schedule_control.TemporalFixtureLegacyScheduleControl._plan_async.__get__(control)
    monkeypatch.setattr(control, "_plan_async", real_async_plan)
    monkeypatch.setattr(control, "_pin", lambda schedule_id: (row, "fixture-schedule"))
    monkeypatch.setattr(temporal_schedule_control, "_DESCRIBE_OPERATION_TIMEOUT_SECONDS", 0.03)
    finished = threading.Event()
    outcome = []

    def call_public_describe():
        try:
            control.describe(row["id"])
        except BaseException as error:
            outcome.append(error)
        finally:
            finished.set()

    caller = threading.Thread(target=call_public_describe, daemon=True)
    started_at = time.monotonic()
    caller.start()
    try:
        assert connection.query_started.wait(1)
        returned_before_rescue = finished.wait(0.25)
        elapsed = time.monotonic() - started_at
        close_calls_before_rescue = connection.close_calls
        events_before_rescue = list(connection.events)
    finally:
        connection.release_for_test_cleanup()
        caller.join(timeout=1)

    assert returned_before_rescue, "asyncio.run() must not wait for psycopg's cancelled socket read"
    assert elapsed < 0.25
    assert close_calls_before_rescue == 1
    assert events_before_rescue == ["connection_closed", "query_cancelled", "query_released"]
    assert len(outcome) == 1 and isinstance(outcome[0], TimeoutError)
    assert not caller.is_alive()


@pytest.mark.asyncio
async def test_internal_plan_deadline_closes_and_drains_driver_task(monkeypatch):
    import psycopg

    control, _, _, _, row = _readback_fixture(monkeypatch, [])
    real_async_plan = temporal_schedule_control.TemporalFixtureLegacyScheduleControl._plan_async.__get__(control)
    monkeypatch.setattr(control, "_plan_async", real_async_plan)
    monkeypatch.setattr(
        temporal_schedule_control, "_PLAN_OPERATION_TIMEOUT_SECONDS", 0.03, raising=False
    )
    connection = _CancellationSensitiveConnection()

    async def connect(cls, conninfo, **kwargs):
        return connection

    monkeypatch.setattr(psycopg.AsyncConnection, "connect", classmethod(connect))
    started_at = time.monotonic()
    task = asyncio.create_task(control._plan_async(row))
    try:
        assert await asyncio.to_thread(connection.query_started.wait, 1)
        done, _ = await asyncio.wait({task}, timeout=0.25)
        assert task in done, "the internal plan deadline must close and drain the owned database task"
        assert isinstance(task.exception(), TimeoutError)
        assert time.monotonic() - started_at < 0.25
        assert connection.events == ["connection_closed", "query_cancelled", "query_released"]
        assert not [
            task for task in asyncio.all_tasks()
            if task is not asyncio.current_task()
            and task.get_name().startswith("temporal-plan-read-")
            and not task.done()
        ]
    finally:
        connection.release_for_test_cleanup()
        if not task.done():
            task.cancel()
            await asyncio.wait({task}, timeout=0.25)


@pytest.mark.asyncio
async def test_internal_plan_deadline_cancels_connect_stage(monkeypatch):
    import psycopg

    control, _, _, _, row = _readback_fixture(monkeypatch, [])
    real_async_plan = temporal_schedule_control.TemporalFixtureLegacyScheduleControl._plan_async.__get__(control)
    monkeypatch.setattr(control, "_plan_async", real_async_plan)
    monkeypatch.setattr(
        temporal_schedule_control, "_PLAN_OPERATION_TIMEOUT_SECONDS", 0.03
    )
    connect_started = asyncio.Event()
    connect_cancelled = asyncio.Event()

    async def connect(cls, conninfo, **kwargs):
        assert kwargs["connect_timeout"] == 3
        connect_started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            connect_cancelled.set()
            raise

    monkeypatch.setattr(psycopg.AsyncConnection, "connect", classmethod(connect))
    task = asyncio.create_task(control._plan_async(row))
    done, _ = await asyncio.wait({task}, timeout=0.25)

    assert task in done
    assert isinstance(task.exception(), TimeoutError)
    assert connect_started.is_set()
    assert connect_cancelled.is_set()
    assert not [
        pending for pending in asyncio.all_tasks()
        if pending is not asyncio.current_task()
        and pending.get_name().startswith("temporal-plan-read-")
        and not pending.done()
    ]


def test_public_resume_preflight_timeout_prevents_local_rollback_mutation(monkeypatch):
    control, _, _, _, row = _readback_fixture(monkeypatch, [])
    plan_async_started = threading.Event()
    plan_async_cancelled = threading.Event()
    sync_plan_started = threading.Event()
    local_resume_calls = []

    def slow_sync_plan(_row, *, required=False):
        sync_plan_started.set()
        time.sleep(0.2)
        return {"goal_id": "fixture-goal"}

    async def slow_async_plan(_row, *, required=False):
        assert required is True
        plan_async_started.set()
        try:
            await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            plan_async_cancelled.set()
            raise
        return {"goal_id": "fixture-goal"}

    def local_resume(self, schedule_id, after_slot, *, subject_id=None):
        local_resume_calls.append((schedule_id, after_slot, subject_id))

    async def no_remote_resume(_row, _remote_id, _after_slot):
        return None

    monkeypatch.setattr(control, "_pin", lambda schedule_id: (row, "fixture-schedule"))
    monkeypatch.setattr(control, "_plan", slow_sync_plan, raising=False)
    monkeypatch.setattr(control, "_plan_async", slow_async_plan)
    monkeypatch.setattr(control, "_resume", no_remote_resume)
    monkeypatch.setattr(
        temporal_schedule_control.FixtureLegacyScheduleControl,
        "resume_after",
        local_resume,
    )
    monkeypatch.setattr(
        temporal_schedule_control,
        "_PLAN_PREFLIGHT_OPERATION_TIMEOUT_SECONDS",
        0.03,
        raising=False,
    )

    started_at = time.monotonic()
    with pytest.raises(TimeoutError):
        control.resume_after(row["id"], datetime(2025, 1, 1, tzinfo=timezone.utc))
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.15
    assert local_resume_calls == []
    assert plan_async_started.is_set()
    assert plan_async_cancelled.is_set()
    assert not sync_plan_started.is_set()


@pytest.mark.asyncio
async def test_pause_retries_transient_post_pause_readback_once(monkeypatch):
    control, _, handle, description, row = _readback_fixture(monkeypatch, [])
    transient = RPCError("post-pause describe deadline", RPCStatusCode.DEADLINE_EXCEEDED, b"")
    handle.results = [description, transient, description]

    await control._pause(row, "fixture-schedule")

    assert handle.paused_at_describe == [False, True, True]
    assert handle.pause_calls == [
        ("V4 no-effects fixture cutover", timedelta(seconds=2))
    ]
    assert handle.rpc_timeouts == [
        timedelta(seconds=2), timedelta(seconds=2), timedelta(seconds=2)
    ]
    assert description.schedule.state.paused is True


@pytest.mark.asyncio
async def test_pause_propagates_persistent_post_pause_deadlines_without_repeating_pause(monkeypatch):
    control, _, handle, description, row = _readback_fixture(monkeypatch, [])
    first_deadline = RPCError("first post-pause deadline", RPCStatusCode.DEADLINE_EXCEEDED, b"")
    second_deadline = RPCError("second post-pause deadline", RPCStatusCode.DEADLINE_EXCEEDED, b"")
    handle.results = [description, first_deadline, second_deadline]

    with pytest.raises(RPCError, match="second post-pause deadline"):
        await control._pause(row, "fixture-schedule")

    assert handle.paused_at_describe == [False, True, True]
    assert handle.pause_calls == [
        ("V4 no-effects fixture cutover", timedelta(seconds=2))
    ]
    assert handle.rpc_timeouts == [
        timedelta(seconds=2), timedelta(seconds=2), timedelta(seconds=2)
    ]
