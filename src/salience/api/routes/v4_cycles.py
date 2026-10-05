"""Explicitly enabled no-effects V4 fixture commands over canonical transactions."""

import asyncio
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from salience.cycles.admission import CycleAdmission
from salience.cycles.contracts import CycleRequest, parse_goal
from salience.cycles.governance import CycleGovernance, ReviewResponse
from salience.cycles.schedule_cutover import CycleScheduleCutover


router = APIRouter(prefix="/v1", tags=["v4 fixture cycles"])


class BaselineCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    expires_at: AwareDatetime
    reason: str = Field(min_length=1, max_length=2000)


class GoalRevisionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=256)
    reason: str = Field(min_length=1, max_length=2000)
    spec: dict


class GoalStateCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["draft", "active", "paused", "completed", "cancelled"]
    expected_revision: int = Field(strict=True, ge=1)
    expected_state_revision: int = Field(strict=True, ge=1)
    idempotency_key: str = Field(min_length=1, max_length=256)
    reason: str = Field(min_length=1, max_length=2000)


class CancelCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=2000)


class StopCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stopped: bool
    expected_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=256)
    reason: str = Field(min_length=1, max_length=2000)


class OpenCaseCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["admission_review", "retry", "reconciliation", "rework", "manual_review"]
    failure_class: Literal["policy_denial", "technical_failure", "quality_failure", "unknown_effect", "admission_review", "deadline", "manual_review"]
    owner_id: UUID
    deadline: AwareDatetime
    reason: str = Field(min_length=1, max_length=2000)
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    account_ref: Literal["fixture-account"]


class CaseRevisionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=256)


class ArchiveCaseCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=2000)
    evidence: dict
    retain_until: AwareDatetime


class CutoverPrepareCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    legacy_schedule_id: UUID
    expected_revision: int = Field(ge=1)
    first_v4_slot: AwareDatetime
    idempotency_key: str = Field(min_length=1, max_length=256)


class CutoverPollCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=256)


class CutoverTransitionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=256)


def _service(request: Request) -> CycleGovernance:
    principal = request.state.principal
    return CycleGovernance(
        request.app.state.identity_boundary.database_url,
        workspace_id=principal.workspace_id,
        subject_id=principal.subject_id,
        trace_context=request.state.trace_context,
    )


def _schedule_service(request: Request) -> CycleScheduleCutover:
    principal = request.state.principal
    return CycleScheduleCutover(
        request.app.state.identity_boundary.database_url,
        workspace_id=principal.workspace_id,
        subject_id=principal.subject_id,
        trace_context=request.state.trace_context,
        legacy_control=getattr(request.app.state,"legacy_schedule_control",None),
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


@router.post("/v4/goals/{goal_id}/revisions")
async def revise_goal(goal_id: UUID, command: GoalRevisionCommand, request: Request):
    try:
        spec = parse_goal(command.spec)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail="invalid fixture goal specification") from error
    revision = await _invoke(
        _service(request).revise_goal, goal_id, spec,
        expected_revision=command.expected_revision,
        idempotency_key=command.idempotency_key, reason=command.reason,
    )
    return {"goal_id": str(goal_id), "revision": revision}


@router.post("/v4/goals/{goal_id}/state")
async def set_goal_state(goal_id: UUID, command: GoalStateCommand, request: Request):
    return await _invoke(_service(request).set_goal_state, goal_id, command.state,
                         expected_revision=command.expected_revision,
                         expected_state_revision=command.expected_state_revision,
                         idempotency_key=command.idempotency_key, reason=command.reason)


@router.get("/v4/goals/{goal_id}")
async def inspect_goal(goal_id: UUID, request: Request):
    return await _invoke(_service(request).inspect_goal,goal_id)


@router.post("/v4/goals/{goal_id}/baseline")
async def approve_baseline(goal_id: UUID, command: BaselineCommand, request: Request):
    approval_id = await _invoke(
        _service(request).approve_baseline, goal_id,
        expected_revision=command.expected_revision,
        expires_at=command.expires_at,
        reason=command.reason,
    )
    return {"approval_id": str(approval_id)}


