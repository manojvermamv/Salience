"""Small typed Temporal readback contract shared with rollback preflight."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class WorkerReadback:
    """Read-only evidence of exact Temporal registration and drained state."""

    route_id: UUID
    slot_id: UUID
    owner_id: UUID
    fencing_epoch: int
    route_manifest_sha256: str
    artifact_lock_sha256: str
    temporal_endpoint_id: str
    namespace: str
    task_queue: str
    registered_workflow_types: frozenset[str]
    registered_activity_types: frozenset[str]
    poller_count: int
    active_task_count: int
    observed_at: datetime
    complete: bool

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, UUID)
            for value in (self.route_id, self.slot_id, self.owner_id)
        ):
            raise ValueError("worker readback route, slot, and owner UUIDs are required")
        if type(self.fencing_epoch) is not int or self.fencing_epoch < 1:
            raise ValueError("worker readback needs a positive route fencing epoch")
        for value in (self.route_manifest_sha256, self.artifact_lock_sha256):
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise ValueError("worker readback digests must be lowercase SHA-256")
        for value in (self.temporal_endpoint_id, self.namespace, self.task_queue):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("worker readback target, namespace, and queue are required")
        for values in (
            self.registered_workflow_types,
            self.registered_activity_types,
        ):
            if not isinstance(values, frozenset) or any(
                not isinstance(item, str) or not item.strip() for item in values
            ):
                raise ValueError("worker readback registrations must be immutable name sets")
        if any(
            type(value) is not int or value < 0
            for value in (self.poller_count, self.active_task_count)
        ):
            raise ValueError("worker readback counts must be non-negative integers")
        if (
            not isinstance(self.observed_at, datetime)
            or self.observed_at.tzinfo is None
            or self.observed_at.utcoffset() is None
        ):
            raise ValueError("worker readback observation time must be timezone-aware")
        if type(self.complete) is not bool:
            raise ValueError("worker readback completeness must be explicit")


@dataclass(frozen=True, slots=True)
class WorkerDrainReceipt:
    """Route-bound evidence of the worker drain boundary and its readback."""

    status: Literal["lease_lost", "stopped"]
    route_id: UUID
    slot_id: UUID
    owner_id: UUID
    fencing_epoch: int
    route_manifest_sha256: str
    artifact_lock_sha256: str
    route_tuple_sha256: str
    boundary_at: datetime
    readback_observed_at: datetime

    def __post_init__(self) -> None:
        if self.status not in {"lease_lost", "stopped"}:
            raise ValueError("worker drain receipt status is unsupported")
        if any(
            not isinstance(value, UUID)
            for value in (self.route_id, self.slot_id, self.owner_id)
        ):
            raise ValueError("worker drain receipt route, slot, and owner UUIDs are required")
        if type(self.fencing_epoch) is not int or self.fencing_epoch < 1:
            raise ValueError("worker drain receipt needs a positive route fencing epoch")
        for value in (
            self.route_manifest_sha256,
            self.artifact_lock_sha256,
            self.route_tuple_sha256,
        ):
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise ValueError("worker drain receipt digests must be lowercase SHA-256")
        for name in ("boundary_at", "readback_observed_at"):
            value = getattr(self, name)
            if (
                not isinstance(value, datetime)
                or value.tzinfo is None
                or value.utcoffset() is None
            ):
                raise ValueError(f"worker drain receipt {name} must be timezone-aware")
        if self.readback_observed_at < self.boundary_at:
            raise ValueError("worker drain receipt readback predates its drain boundary")


class TemporalWorkerReadback(Protocol):
    """Injected read-only view bound to one Temporal target and namespace."""

    def read_worker(self, route_id: UUID, slot_id: UUID) -> WorkerReadback: ...
