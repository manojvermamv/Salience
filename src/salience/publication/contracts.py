"""Owned, provider-neutral DTOs for governed publication."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


PublicationState = Literal[
    "planned",
    "authorized",
    "submitting",
    "accepted",
    "processing",
    "published",
    "failed",
    "cancelled",
    "dead_lettered",
    "ambiguous_requires_reconciliation",
]


class PublisherAccount(BaseModel):
    model_config = ConfigDict(frozen=True)

    contract_version: Literal["PublisherAccount@v1"] = "PublisherAccount@v1"
    id: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(min_length=1, max_length=128)
    platform: str = Field(min_length=1, max_length=64)
    account_type: str = Field(min_length=1, max_length=64)
    external_account_reference: str = Field(min_length=1, max_length=255)
    status: Literal["active", "disabled", "revoked"] = "active"


class PublisherConnection(BaseModel):
    model_config = ConfigDict(frozen=True)

    contract_version: Literal["PublisherConnection@v1"] = "PublisherConnection@v1"
    id: str = Field(min_length=1, max_length=128)
    publisher_account_id: str = Field(min_length=1, max_length=128)
    version: int = Field(gt=0)
    secret_reference: str = Field(min_length=8, max_length=1024)
    required_scopes: tuple[str, ...] = Field(min_length=1)
    granted_scopes: tuple[str, ...] = ()
    status: Literal["active", "expired", "revoked", "disabled"]
    expires_at: datetime | None = None
    refresh_after: datetime | None = None


class PublisherCapabilityProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    contract_version: Literal["PublisherCapabilityProfile@v1"] = "PublisherCapabilityProfile@v1"
    publisher_id: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=64)
    platform: str = Field(min_length=1, max_length=64)
    account_types: tuple[str, ...] = Field(min_length=1)
    contract_compatibility: dict[str, str] = Field(min_length=1)
    enabled: bool
    audit_state: Literal["unverified", "verified", "suspended"]
    granted_scopes: tuple[str, ...]
    supported_content_types: tuple[str, ...] = Field(min_length=1)
    supported_visibilities: tuple[Literal["private", "unlisted", "public"], ...] = Field(min_length=1)
    disclosure_support: bool
    scheduling_support: bool
    cancellation_support: bool
    reconciliation_support: bool
    quota_state: Literal["available", "exhausted", "unavailable"]
    health_state: Literal["healthy", "degraded", "unavailable"]


class PublicationRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    contract_version: Literal["PublicationRequest@v1"] = "PublicationRequest@v1"
    id: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(min_length=1, max_length=128)
    content_program_id: str = Field(min_length=1, max_length=128)
    ready_package_id: str = Field(min_length=1, max_length=128)
    publisher_account_id: str = Field(min_length=1, max_length=128)
    platform: str = Field(min_length=1, max_length=64)
    destination: str = Field(min_length=1, max_length=255)
    locale: str = Field(min_length=1, max_length=32)
    territory: str = Field(min_length=1, max_length=64)
    visibility: Literal["private", "unlisted", "public"]
    capability_profile_version: int = Field(gt=0)
    idempotency_key: str = Field(min_length=1, max_length=255)
    approval_reference: str = Field(min_length=1, max_length=128)
    scheduled_for: datetime | None = None
    publisher_id: str | None = Field(default=None, min_length=1, max_length=128)


class PublicationPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    contract_version: Literal["PublicationPlan@v1"] = "PublicationPlan@v1"
    id: str = Field(min_length=1, max_length=128)
    publication_request_id: str = Field(min_length=1, max_length=128)
    version: int = Field(gt=0)
    publisher_id: str = Field(min_length=1, max_length=128)
    publisher_version: str = Field(min_length=1, max_length=64)
    external_effect_id: str = Field(min_length=1, max_length=128)
    budget_reservation_id: str | None = None
    state: PublicationState = "planned"


class PublicationAttempt(BaseModel):
    model_config = ConfigDict(frozen=True)

    contract_version: Literal["PublicationAttempt@v1"] = "PublicationAttempt@v1"
    id: str = Field(min_length=1, max_length=128)
    publication_plan_id: str = Field(min_length=1, max_length=128)
    attempt_number: int = Field(gt=0)
    state: PublicationState
    idempotency_key: str = Field(min_length=1, max_length=255)


class RemotePublicationReceipt(BaseModel):
    model_config = ConfigDict(frozen=True)

    contract_version: Literal["RemotePublicationReceipt@v1"] = "RemotePublicationReceipt@v1"
    id: str = Field(min_length=1, max_length=128)
    publication_attempt_id: str = Field(min_length=1, max_length=128)
    publisher_id: str = Field(min_length=1, max_length=128)
    remote_id: str = Field(min_length=1, max_length=255)
    state: PublicationState
    safe_metadata_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    remote_url: str | None = None


class Publication(BaseModel):
    model_config = ConfigDict(frozen=True)

    contract_version: Literal["Publication@v1"] = "Publication@v1"
    id: str = Field(min_length=1, max_length=128)
    publication_request_id: str = Field(min_length=1, max_length=128)
    remote_receipt_id: str = Field(min_length=1, max_length=128)
    state: Literal["published", "failed", "cancelled"]


class CredentialLease:
    """Ephemeral edge-only credential material that cannot cross DTO boundaries."""

    def __init__(self, value: str, *, expires_at: datetime, granted_scopes: frozenset[str]) -> None:
        if not value:
            raise ValueError("credential lease value is required")
        self._value = value
        self.expires_at = expires_at
        self.granted_scopes = granted_scopes

    def reveal(self) -> str:
        return self._value

    def model_dump(self, *_args: object, **_kwargs: object) -> Mapping[str, object]:
        raise TypeError("CredentialLease must not be serialized")

    def __repr__(self) -> str:
        return f"CredentialLease(expires_at={self.expires_at.isoformat()}, scopes=<redacted>)"

    __str__ = __repr__


class PublisherAdapter(Protocol):
    @property
    def capabilities(self) -> PublisherCapabilityProfile: ...

    async def preflight(self, request: PublicationRequest) -> None: ...

    async def submit(self, request: PublicationRequest, lease: CredentialLease) -> RemotePublicationReceipt: ...

    async def reconcile(self, idempotency_key: str) -> RemotePublicationReceipt | None: ...

