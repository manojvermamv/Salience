"""Read-only verification for workspace-scoped production rollback starts."""

from __future__ import annotations

from dataclasses import dataclass, replace
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
ROLLBACK_VERIFIER_ABI_V3 = "salience.p1-recovery-rollback-verifier.v3"
ROLLBACK_INVENTORY_SCHEMA_V3 = "salience.p1-recovery-rollback-inventory.v3"
ROLLBACK_MIGRATION_HEAD_V3 = "0053_p1_recovery_rollback_v3"
ROLLBACK_VERIFIER_ABI_V4 = "salience.p1-recovery-rollback-verifier.v4"
ROLLBACK_INVENTORY_SCHEMA_V4 = "salience.p1-recovery-rollback-inventory.v4"
ROLLBACK_MIGRATION_HEAD_V4 = "0055_p1_recovery_open_rollback_v4"
ROLLBACK_VERIFIER_ABI_V5 = "salience.p1-recovery-rollback-verifier.v5"
ROLLBACK_INVENTORY_SCHEMA_V5 = "salience.p1-recovery-rollback-inventory.v5"
ROLLBACK_MIGRATION_HEAD_V5 = "0058_p1_preclaim_rollback_v5"
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
_CASE_OPENING_KEYS = frozenset({
    "schema_version", "command_id", "case_id", "work_id", "alert_event_id", "workspace_id", "goal_id",
    "intent_id", "cycle_id", "context_id", "operation_id", "actor_id", "action", "resulting_revision",
    "state", "kind", "failure_class", "eligibility_revision", "generation", "request_fingerprint",
    "permit_id", "claim_id", "external_effect_id", "evidence_state", "request", "authorization",
})
_CASE_OPENING_REQUEST_KEYS = frozenset({
    "workspace_id", "actor_id", "intent_id", "cycle_id", "context_id", "operation_id",
    "eligibility_revision", "generation", "request_fingerprint", "deadline", "idempotency_key",
})
_CASE_OPENING_AUTHORIZATION_KEYS = frozenset({
    "policy_decision_id", "policy_version_id", "policy_document_hash", "stage_decision_id", "route_decision_id",
    "route_id", "slot_id", "fencing_epoch", "workspace_stop_revision", "goal_stop_revision", "actor_revision",
    "expires_at", "recipient_profile_revision", "retry_policy_revision", "alert_policy_sha256",
})


_PRECLAIM_OPENING_KEYS = _CASE_OPENING_KEYS | {
    "opening_kind", "approval_request_id", "publication_request_id", "publication_plan_id",
    "publication_attempt_id", "job_id", "workflow_id", "workflow_run_id",
    "provider_idempotency_key", "budget_reservation_id",
}


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
    return _read_workspace_inventory(
        database_url, workspace_id, ROLLBACK_MIGRATION_HEAD_V2,
        "SELECT public.v4_p1_recovery_rollback_inventory_v2(%s)",
        validate_recovery_rollback_inventory_v2,
    )


def validate_recovery_rollback_inventory_v3(
    inventory: object, *, workspace_id: UUID
) -> tuple[RecoveryRollbackRouteClaimV2, ...]:
    if (
        not isinstance(inventory, dict)
        or set(inventory) != _INVENTORY_KEYS | {"case_lineage"}
        or inventory["schema_version"] != ROLLBACK_INVENTORY_SCHEMA_V3
        or inventory["migration_head"] != ROLLBACK_MIGRATION_HEAD_V3
    ):
        raise RecoveryRollbackVerificationError("rollback v3 inventory envelope is incomplete")
    lineage = inventory["case_lineage"]
    counts = inventory["relation_counts"]
    if (
        not isinstance(lineage, list)
        or not isinstance(counts, dict)
        or type(counts.get("production_case_lineage")) is not int
        or counts["production_case_lineage"] != len(lineage)
    ):
        raise RecoveryRollbackVerificationError("rollback case lineage inventory is partial")
    common = {key: inventory[key] for key in _INVENTORY_KEYS}
    common["schema_version"] = ROLLBACK_INVENTORY_SCHEMA_V2
    common["migration_head"] = ROLLBACK_MIGRATION_HEAD_V2
    common["relation_counts"] = {key: value for key, value in counts.items() if key != "production_case_lineage"}
    routes = validate_recovery_rollback_inventory_v2(common, workspace_id=workspace_id)
    case_work = {
        item["work_id"]: item
        for item in inventory["recovery_work"]
        if isinstance(item, dict) and item.get("source_discriminant") == "cycle_case"
    }
    seen = set()
    for record in lineage:
        if not isinstance(record, dict) or set(record) != {
            "work_id", "case_id", "source_revision", "current_revision", "current_state",
            "command_receipts", "archive",
        }:
            raise RecoveryRollbackVerificationError("rollback case lineage record is incomplete")
        work_id = str(_uuid(record["work_id"], "case lineage work_id"))
        work = case_work.get(work_id)
        if (
            work is None or work_id in seen
            or record["case_id"] != work["source_id"]
            or work["workspace_id"] != str(workspace_id)
            or type(record["source_revision"]) is not int
            or record["source_revision"] != work["source_revision"]
            or type(record["current_revision"]) is not int
            or record["current_revision"] < record["source_revision"]
            or not isinstance(record["command_receipts"], list)
            or len(record["command_receipts"]) != record["current_revision"] - record["source_revision"]
        ):
            raise RecoveryRollbackVerificationError("rollback case lineage differs from the workspace source")
        seen.add(work_id)
    if seen != case_work.keys():
        raise RecoveryRollbackVerificationError("rollback case lineage omits a bound source")
    return routes


