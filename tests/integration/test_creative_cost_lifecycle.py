"""PostgreSQL cost reservation and settlement contracts for creative effects."""

import os
from uuid import uuid4

import psycopg
import pytest

from salience.creative.repository import CreativeRepository
from salience.workflows.persistence import CanonicalJobStore


async def _creative_effect() -> dict[str, str]:
    from test_creative_repository import _seed_brief

    seeded = await _seed_brief()
    store = CanonicalJobStore(os.environ["TEST_DATABASE_URL"])
    run = await store.create_creative_run(
        workflow_run_id=f"creative-cost-{uuid4()}",
        task_queue="creative-cost-test",
        workspace_id=seeded["workspace_id"],
        content_program_id=seeded["program_id"],
        brief_id=seeded["brief_id"],
        idempotency_key=f"creative-cost-{uuid4()}",
        dry_run=False,
    )
    repository = CreativeRepository(os.environ["TEST_DATABASE_URL"])
    script_id = await repository.record_script(
        workspace_id=seeded["workspace_id"],
        program_id=seeded["program_id"],
        brief_id=seeded["brief_id"],
        script_key="creative-cost-script",
        version=1,
        status="approved",
        target_format="short_video",
        target_duration_seconds=30,
        script={"sections": [{"text": "Cost lifecycle evidence"}]},
        claim_ids=[seeded["claim_id"]],
        evidence_ids=[seeded["evidence_id"]],
        trace_id=run.trace_context.trace_id,
    )
    creative_job_id = await repository.record_creative_job(
        workspace_id=seeded["workspace_id"],
        program_id=seeded["program_id"],
        brief_id=seeded["brief_id"],
        script_id=script_id,
        job_id=str(run.job_id),
        requested_capability="text_to_video",
        request_fingerprint=f"creative-cost-fingerprint-{uuid4()}",
        idempotency_key=f"creative-cost-request-{uuid4()}",
        request={"capability": "text_to_video"},
        timeout_seconds=60,
        trace_id=run.trace_context.trace_id,
    )
    effect_key = f"creative-cost-effect-{uuid4()}"
    await store.plan_effect(run, effect_key)
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO budgets (workspace_id, content_program_id, name, scope, limit_amount, status)
            VALUES (%s, %s, %s, 'creative', 0.000100, 'active')
            RETURNING id::text
            """,
            (seeded["workspace_id"], seeded["program_id"], f"creative-cost-{uuid4()}"),
        )
        budget_id = cursor.fetchone()[0]
        cursor.execute(
            """
            SELECT id::text FROM external_effects
            WHERE workspace_id = %s AND idempotency_key = %s
            """,
            (seeded["workspace_id"], effect_key),
        )
        external_effect_id = cursor.fetchone()[0]
    return {
        "budget_id": budget_id,
        "reservation_key": f"creative-cost-reservation-{uuid4()}",
        "job_id": str(run.job_id),
        "external_effect_id": external_effect_id,
        "creative_job_id": creative_job_id,
    }


@pytest.mark.asyncio
async def test_reserve_for_effect_is_idempotent_after_restart() -> None:
    from salience.governance.cost_repository import CostReservationRepository

    creative_effect = await _creative_effect()
    repository = CostReservationRepository(os.environ["TEST_DATABASE_URL"])

    first = await repository.reserve_for_effect(**creative_effect, estimated_micros=100)
    second = await repository.reserve_for_effect(**creative_effect, estimated_micros=100)

    assert second.reservation_id == first.reservation_id
    assert await repository.reservation_count(effect_id=creative_effect["external_effect_id"]) == 1


@pytest.mark.asyncio
async def test_settlement_records_overage_once_and_blocks_finalization() -> None:
    from salience.governance.cost_repository import CostReservationRepository

    creative_effect = await _creative_effect()
    repository = CostReservationRepository(os.environ["TEST_DATABASE_URL"])
    reservation = await repository.reserve_for_effect(**creative_effect, estimated_micros=100)

    outcome = await repository.settle(reservation.reservation_id, actual_micros=120)

    assert outcome.status == "overage_pending_approval"
    assert await repository.settle(reservation.reservation_id, actual_micros=120) == outcome


@pytest.mark.asyncio
async def test_release_after_settlement_returns_the_original_terminal_outcome() -> None:
    from salience.governance.cost_repository import CostReservationRepository

    creative_effect = await _creative_effect()
    repository = CostReservationRepository(os.environ["TEST_DATABASE_URL"])
    reservation = await repository.reserve_for_effect(**creative_effect, estimated_micros=100)
    settled = await repository.settle(reservation.reservation_id, actual_micros=80)

    assert await repository.release_unused(reservation.reservation_id) == settled
