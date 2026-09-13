from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from salience.db.base import Base, CanonicalIdentity, JsonDocument


class Workspace(CanonicalIdentity, Base):
    __tablename__ = "workspaces"

    slug: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    data_classification: Mapped[str] = mapped_column(
        String(32), nullable=False, default="internal"
    )
    retention_policy: Mapped[str | None] = mapped_column(String(128))
    delete_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    domain_policy_ref: Mapped[str | None] = mapped_column(String(255))
    jurisdiction_refs: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)
    attributes: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)


class ContentProgram(CanonicalIdentity, Base):
    __tablename__ = "content_programs"
    __table_args__ = (UniqueConstraint("workspace_id", "slug"),)

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    niche: Mapped[str] = mapped_column(Text, nullable=False)
    constraints: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    dry_run_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    data_classification: Mapped[str] = mapped_column(
        String(32), nullable=False, default="internal"
    )
    retention_policy: Mapped[str | None] = mapped_column(String(128))
    delete_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    domain_policy_ref: Mapped[str | None] = mapped_column(String(255))


class ExternalIdentityMapping(CanonicalIdentity, Base):
    __tablename__ = "external_identity_mappings"
    __table_args__ = (
        UniqueConstraint("workspace_id", "external_system", "external_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subject_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[UUID] = mapped_column(nullable=False)
    external_system: Mapped[str] = mapped_column(String(128), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    attributes: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)


class PolicyVersion(CanonicalIdentity, Base):
    __tablename__ = "policy_versions"
    __table_args__ = (UniqueConstraint("workspace_id", "policy_name", "version"),)

    workspace_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    policy_name: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    document: Mapped[JsonDocument] = mapped_column(JSONB, nullable=False)
    document_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    jurisdiction_refs: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Budget(CanonicalIdentity, Base):
    __tablename__ = "budgets"
    __table_args__ = (UniqueConstraint("workspace_id", "name"),)

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    content_program_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("content_programs.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    limit_amount: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")


class PluginVersion(CanonicalIdentity, Base):
    __tablename__ = "plugin_versions"
    __table_args__ = (UniqueConstraint("plugin_id", "version"),)

    plugin_id: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="enabled")
    adapter_contract_version: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_metadata: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)
    protocol_compatibility: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)
    trust_classification: Mapped[str] = mapped_column(
        String(64), nullable=False, default="unclassified"
    )
    delegated_authority_scopes: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)


class PluginCapability(CanonicalIdentity, Base):
    __tablename__ = "plugin_capabilities"
    __table_args__ = (UniqueConstraint("plugin_version_id", "capability_name"),)

    plugin_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("plugin_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    capability_name: Mapped[str] = mapped_column(String(128), nullable=False)
    contract_version: Mapped[str] = mapped_column(String(64), nullable=False)
    effect_classification: Mapped[str] = mapped_column(String(64), nullable=False)
    input_schema: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)
    output_schema: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)


class Job(CanonicalIdentity, Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("workspace_id", "idempotency_key"),)

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    content_program_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("content_programs.id", ondelete="RESTRICT"), index=True
    )
    parent_job_id: Mapped[UUID | None] = mapped_column(ForeignKey("jobs.id"), index=True)
    job_type: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    workflow_run_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    task_queue: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    input_payload: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)
    output_payload: Mapped[JsonDocument | None] = mapped_column(JSONB)
    retry_policy: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    timeout_seconds: Mapped[int | None] = mapped_column(Integer)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    span_id: Mapped[str | None] = mapped_column(String(32))
    policy_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("policy_versions.id", ondelete="RESTRICT")
    )
    budget_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("budgets.id", ondelete="RESTRICT")
    )
    actor_kind: Mapped[str] = mapped_column(String(64), nullable=False, default="system")
    actor_id: Mapped[str | None] = mapped_column(String(255))
    delegated_authority: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)


class JobCheckpoint(CanonicalIdentity, Base):
    __tablename__ = "job_checkpoints"
    __table_args__ = (UniqueConstraint("job_id", "sequence_no"),)

    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state: Mapped[str] = mapped_column(String(64), nullable=False)
    checkpoint_payload: Mapped[JsonDocument] = mapped_column(JSONB, nullable=False)
    resume_reason: Mapped[str | None] = mapped_column(String(255))
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    span_id: Mapped[str | None] = mapped_column(String(32))


