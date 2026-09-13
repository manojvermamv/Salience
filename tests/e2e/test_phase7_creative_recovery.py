import asyncio
import json
import os
import subprocess
from uuid import uuid4

import pytest
from temporalio.client import Client

from salience.agents.fixtures import fixture_agent_service
from salience.creative.media import MediaEngine, StorageCapacityGuard
from salience.creative.providers import FixtureCreativeProvider
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
from salience.storage.memory import MemoryObjectStore
from salience.workflows.creative import (
    CreativeProductionRequest,
    CreativeWorkflowState,
    CreativeProductionWorkflow,
    build_creative_worker,
)
from salience.workflows.persistence import CanonicalJobStore


class ReconciliationProbeProvider(FixtureCreativeProvider):
    """Counts every submission call so fixture idempotency cannot mask a retry."""

    def __init__(self) -> None:
        super().__init__()
        self.submit_attempts = 0
        self.reconcile_calls = 0

    async def submit(self, request):
        self.submit_attempts += 1
        return await super().submit(request)

    async def reconcile(self, request_key):
        self.reconcile_calls += 1
        job = self._jobs_by_key.get(request_key)
        return job.result if job is not None else None


async def _seed_brief(database_url: str) -> dict[str, str]:
    store = CanonicalJobStore(database_url)
    workspace = await store.create_workspace(
        slug=f"creative-recovery-{uuid4().hex}", display_name="Creative recovery"
    )
    program = await store.create_content_program(
        workspace_id=workspace.workspace_id,
        slug="creative",
        name="Creative",
        niche="Urban gardening",
    )
    intelligence = IntelligenceRepository(database_url)
    source_id = await intelligence.record_source(
        workspace_id=workspace.workspace_id,
        program_id=program.content_program_id,
        source=SourceInput("fixture", "1", "fixture", "untrusted_external", {}),
        trace_id="creative-source",
    )
    fetch_id = await intelligence.record_fetch(
        workspace_id=workspace.workspace_id,
        program_id=program.content_program_id,
        fetch=FetchInput(
            source_id,
            "creative-recovery-resource",
            "2026-09-13",
            "creative-recovery-fetch",
            "https://example.test/creative-recovery",
            "c" * 64,
        ),
        trace_id="creative-fetch",
    )
    evidence_id = await intelligence.record_evidence(
        workspace_id=workspace.workspace_id,
        program_id=program.content_program_id,
        source_id=source_id,
        fetch_id=fetch_id,
        source_uri="https://example.test/creative-recovery",
        content={"title": "Garden evidence"},
        content_hash="c" * 64,
        idempotency_key="creative-recovery-evidence",
        trace_id="creative-evidence",
    )
    signal_id = await intelligence.record_signal(
        workspace_id=workspace.workspace_id,
        program_id=program.content_program_id,
        signal=SignalInput("creative-recovery-signal", "Garden evidence", {"freshness": 1}, {"present": ["freshness"]}),
        trace_id="creative-signal",
    )
    await intelligence.link_signal_support(
        signal_id=signal_id,
        evidence_id=evidence_id,
        fetch_id=fetch_id,
        source_id=source_id,
        trace_id="creative-support",
    )
    opportunity_id = await intelligence.record_opportunity(
        workspace_id=workspace.workspace_id,
        program_id=program.content_program_id,
        opportunity=OpportunityInput("creative-recovery-opportunity", "Garden evidence", 80, [signal_id], "Source grounded"),
        trace_id="creative-opportunity",
    )
    package_id = await intelligence.record_package(
        workspace_id=workspace.workspace_id,
        program_id=program.content_program_id,
        opportunity_id=opportunity_id,
        package=PackageInput("creative-recovery-package", {"audience": "gardeners"}),
        trace_id="creative-package",
    )
    claim_id = await intelligence.record_claim(
        workspace_id=workspace.workspace_id,
        program_id=program.content_program_id,
        claim=ClaimInput("creative-recovery-claim", "Gardeners need reliable care guidance.", "verified"),
        trace_id="creative-claim",
    )
    await intelligence.link_claim_evidence(
        claim_id=claim_id,
        evidence_id=evidence_id,
        relation="supporting",
        trace_id="creative-claim-link",
    )
    brief_id = await intelligence.record_content_brief(
        workspace_id=workspace.workspace_id,
        program_id=program.content_program_id,
        brief=ContentBriefInput(
            "creative-recovery-brief",
            1,
            opportunity_id,
            package_id,
            [claim_id],
            {"topic": "Garden evidence", "recommended_next_action": "phase_7_review"},
        ),
        trace_id="creative-brief",
    )
    return {
        "workspace_id": workspace.workspace_id,
        "program_id": program.content_program_id,
        "brief_id": brief_id,
        "source_id": source_id,
    }


