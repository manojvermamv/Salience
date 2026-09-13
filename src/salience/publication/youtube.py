"""Private-only YouTube Data API resumable-upload boundary.

This module deliberately starts the official resumable session only. Streaming
private object bytes to the opaque session URI remains an edge capability; the
canonical publication store receives neither bearer material nor that URI.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from salience.publication.contracts import (
    CredentialLease,
    PublicationRequest,
    PublisherCapabilityProfile,
    PublisherWebhookEvent,
    RemotePublicationReceipt,
)


YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
_YOUTUBE_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"


class YouTubePublisherError(ValueError):
    pass


class YouTubeProviderResponseError(YouTubePublisherError):
    def __init__(self, status_code: int, reason: str) -> None:
        self.status_code = status_code
        self.reason = reason
        super().__init__(f"YouTube upload session request failed: {status_code} {reason}")


class YouTubeUploadRequest(BaseModel):
    """Ephemeral upload-start input; it is never a canonical publication row."""

    model_config = ConfigDict(frozen=True)

    publication: PublicationRequest
    delivery_url: str = Field(min_length=9, max_length=2048)
    content_length: int = Field(gt=0)
    content_type: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(max_length=5_000)
    category_id: str = Field(default="22", pattern=r"^[0-9]+$")

    @field_validator("delivery_url")
    @classmethod
    def _require_https_delivery(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("YouTube delivery capability must use HTTPS")
        return value

    @field_validator("content_type")
    @classmethod
    def _require_video_content_type(cls, value: str) -> str:
        if value != "application/octet-stream" and not value.startswith("video/"):
            raise ValueError("YouTube upload content type must be video/* or application/octet-stream")
        return value


class YouTubeUploadAttempt(BaseModel):
    """Safe session projection suitable for workflow state and canonical evidence."""

    model_config = ConfigDict(frozen=True)

    contract_version: str = "YouTubeUploadAttempt@v1"
    publisher_id: str = "youtube-publisher"
    publication_attempt_id: str = Field(min_length=1, max_length=128)
    upload_session_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: str = "session_started"


@dataclass(frozen=True, repr=False)
class _YouTubeEdgeSession:
    """Edge-only resumable session state; its URI must never enter a DTO or database row."""

    idempotency_key: str
    publication_attempt_id: str
    upload_session_id: str
    request_hash: str
    _location: str

    def __repr__(self) -> str:
        return (
            "_YouTubeEdgeSession("
            f"idempotency_key={self.idempotency_key!r}, "
            f"publication_attempt_id={self.publication_attempt_id!r}, "
            f"upload_session_id={self.upload_session_id!r}, request_hash=<redacted>, location=<redacted>)"
        )


class YouTubeSessionStore(Protocol):
    """Secure edge-state storage for opaque resumable locations, keyed by request identity."""

    async def load(self, idempotency_key: str) -> _YouTubeEdgeSession | None: ...

    async def save(self, session: _YouTubeEdgeSession) -> _YouTubeEdgeSession: ...


@runtime_checkable
class DurableYouTubeSessionStore(YouTubeSessionStore, Protocol):
    """Production edge-state storage for opaque resumable locations."""

    @property
    def is_durable(self) -> bool: ...


class InMemoryYouTubeSessionStore:
    """Test-only edge store; production supplies durable isolated edge storage."""

    def __init__(self) -> None:
        self._sessions: dict[str, _YouTubeEdgeSession] = {}

    @property
    def is_durable(self) -> bool:
        return False

    async def load(self, idempotency_key: str) -> _YouTubeEdgeSession | None:
        return self._sessions.get(idempotency_key)

    async def save(self, session: _YouTubeEdgeSession) -> _YouTubeEdgeSession:
        existing = self._sessions.setdefault(session.idempotency_key, session)
        if (
            existing.upload_session_id != session.upload_session_id
            or existing.request_hash != session.request_hash
        ):
            raise YouTubePublisherError("YouTube resumable session differs from idempotent request")
        return existing


class YouTubePublisherAdapter:
    """Owned HTTP adapter for official YouTube private resumable sessions.

    `enabled` is deliberately false by default. The generic publisher workflow
    does not select this adapter until a future edge uploader can pass the
    immutable package's media projection as `YouTubeUploadRequest`.
    """

    publisher_id = "youtube-publisher"
    publisher_version = "youtube-data-api-v3-resumable-1"

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        connection_reference: str,
        enabled: bool = False,
        session_store: DurableYouTubeSessionStore | None = None,
    ) -> None:
        if not connection_reference:
            raise ValueError("YouTube publisher requires a secret-reference-only connection identity")
        self._client = client
        self._connection_reference = connection_reference
        self._enabled = enabled
        if session_store is None or not isinstance(session_store, DurableYouTubeSessionStore):
            raise ValueError("YouTube publisher requires an explicit durable edge session store")
        if not session_store.is_durable:
            raise ValueError("YouTube publisher requires an explicit durable edge session store")
        self._session_store = session_store

    @property
    def capabilities(self) -> PublisherCapabilityProfile:
        return PublisherCapabilityProfile(
            publisher_id=self.publisher_id,
            version=self.publisher_version,
            platform="youtube",
            account_types=("creator",),
            contract_compatibility={"publication": "1.0"},
            enabled=self._enabled,
            audit_state="verified" if self._enabled else "unverified",
            granted_scopes=("publish:create", YOUTUBE_UPLOAD_SCOPE),
            supported_content_types=("video",),
            supported_visibilities=("private",),
            disclosure_support=True,
            scheduling_support=True,
            cancellation_support=False,
            reconciliation_support=True,
            quota_state="available" if self._enabled else "unavailable",
            health_state="healthy" if self._enabled else "unavailable",
        )

    async def preflight(self, request: PublicationRequest) -> None:
        if not self._enabled:
            raise YouTubePublisherError("YouTube publisher is disabled by default")
        if request.platform != "youtube" or request.publisher_id not in {None, self.publisher_id}:
            raise YouTubePublisherError("YouTube publication must select the YouTube publisher")
        if request.visibility != "private":
            raise YouTubePublisherError("YouTube publishing is restricted to private visibility")

    async def prepare_delivery(self, request: PublicationRequest, delivery_url: str) -> str:
        await self.preflight(request)
        if not delivery_url.startswith("https://"):
            raise YouTubePublisherError("YouTube delivery capability must use HTTPS")
        return delivery_url

    async def prepare_upload(
        self, request: YouTubeUploadRequest, lease: CredentialLease
    ) -> YouTubeUploadAttempt:
        await self.preflight(request.publication)
        self._require_upload_lease(lease)
        request_hash = _safe_request_hash(request)
        existing = await self._session_store.load(request.publication.idempotency_key)
        if existing is not None:
            if existing.request_hash != request_hash:
                raise YouTubePublisherError("YouTube resumable session differs from idempotent request")
            return YouTubeUploadAttempt(
                publication_attempt_id=existing.publication_attempt_id,
                upload_session_id=existing.upload_session_id,
            )
        metadata = {
            "snippet": {
                "title": request.title,
                "description": request.description,
                "categoryId": request.category_id,
            },
            "status": {"privacyStatus": "private"},
        }
        response = await self._client.post(
            _YOUTUBE_UPLOAD_URL,
            params={"uploadType": "resumable", "part": "snippet,status"},
            headers={
                "Authorization": f"Bearer {lease.reveal()}",
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Length": str(request.content_length),
                "X-Upload-Content-Type": request.content_type,
            },
            json=metadata,
        )
        self._raise_for_session_response(response)
        session_location = response.headers.get("Location")
        if session_location is None:
            raise YouTubeProviderResponseError(response.status_code, "missing resumable session location")
        self._validate_session_location(session_location)
        session_id = _safe_session_id(request.publication.idempotency_key, session_location)
        await self._session_store.save(
            _YouTubeEdgeSession(
                idempotency_key=request.publication.idempotency_key,
                publication_attempt_id=request.publication.id,
                upload_session_id=session_id,
                request_hash=request_hash,
                _location=session_location,
            )
        )
        return YouTubeUploadAttempt(
            publication_attempt_id=request.publication.id,
            upload_session_id=session_id,
        )

    async def create_or_resume(
        self, request: PublicationRequest, lease: CredentialLease
    ) -> RemotePublicationReceipt:
        return await self.submit(request, lease)

    async def submit(
        self, request: PublicationRequest, lease: CredentialLease
    ) -> RemotePublicationReceipt:
        await self.preflight(request)
        self._require_upload_lease(lease)
        existing = await self.reconcile(request.idempotency_key)
        if existing is not None:
            return existing
        raise YouTubePublisherError(
            "YouTube submission requires an explicit private YouTubeUploadRequest edge handoff"
        )

    async def reconcile(self, idempotency_key: str) -> RemotePublicationReceipt | None:
        session = await self._session_store.load(idempotency_key)
        if session is None:
            return None
        return RemotePublicationReceipt(
            id=f"youtube-session-{session.upload_session_id[:24]}",
            publication_attempt_id=session.publication_attempt_id,
            publisher_id=self.publisher_id,
            remote_id=session.upload_session_id,
            state="accepted",
            safe_metadata_hash=_safe_session_metadata_hash(session.upload_session_id),
        )

    async def status(self, remote_id: str) -> RemotePublicationReceipt | None:
        return None

    async def cancel(self, remote_id: str) -> RemotePublicationReceipt | None:
        return await self.status(remote_id)

    async def verify_webhook(self, webhook: object) -> PublisherWebhookEvent:
        raise YouTubePublisherError("YouTube webhook verification is not configured for this adapter")

    async def refresh_capabilities(self) -> PublisherCapabilityProfile:
        return self.capabilities

    @staticmethod
    def _require_upload_lease(lease: CredentialLease) -> None:
        if lease.expires_at <= datetime.now(UTC):
            raise YouTubePublisherError("YouTube credential lease has expired")
        if YOUTUBE_UPLOAD_SCOPE not in lease.granted_scopes:
            raise YouTubePublisherError("YouTube credential lease lacks youtube.upload scope")

    @staticmethod
    def _validate_session_location(session_location: str) -> None:
        parsed = httpx.URL(session_location)
        if parsed.scheme != "https" or parsed.host != "www.googleapis.com":
            raise YouTubeProviderResponseError(502, "unsafe resumable session location")

    @staticmethod
    def _raise_for_session_response(response: httpx.Response) -> None:
        if response.status_code in {200, 201}:
            return
        reason = "unknown"
        try:
            payload = response.json()
            if isinstance(payload, dict):
                error = payload.get("error")
                if isinstance(error, dict) and isinstance(error.get("status"), str):
                    reason = error["status"]
        except (json.JSONDecodeError, ValueError):
            pass
        raise YouTubeProviderResponseError(response.status_code, reason)


def run_live_smoke() -> str:
    """Return an explicit live-test status without ever making a public post."""

    if not os.environ.get("YOUTUBE_PUBLISHER_CONNECTION_REF"):
        return "NOT RUN: YOUTUBE_PUBLISHER_CONNECTION_REF is not configured"
    if os.environ.get("YOUTUBE_PUBLISHER_ENABLE_LIVE_SMOKE") != "true":
        return "NOT RUN: YOUTUBE_PUBLISHER_ENABLE_LIVE_SMOKE is not true"
    if os.environ.get("YOUTUBE_PUBLISHER_VISIBILITY", "private") != "private":
        return "NOT RUN: YOUTUBE_PUBLISHER_VISIBILITY must be private"
    return "NOT RUN: private test asset and operator-managed credential lease are not configured"


def _safe_session_id(idempotency_key: str, session_location: str) -> str:
    return hashlib.sha256(f"{idempotency_key}:{session_location}".encode()).hexdigest()


def _safe_request_hash(request: YouTubeUploadRequest) -> str:
    return hashlib.sha256(
        request.model_dump_json(exclude={"delivery_url"}).encode()
    ).hexdigest()


def _safe_session_metadata_hash(upload_session_id: str) -> str:
    return hashlib.sha256(f"youtube-session:{upload_session_id}".encode()).hexdigest()
