"""Provider-neutral creative lifecycle contracts and safe fixture/REST adapters."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Awaitable, Callable, Protocol

import httpx

from salience.creative.contracts import (
    CreativeCapabilityRequest,
    CreativeProviderCapabilities,
    ProviderJobResult,
    ProviderUsage,
    ProviderWebhookEvent,
)
from salience.governance.secrets import SecretReference, SecretResolver


class ProviderError(RuntimeError):
    pass


class ProviderResponseError(ProviderError):
    def __init__(self, failure_class: str, *, retry_after: str | None = None) -> None:
        super().__init__(f"provider response failure: {failure_class}")
        self.failure_class = failure_class
        self.retry_after = retry_after


class ProviderWebhookRejected(ProviderError):
    pass


class CreativeProvider(Protocol):
    @property
    def capabilities(self) -> CreativeProviderCapabilities: ...

    async def submit(self, request: CreativeCapabilityRequest) -> ProviderJobResult: ...

    async def reconcile(self, request_key: str) -> ProviderJobResult | None: ...

    async def get_status(self, external_job_id: str) -> ProviderJobResult: ...

    async def cancel(self, external_job_id: str) -> ProviderJobResult: ...

    async def verify_webhook(
        self, payload: Mapping[str, Any], *, signature: str | None
    ) -> ProviderWebhookEvent: ...

    async def download(self, external_job_id: str) -> bytes: ...


@dataclass
class _FixtureJob:
    result: ProviderJobResult
    poll_count: int = 0


class FixtureCreativeProvider:
    """Deterministic provider fixture with explicit poll and webhook transitions."""

    provider_id = "fixture-creative"
    provider_version = "1.0.0"

    @property
    def capabilities(self) -> CreativeProviderCapabilities:
        return CreativeProviderCapabilities(
            supported_capabilities=("text_to_video",),
            modalities=("video",),
            formats=("video/mp4",),
            aspect_ratios=("9:16", "16:9"),
            minimum_duration_seconds=1,
            maximum_duration_seconds=60,
            async_support=True,
            polling_support=True,
            webhook_support=True,
            cancellation_support=True,
            reconciliation_support=True,
            enabled=True,
            contract_compatibility={"creative": "1.0"},
            max_concurrency=1,
            rate_state="available",
            estimated_cost_micros=0,
            limitations=("fixture only",),
        )

    def __init__(self) -> None:
        self._jobs_by_key: dict[str, _FixtureJob] = {}
        self._jobs_by_external_id: dict[str, _FixtureJob] = {}
        self._submit_count = 0

    @property
    def submit_count(self) -> int:
        return self._submit_count

    async def submit(self, request: CreativeCapabilityRequest) -> ProviderJobResult:
        existing = self._jobs_by_key.get(request.request_key)
        if existing is not None:
            return existing.result
        self._submit_count += 1
        external_job_id = self._external_job_id(request.request_key)
        result = ProviderJobResult(
            provider_id=self.provider_id,
            provider_version=self.provider_version,
            model_id="fixture-creative-v1",
            capability=request.capability,
            request_key=request.request_key,
            external_job_id=external_job_id,
            state="submitted",
            usage=ProviderUsage(estimated_micros=self.capabilities.estimated_cost_micros),
            metadata={"idempotency_key": request.request_key, "transport": "fixture"},
        )
        job = _FixtureJob(result)
        self._jobs_by_key[request.request_key] = job
        self._jobs_by_external_id[external_job_id] = job
        return result

    async def reconcile(self, request_key: str) -> ProviderJobResult | None:
        job = self._jobs_by_key.get(request_key)
        return job.result if job is not None else None

    async def get_status(self, external_job_id: str) -> ProviderJobResult:
        job = self._job(external_job_id)
        if job.result.state in {"completed", "failed", "cancelled"}:
            return job.result
        job.poll_count += 1
        state = "running" if job.poll_count == 1 else "completed"
        job.result = job.result.model_copy(
            update={
                "state": state,
                "download_reference": (
                    f"fixture://creative/{external_job_id}" if state == "completed" else None
                ),
            }
        )
        return job.result

    async def cancel(self, external_job_id: str) -> ProviderJobResult:
        job = self._job(external_job_id)
        job.result = job.result.model_copy(update={"state": "cancelled"})
        return job.result

    async def verify_webhook(
        self, payload: Mapping[str, Any], *, signature: str | None
    ) -> ProviderWebhookEvent:
        if signature != "fixture-signature":
            raise ProviderWebhookRejected("provider webhook signature was not accepted")
        external_job_id = payload.get("id")
        status = payload.get("status")
        if not isinstance(external_job_id, str) or not isinstance(status, str):
            raise ProviderWebhookRejected("provider webhook payload is malformed")
        job = self._job(external_job_id)
        state = _normalize_state(status)
        job.result = job.result.model_copy(
            update={
                "state": state,
                "download_reference": (
                    f"fixture://creative/{external_job_id}" if state == "completed" else None
                ),
            }
        )
        return ProviderWebhookEvent(
            provider_id=self.provider_id,
            delivery_id=_delivery_id(payload),
            external_job_id=external_job_id,
            state=state,
            safe_payload_hash=_safe_payload_hash(payload),
            usage=job.result.usage,
        )

    async def download(self, external_job_id: str) -> bytes:
        job = self._job(external_job_id)
        if job.result.state != "completed":
            raise ProviderResponseError("download_not_ready")
        return f"fixture-media:{external_job_id}".encode()

    def _external_job_id(self, request_key: str) -> str:
        fingerprint = sha256(f"{self.provider_id}:{request_key}".encode()).hexdigest()[:20]
        return f"{self.provider_id}-{fingerprint}"

    def _job(self, external_job_id: str) -> _FixtureJob:
        try:
            return self._jobs_by_external_id[external_job_id]
        except KeyError as error:
            raise KeyError(f"unknown provider job: {external_job_id}") from error


class ReplacementFixtureCreativeProvider(FixtureCreativeProvider):
    """Capability-equivalent fixture used to prove provider replacement portability."""

    provider_id = "replacement-fixture-creative"
    provider_version = "1.0.0"


WebhookVerifier = Callable[[Mapping[str, Any], str | None], bool | Awaitable[bool]]


class SynthesiaCreativeProvider:
    """Credential-gated owned DTO adapter for Synthesia's asynchronous video API."""

    provider_id = "synthesia"
    provider_version = "v2"

    @property
    def capabilities(self) -> CreativeProviderCapabilities:
        return CreativeProviderCapabilities(
            supported_capabilities=("avatar_video",),
            modalities=("video",),
            formats=("video/mp4",),
            aspect_ratios=("16:9", "9:16"),
            minimum_duration_seconds=1,
            maximum_duration_seconds=21_600,
            async_support=True,
            polling_support=True,
            webhook_support=self._webhook_verifier is not None,
            cancellation_support=True,
            reconciliation_support=False,
            enabled=self._secret_reference is not None,
            contract_compatibility={"creative": "1.0"},
            max_concurrency=1,
            rate_state="available",
            estimated_cost_micros=0,
            limitations=("reconciliation by idempotency key is unavailable",),
        )

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        secret_resolver: SecretResolver,
        secret_reference: SecretReference | None,
        scopes: frozenset[str],
        base_url: str = "https://api.synthesia.io",
        webhook_verifier: WebhookVerifier | None = None,
    ) -> None:
        self._client = client
        self._secret_resolver = secret_resolver
        self._secret_reference = secret_reference
        self._scopes = scopes
        self._base_url = base_url.rstrip("/")
        self._webhook_verifier = webhook_verifier
        self._jobs: dict[str, ProviderJobResult] = {}

    async def submit(self, request: CreativeCapabilityRequest) -> ProviderJobResult:
        authorization = self._authorization()
        response = await self._client.post(
            f"{self._base_url}/v2/videos",
            headers={
                "Authorization": authorization,
                "Idempotency-Key": request.request_key,
                "Content-Type": "application/json",
            },
            json=_synthesia_request(request),
        )
        self._raise_for_response(response)
        payload = _response_json(response)
        external_job_id = _external_job_id(payload)
        result = ProviderJobResult(
            provider_id=self.provider_id,
            provider_version=self.provider_version,
            model_id=_string_or_none(payload.get("model")),
            capability=request.capability,
            request_key=request.request_key,
            external_job_id=external_job_id,
            state="submitted",
            usage=ProviderUsage(estimated_micros=self.capabilities.estimated_cost_micros),
            metadata={
                "idempotency_key": request.request_key,
                "rate_limit": _rate_limit_metadata(response),
            },
        )
        self._jobs[external_job_id] = result
        return result

    async def reconcile(self, request_key: str) -> ProviderJobResult | None:
        # The documented video API retrieves by provider video ID, not callback/idempotency key.
        # An ambiguous accepted submission must therefore stay unresolved rather than be replayed.
        return None

    async def get_status(self, external_job_id: str) -> ProviderJobResult:
        response = await self._client.get(
            f"{self._base_url}/v2/videos/{external_job_id}", headers=self._headers()
        )
        self._raise_for_response(response)
        payload = _response_json(response)
        current = self._jobs.get(external_job_id)
        if current is None:
            raise KeyError(f"unknown provider job: {external_job_id}")
        state = _normalize_state(_string_or_none(payload.get("status")) or "running")
        result = current.model_copy(
            update={
                "state": state,
                "download_reference": _download_reference(payload) if state == "completed" else None,
                "metadata": {
                    **current.metadata,
                    "rate_limit": _rate_limit_metadata(response),
                },
            }
        )
        self._jobs[external_job_id] = result
        return result

    async def cancel(self, external_job_id: str) -> ProviderJobResult:
        response = await self._client.delete(
            f"{self._base_url}/v2/videos/{external_job_id}", headers=self._headers()
        )
        self._raise_for_response(response)
        current = self._jobs.get(external_job_id)
        if current is None:
            raise KeyError(f"unknown provider job: {external_job_id}")
        result = current.model_copy(update={"state": "cancelled"})
        self._jobs[external_job_id] = result
        return result

    async def verify_webhook(
        self, payload: Mapping[str, Any], *, signature: str | None
    ) -> ProviderWebhookEvent:
        if self._webhook_verifier is None:
            raise ProviderWebhookRejected("provider webhook signature verifier is not configured")
        accepted = self._webhook_verifier(payload, signature)
        if hasattr(accepted, "__await__"):
            accepted = await accepted
        if not accepted:
            raise ProviderWebhookRejected("provider webhook signature was not accepted")
        external_job_id = _external_job_id(payload)
        current = self._jobs.get(external_job_id)
        if current is None:
            raise KeyError(f"unknown provider job: {external_job_id}")
        result = current.model_copy(
            update={"state": _normalize_state(_string_or_none(payload.get("status")) or "running")}
        )
        self._jobs[external_job_id] = result
        return ProviderWebhookEvent(
            provider_id=self.provider_id,
            delivery_id=_delivery_id(payload),
            external_job_id=external_job_id,
            state=result.state,
            safe_payload_hash=_safe_payload_hash(payload),
            usage=result.usage,
        )

    async def download(self, external_job_id: str) -> bytes:
        current = self._jobs.get(external_job_id)
        if current is None or current.state != "completed" or not current.download_reference:
            raise ProviderResponseError("download_not_ready")
        response = await self._client.get(current.download_reference)
        self._raise_for_response(response)
        return response.content

    async def aclose(self) -> None:
        await self._client.aclose()

    def _authorization(self) -> str:
        if self._secret_reference is None:
            raise ProviderError("Synthesia provider is disabled without a secret reference")
        return self._secret_resolver.resolve(self._secret_reference, self._scopes).reveal()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": self._authorization()}

    @staticmethod
    def _raise_for_response(response: httpx.Response) -> None:
        if not response.is_error:
            return
        failure_class = (
            "rate_limited"
            if response.status_code == 429
            else "access_denied"
            if response.status_code in {401, 403}
            else "provider_rejected"
            if 400 <= response.status_code < 500
            else "provider_unavailable"
        )
        raise ProviderResponseError(
            failure_class, retry_after=response.headers.get("retry-after")
        )


