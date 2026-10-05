from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel, Field


class AgentView(BaseModel):
    agent_id: str
    version: str | None = None
    status: str | None = None


class AgentRunView(BaseModel):
    agent_id: str
    status: str
    run_id: str | None = None
    output: dict[str, Any] = Field(default_factory=dict)


class IntelligenceRunView(BaseModel):
    job_id: str
    state: str
    dry_run: bool
    trace_id: str
    output: dict[str, Any] = Field(default_factory=dict)


class IntelligenceScheduleView(BaseModel):
    schedule_id: str
    job_type: str
    schedule_expression: str


class ContentBriefView(BaseModel):
    brief_id: str
    content_program_id: str
    opportunity_id: str
    package_id: str
    content: dict[str, Any]
    claim_ids: list[str]


class CreativeRunView(BaseModel):
    job_id: str
    state: str
    dry_run: bool
    trace_id: str
    output: dict[str, Any] = Field(default_factory=dict)


class CreativeScriptView(BaseModel):
    job_id: str
    trace_id: str
    script_id: str


class CreativeAssetView(BaseModel):
    job_id: str
    trace_id: str
    asset_id: str


class CreativePackageView(BaseModel):
    job_id: str
    trace_id: str
    ready_package_id: str


class PublicationRunView(BaseModel):
    job_id: str
    state: str
    dry_run: bool
    trace_id: str
    output: dict[str, Any] = Field(default_factory=dict)


class PublicationScheduleView(BaseModel):
    schedule_id: str
    job_type: str
    schedule_expression: str


@dataclass
class _ControlClient:
    base_url: str
    token: str

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        response = httpx.request(
            method,
            f"{self.base_url.rstrip('/')}{path}",
            headers={
                "Authorization": f"Bearer {self.token}",
                "X-Salience-Scopes": "control:read,control:write",
            },
            json=payload,
            timeout=10,
        )
        response.raise_for_status()
        return response.json()


@dataclass
class AgentsClient(_ControlClient):
    def describe(self, agent_id: str) -> AgentView:
        return AgentView.model_validate(self._request("GET", f"/v1/agents/{agent_id}"))

    def run(
        self, agent_id: str, input: dict[str, Any], *, mode: str = "async"
    ) -> AgentRunView:
        return AgentRunView.model_validate(
            self._request(
                "POST",
                f"/v1/agents/{agent_id}/runs",
                {"input": input, "mode": mode},
            )
        )


