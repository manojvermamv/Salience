"""Single fixture path for the Phase 7-8 release gate."""

import asyncio
import os
from uuid import uuid4

import psycopg
import pytest
from temporalio.client import Client

from salience.agents.fixtures import fixture_agent_service
from salience.creative.repository import CreativeRepository
from salience.governance.cost_repository import CostReservationRepository
from salience.intelligence.repository import IntelligenceRepository
from salience.workflows.creative import (
    CreativeProductionRequest,
    CreativeProductionWorkflow,
    CreativeWorkflowState,
    build_creative_worker,
)
from salience.workflows.persistence import CanonicalJobStore

from test_phase7_creative_recovery import (
    ReconciliationProbeProvider,
    _create_creative_budget,
    _effect_id,
    _seed_brief,
    _validated_fixture_media,
)


@pytest.mark.asyncio
async def test_release_gate_survives_crash_and_converges_cost_webhook_rights_and_immutability(
    tmp_path,
) -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    temporal_client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    seeded = await _seed_brief(database_url)
    task_queue = f"salience-release-gate-{uuid4()}"
    workflow_id = f"creative-release-gate-{uuid4()}"
    idempotency_key = f"creative-release-gate-{uuid4()}"
    budget_id = await _create_creative_budget(
        database_url,
        workspace_id=seeded["workspace_id"],
        program_id=seeded["program_id"],
        limit_amount="1.000000",
    )
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
        cost_repository=CostReservationRepository(database_url),
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
            budget_id=budget_id,
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
    assert result.ready_package_id is not None
    assert result.provider_submit_count == provider.submit_attempts == 1
    assert provider.reconcile_calls == 1

    effect_id = await _effect_id(
        database_url,
        seeded["workspace_id"],
        f"{idempotency_key}:text-to-video:variant:1",
    )
    assert await CostReservationRepository(database_url).reservation_count(effect_id=effect_id) == 1

    repository = CreativeRepository(database_url)
    external_job_id = _provider_external_job_id(database_url, result.provider_job_id)
    event = await provider.verify_webhook(
        {"id": external_job_id, "status": "completed", "delivery_id": f"gate-{uuid4()}"},
        signature="fixture-signature",
    )
    first_receipt = await repository.record_verified_webhook(event=event, trace_id=result.trace_id)
    second_receipt = await repository.record_verified_webhook(event=event, trace_id=result.trace_id)

    assert first_receipt.receipt_id == second_receipt.receipt_id
    counts = _release_gate_counts(
        database_url,
        job_id=result.job_id,
        trace_id=result.trace_id,
        ready_package_id=result.ready_package_id,
        asset_id=result.asset_id,
        provider_job_id=result.provider_job_id,
    )
    assert counts["actual_cost_entries"] == 1
    assert counts["asset_provenance_records"] == 1
    assert counts["webhook_receipts"] == 1
    assert counts["audit_records"] >= 2
    assert counts["provenance_records"] >= 2
    assert {"cost.reserved", "cost.settled"}.issubset(
        _release_gate_actions(database_url, job_id=result.job_id, trace_id=result.trace_id)
    )
    lineage = await repository.lineage_for_ready_package(result.ready_package_id)
    assert lineage["source_ids"] == [seeded["source_id"]]
    assert lineage["asset_ids"] == [result.asset_id]
    _assert_ready_package_is_immutable(database_url, result.ready_package_id)


def _provider_external_job_id(database_url: str, provider_job_id: str | None) -> str:
    assert provider_job_id is not None
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT external_job_id FROM provider_jobs WHERE id = %s", (provider_job_id,))
        row = cursor.fetchone()
    assert row is not None
    return str(row[0])


def _release_gate_counts(
    database_url: str,
    *,
    job_id: str,
    trace_id: str,
    ready_package_id: str,
    asset_id: str | None,
    provider_job_id: str | None,
) -> dict[str, int]:
    assert asset_id is not None
    assert provider_job_id is not None
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                (SELECT count(*)
                 FROM cost_ledger_entries
                 WHERE job_id = %s AND usage ->> 'kind' = 'settlement'),
                (SELECT count(*) FROM audit_events WHERE job_id = %s AND trace_id = %s),
                (SELECT count(*) FROM provenance_records WHERE job_id = %s AND trace_id = %s),
                (SELECT count(*) FROM asset_provenance WHERE asset_id = %s),
                (SELECT count(*) FROM creative_provider_webhook_receipts WHERE provider_job_id = %s)
            """,
            (job_id, job_id, trace_id, job_id, trace_id, asset_id, provider_job_id),
        )
        row = cursor.fetchone()
    assert row is not None
    return {
        "actual_cost_entries": int(row[0]),
        "audit_records": int(row[1]),
        "provenance_records": int(row[2]),
        "asset_provenance_records": int(row[3]),
        "webhook_receipts": int(row[4]),
    }


def _assert_ready_package_is_immutable(database_url: str, ready_package_id: str) -> None:
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT candidate.id
            FROM title_thumbnail_candidates candidate
            JOIN ready_to_publish_packages ready
              ON ready.distribution_package_id = candidate.distribution_package_id
            WHERE ready.id = %s
            """,
            (ready_package_id,),
        )
        row = cursor.fetchone()
        assert row is not None
        with pytest.raises(
            psycopg.errors.RaiseException,
            match="immutable approved distribution decision",
        ):
            cursor.execute(
                "UPDATE title_thumbnail_candidates SET title = 'changed' WHERE id = %s",
                (row[0],),
            )


def _release_gate_actions(database_url: str, *, job_id: str, trace_id: str) -> set[str]:
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT action FROM audit_events WHERE job_id = %s AND trace_id = %s",
            (job_id, trace_id),
        )
        return {str(action) for (action,) in cursor.fetchall()}
