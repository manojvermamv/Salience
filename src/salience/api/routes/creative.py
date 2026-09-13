"""Scoped inspection and start routes for provider-neutral creative workflows."""

import json

from fastapi import APIRouter, Depends, HTTPException, Request, status

from salience.api.dependencies import RequestContext, require_scope
from salience.api.schemas import (
    CreativeAssetResponse,
    CreativePackageResponse,
    CreativeRunRequest,
    CreativeRunResponse,
    CreativeScriptResponse,
    CreativeWebhookResponse,
)
from salience.creative.providers import ProviderWebhookRejected


router = APIRouter(prefix="/v1/creative", tags=["creative"])


@router.post(
    "/providers/{provider_id}/webhooks",
    response_model=CreativeWebhookResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def receive_provider_webhook(provider_id: str, request: Request) -> CreativeWebhookResponse:
    provider = request.app.state.creative_providers.get(provider_id)
    repository = request.app.state.creative_repository
    if provider is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    try:
        payload = json.loads((await request.body()).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT) from error
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT)
    try:
        event = await provider.verify_webhook(
            payload, signature=request.headers.get("X-Salience-Signature")
        )
        if event.provider_id != provider_id:
            raise ProviderWebhookRejected("provider webhook identity does not match route")
        receipt = await repository.record_verified_webhook(event=event, trace_id="webhook-ingress")
    except ProviderWebhookRejected as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from error
    return CreativeWebhookResponse(**receipt.__dict__)


@router.post("/runs", response_model=CreativeRunResponse, status_code=status.HTTP_202_ACCEPTED)
async def start_run(
    payload: CreativeRunRequest,
    request: Request,
    _: RequestContext = Depends(require_scope("control:write")),
) -> CreativeRunResponse:
    try:
        run = await request.app.state.control_plane.start_creative(
            workspace_id=payload.workspace_id,
            content_program_id=payload.content_program_id,
            brief_id=payload.brief_id,
            idempotency_key=payload.idempotency_key,
            target_profile_key=payload.target_profile_key,
            target_profile_version=payload.target_profile_version,
            dry_run=payload.dry_run,
            budget_id=payload.budget_id,
        )
    except KeyError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from error
    except PermissionError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error
    return CreativeRunResponse(**run.__dict__)


@router.get("/runs/{job_id}", response_model=CreativeRunResponse)
async def get_run(
    job_id: str,
    request: Request,
    _: RequestContext = Depends(require_scope("control:read")),
) -> CreativeRunResponse:
    run = await request.app.state.control_plane.get_creative(job_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return CreativeRunResponse(**run.__dict__)


@router.get("/runs/{job_id}/script", response_model=CreativeScriptResponse)
async def get_script(
    job_id: str,
    request: Request,
    _: RequestContext = Depends(require_scope("control:read")),
) -> CreativeScriptResponse:
    run = await _creative_run_or_not_found(request, job_id)
    script_id = run.output.get("script_id")
    if not isinstance(script_id, str) or not script_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return CreativeScriptResponse(job_id=run.job_id, trace_id=run.trace_id, script_id=script_id)


@router.get("/runs/{job_id}/asset", response_model=CreativeAssetResponse)
async def get_asset(
    job_id: str,
    request: Request,
    _: RequestContext = Depends(require_scope("control:read")),
) -> CreativeAssetResponse:
    run = await _creative_run_or_not_found(request, job_id)
    asset_id = run.output.get("asset_id")
    if not isinstance(asset_id, str) or not asset_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return CreativeAssetResponse(job_id=run.job_id, trace_id=run.trace_id, asset_id=asset_id)


@router.get("/runs/{job_id}/package", response_model=CreativePackageResponse)
async def get_package(
    job_id: str,
    request: Request,
    _: RequestContext = Depends(require_scope("control:read")),
) -> CreativePackageResponse:
    run = await _creative_run_or_not_found(request, job_id)
    ready_package_id = run.output.get("ready_package_id")
    if not isinstance(ready_package_id, str) or not ready_package_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return CreativePackageResponse(
        job_id=run.job_id,
        trace_id=run.trace_id,
        ready_package_id=ready_package_id,
    )


@router.get("/packages/{ready_package_id}/lineage")
async def get_package_lineage(
    ready_package_id: str,
    request: Request,
    _: RequestContext = Depends(require_scope("control:read")),
) -> dict[str, object]:
    lineage = await request.app.state.control_plane.get_ready_package_lineage(ready_package_id)
    if lineage is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return lineage


async def _creative_run_or_not_found(request: Request, job_id: str):
    run = await request.app.state.control_plane.get_creative(job_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return run
