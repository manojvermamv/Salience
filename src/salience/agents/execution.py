from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID, uuid4

from jsonschema import ValidationError, validate

from salience.agents.registry import AgentRegistry
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
    events: tuple[str, ...] = ()


class AgentRuntime(Protocol):
    async def invoke(
        self, invocation: AgentInvocation, context: AgentExecutionContext
    ) -> dict[str, Any]: ...


class AgentService:
    def __init__(
        self, *, registry: AgentRegistry, runtimes: dict[str, AgentRuntime]
    ) -> None:
        self._registry = registry
        self._runtimes = runtimes
        self._runs: dict[UUID, AgentRun] = {}

    async def invoke(self, invocation: AgentInvocation) -> AgentRun:
        return await self._invoke(invocation, parent_run_id=None, trace_context=None)

    async def invoke_by_id(self, agent_id: str, input: dict[str, Any]) -> AgentRun:
        return await self.invoke(AgentInvocation(agent_id=agent_id, input=input))

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
        )
        return await self._invoke(
            invocation,
            parent_run_id=parent_run_id,
            trace_context=parent_trace.new_child(),
        )

    async def _invoke(
        self,
        invocation: AgentInvocation,
        *,
        parent_run_id: UUID | None,
        trace_context: TraceContext | None,
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
        context = AgentExecutionContext(
            trace_context=trace_context or TraceContext.new_root(),
            parent_run_id=parent_run_id,
            delegated_authority=frozenset(manifest.delegated_authority_scopes),
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
            events=("agent.started", "agent.succeeded"),
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
