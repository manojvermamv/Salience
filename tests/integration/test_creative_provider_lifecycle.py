"""Canonical lifecycle transition contracts for asynchronous creative providers."""

import os
from uuid import uuid4

import pytest

from salience.creative.repository import CreativeRepository


async def _provider_job() -> tuple[CreativeRepository, str]:
    from test_creative_cost_lifecycle import _creative_effect

    effect = await _creative_effect()
    repository = CreativeRepository(os.environ["TEST_DATABASE_URL"])
    provider_job_id = await repository.record_provider_job(
        creative_job_id=effect["creative_job_id"],
        provider_id="fixture-lifecycle",
        provider_version="1.0.0",
        model_id="fixture-v1",
        external_job_id=f"fixture-lifecycle-{uuid4()}",
        state="submitted",
        normalized_request={"capability": "text_to_video"},
        estimated_cost_micros=100,
        trace_id="trace-provider-lifecycle",
    )
    return repository, provider_job_id


@pytest.mark.asyncio
async def test_provider_lifecycle_transitions_are_conditional_and_terminal() -> None:
    repository, provider_job_id = await _provider_job()

    running = await repository.transition_provider_job(
        provider_job_id=provider_job_id,
        state="running",
        trace_id="trace-provider-running",
        next_poll_after_seconds=1,
    )
    completed = await repository.transition_provider_job(
        provider_job_id=provider_job_id,
        state="completed",
        trace_id="trace-provider-completed",
        actual_cost_micros=90,
    )

    assert running.state == "running"
    assert running.next_poll_after is not None
    assert completed.state == "completed"
    assert completed.actual_cost_status == "known"
    with pytest.raises(ValueError, match="terminal"):
        await repository.transition_provider_job(
            provider_job_id=provider_job_id,
            state="running",
            trace_id="trace-provider-invalid",
        )


@pytest.mark.asyncio
async def test_provider_cancellation_request_precedes_cancelled_terminal_state() -> None:
    repository, provider_job_id = await _provider_job()

    requested = await repository.request_provider_cancellation(
        provider_job_id=provider_job_id, trace_id="trace-provider-cancel-request"
    )
    cancelled = await repository.transition_provider_job(
        provider_job_id=provider_job_id,
        state="cancelled",
        trace_id="trace-provider-cancelled",
    )

    assert requested.cancel_requested_at is not None
    assert cancelled.state == "cancelled"
    assert cancelled.terminal_at is not None