@router.post("/v4/goals/{goal_id}/baseline/{approval_id}/revoke")
async def revoke_baseline(goal_id: UUID, approval_id: UUID, command: CancelCommand, request: Request):
    await _invoke(_service(request).revoke_baseline, goal_id, approval_id=approval_id,
                  reason=command.reason)
    return {"goal_id": str(goal_id), "approval_id": str(approval_id), "revoked": True}


@router.post("/v4/goals/{goal_id}/requests")
async def request_cycle(goal_id: UUID, command: CycleRequest, request: Request):
    intent_id = await _invoke(_service(request).request_cycle, goal_id, command)
    return {"intent_id": str(intent_id)}


@router.post("/v4/goals/{goal_id}/schedule-cutover")
async def prepare_schedule_cutover(goal_id: UUID, command: CutoverPrepareCommand, request: Request):
    return await _invoke(
        _schedule_service(request).prepare, goal_id,
        legacy_schedule_id=command.legacy_schedule_id,
        expected_revision=command.expected_revision,
        first_v4_slot=command.first_v4_slot,
        idempotency_key=command.idempotency_key,
    )


@router.get("/v4/goals/{goal_id}/schedule-cutover")
async def inspect_schedule_cutover(goal_id: UUID, request: Request):
    return await _invoke(_schedule_service(request).inspect, goal_id)


@router.post("/v4/goals/{goal_id}/schedule-cutover/activate")
async def activate_schedule_cutover(goal_id: UUID, command: CutoverTransitionCommand, request: Request):
    return await _invoke(_schedule_service(request).activate, goal_id, idempotency_key=command.idempotency_key)


@router.post("/v4/goals/{goal_id}/schedule-cutover/poll")
async def poll_schedule_cutover(goal_id: UUID, command: CutoverPollCommand, request: Request):
    return await _invoke(_schedule_service(request).poll, goal_id,
                         expected_revision=command.expected_revision, idempotency_key=command.idempotency_key)


@router.post("/v4/goals/{goal_id}/schedule-cutover/rollback")
async def rollback_schedule_cutover(goal_id: UUID, command: CutoverTransitionCommand, request: Request):
    return await _invoke(_schedule_service(request).rollback, goal_id, idempotency_key=command.idempotency_key)


def _inspect_intent(service, intent_id):
    with service._command("cycles:read") as connection:
        intent = connection.execute("""SELECT i.id AS intent_id,i.goal_id,i.goal_revision,i.eligibility_revision,
            i.due_at,i.expires_at,a.disposition,a.reason,a.cycle_id FROM v4_cycle_intents i
            JOIN v4_goals g ON g.id=i.goal_id LEFT JOIN v4_admissions a ON a.intent_id=i.id AND a.eligibility_revision=i.eligibility_revision
            WHERE i.id=%s AND g.workspace_id=%s""",(intent_id,service.workspace_id)).fetchone()
        if not intent:
            raise PermissionError("intent outside current scope")
        result = _safe_record(intent)
        result["runtime_waits"] = [_safe_record(wait) for wait in connection.execute(
            "SELECT id,revision,kind,due_at,state,runtime_id,result,handoff_attempts,owner_id FROM v4_runtime_waits WHERE intent_id=%s ORDER BY created_at,id",(intent_id,)).fetchall()]
        return result


@router.get("/v4/intents/{intent_id}")
async def inspect_intent(intent_id: UUID, request: Request):
    return await _invoke(_inspect_intent,_service(request),intent_id)


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
        result = {
            "cycle_id": str(row["cycle_id"]), "goal_id": str(row["goal_id"]),
            "context_id": str(row["context_id"]),
            "operation_id": str(row["operation_id"]),
            "state": row["state"], "disposition": row["disposition"],
            "dry_run": row["dry_run"] == "true",
        }
        hold = connection.execute("SELECT owner_id,reason,created_at FROM v4_runtime_holds WHERE cycle_id=%s",(cycle_id,)).fetchone()
        result["runtime_hold"] = _safe_record(hold) if hold else None
        return result


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


@router.post("/workspaces/{workspace_id}/v4/stop")
async def set_workspace_stop(workspace_id: UUID, command: StopCommand, request: Request):
    return await _invoke(_service(request).set_stop, stopped=command.stopped,
                         expected_revision=command.expected_revision,
                         idempotency_key=command.idempotency_key, reason=command.reason)


