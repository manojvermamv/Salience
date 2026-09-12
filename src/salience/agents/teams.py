from dataclasses import dataclass

from salience.agents.execution import AgentInvocation, AgentRun, AgentService


@dataclass(frozen=True)
class TeamManifest:
    team_id: str
    members: tuple[str, ...]


class TeamRunner:
    def __init__(self, service: AgentService) -> None:
        self._service = service

    async def invoke(self, team: TeamManifest, input: dict[str, object]) -> list[AgentRun]:
        return [
            await self._service.invoke(AgentInvocation(agent_id=member, input=input))
            for member in team.members
        ]
