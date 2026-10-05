import asyncio
from dataclasses import dataclass

from salience.agents.execution import AgentInvocation, AgentRun, AgentService


@dataclass(frozen=True)
class TeamManifest:
    team_id: str
    members: tuple[str, ...]
    lead_agent_id: str | None = None
    max_concurrency: int = 4
    timeout_seconds: float = 30

    def __post_init__(self):
        if not self.team_id.strip() or len(self.team_id)>128:
            raise ValueError("bounded nonempty team ID required")
        if not 1 <= len(self.members) <= 16:
            raise ValueError("team must contain between 1 and 16 tasks")
        if type(self.max_concurrency) is not int or not 1 <= self.max_concurrency <= 8:
            raise ValueError("team concurrency must be between 1 and 8")
        if not 0 < self.timeout_seconds <= 300:
            raise ValueError("bounded team timeout required")


class TeamRunner:
    def __init__(self, service: AgentService) -> None:
        self._service = service

    async def invoke(self, team: TeamManifest, input: dict[str, object]) -> list[AgentRun]:
        for member in (team.lead_agent_id or team.members[0], *team.members):
            manifest=self._service.describe_agent(member)
            if manifest.status != "enabled" or manifest.effect_classification not in {"read","none","pure"}:
                raise PermissionError("parallel teams require enabled read-only agents")
            if not manifest.supports_async or member not in self._service._runtimes:
                raise LookupError("parallel team member requires an asynchronous runtime")
            self._service._validate(manifest.input_schema,input,"input")
        parent=self._service.create_parent(team.lead_agent_id or team.members[0],input)
        slots=asyncio.Semaphore(team.max_concurrency)
        results=[None]*len(team.members)
        async def invoke(index,member):
            async with slots:
                results[index]=await self._service.invoke_child(AgentInvocation(member,input,mode="async"),parent)
        try:
            async with asyncio.timeout(team.timeout_seconds):
                async with asyncio.TaskGroup() as group:
                    for index,member in enumerate(team.members):group.create_task(invoke(index,member))
        except BaseException as failure:
            self._service.finish_parent(parent, status="unknown" if isinstance(failure,(TimeoutError,asyncio.CancelledError)) else "failed",
                output={"tasks":[{"run_id":str(run.id),"output":run.output} for run in results if run is not None]})
            raise
        self._service.finish_parent(parent, status="succeeded",
            output={"tasks":[{"run_id":str(run.id),"output":run.output} for run in results]})
        return results
