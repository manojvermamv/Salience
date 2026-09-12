from dataclasses import dataclass
from re import sub
from uuid import uuid4

from salience.agents.execution import AgentInvocation
from salience.agents.fixtures import fixture_agent_service
from salience.bootstrap.contracts import ResearchEvidenceInput, StrategyVersionInput
from salience.bootstrap.repository import BootstrapRepository
from salience.memory.contracts import MemoryRecordInput
from salience.memory.repository import MemoryRepository
from salience.research.contracts import ResearchConnector
from salience.research.fixtures import FixtureResearchConnector
from salience.workflows.persistence import CanonicalJobStore


@dataclass(frozen=True)
class BootstrapResult:
    workspace_id: str
    content_program_id: str
    strategy_id: str
    strategy: dict[str, object]
    assumptions: list[str]
    evidence: list[dict[str, object]]
    research_agent_run_id: str
    strategy_agent_run_id: str


class ContentProgramService:
    def __init__(
        self,
        *,
        database_url: str,
        research_connector: ResearchConnector | None = None,
    ) -> None:
        self._store = CanonicalJobStore(database_url)
        self._memory = MemoryRepository(database_url)
        self._bootstrap = BootstrapRepository(database_url)
        self._research = research_connector or FixtureResearchConnector()
        self._agents = fixture_agent_service()

    async def create_from_niche(self, *, niche: str) -> BootstrapResult:
        normalized_niche = niche.strip()
        if not normalized_niche:
            raise ValueError("niche must be non-empty")
        slug = sub(r"[^a-z0-9]+", "-", normalized_niche.lower()).strip("-")
        workspace = await self._store.create_workspace(
            slug=f"bootstrap-{slug}-{uuid4().hex[:8]}",
            display_name=f"{normalized_niche} program",
        )
        program = await self._store.create_content_program(
            workspace_id=workspace.workspace_id,
            slug="default",
            name=f"{normalized_niche} content program",
            niche=normalized_niche,
        )
        research_run = await self._agents.invoke(
            AgentInvocation(agent_id="research_agent", input={"niche": normalized_niche})
        )
        findings = await self._research.research(normalized_niche)
        evidence: list[dict[str, object]] = []
        evidence_ids: list[str] = []
        for finding in findings:
            evidence_id = await self._bootstrap.record_evidence(
                workspace_id=workspace.workspace_id,
                program_id=program.content_program_id,
                evidence=ResearchEvidenceInput(
                    source_uri=finding.source_uri,
                    fetched_at=finding.fetched_at,
                    content=finding.content,
                    trust_level=finding.trust_level,
                    verification_status=finding.verification_status,
                ),
            )
            evidence_ids.append(evidence_id)
            evidence.append({"id": evidence_id, "source_uri": finding.source_uri, **finding.content})
            await self._memory.record(
                workspace_id=workspace.workspace_id,
                program_id=program.content_program_id,
                record=MemoryRecordInput(
                    scope="evidence",
                    content={"evidence_id": evidence_id, **finding.content},
                    trust_level=finding.trust_level,
                    evidence_ids=[evidence_id],
                ),
            )
        strategy_run = await self._agents.invoke(
            AgentInvocation(agent_id="strategy_agent", input={"niche": normalized_niche})
        )
        assumptions = ["Fixture evidence is provisional and requires production verification."]
        strategy = dict(strategy_run.output)
        persisted_strategy = await self._bootstrap.record_strategy(
            workspace_id=workspace.workspace_id,
            program_id=program.content_program_id,
            strategy=StrategyVersionInput(
                strategy=strategy,
                assumptions=assumptions,
                evidence_ids=evidence_ids,
            ),
        )
        await self._memory.record(
            workspace_id=workspace.workspace_id,
            program_id=program.content_program_id,
            record=MemoryRecordInput(
                scope="semantic",
                content={"strategy_id": persisted_strategy.strategy_id, **strategy},
                trust_level="fixture",
                evidence_ids=evidence_ids,
            ),
        )
        return BootstrapResult(
            workspace_id=workspace.workspace_id,
            content_program_id=program.content_program_id,
            strategy_id=persisted_strategy.strategy_id,
            strategy=strategy,
            assumptions=assumptions,
            evidence=evidence,
            research_agent_run_id=str(research_run.id),
            strategy_agent_run_id=str(strategy_run.id),
        )
