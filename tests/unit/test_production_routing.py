from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from uuid import UUID

import pytest

from salience.cycles.production_dispatch import (
    ProductionAuthorizationDenied,
    ProductionEffectDispatch,
)
from salience.cycles.production_routing import (
    RouteTupleV1,
    canonical_route_manifest_bytes,
    canonical_route_tuple_bytes,
    parse_route_bundle,
    parse_route_manifest,
    route_manifest_sha256,
    route_tuple_sha256,
)


LOCK_SHA256 = "f" * 64
ROUTE_ID = UUID("00000000-0000-0000-0000-000000000020")
SLOT_A = UUID("00000000-0000-0000-0000-000000000030")
SLOT_B = UUID("00000000-0000-0000-0000-000000000031")


def _route_tuple(
    *,
    task_queue: str = "publication-prod",
    workflow_types: tuple[str, ...] = ("ZWorkflow", "AWorkflow"),
    producer_bindings: tuple[dict[str, str], ...] | None = None,
) -> RouteTupleV1:
    return RouteTupleV1(
        deployment_id=UUID("00000000-0000-0000-0000-000000000010"),
        environment_id=UUID("00000000-0000-0000-0000-000000000011"),
        temporal_endpoint_id="temporal-prod-cluster",
        namespace="publication-prod",
        task_queue=task_queue,
        worker_kind="publication",
        workflow_types=workflow_types,
        activity_types=("ZActivity", "AActivity"),
        producer_bindings=producer_bindings
        if producer_bindings is not None
        else (
            {"source": "jobs", "producer": "publication"},
            {"producer": "admission", "source": "cycles"},
        ),
        compatibility_mode="pinned-build-id",
    )


def _tuple_payload(value: RouteTupleV1) -> dict[str, object]:
    return {
        "deployment_id": str(value.deployment_id),
        "environment_id": str(value.environment_id),
        "temporal_endpoint_id": value.temporal_endpoint_id,
        "namespace": value.namespace,
        "task_queue": value.task_queue,
        "worker_kind": value.worker_kind,
        "workflow_types": list(value.workflow_types),
        "activity_types": list(value.activity_types),
        "producer_bindings": [dict(binding) for binding in value.producer_bindings],
        "compatibility_mode": value.compatibility_mode,
    }


def _manifest_payload(
    *,
    route_id: UUID = ROUTE_ID,
    route_tuple: RouteTupleV1 | None = None,
    slots: list[UUID] | None = None,
) -> dict[str, object]:
    value = route_tuple or _route_tuple()
    return {
        "schema_version": "salience.p1-production-route-manifest.v1",
        "route_id": str(route_id),
        "route_tuple": _tuple_payload(value),
        "route_tuple_sha256": route_tuple_sha256(value),
        "worker_build_id": "salience-publication-build-2026-10-28",
        "slots": [str(slot) for slot in (slots if slots is not None else [SLOT_B, SLOT_A])],
        "artifact_sha256": "a" * 64,
        "image_sha256": "b" * 64,
    }


def test_route_id_is_stable_uuid_and_tuple_digest_is_canonical() -> None:
    first = _route_tuple()
    reordered = _route_tuple(
        workflow_types=("AWorkflow", "ZWorkflow"),
        producer_bindings=(
            {"source": "cycles", "producer": "admission"},
            {"producer": "publication", "source": "jobs"},
        ),
    )

    expected = (
        b'{"activity_types":["AActivity","ZActivity"],'
        b'"compatibility_mode":"pinned-build-id",'
        b'"deployment_id":"00000000-0000-0000-0000-000000000010",'
        b'"environment_id":"00000000-0000-0000-0000-000000000011",'
        b'"namespace":"publication-prod",'
        b'"producer_bindings":[{"producer":"admission","source":"cycles"},'
        b'{"producer":"publication","source":"jobs"}],'
        b'"task_queue":"publication-prod",'
        b'"temporal_endpoint_id":"temporal-prod-cluster",'
        b'"worker_kind":"publication",'
        b'"workflow_types":["AWorkflow","ZWorkflow"]}\n'
    )
    assert canonical_route_tuple_bytes(first) == expected
    assert canonical_route_tuple_bytes(reordered) == expected
    assert route_tuple_sha256(first) == route_tuple_sha256(reordered)
    assert route_tuple_sha256(first) == sha256(expected).hexdigest()

    assert route_tuple_sha256(_route_tuple(task_queue="alternate-queue")) != route_tuple_sha256(first)
    assert route_tuple_sha256(
        _route_tuple(producer_bindings=({"producer": "other", "source": "jobs"},))
    ) != route_tuple_sha256(first)
    assert route_tuple_sha256(
        _route_tuple(workflow_types=("AWorkflow", "DifferentWorkflow"))
    ) != route_tuple_sha256(first)

    manifest = parse_route_manifest(_manifest_payload(), expected_lock_sha256=LOCK_SHA256)
    assert manifest.route_id == UUID("00000000-0000-0000-0000-000000000020")
    assert manifest.route_id != UUID(route_tuple_sha256(first)[:32])
    assert manifest.route_tuple_sha256 == route_tuple_sha256(first)


