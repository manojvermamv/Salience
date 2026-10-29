"""Provider-neutral route lease authority contracts for P1."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from importlib import import_module
from pathlib import Path
import sys
from threading import Barrier
from types import SimpleNamespace
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
    def __init__(self, start: datetime | None = None) -> None:
        self.current = start or datetime.now(timezone.utc)

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
    lease_ttl: timedelta = timedelta(seconds=30),
):
    contract = _route_contract()
    fake = _fake_module()
    authority = fake.FakeRouteLeaseAuthority(
        clock=clock if clock is not None else _Clock(),
        decision_ttl=decision_ttl,
        lease_ttl=lease_ttl,
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


def _publication_route():
    from salience.cycles.production_routing import (
        ProductionRouteManifestV1,
        RouteTupleV1,
        route_tuple_sha256,
    )

    workflow_types = ("GovernedPublicationWorkflow",)
    activity_types = (
        "salience.publication.request",
        "salience.publication.authorize",
        "salience.publication.delivery",
        "salience.publication.submit_or_reconcile",
        "salience.publication.await",
        "salience.publication.complete",
        "salience.publication.denied",
        "salience.publication.cancel",
        "salience.publication.dead_letter",
    )
    route_tuple = RouteTupleV1(
        deployment_id=_DEPLOYMENT_ID,
        environment_id=_ENVIRONMENT_ID,
        temporal_endpoint_id="temporal-endpoint-test",
        namespace="salience-production-test",
        task_queue="salience-publication-test",
        worker_kind="production-publication",
        workflow_types=workflow_types,
        activity_types=activity_types,
        producer_bindings=(),
        compatibility_mode="exact-head",
    )
    return ProductionRouteManifestV1(
        route_id=_ROUTE_ID,
        route_tuple=route_tuple,
        route_tuple_sha256=route_tuple_sha256(route_tuple),
        worker_build_id="salience-worker-build-2026-10-28",
        slots=(_SLOT_A, _SLOT_B),
        artifact_sha256="e" * 64,
        image_sha256="f" * 64,
        artifact_lock_sha256=_ARTIFACT_LOCK,
    )


def _route_lease(authority, route=None, *, owner_id=_OWNER_A, slot_id=_SLOT_A):
    from salience.cycles.production_routing import (
        RouteLeaseClaim,
        route_manifest_sha256,
    )

    route = route or _publication_route()
    return authority.claim_slot(
        RouteLeaseClaim(
            route_id=route.route_id,
            route_manifest_sha256=route_manifest_sha256(route),
            artifact_lock_sha256=route.artifact_lock_sha256,
            slot_id=slot_id,
            owner_id=owner_id,
            build_id=route.worker_build_id,
        )
    )


def _publication_state():
    from salience.workflows.publication import PublicationWorkflowState

    return PublicationWorkflowState(store=object(), repository=object())


def _approval_and_publication_binding(route):
    from datetime import datetime, timedelta, timezone
    from salience.cycles.production_dispatch import PublicationEffectBinding
    from salience.governance.production_policy import ProductionApprovalBinding

    approval = ProductionApprovalBinding(
        approval_request_id=UUID("00000000-0000-0000-0000-000000000201"),
        workspace_id=UUID("00000000-0000-0000-0000-000000000202"),
        program_id=UUID("00000000-0000-0000-0000-000000000203"),
        goal_id=UUID("00000000-0000-0000-0000-000000000204"),
        goal_revision=1,
        intent_id=UUID("00000000-0000-0000-0000-000000000205"),
        cycle_id=UUID("00000000-0000-0000-0000-000000000206"),
        context_id=UUID("00000000-0000-0000-0000-000000000207"),
        operation_id=UUID("00000000-0000-0000-0000-000000000208"),
        subject_id=UUID("00000000-0000-0000-0000-000000000209"),
        policy_version_id=UUID("00000000-0000-0000-0000-000000000210"),
        policy_document_hash="a" * 64,
        effect_type="write",
        purpose="authorized publication",
        artifact_sha256="9" * 64,
        account_id=UUID("00000000-0000-0000-0000-000000000211"),
        provider_id="publisher.test",
        required_scopes=("publish:create",),
        budget_id=UUID("00000000-0000-0000-0000-000000000212"),
        budget_reservation_id=UUID("00000000-0000-0000-0000-000000000213"),
        expires_at=datetime(2026, 10, 28, 12, 5, tzinfo=timezone.utc),
    )
    publication = PublicationEffectBinding(
        publication_request_id=UUID("00000000-0000-0000-0000-000000000214"),
        job_id=UUID("00000000-0000-0000-0000-000000000215"),
        workflow_id="publication-cycle-stable",
        workflow_run_id="publication-run-stable",
        publication_plan_id=UUID("00000000-0000-0000-0000-000000000216"),
        publication_attempt_id=UUID("00000000-0000-0000-0000-000000000217"),
        external_effect_id=UUID("00000000-0000-0000-0000-000000000218"),
    )
    return approval, publication


def _permit_factory(route, lease):
    from datetime import datetime, timedelta, timezone

    from salience.cycles.production_dispatch import ProductionPermitRequest, RouteBinding

    route_binding = RouteBinding(
        deployment_id=route.route_tuple.deployment_id,
        environment_id=route.route_tuple.environment_id,
        route_id=route.route_id,
        slot_id=lease.slot_id,
        owner_id=lease.owner_id,
        fencing_epoch=lease.fencing_epoch,
        route_manifest_sha256=lease.route_manifest_sha256,
        artifact_lock_sha256=lease.artifact_lock_sha256,
        route_tuple_sha256=route.route_tuple_sha256,
    )

    def create(approval, publication):
        return ProductionPermitRequest(
            workspace_id=approval.workspace_id,
            goal_id=approval.goal_id,
            goal_revision=approval.goal_revision,
            program_id=approval.program_id,
            intent_id=approval.intent_id,
            cycle_id=approval.cycle_id,
            context_id=approval.context_id,
            operation_id=approval.operation_id,
            subject_id=approval.subject_id,
            policy_version_id=approval.policy_version_id,
            approval_request_id=approval.approval_request_id,
            required_scopes=approval.required_scopes,
            effect_type=approval.effect_type,
            purpose=approval.purpose,
            artifact_sha256=approval.artifact_sha256,
            account_id=approval.account_id,
            budget_id=approval.budget_id,
            budget_reservation_id=approval.budget_reservation_id,
            provider_id=approval.provider_id,
            idempotency_key="publication-operation-stable",
            route=route_binding,
            execution_deadline=datetime.now(timezone.utc) + timedelta(seconds=20),
            ttl_seconds=20,
            publication_binding=publication,
        )

    return create


def _worker_readback(
    route,
    lease,
    *,
    poller_count=0,
    active_task_count=0,
    observed_at=None,
):
    from datetime import datetime, timezone

    from salience.workflows.worker import WorkerReadback

    return WorkerReadback(
        route_id=route.route_id,
        slot_id=lease.slot_id,
        owner_id=lease.owner_id,
        fencing_epoch=lease.fencing_epoch,
        route_manifest_sha256=lease.route_manifest_sha256,
        artifact_lock_sha256=lease.artifact_lock_sha256,
        temporal_endpoint_id=route.route_tuple.temporal_endpoint_id,
        namespace=route.route_tuple.namespace,
        task_queue=route.route_tuple.task_queue,
        registered_workflow_types=frozenset(route.route_tuple.workflow_types),
        registered_activity_types=frozenset(route.route_tuple.activity_types),
        poller_count=poller_count,
        active_task_count=active_task_count,
        observed_at=observed_at or datetime.now(timezone.utc),
        complete=True,
    )


def _worker_drain_receipt(route, lease, *, boundary_at, readback_observed_at=None):
    from salience.workflows.temporal_readback import WorkerDrainReceipt

    return WorkerDrainReceipt(
        status="stopped",
        route_id=route.route_id,
        slot_id=lease.slot_id,
        owner_id=lease.owner_id,
        fencing_epoch=lease.fencing_epoch,
        route_manifest_sha256=lease.route_manifest_sha256,
        artifact_lock_sha256=lease.artifact_lock_sha256,
        route_tuple_sha256=route.route_tuple_sha256,
        boundary_at=boundary_at,
        readback_observed_at=readback_observed_at or boundary_at,
    )


def test_route_transfer_rejects_stale_pre_drain_readback_without_release(monkeypatch):
    from dataclasses import replace

    from salience.workflows import worker

    clock = _Clock(datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc))
    authority = _authority(clock)
    route = _publication_route()
    lease = _route_lease(authority, route)
    release_calls = []
    release_slot = authority.release_slot

    def record_release(current):
        release_calls.append(current)
        return release_slot(current)

    monkeypatch.setattr(authority, "release_slot", record_release)

    stale = replace(
        _worker_readback(route, lease),
        observed_at=clock() - timedelta(seconds=1),
    )

    with pytest.raises(PermissionError, match="readback"):
        worker.transfer_routed_worker(
            route=route,
            lease=lease,
            drain_receipt=_worker_drain_receipt(
                route, lease, boundary_at=clock()
            ),
            route_authority=authority,
            temporal_readback=SimpleNamespace(
                read_worker=lambda route_id, slot_id: stale
            ),
            now=clock,
        )

    assert release_calls == []


def test_route_transfer_rejects_future_readback_without_release(monkeypatch):
    from dataclasses import replace

    from salience.workflows import worker

    clock = _Clock(datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc))
    authority = _authority(clock)
    route = _publication_route()
    lease = _route_lease(authority, route)
    release_calls = []
    release_slot = authority.release_slot

    def record_release(current):
        release_calls.append(current)
        return release_slot(current)

    monkeypatch.setattr(authority, "release_slot", record_release)

    future = replace(
        _worker_readback(route, lease),
        observed_at=clock() + timedelta(seconds=1),
    )

    with pytest.raises(PermissionError, match="readback"):
        worker.transfer_routed_worker(
            route=route,
            lease=lease,
            drain_receipt=_worker_drain_receipt(
                route, lease, boundary_at=clock()
            ),
            route_authority=authority,
            temporal_readback=SimpleNamespace(
                read_worker=lambda route_id, slot_id: future
            ),
            now=clock,
        )

    assert release_calls == []


def test_route_transfer_accepts_readback_at_both_boundary_equalities(monkeypatch):
    from salience.workflows import worker

    clock = _Clock(datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc))
    authority = _authority(clock)
    route = _publication_route()
    lease = _route_lease(authority, route)
    release_calls = []
    release_slot = authority.release_slot

    def record_release(current):
        release_calls.append(current)
        return release_slot(current)

    monkeypatch.setattr(authority, "release_slot", record_release)

    released = worker.transfer_routed_worker(
        route=route,
        lease=lease,
        drain_receipt=_worker_drain_receipt(
            route, lease, boundary_at=clock(), readback_observed_at=clock()
        ),
        route_authority=authority,
        temporal_readback=SimpleNamespace(
            read_worker=lambda route_id, slot_id: _worker_readback(
                route, lease, observed_at=clock()
            )
        ),
        now=clock,
    )

    assert released.state == "released"
    assert release_calls == [lease]


def test_route_transfer_rejects_receipt_identity_mismatch_without_release(monkeypatch):
    from dataclasses import replace

    from salience.workflows import worker

    clock = _Clock(datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc))
    authority = _authority(clock)
    route = _publication_route()
    lease = _route_lease(authority, route)
    receipt = replace(
        _worker_drain_receipt(route, lease, boundary_at=clock()),
        fencing_epoch=lease.fencing_epoch + 1,
    )
    readback_calls = []
    release_calls = []
    release_slot = authority.release_slot

    def record_release(current):
        release_calls.append(current)
        return release_slot(current)

    monkeypatch.setattr(authority, "release_slot", record_release)

    with pytest.raises(PermissionError, match="drain receipt"):
        worker.transfer_routed_worker(
            route=route,
            lease=lease,
            drain_receipt=receipt,
            route_authority=authority,
            temporal_readback=SimpleNamespace(
                read_worker=lambda route_id, slot_id: readback_calls.append(
                    (route_id, slot_id)
                )
            ),
            now=clock,
        )

    assert readback_calls == []
    assert release_calls == []


@pytest.mark.asyncio
async def test_route_preflight_denies_before_temporal_connect(monkeypatch, tmp_path):
    import hashlib
    import json

    from salience.cycles.publication_recovery_compatibility import (
        PublicationWorkerCompatibilityError,
    )
    from salience.cycles.production_routing import canonical_route_manifest_bytes
    from salience.workflows import worker

    route = _publication_route()
    authority = _authority()
    lease = _route_lease(authority, route)
    connects = []
    registrations = []
    preflights = []
    from importlib.util import spec_from_file_location, module_from_spec

    launcher_spec = spec_from_file_location(
        "salience_fake_external_launcher",
        Path(__file__).resolve().parents[2] / "scripts/launch-v4-publication-worker.py",
    )
    launcher = module_from_spec(launcher_spec)
    launcher_spec.loader.exec_module(launcher)
    control = tmp_path / "control"
    control.mkdir()
    fake_launcher_file = control / "launch-v4-publication-worker.py"
    fake_launcher_file.write_text("# synthetic authenticated launcher\n", encoding="utf-8")
    lock_path = control / "publication-worker-launch-lock.json"
    digest_path = control / "publication-worker-launch-lock.sha256"
    lock = {
        "schema_version": "salience.publication-worker-launch-lock.v2",
        "production": {
            "schema_version": "salience.p1-production-worker-launch.v2",
            "migration_head": "0048_p1_production_schedule_bindings",
            "rollback_verifier_abi": "salience.p1-recovery-rollback-verifier.v2",
            "qualification": "development-only",
            "workflow_allowlist": list(route.route_tuple.workflow_types),
            "activity_allowlist": list(route.route_tuple.activity_types),
            "route_bundle": {
                "schema_version": "salience.p1-production-route-bundle.v1",
                "routes": [json.loads(canonical_route_manifest_bytes(route))],
            },
        },
    }
    lock_bytes = (json.dumps(lock, sort_keys=True, separators=(",", ":")) + "\n").encode()
    lock_path.write_bytes(lock_bytes)
    digest_path.write_text(hashlib.sha256(lock_bytes).hexdigest() + "\n", encoding="ascii")

    async def connector(target, *, namespace):
        connects.append((target, namespace))
        return SimpleNamespace(namespace=namespace)

    def preflight(database_url, selected_route, selected_lease):
        preflights.append((database_url, selected_route, selected_lease))
        raw = launcher._read_fixed_control_file(fake_launcher_file, "fake launcher")
        assert raw.startswith(b"# synthetic authenticated launcher")
        authenticated, lock_digest = launcher._verify_authenticated_lock(
            launcher._read_fixed_control_file(lock_path, "fake lock"),
            launcher._read_fixed_control_file(digest_path, "fake lock digest")
            .decode("ascii")
            .strip(),
        )
        assert authenticated["production"]["migration_head"].startswith("0048_")
        assert lock_digest == hashlib.sha256(lock_bytes).hexdigest()
        raise PublicationWorkerCompatibilityError("wrong production migration head")

    monkeypatch.setattr(worker, "Worker", lambda *args, **kwargs: registrations.append(kwargs))
    with pytest.raises(PublicationWorkerCompatibilityError, match="migration head"):
        await worker.connect_routed_worker(
            route=route,
            lease=lease,
            route_authority=authority,
            database_url="postgresql://primary/salience",
            publication_state=_publication_state(),
            permit_request_factory=_permit_factory(route, lease),
            temporal_connect=connector,
            compatibility_preflight=preflight,
        )
    assert len(preflights) == 1
    assert preflights[0][1:] == (route, lease)
    assert connects == []
    assert registrations == []


def test_worker_registers_exact_route_allowlists_and_namespace(monkeypatch):
    from salience.workflows import worker

    route = _publication_route()
    authority = _authority()
    lease = _route_lease(authority, route)
    calls = []

    class CaptureWorker:
        def __init__(self, client, **kwargs):
            calls.append((client, kwargs))

    client = SimpleNamespace(
        namespace=route.route_tuple.namespace,
        route_endpoint_id=route.route_tuple.temporal_endpoint_id,
        route_build_id=route.worker_build_id,
        route_artifact_sha256=route.artifact_sha256,
        route_image_sha256=route.image_sha256,
    )
    state = _publication_state()
    factory = _permit_factory(route, lease)
    monkeypatch.setattr(worker, "Worker", CaptureWorker)
    worker.build_routed_worker(
        client,
        route=route,
        lease=lease,
        route_authority=authority,
        database_url="postgresql://primary/salience",
        publication_state=state,
        permit_request_factory=factory,
    )

    assert len(calls) == 1
    registered_client, options = calls[0]
    assert registered_client is client
    assert options["task_queue"] == route.route_tuple.task_queue
    assert options["workflows"] == [worker.GovernedPublicationWorkflow]
    registered_activity_names = {
        activity.__temporal_activity_definition.name
        for activity in options["activities"]
    }
    assert registered_activity_names == set(route.route_tuple.activity_types)
    assert state.production_dispatch.database_url == "postgresql://primary/salience"
    assert state.production_dispatch.route_fence is authority
    approval, publication = _approval_and_publication_binding(route)
    request = state.production_permit_request_factory(approval, publication)
    assert request.route.route_id == route.route_id
    assert request.route.slot_id == lease.slot_id
    assert request.route.owner_id == lease.owner_id
    assert request.route.fencing_epoch == lease.fencing_epoch
    assert request.route.route_manifest_sha256 == lease.route_manifest_sha256
    assert request.route.artifact_lock_sha256 == lease.artifact_lock_sha256
    assert request.route.route_tuple_sha256 == route.route_tuple_sha256


@pytest.mark.parametrize(
    ("attestation", "mismatch"),
    [
        ("route_endpoint_id", False),
        ("route_endpoint_id", True),
        ("route_build_id", False),
        ("route_build_id", True),
        ("route_artifact_sha256", False),
        ("route_artifact_sha256", True),
        ("route_image_sha256", False),
        ("route_image_sha256", True),
    ],
)
def test_worker_requires_all_route_attestations_before_registration(
    monkeypatch, attestation, mismatch
):
    from salience.workflows import worker

    route = _publication_route()
    authority = _authority()
    lease = _route_lease(authority, route)
    expected = {
        "route_endpoint_id": route.route_tuple.temporal_endpoint_id,
        "route_build_id": route.worker_build_id,
        "route_artifact_sha256": route.artifact_sha256,
        "route_image_sha256": route.image_sha256,
    }
    attestations = {
        name: (f"wrong-{value}" if name == attestation and mismatch else value)
        for name, value in expected.items()
        if name != attestation or mismatch
    }
    client = SimpleNamespace(namespace=route.route_tuple.namespace, **attestations)
    registrations = []
    monkeypatch.setattr(
        worker,
        "Worker",
        lambda *args, **kwargs: registrations.append((args, kwargs)),
    )
    state = _publication_state()

    with pytest.raises(PermissionError, match="attestation"):
        worker.build_routed_worker(
            client,
            route=route,
            lease=lease,
            route_authority=authority,
            database_url="postgresql://primary/salience",
            publication_state=state,
            permit_request_factory=_permit_factory(route, lease),
        )

    assert registrations == []
    assert state.production_dispatch is None


@pytest.mark.asyncio
async def test_route_renewal_loss_drains_before_expiry_and_preserves_identity():
    from datetime import timedelta

    from salience.workflows import worker

    clock = _Clock()
    authority = _authority(clock)
    route = _publication_route()
    lease = _route_lease(authority, route)
    original_expiry = lease.expires_at
    shutdown_at = []
    renewal_intervals = []
    worker_stopped = asyncio.Event()
    shutdown_called = asyncio.Event()

    class LossAuthority:
        protocol_version = authority.protocol_version

        def read_slot(self, route_id, slot_id):
            return authority.read_slot(route_id, slot_id)

        def renew_slot(self, current):
            raise PermissionError("synthetic route renewal loss")

        def claim_slot(self, request):
            return authority.claim_slot(request)

        def authorize_action(self, request):
            return authority.authorize_action(request)

        def release_slot(self, current):
            return authority.release_slot(current)

        def terminate_slot(self, current):
            return authority.terminate_slot(current)

    class RunningWorker:
        async def run(self):
            try:
                await asyncio.wait_for(worker_stopped.wait(), timeout=0.25)
            except asyncio.TimeoutError:
                pass

        async def shutdown(self):
            shutdown_at.append(clock())
            shutdown_called.set()
            worker_stopped.set()

    async def wait_for_renewal(interval):
        renewal_intervals.append(interval)
        clock.advance(timedelta(seconds=interval))

    readbacks = []

    class Readback:
        def read_worker(self, route_id, slot_id):
            result = _worker_readback(route, lease, observed_at=clock())
            readbacks.append((route_id, slot_id, result))
            return result

    result = await worker.run_routed_worker(
        RunningWorker(),
        route=route,
        lease=lease,
        route_authority=LossAuthority(),
        temporal_readback=Readback(),
        sleep=wait_for_renewal,
        now=clock,
    )

    assert result.status == "lease_lost"
    assert result.boundary_at == shutdown_at[0]
    assert result.readback_observed_at >= result.boundary_at
    assert shutdown_called.is_set()
    assert renewal_intervals and renewal_intervals[0] <= 10
    assert shutdown_at[0] < original_expiry
    assert readbacks[0][2].poller_count == 0
    assert readbacks[0][2].active_task_count == 0
    current = authority.read_slot(route.route_id, lease.slot_id)
    assert current.state == "leased"
    assert current.lease == lease
    assert current.fencing_epoch == lease.fencing_epoch


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "renewal_mismatch",
    ["alternate_slot_owner", "fencing_epoch", "route_manifest_sha256", "artifact_lock_sha256"],
)
async def test_route_renewal_identity_mismatch_drains_under_last_trusted_lease(
    renewal_mismatch,
):
    from dataclasses import replace

    from salience.cycles.production_routing import RouteSlotReadback, route_manifest_sha256
    from salience.workflows import worker

    clock = _Clock()
    authority = _authority(clock)
    route = _publication_route()
    lease = _route_lease(authority, route)
    if renewal_mismatch == "alternate_slot_owner":
        renewed = _route_lease(
            authority, route, owner_id=_OWNER_B, slot_id=_SLOT_B
        )
    elif renewal_mismatch == "fencing_epoch":
        renewed = replace(lease, fencing_epoch=lease.fencing_epoch + 1)
    elif renewal_mismatch == "route_manifest_sha256":
        renewed = replace(lease, route_manifest_sha256="c" * 64)
    else:
        renewed = replace(lease, artifact_lock_sha256="d" * 64)

    class MismatchedRenewalAuthority:
        protocol_version = authority.protocol_version
        renewal_returned = False

        def read_slot(self, route_id, slot_id):
            if (
                self.renewal_returned
                and renewal_mismatch == "fencing_epoch"
                and slot_id == lease.slot_id
            ):
                return RouteSlotReadback(
                    route_id=route_id,
                    slot_id=slot_id,
                    available=True,
                    state="leased",
                    lease=renewed,
                    fencing_epoch=renewed.fencing_epoch,
                )
            return authority.read_slot(route_id, slot_id)

        def renew_slot(self, current):
            self.renewal_returned = True
            return renewed

        def claim_slot(self, request):
            return authority.claim_slot(request)

        def authorize_action(self, request):
            return authority.authorize_action(request)

        def release_slot(self, current):
            return authority.release_slot(current)

        def terminate_slot(self, current):
            return authority.terminate_slot(current)

    assert lease.route_manifest_sha256 == route_manifest_sha256(route)
    shutdown_called = asyncio.Event()
    worker_stopped = asyncio.Event()
    readbacks = []
    sleep_calls = 0

    class RunningWorker:
        async def run(self):
            await worker_stopped.wait()

        async def shutdown(self):
            shutdown_called.set()
            worker_stopped.set()

    class Readback:
        def read_worker(self, route_id, slot_id):
            readbacks.append((route_id, slot_id))
            observed_lease = (
                renewed
                if renewal_mismatch == "alternate_slot_owner"
                and slot_id == renewed.slot_id
                else lease
            )
            return _worker_readback(route, observed_lease, observed_at=clock())

    async def wait_for_renewal(interval):
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls > 1:
            worker_stopped.set()
            await asyncio.sleep(0)
            return
        clock.advance(timedelta(seconds=interval))

    result = await worker.run_routed_worker(
        RunningWorker(),
        route=route,
        lease=lease,
        route_authority=MismatchedRenewalAuthority(),
        temporal_readback=Readback(),
        sleep=wait_for_renewal,
        now=clock,
    )

    assert result.status == "lease_lost"
    assert shutdown_called.is_set()
    assert readbacks == [(route.route_id, lease.slot_id)]
    assert (
        result.route_id,
        result.slot_id,
        result.owner_id,
        result.fencing_epoch,
        result.route_manifest_sha256,
        result.artifact_lock_sha256,
    ) == (
        lease.route_id,
        lease.slot_id,
        lease.owner_id,
        lease.fencing_epoch,
        lease.route_manifest_sha256,
        lease.artifact_lock_sha256,
    )


@pytest.mark.asyncio
async def test_route_run_error_awaits_async_shutdown_before_readback():
    from salience.workflows import worker

    route = _publication_route()
    authority = _authority()
    lease = _route_lease(authority, route)
    shutdown_called = asyncio.Event()
    readbacks = []

    class FailedWorker:
        async def run(self):
            raise RuntimeError("synthetic worker failure")

        async def shutdown(self):
            shutdown_called.set()

    class Readback:
        def read_worker(self, route_id, slot_id):
            readbacks.append((route_id, slot_id))
            return _worker_readback(route, lease)

    async def let_worker_run(_interval):
        await asyncio.sleep(0)

    with pytest.raises(RuntimeError, match="synthetic worker failure"):
        await worker.run_routed_worker(
            FailedWorker(),
            route=route,
            lease=lease,
            route_authority=authority,
            temporal_readback=Readback(),
            sleep=let_worker_run,
        )

    assert shutdown_called.is_set()
    assert readbacks == [(route.route_id, lease.slot_id)]


@pytest.mark.asyncio
async def test_hung_route_drain_fails_closed_at_lease_expiry_without_transfer():
    from salience.workflows import worker

    clock = _Clock()
    authority = _authority(clock, lease_ttl=timedelta(milliseconds=120))
    route = _publication_route()
    lease = _route_lease(authority, route)
    loop = asyncio.get_running_loop()
    started_at = loop.time()
    shutdown_called = asyncio.Event()
    worker_cancelled = asyncio.Event()
    readbacks = []
    releases = []

    class LossAuthority:
        protocol_version = authority.protocol_version

        def read_slot(self, route_id, slot_id):
            return authority.read_slot(route_id, slot_id)

        def renew_slot(self, current):
            raise PermissionError("synthetic route renewal loss")

        def release_slot(self, current):
            releases.append(current)
            return authority.release_slot(current)

        def claim_slot(self, request):
            return authority.claim_slot(request)

        def authorize_action(self, request):
            return authority.authorize_action(request)

        def terminate_slot(self, current):
            return authority.terminate_slot(current)

    class HungWorker:
        async def run(self):
            try:
                await asyncio.sleep(0.25)
            finally:
                worker_cancelled.set()

        async def shutdown(self):
            shutdown_called.set()

    class Readback:
        def read_worker(self, route_id, slot_id):
            readbacks.append((route_id, slot_id))
            return _worker_readback(route, lease)

    def now():
        return clock() + timedelta(seconds=loop.time() - started_at)

    with pytest.raises(TimeoutError, match="lease expiry"):
        await worker.run_routed_worker(
            HungWorker(),
            route=route,
            lease=lease,
            route_authority=LossAuthority(),
            temporal_readback=Readback(),
            now=now,
        )

    await asyncio.sleep(0)
    assert shutdown_called.is_set()
    assert worker_cancelled.is_set()
    assert now() >= lease.expires_at - timedelta(milliseconds=20)
    assert now() <= lease.expires_at + timedelta(milliseconds=50)
    assert readbacks == []
    assert releases == []
    current = authority.read_slot(route.route_id, lease.slot_id)
    assert current.state == "leased"
    assert current.lease == lease
    assert current.fencing_epoch == lease.fencing_epoch


def test_duplicate_slot_owner_never_registers(monkeypatch):
    from dataclasses import replace

    from salience.cycles.production_routing import RouteLeaseClaim, route_manifest_sha256
    from salience.workflows import worker

    route = _publication_route()
    authority = _authority()
    first = _route_lease(authority, route, owner_id=_OWNER_A)
    registered = []
    monkeypatch.setattr(worker, "Worker", lambda *args, **kwargs: registered.append(kwargs))
    with pytest.raises(PermissionError, match="already claimed"):
        authority.claim_slot(
            RouteLeaseClaim(
                route_id=route.route_id,
                route_manifest_sha256=route_manifest_sha256(route),
                artifact_lock_sha256=route.artifact_lock_sha256,
                slot_id=first.slot_id,
                owner_id=_OWNER_B,
                build_id=route.worker_build_id,
            )
        )
    with pytest.raises(PermissionError, match="current lease"):
        worker.build_routed_worker(
            SimpleNamespace(
                namespace=route.route_tuple.namespace,
                route_endpoint_id=route.route_tuple.temporal_endpoint_id,
            ),
            route=route,
            lease=replace(first, owner_id=_OWNER_B),
            route_authority=authority,
            database_url="postgresql://primary/salience",
            publication_state=_publication_state(),
            permit_request_factory=_permit_factory(route, first),
        )
    assert registered == []


def test_v2_external_launcher_claims_and_hands_off_route_lease(
    monkeypatch, tmp_path
):
    import json
    from importlib.util import module_from_spec, spec_from_file_location
    import subprocess

    from salience.cycles.production_routing import canonical_route_manifest_bytes

    route = _publication_route()
    launcher_spec = spec_from_file_location(
        "salience_route_launcher_handoff",
        Path(__file__).resolve().parents[2] / "scripts/launch-v4-publication-worker.py",
    )
    launcher = module_from_spec(launcher_spec)
    launcher_spec.loader.exec_module(launcher)
    control = tmp_path / "control"
    control.mkdir()
    fake_launcher_file = control / "launch-v4-publication-worker.py"
    fake_launcher_file.write_text("# fake v2 launcher\n", encoding="utf-8")
    lock_path = control / "publication-worker-launch-lock.json"
    digest_path = control / "publication-worker-launch-lock.sha256"
    lock = {
        "schema_version": "salience.publication-worker-launch-lock.v2",
        "launcher_sha256": "a" * 64,
        "interpreter_path": "synthetic-python",
        "workers": {
            "deployable": {"module": "salience.workflows.worker", "argv": []},
            "legacy-fixture": {"module": "salience.cycles.legacy_runtime", "argv": []},
        },
        "production": {
            "schema_version": "salience.p1-production-worker-launch.v2",
            "migration_head": "0051_p1_recovery_rollback_v2",
            "rollback_verifier_abi": "salience.p1-recovery-rollback-verifier.v2",
            "qualification": "development-only",
            "workflow_allowlist": list(route.route_tuple.workflow_types),
            "activity_allowlist": list(route.route_tuple.activity_types),
            "route_bundle": {
                "schema_version": "salience.p1-production-route-bundle.v1",
                "routes": [json.loads(canonical_route_manifest_bytes(route))],
            },
        },
    }
    lock_bytes = (json.dumps(lock, sort_keys=True, separators=(",", ":")) + "\n").encode()
    lock_path.write_bytes(lock_bytes)
    digest = __import__("hashlib").sha256(lock_bytes).hexdigest()
    digest_path.write_text(digest + "\n", encoding="ascii")
    monkeypatch.setattr(launcher, "_require_isolated_startup", lambda: None)
    monkeypatch.setattr(launcher, "_EXTERNAL_PRODUCTION_TRUST_DIGEST", digest)
    monkeypatch.setattr(launcher, "CONTROL_LOCK_PATH", lock_path)
    monkeypatch.setattr(launcher, "CONTROL_LOCK_DIGEST_PATH", digest_path)
    monkeypatch.setattr(launcher, "_verify_pin", lambda _lock: tmp_path)
    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "", ""),
    )
    monkeypatch.setenv("DATABASE_URL", "postgresql://primary/salience")
    monkeypatch.chdir(tmp_path)
    authority = _authority()
    handoffs = []

    def child_exec(interpreter, command, environment):
        handoffs.append((interpreter, command, environment))

    with pytest.raises(launcher.LauncherError, match="executor returned"):
        launcher.main(
            [
                "--mode",
                "current",
                "--worker-kind",
                "deployable",
                "--route-id",
                str(route.route_id),
                "--slot-id",
                str(_SLOT_A),
                "--owner-id",
                str(_OWNER_A),
            ],
            route_authority=authority,
            route_child_exec=child_exec,
        )

    assert fake_launcher_file.read_text(encoding="utf-8").startswith("# fake v2")
    assert len(handoffs) == 1
    environment = handoffs[0][2]
    manifest_payload = json.loads(environment["SALIENCE_PRODUCTION_ROUTE_MANIFEST"])
    lease_payload = json.loads(environment["SALIENCE_PRODUCTION_ROUTE_LEASE"])
    assert manifest_payload["route_id"] == str(route.route_id)
    assert manifest_payload["route_tuple"]["namespace"] == route.route_tuple.namespace
    assert lease_payload["route_id"] == str(route.route_id)
    assert lease_payload["slot_id"] == str(_SLOT_A)
    assert lease_payload["owner_id"] == str(_OWNER_A)
    assert lease_payload["fencing_epoch"] == 1
    assert lease_payload["artifact_lock_sha256"] == digest
    assert authority.read_slot(route.route_id, _SLOT_A).lease is not None


def test_routed_publication_uses_production_effect_dispatch_with_exact_route_binding(
    monkeypatch,
):
    from salience.cycles.production_dispatch import ProductionEffectDispatch
    from salience.workflows import worker

    route = _publication_route()
    authority = _authority()
    lease = _route_lease(authority, route)
    monkeypatch.setattr(worker, "Worker", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    state = _publication_state()
    worker.build_routed_worker(
        SimpleNamespace(
            namespace=route.route_tuple.namespace,
            route_endpoint_id=route.route_tuple.temporal_endpoint_id,
            route_build_id=route.worker_build_id,
            route_artifact_sha256=route.artifact_sha256,
            route_image_sha256=route.image_sha256,
        ),
        route=route,
        lease=lease,
        route_authority=authority,
        database_url="postgresql://primary/salience",
        publication_state=state,
        permit_request_factory=_permit_factory(route, lease),
    )
    approval, publication = _approval_and_publication_binding(route)
    request = state.production_permit_request_factory(approval, publication)

    assert isinstance(state.production_dispatch, ProductionEffectDispatch)
    assert state.production_dispatch.route_fence is authority
    assert request.route.owner_id == lease.owner_id
    assert request.route.fencing_epoch == lease.fencing_epoch
    assert request.route.route_tuple_sha256 == route.route_tuple_sha256
    assert request.publication_binding == publication


def test_restart_preserves_workflow_cycle_and_operation_identity(monkeypatch):
    from salience.cycles.production_routing import RouteLeaseClaim, route_manifest_sha256
    from salience.workflows import worker

    clock = _Clock()
    authority = _authority(clock)
    route = _publication_route()
    first_lease = _route_lease(authority, route)
    approval, publication = _approval_and_publication_binding(route)
    state = _publication_state()
    monkeypatch.setattr(worker, "Worker", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    client = SimpleNamespace(
        namespace=route.route_tuple.namespace,
        route_endpoint_id=route.route_tuple.temporal_endpoint_id,
        route_build_id=route.worker_build_id,
        route_artifact_sha256=route.artifact_sha256,
        route_image_sha256=route.image_sha256,
    )
    worker.build_routed_worker(
        client,
        route=route,
        lease=first_lease,
        route_authority=authority,
        database_url="postgresql://primary/salience",
        publication_state=state,
        permit_request_factory=_permit_factory(route, first_lease),
    )
    first_request = state.production_permit_request_factory(approval, publication)
    incorrect_readback = SimpleNamespace(
        read_worker=lambda route_id, slot_id: replace(
            _worker_readback(route, first_lease, observed_at=clock()),
            namespace="wrong-namespace",
        )
    )
    with pytest.raises(PermissionError, match="readback"):
        worker.transfer_routed_worker(
            route=route,
            lease=first_lease,
            drain_receipt=_worker_drain_receipt(
                route, first_lease, boundary_at=clock()
            ),
            route_authority=authority,
            temporal_readback=incorrect_readback,
            now=clock,
        )
    assert authority.read_slot(route.route_id, first_lease.slot_id).lease == first_lease
    released = worker.transfer_routed_worker(
        route=route,
        lease=first_lease,
        drain_receipt=_worker_drain_receipt(
            route, first_lease, boundary_at=clock()
        ),
        route_authority=authority,
        temporal_readback=SimpleNamespace(
            read_worker=lambda route_id, slot_id: _worker_readback(
                route, first_lease, observed_at=clock()
            )
        ),
        now=clock,
    )
    assert released.state == "released"
    assert released.fencing_epoch == first_lease.fencing_epoch
    second_lease = authority.claim_slot(
        RouteLeaseClaim(
            route_id=route.route_id,
            route_manifest_sha256=route_manifest_sha256(route),
            artifact_lock_sha256=route.artifact_lock_sha256,
            slot_id=first_lease.slot_id,
            owner_id=first_lease.owner_id,
            build_id=route.worker_build_id,
        )
    )
    worker.build_routed_worker(
        client,
        route=route,
        lease=second_lease,
        route_authority=authority,
        database_url="postgresql://primary/salience",
        publication_state=state,
        permit_request_factory=_permit_factory(route, second_lease),
    )
    restarted_request = state.production_permit_request_factory(approval, publication)

    assert second_lease.fencing_epoch == first_lease.fencing_epoch + 1
    assert restarted_request.cycle_id == first_request.cycle_id == approval.cycle_id
    assert restarted_request.operation_id == first_request.operation_id == approval.operation_id
    assert restarted_request.publication_binding.workflow_id == publication.workflow_id
    assert restarted_request.publication_binding.workflow_run_id == publication.workflow_run_id
    assert restarted_request.route.fencing_epoch == second_lease.fencing_epoch


def test_launcher_v2_rejects_caller_supplied_hold_lists(tmp_path, monkeypatch):
    from importlib.util import module_from_spec, spec_from_file_location

    source = Path(__file__).resolve().parents[2] / "scripts/launch-v4-publication-worker.py"
    spec = spec_from_file_location("salience_launcher_v2_reject_hold_ids", source)
    launcher = module_from_spec(spec)
    spec.loader.exec_module(launcher)
    _configure_v2_rollback_launcher_main(launcher, tmp_path, monkeypatch)
    # The options remain available to the immutable v1 fixture launcher, while
    # v2 workspace scope rejects them before verifier or prior-code callbacks.
    option_strings = {
        option for action in launcher._parser()._actions for option in action.option_strings
    }
    assert "--expired-hold-operation-id" in option_strings
    assert "--cancel-hold-operation-id" in option_strings
    workspace_id = UUID("00000000-0000-0000-0000-000000000901")
    verifier_calls = []
    process_calls = []
    with pytest.raises(launcher.LauncherError, match="caller hold IDs"):
        launcher._validate_rollback_v2_scope(
            str(workspace_id),
            "00000000-0000-0000-0000-000000000902",
            "00000000-0000-0000-0000-000000000903",
            (None, None, None),
        )
    assert verifier_calls == []
    assert process_calls == []

    with pytest.raises(launcher.LauncherError, match="caller hold IDs"):
        launcher.main(
            [
                "--mode",
                "rollback-restart",
                "--worker-kind",
                "legacy-fixture",
                "--workspace-id",
                str(workspace_id),
                "--expired-hold-operation-id",
                "00000000-0000-0000-0000-000000000902",
                "--cancel-hold-operation-id",
                "00000000-0000-0000-0000-000000000903",
            ],
            route_authority=object(),
            temporal_readback=object(),
            rollback_verifier=lambda _scope: verifier_calls.append(True),
            prior_code_process=lambda: process_calls.append(True),
        )
    assert verifier_calls == []
    assert process_calls == []


def _configure_v2_rollback_launcher_main(launcher, tmp_path, monkeypatch):
    import hashlib
    import json

    control = tmp_path / "control-v2"
    control.mkdir()
    lock_path = control / "publication-worker-launch-lock.json"
    digest_path = control / "publication-worker-launch-lock.sha256"
    lock = {
        "schema_version": "salience.publication-worker-launch-lock.v2",
        "launcher_sha256": "a" * 64,
        "interpreter_path": "synthetic-python",
        "interpreter_sha256": "b" * 64,
        "runtime_root": str(tmp_path),
        "workers": {
            "deployable": {"module": "salience.workflows.worker", "argv": []},
            "legacy-fixture": {"module": "salience.cycles.legacy_runtime", "argv": []},
        },
        "production": {
            "schema_version": "salience.p1-production-worker-launch.v2",
            "migration_head": "0051_p1_recovery_rollback_v2",
            "rollback_verifier_abi": "salience.p1-recovery-rollback-verifier.v2",
            "qualification": "development-only",
            "workflow_allowlist": [],
            "activity_allowlist": [],
            "route_bundle": {},
        },
    }
    raw = (json.dumps(lock, sort_keys=True, separators=(",", ":")) + "\n").encode()
    digest = hashlib.sha256(raw).hexdigest()
    lock_path.write_bytes(raw)
    digest_path.write_text(digest + "\n", encoding="ascii")
    monkeypatch.setattr(launcher, "_require_isolated_startup", lambda: None)
    monkeypatch.setattr(launcher, "CONTROL_LOCK_PATH", lock_path)
    monkeypatch.setattr(launcher, "CONTROL_LOCK_DIGEST_PATH", digest_path)
    monkeypatch.setattr(launcher, "_verify_pin", lambda _lock: tmp_path)
    monkeypatch.setenv("DATABASE_URL", "postgresql://primary/salience")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://primary/salience")
    monkeypatch.chdir(tmp_path)
    return digest


def test_launcher_v2_runs_pre_and_post_readback_before_prior_code(tmp_path, monkeypatch):
    from importlib.util import module_from_spec, spec_from_file_location

    source = Path(__file__).resolve().parents[2] / "scripts/launch-v4-publication-worker.py"
    spec = spec_from_file_location("salience_launcher_v2_pre_post", source)
    launcher = module_from_spec(spec)
    spec.loader.exec_module(launcher)
    _configure_v2_rollback_launcher_main(launcher, tmp_path, monkeypatch)
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://unrelated/test-only")
    workspace_id = UUID("00000000-0000-0000-0000-000000000904")
    digest = "d" * 64
    calls = []

    def verifier(scope):
        calls.append(("verify", scope))
        return SimpleNamespace(
            workspace_id=scope,
            migration_head="0051_p1_recovery_rollback_v2",
            digest=digest,
            readback_digest="f" * 64,
        )

    def deployable_executor(execution):
        assert execution.workspace_id == workspace_id
        assert execution.command[0] == execution.interpreter_path
        calls.append(("prior", None))
        return 0

    result = launcher.main(
        [
            "--mode",
            "rollback-restart",
            "--worker-kind",
            "deployable",
            "--workspace-id",
            str(workspace_id),
        ],
        route_authority=object(),
        temporal_readback=object(),
        rollback_verifier=verifier,
        deployable_executor=deployable_executor,
    )
    assert result == 0
    assert calls == [
        ("verify", workspace_id),
        ("prior", None),
        ("verify", workspace_id),
    ]

    changed = iter((digest, "e" * 64))

    def changed_verifier(scope):
        return SimpleNamespace(
            workspace_id=scope,
            migration_head="0051_p1_recovery_rollback_v2",
            digest=next(changed),
            readback_digest="f" * 64,
        )

    execution = launcher.AuthenticatedDeployableExecution(
        interpreter_path="synthetic-python",
        command=(
            "synthetic-python",
            "-I",
            "-B",
            "-S",
            "-c",
            launcher._WORKER_BOOTSTRAP,
            str(tmp_path),
            "salience.workflows.worker",
        ),
        environment={
            "DATABASE_URL": "postgresql://primary/salience",
            "SALIENCE_PUBLICATION_WORKER_START_MODE": "rollback-restart-v2",
            "SALIENCE_PUBLICATION_ROLLBACK_WORKSPACE_ID": str(workspace_id),
            "SALIENCE_PUBLICATION_WORKER_LAUNCH_LOCK_SHA256": "f" * 64,
        },
        runtime_root=str(tmp_path),
        workspace_id=workspace_id,
        launch_lock_sha256="f" * 64,
    )

    with pytest.raises(launcher.LauncherError, match="inventory or readback changed"):
        launcher._run_rollback_restart_v2(
            workspace_id,
            verifier=changed_verifier,
            deployable_executor=lambda _execution: 0,
            execution=execution,
        )

    changed_readbacks = iter(("a" * 64, "b" * 64))

    def changed_readback_verifier(scope):
        return SimpleNamespace(
            workspace_id=scope,
            migration_head="0051_p1_recovery_rollback_v2",
            digest=digest,
            readback_digest=next(changed_readbacks),
        )

    with pytest.raises(launcher.LauncherError, match="inventory or readback changed"):
        launcher._run_rollback_restart_v2(
            workspace_id,
            verifier=changed_readback_verifier,
            deployable_executor=lambda _execution: 0,
            execution=execution,
        )


def test_rollback_v2_hands_off_authenticated_deployable_only_after_verification(
    tmp_path, monkeypatch
):
    import builtins
    from importlib.util import module_from_spec, spec_from_file_location

    source = Path(__file__).resolve().parents[2] / "scripts/launch-v4-publication-worker.py"
    spec = spec_from_file_location("salience_launcher_v2_authenticated_handoff", source)
    launcher = module_from_spec(spec)
    spec.loader.exec_module(launcher)
    _configure_v2_rollback_launcher_main(launcher, tmp_path, monkeypatch)
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://unrelated/test-only")
    workspace_id = UUID("00000000-0000-0000-0000-000000000905")
    calls = []
    original_import = builtins.__import__

    def tracked_import(name, globals=None, locals=None, fromlist=(), level=0):
        prior_worker = name in {
            "salience.workflows.worker",
            "salience.cycles.legacy_runtime",
            "salience.cycles.legacy_publication_runtime",
        }
        if prior_worker:
            assert "verify" in calls, "prior worker module import happened before preflight"
            calls.append(("worker_import", name))
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", tracked_import)

    def verifier(scope):
        calls.append("verify")
        return SimpleNamespace(
            workspace_id=scope,
            migration_head="0051_p1_recovery_rollback_v2",
            digest="d" * 64,
            readback_digest="e" * 64,
        )

    def deployable_executor(handoff):
        calls.append("handoff")
        assert handoff.interpreter_path == "synthetic-python"
        assert handoff.command[0:2] == ("synthetic-python", "-I")
        assert "salience.workflows.worker" in handoff.command
        assert "--route-id" not in handoff.command
        assert "--route-id" not in handoff.environment
        assert handoff.environment["SALIENCE_PUBLICATION_ROLLBACK_WORKSPACE_ID"] == str(
            workspace_id
        )
        assert "TEST_DATABASE_URL" not in handoff.environment
        __import__("salience.workflows.worker")
        return 0

    result = launcher.main(
        [
            "--mode",
            "rollback-restart",
            "--worker-kind",
            "deployable",
            "--workspace-id",
            str(workspace_id),
        ],
        route_authority=object(),
        temporal_readback=object(),
        rollback_verifier=verifier,
        deployable_executor=deployable_executor,
    )

    assert result == 0
    assert [call if isinstance(call, str) else call[0] for call in calls] == [
        "verify",
        "handoff",
        "worker_import",
        "verify",
    ]


def test_rollback_v2_fails_closed_without_authenticated_deployable_executor(
    tmp_path, monkeypatch
):
    from importlib.util import module_from_spec, spec_from_file_location

    source = Path(__file__).resolve().parents[2] / "scripts/launch-v4-publication-worker.py"
    spec = spec_from_file_location("salience_launcher_v2_missing_handoff", source)
    launcher = module_from_spec(spec)
    spec.loader.exec_module(launcher)
    _configure_v2_rollback_launcher_main(launcher, tmp_path, monkeypatch)
    calls = []

    with pytest.raises(launcher.LauncherError, match="authenticated deployable executor"):
        launcher.main(
            [
                "--mode",
                "rollback-restart",
                "--worker-kind",
                "deployable",
                "--workspace-id",
                str(UUID("00000000-0000-0000-0000-000000000906")),
            ],
            route_authority=object(),
            temporal_readback=object(),
            rollback_verifier=lambda _scope: calls.append("verify"),
        )
    assert calls == []
