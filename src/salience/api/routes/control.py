from fastapi import APIRouter, Depends, HTTPException, Request, status

from salience.api.dependencies import RequestContext, require_scope
from salience.api.schemas import (
    ContentProgramRequest,
    ContentProgramResponse,
    DummyJobRequest,
    JobInspectionResponse,
    JobResponse,
    WorkspaceRequest,
    WorkspaceResponse,
)


router = APIRouter(prefix="/v1", tags=["control"])


@router.post("/workspaces", response_model=WorkspaceResponse, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    payload: WorkspaceRequest,
    request: Request,
    _: RequestContext = Depends(require_scope("control:write")),
) -> WorkspaceResponse:
    workspace = await request.app.state.control_plane.create_workspace(
        slug=payload.slug, display_name=payload.display_name
    )
    return WorkspaceResponse(**workspace.__dict__)


@router.post(
    "/workspaces/{workspace_id}/programs",
    response_model=ContentProgramResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_content_program(
    workspace_id: str,
    payload: ContentProgramRequest,
    request: Request,
    _: RequestContext = Depends(require_scope("control:write")),
) -> ContentProgramResponse:
    try:
        program = await request.app.state.control_plane.create_content_program(
            workspace_id=workspace_id,
            slug=payload.slug,
            name=payload.name,
            niche=payload.niche,
        )
    except KeyError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from error
    return ContentProgramResponse(**program.__dict__)


@router.post("/jobs/dummy", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
async def start_dummy(
    payload: DummyJobRequest,
    request: Request,
    _: RequestContext = Depends(require_scope("control:write")),
) -> JobResponse:
    try:
        job = await request.app.state.control_plane.start_dummy(
            dry_run=payload.dry_run,
            idempotency_key=payload.idempotency_key,
        )
    except PermissionError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    return JobResponse(job_id=job.job_id, state=job.state, dry_run=job.dry_run)


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: str,
    request: Request,
    _: RequestContext = Depends(require_scope("control:read")),
) -> JobResponse:
    job = await request.app.state.control_plane.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return JobResponse(job_id=job.job_id, state=job.state, dry_run=job.dry_run)


@router.get("/jobs/{job_id}/inspection", response_model=JobInspectionResponse)
async def inspect_job(
    job_id: str,
    request: Request,
    _: RequestContext = Depends(require_scope("control:read")),
) -> JobInspectionResponse:
    job = await request.app.state.control_plane.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return JobInspectionResponse(
        job_id=job.job_id,
        trace_id=job.trace_id,
        audit_events=job.audit_events,
        provenance_records=job.provenance_records,
        cost_entries=job.cost_entries,
    )


@router.get("/jobs/{job_id}/audit")
async def job_audit(
    job_id: str,
    request: Request,
    _: RequestContext = Depends(require_scope("control:read")),
) -> list[dict[str, object]]:
    job = await request.app.state.control_plane.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return job.audit_events


@router.get("/jobs/{job_id}/provenance")
async def job_provenance(
    job_id: str,
    request: Request,
    _: RequestContext = Depends(require_scope("control:read")),
) -> list[dict[str, object]]:
    job = await request.app.state.control_plane.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return job.provenance_records


@router.get("/jobs/{job_id}/costs")
async def job_costs(
    job_id: str,
    request: Request,
    _: RequestContext = Depends(require_scope("control:read")),
) -> list[dict[str, object]]:
    job = await request.app.state.control_plane.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return job.cost_entries


@router.get("/jobs/{job_id}/trace")
async def job_trace(
    job_id: str,
    request: Request,
    _: RequestContext = Depends(require_scope("control:read")),
) -> dict[str, str]:
    job = await request.app.state.control_plane.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return {"trace_id": job.trace_id}
