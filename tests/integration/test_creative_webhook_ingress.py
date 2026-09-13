"""Duplicate-safe canonical receipts for verified creative provider callbacks."""

import os
from uuid import uuid4

import pytest

from salience.creative.contracts import ProviderUsage, ProviderWebhookEvent
from salience.creative.repository import CreativeRepository


@pytest.mark.asyncio
async def test_verified_webhook_is_idempotent_and_converges_provider_state() -> None:
    from test_creative_cost_lifecycle import _creative_effect

    effect = await _creative_effect()
    repository = CreativeRepository(os.environ["TEST_DATABASE_URL"])
    external_job_id = f"webhook-job-{uuid4()}"
    provider_job_id = await repository.record_provider_job(
        creative_job_id=effect["creative_job_id"],
        provider_id="fixture-creative",
        provider_version="1.0.0",
        model_id="fixture-v1",
        external_job_id=external_job_id,
        state="running",
        normalized_request={"capability": "text_to_video"},
        estimated_cost_micros=0,
        trace_id="trace-webhook-provider",
    )
    event = ProviderWebhookEvent(
        provider_id="fixture-creative",
        delivery_id=f"delivery-{uuid4()}",
        external_job_id=external_job_id,
        state="completed",
        safe_payload_hash="a" * 64,
        usage=ProviderUsage(estimated_micros=0, actual_micros=0),
    )

    first = await repository.record_verified_webhook(event=event, trace_id="trace-webhook")
    second = await repository.record_verified_webhook(event=event, trace_id="trace-webhook")

    assert first.receipt_id == second.receipt_id
    assert first.provider_job_id == provider_job_id
    assert first.state == "completed"

    conflicting_delivery = event.model_copy(update={"safe_payload_hash": "b" * 64})
    with pytest.raises(ValueError, match="webhook receipt differs"):
        await repository.record_verified_webhook(
            event=conflicting_delivery, trace_id="trace-webhook"
        )
