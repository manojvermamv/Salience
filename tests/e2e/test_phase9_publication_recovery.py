"""Restart safety for the fixture-first governed publication workflow."""

import asyncio
import os
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from temporalio.client import Client

from salience.governance.cost_repository import CostReservationRepository
from salience.publication.delivery import PublicationDelivery
from salience.publication.providers import FixturePublisherAdapter
from salience.publication.repository import PublicationRepository
from salience.workflows.persistence import CanonicalJobStore
from salience.workflows.publication import (
    GovernedPublicationWorkflow,
    PublicationWorkflowRequest,
    PublicationWorkflowState,
    build_publication_worker,
)


sys.path.append(str(Path(__file__).parents[1] / "integration"))


async def _budget(database_url: str, workspace_id: str, program_id: str) -> str:
    def insert() -> str:
        with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO budgets (workspace_id, content_program_id, name, scope, limit_amount, status)
                VALUES (%s, %s, %s, 'publication', 1.000000, 'active')
                RETURNING id::text
                """,
                (workspace_id, program_id, f"publication-budget-{uuid4()}"),
            )
            return cursor.fetchone()[0]

    return await asyncio.to_thread(insert)


async def _remote_receipt_count(database_url: str, job_id: str) -> int:
    def count() -> int:
        with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT count(*)
                FROM remote_publication_receipts receipt
                JOIN publication_attempts attempt ON attempt.id = receipt.publication_attempt_id
                JOIN publication_plans plan ON plan.id = attempt.publication_plan_id
                JOIN publication_requests request ON request.id = plan.publication_request_id
                JOIN jobs job ON job.content_program_id = request.content_program_id
                WHERE job.id = %s AND job.idempotency_key = request.request_key
                """,
                (job_id,),
            )
            return int(cursor.fetchone()[0])

    return await asyncio.to_thread(count)


@pytest.mark.asyncio
async def test_publication_restart_after_remote_acceptance_does_not_duplicate_post() -> None:
    from test_creative_release_gate_migration import _approved_ready_package

    database_url = os.environ["TEST_DATABASE_URL"]
    temporal_client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    ready = await _approved_ready_package()
    repository = PublicationRepository(database_url)
    account = await repository.create_account(
        workspace_id=ready["workspace_id"],
        platform="fixture",
        account_key=f"restart-account-{uuid4()}",
        account_type="creator",
        external_account_reference=f"fixture:restart:{uuid4()}",
    )
    idempotency_key = f"publication-restart-{uuid4()}"
    task_queue = f"salience-publication-restart-{uuid4()}"
    workflow_id = f"publication-restart-{uuid4()}"
    store = CanonicalJobStore(database_url)
    run = await store.create_publication_run(
        workflow_run_id=workflow_id,
        task_queue=task_queue,
        workspace_id=ready["workspace_id"],
        content_program_id=ready["program_id"],
        ready_package_id=ready["ready_package_id"],
        publisher_account_id=account.id,
        idempotency_key=idempotency_key,
    )
    provider = FixturePublisherAdapter(scenario="crash_after_acceptance")
    state = PublicationWorkflowState(
        store=store,
        repository=repository,
        provider=provider,
        delivery=PublicationDelivery("publication-delivery-key"),
        cost_repository=CostReservationRepository(database_url),
        crash_at="publication.accepted",
    )
    request = PublicationWorkflowRequest(
        workspace_id=ready["workspace_id"],
        content_program_id=ready["program_id"],
        ready_package_id=ready["ready_package_id"],
        publisher_account_id=account.id,
        budget_id=await _budget(database_url, ready["workspace_id"], ready["program_id"]),
        idempotency_key=idempotency_key,
    )
    first_worker = build_publication_worker(temporal_client, task_queue=task_queue, state=state)
    first_task = asyncio.create_task(first_worker.run())
    handle = await temporal_client.start_workflow(
        GovernedPublicationWorkflow.run,
        request,
        id=workflow_id,
        task_queue=task_queue,
    )
    try:
        await asyncio.wait_for(state.crash_reached.wait(), timeout=10)
        await asyncio.wait_for(first_worker.shutdown(), timeout=10)
        await asyncio.wait_for(first_task, timeout=10)
        replacement_worker = build_publication_worker(
            temporal_client, task_queue=task_queue, state=state
        )
        replacement_task = asyncio.create_task(replacement_worker.run())
        try:
            result = await asyncio.wait_for(handle.result(), timeout=30)
        finally:
            await asyncio.wait_for(replacement_worker.shutdown(), timeout=10)
            await asyncio.wait_for(replacement_task, timeout=10)
    finally:
        if not first_task.done():
            await asyncio.wait_for(first_worker.shutdown(), timeout=10)
            await asyncio.wait_for(first_task, timeout=10)

    assert result.publication_state == "published"
    assert result.fixture_submit_count == 1
    assert await _remote_receipt_count(database_url, str(run.job_id)) == 1
