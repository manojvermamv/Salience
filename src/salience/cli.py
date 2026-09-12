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
    intelligence = subcommands.add_parser("intelligence")
    intelligence_commands = intelligence.add_subparsers(dest="intelligence_command", required=True)
    intelligence_start = intelligence_commands.add_parser("start")
    intelligence_start.add_argument("--workspace-id", required=True)
    intelligence_start.add_argument("--program-id", required=True)
    intelligence_start.add_argument("--niche", required=True)
    intelligence_start.add_argument("--idempotency-key", required=True)
    intelligence_start.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=True)
    intelligence_inspect = intelligence_commands.add_parser("inspect")
    intelligence_inspect.add_argument("job_id")
    intelligence_schedule = intelligence_commands.add_parser("schedule")
    intelligence_schedule.add_argument("--workspace-id", required=True)
    intelligence_schedule.add_argument("--program-id", required=True)
    intelligence_schedule.add_argument("--name", required=True)
    intelligence_schedule.add_argument("--every-seconds", required=True, type=int)
    intelligence_schedule.add_argument("--niche", required=True)
    intelligence_brief = intelligence_commands.add_parser("brief")
    intelligence_brief.add_argument("--program-id", required=True)
    intelligence_brief.add_argument("--opportunity-id", required=True)
    intelligence_brief.add_argument("--idempotency-key", required=True)
    intelligence_brief.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=True)
    intelligence_inspect_brief = intelligence_commands.add_parser("inspect-brief")
    intelligence_inspect_brief.add_argument("brief_id")
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
    elif parsed.command == "intelligence" and parsed.intelligence_command == "start":
        result = _request(
            method="POST",
            path="/v1/intelligence/runs",
            payload={
                "contract_version": "IntelligenceRunRequest@v1",
                "workspace_id": parsed.workspace_id,
                "content_program_id": parsed.program_id,
                "niche": parsed.niche,
                "dry_run": parsed.dry_run,
                "idempotency_key": parsed.idempotency_key,
            },
        )
    elif parsed.command == "intelligence" and parsed.intelligence_command == "inspect":
        result = _request(method="GET", path=f"/v1/intelligence/runs/{parsed.job_id}")
    elif parsed.command == "intelligence" and parsed.intelligence_command == "brief":
        result = _request(
            method="POST",
            path=f"/v1/intelligence/opportunities/{parsed.opportunity_id}/briefs",
            payload={
                "contract_version": "ContentBriefRequest@v1",
                "content_program_id": parsed.program_id,
                "idempotency_key": parsed.idempotency_key,
                "dry_run": parsed.dry_run,
            },
        )
    elif parsed.command == "intelligence" and parsed.intelligence_command == "inspect-brief":
        result = _request(method="GET", path=f"/v1/intelligence/briefs/{parsed.brief_id}")
    elif parsed.command == "intelligence":
        result = _request(
            method="POST",
            path="/v1/intelligence/schedules",
            payload={
                "workspace_id": parsed.workspace_id,
                "content_program_id": parsed.program_id,
                "name": parsed.name,
                "every_seconds": parsed.every_seconds,
                "niche": parsed.niche,
            },
        )
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