class JobDeadLetter(CanonicalIdentity, Base):
    __tablename__ = "job_dead_letters"
    __table_args__ = (UniqueConstraint("job_id", "attempt"),)

    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    error_type: Mapped[str] = mapped_column(String(255), nullable=False)
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    failed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class JobSchedule(CanonicalIdentity, Base):
    __tablename__ = "job_schedules"
    __table_args__ = (UniqueConstraint("workspace_id", "name"),)

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    content_program_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("content_programs.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    schedule_expression: Mapped[str] = mapped_column(String(255), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    job_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")


class ExternalEffect(CanonicalIdentity, Base):
    __tablename__ = "external_effects"
    __table_args__ = (UniqueConstraint("workspace_id", "idempotency_key"),)

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    effect_type: Mapped[str] = mapped_column(String(128), nullable=False)
    effect_classification: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="planned")
    provider_name: Mapped[str | None] = mapped_column(String(128))
    provider_reference: Mapped[str | None] = mapped_column(String(255))
    request_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    reconciliation_state: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)
    effect_result: Mapped[JsonDocument | None] = mapped_column(JSONB)
    attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Artifact(CanonicalIdentity, Base):
    __tablename__ = "artifacts"

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), index=True
    )
    artifact_type: Mapped[str] = mapped_column(String(128), nullable=False)
    storage_bucket: Mapped[str] = mapped_column(String(128), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    data_classification: Mapped[str] = mapped_column(String(32), nullable=False)
    retention_policy: Mapped[str | None] = mapped_column(String(128))
    delete_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    origin_metadata: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)


class AuditEvent(CanonicalIdentity, Base):
    __tablename__ = "audit_events"
    __table_args__ = (UniqueConstraint("run_id", "sequence_no"),)

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), index=True
    )
    run_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    sequence_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actor_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(255), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(255))
    outcome: Mapped[str] = mapped_column(String(64), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    span_id: Mapped[str | None] = mapped_column(String(32))
    details: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)


class ProvenanceRecord(CanonicalIdentity, Base):
    __tablename__ = "provenance_records"

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), index=True
    )
    artifact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL"), index=True
    )
    parent_provenance_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("provenance_records.id", ondelete="SET NULL"), index=True
    )
    origin_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_uri: Mapped[str | None] = mapped_column(Text)
    source_hash: Mapped[str | None] = mapped_column(String(128))
    verification_status: Mapped[str] = mapped_column(String(64), nullable=False)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    c2pa_manifest: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)
    lineage: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)
    trace_id: Mapped[str | None] = mapped_column(String(64), index=True)
    span_id: Mapped[str | None] = mapped_column(String(32))


class SecretReferenceRecord(CanonicalIdentity, Base):
    __tablename__ = "secret_references"
    __table_args__ = (UniqueConstraint("workspace_id", "name"),)

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    reference_uri: Mapped[str] = mapped_column(String(1024), nullable=False)
    required_scopes: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)
    data_classification: Mapped[str] = mapped_column(String(32), nullable=False)
    rotation_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")


class PermissionGrant(CanonicalIdentity, Base):
    __tablename__ = "permission_grants"
    __table_args__ = (UniqueConstraint("workspace_id", "principal_type", "principal_id", "scope"),)

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    principal_type: Mapped[str] = mapped_column(String(64), nullable=False)
    principal_id: Mapped[str] = mapped_column(String(255), nullable=False)
    scope: Mapped[str] = mapped_column(String(255), nullable=False)
    effect: Mapped[str] = mapped_column(String(16), nullable=False, default="allow")
    constraints: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApprovalRequest(CanonicalIdentity, Base):
    __tablename__ = "approval_requests"

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), index=True
    )
    policy_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("policy_versions.id", ondelete="SET NULL"), index=True
    )
    effect_type: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    request_context: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)
    requested_by: Mapped[str] = mapped_column(String(255), nullable=False)
    decided_by: Mapped[str | None] = mapped_column(String(255))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_reason: Mapped[str | None] = mapped_column(Text)


