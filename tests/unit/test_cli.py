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
