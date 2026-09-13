import asyncio
import json
import os
import subprocess
from uuid import uuid4

import pytest
import psycopg
from temporalio.client import Client

from salience.agents.fixtures import fixture_agent_service
from salience.api.dependencies import TemporalControlPlane
from salience.creative.media import MediaEngine, StorageCapacityGuard
from salience.creative.providers import FixtureCreativeProvider
from salience.creative.repository import CreativeRepository
from salience.governance.cost_repository import CostReservationRepository
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
from salience.workflows.creative import CreativeWorkflowState, build_creative_worker
from salience.workflows.persistence import CanonicalJobStore


async def _seed_content_brief(database_url: str) -> dict[str, str]:
    store = CanonicalJobStore(database_url)
    workspace = await store.create_workspace(
        slug=f"creative-loop-{uuid4().hex}", display_name="Creative loop"
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
        trace_id="creative-loop-source",
    )
    fetch_id = await intelligence.record_fetch(
        workspace_id=workspace.workspace_id,
        program_id=program.content_program_id,
        fetch=FetchInput(
            source_id,
            "creative-loop-resource",
            "2026-09-13",
            "creative-loop-fetch",
            "https://example.test/creative-loop",
            "d" * 64,
        ),
        trace_id="creative-loop-fetch",
    )
    evidence_id = await intelligence.record_evidence(
        workspace_id=workspace.workspace_id,
        program_id=program.content_program_id,
        source_id=source_id,
        fetch_id=fetch_id,
        source_uri="https://example.test/creative-loop",
        content={"title": "Garden evidence"},
        content_hash="d" * 64,
        idempotency_key="creative-loop-evidence",
        trace_id="creative-loop-evidence",
    )
    signal_id = await intelligence.record_signal(
        workspace_id=workspace.workspace_id,
        program_id=program.content_program_id,
        signal=SignalInput(
            "creative-loop-signal",
            "Garden evidence",
            {"freshness": 1},
            {"present": ["freshness"]},
        ),
        trace_id="creative-loop-signal",
    )
    await intelligence.link_signal_support(
        signal_id=signal_id,
        evidence_id=evidence_id,
        fetch_id=fetch_id,
        source_id=source_id,
        trace_id="creative-loop-support",
    )
    opportunity_id = await intelligence.record_opportunity(
        workspace_id=workspace.workspace_id,
        program_id=program.content_program_id,
        opportunity=OpportunityInput(
            "creative-loop-opportunity",
            "Garden evidence",
            80,
            [signal_id],
            "Source grounded",
        ),
        trace_id="creative-loop-opportunity",
    )
    package_id = await intelligence.record_package(
        workspace_id=workspace.workspace_id,
        program_id=program.content_program_id,
        opportunity_id=opportunity_id,
        package=PackageInput("creative-loop-package", {"audience": "gardeners"}),
        trace_id="creative-loop-package",
    )
    claim_id = await intelligence.record_claim(
        workspace_id=workspace.workspace_id,
        program_id=program.content_program_id,
        claim=ClaimInput(
            "creative-loop-claim",
            "Gardeners need reliable care guidance.",
            "verified",
        ),
        trace_id="creative-loop-claim",
    )
    await intelligence.link_claim_evidence(
        claim_id=claim_id,
        evidence_id=evidence_id,
        relation="supporting",
        trace_id="creative-loop-claim-link",
    )
    brief_id = await intelligence.record_content_brief(
        workspace_id=workspace.workspace_id,
        program_id=program.content_program_id,
        brief=ContentBriefInput(
            "creative-loop-brief",
            1,
            opportunity_id,
            package_id,
            [claim_id],
            {"topic": "Garden evidence", "recommended_next_action": "phase_7_review"},
        ),
        trace_id="creative-loop-brief",
    )
    return {
        "workspace_id": workspace.workspace_id,
        "program_id": program.content_program_id,
        "brief_id": brief_id,
        "source_id": source_id,
    }


