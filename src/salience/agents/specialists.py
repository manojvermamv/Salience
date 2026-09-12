from typing import Any

from salience.agents.execution import AgentExecutionContext, AgentInvocation, AgentRuntime


class FixtureResearchRuntime:
    async def invoke(
        self, invocation: AgentInvocation, context: AgentExecutionContext
    ) -> dict[str, Any]:
        return {"niche": invocation.input["niche"], "source": "fixture"}


class FixtureLeadRuntime:
    async def invoke(
        self, invocation: AgentInvocation, context: AgentExecutionContext
    ) -> dict[str, Any]:
        return {"niche": invocation.input["niche"], "source": "fixture"}


class FixtureStrategyRuntime:
    async def invoke(
        self, invocation: AgentInvocation, context: AgentExecutionContext
    ) -> dict[str, Any]:
        return {
            "niche": invocation.input["niche"],
            "content_pillars": ["education", "decision support"],
            "source": "fixture",
        }


def fixture_specialist_runtimes() -> dict[str, AgentRuntime]:
    return {
        "lead_content_agent": FixtureLeadRuntime(),
        "research_agent": FixtureResearchRuntime(),
        "strategy_agent": FixtureStrategyRuntime(),
    }