class BudgetReservation(CanonicalIdentity, Base):
    __tablename__ = "budget_reservations"
    __table_args__ = (UniqueConstraint("budget_id", "reservation_key"),)

    budget_id: Mapped[UUID] = mapped_column(
        ForeignKey("budgets.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), index=True
    )
    external_effect_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("external_effects.id", ondelete="SET NULL"), index=True
    )
    reservation_key: Mapped[str] = mapped_column(String(255), nullable=False)
    estimated_amount: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    reserved_amount: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="reserved")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CostLedgerEntry(CanonicalIdentity, Base):
    __tablename__ = "cost_ledger_entries"

    budget_reservation_id: Mapped[UUID] = mapped_column(
        ForeignKey("budget_reservations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), index=True
    )
    provider_name: Mapped[str | None] = mapped_column(String(128))
    provider_reference: Mapped[str | None] = mapped_column(String(255))
    estimated_amount: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    actual_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    usage: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ResearchSource(CanonicalIdentity, Base):
    __tablename__ = "research_sources"
    __table_args__ = (UniqueConstraint("content_program_id", "source_key", "version"),)

    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    content_program_id: Mapped[UUID] = mapped_column(
        ForeignKey("content_programs.id"), nullable=False, index=True
    )
    source_key: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    connector: Mapped[str] = mapped_column(String(128), nullable=False)
    trust_level: Mapped[str] = mapped_column(String(64), nullable=False)
    configuration: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)
    network_scope: Mapped[JsonDocument] = mapped_column(JSONB, default=list)
    rate_limit: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)
    protocol_metadata: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)


class ResearchFetch(CanonicalIdentity, Base):
    __tablename__ = "research_fetches"
    __table_args__ = (
        UniqueConstraint("research_source_id", "resource_identity", "window_key"),
    )

    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    content_program_id: Mapped[UUID] = mapped_column(
        ForeignKey("content_programs.id"), nullable=False, index=True
    )
    research_source_id: Mapped[UUID] = mapped_column(
        ForeignKey("research_sources.id"), nullable=False, index=True
    )
    resource_identity: Mapped[str] = mapped_column(String(1024), nullable=False)
    window_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    raw_content_hash: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    provenance: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)
    trace_id: Mapped[str | None] = mapped_column(String(64), index=True)
    span_id: Mapped[str | None] = mapped_column(String(32))


class Signal(CanonicalIdentity, Base):
    __tablename__ = "signals"
    __table_args__ = (UniqueConstraint("content_program_id", "fingerprint"),)

    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    content_program_id: Mapped[UUID] = mapped_column(
        ForeignKey("content_programs.id"), nullable=False, index=True
    )
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    features: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)
    feature_availability: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)
    provenance: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)
    trace_id: Mapped[str | None] = mapped_column(String(64), index=True)
    span_id: Mapped[str | None] = mapped_column(String(32))


class TopicOpportunity(CanonicalIdentity, Base):
    __tablename__ = "topic_opportunities"
    __table_args__ = (UniqueConstraint("content_program_id", "fingerprint"),)

    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    content_program_id: Mapped[UUID] = mapped_column(
        ForeignKey("content_programs.id"), nullable=False, index=True
    )
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    score: Mapped[Decimal] = mapped_column(Numeric(7, 3), nullable=False)
    supporting_signal_ids: Mapped[JsonDocument] = mapped_column(JSONB, default=list)
    feature_availability: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    risks: Mapped[JsonDocument] = mapped_column(JSONB, default=list)
    provenance: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)
    trace_id: Mapped[str | None] = mapped_column(String(64), index=True)
    span_id: Mapped[str | None] = mapped_column(String(32))


class StrategicPackage(CanonicalIdentity, Base):
    __tablename__ = "strategic_packages"
    __table_args__ = (UniqueConstraint("topic_opportunity_id", "diversity_fingerprint"),)

    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    content_program_id: Mapped[UUID] = mapped_column(
        ForeignKey("content_programs.id"), nullable=False, index=True
    )
    topic_opportunity_id: Mapped[UUID] = mapped_column(
        ForeignKey("topic_opportunities.id"), nullable=False, index=True
    )
    diversity_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    package: Mapped[JsonDocument] = mapped_column(JSONB, nullable=False)
    provenance: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)
    trace_id: Mapped[str | None] = mapped_column(String(64), index=True)
    span_id: Mapped[str | None] = mapped_column(String(32))