def read_p1_recovery_rollback_inventory_v3(
    database_url: str, *, workspace_id: UUID
) -> RecoveryRollbackInventoryV2:
    """Read workspace inventory including authenticated case command lineage."""
    return _read_workspace_inventory(
        database_url, workspace_id, ROLLBACK_MIGRATION_HEAD_V3,
        "SELECT public.v4_p1_recovery_rollback_inventory_v3(%s)",
        validate_recovery_rollback_inventory_v3,
    )


def validate_recovery_rollback_inventory_v4(
    inventory: object, *, workspace_id: UUID
) -> tuple[RecoveryRollbackRouteClaimV2, ...]:
    if (
        not isinstance(inventory, dict)
        or set(inventory) != _INVENTORY_KEYS | {"case_lineage", "opening_receipts"}
        or inventory["schema_version"] != ROLLBACK_INVENTORY_SCHEMA_V4
        or inventory["migration_head"] != ROLLBACK_MIGRATION_HEAD_V4
    ):
        raise RecoveryRollbackVerificationError("rollback v4 inventory envelope is incomplete")
    receipts = inventory["opening_receipts"]
    counts = inventory["relation_counts"]
    if (
        not isinstance(receipts, list) or not isinstance(counts, dict)
        or type(counts.get("production_case_opening_receipts")) is not int
        or counts["production_case_opening_receipts"] != len(receipts)
    ):
        raise RecoveryRollbackVerificationError("rollback case opening inventory is partial")
    common = {key: value for key, value in inventory.items() if key != "opening_receipts"}
    common["schema_version"] = ROLLBACK_INVENTORY_SCHEMA_V3
    common["migration_head"] = ROLLBACK_MIGRATION_HEAD_V3
    common["relation_counts"] = {
        key: value for key, value in counts.items() if key != "production_case_opening_receipts"
    }
    routes = validate_recovery_rollback_inventory_v3(common, workspace_id=workspace_id)
    permits = {row["permit_id"]: row for row in inventory["production_permits"]}
    claims = {row["claim_id"]: row for row in inventory["production_claims"]}
    events = {row["event_id"]: row for row in inventory["recovery_events"]}
    expected = {}
    for work in inventory["recovery_work"]:
        if not isinstance(work, dict) or "opening_command_id" not in work:
            raise RecoveryRollbackVerificationError("rollback work omits opening provenance")
        if work["opening_command_id"] is not None:
            _uuid(work["opening_command_id"], "opening command_id")
            expected[work["work_id"]] = work
    for receipt in receipts:
        if (not isinstance(receipt, dict) or receipt.keys() != _CASE_OPENING_KEYS
                or not isinstance(receipt["work_id"], str)):
            raise RecoveryRollbackVerificationError("rollback case opening receipt is incomplete")
        work = expected.pop(receipt["work_id"], None)
        if (
            work is None or work["source_discriminant"] != "cycle_case"
            or receipt["command_id"] != work["opening_command_id"]
            or receipt["case_id"] != work["source_id"]
            or receipt["workspace_id"] != str(workspace_id)
            or receipt["actor_id"] != work["owner_id"]
            or receipt["failure_class"] != work["reason"]
            or any(receipt[key] != work[key] for key in (
                "goal_id", "intent_id", "cycle_id", "context_id", "operation_id", "state",
                "eligibility_revision", "request_fingerprint",
            ))
            or receipt["schema_version"] != "salience.p1-production-recovery-case-opening.v1"
            or receipt["action"] != "open" or receipt["kind"] != "reconciliation"
            or type(receipt["resulting_revision"]) is not int or receipt["resulting_revision"] != 1
            or type(receipt["generation"]) is not int or receipt["generation"] < 1
            or receipt["evidence_state"] not in ("submitting", "unknown")
            or not isinstance(receipt["request"], dict) or not isinstance(receipt["authorization"], dict)
        ):
            raise RecoveryRollbackVerificationError("rollback case opening receipt differs from its source")
        for key in ("permit_id", "claim_id", "external_effect_id", "alert_event_id"):
            _uuid(receipt[key], f"opening {key}")
        request, decision = receipt["request"], receipt["authorization"]
        permit = permits.get(receipt["permit_id"], {})
        claim = claims.get(receipt["claim_id"], {})
        alert = events.get(receipt["alert_event_id"], {})
        material_keys = ("operation_id", "generation")
        if (
            request.keys() != _CASE_OPENING_REQUEST_KEYS
            or decision.keys() != _CASE_OPENING_AUTHORIZATION_KEYS
            or any(request[key] != receipt[key] for key in _CASE_OPENING_REQUEST_KEYS & _CASE_OPENING_KEYS)
            or any(permit.get(key) != receipt[key] or claim.get(key) != receipt[key] for key in material_keys)
            or claim.get("permit_id") != receipt["permit_id"]
            or permit.get("request_snapshot", {}).get("external_effect_id") != receipt["external_effect_id"]
            or alert.get("event_kind") != "alert_intent" or alert.get("work_id") != receipt["work_id"]
            or alert.get("policy_decision_id") != decision["policy_decision_id"]
        ):
            raise RecoveryRollbackVerificationError("rollback case opening authority or linked evidence differs")
    if expected:
        raise RecoveryRollbackVerificationError("rollback case opening inventory omits a bound source")
    return routes


