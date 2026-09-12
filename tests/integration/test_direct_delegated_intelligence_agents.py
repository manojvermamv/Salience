import pytest

from salience.agents.execution import AgentInvocation
from salience.agents.fixtures import fixture_agent_service


@pytest.mark.asyncio
async def test_research_agent_direct_and_delegated_outputs_match_schema() -> None:
    service = fixture_agent_service()

    direct = await service.invoke(
        AgentInvocation("research_agent", {"niche": "home gardening"})
    )
    delegated = await service.invoke_from_parent(
        "lead_content_agent",
        AgentInvocation("research_agent", {"niche": "home gardening"}),
    )

    assert direct.output.keys() == delegated.output.keys()
    assert delegated.parent_run_id is not None
    assert delegated.output["contract_version"] == "ResearchResult@v1"
