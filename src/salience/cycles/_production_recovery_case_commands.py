"""Typed commands for production-owned transitions of a canonical recovery case."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from salience.cycles._production_recovery_contracts import (
    RecoveryIdentityConflict,
    _SAFE_OPAQUE_REFERENCE,
    _require_aware,
    _require_sha256,
    _require_uuid,
)
from salience.observability.tracing import TraceContext


CaseAction = Literal["review_approve", "review_reject", "resume", "terminalize", "archive"]
_APPROVAL_ACTIONS = frozenset({"review_approve", "resume"})
_REASON_ACTIONS = frozenset({"review_approve", "review_reject", "archive"})
_CASE_ACTIONS = _REASON_ACTIONS | _APPROVAL_ACTIONS | {"terminalize"}
_APPLY = """
    SELECT public.v4_apply_production_recovery_case_command_v1(
        %s,%s,%s,%s,%s,%s,%s,%s,%s
    ) AS result
"""


@dataclass(frozen=True, slots=True)
class ProductionRecoveryCaseCommand:
    workspace_id: UUID
    work_id: UUID
    case_id: UUID
    actor_id: UUID
    expected_revision: int
    action: CaseAction
    idempotency_key: str
    approval_request_id: UUID | None = None
    reason: str | None = None
    evidence_sha256: str | None = None
    retain_until: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("workspace_id", "work_id", "case_id", "actor_id"):
            _require_uuid(getattr(self, name), name)
        if type(self.expected_revision) is not int or self.expected_revision < 1:
            raise ValueError("positive exact case revision required")
        if not isinstance(self.action, str) or self.action not in _CASE_ACTIONS:
            raise ValueError("canonical recovery case action required")
        if not isinstance(self.idempotency_key, str) or not _SAFE_OPAQUE_REFERENCE.fullmatch(self.idempotency_key):
            raise ValueError("bounded case command idempotency key required")
        if self.action in _APPROVAL_ACTIONS:
            _require_uuid(self.approval_request_id, "approval request ID")
        elif self.approval_request_id is not None:
            raise ValueError("approval is only applicable to review approval and resume")
        if self.action in _REASON_ACTIONS:
            if not isinstance(self.reason, str) or not self.reason.strip() or len(self.reason) > 2000:
                raise ValueError("bounded case command reason required")
        elif self.reason is not None:
            raise ValueError("reason is only applicable to review and archive")
        if self.action == "archive":
            _require_sha256(self.evidence_sha256, "archive evidence")
            object.__setattr__(self, "retain_until", _require_aware(self.retain_until, "archive retention"))
        elif self.evidence_sha256 is not None or self.retain_until is not None:
            raise ValueError("evidence and retention are only applicable to archive")


@dataclass(frozen=True, slots=True)
class ProductionRecoveryCaseAuthorization:
    """Trusted stage and route decisions for one exact command, with database fences."""

    command: ProductionRecoveryCaseCommand
    policy_decision_id: UUID
    policy_version_id: UUID
    policy_document_hash: str
    stage_decision_id: UUID
    route_decision_id: UUID
    route_id: UUID
    slot_id: UUID
    fencing_epoch: int
    workspace_stop_revision: int
    goal_stop_revision: int
    actor_revision: int
    expires_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.command, ProductionRecoveryCaseCommand):
            raise ValueError("exact authorized case command required")
        for name in ("policy_decision_id", "policy_version_id", "stage_decision_id",
                     "route_decision_id", "route_id", "slot_id"):
            _require_uuid(getattr(self, name), name)
        _require_sha256(self.policy_document_hash, "policy document hash")
        for name in ("fencing_epoch", "workspace_stop_revision", "goal_stop_revision", "actor_revision"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"positive {name} required")
        object.__setattr__(self, "expires_at", _require_aware(self.expires_at, "case authorization expiry"))


@dataclass(frozen=True, slots=True)
class ProductionRecoveryCaseReceipt:
    command_id: UUID
    case_id: UUID
    operation_id: UUID
    action: CaseAction
    source_revision: int
    resulting_revision: int
    state: str
    disposition: str | None
    archive_id: UUID | None


def execute_case_command(repository, authority, command: ProductionRecoveryCaseCommand) -> ProductionRecoveryCaseReceipt:
    if not isinstance(command, ProductionRecoveryCaseCommand):
        raise ValueError("typed production recovery case command required")
    request = {
        "approval_request_id": str(command.approval_request_id) if command.approval_request_id else None,
        "reason": command.reason,
        "evidence_sha256": command.evidence_sha256,
        "retain_until": command.retain_until.isoformat() if command.retain_until else None,
    }
    arguments = (
        command.workspace_id, command.work_id, command.case_id, command.actor_id,
        command.expected_revision, command.action, command.idempotency_key, Jsonb(request),
    )
    with repository._connect() as connection:
        try:
            # Release replay-check locks before consulting trusted external authority.
            with connection.transaction():
                result = connection.execute(_APPLY, arguments + (None,)).fetchone()["result"]
            if result is None:
                authorize = getattr(authority, "authorize_case_command", None)
                if not callable(authorize):
                    raise PermissionError("current production case command authority required")
                authorization = authorize(command)
                if not isinstance(authorization, ProductionRecoveryCaseAuthorization) or authorization.command != command:
                    raise PermissionError("production case authorization does not match the exact command")
                binding = {
                    "policy_decision_id": str(authorization.policy_decision_id),
                    "policy_version_id": str(authorization.policy_version_id),
                    "policy_document_hash": authorization.policy_document_hash,
                    "stage_decision_id": str(authorization.stage_decision_id),
                    "route_decision_id": str(authorization.route_decision_id),
                    "route_id": str(authorization.route_id),
                    "slot_id": str(authorization.slot_id),
                    "fencing_epoch": authorization.fencing_epoch,
                    "workspace_stop_revision": authorization.workspace_stop_revision,
                    "goal_stop_revision": authorization.goal_stop_revision,
                    "actor_revision": authorization.actor_revision,
                    "expires_at": authorization.expires_at.isoformat(),
                    "traceparent": TraceContext.new_root().to_carrier()["traceparent"],
                }
                with connection.transaction():
                    result = connection.execute(_APPLY, arguments + (Jsonb(binding),)).fetchone()["result"]
        except psycopg.errors.InsufficientPrivilege as error:
            raise PermissionError(error.diag.message_primary) from error
        except psycopg.errors.InvalidParameterValue as error:
            raise RecoveryIdentityConflict(error.diag.message_primary) from error
    return ProductionRecoveryCaseReceipt(
        command_id=UUID(result["command_id"]),
        case_id=UUID(result["case_id"]),
        operation_id=UUID(result["operation_id"]),
        action=result["action"],
        source_revision=result["source_revision"],
        resulting_revision=result["resulting_revision"],
        state=result["state"],
        disposition=result["disposition"],
        archive_id=UUID(result["archive_id"]) if result["archive_id"] else None,
    )
