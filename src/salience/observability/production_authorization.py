"""Bounded-cardinality metrics for production authorization decisions."""

from __future__ import annotations

import math
from typing import Any


_ACTIONS = frozenset({"policy_load", "issue", "claim", "authorize_action"})
_EFFECTS = frozenset({"read", "write", "destructive", "purchase", "publication"})
_OUTCOMES = frozenset({"allow", "deny", "unknown"})
_REASONS = frozenset(
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
        "stale_revision",
        "stop_active",
        "stop_scope_missing",
        "subject_mismatch",
        "unknown_action",
        "workspace_mismatch",
        "none",
        "other",
    }
)
_INCIDENTS = frozenset(
    {
        "stale_revision",
        "expiry",
        "primary_outage",
        "signal_lag",
        "signal_failure",
        "duplicate_claim",
        "unknown_outcome",
        "unresolved_liability",
    }
)


class ProductionAuthorizationMetrics:
    """Record bounded authorization and liability measurements.

    Callers may pass arbitrary strings, but only the finite label sets in this
    module reach the OpenTelemetry meter.  No workspace, operation, permit,
    account, provider, or subject identifiers are accepted as attributes.
    """

    def __init__(self, meter: Any) -> None:
        self._decisions = meter.create_counter(
            "salience.production.authorization.decisions",
            description="Current production authorization outcomes",
        )
        self._stop_to_denial = meter.create_histogram(
            "salience.production.authorization.stop_to_denial.seconds",
            unit="s",
            description="Elapsed time from committed stop to the next denial",
        )
        self._incidents = meter.create_counter(
            "salience.production.authorization.incidents",
            description="Bounded production authorization incidents",
        )

    def record_decision(
        self,
        *,
        action: str,
        effect: str,
        outcome: str,
        reason_code: str,
    ) -> None:
        self._decisions.add(
            1,
            {
                "action": self._bounded(action, _ACTIONS),
                "effect": self._bounded(effect, _EFFECTS),
                "outcome": self._bounded(outcome, _OUTCOMES),
                "reason_code": self._bounded(reason_code, _REASONS),
            },
        )

    def record_stop_to_denial(self, seconds: float) -> None:
        value = float(seconds)
        if not math.isfinite(value) or value < 0:
            value = 0.0
        self._stop_to_denial.record(min(value, 86_400.0))

    def record_stale_revision(self) -> None:
        self._record_incident("stale_revision")

    def record_expiry(self) -> None:
        self._record_incident("expiry")

    def record_primary_outage(self) -> None:
        self._record_incident("primary_outage")

    def record_signal_lag(self) -> None:
        self._record_incident("signal_lag")

    def record_signal_failure(self) -> None:
        self._record_incident("signal_failure")

    def record_duplicate_claim(self) -> None:
        self._record_incident("duplicate_claim")

    def record_unknown_outcome(self) -> None:
        self._record_incident("unknown_outcome")

    def record_unresolved_liability(self) -> None:
        self._record_incident("unresolved_liability")

    def _record_incident(self, incident: str) -> None:
        self._incidents.add(1, {"incident": incident})

    @staticmethod
    def _bounded(value: str, allowed: frozenset[str]) -> str:
        if not isinstance(value, str):
            return "other"
        return value if value in allowed else "other"
