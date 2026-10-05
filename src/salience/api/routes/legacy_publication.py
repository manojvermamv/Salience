"""Signed V4 compatibility for the original governed-publication routes."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from salience.api.routes.legacy_dispatch import execute, service, CancelCommand
from salience.cycles.legacy_publication import LegacyPublicationCommand


router = APIRouter(prefix="/v1/publications", tags=["V4 legacy fixture publication"])


@router.post("/requests", status_code=202)
async def submit(payload: LegacyPublicationCommand, request: Request):
    return await execute(service(request).submit_publication, payload)


@router.get("/runs/{job_id}")
async def inspect(job_id: UUID, request: Request):
    return await execute(service(request).inspect, job_id)


@router.post("/runs/{job_id}/cancel")
async def cancel(job_id: UUID, payload: CancelCommand, request: Request):
    bridge = service(request)
    view = await execute(bridge.inspect, job_id)
    await execute(bridge.close, view["cycle_id"], disposition="cancelled", reason=payload.reason)
    return await execute(bridge.inspect, job_id)


class PublicationAccountBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(strict=True, ge=1)
    publisher_account_id: UUID


@router.post("/goals/{goal_id}/account-binding")
async def bind_account(goal_id: UUID, payload: PublicationAccountBinding, request: Request):
    return await execute(
        service(request).bind_publication_account,
        goal_id,
        expected_revision=payload.expected_revision,
        publisher_account_id=payload.publisher_account_id,
    )


class PublicationScheduleBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(strict=True, ge=1)


@router.post("/schedules/{goal_id}/bind")
async def bind_schedule(goal_id: UUID, payload: PublicationScheduleBinding, request: Request):
    return await execute(
        service(request).bind_publication_schedule,
        goal_id,
        expected_revision=payload.expected_revision,
    )


class NativePublicationScheduleAdoption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    legacy_schedule_id: UUID
    publication_schedule_id: UUID
    expected_revision: int = Field(strict=True, ge=1)
    first_v4_slot: AwareDatetime


@router.post("/schedules/{goal_id}/adopt-native")
async def adopt_native_schedule(
    goal_id: UUID, payload: NativePublicationScheduleAdoption, request: Request
):
    control = getattr(request.app.state, "legacy_schedule_control", None)
    if control is None:
        raise HTTPException(403, "explicit no-effects Temporal schedule control required")
    return await execute(
        control.adopt_native_publication,
        goal_id,
        schedule_id=payload.legacy_schedule_id,
        publication_schedule_id=payload.publication_schedule_id,
        subject_id=request.state.principal.subject_id,
        expected_revision=payload.expected_revision,
        first_v4_slot=payload.first_v4_slot,
    )
