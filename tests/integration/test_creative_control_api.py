from fastapi.testclient import TestClient

from salience.api.app import create_app
from salience.api.dependencies import InMemoryControlPlane
from salience.creative.providers import FixtureCreativeProvider
from salience.creative.repository import CreativeRepository


def _headers(scopes: str = "control:read,control:write") -> dict[str, str]:
    return {
        "Authorization": "Bearer creative-token",
        "X-Salience-Scopes": scopes,
    }


def _program(client: TestClient) -> tuple[str, str]:
    workspace = client.post(
        "/v1/workspaces",
        headers=_headers(),
        json={"slug": "creative-control", "display_name": "Creative control"},
    ).json()
    program = client.post(
        f"/v1/workspaces/{workspace['workspace_id']}/programs",
        headers=_headers(),
        json={"slug": "shorts", "name": "Shorts", "niche": "Urban gardening"},
    ).json()
    return workspace["workspace_id"], program["content_program_id"]


def test_creative_control_defaults_to_dry_run_and_exposes_safe_inspection() -> None:
    app = create_app(control_token="creative-token", control_plane=InMemoryControlPlane())
    client = TestClient(app)
    workspace_id, program_id = _program(client)

    started = client.post(
        "/v1/creative/runs",
        headers=_headers(),
        json={
            "contract_version": "CreativeProductionRequest@v1",
            "workspace_id": workspace_id,
            "content_program_id": program_id,
            "brief_id": "brief-1",
            "idempotency_key": "creative-control-1",
            "target_profile_key": "fixture-short-video",
            "target_profile_version": 1,
        },
    )

    assert started.status_code == 202
    payload = started.json()
    assert payload["dry_run"] is True
    assert payload["trace_id"]
    assert payload["output"]["brief_id"] == "brief-1"
    inspected = client.get(f"/v1/creative/runs/{payload['job_id']}", headers=_headers())
    assert inspected.status_code == 200
    assert inspected.json()["job_id"] == payload["job_id"]
    assert client.get(f"/v1/creative/runs/{payload['job_id']}/script", headers=_headers()).status_code == 404
    assert client.get(f"/v1/creative/runs/{payload['job_id']}/asset", headers=_headers()).status_code == 404
    assert client.get(f"/v1/creative/runs/{payload['job_id']}/package", headers=_headers()).status_code == 404
    assert client.get("/v1/creative/packages/no-package/lineage", headers=_headers()).status_code == 404
    assert all("publish" not in path for path in app.openapi()["paths"])


def test_creative_control_requires_scoped_read_and_write_access() -> None:
    app = create_app(control_token="creative-token", control_plane=InMemoryControlPlane())
    client = TestClient(app)
    workspace_id, program_id = _program(client)
    response = client.post(
        "/v1/creative/runs",
        headers=_headers("control:read"),
        json={
            "workspace_id": workspace_id,
            "content_program_id": program_id,
            "brief_id": "brief-1",
            "idempotency_key": "creative-control-no-write",
            "target_profile_key": "fixture-short-video",
            "target_profile_version": 1,
        },
    )

    assert response.status_code == 403


def test_creative_control_rejects_non_dry_run_without_budget_identity() -> None:
    app = create_app(control_token="creative-token", control_plane=InMemoryControlPlane())
    client = TestClient(app)
    workspace_id, program_id = _program(client)

    response = client.post(
        "/v1/creative/runs",
        headers=_headers(),
        json={
            "workspace_id": workspace_id,
            "content_program_id": program_id,
            "brief_id": "brief-1",
            "idempotency_key": "creative-control-no-budget",
            "target_profile_key": "fixture-short-video",
            "dry_run": False,
        },
    )

    assert response.status_code == 422


def test_creative_webhook_rejects_unverified_and_unknown_providers() -> None:
    app = create_app(
        control_token="creative-token",
        control_plane=InMemoryControlPlane(),
        creative_providers={"fixture-creative": FixtureCreativeProvider()},
        creative_repository=CreativeRepository("postgresql://unused"),
    )
    client = TestClient(app)

    rejected = client.post(
        "/v1/creative/providers/fixture-creative/webhooks",
        content=b'{"id":"unknown","status":"completed"}',
    )
    unknown = client.post("/v1/creative/providers/unknown/webhooks", content=b"{}")

    assert rejected.status_code == 401
    assert unknown.status_code == 404
