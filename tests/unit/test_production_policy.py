import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid4

import pytest

from salience.governance.policy import EffectClass
from salience.governance.production_policy import (
    ProductionApprovalBinding,
    ProductionPolicyVersion,
    load_policy,
    parse_approval_row,
    parse_policy_row,
)


def _document(*, expires_at: str | None = None) -> dict[str, object]:
    return {
        "schema_version": "salience.p1-production-policy.v1",
        "allowed_effects": ["write", "purchase"],
        "approval_required_effects": ["purchase"],
        "expires_at": expires_at or "2026-10-27T12:00:00+00:00",
        "jurisdiction_refs": ["US", "CA"],
        "publication_scope": {
            "platform": "fixture",
            "destination": "fixture://destination",
            "locale": "en-US",
            "territory": "US",
            "visibility": "private",
        },
    }


def _policy_row(
    *,
    document: dict[str, object] | None = None,
    workspace_id: UUID | None = None,
    status: str = "active",
    effective_at: datetime | None = None,
    database_time: datetime | None = None,
) -> dict[str, object]:
    now = database_time or datetime(2026, 10, 27, 11, 0, tzinfo=UTC)
    body = document if document is not None else _document()
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return {
        "id": UUID("00000000-0000-0000-0000-000000000001"),
        "workspace_id": workspace_id or UUID("00000000-0000-0000-0000-000000000002"),
        "status": status,
        "document": body,
        "document_hash": sha256(canonical.encode("utf-8")).hexdigest(),
        "effective_at": effective_at or now - timedelta(minutes=1),
        "database_time": now,
    }


class _Result:
    def __init__(self, row: object | None) -> None:
        self._row = row

    def fetchone(self) -> object | None:
        return self._row


class _Connection:
    def __init__(self, row: object | None) -> None:
        self.row = row
        self.statement = ""
        self.parameters: tuple[object, ...] = ()

    def execute(self, statement: str, parameters: tuple[object, ...]) -> _Result:
        self.statement = statement
        self.parameters = parameters
        return _Result(self.row)


def test_policy_document_v1_is_strict_and_hash_bound() -> None:
    workspace_id = UUID("00000000-0000-0000-0000-000000000002")
    now = datetime(2026, 10, 27, 11, 0, tzinfo=UTC)

    policy = parse_policy_row(
        _policy_row(workspace_id=workspace_id, database_time=now),
        workspace_id=workspace_id,
        database_time=now,
    )

    assert isinstance(policy, ProductionPolicyVersion)
    assert policy.id == UUID("00000000-0000-0000-0000-000000000001")
    assert policy.workspace_id == workspace_id
    assert policy.allowed_effects == frozenset(
        {EffectClass.WRITE, EffectClass.PURCHASE}
    )
    assert policy.approval_required_effects == frozenset({EffectClass.PURCHASE})
    assert policy.jurisdiction_refs == frozenset({"US", "CA"})
    assert policy.expires_at == datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
    assert policy.publication_scope is not None
    assert policy.publication_scope.platform == "fixture"
    assert policy.publication_scope.destination == "fixture://destination"

    unicode_document = _document()
    unicode_document["jurisdiction_refs"] = ["Québec"]
    unicode_policy = parse_policy_row(
        _policy_row(
            document=unicode_document,
            workspace_id=workspace_id,
            database_time=now,
        ),
        workspace_id=workspace_id,
        database_time=now,
    )
    assert unicode_policy.jurisdiction_refs == frozenset({"Québec"})

    malformed_documents = (
        {**_document(), "allowed_effects": ["write", "unknown-effect"]},
        {**_document(), "expires_at": "not-a-timestamp"},
        {**_document(), "expires_at": "2026-10-27T12:00:00"},
        {**_document(), "expires_at": "2026-10-27T10:59:59+00:00"},
    )
    for document in malformed_documents:
        row = _policy_row(
            document=document, workspace_id=workspace_id, database_time=now
        )
        with pytest.raises(PermissionError):
            parse_policy_row(row, workspace_id=workspace_id, database_time=now)

    invalid_rows = (
        _policy_row(
            workspace_id=workspace_id,
            effective_at=now + timedelta(microseconds=1),
            database_time=now,
        ),
        _policy_row(workspace_id=uuid4(), database_time=now),
        _policy_row(workspace_id=workspace_id, status="revoked", database_time=now),
    )
    for row in invalid_rows:
        with pytest.raises(PermissionError):
            parse_policy_row(row, workspace_id=workspace_id, database_time=now)

    mismatched_hash = _policy_row(workspace_id=workspace_id, database_time=now)
    mismatched_hash["document_hash"] = "0" * 64
    with pytest.raises(PermissionError):
        parse_policy_row(mismatched_hash, workspace_id=workspace_id, database_time=now)


def test_policy_loader_fails_closed_on_unknown_or_legacy_shapes() -> None:
    workspace_id = UUID("00000000-0000-0000-0000-000000000002")
    policy_id = UUID("00000000-0000-0000-0000-000000000001")
    now = datetime(2026, 10, 27, 11, 0, tzinfo=UTC)
    connection = _Connection(_policy_row(workspace_id=workspace_id, database_time=now))

    loaded = load_policy(
        connection, workspace_id=workspace_id, policy_version_id=policy_id
    )

    assert isinstance(loaded, ProductionPolicyVersion)
    assert loaded.id == policy_id
    assert "clock_timestamp()" in connection.statement
    assert "policy_versions" in connection.statement
    assert connection.parameters == (workspace_id, policy_id)

    malformed_documents: list[dict[str, object]] = []
    missing_key = _document()
    del missing_key["approval_required_effects"]
    malformed_documents.append(missing_key)
    malformed_documents.append({**_document(), "unrecognized": "legacy"})
    legacy_version = _document()
    legacy_version["schema_version"] = "salience.p1-production-policy.v0"
    malformed_documents.append(legacy_version)

    for document in malformed_documents:
        row = _policy_row(
            document=document, workspace_id=workspace_id, database_time=now
        )
        with pytest.raises(PermissionError):
            load_policy(
                _Connection(row),
                workspace_id=workspace_id,
                policy_version_id=policy_id,
            )

    with pytest.raises(PermissionError):
        load_policy(
            _Connection(None),
            workspace_id=workspace_id,
            policy_version_id=policy_id,
        )


