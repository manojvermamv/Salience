"""Scoped HTTP contracts for governed publication control."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from salience.api.app import create_app
from salience.api.dependencies import InMemoryControlPlane
from salience.publication.contracts import CredentialLease, PublicationRequest
from salience.publication.providers import FixturePublisherAdapter


def _headers(scopes: str = "control:read,control:write") -> dict[str, str]:
    return {"Authorization": "Bearer publication-token", "X-Salience-Scopes": scopes}


def _program(client: TestClient) -> tuple[str, str]:
    workspace = client.post(
        "/v1/workspaces",
        headers=_headers(),
        json={"slug": "publication-control", "display_name": "Publication control"},
    ).json()
    program = client.post(
        f"/v1/workspaces/{workspace['workspace_id']}/programs",
        headers=_headers(),
        json={"slug": "shorts", "name": "Shorts", "niche": "Urban gardening"},
    ).json()
    return workspace["workspace_id"], program["content_program_id"]


def test_publication_start_requires_ready_package_account_and_rejects_secret_fields() -> None:
    client = TestClient(
        create_app(control_token="publication-token", control_plane=InMemoryControlPlane())
    )
    workspace_id, program_id = _program(client)

    missing = client.post("/v1/publications/requests", headers=_headers(), json={"ready_package_id": "ready-1"})
    secret = client.post(
        "/v1/publications/requests",
        headers=_headers(),
        json={
            "workspace_id": workspace_id,
            "content_program_id": program_id,
            "ready_package_id": "ready-1",
            "publisher_account_id": "account-1",
            "publication_approval_request_id": "publication-approval-1",
            "budget_id": "budget-1",
            "idempotency_key": "publication-control-1",
            "access_token": "must-never-enter-control-plane",
        },
    )
    started = client.post(
        "/v1/publications/requests",
        headers=_headers(),
        json={
            "workspace_id": workspace_id,
            "content_program_id": program_id,
            "ready_package_id": "ready-1",
            "publisher_account_id": "account-1",
            "publication_approval_request_id": "publication-approval-1",
            "budget_id": "budget-1",
            "idempotency_key": "publication-control-2",
        },
    )

    assert missing.status_code == 422
    assert secret.status_code == 422
    assert started.status_code == 202
    assert started.json()["trace_id"]
    assert client.get(
        f"/v1/publications/runs/{started.json()['job_id']}", headers=_headers()
    ).status_code == 200
    assert client.post(
        f"/v1/publications/runs/{started.json()['job_id']}/cancel", headers=_headers("control:read")
    ).status_code == 403


def test_publication_schedule_requires_scoped_immutable_references() -> None:
    client = TestClient(
        create_app(control_token="publication-token", control_plane=InMemoryControlPlane())
    )
    workspace_id, program_id = _program(client)
    payload = {
        "workspace_id": workspace_id,
        "content_program_id": program_id,
        "publication_request_id": "publication-request-1",
        "publication_plan_id": "publication-plan-1",
        "schedule_version": 1,
        "name": "weekday-private-release",
        "every_seconds": 86_400,
        "budget_id": "budget-1",
    }

    denied = client.post("/v1/publications/schedules", headers=_headers("control:read"), json=payload)
    created = client.post("/v1/publications/schedules", headers=_headers(), json=payload)

    assert denied.status_code == 403
    assert created.status_code == 201
    assert created.json()["schedule_id"]
    assert created.json()["job_type"] == "governed_publication"


@dataclass(frozen=True)
class _Receipt:
    receipt_id: str
    publication_attempt_id: str
    state: str


class _WebhookRepository:
    def __init__(self) -> None:
        self._receipts: dict[str, _Receipt] = {}

    async def record_verified_webhook_event(self, event, *, trace_id: str) -> _Receipt:
        return self._receipts.setdefault(
            event.delivery_identity,
            _Receipt(
                receipt_id=f"receipt:{event.delivery_identity}",
                publication_attempt_id="publication-attempt-1",
                state=event.state,
            ),
        )


def test_publisher_webhook_duplicate_returns_the_same_safe_receipt() -> None:
    provider = FixturePublisherAdapter()
    request = PublicationRequest(
        id="publication-attempt-1",
        workspace_id="workspace-1",
        content_program_id="program-1",
        ready_package_id="ready-1",
        publisher_account_id="account-1",
        publication_approval_request_id="publication-approval-1",
        platform="fixture",
        destination="fixture://account-1",
        locale="en",
        territory="global",
        visibility="private",
        capability_profile_version=1,
        idempotency_key="publication-webhook-control",
        approval_reference="approval-1",
        publisher_id="fixture-publisher",
    )
    accepted = asyncio.run(
        provider.submit(
            request,
            CredentialLease(
                "fixture-control-lease",
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
                granted_scopes=frozenset({"publish:create"}),
            ),
        )
    )
    signed = provider.signed_webhook(accepted, state="published", delivery_identity="delivery-1")
    client = TestClient(
        create_app(
            control_token="publication-token",
            control_plane=InMemoryControlPlane(),
            publisher_adapters={"fixture-publisher": provider},
            publication_repository=_WebhookRepository(),
        )
    )
    body = {
        "publisher_id": signed.publisher_id,
        "delivery_identity": signed.delivery_identity,
        "remote_id": signed.remote_id,
        "state": signed.state,
        "safe_payload_hash": signed.safe_payload_hash,
        "signature": signed.signature,
    }

    first = client.post("/v1/publishers/fixture-publisher/webhooks", json=body)
    second = client.post("/v1/publishers/fixture-publisher/webhooks", json=body)

    assert first.status_code == second.status_code == 202
    assert first.json()["receipt_id"] == second.json()["receipt_id"]
    assert "signature" not in first.text
