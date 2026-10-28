"""Typed contracts shared by the injected production schedule boundary."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from types import MappingProxyType
from typing import Protocol, runtime_checkable
from uuid import UUID

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_OUTCOME_CLASS = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z")
_MEMBER_SCHEMA = "salience.p1-production-schedule-inventory-member.v1"
_INVENTORY_SCHEMA = "salience.p1-production-schedule-inventory.v1"
_APPROVAL_SCHEMA = "salience.p1-production-schedule-binding-approval.v1"
_MEMBER_KEYS = frozenset(
    {
        "schema_version",
        "target_alias",
        "namespace",
        "remote_schedule_id",
        "classification",
        "source_kind",
        "schedule_id",
        "publication_schedule_id",
        "workspace_id",
        "goal_id",
        "goal_revision",
        "source_actor_id",
        "source_owner_subject_id",
        "schedule_operator_subject_id",
        "owner_ref",
        "exclusion_reason",
        "workflow_type",
        "action_id",
        "argument_sha256",
        "task_queue",
        "route_id",
        "route_tuple_sha256",
        "slot_id",
        "owner_id",
        "fencing_epoch",
        "route_manifest_sha256",
        "artifact_lock_sha256",
        "cadence_policy_revision",
        "interval_seconds",
        "first_v4_slot",
        "observed_last_slot",
        "remote_state",
        "next_action",
        "running_action_count",
        "history_page_count",
        "history_action_count",
        "history_page_chain_sha256",
        "history_action_set_sha256",
        "history_watermark",
        "history_watermark_sha256",
    }
)
_APPROVAL_FIELDS = (
    "schema_version",
    "target_alias",
    "namespace",
    "inventory_id",
    "inventory_sha256",
    "source_kind",
    "schedule_id",
    "publication_schedule_id",
    "workspace_id",
    "goal_id",
    "goal_revision",
    "source_actor_id",
    "source_owner_subject_id",
    "schedule_operator_subject_id",
    "remote_schedule_id",
    "task_queue",
    "workflow_type",
    "action_id",
    "argument_sha256",
    "route_id",
    "route_tuple_sha256",
    "slot_id",
    "owner_id",
    "fencing_epoch",
    "route_manifest_sha256",
    "artifact_lock_sha256",
    "cadence_policy_revision",
    "first_v4_slot",
    "history_watermark_sha256",
    "policy_version_id",
    "policy_document_hash",
    "expires_at",
)

def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{label} must be lowercase SHA-256")
    return value


def _aware(value: object, label: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _time_text(value: datetime | None, label: str) -> str | None:
    if value is None:
        return None
    return _aware(value, label).isoformat()


def _local_evidence_sha256(value: object) -> str:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError("sanitized schedule evidence must be canonical JSON") from error
    return sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class TemporalScheduleAction:
    """Sanitized, payload-free action history returned by the target."""

    action_id_sha256: str
    scheduled_slot: datetime
    started_at: datetime | None
    completed_at: datetime | None
    outcome_class: str
    workflow_id_sha256: str

    def __post_init__(self) -> None:
        _digest(self.action_id_sha256, "action identity digest")
        _digest(self.workflow_id_sha256, "workflow identity digest")
        _aware(self.scheduled_slot, "scheduled slot")
        if self.started_at is not None:
            _aware(self.started_at, "action start")
        if self.completed_at is not None:
            _aware(self.completed_at, "action completion")
        if not isinstance(self.outcome_class, str) or not _OUTCOME_CLASS.fullmatch(
            self.outcome_class
        ):
            raise ValueError("bounded sanitized action outcome required")

    def payload(self) -> dict[str, str | None]:
        return {
            "action_id_sha256": self.action_id_sha256,
            "scheduled_slot": _time_text(self.scheduled_slot, "scheduled slot"),
            "started_at": _time_text(self.started_at, "action start"),
            "completed_at": _time_text(self.completed_at, "action completion"),
            "outcome_class": self.outcome_class,
            "workflow_id_sha256": self.workflow_id_sha256,
        }


@dataclass(frozen=True, slots=True)
class TemporalScheduleDescriptor:
    """Typed inventory fact for one remote ID; never contains workflow payload."""

    remote_schedule_id: str
    classification: str
    owner_ref: str
    exclusion_reason: str | None
    workflow_type: str
    action_id: str
    argument_sha256: str
    task_queue: str
    route_id: UUID
    route_tuple_sha256: str
    slot_id: UUID
    owner_id: UUID
    fencing_epoch: int
    route_manifest_sha256: str
    artifact_lock_sha256: str
    cadence_policy_revision: str
    interval_seconds: int
    first_v4_slot: datetime
    remote_state: str
    next_action: datetime | None
    running_action_count: int
    source_kind: str | None = None
    schedule_id: UUID | None = None
    publication_schedule_id: UUID | None = None
    workspace_id: UUID | None = None
    goal_id: UUID | None = None
    goal_revision: int | None = None
    source_actor_id: UUID | None = None
    source_owner_subject_id: UUID | None = None
    schedule_operator_subject_id: UUID | None = None

    def __post_init__(self) -> None:
        _text(self.remote_schedule_id, "remote schedule ID")
        if self.classification not in {"bound", "out_of_scope"}:
            raise ValueError("schedule inventory classification must be explicit")
        _text(self.owner_ref, "schedule owner reference")
        if self.classification == "bound":
            if self.exclusion_reason is not None:
                raise ValueError("bound schedule cannot have an exclusion reason")
            if self.source_kind not in {"intelligence_research", "governed_publication"}:
                raise ValueError("bound schedule source kind is unsupported")
            required_ids = (
                self.schedule_id,
                self.workspace_id,
                self.goal_id,
                self.source_actor_id,
                self.source_owner_subject_id,
                self.schedule_operator_subject_id,
            )
            if any(not isinstance(value, UUID) for value in required_ids):
                raise ValueError("bound schedule canonical source UUIDs are required")
            if type(self.goal_revision) is not int or self.goal_revision < 1:
                raise ValueError("bound schedule goal revision must be positive")
            if self.source_actor_id == self.source_owner_subject_id:
                raise ValueError("source actor and named source owner must differ")
            if self.source_kind == "intelligence_research" and self.publication_schedule_id is not None:
                raise ValueError("intelligence schedule cannot bind publication identity")
            if self.source_kind == "governed_publication" and not isinstance(
                self.publication_schedule_id, UUID
            ):
                raise ValueError("publication schedule identity is required")
        else:
            if not isinstance(self.exclusion_reason, str) or not self.exclusion_reason.strip():
                raise ValueError("out-of-scope schedule requires a non-empty exclusion reason")
            if any(
                value is not None
                for value in (
                    self.source_kind,
                    self.schedule_id,
                    self.publication_schedule_id,
                    self.workspace_id,
                    self.goal_id,
                    self.goal_revision,
                    self.source_actor_id,
                    self.source_owner_subject_id,
                    self.schedule_operator_subject_id,
                )
            ):
                raise ValueError("out-of-scope schedule cannot carry canonical source IDs")
        for name, value in (
            ("route ID", self.route_id),
            ("route slot ID", self.slot_id),
            ("route owner ID", self.owner_id),
        ):
            if not isinstance(value, UUID):
                raise ValueError(f"{name} UUID required")
        for name, value in (
            ("route tuple digest", self.route_tuple_sha256),
            ("argument digest", self.argument_sha256),
            ("route manifest digest", self.route_manifest_sha256),
            ("artifact lock digest", self.artifact_lock_sha256),
        ):
            _digest(value, name)
        _text(self.workflow_type, "workflow type")
        _text(self.action_id, "schedule action ID")
        _text(self.task_queue, "schedule task queue")
        _text(self.cadence_policy_revision, "cadence policy revision")
        _text(self.remote_state, "remote schedule state")
        if type(self.fencing_epoch) is not int or self.fencing_epoch < 1:
            raise ValueError("positive route fencing epoch required")
        if type(self.interval_seconds) is not int or not 1 <= self.interval_seconds <= 31_536_000:
            raise ValueError("supported positive interval required")
        _aware(self.first_v4_slot, "first V4 slot")
        if self.next_action is not None:
            _aware(self.next_action, "next action")
        if type(self.running_action_count) is not int or self.running_action_count < 0:
            raise ValueError("non-negative running action count required")


@dataclass(frozen=True, slots=True)
class TemporalScheduleInventoryPage:
    items: tuple[TemporalScheduleDescriptor, ...]
    next_page_token: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.items, tuple) or any(
            not isinstance(item, TemporalScheduleDescriptor) for item in self.items
        ):
            raise ValueError("typed schedule inventory page items required")
        if self.next_page_token is not None:
            _text(self.next_page_token, "next inventory page token")


@dataclass(frozen=True, slots=True)
class TemporalScheduleHistoryPage:
    items: tuple[TemporalScheduleAction, ...]
    next_page_token: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.items, tuple) or any(
            not isinstance(item, TemporalScheduleAction) for item in self.items
        ):
            raise ValueError("typed schedule history page items required")
        if self.next_page_token is not None:
            _text(self.next_page_token, "next history page token")


@dataclass(frozen=True, slots=True)
class TemporalScheduleReadback:
    remote_schedule_id: str
    target_alias: str
    namespace: str
    workflow_type: str
    action_id: str
    argument_sha256: str
    task_queue: str
    route_id: UUID
    route_tuple_sha256: str
    slot_id: UUID
    owner_id: UUID
    fencing_epoch: int
    route_manifest_sha256: str
    artifact_lock_sha256: str
    cadence_policy_revision: str
    interval_seconds: int
    first_v4_slot: datetime
    paused: bool
    next_action: datetime | None
    last_slot: datetime | None
    resume_after: datetime | None
    running_action_count: int

    def __post_init__(self) -> None:
        _text(self.remote_schedule_id, "remote schedule ID")
        _text(self.target_alias, "Temporal target alias")
        _text(self.namespace, "Temporal namespace")
        for name, value in (
            ("workflow type", self.workflow_type),
            ("schedule action ID", self.action_id),
            ("task queue", self.task_queue),
            ("cadence policy revision", self.cadence_policy_revision),
        ):
            _text(value, name)
        for name, value in (
            ("argument digest", self.argument_sha256),
            ("route tuple digest", self.route_tuple_sha256),
            ("route manifest digest", self.route_manifest_sha256),
            ("artifact lock digest", self.artifact_lock_sha256),
        ):
            _digest(value, name)
        for name, value in (
            ("route ID", self.route_id),
            ("route slot ID", self.slot_id),
            ("route owner ID", self.owner_id),
        ):
            if not isinstance(value, UUID):
                raise ValueError(f"{name} UUID required")
        if type(self.fencing_epoch) is not int or self.fencing_epoch < 1:
            raise ValueError("positive route fencing epoch required")
        if type(self.interval_seconds) is not int or not 1 <= self.interval_seconds <= 31_536_000:
            raise ValueError("supported positive interval required")
        if type(self.paused) is not bool:
            raise ValueError("remote pause state must be explicit")
        _aware(self.first_v4_slot, "first V4 slot")
        for value, label in (
            (self.next_action, "next action"),
            (self.last_slot, "last action slot"),
            (self.resume_after, "resume watermark"),
        ):
            if value is not None:
                _aware(value, label)
        if type(self.running_action_count) is not int or self.running_action_count < 0:
            raise ValueError("non-negative running action count required")


@runtime_checkable
class TemporalScheduleTarget(Protocol):
    """Target and namespace scoped inventory, readback, pause and resume only."""

    def list_page(self, page_token: str | None) -> TemporalScheduleInventoryPage: ...

    def history_page(
        self, remote_schedule_id: str, page_token: str | None
    ) -> TemporalScheduleHistoryPage: ...

    def describe(self, remote_schedule_id: str) -> TemporalScheduleReadback: ...

    def pause(self, remote_schedule_id: str, command_key: str) -> None: ...

    def resume_after(
        self, remote_schedule_id: str, command_key: str, start_at: datetime
    ) -> None: ...


@runtime_checkable
class ScheduleAlertSink(Protocol):
    def emit_hold(
        self,
        workspace_id: UUID,
        source_id: UUID,
        reason_code: str,
        evidence_sha256: str,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class ProductionInventoryReceipt:
    inventory_id: UUID
    inventory_sha256: str
    page_count: int
    member_count: int
    bound_member_count: int
    out_of_scope_member_count: int
    finalized_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ProductionScheduleBinding:
    inventory_id: UUID
    inventory_sha256: str
    target_alias: str
    namespace: str
    remote_schedule_id: str
    source_approval_id: UUID
    member_sha256: str
    binding_fingerprint: str
    source_kind: str
    schedule_id: UUID
    publication_schedule_id: UUID | None
    workspace_id: UUID
    goal_id: UUID
    goal_revision: int
    source_actor_id: UUID
    source_owner_subject_id: UUID
    schedule_operator_subject_id: UUID
    route_id: UUID
    route_tuple_sha256: str
    slot_id: UUID
    owner_id: UUID
    fencing_epoch: int
    route_manifest_sha256: str
    artifact_lock_sha256: str
    interval_seconds: int
    first_v4_slot: datetime
    member_snapshot: Mapping[str, object]

    def __post_init__(self) -> None:
        for name in (
            "inventory_id",
            "source_approval_id",
            "schedule_id",
            "workspace_id",
            "goal_id",
            "source_actor_id",
            "source_owner_subject_id",
            "schedule_operator_subject_id",
            "route_id",
            "slot_id",
            "owner_id",
        ):
            if not isinstance(getattr(self, name), UUID):
                raise ValueError(f"production schedule binding {name} UUID required")
        if self.publication_schedule_id is not None and not isinstance(
            self.publication_schedule_id, UUID
        ):
            raise ValueError("publication schedule UUID required")
        for name in (
            "inventory_sha256",
            "member_sha256",
            "binding_fingerprint",
            "route_tuple_sha256",
            "route_manifest_sha256",
            "artifact_lock_sha256",
        ):
            _digest(getattr(self, name), name)
        _text(self.target_alias, "target alias")
        _text(self.namespace, "namespace")
        _text(self.remote_schedule_id, "remote schedule ID")
        _aware(self.first_v4_slot, "first V4 slot")
        if not isinstance(self.member_snapshot, Mapping) or frozenset(
            self.member_snapshot
        ) != _MEMBER_KEYS:
            raise ValueError("complete immutable production member snapshot required")
        object.__setattr__(self, "member_snapshot", MappingProxyType(dict(self.member_snapshot)))


@dataclass(frozen=True, slots=True)
class ProductionIntentKey:
    intent_id: UUID
    eligibility_revision: int

    def __post_init__(self) -> None:
        if not isinstance(self.intent_id, UUID):
            raise ValueError("canonical intent UUID required")
        if type(self.eligibility_revision) is not int or self.eligibility_revision < 1:
            raise ValueError("positive canonical eligibility revision required")