def test_policy_loader_accepts_default_psycopg_tuple_rows() -> None:
    workspace_id = UUID("00000000-0000-0000-0000-000000000002")
    policy_id = UUID("00000000-0000-0000-0000-000000000001")
    policy_row = _policy_row(workspace_id=workspace_id)
    selected_columns = (
        "id",
        "workspace_id",
        "status",
        "document",
        "document_hash",
        "effective_at",
        "database_time",
    )
    default_psycopg_row = tuple(policy_row[column] for column in selected_columns)

    loaded = load_policy(
        _Connection(default_psycopg_row),
        workspace_id=workspace_id,
        policy_version_id=policy_id,
    )

    assert isinstance(default_psycopg_row, tuple)
    assert loaded.id == policy_id


def _approval_fixture() -> tuple[dict[str, object], ProductionApprovalBinding]:
    values = {
        "workspace_id": UUID("10000000-0000-0000-0000-000000000001"),
        "program_id": UUID("10000000-0000-0000-0000-000000000002"),
        "goal_id": UUID("10000000-0000-0000-0000-000000000003"),
        "goal_revision": 7,
        "intent_id": UUID("10000000-0000-0000-0000-000000000004"),
        "cycle_id": UUID("10000000-0000-0000-0000-000000000005"),
        "context_id": UUID("10000000-0000-0000-0000-000000000006"),
        "operation_id": UUID("10000000-0000-0000-0000-000000000007"),
        "subject_id": UUID("10000000-0000-0000-0000-000000000008"),
        "policy_version_id": UUID("10000000-0000-0000-0000-000000000009"),
        "policy_document_hash": "a" * 64,
        "effect_type": "purchase",
        "purpose": "approved_purchase",
        "artifact_sha256": "b" * 64,
        "account_id": UUID("10000000-0000-0000-0000-000000000010"),
        "provider_id": "fixture-provider",
        "expires_at": datetime(2026, 10, 27, 12, 0, tzinfo=UTC),
    }
    approval_id = UUID("10000000-0000-0000-0000-000000000011")
    expected = ProductionApprovalBinding(approval_request_id=approval_id, **values)
    def as_json_value(value: object) -> object:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, UUID):
            return str(value)
        return value

    context = {
        "schema_version": "salience.p1-effect-approval.v1",
        **{key: as_json_value(value) for key, value in values.items()},
    }
    row: dict[str, object] = {
        "id": approval_id,
        "workspace_id": values["workspace_id"],
        "policy_version_id": values["policy_version_id"],
        "effect_type": values["effect_type"],
        "status": "approved",
        "decided_by": "policy-owner@example.test",
        "decided_at": datetime(2026, 10, 27, 10, 0, tzinfo=UTC),
        "request_context": context,
        "job_id": None,
    }
    return row, expected


def test_approval_binding_matches_exact_operation_scope_and_expiry() -> None:
    row, expected = _approval_fixture()
    database_time = datetime(2026, 10, 27, 11, 0, tzinfo=UTC)

    binding = parse_approval_row(row, expected=expected, database_time=database_time)

    assert binding == replace(
        expected,
        decided_by="policy-owner@example.test",
        decided_at=datetime(2026, 10, 27, 10, 0, tzinfo=UTC),
    )

    context_field_mismatches: dict[str, object] = {
        "policy_version_id": str(uuid4()),
        "policy_document_hash": "c" * 64,
        "workspace_id": str(uuid4()),
        "program_id": str(uuid4()),
        "goal_id": str(uuid4()),
        "goal_revision": expected.goal_revision + 1,
        "intent_id": str(uuid4()),
        "cycle_id": str(uuid4()),
        "context_id": str(uuid4()),
        "operation_id": str(uuid4()),
        "subject_id": str(uuid4()),
        "effect_type": "write",
        "purpose": "different-purpose",
        "artifact_sha256": "c" * 64,
        "account_id": str(uuid4()),
        "provider_id": "different-provider",
        "expires_at": "2026-10-27T12:00:01+00:00",
    }
    for key, value in context_field_mismatches.items():
        mismatched = dict(row)
        mismatched["request_context"] = {**row["request_context"], key: value}
        with pytest.raises(PermissionError):
            parse_approval_row(
                mismatched, expected=expected, database_time=database_time
            )

    row_field_mismatches = (
        {**row, "id": uuid4()},
        {**row, "workspace_id": uuid4()},
        {**row, "policy_version_id": uuid4()},
        {**row, "effect_type": "write"},
        {**row, "status": "pending"},
        {**row, "decided_by": None},
        {**row, "decided_at": None},
        {**row, "decided_at": datetime(2026, 10, 27, 12, 0)},
        {
            **row,
            "request_context": {
                **row["request_context"],
                "schema_version": "legacy",
            },
        },
    )
    for mismatched in row_field_mismatches:
        with pytest.raises(PermissionError):
            parse_approval_row(
                mismatched, expected=expected, database_time=database_time
            )