def _synthesia_request(request: CreativeCapabilityRequest) -> dict[str, object]:
    extension = request.provider_extension
    script_text = extension.get("script_text")
    if not isinstance(script_text, str):
        script_text = ""
    input_item: dict[str, object] = {"scriptText": script_text}
    for key in ("avatar", "background", "voice"):
        if key in extension:
            input_item[key] = extension[key]
    return {
        "title": request.request_key,
        "input": [input_item],
        "test": extension.get("test") is True,
        "metadata": {"salience_request_key": request.request_key},
    }


def _response_json(response: httpx.Response) -> Mapping[str, Any]:
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError) as error:
        raise ProviderResponseError("malformed_response") from error
    if not isinstance(payload, Mapping):
        raise ProviderResponseError("malformed_response")
    return payload


def _external_job_id(payload: Mapping[str, Any]) -> str:
    value = payload.get("id")
    if not isinstance(value, str):
        video = payload.get("video")
        value = video.get("id") if isinstance(video, Mapping) else None
    if not isinstance(value, str) or not value:
        raise ProviderResponseError("malformed_response")
    return value


def _normalize_state(value: str) -> str:
    normalized = value.strip().casefold()
    if normalized in {"complete", "completed", "done", "succeeded"}:
        return "completed"
    if normalized in {"failed", "error", "rejected"}:
        return "failed"
    if normalized in {"cancelled", "canceled"}:
        return "cancelled"
    if normalized in {"submitted", "queued", "pending"}:
        return "submitted"
    return "running"


def _delivery_id(payload: Mapping[str, Any]) -> str | None:
    delivery_id = payload.get("delivery_id")
    if delivery_id is None:
        return None
    if not isinstance(delivery_id, str) or not delivery_id:
        raise ProviderWebhookRejected("provider webhook delivery identity is malformed")
    return delivery_id


def _safe_payload_hash(payload: Mapping[str, Any]) -> str:
    try:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    except (TypeError, ValueError) as error:
        raise ProviderWebhookRejected("provider webhook payload is not hashable") from error
    return sha256(encoded).hexdigest()


def _download_reference(payload: Mapping[str, Any]) -> str | None:
    for key in ("download", "download_url", "downloadUrl", "url"):
        value = payload.get(key)
        if isinstance(value, str) and value.startswith("https://"):
            return value
    return None


def _rate_limit_metadata(response: httpx.Response) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for key in ("retry-after", "x-ratelimit-reset"):
        value = response.headers.get(key)
        if value:
            metadata[key] = value
    return metadata


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None
