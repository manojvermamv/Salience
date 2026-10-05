"""Signed, explicitly enabled local parallel-agent commands."""
import asyncio
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request

from salience.agents.parallel_contracts import ParallelTeamRequest
from salience.agents.parallel_store import ParallelTeamStore

router = APIRouter(prefix='/v1/workspaces/{workspace_id}/agent-teams')


async def command(request, operation, *args):
    principal = request.state.principal
    store = ParallelTeamStore(request.app.state.identity_boundary.database_url,
        workspace_id=principal.workspace_id, service=request.app.state.parallel_agent_service)
    try:
        return await asyncio.to_thread(getattr(store, operation), principal.subject_id, *args)
    except PermissionError as error:
        raise HTTPException(403, 'current authority required') from error
    except LookupError as error:
        raise HTTPException(404, 'agent or team unavailable') from error
    except ValueError as error:
        raise HTTPException(409, 'invalid or conflicting team plan') from error


@router.post('', status_code=202)
async def submit(workspace_id: UUID, request: Request, plan: ParallelTeamRequest,
                 idempotency_key: str = Header(alias='Idempotency-Key', min_length=1, max_length=256)):
    return await command(request, 'submit', plan, idempotency_key)


@router.get('/{run_id}')
async def inspect(workspace_id: UUID, run_id: UUID, request: Request):
    return await command(request, 'inspect', run_id)


@router.post('/{run_id}/cancel')
async def cancel(workspace_id: UUID, run_id: UUID, request: Request):
    return await command(request, 'cancel', run_id)
