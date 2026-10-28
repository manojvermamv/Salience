"""Deterministic in-process route authority for integration contracts only.

This fake is not imported by the application and does not qualify a remote
lease service. Its lock supplies one serialization order for tests that need
to exercise claim, fencing, and outage behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Callable, Literal
from uuid import UUID, uuid4

from salience.cycles.production_dispatch import RouteFenceDecision, RouteFenceRequest
from salience.cycles.production_routing import (
    ROUTE_FENCE_AUTHORITY_VERSION,
    RouteLease,
    RouteLeaseClaim,
    RouteSlotReadback,
)


@dataclass(slots=True)
class _SlotState:
    state: Literal[
        "missing", "leased", "released", "terminated", "expired"
    ] = "missing"
    last_fencing_epoch: int = 0
    lease: RouteLease | None = None
    build_id: str | None = None


@dataclass(frozen=True, slots=True)
class RouteDecisionRecord:
    """Test-only evidence that a decision is unused or in flight."""

    decision: RouteFenceDecision
    expires_at: datetime
    consumed: bool = False


class FakeRouteLeaseAuthority:
    """Thread-safe deterministic test fake implementing the route protocol."""

    protocol_version = ROUTE_FENCE_AUTHORITY_VERSION

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        lease_ttl: timedelta = timedelta(seconds=30),
        decision_ttl: timedelta = timedelta(seconds=1),
    ) -> None:
        if lease_ttl <= timedelta(0):
            raise ValueError("route lease TTL must be positive")
        if decision_ttl <= timedelta(0):
            raise ValueError("route decision TTL must be positive")
        self._clock = (
            clock if clock is not None else (lambda: datetime.now(timezone.utc))
        )
        self._lease_ttl = lease_ttl
        self._decision_ttl = decision_ttl
        self._lock = Lock()
        self._available = True
        self._slots: dict[tuple[UUID, UUID], _SlotState] = {}
        self._decisions: dict[UUID, RouteDecisionRecord] = {}

    def claim_slot(self, request: RouteLeaseClaim) -> RouteLease:
        with self._lock:
            now = self._now()
            self._require_available()
            key = (request.route_id, request.slot_id)
            state = self._slots.setdefault(key, _SlotState())
            self._expire_if_needed(state, now)
            if state.lease is not None:
                raise PermissionError("route slot is already claimed")

            lease = RouteLease(
                route_id=request.route_id,
                slot_id=request.slot_id,
                owner_id=request.owner_id,
                fencing_epoch=state.last_fencing_epoch + 1,
                expires_at=now + self._lease_ttl,
                route_manifest_sha256=request.route_manifest_sha256,
                artifact_lock_sha256=request.artifact_lock_sha256,
            )
            state.state = "leased"
            state.last_fencing_epoch = lease.fencing_epoch
            state.lease = lease
            state.build_id = request.build_id
            return lease

    def renew_slot(self, lease: RouteLease) -> RouteLease:
        with self._lock:
            now = self._now()
            self._require_available()
            state = self._slots.get((lease.route_id, lease.slot_id))
            self._require_current_lease(state, lease, now)
            assert state is not None
            renewed = replace(lease, expires_at=now + self._lease_ttl)
            state.lease = renewed
            return renewed

    def authorize_action(self, request: RouteFenceRequest) -> RouteFenceDecision:
        with self._lock:
            now = self._now()
            if not self._available:
                return self._denied(request, "route_fence_unavailable")
            state = self._slots.get((request.route_id, request.slot_id))
            lease = self._active_lease(state, now)
            if lease is None or not self._lease_matches_request(lease, request):
                return self._denied(request, "route_fence_denied")

            decision_id = self._new_decision_id()
            decision = RouteFenceDecision(
                authorized=True,
                decision_id=decision_id,
                reason_code=None,
                request=request,
            )
            self._decisions[decision_id] = RouteDecisionRecord(
                decision=decision,
                expires_at=now + self._decision_ttl,
            )
            return decision

    def release_slot(self, lease: RouteLease) -> RouteSlotReadback:
        with self._lock:
            now = self._now()
            self._require_available()
            state = self._slots.get((lease.route_id, lease.slot_id))
            self._require_current_lease(state, lease, now)
            assert state is not None
            state.state = "released"
            state.lease = None
            return self._readback(lease.route_id, lease.slot_id, state)

    def terminate_slot(self, lease: RouteLease) -> RouteSlotReadback:
        with self._lock:
            now = self._now()
            self._require_available()
            state = self._slots.get((lease.route_id, lease.slot_id))
            self._require_current_lease(state, lease, now)
            assert state is not None
            state.state = "terminated"
            state.lease = None
            return self._readback(lease.route_id, lease.slot_id, state)

    def read_slot(self, route_id: UUID, slot_id: UUID) -> RouteSlotReadback:
        with self._lock:
            now = self._now()
            if not self._available:
                return self._unavailable_readback(route_id, slot_id)
            state = self._slots.get((route_id, slot_id))
            if state is None:
                return RouteSlotReadback(
                    route_id=route_id,
                    slot_id=slot_id,
                    available=True,
                    state="missing",
                    lease=None,
                    fencing_epoch=None,
                )
            self._expire_if_needed(state, now)
            return self._readback(route_id, slot_id, state)

    def consume_decision(
        self, decision_id: UUID | None, request: RouteFenceRequest
    ) -> bool:
        """Consume one exact decision as an in-flight action liability."""

        with self._lock:
            now = self._now()
            if not self._available or not isinstance(decision_id, UUID):
                return False
            record = self._decisions.get(decision_id)
            if (
                record is None
                or record.consumed
                or record.expires_at <= now
                or not self._same_request(record.decision.request, request)
            ):
                return False
            state = self._slots.get((request.route_id, request.slot_id))
            lease = self._active_lease(state, now)
            if lease is None or not self._lease_matches_request(lease, request):
                return False
            self._decisions[decision_id] = replace(record, consumed=True)
            return True

    def decision_record(self, decision_id: UUID | None) -> RouteDecisionRecord | None:
        """Return retained test evidence without consuming or refreshing it."""

        if not isinstance(decision_id, UUID):
            return None
        with self._lock:
            return self._decisions.get(decision_id)

    def set_available(self, available: bool) -> None:
        """Toggle the fake service for deterministic outage tests."""

        if type(available) is not bool:
            raise ValueError("fake route service availability must be explicit")
        with self._lock:
            self._available = available

    def _now(self) -> datetime:
        now = self._clock()
        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() is None
        ):
            raise ValueError("route authority clock must return an aware datetime")
        return now

    def _require_available(self) -> None:
        if not self._available:
            raise PermissionError("route lease authority unavailable")

    def _require_current_lease(
        self, state: _SlotState | None, lease: RouteLease, now: datetime
    ) -> None:
        current = self._active_lease(state, now)
        if current is None or current != lease:
            raise PermissionError("route lease is stale")

    @staticmethod
    def _active_lease(state: _SlotState | None, now: datetime) -> RouteLease | None:
        if state is None or state.lease is None or state.lease.expires_at <= now:
            if state is not None and state.lease is not None:
                state.state = "expired"
                state.lease = None
            return None
        return state.lease

    @staticmethod
    def _expire_if_needed(state: _SlotState, now: datetime) -> None:
        if state.lease is not None and state.lease.expires_at <= now:
            state.state = "expired"
            state.lease = None

    @staticmethod
    def _lease_matches_request(lease: RouteLease, request: RouteFenceRequest) -> bool:
        return (
            lease.route_id == request.route_id
            and lease.slot_id == request.slot_id
            and lease.owner_id == request.owner_id
            and lease.fencing_epoch == request.fencing_epoch
            and lease.route_manifest_sha256 == request.route_manifest_sha256
            and lease.artifact_lock_sha256 == request.artifact_lock_sha256
        )

    @staticmethod
    def _same_request(left: RouteFenceRequest, right: RouteFenceRequest) -> bool:
        fields = (
            "deployment_id",
            "environment_id",
            "route_id",
            "slot_id",
            "owner_id",
            "fencing_epoch",
            "route_manifest_sha256",
            "artifact_lock_sha256",
            "route_tuple_sha256",
            "operation_id",
            "permit_id",
            "action_kind",
        )
        return all(getattr(left, name) == getattr(right, name) for name in fields)

    def _new_decision_id(self) -> UUID:
        while True:
            decision_id = uuid4()
            if decision_id not in self._decisions:
                return decision_id

    @staticmethod
    def _denied(request: RouteFenceRequest, reason: str) -> RouteFenceDecision:
        return RouteFenceDecision(
            authorized=False,
            decision_id=None,
            reason_code=reason,
            request=request,
        )

    def _readback(
        self, route_id: UUID, slot_id: UUID, state: _SlotState
    ) -> RouteSlotReadback:
        if not self._available:
            return self._unavailable_readback(route_id, slot_id)
        return RouteSlotReadback(
            route_id=route_id,
            slot_id=slot_id,
            available=True,
            state=state.state,
            lease=state.lease,
            fencing_epoch=state.last_fencing_epoch or None,
        )

    @staticmethod
    def _unavailable_readback(route_id: UUID, slot_id: UUID) -> RouteSlotReadback:
        return RouteSlotReadback(
            route_id=route_id,
            slot_id=slot_id,
            available=False,
            state="unavailable",
            lease=None,
            fencing_epoch=None,
        )