@pytest.mark.asyncio
async def test_control_plane_runs_fixture_brief_to_ready_package_with_reverse_lineage(
    tmp_path,
) -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    temporal_target = os.environ["TEST_TEMPORAL_TARGET"]
    seeded = await _seed_content_brief(database_url)
    budget_id = await _create_creative_budget(
        database_url,
        workspace_id=seeded["workspace_id"],
        program_id=seeded["program_id"],
    )
    task_queue = f"salience-creative-control-{uuid4()}"
    provider = FixtureCreativeProvider()
    state = CreativeWorkflowState(
        store=CanonicalJobStore(database_url),
        intelligence_repository=IntelligenceRepository(database_url),
        creative_repository=CreativeRepository(database_url),
        agents=fixture_agent_service(),
        provider=provider,
        media=_validated_fixture_media(tmp_path),
        cost_repository=CostReservationRepository(database_url),
    )
    temporal_client = await Client.connect(temporal_target)
    worker = build_creative_worker(temporal_client, task_queue=task_queue, state=state)
    worker_task = asyncio.create_task(worker.run())
    control = TemporalControlPlane(
        database_url=database_url,
        temporal_target=temporal_target,
        task_queue=task_queue,
        creative_effects_enabled=True,
    )
    try:
        started = await control.start_creative(
            workspace_id=seeded["workspace_id"],
            content_program_id=seeded["program_id"],
            brief_id=seeded["brief_id"],
            idempotency_key=f"creative-control-{uuid4()}",
            target_profile_key="fixture-short-video",
            target_profile_version=1,
            dry_run=False,
            budget_id=budget_id,
            max_variants=2,
        )
        completed = await _wait_for_completion(control, started.job_id)
    finally:
        await asyncio.wait_for(worker.shutdown(), timeout=10)
        await asyncio.wait_for(worker_task, timeout=10)

    ready_package_id = completed.output["ready_package_id"]
    assert completed.state == "succeeded"
    assert isinstance(ready_package_id, str) and ready_package_id
    assert provider.submit_count == 2
    lineage = await control.get_ready_package_lineage(ready_package_id)
    assert lineage is not None
    assert lineage["source_ids"] == [seeded["source_id"]]
    assert lineage["asset_ids"]
    assert lineage["agent_run_ids"]
    assert _creative_agent_ids(database_url, completed.job_id) == {
        "writer_agent",
        "creative_director_agent",
        "production_agent",
    }
    assert _asset_inspection(database_url, completed.output["asset_id"])["video_codec"] == "h264"
    assert _asset_variant_decisions(database_url, completed.job_id) == [
        ("variant-1", "selected", "deterministic_primary_variant"),
        ("variant-2", "rejected", "not_selected_after_deterministic_primary_selection"),
    ]
    assert _script_history(database_url, completed.output["script_id"]) == [
        (1, "draft"),
        (2, "approved"),
    ]
    title_candidates, originality_evaluations = await asyncio.to_thread(
        _distribution_gate_counts, database_url, ready_package_id
    )
    assert title_candidates == 1
    assert originality_evaluations == 1


async def _create_creative_budget(
    database_url: str, *, workspace_id: str, program_id: str
) -> str:
    def _insert() -> str:
        with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO budgets (workspace_id, content_program_id, name, scope, limit_amount, status)
                VALUES (%s, %s, %s, 'creative', 1.000000, 'active')
                RETURNING id::text
                """,
                (workspace_id, program_id, f"creative-control-{uuid4()}"),
            )
            return cursor.fetchone()[0]

    return await asyncio.to_thread(_insert)


async def _wait_for_completion(
    control: TemporalControlPlane, job_id: str
) -> object:
    for _ in range(300):
        run = await control.get_creative(job_id)
        if run is not None and run.state in {"succeeded", "denied", "failed", "cancelled"}:
            return run
        await asyncio.sleep(0.1)
    raise TimeoutError("creative control run did not reach a terminal state")


def _distribution_gate_counts(
    database_url: str, ready_package_id: str
) -> tuple[int, int]:
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                (SELECT count(*)
                 FROM title_thumbnail_candidates candidate
                 WHERE candidate.distribution_package_id = ready.distribution_package_id),
                (SELECT count(*)
                 FROM originality_evaluations evaluation
                 WHERE evaluation.distribution_package_id = ready.distribution_package_id)
            FROM ready_to_publish_packages ready
            WHERE ready.id = %s
            """,
            (ready_package_id,),
        )
        row = cursor.fetchone()
    if row is None:
        raise AssertionError("ready package was not persisted")
    return int(row[0]), int(row[1])


def _script_history(database_url: str, approved_script_id: str) -> list[tuple[int, str]]:
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT version, status
            FROM script_versions
            WHERE script_key = (SELECT script_key FROM script_versions WHERE id = %s)
            ORDER BY version
            """,
            (approved_script_id,),
        )
        return [(int(version), str(status)) for version, status in cursor.fetchall()]


def _asset_inspection(database_url: str, asset_id: str) -> dict[str, object]:
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT technical_properties FROM assets WHERE id = %s", (asset_id,))
        row = cursor.fetchone()
    if row is None:
        raise AssertionError("asset was not persisted")
    return dict(row[0])


def _asset_variant_decisions(
    database_url: str, job_id: str
) -> list[tuple[str, str, str | None]]:
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT variant.variant_key, variant.selection_state, variant.selection_reason
            FROM asset_variants variant
            JOIN provider_jobs provider_job ON provider_job.id = variant.provider_job_id
            JOIN creative_jobs creative_job ON creative_job.id = provider_job.creative_job_id
            WHERE creative_job.job_id = %s
            ORDER BY variant_key
            """,
            (job_id,),
        )
        return [(str(key), str(state), reason) for key, state, reason in cursor.fetchall()]


def _creative_agent_ids(database_url: str, job_id: str) -> set[str]:
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT version.agent_id
            FROM agent_runs run
            JOIN agent_versions version ON version.id = run.agent_version_id
            WHERE run.job_id = %s
            """,
            (job_id,),
        )
        return {str(agent_id) for (agent_id,) in cursor.fetchall()}


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
