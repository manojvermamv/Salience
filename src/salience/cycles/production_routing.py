"""Canonical production route tuple and manifest evidence.

The route manifest is parsed only after its containing launcher lock has been
authenticated. The outer lock digest is carried in the resulting value, but is
not embedded in or self-referenced by the manifest bytes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
import re
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, Protocol, cast, runtime_checkable
from uuid import UUID

if TYPE_CHECKING:
    from salience.cycles.production_dispatch import RouteFenceDecision, RouteFenceRequest


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_ROUTE_MANIFEST_VERSION = "salience.p1-production-route-manifest.v1"
_ROUTE_BUNDLE_VERSION = "salience.p1-production-route-bundle.v1"
ROUTE_FENCE_AUTHORITY_VERSION: Literal["salience.p1-route-fence.v1"] = (
    "salience.p1-route-fence.v1"
)
_ROUTE_TUPLE_FIELDS = frozenset(
    {
        "deployment_id",
        "environment_id",
        "temporal_endpoint_id",
        "namespace",
        "task_queue",
        "worker_kind",
        "workflow_types",
        "activity_types",
        "producer_bindings",
        "compatibility_mode",
    }
)
_ROUTE_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "route_id",
        "route_tuple",
        "route_tuple_sha256",
        "worker_build_id",
        "slots",
        "artifact_sha256",
        "image_sha256",
    }
)


def _require_digest(value: object, name: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{name} must be lowercase SHA-256")
    return value


def _require_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


@dataclass(frozen=True, slots=True)
class RouteLeaseClaim:
    route_id: UUID
    route_manifest_sha256: str
    artifact_lock_sha256: str
    slot_id: UUID
    owner_id: UUID
    build_id: str

    def __post_init__(self) -> None:
        for name in ("route_id", "slot_id", "owner_id"):
            if not isinstance(getattr(self, name), UUID):
                raise ValueError(f"route lease claim {name} UUID required")
        _require_digest(self.route_manifest_sha256, "route manifest digest")
        _require_digest(self.artifact_lock_sha256, "artifact lock digest")
        _require_text(self.build_id, "route worker build ID")


@dataclass(frozen=True, slots=True)
class RouteLease:
    route_id: UUID
    slot_id: UUID
    owner_id: UUID
    fencing_epoch: int
    expires_at: datetime
    route_manifest_sha256: str
    artifact_lock_sha256: str

    def __post_init__(self) -> None:
        for name in ("route_id", "slot_id", "owner_id"):
            if not isinstance(getattr(self, name), UUID):
                raise ValueError(f"route lease {name} UUID required")
        if type(self.fencing_epoch) is not int or self.fencing_epoch < 1:
            raise ValueError("positive route fencing epoch required")
        if (
            not isinstance(self.expires_at, datetime)
            or self.expires_at.tzinfo is None
            or self.expires_at.utcoffset() is None
        ):
            raise ValueError("route lease expiry must be timezone-aware")
        _require_digest(self.route_manifest_sha256, "route manifest digest")
        _require_digest(self.artifact_lock_sha256, "artifact lock digest")


@dataclass(frozen=True, slots=True)
class RouteSlotReadback:
    route_id: UUID
    slot_id: UUID
    available: bool
    state: Literal[
        "missing", "leased", "released", "terminated", "expired", "unavailable"
    ]
    lease: RouteLease | None
    fencing_epoch: int | None
    protocol_version: Literal["salience.p1-route-fence.v1"] = (
        ROUTE_FENCE_AUTHORITY_VERSION
    )

    def __post_init__(self) -> None:
        if not isinstance(self.route_id, UUID) or not isinstance(self.slot_id, UUID):
            raise ValueError("route slot readback UUIDs are required")
        if type(self.available) is not bool:
            raise ValueError("route slot readback availability must be explicit")
        if self.state not in {
            "missing",
            "leased",
            "released",
            "terminated",
            "expired",
            "unavailable",
        }:
            raise ValueError("route slot readback state is invalid")
        if self.protocol_version != ROUTE_FENCE_AUTHORITY_VERSION:
            raise ValueError("route slot readback version is unsupported")
        if self.state == "unavailable":
            if self.available or self.lease is not None or self.fencing_epoch is not None:
                raise ValueError("unavailable route readback cannot expose cached lease state")
            return
        if not self.available:
            raise ValueError("available route readback state is required")
        if self.state == "missing":
            if self.lease is not None or self.fencing_epoch is not None:
                raise ValueError("missing route slot cannot carry lease state")
            return
        if type(self.fencing_epoch) is not int or self.fencing_epoch < 1:
            raise ValueError("route slot readback needs a positive fencing epoch")
        if self.state == "leased":
            if (
                not isinstance(self.lease, RouteLease)
                or self.lease.route_id != self.route_id
                or self.lease.slot_id != self.slot_id
                or self.lease.fencing_epoch != self.fencing_epoch
            ):
                raise ValueError("leased route slot readback needs its current lease")
        elif self.lease is not None:
            raise ValueError("inactive route slot readback cannot carry a current lease")


@runtime_checkable
class RouteLeaseAuthority(Protocol):
    """Versioned provider-neutral slot lease and action-fence boundary.

    All methods are implemented against one ``salience.p1-route-fence.v1``
    serialization order. Action requests and decisions use the existing
    ``production_dispatch`` contract, imported for type checking only so this
    module does not create a runtime dependency cycle.
    """

    protocol_version: Literal["salience.p1-route-fence.v1"]

    def claim_slot(self, request: RouteLeaseClaim) -> RouteLease: ...

    def renew_slot(self, lease: RouteLease) -> RouteLease: ...

    def authorize_action(self, request: RouteFenceRequest) -> RouteFenceDecision: ...

    def release_slot(self, lease: RouteLease) -> RouteSlotReadback: ...

    def terminate_slot(self, lease: RouteLease) -> RouteSlotReadback: ...

    def read_slot(self, route_id: UUID, slot_id: UUID) -> RouteSlotReadback: ...


def _parse_uuid(value: object, name: str) -> UUID:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a canonical UUID string")
    try:
        parsed = UUID(value)
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a canonical UUID string") from error
    if str(parsed) != value:
        raise ValueError(f"{name} must be a canonical UUID string")
    return parsed


def _canonical_json_bytes(value: object) -> bytes:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError("route evidence must be canonical JSON data") from error
    return encoded


def _canonical_document_bytes(value: object) -> bytes:
    return _canonical_json_bytes(value) + b"\n"


@dataclass(frozen=True, slots=True)
class RouteTupleV1:
    deployment_id: UUID
    environment_id: UUID
    temporal_endpoint_id: str
    namespace: str
    task_queue: str
    worker_kind: str
    workflow_types: tuple[str, ...]
    activity_types: tuple[str, ...]
    producer_bindings: tuple[Mapping[str, str], ...]
    compatibility_mode: str

    def __post_init__(self) -> None:
        for field_name in ("deployment_id", "environment_id"):
            if not isinstance(getattr(self, field_name), UUID):
                raise ValueError(f"route tuple {field_name} UUID required")
        for field_name in (
            "temporal_endpoint_id",
            "namespace",
            "task_queue",
            "worker_kind",
            "compatibility_mode",
        ):
            _require_text(getattr(self, field_name), field_name)
        for field_name in ("workflow_types", "activity_types"):
            values = getattr(self, field_name)
            if not isinstance(values, tuple) or any(
                not isinstance(item, str) or not item.strip() for item in values
            ):
                raise ValueError(f"route tuple {field_name} must be a tuple of names")
            if len(values) != len(set(values)):
                raise ValueError(f"duplicate route tuple {field_name} entry")
        if not isinstance(self.producer_bindings, tuple):
            raise ValueError("route tuple producer_bindings must be a tuple")
        normalized: list[Mapping[str, str]] = []
        for binding in self.producer_bindings:
            if not isinstance(binding, Mapping):
                raise ValueError("route producer binding must be a mapping")
            copied: dict[str, str] = {}
            for key, value in binding.items():
                if not isinstance(key, str) or not key.strip():
                    raise ValueError("route producer binding keys must be non-empty strings")
                if not isinstance(value, str) or not value.strip():
                    raise ValueError("route producer binding values must be non-empty strings")
                copied[key] = value
            normalized.append(MappingProxyType(copied))
        binding_bytes = [_canonical_json_bytes(dict(binding)) for binding in normalized]
        if len(binding_bytes) != len(set(binding_bytes)):
            raise ValueError("duplicate route producer binding")
        object.__setattr__(self, "producer_bindings", tuple(normalized))


def _route_tuple_payload(value: RouteTupleV1) -> dict[str, object]:
    if not isinstance(value, RouteTupleV1):
        raise ValueError("RouteTupleV1 required")
    return {
        "deployment_id": str(value.deployment_id),
        "environment_id": str(value.environment_id),
        "temporal_endpoint_id": value.temporal_endpoint_id,
        "namespace": value.namespace,
        "task_queue": value.task_queue,
        "worker_kind": value.worker_kind,
        "workflow_types": sorted(value.workflow_types),
        "activity_types": sorted(value.activity_types),
        "producer_bindings": [
            json.loads(binding_bytes.decode("utf-8"))
            for binding_bytes in sorted(
                _canonical_json_bytes(dict(binding)) for binding in value.producer_bindings
            )
        ],
        "compatibility_mode": value.compatibility_mode,
    }


def canonical_route_tuple_bytes(value: RouteTupleV1) -> bytes:
    """Serialize only the route's canonical execution tuple."""

    return _canonical_document_bytes(_route_tuple_payload(value))


