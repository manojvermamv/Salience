from salience.sdk.client import SalienceClient


class Response:
    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, str]:
        return {"agent_id": "research_agent", "status": "succeeded"}


def test_client_describes_and_runs_research_agent(monkeypatch) -> None:
    requests: list[tuple[str, str, dict[str, object] | None]] = []

    def request(method, url, **kwargs):
        requests.append((method, url, kwargs.get("json")))
        return Response()

    monkeypatch.setattr("salience.sdk.client.httpx.request", request)
    client = SalienceClient("https://api.example", "token")

    assert client.agents.describe("research_agent").agent_id == "research_agent"
    assert client.agents.run("research_agent", {"niche": "finance"}).status == "succeeded"
    assert requests == [
        ("GET", "https://api.example/v1/agents/research_agent", None),
        (
            "POST",
            "https://api.example/v1/agents/research_agent/runs",
            {"input": {"niche": "finance"}, "mode": "async"},
        ),
    ]
