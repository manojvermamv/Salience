import os
from uuid import uuid4

import pytest

from salience.intelligence.briefs import ContentBriefAssembler
from salience.intelligence.claims import ClaimEvidence, ClaimVerifier
from salience.intelligence.contracts import (
    ClaimInput,
    FetchInput,
    OpportunityInput,
    SignalInput,
    SourceInput,
)
from salience.intelligence.packages import PackageEvaluator, StrategicPackageGenerator
from salience.intelligence.repository import IntelligenceRepository
from salience.workflows.persistence import CanonicalJobStore


@pytest.mark.asyncio
async def test_content_brief_repository_persists_selected_package_and_claim_lineage() -> None:
    store = CanonicalJobStore(os.environ["TEST_DATABASE_URL"])
    workspace = await store.create_workspace(
        slug=f"brief-{uuid4().hex}", display_name="Brief lineage"
    )
    program = await store.create_content_program(
        workspace_id=workspace.workspace_id,
        slug="loop",
        name="Loop",
        niche="Urban gardening",
    )
    repository = IntelligenceRepository(os.environ["TEST_DATABASE_URL"])
    source_id = await repository.record_source(
        workspace_id=workspace.workspace_id,
        program_id=program.content_program_id,
        source=SourceInput("fixture", "1", "fixture", "untrusted_external", {}),
        trace_id="trace",
    )
    fetch_id = await repository.record_fetch(
        workspace_id=workspace.workspace_id,
        program_id=program.content_program_id,
        fetch=FetchInput(source_id, "resource", "window", "request", "https://example.test/a", "a" * 64),
        trace_id="trace",
    )
    evidence_id = await repository.record_evidence(
        workspace_id=workspace.workspace_id,
        program_id=program.content_program_id,
        source_id=source_id,
        fetch_id=fetch_id,
        source_uri="https://example.test/a",
        content={"title": "Water"},
        content_hash="a" * 64,
        idempotency_key="evidence",
        trace_id="trace",
    )
    signal_id = await repository.record_signal(
        workspace_id=workspace.workspace_id,
        program_id=program.content_program_id,
        signal=SignalInput("signal", "Water", {"freshness": 1}, {"present": ["freshness"]}),
        trace_id="trace",
    )
    await repository.link_signal_support(
        signal_id=signal_id,
        evidence_id=evidence_id,
        fetch_id=fetch_id,
        source_id=source_id,
        trace_id="trace",
    )
    opportunity = OpportunityInput("opportunity", "Water", 75, [signal_id], "Fresh")
    opportunity_id = await repository.record_opportunity(
        workspace_id=workspace.workspace_id,
        program_id=program.content_program_id,
        opportunity=opportunity,
        trace_id="trace",
    )
    package = StrategicPackageGenerator().generate(opportunity, {}, count=3)[0]
    package_id = await repository.record_package(
        workspace_id=workspace.workspace_id,
        program_id=program.content_program_id,
        opportunity_id=opportunity_id,
        package=package,
        trace_id="trace",
    )
    await repository.record_evaluation(
        package_id=package_id,
        evaluation=PackageEvaluator().evaluate(package, [{"verification_status": "verified"}], {}),
        trace_id="trace",
    )
    verified = ClaimVerifier().link(
        ClaimInput("claim", "Water use can be measured."),
        [ClaimEvidence(evidence_id, "supporting", "verified", "untrusted_external")],
    )
    claim_id = await repository.record_claim(
        workspace_id=workspace.workspace_id,
        program_id=program.content_program_id,
        claim=verified.claim,
        trace_id="trace",
    )
    await repository.link_claim_evidence(
        claim_id=claim_id,
        evidence_id=evidence_id,
        relation="supporting",
        trace_id="trace",
    )
    brief = ContentBriefAssembler().create(
        program_id=program.content_program_id,
        opportunity_id=opportunity_id,
        opportunity=opportunity,
        package_id=package_id,
        package=package,
        strategy_version_id=None,
        claims=[(claim_id, verified.claim)],
    )
    brief_id = await repository.record_content_brief(
        workspace_id=workspace.workspace_id,
        program_id=program.content_program_id,
        brief=brief,
        trace_id="trace",
    )

    lineage = await repository.lineage_for_brief(brief_id)
    assert lineage["opportunity_id"] == opportunity_id
    assert lineage["package_id"] == package_id
    assert lineage["claim_ids"] == [claim_id]
