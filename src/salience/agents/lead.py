from salience.agents.execution import AgentInvocation, AgentRun, AgentService


class IntelligenceLeadResult:
    def __init__(self, *, research: AgentRun, strategy: AgentRun) -> None:
        self.research = research
        self.strategy = strategy


class LeadContentAgent:
    def __init__(self, service: AgentService) -> None:
        self._service = service

    async def bootstrap(self, niche: str) -> AgentRun:
        return await self._service.invoke(
            AgentInvocation(agent_id="lead_content_agent", input={"niche": niche})
        )

    async def run_intelligence(self, niche: str) -> IntelligenceLeadResult:
        research = await self._service.invoke_from_parent(
            "lead_content_agent",
            AgentInvocation(agent_id="research_agent", input={"niche": niche}),
        )
        strategy = await self._service.invoke_from_parent(
            "lead_content_agent",
            AgentInvocation(agent_id="strategy_agent", input={"niche": niche}),
        )
        return IntelligenceLeadResult(research=research, strategy=strategy)