class ContentBriefVersion(CanonicalIdentity, Base):
    __tablename__ = "content_brief_versions"
    __table_args__ = (UniqueConstraint("content_program_id", "brief_key", "version"),)

    workspace_id: Mapped[UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    content_program_id: Mapped[UUID] = mapped_column(
        ForeignKey("content_programs.id"), nullable=False, index=True
    )
    brief_key: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    topic_opportunity_id: Mapped[UUID] = mapped_column(
        ForeignKey("topic_opportunities.id"), nullable=False, index=True
    )
    strategic_package_id: Mapped[UUID] = mapped_column(
        ForeignKey("strategic_packages.id"), nullable=False, index=True
    )
    strategy_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("strategy_versions.id"), index=True
    )
    content: Mapped[JsonDocument] = mapped_column(JSONB, nullable=False)
    claim_ids: Mapped[JsonDocument] = mapped_column(JSONB, default=list)
    provenance: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)
    trace_id: Mapped[str | None] = mapped_column(String(64), index=True)
    span_id: Mapped[str | None] = mapped_column(String(32))


class ModelInvocationRecord(Base):
    __tablename__ = "model_invocations"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID | None] = mapped_column(index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    job_id: Mapped[UUID | None] = mapped_column(ForeignKey("jobs.id"), index=True)
    parent_agent_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id"), index=True
    )
    capability: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    runtime_id: Mapped[str | None] = mapped_column(String(255))
    provider: Mapped[str | None] = mapped_column(String(255))
    model: Mapped[str | None] = mapped_column(String(255))
    input_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    output_hash: Mapped[str | None] = mapped_column(String(128))
    usage: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    actual_cost_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    trace_id: Mapped[str | None] = mapped_column(String(64), index=True)
    span_id: Mapped[str | None] = mapped_column(String(32))


class ScriptVersion(CanonicalIdentity, Base):
    __tablename__ = "script_versions"
    __table_args__ = (
        UniqueConstraint(
            "content_program_id",
            "script_key",
            "version",
            name="uq_script_versions_program_key_version",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    content_program_id: Mapped[UUID] = mapped_column(
        ForeignKey("content_programs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    content_brief_id: Mapped[UUID] = mapped_column(
        ForeignKey("content_brief_versions.id", ondelete="RESTRICT"), nullable=False
    )
    parent_script_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("script_versions.id", ondelete="RESTRICT"), index=True
    )
    script_key: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    target_format: Mapped[str] = mapped_column(String(128), nullable=False)
    target_duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    script: Mapped[JsonDocument] = mapped_column(JSONB, nullable=False)
    claim_ids: Mapped[JsonDocument] = mapped_column(JSONB, default=list)
    evidence_ids: Mapped[JsonDocument] = mapped_column(JSONB, default=list)
    provenance: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)
    trace_id: Mapped[str | None] = mapped_column(String(64), index=True)
    span_id: Mapped[str | None] = mapped_column(String(32))


class CreativeBrief(CanonicalIdentity, Base):
    __tablename__ = "creative_briefs"
    __table_args__ = (UniqueConstraint("content_program_id", "creative_key", "version"),)

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    content_program_id: Mapped[UUID] = mapped_column(
        ForeignKey("content_programs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    content_brief_id: Mapped[UUID] = mapped_column(
        ForeignKey("content_brief_versions.id", ondelete="RESTRICT"), nullable=False
    )
    script_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("script_versions.id", ondelete="RESTRICT"), nullable=False
    )
    creative_key: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    creative_plan: Mapped[JsonDocument] = mapped_column(JSONB, nullable=False)
    provenance: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)
    trace_id: Mapped[str | None] = mapped_column(String(64), index=True)
    span_id: Mapped[str | None] = mapped_column(String(32))


