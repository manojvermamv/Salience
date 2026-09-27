"""Explicitly enabled no-effects V4 fixture commands over canonical transactions."""

import asyncio
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from salience.cycles.admission import CycleAdmission
from salience.cycles.contracts import CycleRequest, parse_goal


router = APIRouter(prefix="/v1", tags=["v4 fixture cycles"])


class BaselineCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    expires_at: AwareDatetime
    reason: str = Field(min_length=1, max_length=2000)


class CancelCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=2000)


def _service(request: Request) -> CycleAdmission:
    principal = request.state.principal
    return CycleAdmission(
        request.app.state.identity_boundary.database_url,
        workspace_id=principal.workspace_id,
        subject_id=principal.subject_id,
        trace_context=request.state.trace_context,
    )


async def _invoke(call, *args, **kwargs):
    try:
        return await asyncio.to_thread(call, *args, **kwargs)
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/workspaces/{workspace_id}/v4/goals", status_code=201)
async def create_goal(workspace_id: UUID, payload: dict, request: Request, idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=256)):
    try:
        spec = parse_goal(payload)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail="invalid fixture goal specification") from error
    goal_id = await _invoke(_service(request).create_goal, spec, idempotency_key=idempotency_key)
    return {"goal_id": str(goal_id)}


@router.post("/v4/goals/{goal_id}/baseline")
async def approve_baseline(goal_id: UUID, command: BaselineCommand, request: Request):
    approval_id = await _invoke(
        _service(request).approve_baseline, goal_id,
        expected_revision=command.expected_revision,
        expires_at=command.expires_at,
        reason=command.reason,
    )
    return {"approval_id": str(approval_id)}


@router.post("/v4/goals/{goal_id}/requests")
async def request_cycle(goal_id: UUID, command: CycleRequest, request: Request):
    intent_id = await _invoke(_service(request).request_cycle, goal_id, command)
    return {"intent_id": str(intent_id)}


@router.post("/v4/intents/{intent_id}/admit")
async def admit(intent_id: UUID, request: Request):
    result = await _invoke(_service(request).admit, intent_id)
    return {
        "intent_id": str(intent_id),
        "disposition": result["disposition"],
        "reason": result["reason"],
        "cycle_id": str(result["cycle_id"]) if result["cycle_id"] else None,
        "eligibility_revision": result["eligibility_revision"],
    }


def _inspect(service: CycleAdmission, cycle_id: UUID):
    with service._command("cycles:read") as connection:
        row = connection.execute("""
            SELECT cycle.id AS cycle_id,intent.goal_id,cycle.context_id,cycle.operation_id,
                   cycle.state,cycle.disposition,context.payload->>'dry_run' AS dry_run
            FROM v4_cycles AS cycle
            JOIN v4_cycle_intents AS intent ON intent.id=cycle.intent_id
            JOIN v4_goals AS goal ON goal.id=intent.goal_id
            JOIN v4_run_contexts AS context ON context.id=cycle.context_id
            WHERE cycle.id=%s AND goal.workspace_id=%s
        """, (cycle_id, service.workspace_id)).fetchone()
        if not row:
            raise PermissionError("cycle outside current scope")
        return {
            "cycle_id": str(row["cycle_id"]), "goal_id": str(row["goal_id"]),
            "context_id": str(row["context_id"]),
            "operation_id": str(row["operation_id"]),
            "state": row["state"], "disposition": row["disposition"],
            "dry_run": row["dry_run"] == "true",
        }


@router.get("/v4/cycles/{cycle_id}")
async def inspect_cycle(cycle_id: UUID, request: Request):
    return await _invoke(_inspect, _service(request), cycle_id)


def _events(service: CycleAdmission, cycle_id: UUID, limit: int, after: UUID | None):
    with service._command("cycles:read") as connection:
        scoped = connection.execute("""
            SELECT 1 FROM v4_cycles AS cycle
            JOIN v4_cycle_intents AS intent ON intent.id=cycle.intent_id
            JOIN v4_goals AS goal ON goal.id=intent.goal_id
            WHERE cycle.id=%s AND goal.workspace_id=%s
        """, (cycle_id, service.workspace_id)).fetchone()
        if not scoped:
            raise PermissionError("cycle outside current scope")
        cursor: tuple[datetime, UUID] | None = None
        if after:
            row = connection.execute(
                "SELECT created_at,id FROM v4_cycle_events WHERE id=%s AND cycle_id=%s AND workspace_id=%s",
                (after, cycle_id, service.workspace_id),
            ).fetchone()
            if not row:
                raise ValueError("unknown event cursor")
            cursor = row["created_at"], row["id"]
        rows = connection.execute("""
            SELECT id,kind,traceparent,created_at FROM v4_cycle_events
            WHERE cycle_id=%s AND workspace_id=%s
              AND (%s::timestamptz IS NULL OR (created_at,id)>(%s::timestamptz,%s::uuid))
            ORDER BY created_at,id LIMIT %s
        """, (cycle_id, service.workspace_id,
              cursor[0] if cursor else None, cursor[0] if cursor else None,
              cursor[1] if cursor else None, limit + 1)).fetchall()
        selected = rows[:limit]
        return {
            "events": [
                {"event_id": str(row["id"]), "kind": row["kind"],
                 "traceparent": row["traceparent"],
                 "created_at": row["created_at"].isoformat()}
                for row in selected
            ],
            "next_cursor": str(selected[-1]["id"]) if len(rows) > limit else None,
        }


@router.get("/v4/cycles/{cycle_id}/events")
async def cycle_events(cycle_id: UUID, request: Request, limit: int = Query(50, ge=1, le=100), after: UUID | None = None):
    return await _invoke(_events, _service(request), cycle_id, limit, after)


@router.post("/v4/cycles/{cycle_id}/cancel")
async def cancel_cycle(cycle_id: UUID, command: CancelCommand, request: Request):
    result = await _invoke(
        _service(request).close, cycle_id,
        disposition="cancelled", reason=command.reason,
    )
    return {"cycle_id": str(result["id"]), "state": result["state"],
            "disposition": result["disposition"]}
