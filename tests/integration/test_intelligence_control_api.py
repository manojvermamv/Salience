from fastapi.testclient import TestClient

from salience.api.app import create_app
from salience.api.dependencies import InMemoryControlPlane


def _headers() -> dict[str, str]:
    return {
        "Authorization": "Bearer intelligence-token",
        "X-Salience-Scopes": "control:read,control:write",
    }


def test_admin_can_start_inspect_and_schedule_a_dry_run_intelligence_job() -> None:
    app = create_app(
        control_token="intelligence-token", control_plane=InMemoryControlPlane()
    )
    client = TestClient(app)
    workspace = client.post(
        "/v1/workspaces",
        headers=_headers(),
        json={"slug": "urban-garden", "display_name": "Urban garden"},
    ).json()
    program = client.post(
        f"/v1/workspaces/{workspace['workspace_id']}/programs",
        headers=_headers(),
        json={"slug": "editorial", "name": "Editorial", "niche": "Urban gardening"},
    ).json()

    started = client.post(
        "/v1/intelligence/runs",
        headers=_headers(),
        json={
            "contract_version": "IntelligenceRunRequest@v1",
            "workspace_id": workspace["workspace_id"],
            "content_program_id": program["content_program_id"],
            "niche": "Urban gardening",
            "dry_run": True,
            "idempotency_key": "urban-garden-2026-09-12",
        },
    )
    assert started.status_code == 202
    job_id = started.json()["job_id"]
    assert started.json()["trace_id"]
    assert client.get(f"/v1/intelligence/runs/{job_id}", headers=_headers()).json()[
        "state"
    ] == "succeeded"

    scheduled = client.post(
        "/v1/intelligence/schedules",
        headers=_headers(),
        json={
            "workspace_id": workspace["workspace_id"],
            "content_program_id": program["content_program_id"],
            "name": "daily-research",
            "every_seconds": 86400,
            "niche": "Urban gardening",
        },
    )
    assert scheduled.status_code == 201
    assert scheduled.json()["job_type"] == "intelligence_research"

    brief = client.post(
        "/v1/intelligence/opportunities/opportunity-123/briefs",
        headers=_headers(),
        json={
            "contract_version": "ContentBriefRequest@v1",
            "content_program_id": program["content_program_id"],
            "idempotency_key": "selected-brief-2026-09-12",
            "dry_run": True,
        },
    )
    assert brief.status_code == 202
    assert brief.json()["job_id"]
