from dataclasses import dataclass

from salience.a2a.fixture_agent import FixtureA2AAgent
from salience.a2a.gateway import RemoteAgentGateway
from salience.agents.execution import AgentInvocation
from salience.agents.fixtures import fixture_agent_service
from salience.bootstrap.service import ContentProgramService
from salience.mcp.fixture_server import FixtureMcpServer
from salience.mcp.gateway import ToolGateway
from salience.workflows.jobs import run_restart_reconciliation_scenario


@dataclass(frozen=True)
class CrossPhaseReport:
    phase_1_recovery: bool
    phase_2_agents: bool
    phase_3_protocols: bool
    phase_4_bootstrap: bool
    retained_ids: dict[str, str]


async def run_cross_phase_verifier(
    *, temporal_target: str, database_url: str
) -> CrossPhaseReport:
    recovery = await run_restart_reconciliation_scenario(
        temporal_target=temporal_target, database_url=database_url
    )
    agents = fixture_agent_service()
    direct = await agents.invoke_by_id("research_agent", {"niche": "finance"})
    delegated = await agents.invoke_from_parent(
        "lead_content_agent", AgentInvocation("research_agent", {"niche": "finance"})
    )
    mcp = ToolGateway(
        server=FixtureMcpServer(),
        allowed_scopes=frozenset({"tool:niche.lookup"}),
        supported_protocol_version="2025-11-25",
    )
    tool = await mcp.discover("fixture-mcp", "niche.lookup")
    tool_result = await mcp.invoke(tool, {"niche": "finance"})
    a2a = RemoteAgentGateway(
        agent=FixtureA2AAgent(), supported_protocol_version="0.3.0"
    )
    descriptor = await a2a.discover("fixture-a2a")
    remote = await a2a.invoke(descriptor, {"niche": "finance"}, delegated.id)
    bootstrap = await ContentProgramService(database_url=database_url).create_from_niche(
        niche="Personal Finance"
    )
    return CrossPhaseReport(
        phase_1_recovery=(
            recovery.status == "succeeded"
            and recovery.provider_effect_calls == 1
            and recovery.reconciled
        ),
        phase_2_agents=(
            direct.output == delegated.output and delegated.parent_run_id is not None
        ),
        phase_3_protocols=(
            tool_result.output["source"] == "fixture"
            and remote.output["source"] == "fixture"
            and remote.parent_run_id == delegated.id
        ),
        phase_4_bootstrap=bool(bootstrap.strategy["content_pillars"])
        and bool(bootstrap.evidence),
        retained_ids={
            "bootstrap_workspace_id": bootstrap.workspace_id,
            "bootstrap_program_id": bootstrap.content_program_id,
            "bootstrap_strategy_id": bootstrap.strategy_id,
            "delegated_agent_run_id": str(delegated.id),
        },
    )
