from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from salience.observability.tracing import TraceContext


REDACTED = "[REDACTED]"
SENSITIVE_FIELD_NAMES = frozenset(
    {"authorization", "cookie", "password", "secret", "token"}
)


def redact(value: Any, *, known_secret_values: frozenset[str] = frozenset()) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                REDACTED
                if any(sensitive in key.lower() for sensitive in SENSITIVE_FIELD_NAMES)
                else redact(nested_value, known_secret_values=known_secret_values)
            )
            for key, nested_value in value.items()
        }
    if isinstance(value, list):
        return [redact(item, known_secret_values=known_secret_values) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item, known_secret_values=known_secret_values) for item in value)
    if isinstance(value, str) and value in known_secret_values:
        return REDACTED
    return value


@dataclass(frozen=True)
class AuditEvent:
    id: UUID
    action: str
    outcome: str
    trace_id: str
    span_id: str
    details: dict[str, Any]


@dataclass(frozen=True)
class ProvenanceRecord:
    id: UUID
    policy_version_id: UUID
    trace_id: str
    origin_type: str
    verification_status: str


@dataclass
class InMemoryGovernanceJournal:
    known_secret_values: frozenset[str] = frozenset()
    audit_events: list[AuditEvent] = field(default_factory=list)
    provenance_records: list[ProvenanceRecord] = field(default_factory=list)

    def record_authorization(
        self,
        *,
        policy_version_id: UUID,
        allowed: bool,
        reasons: tuple[str, ...],
        trace_context: TraceContext,
        details: dict[str, Any],
    ) -> None:
        safe_details = redact(details, known_secret_values=self.known_secret_values)
        self.audit_events.append(
            AuditEvent(
                id=uuid4(),
                action="policy.authorize",
                outcome="allowed" if allowed else "denied",
                trace_id=trace_context.trace_id,
                span_id=trace_context.span_id,
                details={**safe_details, "reasons": reasons},
            )
        )
        self.provenance_records.append(
            ProvenanceRecord(
                id=uuid4(),
                policy_version_id=policy_version_id,
                trace_id=trace_context.trace_id,
                origin_type="policy_decision",
                verification_status="authorized" if allowed else "denied",
            )
        )

