from fastapi.testclient import TestClient

from salience.api.app import create_app
from salience.agents.fixtures import fixture_agent_service


def test_control_api_lists_describes_and_runs_research_agent() -> None:
    app = create_app(
        control_token="agent-token", agent_service=fixture_agent_service()
    )
    client = TestClient(app)
    headers = {
        "Authorization": "Bearer agent-token",
        "X-Salience-Scopes": "control:read,control:write",
    }

    assert client.get("/v1/agents", headers=headers).status_code == 200
    assert client.get("/v1/agents/research_agent", headers=headers).json()[
        "agent_id"
    ] == "research_agent"
    run = client.post(
        "/v1/agents/research_agent/runs",
        headers=headers,
        json={"input": {"niche": "finance"}, "mode": "sync"},
    )
    assert run.status_code == 201
    assert run.json()["status"] == "succeeded"
