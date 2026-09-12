from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID, uuid4

from jsonschema import ValidationError, validate

from salience.agents.registry import AgentRegistry
from salience.governance.trust import TrustContext
from salience.models.contracts import ModelGateway, ModelRequest
from salience.observability.tracing import TraceContext


@dataclass(frozen=True)
class AgentInvocation:
    agent_id: str
    input: dict[str, Any]
    mode: str = "sync"


@dataclass(frozen=True)
class AgentExecutionContext:
    trace_context: TraceContext
    parent_run_id: UUID | None = None
    delegated_authority: frozenset[str] = frozenset()
    tool_scopes: frozenset[str] = frozenset()
    memory_scopes: frozenset[str] = frozenset()
    trust_context: TrustContext | None = None


@dataclass(frozen=True)
class AgentRun:
    id: UUID
    agent_id: str
    request: AgentInvocation
    output: dict[str, Any]
    result_schema: dict[str, Any]
    parent_run_id: UUID | None
    status: str
    trace_context: TraceContext
    runtime_id: str | None = None
    events: tuple[str, ...] = ()
    execution_context: AgentExecutionContext | None = None


class AgentRuntime(Protocol):
    async def invoke(
        self, invocation: AgentInvocation, context: AgentExecutionContext
    ) -> dict[str, Any]: ...


class AgentService:
    def __init__(
        self,
        *,
        registry: AgentRegistry,
        runtimes: dict[str, AgentRuntime],
        model_gateways: dict[str, ModelGateway] | None = None,
    ) -> None:
        self._registry = registry
        self._runtimes = runtimes
        self._model_gateways = model_gateways or {}
        self._runs: dict[UUID, AgentRun] = {}

    async def invoke(self, invocation: AgentInvocation) -> AgentRun:
        return await self._invoke(
            invocation,
            parent_run_id=None,
            trace_context=None,
            runtime_id=None,
            parent_execution_context=None,
        )

    async def invoke_by_id(self, agent_id: str, input: dict[str, Any]) -> AgentRun:
        return await self.invoke(AgentInvocation(agent_id=agent_id, input=input))

    async def invoke_with_runtime(
        self, agent_id: str, runtime_id: str, input: dict[str, Any]
    ) -> AgentRun:
        gateway = self._model_gateways.get(runtime_id)
        if gateway is None:
            raise LookupError(f"model runtime unavailable: {runtime_id}")
        trace_context = TraceContext.new_root()
        await gateway.complete(
            ModelRequest(
                prompt=str(input),
                output_schema={"type": "object"},
                capability=f"agent.invoke.{agent_id}",
                trace_context=trace_context,
            )
        )
        return await self._invoke(
            AgentInvocation(agent_id=agent_id, input=input),
            parent_run_id=None,
            trace_context=trace_context,
            runtime_id=runtime_id,
            parent_execution_context=None,
        )

    async def invoke_from_parent(
        self, parent_agent_id: str, invocation: AgentInvocation
    ) -> AgentRun:
        parent_manifest = self._registry.resolve(parent_agent_id)
        parent_run_id = uuid4()
        parent_trace = TraceContext.new_root()
        self._runs[parent_run_id] = AgentRun(
            id=parent_run_id,
            agent_id=parent_manifest.agent_id,
            request=AgentInvocation(parent_agent_id, invocation.input),
            output={},
            result_schema=parent_manifest.output_schema,
            parent_run_id=None,
            status="delegating",
            trace_context=parent_trace,
            events=("agent.delegated",),
            execution_context=AgentExecutionContext(
                trace_context=parent_trace,
                delegated_authority=frozenset(parent_manifest.delegated_authority_scopes),
                tool_scopes=frozenset(parent_manifest.tool_scopes),
                memory_scopes=frozenset(parent_manifest.memory_scopes),
                trust_context=TrustContext.internal_memory_writer(
                    f"agent://{parent_manifest.agent_id}",
                    scopes=frozenset(parent_manifest.memory_scopes),
                ),
            ),
        )
        return await self._invoke(
            invocation,
            parent_run_id=parent_run_id,
            trace_context=parent_trace.new_child(),
            runtime_id=None,
            parent_execution_context=self._runs[parent_run_id].execution_context,
        )

    async def _invoke(
        self,
        invocation: AgentInvocation,
        *,
        parent_run_id: UUID | None,
        trace_context: TraceContext | None,
        runtime_id: str | None,
        parent_execution_context: AgentExecutionContext | None,
    ) -> AgentRun:
        manifest = self._registry.resolve(invocation.agent_id)
        if invocation.mode == "sync" and not manifest.supports_sync:
            raise ValueError("agent does not support synchronous invocation")
        if invocation.mode == "async" and not manifest.supports_async:
            raise ValueError("agent does not support asynchronous invocation")
        self._validate(manifest.input_schema, invocation.input, "input")
        runtime = self._runtimes.get(manifest.agent_id)
        if runtime is None:
            raise LookupError(f"runtime unavailable for {manifest.agent_id}")
        manifest_tool_scopes = frozenset(manifest.tool_scopes)
        manifest_memory_scopes = frozenset(manifest.memory_scopes)
        manifest_delegated_authority = frozenset(manifest.delegated_authority_scopes)
        if parent_execution_context is not None:
            manifest_tool_scopes &= parent_execution_context.tool_scopes
            manifest_memory_scopes &= parent_execution_context.memory_scopes
            manifest_delegated_authority &= parent_execution_context.delegated_authority
        context = AgentExecutionContext(
            trace_context=trace_context or TraceContext.new_root(),
            parent_run_id=parent_run_id,
            delegated_authority=manifest_delegated_authority,
            tool_scopes=manifest_tool_scopes,
            memory_scopes=manifest_memory_scopes,
            trust_context=TrustContext.internal_memory_writer(
                f"agent://{manifest.agent_id}", scopes=manifest_memory_scopes
            ),
        )
        output = await runtime.invoke(invocation, context)
        self._validate(manifest.output_schema, output, "output")
        run = AgentRun(
            id=uuid4(),
            agent_id=manifest.agent_id,
            request=invocation,
            output=output,
            result_schema=manifest.output_schema,
            parent_run_id=parent_run_id,
            status="succeeded",
            trace_context=context.trace_context,
            runtime_id=runtime_id,
            events=("agent.started", "agent.succeeded"),
            execution_context=context,
        )
        self._runs[run.id] = run
        return run

    @staticmethod
    def _validate(schema: dict[str, Any], value: dict[str, Any], label: str) -> None:
        try:
            validate(value, schema)
        except ValidationError as error:
            raise ValueError(f"{label} schema validation failed: {error.message}") from error

    def get_run(self, run_id: UUID) -> AgentRun | None:
        return self._runs.get(run_id)

    def list_agents(self):
        return self._registry.list()

    def describe_agent(self, agent_id: str):
        return self._registry.describe(agent_id)
