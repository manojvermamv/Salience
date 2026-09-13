import os
from uuid import uuid4

import pytest

from salience.creative.repository import CreativeRepository
from salience.intelligence.contracts import (
    ClaimInput,
    ContentBriefInput,
    FetchInput,
    OpportunityInput,
    PackageInput,
    SignalInput,
    SourceInput,
)
from salience.intelligence.repository import IntelligenceRepository
from salience.workflows.persistence import CanonicalJobStore


async def _seed_brief() -> dict[str, str]:
    store = CanonicalJobStore(os.environ["TEST_DATABASE_URL"])
    workspace = await store.create_workspace(
        slug=f"creative-{uuid4().hex}", display_name="Creative repository"
    )
    program = await store.create_content_program(
        workspace_id=workspace.workspace_id,
        slug="creative-loop",
        name="Creative loop",
        niche="Urban gardening",
    )
    intelligence = IntelligenceRepository(os.environ["TEST_DATABASE_URL"])
    source_id = await intelligence.record_source(
        workspace_id=workspace.workspace_id,
        program_id=program.content_program_id,
        source=SourceInput("fixture", "1", "fixture", "untrusted_external", {}),
        trace_id="trace-source",
    )
    fetch_id = await intelligence.record_fetch(
        workspace_id=workspace.workspace_id,
        program_id=program.content_program_id,
        fetch=FetchInput(
            source_id,
            "creative-resource",
            "2026-09-13",
            "creative-fetch",
            "https://example.test/creative",
            "a" * 64,
        ),
        trace_id="trace-fetch",
    )
    evidence_id = await intelligence.record_evidence(
        workspace_id=workspace.workspace_id,
        program_id=program.content_program_id,
        source_id=source_id,
        fetch_id=fetch_id,
        source_uri="https://example.test/creative",
        content={"title": "Garden evidence"},
        content_hash="a" * 64,
        idempotency_key="creative-evidence",
        trace_id="trace-evidence",
    )
    signal_id = await intelligence.record_signal(
        workspace_id=workspace.workspace_id,
        program_id=program.content_program_id,
        signal=SignalInput("creative-signal", "Garden evidence", {"freshness": 1}, {"present": ["freshness"]}),
        trace_id="trace-signal",
    )
    await intelligence.link_signal_support(
        signal_id=signal_id,
        evidence_id=evidence_id,
        fetch_id=fetch_id,
        source_id=source_id,
        trace_id="trace-support",
    )
    opportunity_id = await intelligence.record_opportunity(
        workspace_id=workspace.workspace_id,
        program_id=program.content_program_id,
        opportunity=OpportunityInput(
            "creative-opportunity", "Garden evidence", 80, [signal_id], "Source grounded"
        ),
        trace_id="trace-opportunity",
    )
    package_id = await intelligence.record_package(
        workspace_id=workspace.workspace_id,
        program_id=program.content_program_id,
        opportunity_id=opportunity_id,
        package=PackageInput("creative-package", {"audience": "gardeners"}),
        trace_id="trace-package",
    )
    claim_id = await intelligence.record_claim(
        workspace_id=workspace.workspace_id,
        program_id=program.content_program_id,
        claim=ClaimInput("creative-claim", "Gardeners need reliable care guidance.", "verified"),
        trace_id="trace-claim",
    )
    await intelligence.link_claim_evidence(
        claim_id=claim_id,
        evidence_id=evidence_id,
        relation="supporting",
        trace_id="trace-claim-link",
    )
    brief_id = await intelligence.record_content_brief(
        workspace_id=workspace.workspace_id,
        program_id=program.content_program_id,
        brief=ContentBriefInput(
            "creative-brief",
            1,
            opportunity_id,
            package_id,
            [claim_id],
            {"topic": "Garden evidence", "recommended_next_action": "phase_7_review"},
        ),
        trace_id="trace-brief",
    )
    return {
        "workspace_id": workspace.workspace_id,
        "program_id": program.content_program_id,
        "brief_id": brief_id,
        "source_id": source_id,
        "fetch_id": fetch_id,
        "evidence_id": evidence_id,
        "signal_id": signal_id,
        "opportunity_id": opportunity_id,
        "strategic_package_id": package_id,
        "claim_id": claim_id,
    }


