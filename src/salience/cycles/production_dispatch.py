"""Effects-disabled production authorization transactions.

This module persists authorization evidence and never calls a provider, starts a
workflow, obtains a credential, or enables an effect adapter.  The injected
route-fence protocol is a local boundary contract until an owning platform
supplies and qualifies its versioned implementation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import re
from typing import Protocol
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from salience.cycles.authority import current_authority
from salience.cycles.baselines import resolve_baseline
from salience.cycles.contracts import (
    AuthoritySnapshot,
    GoalSpecV3,
    RunContextV2,
    RunContextV3,
    parse_goal,
)
from salience.governance.policy import EffectClass
from salience.governance.production_policy import (
    ProductionApprovalBinding,
    ProductionPolicyVersion,
    parse_approval_row,
    parse_policy_row,
)
from salience.observability.production_authorization import ProductionAuthorizationMetrics


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_ACTION = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_REASON_CODES = frozenset(
    {
        "approval_expired",
        "approval_invalid",
        "approval_missing",
        "authority_changed",
        "authority_unavailable",
        "baseline_missing",
        "budget_missing",
        "budget_unavailable",
        "context_invalid",
        "current_operation_missing",
        "dispatch_already_claimed",
        "effect_not_allowed",
        "generation_advance_required",
        "grant_expired",
        "grant_missing",
        "identity_expired",
        "invalid_binding",
        "invalid_request",
        "operation_not_runnable",
        "permit_expired",
        "permit_missing",
        "policy_expired",
        "policy_invalid",
        "policy_missing",
        "primary_unavailable",
        "program_inactive",
        "provider_account_invalid",
        "provider_mismatch",
        "route_fence_denied",
        "route_fence_invalid",
        "route_fence_unavailable",
        "reconciliation_unresolved",
        "stale_revision",
        "stop_active",
        "stop_scope_missing",
        "subject_mismatch",
        "unknown_action",
        "workspace_mismatch",
        "other",
    }
)
_SNAPSHOT_METADATA = frozenset(
    {
        "schema_version",
        "generation",
        "recovery_command_id",
        "subject_revision",
        "grant_ids",
        "policy_document_hash",
        "approval_expires_at",
        "route_fence_decision_id",
        "workspace_stop_revision",
        "goal_stop_revision",
        "baseline_approval_id",
        "budget_reservation_ids",
    }
)
_ROUTE_FIELDS = frozenset(
    {
        "deployment_id",
        "environment_id",
        "route_id",
        "slot_id",
        "owner_id",
        "fencing_epoch",
        "route_manifest_sha256",
        "artifact_lock_sha256",
    }
)
_OPTIONAL_ROUTE_FIELDS = frozenset({"route_tuple_sha256"})
_PUBLICATION_FIELDS = frozenset(
    {
        "publication_request_id",
        "job_id",
        "workflow_id",
        "workflow_run_id",
        "publication_plan_id",
        "publication_attempt_id",
        "external_effect_id",
        "remote_receipt_id",
    }
)


class ProductionAuthorizationDenied(PermissionError):
    """A bounded current-authority denial with no sensitive row values."""

    def __init__(self, reason_code: str) -> None:
        safe_code = reason_code if reason_code in _REASON_CODES else "other"
        self.reason_code = safe_code
        super().__init__(safe_code)


@dataclass(frozen=True, slots=True)
class RouteBinding:
    deployment_id: UUID
    environment_id: UUID
    route_id: UUID
    slot_id: UUID
    owner_id: UUID
    fencing_epoch: int
    route_manifest_sha256: str
    artifact_lock_sha256: str
    route_tuple_sha256: str | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, UUID)
            for value in (
                self.deployment_id,
                self.environment_id,
                self.route_id,
                self.slot_id,
                self.owner_id,
            )
        ):
            raise ValueError("route binding UUIDs are required")
        if type(self.fencing_epoch) is not int or self.fencing_epoch < 1:
            raise ValueError("positive route fencing epoch required")
        if not _SHA256.fullmatch(self.route_manifest_sha256) or not _SHA256.fullmatch(
            self.artifact_lock_sha256
        ):
            raise ValueError("route binding digests must be lowercase SHA-256")
        if self.route_tuple_sha256 is not None and not _SHA256.fullmatch(
            self.route_tuple_sha256
        ):
            raise ValueError("route tuple digest must be lowercase SHA-256")


@dataclass(frozen=True, slots=True)
class RouteFenceRequest(RouteBinding):
    operation_id: UUID
    permit_id: UUID
    action_kind: str
    # Tuple evidence is authenticated by route_manifest_sha256 and recorded
    # with the permit. It is not a member of the route-fence request identity.
    route_tuple_sha256: str | None = field(default=None, kw_only=True, compare=False)

    def __post_init__(self) -> None:
        super(RouteFenceRequest, self).__post_init__()
        if not isinstance(self.operation_id, UUID) or not isinstance(self.permit_id, UUID):
            raise ValueError("route operation and permit UUIDs are required")
        if not isinstance(self.action_kind, str) or not _ACTION.fullmatch(self.action_kind):
            raise ValueError("bounded route action kind required")


@dataclass(frozen=True, slots=True)
class RouteFenceDecision:
    authorized: bool
    decision_id: UUID | None
    reason_code: str | None
    request: RouteFenceRequest

    def __post_init__(self) -> None:
        if type(self.authorized) is not bool:
            raise ValueError("route-fence authorization must be explicit")
        if not isinstance(self.request, RouteFenceRequest):
            raise ValueError("route-fence request tuple required")
        if self.authorized:
            if not isinstance(self.decision_id, UUID) or self.reason_code is not None:
                raise ValueError("allowed route decision needs one decision ID")
        elif self.decision_id is not None or not isinstance(self.reason_code, str):
            raise ValueError("denied route decision needs a bounded reason")
        elif self.reason_code not in _REASON_CODES:
            raise ValueError("route-fence reason code is outside the bounded set")


class RouteFenceAuthority(Protocol):
    """Injected route-fence interface; target version remains unpinned."""

    def authorize_action(self, request: RouteFenceRequest) -> RouteFenceDecision: ...


@dataclass(frozen=True, slots=True)
class PublicationEffectBinding:
    publication_request_id: UUID
    job_id: UUID
    workflow_id: str
    workflow_run_id: str
    publication_plan_id: UUID
    publication_attempt_id: UUID
    external_effect_id: UUID
    remote_receipt_id: UUID | None = None

    def __post_init__(self) -> None:
        for field in (
            "publication_request_id",
            "job_id",
            "publication_plan_id",
            "publication_attempt_id",
            "external_effect_id",
        ):
            if not isinstance(getattr(self, field), UUID):
                raise ValueError(f"publication binding {field} UUID required")
        if self.remote_receipt_id is not None and not isinstance(
            self.remote_receipt_id, UUID
        ):
            raise ValueError("publication receipt UUID required when present")
        for field in ("workflow_id", "workflow_run_id"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip() or len(value) > 256:
                raise ValueError(f"bounded publication {field} required")


@dataclass(frozen=True, slots=True)
class ProductionPermitRequest:
    workspace_id: UUID
    goal_id: UUID
    goal_revision: int
    program_id: UUID
    intent_id: UUID
    cycle_id: UUID
    context_id: UUID
    operation_id: UUID
    subject_id: UUID
    policy_version_id: UUID
    approval_request_id: UUID
    required_scopes: tuple[str, ...]
    effect_type: str
    purpose: str
    artifact_sha256: str
    account_id: UUID
    budget_id: UUID
    budget_reservation_id: UUID
    provider_id: str
    idempotency_key: str
    route: RouteBinding
    execution_deadline: datetime
    ttl_seconds: int
    publication_binding: PublicationEffectBinding | None = None
    generation: int = 1
    recovery_command_id: UUID | None = None

    def __post_init__(self) -> None:
        for field in (
            "workspace_id",
            "goal_id",
            "program_id",
            "intent_id",
            "cycle_id",
            "context_id",
            "operation_id",
            "subject_id",
            "policy_version_id",
            "approval_request_id",
            "account_id",
            "budget_id",
            "budget_reservation_id",
        ):
            if not isinstance(getattr(self, field), UUID):
                raise ValueError(f"production permit {field} UUID required")
        if type(self.goal_revision) is not int or self.goal_revision < 1:
            raise ValueError("positive goal revision required")
        if type(self.generation) is not int or self.generation < 1:
            raise ValueError("positive production permit generation required")
        if self.generation == 1 and self.recovery_command_id is not None:
            raise ValueError("first production permit generation cannot use a recovery command")
        if self.generation > 1 and not isinstance(self.recovery_command_id, UUID):
            raise ValueError("later production permit generations require a typed recovery command")
        if (
            not isinstance(self.required_scopes, tuple)
            or not self.required_scopes
            or any(not isinstance(scope, str) or not scope.strip() for scope in self.required_scopes)
            or len(set(self.required_scopes)) != len(self.required_scopes)
        ):
            raise ValueError("unique required production scopes are required")
        if any(len(scope) > 128 for scope in self.required_scopes):
            raise ValueError("production scope exceeds its bound")
        if self.effect_type == "fixture_execution" or not isinstance(
            self.effect_type, str
        ):
            raise ValueError("fixture and missing effect types are not production effects")
        try:
            EffectClass(self.effect_type)
        except ValueError as exc:
            raise ValueError("known production effect type required") from exc
        if not isinstance(self.purpose, str) or not self.purpose.strip() or len(self.purpose) > 256:
            raise ValueError("bounded production purpose required")
        if not isinstance(self.artifact_sha256, str) or not _SHA256.fullmatch(
            self.artifact_sha256
        ):
            raise ValueError("production artifact SHA-256 required")
        if self.account_id == UUID(int=0) or self.provider_id in {"fixture.noop", ""}:
            raise ValueError("fixture account or provider cannot authorize production")
        if not isinstance(self.provider_id, str) or len(self.provider_id) > 128:
            raise ValueError("bounded provider identity required")
        if not isinstance(self.idempotency_key, str) or not self.idempotency_key.strip() or len(
            self.idempotency_key
        ) > 256:
            raise ValueError("bounded original idempotency key required")
        if not isinstance(self.route, RouteBinding):
            raise ValueError("exact route binding required")
        if (
            not isinstance(self.execution_deadline, datetime)
            or self.execution_deadline.tzinfo is None
            or self.execution_deadline.utcoffset() is None
        ):
            raise ValueError("timezone-aware execution deadline required")
        if type(self.ttl_seconds) is not int or not 1 <= self.ttl_seconds <= 60:
            raise ValueError("production permit TTL must be between 1 and 60 seconds")
        if self.publication_binding is not None and not isinstance(
            self.publication_binding, PublicationEffectBinding
        ):
            raise ValueError("typed publication binding required")


@dataclass(frozen=True, slots=True)
class ProductionRecoveryCommandReference:
    """Typed Task 7 evidence reference consumed by same-operation Task 8 paths."""

    event_id: UUID
    work_id: UUID
    operation_id: UUID
    request_fingerprint: str
    original_idempotency_key: str
    case_id: UUID
    case_revision: int
    stage_decision_id: UUID
    policy_decision_id: UUID
    workspace_stop_revision: int
    goal_stop_revision: int
    route_decision_id: UUID

    def __post_init__(self) -> None:
        for name in (
            "event_id", "work_id", "operation_id", "case_id",
            "stage_decision_id", "policy_decision_id", "route_decision_id",
        ):
            if not isinstance(getattr(self, name), UUID):
                raise ValueError(f"recovery command reference {name} UUID required")
        if not isinstance(self.request_fingerprint, str) or not _SHA256.fullmatch(
            self.request_fingerprint
        ):
            raise ValueError("recovery command reference request fingerprint required")
        if (
            not isinstance(self.original_idempotency_key, str)
            or not self.original_idempotency_key.strip()
            or len(self.original_idempotency_key) > 256
        ):
            raise ValueError("original bounded idempotency key required")
        if type(self.case_revision) is not int or self.case_revision < 1:
            raise ValueError("positive recovery case revision required")
        for name in ("workspace_stop_revision", "goal_stop_revision"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"positive {name} required")


@dataclass(frozen=True, slots=True)
class ProductionPermitResult:
    permit_id: UUID
    operation_id: UUID
    generation: int
    expires_at: datetime
    claim_id: UUID | None
    dispatch_allowed: bool


@dataclass(frozen=True, slots=True)
class _AuthorityFacts:
    workspace_id: UUID
    goal_id: UUID
    goal_revision: int
    program_id: UUID
    intent_id: UUID
    cycle_id: UUID
    context_id: UUID
    operation_id: UUID
    subject_id: UUID
    context: RunContextV2 | RunContextV3
    policy: ProductionPolicyVersion
    approval: ProductionApprovalBinding
    subject_revision: int
    grant_ids: tuple[UUID, ...]
    workspace_stop_revision: int
    goal_stop_revision: int
    baseline_approval_id: UUID
    budget_reservation_ids: tuple[UUID, ...]
    expires_at: datetime
    traceparent: str


class ProductionEffectDispatch:
    """Synchronous primary-database permit, claim and action barriers."""

    def __init__(
        self,
        database_url: str,
        route_fence: RouteFenceAuthority,
        *,
        metrics: ProductionAuthorizationMetrics | None = None,
    ) -> None:
        if not isinstance(database_url, str) or not database_url.strip():
            raise ValueError("production dispatch database URL required")
        if not callable(getattr(route_fence, "authorize_action", None)):
            raise ValueError("route-fence authority must implement authorize_action")
        self.database_url = database_url
        self.route_fence = route_fence
        self.metrics = metrics

    def issue_permit(self, request: ProductionPermitRequest) -> ProductionPermitResult:
        if not isinstance(request, ProductionPermitRequest):
            raise ValueError("typed production permit request required")
        if request.route.route_tuple_sha256 is None:
            raise ProductionAuthorizationDenied("invalid_binding")
        attempt_id = uuid4()
        try:
            result = self._issue_permit(request)
        except ProductionAuthorizationDenied as denial:
            self._record_decision("issue", getattr(request, "effect_type", "other"), "deny", denial.reason_code)
            self._persist_denial_event(
                request.operation_id,
                "issue",
                denial.reason_code,
                permit_id=None,
                request=request if isinstance(request, ProductionPermitRequest) else None,
                attempt_id=attempt_id,
            )
            raise
        except psycopg.OperationalError:
            self._record_primary_outage("issue", getattr(request, "effect_type", "other"))
            raise
        except (psycopg.errors.RaiseException, psycopg.IntegrityError) as error:
            if request.generation <= 1:
                raise
            denial = ProductionAuthorizationDenied("generation_advance_required")
            self._record_decision("issue", request.effect_type, "deny", denial.reason_code)
            self._persist_denial_event(
                request.operation_id,
                "issue",
                denial.reason_code,
                permit_id=None,
                request=request,
                attempt_id=attempt_id,
            )
            raise denial from error
        self._record_decision("issue", request.effect_type, "allow", "none")
        return result

    def _current_generation_for_reissue(
        self, request: ProductionPermitRequest, recovery_command_id: UUID
    ) -> int:
        """Read the current primary pointer before the recovery wrapper builds its next request."""
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                """SELECT dispatch.generation,dispatch.idempotency_key,
                          permit.recovery_command_id
                   FROM public.v4_production_current_dispatch dispatch
                   JOIN public.v4_production_effect_permits permit
                     ON permit.permit_id=dispatch.current_permit_id
                    AND permit.operation_id=dispatch.operation_id
                    AND permit.generation=dispatch.generation
                   WHERE dispatch.operation_id=%s""",
                (request.operation_id,),
            ).fetchone()
        if row is None or row["idempotency_key"] != request.idempotency_key:
            raise ProductionAuthorizationDenied("generation_advance_required")
        if row["recovery_command_id"] == recovery_command_id:
            return row["generation"]
        return row["generation"] + 1

    def _issue_permit(self, request: ProductionPermitRequest) -> ProductionPermitResult:
        if not isinstance(request, ProductionPermitRequest):
            raise ValueError("typed production permit request required")
        if request.route.route_tuple_sha256 is None:
            raise ProductionAuthorizationDenied("invalid_binding")
        permit_id = uuid4()
        denial: ProductionAuthorizationDenied | None = None
        result: ProductionPermitResult | None = None
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            with connection.transaction():
                self._lock_operation(connection, request.operation_id)
                facts = self._load_authority(connection, request)
                pointer = connection.execute(
                    "SELECT * FROM public.v4_production_current_dispatch "
                    "WHERE operation_id=%s FOR UPDATE",
                    (request.operation_id,),
                ).fetchone()
                if pointer is not None:
                    existing = connection.execute(
                        "SELECT * FROM public.v4_production_effect_permits "
                        "WHERE permit_id=%s AND operation_id=%s FOR UPDATE",
                        (pointer["current_permit_id"], request.operation_id),
                    ).fetchone()
                    if (
                        pointer["generation"] == request.generation
                        and existing is not None
                        and pointer["idempotency_key"] == request.idempotency_key
                        and self._snapshot_matches_request(
                            existing["request_snapshot"], request
                        )
                    ):
                        now = self._database_time(connection)
                        if existing["expires_at"] <= now:
                            raise ProductionAuthorizationDenied("permit_expired")
                        self._validate_permit_row(existing, request, facts)
                        claim_id = pointer["current_claim_id"]
                        return ProductionPermitResult(
                            permit_id=existing["permit_id"],
                            operation_id=request.operation_id,
                            generation=request.generation,
                            expires_at=existing["expires_at"],
                            claim_id=claim_id,
                            dispatch_allowed=False,
                        )
                    if (
                        request.generation != pointer["generation"] + 1
                        or request.recovery_command_id is None
                        or pointer["idempotency_key"] != request.idempotency_key
                        or pointer["current_claim_id"] is None
                    ):
                        raise ProductionAuthorizationDenied("generation_advance_required")
                elif request.generation != 1 or request.recovery_command_id is not None:
                    raise ProductionAuthorizationDenied("generation_advance_required")

                expiry = min(
                    self._database_time(connection) + timedelta(seconds=request.ttl_seconds),
                    facts.expires_at,
                )
                if expiry <= self._database_time(connection):
                    raise ProductionAuthorizationDenied("permit_expired")
                route_request = self._route_request(
                    request.route, request.operation_id, permit_id, "issue"
                )
                decision: RouteFenceDecision | None = None
                try:
                    decision = self._route_decision(route_request)
                    if not decision.authorized:
                        denial = ProductionAuthorizationDenied(
                            decision.reason_code or "route_fence_denied"
                        )
                except ProductionAuthorizationDenied as error:
                    denial = error
                now = self._database_time(connection)
                if denial is None and (expiry <= now or facts.expires_at <= now):
                    denial = ProductionAuthorizationDenied("permit_expired")
                if denial is None:
                    assert decision is not None and decision.decision_id is not None
                    snapshot = self._permit_snapshot(request, facts, decision.decision_id)
                    connection.execute(
                        """INSERT INTO public.v4_production_effect_permits(
                              permit_id,operation_id,generation,workspace_id,goal_id,goal_revision,
                              program_id,intent_id,cycle_id,context_id,subject_id,subject_revision,
                              grant_ids,policy_version_id,policy_document_hash,approval_request_id,
                              approval_expires_at,effect_type,purpose,account_id,provider_id,
                              artifact_sha256,deployment_id,environment_id,route_id,slot_id,owner_id,
                              fencing_epoch,route_manifest_sha256,artifact_lock_sha256,
                              route_fence_decision_id,expires_at,request_snapshot,recovery_command_id)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                              %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (
                            permit_id,
                            request.operation_id,
                            request.generation,
                            facts.workspace_id,
                            facts.goal_id,
                            facts.goal_revision,
                            facts.program_id,
                            facts.intent_id,
                            facts.cycle_id,
                            facts.context_id,
                            facts.subject_id,
                            facts.subject_revision,
                            Jsonb([str(grant_id) for grant_id in facts.grant_ids]),
                            facts.policy.id,
                            facts.policy.document_hash,
                            facts.approval.approval_request_id,
                            facts.approval.expires_at,
                            request.effect_type,
                            request.purpose,
                            str(request.account_id),
                            request.provider_id,
                            request.artifact_sha256,
                            request.route.deployment_id,
                            request.route.environment_id,
                            request.route.route_id,
                            request.route.slot_id,
                            request.route.owner_id,
                            request.route.fencing_epoch,
                            request.route.route_manifest_sha256,
                            request.route.artifact_lock_sha256,
                            decision.decision_id,
                            expiry,
                            Jsonb(snapshot),
                            request.recovery_command_id,
                        ),
                    )
                    connection.execute(
                        "SELECT public.v4_advance_production_current_dispatch_v1(%s,%s,%s,NULL,%s,%s)",
                        (
                            request.operation_id,
                            request.generation - 1,
                            permit_id,
                            request.idempotency_key,
                            request.recovery_command_id,
                        ),
                    )
                    self._write_event(
                        connection,
                        facts,
                        "production_permit_issued",
                        action="issue",
                        permit_id=permit_id,
                        claim_id=None,
                        decision_id=decision.decision_id,
                        reason_code=None,
                        request=request,
                        database_time=now,
                    )
                    result = ProductionPermitResult(
                        permit_id=permit_id,
                        operation_id=request.operation_id,
                        generation=request.generation,
                        expires_at=expiry,
                        claim_id=None,
                        dispatch_allowed=False,
                    )
        if denial is not None:
            raise denial
        assert result is not None
        return result

    def claim_permit(self, operation_id: UUID, permit_id: UUID) -> ProductionPermitResult:
        attempt_id = uuid4()
        effect = self._permit_effect(operation_id, permit_id)
        try:
            result = self._claim_permit(operation_id, permit_id)
        except ProductionAuthorizationDenied as denial:
            self._record_decision("claim", effect, "deny", denial.reason_code)
            self._persist_denial_event(
                operation_id,
                "claim",
                denial.reason_code,
                permit_id=permit_id,
                request=None,
                attempt_id=attempt_id,
            )
            raise
        except psycopg.OperationalError:
            self._record_primary_outage("claim", effect)
            raise
        self._record_decision("claim", effect, "allow", "none")
        return result

    def _claim_permit(self, operation_id: UUID, permit_id: UUID) -> ProductionPermitResult:
        if not isinstance(operation_id, UUID) or not isinstance(permit_id, UUID):
            raise ValueError("production operation and permit UUIDs are required")
        denial: ProductionAuthorizationDenied | None = None
        result: ProductionPermitResult | None = None
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            with connection.transaction():
                self._lock_operation(connection, operation_id)
                pointer = connection.execute(
                    "SELECT * FROM public.v4_production_current_dispatch "
                    "WHERE operation_id=%s FOR UPDATE",
                    (operation_id,),
                ).fetchone()
                permit = connection.execute(
                    "SELECT * FROM public.v4_production_effect_permits "
                    "WHERE operation_id=%s AND permit_id=%s FOR UPDATE",
                    (operation_id, permit_id),
                ).fetchone()
                if pointer is None or permit is None:
                    raise ProductionAuthorizationDenied("permit_missing")
                if pointer["generation"] != permit["generation"]:
                    raise ProductionAuthorizationDenied("generation_advance_required")
                if pointer["current_permit_id"] != permit_id:
                    raise ProductionAuthorizationDenied("current_operation_missing")
                if pointer["current_claim_id"] is not None:
                    raise ProductionAuthorizationDenied("dispatch_already_claimed")
                request = self._request_from_snapshot(permit["request_snapshot"])
                if request.operation_id != operation_id or request.idempotency_key != pointer[
                    "idempotency_key"
                ]:
                    raise ProductionAuthorizationDenied("invalid_binding")
                facts = self._load_authority(connection, request)
                decision: RouteFenceDecision | None = None
                try:
                    with connection.transaction():
                        now = self._database_time(connection)
                        if permit["expires_at"] <= now:
                            raise ProductionAuthorizationDenied("permit_expired")
                        if permit["expires_at"] > facts.expires_at:
                            raise ProductionAuthorizationDenied("authority_changed")
                        self._validate_permit_row(permit, request, facts)
                except ProductionAuthorizationDenied as error:
                    denial = error
                if denial is None:
                    route_request = self._route_request(
                        request.route, operation_id, permit_id, "claim"
                    )
                    try:
                        decision = self._route_decision(route_request)
                        if not decision.authorized:
                            denial = ProductionAuthorizationDenied(
                                decision.reason_code or "route_fence_denied"
                            )
                    except ProductionAuthorizationDenied as error:
                        denial = error
                    now = self._database_time(connection)
                    if denial is None and (
                        permit["expires_at"] <= now or facts.expires_at <= now
                    ):
                        denial = ProductionAuthorizationDenied("permit_expired")
                    if denial is None:
                        assert decision is not None and decision.decision_id is not None
                        claim_id = uuid4()
                        connection.execute(
                            """INSERT INTO public.v4_production_effect_permit_claims(
                              claim_id,permit_id,operation_id,generation,route_fence_decision_id)
                       VALUES(%s,%s,%s,%s,%s)""",
                            (
                                claim_id,
                                permit_id,
                                operation_id,
                                pointer["generation"],
                                decision.decision_id,
                            ),
                        )
                        connection.execute(
                            "SELECT public.v4_advance_production_current_dispatch_v1(%s,%s,%s,%s,%s,NULL)",
                            (
                                operation_id,
                                pointer["generation"],
                                permit_id,
                                claim_id,
                                request.idempotency_key,
                            ),
                        )
                        self._write_event(
                            connection,
                            facts,
                            "production_permit_claimed",
                            action="claim",
                            permit_id=permit_id,
                            claim_id=claim_id,
                            decision_id=decision.decision_id,
                            reason_code=None,
                            request=request,
                            database_time=now,
                        )
                        result = ProductionPermitResult(
                            permit_id=permit_id,
                            operation_id=operation_id,
                            generation=pointer["generation"],
                            expires_at=permit["expires_at"],
                            claim_id=claim_id,
                            dispatch_allowed=True,
                        )
        if denial is not None:
            raise denial
        assert result is not None
        return result

    def authorize_action(
        self, operation_id: UUID, permit_id: UUID, action_kind: str
    ) -> RouteFenceDecision:
        attempt_id = uuid4()
        effect = self._permit_effect(operation_id, permit_id)
        try:
            decision = self._authorize_action(operation_id, permit_id, action_kind)
        except ProductionAuthorizationDenied as denial:
            self._record_decision("authorize_action", effect, "deny", denial.reason_code)
            self._persist_denial_event(
                operation_id,
                action_kind,
                denial.reason_code,
                permit_id=permit_id,
                request=None,
                attempt_id=attempt_id,
            )
            raise
        except psycopg.OperationalError:
            self._record_primary_outage("authorize_action", effect)
            raise
        outcome = "allow" if decision.authorized else "deny"
        reason_code = decision.reason_code or "none"
        self._record_decision("authorize_action", effect, outcome, reason_code)
        if not decision.authorized:
            self._persist_denial_event(
                operation_id,
                action_kind,
                decision.reason_code or "other",
                permit_id=permit_id,
                request=None,
                attempt_id=attempt_id,
            )
        return decision

    def record_publication_liability_held(
        self,
        publication_request_id: UUID | str,
        action_kind: str,
        reason_code: str,
    ) -> bool:
        """Append an immutable hold event for an already claimed publication."""
        try:
            publication_request_id = UUID(str(publication_request_id))
        except (TypeError, ValueError) as error:
            raise ValueError("canonical publication request UUID required") from error
        if not isinstance(action_kind, str) or not _ACTION.fullmatch(action_kind):
            raise ValueError("bounded production action kind required")
        safe_reason = reason_code if reason_code in _REASON_CODES else "other"
        return self._record_publication_liability_held(
            publication_request_id, action_kind, safe_reason
        )

    def cancel_unsubmitted_publication(
        self,
        operation_id: UUID | str,
        permit_id: UUID | str,
        publication_request_id: UUID | str,
        publication_plan_id: UUID | str,
        publication_attempt_id: UUID | str,
        external_effect_id: UUID | str,
    ) -> str | None:
        """Cancel only a canonical publication with no committed claim or provider attempt."""
        try:
            identifiers = tuple(
                UUID(str(value))
                for value in (
                    operation_id,
                    permit_id,
                    publication_request_id,
                    publication_plan_id,
                    publication_attempt_id,
                    external_effect_id,
                )
            )
        except (TypeError, ValueError) as error:
            raise ValueError("canonical publication cancellation UUIDs required") from error
        return self._cancel_unsubmitted_publication(*identifiers)

    def _cancel_unsubmitted_publication(
        self,
        operation_id: UUID,
        permit_id: UUID,
        publication_request_id: UUID,
        publication_plan_id: UUID,
        publication_attempt_id: UUID,
        external_effect_id: UUID,
    ) -> str | None:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            with connection.transaction():
                self._lock_operation(connection, operation_id)
                pointer = connection.execute(
                    """SELECT * FROM public.v4_production_current_dispatch
                       WHERE operation_id=%s FOR UPDATE""",
                    (operation_id,),
                ).fetchone()
                permit = connection.execute(
                    """SELECT * FROM public.v4_production_effect_permits
                       WHERE operation_id=%s AND permit_id=%s FOR UPDATE""",
                    (operation_id, permit_id),
                ).fetchone()
                if (
                    pointer is None
                    or permit is None
                    or pointer["generation"] != permit["generation"]
                    or pointer["current_permit_id"] != permit_id
                    or pointer["current_claim_id"] is not None
                ):
                    return None
                request = self._request_from_snapshot(permit["request_snapshot"])
                binding = request.publication_binding
                if (
                    binding is None
                    or binding.publication_request_id != publication_request_id
                    or binding.publication_plan_id != publication_plan_id
                    or binding.publication_attempt_id != publication_attempt_id
                    or binding.external_effect_id != external_effect_id
                    or binding.remote_receipt_id is not None
                ):
                    return None

                row = connection.execute(
                    """SELECT publication_request.state AS request_state,
                              plan.id AS plan_id,plan.version,plan.state AS plan_state,
                              plan.external_effect_id,plan.budget_reservation_id,
                              attempt.id AS attempt_id,attempt.attempt_number,
                              attempt.state AS attempt_state,attempt.submitted_at,
                              effect.id AS effect_id,effect.job_id AS effect_job_id,
                              effect.status AS effect_state,effect.attempted_at,
                              job.id AS job_id,job.job_type,job.state AS job_state,
                              job.workflow_run_id,job.input_payload,
                              reservation.id AS reservation_id,reservation.status AS reservation_state
                       FROM public.publication_requests publication_request
                       JOIN public.publication_plans plan
                         ON plan.id=%s AND plan.publication_request_id=publication_request.id
                        AND NOT EXISTS (
                            SELECT 1 FROM public.publication_plans newer_plan
                            WHERE newer_plan.publication_request_id=plan.publication_request_id
                              AND newer_plan.version>plan.version
                        )
                       JOIN public.publication_attempts attempt
                         ON attempt.id=%s AND attempt.publication_plan_id=plan.id
                        AND NOT EXISTS (
                            SELECT 1 FROM public.publication_attempts newer_attempt
                            WHERE newer_attempt.publication_plan_id=attempt.publication_plan_id
                              AND newer_attempt.attempt_number>attempt.attempt_number
                        )
                       JOIN public.external_effects effect ON effect.id=plan.external_effect_id
                       JOIN public.jobs job ON job.id=%s AND job.id=effect.job_id
                       JOIN public.budget_reservations reservation
                         ON reservation.id=plan.budget_reservation_id
                       WHERE publication_request.id=%s
                       FOR UPDATE OF publication_request,plan,attempt,effect,job,reservation""",
                    (
                        publication_plan_id,
                        publication_attempt_id,
                        binding.job_id,
                        publication_request_id,
                    ),
                ).fetchone()
                if row is None:
                    return None
                receipt = connection.execute(
                    """SELECT id FROM public.remote_publication_receipts
                       WHERE publication_attempt_id=%s FOR UPDATE""",
                    (publication_attempt_id,),
                ).fetchone()
                publication = connection.execute(
                    """SELECT id FROM public.publications
                       WHERE publication_request_id=%s FOR UPDATE""",
                    (publication_request_id,),
                ).fetchone()
                if (
                    row["plan_id"] != publication_plan_id
                    or row["external_effect_id"] != external_effect_id
                    or row["effect_id"] != external_effect_id
                    or row["effect_job_id"] != binding.job_id
                    or row["attempt_id"] != publication_attempt_id
                    or row["job_id"] != binding.job_id
                    or row["job_type"] != "publication"
                    or row["workflow_run_id"] != binding.workflow_run_id
                    or not isinstance(row["input_payload"], Mapping)
                    or row["input_payload"].get("workflow_id") != binding.workflow_id
                    or row["reservation_id"] != request.budget_reservation_id
                    or row["reservation_id"] != row["budget_reservation_id"]
                    or receipt is not None
                    or publication is not None
                    or row["submitted_at"] is not None
                    or row["attempted_at"] is not None
                ):
                    return None
                status_hash = hashlib.sha256(
                    f"publication-cancelled-before-submit:{publication_request_id}:{publication_attempt_id}".encode()
                ).hexdigest()
                prior_status_event = connection.execute(
                    """SELECT id FROM public.publication_status_events
                       WHERE publication_attempt_id=%s AND state='cancelled'
                         AND source='cancellation' AND safe_payload_hash=%s""",
                    (publication_attempt_id, status_hash),
                ).fetchone()
                if (
                    row["effect_state"] == "cancelled"
                    and row["job_state"] == "cancelled"
                    and prior_status_event is not None
                    and row["reservation_state"] in {"reserved", "released"}
                ):
                    return str(row["reservation_id"])
                if (
                    row["request_state"] not in {"planned", "authorized"}
                    or row["plan_state"] not in {"planned", "authorized", "reserved"}
                    or row["attempt_state"] not in {"planned", "authorized"}
                    or row["effect_state"] != "planned"
                    or row["job_state"] not in {"queued", "running"}
                    or row["reservation_state"] != "reserved"
                ):
                    return None

                now = self._database_time(connection)
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    (str(publication_attempt_id),),
                )
                connection.execute(
                    """INSERT INTO public.publication_status_events(
                              publication_attempt_id,sequence_no,state,source,safe_payload_hash,
                              trace_id,span_id)
                       SELECT %s,COALESCE(MAX(sequence_no),0)+1,'cancelled','cancellation',%s,%s,%s
                       FROM public.publication_status_events
                       WHERE publication_attempt_id=%s
                       ON CONFLICT (publication_attempt_id,source,safe_payload_hash) DO NOTHING""",
                    (
                        publication_attempt_id,
                        status_hash,
                        permit["request_snapshot"].get("trace_id"),
                        permit["request_snapshot"].get("span_id"),
                        publication_attempt_id,
                    ),
                )
                connection.execute(
                    """UPDATE public.external_effects
                       SET status='cancelled',completed_at=%s,updated_at=%s,
                           reconciliation_state=%s
                       WHERE id=%s""",
                    (
                        now,
                        now,
                        Jsonb({"state": "cancelled_before_submit"}),
                        external_effect_id,
                    ),
                )
                connection.execute(
                    """UPDATE public.jobs SET state='cancelled',finished_at=%s,updated_at=%s
                       WHERE id=%s""",
                    (now, now, binding.job_id),
                )
                cycle = connection.execute(
                    """SELECT cycle.id AS cycle_id,cycle.intent_id,goal.workspace_id,
                              goal.id AS goal_id,context.payload->>'traceparent' AS traceparent
                       FROM public.v4_cycles cycle
                       JOIN public.v4_cycle_intents intent ON intent.id=cycle.intent_id
                       JOIN public.v4_goals goal ON goal.id=intent.goal_id
                       JOIN public.v4_run_contexts context ON context.id=cycle.context_id
                       WHERE cycle.operation_id=%s""",
                    (operation_id,),
                ).fetchone()
                if cycle is None:
                    raise ProductionAuthorizationDenied("current_operation_missing")
                prior = connection.execute(
                    """SELECT id FROM public.v4_cycle_events
                       WHERE cycle_id=%s AND kind='production_publication_cancelled_before_submit'
                         AND payload->>'operation_id'=%s AND payload->>'permit_id'=%s
                       LIMIT 1""",
                    (cycle["cycle_id"], str(operation_id), str(permit_id)),
                ).fetchone()
                if prior is None:
                    traceparent = cycle["traceparent"] or (
                        f"00-{operation_id.hex}-{permit_id.hex[:16]}-01"
                    )
                    connection.execute(
                        """INSERT INTO public.v4_cycle_events(
                                  id,workspace_id,subject_id,goal_id,intent_id,cycle_id,kind,
                                  traceparent,payload)
                           VALUES(%s,%s,%s,%s,%s,%s,'production_publication_cancelled_before_submit',%s,%s)""",
                        (
                            uuid4(),
                            cycle["workspace_id"],
                            permit["subject_id"],
                            cycle["goal_id"],
                            cycle["intent_id"],
                            cycle["cycle_id"],
                            traceparent,
                            Jsonb(
                                {
                                    "schema_version": "salience.p1-production-publication-cancelled-before-submit.v1",
                                    "operation_id": str(operation_id),
                                    "permit_id": str(permit_id),
                                    "claim_id": None,
                                    "publication_request_id": str(publication_request_id),
                                    "publication_plan_id": str(publication_plan_id),
                                    "publication_attempt_id": str(publication_attempt_id),
                                    "publication_status": "cancelled",
                                    "external_effect_id": str(external_effect_id),
                                    "budget_reservation_id": str(row["reservation_id"]),
                                    "cancelled_at": now.astimezone(timezone.utc).isoformat(),
                                }
                            ),
                        ),
                    )
                return str(row["reservation_id"])

    def _record_publication_liability_held(
        self,
        publication_request_id: UUID,
        action_kind: str,
        reason_code: str,
    ) -> bool:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            with connection.transaction():
                candidates = connection.execute(
                    """SELECT pointer.operation_id
                       FROM public.v4_production_current_dispatch pointer
                       JOIN public.v4_production_effect_permits permit
                         ON permit.operation_id=pointer.operation_id
                        AND permit.permit_id=pointer.current_permit_id
                       WHERE permit.request_snapshot->>'publication_request_id'=%s
                       ORDER BY pointer.operation_id""",
                    (str(publication_request_id),),
                ).fetchall()
                if not candidates:
                    return False
                if len(candidates) != 1:
                    raise ProductionAuthorizationDenied("invalid_binding")
                operation_id = candidates[0]["operation_id"]
                self._lock_operation(connection, operation_id)
                pointer = connection.execute(
                    """SELECT * FROM public.v4_production_current_dispatch
                       WHERE operation_id=%s FOR UPDATE""",
                    (operation_id,),
                ).fetchone()
                if pointer is None or pointer["current_claim_id"] is None:
                    return False
                permit_id = pointer["current_permit_id"]
                permit = connection.execute(
                    """SELECT * FROM public.v4_production_effect_permits
                       WHERE operation_id=%s AND permit_id=%s FOR UPDATE""",
                    (operation_id, permit_id),
                ).fetchone()
                if pointer is None or permit is None:
                    raise ProductionAuthorizationDenied("permit_missing")
                if (
                    pointer["generation"] != permit["generation"]
                    or pointer["current_permit_id"] != permit_id
                ):
                    raise ProductionAuthorizationDenied("current_operation_missing")
                claim_id = pointer["current_claim_id"]
                if claim_id is None:
                    return False
                claim = connection.execute(
                    """SELECT claim_id FROM public.v4_production_effect_permit_claims
                       WHERE claim_id=%s AND operation_id=%s AND permit_id=%s
                         AND generation=%s FOR UPDATE""",
                    (claim_id, operation_id, permit_id, pointer["generation"]),
                ).fetchone()
                if claim is None:
                    raise ProductionAuthorizationDenied("current_operation_missing")
                request = self._request_from_snapshot(permit["request_snapshot"])
                binding = request.publication_binding
                if binding is None or binding.publication_request_id != publication_request_id:
                    raise ProductionAuthorizationDenied("invalid_binding")

                request_row = connection.execute(
                    """SELECT id FROM public.publication_requests
                       WHERE id=%s FOR UPDATE""",
                    (binding.publication_request_id,),
                ).fetchone()
                current_plan = connection.execute(
                    """SELECT id,external_effect_id FROM public.publication_plans
                       WHERE publication_request_id=%s
                       ORDER BY version DESC LIMIT 1 FOR UPDATE""",
                    (binding.publication_request_id,),
                ).fetchone()
                plan = connection.execute(
                    """SELECT id,external_effect_id FROM public.publication_plans
                       WHERE id=%s AND publication_request_id=%s FOR UPDATE""",
                    (binding.publication_plan_id, binding.publication_request_id),
                ).fetchone()
                attempt = connection.execute(
                    """SELECT id FROM public.publication_attempts
                       WHERE id=%s AND publication_plan_id=%s FOR UPDATE""",
                    (binding.publication_attempt_id, binding.publication_plan_id),
                ).fetchone()
                effect_id = plan["external_effect_id"] if plan is not None else None
                if effect_id is not None:
                    connection.execute(
                        "SELECT id FROM public.external_effects WHERE id=%s FOR UPDATE",
                        (effect_id,),
                    ).fetchone()
                receipt = None
                publication = None
                if attempt is not None:
                    receipt = connection.execute(
                        """SELECT id,remote_id,state FROM public.remote_publication_receipts
                           WHERE publication_attempt_id=%s FOR UPDATE""",
                        (binding.publication_attempt_id,),
                    ).fetchone()
                    if receipt is not None:
                        publication = connection.execute(
                            """SELECT id FROM public.publications
                               WHERE remote_publication_receipt_id=%s FOR UPDATE""",
                            (receipt["id"],),
                        ).fetchone()
                cycle = connection.execute(
                    """SELECT cycle.id AS cycle_id,cycle.intent_id,goal.workspace_id,
                              goal.id AS goal_id,context.payload->>'traceparent' AS traceparent
                       FROM public.v4_cycles cycle
                       JOIN public.v4_cycle_intents intent ON intent.id=cycle.intent_id
                       JOIN public.v4_goals goal ON goal.id=intent.goal_id
                       JOIN public.v4_run_contexts context ON context.id=cycle.context_id
                       WHERE cycle.operation_id=%s""",
                    (operation_id,),
                ).fetchone()
                if cycle is None:
                    raise ProductionAuthorizationDenied("current_operation_missing")

                payload = {
                    "schema_version": "salience.p1-production-publication-liability-held.v1",
                    "operation_id": str(operation_id),
                    "permit_id": str(permit_id),
                    "claim_id": str(claim_id),
                    "action": action_kind,
                    "reason_code": reason_code,
                    "approval_request_id": str(permit["approval_request_id"]),
                    "policy_version_id": str(permit["policy_version_id"]),
                    "policy_document_hash": permit["policy_document_hash"],
                    "account_id": permit["account_id"],
                    "provider_id": permit["provider_id"],
                    "budget_id": str(request.budget_id),
                    "budget_reservation_id": str(request.budget_reservation_id),
                    "idempotency_key": request.idempotency_key,
                    "publication_request_id": str(binding.publication_request_id),
                    "job_id": str(binding.job_id),
                    "publication_plan_id": str(binding.publication_plan_id),
                    "publication_attempt_id": str(binding.publication_attempt_id),
                    "external_effect_id": str(binding.external_effect_id),
                    "current_publication_plan_id": (
                        str(current_plan["id"]) if current_plan is not None else None
                    ),
                    "current_external_effect_id": (
                        str(current_plan["external_effect_id"])
                        if current_plan is not None and current_plan["external_effect_id"] is not None
                        else None
                    ),
                    "publication_request_present": request_row is not None,
                    "publication_plan_present": plan is not None,
                    "publication_attempt_present": attempt is not None,
                    "remote_receipt_id": str(receipt["id"]) if receipt is not None else None,
                    "remote_id": receipt["remote_id"] if receipt is not None else None,
                    "remote_receipt_state": receipt["state"] if receipt is not None else None,
                    "publication_id": str(publication["id"]) if publication is not None else None,
                    "held_at": self._database_time(connection).astimezone(timezone.utc).isoformat(),
                }
                prior = connection.execute(
                    """SELECT id FROM public.v4_cycle_events
                       WHERE cycle_id=%s AND kind='production_publication_liability_held'
                         AND payload->>'operation_id'=%s
                         AND payload->>'permit_id'=%s
                         AND payload->>'claim_id'=%s
                         AND payload->>'action'=%s
                         AND payload->>'reason_code'=%s
                       LIMIT 1""",
                    (
                        cycle["cycle_id"],
                        str(operation_id),
                        str(permit_id),
                        str(claim_id),
                        action_kind,
                        reason_code,
                    ),
                ).fetchone()
                if prior is not None:
                    return True
                traceparent = cycle["traceparent"] or (
                    f"00-{operation_id.hex}-{permit_id.hex[:16]}-01"
                )
                connection.execute(
                    """INSERT INTO public.v4_cycle_events(
                              id,workspace_id,subject_id,goal_id,intent_id,cycle_id,kind,
                              traceparent,payload)
                       VALUES(%s,%s,%s,%s,%s,%s,'production_publication_liability_held',%s,%s)""",
                    (
                        uuid4(),
                        cycle["workspace_id"],
                        permit["subject_id"],
                        cycle["goal_id"],
                        cycle["intent_id"],
                        cycle["cycle_id"],
                        traceparent,
                        Jsonb(payload),
                    ),
                )
                return True

    def _authorize_action(
        self, operation_id: UUID, permit_id: UUID, action_kind: str
    ) -> RouteFenceDecision:
        if not isinstance(operation_id, UUID) or not isinstance(permit_id, UUID):
            raise ValueError("production operation and permit UUIDs are required")
        if not isinstance(action_kind, str) or not _ACTION.fullmatch(action_kind):
            raise ValueError("bounded production action kind required")
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            with connection.transaction():
                self._lock_operation(connection, operation_id)
                pointer = connection.execute(
                    "SELECT * FROM public.v4_production_current_dispatch "
                    "WHERE operation_id=%s FOR UPDATE",
                    (operation_id,),
                ).fetchone()
                permit = connection.execute(
                    "SELECT * FROM public.v4_production_effect_permits "
                    "WHERE operation_id=%s AND permit_id=%s FOR UPDATE",
                    (operation_id, permit_id),
                ).fetchone()
                if pointer is None or permit is None:
                    raise ProductionAuthorizationDenied("permit_missing")
                request = self._request_from_snapshot(permit["request_snapshot"])
                route_request = self._route_request(
                    request.route, operation_id, permit_id, action_kind
                )
                preclaim_delivery = (
                    action_kind == "delivery" and pointer["current_claim_id"] is None
                )
                if (
                    pointer["generation"] != permit["generation"]
                    or pointer["current_permit_id"] != permit_id
                    or (pointer["current_claim_id"] is None and not preclaim_delivery)
                ):
                    return self._denied_action(
                        route_request,
                        "dispatch_already_claimed",
                    )
                claim = None
                if pointer["current_claim_id"] is not None:
                    claim = connection.execute(
                        """SELECT * FROM public.v4_production_effect_permit_claims
                           WHERE claim_id=%s AND operation_id=%s AND permit_id=%s AND generation=%s
                           FOR UPDATE""",
                        (
                            pointer["current_claim_id"],
                            operation_id,
                            permit_id,
                            pointer["generation"],
                        ),
                    ).fetchone()
                    if claim is None:
                        return self._denied_action(
                            route_request, "current_operation_missing"
                        )
                elif not preclaim_delivery:
                    return self._denied_action(
                        route_request, "dispatch_already_claimed"
                    )
                facts: _AuthorityFacts | None = None
                try:
                    facts = self._load_authority(connection, request)
                    self._validate_permit_row(permit, request, facts)
                    now = self._database_time(connection)
                    if permit["expires_at"] <= now or facts.expires_at <= now:
                        return self._denied_action(
                            route_request,
                            "permit_expired",
                        )
                except ProductionAuthorizationDenied as denial:
                    return self._denied_action(
                        route_request,
                        denial.reason_code,
                    )
                try:
                    decision = self._route_decision(route_request)
                except ProductionAuthorizationDenied as denial:
                    return self._denied_action(
                        route_request,
                        denial.reason_code,
                    )
                now = self._database_time(connection)
                if decision.authorized and (
                    permit["expires_at"] <= now or facts.expires_at <= now
                ):
                    decision = RouteFenceDecision(
                        authorized=False,
                        decision_id=None,
                        reason_code="permit_expired",
                        request=route_request,
                    )
                if decision.authorized:
                    self._write_event(
                        connection,
                        facts,
                        "production_action_authorized",
                        action=action_kind,
                        permit_id=permit_id,
                        claim_id=claim["claim_id"] if claim is not None else None,
                        decision_id=decision.decision_id,
                        reason_code=decision.reason_code,
                        request=request,
                        database_time=now,
                    )
                return decision

    def _denied_action(
        self,
        route_request: RouteFenceRequest,
        reason_code: str,
    ) -> RouteFenceDecision:
        safe_reason = reason_code if reason_code in _REASON_CODES else "other"
        return RouteFenceDecision(
            authorized=False,
            decision_id=None,
            reason_code=safe_reason,
            request=route_request,
        )

    def _load_authority(self, connection, request: ProductionPermitRequest) -> _AuthorityFacts:
        operation = connection.execute(
            """SELECT workspace.id AS workspace_id,workspace.status AS workspace_status,
                      goal.id AS goal_id,goal.revision AS current_goal_revision,
                      goal.state AS goal_state,intent.id AS intent_id,
                      intent.goal_revision AS intent_goal_revision,intent.expires_at AS intent_expires_at,
                      cycle.id AS cycle_id,cycle.context_id,cycle.operation_id,cycle.state AS cycle_state,
                      context.payload AS context_payload,context.schema_version AS context_schema_version,
                      revision.payload AS goal_payload
               FROM public.v4_cycles cycle
               JOIN public.v4_cycle_intents intent ON intent.id=cycle.intent_id
               JOIN public.v4_goals goal ON goal.id=intent.goal_id
               JOIN public.workspaces workspace ON workspace.id=goal.workspace_id
               JOIN public.v4_goal_revisions revision
                 ON revision.goal_id=goal.id AND revision.revision=intent.goal_revision
               JOIN public.v4_run_contexts context
                 ON context.id=cycle.context_id AND context.cycle_id=cycle.id
               WHERE cycle.operation_id=%s
               FOR UPDATE OF workspace,goal,intent,cycle""",
            (request.operation_id,),
        ).fetchone()
        if operation is None:
            raise ProductionAuthorizationDenied("current_operation_missing")
        if (
            operation["workspace_id"] != request.workspace_id
            or operation["goal_id"] != request.goal_id
            or operation["intent_id"] != request.intent_id
            or operation["cycle_id"] != request.cycle_id
            or operation["context_id"] != request.context_id
            or operation["operation_id"] != request.operation_id
        ):
            raise ProductionAuthorizationDenied("invalid_binding")
        if operation["workspace_status"] != "active":
            raise ProductionAuthorizationDenied("workspace_mismatch")
        if operation["goal_state"] != "active" or operation["cycle_state"] != "runnable":
            raise ProductionAuthorizationDenied("operation_not_runnable")
        if operation["current_goal_revision"] != operation["intent_goal_revision"]:
            raise ProductionAuthorizationDenied("stale_revision")
        if operation["current_goal_revision"] != request.goal_revision:
            raise ProductionAuthorizationDenied("stale_revision")
        try:
            goal_spec = parse_goal(operation["goal_payload"])
            if operation["context_schema_version"] == "RunContext.local.v3":
                context = RunContextV3.model_validate(operation["context_payload"])
            elif operation["context_schema_version"] == "RunContext.local.v2":
                context = RunContextV2.model_validate(operation["context_payload"])
            else:
                raise ValueError("supported current run context required")
        except (ValidationError, ValueError, TypeError) as error:
            raise ProductionAuthorizationDenied("context_invalid") from error
        if (
            context.workspace_id != operation["workspace_id"]
            or context.goal_id != operation["goal_id"]
            or context.goal_revision != operation["current_goal_revision"]
            or context.intent_id != operation["intent_id"]
            or context.cycle_id != operation["cycle_id"]
            or context.context_id != operation["context_id"]
            or context.operation_id != request.operation_id
            or context.subject_id != request.subject_id
            or context.content_program_id != request.program_id
            or request.execution_deadline != context.execution_deadline
        ):
            raise ProductionAuthorizationDenied("invalid_binding")
        if context.production_effects_enabled is not False:
            raise ProductionAuthorizationDenied("invalid_binding")
        program = connection.execute(
            """SELECT id,workspace_id,status,dry_run_default FROM public.content_programs
               WHERE id=%s AND workspace_id=%s FOR UPDATE""",
            (context.content_program_id, context.workspace_id),
        ).fetchone()
        if (
            program is None
            or program["status"] != "active"
            or program["dry_run_default"] is not True
        ):
            raise ProductionAuthorizationDenied("program_inactive")
        if goal_spec.schema_version == "GoalSpec.local.v3" and not isinstance(
            context, RunContextV3
        ):
            raise ProductionAuthorizationDenied("context_invalid")
        if not isinstance(goal_spec, GoalSpecV3) or not isinstance(context, RunContextV3):
            raise ProductionAuthorizationDenied("budget_missing")

        subject = connection.execute(
            """SELECT id,workspace_id,revision,enabled,expires_at
               FROM public.identity_subjects WHERE id=%s AND workspace_id=%s FOR UPDATE""",
            (context.subject_id, context.workspace_id),
        ).fetchone()
        if subject is None or subject["enabled"] is not True:
            raise ProductionAuthorizationDenied("subject_mismatch")
        if subject["revision"] != context.authority.identity_revision:
            raise ProductionAuthorizationDenied("stale_revision")

        scopes = tuple(sorted(set(request.required_scopes) | {"cycles:write"}))
        grants = connection.execute(
            """SELECT id,scope,effect,constraints,expires_at
               FROM public.permission_grants
               WHERE workspace_id=%s AND principal_type='identity' AND principal_id=%s
                 AND scope=ANY(%s)
               ORDER BY scope,id FOR UPDATE""",
            (context.workspace_id, str(context.subject_id), list(scopes)),
        ).fetchall()
        grants_by_scope: dict[str, list[Mapping[str, object]]] = {}
        for grant in grants:
            grants_by_scope.setdefault(grant["scope"], []).append(grant)
        if any(len(grants_by_scope.get(scope, [])) != 1 for scope in scopes):
            raise ProductionAuthorizationDenied("grant_missing")
        if any(
            grant["effect"] != "allow" or grant["constraints"] != {}
            for matching in grants_by_scope.values()
            for grant in matching
        ):
            raise ProductionAuthorizationDenied("grant_missing")
        try:
            context_authority = current_authority(
                connection, context.workspace_id, context.subject_id
            )
            if AuthoritySnapshot.model_validate(context_authority) != context.authority:
                raise ProductionAuthorizationDenied("stale_revision")
        except ProductionAuthorizationDenied:
            raise
        except (PermissionError, ValidationError) as error:
            raise ProductionAuthorizationDenied("authority_changed") from error

        now = self._database_time(connection)
        if subject["expires_at"] <= now:
            raise ProductionAuthorizationDenied("identity_expired")
        for scope in scopes:
            if grants_by_scope[scope][0]["expires_at"] <= now:
                raise ProductionAuthorizationDenied("grant_expired")
        if operation["intent_expires_at"] <= now or context.execution_deadline <= now:
            raise ProductionAuthorizationDenied("operation_not_runnable")
        if goal_spec.horizon_end <= now:
            raise ProductionAuthorizationDenied("operation_not_runnable")

        stop_rows = connection.execute(
            """SELECT scope_key,revision,stopped,changed_at FROM public.v4_stop_scopes
               WHERE workspace_id=%s AND scope_key=ANY(%s)
               ORDER BY scope_key FOR UPDATE""",
            (
                context.workspace_id,
                ["workspace", "goal:" + str(context.goal_id)],
            ),
        ).fetchall()
        stops = {row["scope_key"]: row for row in stop_rows}
        if "workspace" not in stops or "goal:" + str(context.goal_id) not in stops:
            raise ProductionAuthorizationDenied("stop_scope_missing")
        active_stops = [stop for stop in stops.values() if stop["stopped"]]
        if active_stops:
            if self.metrics is not None:
                changed_at = min(stop["changed_at"] for stop in active_stops)
                latency = max(0.0, (now - changed_at).total_seconds())
                self.metrics.record_stop_to_denial(min(latency, 86_400.0))
            raise ProductionAuthorizationDenied("stop_active")

        baseline_row = connection.execute(
            """SELECT approval_id,goal_id,goal_revision,subject_id,expires_at
               FROM public.v4_goal_baselines WHERE approval_id=%s AND goal_id=%s
                 AND goal_revision=%s FOR UPDATE""",
            (context.baseline_approval_id, context.goal_id, context.goal_revision),
        ).fetchone()
        if baseline_row is None or baseline_row["subject_id"] != context.subject_id:
            raise ProductionAuthorizationDenied("baseline_missing")
        baseline = resolve_baseline(
            connection,
            context.goal_id,
            context.goal_revision,
            approval_id=context.baseline_approval_id,
        )
        if baseline is None or baseline["expires_at"] <= now:
            raise ProductionAuthorizationDenied("baseline_missing")

        allocation, budget_reservation_ids, budget_expiries = self._lock_budget(
            connection, context, goal_spec, now
        )
        account_expiries = self._lock_account(
            connection, request, context.workspace_id, context.content_program_id, now
        )

        policy_row = connection.execute(
            """SELECT id,workspace_id,status,document,document_hash,effective_at,
                      clock_timestamp() AS database_time
               FROM public.policy_versions WHERE id=%s AND workspace_id=%s FOR SHARE""",
            (request.policy_version_id, context.workspace_id),
        ).fetchone()
        if policy_row is None:
            raise ProductionAuthorizationDenied("policy_missing")
        try:
            policy = parse_policy_row(
                policy_row,
                workspace_id=context.workspace_id,
                database_time=policy_row["database_time"],
            )
        except PermissionError as error:
            code = "policy_expired" if "expired" in str(error) else "policy_invalid"
            raise ProductionAuthorizationDenied(code) from error
        effect = EffectClass(request.effect_type)
        if effect not in policy.allowed_effects:
            raise ProductionAuthorizationDenied("effect_not_allowed")
        if not policy.jurisdiction_refs.issubset(set(context.jurisdiction_policy_refs)):
            raise ProductionAuthorizationDenied("policy_invalid")
        if request.publication_binding is None:
            if policy.publication_scope is not None:
                raise ProductionAuthorizationDenied("invalid_binding")
        else:
            if policy.publication_scope is None:
                raise ProductionAuthorizationDenied("invalid_binding")
            self._lock_publication(
                connection,
                request,
                context.workspace_id,
                context.content_program_id,
                policy.publication_scope,
                now,
            )

        approval_row = connection.execute(
            """SELECT id,workspace_id,policy_version_id,effect_type,status,request_context,
                      job_id,decided_by,decided_at
               FROM public.approval_requests
               WHERE id=%s AND workspace_id=%s FOR SHARE""",
            (request.approval_request_id, context.workspace_id),
        ).fetchone()
        if approval_row is None:
            raise ProductionAuthorizationDenied("approval_missing")
        try:
            approval_context = approval_row["request_context"]
            expiry_value = approval_context.get("expires_at") if isinstance(
                approval_context, Mapping
            ) else None
            approval_expires_at = self._parse_aware_datetime(expiry_value)
            expected = ProductionApprovalBinding(
                approval_request_id=request.approval_request_id,
                workspace_id=context.workspace_id,
                program_id=context.content_program_id,
                goal_id=context.goal_id,
                goal_revision=context.goal_revision,
                intent_id=context.intent_id,
                cycle_id=context.cycle_id,
                context_id=context.context_id,
                operation_id=context.operation_id,
                subject_id=context.subject_id,
                policy_version_id=request.policy_version_id,
                policy_document_hash=policy.document_hash,
                effect_type=request.effect_type,
                purpose=request.purpose,
                artifact_sha256=request.artifact_sha256,
                account_id=request.account_id,
                provider_id=request.provider_id,
                required_scopes=request.required_scopes,
                budget_id=request.budget_id,
                budget_reservation_id=request.budget_reservation_id,
                expires_at=approval_expires_at,
                publication_request_id=(
                    request.publication_binding.publication_request_id
                    if request.publication_binding else None
                ),
                job_id=request.publication_binding.job_id if request.publication_binding else None,
                workflow_id=request.publication_binding.workflow_id if request.publication_binding else None,
                workflow_run_id=(
                    request.publication_binding.workflow_run_id
                    if request.publication_binding else None
                ),
                publication_plan_id=(
                    request.publication_binding.publication_plan_id
                    if request.publication_binding else None
                ),
                publication_attempt_id=(
                    request.publication_binding.publication_attempt_id
                    if request.publication_binding else None
                ),
                external_effect_id=(
                    request.publication_binding.external_effect_id
                    if request.publication_binding else None
                ),
                remote_receipt_id=(
                    request.publication_binding.remote_receipt_id
                    if request.publication_binding else None
                ),
            )
            approval = parse_approval_row(
                approval_row,
                expected=expected,
                database_time=now,
            )
            if request.required_scopes != approval.required_scopes:
                raise ProductionAuthorizationDenied("invalid_binding")
        except (PermissionError, ValidationError, ValueError, TypeError) as error:
            error_text = str(error)
            code = "approval_expired" if "expired" in error_text else "approval_invalid"
            raise ProductionAuthorizationDenied(code) from error

        production_budget_expiries = self._lock_production_budget(
            connection, request, context.workspace_id, context.content_program_id, now
        )

        expires = [
            subject["expires_at"],
            operation["intent_expires_at"],
            *(grant["expires_at"] for matching in grants_by_scope.values() for grant in matching),
            policy.expires_at,
            approval.expires_at,
            baseline["expires_at"],
            context.execution_deadline,
            goal_spec.horizon_end,
            allocation["period_end"],
            *budget_expiries,
            *production_budget_expiries,
            *account_expiries,
        ]
        if any(not isinstance(value, datetime) or value.tzinfo is None for value in expires):
            raise ProductionAuthorizationDenied("authority_changed")
        valid_until = min(expires)
        if valid_until <= now:
            raise ProductionAuthorizationDenied("authority_changed")
        return _AuthorityFacts(
            workspace_id=context.workspace_id,
            goal_id=context.goal_id,
            goal_revision=context.goal_revision,
            program_id=context.content_program_id,
            intent_id=context.intent_id,
            cycle_id=context.cycle_id,
            context_id=context.context_id,
            operation_id=context.operation_id,
            subject_id=context.subject_id,
            context=context,
            policy=policy,
            approval=approval,
            subject_revision=subject["revision"],
            grant_ids=tuple(
                grants_by_scope[scope][0]["id"] for scope in scopes
            ),
            workspace_stop_revision=stops["workspace"]["revision"],
            goal_stop_revision=stops["goal:" + str(context.goal_id)]["revision"],
            baseline_approval_id=baseline["approval_id"],
            budget_reservation_ids=tuple(budget_reservation_ids),
            expires_at=valid_until,
            traceparent=context.traceparent,
        )

    def _lock_budget(self, connection, context, goal_spec: GoalSpecV3, now):
        allocation = connection.execute(
            """SELECT * FROM public.v4_cycle_allocations
               WHERE cycle_id=%s AND workspace_id=%s AND content_program_id=%s FOR UPDATE""",
            (context.cycle_id, context.workspace_id, context.content_program_id),
        ).fetchone()
        if allocation is None or allocation["id"] != context.allocation_id:
            raise ProductionAuthorizationDenied("budget_missing")
        expected_budget_ids = tuple(sorted(context.allocation.budget_ids, key=str))
        if (
            tuple(sorted(allocation["budget_ids"], key=str)) != expected_budget_ids
            or allocation["account_ref"] != goal_spec.account_refs[0]
            or allocation["account_ref"] != "fixture-account"
            or allocation["period_start"] != context.allocation.period_start
            or allocation["period_end"] != context.allocation.period_end
            or allocation["period_start"] > now
            or allocation["period_end"] <= now
        ):
            raise ProductionAuthorizationDenied("budget_unavailable")
        budget_rows = connection.execute(
            """SELECT id,workspace_id,content_program_id,scope,currency,status,period_start,period_end
               FROM public.budgets WHERE id=ANY(%s) AND workspace_id=%s
                 AND content_program_id=%s ORDER BY id FOR UPDATE""",
            (list(expected_budget_ids), context.workspace_id, context.content_program_id),
        ).fetchall()
        if len(budget_rows) != len(expected_budget_ids):
            raise ProductionAuthorizationDenied("budget_missing")
        required_budget_scopes = {"program", "account:" + allocation["account_ref"]}
        if {row["scope"] for row in budget_rows} != required_budget_scopes:
            raise ProductionAuthorizationDenied("budget_unavailable")
        if any(
            row["status"] != "active"
            or row["currency"] != allocation["currency"]
            or (row["period_start"] is not None and row["period_start"] > now)
            or (row["period_end"] is not None and row["period_end"] <= now)
            for row in budget_rows
        ):
            raise ProductionAuthorizationDenied("budget_unavailable")
        reservations = connection.execute(
            """SELECT mapping.budget_id,reservation.id,reservation.status,
                      reservation.expires_at,reservation.reserved_amount
               FROM public.v4_allocation_reservations mapping
               JOIN public.budget_reservations reservation
                 ON reservation.id=mapping.reservation_id
               WHERE mapping.allocation_id=%s AND mapping.operation_id IS NULL
               ORDER BY mapping.budget_id FOR UPDATE OF reservation""",
            (allocation["id"],),
        ).fetchall()
        by_budget = {row["budget_id"]: row for row in reservations}
        if len(by_budget) != len(expected_budget_ids) or set(by_budget) != set(expected_budget_ids):
            raise ProductionAuthorizationDenied("budget_missing")
        if any(
            row["status"] != "reserved"
            or row["reserved_amount"] <= 0
            or (row["expires_at"] is not None and row["expires_at"] <= now)
            for row in reservations
        ):
            raise ProductionAuthorizationDenied("budget_unavailable")
        return (
            allocation,
            tuple(by_budget[budget_id]["id"] for budget_id in expected_budget_ids),
            tuple(
                row["period_end"] for row in budget_rows if row["period_end"] is not None
            )
            + tuple(
                by_budget[budget_id]["expires_at"]
                for budget_id in expected_budget_ids
                if by_budget[budget_id]["expires_at"] is not None
            ),
        )

    @staticmethod
    def _lock_production_budget(
        connection,
        request: ProductionPermitRequest,
        workspace_id: UUID,
        program_id: UUID,
        now: datetime,
    ) -> tuple[datetime, ...]:
        """Lock the separately approved publisher-account budget reservation."""
        row = connection.execute(
            """SELECT reservation.id AS reservation_id,reservation.budget_id,
                      reservation.status AS reservation_status,reservation.expires_at,
                      reservation.reserved_amount,budget.id AS current_budget_id,
                      budget.workspace_id,budget.content_program_id,budget.scope,
                      budget.status AS budget_status,budget.period_start,budget.period_end
               FROM public.budget_reservations reservation
               JOIN public.budgets budget ON budget.id=reservation.budget_id
               WHERE reservation.id=%s AND budget.id=%s
               FOR UPDATE OF reservation,budget""",
            (request.budget_reservation_id, request.budget_id),
        ).fetchone()
        if row is None:
            raise ProductionAuthorizationDenied("budget_missing")
        if (
            row["reservation_id"] != request.budget_reservation_id
            or row["budget_id"] != request.budget_id
            or row["current_budget_id"] != request.budget_id
            or row["workspace_id"] != workspace_id
            or row["content_program_id"] != program_id
            or row["scope"] != "publisher_account:" + str(request.account_id)
            or row["budget_status"] != "active"
            or row["reservation_status"] != "reserved"
            or row["reserved_amount"] <= 0
            or row["expires_at"] is None
            or row["expires_at"] <= now
            or (row["period_start"] is not None and row["period_start"] > now)
            or (row["period_end"] is not None and row["period_end"] <= now)
        ):
            raise ProductionAuthorizationDenied("budget_unavailable")
        return tuple(
            expiry
            for expiry in (row["expires_at"], row["period_end"])
            if expiry is not None
        )

    def _lock_account(self, connection, request, workspace_id, program_id, now):
        account = connection.execute(
            """SELECT id,workspace_id,content_program_id,platform,status
               FROM public.publisher_accounts
               WHERE id=%s AND workspace_id=%s AND content_program_id=%s FOR UPDATE""",
            (request.account_id, workspace_id, program_id),
        ).fetchone()
        if account is None or account["status"] != "active":
            raise ProductionAuthorizationDenied("provider_account_invalid")
        profile = connection.execute(
            """SELECT publisher_id,platform,audit_state,expires_at
               FROM public.publisher_capability_profiles
               WHERE workspace_id=%s AND publisher_account_id=%s AND publisher_id=%s
                 AND platform=%s AND audit_state='verified'
                 AND (expires_at IS NULL OR expires_at>clock_timestamp())
               ORDER BY observed_at DESC,profile_version DESC LIMIT 1 FOR UPDATE""",
            (workspace_id, request.account_id, request.provider_id, account["platform"]),
        ).fetchone()
        if profile is None:
            raise ProductionAuthorizationDenied("provider_mismatch")
        connection_row = connection.execute(
            """SELECT connection.status,connection.granted_scopes,connection.expires_at,
                      connection.revoked_at,secret.status AS secret_status
               FROM public.publisher_connections connection
               JOIN public.secret_references secret
                 ON secret.id=connection.secret_reference_id
               WHERE connection.publisher_account_id=%s
               ORDER BY connection.version DESC LIMIT 1
               FOR UPDATE OF connection,secret""",
            (request.account_id,),
        ).fetchone()
        if (
            connection_row is None
            or connection_row["status"] != "active"
            or connection_row["secret_status"] != "active"
            or connection_row["revoked_at"] is not None
            or (
                connection_row["expires_at"] is not None
                and connection_row["expires_at"] <= now
            )
        ):
            raise ProductionAuthorizationDenied("provider_account_invalid")
        granted_scopes = connection_row["granted_scopes"]
        if not isinstance(granted_scopes, Sequence) or isinstance(
            granted_scopes, (str, bytes, bytearray)
        ) or not set(request.required_scopes).issubset(set(granted_scopes)):
            raise ProductionAuthorizationDenied("provider_account_invalid")
        account_expiries = [connection_row["expires_at"]]
        if profile["expires_at"] is not None:
            account_expiries.append(profile["expires_at"])
        return tuple(expiry for expiry in account_expiries if expiry is not None)

    @staticmethod
    def _lock_publication(
        connection,
        request: ProductionPermitRequest,
        workspace_id: UUID,
        program_id: UUID,
        policy_scope,
        now: datetime,
    ) -> None:
        binding = request.publication_binding
        assert binding is not None
        row = connection.execute(
            """SELECT publication_request.id AS publication_request_id,
                      publication_request.workspace_id,publication_request.content_program_id,
                      publication_request.publisher_account_id,
                      publication_request.publisher_capability_profile_id,
                      publication_request.publisher_id AS request_publisher_id,
                      publication_request.platform,publication_request.destination,
                      publication_request.locale,publication_request.territory,
                      publication_request.visibility,publication_request.state AS request_state,
                      plan.id AS plan_id,plan.publication_request_id AS plan_request_id,
                      plan.publisher_id AS plan_publisher_id,plan.external_effect_id,
                      plan.budget_reservation_id AS plan_budget_reservation_id,
                      plan.state AS plan_state,attempt.id AS attempt_id,
                      attempt.publication_plan_id AS attempt_plan_id,
                      attempt.state AS attempt_state,attempt.attempt_number,
                      publication_effect.job_id AS effect_job_id,
                      publication_effect.status AS external_effect_state,
                      account.platform AS account_platform,
                      account.status AS account_status,profile.publisher_account_id AS profile_account_id,
                      profile.publisher_id AS profile_publisher_id,profile.platform AS profile_platform,
                      profile.audit_state,profile.expires_at AS profile_expires_at,
                      job.id AS job_id,job.workspace_id AS job_workspace_id,
                      job.content_program_id AS job_program_id,job.job_type,job.state AS job_state,
                      job.workflow_run_id,job.input_payload,
                      publication_approval.status AS publication_approval_status
               FROM public.publication_requests publication_request
               JOIN public.publisher_accounts account
                 ON account.id=publication_request.publisher_account_id
               JOIN public.publisher_capability_profiles profile
                 ON profile.id=publication_request.publisher_capability_profile_id
               JOIN public.publication_plans plan
                 ON plan.id=%s AND plan.publication_request_id=publication_request.id
               JOIN public.publication_attempts attempt
                 ON attempt.id=%s AND attempt.publication_plan_id=plan.id
                AND NOT EXISTS (
                    SELECT 1 FROM public.publication_attempts newer_attempt
                    WHERE newer_attempt.publication_plan_id=attempt.publication_plan_id
                      AND newer_attempt.attempt_number>attempt.attempt_number
                )
               JOIN public.external_effects publication_effect
                 ON publication_effect.id=plan.external_effect_id
               JOIN public.jobs job ON job.id=%s
               JOIN public.approval_requests publication_approval
                 ON publication_approval.id=publication_request.publication_approval_request_id
               WHERE publication_request.id=%s
                 AND NOT EXISTS (
                    SELECT 1 FROM public.publication_plans newer_plan
                    WHERE newer_plan.publication_request_id=plan.publication_request_id
                      AND newer_plan.version>plan.version
                 )
               FOR UPDATE OF publication_request,plan,attempt,account,profile,job,
                             publication_approval,publication_effect""",
            (
                binding.publication_plan_id,
                binding.publication_attempt_id,
                binding.job_id,
                binding.publication_request_id,
            ),
        ).fetchone()
        if row is None:
            raise ProductionAuthorizationDenied("invalid_binding")
        workflow_payload = row["input_payload"]
        if (
            row["publication_request_id"] != binding.publication_request_id
            or row["workspace_id"] != workspace_id
            or row["content_program_id"] != program_id
            or row["publisher_account_id"] != request.account_id
            or row["publisher_capability_profile_id"] is None
            or row["request_publisher_id"] != request.provider_id
            or row["plan_id"] != binding.publication_plan_id
            or row["plan_request_id"] != binding.publication_request_id
            or row["plan_publisher_id"] != request.provider_id
            or row["external_effect_id"] != binding.external_effect_id
            or row["effect_job_id"] != binding.job_id
            or row["external_effect_state"] not in {
                "planned", "submitting", "unknown", "accepted", "completed"
            }
            or row["plan_budget_reservation_id"] != request.budget_reservation_id
            or row["attempt_id"] != binding.publication_attempt_id
            or row["attempt_plan_id"] != binding.publication_plan_id
            or row["job_id"] != binding.job_id
            or row["job_workspace_id"] != workspace_id
            or row["job_program_id"] != program_id
            or row["job_type"] != "publication"
            or row["workflow_run_id"] != binding.workflow_run_id
            or not isinstance(workflow_payload, Mapping)
            or workflow_payload.get("workflow_id") != binding.workflow_id
            or row["publication_approval_status"] != "approved"
            or row["request_state"] not in {"planned", "authorized"}
            or row["plan_state"] not in {"planned", "authorized"}
            or row["attempt_state"] not in {"planned", "authorized"}
            or row["job_state"] not in {"queued", "running"}
            or row["account_status"] != "active"
            or row["account_platform"] != row["platform"]
            or row["profile_account_id"] != request.account_id
            or row["profile_publisher_id"] != request.provider_id
            or row["profile_platform"] != row["platform"]
            or row["audit_state"] != "verified"
            or (
                row["profile_expires_at"] is not None
                and row["profile_expires_at"] <= now
            )
            or row["platform"] != policy_scope.platform
            or row["destination"] != policy_scope.destination
            or row["locale"] != policy_scope.locale
            or row["territory"] != policy_scope.territory
            or row["visibility"] != policy_scope.visibility
        ):
            raise ProductionAuthorizationDenied("invalid_binding")

    @staticmethod
    def _database_time(connection) -> datetime:
        row = connection.execute("SELECT clock_timestamp() AS now").fetchone()
        return row["now"]

    def _permit_effect(self, operation_id: UUID, permit_id: UUID) -> str:
        if not isinstance(operation_id, UUID) or not isinstance(permit_id, UUID):
            return "other"
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                row = connection.execute(
                    """SELECT request_snapshot->>'effect_type' AS effect_type
                       FROM public.v4_production_effect_permits
                       WHERE operation_id=%s AND permit_id=%s""",
                    (operation_id, permit_id),
                ).fetchone()
            return row["effect_type"] if row is not None else "other"
        except psycopg.Error:
            return "other"

    def _record_decision(
        self, action: str, effect: str, outcome: str, reason_code: str
    ) -> None:
        if self.metrics is None:
            return
        self.metrics.record_decision(
            action=action,
            effect=effect,
            outcome=outcome,
            reason_code=reason_code,
        )
        if outcome != "deny":
            return
        if reason_code == "stale_revision":
            self.metrics.record_stale_revision()
        if reason_code in {
            "approval_expired",
            "grant_expired",
            "identity_expired",
            "permit_expired",
            "policy_expired",
        }:
            self.metrics.record_expiry()
        if reason_code == "dispatch_already_claimed":
            self.metrics.record_duplicate_claim()

    def _record_primary_outage(self, action: str, effect: str) -> None:
        if self.metrics is None:
            return
        self.metrics.record_decision(
            action=action,
            effect=effect,
            outcome="deny",
            reason_code="primary_unavailable",
        )
        self.metrics.record_primary_outage()

    def _persist_denial_event(
        self,
        operation_id: UUID,
        action: str,
        reason_code: str,
        *,
        permit_id: UUID | None,
        request: ProductionPermitRequest | None,
        attempt_id: UUID,
    ) -> None:
        """Append one redacted denial event for this attempt in a safe transaction."""
        if not isinstance(operation_id, UUID):
            return
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                with connection.transaction():
                    cycle = connection.execute(
                        """SELECT workspace.id AS workspace_id,goal.id AS goal_id,
                                  intent.id AS intent_id,intent.goal_revision,
                                  cycle.id AS cycle_id,cycle.context_id,cycle.operation_id,
                                  context.payload AS context_payload,
                                  clock_timestamp() AS database_time
                           FROM public.v4_cycles cycle
                           JOIN public.v4_cycle_intents intent ON intent.id=cycle.intent_id
                           JOIN public.v4_goals goal ON goal.id=intent.goal_id
                           JOIN public.workspaces workspace ON workspace.id=goal.workspace_id
                           JOIN public.v4_run_contexts context
                             ON context.id=cycle.context_id AND context.cycle_id=cycle.id
                           WHERE cycle.operation_id=%s""",
                        (operation_id,),
                    ).fetchone()
                    if cycle is None:
                        return
                    context_payload = cycle["context_payload"]
                    subject_value = (
                        context_payload.get("subject_id")
                        if isinstance(context_payload, Mapping)
                        else None
                    )
                    if not isinstance(subject_value, str):
                        return
                    subject_id = UUID(subject_value)
                    candidate_approval_id = (
                        request.approval_request_id if request is not None else None
                    )
                    snapshot = None
                    if permit_id is not None:
                        permit = connection.execute(
                            """SELECT request_snapshot
                               FROM public.v4_production_effect_permits
                               WHERE operation_id=%s AND permit_id=%s""",
                            (operation_id, permit_id),
                        ).fetchone()
                        if permit is not None:
                            snapshot = permit["request_snapshot"]
                            snapshot_approval_id = (
                                snapshot.get("approval_request_id")
                                if isinstance(snapshot, Mapping)
                                else None
                            )
                            if isinstance(snapshot_approval_id, str):
                                candidate_approval_id = UUID(snapshot_approval_id)

                    approval_id = None
                    scopes: list[str] = []
                    if candidate_approval_id is not None:
                        approval = connection.execute(
                            """SELECT workspace_id,request_context
                           FROM public.approval_requests
                           WHERE id=%s AND workspace_id=%s""",
                            (candidate_approval_id, cycle["workspace_id"]),
                        ).fetchone()
                        approval_context = (
                            approval["request_context"] if approval is not None else None
                        )
                        expected_context_bindings = {
                            "workspace_id": str(cycle["workspace_id"]),
                            "goal_id": str(cycle["goal_id"]),
                            "intent_id": str(cycle["intent_id"]),
                            "cycle_id": str(cycle["cycle_id"]),
                            "context_id": str(cycle["context_id"]),
                            "operation_id": str(cycle["operation_id"]),
                            "subject_id": str(subject_id),
                        }
                        if (
                            approval is not None
                            and approval["workspace_id"] == cycle["workspace_id"]
                            and isinstance(approval_context, Mapping)
                            and approval_context.get("schema_version")
                            == "salience.p1-effect-approval.v1"
                            and all(
                                approval_context.get(key) == value
                                for key, value in expected_context_bindings.items()
                            )
                        ):
                            candidate_scopes = approval_context.get("required_scopes")
                            if (
                                isinstance(candidate_scopes, list)
                                and candidate_scopes
                                and all(
                                    isinstance(scope, str)
                                    and scope.strip()
                                    and len(scope) <= 128
                                    for scope in candidate_scopes
                                )
                                and len(candidate_scopes) == len(set(candidate_scopes))
                            ):
                                approval_id = candidate_approval_id
                                scopes = candidate_scopes

                    if permit_id is not None:
                        snapshot_row = connection.execute(
                            """SELECT request_snapshot
                               FROM public.v4_production_effect_permits
                               WHERE operation_id=%s AND permit_id=%s""",
                            (operation_id, permit_id),
                        ).fetchone()
                        if snapshot_row is not None:
                            snapshot = snapshot_row["request_snapshot"]
                    if not isinstance(snapshot, Mapping):
                        snapshot = {}
                    pointer = connection.execute(
                        """SELECT current_claim_id
                           FROM public.v4_production_current_dispatch
                           WHERE operation_id=%s""",
                        (operation_id,),
                    ).fetchone()
                    claim_id = pointer["current_claim_id"] if pointer is not None else None
                    payload = {
                        "schema_version": "salience.p1-production-authorization-event.v1",
                        "authorization_attempt_id": str(attempt_id),
                        "action": action,
                        "outcome": "deny",
                        "operation_id": str(operation_id),
                        "permit_id": str(permit_id) if permit_id is not None else None,
                        "claim_id": str(claim_id) if claim_id is not None else None,
                        "decision_id": None,
                        "reason_code": reason_code,
                        "workspace_id": str(cycle["workspace_id"]),
                        "goal_id": str(cycle["goal_id"]),
                        "goal_revision": cycle["goal_revision"],
                        "intent_id": str(cycle["intent_id"]),
                        "cycle_id": str(cycle["cycle_id"]),
                        "context_id": str(cycle["context_id"]),
                        "subject_id": str(subject_id),
                        "approval_request_id": (
                            str(approval_id) if approval_id is not None else None
                        ),
                        "required_scopes": scopes,
                        "effect_type": snapshot.get("effect_type"),
                        "account_id": snapshot.get("account_id"),
                        "database_time": cycle["database_time"].astimezone(timezone.utc).isoformat(),
                    }
                    connection.execute(
                        """INSERT INTO public.v4_cycle_events(
                                  id,workspace_id,subject_id,goal_id,intent_id,cycle_id,kind,
                                  traceparent,payload)
                           VALUES(%s,%s,%s,%s,%s,%s,'production_authorization_denied',%s,%s)""",
                        (
                            uuid4(),
                            cycle["workspace_id"],
                            subject_id,
                            cycle["goal_id"],
                            cycle["intent_id"],
                            cycle["cycle_id"],
                            context_payload.get("traceparent")
                            if isinstance(context_payload, Mapping)
                            else None,
                            Jsonb(payload),
                        ),
                    )
        except (psycopg.Error, TypeError, ValueError):
            if self.metrics is not None:
                self.metrics.record_primary_outage()

    @staticmethod
    def _parse_aware_datetime(value: object) -> datetime:
        if not isinstance(value, str):
            raise ValueError("timezone-aware timestamp required")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("timezone-aware timestamp required")
        return parsed

    @staticmethod
    def _lock_operation(connection, operation_id: UUID) -> None:
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (str(operation_id),),
        )

    def _route_decision(self, request: RouteFenceRequest) -> RouteFenceDecision:
        try:
            decision = self.route_fence.authorize_action(request)
        except Exception as error:
            raise ProductionAuthorizationDenied("route_fence_unavailable") from error
        if not isinstance(decision, RouteFenceDecision) or decision.request != request:
            raise ProductionAuthorizationDenied("route_fence_invalid")
        return decision

    @staticmethod
    def _route_request(
        binding: RouteBinding, operation_id: UUID, permit_id: UUID, action_kind: str
    ) -> RouteFenceRequest:
        return RouteFenceRequest(
            deployment_id=binding.deployment_id,
            environment_id=binding.environment_id,
            route_id=binding.route_id,
            slot_id=binding.slot_id,
            owner_id=binding.owner_id,
            fencing_epoch=binding.fencing_epoch,
            route_manifest_sha256=binding.route_manifest_sha256,
            artifact_lock_sha256=binding.artifact_lock_sha256,
            operation_id=operation_id,
            permit_id=permit_id,
            action_kind=action_kind,
        )

    @staticmethod
    def _permit_snapshot(
        request: ProductionPermitRequest, facts: _AuthorityFacts, decision_id: UUID
    ) -> dict[str, object]:
        binding = request.publication_binding
        snapshot: dict[str, object] = {
            "schema_version": "salience.p1-production-effect-permit.v2",
            "generation": request.generation,
            "recovery_command_id": (
                str(request.recovery_command_id)
                if request.recovery_command_id is not None
                else None
            ),
            "workspace_id": str(request.workspace_id),
            "goal_id": str(request.goal_id),
            "goal_revision": request.goal_revision,
            "program_id": str(request.program_id),
            "intent_id": str(request.intent_id),
            "cycle_id": str(request.cycle_id),
            "context_id": str(request.context_id),
            "operation_id": str(request.operation_id),
            "subject_id": str(request.subject_id),
            "subject_revision": facts.subject_revision,
            "grant_ids": [str(grant_id) for grant_id in facts.grant_ids],
            "required_scopes": list(request.required_scopes),
            "policy_version_id": str(facts.policy.id),
            "policy_document_hash": facts.policy.document_hash,
            "approval_request_id": str(facts.approval.approval_request_id),
            "approval_expires_at": facts.approval.expires_at.isoformat(),
            "effect_type": request.effect_type,
            "purpose": request.purpose,
            "artifact_sha256": request.artifact_sha256,
            "account_id": str(request.account_id),
            "budget_id": str(request.budget_id),
            "budget_reservation_id": str(request.budget_reservation_id),
            "provider_id": request.provider_id,
            "idempotency_key": request.idempotency_key,
            "execution_deadline": request.execution_deadline.isoformat(),
            "ttl_seconds": request.ttl_seconds,
            "deployment_id": str(request.route.deployment_id),
            "environment_id": str(request.route.environment_id),
            "route_id": str(request.route.route_id),
            "slot_id": str(request.route.slot_id),
            "owner_id": str(request.route.owner_id),
            "fencing_epoch": request.route.fencing_epoch,
            "route_manifest_sha256": request.route.route_manifest_sha256,
            "artifact_lock_sha256": request.route.artifact_lock_sha256,
            "route_tuple_sha256": request.route.route_tuple_sha256,
            "route_fence_decision_id": str(decision_id),
            "workspace_stop_revision": facts.workspace_stop_revision,
            "goal_stop_revision": facts.goal_stop_revision,
            "baseline_approval_id": str(facts.baseline_approval_id),
            "budget_reservation_ids": [
                str(reservation_id) for reservation_id in facts.budget_reservation_ids
            ],
        }
        if binding is not None:
            for field in _PUBLICATION_FIELDS:
                value = getattr(binding, field)
                if value is not None:
                    snapshot[field] = str(value) if isinstance(value, UUID) else value
        return snapshot

    @staticmethod
    def _request_from_snapshot(value: object) -> ProductionPermitRequest:
        if not isinstance(value, Mapping):
            raise ProductionAuthorizationDenied("invalid_binding")
        allowed = (
            _SNAPSHOT_METADATA
            | _ROUTE_FIELDS
            | _OPTIONAL_ROUTE_FIELDS
            | _PUBLICATION_FIELDS
            | frozenset(
                {
                    "workspace_id",
                    "goal_id",
                    "goal_revision",
                    "program_id",
                    "intent_id",
                    "cycle_id",
                    "context_id",
                    "operation_id",
                    "subject_id",
                    "required_scopes",
                    "policy_version_id",
                    "approval_request_id",
                    "effect_type",
                    "purpose",
                    "artifact_sha256",
                    "account_id",
                    "budget_id",
                    "budget_reservation_id",
                    "provider_id",
                    "idempotency_key",
                    "execution_deadline",
                    "ttl_seconds",
                }
            )
        )
        schema_version = value.get("schema_version")
        generation = value.get("generation")
        recovery_command_id = value.get("recovery_command_id")
        v1_snapshot = (
            schema_version == "salience.p1-production-effect-permit.v1"
            and generation == 1
            and "recovery_command_id" not in value
        )
        v2_snapshot = (
            schema_version == "salience.p1-production-effect-permit.v2"
            and type(generation) is int
            and generation >= 1
            and "recovery_command_id" in value
            and "route_tuple_sha256" in value
            and (
                (generation == 1 and recovery_command_id is None)
                or (
                    generation > 1
                    and isinstance(recovery_command_id, str)
                )
            )
        )
        if (
            not (v1_snapshot or v2_snapshot)
            or frozenset(value) - allowed
            or not all(
                key in value
                for key in allowed
                - _PUBLICATION_FIELDS
                - _SNAPSHOT_METADATA
                - _OPTIONAL_ROUTE_FIELDS
            )
        ):
            raise ProductionAuthorizationDenied("invalid_binding")
        try:
            if "route_tuple_sha256" in value and (
                not isinstance(value["route_tuple_sha256"], str)
                or not _SHA256.fullmatch(value["route_tuple_sha256"])
            ):
                raise ValueError("route tuple digest must be lowercase SHA-256")
            route = RouteBinding(
                deployment_id=UUID(str(value["deployment_id"])),
                environment_id=UUID(str(value["environment_id"])),
                route_id=UUID(str(value["route_id"])),
                slot_id=UUID(str(value["slot_id"])),
                owner_id=UUID(str(value["owner_id"])),
                fencing_epoch=value["fencing_epoch"],
                route_manifest_sha256=value["route_manifest_sha256"],
                artifact_lock_sha256=value["artifact_lock_sha256"],
                route_tuple_sha256=value.get("route_tuple_sha256"),
            )
            publication = None
            publication_present = frozenset(value) & _PUBLICATION_FIELDS
            required_publication = _PUBLICATION_FIELDS - {"remote_receipt_id"}
            if publication_present:
                if not required_publication.issubset(publication_present):
                    raise ValueError("incomplete publication binding")
                publication = PublicationEffectBinding(
                    publication_request_id=UUID(str(value["publication_request_id"])),
                    job_id=UUID(str(value["job_id"])),
                    workflow_id=str(value["workflow_id"]),
                    workflow_run_id=str(value["workflow_run_id"]),
                    publication_plan_id=UUID(str(value["publication_plan_id"])),
                    publication_attempt_id=UUID(str(value["publication_attempt_id"])),
                    external_effect_id=UUID(str(value["external_effect_id"])),
                    remote_receipt_id=(
                        UUID(str(value["remote_receipt_id"]))
                        if value.get("remote_receipt_id") is not None else None
                    ),
                )
            deadline = ProductionEffectDispatch._parse_aware_datetime(
                value["execution_deadline"]
            )
            return ProductionPermitRequest(
                workspace_id=UUID(str(value["workspace_id"])),
                goal_id=UUID(str(value["goal_id"])),
                goal_revision=value["goal_revision"],
                program_id=UUID(str(value["program_id"])),
                intent_id=UUID(str(value["intent_id"])),
                cycle_id=UUID(str(value["cycle_id"])),
                context_id=UUID(str(value["context_id"])),
                operation_id=UUID(str(value["operation_id"])),
                subject_id=UUID(str(value["subject_id"])),
                policy_version_id=UUID(str(value["policy_version_id"])),
                approval_request_id=UUID(str(value["approval_request_id"])),
                required_scopes=tuple(value["required_scopes"]),
                effect_type=value["effect_type"],
                purpose=value["purpose"],
                artifact_sha256=value["artifact_sha256"],
                account_id=UUID(str(value["account_id"])),
                budget_id=UUID(str(value["budget_id"])),
                budget_reservation_id=UUID(str(value["budget_reservation_id"])),
                provider_id=value["provider_id"],
                idempotency_key=value["idempotency_key"],
                route=route,
                execution_deadline=deadline,
                ttl_seconds=value["ttl_seconds"],
                publication_binding=publication,
                generation=generation,
                recovery_command_id=(
                    UUID(str(recovery_command_id))
                    if recovery_command_id is not None
                    else None
                ),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ProductionAuthorizationDenied("invalid_binding") from error

    @staticmethod
    def _snapshot_matches_request(
        snapshot: object, request: ProductionPermitRequest
    ) -> bool:
        try:
            return ProductionEffectDispatch._request_from_snapshot(snapshot) == request
        except ProductionAuthorizationDenied:
            return False

    @staticmethod
    def _validate_permit_row(permit, request: ProductionPermitRequest, facts: _AuthorityFacts):
        route = request.route
        snapshot = permit["request_snapshot"]
        if (
            permit["generation"] != request.generation
            or permit["recovery_command_id"] != request.recovery_command_id
        ):
            raise ProductionAuthorizationDenied("invalid_binding")
        current_revision_bindings = {
            "subject_revision": facts.subject_revision,
            "grant_ids": [str(grant_id) for grant_id in facts.grant_ids],
            "workspace_stop_revision": facts.workspace_stop_revision,
            "goal_stop_revision": facts.goal_stop_revision,
            "baseline_approval_id": str(facts.baseline_approval_id),
            "budget_reservation_ids": [
                str(reservation_id) for reservation_id in facts.budget_reservation_ids
            ],
        }
        if (
            not isinstance(snapshot, Mapping)
            or any(snapshot.get(key) != value for key, value in current_revision_bindings.items())
            or permit["grant_ids"] != current_revision_bindings["grant_ids"]
        ):
            raise ProductionAuthorizationDenied("stale_revision")
        if permit["expires_at"] > facts.expires_at:
            raise ProductionAuthorizationDenied("authority_changed")
        bindings = (
            permit["workspace_id"] == facts.workspace_id,
            permit["goal_id"] == facts.goal_id,
            permit["goal_revision"] == facts.goal_revision,
            permit["program_id"] == facts.program_id,
            permit["intent_id"] == facts.intent_id,
            permit["cycle_id"] == facts.cycle_id,
            permit["context_id"] == facts.context_id,
            permit["subject_id"] == facts.subject_id,
            permit["subject_revision"] == facts.subject_revision,
            permit["policy_version_id"] == facts.policy.id,
            permit["policy_document_hash"] == facts.policy.document_hash,
            permit["approval_request_id"] == facts.approval.approval_request_id,
            permit["approval_expires_at"] == facts.approval.expires_at,
            permit["effect_type"] == request.effect_type,
            permit["purpose"] == request.purpose,
            permit["account_id"] == str(request.account_id),
            permit["provider_id"] == request.provider_id,
            permit["artifact_sha256"] == request.artifact_sha256,
            permit["deployment_id"] == route.deployment_id,
            permit["environment_id"] == route.environment_id,
            permit["route_id"] == route.route_id,
            permit["slot_id"] == route.slot_id,
            permit["owner_id"] == route.owner_id,
            permit["fencing_epoch"] == route.fencing_epoch,
            permit["route_manifest_sha256"] == route.route_manifest_sha256,
            permit["artifact_lock_sha256"] == route.artifact_lock_sha256,
        )
        if not all(bindings) or not ProductionEffectDispatch._snapshot_matches_request(
            permit["request_snapshot"], request
        ):
            raise ProductionAuthorizationDenied("authority_changed")

    @staticmethod
    def _write_event(
        connection,
        facts: _AuthorityFacts,
        kind: str,
        *,
        action: str,
        permit_id: UUID,
        claim_id: UUID | None,
        decision_id: UUID | None,
        reason_code: str | None,
        request: ProductionPermitRequest,
        database_time: datetime,
    ) -> None:
        payload: dict[str, object] = {
            "schema_version": "salience.p1-production-authorization-event.v1",
            "action": action,
            "outcome": "allow" if reason_code is None else "deny",
            "operation_id": str(facts.operation_id),
            "permit_id": str(permit_id),
            "claim_id": str(claim_id) if claim_id is not None else None,
            "decision_id": str(decision_id) if decision_id is not None else None,
            "reason_code": reason_code,
            "workspace_id": str(facts.workspace_id),
            "goal_id": str(facts.goal_id),
            "goal_revision": facts.goal_revision,
            "intent_id": str(facts.intent_id),
            "cycle_id": str(facts.cycle_id),
            "context_id": str(facts.context_id),
            "subject_id": str(facts.subject_id),
            "subject_revision": facts.subject_revision,
            "grant_ids": [str(grant_id) for grant_id in facts.grant_ids],
            "policy_version_id": str(facts.policy.id),
            "policy_document_hash": facts.policy.document_hash,
            "approval_request_id": str(facts.approval.approval_request_id),
            "approval_expires_at": facts.approval.expires_at.isoformat(),
            "required_scopes": list(facts.approval.required_scopes),
            "effect_type": request.effect_type,
            "purpose": request.purpose,
            "artifact_sha256": request.artifact_sha256,
            "account_id": str(request.account_id),
            "budget_id": str(request.budget_id),
            "budget_reservation_id": str(request.budget_reservation_id),
            "provider_id": request.provider_id,
            "idempotency_key": request.idempotency_key,
            "workspace_stop_revision": facts.workspace_stop_revision,
            "goal_stop_revision": facts.goal_stop_revision,
            "database_time": database_time.astimezone(timezone.utc).isoformat(),
        }
        binding = request.publication_binding
        if binding is not None:
            payload["publication_binding"] = {
                field: (str(value) if isinstance(value, UUID) else value)
                for field in _PUBLICATION_FIELDS
                if (value := getattr(binding, field)) is not None
            }
        connection.execute(
            """INSERT INTO public.v4_cycle_events(
                      id,workspace_id,subject_id,goal_id,intent_id,cycle_id,kind,
                      traceparent,payload)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                uuid4(),
                facts.workspace_id,
                facts.subject_id,
                facts.goal_id,
                facts.intent_id,
                facts.cycle_id,
                kind,
                facts.traceparent,
                Jsonb(payload),
            ),
        )
