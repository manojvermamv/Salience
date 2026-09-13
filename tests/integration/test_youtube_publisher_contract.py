"""Owned, private-only YouTube resumable-upload adapter contracts."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from salience.publication.contracts import CredentialLease, PublicationRequest, PublisherAdapter
from salience.publication.youtube import YouTubePublisherAdapter, YouTubeUploadRequest


def _publication(*, visibility: str = "private") -> PublicationRequest:
    return PublicationRequest(
        id="publication-attempt-1",
        workspace_id="workspace-1",
        content_program_id="program-1",
        ready_package_id="ready-package-1",
        publisher_account_id="publisher-account-1",
        platform="youtube",
        destination="youtube://channel-1",
        locale="en",
        territory="global",
        visibility=visibility,
        capability_profile_version=1,
        idempotency_key="youtube-upload-1",
        approval_reference="approval-1",
        publisher_id="youtube-publisher",
    )


def _lease() -> CredentialLease:
    return CredentialLease(
        "test-youtube-bearer-token",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        granted_scopes=frozenset({"https://www.googleapis.com/auth/youtube.upload"}),
    )


@pytest.mark.asyncio
async def test_youtube_adapter_starts_private_resumable_session_without_persisting_bearer_token() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("Authorization")
        seen["content_type"] = request.headers.get("X-Upload-Content-Type")
        return httpx.Response(
            200,
            headers={"Location": "https://www.googleapis.com/upload/resumable/session-secret"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = YouTubePublisherAdapter(
            client=client,
            connection_reference="secret://youtube/private-test-connection",
            enabled=True,
        )
        attempt = await adapter.prepare_upload(
            YouTubeUploadRequest(
                publication=_publication(),
                delivery_url="https://delivery.example/assets/ready-package-1",
                content_length=1_024,
                content_type="video/mp4",
                title="Private test upload",
                description="Deterministic integration contract",
            ),
            _lease(),
        )

    assert seen["method"] == "POST"
    assert "uploadType=resumable" in str(seen["url"])
    assert seen["authorization"] == "Bearer test-youtube-bearer-token"
    assert seen["content_type"] == "video/mp4"
    assert attempt.upload_session_id
    assert "Bearer" not in attempt.model_dump_json()
    assert "test-youtube-bearer-token" not in attempt.model_dump_json()
    assert "session-secret" not in attempt.model_dump_json()
    assert adapter.capabilities.supported_visibilities == ("private",)
    assert isinstance(adapter, PublisherAdapter)


@pytest.mark.asyncio
async def test_youtube_adapter_fails_closed_for_non_private_uploads_without_http() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: pytest.fail(str(request.url)))
    ) as client:
        adapter = YouTubePublisherAdapter(
            client=client,
            connection_reference="secret://youtube/private-test-connection",
            enabled=True,
        )
        with pytest.raises(ValueError, match="private"):
            await adapter.prepare_upload(
                YouTubeUploadRequest(
                    publication=_publication(visibility="unlisted"),
                    delivery_url="https://delivery.example/assets/ready-package-1",
                    content_length=1_024,
                    content_type="video/mp4",
                    title="Private test upload",
                    description="Deterministic integration contract",
                ),
                _lease(),
            )
