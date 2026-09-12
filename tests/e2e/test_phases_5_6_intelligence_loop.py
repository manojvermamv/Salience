import asyncio
import os
from uuid import uuid4

import pytest
from temporalio.client import Client

from salience.agents.fixtures import fixture_agent_service
from salience.intelligence.repository import IntelligenceRepository
from salience.workflows.intelligence import (
    IntelligenceLoopRequest,
    IntelligenceLoopWorkflow,
    IntelligenceWorkflowState,
    build_intelligence_worker,
)
from salience.workflows.persistence import CanonicalJobStore


@pytest.mark.asyncio
async def test_niche_to_selected_content_brief_has_complete_lineage() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    temporal_client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    store = CanonicalJobStore(database_url)
    workspace = await store.create_workspace(
        slug=f"full-loop-{uuid4().hex}", display_name="Full intelligence loop"
    )
    program = await store.create_content_program(
        workspace_id=workspace.workspace_id,
        slug="loop",
        name="Loop",
        niche="Urban gardening",
    )
    task_queue = f"salience-full-loop-{uuid4()}"
    workflow_id = f"full-loop-{uuid4()}"
    run = await store.create_intelligence_run(
        workflow_run_id=workflow_id,
        task_queue=task_queue,
        workspace_id=workspace.workspace_id,
        content_program_id=program.content_program_id,
        niche="Urban gardening",
        idempotency_key=f"full-loop-{uuid4()}",
        dry_run=True,
    )
    worker = build_intelligence_worker(
        temporal_client,
        task_queue=task_queue,
        state=IntelligenceWorkflowState(
            store=store,
            repository=IntelligenceRepository(database_url),
            agents=fixture_agent_service(),
        ),
    )
    task = asyncio.create_task(worker.run())
    try:
        result = await (
            await temporal_client.start_workflow(
                IntelligenceLoopWorkflow.run,
                IntelligenceLoopRequest(
                    workspace_id=workspace.workspace_id,
                    content_program_id=program.content_program_id,
                    niche="Urban gardening",
                    idempotency_key=f"ignored-by-existing-job-{uuid4()}",
                    dry_run=True,
                ),
                id=workflow_id,
                task_queue=task_queue,
            )
        ).result()
    finally:
        await asyncio.wait_for(worker.shutdown(), timeout=10)
        await asyncio.wait_for(task, timeout=10)

    assert result.content_brief_id
    lineage = await IntelligenceRepository(database_url).lineage_for_brief(result.content_brief_id)
    assert lineage["source_ids"] and lineage["fetch_ids"] and lineage["signal_ids"]
    assert lineage["opportunity_id"] in result.opportunity_ids
    assert lineage["package_id"] == result.selected_package_id
    assert result.lead_agent_run_id
