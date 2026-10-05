"""Behavioral acceptance for independent team members running concurrently."""
import asyncio
import pytest
from salience.agents.contracts import AgentManifest
from salience.agents.execution import AgentService
from salience.agents.registry import AgentRegistry
from salience.agents.teams import TeamManifest, TeamRunner


@pytest.mark.asyncio
async def test_independent_team_members_overlap():
    registry = AgentRegistry()
    for name in ('research_agent', 'strategy_agent'):
        registry.register(AgentManifest(agent_id=name,version='1.0.0',input_schema={'type':'object'},output_schema={'type':'object'},tool_scopes=[],memory_scopes=[],effect_classification='read',supports_sync=True,supports_async=True))
    entered=set(); both=asyncio.Event()
    class Runtime:
        async def invoke(self, invocation, context):
            entered.add(invocation.agent_id)
            if len(entered)==2:both.set()
            await asyncio.wait_for(both.wait(), timeout=.2)
            return {'agent': invocation.agent_id}
    runtime=Runtime()
    service=AgentService(registry=registry,runtimes={n:runtime for n in ('research_agent','strategy_agent')})
    result=await TeamRunner(service).invoke(TeamManifest('independent',('research_agent','strategy_agent')), {})
    assert [r.agent_id for r in result]==['research_agent','strategy_agent']
    assert len({r.parent_run_id for r in result})==1 and result[0].parent_run_id is not None
    assert service.get_run(result[0].parent_run_id).status=='succeeded'


def service_for(runtime, *, child_scopes=('demo:read','extra:read'), parent_scopes=('demo:read',)):
    registry=AgentRegistry()
    for name,scopes in (('lead_agent',parent_scopes),('child_agent',child_scopes)):
        registry.register(AgentManifest(agent_id=name,version='1.0.0',input_schema={'type':'object'},output_schema={'type':'object'},tool_scopes=list(scopes),memory_scopes=list(scopes),delegated_authority_scopes=list(scopes),effect_classification='read',supports_sync=True,supports_async=True))
    return AgentService(registry=registry,runtimes={'lead_agent':runtime,'child_agent':runtime})


@pytest.mark.asyncio
async def test_cap_reduced_permissions_and_order():
    active=0;peak=0;entered=0;both=asyncio.Event();contexts=[]
    class Runtime:
        async def invoke(self,invocation,context):
            nonlocal active,peak,entered
            active+=1;entered+=1;peak=max(peak,active);contexts.append(context)
            if entered==2:both.set()
            try:
                await asyncio.wait_for(both.wait(),1)
                await asyncio.sleep(.01)
                return {'ordinal':len(contexts)}
            finally:active-=1
    result=await TeamRunner(service_for(Runtime())).invoke(TeamManifest('cap',('child_agent',)*6,lead_agent_id='lead_agent',max_concurrency=2),{})
    assert peak==2 and active==0 and len(result)==6
    assert len({c.run_id for c in contexts})==6 and len({c.parent_run_id for c in contexts})==1
    assert all(c.tool_scopes==c.memory_scopes==c.delegated_authority==frozenset({'demo:read'}) for c in contexts)
    assert len({r.trace_context.trace_id for r in result})==1 and len({r.trace_context.span_id for r in result})==6


@pytest.mark.asyncio
async def test_timeout_drains_all_children():
    entered=0;drained=0
    class Runtime:
        async def invoke(self,invocation,context):
            nonlocal entered,drained
            entered+=1
            try:await asyncio.Event().wait()
            finally:drained+=1
    with pytest.raises(TimeoutError):
        await TeamRunner(service_for(Runtime())).invoke(TeamManifest('timeout',('child_agent',)*4,lead_agent_id='lead_agent',max_concurrency=2,timeout_seconds=.03),{})
    assert entered==drained==2


@pytest.mark.asyncio
async def test_invalid_member_preflight_calls_no_runtime():
    called=[]
    class Runtime:
        async def invoke(self,invocation,context):called.append(True);return {}
    service=service_for(Runtime())
    with pytest.raises(LookupError):
        await TeamRunner(service).invoke(TeamManifest('invalid',('child_agent','missing_agent'),lead_agent_id='lead_agent'),{})
    assert called==[]


@pytest.mark.parametrize('changes',[
    {'max_concurrency':9},{'max_concurrency':True},{'timeout_seconds':301},
    {'max_spend_micros':1},{'tasks':[]},{'unexpected':True},
    {'tasks':[{'key':'same','agent_id':'agent','input':{}}]*2},
    {'tasks':[{'key':'big','agent_id':'agent','input':{'data':'x'*8192}}]},
    {'tasks':[{'key':'nan','agent_id':'agent','input':{'data':float('nan')}}]},
])
def test_bounded_plan_rejects_untrusted_authority_and_unbounded_values(changes):
    from pydantic import ValidationError
    from salience.agents.parallel_contracts import ParallelTeamRequest
    plan={'team_id':'demo','lead_agent_id':'agent','tasks':[{'key':'one','agent_id':'agent','input':{}}]}
    with pytest.raises(ValidationError):ParallelTeamRequest.model_validate(plan|changes)


def test_model_runtime_never_enables_paid_gateway_implicitly():
    from salience.agents.model_runtime import ModelAgentRuntime
    with pytest.raises(PermissionError):ModelAgentRuntime(gateway=object(),output_schema={})

@pytest.mark.asyncio
async def test_lead_content_intelligence_uses_one_parent():
    from salience.agents.fixtures import fixture_agent_service
    from salience.agents.lead import LeadContentAgent
    result=await LeadContentAgent(fixture_agent_service()).run_intelligence('fixture niche')
    assert result.research.parent_run_id==result.strategy.parent_run_id
    assert result.research.parent_run_id is not None
    assert result.research.trace_context.trace_id==result.strategy.trace_context.trace_id


@pytest.mark.asyncio
async def test_declared_zero_cost_model_cannot_report_paid_output():
    from salience.agents.model_runtime import ModelAgentRuntime
    from salience.models.contracts import ModelResult
    called=[]
    class Gateway:
        async def complete(self,request):
            called.append(request)
            return ModelResult(runtime_id='bad',output={},usage={},latency_ms=0,actual_cost_micros=1)
    runtime=ModelAgentRuntime(gateway=Gateway(),output_schema={},zero_cost_fixture=True)
    with pytest.raises(ExceptionGroup) as failure:
        await TeamRunner(service_for(runtime)).invoke(TeamManifest('cost',('child_agent',),lead_agent_id='lead_agent'),{})
    assert isinstance(failure.value.exceptions[0],PermissionError) and len(called)==1
