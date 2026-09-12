from fastapi import APIRouter, Depends, HTTPException, Request, status

from salience.api.dependencies import RequestContext, require_scope
from salience.api.schemas import (
    ContentBriefRequest,
    ContentBriefResponse,
    IntelligenceRunRequest,
    IntelligenceRunResponse,
    IntelligenceScheduleRequest,
    IntelligenceScheduleResponse,
)


router = APIRouter(prefix="/v1/intelligence", tags=["intelligence"])


@router.post("/runs", response_model=IntelligenceRunResponse, status_code=status.HTTP_202_ACCEPTED)
async def start_run(
    payload: IntelligenceRunRequest,
    request: Request,
    _: RequestContext = Depends(require_scope("control:write")),
) -> IntelligenceRunResponse:
    try:
        run = await request.app.state.control_plane.start_intelligence(
            workspace_id=payload.workspace_id,
            content_program_id=payload.content_program_id,
            niche=payload.niche,
            dry_run=payload.dry_run,
            idempotency_key=payload.idempotency_key,
        )
    except (KeyError, PermissionError, ValueError) as error:
        raise HTTPException(
            status_code=(status.HTTP_404_NOT_FOUND if isinstance(error, KeyError) else status.HTTP_403_FORBIDDEN),
            detail=str(error),
        ) from error
    return IntelligenceRunResponse(
        job_id=run.job_id,
        state=run.state,
        dry_run=run.dry_run,
        trace_id=run.trace_id,
        output=run.output,
    )


@router.get("/runs/{job_id}", response_model=IntelligenceRunResponse)
async def get_run(
    job_id: str,
    request: Request,
    _: RequestContext = Depends(require_scope("control:read")),
) -> IntelligenceRunResponse:
    run = await request.app.state.control_plane.get_intelligence(job_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return IntelligenceRunResponse(
        job_id=run.job_id,
        state=run.state,
        dry_run=run.dry_run,
        trace_id=run.trace_id,
        output=run.output,
    )


@router.post(
    "/schedules",
    response_model=IntelligenceScheduleResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_schedule(
    payload: IntelligenceScheduleRequest,
    request: Request,
    _: RequestContext = Depends(require_scope("control:write")),
) -> IntelligenceScheduleResponse:
    try:
        schedule = await request.app.state.control_plane.create_intelligence_schedule(
            workspace_id=payload.workspace_id,
            content_program_id=payload.content_program_id,
            name=payload.name,
            every_seconds=payload.every_seconds,
            niche=payload.niche,
        )
    except KeyError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from error
    return IntelligenceScheduleResponse(**schedule.__dict__)


@router.post(
    "/opportunities/{opportunity_id}/briefs",
    response_model=IntelligenceRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_content_brief(
    opportunity_id: str,
    payload: ContentBriefRequest,
    request: Request,
    _: RequestContext = Depends(require_scope("control:write")),
) -> IntelligenceRunResponse:
    try:
        run = await request.app.state.control_plane.start_content_brief(
            opportunity_id=opportunity_id,
            content_program_id=payload.content_program_id,
            idempotency_key=payload.idempotency_key,
            dry_run=payload.dry_run,
        )
    except (KeyError, PermissionError) as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND if isinstance(error, KeyError) else status.HTTP_403_FORBIDDEN,
            detail=str(error),
        ) from error
    return IntelligenceRunResponse(
        job_id=run.job_id,
        state=run.state,
        dry_run=run.dry_run,
        trace_id=run.trace_id,
        output=run.output,
    )


@router.get("/briefs/{brief_id}", response_model=ContentBriefResponse)
async def get_content_brief(
    brief_id: str,
    request: Request,
    _: RequestContext = Depends(require_scope("control:read")),
) -> ContentBriefResponse:
    brief = await request.app.state.control_plane.get_content_brief(brief_id)
    if brief is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return ContentBriefResponse(**brief.__dict__)


@router.get("/briefs/{brief_id}/lineage")
async def get_content_brief_lineage(
    brief_id: str,
    request: Request,
    _: RequestContext = Depends(require_scope("control:read")),
) -> dict[str, object]:
    lineage = await request.app.state.control_plane.get_content_brief_lineage(brief_id)
    if lineage is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return lineage
