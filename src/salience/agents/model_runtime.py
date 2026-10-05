"""Typed model output as an agent result, without implicit runtime fallback."""
import json

from salience.models.contracts import ModelRequest


class ModelAgentRuntime:
    def __init__(self, *, gateway, output_schema, zero_cost_fixture=False):
        # Operator-owned injection only; neither team plans nor model outputs set this.
        if zero_cost_fixture is not True:
            raise PermissionError('paid/live model runtime qualification remains held')
        self.gateway = gateway
        self.output_schema = output_schema

    async def invoke(self, invocation, context):
        result = await self.gateway.complete(ModelRequest(
            prompt=json.dumps(invocation.input, sort_keys=True, allow_nan=False),
            output_schema=self.output_schema,
            capability=f'agent.invoke.{invocation.agent_id}',
            parent_agent_run_id=context.run_id,
            trace_context=context.trace_context,
            metadata={'workspace_id': str(context.workspace_id),
                      'team_parent_run_id': str(context.parent_run_id)},
        ))
        if result.actual_cost_micros != 0:
            raise PermissionError('zero spend contract violated')
        return result.output
