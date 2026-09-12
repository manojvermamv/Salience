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