@pytest.mark.asyncio
async def test_restart_after_provider_acceptance_reconciles_without_resubmission(tmp_path) -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    temporal_client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    seeded = await _seed_brief(database_url)
    task_queue = f"salience-creative-{uuid4()}"
    workflow_id = f"creative-restart-{uuid4()}"
    idempotency_key = f"creative-{uuid4()}"
    store = CanonicalJobStore(database_url)
    run = await store.create_creative_run(
        workflow_run_id=workflow_id,
        task_queue=task_queue,
        workspace_id=seeded["workspace_id"],
        content_program_id=seeded["program_id"],
        brief_id=seeded["brief_id"],
        idempotency_key=idempotency_key,
        dry_run=False,
    )
    provider = ReconciliationProbeProvider()
    state = CreativeWorkflowState(
        store=store,
        intelligence_repository=IntelligenceRepository(database_url),
        creative_repository=CreativeRepository(database_url),
        agents=fixture_agent_service(),
        provider=provider,
        media=_validated_fixture_media(tmp_path),
        crash_at="provider.submitted",
    )
    first_worker = build_creative_worker(temporal_client, task_queue=task_queue, state=state)
    first_task = asyncio.create_task(first_worker.run())
    handle = await temporal_client.start_workflow(
        CreativeProductionWorkflow.run,
        CreativeProductionRequest(
            workspace_id=seeded["workspace_id"],
            content_program_id=seeded["program_id"],
            brief_id=seeded["brief_id"],
            idempotency_key=idempotency_key,
            dry_run=False,
        ),
        id=workflow_id,
        task_queue=task_queue,
    )
    try:
        await asyncio.wait_for(state.crash_reached.wait(), timeout=10)
        await asyncio.wait_for(first_worker.shutdown(), timeout=10)
        await asyncio.wait_for(first_task, timeout=10)
        second_worker = build_creative_worker(temporal_client, task_queue=task_queue, state=state)
        second_task = asyncio.create_task(second_worker.run())
        try:
            result = await asyncio.wait_for(handle.result(), timeout=30)
        finally:
            await asyncio.wait_for(second_worker.shutdown(), timeout=10)
            await asyncio.wait_for(second_task, timeout=10)
    finally:
        if not first_task.done():
            await asyncio.wait_for(first_worker.shutdown(), timeout=10)
            await asyncio.wait_for(first_task, timeout=10)

    assert result.state == "completed"
    assert result.ready_package_id
    assert result.provider_submit_count == 1
    assert provider.submit_attempts == 1
    assert provider.reconcile_calls == 1
    lineage = await CreativeRepository(database_url).lineage_for_ready_package(result.ready_package_id)
    assert lineage["source_ids"] == [seeded["source_id"]]
    assert lineage["asset_ids"] == [result.asset_id]
    assert lineage["agent_run_ids"]


@pytest.mark.asyncio
async def test_budget_denial_happens_before_creative_provider_submission() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    temporal_client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    seeded = await _seed_brief(database_url)
    task_queue = f"salience-creative-denial-{uuid4()}"
    workflow_id = f"creative-denial-{uuid4()}"
    idempotency_key = f"creative-denial-{uuid4()}"
    store = CanonicalJobStore(database_url)
    await store.create_creative_run(
        workflow_run_id=workflow_id,
        task_queue=task_queue,
        workspace_id=seeded["workspace_id"],
        content_program_id=seeded["program_id"],
        brief_id=seeded["brief_id"],
        idempotency_key=idempotency_key,
        dry_run=False,
    )
    provider = FixtureCreativeProvider()
    state = CreativeWorkflowState(
        store=store,
        intelligence_repository=IntelligenceRepository(database_url),
        creative_repository=CreativeRepository(database_url),
        agents=fixture_agent_service(),
        provider=provider,
        budget_available_micros=0,
    )
    worker = build_creative_worker(temporal_client, task_queue=task_queue, state=state)
    worker_task = asyncio.create_task(worker.run())
    try:
        handle = await temporal_client.start_workflow(
            CreativeProductionWorkflow.run,
            CreativeProductionRequest(
                workspace_id=seeded["workspace_id"],
                content_program_id=seeded["program_id"],
                brief_id=seeded["brief_id"],
                idempotency_key=idempotency_key,
                dry_run=False,
            ),
            id=workflow_id,
            task_queue=task_queue,
        )
        result = await asyncio.wait_for(handle.result(), timeout=30)
    finally:
        await asyncio.wait_for(worker.shutdown(), timeout=10)
        await asyncio.wait_for(worker_task, timeout=10)

    assert result.state == "denied"
    assert result.denial_reason == "budget_exceeded"
    assert provider.submit_count == 0


def _validated_fixture_media(tmp_path):
    def probe(command, **_kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "format": {"format_name": "mov,mp4,m4a", "duration": "30", "size": "64"},
                    "streams": [
                        {"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920}
                    ],
                }
            ),
            stderr="",
        )

    return MediaEngine(
        object_store=MemoryObjectStore(),
        capacity_guard=StorageCapacityGuard(minimum_free_bytes=0),
        ffmpeg_path="missing-ffmpeg",
        ffprobe_path="/bin/true",
        temporary_root=tmp_path,
        command_runner=probe,
    )
