"""Provider-neutral recorder wrapper for the PublisherAdapter protocol."""

from __future__ import annotations

import asyncio
import json
from threading import Lock
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import UUID, uuid4

from salience.publication.contracts import (
    CredentialLease,
    PublicationRequest,
    PublisherAdapter,
    PublisherCapabilityProfile,
    PublisherWebhookEvent,
    RemotePublicationReceipt,
)


EVENT_SCHEMA = "salience.adapter-operation.v1"
OPERATIONS = (
    "capabilities",
    "preflight",
    "prepare_delivery",
    "create_or_resume",
    "submit",
    "reconcile",
    "status",
    "cancel",
    "verify_webhook",
    "refresh_capabilities",
)


def _base_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("adapter recorder requires a local HTTP receiver URL")
    return value.rstrip("/")


def _canonical_uuid(value: str) -> str:
    normalized = str(UUID(value))
    if normalized != value:
        raise ValueError("adapter recorder IDs must be canonical UUIDs")
    return normalized


def _post_event(base_url: str, event: dict[str, object]) -> None:
    body = (json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n").encode()
    request = Request(
        base_url + "/v1/adapter-events",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        if response.status != 202:
            raise RuntimeError("adapter operation receiver rejected an event")


class _AdapterOperationEventSink:
    """Share one run sequence across every adapter owned by a worker."""

    def __init__(
        self,
        *,
        receiver_url: str,
        run_id: str,
        worker_id: str | None = None,
    ) -> None:
        self.receiver_url = _base_url(receiver_url)
        self.run_id = _canonical_uuid(run_id)
        self.worker_id = _canonical_uuid(worker_id or str(uuid4()))
        self.sequence = 0
        self.lock = Lock()

    def record(self, operation: str) -> None:
        if operation not in OPERATIONS:
            raise ValueError("unknown PublisherAdapter operation")
        with self.lock:
            self.sequence += 1
            event = {
                "schema": EVENT_SCHEMA,
                "run_id": self.run_id,
                "worker_id": self.worker_id,
                "sequence": self.sequence,
                "operation": operation,
            }
        _post_event(self.receiver_url, event)


class RecordingPublisherAdapter:
    """Record neutral operation names before forwarding to an adapter."""

    def __init__(
        self,
        adapter: PublisherAdapter,
        *,
        receiver_url: str | None = None,
        run_id: str | None = None,
        worker_id: str | None = None,
        event_sink: _AdapterOperationEventSink | None = None,
    ) -> None:
        self._adapter = adapter
        if event_sink is None:
            if receiver_url is None or run_id is None:
                raise ValueError("adapter recorder URL and run ID are required")
            event_sink = _AdapterOperationEventSink(
                receiver_url=receiver_url,
                run_id=run_id,
                worker_id=worker_id,
            )
        self._event_sink = event_sink

    def _record(self, operation: str) -> None:
        self._event_sink.record(operation)

    @property
    def capabilities(self) -> PublisherCapabilityProfile:
        self._record("capabilities")
        return self._adapter.capabilities

    async def preflight(self, request: PublicationRequest) -> None:
        self._record("preflight")
        await self._adapter.preflight(request)

    async def prepare_delivery(self, request: PublicationRequest, delivery_url: str) -> str:
        self._record("prepare_delivery")
        return await self._adapter.prepare_delivery(request, delivery_url)

    async def create_or_resume(
        self, request: PublicationRequest, lease: CredentialLease
    ) -> RemotePublicationReceipt:
        self._record("create_or_resume")
        return await self._adapter.create_or_resume(request, lease)

    async def submit(
        self, request: PublicationRequest, lease: CredentialLease
    ) -> RemotePublicationReceipt:
        self._record("submit")
        return await self._adapter.submit(request, lease)

    async def reconcile(self, idempotency_key: str) -> RemotePublicationReceipt | None:
        self._record("reconcile")
        return await self._adapter.reconcile(idempotency_key)

    async def status(self, remote_id: str) -> RemotePublicationReceipt | None:
        self._record("status")
        return await self._adapter.status(remote_id)

    async def cancel(self, remote_id: str) -> RemotePublicationReceipt | None:
        self._record("cancel")
        return await self._adapter.cancel(remote_id)

    async def verify_webhook(self, webhook: object) -> PublisherWebhookEvent:
        self._record("verify_webhook")
        return await self._adapter.verify_webhook(webhook)

    async def refresh_capabilities(self) -> PublisherCapabilityProfile:
        self._record("refresh_capabilities")
        return await self._adapter.refresh_capabilities()


def install_publication_activities_adapter_recorder(
    *,
    receiver_url: str,
    run_id: str,
    worker_id: str | None = None,
):
    """Wrap the actual PublicationActivities adapter state before worker setup.

    This test-only class hook is installed in the candidate process bootstrap,
    after the authenticated launcher validates the pinned runtime and before
    the legacy worker constructs its activities. It modifies no installed
    module files.
    """
    from salience.workflows.publication import PublicationActivities

    event_sink = _AdapterOperationEventSink(
        receiver_url=receiver_url,
        run_id=run_id,
        worker_id=worker_id,
    )
    original_init = PublicationActivities.__init__
    wrapped_by_identity: dict[int, RecordingPublisherAdapter] = {}

    def wrap(adapter: PublisherAdapter) -> RecordingPublisherAdapter:
        if isinstance(adapter, RecordingPublisherAdapter):
            return adapter
        wrapped = wrapped_by_identity.get(id(adapter))
        if wrapped is None:
            wrapped = RecordingPublisherAdapter(adapter, event_sink=event_sink)
            wrapped_by_identity[id(adapter)] = wrapped
        return wrapped

    def instrumented_init(self, state) -> None:
        state.provider = wrap(state.provider)
        state.publisher_adapters = {
            publisher_id: wrap(adapter)
            for publisher_id, adapter in state.publisher_adapters.items()
        }
        original_init(self, state)

    PublicationActivities.__init__ = instrumented_init

    def restore() -> None:
        if PublicationActivities.__init__ is instrumented_init:
            PublicationActivities.__init__ = original_init

    return restore


class _NeutralProbeAdapter:
    """No-effect structural adapter used only by recorder controls."""

    @property
    def capabilities(self) -> PublisherCapabilityProfile:
        return PublisherCapabilityProfile(
            publisher_id="operation-probe",
            version="1",
            platform="neutral-fixture",
            account_types=("fixture",),
            contract_compatibility={"publication": "1"},
            enabled=True,
            audit_state="verified",
            granted_scopes=(),
            supported_content_types=("fixture",),
            supported_visibilities=("private",),
            disclosure_support=False,
            scheduling_support=False,
            cancellation_support=True,
            reconciliation_support=True,
            quota_state="available",
            health_state="healthy",
        )

    async def preflight(self, _request: PublicationRequest) -> None:
        return None

    async def prepare_delivery(
        self, _request: PublicationRequest, _delivery_url: str
    ) -> str:
        return "https://fixture.invalid/no-content"

    async def create_or_resume(
        self, _request: PublicationRequest, _lease: CredentialLease
    ) -> RemotePublicationReceipt:
        return self._receipt()

    async def submit(
        self, _request: PublicationRequest, _lease: CredentialLease
    ) -> RemotePublicationReceipt:
        return self._receipt()

    async def reconcile(self, _idempotency_key: str) -> None:
        return None

    async def status(self, _remote_id: str) -> None:
        return None

    async def cancel(self, _remote_id: str) -> None:
        return None

    async def verify_webhook(self, _webhook: object) -> PublisherWebhookEvent:
        return PublisherWebhookEvent(
            publisher_id="operation-probe",
            delivery_identity="fixture-delivery",
            remote_id="fixture-remote",
            state="accepted",
            safe_payload_hash="0" * 64,
        )

    async def refresh_capabilities(self) -> PublisherCapabilityProfile:
        return self.capabilities

    @staticmethod
    def _receipt() -> RemotePublicationReceipt:
        return RemotePublicationReceipt(
            id="fixture-receipt",
            publication_attempt_id="fixture-attempt",
            publisher_id="operation-probe",
            remote_id="fixture-remote",
            state="accepted",
            safe_metadata_hash="0" * 64,
        )


async def run_positive_control(*, base_url: str, run_id: str) -> None:
    """Exercise the protocol without credentials, payloads, or remote effects."""
    probe = RecordingPublisherAdapter(
        _NeutralProbeAdapter(), receiver_url=base_url, run_id=run_id
    )
    request = object()
    opaque_lease = object()
    _ = probe.capabilities
    await probe.preflight(request)
    await probe.prepare_delivery(request, "https://fixture.invalid/no-content")
    await probe.create_or_resume(request, opaque_lease)
    await probe.submit(request, opaque_lease)
    await probe.reconcile("fixture-idempotency-reference")
    await probe.status("fixture-remote-reference")
    await probe.cancel("fixture-remote-reference")
    await probe.verify_webhook(object())
    await probe.refresh_capabilities()


async def run_single_operation(*, base_url: str, run_id: str) -> None:
    """Intentionally wire one neutral adapter call for recorder sensitivity checks."""
    probe = RecordingPublisherAdapter(
        _NeutralProbeAdapter(), receiver_url=base_url, run_id=run_id
    )
    await probe.preflight(object())


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receiver_url")
    parser.add_argument("run_id")
    arguments = parser.parse_args()
    asyncio.run(
        run_positive_control(
            base_url=arguments.receiver_url,
            run_id=arguments.run_id,
        )
    )