def test_route_manifest_digest_authenticates_route_uuid_and_tuple_hash() -> None:
    original = parse_route_manifest(_manifest_payload(), expected_lock_sha256=LOCK_SHA256)
    changed_route_id = parse_route_manifest(
        _manifest_payload(route_id=UUID("00000000-0000-0000-0000-000000000021")),
        expected_lock_sha256=LOCK_SHA256,
    )
    changed_tuple = parse_route_manifest(
        _manifest_payload(route_tuple=_route_tuple(task_queue="publication-canary")),
        expected_lock_sha256=LOCK_SHA256,
    )

    assert original.route_tuple_sha256 == changed_route_id.route_tuple_sha256
    assert original.route_id != changed_route_id.route_id
    assert route_manifest_sha256(original) != route_manifest_sha256(changed_route_id)
    assert original.route_id == changed_tuple.route_id
    assert original.route_tuple_sha256 != changed_tuple.route_tuple_sha256
    assert route_manifest_sha256(original) != route_manifest_sha256(changed_tuple)

    encoded = canonical_route_manifest_bytes(original)
    assert b'"route_id":"00000000-0000-0000-0000-000000000020"' in encoded
    assert f'"route_tuple_sha256":"{original.route_tuple_sha256}"'.encode() in encoded
    assert b'"artifact_lock_sha256"' not in encoded
    assert b'"route_manifest_sha256"' not in encoded
    assert (
        b'"slots":["00000000-0000-0000-0000-000000000030",'
        b'"00000000-0000-0000-0000-000000000031"]'
    ) in encoded