class Storyboard(CanonicalIdentity, Base):
    __tablename__ = "storyboards"
    __table_args__ = (UniqueConstraint("creative_brief_id", "version"),)

    creative_brief_id: Mapped[UUID] = mapped_column(
        ForeignKey("creative_briefs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[JsonDocument] = mapped_column(JSONB, nullable=False)
    provenance: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)
    trace_id: Mapped[str | None] = mapped_column(String(64), index=True)
    span_id: Mapped[str | None] = mapped_column(String(32))


class ShotPlan(CanonicalIdentity, Base):
    __tablename__ = "shot_plans"
    __table_args__ = (UniqueConstraint("storyboard_id", "sequence_no"),)

    storyboard_id: Mapped[UUID] = mapped_column(
        ForeignKey("storyboards.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_seconds: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    shot: Mapped[JsonDocument] = mapped_column(JSONB, nullable=False)
    capability_requirements: Mapped[JsonDocument] = mapped_column(JSONB, default=list)


class CreativeJob(CanonicalIdentity, Base):
    __tablename__ = "creative_jobs"
    __table_args__ = (
        UniqueConstraint(
            "content_program_id",
            "request_fingerprint",
            name="uq_creative_jobs_program_request_fingerprint",
        ),
        UniqueConstraint("content_program_id", "idempotency_key"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    content_program_id: Mapped[UUID] = mapped_column(
        ForeignKey("content_programs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[UUID | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    content_brief_id: Mapped[UUID] = mapped_column(
        ForeignKey("content_brief_versions.id", ondelete="RESTRICT"), nullable=False
    )
    script_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("script_versions.id", ondelete="RESTRICT")
    )
    creative_brief_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("creative_briefs.id", ondelete="RESTRICT")
    )
    requested_capability: Mapped[str] = mapped_column(String(128), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    request: Mapped[JsonDocument] = mapped_column(JSONB, nullable=False)
    budget_reservation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("budget_reservations.id", ondelete="RESTRICT")
    )
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    trace_id: Mapped[str | None] = mapped_column(String(64), index=True)
    span_id: Mapped[str | None] = mapped_column(String(32))
    provenance: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)


class ProviderJob(CanonicalIdentity, Base):
    __tablename__ = "provider_jobs"
    __table_args__ = (
        UniqueConstraint(
            "provider_id",
            "external_job_id",
            name="uq_provider_jobs_provider_external",
        ),
    )

    creative_job_id: Mapped[UUID] = mapped_column(
        ForeignKey("creative_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plugin_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("plugin_versions.id", ondelete="RESTRICT")
    )
    provider_id: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_version: Mapped[str | None] = mapped_column(String(64))
    model_id: Mapped[str | None] = mapped_column(String(255))
    external_job_id: Mapped[str | None] = mapped_column(String(255))
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    reconciliation_state: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)
    normalized_request: Mapped[JsonDocument] = mapped_column(JSONB, nullable=False)
    provider_extension: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)
    estimated_cost_micros: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    actual_cost_micros: Mapped[int | None] = mapped_column(BigInteger)
    failure_class: Mapped[str | None] = mapped_column(String(128))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trace_id: Mapped[str | None] = mapped_column(String(64), index=True)
    span_id: Mapped[str | None] = mapped_column(String(32))


class Asset(CanonicalIdentity, Base):
    __tablename__ = "assets"
    __table_args__ = (
        UniqueConstraint(
            "content_program_id",
            "content_hash",
            "media_type",
            name="uq_assets_program_hash_media_type",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    content_program_id: Mapped[UUID] = mapped_column(
        ForeignKey("content_programs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    artifact_id: Mapped[UUID | None] = mapped_column(ForeignKey("artifacts.id", ondelete="SET NULL"))
    creative_job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("creative_jobs.id", ondelete="SET NULL")
    )
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    origin_type: Mapped[str] = mapped_column(String(64), nullable=False)
    technical_properties: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)
    creation_parameters: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)
    rights_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    provenance_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="not_configured"
    )
    trace_id: Mapped[str | None] = mapped_column(String(64), index=True)
    span_id: Mapped[str | None] = mapped_column(String(32))


class AssetVariant(CanonicalIdentity, Base):
    __tablename__ = "asset_variants"
    __table_args__ = (UniqueConstraint("asset_id", "variant_key"),)

    asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider_job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("provider_jobs.id", ondelete="SET NULL")
    )
    variant_key: Mapped[str] = mapped_column(String(255), nullable=False)
    selection_state: Mapped[str] = mapped_column(String(32), nullable=False)
    selection_reason: Mapped[str | None] = mapped_column(Text)
    verifier_results: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)


class AssetRelationship(CanonicalIdentity, Base):
    __tablename__ = "asset_relationships"
    __table_args__ = (
        UniqueConstraint("parent_asset_id", "child_asset_id", "relationship_type"),
    )

    parent_asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("assets.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    child_asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("assets.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    relationship_type: Mapped[str] = mapped_column(String(64), nullable=False)
    transformation: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)


class CaptionTrack(CanonicalIdentity, Base):
    __tablename__ = "caption_tracks"
    __table_args__ = (UniqueConstraint("asset_id", "language_code", "format"),)

    asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    language_code: Mapped[str] = mapped_column(String(32), nullable=False)
    format: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[JsonDocument] = mapped_column(JSONB, nullable=False)
    artifact_id: Mapped[UUID | None] = mapped_column(ForeignKey("artifacts.id", ondelete="SET NULL"))
    validation: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)


