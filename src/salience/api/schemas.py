from pydantic import BaseModel, Field


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
