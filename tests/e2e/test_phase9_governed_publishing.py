"""Poll and duplicate-webhook convergence for governed publication."""

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


async def _publication_evidence(database_url: str, request_key: str) -> tuple[list[str], int]:
    def inspect() -> tuple[list[str], int]:
        with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT event.state
                FROM publication_status_events event
                JOIN publication_attempts attempt ON attempt.id = event.publication_attempt_id
                JOIN publication_plans plan ON plan.id = attempt.publication_plan_id
                JOIN publication_requests request ON request.id = plan.publication_request_id
                WHERE request.request_key = %s
                ORDER BY event.sequence_no
                """,
                (request_key,),
            )
            states = [str(state) for (state,) in cursor.fetchall()]
            cursor.execute(
                """
                SELECT count(*)
                FROM publisher_webhook_receipts receipt
                JOIN publication_attempts attempt ON attempt.id = receipt.publication_attempt_id
                JOIN publication_plans plan ON plan.id = attempt.publication_plan_id
                JOIN publication_requests request ON request.id = plan.publication_request_id
                WHERE request.request_key = %s
                """,
                (request_key,),
            )
            return states, int(cursor.fetchone()[0])

    return await asyncio.to_thread(inspect)


@pytest.mark.asyncio
async def test_poll_and_duplicate_webhook_converge_to_one_receipt() -> None:
    from test_creative_release_gate_migration import _approved_ready_package

    database_url = os.environ["TEST_DATABASE_URL"]
    temporal_client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    ready = await _approved_ready_package()
    repository = PublicationRepository(database_url)
    account = await repository.create_account(
        workspace_id=ready["workspace_id"],
        platform="fixture",
        account_key=f"webhook-account-{uuid4()}",
        account_type="creator",
        external_account_reference=f"fixture:webhook:{uuid4()}",
    )
    idempotency_key = f"publication-webhook-{uuid4()}"
    task_queue = f"salience-publication-webhook-{uuid4()}"
    workflow_id = f"publication-webhook-{uuid4()}"
    store = CanonicalJobStore(database_url)
    await store.create_publication_run(
        workflow_run_id=workflow_id,
        task_queue=task_queue,
        workspace_id=ready["workspace_id"],
        content_program_id=ready["program_id"],
        ready_package_id=ready["ready_package_id"],
        publisher_account_id=account.id,
        idempotency_key=idempotency_key,
    )
    provider = FixturePublisherAdapter(scenario="published")
    state = PublicationWorkflowState(
        store=store,
        repository=repository,
        provider=provider,
        delivery=PublicationDelivery("publication-delivery-key"),
        cost_repository=CostReservationRepository(database_url),
    )
    worker = build_publication_worker(temporal_client, task_queue=task_queue, state=state)
    worker_task = asyncio.create_task(worker.run())
    try:
        handle = await temporal_client.start_workflow(
            GovernedPublicationWorkflow.run,
            PublicationWorkflowRequest(
                workspace_id=ready["workspace_id"],
                content_program_id=ready["program_id"],
                ready_package_id=ready["ready_package_id"],
                publisher_account_id=account.id,
                budget_id=await _budget(
                    database_url, ready["workspace_id"], ready["program_id"]
                ),
                idempotency_key=idempotency_key,
            ),
            id=workflow_id,
            task_queue=task_queue,
        )
        await asyncio.wait_for(handle.result(), timeout=30)
    finally:
        await asyncio.wait_for(worker.shutdown(), timeout=10)
        await asyncio.wait_for(worker_task, timeout=10)

    states, webhook_receipt_count = await _publication_evidence(database_url, idempotency_key)
    assert states == ["accepted", "processing", "published"]
    assert webhook_receipt_count == 1