def read_p1_recovery_rollback_inventory_v4(
    database_url: str, *, workspace_id: UUID
) -> RecoveryRollbackInventoryV2:
    """Read workspace inventory including canonical case-opening provenance."""
    return _read_workspace_inventory(
        database_url, workspace_id, ROLLBACK_MIGRATION_HEAD_V4,
        "SELECT public.v4_p1_recovery_rollback_inventory_v4(%s)",
        validate_recovery_rollback_inventory_v4,
    )


def validate_recovery_rollback_inventory_v5(
    inventory: object, *, workspace_id: UUID,
) -> tuple[RecoveryRollbackRouteClaimV2, ...]:
    if (
        not isinstance(inventory, dict)
        or set(inventory) != _INVENTORY_KEYS | {"case_lineage", "opening_receipts", "preclaim_lineage"}
        or inventory["schema_version"] != ROLLBACK_INVENTORY_SCHEMA_V5
        or inventory["migration_head"] != ROLLBACK_MIGRATION_HEAD_V5
    ):
        raise RecoveryRollbackVerificationError("rollback v5 inventory envelope is incomplete")
    lineage = inventory["preclaim_lineage"]
    counts = inventory["relation_counts"]
    if (
        not isinstance(lineage, list) or not isinstance(counts, dict)
        or type(counts.get("production_preclaim_lineage")) is not int
        or counts["production_preclaim_lineage"] != len(lineage)
    ):
        raise RecoveryRollbackVerificationError("rollback preclaim inventory is incomplete")
    def indexed(relation: str, id_field: str) -> dict:
        indexed_rows = {}
        rows = inventory[relation]
        if not isinstance(rows, list):
            raise RecoveryRollbackVerificationError(f"rollback {relation} is unavailable")
        for row in rows:
            if not isinstance(row, dict):
                raise RecoveryRollbackVerificationError(f"rollback {relation} contains an incomplete row")
            identity = str(_uuid(row.get(id_field), id_field))
            if identity in indexed_rows:
                raise RecoveryRollbackVerificationError(f"rollback {relation} contains a duplicate row")
            indexed_rows[identity] = row
        return indexed_rows

    work_rows = indexed("recovery_work", "work_id")
    events = indexed("recovery_events", "event_id")
    permits = indexed("production_permits", "permit_id")
    pointers = indexed("production_current_pointers", "operation_id")
    claims = indexed("production_claims", "claim_id")
    claimed_permits = {row.get("permit_id") for row in claims.values() if isinstance(row.get("permit_id"), str)}
    accepted_work = set()
    accepted_proofs = set()
    for item in lineage:
        if not isinstance(item, dict) or set(item) != {"opening", "renewal"}:
            raise RecoveryRollbackVerificationError("rollback preclaim lineage is incomplete")
        opening = item["opening"]
        if not isinstance(opening, dict) or opening.keys() != _PRECLAIM_OPENING_KEYS:
            raise RecoveryRollbackVerificationError("rollback preclaim opening is incomplete")
        work_id = str(_uuid(opening["work_id"], "preclaim work ID"))
        work = work_rows.get(work_id, {})
        original_id = str(_uuid(opening["permit_id"], "preclaim predecessor permit"))
        original = permits.get(original_id, {})
        operation = str(_uuid(opening["operation_id"], "preclaim operation ID"))
        pointer = pointers.get(operation, {})
        alert = events.get(str(_uuid(opening["alert_event_id"], "preclaim alert ID")), {})
        request = opening["request"]
        decision = opening["authorization"]
        original_snapshot = original.get("request_snapshot")
        if (
            work_id in accepted_work or opening["workspace_id"] != str(workspace_id)
            or opening["schema_version"] != "salience.p1-production-preclaim-case-opening.v1"
            or opening.get("opening_kind") != "expired_unclaimed_permit"
            or opening["command_id"] != work.get("opening_command_id")
            or opening["case_id"] != work.get("source_id") or opening["claim_id"] is not None
            or opening["actor_id"] != work.get("owner_id")
            or opening["failure_class"] != work.get("reason")
            or any(opening[key] != work.get(key) for key in (
                "goal_id", "intent_id", "cycle_id", "context_id", "operation_id", "state",
                "eligibility_revision", "request_fingerprint",
            ))
            or opening["action"] != "open" or opening["kind"] != "manual_review"
            or opening["state"] != "awaiting_review" or opening["evidence_state"] != "planned"
            or type(opening["generation"]) is not int or opening["generation"] != 1
            or type(opening["resulting_revision"]) is not int or opening["resulting_revision"] != 1
            or opening["operation_id"] != original.get("operation_id")
            or original.get("generation") != 1 or original_id in claimed_permits
            or not isinstance(original_snapshot, dict)
            or any(opening[key] != original_snapshot.get(key) for key in (
                "publication_request_id", "publication_plan_id", "publication_attempt_id",
                "external_effect_id", "job_id", "workflow_id", "workflow_run_id", "budget_reservation_id",
            ))
            or original.get("approval_request_id") != opening["approval_request_id"]
            or pointer.get("idempotency_key") != opening["provider_idempotency_key"]
            or alert.get("work_id") != work_id or alert.get("event_kind") != "alert_intent"
            or not isinstance(request, dict)
            or request.keys() != _CASE_OPENING_REQUEST_KEYS | {"opening_kind", "permit_id"}
            or any(request[key] != opening[key] for key in request.keys() & opening.keys())
            or not isinstance(decision, dict) or decision.keys() != _CASE_OPENING_AUTHORIZATION_KEYS
            or alert.get("policy_decision_id") != decision["policy_decision_id"]
        ):
            raise RecoveryRollbackVerificationError("rollback preclaim opening differs from its source")
        accepted_work.add(work_id)
        renewal = item["renewal"]
        if renewal is None:
            if pointer.get("current_permit_id") != original_id or pointer.get("generation") != 1:
                raise RecoveryRollbackVerificationError("rollback preclaim inventory omits a renewal proof")
            continue
        if not isinstance(renewal, dict):
            raise RecoveryRollbackVerificationError("rollback preclaim renewal is incomplete")
        proof_id = str(_uuid(renewal.get("event_id"), "preclaim proof ID"))
        payload = renewal.get("payload")
        if (
            proof_id in accepted_proofs or renewal != events.get(proof_id)
            or renewal.get("event_kind") != "preclaim_permit_resume" or renewal.get("work_id") != work_id
            or not isinstance(payload, dict) or payload.get("predecessor_permit_id") != original_id
            or payload.get("opening_command_id") != opening["command_id"]
            or payload.get("schema_version") != "salience.p1-preclaim-permit-resume.v1"
            or payload.get("operation_id") != operation or payload.get("work_id") != work_id
            or payload.get("approval_request_id") != opening["approval_request_id"]
        ):
            raise RecoveryRollbackVerificationError("rollback preclaim renewal differs from its source")
        accepted_proofs.add(proof_id)
        successor_id = str(_uuid(payload.get("successor_permit_id"), "preclaim successor permit"))
        successor = permits.get(successor_id, {})
        if (
            successor.get("generation") != 2 or successor.get("operation_id") != operation
            or successor.get("recovery_command_id") != proof_id
            or successor.get("approval_request_id") != opening["approval_request_id"]
            or successor.get("route_fence_decision_id") != payload.get("route_decision_id")
            or pointer.get("generation") != 2 or pointer.get("current_permit_id") != successor_id
        ):
            raise RecoveryRollbackVerificationError("rollback preclaim successor differs from its proof")
    if accepted_proofs != {key for key, row in events.items() if row.get("event_kind") == "preclaim_permit_resume"}:
        raise RecoveryRollbackVerificationError("rollback preclaim lineage omits a renewal proof")
    legacy = {key: value for key, value in inventory.items() if key != "preclaim_lineage"}
    legacy["schema_version"] = ROLLBACK_INVENTORY_SCHEMA_V4
    legacy["migration_head"] = ROLLBACK_MIGRATION_HEAD_V4
    legacy["relation_counts"] = {key: value for key, value in counts.items() if key != "production_preclaim_lineage"}
    legacy["recovery_work"] = [
        row | {"opening_command_id": None} if isinstance(row, dict) and row.get("work_id") in accepted_work else row
        for row in inventory["recovery_work"]
    ]
    return validate_recovery_rollback_inventory_v4(legacy, workspace_id=workspace_id)


