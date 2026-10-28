"""Provider-neutral route lease authority contracts for P1."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from importlib import import_module
from pathlib import Path
import sys
from threading import Barrier
from uuid import UUID

import pytest

from salience.cycles import production_routing
from salience.cycles.production_dispatch import RouteFenceRequest


_TESTS_DIR = Path(__file__).resolve().parents[1]
_FAKE_PATH = _TESTS_DIR / "support" / "production_route_authority.py"
_ROUTE_ID = UUID("00000000-0000-0000-0000-000000000101")
_DEPLOYMENT_ID = UUID("00000000-0000-0000-0000-000000000102")
_ENVIRONMENT_ID = UUID("00000000-0000-0000-0000-000000000103")
_SLOT_A = UUID("00000000-0000-0000-0000-000000000104")
_SLOT_B = UUID("00000000-0000-0000-0000-000000000105")
_OWNER_A = UUID("00000000-0000-0000-0000-000000000106")
_OWNER_B = UUID("00000000-0000-0000-0000-000000000107")
_OWNER_C = UUID("00000000-0000-0000-0000-000000000108")
_MANIFEST = "a" * 64
_ARTIFACT_LOCK = "b" * 64
_DRIFTED_ARTIFACT_LOCK = "c" * 64


class _Clock:
    def __init__(self) -> None:
        self.current = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> None:
        self.current += delta


def _route_contract():
    required = (
        "ROUTE_FENCE_AUTHORITY_VERSION",
        "RouteLease",
        "RouteLeaseAuthority",
        "RouteLeaseClaim",
        "RouteSlotReadback",
    )
    missing = [name for name in required if not hasattr(production_routing, name)]
    assert not missing, f"missing provider-neutral route lease contract: {missing}"
    return production_routing


def _fake_module():
    assert _FAKE_PATH.is_file(), "missing tests/support/production_route_authority.py"
    if str(_TESTS_DIR) not in sys.path:
        sys.path.insert(0, str(_TESTS_DIR))
    return import_module("support.production_route_authority")


def _authority(
    clock: _Clock | None = None,
    *,
    decision_ttl: timedelta = timedelta(seconds=1),
):
    contract = _route_contract()
    fake = _fake_module()
    authority = fake.FakeRouteLeaseAuthority(
        clock=clock if clock is not None else _Clock(),
        decision_ttl=decision_ttl,
    )
    assert isinstance(authority, contract.RouteLeaseAuthority)
    assert authority.protocol_version == "salience.p1-route-fence.v1"
    return authority


def _claim(
    owner_id: UUID,
    *,
    slot_id: UUID = _SLOT_A,
    manifest_sha256: str = _MANIFEST,
    artifact_lock_sha256: str = _ARTIFACT_LOCK,
):
    contract = _route_contract()
    return contract.RouteLeaseClaim(
        route_id=_ROUTE_ID,
        route_manifest_sha256=manifest_sha256,
        artifact_lock_sha256=artifact_lock_sha256,
        slot_id=slot_id,
        owner_id=owner_id,
        build_id="salience-worker-build-2026-10-28",
    )


def _request(
    lease,
    *,
    operation_id: UUID = UUID("00000000-0000-0000-0000-000000000109"),
    action_kind: str = "publish",
) -> RouteFenceRequest:
    return RouteFenceRequest(
        deployment_id=_DEPLOYMENT_ID,
        environment_id=_ENVIRONMENT_ID,
        route_id=lease.route_id,
        slot_id=lease.slot_id,
        owner_id=lease.owner_id,
        fencing_epoch=lease.fencing_epoch,
        route_manifest_sha256=lease.route_manifest_sha256,
        artifact_lock_sha256=lease.artifact_lock_sha256,
        operation_id=operation_id,
        permit_id=UUID("00000000-0000-0000-0000-000000000110"),
        action_kind=action_kind,
        route_tuple_sha256="d" * 64,
    )


def test_route_claim_is_compare_and_swap_for_each_slot() -> None:
    authority = _authority()
    start = Barrier(5)
    contenders = (
        (_SLOT_A, _OWNER_A),
        (_SLOT_A, _OWNER_B),
        (_SLOT_B, _OWNER_B),
        (_SLOT_B, _OWNER_C),
    )

    def compete(slot_id: UUID, owner_id: UUID):
        start.wait(timeout=5)
        try:
            return authority.claim_slot(_claim(owner_id, slot_id=slot_id))
        except PermissionError:
            return None

    with ThreadPoolExecutor(max_workers=4) as workers:
        futures = [workers.submit(compete, slot, owner) for slot, owner in contenders]
        start.wait(timeout=5)
        results = [future.result(timeout=5) for future in futures]

    successful = [lease for lease in results if lease is not None]
    assert len(successful) == 2
    assert {lease.slot_id for lease in successful} == {_SLOT_A, _SLOT_B}
    for slot_id in (_SLOT_A, _SLOT_B):
        readback = authority.read_slot(_ROUTE_ID, slot_id)
        assert readback.state == "leased"
        assert readback.lease is not None
        assert readback.lease.fencing_epoch == 1
        winner = next(lease for lease in successful if lease.slot_id == slot_id)
        valid_owners = {
            owner for candidate_slot, owner in contenders if candidate_slot == slot_id
        }
        assert winner.owner_id in valid_owners
        assert readback.lease == winner


def test_fake_route_authority_linearizes_transfer_and_one_use_decisions() -> None:
    clock = _Clock()
    authority = _authority(clock, decision_ttl=timedelta(seconds=30))
    first_lease = authority.claim_slot(_claim(_OWNER_A))
    clock.advance(timedelta(seconds=1))
    renewed = authority.renew_slot(first_lease)
    assert renewed.fencing_epoch == first_lease.fencing_epoch
    assert renewed.expires_at > first_lease.expires_at

    in_flight_request = _request(renewed)
    consumed_decision = authority.authorize_action(in_flight_request)
    pending_request = _request(
        renewed,
        operation_id=UUID("00000000-0000-0000-0000-000000000111"),
        action_kind="finalize",
    )
    pending_decision = authority.authorize_action(pending_request)
    assert consumed_decision.authorized is True
    assert pending_decision.authorized is True
    assert consumed_decision.decision_id != pending_decision.decision_id

    decision_id = consumed_decision.decision_id
    assert decision_id is not None
    for different_request in (
        replace(in_flight_request, action_kind="cancel"),
        replace(in_flight_request, route_tuple_sha256="e" * 64),
        replace(
            in_flight_request,
            operation_id=UUID("00000000-0000-0000-0000-000000000112"),
        ),
        replace(in_flight_request, fencing_epoch=in_flight_request.fencing_epoch + 1),
    ):
        assert authority.consume_decision(decision_id, different_request) is False

    assert authority.consume_decision(decision_id, in_flight_request) is True
    consumed_record = authority.decision_record(decision_id)
    assert consumed_record is not None and consumed_record.consumed is True
    assert consumed_record.decision is consumed_decision
    assert authority.consume_decision(decision_id, in_flight_request) is False

    released = authority.release_slot(renewed)
    assert released.state == "released"
    assert released.protocol_version == "salience.p1-route-fence.v1"
    second_lease = authority.claim_slot(_claim(_OWNER_B))
    assert second_lease.fencing_epoch == renewed.fencing_epoch + 1
    assert authority.consume_decision(pending_decision.decision_id, pending_request) is False
    assert authority.decision_record(decision_id) == consumed_record
    pending_record = authority.decision_record(pending_decision.decision_id)
    assert pending_record is not None and pending_record.consumed is False
    readback = authority.read_slot(_ROUTE_ID, _SLOT_A)
    assert readback.protocol_version == "salience.p1-route-fence.v1"
    assert readback.lease == second_lease


def test_route_decision_rejects_stale_owner_and_artifact_drift() -> None:
    authority = _authority()
    old_lease = authority.claim_slot(_claim(_OWNER_A))
    old_request = _request(old_lease)
    authority.release_slot(old_lease)
    current_lease = authority.claim_slot(
        _claim(_OWNER_B, artifact_lock_sha256=_DRIFTED_ARTIFACT_LOCK)
    )

    stale_owner_decision = authority.authorize_action(old_request)
    assert stale_owner_decision.authorized is False
    assert stale_owner_decision.reason_code == "route_fence_denied"

    current_owner_stale_artifact = replace(
        old_request,
        owner_id=current_lease.owner_id,
        fencing_epoch=current_lease.fencing_epoch,
    )
    drifted_decision = authority.authorize_action(current_owner_stale_artifact)
    assert drifted_decision.authorized is False
    assert drifted_decision.reason_code == "route_fence_denied"

    current_request = _request(current_lease)
    allowed = authority.authorize_action(current_request)
    assert allowed.authorized is True
    assert allowed.decision_id is not None
    assert allowed.request is current_request
    terminated = authority.terminate_slot(current_lease)
    assert terminated.state == "terminated"
    assert terminated.protocol_version == "salience.p1-route-fence.v1"
    after_termination = authority.authorize_action(current_request)
    assert after_termination.authorized is False
    assert after_termination.reason_code == "route_fence_denied"
    assert authority.read_slot(_ROUTE_ID, _SLOT_A).state == "terminated"


def test_route_service_outage_denies_without_cached_epoch() -> None:
    authority = _authority()
    lease = authority.claim_slot(_claim(_OWNER_A))
    request = _request(lease)
    assert authority.authorize_action(request).authorized is True

    authority.set_available(False)
    denied = authority.authorize_action(request)
    assert denied.authorized is False
    assert denied.decision_id is None
    assert denied.reason_code == "route_fence_unavailable"
    readback = authority.read_slot(_ROUTE_ID, _SLOT_A)
    assert readback.available is False
    assert readback.state == "unavailable"
    assert readback.fencing_epoch is None
    assert readback.lease is None
    with pytest.raises(PermissionError):
        authority.claim_slot(_claim(_OWNER_B, slot_id=_SLOT_B))
    with pytest.raises(PermissionError):
        authority.renew_slot(lease)
    with pytest.raises(PermissionError):
        authority.release_slot(lease)
    with pytest.raises(PermissionError):
        authority.terminate_slot(lease)

    clock = _Clock()
    expiring_authority = _authority(clock)
    short_lease = expiring_authority.claim_slot(_claim(_OWNER_A))
    short_request = _request(short_lease)
    expiring_decision = expiring_authority.authorize_action(short_request)
    clock.advance(timedelta(seconds=2))
    assert expiring_authority.consume_decision(
        expiring_decision.decision_id, short_request
    ) is False
