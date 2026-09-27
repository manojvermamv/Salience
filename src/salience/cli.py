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


def _v4_request(
    *, method: str, path: str, payload: dict[str, object] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, object]:
    base_url = os.environ.get("SALIENCE_CONTROL_URL", "http://127.0.0.1:8000")
    token = os.environ["SALIENCE_CONTROL_JWT"]
    headers = {"Authorization": f"Bearer {token}"}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    response = httpx.request(
        method,
        f"{base_url.rstrip('/')}{path}",
        headers=headers,
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
    creative = subcommands.add_parser("creative")
    creative_commands = creative.add_subparsers(dest="creative_command", required=True)
    creative_start = creative_commands.add_parser("start")
    creative_start.add_argument("--workspace-id", required=True)
    creative_start.add_argument("--program-id", required=True)
    creative_start.add_argument("--brief-id", required=True)
    creative_start.add_argument("--idempotency-key", required=True)
    creative_start.add_argument("--profile-key", required=True)
    creative_start.add_argument("--profile-version", type=int, default=1)
    creative_start.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=True)
    creative_inspect = creative_commands.add_parser("inspect")
    creative_inspect.add_argument("job_id")
    creative_lineage = creative_commands.add_parser("package-lineage")
    creative_lineage.add_argument("ready_package_id")
    publication = subcommands.add_parser("publication")
    publication_commands = publication.add_subparsers(dest="publication_command", required=True)
    publication_start = publication_commands.add_parser("start")
    publication_start.add_argument("--workspace-id", required=True)
    publication_start.add_argument("--program-id", required=True)
    publication_start.add_argument("--ready-package-id", required=True)
    publication_start.add_argument("--publisher-account-id", required=True)
    publication_start.add_argument("--publication-approval-request-id", required=True)
    publication_start.add_argument("--budget-id", required=True)
    publication_start.add_argument("--idempotency-key", required=True)
    publication_inspect = publication_commands.add_parser("inspect")
    publication_inspect.add_argument("job_id")
    publication_cancel = publication_commands.add_parser("cancel")
    publication_cancel.add_argument("job_id")
    publication_schedule = publication_commands.add_parser("schedule")
    publication_schedule.add_argument("--workspace-id", required=True)
    publication_schedule.add_argument("--program-id", required=True)
    publication_schedule.add_argument("--publication-request-id", required=True)
    publication_schedule.add_argument("--publication-plan-id", required=True)
    publication_schedule.add_argument("--schedule-version", required=True, type=int)
    publication_schedule.add_argument("--name", required=True)
    publication_schedule.add_argument("--every-seconds", required=True, type=int)
    publication_schedule.add_argument("--budget-id", required=True)
    agents = subcommands.add_parser("agents")
    agent_commands = agents.add_subparsers(dest="agent_command", required=True)
    agent_commands.add_parser("list")
    describe = agent_commands.add_parser("describe")
    describe.add_argument("agent_id")
    run = agent_commands.add_parser("run")
    run.add_argument("agent_id")
    run.add_argument("--niche", required=True)
    run.add_argument("--mode", choices=("sync", "async"), default="async")
    cycles = subcommands.add_parser("cycles")
    cycle_commands = cycles.add_subparsers(dest="cycle_command", required=True)
    goal_create = cycle_commands.add_parser("goal-create")
    goal_create.add_argument("--workspace-id", required=True)
    goal_create.add_argument("--spec-json", required=True)
    goal_create.add_argument("--idempotency-key", required=True)
    baseline = cycle_commands.add_parser("baseline-approve")
    baseline.add_argument("--goal-id", required=True)
    baseline.add_argument("--expected-revision", type=int, required=True)
    baseline.add_argument("--expires-at", required=True)
    baseline.add_argument("--reason", required=True)
    cycle_request = cycle_commands.add_parser("request")
    cycle_request.add_argument("--goal-id", required=True)
    cycle_request.add_argument("--request-json", required=True)
    cycle_admit = cycle_commands.add_parser("admit")
    cycle_admit.add_argument("--intent-id", required=True)
    cycle_inspect = cycle_commands.add_parser("inspect")
    cycle_inspect.add_argument("--cycle-id", required=True)
    cycle_cancel = cycle_commands.add_parser("cancel")
    cycle_cancel.add_argument("--cycle-id", required=True)
    cycle_cancel.add_argument("--reason", required=True)
    cycle_events = cycle_commands.add_parser("events")
    cycle_events.add_argument("--cycle-id", required=True)
    cycle_events.add_argument("--limit", type=int, default=50)
    cycle_events.add_argument("--after")

    parsed = parser.parse_args(arguments)
    if parsed.command == "cycles":
        if parsed.cycle_command == "goal-create":
            result = _v4_request(
                method="POST", path=f"/v1/workspaces/{parsed.workspace_id}/v4/goals",
                payload=json.loads(parsed.spec_json), idempotency_key=parsed.idempotency_key,
            )
        elif parsed.cycle_command == "baseline-approve":
            result = _v4_request(
                method="POST", path=f"/v1/v4/goals/{parsed.goal_id}/baseline",
                payload={"expected_revision": parsed.expected_revision,
                         "expires_at": parsed.expires_at, "reason": parsed.reason},
            )
        elif parsed.cycle_command == "request":
            result = _v4_request(
                method="POST", path=f"/v1/v4/goals/{parsed.goal_id}/requests",
                payload=json.loads(parsed.request_json),
            )
        elif parsed.cycle_command == "admit":
            result = _v4_request(
                method="POST", path=f"/v1/v4/intents/{parsed.intent_id}/admit", payload={},
            )
        elif parsed.cycle_command == "inspect":
            result = _v4_request(method="GET", path=f"/v1/v4/cycles/{parsed.cycle_id}")
        elif parsed.cycle_command == "cancel":
            result = _v4_request(
                method="POST", path=f"/v1/v4/cycles/{parsed.cycle_id}/cancel",
                payload={"reason": parsed.reason},
            )
        else:
            query = f"limit={parsed.limit}" + (f"&after={parsed.after}" if parsed.after else "")
            result = _v4_request(
                method="GET", path=f"/v1/v4/cycles/{parsed.cycle_id}/events?{query}",
            )
    elif parsed.command == "jobs" and parsed.job_command == "start-dummy":
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
    elif parsed.command == "creative" and parsed.creative_command == "start":
        result = _request(
            method="POST",
            path="/v1/creative/runs",
            payload={
                "contract_version": "CreativeProductionRequest@v1",
                "workspace_id": parsed.workspace_id,
                "content_program_id": parsed.program_id,
                "brief_id": parsed.brief_id,
                "idempotency_key": parsed.idempotency_key,
                "target_profile_key": parsed.profile_key,
                "target_profile_version": parsed.profile_version,
                "dry_run": parsed.dry_run,
            },
        )
    elif parsed.command == "creative" and parsed.creative_command == "inspect":
        result = _request(method="GET", path=f"/v1/creative/runs/{parsed.job_id}")
    elif parsed.command == "creative":
        result = _request(
            method="GET",
            path=f"/v1/creative/packages/{parsed.ready_package_id}/lineage",
        )
    elif parsed.command == "publication" and parsed.publication_command == "start":
        result = _request(
            method="POST",
            path="/v1/publications/requests",
            payload={
                "contract_version": "PublicationWorkflowRequest@v1",
                "workspace_id": parsed.workspace_id,
                "content_program_id": parsed.program_id,
                "ready_package_id": parsed.ready_package_id,
                "publisher_account_id": parsed.publisher_account_id,
                "publication_approval_request_id": parsed.publication_approval_request_id,
                "budget_id": parsed.budget_id,
                "idempotency_key": parsed.idempotency_key,
            },
        )
    elif parsed.command == "publication" and parsed.publication_command == "schedule":
        result = _request(
            method="POST",
            path="/v1/publications/schedules",
            payload={
                "workspace_id": parsed.workspace_id,
                "content_program_id": parsed.program_id,
                "publication_request_id": parsed.publication_request_id,
                "publication_plan_id": parsed.publication_plan_id,
                "schedule_version": parsed.schedule_version,
                "name": parsed.name,
                "every_seconds": parsed.every_seconds,
                "budget_id": parsed.budget_id,
            },
        )
    elif parsed.command == "publication" and parsed.publication_command == "inspect":
        result = _request(method="GET", path=f"/v1/publications/runs/{parsed.job_id}")
    elif parsed.command == "publication":
        result = _request(method="POST", path=f"/v1/publications/runs/{parsed.job_id}/cancel")
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
