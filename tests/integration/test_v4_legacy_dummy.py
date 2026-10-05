"""A legacy dummy start must retain the admitted scope and never send effects."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
from uuid import uuid4

import psycopg
import pytest
from pydantic import ValidationError

from test_v4_cycle_admission import cycles
from test_v4_cadence_policy import policy
from test_v4_legacy_dispatch import legacy


@pytest.fixture
def dummy(legacy):
    from salience.cycles.legacy_dispatch import LegacyDummyCommand
    bridge, intelligence, service, database = legacy
    with psycopg.connect(database) as connection:
        connection.execute("INSERT INTO permission_grants(workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at) VALUES(%s,'identity',%s,'legacy:dummy','allow','{}',now()+interval '1 hour')", (service.workspace_id,str(service.subject_id)))
    return bridge, LegacyDummyCommand(goal_id=intelligence.goal_id,request=intelligence.request), service, database


def test_dummy_atomic_concurrent_replay_retains_original_scope(dummy, monkeypatch):
    bridge, command, service, database = dummy
    materialize = bridge._materialize
    def crash(*args):
        materialize(*args)
        raise RuntimeError("Dummy before commit")
    monkeypatch.setattr(bridge,"_materialize",crash)
    with pytest.raises(RuntimeError,match="before commit"):
        bridge.submit_dummy(command)
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM v4_cycles cycle JOIN v4_cycle_intents intent ON intent.id=cycle.intent_id WHERE intent.goal_id=%s", (command.goal_id,)).fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM jobs WHERE workspace_id=%s",(service.workspace_id,)).fetchone()[0] == 0
    monkeypatch.setattr(bridge,"_materialize",materialize)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results=list(pool.map(lambda _:bridge.submit_dummy(command),range(3)))
    assert results[0] == results[1] == results[2] and results[0]["state"] == "queued"
    with psycopg.connect(database) as connection:
        identity=connection.execute("SELECT job.workspace_id,job.content_program_id,job.job_type,job.dry_run,binding.stage,cycle.operation_id,ctx.payload->>'content_program_id' FROM jobs job JOIN v4_legacy_dispatches binding ON binding.job_id=job.id JOIN v4_cycles cycle ON cycle.id=binding.cycle_id JOIN v4_run_contexts ctx ON ctx.id=cycle.context_id WHERE job.id=%s",(results[0]["job_id"],)).fetchone()
        assert identity[0] == service.workspace_id and str(identity[1]) == identity[6]
        assert identity[2:5] == ("durable_dummy",True,"dummy") and str(identity[5]) == results[0]["operation_id"]
        assert connection.execute("SELECT count(*) FROM v4_cycle_outbox WHERE cycle_id=%s",(results[0]["cycle_id"],)).fetchone()[0] == 1
    from salience.cycles.legacy_dispatch import LegacyIntelligenceCommand
    with pytest.raises(ValueError, match="workload conflict"):
        bridge.submit(LegacyIntelligenceCommand(goal_id=command.goal_id,niche="Fixture",request=command.request.model_copy(update={"idempotency_key":"other-stage"})))


def test_dummy_requires_current_stage_grant_and_rejects_effect_controls(dummy):
    bridge,command,service,database=dummy
    for extra in ({"dry_run":False},{"mode":"timeout"},{"crash_after_remote_acceptance":True},{"effect_delay_seconds":60}):
        with pytest.raises(ValidationError):
            type(command).model_validate(command.model_dump()|extra)
    with psycopg.connect(database) as connection:
        connection.execute("UPDATE permission_grants SET effect='deny' WHERE principal_id=%s AND scope='legacy:dummy'",(str(service.subject_id),))
    with pytest.raises(PermissionError,match="authority"):
        bridge.submit_dummy(command)
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM jobs WHERE workspace_id=%s",(service.workspace_id,)).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_actual_dummy_workflow_uses_original_permit_and_no_send_provider(dummy):
    from temporalio.client import Client
    from salience.cycles.legacy_runtime import build_legacy_worker
    from salience.cycles.outbox import CycleOutbox
    from salience.cycles.runtime import TemporalCycleTransport
    bridge,command,service,database=dummy
    submitted=bridge.submit_dummy(command)
    with psycopg.connect(database) as connection:
        workspaces=connection.execute("SELECT count(*) FROM workspaces").fetchone()[0]
        programs=connection.execute("SELECT count(*) FROM content_programs").fetchone()[0]
    client=await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    box=CycleOutbox(database,workspace_id=service.workspace_id,delivery_lane="legacy")
    transport=TemporalCycleTransport(client,task_queue=bridge.task_queue)
    async with build_legacy_worker(client,task_queue=bridge.task_queue,outbox=box):
        assert await box.dispatch_one(transport)
        child=client.get_workflow_handle("salience-v4-legacy-operation:"+submitted["operation_id"])
        async with asyncio.timeout(10):
            while True:
                try:
                    await child.describe()
                    break
                except Exception: await asyncio.sleep(.05)
        assert await asyncio.wait_for(child.result(),15) == "succeeded"
        assert bridge.inspect(submitted["job_id"])["state"] == "succeeded"
        assert (await child.describe()).workflow_type == "DurableDummyWorkflow"
        with psycopg.connect(database) as connection:
            assert connection.execute("SELECT count(*) FROM workspaces").fetchone()[0] == workspaces
            assert connection.execute("SELECT count(*) FROM content_programs").fetchone()[0] == programs
            assert connection.execute("SELECT provider_reference FROM external_effects WHERE job_id=%s",(submitted["job_id"],)).fetchone()[0] == "dry-run:v4-legacy:"+submitted["operation_id"]
            assert connection.execute("SELECT count(*) FROM v4_permit_claims WHERE cycle_id=%s",(submitted["cycle_id"],)).fetchone()[0] == 1
        service.close(submitted["cycle_id"],disposition="completed",reason="Canonical dummy verified")
        assert await box.dispatch_one(transport)
        await asyncio.wait_for(client.get_workflow_handle(transport.workflow_id(submitted["cycle_id"])).result(),10)


def test_signed_dummy_sdk_cli_never_calls_old_control_plane(dummy,monkeypatch,capsys):
    import httpx,jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    from fastapi.testclient import TestClient
    from salience.api.p0 import create_p0_app
    from salience.sdk.client import SalienceClient
    from salience.cli import main
    bridge,command,service,database=dummy
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    app=create_p0_app(database_url=database,workspace_id=service.workspace_id,issuer="https://fixture.invalid",audience="fixture",public_key=key.public_key(),enable_legacy_dispatch=True,legacy_fixture_queue=bridge.task_queue)
    async def forbidden(**kwargs):raise AssertionError("Old dummy start is unreachable")
    monkeypatch.setattr(app.state.control_plane,"start_dummy",forbidden)
    now=datetime.now(timezone.utc)
    token=jwt.encode({"iss":"https://fixture.invalid","aud":"fixture","sub":str(service.subject_id),"iat":now,"nbf":now,"exp":now+timedelta(minutes=5)},key,algorithm="RS256")
    with TestClient(app) as client:
        monkeypatch.setattr(httpx,"request",lambda method,url,**kwargs:client.request(method,url,**{k:v for k,v in kwargs.items() if k!="timeout"}))
        sdk=SalienceClient("http://testserver",token).legacy_jobs
        first=sdk.submit_dummy(command.model_dump(mode="json"))
        monkeypatch.setenv("SALIENCE_CONTROL_JWT",token)
        monkeypatch.setenv("SALIENCE_CONTROL_URL","http://testserver")
        main(["legacy-jobs","dummy","--command-json",json.dumps(command.model_dump(mode="json"))])
        assert json.loads(capsys.readouterr().out) == first
        assert sdk.inspect(first["job_id"])["state"] == "queued"
        headers={"Authorization":"Bearer "+token,"X-Salience-Scopes":"admin:*,legacy:dummy,cycles:write"}
        assert client.post("/v1/jobs/dummy",headers=headers,json={"dry_run":True,"idempotency_key":"old"}).status_code == 422
        assert client.post("/v1/jobs/dummy",headers=headers,json=command.model_dump(mode="json")|{"goal_id":str(uuid4())}).status_code == 403
        assert sdk.cancel(first["job_id"],reason="Cancel before delivery")["cycle_state"] == "closed"
        with psycopg.connect(database) as connection:
            connection.execute("UPDATE permission_grants SET effect='deny' WHERE principal_id=%s AND scope='legacy:dummy'",(str(service.subject_id),))
        assert client.post("/v1/jobs/dummy",headers=headers,json=command.model_dump(mode="json")).status_code == 403


@pytest.mark.asyncio
async def test_dummy_stage_revocation_after_admission_holds_original_without_start(dummy):
    from temporalio.client import Client
    from salience.cycles.legacy_runtime import build_legacy_worker
    from salience.cycles.outbox import CycleOutbox
    from salience.cycles.runtime import TemporalCycleTransport
    bridge,command,service,database=dummy
    submitted=bridge.submit_dummy(command)
    with psycopg.connect(database) as connection:
        connection.execute("UPDATE permission_grants SET effect='deny' WHERE principal_id=%s AND scope='legacy:dummy'",(str(service.subject_id),))
    client=await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    box=CycleOutbox(database,workspace_id=service.workspace_id,delivery_lane="legacy")
    transport=TemporalCycleTransport(client,task_queue=bridge.task_queue)
    async with build_legacy_worker(client,task_queue=bridge.task_queue,outbox=box):
        assert await box.dispatch_one(transport)
        async with asyncio.timeout(15):
            while bridge.inspect(submitted["job_id"])["dispatch_state"] != "unknown":
                await asyncio.sleep(.05)
        with pytest.raises(Exception):
            await client.get_workflow_handle("salience-v4-legacy-operation:"+submitted["operation_id"]).describe()
        with psycopg.connect(database) as connection:
            assert connection.execute("SELECT count(*) FROM v4_permit_claims WHERE cycle_id=%s",(submitted["cycle_id"],)).fetchone()[0] == 1
            assert connection.execute("SELECT count(*) FROM external_effects WHERE job_id=%s",(submitted["job_id"],)).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_actual_dummy_cancel_is_terminal_readback_after_committed_close(dummy,monkeypatch):
    from temporalio import activity
    from temporalio.client import Client,WorkflowHandle,WorkflowExecutionStatus
    from salience.cycles.legacy_runtime import build_legacy_worker,GuardedDummyActivities
    from salience.workflows.jobs import DummyActivities
    from salience.cycles.outbox import CycleOutbox
    from salience.cycles.runtime import TemporalCycleTransport
    bridge,command,service,database=dummy
    submitted=bridge.submit_dummy(command)
    reached=asyncio.Event()
    release=asyncio.Event()
    @activity.defn(name="salience.checkpoint")
    async def checkpoint(self,checkpoint_name:str)->None:
        await DummyActivities.checkpoint(self,checkpoint_name)
        if checkpoint_name == "before_external_effect":
            reached.set()
            await release.wait()
    monkeypatch.setattr(GuardedDummyActivities,"checkpoint",checkpoint)
    original_cancel=WorkflowHandle.cancel
    calls=[]
    async def lost_ack(handle,**kwargs):
        await original_cancel(handle,**kwargs)
        calls.append(handle.id)
        async with asyncio.timeout(5):
            while (await handle.describe()).status == WorkflowExecutionStatus.RUNNING:
                await asyncio.sleep(.05)
        raise TimeoutError("Original cancellation accepted; acknowledgment lost")
    monkeypatch.setattr(WorkflowHandle,"cancel",lost_ack)
    client=await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    box=CycleOutbox(database,workspace_id=service.workspace_id,delivery_lane="legacy")
    transport=TemporalCycleTransport(client,task_queue=bridge.task_queue)
    async with build_legacy_worker(client,task_queue=bridge.task_queue,outbox=box):
        try:
            assert await box.dispatch_one(transport)
            await asyncio.wait_for(reached.wait(),10)
            service.close(submitted["cycle_id"],disposition="cancelled",reason="Original dummy physical cancellation")
            assert await box.dispatch_one(transport)
            await asyncio.wait_for(client.get_workflow_handle(transport.workflow_id(submitted["cycle_id"])).result(),10)
            child=client.get_workflow_handle("salience-v4-legacy-operation:"+submitted["operation_id"])
            assert (await child.describe()).status == WorkflowExecutionStatus.CANCELED
            assert calls == [child.id]
            assert bridge.inspect(submitted["job_id"])["state"] == "cancelled"
            with psycopg.connect(database) as connection:
                assert connection.execute("SELECT count(*) FROM external_effects WHERE job_id=%s",(submitted["job_id"],)).fetchone()[0] == 0
                assert connection.execute("SELECT kind FROM v4_cycle_outbox WHERE cycle_id=%s ORDER BY sequence",(submitted["cycle_id"],)).fetchall() == [("start",),("close",)]
        finally:
            release.set()


@pytest.mark.asyncio
async def test_forged_dummy_workflow_payload_cannot_send_or_crash_worker(dummy):
    from hashlib import sha256
    from temporalio.client import Client
    from salience.cycles.governance import CycleGovernance,PermitRequest
    from salience.cycles.legacy_runtime import build_legacy_worker
    from salience.cycles.outbox import CycleOutbox
    from salience.workflows.jobs import DurableDummyWorkflow,DummyWorkflowRequest
    bridge,command,service,database=dummy
    submitted=bridge.submit_dummy(command)
    governance=CycleGovernance(database,workspace_id=service.workspace_id,subject_id=service.subject_id)
    permit=governance.issue_permit(submitted["cycle_id"],PermitRequest(context_id=submitted["context_id"],operation_id=submitted["operation_id"],expected_goal_revision=1,account_ref="fixture-account",purpose="fixture_execution",effect="fixture.noop",artifact_sha256=sha256(b"fixture").hexdigest(),ttl_seconds=30),idempotency_key="fixture-permit")
    assert governance.claim_permit(permit["permit_id"])["dispatch_allowed"]
    client=await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    box=CycleOutbox(database,workspace_id=service.workspace_id,delivery_lane="legacy")
    async with build_legacy_worker(client,task_queue=bridge.task_queue,outbox=box):
        child=await client.start_workflow(DurableDummyWorkflow.run,DummyWorkflowRequest(idempotency_key="v4-legacy:"+submitted["operation_id"],dry_run=False,crash_after_remote_acceptance=True),id="salience-v4-legacy-operation:"+submitted["operation_id"],task_queue=bridge.task_queue,execution_timeout=timedelta(seconds=25))
        assert await asyncio.wait_for(child.result(),15) == "dead_lettered"
        with psycopg.connect(database) as connection:
            assert connection.execute("SELECT count(*) FROM external_effects WHERE job_id=%s",(submitted["job_id"],)).fetchone()[0] == 0


def test_dummy_migration_empty_restore_and_populated_stage_preservation(dummy):
    import subprocess,sys
    from urllib.parse import urlsplit,urlunsplit
    bridge,command,_,database=dummy
    name="legacy_stage_migration_"+uuid4().hex
    fresh=urlunsplit(urlsplit(database)._replace(path="/"+name))
    with psycopg.connect(database,autocommit=True) as admin:
        admin.execute(psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(name)))
        try:
            def migrate(db,direction,target):
                return subprocess.run([sys.executable,"-m","alembic","-x","database_url="+db,direction,target],capture_output=True,text=True)
            def guard():
                with psycopg.connect(fresh) as connection:
                    return connection.execute("SELECT pg_get_functiondef('v4_guard_legacy_dispatch()'::regprocedure)").fetchone()[0]
            assert migrate(fresh,"upgrade","0037_native_schedule_sources").returncode == 0
            original=guard()
            assert migrate(fresh,"upgrade","head").returncode == 0
            assert guard() != original
            assert migrate(fresh,"downgrade","0037_native_schedule_sources").returncode == 0
            assert guard() == original
            assert migrate(fresh,"upgrade","head").returncode == 0
            submitted=bridge.submit_dummy(command)
            result=migrate(database,"downgrade","0037_native_schedule_sources")
            assert result.returncode != 0 and any(reason in result.stderr for reason in ("preserve original legacy stage history", "preserve original legacy creative history"))
            with psycopg.connect(database) as connection:
                assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0039_legacy_creative_stage"
                assert connection.execute("SELECT stage FROM v4_legacy_dispatches WHERE job_id=%s",(submitted["job_id"],)).fetchone()[0] == "dummy"
        finally:
            admin.execute(psycopg.sql.SQL("DROP DATABASE {} WITH (FORCE)").format(psycopg.sql.Identifier(name)))
