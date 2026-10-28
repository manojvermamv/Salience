"""Provider-neutral typed contracts for production recovery."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import re
from typing import Protocol
from uuid import UUID

from salience.cycles.production_dispatch import ProductionRecoveryCommandReference


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_CODE = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z")
_SAFE_PROVIDER_STATE = re.compile(r"[a-z][a-z0-9_.-]{0,127}\Z")
_SAFE_OPAQUE_REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}\Z")
_EVENT_KINDS = frozenset(
    {
        "remote_read_observation",
        "alert_intent",
        "delivery_attempt",
        "delivery_receipt",
        "acknowledgment",
        "retry_scheduled",
        "escalation",
        "owner_assignment",
        "owner_unavailable",
        "stop_revocation_fence",
        "held_conflict",
        "same_operation_no_effect_reissue",
    }
)


class RecoveryInventoryGap(LookupError):
    """A canonical source lacks the exact Task 6 production enrollment."""


class RecoveryIdentityConflict(ValueError):
    """An existing work identity conflicts with the requested exact source."""


class RecoveryProviderReadDenied(PermissionError):
    """The source is not eligible for an exact original-operation remote read."""


class RecoveryDeadlineNotDue(ValueError):
    """The exact canonical deadline has not become due."""


class RecoveryEventConflict(ValueError):
    """A stable event key was reused with different evidence."""


def _require_uuid(value: object, name: str) -> UUID:
    if not isinstance(value, UUID):
        raise ValueError(f"{name} must be a UUID")
    return value


def _require_sha256(value: object, name: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _require_aware(value: object, name: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _safe_code(value: object, name: str) -> str:
    if not isinstance(value, str) or not _SAFE_CODE.fullmatch(value):
        raise ValueError(f"{name} must be a bounded safe code")
    return value


def _scalar_payload(payload: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(payload, Mapping):
        raise ValueError("recovery event payload must be a mapping")
    result = dict(payload)
    if any(isinstance(value, (dict, list, tuple, set)) for value in result.values()):
        raise ValueError("recovery event payload values must be JSON scalars")
    return result


@dataclass(frozen=True, slots=True)
class ProductionRecoveryPolicyBinding:
    """Explicit immutable profile and retry-policy identity for first binding."""

    policy_decision_id: UUID
    recipient_profile_id: UUID
    recipient_profile_revision: int
    recipient_profile_sha256: str
    retry_policy_id: UUID
    retry_policy_revision: int
    retry_policy_sha256: str

    def __post_init__(self) -> None:
        for name in ("policy_decision_id", "recipient_profile_id", "retry_policy_id"):
            _require_uuid(getattr(self, name), name)
        if type(self.recipient_profile_revision) is not int or self.recipient_profile_revision < 1:
            raise ValueError("positive recipient profile revision required")
        if type(self.retry_policy_revision) is not int or self.retry_policy_revision < 1:
            raise ValueError("positive retry policy revision required")
        _require_sha256(self.recipient_profile_sha256, "recipient profile SHA-256")
        _require_sha256(self.retry_policy_sha256, "retry policy SHA-256")


@dataclass(frozen=True, slots=True)
class ProductionRecoveryWork:
    work_id: UUID
    source_discriminant: str
    source_id: UUID
    workspace_id: UUID
    goal_id: UUID
    intent_id: UUID
    cycle_id: UUID | None
    context_id: UUID | None
    operation_id: UUID | None
    source_revision: int
    case_revision: int | None
    eligibility_revision: int
    owner_id: UUID
    target_alias: str | None
    remote_account_id: str | None
    remote_resource_id: str | None
    reason: str
    state: str
    request_fingerprint: str
    source_fingerprint: str
    snapshot_version: str | None
    snapshot: Mapping[str, object] | None
    snapshot_sha256: str | None
    deadline: datetime
    due_at: datetime | None
    expires_at: datetime | None
    deadline_revision: int
    recipient_profile_revision: int

    @property
    def case_id(self) -> UUID | None:
        return self.source_id if self.source_discriminant == "cycle_case" else None


@dataclass(frozen=True, slots=True)
class ProductionScopedReadGrant:
    grant_id: UUID
    provider_id: str
    account_id: str
    resource_id: str | None
    operation_id: UUID
    idempotency_key: str
    request_fingerprint: str
    scope: str
    policy_decision_id: UUID
    policy_revision: int
    stop_revision: int
    route_decision_id: UUID
    expires_at: datetime

    def __post_init__(self) -> None:
        for name in ("grant_id", "operation_id", "policy_decision_id", "route_decision_id"):
            _require_uuid(getattr(self, name), name)
        _require_sha256(self.request_fingerprint, "request fingerprint")
        if self.scope != "original_operation_read":
            raise ValueError("original-operation read scope required")
        if not self.provider_id or not self.account_id or not self.idempotency_key:
            raise ValueError("exact provider, account, and operation key required")
        if self.resource_id is not None and not isinstance(self.resource_id, str):
            raise ValueError("remote resource identity must be text or NULL")
        if type(self.policy_revision) is not int or self.policy_revision < 1:
            raise ValueError("positive policy revision required")
        if type(self.stop_revision) is not int or self.stop_revision < 1:
            raise ValueError("positive stop revision required")
        object.__setattr__(self, "expires_at", _require_aware(self.expires_at, "grant expiry"))


@dataclass(frozen=True, slots=True)
class ProductionRemoteReadRequest:
    operation_id: UUID
    idempotency_key: str
    account_id: str
    target_id: str
    resource_id: str | None
    request_fingerprint: str
    read_grant: ProductionScopedReadGrant

    def __post_init__(self) -> None:
        _require_uuid(self.operation_id, "operation ID")
        _require_sha256(self.request_fingerprint, "request fingerprint")
        if not self.idempotency_key or not self.account_id or not self.target_id:
            raise ValueError("exact original operation read identity is required")
        if self.resource_id is not None and not isinstance(self.resource_id, str):
            raise ValueError("resource ID must be text or NULL")
        grant = self.read_grant
        if (
            grant.operation_id != self.operation_id
            or grant.idempotency_key != self.idempotency_key
            or grant.account_id != self.account_id
            or grant.provider_id != self.target_id
            or grant.resource_id != self.resource_id
            or grant.request_fingerprint != self.request_fingerprint
        ):
            raise ValueError("read grant does not exactly match the original operation")


class ProductionRemoteReadTarget(Protocol):
    def read_operation(
        self, request: ProductionRemoteReadRequest
    ) -> ProductionRemoteObservation: ...


@dataclass(frozen=True, slots=True)
class ProductionRemoteObservation:
    provider_id: str
    account_id: str
    operation_id: UUID
    idempotency_key: str
    resource_id: str | None
    request_fingerprint: str
    result: str
    remote_state: str
    response_sha256: str
    observed_at: datetime

    def __post_init__(self) -> None:
        _require_uuid(self.operation_id, "observed operation ID")
        _require_sha256(self.request_fingerprint, "observed request fingerprint")
        _require_sha256(self.response_sha256, "provider response SHA-256")
        _require_aware(self.observed_at, "provider observation time")
        if not self.provider_id or not self.account_id or not self.idempotency_key:
            raise ValueError("exact remote observation identity required")
        if self.resource_id is not None and not isinstance(self.resource_id, str):
            raise ValueError("observed resource ID must be text or NULL")
        if self.result not in {"in_progress", "succeeded", "not_found", "unknown"}:
            raise ValueError("unsupported provider result classification")
        if not isinstance(self.remote_state, str) or not _SAFE_PROVIDER_STATE.fullmatch(
            self.remote_state
        ):
            raise ValueError("bounded normalized safe remote state required")


@dataclass(frozen=True, slots=True)
class ProductionProviderReadContract:
    provider_id: str
    exact_idempotency_lookup: bool
    authoritative_visibility_window: timedelta | None

    def __post_init__(self) -> None:
        if not isinstance(self.provider_id, str) or not self.provider_id:
            raise ValueError("explicit provider identity required")
        if type(self.exact_idempotency_lookup) is not bool:
            raise ValueError("explicit exact-idempotency-lookup capability required")
        if self.authoritative_visibility_window is not None and (
            not isinstance(self.authoritative_visibility_window, timedelta)
            or self.authoritative_visibility_window < timedelta(0)
        ):
            raise ValueError("visibility window must be a nonnegative duration or NULL")


@dataclass(frozen=True, slots=True)
class ProductionRecoveryReadContext:
    idempotency_key: str
    operation_started_at: datetime
    provider_contract: ProductionProviderReadContract

    def __post_init__(self) -> None:
        if not isinstance(self.idempotency_key, str) or not self.idempotency_key:
            raise ValueError("original operation idempotency key required")
        object.__setattr__(
            self,
            "operation_started_at",
            _require_aware(self.operation_started_at, "operation claim time"),
        )


@dataclass(frozen=True, slots=True)
class ProductionRecoveryDecision:
    allowed: bool
    decision_id: UUID
    route_decision_id: UUID
    policy_revision: int
    stop_revision: int

    def __post_init__(self) -> None:
        if type(self.allowed) is not bool:
            raise ValueError("explicit recovery decision required")
        _require_uuid(self.decision_id, "decision ID")
        _require_uuid(self.route_decision_id, "route decision ID")
        if type(self.policy_revision) is not int or self.policy_revision < 1:
            raise ValueError("positive policy revision required")
        if type(self.stop_revision) is not int or self.stop_revision < 1:
            raise ValueError("positive stop revision required")


@dataclass(frozen=True, slots=True)
class ProductionNoEffectAuthorization:
    stage_decision_id: UUID
    policy_decision_id: UUID
    workspace_stop_revision: int
    goal_stop_revision: int
    route_decision_id: UUID

    def __post_init__(self) -> None:
        for name in ("stage_decision_id", "policy_decision_id", "route_decision_id"):
            _require_uuid(getattr(self, name), name)
        for name in ("workspace_stop_revision", "goal_stop_revision"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"positive {name} required")


class ProductionRecoveryAuthority(Protocol):
    def authorize_remote_read(
        self, work: ProductionRecoveryWork, context: ProductionRecoveryReadContext
    ) -> ProductionScopedReadGrant: ...

    def recheck_remote_read(
        self, work: ProductionRecoveryWork, grant: ProductionScopedReadGrant
    ) -> None: ...

    def authorize_alert(
        self, work: ProductionRecoveryWork, request: ProductionAlertRequest
    ) -> ProductionRecoveryDecision: ...

    def authorize_acknowledgment(
        self, work: ProductionRecoveryWork, command: ProductionAlertAcknowledgment
    ) -> ProductionRecoveryDecision: ...

    def authorize_deadline_wake(
        self,
        work: ProductionRecoveryWork,
        source: Mapping[str, object],
        action_kind: str,
        idempotency_key: str,
    ) -> ProductionRecoveryDecision: ...

    def authorize_no_effect_reissue(
        self,
        work: ProductionRecoveryWork,
        command: NoEffectGenerationReissueCommand,
        observation: Mapping[str, object],
    ) -> ProductionNoEffectAuthorization: ...


@dataclass(frozen=True, slots=True)
class ProductionRecoveryReadResult:
    event_id: UUID
    payload_sha256: str
    result_class: str
    remote_state: str | None
    operation_id: UUID
    idempotency_key: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ProductionRecipientProfile:
    profile_id: UUID
    revision: int
    profile_sha256: str
    owner_subject_id: UUID
    on_call_group_ids: tuple[UUID, ...]

    def __post_init__(self) -> None:
        _require_uuid(self.profile_id, "recipient profile ID")
        _require_uuid(self.owner_subject_id, "recipient owner ID")
        _require_sha256(self.profile_sha256, "recipient profile SHA-256")
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError("positive recipient profile revision required")
        if not isinstance(self.on_call_group_ids, tuple) or any(
            not isinstance(value, UUID) for value in self.on_call_group_ids
        ) or len(set(self.on_call_group_ids)) != len(self.on_call_group_ids):
            raise ValueError("explicit unique on-call group IDs required")


@dataclass(frozen=True, slots=True)
class ProductionAlertRetryPolicy:
    policy_id: UUID
    revision: int
    policy_sha256: str
    maximum_attempts: int
    retry_intervals: tuple[timedelta, ...]
    claim_ttl: timedelta
    owner_response_window: timedelta
    escalation_after: tuple[timedelta, ...]

    def __post_init__(self) -> None:
        _require_uuid(self.policy_id, "retry policy ID")
        _require_sha256(self.policy_sha256, "retry policy SHA-256")
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError("positive retry policy revision required")
        if type(self.maximum_attempts) is not int or not 1 <= self.maximum_attempts <= 20:
            raise ValueError("retry policy must explicitly bound attempts")
        if not isinstance(self.retry_intervals, tuple) or len(self.retry_intervals) < self.maximum_attempts - 1:
            raise ValueError("retry policy must provide every retry interval")
        if any(not isinstance(delay, timedelta) or delay <= timedelta(0) for delay in self.retry_intervals):
            raise ValueError("retry intervals must be positive durations")
        for name in ("claim_ttl", "owner_response_window"):
            value = getattr(self, name)
            if not isinstance(value, timedelta) or value <= timedelta(0):
                raise ValueError(f"{name} must be a positive policy duration")
        if not isinstance(self.escalation_after, tuple) or any(
            not isinstance(value, timedelta) or value <= timedelta(0)
            for value in self.escalation_after
        ):
            raise ValueError("escalation times must be explicit positive durations")


@dataclass(frozen=True, slots=True)
class ProductionAlertRequest:
    alert_event_id: UUID
    work_id: UUID
    workspace_id: UUID
    source_id: UUID
    operation_id: UUID | None
    case_id: UUID | None
    reason: str
    state: str
    deadline: datetime
    target_alias: str | None
    severity: str
    escalation_level: int
    recipient_tier: str
    recipient_profile: ProductionRecipientProfile
    retry_policy: ProductionAlertRetryPolicy
    policy_decision_id: UUID

    def __post_init__(self) -> None:
        for name in ("alert_event_id", "work_id", "workspace_id", "source_id", "policy_decision_id"):
            _require_uuid(getattr(self, name), name)
        if self.operation_id is not None:
            _require_uuid(self.operation_id, "operation ID")
        if self.case_id is not None:
            _require_uuid(self.case_id, "case ID")
        if not isinstance(self.reason, str) or not self.reason or len(self.reason) > 128:
            raise ValueError("bounded typed recovery reason required")
        if not isinstance(self.state, str) or not self.state or len(self.state) > 64:
            raise ValueError("bounded recovery state required")
        object.__setattr__(self, "deadline", _require_aware(self.deadline, "alert deadline"))
        if self.target_alias is not None and (not isinstance(self.target_alias, str) or len(self.target_alias) > 256):
            raise ValueError("bounded target alias required")
        _safe_code(self.severity, "alert severity")
        if self.recipient_tier not in {"owner", "on_call"}:
            raise ValueError("explicit owner or on-call recipient tier required")
        if type(self.escalation_level) is not int or self.escalation_level < 0:
            raise ValueError("nonnegative explicit escalation level required")

    @property
    def idempotency_key(self) -> str:
        return f"p1-recovery-alert:{self.alert_event_id}"


@dataclass(frozen=True, slots=True)
class ProductionAlertReceipt:
    alert_event_id: UUID
    idempotency_key: str
    provider_message_id: str | None
    status: str
    accepted_at: datetime | None

    def __post_init__(self) -> None:
        _require_uuid(self.alert_event_id, "alert event ID")
        if not isinstance(self.idempotency_key, str) or not _SAFE_OPAQUE_REFERENCE.fullmatch(
            self.idempotency_key
        ):
            raise ValueError("stable alert idempotency key required")
        if self.provider_message_id is not None and (
            not isinstance(self.provider_message_id, str)
            or not _SAFE_OPAQUE_REFERENCE.fullmatch(self.provider_message_id)
        ):
            raise ValueError("safe bounded opaque provider message ID required")
        if self.status not in {"accepted", "held", "uncertain"}:
            raise ValueError("known alert receipt status required")
        if self.accepted_at is not None:
            object.__setattr__(self, "accepted_at", _require_aware(self.accepted_at, "alert receipt time"))
        if self.status == "accepted" and (self.provider_message_id is None or self.accepted_at is None):
            raise ValueError("accepted alert requires provider receipt evidence")


class ProductionAlertTarget(Protocol):
    def send(
        self, request: ProductionAlertRequest, idempotency_key: str
    ) -> ProductionAlertReceipt: ...

    def lookup(self, idempotency_key: str) -> ProductionAlertReceipt | None: ...


@dataclass(frozen=True, slots=True)
class ProductionAlertAcknowledgment:
    work_id: UUID
    alert_event_id: UUID
    source_id: UUID
    delivery_id: str
    actor_id: UUID
    idempotency_key: str
    policy_decision_id: UUID

    def __post_init__(self) -> None:
        for name in ("work_id", "alert_event_id", "source_id", "actor_id", "policy_decision_id"):
            _require_uuid(getattr(self, name), name)
        if not isinstance(self.delivery_id, str) or not _SAFE_OPAQUE_REFERENCE.fullmatch(
            self.delivery_id
        ):
            raise ValueError("exact safe bounded provider delivery ID required")
        if not isinstance(self.idempotency_key, str) or not _SAFE_OPAQUE_REFERENCE.fullmatch(
            self.idempotency_key
        ):
            raise ValueError("stable acknowledgment idempotency key required")


@dataclass(frozen=True, slots=True)
class ProductionRecoveryEvent:
    event_id: UUID
    work_id: UUID
    event_key: str
    event_kind: str
    payload: Mapping[str, object]
    payload_sha256: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class TypedCaseCommandReceipt:
    case_id: UUID
    action: str
    resulting_revision: int
    command_id: UUID


@dataclass(frozen=True, slots=True)
class TypedIntentWakeReceipt:
    intent_id: UUID
    eligibility_revision: int
    disposition: str
    cycle_id: UUID | None


@dataclass(frozen=True, slots=True)
class ProductionDeadlineWakeReceipt:
    event_id: UUID
    command_result: TypedCaseCommandReceipt | TypedIntentWakeReceipt


@dataclass(frozen=True, slots=True)
class NoEffectGenerationReissueCommand:
    work_id: UUID
    case_id: UUID
    expected_case_revision: int
    observation_event_id: UUID
    observation_sha256: str
    operation_id: UUID
    request_fingerprint: str
    original_idempotency_key: str
    stage_decision_id: UUID
    policy_decision_id: UUID
    workspace_stop_revision: int
    goal_stop_revision: int
    route_decision_id: UUID
    idempotency_key: str

    def __post_init__(self) -> None:
        for name in (
            "work_id", "case_id", "observation_event_id", "operation_id",
            "stage_decision_id", "policy_decision_id", "route_decision_id",
        ):
            _require_uuid(getattr(self, name), name)
        if type(self.expected_case_revision) is not int or self.expected_case_revision < 1:
            raise ValueError("positive expected case revision required")
        for name in ("workspace_stop_revision", "goal_stop_revision"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"positive {name} required")
        _require_sha256(self.observation_sha256, "observation payload SHA-256")
        _require_sha256(self.request_fingerprint, "request fingerprint")
        for name in ("original_idempotency_key", "idempotency_key"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or len(value) > 256:
                raise ValueError(f"bounded {name} required")


@dataclass(frozen=True, slots=True)
class ProductionNoEffectReissueReceipt:
    event_id: UUID
    work_id: UUID
    operation_id: UUID
    idempotency_key: str
    case_revision: int
    payload_sha256: str
    command_reference: ProductionRecoveryCommandReference


@dataclass(frozen=True, slots=True)
class ProductionCaseDeadlineCommand:
    case_id: UUID
    expected_revision: int
    operation_id: UUID
    context_id: UUID
    deadline: datetime
    idempotency_key: str
    policy_decision_id: UUID
    stop_revision: int
    route_decision_id: UUID


@dataclass(frozen=True, slots=True)
class ProductionIntentDeadlineCommand:
    intent_id: UUID
    expected_revision: int
    due_at: datetime
    expires_at: datetime
    idempotency_key: str
    policy_decision_id: UUID
    stop_revision: int
    route_decision_id: UUID
