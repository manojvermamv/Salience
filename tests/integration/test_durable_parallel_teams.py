"""Durable concurrency, authority and signed command acceptance against PostgreSQL."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import UUID, uuid4

from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
import httpx
import jwt
import psycopg
import pytest

from salience.agents.contracts import AgentManifest
from salience.agents.execution import AgentService
from salience.agents.model_runtime import ModelAgentRuntime
from salience.agents.parallel_contracts import ParallelTeamRequest
from salience.agents.parallel_store import ParallelTeamStore
from salience.agents.parallel_worker import ParallelTeamWorker
from salience.agents.registry import AgentRegistry
from salience.api.p0 import create_p0_app
from salience.cli import main
from salience.models.openai_compatible import OpenAICompatibleAdapter
from salience.models.recording import RecordedModelGateway, PostgreSQLModelInvocationRepository
from salience.sdk.client import SalienceClient

@pytest.fixture
def setup_team(monkeypatch):
    monkeypatch.setenv('SALIENCE_DEPLOYMENT_MODE','fixture')
    monkeypatch.setenv('SALIENCE_EFFECTS_ENABLED','false')
    db=os.environ['TEST_DATABASE_URL'];workspace=uuid4();actor=uuid4()
    with psycopg.connect(db) as c:
        c.execute("INSERT INTO workspaces(id,slug,display_name) VALUES(%s,%s,'parallel fixture')",(workspace,str(workspace)))
        c.execute("INSERT INTO identity_subjects(id,workspace_id,issuer,subject,expires_at) VALUES(%s,%s,'https://fixture.invalid',%s,now()+interval '1 hour')",(actor,workspace,str(actor)))
        for scope in ('agents:delegate','agents:read','demo:read'):
            c.execute("INSERT INTO permission_grants(workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at) VALUES(%s,'identity',%s,%s,'allow','{}',now()+interval '1 hour')",(workspace,str(actor),scope))
    registry=AgentRegistry(); name='parallel_'+uuid4().hex
    registry.register(AgentManifest(agent_id=name,version='1.0.0',input_schema={'type':'object'},output_schema={'type':'object'},tool_scopes=['demo:read'],memory_scopes=[],effect_classification='read',supports_sync=True,supports_async=True,timeout_seconds=5))
    class Runtime:
        async def invoke(self, invocation, context):return {'value':invocation.input.get('value')}
    service=AgentService(registry=registry,runtimes={name:Runtime()})
    store=ParallelTeamStore(db,workspace_id=workspace,service=service)
    plan=ParallelTeamRequest(team_id='fixture',lead_agent_id=name,tasks=tuple({'key':str(i),'agent_id':name,'input':{'value':i}} for i in range(4)),max_concurrency=2,timeout_seconds=20)
    return db,workspace,actor,registry,service,store,plan

async def drain(store,actor,run_id,workers=1):
    stop=asyncio.Event();tasks=[asyncio.create_task(ParallelTeamWorker(store).run(stop)) for _ in range(workers)]
    try:
        async with asyncio.timeout(8):
            while True:
                result=await asyncio.to_thread(store.inspect,actor,run_id)
                if result['status'] not in {'queued','running'}:return result
                for task in tasks:
                    if task.done():task.result()
                await asyncio.sleep(.02)
    finally:
        stop.set();await asyncio.gather(*tasks)

def test_concurrent_submission_and_immutable_sql_history(setup_team):
    db,workspace,actor,registry,service,store,plan=setup_team
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(lambda _:store.submit(actor,plan,'same'),range(2)))
    assert results[0]==results[1]
    with pytest.raises(ValueError):store.submit(actor,plan.model_copy(update={'max_concurrency':1}),'same')
    task=store.claim();assert task
    assert store.finish(task,state='succeeded',output={'saved':True})
    assert not store.finish(task,state='succeeded',output={'saved':False})
    for sql,args in [
        ("UPDATE parallel_agent_tasks SET state='queued' WHERE id=%s",(task['id'],)),
        ("UPDATE agent_runs SET input_payload='{}' WHERE id=%s",(task['id'],)),
        ("UPDATE agent_runs SET output_payload='{}' WHERE id=%s",(task['id'],)),
        ("DELETE FROM agent_events WHERE agent_run_id=%s",(task['id'],)),
        ("UPDATE parallel_agent_teams SET max_concurrency=8 WHERE id=%s",(task['team_run_id'],)),
    ]:
        with psycopg.connect(db) as c,pytest.raises(psycopg.Error):c.execute(sql,args)

@pytest.mark.asyncio
@pytest.mark.parametrize('team_cap,workspace_cap', [(2,3),(3,2)])
async def test_parallel_model_outputs_lineage_and_database_caps(setup_team,team_cap,workspace_cap):
    db,workspace,actor,registry,service,store,plan=setup_team
    plan=plan.model_copy(update={'max_concurrency':team_cap})
    entered=0;peak=0;active=0;barrier=asyncio.Event();contexts=[]
    async def respond(request):
        nonlocal entered,peak,active
        entered+=1;active+=1;peak=max(peak,active)
        if entered%2==0:barrier.set()
        await asyncio.wait_for(barrier.wait(),1)
        data=json.loads(request.content);value=json.loads(data['messages'][0]['content'])['value']
        await asyncio.sleep(.05);active-=1
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'generated':value})}}],'usage':{'prompt_tokens':2,'completion_tokens':3}})
    adapter=OpenAICompatibleAdapter(runtime_id='local-mock',base_url='http://fixture.invalid/v1',model='fixture',api_key='fixture-only',http_client_factory=lambda:httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    gateway=RecordedModelGateway(gateway=adapter,repository=PostgreSQLModelInvocationRepository(db))
    class Runtime(ModelAgentRuntime):
        async def invoke(self,invocation,context):
            contexts.append(context)
            assert context.tool_scopes==frozenset({'demo:read'})
            assert context.workspace_id==workspace
            return await super().invoke(invocation,context)
    service._runtimes[plan.lead_agent_id]=Runtime(gateway=gateway,output_schema={'type':'object'},zero_cost_fixture=True)
    result=store.submit(actor,plan,'models')
    with store.connect() as c:c.execute('UPDATE parallel_agent_limits SET max_running=%s WHERE workspace_id=%s',(workspace_cap,workspace))
    done=await drain(store,actor,result['run_id'],workers=2)
    assert peak==2 and entered==4
    assert [t['output'] for t in done['tasks']]==[{'generated':i} for i in range(4)]
    assert len({c.parent_run_id for c in contexts})==1 and len({c.trace_context.trace_id for c in contexts})==1
    with store.connect() as c:
        records=c.execute('SELECT * FROM model_invocations WHERE parent_agent_run_id=ANY(%s)',([UUID(t['run_id']) for t in done['tasks']],)).fetchall()
        assert len(records)==4 and all(r['actual_cost_micros']==0 and r['usage']['output_tokens']==3 for r in records)
        parent=c.execute('SELECT * FROM agent_runs WHERE id=%s',(result['run_id'],)).fetchone()
        assert parent['state']=='succeeded' and parent['output_payload']['tasks']==done['tasks']

@pytest.mark.asyncio
async def test_mixed_results_revocation_and_cancellation(setup_team):
    db,workspace,actor,registry,service,store,plan=setup_team
    class Runtime:
        async def invoke(self,invocation,context):
            if invocation.input['value']==1:raise RuntimeError('private provider detail')
            return {'result':invocation.input['value']}
    service._runtimes[plan.lead_agent_id]=Runtime()
    run=store.submit(actor,plan,'mixed');done=await drain(store,actor,run['run_id'])
    assert done['status']=='partial' and done['tasks'][1]['error_type']=='RuntimeError'
    run=store.submit(actor,plan,'cancel');task=store.claim();cancelled=store.cancel(actor,run['run_id'])
    assert cancelled['status']=='cancelled'
    assert [t['status'] for t in cancelled['tasks']]==['unknown','cancelled','cancelled','cancelled']
    assert not store.finish(task,state='succeeded',output={'late':True})
    run=store.submit(actor,plan,'revoke')
    with store.connect() as c:c.execute("UPDATE permission_grants SET effect='deny' WHERE workspace_id=%s AND scope='agents:delegate'",(workspace,))
    assert store.claim() is None
    assert all(t['status']=='held' for t in store.inspect(actor,run['run_id'])['tasks'])
    with pytest.raises(PermissionError):store.submit(actor,plan,'after-revoke')

def test_signed_api_sdk_cli_and_opt_in(setup_team,monkeypatch,capsys):
    db,workspace,actor,registry,service,store,plan=setup_team
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048);now=datetime.now(timezone.utc)
    token=jwt.encode({'iss':'https://fixture.invalid','aud':'salience-p0','sub':str(actor),'iat':now,'nbf':now,'exp':now+timedelta(minutes=5)},key,algorithm='RS256')
    settings=dict(database_url=db,workspace_id=workspace,issuer='https://fixture.invalid',audience='salience-p0',public_key=key.public_key())
    path=f'/v1/workspaces/{workspace}/agent-teams';headers={'Authorization':'Bearer '+token,'Idempotency-Key':'public'}
    with TestClient(create_p0_app(**settings)) as client:
        assert client.post(path,headers=headers,json=plan.model_dump(mode='json')).status_code==403
    with TestClient(create_p0_app(**settings,enable_parallel_agent_teams=True,parallel_agent_service=service)) as client:
        assert client.post(path,json=plan.model_dump(mode='json')).status_code==401
        assert client.post(path.replace(str(workspace),str(uuid4())),headers=headers,json=plan.model_dump(mode='json')).status_code==403
        # Actual SDK and CLI transport their signed command to the ASGI boundary.
        monkeypatch.setattr(httpx,'request',lambda method,url,**kw:client.request(method,url,**{k:v for k,v in kw.items() if k!='timeout'}))
        sdk=SalienceClient('http://testserver',token).agent_teams
        result=sdk.submit(str(workspace),plan.model_dump(mode='json'),idempotency_key='public')
        monkeypatch.setenv('SALIENCE_CONTROL_URL','http://testserver');monkeypatch.setenv('SALIENCE_CONTROL_JWT',token)
        main(['agent-teams','inspect','--workspace-id',str(workspace),'--run-id',result['run_id']])
        assert json.loads(capsys.readouterr().out)['run_id']==result['run_id']
        main(['agent-teams','submit','--workspace-id',str(workspace),'--plan-json',plan.model_dump_json(),'--idempotency-key','public'])
        assert json.loads(capsys.readouterr().out)['run_id']==result['run_id']
        assert sdk.inspect(str(workspace),result['run_id'])['status']=='queued'
        assert sdk.cancel(str(workspace),result['run_id'])['status']=='cancelled'
        main(['agent-teams','cancel','--workspace-id',str(workspace),'--run-id',result['run_id']])
        assert json.loads(capsys.readouterr().out)['status']=='cancelled'
        assert client.get(path+'/'+str(uuid4()),headers=headers).status_code==404
        assert client.post(path,headers=headers,json=plan.model_dump(mode='json')|{'max_concurrency':9}).status_code==422
    monkeypatch.setenv('SALIENCE_DEPLOYMENT_MODE','production')
    with pytest.raises(ValueError):create_p0_app(**settings,enable_parallel_agent_teams=True)

def test_process_death_keeps_completed_and_unknown_and_resumes_queued(setup_team):
    db,workspace,actor,*_=setup_team
    helper=Path(__file__).parents[1]/'fixtures/parallel_agent_process.py'
    spec=importlib.util.spec_from_file_location('parallel_process_fixture',helper);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    store=ParallelTeamStore(db,workspace_id=workspace,service=module.service)
    name='parallel_process_fixture'
    plan=ParallelTeamRequest(team_id='restart',lead_agent_id=name,tasks=tuple({'key':str(i),'agent_id':name,'input':{'value':i,'block':i==1}} for i in range(3)),max_concurrency=1,timeout_seconds=20)
    original=store.submit(actor,plan,'restart');env=os.environ|{'TEST_WORKSPACE_ID':str(workspace),'PYTHONPATH':'src'}
    def until(predicate,process):
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            assert process.poll() is None
            result=store.inspect(actor,original['run_id'])
            if predicate(result):return result
            time.sleep(.02)
        raise AssertionError('worker did not reach expected state')
    first=subprocess.Popen([sys.executable,str(helper)],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    second=None
    def entered_blocked():
        with store.connect() as c:
            return c.execute("SELECT count(*) AS n FROM agent_events WHERE event_type='fixture.entered' AND agent_run_id=%s",(original['tasks'][1]['run_id'],)).fetchone()['n']==1
    try:
        saved=until(lambda r:r['tasks'][0]['status']=='succeeded' and r['tasks'][1]['status']=='running',first)
        # A durable claim precedes runtime entry. Kill only after the blocked call entered.
        until(lambda r:entered_blocked(),first)
        first.kill();first.communicate(timeout=5)
        second=subprocess.Popen([sys.executable,str(helper)],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        done=until(lambda r:r['status']=='partial',second)
        assert [t['status'] for t in done['tasks']]==['succeeded','unknown','succeeded']
        assert done['tasks'][0]==saved['tasks'][0]
        assert [t['run_id'] for t in done['tasks']]==[t['run_id'] for t in original['tasks']]
        with store.connect() as c:
            counts=c.execute("SELECT agent_run_id,count(*) AS n FROM agent_events WHERE event_type='fixture.entered' AND agent_run_id=ANY(%s) GROUP BY agent_run_id",([UUID(t['run_id']) for t in done['tasks']],)).fetchall()
            assert len(counts)==3 and all(row['n']==1 for row in counts)
    finally:
        for process in (first,second):
            if process and process.poll() is None:process.kill();process.communicate(timeout=5)

@pytest.mark.parametrize('change',['registry','canonical','identity','scope'])
def test_queued_claims_require_current_identity_scope_and_pinned_versions(setup_team,change):
    db,workspace,actor,registry,service,store,plan=setup_team
    run=store.submit(actor,plan,'authority')
    if change=='registry':registry.disable(plan.lead_agent_id,'1.0.0')
    else:
        with store.connect() as c:
            if change=='canonical':c.execute("UPDATE agent_versions SET status='disabled' WHERE agent_id=%s",(plan.lead_agent_id,))
            elif change=='identity':c.execute("UPDATE identity_subjects SET expires_at=now()-interval '1 second' WHERE id=%s",(actor,))
            else:c.execute("UPDATE permission_grants SET effect='deny' WHERE workspace_id=%s AND scope='demo:read'",(workspace,))
    assert store.claim() is None
    with store.connect() as c:
        assert all(t['state']=='held' for t in c.execute('SELECT state FROM parallel_agent_tasks WHERE team_run_id=%s',(run['run_id'],)).fetchall())
        assert c.execute('SELECT count(*) AS n FROM agent_events WHERE event_type=\'agent.started\' AND agent_run_id=ANY(%s)',([UUID(t['run_id']) for t in run['tasks']],)).fetchone()['n']==0

@pytest.mark.asyncio
async def test_absolute_deadline_and_output_bounds_are_durable(setup_team):
    db,workspace,actor,registry,service,store,plan=setup_team
    entered=[];drained=[]
    class Blocking:
        async def invoke(self,invocation,context):
            entered.append(context.run_id)
            try:await asyncio.Event().wait()
            finally:drained.append(context.run_id)
    service._runtimes[plan.lead_agent_id]=Blocking()
    plan=plan.model_copy(update={'timeout_seconds':1,'max_concurrency':1})
    run=store.submit(actor,plan,'deadline');result=await drain(store,actor,run['run_id'])
    assert [t['status'] for t in result['tasks']]==['unknown','timed_out','timed_out','timed_out']
    assert entered==drained and len(entered)==1 and store.claim() is None
    class Oversized:
        async def invoke(self,invocation,context):return {'oversized':'x'*66000}
    service._runtimes[plan.lead_agent_id]=Oversized()
    plan=plan.model_copy(update={'timeout_seconds':20})
    run=store.submit(actor,plan,'output-bounds');result=await drain(store,actor,run['run_id'])
    assert all(t['status']=='failed' and t['output'] is None and t['error_type']=='InvalidOrOversizedOutput' for t in result['tasks'])

def test_database_rejects_claim_above_team_cap(setup_team):
    db,workspace,actor,registry,service,store,plan=setup_team
    run=store.submit(actor,plan.model_copy(update={'max_concurrency':1}),'sql-cap');first=store.claim();assert first
    with store.connect() as c,pytest.raises(psycopg.Error,match='parallel concurrency'):
        c.execute("UPDATE parallel_agent_tasks SET state='running',claim_id=gen_random_uuid(),lease_until=clock_timestamp()+interval '1 second' WHERE id=%s",(run['tasks'][1]['run_id'],))


def test_additive_migration_empty_rollback_and_populated_preservation():
    from urllib.parse import urlsplit,urlunsplit
    source=os.environ['TEST_DATABASE_URL'];name='parallel_migration_'+uuid4().hex
    database=urlunsplit(urlsplit(source)._replace(path='/'+name))
    with psycopg.connect(source,autocommit=True) as admin:
        admin.execute(psycopg.sql.SQL('CREATE DATABASE {}').format(psycopg.sql.Identifier(name)))
        try:
            def migrate(direction,target):
                return subprocess.run([sys.executable,'-m','alembic','-x',f'database_url={database}',direction,target],capture_output=True,text=True)
            assert migrate('upgrade','head').returncode==0
            assert migrate('downgrade','0034_runtime_delivery_scope').returncode==0
            assert migrate('upgrade','head').returncode==0
            workspace=uuid4();actor=uuid4()
            with psycopg.connect(database) as c:
                c.execute("INSERT INTO workspaces(id,slug,display_name) VALUES(%s,%s,'preservation')",(workspace,str(workspace)))
                c.execute("INSERT INTO identity_subjects(id,workspace_id,issuer,subject,expires_at) VALUES(%s,%s,'https://fixture.invalid',%s,now()+interval '1 hour')",(actor,workspace,str(actor)))
                c.execute("INSERT INTO permission_grants(workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at) VALUES(%s,'identity',%s,'agents:delegate','allow','{}',now()+interval '1 hour')",(workspace,str(actor)))
            registry=AgentRegistry()
            registry.register(AgentManifest(agent_id='preservation_agent',version='1.0.0',input_schema={'type':'object'},output_schema={'type':'object'},tool_scopes=[],memory_scopes=[],effect_classification='read',supports_sync=True,supports_async=True))
            service=AgentService(registry=registry,runtimes={'preservation_agent':object()})
            store=ParallelTeamStore(database,workspace_id=workspace,service=service)
            plan=ParallelTeamRequest(team_id='preserve',lead_agent_id='preservation_agent',tasks=({'key':'one','agent_id':'preservation_agent','input':{}},))
            run=store.submit(actor,plan,'preserve')
            result=migrate('downgrade','0034_runtime_delivery_scope')
            assert result.returncode!=0 and 'preserve parallel agent execution history' in result.stderr
            with store.connect() as c:assert store._inspect(c,run['run_id'])['tasks']==run['tasks']
            with store.connect() as c:assert c.execute('SELECT version_num FROM alembic_version').fetchone()['version_num']=='0039_legacy_creative_stage'
        finally:
            admin.execute(psycopg.sql.SQL('DROP DATABASE {} WITH (FORCE)').format(psycopg.sql.Identifier(name)))

@pytest.mark.asyncio
async def test_worker_stop_drains_claimed_calls_without_reexecution(setup_team):
    db,workspace,actor,registry,service,store,plan=setup_team
    entered=asyncio.Event();drained=asyncio.Event()
    class Runtime:
        async def invoke(self,invocation,context):
            entered.set()
            try:await asyncio.Event().wait()
            finally:drained.set()
    service._runtimes[plan.lead_agent_id]=Runtime()
    run=store.submit(actor,plan.model_copy(update={'max_concurrency':1}),'stop-worker')
    stop=asyncio.Event();worker=asyncio.create_task(ParallelTeamWorker(store).run(stop))
    await asyncio.wait_for(entered.wait(),3)
    stop.set();await asyncio.wait_for(worker,3)
    assert drained.is_set()
    result=store.inspect(actor,run['run_id'])
    assert result['tasks'][0]['status']=='unknown' and all(t['status']=='queued' for t in result['tasks'][1:])
    # The next claim is a different canonical child.
    assert str(store.claim()['id'])==result['tasks'][1]['run_id']


def test_configured_signed_fixture_api_requires_opt_in(setup_team,monkeypatch,tmp_path):
    from salience.api.app import create_configured_app
    db,workspace,*_=setup_team
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    from cryptography.hazmat.primitives import serialization
    path=tmp_path/'public.pem';path.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))
    for name,value in {'SALIENCE_PARALLEL_AGENT_TEAMS_ENABLED':'true','DATABASE_URL':db,'SALIENCE_WORKSPACE_ID':str(workspace),'SALIENCE_IDENTITY_ISSUER':'https://fixture.invalid','SALIENCE_IDENTITY_AUDIENCE':'salience-p0','SALIENCE_IDENTITY_PUBLIC_KEY_FILE':str(path)}.items():monkeypatch.setenv(name,value)
    app=create_configured_app()
    assert '/v1/workspaces/{workspace_id}/agent-teams' in app.openapi()['paths']
    monkeypatch.setenv('SALIENCE_EFFECTS_ENABLED','true')
    with pytest.raises(ValueError):create_configured_app()


def test_two_processes_overlap_under_shared_workspace_cap(setup_team):
    db,workspace,actor,*_=setup_team
    helper=Path(__file__).parents[1]/'fixtures/parallel_agent_process.py'
    spec=importlib.util.spec_from_file_location('parallel_process_fixture',helper);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    store=ParallelTeamStore(db,workspace_id=workspace,service=module.service);name='parallel_process_fixture'
    plan=ParallelTeamRequest(team_id='replicas',lead_agent_id=name,tasks=tuple({'key':str(i),'agent_id':name,'input':{'value':i,'block':True}} for i in range(4)),max_concurrency=3,timeout_seconds=20)
    run=store.submit(actor,plan,'replicas')
    with store.connect() as c:c.execute('UPDATE parallel_agent_limits SET max_running=2 WHERE workspace_id=%s',(workspace,))
    env=os.environ|{'TEST_WORKSPACE_ID':str(workspace),'TEST_WORKER_SLOTS':'1','PYTHONPATH':'src'}
    processes=[subprocess.Popen([sys.executable,str(helper)],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE) for _ in range(2)]
    try:
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            assert all(p.poll() is None for p in processes)
            with store.connect() as c:
                events=c.execute("SELECT payload FROM agent_events WHERE event_type='fixture.entered' AND agent_run_id=ANY(%s)",([UUID(t['run_id']) for t in run['tasks']],)).fetchall()
            if len(events)==2:break
            time.sleep(.02)
        assert {event['payload']['pid'] for event in events}=={p.pid for p in processes}
        state=store.inspect(actor,run['run_id'])
        assert [t['status'] for t in state['tasks']]==['running','running','queued','queued']
        assert store.claim() is None
    finally:
        for process in processes:
            if process.poll() is None:process.kill()
            process.communicate(timeout=5)
        store.cancel(actor,run['run_id'])


def test_expiry_between_eligibility_and_claim_update_does_not_stop_worker(setup_team,monkeypatch):
    from contextlib import contextmanager
    db,workspace,actor,registry,service,store,plan=setup_team
    run=store.submit(actor,plan.model_copy(update={'timeout_seconds':1}),'claim-expiry-race')
    original_connect=store.connect;delayed=False
    @contextmanager
    def connect():
        with original_connect() as c:
            class Connection:
                def execute(self,sql,*args):
                    nonlocal delayed
                    if not delayed and sql.startswith("UPDATE parallel_agent_tasks SET state='running'"):
                        delayed=True
                        time.sleep(1.1)
                    return c.execute(sql,*args)
                def transaction(self):return c.transaction()
            yield Connection()
    monkeypatch.setattr(store,'connect',connect)
    assert store.claim() is None
    result=store.inspect(actor,run['run_id'])
    assert all(task['status']=='timed_out' for task in result['tasks'])
    with store.connect() as c:
        assert c.execute("SELECT count(*) AS n FROM agent_events WHERE event_type='agent.started' AND agent_run_id=ANY(%s)",([UUID(t['run_id']) for t in result['tasks']],)).fetchone()['n']==0
