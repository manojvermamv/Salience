"""Open a canonical production reconciliation case for a claimed publication."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from salience.cycles._production_recovery_contracts import (
    ProductionRecoveryPolicyBinding,
    RecoveryIdentityConflict,
    _SAFE_OPAQUE_REFERENCE,
    _require_aware,
    _require_sha256,
    _require_uuid,
)
from salience.cycles._production_recovery_work import ProductionRecoveryWorkRepository
from salience.observability.tracing import TraceContext


_OPEN = "SELECT public.v4_open_production_recovery_case_v1(%s,%s) AS result"


@dataclass(frozen=True, slots=True)
class ProductionRecoveryCaseOpenCommand:
    """The exact operation and production dispatch generation under recovery."""

    workspace_id: UUID
    actor_id: UUID
    intent_id: UUID
    cycle_id: UUID
    context_id: UUID
    operation_id: UUID
    eligibility_revision: int
    generation: int
    request_fingerprint: str
    deadline: datetime
    idempotency_key: str

    def __post_init__(self) -> None:
        for name in ("workspace_id", "actor_id", "intent_id", "cycle_id", "context_id", "operation_id"):
            _require_uuid(getattr(self, name), name)
        for name in ("eligibility_revision", "generation"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"positive exact {name} required")
        _require_sha256(self.request_fingerprint, "original request fingerprint")
        object.__setattr__(self, "deadline", _require_aware(self.deadline, "case deadline"))
        if not isinstance(self.idempotency_key, str) or not _SAFE_OPAQUE_REFERENCE.fullmatch(self.idempotency_key):
            raise ValueError("bounded opening command idempotency key required")


@dataclass(frozen=True, slots=True)
class ProductionRecoveryCaseOpenAuthorization:
    """Current independent recovery decisions for one exact case opening."""

    command: ProductionRecoveryCaseOpenCommand
    alert_policy: ProductionRecoveryPolicyBinding
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
        if not isinstance(self.command, ProductionRecoveryCaseOpenCommand):
            raise ValueError("exact authorized case opening command required")
        if not isinstance(self.alert_policy, ProductionRecoveryPolicyBinding):
            raise ValueError("authorized recovery alert policy required")
        for name in ("policy_version_id", "stage_decision_id", "route_decision_id", "route_id", "slot_id"):
            _require_uuid(getattr(self, name), name)
        _require_sha256(self.policy_document_hash, "policy document hash")
        for name in ("fencing_epoch", "workspace_stop_revision", "goal_stop_revision", "actor_revision"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"positive {name} required")
        object.__setattr__(self, "expires_at", _require_aware(self.expires_at, "opening authorization expiry"))


@dataclass(frozen=True, slots=True)
class ProductionRecoveryCaseOpenReceipt:
    command_id: UUID
    case_id: UUID
    work_id: UUID
    alert_event_id: UUID
    operation_id: UUID
    resulting_revision: int
    state: str


def open_case(
    repository: ProductionRecoveryWorkRepository, authority: object, command: ProductionRecoveryCaseOpenCommand,
) -> ProductionRecoveryCaseOpenReceipt:
    if not isinstance(command, ProductionRecoveryCaseOpenCommand):
        raise ValueError("typed production recovery case opening required")
    request = Jsonb({
        "workspace_id": str(command.workspace_id),
        "actor_id": str(command.actor_id),
        "intent_id": str(command.intent_id),
        "cycle_id": str(command.cycle_id),
        "context_id": str(command.context_id),
        "operation_id": str(command.operation_id),
        "eligibility_revision": command.eligibility_revision,
        "generation": command.generation,
        "request_fingerprint": command.request_fingerprint,
        "deadline": command.deadline.isoformat(),
        "idempotency_key": command.idempotency_key,
    })
    with repository._connect() as connection:
        try:
            with connection.transaction():
                result = connection.execute(_OPEN, (request, None)).fetchone()["result"]
            if result is None:
                authorize = getattr(authority, "authorize_case_opening", None)
                if not callable(authorize):
                    raise PermissionError("current production case opening authority required")
                decision = authorize(command)
                if not isinstance(decision, ProductionRecoveryCaseOpenAuthorization) or decision.command != command:
                    raise PermissionError("production case opening authorization does not match the exact command")
                alert = decision.alert_policy
                binding = Jsonb({
                    "policy_decision_id": str(alert.policy_decision_id),
                    "policy_version_id": str(decision.policy_version_id),
                    "policy_document_hash": decision.policy_document_hash,
                    "stage_decision_id": str(decision.stage_decision_id),
                    "route_decision_id": str(decision.route_decision_id),
                    "route_id": str(decision.route_id),
                    "slot_id": str(decision.slot_id),
                    "fencing_epoch": decision.fencing_epoch,
                    "workspace_stop_revision": decision.workspace_stop_revision,
                    "goal_stop_revision": decision.goal_stop_revision,
                    "actor_revision": decision.actor_revision,
                    "expires_at": decision.expires_at.isoformat(),
                    "traceparent": TraceContext.new_root().to_carrier()["traceparent"],
                    "recipient_profile_revision": alert.recipient_profile_revision,
                    "retry_policy_revision": alert.retry_policy_revision,
                    "alert_policy_sha256": repository._policy_evidence(alert),
                })
                with connection.transaction():
                    result = connection.execute(_OPEN, (request, binding)).fetchone()["result"]
        except psycopg.errors.InsufficientPrivilege as error:
            raise PermissionError(error.diag.message_primary) from error
        except psycopg.errors.InvalidParameterValue as error:
            raise RecoveryIdentityConflict(error.diag.message_primary) from error
    return ProductionRecoveryCaseOpenReceipt(
        command_id=UUID(result["command_id"]),
        case_id=UUID(result["case_id"]),
        work_id=UUID(result["work_id"]),
        alert_event_id=UUID(result["alert_event_id"]),
        operation_id=UUID(result["operation_id"]),
        resulting_revision=result["resulting_revision"],
        state=result["state"],
    )
