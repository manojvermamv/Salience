from salience.agents.execution import AgentInvocation, AgentRun, AgentService
from salience.agents.teams import TeamManifest, TeamRunner


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
        research, strategy = await TeamRunner(self._service).invoke(
            TeamManifest("intelligence", ("research_agent", "strategy_agent"),
                         lead_agent_id="lead_content_agent", max_concurrency=2),
            {"niche": niche},
        )
        return IntelligenceLeadResult(research=research, strategy=strategy)
