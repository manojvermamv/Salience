import json

from salience import cli


class Response:
    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, str]:
        return {"job_id": "job-1", "state": "queued"}


def test_cli_uses_the_control_api_schema(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}

    def request(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured.update(kwargs)
        return Response()

    monkeypatch.setenv("CONTROL_PLANE_TOKEN", "test-token")
    monkeypatch.setattr(cli.httpx, "request", request)

    cli.main(["jobs", "start-dummy", "--idempotency-key", "job-key"])

    assert captured["method"] == "POST"
    assert captured["url"] == "http://127.0.0.1:8000/v1/jobs/dummy"
    assert captured["json"] == {"dry_run": True, "idempotency_key": "job-key"}
    assert json.loads(capsys.readouterr().out)["job_id"] == "job-1"


def test_cli_starts_creative_runs_with_the_versioned_control_schema(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}

    def request(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured.update(kwargs)
        return Response()

    monkeypatch.setenv("CONTROL_PLANE_TOKEN", "test-token")
    monkeypatch.setattr(cli.httpx, "request", request)

    cli.main(
        [
            "creative",
            "start",
            "--workspace-id",
            "workspace-1",
            "--program-id",
            "program-1",
            "--brief-id",
            "brief-1",
            "--idempotency-key",
            "creative-key",
            "--profile-key",
            "fixture-short-video",
        ]
    )

    assert captured["method"] == "POST"
    assert captured["url"] == "http://127.0.0.1:8000/v1/creative/runs"
    assert captured["json"] == {
        "contract_version": "CreativeProductionRequest@v1",
        "workspace_id": "workspace-1",
        "content_program_id": "program-1",
        "brief_id": "brief-1",
        "idempotency_key": "creative-key",
        "target_profile_key": "fixture-short-video",
        "target_profile_version": 1,
        "dry_run": True,
    }
    assert json.loads(capsys.readouterr().out)["job_id"] == "job-1"


def test_cli_starts_publication_through_the_control_api(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}

    def request(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured.update(kwargs)
        return Response()

    monkeypatch.setenv("CONTROL_PLANE_TOKEN", "test-token")
    monkeypatch.setattr(cli.httpx, "request", request)

    cli.main(
        [
            "publication",
            "start",
            "--workspace-id",
            "workspace-1",
            "--program-id",
            "program-1",
            "--ready-package-id",
            "ready-1",
            "--publisher-account-id",
            "account-1",
            "--publication-approval-request-id",
            "publication-approval-1",
            "--budget-id",
            "budget-1",
            "--idempotency-key",
            "publication-key",
        ]
    )

    assert captured["method"] == "POST"
    assert captured["url"] == "http://127.0.0.1:8000/v1/publications/requests"
    assert captured["json"] == {
        "contract_version": "PublicationWorkflowRequest@v1",
        "workspace_id": "workspace-1",
        "content_program_id": "program-1",
        "ready_package_id": "ready-1",
        "publisher_account_id": "account-1",
        "publication_approval_request_id": "publication-approval-1",
        "budget_id": "budget-1",
        "idempotency_key": "publication-key",
    }
    assert json.loads(capsys.readouterr().out)["job_id"] == "job-1"


def test_cli_schedules_publication_with_immutable_references(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}

    def request(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured.update(kwargs)
        return Response()

    monkeypatch.setenv("CONTROL_PLANE_TOKEN", "test-token")
    monkeypatch.setattr(cli.httpx, "request", request)

    cli.main(
        [
            "publication", "schedule", "--workspace-id", "workspace-1", "--program-id", "program-1",
            "--publication-request-id", "publication-request-1", "--publication-plan-id", "publication-plan-1",
            "--schedule-version", "1", "--name", "weekday-private-release", "--every-seconds", "86400",
            "--budget-id", "budget-1",
        ]
    )

    assert captured["method"] == "POST"
    assert captured["url"] == "http://127.0.0.1:8000/v1/publications/schedules"
    assert captured["json"] == {
        "workspace_id": "workspace-1", "content_program_id": "program-1",
        "publication_request_id": "publication-request-1", "publication_plan_id": "publication-plan-1",
        "schedule_version": 1, "name": "weekday-private-release", "every_seconds": 86400,
        "budget_id": "budget-1",
    }
    assert json.loads(capsys.readouterr().out)["job_id"] == "job-1"


def test_cli_uses_signed_fixture_cutover_contract(monkeypatch, capsys) -> None:
    captured = []

    def request(method, url, **kwargs):
        captured.append((method, url, kwargs.get("json"), kwargs["headers"]))
        return Response()

    monkeypatch.setenv("SALIENCE_CONTROL_JWT", "signed-fixture")
    monkeypatch.setattr(cli.httpx, "request", request)
    cli.main(["cycles", "cutover-prepare", "--goal-id", "goal-1", "--legacy-schedule-id", "legacy-1",
              "--expected-revision", "2", "--first-v4-slot", "2030-01-01T00:00:00+00:00", "--idempotency-key", "prepare"])
    cli.main(["cycles", "cutover-inspect", "--goal-id", "goal-1"])
    cli.main(["cycles", "cutover-activate", "--goal-id", "goal-1", "--idempotency-key", "activate"])
    cli.main(["cycles", "cutover-poll", "--goal-id", "goal-1", "--expected-revision", "2", "--idempotency-key", "poll"])
    cli.main(["cycles", "cutover-rollback", "--goal-id", "goal-1", "--idempotency-key", "rollback"])
    assert [entry[:3] for entry in captured] == [
        ("POST", "http://127.0.0.1:8000/v1/v4/goals/goal-1/schedule-cutover",
         {"legacy_schedule_id": "legacy-1", "expected_revision": 2, "first_v4_slot": "2030-01-01T00:00:00+00:00", "idempotency_key": "prepare"}),
        ("GET", "http://127.0.0.1:8000/v1/v4/goals/goal-1/schedule-cutover", None),
        ("POST", "http://127.0.0.1:8000/v1/v4/goals/goal-1/schedule-cutover/activate", {"idempotency_key": "activate"}),
        ("POST", "http://127.0.0.1:8000/v1/v4/goals/goal-1/schedule-cutover/poll", {"expected_revision": 2, "idempotency_key": "poll"}),
        ("POST", "http://127.0.0.1:8000/v1/v4/goals/goal-1/schedule-cutover/rollback", {"idempotency_key": "rollback"}),
    ]
    assert all(entry[3]["Authorization"] == "Bearer signed-fixture" for entry in captured)
    capsys.readouterr()
