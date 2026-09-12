from fastapi.testclient import TestClient

from salience.api.app import create_app
from salience.api.dependencies import InMemoryControlPlane


def admin_headers() -> dict[str, str]:
    return {
        "Authorization": "Bearer test-control-token",
        "X-Salience-Scopes": "control:read,control:write",
    }


def test_admin_can_start_and_inspect_a_dry_run_dummy_job() -> None:
    app = create_app(
        control_token="test-control-token", control_plane=InMemoryControlPlane()
    )
    client = TestClient(app)

    response = client.post(
        "/v1/jobs/dummy",
        headers=admin_headers(),
        json={"dry_run": True, "idempotency_key": "api-dry-run"},
    )

    assert response.status_code == 202
    job_id = response.json()["job_id"]
    assert client.get(f"/v1/jobs/{job_id}", headers=admin_headers()).json()[
        "state"
    ] == "succeeded"
    inspection = client.get(
        f"/v1/jobs/{job_id}/inspection", headers=admin_headers()
    )
    assert inspection.status_code == 200
    assert inspection.json()["audit_events"]
    assert inspection.json()["provenance_records"]
    assert inspection.json()["trace_id"]
    assert inspection.json()["cost_entries"]
    assert client.get(f"/v1/jobs/{job_id}/audit", headers=admin_headers()).status_code == 200
    assert (
        client.get(f"/v1/jobs/{job_id}/provenance", headers=admin_headers()).status_code
        == 200
    )
    assert client.get(f"/v1/jobs/{job_id}/costs", headers=admin_headers()).status_code == 200
    assert client.get(f"/v1/jobs/{job_id}/trace", headers=admin_headers()).json()[
        "trace_id"
    ] == inspection.json()["trace_id"]


def test_admin_can_create_workspace_and_content_program() -> None:
    app = create_app(
        control_token="test-control-token", control_plane=InMemoryControlPlane()
    )
    client = TestClient(app)

    workspace = client.post(
        "/v1/workspaces",
        headers=admin_headers(),
        json={"slug": "personal-finance", "display_name": "Personal Finance"},
    )
    assert workspace.status_code == 201
    program = client.post(
        f"/v1/workspaces/{workspace.json()['workspace_id']}/programs",
        headers=admin_headers(),
        json={
            "slug": "daily-brief",
            "name": "Daily Brief",
            "niche": "Personal finance",
        },
    )
    assert program.status_code == 201
    assert program.json()["workspace_id"] == workspace.json()["workspace_id"]


def test_control_plane_rejects_missing_token_or_scope() -> None:
    app = create_app(
        control_token="test-control-token", control_plane=InMemoryControlPlane()
    )
    client = TestClient(app)

    assert client.get("/health/live").status_code == 200
    assert client.post("/v1/jobs/dummy", json={"dry_run": True}).status_code == 401
    assert (
        client.post(
            "/v1/jobs/dummy",
            headers={"Authorization": "Bearer test-control-token"},
            json={"dry_run": True},
        ).status_code
        == 403
    )
