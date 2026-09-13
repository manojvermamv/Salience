from dataclasses import dataclass
from typing import Any

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
        ready_package_id: str,
        publisher_account_id: str,
        budget_id: str,
        idempotency_key: str,
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
                    "ready_package_id": ready_package_id,
                    "publisher_account_id": publisher_account_id,
                    "budget_id": budget_id,
                    "idempotency_key": idempotency_key,
                },
            )
        )

    def cancel(self, job_id: str) -> PublicationRunView:
        return PublicationRunView.model_validate(
            self._request("POST", f"/v1/publications/runs/{job_id}/cancel")
        )


class SalienceClient:
    def __init__(self, base_url: str, token: str) -> None:
        self.agents = AgentsClient(base_url, token)
        self.intelligence = IntelligenceClient(base_url, token)
        self.creative = CreativeClient(base_url, token)
        self.publication = PublicationClient(base_url, token)
