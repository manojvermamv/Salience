from uuid import uuid4

import pytest

from salience.agents.contracts import AgentManifest
from salience.agents.execution import AgentExecutionContext, AgentInvocation, AgentService
from salience.agents.registry import AgentRegistry
from salience.agents.specialists import fixture_specialist_runtimes
from salience.agents.teams import TeamManifest, TeamRunner
from salience.observability.tracing import TraceContext


def build_service() -> AgentService:
    registry = AgentRegistry()
    registry.register(
        AgentManifest(
            agent_id="research_agent",
            version="1.0.0",
            input_schema={"type": "object", "required": ["niche"]},
            output_schema={"type": "object", "required": ["niche", "source"]},
            tool_scopes=[],
            memory_scopes=["evidence"],
            effect_classification="read",
            supports_sync=True,
            supports_async=True,
        )
    )
    return AgentService(registry=registry, runtimes=fixture_specialist_runtimes())


@pytest.mark.asyncio
async def test_agent_execution_rejects_invalid_input_before_runtime() -> None:
    service = build_service()

    with pytest.raises(ValueError, match="input schema"):
        await service.invoke_by_id("research_agent", {"wrong": "shape"})


@pytest.mark.asyncio
async def test_team_members_use_the_same_direct_agent_boundary() -> None:
    service = build_service()

    runs = await TeamRunner(service).invoke(
        TeamManifest(team_id="research-team", members=("research_agent",)),
        {"niche": "finance"},
    )

    assert runs[0].agent_id == "research_agent"
    assert runs[0].output["source"] == "fixture"


class _ContextRecordingRuntime:
    context: AgentExecutionContext | None = None

    async def invoke(
        self, invocation: AgentInvocation, context: AgentExecutionContext
    ) -> dict[str, str]:
        self.context = context
        return {"niche": invocation.input["niche"], "source": "recording"}


@pytest.mark.asyncio
async def test_agent_run_id_is_allocated_before_runtime_invocation() -> None:
    registry = AgentRegistry()
    registry.register(
        AgentManifest(
            agent_id="recording_agent",
            version="1.0.0",
            input_schema={"type": "object", "required": ["niche"]},
            output_schema={"type": "object", "required": ["niche", "source"]},
            tool_scopes=[],
            memory_scopes=[],
            effect_classification="read",
            supports_sync=True,
            supports_async=True,
        )
    )
    runtime = _ContextRecordingRuntime()
    service = AgentService(registry=registry, runtimes={"recording_agent": runtime})

    run = await service.invoke(AgentInvocation("recording_agent", {"niche": "testing"}))

    assert runtime.context is not None
    assert runtime.context.run_id == run.id
    assert run.execution_context is not None
    assert run.execution_context.run_id == run.id


def test_execution_context_accepts_canonical_run_id() -> None:
    run_id = uuid4()
    context = AgentExecutionContext(run_id=run_id, trace_context=TraceContext.new_root())

    assert context.run_id == run_id