@pytest.mark.asyncio
async def test_creative_repository_is_idempotent_and_preserves_ready_package_lineage() -> None:
    seeded = await _seed_brief()
    repository = CreativeRepository(os.environ["TEST_DATABASE_URL"])

    script_id = await repository.record_script(
        workspace_id=seeded["workspace_id"],
        program_id=seeded["program_id"],
        brief_id=seeded["brief_id"],
        script_key="garden-video",
        version=1,
        status="approved",
        target_format="short_video",
        target_duration_seconds=45,
        script={"sections": [{"text": "Reliable garden guidance"}]},
        claim_ids=[seeded["claim_id"]],
        evidence_ids=[seeded["evidence_id"]],
        trace_id="trace-script",
    )
    creative_job_id = await repository.record_creative_job(
        workspace_id=seeded["workspace_id"],
        program_id=seeded["program_id"],
        brief_id=seeded["brief_id"],
        script_id=script_id,
        requested_capability="text_to_video",
        request_fingerprint="creative-request-fingerprint",
        idempotency_key="creative-request-key",
        request={"prompt": "garden guidance", "duration_seconds": 45},
        timeout_seconds=60,
        trace_id="trace-creative-job",
    )
    provider_input = {
        "creative_job_id": creative_job_id,
        "provider_id": "fixture-video",
        "provider_version": "1.0.0",
        "model_id": "fixture-v1",
        "external_job_id": f"fixture-job-{seeded['program_id']}",
        "state": "succeeded",
        "normalized_request": {"duration_seconds": 45},
        "estimated_cost_micros": 100,
        "actual_cost_micros": 90,
        "trace_id": "trace-provider-job",
    }
    first_provider_id = await repository.record_provider_job(**provider_input)
    second_provider_id = await repository.record_provider_job(**provider_input)
    asset = await repository.record_asset_variant(
        workspace_id=seeded["workspace_id"],
        program_id=seeded["program_id"],
        creative_job_id=creative_job_id,
        provider_job_id=first_provider_id,
        storage_key="creative/garden-video.mp4",
        content_hash="b" * 64,
        media_type="video/mp4",
        byte_size=512,
        origin_type="generated",
        variant_key="candidate-1",
        selection_state="selected",
        selection_reason="best deterministic fixture",
        trace_id="trace-asset",
    )
    profile_id = await repository.record_platform_profile(
        workspace_id=seeded["workspace_id"],
        program_id=seeded["program_id"],
        profile_key="short-video",
        version=1,
        target_platform="fixture-platform",
        rules={"aspect_ratio": "9:16", "caption_limit": 100},
        status="active",
    )
    distribution_id = await repository.record_distribution_package(
        workspace_id=seeded["workspace_id"],
        program_id=seeded["program_id"],
        brief_id=seeded["brief_id"],
        script_id=script_id,
        platform_profile_id=profile_id,
        package_key="garden-video-package",
        version=1,
        locale="en",
        package_metadata={"title": "Garden guidance"},
        status="validated",
        asset_ids=[asset.asset_id],
    )
    title_candidate_id = await repository.record_title_thumbnail_candidate(
        distribution_package_id=distribution_id,
        candidate_key="garden-primary",
        title="Evidence-linked garden care",
        thumbnail_asset_id=asset.asset_id,
        selection_state="selected",
        reason="unique",
        score=0.0,
    )
    localization_id = await repository.record_localization(
        distribution_package_id=distribution_id,
        source_locale="en",
        target_locale="fr",
        content={"title": "Jardin"},
        claim_ids=[seeded["claim_id"]],
        status="verified",
    )
    originality_id = await repository.record_originality_evaluation(
        distribution_package_id=distribution_id,
        evaluator_version="originality@v1",
        metrics={"title_match_count": 0.0},
        status="allowed",
        reason="unique",
    )
    disclosure_id = await repository.record_synthetic_media_disclosure(
        distribution_package_id=distribution_id,
        decision={"required": True, "label": "Synthetic media"},
        status="approved",
    )
    ready_id = await repository.record_ready_package(
        workspace_id=seeded["workspace_id"],
        program_id=seeded["program_id"],
        brief_id=seeded["brief_id"],
        script_id=script_id,
        distribution_package_id=distribution_id,
        platform_profile_id=profile_id,
        disclosure_id=disclosure_id,
        ready_package_key="garden-video-ready",
        version=1,
        approval_state="approved",
        verifier_results={"all_passed": True},
        lineage={"brief_id": seeded["brief_id"]},
    )

    lineage = await repository.lineage_for_ready_package(ready_id)

    assert first_provider_id == second_provider_id
    assert lineage["brief_id"] == seeded["brief_id"]
    assert lineage["source_ids"] == [seeded["source_id"]]
    assert lineage["fetch_ids"] == [seeded["fetch_id"]]
    assert lineage["evidence_ids"] == [seeded["evidence_id"]]
    assert lineage["asset_ids"] == [asset.asset_id]
    assert lineage["provider_job_ids"] == [first_provider_id]
    assert title_candidate_id
    assert localization_id
    assert originality_id


@pytest.mark.asyncio
async def test_intelligence_repository_loads_a_brief_only_in_its_canonical_scope() -> None:
    seeded = await _seed_brief()

    handoff = await IntelligenceRepository(os.environ["TEST_DATABASE_URL"]).exact_content_brief(
        brief_id=seeded["brief_id"],
        workspace_id=seeded["workspace_id"],
        program_id=seeded["program_id"],
    )

    assert handoff["brief_id"] == seeded["brief_id"]
    assert handoff["claim_ids"] == [seeded["claim_id"]]
