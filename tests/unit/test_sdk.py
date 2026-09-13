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


def test_client_starts_and_inspects_governed_publication_through_http(monkeypatch) -> None:
    requests: list[tuple[str, str, dict[str, object] | None]] = []

    class PublicationResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, object]:
            return {
                "job_id": "publication-job-1",
                "state": "running",
                "dry_run": False,
                "trace_id": "trace-1",
                "output": {"publication_request_id": "request-1"},
            }

    def request(method, url, **kwargs):
        requests.append((method, url, kwargs.get("json")))
        return PublicationResponse()

    monkeypatch.setattr("salience.sdk.client.httpx.request", request)
    client = SalienceClient("https://api.example", "token")

    started = client.publication.start(
        workspace_id="workspace-1",
        content_program_id="program-1",
        ready_package_id="ready-1",
        publisher_account_id="account-1",
        budget_id="budget-1",
        idempotency_key="publication-key",
    )
    inspected = client.publication.inspect("publication-job-1")

    assert started.trace_id == "trace-1"
    assert inspected.job_id == "publication-job-1"
    assert requests == [
        (
            "POST",
            "https://api.example/v1/publications/requests",
            {
                "contract_version": "PublicationWorkflowRequest@v1",
                "workspace_id": "workspace-1",
                "content_program_id": "program-1",
                "ready_package_id": "ready-1",
                "publisher_account_id": "account-1",
                "budget_id": "budget-1",
                "idempotency_key": "publication-key",
            },
        ),
        ("GET", "https://api.example/v1/publications/runs/publication-job-1", None),
    ]


def test_client_schedules_governed_publication_through_http(monkeypatch) -> None:
    requests: list[tuple[str, str, dict[str, object] | None]] = []

    class ScheduleResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, object]:
            return {"schedule_id": "schedule-1", "job_type": "governed_publication", "schedule_expression": "every 86400s"}

    def request(method, url, **kwargs):
        requests.append((method, url, kwargs.get("json")))
        return ScheduleResponse()

    monkeypatch.setattr("salience.sdk.client.httpx.request", request)
    client = SalienceClient("https://api.example", "token")

    schedule = client.publication.schedule(
        workspace_id="workspace-1", content_program_id="program-1",
        publication_request_id="publication-request-1", publication_plan_id="publication-plan-1",
        schedule_version=1, name="weekday-private-release", every_seconds=86_400,
        ready_package_id="ready-1", publisher_account_id="account-1", budget_id="budget-1",
        idempotency_key="publication-schedule-1",
    )

    assert schedule.schedule_id == "schedule-1"
    assert requests == [
        (
            "POST", "https://api.example/v1/publications/schedules",
            {
                "workspace_id": "workspace-1", "content_program_id": "program-1",
                "publication_request_id": "publication-request-1", "publication_plan_id": "publication-plan-1",
                "schedule_version": 1, "name": "weekday-private-release", "every_seconds": 86_400,
                "ready_package_id": "ready-1", "publisher_account_id": "account-1", "budget_id": "budget-1",
                "idempotency_key": "publication-schedule-1",
            },
        )
    ]
