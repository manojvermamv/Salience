import asyncio
import os
from uuid import uuid4

import pytest
from temporalio.client import Client

from salience.agents.fixtures import fixture_agent_service
from salience.intelligence.repository import IntelligenceRepository
from salience.workflows.intelligence import (
    IntelligenceLoopWorkflow,
    IntelligenceLoopRequest,
    IntelligenceWorkflowState,
    build_intelligence_worker,
)
from salience.workflows.persistence import CanonicalJobStore


@pytest.mark.asyncio
async def test_intelligence_run_recovers_after_fetch_persistence_before_activity_return() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    temporal_client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    store = CanonicalJobStore(database_url)
    workspace = await store.create_workspace(
        slug=f"durable-intelligence-{uuid4().hex}", display_name="Durable intelligence"
    )
    program = await store.create_content_program(
        workspace_id=workspace.workspace_id,
        slug="loop",
        name="Loop",
        niche="Urban gardening",
    )
    task_queue = f"salience-intelligence-{uuid4()}"
    workflow_id = f"intelligence-restart-{uuid4()}"
    idempotency_key = f"research-{uuid4()}"
    run = await store.create_intelligence_run(
        workflow_run_id=workflow_id,
        task_queue=task_queue,
        workspace_id=workspace.workspace_id,
        content_program_id=program.content_program_id,
        niche="Urban gardening",
        idempotency_key=idempotency_key,
        dry_run=True,
    )
    state = IntelligenceWorkflowState(
        store=store,
        repository=IntelligenceRepository(database_url),
        agents=fixture_agent_service(),
        crash_at="research.fetch.persisted",
    )
    first_worker = build_intelligence_worker(temporal_client, task_queue=task_queue, state=state)
    first_task = asyncio.create_task(first_worker.run())
    handle = await temporal_client.start_workflow(
        IntelligenceLoopWorkflow.run,
        IntelligenceLoopRequest(
            workspace_id=workspace.workspace_id,
            content_program_id=program.content_program_id,
            niche="Urban gardening",
            idempotency_key=idempotency_key,
            dry_run=True,
        ),
        id=workflow_id,
        task_queue=task_queue,
    )
    try:
        await asyncio.wait_for(state.crash_reached.wait(), timeout=10)
        await asyncio.wait_for(first_worker.shutdown(), timeout=10)
        await asyncio.wait_for(first_task, timeout=10)
        second_worker = build_intelligence_worker(temporal_client, task_queue=task_queue, state=state)
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
    assert await IntelligenceRepository(database_url).count_fetches_for_job(str(run.job_id)) == 1
    assert result.opportunity_ids
    assert result.strategy_version_id