@dataclass
class IntelligenceClient(_ControlClient):
    def start(
        self,
        *,
        workspace_id: str,
        content_program_id: str,
        niche: str,
        idempotency_key: str,
        dry_run: bool = True,
    ) -> IntelligenceRunView:
        return IntelligenceRunView.model_validate(
            self._request(
                "POST",
                "/v1/intelligence/runs",
                {
                    "contract_version": "IntelligenceRunRequest@v1",
                    "workspace_id": workspace_id,
                    "content_program_id": content_program_id,
                    "niche": niche,
                    "dry_run": dry_run,
                    "idempotency_key": idempotency_key,
                },
            )
        )

    def inspect(self, job_id: str) -> IntelligenceRunView:
        return IntelligenceRunView.model_validate(
            self._request("GET", f"/v1/intelligence/runs/{job_id}")
        )

    def schedule(
        self,
        *,
        workspace_id: str,
        content_program_id: str,
        name: str,
        every_seconds: int,
        niche: str,
    ) -> IntelligenceScheduleView:
        return IntelligenceScheduleView.model_validate(
            self._request(
                "POST",
                "/v1/intelligence/schedules",
                {
                    "workspace_id": workspace_id,
                    "content_program_id": content_program_id,
                    "name": name,
                    "every_seconds": every_seconds,
                    "niche": niche,
                },
            )
        )

    def start_brief(
        self,
        *,
        opportunity_id: str,
        content_program_id: str,
        idempotency_key: str,
        dry_run: bool = True,
    ) -> IntelligenceRunView:
        return IntelligenceRunView.model_validate(
            self._request(
                "POST",
                f"/v1/intelligence/opportunities/{opportunity_id}/briefs",
                {
                    "contract_version": "ContentBriefRequest@v1",
                    "content_program_id": content_program_id,
                    "idempotency_key": idempotency_key,
                    "dry_run": dry_run,
                },
            )
        )

    def get_brief(self, brief_id: str) -> ContentBriefView:
        return ContentBriefView.model_validate(
            self._request("GET", f"/v1/intelligence/briefs/{brief_id}")
        )

    def brief_lineage(self, brief_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/intelligence/briefs/{brief_id}/lineage")


@dataclass
class CreativeClient(_ControlClient):
    def start(
        self,
        *,
        workspace_id: str,
        content_program_id: str,
        brief_id: str,
        idempotency_key: str,
        target_profile_key: str,
        target_profile_version: int = 1,
        dry_run: bool = True,
    ) -> CreativeRunView:
        return CreativeRunView.model_validate(
            self._request(
                "POST",
                "/v1/creative/runs",
                {
                    "contract_version": "CreativeProductionRequest@v1",
                    "workspace_id": workspace_id,
                    "content_program_id": content_program_id,
                    "brief_id": brief_id,
                    "idempotency_key": idempotency_key,
                    "target_profile_key": target_profile_key,
                    "target_profile_version": target_profile_version,
                    "dry_run": dry_run,
                },
            )
        )

    def inspect(self, job_id: str) -> CreativeRunView:
        return CreativeRunView.model_validate(
            self._request("GET", f"/v1/creative/runs/{job_id}")
        )

    def script(self, job_id: str) -> CreativeScriptView:
        return CreativeScriptView.model_validate(
            self._request("GET", f"/v1/creative/runs/{job_id}/script")
        )

    def asset(self, job_id: str) -> CreativeAssetView:
        return CreativeAssetView.model_validate(
            self._request("GET", f"/v1/creative/runs/{job_id}/asset")
        )

    def package(self, job_id: str) -> CreativePackageView:
        return CreativePackageView.model_validate(
            self._request("GET", f"/v1/creative/runs/{job_id}/package")
        )

    def package_lineage(self, ready_package_id: str) -> dict[str, Any]:
        return self._request(
            "GET", f"/v1/creative/packages/{ready_package_id}/lineage"
        )


@dataclass
class PublicationClient(_ControlClient):
    def start(
        self,
        *,
        workspace_id: str,
        content_program_id: str,
        ready_package_id: str,
        publisher_account_id: str,
        publication_approval_request_id: str,
        budget_id: str,
        idempotency_key: str,
    ) -> PublicationRunView:
        return PublicationRunView.model_validate(
            self._request(
                "POST",
                "/v1/publications/requests",
                {
                    "contract_version": "PublicationWorkflowRequest@v1",
                    "workspace_id": workspace_id,
                    "content_program_id": content_program_id,
                    "ready_package_id": ready_package_id,
                    "publisher_account_id": publisher_account_id,
                    "publication_approval_request_id": publication_approval_request_id,
                    "budget_id": budget_id,
                    "idempotency_key": idempotency_key,
                },
            )
        )

    def inspect(self, job_id: str) -> PublicationRunView:
        return PublicationRunView.model_validate(
            self._request("GET", f"/v1/publications/runs/{job_id}")
        )

    def schedule(
        self,
        *,
        workspace_id: str,
        content_program_id: str,
        publication_request_id: str,
        publication_plan_id: str,
        schedule_version: int,
        name: str,
        every_seconds: int,
        budget_id: str,
    ) -> PublicationScheduleView:
        return PublicationScheduleView.model_validate(
            self._request(
                "POST",
                "/v1/publications/schedules",
                {
                    "workspace_id": workspace_id,
                    "content_program_id": content_program_id,
                    "publication_request_id": publication_request_id,
                    "publication_plan_id": publication_plan_id,
                    "schedule_version": schedule_version,
                    "name": name,
                    "every_seconds": every_seconds,
                    "budget_id": budget_id,
                },
            )
        )

    def cancel(self, job_id: str) -> PublicationRunView:
        return PublicationRunView.model_validate(
            self._request("POST", f"/v1/publications/runs/{job_id}/cancel")
        )


@dataclass
class CyclesClient(_ControlClient):
    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None,
        *, idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.token}"}
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        response = httpx.request(
            method,
            f"{self.base_url.rstrip('/')}{path}",
            headers=headers,
            json=payload,
            timeout=10,
        )
        response.raise_for_status()
        return response.json()

    def create_goal(
        self, workspace_id: str, spec: dict[str, Any], *, idempotency_key: str
    ) -> dict[str, Any]:
        return self._request(
            "POST", f"/v1/workspaces/{workspace_id}/v4/goals", spec,
            idempotency_key=idempotency_key,
        )

    def revise_goal(
        self, goal_id: str, spec: dict[str, Any], *, expected_revision: int,
        idempotency_key: str, reason: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST", f"/v1/v4/goals/{goal_id}/revisions",
            {"spec": spec, "expected_revision": expected_revision,
             "idempotency_key": idempotency_key, "reason": reason},
        )

    def set_goal_state(self, goal_id: str, state: str, *, expected_revision: int,
                       expected_state_revision: int, idempotency_key: str, reason: str) -> dict[str, Any]:
        return self._request("POST", f"/v1/v4/goals/{goal_id}/state",
                             {"state": state, "expected_revision": expected_revision,
                              "expected_state_revision": expected_state_revision,
                              "idempotency_key": idempotency_key, "reason": reason})

    def inspect_goal(self, goal_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/v4/goals/{goal_id}")

    def approve_baseline(
        self, goal_id: str, *, expected_revision: int, expires_at: str, reason: str
    ) -> dict[str, Any]:
        return self._request(
            "POST", f"/v1/v4/goals/{goal_id}/baseline",
            {"expected_revision": expected_revision, "expires_at": expires_at, "reason": reason},
        )

    def revoke_baseline(self, goal_id: str, approval_id: str, *, reason: str) -> dict[str, Any]:
        return self._request(
            "POST", f"/v1/v4/goals/{goal_id}/baseline/{approval_id}/revoke",
            {"reason": reason},
        )

    def request_cycle(self, goal_id: str, command: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"/v1/v4/goals/{goal_id}/requests", command)

    def prepare_schedule_cutover(self, goal_id: str, command: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"/v1/v4/goals/{goal_id}/schedule-cutover", command)

    def inspect_schedule_cutover(self, goal_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/v4/goals/{goal_id}/schedule-cutover")

    def activate_schedule_cutover(self, goal_id: str, *, idempotency_key: str) -> dict[str, Any]:
        return self._request("POST", f"/v1/v4/goals/{goal_id}/schedule-cutover/activate",
                             {"idempotency_key": idempotency_key})

    def poll_schedule_cutover(self, goal_id: str, *, expected_revision: int, idempotency_key: str) -> dict[str, Any]:
        return self._request("POST", f"/v1/v4/goals/{goal_id}/schedule-cutover/poll",
                             {"expected_revision": expected_revision, "idempotency_key": idempotency_key})

    def rollback_schedule_cutover(self, goal_id: str, *, idempotency_key: str) -> dict[str, Any]:
        return self._request("POST", f"/v1/v4/goals/{goal_id}/schedule-cutover/rollback",
                             {"idempotency_key": idempotency_key})

    def inspect_intent(self, intent_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/v4/intents/{intent_id}")

    def admit(self, intent_id: str) -> dict[str, Any]:
        return self._request("POST", f"/v1/v4/intents/{intent_id}/admit", {})

    def inspect(self, cycle_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/v4/cycles/{cycle_id}")

    def events(
        self, cycle_id: str, *, limit: int = 50, after: str | None = None
    ) -> dict[str, Any]:
        query = urlencode({"limit": limit} | ({"after": after} if after else {}))
        return self._request("GET", f"/v1/v4/cycles/{cycle_id}/events?{query}")

    def cancel(self, cycle_id: str, *, reason: str) -> dict[str, Any]:
        return self._request("POST", f"/v1/v4/cycles/{cycle_id}/cancel", {"reason": reason})

    def stop_workspace(
        self, workspace_id: str, *, stopped: bool, expected_revision: int,
        idempotency_key: str, reason: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST", f"/v1/workspaces/{workspace_id}/v4/stop",
            {"stopped": stopped, "expected_revision": expected_revision,
             "idempotency_key": idempotency_key, "reason": reason},
        )

    def stop_goal(
        self, goal_id: str, *, stopped: bool, expected_revision: int,
        idempotency_key: str, reason: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST", f"/v1/v4/goals/{goal_id}/stop",
            {"stopped": stopped, "expected_revision": expected_revision,
             "idempotency_key": idempotency_key, "reason": reason},
        )

    def open_case(self, target: str, target_id: str, command: dict[str, Any]) -> dict[str, Any]:
        if target not in {"intents", "cycles"}:
            raise ValueError("case target must be intents or cycles")
        return self._request("POST", f"/v1/v4/{target}/{target_id}/cases", command)

    def inspect_case(self, case_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/v4/cases/{case_id}")

    def respond_review(
        self, case_id: str, command: dict[str, Any], *, idempotency_key: str
    ) -> dict[str, Any]:
        return self._request(
            "POST", f"/v1/v4/cases/{case_id}/review", command,
            idempotency_key=idempotency_key,
        )

    def resume_case(
        self, case_id: str, *, expected_revision: int, idempotency_key: str
    ) -> dict[str, Any]:
        return self._request(
            "POST", f"/v1/v4/cases/{case_id}/resume",
            {"expected_revision": expected_revision, "idempotency_key": idempotency_key},
        )

    def terminalize_case(
        self, case_id: str, *, expected_revision: int, idempotency_key: str
    ) -> dict[str, Any]:
        return self._request(
            "POST", f"/v1/v4/cases/{case_id}/terminalize",
            {"expected_revision": expected_revision, "idempotency_key": idempotency_key},
        )

    def archive_case(self, case_id: str, command: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"/v1/v4/cases/{case_id}/archive", command)

    def ack_notification(self, notification_id: str) -> dict[str, Any]:
        return self._request("POST", f"/v1/v4/notifications/{notification_id}/ack", {})


@dataclass
class ParallelTeamsClient(CyclesClient):
    def submit(self, workspace_id: str, plan: dict[str, Any], *, idempotency_key: str):
        return self._request('POST', f'/v1/workspaces/{workspace_id}/agent-teams', plan,
                             idempotency_key=idempotency_key)

    def inspect(self, workspace_id: str, run_id: str):
        return self._request('GET', f'/v1/workspaces/{workspace_id}/agent-teams/{run_id}')

    def cancel(self, workspace_id: str, run_id: str):
        return self._request('POST', f'/v1/workspaces/{workspace_id}/agent-teams/{run_id}/cancel')


class LegacyIntelligenceClient(CyclesClient):
    """Signed V4 compatibility contract; requests require explicit goal/slot."""

    def submit(self, command: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST","/v1/intelligence/runs",command)

    def submit_brief(self, command: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST",f"/v1/intelligence/opportunities/{command['selected_opportunity_id']}/briefs",command)

    def adopt_native_schedule(self, goal_id: str, command: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST",f"/v1/intelligence/schedules/{goal_id}/adopt-native",command)

    def inspect(self, job_id: str) -> dict[str, Any]:
        return self._request("GET",f"/v1/intelligence/runs/{job_id}")

    def cancel(self, job_id: str, *, reason: str) -> dict[str, Any]:
        return self._request("POST",f"/v1/intelligence/runs/{job_id}/cancel",{"reason":reason})

    def bind_schedule(self, goal_id: str, *, expected_revision: int, niche: str) -> dict[str, Any]:
        return self._request("POST",f"/v1/intelligence/schedules/{goal_id}/bind",{"expected_revision":expected_revision,"niche":niche})


class SalienceClient:
    def __init__(self, base_url: str, token: str) -> None:
        self.agents = AgentsClient(base_url, token)
        self.intelligence = IntelligenceClient(base_url, token)
        self.creative = CreativeClient(base_url, token)
        self.publication = PublicationClient(base_url, token)
        self.cycles = CyclesClient(base_url, token)
        self.agent_teams = ParallelTeamsClient(base_url, token)
        self.legacy_intelligence = LegacyIntelligenceClient(base_url, token)
