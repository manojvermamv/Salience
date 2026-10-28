"""Read-only v2 verification for workspace-scoped production rollback starts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import TYPE_CHECKING, Any, Mapping
from uuid import UUID

import psycopg

if TYPE_CHECKING:
    from salience.cycles.production_routing import RouteLeaseAuthority
    from salience.workflows.temporal_readback import TemporalWorkerReadback


ROLLBACK_VERIFIER_ABI_V2 = "salience.p1-recovery-rollback-verifier.v2"
ROLLBACK_INVENTORY_SCHEMA_V2 = "salience.p1-recovery-rollback-inventory.v2"
ROLLBACK_MIGRATION_HEAD_V2 = "0051_p1_recovery_rollback_v2"
_REASON_KEYS = ("expired_permit_receipt", "published_during_cancel")
_INVENTORY_ARRAYS = {
    "source_identities": "source_identities",
    "enrollments": "v4_production_intent_enrollments",
    "recovery_work": "v4_production_recovery_work",
    "recovery_events": "v4_production_recovery_events",
    "fixture_holds": "v4_legacy_publication_recovery_holds",
    "production_permits": "v4_production_effect_permits",
    "production_claims": "v4_production_effect_permit_claims",
    "production_current_pointers": "v4_production_current_dispatch",
    "schedule_inventories": "v4_production_schedule_inventories",
    "schedule_members": "v4_production_schedule_inventory_members",
    "schedule_pages": "v4_production_schedule_inventory_pages",
    "schedule_bindings": "v4_production_schedule_source_bindings",
    "schedule_approvals": "schedule_binding_approvals",
    "production_liabilities": "production_liabilities",
    "route_claims": "route_claims",
    "blockers": None,
}
_INVENTORY_KEYS = frozenset(
    {
        "schema_version",
        "migration_head",
        "workspace_id",
        "complete",
        "relation_counts",
        *_INVENTORY_ARRAYS,
        "reason_counts",
    }
)
_RELATION_COUNT_KEYS = frozenset(
    relation for relation in _INVENTORY_ARRAYS.values() if relation is not None
)


class RecoveryRollbackVerificationError(RuntimeError):
    """The complete workspace, route, or Temporal readback cannot be trusted."""


@dataclass(frozen=True, slots=True)
class RecoveryRollbackRouteClaimV2:
    route_id: UUID
    slot_id: UUID
    owner_id: UUID
    fencing_epoch: int
    route_manifest_sha256: str
    artifact_lock_sha256: str


@dataclass(frozen=True, slots=True)
class RecoveryRollbackTemporalReadbackV2:
    route_id: UUID
    slot_id: UUID
    owner_id: UUID
    fencing_epoch: int
    route_manifest_sha256: str
    artifact_lock_sha256: str
    temporal_endpoint_id: str
    namespace: str
    task_queue: str
    registered_workflow_types: tuple[str, ...]
    registered_activity_types: tuple[str, ...]
    poller_count: int
    active_task_count: int
    complete: bool


@dataclass(frozen=True, slots=True)
class RecoveryRollbackInventoryV2:
    workspace_id: UUID
    migration_head: str
    digest: str
    inventory: Mapping[str, Any]
    route_claims: tuple[RecoveryRollbackRouteClaimV2, ...]
    fixture_liabilities: tuple[Mapping[str, Any], ...]
    temporal_readbacks: tuple[RecoveryRollbackTemporalReadbackV2, ...] = ()
    readback_digest: str | None = None

    @property
    def reason_counts(self) -> Mapping[str, Mapping[str, int]]:
        return self.inventory["reason_counts"]


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _uuid(value: object, field: str) -> UUID:
    if not isinstance(value, str):
        raise RecoveryRollbackVerificationError(f"inventory {field} UUID is missing")
    try:
        parsed = UUID(value)
    except (TypeError, ValueError, AttributeError) as error:
        raise RecoveryRollbackVerificationError(
            f"inventory {field} UUID is malformed"
        ) from error
    if str(parsed) != value:
        raise RecoveryRollbackVerificationError(
            f"inventory {field} UUID is not canonical"
        )
    return parsed


def _digest(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise RecoveryRollbackVerificationError(
            f"inventory {field} must be lowercase SHA-256"
        )
    return value


def validate_recovery_rollback_inventory_v2(
    inventory: object, *, workspace_id: UUID
) -> tuple[RecoveryRollbackRouteClaimV2, ...]:
    """Validate the exact SQL envelope before treating any empty set as complete."""
    if not isinstance(inventory, dict) or set(inventory) != _INVENTORY_KEYS:
        raise RecoveryRollbackVerificationError(
            "rollback inventory envelope is incomplete or contains unknown fields"
        )
    if (
        inventory["schema_version"] != ROLLBACK_INVENTORY_SCHEMA_V2
        or inventory["migration_head"] != ROLLBACK_MIGRATION_HEAD_V2
        or inventory["workspace_id"] != str(workspace_id)
        or inventory["complete"] is not True
    ):
        raise RecoveryRollbackVerificationError(
            "rollback inventory scope, schema, or migration head does not match"
        )

    relation_counts = inventory["relation_counts"]
    if not isinstance(relation_counts, dict) or set(relation_counts) != _RELATION_COUNT_KEYS:
        raise RecoveryRollbackVerificationError(
            "rollback inventory relation counts are incomplete"
        )
    for array_name, relation_name in _INVENTORY_ARRAYS.items():
        value = inventory[array_name]
        if not isinstance(value, list):
            raise RecoveryRollbackVerificationError(
                f"rollback inventory relation {array_name} is unavailable"
            )
        if relation_name is not None:
            observed = relation_counts.get(relation_name)
            if type(observed) is not int or observed < 0 or observed != len(value):
                raise RecoveryRollbackVerificationError(
                    f"rollback inventory relation {relation_name} is partial"
                )

    reasons = inventory["reason_counts"]
    if not isinstance(reasons, dict) or set(reasons) != set(_REASON_KEYS):
        raise RecoveryRollbackVerificationError(
            "rollback inventory must query both hold reasons"
        )
    for reason in _REASON_KEYS:
        counts = reasons[reason]
        if not isinstance(counts, dict) or set(counts) != {
            "production_work",
            "fixture_holds",
        }:
            raise RecoveryRollbackVerificationError(
                f"rollback inventory count is incomplete for {reason}"
            )
        if any(type(counts[key]) is not int or counts[key] < 0 for key in counts):
            raise RecoveryRollbackVerificationError(
                f"rollback inventory count is invalid for {reason}"
            )
    if not isinstance(inventory["blockers"], list):
        raise RecoveryRollbackVerificationError("rollback blocker set is unavailable")
    if inventory["blockers"]:
        raise RecoveryRollbackVerificationError(
            "rollback inventory contains unresolved blockers: "
                + json.dumps(inventory["blockers"], sort_keys=True, default=str)
        )

    source_identity_key = []
    for row in inventory["source_identities"]:
        if not isinstance(row, dict) or not {
            "source_kind",
            "source_id",
            "source_revision",
            "workspace_id",
        } <= set(row):
            raise RecoveryRollbackVerificationError(
                "rollback source identity is incomplete"
            )
        if row["workspace_id"] != str(workspace_id):
            raise RecoveryRollbackVerificationError(
                "rollback source identity is outside workspace scope"
            )
        source_id = _uuid(row["source_id"], "source identity source_id")
        revision = row["source_revision"]
        if type(revision) is not int or revision < 1:
            raise RecoveryRollbackVerificationError(
                "rollback source identity revision is invalid"
            )
        if not isinstance(row["source_kind"], str) or not row["source_kind"]:
            raise RecoveryRollbackVerificationError(
                "rollback source identity kind is invalid"
            )
        source_identity_key.append(
            (row["source_kind"], str(source_id), revision)
        )
    if source_identity_key != sorted(source_identity_key):
        raise RecoveryRollbackVerificationError(
            "rollback source identities are not deterministically ordered"
        )

    claims: dict[tuple[UUID, UUID], RecoveryRollbackRouteClaimV2] = {}
    for row in inventory["route_claims"]:
        if not isinstance(row, dict) or set(row) != {
            "source_kind",
            "workspace_id",
            "route_id",
            "slot_id",
            "owner_id",
            "fencing_epoch",
            "route_manifest_sha256",
            "artifact_lock_sha256",
        }:
            raise RecoveryRollbackVerificationError("route claim row is incomplete")
        if row["workspace_id"] != str(workspace_id) or not isinstance(
            row["source_kind"], str
        ):
            raise RecoveryRollbackVerificationError("route claim is outside workspace scope")
        epoch = row["fencing_epoch"]
        if type(epoch) is not int or epoch < 1:
            raise RecoveryRollbackVerificationError("route claim fencing epoch is invalid")
        claim = RecoveryRollbackRouteClaimV2(
            route_id=_uuid(row["route_id"], "route_id"),
            slot_id=_uuid(row["slot_id"], "slot_id"),
            owner_id=_uuid(row["owner_id"], "owner_id"),
            fencing_epoch=epoch,
            route_manifest_sha256=_digest(
                row["route_manifest_sha256"], "route_manifest_sha256"
            ),
            artifact_lock_sha256=_digest(
                row["artifact_lock_sha256"], "artifact_lock_sha256"
            ),
        )
        key = (claim.route_id, claim.slot_id)
        prior = claims.get(key)
        if prior is not None and prior != claim:
            raise RecoveryRollbackVerificationError(
                "workspace route claims disagree for the same route slot"
            )
        claims[key] = claim
    return tuple(claims[key] for key in sorted(claims, key=lambda key: (str(key[0]), str(key[1]))))


def read_p1_recovery_rollback_inventory_v2(
    database_url: str, *, workspace_id: UUID
) -> RecoveryRollbackInventoryV2:
    """Read and validate the exact workspace inventory in one read-only snapshot."""
    if not isinstance(database_url, str) or not database_url.strip():
        raise RecoveryRollbackVerificationError("rollback database URL is unavailable")
    if not isinstance(workspace_id, UUID):
        raise RecoveryRollbackVerificationError("rollback workspace UUID is required")
    try:
        with psycopg.connect(database_url, connect_timeout=3) as connection:
            connection.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
            )
            connection.execute("SET LOCAL statement_timeout='10s'")
            head = connection.execute(
                "SELECT version_num FROM public.alembic_version"
            ).fetchone()
            if head is None or head[0] != ROLLBACK_MIGRATION_HEAD_V2:
                raise RecoveryRollbackVerificationError(
                    "database does not have the exact 0051 migration head"
                )
            row = connection.execute(
                "SELECT public.v4_p1_recovery_rollback_inventory_v2(%s)",
                (workspace_id,),
            ).fetchone()
            if row is None or not isinstance(row[0], dict):
                raise RecoveryRollbackVerificationError(
                    "workspace inventory query returned no complete envelope"
                )
            raw_inventory = row[0]
            route_claims = validate_recovery_rollback_inventory_v2(
                raw_inventory, workspace_id=workspace_id
            )
            from salience.cycles.p1_recovery_rollback_fixtures import (
                read_fixture_liabilities,
            )

            fixture_liabilities = read_fixture_liabilities(
                connection, workspace_id, raw_inventory
            )
    except RecoveryRollbackVerificationError:
        raise
    except Exception as error:
        raise RecoveryRollbackVerificationError(
            "workspace rollback inventory is unavailable or incomplete"
        ) from error

    digest_material = {
        "inventory": raw_inventory,
        "fixture_liabilities": list(fixture_liabilities),
    }
    digest = hashlib.sha256(_canonical_bytes(digest_material)).hexdigest()
    return RecoveryRollbackInventoryV2(
        workspace_id=workspace_id,
        migration_head=raw_inventory["migration_head"],
        digest=digest,
        inventory=raw_inventory,
        route_claims=route_claims,
        fixture_liabilities=fixture_liabilities,
    )


def _verify_route_claims(
    route_claims: tuple[RecoveryRollbackRouteClaimV2, ...], route_authority
) -> None:
    from salience.cycles.production_routing import (
        ROUTE_FENCE_AUTHORITY_VERSION,
        RouteLease,
        RouteSlotReadback,
    )

    if getattr(route_authority, "protocol_version", None) != ROUTE_FENCE_AUTHORITY_VERSION:
        raise RecoveryRollbackVerificationError("route authority version is unknown")
    reader = getattr(route_authority, "read_slot", None)
    if not callable(reader):
        raise RecoveryRollbackVerificationError("route authority readback is unavailable")
    if not route_claims:
        raise RecoveryRollbackVerificationError("workspace has no production route claim")

    now = datetime.now(timezone.utc)
    for claim in route_claims:
        try:
            readback = reader(claim.route_id, claim.slot_id)
        except Exception as error:
            raise RecoveryRollbackVerificationError(
                "route authority readback is unavailable"
            ) from error
        if (
            not isinstance(readback, RouteSlotReadback)
            or not readback.available
            or readback.state != "leased"
            or readback.lease is None
            or not isinstance(readback.lease, RouteLease)
            or readback.route_id != claim.route_id
            or readback.slot_id != claim.slot_id
            or readback.fencing_epoch != claim.fencing_epoch
        ):
            raise RecoveryRollbackVerificationError(
                "route claim is unknown, stale, unavailable, or not currently leased"
            )
        lease = readback.lease
        if (
            lease.route_id != claim.route_id
            or lease.slot_id != claim.slot_id
            or lease.owner_id != claim.owner_id
            or lease.fencing_epoch != claim.fencing_epoch
            or lease.route_manifest_sha256 != claim.route_manifest_sha256
            or lease.artifact_lock_sha256 != claim.artifact_lock_sha256
            or lease.expires_at <= now
        ):
            raise RecoveryRollbackVerificationError(
                "current route lease does not match the complete workspace claim"
            )


def _verify_temporal_readbacks(
    route_claims: tuple[RecoveryRollbackRouteClaimV2, ...], temporal_readback
) -> tuple[RecoveryRollbackTemporalReadbackV2, ...]:
    from salience.cycles.production_routing import (
        PRODUCTION_ACTIVITY_ALLOWLIST,
        PRODUCTION_WORKFLOW_ALLOWLIST,
    )
    from salience.workflows.temporal_readback import WorkerReadback

    reader = getattr(temporal_readback, "read_worker", None)
    if not callable(reader):
        raise RecoveryRollbackVerificationError("Temporal worker readback is unavailable")
    now = datetime.now(timezone.utc)
    observed_targets: dict[UUID, tuple[str, str, str]] = {}
    evidence: list[RecoveryRollbackTemporalReadbackV2] = []
    for claim in route_claims:
        try:
            readback = reader(claim.route_id, claim.slot_id)
        except Exception as error:
            raise RecoveryRollbackVerificationError(
                "Temporal worker readback is unavailable"
            ) from error
        if not isinstance(readback, WorkerReadback):
            raise RecoveryRollbackVerificationError(
                "Temporal worker readback has an unknown ABI"
            )
        observed_age = now - readback.observed_at
        target = (
            readback.temporal_endpoint_id,
            readback.namespace,
            readback.task_queue,
        )
        if (
            not readback.complete
            or readback.route_id != claim.route_id
            or readback.slot_id != claim.slot_id
            or readback.owner_id != claim.owner_id
            or readback.fencing_epoch != claim.fencing_epoch
            or readback.route_manifest_sha256 != claim.route_manifest_sha256
            or readback.artifact_lock_sha256 != claim.artifact_lock_sha256
            or readback.registered_workflow_types
            != frozenset(PRODUCTION_WORKFLOW_ALLOWLIST)
            or readback.registered_activity_types
            != frozenset(PRODUCTION_ACTIVITY_ALLOWLIST)
            or readback.poller_count != 0
            or readback.active_task_count != 0
            or observed_age > timedelta(seconds=60)
            or observed_age < -timedelta(seconds=5)
            or not all(isinstance(value, str) and value.strip() for value in target)
            or target[2].startswith("salience-v4-local-legacy-")
        ):
            raise RecoveryRollbackVerificationError(
                "Temporal registration or drain readback is incomplete, stale, or mismatched"
            )
        prior_target = observed_targets.setdefault(claim.route_id, target)
        if prior_target != target:
            raise RecoveryRollbackVerificationError(
                "Temporal readbacks disagree about one production route target"
            )
        evidence.append(
            RecoveryRollbackTemporalReadbackV2(
                route_id=readback.route_id,
                slot_id=readback.slot_id,
                owner_id=readback.owner_id,
                fencing_epoch=readback.fencing_epoch,
                route_manifest_sha256=readback.route_manifest_sha256,
                artifact_lock_sha256=readback.artifact_lock_sha256,
                temporal_endpoint_id=readback.temporal_endpoint_id,
                namespace=readback.namespace,
                task_queue=readback.task_queue,
                registered_workflow_types=tuple(
                    sorted(readback.registered_workflow_types)
                ),
                registered_activity_types=tuple(
                    sorted(readback.registered_activity_types)
                ),
                poller_count=readback.poller_count,
                active_task_count=readback.active_task_count,
                complete=readback.complete,
            )
        )
    return tuple(sorted(evidence, key=lambda item: (str(item.route_id), str(item.slot_id))))


def verify_p1_recovery_rollback_v2(
    database_url: str,
    *,
    workspace_id: UUID,
    route_authority: RouteLeaseAuthority,
    temporal_readback: TemporalWorkerReadback,
) -> RecoveryRollbackInventoryV2:
    """Require complete DB, route, and drained Temporal evidence for one scope."""
    inventory = read_p1_recovery_rollback_inventory_v2(
        database_url, workspace_id=workspace_id
    )
    _verify_route_claims(inventory.route_claims, route_authority)
    temporal_evidence = _verify_temporal_readbacks(
        inventory.route_claims, temporal_readback
    )
    readback_material = {
        "route_claims": [
            {
                "route_id": str(claim.route_id),
                "slot_id": str(claim.slot_id),
                "owner_id": str(claim.owner_id),
                "fencing_epoch": claim.fencing_epoch,
                "route_manifest_sha256": claim.route_manifest_sha256,
                "artifact_lock_sha256": claim.artifact_lock_sha256,
            }
            for claim in sorted(
                inventory.route_claims,
                key=lambda item: (str(item.route_id), str(item.slot_id)),
            )
        ],
        "temporal_readbacks": [
            {
                "route_id": str(item.route_id),
                "slot_id": str(item.slot_id),
                "owner_id": str(item.owner_id),
                "fencing_epoch": item.fencing_epoch,
                "route_manifest_sha256": item.route_manifest_sha256,
                "artifact_lock_sha256": item.artifact_lock_sha256,
                "temporal_endpoint_id": item.temporal_endpoint_id,
                "namespace": item.namespace,
                "task_queue": item.task_queue,
                "registered_workflow_types": list(item.registered_workflow_types),
                "registered_activity_types": list(item.registered_activity_types),
                "poller_count": item.poller_count,
                "active_task_count": item.active_task_count,
                "complete": item.complete,
            }
            for item in temporal_evidence
        ],
    }
    readback_digest = hashlib.sha256(_canonical_bytes(readback_material)).hexdigest()
    return RecoveryRollbackInventoryV2(
        workspace_id=inventory.workspace_id,
        migration_head=inventory.migration_head,
        digest=inventory.digest,
        inventory=inventory.inventory,
        route_claims=inventory.route_claims,
        fixture_liabilities=inventory.fixture_liabilities,
        temporal_readbacks=temporal_evidence,
        readback_digest=readback_digest,
    )