def route_tuple_sha256(value: RouteTupleV1) -> str:
    return sha256(canonical_route_tuple_bytes(value)).hexdigest()


@dataclass(frozen=True, slots=True)
class ProductionRouteManifestV1:
    route_id: UUID
    route_tuple: RouteTupleV1
    route_tuple_sha256: str
    worker_build_id: str
    slots: tuple[UUID, ...]
    artifact_sha256: str
    image_sha256: str
    artifact_lock_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.route_id, UUID):
            raise ValueError("production route ID UUID required")
        if not isinstance(self.route_tuple, RouteTupleV1):
            raise ValueError("production route tuple required")
        _require_digest(self.route_tuple_sha256, "route tuple digest")
        if self.route_tuple_sha256 != route_tuple_sha256(self.route_tuple):
            raise ValueError("route tuple digest mismatch")
        _require_text(self.worker_build_id, "worker build ID")
        if not isinstance(self.slots, tuple) or not self.slots:
            raise ValueError("at least one route slot is required")
        if any(not isinstance(slot, UUID) for slot in self.slots):
            raise ValueError("route slot UUIDs are required")
        if len(self.slots) != len(set(self.slots)):
            raise ValueError("duplicate route slot")
        _require_digest(self.artifact_sha256, "artifact digest")
        _require_digest(self.image_sha256, "image digest")
        _require_digest(self.artifact_lock_sha256, "artifact lock digest")


