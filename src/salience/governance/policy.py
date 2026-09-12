from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from salience.governance.audit import InMemoryGovernanceJournal
from salience.governance.scopes import ScopeGrant
from salience.observability.tracing import TraceContext


class EffectClass(StrEnum):
    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"
    PURCHASE = "purchase"


@dataclass(frozen=True)
class PolicyVersion:
    id: UUID
    status: str
    allowed_effects: frozenset[EffectClass]
    approval_required_effects: frozenset[EffectClass]
    expires_at: datetime | None
    jurisdiction_refs: frozenset[str] = frozenset()


@dataclass(frozen=True)
class AuthorizationRequest:
    subject_id: UUID
    required_scopes: frozenset[str]
    effect_class: EffectClass
    policy_version_id: UUID
    dry_run: bool
    trace_context: TraceContext
    context: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reasons: tuple[str, ...]
    requires_approval: bool
    policy_version_id: UUID


class PolicyEngine:
    def __init__(
        self,
        *,
        policies: dict[UUID, PolicyVersion],
        known_scopes: set[str],
        grants: dict[UUID, ScopeGrant],
        journal: InMemoryGovernanceJournal | None = None,
    ) -> None:
        self.policies = policies
        self.known_scopes = frozenset(known_scopes)
        self.grants = grants
        self.journal = journal

    def authorize(
        self,
        request: AuthorizationRequest,
        *,
        estimated_micros: int,
        available_micros: int,
        approved: bool = False,
        now: datetime | None = None,
    ) -> PolicyDecision:
        current_time = now or datetime.now(UTC)
        reasons: list[str] = []
        policy = self.policies.get(request.policy_version_id)
        grant = self.grants.get(request.subject_id)

        if policy is None:
            reasons.append("policy_not_found")
        else:
            if policy.status != "active":
                reasons.append("policy_inactive")
            if policy.expires_at is not None and policy.expires_at <= current_time:
                reasons.append("policy_expired")
            if request.effect_class not in policy.allowed_effects:
                reasons.append("effect_not_allowed")
            if (
                not request.dry_run
                and request.effect_class in policy.approval_required_effects
                and not approved
            ):
                reasons.append("approval_required")

        for scope in sorted(request.required_scopes):
            if scope not in self.known_scopes:
                reasons.append(f"scope_unknown:{scope}")
            elif grant is None or not grant.permits(scope, now=current_time):
                reasons.append(f"scope_missing:{scope}")

        if estimated_micros < 0:
            reasons.append("budget_invalid")
        elif estimated_micros > available_micros:
            reasons.append("budget_exceeded")

        decision = PolicyDecision(
            allowed=not reasons,
            reasons=tuple(reasons),
            requires_approval="approval_required" in reasons,
            policy_version_id=request.policy_version_id,
        )
        if self.journal is not None:
            self.journal.record_authorization(
                policy_version_id=request.policy_version_id,
                allowed=decision.allowed,
                reasons=decision.reasons,
                trace_context=request.trace_context,
                details=request.context,
            )
        return decision

