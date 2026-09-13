"""Scoped governed-publication controls and verified publisher webhook ingress."""

import json

from fastapi import APIRouter, Depends, HTTPException, Request, status

from salience.api.dependencies import RequestContext, require_scope
from salience.api.schemas import (
    PublicationRunRequest,
    PublicationRunResponse,
    PublicationScheduleRequest,
    PublicationScheduleResponse,
    PublicationWebhookResponse,
)
from salience.publication.providers import FixturePublisherError


router = APIRouter(prefix="/v1", tags=["publication"])


@router.post(
    "/publications/requests",
    response_model=PublicationRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_publication(
    payload: PublicationRunRequest,
    request: Request,
    _: RequestContext = Depends(require_scope("control:write")),
) -> PublicationRunResponse:
    try:
        run = await request.app.state.control_plane.start_publication(
            workspace_id=payload.workspace_id,
            content_program_id=payload.content_program_id,
            ready_package_id=payload.ready_package_id,
            publisher_account_id=payload.publisher_account_id,
            budget_id=payload.budget_id,
            idempotency_key=payload.idempotency_key,
            platform=payload.platform,
            destination=payload.destination,
            locale=payload.locale,
            territory=payload.territory,
            visibility=payload.visibility,
            capability_profile_version=payload.capability_profile_version,
        )
    except KeyError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from error
    except (PermissionError, ValueError) as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error
    return PublicationRunResponse(**run.__dict__)


@router.get("/publications/runs/{job_id}", response_model=PublicationRunResponse)
async def get_publication(
    job_id: str,
    request: Request,
    _: RequestContext = Depends(require_scope("control:read")),
) -> PublicationRunResponse:
    run = await request.app.state.control_plane.get_publication(job_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return PublicationRunResponse(**run.__dict__)


@router.post(
    "/publications/schedules",
    response_model=PublicationScheduleResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_publication_schedule(
    payload: PublicationScheduleRequest,
    request: Request,
    _: RequestContext = Depends(require_scope("control:write")),
) -> PublicationScheduleResponse:
    try:
        schedule = await request.app.state.control_plane.create_publication_schedule(
            **payload.model_dump()
        )
    except KeyError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from error
    except (PermissionError, ValueError) as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error
    return PublicationScheduleResponse(**schedule.__dict__)


@router.post("/publications/runs/{job_id}/cancel", response_model=PublicationRunResponse)
async def cancel_publication(
    job_id: str,
    request: Request,
    _: RequestContext = Depends(require_scope("control:write")),
) -> PublicationRunResponse:
    run = await request.app.state.control_plane.cancel_publication(job_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return PublicationRunResponse(**run.__dict__)


@router.post(
    "/publishers/{provider_id}/webhooks",
    response_model=PublicationWebhookResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def receive_publisher_webhook(provider_id: str, request: Request) -> PublicationWebhookResponse:
    provider = request.app.state.publisher_adapters.get(provider_id)
    repository = request.app.state.publication_repository
    if provider is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    try:
        body = json.loads((await request.body()).decode("utf-8"))
        if not isinstance(body, dict):
            raise ValueError("publisher webhook must be an object")
        event = await provider.verify_webhook(body)
        if event.publisher_id != provider_id:
            raise FixturePublisherError("publisher webhook identity does not match route")
        receipt = await repository.record_verified_webhook_event(event, trace_id="publication-webhook")
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT) from error
    except FixturePublisherError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from error
    return PublicationWebhookResponse(
        receipt_id=receipt.receipt_id,
        publication_attempt_id=receipt.publication_attempt_id,
        state=receipt.state,
    )