def test_route_manifest_rejects_incomplete_duplicate_or_mismatched_entries() -> None:
    valid = _manifest_payload()
    bundle = {
        "schema_version": "salience.p1-production-route-bundle.v1",
        "routes": [valid],
    }
    assert len(parse_route_bundle(bundle, expected_lock_sha256=LOCK_SHA256)) == 1
    with pytest.raises(ValueError):
        parse_route_bundle(
            {"schema_version": "salience.p1-production-route-bundle.v1", "routes": []},
            expected_lock_sha256=LOCK_SHA256,
        )
    with pytest.raises(ValueError):
        parse_route_bundle(
            {"schema_version": "salience.p1-production-route-bundle.v1", "routes": [valid, valid]},
            expected_lock_sha256=LOCK_SHA256,
        )

    invalid_manifests: list[dict[str, object]] = []
    missing_slot = deepcopy(valid)
    missing_slot["slots"] = []
    invalid_manifests.append(missing_slot)
    duplicate_slot = deepcopy(valid)
    duplicate_slot["slots"] = [str(SLOT_A), str(SLOT_A)]
    invalid_manifests.append(duplicate_slot)
    duplicate_type = deepcopy(valid)
    duplicate_type["route_tuple"]["workflow_types"] = ["AWorkflow", "AWorkflow"]
    invalid_manifests.append(duplicate_type)
    duplicate_binding = deepcopy(valid)
    duplicate_binding["route_tuple"]["producer_bindings"] = [
        {"source": "jobs", "producer": "publication"},
        {"producer": "publication", "source": "jobs"},
    ]
    invalid_manifests.append(duplicate_binding)
    mismatched_tuple_hash = deepcopy(valid)
    mismatched_tuple_hash["route_tuple_sha256"] = "c" * 64
    invalid_manifests.append(mismatched_tuple_hash)
    malformed_digest = deepcopy(valid)
    malformed_digest["artifact_sha256"] = "A" * 64
    invalid_manifests.append(malformed_digest)
    malformed_route_id = deepcopy(valid)
    malformed_route_id["route_id"] = "not-a-uuid"
    invalid_manifests.append(malformed_route_id)
    unknown_field = deepcopy(valid)
    unknown_field["extra"] = "forbidden"
    invalid_manifests.append(unknown_field)
    caller_selected_queue = deepcopy(valid)
    caller_selected_queue["task_queue"] = "caller-choice"
    invalid_manifests.append(caller_selected_queue)
    caller_queue_override = deepcopy(valid)
    caller_queue_override["queue"] = "caller-choice"
    invalid_manifests.append(caller_queue_override)

    for payload in invalid_manifests:
        with pytest.raises(ValueError):
            parse_route_manifest(payload, expected_lock_sha256=LOCK_SHA256)

    with pytest.raises(ValueError):
        parse_route_manifest(valid, expected_lock_sha256="invalid-lock-digest")
    assert (
        parse_route_manifest(valid, expected_lock_sha256="e" * 64).artifact_lock_sha256
        == "e" * 64
    )
    with pytest.raises(ValueError):
        parse_route_bundle(
            {"schema_version": "wrong-version", "routes": [valid]},
            expected_lock_sha256=LOCK_SHA256,
        )
    with pytest.raises(ValueError):
        parse_route_bundle(
            {"schema_version": "salience.p1-production-route-bundle.v1", "routes": [valid], "queue": "caller"},
            expected_lock_sha256=LOCK_SHA256,
        )


def test_v1_permit_snapshot_remains_readable_without_tuple_hash() -> None:
    snapshot: dict[str, object] = {
        "schema_version": "salience.p1-production-effect-permit.v1",
        "generation": 1,
        "workspace_id": "00000000-0000-0000-0000-000000000040",
        "goal_id": "00000000-0000-0000-0000-000000000041",
        "goal_revision": 1,
        "program_id": "00000000-0000-0000-0000-000000000042",
        "intent_id": "00000000-0000-0000-0000-000000000043",
        "cycle_id": "00000000-0000-0000-0000-000000000044",
        "context_id": "00000000-0000-0000-0000-000000000045",
        "operation_id": "00000000-0000-0000-0000-000000000046",
        "subject_id": "00000000-0000-0000-0000-000000000047",
        "required_scopes": ["effects:write"],
        "policy_version_id": "00000000-0000-0000-0000-000000000048",
        "approval_request_id": "00000000-0000-0000-0000-000000000049",
        "effect_type": "write",
        "purpose": "approved production action",
        "artifact_sha256": "a" * 64,
        "account_id": "00000000-0000-0000-0000-000000000050",
        "budget_id": "00000000-0000-0000-0000-000000000051",
        "budget_reservation_id": "00000000-0000-0000-0000-000000000052",
        "provider_id": "provider-prod",
        "idempotency_key": "operation-46",
        "execution_deadline": "2030-01-01T00:00:00+00:00",
        "ttl_seconds": 30,
        "deployment_id": "00000000-0000-0000-0000-000000000010",
        "environment_id": "00000000-0000-0000-0000-000000000011",
        "route_id": str(ROUTE_ID),
        "slot_id": str(SLOT_A),
        "owner_id": "00000000-0000-0000-0000-000000000053",
        "fencing_epoch": 7,
        "route_manifest_sha256": "c" * 64,
        "artifact_lock_sha256": LOCK_SHA256,
    }
    request = ProductionEffectDispatch._request_from_snapshot(snapshot)
    assert request.route.route_id == ROUTE_ID
    assert request.route.route_tuple_sha256 is None

    class RouteFence:
        def authorize_action(self, request):  # pragma: no cover - must not be called
            raise AssertionError("legacy route evidence cannot reach route authorization")

    dispatch = ProductionEffectDispatch("postgresql://unused", RouteFence())
    with pytest.raises(ProductionAuthorizationDenied, match="invalid_binding"):
        dispatch.issue_permit(request)
