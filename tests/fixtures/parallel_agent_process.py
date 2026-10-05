"""Subprocess acceptance runtime; this never calls a provider."""
import asyncio
import os
import psycopg
from psycopg.types.json import Jsonb
from salience.agents.contracts import AgentManifest
from salience.agents.execution import AgentService
from salience.agents.parallel_store import ParallelTeamStore
from salience.agents.parallel_worker import ParallelTeamWorker
from salience.agents.registry import AgentRegistry

class Runtime:
    async def invoke(self, invocation, context):
        with psycopg.connect(os.environ['TEST_DATABASE_URL']) as c:
            c.execute("INSERT INTO agent_events(agent_run_id,event_type,payload,trace_id,span_id) VALUES(%s,'fixture.entered',%s,%s,%s)", (context.run_id,Jsonb({'pid':os.getpid()}),context.trace_context.trace_id,context.trace_context.span_id))
        if invocation.input.get('block'):
            await asyncio.Event().wait()
        return {'result':invocation.input['value']}

registry=AgentRegistry()
registry.register(AgentManifest(agent_id='parallel_process_fixture',version='1.0.1',input_schema={'type':'object'},output_schema={'type':'object'},tool_scopes=[],memory_scopes=[],effect_classification='read',supports_sync=True,supports_async=True,timeout_seconds=5))
service=AgentService(registry=registry,runtimes={'parallel_process_fixture':Runtime()})
if __name__=='__main__':
    store=ParallelTeamStore(os.environ['TEST_DATABASE_URL'],workspace_id=os.environ['TEST_WORKSPACE_ID'],service=service)
    asyncio.run(ParallelTeamWorker(store,slots=int(os.environ.get('TEST_WORKER_SLOTS','2'))).run(asyncio.Event()))
