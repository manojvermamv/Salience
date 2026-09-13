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


async def _publication_destination(database_url: str, request_key: str) -> str:
    def inspect() -> str:
        with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT destination FROM publication_requests WHERE request_key = %s",
                (request_key,),
            )
            row = cursor.fetchone()
            assert row is not None
            return str(row[0])

    return await asyncio.to_thread(inspect)


async def _set_connection_status(database_url: str, account_id: str, status: str) -> None:
    def update() -> None:
        with psycopg.connect(database_url) as connection:
            connection.execute(
                "UPDATE publisher_connections SET status = %s WHERE publisher_account_id = %s",
                (status, account_id),
            )

    await asyncio.to_thread(update)


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
                destination="fixture://canonical-effect-input",
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
    assert await _publication_destination(database_url, idempotency_key) == (
        "fixture://canonical-effect-input"
    )


@pytest.mark.asyncio
async def test_revoked_canonical_connection_denies_before_fixture_submission() -> None:
    from test_creative_release_gate_migration import _approved_ready_package

    database_url = os.environ["TEST_DATABASE_URL"]
    temporal_client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    ready = await _approved_ready_package()
    repository = PublicationRepository(database_url)
    account = await repository.create_account(
        workspace_id=ready["workspace_id"],
        platform="fixture",
        account_key=f"revoked-account-{uuid4()}",
        account_type="creator",
        external_account_reference=f"fixture:revoked:{uuid4()}",
    )
    await _set_connection_status(database_url, account.id, "revoked")
    idempotency_key = f"publication-revoked-{uuid4()}"
    task_queue = f"salience-publication-revoked-{uuid4()}"
    workflow_id = f"publication-revoked-{uuid4()}"
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
                budget_id=await _budget(database_url, ready["workspace_id"], ready["program_id"]),
                idempotency_key=idempotency_key,
            ),
            id=workflow_id,
            task_queue=task_queue,
        )
        result = await asyncio.wait_for(handle.result(), timeout=30)
    finally:
        await asyncio.wait_for(worker.shutdown(), timeout=10)
        await asyncio.wait_for(worker_task, timeout=10)

    assert result.publication_state == "denied"
    assert result.denial_reason is not None
    assert "connection_status" in result.denial_reason
    assert provider.submit_count == 0


@pytest.mark.asyncio
async def test_scheduled_execution_uses_its_exact_persisted_request_and_plan() -> None:
    from test_creative_release_gate_migration import _approved_ready_package

    database_url = os.environ["TEST_DATABASE_URL"]
    temporal_client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    ready = await _approved_ready_package()
    repository = PublicationRepository(database_url)
    account = await repository.create_account(
        workspace_id=ready["workspace_id"],
        platform="fixture",
        account_key=f"scheduled-account-{uuid4()}",
        account_type="creator",
        external_account_reference=f"fixture:scheduled:{uuid4()}",
    )
    idempotency_key = f"publication-scheduled-{uuid4()}"
    request = await repository.create_request(
        ready_package_id=ready["ready_package_id"],
        workspace_id=ready["workspace_id"],
        content_program_id=ready["program_id"],
        publisher_account_id=account.id,
        idempotency_key=idempotency_key,
        destination="fixture://scheduled-canonical",
    )
    plan = await repository.create_plan(
        publication_request_id=request.id,
        version=1,
        publisher_id="fixture-publisher",
        publisher_version="1",
    )
    task_queue = f"salience-publication-scheduled-{uuid4()}"
    workflow_id = f"publication-scheduled-{uuid4()}"
    store = CanonicalJobStore(database_url)
    job_schedule = await store.create_schedule(
        workspace_id=ready["workspace_id"],
        content_program_id=ready["program_id"],
        name=f"scheduled-{uuid4()}",
        schedule_expression="every 86400s",
        job_type="governed_publication",
        payload={"publication_request_id": request.id, "publication_plan_id": plan.id},
    )
    await repository.create_schedule(
        publication_request_id=request.id,
        publication_plan_id=plan.id,
        job_schedule_id=job_schedule.schedule_id,
        version=1,
        schedule_fingerprint="b" * 64,
    )
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
                budget_id=await _budget(database_url, ready["workspace_id"], ready["program_id"]),
                idempotency_key=idempotency_key,
                destination="fixture://scheduled-canonical",
                scheduled_publication_request_id=request.id,
                scheduled_publication_plan_id=plan.id,
            ),
            id=workflow_id,
            task_queue=task_queue,
        )
        result = await asyncio.wait_for(handle.result(), timeout=30)
    finally:
        await asyncio.wait_for(worker.shutdown(), timeout=10)
        await asyncio.wait_for(worker_task, timeout=10)

    assert result.publication_state == "published"
    assert result.publication_request_id == request.id
    assert result.publication_plan_id == plan.id
    assert provider.submit_count == 1
    assert await _publication_destination(database_url, idempotency_key) == "fixture://scheduled-canonical"
