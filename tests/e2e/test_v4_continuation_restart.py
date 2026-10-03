"""Canonical cycle identities across actual Continue-As-New process death."""
import asyncio
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import psycopg
import pytest
from temporalio.client import Client
from temporalio.worker import Replayer

from test_v4_outbox_recovery import scenario, wait_consumed, inbox_count
from salience.cycles.runtime import TemporalCycleTransport, build_local_cycle_worker
from salience.cycles.workflow import LocalCycleWorkflow


@pytest.mark.asyncio
async def test_process_kill_after_continuation_preserves_original_outbox_and_context(tmp_path):
    service,outbox,admitted = scenario()
    client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    queue = "salience-v4-local-"+uuid4().hex
    transport = TemporalCycleTransport(client,task_queue=queue)
    environment = os.environ | {"SALIENCE_DEPLOYMENT_MODE":"fixture", "V4_FIXTURE_WORKSPACE":str(service.workspace_id),
        "V4_FIXTURE_QUEUE":queue,"PYTHONPATH":str(Path(__file__).resolve().parents[2]/"src")}
    def worker():
        with (tmp_path/"worker.log").open("a") as output:
            return subprocess.Popen([sys.executable,"-m","salience.cycles.runtime"],env=environment,stdout=output,stderr=subprocess.STDOUT)
    first,second = worker(),None
    runtime_id = transport.workflow_id(admitted["cycle_id"])
    try:
        start = outbox.claim()
        await transport.deliver(start)
        await wait_consumed(service.database_url,admitted["cycle_id"],1)
        outbox.ack(start["id"],start["lease_token"])
        first_run = (await client.get_workflow_handle(runtime_id).describe()).run_id
        service.recover(admitted["cycle_id"],state="retry_due")
        recovery = outbox.claim()
        for _ in range(8):
            await transport.deliver(recovery)
        await wait_consumed(service.database_url,admitted["cycle_id"],2)
        outbox.ack(recovery["id"],recovery["lease_token"])
        async with asyncio.timeout(20):
            while (await client.get_workflow_handle(runtime_id).describe()).run_id == first_run:
                await asyncio.sleep(.05)
        first.kill()
        await asyncio.to_thread(first.wait,10)
        service.close(admitted["cycle_id"],disposition="cancelled",reason="continuation crash fixture")
        assert await outbox.dispatch_one(transport)
        second = worker()
        assert await asyncio.wait_for(client.get_workflow_handle(runtime_id).result(),30) == {"cycle_id":str(admitted["cycle_id"]),"state":"recorded"}
        for run in (first_run,(await client.get_workflow_handle(runtime_id).describe()).run_id):
            history = await client.get_workflow_handle(runtime_id,run_id=run).fetch_history()
            await Replayer(workflows=[LocalCycleWorkflow]).replay_workflow(history)
        assert inbox_count(service.database_url,admitted["cycle_id"]) == 3
        with psycopg.connect(service.database_url) as connection:
            cycle,context,operation = connection.execute("SELECT id,context_id,operation_id FROM v4_cycles WHERE intent_id=%s",(admitted["intent_id"],)).fetchone()
            assert cycle == admitted["cycle_id"]
            assert connection.execute("SELECT count(*) FROM v4_run_contexts WHERE id=%s AND cycle_id=%s",(context,cycle)).fetchone()[0] == 1
            assert connection.execute("SELECT count(*) FROM v4_cycle_outbox WHERE cycle_id=%s AND kind='start'",(cycle,)).fetchone()[0] == 1
            assert operation
    finally:
        for process in (first,second):
            if process is not None and process.poll() is None:
                process.kill()
                await asyncio.to_thread(process.wait,10)


@pytest.mark.asyncio
async def test_runtime_deadline_hold_is_owned_and_later_closure_reconciles_without_signal():
    service,outbox,admitted = scenario(2,wall_time_seconds=1)
    client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    queue = "salience-v4-local-"+uuid4().hex
    transport = TemporalCycleTransport(client,task_queue=queue)
    async with build_local_cycle_worker(client,task_queue=queue,outbox=outbox):
        assert await outbox.dispatch_one(transport)
        result = await asyncio.wait_for(client.get_workflow_handle(transport.workflow_id(admitted["cycle_id"])).result(),15)
    assert result["state"] == "held_timeout"
    with psycopg.connect(service.database_url) as connection:
        assert connection.execute("SELECT owner_id,reason FROM v4_runtime_holds WHERE cycle_id=%s",(admitted["cycle_id"],)).fetchone() == (service.subject_id,"held_timeout")
        assert connection.execute("SELECT count(*) FROM v4_cycle_events WHERE cycle_id=%s AND kind='runtime_held'",(admitted["cycle_id"],)).fetchone()[0] == 1
    service.close(admitted["cycle_id"],disposition="cancelled",reason="owner closes bounded runtime")
    class NoSignal:
        async def deliver(self,message):
            raise AssertionError("a completed held workflow must not receive a signal")
    assert await outbox.dispatch_one(NoSignal())
    assert inbox_count(service.database_url,admitted["cycle_id"]) == 2
