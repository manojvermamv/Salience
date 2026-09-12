import pytest

from salience.agents.contracts import AgentManifest
from salience.agents.execution import AgentInvocation, AgentService
from salience.agents.registry import AgentRegistry
from salience.agents.specialists import fixture_specialist_runtimes


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
    registry.register(
        AgentManifest(
            agent_id="lead_content_agent",
            version="1.0.0",
            input_schema={"type": "object", "required": ["niche"]},
            output_schema={"type": "object"},
            tool_scopes=[],
            memory_scopes=[],
            effect_classification="read",
            supports_sync=True,
            supports_async=True,
        )
    )
    return AgentService(registry=registry, runtimes=fixture_specialist_runtimes())


@pytest.mark.asyncio
async def test_lead_uses_same_research_contract_as_direct_user() -> None:
    service = build_service()
    direct = await service.invoke(
        AgentInvocation(agent_id="research_agent", input={"niche": "finance"})
    )
    delegated = await service.invoke_from_parent(
        "lead_content_agent", direct.request
    )

    assert direct.result_schema == delegated.result_schema
    assert delegated.parent_run_id is not None
    assert delegated.output["source"] == "fixture"