def read_p1_recovery_rollback_inventory_v5(
    database_url: str, *, workspace_id: UUID,
) -> RecoveryRollbackInventoryV2:
    """Read workspace inventory including approved preclaim permit recovery."""
    return _read_workspace_inventory(
        database_url, workspace_id, ROLLBACK_MIGRATION_HEAD_V5,
        "SELECT public.v4_p1_recovery_rollback_inventory_v5(%s)",
        validate_recovery_rollback_inventory_v5,
    )


def _read_workspace_inventory(
    database_url: str, workspace_id: UUID, migration: str, query: str, validate,
) -> RecoveryRollbackInventoryV2:
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
            heads = connection.execute(
                "SELECT version_num FROM public.alembic_version"
            ).fetchall()
            if heads != [(migration,)]:
                raise RecoveryRollbackVerificationError(
                    f"database does not have the exact {migration} migration head"
                )
            row = connection.execute(query, (workspace_id,)).fetchone()
            if row is None or not isinstance(row[0], dict):
                raise RecoveryRollbackVerificationError(
                    "workspace inventory query returned no complete envelope"
                )
            raw_inventory = row[0]
            route_claims = validate(raw_inventory, workspace_id=workspace_id)
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
    return _verify_workspace_readbacks(
        read_p1_recovery_rollback_inventory_v2(database_url, workspace_id=workspace_id),
        route_authority, temporal_readback,
    )


