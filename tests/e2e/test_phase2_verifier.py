import pytest

from salience.agents.execution import AgentInvocation
from salience.agents.fixtures import fixture_agent_service
from salience.agents.teams import TeamManifest, TeamRunner


@pytest.mark.asyncio
async def test_phase_two_runs_direct_delegated_and_team_fixture_agents() -> None:
    service = fixture_agent_service()
    direct = await service.invoke_by_id("research_agent", {"niche": "finance"})
    delegated = await service.invoke_from_parent(
        "lead_content_agent", AgentInvocation("research_agent", {"niche": "finance"})
    )
    team = await TeamRunner(service).invoke(
        TeamManifest("research-team", ("research_agent",)), {"niche": "finance"}
    )

    assert direct.status == delegated.status == team[0].status == "succeeded"
    assert delegated.parent_run_id is not None
    assert direct.output == delegated.output == team[0].output