class Composition(CanonicalIdentity, Base):
    __tablename__ = "compositions"
    __table_args__ = (UniqueConstraint("content_program_id", "composition_key", "version"),)

    content_program_id: Mapped[UUID] = mapped_column(
        ForeignKey("content_programs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    output_asset_id: Mapped[UUID | None] = mapped_column(ForeignKey("assets.id", ondelete="SET NULL"))
    composition_key: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    specification: Mapped[JsonDocument] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)


class AssetLicense(CanonicalIdentity, Base):
    __tablename__ = "asset_licenses"

    asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    license_type: Mapped[str] = mapped_column(String(64), nullable=False)
    attribution: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)
    commercial_use: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    terms: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), nullable=False)


class ConsentRecord(CanonicalIdentity, Base):
    __tablename__ = "consent_records"
    __table_args__ = (UniqueConstraint("workspace_id", "subject_type", "subject_id"),)

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    subject_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    permitted_channels: Mapped[JsonDocument] = mapped_column(JSONB, default=list)
    commercial_use: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    territories: Mapped[JsonDocument] = mapped_column(JSONB, default=list)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    evidence: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)


class LikenessIdentity(CanonicalIdentity, Base):
    __tablename__ = "likeness_identities"
    __table_args__ = (UniqueConstraint("workspace_id", "identity_key"),)

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    identity_key: Mapped[str] = mapped_column(String(255), nullable=False)
    consent_record_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("consent_records.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attributes: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)


class VoiceIdentity(CanonicalIdentity, Base):
    __tablename__ = "voice_identities"
    __table_args__ = (UniqueConstraint("workspace_id", "identity_key"),)

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    identity_key: Mapped[str] = mapped_column(String(255), nullable=False)
    consent_record_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("consent_records.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attributes: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)


class UsageRestriction(CanonicalIdentity, Base):
    __tablename__ = "usage_restrictions"

    asset_id: Mapped[UUID | None] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"))
    restriction_type: Mapped[str] = mapped_column(String(64), nullable=False)
    document: Mapped[JsonDocument] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)


class AssetProvenance(CanonicalIdentity, Base):
    __tablename__ = "asset_provenance"
    __table_args__ = (UniqueConstraint("asset_id"),)

    asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provenance_record_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("provenance_records.id", ondelete="SET NULL")
    )
    origin_type: Mapped[str] = mapped_column(String(64), nullable=False)
    ingredients: Mapped[JsonDocument] = mapped_column(JSONB, default=list)
    transformations: Mapped[JsonDocument] = mapped_column(JSONB, default=list)
    c2pa_manifest_reference: Mapped[str | None] = mapped_column(Text)
    validation_status: Mapped[str] = mapped_column(String(32), nullable=False)
    signer_metadata: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)


