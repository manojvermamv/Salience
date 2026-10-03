"""Actual legacy histories, patch replay and bounded continuation on Temporal."""
import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from uuid import uuid4

import pytest
from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Replayer, Worker

from salience.cycles.legacy_workflow import LegacyLocalCycleWorkflow
from salience.cycles.workflow import LocalCycleWorkflow


class Receipts:
    def __init__(self, lifetime=30):
        self.messages = []
        self.bindings = 0
        self.holds = []
        self.lifetime = lifetime

    @activity.defn(name="salience.v4.fixture_consume")
    async def consume(self, message: dict) -> dict:
        self.messages.append(message)
        return {"cycle_id": message["cycle_id"], "state": "recorded"}

    @activity.defn(name="salience.v4.fixture_runtime_binding")
    async def bind(self, message: dict) -> dict:
        self.bindings += 1
        return {"cycle_id": message["cycle_id"], "context_id": "original-context",
                "operation_id": "original-operation",
                "deadline": (datetime.now(timezone.utc)+timedelta(seconds=self.lifetime)).isoformat()}

    @activity.defn(name="salience.v4.fixture_runtime_hold")
    async def hold(self, message: dict) -> dict:
        self.holds.append(message)
        return message

    @property
    def activities(self):
        return [self.consume, self.bind, self.hold]


def envelope(cycle, kind="start"):
    return {"message_id": str(uuid4()), "cycle_id": cycle, "kind": kind, "traceparent": "fixture"}


@pytest.mark.asyncio
async def test_real_legacy_history_replays_and_wait_upgrades_without_new_binding():
    client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    queue = "salience-v4-local-"+uuid4().hex
    initial = envelope(str(uuid4()))
    receipts = Receipts()
    async with Worker(client, task_queue=queue, workflows=[LegacyLocalCycleWorkflow], activities=receipts.activities):
        handle = await client.start_workflow(LegacyLocalCycleWorkflow.run, initial, id=str(uuid4()), task_queue=queue)
        async with asyncio.timeout(15):
            while not receipts.messages:
                await asyncio.sleep(.05)
    old_history = await handle.fetch_history()
    Path("artifacts/v4-p0").mkdir(parents=True, exist_ok=True)
    Path("artifacts/v4-p0/item6-pre-upgrade-history.json").write_text(old_history.to_json())
    await Replayer(workflows=[LocalCycleWorkflow]).replay_workflow(old_history)
    async with Worker(client, task_queue=queue, workflows=[LocalCycleWorkflow], activities=receipts.activities):
        await handle.signal(LocalCycleWorkflow.deliver, envelope(initial["cycle_id"], "close"))
        assert (await asyncio.wait_for(handle.result(), 15))["state"] == "recorded"
    await Replayer(workflows=[LocalCycleWorkflow]).replay_workflow(await handle.fetch_history())
    assert receipts.bindings == 0
    assert len(receipts.messages) == 2


@pytest.mark.asyncio
async def test_continue_as_new_carries_pending_identity_and_consumes_start_once():
    client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    queue = "salience-v4-local-"+uuid4().hex
    initial = envelope(str(uuid4()))
    receipts = Receipts()
    async with Worker(client, task_queue=queue, workflows=[LocalCycleWorkflow], activities=receipts.activities):
        handle = await client.start_workflow(LocalCycleWorkflow.run, initial, id=str(uuid4()), task_queue=queue)
        first_run = handle.first_execution_run_id
        for _ in range(10):
            await handle.signal(LocalCycleWorkflow.deliver, envelope(initial["cycle_id"], "recovery"))
        await handle.signal(LocalCycleWorkflow.deliver, envelope(initial["cycle_id"], "close"))
        result = await asyncio.wait_for(handle.result(), 20)
    assert result == {"cycle_id": initial["cycle_id"], "state": "recorded"}
    assert (await client.get_workflow_handle(handle.id).describe()).run_id != first_run
    assert receipts.bindings == 1
    assert [m["kind"] for m in receipts.messages].count("start") == 1
    assert len(receipts.messages) == 12
    await Replayer(workflows=[LocalCycleWorkflow]).replay_workflow(await client.get_workflow_handle(handle.id).fetch_history())


@pytest.mark.asyncio
async def test_deadline_is_absolute_and_has_a_canonical_owner_hold():
    client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    queue = "salience-v4-local-"+uuid4().hex
    receipts = Receipts(lifetime=1)
    initial = envelope(str(uuid4()))
    async with Worker(client, task_queue=queue, workflows=[LocalCycleWorkflow], activities=receipts.activities):
        handle = await client.start_workflow(LocalCycleWorkflow.run, initial, id=str(uuid4()), task_queue=queue)
        result = await asyncio.wait_for(handle.result(), 10)
    assert result["state"] == "held_timeout"
    assert len(receipts.holds) == 1
    assert receipts.holds[0]["operation_id"] == "original-operation"


@pytest.mark.asyncio
async def test_continuations_never_reset_global_message_cap_and_replay_every_run():
    client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    queue = "salience-v4-local-"+uuid4().hex
    initial = envelope(str(uuid4()))
    receipts = Receipts()
    async with Worker(client,task_queue=queue,workflows=[LocalCycleWorkflow],activities=receipts.activities):
        handle = await client.start_workflow(LocalCycleWorkflow.run,initial,id=str(uuid4()),task_queue=queue)
        for index in range(31):
            await handle.signal(LocalCycleWorkflow.deliver,envelope(initial["cycle_id"],"recovery"))
            async with asyncio.timeout(15):
                while len(receipts.messages) < index+2:
                    await asyncio.sleep(.02)
        assert (await asyncio.wait_for(handle.result(),15))["state"] == "held_message_limit"
    assert len(receipts.messages) == 32 and receipts.bindings == 1
    assert len(receipts.holds) == 1
    run_id = handle.first_execution_run_id
    runs = 0
    while run_id:
        history = await client.get_workflow_handle(handle.id,run_id=run_id).fetch_history()
        await Replayer(workflows=[LocalCycleWorkflow]).replay_workflow(history)
        runs += 1
        run_id = next((event.workflow_execution_continued_as_new_event_attributes.new_execution_run_id for event in history.events if event.HasField("workflow_execution_continued_as_new_event_attributes")),None)
    assert runs == 4


@pytest.mark.asyncio
async def test_wait_deadline_survives_continuation_without_another_initial_activity():
    client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    queue = "salience-v4-local-"+uuid4().hex
    initial = envelope(str(uuid4()))
    receipts = Receipts(lifetime=2)
    async with Worker(client,task_queue=queue,workflows=[LocalCycleWorkflow],activities=receipts.activities):
        handle = await client.start_workflow(LocalCycleWorkflow.run,initial,id=str(uuid4()),task_queue=queue)
        for _ in range(8):
            await handle.signal(LocalCycleWorkflow.deliver,envelope(initial["cycle_id"],"recovery"))
        result = await asyncio.wait_for(handle.result(),8)
    assert result["state"] == "held_timeout"
    assert receipts.bindings == 1 and len(receipts.messages) == 9
    assert (await client.get_workflow_handle(handle.id).describe()).run_id != handle.first_execution_run_id
    assert receipts.holds[0]["context_id"] == "original-context"
