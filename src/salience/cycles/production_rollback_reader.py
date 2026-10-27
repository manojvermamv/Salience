"""Read the versioned production-policy rollback inventory without authorizing rollback."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping
from uuid import UUID


_SCHEMA_VERSION = "salience.p1-production-policy-rollback-inventory.v1"
_FINGERPRINT = re.compile(r"[0-9a-f]{64}\Z")
_ENTRY_KEYS = frozenset({"source", "relation", "identity", "row"})
_ENTRY_KEYS_WITH_STATUS = _ENTRY_KEYS | {"inventory_status"}

_READ_INVENTORY_SQL = """
WITH rollback_snapshot AS MATERIALIZED (
    SELECT public.v4_production_policy_rollback_inventory_v1(%s::uuid) AS payload
)
SELECT payload,
       encode(
           public.digest((payload->'inventory')::text, 'sha256'),
           'hex'
       ) AS recomputed_inventory_sha256
FROM rollback_snapshot
"""


@dataclass(frozen=True)
class ProductionPolicyRollbackInventoryV1:
    """Raw inventory plus unresolved rows that still require owning verification.

    This result is evidence for a later rollback verifier.  Entries that have
    rows or explicit non-empty status markers remain blockers until an owning
    verifier classifies them; this reader never labels an operation safe.
    """

    schema_version: str
    workspace_id: UUID
    inventory: tuple[Mapping[str, Any], ...]
    inventory_sha256: str
    status: str
    blockers: tuple[Mapping[str, Any], ...]


def read_production_policy_rollback_inventory(
    connection: Any, *, workspace_id: UUID
) -> ProductionPolicyRollbackInventoryV1:
    """Read and validate one workspace-scoped rollback snapshot.

    The SQL function and the digest recomputation execute in one parameterized
    PostgreSQL statement so ``jsonb::text`` supplies the exact digest encoding
    used by migration 0044.
    """

    if not isinstance(workspace_id, UUID):
        raise ValueError("rollback inventory workspace_id must be a UUID")

    row = connection.execute(_READ_INVENTORY_SQL, (workspace_id,)).fetchone()
    if row is None:
        raise ValueError("rollback inventory query returned no result")
    if isinstance(row, Mapping):
        payload = row.get("payload")
        recomputed_digest = row.get("recomputed_inventory_sha256")
    else:
        try:
            payload, recomputed_digest = row[0], row[1]
        except (IndexError, KeyError, TypeError) as error:
            raise ValueError("rollback inventory query returned an invalid result shape") from error

    if not isinstance(payload, Mapping):
        raise ValueError("rollback inventory payload must be an object")
    if set(payload) != {
        "schema_version",
        "workspace_id",
        "inventory",
        "inventory_sha256",
    }:
        raise ValueError("rollback inventory payload has an invalid versioned shape")
    if payload["schema_version"] != _SCHEMA_VERSION:
        raise ValueError("unsupported rollback inventory schema version")
    if payload["workspace_id"] != str(workspace_id):
        raise ValueError("rollback inventory workspace does not match the requested workspace")

    raw_inventory = payload["inventory"]
    if not isinstance(raw_inventory, list):
        raise ValueError("rollback inventory entries must be a list")

    identities: set[tuple[str, str, str]] = set()
    ordering: list[tuple[str, str, str]] = []
    for entry in raw_inventory:
        if not isinstance(entry, Mapping):
            raise ValueError("rollback inventory entry must be an object")
        if set(entry) not in {_ENTRY_KEYS, _ENTRY_KEYS_WITH_STATUS}:
            raise ValueError("rollback inventory entry has an invalid shape")
        for key in ("source", "relation", "identity"):
            value = entry[key]
            if not isinstance(value, str) or not value:
                raise ValueError("rollback inventory entry identity fields must be non-empty strings")
        if "inventory_status" in entry and (
            not isinstance(entry["inventory_status"], str)
            or not entry["inventory_status"]
        ):
            raise ValueError("rollback inventory status must be a non-empty string")
        if entry["row"] is not None and not isinstance(entry["row"], Mapping):
            raise ValueError("rollback inventory row must be an object or null")

        key = (entry["source"], entry["relation"], entry["identity"])
        if key in identities:
            raise ValueError("rollback inventory contains duplicate entries")
        identities.add(key)
        ordering.append(key)

    if ordering != sorted(ordering):
        raise ValueError("rollback inventory entries are not strictly ordered")

    fingerprint = payload["inventory_sha256"]
    if not isinstance(fingerprint, str) or _FINGERPRINT.fullmatch(fingerprint) is None:
        raise ValueError("rollback inventory fingerprint must be lowercase SHA-256 hex")
    if (
        not isinstance(recomputed_digest, str)
        or _FINGERPRINT.fullmatch(recomputed_digest) is None
    ):
        raise ValueError("database rollback inventory digest must be lowercase SHA-256 hex")
    if fingerprint != recomputed_digest:
        raise ValueError("rollback inventory fingerprint does not match the database digest")

    entries = tuple(raw_inventory)
    # Even empty and absent-relation sentinels need a caller with broader
    # authority to classify their implications for linked liabilities.
    blockers = entries
    return ProductionPolicyRollbackInventoryV1(
        schema_version=_SCHEMA_VERSION,
        workspace_id=workspace_id,
        inventory=entries,
        inventory_sha256=fingerprint,
        status="blocked",
        blockers=blockers,
    )
