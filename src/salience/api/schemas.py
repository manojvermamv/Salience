from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DummyJobRequest(BaseModel):
    dry_run: bool = True
    idempotency_key: str = Field(min_length=1, max_length=255)


class JobResponse(BaseModel):
    job_id: str
    state: str
    dry_run: bool


class JobInspectionResponse(BaseModel):
    job_id: str
    trace_id: str
    audit_events: list[dict[str, object]]
    provenance_records: list[dict[str, object]]
    cost_entries: list[dict[str, object]]


class WorkspaceRequest(BaseModel):
    slug: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9-]+$")
    display_name: str = Field(min_length=1, max_length=255)


class WorkspaceResponse(BaseModel):
    workspace_id: str
    slug: str
    display_name: str


class ContentProgramRequest(BaseModel):
    slug: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9-]+$")
    name: str = Field(min_length=1, max_length=255)
    niche: str = Field(min_length=1)


class ContentProgramResponse(BaseModel):
    content_program_id: str
    workspace_id: str
    slug: str
    name: str
    niche: str


class AgentRunRequest(BaseModel):
    input: dict[str, object]
    mode: str = "async"


class AgentResponse(BaseModel):
    agent_id: str
    version: str
    status: str
    supports_sync: bool
    supports_async: bool
    runtime_id: str | None


class AgentRunResponse(BaseModel):
    run_id: str
    agent_id: str
    status: str
    parent_run_id: str | None
    output: dict[str, object]


class IntelligenceRunRequest(BaseModel):
    contract_version: str = "IntelligenceRunRequest@v1"
    workspace_id: str = Field(min_length=1)
    content_program_id: str = Field(min_length=1)
    niche: str = Field(min_length=1, max_length=500)
    dry_run: bool = True
    idempotency_key: str = Field(min_length=1, max_length=255)


class IntelligenceRunResponse(BaseModel):
    job_id: str
    state: str
    dry_run: bool
    trace_id: str
    output: dict[str, object] = Field(default_factory=dict)


class IntelligenceScheduleRequest(BaseModel):
    workspace_id: str = Field(min_length=1)
    content_program_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9-]+$")
    every_seconds: int = Field(gt=0, le=31_536_000)
    niche: str = Field(min_length=1, max_length=500)


class IntelligenceScheduleResponse(BaseModel):
    schedule_id: str
    workspace_id: str
    content_program_id: str
    name: str
    job_type: str
    schedule_expression: str


class ContentBriefRequest(BaseModel):
    contract_version: str = "ContentBriefRequest@v1"
    content_program_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1, max_length=255)
    dry_run: bool = True


class ContentBriefResponse(BaseModel):
    brief_id: str
    content_program_id: str
    opportunity_id: str
    package_id: str
    content: dict[str, object]
    claim_ids: list[str]


class CreativeRunRequest(BaseModel):
    contract_version: Literal["CreativeProductionRequest@v1"] = "CreativeProductionRequest@v1"
    workspace_id: str = Field(min_length=1)
    content_program_id: str = Field(min_length=1)
    brief_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1, max_length=255)
    target_profile_key: str = Field(min_length=1, max_length=255)
    target_profile_version: int = Field(default=1, gt=0)
    max_variants: int = Field(default=1, gt=0, le=3)
    dry_run: bool = True
    budget_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _require_budget_for_non_dry_run(self) -> "CreativeRunRequest":
        if not self.dry_run and self.budget_id is None:
            raise ValueError("non-dry creative runs require a budget identity")
        return self


class CreativeRunResponse(BaseModel):
    job_id: str
    state: str
    dry_run: bool
    trace_id: str
    output: dict[str, object] = Field(default_factory=dict)


class CreativeScriptResponse(BaseModel):
    job_id: str
    trace_id: str
    script_id: str


class CreativeAssetResponse(BaseModel):
    job_id: str
    trace_id: str
    asset_id: str


class CreativePackageResponse(BaseModel):
    job_id: str
    trace_id: str
    ready_package_id: str


class CreativeWebhookResponse(BaseModel):
    receipt_id: str
    provider_job_id: str
    state: str


class PublicationRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["PublicationWorkflowRequest@v1"] = "PublicationWorkflowRequest@v1"
    workspace_id: str = Field(min_length=1)
    content_program_id: str = Field(min_length=1)
    ready_package_id: str = Field(min_length=1)
    publisher_account_id: str = Field(min_length=1)
    budget_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1, max_length=255)
    platform: str = Field(default="fixture", min_length=1, max_length=64)
    destination: str = Field(default="fixture://account", min_length=1, max_length=255)
    locale: str = Field(default="en", min_length=1, max_length=32)
    territory: str = Field(default="global", min_length=1, max_length=64)
    visibility: Literal["private", "unlisted", "public"] = "private"
    capability_profile_version: int = Field(default=1, gt=0)


class PublicationRunResponse(BaseModel):
    job_id: str
    state: str
    dry_run: bool
    trace_id: str
    output: dict[str, object] = Field(default_factory=dict)


class PublicationScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1)
    content_program_id: str = Field(min_length=1)
    publication_request_id: str = Field(min_length=1)
    publication_plan_id: str = Field(min_length=1)
    schedule_version: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9-]+$")
    every_seconds: int = Field(gt=0, le=31_536_000)
    ready_package_id: str = Field(min_length=1)
    publisher_account_id: str = Field(min_length=1)
    budget_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1, max_length=255)
    platform: str = Field(default="fixture", min_length=1, max_length=64)
    destination: str = Field(default="fixture://account", min_length=1, max_length=255)
    locale: str = Field(default="en", min_length=1, max_length=32)
    territory: str = Field(default="global", min_length=1, max_length=64)
    visibility: Literal["private", "unlisted", "public"] = "private"
    capability_profile_version: int = Field(default=1, gt=0)


class PublicationScheduleResponse(BaseModel):
    schedule_id: str
    workspace_id: str
    content_program_id: str
    name: str
    job_type: str
    schedule_expression: str


class PublicationWebhookResponse(BaseModel):
    receipt_id: str
    publication_attempt_id: str
    state: str
