import pytest

from salience.agents.contracts import AgentManifest
from salience.agents.execution import AgentService
from salience.agents.registry import AgentRegistry
from salience.agents.specialists import fixture_specialist_runtimes
from salience.agents.teams import TeamManifest, TeamRunner


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