def verify_p1_recovery_rollback_v3(
    database_url: str,
    *,
    workspace_id: UUID,
    route_authority: RouteLeaseAuthority,
    temporal_readback: TemporalWorkerReadback,
) -> RecoveryRollbackInventoryV2:
    """Require complete DB, lineage, route, and drained Temporal evidence."""
    return _verify_workspace_readbacks(
        read_p1_recovery_rollback_inventory_v3(database_url, workspace_id=workspace_id),
        route_authority, temporal_readback,
    )


def verify_p1_recovery_rollback_v4(
    database_url: str,
    *,
    workspace_id: UUID,
    route_authority: RouteLeaseAuthority,
    temporal_readback: TemporalWorkerReadback,
) -> RecoveryRollbackInventoryV2:
    """Require complete workspace, case-opening, route, and Temporal evidence."""
    return _verify_workspace_readbacks(
        read_p1_recovery_rollback_inventory_v4(database_url, workspace_id=workspace_id),
        route_authority, temporal_readback,
    )


def verify_p1_recovery_rollback_v5(
    database_url: str, *, workspace_id: UUID,
    route_authority: RouteLeaseAuthority, temporal_readback: TemporalWorkerReadback,
) -> RecoveryRollbackInventoryV2:
    """Require complete workspace, preclaim renewal, route, and Temporal evidence."""
    return _verify_workspace_readbacks(
        read_p1_recovery_rollback_inventory_v5(database_url, workspace_id=workspace_id),
        route_authority, temporal_readback,
    )


def _verify_workspace_readbacks(
    inventory: RecoveryRollbackInventoryV2,
    route_authority: RouteLeaseAuthority,
    temporal_readback: TemporalWorkerReadback,
) -> RecoveryRollbackInventoryV2:
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
    return replace(
        inventory,
        temporal_readbacks=temporal_evidence,
        readback_digest=readback_digest,
    )