@router.post("/v4/goals/{goal_id}/stop")
async def set_goal_stop(goal_id: UUID, command: StopCommand, request: Request):
    return await _invoke(_service(request).set_stop, goal_id=goal_id,
                         stopped=command.stopped,
                         expected_revision=command.expected_revision,
                         idempotency_key=command.idempotency_key, reason=command.reason)


@router.post("/v4/intents/{intent_id}/cases")
async def open_intent_case(intent_id: UUID, command: OpenCaseCommand, request: Request):
    return await _invoke(_service(request).open_case, intent_id, target="intent",
                         **command.model_dump())


@router.post("/v4/cycles/{cycle_id}/cases")
async def open_cycle_case(cycle_id: UUID, command: OpenCaseCommand, request: Request):
    return await _invoke(_service(request).open_case, cycle_id, target="cycle",
                         **command.model_dump())


def _safe_record(row):
    return {field: value.isoformat() if isinstance(value,datetime) else str(value)
            if isinstance(value,UUID) else value for field,value in row.items()}


def _inspect_case(service: CycleGovernance, case_id: UUID):
    with service._command("cycles:read") as connection:
        row = connection.execute("""
            SELECT recovery.id,recovery.goal_id,recovery.target_intent_id,
                   recovery.target_cycle_id,recovery.context_id,recovery.operation_id,
                   recovery.kind,recovery.failure_class,recovery.state,
                   recovery.revision,recovery.owner_id,recovery.deadline
            FROM v4_recovery_cases AS recovery
            JOIN v4_goals AS goal ON goal.id=recovery.goal_id
            WHERE recovery.id=%s AND recovery.workspace_id=%s
              AND goal.workspace_id=%s
        """, (case_id, service.workspace_id, service.workspace_id)).fetchone()
        if not row:
            raise PermissionError("case outside current scope")
        result = _safe_record(row)
        result["runtime_waits"] = [_safe_record(wait) for wait in connection.execute(
            "SELECT id,revision,due_at,state,runtime_id,result,handoff_attempts FROM v4_runtime_waits WHERE case_id=%s ORDER BY created_at,id", (case_id,)).fetchall()]
        result["notifications"] = [_safe_record(notice) for notice in connection.execute("""
            SELECT n.id,n.kind,n.case_revision,d.state AS delivery_state,d.attempts,d.reason,
                   EXISTS(SELECT 1 FROM v4_case_acks a WHERE a.notification_id=n.id) AS acknowledged
            FROM v4_case_notifications n LEFT JOIN v4_notification_deliveries d ON d.notification_id=n.id
            WHERE n.case_id=%s ORDER BY n.created_at,n.id
        """, (case_id,)).fetchall()]
        return result


@router.get("/v4/cases/{case_id}")
async def inspect_case(case_id: UUID, request: Request):
    return await _invoke(_inspect_case, _service(request), case_id)


@router.post("/v4/cases/{case_id}/review")
async def respond_review(case_id: UUID, command: ReviewResponse, request: Request,
                         idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=256)):
    return await _invoke(_service(request).respond_review, case_id, command,
                         idempotency_key=idempotency_key)


@router.post("/v4/cases/{case_id}/resume")
async def resume_case(case_id: UUID, command: CaseRevisionCommand, request: Request):
    return await _invoke(_service(request).resume_case, case_id,
                         expected_revision=command.expected_revision,
                         idempotency_key=command.idempotency_key)


@router.post("/v4/cases/{case_id}/terminalize")
async def terminalize_case(case_id: UUID, command: CaseRevisionCommand, request: Request):
    return await _invoke(_service(request).terminalize_case, case_id,
                         expected_revision=command.expected_revision,
                         idempotency_key=command.idempotency_key)


@router.post("/v4/cases/{case_id}/archive")
async def archive_case(case_id: UUID, command: ArchiveCaseCommand, request: Request):
    return await _invoke(_service(request).archive_case, case_id,
                         reason=command.reason, evidence=command.evidence,
                         retain_until=command.retain_until)


@router.post("/v4/notifications/{notification_id}/ack")
async def ack_notification(notification_id: UUID, request: Request):
    return await _invoke(_service(request).ack_notification, notification_id)
