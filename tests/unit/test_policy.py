from datetime import UTC, datetime, timedelta
from uuid import uuid4

from salience.governance.audit import InMemoryGovernanceJournal
from salience.governance.policy import (
    AuthorizationRequest,
    EffectClass,
    PolicyEngine,
    PolicyVersion,
    ScopeGrant,
)
from salience.observability.tracing import TraceContext


def active_policy(*, requires_approval: bool = False) -> PolicyVersion:
    return PolicyVersion(
        id=uuid4(),
        status="active",
        allowed_effects=frozenset({EffectClass.WRITE}),
        approval_required_effects=(
            frozenset({EffectClass.WRITE}) if requires_approval else frozenset()
        ),
        expires_at=None,
    )


def test_effect_requires_scope_policy_approval_and_budget() -> None:
    subject_id = uuid4()
    policy = active_policy(requires_approval=True)
    journal = InMemoryGovernanceJournal()
    engine = PolicyEngine(
        policies={policy.id: policy},
        known_scopes={"content.write"},
        grants={subject_id: ScopeGrant(subject_id, frozenset())},
        journal=journal,
    )
    request = AuthorizationRequest(
        subject_id=subject_id,
        required_scopes=frozenset({"content.write"}),
        effect_class=EffectClass.WRITE,
        policy_version_id=policy.id,
        dry_run=False,
        trace_context=TraceContext.new_root(),
        context={"token": "very-secret"},
    )

    denied = engine.authorize(request, estimated_micros=10, available_micros=100)
    assert denied.allowed is False
    assert "scope_missing:content.write" in denied.reasons
    assert journal.audit_events[-1].details["token"] == "[REDACTED]"

    engine.grants[subject_id] = ScopeGrant(subject_id, frozenset({"content.write"}))
    pending = engine.authorize(request, estimated_micros=10, available_micros=100)
    assert pending.allowed is False
    assert pending.requires_approval is True

    approved = engine.authorize(
        request,
        estimated_micros=10,
        available_micros=100,
        approved=True,
    )
    assert approved.allowed is True
    assert journal.provenance_records[-1].policy_version_id == policy.id


def test_policy_denies_unknown_scope_expired_policy_and_budget_overflow() -> None:
    subject_id = uuid4()
    expired_policy = PolicyVersion(
        id=uuid4(),
        status="active",
        allowed_effects=frozenset({EffectClass.WRITE}),
        approval_required_effects=frozenset(),
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    engine = PolicyEngine(
        policies={expired_policy.id: expired_policy},
        known_scopes={"content.write"},
        grants={
            subject_id: ScopeGrant(
                subject_id,
                frozenset({"content.write", "unknown.scope"}),
            )
        },
    )

    expired_request = AuthorizationRequest(
        subject_id=subject_id,
        required_scopes=frozenset({"content.write"}),
        effect_class=EffectClass.WRITE,
        policy_version_id=expired_policy.id,
        dry_run=True,
        trace_context=TraceContext.new_root(),
    )
    assert "policy_expired" in engine.authorize(
        expired_request, estimated_micros=1, available_micros=1
    ).reasons

    unknown_scope_request = AuthorizationRequest(
        subject_id=subject_id,
        required_scopes=frozenset({"unknown.scope"}),
        effect_class=EffectClass.WRITE,
        policy_version_id=expired_policy.id,
        dry_run=True,
        trace_context=TraceContext.new_root(),
    )
    assert "scope_unknown:unknown.scope" in engine.authorize(
        unknown_scope_request, estimated_micros=1, available_micros=1
    ).reasons
    assert "budget_exceeded" in engine.authorize(
        expired_request, estimated_micros=2, available_micros=1
    ).reasons

