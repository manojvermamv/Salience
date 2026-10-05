"""Signed compatibility routes: all starts and controls commit V4 outbox."""

import asyncio
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from salience.cycles.admission import CycleAdmission
from salience.cycles.legacy_dispatch import LegacyDispatch, LegacyIntelligenceCommand, LegacyBriefCommand

router = APIRouter(prefix="/v1/intelligence", tags=["V4 legacy fixture compatibility"])


def service(request):
    actor = request.state.principal
    return LegacyDispatch(request.app.state.identity_boundary.database_url,
        workspace_id=actor.workspace_id,subject_id=actor.subject_id,
        task_queue=request.app.state.legacy_fixture_queue,trace_context=request.state.trace_context)


async def execute(operation, *args, **kwargs):
    try:
        return await asyncio.to_thread(operation,*args,**kwargs)
    except PermissionError as error:
        raise HTTPException(403,"current scoped authority required") from error
    except LookupError as error:
        raise HTTPException(404,"legacy job unavailable") from error
    except ValueError as error:
        raise HTTPException(409,"invalid or conflicting canonical command") from error


@router.post("/runs", status_code=202)
async def submit(payload: LegacyIntelligenceCommand, request: Request):
    return await execute(service(request).submit,payload)


@router.post("/opportunities/{opportunity_id}/briefs", status_code=202)
async def submit_brief(opportunity_id: UUID, payload: LegacyBriefCommand, request: Request):
    if opportunity_id != payload.selected_opportunity_id:
        raise HTTPException(409, "selected opportunity path and command disagree")
    return await execute(service(request).submit_brief, payload)


@router.get("/runs/{job_id}")
async def inspect(job_id: UUID, request: Request):
    return await execute(service(request).inspect,job_id)


class CancelCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1,max_length=2000)


@router.post("/runs/{job_id}/cancel")
async def cancel(job_id: UUID, payload: CancelCommand, request: Request):
    bridge = service(request)
    # Inspection and mutation both recheck database scope; this commits close
    # before the worker performs any Temporal signal.
    view = await execute(bridge.inspect,job_id)
    await execute(bridge.close,view["cycle_id"],disposition="cancelled",reason=payload.reason)
    return await execute(bridge.inspect,job_id)


class ScheduleBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(strict=True,ge=1)
    niche: str = Field(min_length=1,max_length=256)


@router.post("/schedules/{goal_id}/bind")
async def bind_schedule(goal_id: UUID, payload: ScheduleBinding, request: Request):
    return await execute(service(request).bind_schedule,goal_id,expected_revision=payload.expected_revision,niche=payload.niche)
