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


def test_client_starts_and_inspects_provider_neutral_creative_runs(monkeypatch) -> None:
    requests: list[tuple[str, str, dict[str, object] | None]] = []

    class CreativeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, object]:
            return {
                "job_id": "creative-job-1",
                "state": "running",
                "dry_run": True,
                "trace_id": "trace-1",
                "output": {"brief_id": "brief-1"},
            }

    def request(method, url, **kwargs):
        requests.append((method, url, kwargs.get("json")))
        return CreativeResponse()

    monkeypatch.setattr("salience.sdk.client.httpx.request", request)
    client = SalienceClient("https://api.example", "token")

    started = client.creative.start(
        workspace_id="workspace-1",
        content_program_id="program-1",
        brief_id="brief-1",
        idempotency_key="creative-key",
        target_profile_key="fixture-short-video",
    )
    inspected = client.creative.inspect("creative-job-1")

    assert started.trace_id == "trace-1"
    assert inspected.job_id == "creative-job-1"
    assert requests == [
        (
            "POST",
            "https://api.example/v1/creative/runs",
            {
                "contract_version": "CreativeProductionRequest@v1",
                "workspace_id": "workspace-1",
                "content_program_id": "program-1",
                "brief_id": "brief-1",
                "idempotency_key": "creative-key",
                "target_profile_key": "fixture-short-video",
                "target_profile_version": 1,
                "dry_run": True,
            },
        ),
        ("GET", "https://api.example/v1/creative/runs/creative-job-1", None),
    ]
