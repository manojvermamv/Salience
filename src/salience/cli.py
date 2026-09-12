"""Small HTTP-only CLI for the public control-plane schema."""

import argparse
import json
import os
from typing import Sequence

import httpx


def _request(
    *, method: str, path: str, payload: dict[str, object] | None = None
) -> dict[str, object]:
    base_url = os.environ.get("SALIENCE_CONTROL_URL", "http://127.0.0.1:8000")
    token = os.environ["CONTROL_PLANE_TOKEN"]
    scopes = os.environ.get("SALIENCE_CONTROL_SCOPES", "control:read,control:write")
    response = httpx.request(
        method,
        f"{base_url.rstrip('/')}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Salience-Scopes": scopes,
        },
        json=payload,
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def main(arguments: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="content")
    subcommands = parser.add_subparsers(dest="command", required=True)
    jobs = subcommands.add_parser("jobs")
    job_commands = jobs.add_subparsers(dest="job_command", required=True)
    start = job_commands.add_parser("start-dummy")
    start.add_argument("--idempotency-key", required=True)
    start.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=True)
    inspect = job_commands.add_parser("inspect")
    inspect.add_argument("job_id")
    agents = subcommands.add_parser("agents")
    agent_commands = agents.add_subparsers(dest="agent_command", required=True)
    agent_commands.add_parser("list")
    describe = agent_commands.add_parser("describe")
    describe.add_argument("agent_id")
    run = agent_commands.add_parser("run")
    run.add_argument("agent_id")
    run.add_argument("--niche", required=True)
    run.add_argument("--mode", choices=("sync", "async"), default="async")

    parsed = parser.parse_args(arguments)
    if parsed.command == "jobs" and parsed.job_command == "start-dummy":
        result = _request(
            method="POST",
            path="/v1/jobs/dummy",
            payload={"dry_run": parsed.dry_run, "idempotency_key": parsed.idempotency_key},
        )
    elif parsed.command == "jobs":
        result = _request(method="GET", path=f"/v1/jobs/{parsed.job_id}/inspection")
    elif parsed.agent_command == "list":
        result = _request(method="GET", path="/v1/agents")
    elif parsed.agent_command == "describe":
        result = _request(method="GET", path=f"/v1/agents/{parsed.agent_id}")
    else:
        result = _request(
            method="POST",
            path=f"/v1/agents/{parsed.agent_id}/runs",
            payload={"input": {"niche": parsed.niche}, "mode": parsed.mode},
        )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
