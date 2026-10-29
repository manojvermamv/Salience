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
    plan_started = threading.Event()
    plan_finished = threading.Event()

    def slow_plan(_row, *, required=False):
        plan_started.set()
        time.sleep(0.2)
        plan_finished.set()
        return None

    monkeypatch.setattr(control, "_plan", slow_plan)
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.03):
            await control._verified(client, handle, row)

    assert plan_started.is_set()
    await asyncio.to_thread(plan_finished.wait, 1)
    assert plan_finished.is_set()


@pytest.mark.asyncio
async def test_resume_plan_lookup_does_not_exceed_async_deadline(monkeypatch):
    control, _, handle, _, row = _readback_fixture(monkeypatch, [])
    plan_started = threading.Event()
    plan_finished = threading.Event()

    def slow_plan(_row, *, required=False):
        plan_started.set()
        time.sleep(0.5)
        plan_finished.set()
        return {"goal_id": "fixture-goal"}

    monkeypatch.setattr(control, "_plan", slow_plan)
    loop = asyncio.get_running_loop()
    started_at = loop.time()
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.03):
            await control._resume(row, "fixture-schedule", datetime(2025, 1, 1, tzinfo=timezone.utc))
    elapsed = loop.time() - started_at

    assert elapsed < 0.3
    assert plan_started.is_set()
    assert handle.update_calls == []
    await asyncio.to_thread(plan_finished.wait, 1)
    assert plan_finished.is_set()


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
