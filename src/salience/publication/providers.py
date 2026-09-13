"""Deterministic, provider-neutral publisher fixtures for governed workflows."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from salience.publication.contracts import (
    CredentialLease,
    PublicationRequest,
    PublisherCapabilityProfile,
    PublisherWebhookEvent,
    RemotePublicationReceipt,
)


class FixturePublisherError(RuntimeError):
    pass


@dataclass(frozen=True)
class SignedFixtureWebhook:
    publisher_id: str
    delivery_identity: str
    remote_id: str
    state: str
    safe_payload_hash: str
    signature: str


class FixturePublisherAdapter:
    """CI-safe remote publisher that makes idempotency and recovery observable."""

    _SCENARIOS = frozenset(
        {
            "accepted",
            "crash_after_acceptance",
            "processing",
            "published",
            "failed",
            "cancelled",
            "quota",
            "timeout",
            "ambiguous",
        }
    )

    def __init__(self, *, scenario: str = "accepted", signing_key: str = "fixture-webhook-key") -> None:
        if scenario not in self._SCENARIOS:
            raise ValueError(f"unsupported fixture scenario: {scenario}")
        self._scenario = scenario
        self._signing_key = signing_key.encode()
        self._submissions: dict[str, RemotePublicationReceipt] = {}
        self._status_calls: dict[str, int] = {}
        self._submit_count = 0

    @property
    def submit_count(self) -> int:
        return self._submit_count

    @property
    def capabilities(self) -> PublisherCapabilityProfile:
        return PublisherCapabilityProfile(
            publisher_id="fixture-publisher",
            version="1",
            platform="fixture",
            account_types=("creator",),
            contract_compatibility={"publication": "1.0"},
            enabled=True,
            audit_state="verified",
            granted_scopes=("publish:create",),
            supported_content_types=("video",),
            supported_visibilities=("private",),
            disclosure_support=True,
            scheduling_support=True,
            cancellation_support=True,
            reconciliation_support=True,
            quota_state="available",
            health_state="healthy",
        )

    async def preflight(self, request: PublicationRequest) -> None:
        if request.platform != "fixture" or request.visibility != "private":
            raise FixturePublisherError("fixture provider requires private fixture publication")

    async def prepare_delivery(self, request: PublicationRequest, delivery_url: str) -> str:
        await self.preflight(request)
        if not delivery_url.startswith("https://"):
            raise FixturePublisherError("publisher delivery must use HTTPS")
        return delivery_url

    async def create_or_resume(
        self, request: PublicationRequest, lease: CredentialLease
    ) -> RemotePublicationReceipt:
        return await self.submit(request, lease)

    async def submit(
        self, request: PublicationRequest, lease: CredentialLease
    ) -> RemotePublicationReceipt:
        await self.preflight(request)
        if lease.expires_at <= datetime.now(UTC):
            raise FixturePublisherError("credential lease has expired")
        if "publish:create" not in lease.granted_scopes:
            raise FixturePublisherError("credential lease lacks publish:create")
        existing = self._submissions.get(request.idempotency_key)
        if existing is not None:
            return existing
        if self._scenario == "quota":
            raise FixturePublisherError("quota exhausted")
        if self._scenario == "timeout":
            raise FixturePublisherError("timeout requires reconciliation")
        if self._scenario == "ambiguous":
            raise FixturePublisherError("ambiguous outcome requires reconciliation")
        remote_id = f"fixture-{_hash(request.idempotency_key)[:24]}"
        receipt = RemotePublicationReceipt(
            id=f"fixture-receipt-{_hash(remote_id)[:24]}",
            publication_attempt_id=request.id,
            publisher_id="fixture-publisher",
            remote_id=remote_id,
            state="accepted" if self._scenario in {"accepted", "crash_after_acceptance", "processing", "published"} else self._scenario,
            safe_metadata_hash=_hash(
                {
                    "request_id": request.id,
                    "idempotency_key": request.idempotency_key,
                    "remote_id": remote_id,
                }
            ),
            remote_url=f"https://fixture.invalid/publications/{remote_id}",
        )
        self._submissions[request.idempotency_key] = receipt
        self._submit_count += 1
        return receipt

    async def reconcile(self, idempotency_key: str) -> RemotePublicationReceipt | None:
        return self._submissions.get(idempotency_key)

    async def status(self, remote_id: str) -> RemotePublicationReceipt | None:
        receipt = next(
            (item for item in self._submissions.values() if item.remote_id == remote_id),
            None,
        )
        if receipt is None:
            return None
        polls = self._status_calls.get(remote_id, 0) + 1
        self._status_calls[remote_id] = polls
        if self._scenario in {"failed", "cancelled"}:
            state = self._scenario
        elif self._scenario == "processing":
            state = "processing"
        elif self._scenario in {"accepted", "crash_after_acceptance", "published"}:
            state = "processing" if polls == 1 else "published"
        else:
            state = receipt.state
        return receipt.model_copy(update={"state": state})

    async def cancel(self, remote_id: str) -> RemotePublicationReceipt | None:
        receipt = await self.status(remote_id)
        if receipt is None:
            return None
        return receipt.model_copy(update={"state": "cancelled"})

    async def refresh_capabilities(self) -> PublisherCapabilityProfile:
        return self.capabilities

    def signed_webhook(
        self,
        receipt: RemotePublicationReceipt,
        *,
        state: str,
        delivery_identity: str,
    ) -> SignedFixtureWebhook:
        payload = {
            "publisher_id": receipt.publisher_id,
            "delivery_identity": delivery_identity,
            "remote_id": receipt.remote_id,
            "state": state,
        }
        serialized = _canonical_json(payload)
        return SignedFixtureWebhook(
            **payload,
            safe_payload_hash=_hash(serialized),
            signature=hmac.new(self._signing_key, serialized.encode(), hashlib.sha256).hexdigest(),
        )

    async def verify_webhook(self, webhook: object) -> PublisherWebhookEvent:
        if isinstance(webhook, Mapping):
            try:
                webhook = SignedFixtureWebhook(**webhook)
            except TypeError as error:
                raise FixturePublisherError("invalid fixture webhook payload") from error
        if not isinstance(webhook, SignedFixtureWebhook):
            raise FixturePublisherError("unsupported fixture webhook payload")
        payload = {
            "publisher_id": webhook.publisher_id,
            "delivery_identity": webhook.delivery_identity,
            "remote_id": webhook.remote_id,
            "state": webhook.state,
        }
        serialized = _canonical_json(payload)
        expected_signature = hmac.new(
            self._signing_key, serialized.encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(webhook.signature, expected_signature):
            raise FixturePublisherError("invalid webhook signature")
        safe_payload_hash = _hash(serialized)
        if not hmac.compare_digest(webhook.safe_payload_hash, safe_payload_hash):
            raise FixturePublisherError("invalid webhook payload hash")
        return PublisherWebhookEvent(
            publisher_id=webhook.publisher_id,
            delivery_identity=webhook.delivery_identity,
            remote_id=webhook.remote_id,
            state=webhook.state,
            safe_payload_hash=safe_payload_hash,
        )


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: object) -> str:
    serialized = value if isinstance(value, str) else _canonical_json(value)
    return hashlib.sha256(serialized.encode()).hexdigest()
