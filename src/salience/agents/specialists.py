from typing import Any

from salience.agents.execution import AgentExecutionContext, AgentInvocation, AgentRuntime
from salience.agents.intelligence import (
    BrowserResearchAgentRuntime,
    ResearchAgentRuntime,
    StrategyAgentRuntime,
)
from salience.research.fixtures import FixtureResearchConnector
from salience.research.contracts import ResearchConnector


class FixtureLeadRuntime:
    async def invoke(
        self, invocation: AgentInvocation, context: AgentExecutionContext
    ) -> dict[str, Any]:
        return {"niche": invocation.input["niche"], "source": "fixture"}


def fixture_specialist_runtimes(
    *, research_connector: ResearchConnector | None = None, source_label: str = "fixture"
) -> dict[str, AgentRuntime]:
    return {
        "lead_content_agent": FixtureLeadRuntime(),
        "research_agent": ResearchAgentRuntime(
            connector=research_connector or FixtureResearchConnector(), source_label=source_label
        ),
        "strategy_agent": StrategyAgentRuntime(source_label="fixture"),
        "browser_research_agent": BrowserResearchAgentRuntime(),
    }
