import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import psycopg
import pytest
from temporalio.client import Client
from temporalio.worker import Replayer

from salience.cycles.admission import CycleAdmission
from salience.cycles.contracts import GoalSpec
from salience.cycles.outbox import CycleOutbox
from salience.cycles.runtime import TemporalCycleTransport, build_local_cycle_worker
from salience.cycles.workflow import LocalCycleWorkflow


def scenario():
    database=os.environ["TEST_DATABASE_URL"]
    workspace, subject=uuid4(),uuid4()
    with psycopg.connect(database) as connection:
        connection.execute("INSERT INTO workspaces (id,slug,display_name) VALUES (%s,%s,'outbox fixture')",(workspace,str(workspace)))
        connection.execute("INSERT INTO identity_subjects (id,workspace_id,issuer,subject,expires_at) VALUES (%s,%s,'https://fixture.invalid',%s,now()+interval '1 hour')",(subject,workspace,str(subject)))
        for scope in ["goals:write","cycles:write"]:
            connection.execute("INSERT INTO permission_grants (workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at) VALUES (%s,'identity',%s,%s,'allow','{}',now()+interval '1 hour')",(workspace,str(subject),scope))
    service=CycleAdmission(database,workspace_id=workspace,subject_id=subject)
    now=datetime.now(timezone.utc)
    goal=service.create_goal(GoalSpec(objective="outbox crash fixture",metric_versions=("fixture@1",),audience="internal",account_refs=("fixture",),brand_scope="fixture",source_policy="fixture-only",horizon_end=now+timedelta(hours=1)))
    requested=service.request_intent(goal,slot="one",due_at=now,expires_at=now+timedelta(minutes=5))
    admitted=service.admit(requested)
    return service,CycleOutbox(database,workspace_id=workspace),admitted


def inbox_count(database,cycle_id):
    with psycopg.connect(database) as connection:
        return connection.execute("SELECT count(*) FROM v4_cycle_inbox WHERE cycle_id=%s",(cycle_id,)).fetchone()[0]


async def wait_consumed(database,cycle_id,count):
    async with asyncio.timeout(30):
        while await asyncio.to_thread(inbox_count,database,cycle_id)<count:
            await asyncio.sleep(0.1)


@pytest.mark.asyncio
async def test_process_kill_ack_loss_and_queued_signal_resume_original_cycle(tmp_path):
    service,outbox,admitted=scenario()
    client=await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    queue="salience-v4-local-"+uuid4().hex
    transport=TemporalCycleTransport(client,task_queue=queue)
    environment=os.environ | {"SALIENCE_DEPLOYMENT_MODE":"fixture","V4_FIXTURE_WORKSPACE":str(service.workspace_id),"V4_FIXTURE_QUEUE":queue,"PYTHONPATH":str(Path(__file__).resolve().parents[2] / "src")}
    logs=tmp_path / "worker.log"
    def worker():
        with logs.open("a") as output:
            return subprocess.Popen([sys.executable,"-m","salience.cycles.runtime"],env=environment,stdout=output,stderr=subprocess.STDOUT)
    first=worker()
    second=None
    try:
        message=outbox.claim()
        await transport.deliver(message)
        await wait_consumed(service.database_url,admitted["cycle_id"],1)
        first.kill()
        await asyncio.to_thread(first.wait,10)
        await transport.deliver(message)
        with psycopg.connect(service.database_url) as connection:
            connection.execute("UPDATE v4_cycle_outbox SET lease_until=now()-interval '1 second' WHERE id=%s",(message["id"],))
        assert await outbox.dispatch_one(transport)
        service.close(admitted["cycle_id"],disposition="abstain",reason="fixture complete")
        assert await outbox.dispatch_one(transport)
        second=worker()
        result=await asyncio.wait_for(client.get_workflow_handle(transport.workflow_id(admitted["cycle_id"])).result(),30)
        assert result["cycle_id"]==str(admitted["cycle_id"])
        history=await client.get_workflow_handle(transport.workflow_id(admitted["cycle_id"])).fetch_history()
        await Replayer(workflows=[LocalCycleWorkflow]).replay_workflow(history)
        assert inbox_count(service.database_url,admitted["cycle_id"])==2
        assert outbox.consume(message["id"])["cycle_id"]==admitted["cycle_id"]
        with psycopg.connect(service.database_url) as connection:
            assert connection.execute("SELECT count(DISTINCT payload->>'message_id') FROM v4_cycle_events WHERE cycle_id=%s AND kind='fixture_consumed'",(admitted["cycle_id"],)).fetchone()[0]==2
    finally:
        for process in [first,second]:
            if process and process.poll() is None:
                process.kill()
                await asyncio.to_thread(process.wait,10)
        print(logs.read_text())


@pytest.mark.asyncio
async def test_outbox_workflow_activity_trace_matches_canonical_consumption():
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    service,_,admitted=scenario()
    provider=TracerProvider()
    exporter=InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    outbox=CycleOutbox(service.database_url,workspace_id=service.workspace_id,tracer=provider.get_tracer("fixture"))
    client=await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    queue="salience-v4-local-"+uuid4().hex
    transport=TemporalCycleTransport(client,task_queue=queue)
    worker=build_local_cycle_worker(client,task_queue=queue,outbox=outbox)
    async with worker:
        assert await outbox.dispatch_one(transport)
        await wait_consumed(service.database_url,admitted["cycle_id"],1)
        service.close(admitted["cycle_id"],disposition="completed",reason="fixture")
        assert await outbox.dispatch_one(transport)
        await asyncio.wait_for(client.get_workflow_handle(transport.workflow_id(admitted["cycle_id"])).result(),20)
    spans=exporter.get_finished_spans()
    assert {span.name for span in spans} >= {"cycle.outbox.delivery","cycle.fixture.consume"}
    assert all(f"{span.context.trace_id:032x}"==service.trace.trace_id for span in spans)
    with psycopg.connect(service.database_url) as connection:
        carriers=connection.execute("SELECT traceparent FROM v4_cycle_inbox WHERE cycle_id=%s",(admitted["cycle_id"],)).fetchall()
    ids={f"{span.context.span_id:016x}" for span in spans}
    assert all(carrier[0].split("-")[2] in ids for carrier in carriers)
    provider.shutdown()


def test_local_cycle_transport_rejects_production_queue():
    with pytest.raises(ValueError,match="local fixture"):
        TemporalCycleTransport(None,task_queue="production")
