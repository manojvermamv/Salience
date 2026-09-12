import os
from uuid import uuid4

import pytest

from salience.intelligence.contracts import (
    ClaimInput,
    ContentBriefInput,
    FetchInput,
    OpportunityInput,
    PackageEvaluationInput,
    PackageInput,
    SignalInput,
    SourceInput,
)
from salience.intelligence.repository import IntelligenceRepository
from salience.workflows.persistence import CanonicalJobStore


@pytest.mark.asyncio
async def test_intelligence_repository_is_idempotent_and_preserves_brief_lineage() -> None:
    store = CanonicalJobStore(os.environ["TEST_DATABASE_URL"])
    workspace = await store.create_workspace(
        slug=f"intelligence-{uuid4().hex}", display_name="Intelligence repository"
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
        source=SourceInput(
            source_key="rss-gardens",
            version="v1",
            connector="rss_atom",
            trust_level="untrusted_external",
            configuration={"url": "https://feeds.example/gardens.xml"},
        ),
        trace_id="trace-source",
    )
    fetch_input = FetchInput(
        source_id=source_id,
        resource_identity="guid-42",
        window_key="2026-09-12",
        request_fingerprint="fetch-fingerprint",
        canonical_url="https://feeds.example/gardens/42",
        raw_hash="a" * 64,
    )
    first_fetch_id = await repository.record_fetch(
        workspace_id=workspace.workspace_id,
        program_id=program.content_program_id,
        fetch=fetch_input,
        trace_id="trace-fetch",
    )
    second_fetch_id = await repository.record_fetch(
        workspace_id=workspace.workspace_id,
        program_id=program.content_program_id,
        fetch=fetch_input,
        trace_id="trace-fetch",
    )
    evidence_id = await repository.record_evidence(
        workspace_id=workspace.workspace_id,
        program_id=program.content_program_id,
        source_id=source_id,
        fetch_id=first_fetch_id,
        source_uri="https://feeds.example/gardens/42",
        content={"title": "Garden trends"},
        content_hash="a" * 64,
        idempotency_key="evidence-42",
        trace_id="trace-evidence",
    )
    signal_id = await repository.record_signal(
        workspace_id=workspace.workspace_id,
        program_id=program.content_program_id,
        signal=SignalInput(
            fingerprint="garden-trends",
            topic="Garden trends",
            features={"freshness": 0.8, "confidence": 0.7},
            availability={"present": ["freshness", "confidence"], "missing": []},
        ),
        trace_id="trace-signal",
    )
    await repository.link_signal_support(
        signal_id=signal_id,
        evidence_id=evidence_id,
        fetch_id=first_fetch_id,
        source_id=source_id,
        trace_id="trace-support",
    )
    opportunity_id = await repository.record_opportunity(
        workspace_id=workspace.workspace_id,
        program_id=program.content_program_id,
        opportunity=OpportunityInput(
            fingerprint="garden-trends-opportunity",
            topic="Garden trends",
            score=72.5,
            signal_ids=[signal_id],
            explanation="Fresh, source-grounded garden interest",
        ),
        trace_id="trace-opportunity",
    )
    package_id = await repository.record_package(
        workspace_id=workspace.workspace_id,
        program_id=program.content_program_id,
        opportunity_id=opportunity_id,
        package=PackageInput(
            diversity_fingerprint="practical-garden-checklist",
            content={"audience": "urban gardeners", "hook": "A quick seasonal check"},
        ),
        trace_id="trace-package",
    )
    await repository.record_evaluation(
        package_id=package_id,
        evaluation=PackageEvaluationInput(
            evaluation_key="deterministic-v1", score=81, reason="evidence available"
        ),
        trace_id="trace-evaluation",
    )
    claim_id = await repository.record_claim(
        workspace_id=workspace.workspace_id,
        program_id=program.content_program_id,
        claim=ClaimInput(
            fingerprint="garden-claim", text="Gardeners seek seasonal checklists"
        ),
        trace_id="trace-claim",
    )
    await repository.link_claim_evidence(
        claim_id=claim_id,
        evidence_id=evidence_id,
        relation="supporting",
        trace_id="trace-claim-link",
    )
    brief_id = await repository.record_content_brief(
        workspace_id=workspace.workspace_id,
        program_id=program.content_program_id,
        brief=ContentBriefInput(
            brief_key="garden-trends-v1",
            version=1,
            opportunity_id=opportunity_id,
            package_id=package_id,
            claim_ids=[claim_id],
            content={"topic": "Garden trends", "recommended_next_action": "phase_7_review"},
        ),
        trace_id="trace-brief",
    )

    assert first_fetch_id == second_fetch_id
    assert await repository.lineage_for_brief(brief_id) == {
        "source_ids": [source_id],
        "fetch_ids": [first_fetch_id],
        "signal_ids": [signal_id],
        "opportunity_id": opportunity_id,
        "package_id": package_id,
        "claim_ids": [claim_id],
    }
