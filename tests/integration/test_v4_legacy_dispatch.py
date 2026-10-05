"""Legacy compatibility commands must enter canonical admission and outbox."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import os
from uuid import uuid4

import psycopg
import pytest

from test_v4_cadence_policy import policy, request
from test_v4_cycle_admission import cycles
from test_v4_schedule_cutover import cutover


@pytest.fixture
def legacy(policy, monkeypatch):
    from salience.cycles.legacy_dispatch import LegacyDispatch, LegacyIntelligenceCommand

    monkeypatch.setenv("SALIENCE_DEPLOYMENT_MODE", "fixture")
    monkeypatch.setenv("SALIENCE_EFFECTS_ENABLED", "false")
    service, goal, spec, database = policy
    bridge = LegacyDispatch(database, workspace_id=service.workspace_id,
                            subject_id=service.subject_id,
                            task_queue="salience-v4-local-legacy-" + str(uuid4()))
    with psycopg.connect(database) as connection:
        for scope in ("cycles:permit", "cycles:read", "cycles:stop"):
            connection.execute("INSERT INTO permission_grants(workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at) VALUES(%s,'identity',%s,%s,'allow','{}',now()+interval '1 hour')", (service.workspace_id,str(service.subject_id),scope))
    command = LegacyIntelligenceCommand(goal_id=goal, request=request(spec), niche="Fixture")
    return bridge, command, service, database


def test_legacy_submit_commits_one_canonical_job_and_start_under_concurrent_replay(legacy):
    bridge, command, service, database = legacy
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: bridge.submit(command), range(3)))
    assert results[0] == results[1] == results[2]
    result = results[0]
    assert result["state"] == "queued" and result["disposition"] == "admitted"
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM v4_cycle_outbox WHERE cycle_id=%s", (result["cycle_id"],)).fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM jobs WHERE id=%s AND dry_run AND state='queued'", (result["job_id"],)).fetchone()[0] == 1
        identity = connection.execute("SELECT context_id,operation_id FROM v4_cycles WHERE id=%s", (result["cycle_id"],)).fetchone()
    assert tuple(map(str, identity)) == (result["context_id"], result["operation_id"])


def test_legacy_submit_rolls_back_everything_before_commit(legacy, monkeypatch):
    bridge, command, service, database = legacy
    original = bridge._materialize
    def crash(*args):
        original(*args)
        raise RuntimeError("crash before commit")
    monkeypatch.setattr(bridge, "_materialize", crash)
    with pytest.raises(RuntimeError, match="crash before commit"):
        bridge.submit(command)
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM v4_cycle_intents WHERE goal_id=%s", (command.goal_id,)).fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM jobs WHERE workspace_id=%s", (service.workspace_id,)).fetchone()[0] == 0
    monkeypatch.setattr(bridge, "_materialize", original)
    assert bridge.submit(command)["state"] == "queued"


def test_legacy_replay_conflict_and_current_revocation(legacy):
    bridge, command, service, database = legacy
    result = bridge.submit(command)
    with pytest.raises(ValueError, match="conflict"):
        bridge.submit(command.model_copy(update={"niche": "changed"}))
    with psycopg.connect(database) as connection:
        connection.execute("UPDATE identity_subjects SET enabled=false WHERE id=%s", (service.subject_id,))
    with pytest.raises(PermissionError):
        bridge.submit(command)


def test_legacy_binding_direct_sql_identity_guard_and_delivery_lane(legacy):
    from salience.cycles.outbox import CycleOutbox
    bridge, command, service, database = legacy
    result = bridge.submit(command)
    assert CycleOutbox(database, workspace_id=service.workspace_id).claim() is None
    box = CycleOutbox(database, workspace_id=service.workspace_id, delivery_lane="legacy")
    message = box.claim()
    assert str(message["cycle_id"]) == result["cycle_id"]
    with psycopg.connect(database) as connection, pytest.raises(psycopg.Error, match="immutable"):
        connection.execute("UPDATE jobs SET workflow_run_id='changed' WHERE id=%s", (result["job_id"],))
    with psycopg.connect(database) as connection, pytest.raises(psycopg.Error, match="immutable"):
        connection.execute("DELETE FROM v4_legacy_dispatches WHERE cycle_id=%s", (result["cycle_id"],))


def test_legacy_dispatch_rejects_effects_and_cross_program(legacy, monkeypatch):
    bridge, command, service, database = legacy
    monkeypatch.setenv("SALIENCE_EFFECTS_ENABLED", "true")
    with pytest.raises(PermissionError):
        bridge.submit(command)
    monkeypatch.setenv("SALIENCE_EFFECTS_ENABLED", "false")
    foreign = command.model_copy(update={"goal_id": uuid4()})
    with pytest.raises(PermissionError):
        bridge.submit(foreign)


def test_legacy_lane_cannot_be_consumed_or_exhausted_by_fixture_dispatcher(legacy):
    from salience.cycles.outbox import CycleOutbox
    bridge, command, service, database = legacy
    result=bridge.submit(command)
    box=CycleOutbox(database,workspace_id=service.workspace_id,delivery_lane="legacy",max_attempts=2)
    claimed=box.claim()
    with psycopg.connect(database) as connection:
        connection.execute("UPDATE v4_cycle_outbox SET lease_until=now()-interval '1 second' WHERE id=%s",(claimed["id"],))
    fixture=CycleOutbox(database,workspace_id=service.workspace_id,max_attempts=1)
    assert fixture.claim() is None
    with pytest.raises(PermissionError,match="delivery lane"):
        fixture.consume(claimed["id"])
    retry=box.claim()
    assert retry["id"]==claimed["id"] and retry["attempts"]==2


def test_adopting_undelivered_cycle_keeps_original_context_trace(legacy):
    bridge,command,service,database=legacy
    intent_id=service.request_cycle(command.goal_id,command.request)
    cycle=service.admit(intent_id)["cycle_id"]
    result=bridge.submit(command)
    assert result["cycle_id"]==str(cycle)
    with psycopg.connect(database) as connection:
        context_trace=connection.execute("SELECT traceparent FROM v4_cycle_outbox WHERE cycle_id=%s AND kind='start'",(cycle,)).fetchone()[0]
        trace=connection.execute("SELECT trace_id FROM jobs WHERE id=%s",(result["job_id"],)).fetchone()[0]
    assert trace==context_trace.split("-")[1]


@pytest.mark.asyncio
async def test_cancel_before_delivery_never_starts_legacy_intelligence(legacy):
    import asyncio
    from temporalio.client import Client
    from salience.cycles.outbox import CycleOutbox
    from salience.cycles.runtime import TemporalCycleTransport
    from salience.cycles.legacy_runtime import build_legacy_worker

    bridge,command,service,database=legacy
    result=bridge.submit(command)
    service.close(result["cycle_id"],disposition="cancelled",reason="Cancel before delivery")
    client=await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    box=CycleOutbox(database,workspace_id=service.workspace_id,delivery_lane="legacy")
    transport=TemporalCycleTransport(client,task_queue=bridge.task_queue)
    async with build_legacy_worker(client,task_queue=bridge.task_queue,outbox=box):
        assert await box.dispatch_one(transport)
        assert await box.dispatch_one(transport)
        await asyncio.wait_for(client.get_workflow_handle(transport.workflow_id(result["cycle_id"])).result(),10)
    assert bridge.inspect(result["job_id"])["state"]=="cancelled"
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM v4_permit_claims WHERE cycle_id=%s",(result["cycle_id"],)).fetchone()[0]==0


def test_legacy_migration_empty_rollback_and_populated_history_preservation(legacy):
    import subprocess,sys
    from urllib.parse import urlsplit,urlunsplit
    from salience.cycles.legacy_dispatch import LegacyDispatch
    from salience.cycles.admission import CycleAdmission
    from test_v4_cycle_admission import approved_goal

    bridge,command,service,source=legacy
    name="legacy_migration_"+uuid4().hex[:12]
    database=urlunsplit(urlsplit(source)._replace(path="/"+name))
    with psycopg.connect(source,autocommit=True) as admin:
        admin.execute(psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(name)))
        try:
            def migrate(direction,target):
                return subprocess.run([sys.executable,"-m","alembic","-x",f"database_url={database}",direction,target],capture_output=True,text=True)
            assert migrate("upgrade","head").returncode==0
            assert migrate("downgrade","0035_parallel_agent_teams").returncode==0
            assert migrate("upgrade","head").returncode==0
            with psycopg.connect(source) as c:
                spec=c.execute("SELECT payload FROM v4_goal_revisions WHERE goal_id=%s AND revision=1",(command.goal_id,)).fetchone()[0]
            with psycopg.connect(database) as c:
                c.execute("INSERT INTO workspaces(id,slug,display_name) VALUES(%s,%s,'Migration')",(service.workspace_id,str(service.workspace_id)))
                c.execute("INSERT INTO content_programs(id,workspace_id,slug,name,niche) VALUES(%s,%s,%s,'Migration','Fixture')",(spec["content_program_id"],service.workspace_id,str(uuid4())))
                c.execute("INSERT INTO identity_subjects(id,workspace_id,issuer,subject,expires_at) VALUES(%s,%s,'https://fixture.invalid',%s,now()+interval '1 hour')",(service.subject_id,service.workspace_id,str(service.subject_id)))
                for scope in ("goals:write","goals:approve","cycles:write"):
                    c.execute("INSERT INTO permission_grants(workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at) VALUES(%s,'identity',%s,%s,'allow','{}',now()+interval '1 hour')",(service.workspace_id,str(service.subject_id),scope))
            from salience.cycles.contracts import parse_goal
            isolated=CycleAdmission(database,workspace_id=service.workspace_id,subject_id=service.subject_id)
            goal=approved_goal(isolated,parse_goal(spec))
            binding=LegacyDispatch(database,workspace_id=service.workspace_id,subject_id=service.subject_id,task_queue=bridge.task_queue).submit(command.model_copy(update={"goal_id":goal}))
            rolled=migrate("downgrade","0035_parallel_agent_teams")
            assert rolled.returncode!=0 and "preserve legacy dispatch" in rolled.stderr
            with psycopg.connect(database) as c:
                assert c.execute("SELECT version_num FROM alembic_version").fetchone()[0]=="0037_native_schedule_sources"
                assert str(c.execute("SELECT operation_id FROM v4_legacy_dispatches WHERE job_id=%s",(binding["job_id"],)).fetchone()[0])==binding["operation_id"]
        finally:
            admin.execute(psycopg.sql.SQL("DROP DATABASE {} WITH (FORCE)").format(psycopg.sql.Identifier(name)))


@pytest.mark.asyncio
async def test_legacy_runtime_executes_actual_intelligence_once_and_ordered_close(legacy):
    import asyncio
    from temporalio.client import Client
    from salience.cycles.outbox import CycleOutbox
    from salience.cycles.runtime import TemporalCycleTransport
    from salience.cycles.legacy_runtime import build_legacy_worker

    bridge, command, service, database = legacy
    result = bridge.submit(command)
    client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    outbox = CycleOutbox(database,workspace_id=service.workspace_id,delivery_lane="legacy")
    transport = TemporalCycleTransport(client,task_queue=bridge.task_queue)
    async with build_legacy_worker(client,task_queue=bridge.task_queue,outbox=outbox):
        assert await outbox.dispatch_one(transport)
        execution = client.get_workflow_handle("salience-v4-legacy-operation:" + result["operation_id"])
        async with asyncio.timeout(20):
            while True:
                try:
                    await execution.describe()
                    break
                except Exception:
                    await asyncio.sleep(0.05)
        output = await asyncio.wait_for(execution.result(),20)
        assert output["state"] == "completed" and output["job_id"] == result["job_id"]
        assert bridge.inspect(result["job_id"])["state"] == "succeeded"
        # Canonical recovery changes control state, never starts another stage.
        service.recover(result["cycle_id"],state="retry_due")
        service.recover(result["cycle_id"],state="runnable")
        service.close(result["cycle_id"],disposition="completed",reason="Fixture execution complete")
        for _ in range(3):
            assert await outbox.dispatch_one(transport)
        await asyncio.wait_for(client.get_workflow_handle(transport.workflow_id(result["cycle_id"])).result(),10)
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM v4_cycle_inbox WHERE cycle_id=%s",(result["cycle_id"],)).fetchone()[0] == 4
        assert connection.execute("SELECT count(*) FROM v4_permit_claims WHERE cycle_id=%s",(result["cycle_id"],)).fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM jobs WHERE id=%s",(result["job_id"],)).fetchone()[0] == 1


@pytest.mark.asyncio
async def test_legacy_adapter_lost_ack_holds_original_without_second_start(legacy):
    import asyncio
    from salience.cycles.outbox import CycleOutbox
    from salience.cycles.legacy_runtime import LegacyIntelligenceAdapter, LegacyCycleActivities

    bridge, command, service, database = legacy
    result = bridge.submit(command)
    outbox = CycleOutbox(database,workspace_id=service.workspace_id,delivery_lane="legacy")
    message = outbox.claim()
    calls = []
    class LostAck:
        async def start_workflow(self,*args,**kwargs):
            calls.append(kwargs["id"])
            raise TimeoutError("remote start acknowledgment unknown")
    activities = LegacyCycleActivities(outbox,adapter=LegacyIntelligenceAdapter(LostAck(),outbox,task_queue=bridge.task_queue))
    envelope = {"message_id":str(message["id"]),"cycle_id":result["cycle_id"],"kind":"start","traceparent":message["traceparent"]}
    with pytest.raises(TimeoutError):
        await activities.consume(envelope)
    assert (await activities.consume(envelope))["state"] == "held"
    assert calls == ["salience-v4-legacy-operation:" + result["operation_id"]]
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT state FROM v4_permit_claims WHERE cycle_id=%s",(result["cycle_id"],)).fetchone()[0] == "unknown"


def test_legacy_stage_guard_rechecks_stop_after_claim(legacy):
    from hashlib import sha256
    from salience.cycles.governance import CycleGovernance, PermitRequest
    from salience.cycles.legacy_runtime import binding_for_operation, require_current_stage
    from salience.cycles.outbox import CycleOutbox

    bridge, command, service, database = legacy
    result = bridge.submit(command)
    outbox = CycleOutbox(database,workspace_id=service.workspace_id,delivery_lane="legacy")
    governance = CycleGovernance(database,workspace_id=service.workspace_id,subject_id=service.subject_id)
    permit = governance.issue_permit(result["cycle_id"],PermitRequest(context_id=result["context_id"],operation_id=result["operation_id"],
        expected_goal_revision=1,account_ref="fixture-account",purpose="fixture_execution",effect="fixture.noop",artifact_sha256=sha256(b"stage").hexdigest(),ttl_seconds=30),idempotency_key="claim")
    assert governance.claim_permit(permit["permit_id"])["dispatch_allowed"]
    binding = binding_for_operation(outbox,result["operation_id"])
    require_current_stage(outbox,binding)
    governance.set_stop(goal_id=command.goal_id,stopped=True,expected_revision=1,idempotency_key="stop",reason="Fixture stop")
    with pytest.raises(PermissionError):
        require_current_stage(outbox,binding)


def test_legacy_cutover_poll_atomically_materializes_scheduled_job_and_watermark(cutover, monkeypatch):
    import time
    from salience.cycles.legacy_dispatch import LegacyDispatch
    scheduler, service, goal, spec, legacy_id, database = cutover
    monkeypatch.setenv("SALIENCE_DEPLOYMENT_MODE","fixture")
    scheduler.prepare(goal,legacy_schedule_id=legacy_id,expected_revision=1,first_v4_slot=spec.cadence.anchor,idempotency_key="prepare")
    bridge = LegacyDispatch(database,workspace_id=service.workspace_id,subject_id=service.subject_id,task_queue="salience-v4-local-legacy-schedule")
    bridge.bind_schedule(goal,expected_revision=1,niche="Fixture")
    scheduler.activate(goal,idempotency_key="activate")
    time.sleep(max(0,(spec.cadence.anchor-datetime.now(timezone.utc)).total_seconds())+.05)
    original = bridge._materialize
    monkeypatch.setattr(LegacyDispatch,"_materialize",lambda *args: (_ for _ in ()).throw(RuntimeError("before job commit")))
    with pytest.raises(RuntimeError):
        scheduler.poll(goal,expected_revision=1,idempotency_key="poll")
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM v4_cycles c JOIN v4_cycle_intents i ON i.id=c.intent_id WHERE i.goal_id=%s",(goal,)).fetchone()[0] == 0
    monkeypatch.setattr(LegacyDispatch,"_materialize",lambda self,*args: original(*args))
    result = scheduler.poll(goal,expected_revision=1,idempotency_key="poll")
    assert result["admissions"][0]["disposition"] == "admitted"
    repeated = scheduler.poll(goal,expected_revision=1,idempotency_key="poll")
    assert repeated["admissions"] == result["admissions"]
    with psycopg.connect(database) as connection:
        binding = connection.execute("SELECT cycle_id,job_id FROM v4_legacy_dispatches WHERE goal_id=%s",(goal,)).fetchall()
        assert len(binding)==1
    with pytest.raises(ValueError,match="unresolved"):
        scheduler.rollback(goal,idempotency_key="rollback")


def test_signed_legacy_api_commits_without_temporal_and_denies_old_unbound_body(legacy,monkeypatch,capsys):
    from cryptography.hazmat.primitives.asymmetric import rsa
    from datetime import timedelta
    from fastapi.testclient import TestClient
    import jwt
    from salience.api.p0 import create_p0_app

    bridge, command, service, database = legacy
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    app=create_p0_app(database_url=database,workspace_id=service.workspace_id,issuer="https://fixture.invalid",audience="fixture",
        public_key=key.public_key(),enable_legacy_dispatch=True,legacy_fixture_queue=bridge.task_queue)
    now=datetime.now(timezone.utc)
    token=jwt.encode({"iss":"https://fixture.invalid","aud":"fixture","sub":str(service.subject_id),"iat":now,"nbf":now,"exp":now+timedelta(minutes=5)},key,algorithm="RS256")
    headers={"Authorization":"Bearer "+token,"X-Salience-Scopes":"admin:*,control:write"}
    with TestClient(app) as client:
        first=client.post("/v1/intelligence/runs",headers=headers,json=command.model_dump(mode="json"))
        assert first.status_code==202, first.text
        assert client.post("/v1/intelligence/runs",headers=headers,json=command.model_dump(mode="json")).json()==first.json()
        job=first.json()["job_id"]
        import httpx,json
        from salience.sdk.client import SalienceClient
        from salience.cli import main
        monkeypatch.setattr(httpx,"request",lambda method,url,**kwargs:client.request(method,url,**{k:v for k,v in kwargs.items() if k!="timeout"}))
        sdk=SalienceClient("http://testserver",token).legacy_intelligence
        assert sdk.submit(command.model_dump(mode="json"))==first.json()
        assert sdk.inspect(job)["state"]=="queued"
        monkeypatch.setenv("SALIENCE_CONTROL_JWT",token)
        monkeypatch.setenv("SALIENCE_CONTROL_URL","http://testserver")
        main(["legacy-intelligence","submit","--command-json",json.dumps(command.model_dump(mode="json"))])
        assert json.loads(capsys.readouterr().out)["job_id"]==job
        assert client.get("/v1/intelligence/runs/"+job,headers=headers).json()["state"]=="queued"
        assert client.post("/v1/intelligence/runs",headers=headers,json={"workspace_id":str(service.workspace_id),"content_program_id":str(uuid4()),"niche":"Fixture","idempotency_key":"old"}).status_code==422
        assert client.post("/v1/intelligence/runs/"+job+"/cancel",headers=headers,json={"reason":"Cancel before dispatch"}).status_code==200
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT kind FROM v4_cycle_outbox WHERE cycle_id=%s ORDER BY sequence",(first.json()["cycle_id"],)).fetchall()==[("start",),("close",)]


@pytest.mark.asyncio
async def test_actual_temporal_schedule_pause_and_rollback_watermark(policy, monkeypatch):
    import asyncio
    from datetime import timedelta
    from psycopg.types.json import Jsonb
    from temporalio.client import Client,Schedule,ScheduleActionStartWorkflow,ScheduleSpec,ScheduleIntervalSpec
    from salience.cycles.contracts import CadencePolicy
    from salience.cycles.schedule_cutover import CycleScheduleCutover
    from salience.cycles.temporal_schedule_control import TemporalFixtureLegacyScheduleControl
    from salience.cycles.legacy_dispatch import LegacyDispatch
    from test_v4_cycle_admission import approved_goal

    monkeypatch.setenv("SALIENCE_DEPLOYMENT_MODE","fixture")
    service, _, old_spec, database = policy
    anchor=datetime.now(timezone.utc).replace(microsecond=0)+timedelta(minutes=10)
    spec=old_spec.model_copy(update={"cadence":CadencePolicy(anchor=anchor,interval_seconds=60)})
    goal=approved_goal(service,spec)
    legacy_id=uuid4()
    remote_id=f"salience-v4-legacy-fixture:{service.workspace_id}:{legacy_id}"
    queue="salience-v4-local-legacy-temporal-"+str(uuid4())
    with psycopg.connect(database) as connection:
        connection.execute("INSERT INTO permission_grants(workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at) VALUES(%s,'identity',%s,'cycles:schedule','allow','{}',now()+interval '1 hour')",(service.workspace_id,str(service.subject_id)))
        connection.execute("INSERT INTO job_schedules(id,workspace_id,content_program_id,name,schedule_expression,timezone,job_type,payload,next_run_at) VALUES(%s,%s,%s,%s,'every:60s','UTC','v4_fixture_legacy',%s,%s)",(legacy_id,service.workspace_id,spec.content_program_id,str(legacy_id),Jsonb({"dry_run":True,"last_slot":(anchor-timedelta(seconds=60)).isoformat(),"temporal_schedule_id":remote_id}),anchor))
    client=await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    payload={"workspace_id":str(service.workspace_id),"content_program_id":str(spec.content_program_id),"niche":"Fixture","idempotency_key":"old-fixture","dry_run":True}
    await client.create_schedule(remote_id,Schedule(action=ScheduleActionStartWorkflow("IntelligenceLoopWorkflow",payload,id=remote_id+":run",task_queue=queue),spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=timedelta(seconds=60),offset=timedelta(seconds=anchor.timestamp()%60))],start_at=anchor)))
    control=TemporalFixtureLegacyScheduleControl(database,workspace_id=service.workspace_id,temporal_target=os.environ["TEST_TEMPORAL_TARGET"],task_queue=queue)
    scheduler=CycleScheduleCutover(database,workspace_id=service.workspace_id,subject_id=service.subject_id,legacy_control=control)
    try:
        await asyncio.to_thread(scheduler.prepare,goal,legacy_schedule_id=legacy_id,expected_revision=1,first_v4_slot=anchor,idempotency_key="prepare")
        await asyncio.to_thread(LegacyDispatch(database, workspace_id=service.workspace_id,
            subject_id=service.subject_id, task_queue=queue).bind_schedule, goal, expected_revision=1, niche="Fixture")
        # A real old schedule execution must drain before V4 activation.
        handle = client.get_schedule_handle(remote_id)
        await handle.trigger()
        async with asyncio.timeout(10):
            while not (description := await handle.describe()).info.running_actions:
                await asyncio.sleep(0.05)
        old_execution = description.info.running_actions[0]
        with pytest.raises(ValueError, match="in-flight legacy actions"):
            await asyncio.to_thread(scheduler.activate, goal, idempotency_key="activate")
        assert (await handle.describe()).schedule.state.paused
        with psycopg.connect(database) as connection:
            assert connection.execute("SELECT state FROM v4_schedule_cutovers WHERE goal_id=%s", (goal,)).fetchone()[0] == "pending"
        await client.get_workflow_handle(old_execution.workflow_id).terminate(reason="Disposable no-effects fixture drain")
        async with asyncio.timeout(10):
            while (await handle.describe()).info.running_actions:
                await asyncio.sleep(0.05)
        active=await asyncio.to_thread(scheduler.activate,goal,idempotency_key="activate")
        assert active["state"]=="active"
        assert (await client.get_schedule_handle(remote_id).describe()).schedule.state.paused
        rolled=await asyncio.to_thread(scheduler.rollback,goal,idempotency_key="rollback")
        assert rolled["state"]=="rolled_back"
        description=await client.get_schedule_handle(remote_id).describe()
        assert not description.schedule.state.paused
        assert description.schedule.spec.start_at > anchor-timedelta(seconds=60)
    finally:
        await client.get_schedule_handle(remote_id).delete()


@pytest.mark.asyncio
async def test_real_legacy_worker_exit_after_start_preserves_unknown_without_new_run(legacy,tmp_path):
    import asyncio,subprocess,sys
    from temporalio.client import Client

    bridge, command, service, database = legacy
    result=bridge.submit(command)
    env=os.environ|{"TEST_DATABASE_URL":database,"V4_FIXTURE_WORKSPACE":str(service.workspace_id),"V4_LEGACY_FIXTURE_QUEUE":bridge.task_queue,"SALIENCE_DEPLOYMENT_MODE":"fixture","SALIENCE_EFFECTS_ENABLED":"false"}
    script="tests/fixtures/legacy_dispatch_process.py"
    with (tmp_path/"crash.log").open("w") as log:
        first=subprocess.Popen([sys.executable,script],env=env|{"LEGACY_FIXTURE_CRASH_AFTER_START":"1"},stdout=log,stderr=subprocess.STDOUT)
        try:
            assert await asyncio.to_thread(first.wait,20)==137
        finally:
            if first.poll() is None:
                first.kill();await asyncio.to_thread(first.wait)
    client=await Client.connect(env["TEST_TEMPORAL_TARGET"])
    handle=client.get_workflow_handle("salience-v4-legacy-operation:"+result["operation_id"])
    original=(await handle.describe()).run_id
    with (tmp_path/"restart.log").open("w") as log:
        second=subprocess.Popen([sys.executable,script],env=env,stdout=log,stderr=subprocess.STDOUT)
        try:
            async with asyncio.timeout(20):
                while bridge.inspect(result["job_id"])["dispatch_state"] != "unknown":
                    await asyncio.sleep(.05)
            assert (await handle.describe()).run_id == original
            with psycopg.connect(database) as connection:
                assert connection.execute("SELECT count(*) FROM v4_permit_claims WHERE cycle_id=%s",(result["cycle_id"],)).fetchone()[0]==1
                assert connection.execute("SELECT count(*) FROM jobs WHERE id=%s",(result["job_id"],)).fetchone()[0]==1
                assert connection.execute("SELECT count(*) FROM v4_cycle_events WHERE cycle_id=%s AND kind='fixture_adapter_accepted'",(result["cycle_id"],)).fetchone()[0]==0
        finally:
            second.terminate()
            try:
                await asyncio.to_thread(second.wait,5)
            except subprocess.TimeoutExpired:
                second.kill();await asyncio.to_thread(second.wait)
            from temporalio.client import WorkflowExecutionStatus
            if (await handle.describe()).status == WorkflowExecutionStatus.RUNNING:
                await handle.terminate(reason="End disposable legacy hard-exit fixture")