class PlatformProfile(CanonicalIdentity, Base):
    __tablename__ = "platform_profiles"
    __table_args__ = (
        UniqueConstraint(
            "content_program_id",
            "profile_key",
            "version",
            name="uq_platform_profiles_program_key_version",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    content_program_id: Mapped[UUID] = mapped_column(
        ForeignKey("content_programs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    profile_key: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    target_platform: Mapped[str] = mapped_column(String(128), nullable=False)
    rules: Mapped[JsonDocument] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)


class DistributionPackage(CanonicalIdentity, Base):
    __tablename__ = "distribution_packages"
    __table_args__ = (
        UniqueConstraint(
            "content_program_id",
            "package_key",
            "version",
            name="uq_distribution_packages_program_key_version",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    content_program_id: Mapped[UUID] = mapped_column(
        ForeignKey("content_programs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    content_brief_id: Mapped[UUID] = mapped_column(
        ForeignKey("content_brief_versions.id", ondelete="RESTRICT"), nullable=False
    )
    script_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("script_versions.id", ondelete="RESTRICT"), nullable=False
    )
    platform_profile_id: Mapped[UUID] = mapped_column(
        ForeignKey("platform_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    package_key: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    locale: Mapped[str] = mapped_column(String(32), nullable=False)
    package_metadata: Mapped[JsonDocument] = mapped_column("metadata", JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    verifier_results: Mapped[JsonDocument] = mapped_column(JSONB, default=dict)


class DistributionPackageVariant(CanonicalIdentity, Base):
    __tablename__ = "distribution_package_variants"
    __table_args__ = (UniqueConstraint("distribution_package_id", "variant_key"),)

    distribution_package_id: Mapped[UUID] = mapped_column(
        ForeignKey("distribution_packages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    variant_key: Mapped[str] = mapped_column(String(255), nullable=False)
    package_metadata: Mapped[JsonDocument] = mapped_column("metadata", JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)


class DistributionPackageAsset(CanonicalIdentity, Base):
    __tablename__ = "distribution_package_assets"
    __table_args__ = (
        UniqueConstraint("distribution_package_id", "asset_role"),
    )

    distribution_package_id: Mapped[UUID] = mapped_column(
        ForeignKey("distribution_packages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("assets.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    asset_role: Mapped[str] = mapped_column(String(64), nullable=False)
    selection_reason: Mapped[str | None] = mapped_column(Text)


class TitleThumbnailCandidate(CanonicalIdentity, Base):
    __tablename__ = "title_thumbnail_candidates"
    __table_args__ = (UniqueConstraint("distribution_package_id", "candidate_key"),)

    distribution_package_id: Mapped[UUID] = mapped_column(
        ForeignKey("distribution_packages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    candidate_key: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    thumbnail_asset_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("assets.id", ondelete="SET NULL")
    )
    score: Mapped[Decimal | None] = mapped_column(Numeric(7, 3))
    selection_state: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)


class Localization(CanonicalIdentity, Base):
    __tablename__ = "localizations"
    __table_args__ = (UniqueConstraint("distribution_package_id", "target_locale"),)

    distribution_package_id: Mapped[UUID] = mapped_column(
        ForeignKey("distribution_packages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_locale: Mapped[str] = mapped_column(String(32), nullable=False)
    target_locale: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[JsonDocument] = mapped_column(JSONB, nullable=False)
    claim_ids: Mapped[JsonDocument] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(32), nullable=False)


class OriginalityEvaluation(CanonicalIdentity, Base):
    __tablename__ = "originality_evaluations"
    __table_args__ = (UniqueConstraint("distribution_package_id", "evaluator_version"),)

    distribution_package_id: Mapped[UUID] = mapped_column(
        ForeignKey("distribution_packages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    evaluator_version: Mapped[str] = mapped_column(String(64), nullable=False)
    metrics: Mapped[JsonDocument] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)


class SyntheticMediaDisclosure(CanonicalIdentity, Base):
    __tablename__ = "synthetic_media_disclosures"
    __table_args__ = (UniqueConstraint("distribution_package_id"),)

    distribution_package_id: Mapped[UUID] = mapped_column(
        ForeignKey("distribution_packages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    decision: Mapped[JsonDocument] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    policy_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("policy_versions.id", ondelete="RESTRICT")
    )


class ReadyToPublishPackage(CanonicalIdentity, Base):
    __tablename__ = "ready_to_publish_packages"
    __table_args__ = (
        UniqueConstraint(
            "content_program_id",
            "ready_package_key",
            "version",
            name="uq_ready_package_program_key_version",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    content_program_id: Mapped[UUID] = mapped_column(
        ForeignKey("content_programs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    content_brief_id: Mapped[UUID] = mapped_column(
        ForeignKey("content_brief_versions.id", ondelete="RESTRICT"), nullable=False
    )
    script_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("script_versions.id", ondelete="RESTRICT"), nullable=False
    )
    distribution_package_id: Mapped[UUID] = mapped_column(
        ForeignKey("distribution_packages.id", ondelete="RESTRICT"), nullable=False
    )
    platform_profile_id: Mapped[UUID] = mapped_column(
        ForeignKey("platform_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    disclosure_id: Mapped[UUID] = mapped_column(
        ForeignKey("synthetic_media_disclosures.id", ondelete="RESTRICT"), nullable=False
    )
    ready_package_key: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    approval_request_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("approval_requests.id", ondelete="RESTRICT")
    )
    approval_state: Mapped[str] = mapped_column(String(32), nullable=False)
    verifier_results: Mapped[JsonDocument] = mapped_column(JSONB, nullable=False)
    policy_versions: Mapped[JsonDocument] = mapped_column(JSONB, default=list)
    lineage: Mapped[JsonDocument] = mapped_column(JSONB, nullable=False)
