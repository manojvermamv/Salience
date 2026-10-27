"""Strict projections for canonical production policy and approval rows."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from hashlib import sha256
import json
import re
from typing import Mapping
from uuid import UUID

from salience.governance.policy import EffectClass


_POLICY_SCHEMA_VERSION = "salience.p1-production-policy.v1"
_APPROVAL_SCHEMA_VERSION = "salience.p1-effect-approval.v1"
_POLICY_REQUIRED_KEYS = frozenset(
    {
        "schema_version",
        "allowed_effects",
        "approval_required_effects",
        "expires_at",
        "jurisdiction_refs",
    }
)
_PUBLICATION_SCOPE_KEYS = frozenset(
    {"platform", "destination", "locale", "territory", "visibility"}
)
_APPROVAL_BINDING_KEYS = (
    "workspace_id",
    "program_id",
    "goal_id",
    "goal_revision",
    "intent_id",
    "cycle_id",
    "context_id",
    "operation_id",
    "subject_id",
    "policy_version_id",
    "policy_document_hash",
    "effect_type",
    "purpose",
    "artifact_sha256",
    "account_id",
    "provider_id",
    "expires_at",
)
_PUBLICATION_BINDING_KEYS = (
    "publication_request_id",
    "job_id",
    "workflow_id",
    "workflow_run_id",
    "publication_plan_id",
    "publication_attempt_id",
)
_OPTIONAL_PUBLICATION_IDENTITY_KEYS = ("external_effect_id", "remote_receipt_id")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class ProductionPublicationScope:
    platform: str
    destination: str
    locale: str
    territory: str
    visibility: str


@dataclass(frozen=True)
class ProductionPolicyVersion:
    id: UUID
    workspace_id: UUID
    status: str
    document_hash: str
    effective_at: datetime
    expires_at: datetime
    allowed_effects: frozenset[EffectClass]
    approval_required_effects: frozenset[EffectClass]
    jurisdiction_refs: frozenset[str]
    publication_scope: ProductionPublicationScope | None = None


@dataclass(frozen=True)
class ProductionApprovalBinding:
    approval_request_id: UUID
    workspace_id: UUID
    program_id: UUID
    goal_id: UUID
    goal_revision: int
    intent_id: UUID
    cycle_id: UUID
    context_id: UUID
    operation_id: UUID
    subject_id: UUID
    policy_version_id: UUID
    policy_document_hash: str
    effect_type: str
    purpose: str
    artifact_sha256: str
    account_id: UUID
    provider_id: str
    expires_at: datetime
    publication_request_id: UUID | None = None
    job_id: UUID | None = None
    workflow_id: str | None = None
    workflow_run_id: str | None = None
    publication_plan_id: UUID | None = None
    publication_attempt_id: UUID | None = None
    external_effect_id: UUID | None = None
    remote_receipt_id: UUID | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None


def parse_policy_row(
    row: Mapping[str, object], *, workspace_id: UUID, database_time: datetime
) -> ProductionPolicyVersion:
    """Validate and project an active policy row against database time."""

    now = _aware_datetime(database_time, "database_time")
    row_workspace_id = _uuid(row.get("workspace_id"), "workspace_id")
    if row_workspace_id != workspace_id:
        raise PermissionError("production policy workspace mismatch")
    if row.get("status") != "active":
        raise PermissionError("production policy is not active")

    effective_at = _aware_datetime(row.get("effective_at"), "effective_at")
    if effective_at > now:
        raise PermissionError("production policy is not yet effective")

    document_value = row.get("document")
    if not isinstance(document_value, Mapping):
        raise PermissionError("production policy document is malformed")
    document = dict(document_value)
    keys = frozenset(document)
    if keys not in {
        _POLICY_REQUIRED_KEYS,
        _POLICY_REQUIRED_KEYS | {"publication_scope"},
    }:
        raise PermissionError("production policy document has unknown or missing keys")
    if document.get("schema_version") != _POLICY_SCHEMA_VERSION:
        raise PermissionError("production policy document version is unsupported")

    stored_hash = row.get("document_hash")
    if (
        not isinstance(stored_hash, str)
        or _SHA256_PATTERN.fullmatch(stored_hash) is None
    ):
        raise PermissionError("production policy document hash is malformed")
    try:
        canonical_document = json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise PermissionError(
            "production policy document is not canonical JSON"
        ) from exc
    calculated_hash = sha256(canonical_document.encode("utf-8")).hexdigest()
    if calculated_hash != stored_hash:
        raise PermissionError("production policy document hash mismatch")

    allowed_effects = _effect_set(document.get("allowed_effects"), "allowed_effects")
    approval_required_effects = _effect_set(
        document.get("approval_required_effects"), "approval_required_effects"
    )
    expires_at = _document_datetime(document.get("expires_at"), "expires_at")
    if expires_at <= now:
        raise PermissionError("production policy is expired")
    jurisdiction_refs = _string_set(
        document.get("jurisdiction_refs"), "jurisdiction_refs"
    )
    publication_scope = None
    if "publication_scope" in document:
        publication_scope = _publication_scope(document.get("publication_scope"))

    return ProductionPolicyVersion(
        id=_uuid(row.get("id"), "id"),
        workspace_id=row_workspace_id,
        status="active",
        document_hash=stored_hash,
        effective_at=effective_at,
        expires_at=expires_at,
        allowed_effects=allowed_effects,
        approval_required_effects=approval_required_effects,
        jurisdiction_refs=jurisdiction_refs,
        publication_scope=publication_scope,
    )


def load_policy(
    connection: object, *, workspace_id: UUID, policy_version_id: UUID
) -> ProductionPolicyVersion:
    """Read and validate a policy from its canonical row using database time."""

    result = connection.execute(  # type: ignore[attr-defined]
        """
        SELECT id, workspace_id, status, document, document_hash, effective_at,
               clock_timestamp() AS database_time
        FROM policy_versions
        WHERE workspace_id = %s AND id = %s
        """,
        (workspace_id, policy_version_id),
    )
    row = result.fetchone()
    if not isinstance(row, Mapping):
        raise PermissionError("canonical production policy required")
    if _uuid(row.get("id"), "id") != policy_version_id:
        raise PermissionError("canonical production policy identity mismatch")
    return parse_policy_row(
        row,
        workspace_id=workspace_id,
        database_time=_aware_datetime(row.get("database_time"), "database_time"),
    )


def parse_approval_row(
    row: Mapping[str, object],
    *,
    expected: ProductionApprovalBinding,
    database_time: datetime,
) -> ProductionApprovalBinding:
    """Validate a current approved row against one exact operation binding."""

    now = _aware_datetime(database_time, "database_time")
    if row.get("status") != "approved":
        raise PermissionError("production approval is not currently approved")
    approval_id = _uuid(row.get("id"), "id")
    if approval_id != expected.approval_request_id:
        raise PermissionError("production approval identity mismatch")
    for field in ("workspace_id", "policy_version_id"):
        if _uuid(row.get(field), field) != getattr(expected, field):
            raise PermissionError(f"production approval {field} mismatch")
    if row.get("effect_type") != expected.effect_type:
        raise PermissionError("production approval effect mismatch")

    decided_by = row.get("decided_by")
    if not isinstance(decided_by, str) or not decided_by.strip():
        raise PermissionError("production approval decision actor is missing")
    decided_at = _aware_datetime(row.get("decided_at"), "decided_at")
    if decided_at > now:
        raise PermissionError("production approval decision is from the future")

    expected_expires_at = _aware_datetime(expected.expires_at, "expected expires_at")
    if expected_expires_at <= now:
        raise PermissionError("production approval is expired")

    context_value = row.get("request_context")
    if not isinstance(context_value, Mapping):
        raise PermissionError("production approval binding is malformed")
    context = dict(context_value)
    publication_present = any(key in context for key in _PUBLICATION_BINDING_KEYS)
    expected_publication = any(
        getattr(expected, key) is not None for key in _PUBLICATION_BINDING_KEYS
    )
    if publication_present != expected_publication:
        raise PermissionError("production approval publication scope mismatch")
    if expected_publication and any(
        getattr(expected, key) is None for key in _PUBLICATION_BINDING_KEYS
    ):
        raise PermissionError("production approval publication binding is incomplete")

    context_keys = {"schema_version", *_APPROVAL_BINDING_KEYS}
    if expected_publication:
        context_keys.update(_PUBLICATION_BINDING_KEYS)
    for key in _OPTIONAL_PUBLICATION_IDENTITY_KEYS:
        if getattr(expected, key) is not None:
            if not expected_publication:
                raise PermissionError(
                    "production approval effect identity lacks publication scope"
                )
            context_keys.add(key)
    if frozenset(context) != frozenset(context_keys):
        raise PermissionError("production approval binding has unknown or missing keys")
    if context.get("schema_version") != _APPROVAL_SCHEMA_VERSION:
        raise PermissionError("production approval schema version is unsupported")

    parsed_values: dict[str, object] = {}
    for field in _APPROVAL_BINDING_KEYS:
        parsed_values[field] = _parse_context_binding(field, context.get(field))
    if parsed_values["expires_at"] != expected_expires_at:
        raise PermissionError("production approval expiry mismatch")
    for field in _APPROVAL_BINDING_KEYS:
        if field == "expires_at":
            continue
        if parsed_values[field] != getattr(expected, field):
            raise PermissionError(f"production approval {field} mismatch")

    for field in _PUBLICATION_BINDING_KEYS:
        if expected_publication:
            parsed_values[field] = _parse_context_binding(field, context.get(field))
            if parsed_values[field] != getattr(expected, field):
                raise PermissionError(f"production approval {field} mismatch")
    for field in _OPTIONAL_PUBLICATION_IDENTITY_KEYS:
        if field in context:
            parsed_values[field] = _parse_context_binding(field, context.get(field))
            if parsed_values[field] != getattr(expected, field):
                raise PermissionError(f"production approval {field} mismatch")

    if expected_publication and _uuid(row.get("job_id"), "job_id") != expected.job_id:
        raise PermissionError("production approval row job mismatch")

    return replace(
        expected,
        expires_at=parsed_values["expires_at"],
        decided_by=decided_by,
        decided_at=decided_at,
    )


def _effect_set(value: object, field: str) -> frozenset[EffectClass]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise PermissionError(f"production policy {field} is malformed")
    if len(value) != len(set(value)):
        raise PermissionError(f"production policy {field} contains duplicates")
    try:
        return frozenset(EffectClass(item) for item in value)
    except ValueError as exc:
        raise PermissionError(
            f"production policy {field} has an unsupported effect"
        ) from exc


def _string_set(value: object, field: str) -> frozenset[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise PermissionError(f"production policy {field} is malformed")
    if len(value) != len(set(value)):
        raise PermissionError(f"production policy {field} contains duplicates")
    return frozenset(value)


def _publication_scope(value: object) -> ProductionPublicationScope:
    if not isinstance(value, Mapping) or frozenset(value) != _PUBLICATION_SCOPE_KEYS:
        raise PermissionError(
            "production publication scope has unknown or missing keys"
        )
    fields: dict[str, str] = {}
    for key in _PUBLICATION_SCOPE_KEYS:
        item = value.get(key)
        if not isinstance(item, str) or not item.strip():
            raise PermissionError(f"production publication scope {key} is malformed")
        fields[key] = item
    return ProductionPublicationScope(**fields)


def _document_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise PermissionError(
            f"production policy {field} must be an explicit timestamp"
        )
    try:
        return _aware_datetime(datetime.fromisoformat(value), field)
    except ValueError as exc:
        raise PermissionError(f"production policy {field} is malformed") from exc


def _aware_datetime(value: object, field: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise PermissionError(f"production authority {field} must be timezone-aware")
    return value


def _uuid(value: object, field: str) -> UUID:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            parsed = UUID(value)
        except ValueError as exc:
            raise PermissionError(f"production authority {field} is malformed") from exc
        if str(parsed) != value:
            raise PermissionError(f"production authority {field} is not canonical")
        return parsed
    raise PermissionError(f"production authority {field} is missing")


def _parse_context_binding(field: str, value: object) -> object:
    if field in {
        "workspace_id",
        "program_id",
        "goal_id",
        "intent_id",
        "cycle_id",
        "context_id",
        "operation_id",
        "subject_id",
        "policy_version_id",
        "account_id",
        *_PUBLICATION_BINDING_KEYS[:2],
        *_PUBLICATION_BINDING_KEYS[4:],
        *_OPTIONAL_PUBLICATION_IDENTITY_KEYS,
    }:
        return _uuid(value, field)
    if field == "goal_revision":
        if type(value) is not int or value <= 0:
            raise PermissionError("production approval goal_revision is malformed")
        return value
    if field in {"policy_document_hash", "artifact_sha256"}:
        if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
            raise PermissionError(f"production approval {field} is malformed")
        return value
    if field == "expires_at":
        return _document_datetime(value, field)
    if not isinstance(value, str) or not value.strip():
        raise PermissionError(f"production approval {field} is malformed")
    return value
