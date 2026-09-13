import httpx
import pytest

from salience.creative.contracts import CreativeCapabilityRequest
from salience.creative.providers import ProviderResponseError, SynthesiaCreativeProvider
from salience.governance.secrets import SecretReference, SecretResolver


def _request() -> CreativeCapabilityRequest:
    return CreativeCapabilityRequest(
        request_key="synthesia-42",
        content_program_id="program-1",
        brief_id="brief-1",
        script_id="script-1",
        capability="avatar_video",
        expected_modality="video",
        aspect_ratio="16:9",
        resolution="1920x1080",
        duration_seconds=15,
        max_variants=1,
        provider_extension={"script_text": "A governed fixture script", "avatar": "anna"},
    )


@pytest.mark.asyncio
async def test_synthesia_adapter_maps_submit_without_leaking_authorization() -> None:
    observed: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["authorization"] = request.headers["Authorization"]
        observed["path"] = request.url.path
        observed["payload"] = request.content
        return httpx.Response(201, json={"id": "synthesia-video-42"})

    provider = SynthesiaCreativeProvider(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        secret_resolver=SecretResolver({"env://SYNTHESIA": "very-secret-token"}),
        secret_reference=SecretReference(
            "env://SYNTHESIA", frozenset({"creative.provider.synthesia"})
        ),
        scopes=frozenset({"creative.provider.synthesia"}),
    )

    result = await provider.submit(_request())

    assert result.state == "submitted"
    assert result.external_job_id == "synthesia-video-42"
    assert observed["path"] == "/v2/videos"
    assert "very-secret-token" not in repr(result)
    await provider.aclose()


@pytest.mark.asyncio
async def test_synthesia_adapter_normalizes_provider_rejection() -> None:
    provider = SynthesiaCreativeProvider(
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(429, headers={"Retry-After": "10"}))
        ),
        secret_resolver=SecretResolver({"env://SYNTHESIA": "very-secret-token"}),
        secret_reference=SecretReference(
            "env://SYNTHESIA", frozenset({"creative.provider.synthesia"})
        ),
        scopes=frozenset({"creative.provider.synthesia"}),
    )

    with pytest.raises(ProviderResponseError, match="rate_limited"):
        await provider.submit(_request())
    await provider.aclose()