def _route_manifest_payload(value: ProductionRouteManifestV1) -> dict[str, object]:
    if not isinstance(value, ProductionRouteManifestV1):
        raise ValueError("ProductionRouteManifestV1 required")
    return {
        "schema_version": _ROUTE_MANIFEST_VERSION,
        "route_id": str(value.route_id),
        "route_tuple": _route_tuple_payload(value.route_tuple),
        "route_tuple_sha256": value.route_tuple_sha256,
        "worker_build_id": value.worker_build_id,
        "slots": sorted(str(slot) for slot in value.slots),
        "artifact_sha256": value.artifact_sha256,
        "image_sha256": value.image_sha256,
    }


def canonical_route_manifest_bytes(value: ProductionRouteManifestV1) -> bytes:
    """Serialize the authenticated manifest without its outer lock digest."""

    return _canonical_document_bytes(_route_manifest_payload(value))


def route_manifest_sha256(value: ProductionRouteManifestV1) -> str:
    return sha256(canonical_route_manifest_bytes(value)).hexdigest()


def _parse_route_tuple(payload: object) -> RouteTupleV1:
    if not isinstance(payload, Mapping) or frozenset(payload) != _ROUTE_TUPLE_FIELDS:
        raise ValueError("route tuple has missing or unknown fields")
    workflow_types = payload["workflow_types"]
    activity_types = payload["activity_types"]
    producer_bindings = payload["producer_bindings"]
    if not isinstance(workflow_types, list) or not isinstance(activity_types, list):
        raise ValueError("route workflow and activity types must be arrays")
    if not isinstance(producer_bindings, list):
        raise ValueError("route producer bindings must be an array")
    if any(not isinstance(binding, Mapping) for binding in producer_bindings):
        raise ValueError("route producer bindings must be mappings")
    return RouteTupleV1(
        deployment_id=_parse_uuid(payload["deployment_id"], "deployment_id"),
        environment_id=_parse_uuid(payload["environment_id"], "environment_id"),
        temporal_endpoint_id=_require_text(
            payload["temporal_endpoint_id"], "temporal_endpoint_id"
        ),
        namespace=_require_text(payload["namespace"], "namespace"),
        task_queue=_require_text(payload["task_queue"], "task_queue"),
        worker_kind=_require_text(payload["worker_kind"], "worker_kind"),
        workflow_types=tuple(workflow_types),
        activity_types=tuple(activity_types),
        producer_bindings=tuple(cast(Mapping[str, str], item) for item in producer_bindings),
        compatibility_mode=_require_text(
            payload["compatibility_mode"], "compatibility_mode"
        ),
    )


