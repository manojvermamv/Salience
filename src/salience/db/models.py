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
