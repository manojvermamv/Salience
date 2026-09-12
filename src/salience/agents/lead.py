from salience.agents.execution import AgentInvocation, AgentRun, AgentService


class LeadContentAgent:
    def __init__(self, service: AgentService) -> None:
        self._service = service

    async def bootstrap(self, niche: str) -> AgentRun:
        return await self._service.invoke(
            AgentInvocation(agent_id="lead_content_agent", input={"niche": niche})
        )
