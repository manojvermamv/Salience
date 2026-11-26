"""Verify and record permanent closure of the exact original fixture send."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from uuid import UUID

import psycopg
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from psycopg.types.json import Jsonb

from salience.cycles._production_recovery_contracts import (
    RecoveryEventConflict,
    _require_aware,
    _require_uuid,
)
from salience.cycles._production_recovery_work import ProductionRecoveryWorkRepository
from salience.fixtures.publication_send_barrier import (
    FixturePublicationBarrierScope,
    FixturePublicationNoEffectReceipt,
    FixturePublicationSend,
    verify_fixture_publication_no_effect,
)


@dataclass(frozen=True, slots=True)
class ProductionClaimedPublicationClosureTrust:
    """Independently configured trust for one fixture acceptance boundary."""

    scope: FixturePublicationBarrierScope
    public_key: Ed25519PublicKey

    def __post_init__(self) -> None:
        if not isinstance(self.scope, FixturePublicationBarrierScope) or not isinstance(self.public_key, Ed25519PublicKey):
            raise ValueError("configured fixture scope and Ed25519 public key required")


@dataclass(frozen=True, slots=True)
class ProductionClaimedPublicationClosureCommand:
    """Canonical source and verified signed proof presented for stage authorization."""

    work_id: UUID
    workspace_id: UUID
    case_id: UUID
    case_revision: int
    workspace_stop_revision: int
    goal_stop_revision: int
    proof: FixturePublicationNoEffectReceipt
    signer_sha256: str


@dataclass(frozen=True, slots=True)
class ProductionClaimedPublicationClosureAuthorization:
    """Independent current policy and route decisions for this exact proof intake."""

    command: ProductionClaimedPublicationClosureCommand
    policy_decision_id: UUID
    route_decision_id: UUID
    expires_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.command, ProductionClaimedPublicationClosureCommand):
            raise ValueError("exact claimed publication closure command required")
        _require_uuid(self.policy_decision_id, "closure policy decision ID")
        _require_uuid(self.route_decision_id, "closure route decision ID")
        object.__setattr__(self, "expires_at", _require_aware(self.expires_at, "closure authorization expiry"))


@dataclass(frozen=True, slots=True)
class ProductionClaimedPublicationClosureReceipt:
    work_id: UUID
    operation_id: UUID
    closure_id: UUID
    policy_decision_id: UUID
    route_decision_id: UUID


def record_claimed_publication_closure(
    repository: ProductionRecoveryWorkRepository,
    authority: object,
    trust: ProductionClaimedPublicationClosureTrust | None,
    work_id: UUID,
    proof: FixturePublicationNoEffectReceipt,
) -> ProductionClaimedPublicationClosureReceipt:
    _require_uuid(work_id, "work ID")
    if not isinstance(trust, ProductionClaimedPublicationClosureTrust):
        raise PermissionError("configured fixture trust required for claimed publication closure")
    if not isinstance(proof, FixturePublicationNoEffectReceipt):
        raise PermissionError("signed fixture finality proof required for claimed publication closure")

    try:
        with repository._connect() as connection:
            source = connection.execute(
                "SELECT public.v4_read_claimed_publication_closure_source_v1(%s) AS source",
                (work_id,),
            ).fetchone()["source"]
    except psycopg.errors.InsufficientPrivilege as error:
        raise PermissionError(error.diag.message_primary) from error
    scope = trust.scope
    if (
        source is None
        or source["work_id"] != str(work_id)
        or source["environment_id"] != str(scope.environment_id)
        or source["namespace"] not in (None, scope.namespace)
    ):
        raise PermissionError("fixture trust does not match the canonical publication environment")

    send = FixturePublicationSend(
        operation_id=UUID(source["operation_id"]), request_sha256=source["request_fingerprint"],
        provider_id=source["provider_id"], account_id=source["account_id"], original_key=source["original_key"],
        permit_id=UUID(source["permit_id"]), claim_id=UUID(source["claim_id"]), generation=source["generation"],
    )
    verified = verify_fixture_publication_no_effect(
        proof, scope=scope, expected_send=send, public_key=trust.public_key,
    )
    signer_sha256 = sha256(trust.public_key.public_bytes_raw()).hexdigest()
    command = ProductionClaimedPublicationClosureCommand(
        work_id=work_id,
        workspace_id=UUID(source["workspace_id"]),
        case_id=UUID(source["case_id"]),
        case_revision=source["case_revision"],
        workspace_stop_revision=source["workspace_stop_revision"],
        goal_stop_revision=source["goal_stop_revision"],
        proof=verified,
        signer_sha256=signer_sha256,
    )
    authorize = getattr(authority, "authorize_claimed_publication_closure", None)
    if not callable(authorize):
        raise PermissionError("current claimed publication closure authorization required")
    decision = authorize(command)
    if (
        not isinstance(decision, ProductionClaimedPublicationClosureAuthorization)
        or decision.command != command
        or decision.expires_at <= repository._now()
    ):
        raise PermissionError("current claimed publication closure authorization must match the exact proof")

    signed_evidence = {
        "operation_id": str(send.operation_id),
        "permit_id": str(send.permit_id),
        "claim_id": str(send.claim_id),
        "generation": send.generation,
        "request_fingerprint": send.request_sha256,
        "environment_id": str(scope.environment_id),
        "namespace": scope.namespace,
        "barrier_id": str(scope.barrier_id),
        "closure_id": str(verified.closure_id),
        "signer_sha256": signer_sha256,
        "signature_hex": verified.signature.hex(),
    }
    evidence = Jsonb(signed_evidence | {
        "case_revision": command.case_revision,
        "workspace_stop_revision": command.workspace_stop_revision,
        "goal_stop_revision": command.goal_stop_revision,
        "policy_decision_id": str(decision.policy_decision_id),
        "route_decision_id": str(decision.route_decision_id),
        "authority_expires_at": decision.expires_at.isoformat(),
    })
    try:
        with repository._connect() as connection:
            result = connection.execute(
                "SELECT public.v4_record_claimed_publication_closure_v1(%s,%s) AS result",
                (work_id, evidence),
            ).fetchone()["result"]
            if (
                not isinstance(result, dict)
                or result.get("schema_version") != "salience.p1-claimed-publication-closure.v1"
                or result.get("work_id") != str(work_id)
                or result.get("workspace_id") != str(command.workspace_id)
                or result.get("case_id") != str(command.case_id)
                or result.get("case_revision") != command.case_revision
                or any(result.get(key) != value for key, value in signed_evidence.items())
            ):
                raise RecoveryEventConflict("closure receipt does not match the exact original proof")
            return ProductionClaimedPublicationClosureReceipt(
                work_id, send.operation_id, verified.closure_id,
                UUID(result["policy_decision_id"]), UUID(result["route_decision_id"]),
            )
    except psycopg.errors.InsufficientPrivilege as error:
        raise PermissionError(error.diag.message_primary) from error
    except psycopg.errors.UniqueViolation as error:
        raise RecoveryEventConflict(error.diag.message_primary) from error
    except psycopg.errors.InvalidParameterValue as error:
        if error.diag.message_primary == "closure evidence conflict for the original claim":
            raise RecoveryEventConflict(error.diag.message_primary) from error
        raise
