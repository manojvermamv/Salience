import pytest

from salience.agents.execution import AgentInvocation
from salience.agents.fixtures import fixture_agent_service


@pytest.mark.asyncio
async def test_intelligence_agents_emit_versioned_provider_neutral_contracts() -> None:
    service = fixture_agent_service()

    research = await service.invoke(
        AgentInvocation("research_agent", {"niche": "home gardening"})
    )
    strategy = await service.invoke(
        AgentInvocation("strategy_agent", {"niche": "home gardening"})
    )
    browser = await service.invoke(
        AgentInvocation("browser_research_agent", {"url": "https://example.test/article"})
    )

    assert research.output["contract_version"] == "ResearchResult@v1"
    assert {"findings", "contradictions", "unresolved_questions"} <= research.output.keys()
    assert strategy.output["contract_version"] == "StrategyProposal@v1"
    assert {"audience", "positioning", "topic_priorities", "uncertainties"} <= strategy.output.keys()
    assert browser.output["contract_version"] == "BrowserResearchResult@v1"
    assert browser.output["effect_classification"] == "read"
