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


def _readback_fixture(monkeypatch, outcomes):
    control = temporal_schedule_control.TemporalFixtureLegacyScheduleControl(
        "postgresql://unused", workspace_id=WORKSPACE_ID,
        temporal_target="temporal-fixture", task_queue=TASK_QUEUE,
    )
    monkeypatch.setattr(control, "_plan", lambda row, required=False: None)

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
    query_started = asyncio.Event()
    connection_closed = asyncio.Event()

    class Connection:
        async def execute(self, query, params=None):
            if query == temporal_schedule_control._PLAN_LOOKUP_QUERY:
                query_started.set()
                await asyncio.Future()

        async def close(self):
            connection_closed.set()

    async def connect(cls, conninfo, **kwargs):
        return Connection()

    monkeypatch.setattr(psycopg.AsyncConnection, "connect", classmethod(connect))
    task = asyncio.create_task(control._plan_async(row))
    await query_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert connection_closed.is_set()


def test_temporal_operation_deadlines_cover_bounded_read_phases():
    control_module = temporal_schedule_control
    describe_phase = (
        control_module._PLAN_IO_BOUND_SECONDS
        + control_module._TEMPORAL_DESCRIBE_ATTEMPTS
        * control_module._TEMPORAL_DESCRIBE_TIMEOUT_SECONDS
        + (control_module._TEMPORAL_DESCRIBE_ATTEMPTS - 1)
        * control_module._TEMPORAL_DESCRIBE_RETRY_DELAY_SECONDS
    )
    assert control_module._PLAN_IO_BOUND_SECONDS == (
        control_module._PLAN_CONNECT_TIMEOUT_SECONDS
        + control_module._PLAN_STATEMENT_TIMEOUT_SECONDS
    )
    assert control_module._PLAN_CONNECT_TIMEOUT_SECONDS == 3
    assert control_module._PLAN_STATEMENT_TIMEOUT_SECONDS == 3
    assert control_module._PLAN_STATEMENT_TIMEOUT_COMMAND == "SET LOCAL statement_timeout='3s'"
    assert control_module._TEMPORAL_DESCRIBE_TIMEOUT_SECONDS == 2
    assert control_module._TEMPORAL_DESCRIBE_ATTEMPTS == 2
    assert control_module._TEMPORAL_UPDATE_TIMEOUT_SECONDS == 2
    assert control_module._DESCRIBE_OPERATION_TIMEOUT_SECONDS >= describe_phase + 0.95
    assert control_module._DESCRIBE_OPERATION_TIMEOUT_SECONDS == 11
    assert control_module._PAUSE_OPERATION_TIMEOUT_SECONDS >= (
        2 * describe_phase + control_module._TEMPORAL_UPDATE_TIMEOUT_SECONDS + 2.9
    )
    assert control_module._PAUSE_OPERATION_TIMEOUT_SECONDS == 25
    resume_phase = (
        3 * control_module._PLAN_IO_BOUND_SECONDS
        + control_module._TEMPORAL_UPDATE_TIMEOUT_SECONDS
        + control_module._TEMPORAL_DESCRIBE_ATTEMPTS
        * control_module._TEMPORAL_DESCRIBE_TIMEOUT_SECONDS
        + (control_module._TEMPORAL_DESCRIBE_ATTEMPTS - 1)
        * control_module._TEMPORAL_DESCRIBE_RETRY_DELAY_SECONDS
    )
    assert control_module._RESUME_OPERATION_TIMEOUT_SECONDS >= resume_phase + 2.95
    assert control_module._RESUME_OPERATION_TIMEOUT_SECONDS == 27


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


def test_public_describe_deadline_does_not_wait_for_plan_worker(monkeypatch):
    control, _, _, _, row = _readback_fixture(monkeypatch, [])
    plan_started = asyncio.Event()
    plan_cancelled = asyncio.Event()
    sync_plan_called = threading.Event()

    def slow_sync_plan(_row, *, required=False):
        sync_plan_called.set()
        time.sleep(0.2)
        return None

    async def slow_async_plan(_row, *, required=False):
        plan_started.set()
        try:
            await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            plan_cancelled.set()
            raise
        return None

    original_describe = control._describe

    async def scaled_describe(*args):
        async with asyncio.timeout(0.03):
            return await original_describe(*args)

    monkeypatch.setattr(control, "_pin", lambda schedule_id: (row, "fixture-schedule"))
    monkeypatch.setattr(control, "_plan", slow_sync_plan)
    monkeypatch.setattr(control, "_plan_async", slow_async_plan, raising=False)
    monkeypatch.setattr(control, "_describe", scaled_describe)

    started_at = time.monotonic()
    with pytest.raises(TimeoutError):
        control.describe(row["id"])
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.15
    assert plan_started.is_set()
    assert plan_cancelled.is_set()
    assert not sync_plan_called.is_set()


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
