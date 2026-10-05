"""Signed no-send dummy compatibility, backed by canonical original cycles."""

from uuid import UUID

from fastapi import APIRouter, Request

from salience.api.routes.legacy_dispatch import service, execute, CancelCommand
from salience.cycles.legacy_dispatch import LegacyDummyCommand

router = APIRouter(prefix="/v1/jobs", tags=["V4 legacy fixture jobs"])


@router.post("/dummy", status_code=202)
async def submit(payload: LegacyDummyCommand, request: Request):
    return await execute(service(request).submit_dummy, payload)


@router.get("/{job_id}")
async def inspect(job_id: UUID, request: Request):
    return await execute(service(request).inspect, job_id)


@router.post("/{job_id}/cancel")
async def cancel(job_id: UUID, payload: CancelCommand, request: Request):
    bridge = service(request)
    view = await execute(bridge.inspect, job_id)
    await execute(bridge.close, view["cycle_id"], disposition="cancelled", reason=payload.reason)
    return await execute(bridge.inspect, job_id)
