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
