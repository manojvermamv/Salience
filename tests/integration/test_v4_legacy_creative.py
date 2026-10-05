"""Actual dry creative production retains the original cycle and current authority."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
from uuid import uuid4

import psycopg
import pytest
import pytest_asyncio

from test_v4_cycle_admission import cycles, approved_goal
from test_v4_cadence_policy import policy
from test_v4_legacy_dispatch import legacy


async def wait_started(handle):
    from temporalio.service import RPCError, RPCStatusCode
    async with asyncio.timeout(10):
        while True:
            try:
                return await handle.describe()
            except RPCError as error:
                if error.status != RPCStatusCode.NOT_FOUND:
                    raise
                await asyncio.sleep(.05)


@pytest_asyncio.fixture
async def creative(legacy):
    from temporalio.client import Client
    from salience.cycles.contracts import GoalSpecV2
    from salience.cycles.legacy_dispatch import LegacyCreativeCommand
    from salience.cycles.legacy_runtime import build_legacy_worker
    from salience.cycles.outbox import CycleOutbox
    from salience.cycles.runtime import TemporalCycleTransport
    bridge, intelligence, service, database = legacy
    submitted = bridge.submit(intelligence)
    client = await Client.connect(os.environ['TEST_TEMPORAL_TARGET'])
    box = CycleOutbox(database, workspace_id=service.workspace_id, delivery_lane='legacy')
    transport = TemporalCycleTransport(client, task_queue=bridge.task_queue)
    async with build_legacy_worker(client, task_queue=bridge.task_queue, outbox=box):
        assert await box.dispatch_one(transport)
        child = client.get_workflow_handle('salience-v4-legacy-operation:'+submitted['operation_id'])
        await wait_started(child)
        result = await asyncio.wait_for(child.result(),20)
        assert result['state'] == 'completed'
        service.close(submitted['cycle_id'],disposition='completed',reason='Original intelligence brief verified')
        assert await box.dispatch_one(transport)
        await asyncio.wait_for(client.get_workflow_handle(transport.workflow_id(submitted['cycle_id'])).result(),10)
    with psycopg.connect(database) as connection:
        spec = GoalSpecV2.model_validate(connection.execute('SELECT payload FROM v4_goal_revisions WHERE goal_id=%s AND revision=1',(intelligence.goal_id,)).fetchone()[0])
        connection.execute("INSERT INTO permission_grants(workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at) VALUES(%s,'identity',%s,'legacy:creative','allow','{}',now()+interval '1 hour')",(service.workspace_id,str(service.subject_id)))
    # A new explicitly approved stage goal shares the actual program. It does
    # not synthesize another cycle on the original completed intelligence goal.
    # The intelligence assembler correctly excludes unverified external claims.
    # Create a separate, explicitly reviewed synthetic fixture brief; retain
    # the original unverified brief and its provenance unchanged.
    from salience.intelligence.repository import IntelligenceRepository
    from salience.intelligence.contracts import ClaimInput, ContentBriefInput
    repository=IntelligenceRepository(database)
    with psycopg.connect(database) as connection:
        original=connection.execute('SELECT topic_opportunity_id,strategic_package_id,content,claim_ids FROM content_brief_versions WHERE id=%s',(result['content_brief_id'],)).fetchone()
        evidence=connection.execute("SELECT id FROM research_evidence WHERE workspace_id=%s AND content_program_id=%s AND source_uri LIKE 'fixture://%%'",(service.workspace_id,spec.content_program_id)).fetchone()[0]
    assert original[3] == []
    claim=await repository.record_claim(workspace_id=str(service.workspace_id),program_id=str(spec.content_program_id),claim=ClaimInput('creative-reviewed-fixture','Synthetic fixture audience seeks practical guidance.','verified'),trace_id='creative-fixture-review')
    await repository.link_claim_evidence(claim_id=claim,evidence_id=str(evidence),relation='supporting',trace_id='creative-fixture-review')
    reviewed=await repository.record_content_brief(workspace_id=str(service.workspace_id),program_id=str(spec.content_program_id),brief=ContentBriefInput('creative-reviewed-fixture',1,str(original[0]),str(original[1]),[claim],original[2],provenance={'qualification':'explicit synthetic fixture review; no external claim verification','source_brief_id':result['content_brief_id']}),trace_id='creative-fixture-review')
    goal = approved_goal(service,spec)
    command = LegacyCreativeCommand(goal_id=goal,request=intelligence.request.model_copy(update={"idempotency_key":"creative-original"}),brief_id=reviewed,max_variants=3)
    return bridge, command, service, database, client, box, transport


def test_creative_atomic_scope_replay_and_effect_controls(creative,monkeypatch):
    from pydantic import ValidationError
    bridge,command,service,database,*_ = creative
    materialize=bridge._materialize
    def crash(*args):
        materialize(*args)
        raise RuntimeError('Creative before commit')
    monkeypatch.setattr(bridge,'_materialize',crash)
    with pytest.raises(RuntimeError,match='before commit'):bridge.submit_creative(command)
    with psycopg.connect(database) as connection:
        assert connection.execute('SELECT count(*) FROM v4_cycle_intents WHERE goal_id=%s',(command.goal_id,)).fetchone()[0] == 0
    monkeypatch.setattr(bridge,'_materialize',materialize)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results=list(pool.map(lambda _:bridge.submit_creative(command),range(3)))
    assert results[0] == results[1] == results[2]
    with psycopg.connect(database) as connection:
        row=connection.execute("SELECT job.workspace_id,job.content_program_id,job.job_type,job.dry_run,binding.stage,ctx.payload->>'content_program_id' FROM jobs job JOIN v4_legacy_dispatches binding ON binding.job_id=job.id JOIN v4_run_contexts ctx ON ctx.id=binding.context_id WHERE job.id=%s",(results[0]['job_id'],)).fetchone()
        assert row[0] == service.workspace_id and str(row[1]) == row[5]
        assert row[2:5] == ('creative_production',True,'creative')
        assert connection.execute('SELECT count(*) FROM v4_cycle_outbox WHERE cycle_id=%s',(results[0]['cycle_id'],)).fetchone()[0] == 1
    for extra in ({'dry_run':False},{'budget_id':str(uuid4())},{'target_profile_key':'live-platform'},{'max_variants':4},{'max_variants':True}):
        with pytest.raises(ValidationError):type(command).model_validate(command.model_dump()|extra)
    with pytest.raises(ValueError,match='fingerprint conflict'):
        bridge.submit_creative(command.model_copy(update={'max_variants':2}))
    from salience.cycles.legacy_dispatch import LegacyIntelligenceCommand
    with pytest.raises(ValueError,match='workload conflict'):
        bridge.submit(LegacyIntelligenceCommand(goal_id=command.goal_id,niche='Fixture',request=command.request.model_copy(update={'idempotency_key':'mixed-stage'})))
    with pytest.raises(PermissionError,match='outside original'):
        bridge.submit_creative(command.model_copy(update={'brief_id':uuid4(),'request':command.request.model_copy(update={'idempotency_key':'foreign-brief'})}))


@pytest.mark.asyncio
async def test_actual_creative_workflow_original_permit_zero_provider(creative,monkeypatch):
    from salience.creative.providers import FixtureCreativeProvider
    from salience.cycles.legacy_runtime import build_legacy_worker
    bridge,command,service,database,client,box,transport=creative
    async def forbidden(*args,**kwargs):raise AssertionError('No creative provider submission allowed')
    monkeypatch.setattr(FixtureCreativeProvider,'submit',forbidden)
    submitted=bridge.submit_creative(command)
    async with build_legacy_worker(client,task_queue=bridge.task_queue,outbox=box):
        assert await box.dispatch_one(transport)
        child=client.get_workflow_handle('salience-v4-legacy-operation:'+submitted['operation_id'])
        assert (await wait_started(child)).workflow_type == 'CreativeProductionWorkflow'
        result=await asyncio.wait_for(child.result(),20)
        assert result['state'] == 'dry_run_completed' and result['provider_submit_count'] == 0
        assert result['job_id'] == submitted['job_id'] and result['script_id']
        assert result['ready_package_id'] is None and result['asset_id'] is None
        with psycopg.connect(database) as connection:
            assert connection.execute('SELECT count(*) FROM v4_permit_claims WHERE cycle_id=%s',(submitted['cycle_id'],)).fetchone()[0] == 1
            assert connection.execute('SELECT count(*) FROM external_effects WHERE job_id=%s',(submitted['job_id'],)).fetchone()[0] == 0
            assert connection.execute('SELECT workspace_id,content_program_id FROM script_versions WHERE id=%s',(result['script_id'],)).fetchone()[0] == service.workspace_id
        service.close(submitted['cycle_id'],disposition='completed',reason='Original dry creative verified')
        assert await box.dispatch_one(transport)
        await asyncio.wait_for(client.get_workflow_handle(transport.workflow_id(submitted['cycle_id'])).result(),10)
        assert bridge.inspect(submitted['job_id'])['state'] == 'succeeded'


def test_signed_creative_sdk_cli_scope_and_old_controller_denial(creative,monkeypatch,capsys):
    import httpx,jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    from fastapi.testclient import TestClient
    from salience.api.p0 import create_p0_app
    from salience.sdk.client import SalienceClient
    from salience.cli import main
    bridge,command,service,database,*_=creative
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    app=create_p0_app(database_url=database,workspace_id=service.workspace_id,issuer='https://fixture.invalid',audience='fixture',public_key=key.public_key(),enable_legacy_dispatch=True,legacy_fixture_queue=bridge.task_queue)
    async def forbidden(**kwargs):raise AssertionError('Direct creative controller start unreachable')
    monkeypatch.setattr(app.state.control_plane,'start_creative',forbidden)
    now=datetime.now(timezone.utc)
    token=jwt.encode({'iss':'https://fixture.invalid','aud':'fixture','sub':str(service.subject_id),'iat':now,'nbf':now,'exp':now+timedelta(minutes=5)},key,algorithm='RS256')
    with TestClient(app) as client:
        monkeypatch.setattr(httpx,'request',lambda method,url,**kwargs:client.request(method,url,**{k:v for k,v in kwargs.items() if k!='timeout'}))
        sdk=SalienceClient('http://testserver',token).legacy_creative
        first=sdk.submit(command.model_dump(mode='json'))
        monkeypatch.setenv('SALIENCE_CONTROL_JWT',token);monkeypatch.setenv('SALIENCE_CONTROL_URL','http://testserver')
        main(['legacy-creative','submit','--command-json',json.dumps(command.model_dump(mode='json'))])
        assert json.loads(capsys.readouterr().out) == first
        assert sdk.inspect(first['job_id'])['state'] == 'queued'
        headers={'Authorization':'Bearer '+token,'X-Salience-Scopes':'legacy:creative,admin:*'}
        assert client.post('/v1/creative/runs',headers=headers,json={'brief_id':str(command.brief_id),'idempotency_key':'old'}).status_code == 422
        assert client.get('/v1/creative/runs/'+str(uuid4()),headers=headers).status_code == 403
        assert sdk.cancel(first['job_id'],reason='Original creative cancelled before delivery')['cycle_state'] == 'closed'
        with psycopg.connect(database) as connection:
            connection.execute("UPDATE permission_grants SET effect='deny' WHERE principal_id=%s AND scope='legacy:creative'",(str(service.subject_id),))
        assert client.post('/v1/creative/runs',headers=headers,json=command.model_dump(mode='json')).status_code == 403


@pytest.mark.asyncio
async def test_creative_revoked_stage_holds_without_physical_start(creative):
    from temporalio.service import RPCError,RPCStatusCode
    from salience.cycles.legacy_runtime import build_legacy_worker
    bridge,command,service,database,client,box,transport=creative
    submitted=bridge.submit_creative(command)
    with psycopg.connect(database) as connection:
        connection.execute("UPDATE permission_grants SET effect='deny' WHERE principal_id=%s AND scope='legacy:creative'",(str(service.subject_id),))
    async with build_legacy_worker(client,task_queue=bridge.task_queue,outbox=box):
        assert await box.dispatch_one(transport)
        async with asyncio.timeout(10):
            while bridge.inspect(submitted['job_id'])['dispatch_state'] != 'unknown':await asyncio.sleep(.05)
        with pytest.raises(RPCError) as missing:
            await client.get_workflow_handle('salience-v4-legacy-operation:'+submitted['operation_id']).describe()
        assert missing.value.status == RPCStatusCode.NOT_FOUND
        with psycopg.connect(database) as connection:
            assert connection.execute('SELECT count(*) FROM script_versions WHERE workspace_id=%s',(service.workspace_id,)).fetchone()[0] == 0
            assert connection.execute('SELECT count(*) FROM external_effects WHERE job_id=%s',(submitted['job_id'],)).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_creative_ordered_close_physically_cancels_original_run_after_lost_ack(creative,monkeypatch):
    from temporalio import activity
    from temporalio.client import WorkflowHandle,WorkflowExecutionStatus
    from salience.cycles.legacy_runtime import GuardedCreativeActivities,build_legacy_worker
    from salience.workflows.creative import CreativeProductionRequest
    bridge,command,service,database,client,box,transport=creative
    reached,release=asyncio.Event(),asyncio.Event()
    original_load=GuardedCreativeActivities.load_brief
    @activity.defn(name='salience.creative.load_brief')
    async def blocked(self,request:CreativeProductionRequest)->dict:
        result=await original_load(self,request)
        reached.set();await release.wait()
        return result
    monkeypatch.setattr(GuardedCreativeActivities,'load_brief',blocked)
    signal=WorkflowHandle.signal
    seen=[]
    async def lost_ack(handle,*args,**kwargs):
        if not handle.id.startswith('salience-v4-legacy-operation:'):
            return await signal(handle,*args,**kwargs)
        seen.append((handle.id,handle.run_id))
        await signal(handle,*args,**kwargs)
        release.set()
        async with asyncio.timeout(10):
            while (await handle.describe()).status == WorkflowExecutionStatus.RUNNING:await asyncio.sleep(.05)
        raise TimeoutError('Original creative signal accepted acknowledgment lost')
    monkeypatch.setattr(WorkflowHandle,'signal',lost_ack)
    submitted=bridge.submit_creative(command)
    async with build_legacy_worker(client,task_queue=bridge.task_queue,outbox=box):
        try:
            assert await box.dispatch_one(transport)
            await asyncio.wait_for(reached.wait(),10)
            service.close(submitted['cycle_id'],disposition='cancelled',reason='Original creative physical cancellation')
            assert await box.dispatch_one(transport)
            await asyncio.wait_for(client.get_workflow_handle(transport.workflow_id(submitted['cycle_id'])).result(),15)
            child=client.get_workflow_handle('salience-v4-legacy-operation:'+submitted['operation_id'])
            result=await asyncio.wait_for(child.result(),10)
            assert result['state'] == 'cancelled'
            assert bridge.inspect(submitted['job_id'])['state'] == 'cancelled'
            assert (child.id,(await child.describe()).run_id) in seen
            with psycopg.connect(database) as connection:
                assert connection.execute('SELECT kind FROM v4_cycle_outbox WHERE cycle_id=%s ORDER BY sequence',(submitted['cycle_id'],)).fetchall() == [('start',),('close',)]
                assert connection.execute('SELECT count(*) FROM external_effects WHERE job_id=%s',(submitted['job_id'],)).fetchone()[0] == 0
        finally:release.set()


@pytest.mark.asyncio
async def test_creative_forged_native_request_rejected_before_business_read(creative,monkeypatch):
    from hashlib import sha256
    from salience.cycles.governance import CycleGovernance,PermitRequest
    from salience.cycles.legacy_runtime import build_legacy_worker,legacy_execution,binding_for_job
    from salience.workflows.creative import CreativeProductionWorkflow,CreativeProductionRequest
    from salience.intelligence.repository import IntelligenceRepository
    from temporalio.client import WorkflowFailureError
    from dataclasses import replace
    bridge,command,service,database,client,box,_=creative
    submitted=bridge.submit_creative(command)
    governance=CycleGovernance(database,workspace_id=service.workspace_id,subject_id=service.subject_id)
    permit=governance.issue_permit(submitted['cycle_id'],PermitRequest(context_id=submitted['context_id'],operation_id=submitted['operation_id'],expected_goal_revision=1,account_ref='fixture-account',purpose='fixture_execution',effect='fixture.noop',artifact_sha256=sha256(b'fixture').hexdigest(),ttl_seconds=30),idempotency_key='forged-permit')
    assert governance.claim_permit(permit['permit_id'])['dispatch_allowed']
    async def forbidden(*args,**kwargs):raise AssertionError('Forged input must reject before business data reads')
    monkeypatch.setattr(IntelligenceRepository,'exact_content_brief',forbidden)
    expected=legacy_execution(binding_for_job(box,submitted['job_id']))[2]
    forged=replace(expected,dry_run=False,budget_id=str(uuid4()))
    async with build_legacy_worker(client,task_queue=bridge.task_queue,outbox=box):
        child=await client.start_workflow(CreativeProductionWorkflow.run,forged,id='salience-v4-legacy-operation:'+submitted['operation_id'],task_queue=bridge.task_queue,execution_timeout=timedelta(seconds=25))
        with pytest.raises(WorkflowFailureError):await asyncio.wait_for(child.result(),15)
    with psycopg.connect(database) as connection:
        assert connection.execute('SELECT count(*) FROM script_versions WHERE workspace_id=%s',(service.workspace_id,)).fetchone()[0] == 0
        assert connection.execute('SELECT count(*) FROM external_effects WHERE job_id=%s',(submitted['job_id'],)).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_creative_activity_rechecks_stage_before_writer_after_admission(creative,monkeypatch):
    from temporalio import activity
    from temporalio.client import WorkflowFailureError
    from salience.cycles.legacy_runtime import GuardedCreativeActivities,build_legacy_worker
    from salience.workflows.creative import CreativeProductionRequest
    bridge,command,service,database,client,box,transport=creative
    reached,release=asyncio.Event(),asyncio.Event()
    original_load=GuardedCreativeActivities.load_brief
    @activity.defn(name='salience.creative.load_brief')
    async def blocked(self,request:CreativeProductionRequest)->dict:
        result=await original_load(self,request)
        reached.set();await release.wait()
        return result
    monkeypatch.setattr(GuardedCreativeActivities,'load_brief',blocked)
    submitted=bridge.submit_creative(command)
    async with build_legacy_worker(client,task_queue=bridge.task_queue,outbox=box):
        try:
            assert await box.dispatch_one(transport)
            await asyncio.wait_for(reached.wait(),10)
            with psycopg.connect(database) as connection:
                connection.execute("UPDATE permission_grants SET effect='deny' WHERE principal_id=%s AND scope='legacy:creative'",(str(service.subject_id),))
            release.set()
            child=client.get_workflow_handle('salience-v4-legacy-operation:'+submitted['operation_id'])
            with pytest.raises(WorkflowFailureError):await asyncio.wait_for(child.result(),15)
            view=bridge.inspect(submitted['job_id'])
            assert view['state'] == 'running' and view['cycle_state'] == 'runnable'
            with psycopg.connect(database) as connection:
                assert connection.execute('SELECT count(*) FROM script_versions WHERE workspace_id=%s',(service.workspace_id,)).fetchone()[0] == 0
                assert connection.execute('SELECT count(*) FROM external_effects WHERE job_id=%s',(submitted['job_id'],)).fetchone()[0] == 0
                assert connection.execute('SELECT count(*) FROM v4_permit_claims WHERE cycle_id=%s',(submitted['cycle_id'],)).fetchone()[0] == 1
        finally:release.set()


def test_creative_migration_empty_exact_restore_and_populated_refusal(creative):
    import subprocess,sys
    from urllib.parse import urlsplit,urlunsplit
    bridge,command,_,database,*_=creative
    name='legacy_creative_migration_'+uuid4().hex
    fresh=urlunsplit(urlsplit(database)._replace(path='/'+name))
    with psycopg.connect(database,autocommit=True) as admin:
        admin.execute(psycopg.sql.SQL('CREATE DATABASE {}').format(psycopg.sql.Identifier(name)))
        try:
            def migrate(db,direction,target):return subprocess.run([sys.executable,'-m','alembic','-x','database_url='+db,direction,target],capture_output=True,text=True)
            def guard():
                with psycopg.connect(fresh) as connection:return connection.execute("SELECT pg_get_functiondef('v4_guard_legacy_dispatch()'::regprocedure)").fetchone()[0]
            assert migrate(fresh,'upgrade','0038_legacy_dummy_stage').returncode == 0
            original=guard()
            assert migrate(fresh,'upgrade','head').returncode == 0
            assert guard()!=original
            assert migrate(fresh,'downgrade','0038_legacy_dummy_stage').returncode == 0
            assert guard()==original
            with psycopg.connect(database) as connection:
                head_revision=connection.execute('SELECT version_num FROM alembic_version').fetchone()[0]
            submitted=bridge.submit_creative(command)
            result=migrate(database,'downgrade','0038_legacy_dummy_stage')
            assert result.returncode != 0 and 'preserve original legacy creative history' in result.stderr
            with psycopg.connect(database) as connection:
                assert connection.execute('SELECT version_num FROM alembic_version').fetchone()[0] == head_revision
                assert connection.execute('SELECT stage FROM v4_legacy_dispatches WHERE job_id=%s',(submitted['job_id'],)).fetchone()[0] == 'creative'
        finally:admin.execute(psycopg.sql.SQL('DROP DATABASE {} WITH (FORCE)').format(psycopg.sql.Identifier(name)))


@pytest.mark.asyncio
@pytest.mark.parametrize('case',['wrong-stage','wrong-scope'])
async def test_shared_worker_pins_intelligence_stage_and_request_before_fetch(legacy,monkeypatch,case):
    from hashlib import sha256
    from dataclasses import replace
    from temporalio.client import Client,WorkflowFailureError
    from salience.cycles.governance import CycleGovernance,PermitRequest
    from salience.cycles.legacy_dispatch import LegacyDummyCommand
    from salience.cycles.legacy_runtime import build_legacy_worker,binding_for_job,legacy_execution
    from salience.cycles.outbox import CycleOutbox
    from salience.workflows.intelligence import IntelligenceLoopWorkflow,IntelligenceLoopRequest,IntelligenceActivities
    bridge,command,service,database=legacy
    if case == 'wrong-stage':
        with psycopg.connect(database) as connection:
            connection.execute("INSERT INTO permission_grants(workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at) VALUES(%s,'identity',%s,'legacy:dummy','allow','{}',now()+interval '1 hour')",(service.workspace_id,str(service.subject_id)))
        submitted=bridge.submit_dummy(LegacyDummyCommand(goal_id=command.goal_id,request=command.request))
    else:submitted=bridge.submit(command)
    governance=CycleGovernance(database,workspace_id=service.workspace_id,subject_id=service.subject_id)
    permit=governance.issue_permit(submitted['cycle_id'],PermitRequest(context_id=submitted['context_id'],operation_id=submitted['operation_id'],expected_goal_revision=1,account_ref='fixture-account',purpose='fixture_execution',effect='fixture.noop',artifact_sha256=sha256(b'fixture').hexdigest(),ttl_seconds=30),idempotency_key='pinned-stage-permit')
    assert governance.claim_permit(permit['permit_id'])['dispatch_allowed']
    box=CycleOutbox(database,workspace_id=service.workspace_id,delivery_lane='legacy')
    binding=binding_for_job(box,submitted['job_id'])
    if case=='wrong-stage':
        forged=IntelligenceLoopRequest(workspace_id=str(service.workspace_id),content_program_id=str(binding['content_program_id']),niche='Fixture',idempotency_key='v4-legacy:'+submitted['operation_id'])
    else:forged=replace(legacy_execution(binding)[2],workspace_id=str(uuid4()),content_program_id=str(uuid4()),dry_run=False)
    async def forbidden(*args,**kwargs):raise AssertionError('Forged stage or request reached business fetch')
    monkeypatch.setattr(IntelligenceActivities,'fetch',forbidden)
    client=await Client.connect(os.environ['TEST_TEMPORAL_TARGET'])
    async with build_legacy_worker(client,task_queue=bridge.task_queue,outbox=box):
        child=await client.start_workflow(IntelligenceLoopWorkflow.run,forged,id='salience-v4-legacy-operation:'+submitted['operation_id'],task_queue=bridge.task_queue,execution_timeout=timedelta(seconds=25))
        with pytest.raises(WorkflowFailureError) as denied:await asyncio.wait_for(child.result(),15)
        assert 'original canonical intelligence stage required' in str(denied.value.cause.cause) if case=='wrong-stage' else 'original fixed no-send intelligence payload required' in str(denied.value.cause.cause)
    with psycopg.connect(database) as connection:
        assert connection.execute('SELECT count(*) FROM agent_runs WHERE job_id=%s',(submitted['job_id'],)).fetchone()[0] == 0
        assert connection.execute('SELECT count(*) FROM external_effects WHERE job_id=%s',(submitted['job_id'],)).fetchone()[0] == 0
        assert connection.execute('SELECT operation_id,context_id FROM v4_cycles WHERE id=%s',(submitted['cycle_id'],)).fetchone() == (binding['operation_id'],binding['context_id'])