def parse_route_manifest(
    payload: Mapping[str, object], *, expected_lock_sha256: str
) -> ProductionRouteManifestV1:
    """Parse a strict manifest after its containing lock has been authenticated."""

    if not isinstance(payload, Mapping) or frozenset(payload) != _ROUTE_MANIFEST_FIELDS:
        raise ValueError("route manifest has missing or unknown fields")
    if payload["schema_version"] != _ROUTE_MANIFEST_VERSION:
        raise ValueError("unsupported route manifest schema version")
    expected_lock = _require_digest(expected_lock_sha256, "expected artifact lock digest")
    route_tuple = _parse_route_tuple(payload["route_tuple"])
    tuple_digest = _require_digest(payload["route_tuple_sha256"], "route tuple digest")
    if tuple_digest != route_tuple_sha256(route_tuple):
        raise ValueError("route tuple digest mismatch")
    raw_slots = payload["slots"]
    if not isinstance(raw_slots, list) or not raw_slots:
        raise ValueError("at least one route slot is required")
    slots = tuple(_parse_uuid(value, "route slot") for value in raw_slots)
    if len(slots) != len(set(slots)):
        raise ValueError("duplicate route slot")
    return ProductionRouteManifestV1(
        route_id=_parse_uuid(payload["route_id"], "route_id"),
        route_tuple=route_tuple,
        route_tuple_sha256=tuple_digest,
        worker_build_id=_require_text(payload["worker_build_id"], "worker_build_id"),
        slots=slots,
        artifact_sha256=_require_digest(payload["artifact_sha256"], "artifact_sha256"),
        image_sha256=_require_digest(payload["image_sha256"], "image_sha256"),
        artifact_lock_sha256=expected_lock,
    )


def parse_route_bundle(
    payload: Mapping[str, object], *, expected_lock_sha256: str
) -> tuple[ProductionRouteManifestV1, ...]:
    """Parse a non-empty strict bundle with unique stable route UUIDs."""

    if not isinstance(payload, Mapping) or frozenset(payload) != {
        "schema_version",
        "routes",
    }:
        raise ValueError("route bundle has missing or unknown fields")
    if payload["schema_version"] != _ROUTE_BUNDLE_VERSION:
        raise ValueError("unsupported route bundle schema version")
    raw_routes = payload["routes"]
    if not isinstance(raw_routes, list) or not raw_routes:
        raise ValueError("route bundle must contain at least one route")
    routes = tuple(
        parse_route_manifest(route, expected_lock_sha256=expected_lock_sha256)
        for route in raw_routes
    )
    route_ids = tuple(route.route_id for route in routes)
    if len(route_ids) != len(set(route_ids)):
        raise ValueError("duplicate route ID in bundle")
    return routes
