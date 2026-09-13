import pytest

from salience.creative.contracts import CreativeCapabilityRequest
from salience.creative.providers import (
    FixtureCreativeProvider,
    ProviderWebhookRejected,
    ReplacementFixtureCreativeProvider,
)


def _request() -> CreativeCapabilityRequest:
    return CreativeCapabilityRequest(
        request_key="video-42",
        content_program_id="program-1",
        brief_id="brief-1",
        script_id="script-1",
        capability="text_to_video",
        expected_modality="video",
        aspect_ratio="9:16",
        resolution="1080x1920",
        duration_seconds=15,
        max_variants=1,
    )


@pytest.mark.asyncio
async def test_fixture_provider_returns_one_external_id_per_idempotency_key() -> None:
    provider = FixtureCreativeProvider()

    first = await provider.submit(_request())
    second = await provider.submit(_request())
    running = await provider.get_status(first.external_job_id)
    completed = await provider.get_status(first.external_job_id)

    assert first.external_job_id == second.external_job_id
    assert (first.state, running.state, completed.state) == ("submitted", "running", "completed")
    assert await provider.download(first.external_job_id) == await provider.download(second.external_job_id)


@pytest.mark.asyncio
async def test_fixture_provider_rejects_unsigned_webhook_and_replacement_preserves_capability() -> None:
    fixture = FixtureCreativeProvider()
    replacement = ReplacementFixtureCreativeProvider()
    fixture_result = await fixture.submit(_request())
    replacement_result = await replacement.submit(_request())

    with pytest.raises(ProviderWebhookRejected, match="signature"):
        await fixture.verify_webhook({"id": fixture_result.external_job_id}, signature=None)

    accepted = await fixture.verify_webhook(
        {"id": fixture_result.external_job_id, "status": "completed"}, signature="fixture-signature"
    )
    assert accepted.state == "completed"
    assert replacement_result.provider_id == "replacement-fixture-creative"
    assert replacement_result.capability == fixture_result.capability == "text_to_video"
