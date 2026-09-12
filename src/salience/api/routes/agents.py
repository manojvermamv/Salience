from fastapi import APIRouter, Depends, HTTPException, Request, status

from salience.agents.execution import AgentInvocation
from salience.agents.registry import AgentNotFound
from salience.api.dependencies import RequestContext, require_scope
from salience.api.schemas import AgentResponse, AgentRunRequest, AgentRunResponse


router = APIRouter(prefix="/v1/agents", tags=["agents"])


def _agent_response(manifest) -> AgentResponse:
    return AgentResponse(
        agent_id=manifest.agent_id,
        version=manifest.version,
        status=manifest.status,
        supports_sync=manifest.supports_sync,
        supports_async=manifest.supports_async,
        runtime_id=manifest.runtime_id,
    )


@router.get("", response_model=list[AgentResponse])
async def list_agents(
    request: Request,
    _: RequestContext = Depends(require_scope("control:read")),
) -> list[AgentResponse]:
    return [_agent_response(manifest) for manifest in request.app.state.agent_service.list_agents()]


@router.get("/{agent_id}", response_model=AgentResponse)
async def describe_agent(
    agent_id: str,
    request: Request,
    _: RequestContext = Depends(require_scope("control:read")),
) -> AgentResponse:
    try:
        return _agent_response(request.app.state.agent_service.describe_agent(agent_id))
    except AgentNotFound as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from error


@router.post("/{agent_id}/runs", response_model=AgentRunResponse, status_code=status.HTTP_201_CREATED)
async def run_agent(
    agent_id: str,
    payload: AgentRunRequest,
    request: Request,
    _: RequestContext = Depends(require_scope("control:write")),
) -> AgentRunResponse:
    try:
        run = await request.app.state.agent_service.invoke(
            AgentInvocation(agent_id=agent_id, input=payload.input, mode=payload.mode)
        )
    except (AgentNotFound, LookupError) as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
    return AgentRunResponse(
        run_id=str(run.id),
        agent_id=run.agent_id,
        status=run.status,
        parent_run_id=str(run.parent_run_id) if run.parent_run_id else None,
        output=run.output,
    )
