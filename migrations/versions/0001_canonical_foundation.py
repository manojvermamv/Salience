"""Create the immutable Phase-1 canonical data foundation."""

from alembic import op


revision = "0001_canonical_foundation"
down_revision = None
branch_labels = None
depends_on = None


SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE workspaces (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    slug VARCHAR(128) NOT NULL UNIQUE,
    display_name VARCHAR(255) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    data_classification VARCHAR(32) NOT NULL DEFAULT 'internal',
    retention_policy VARCHAR(128),
    delete_after TIMESTAMPTZ,
    domain_policy_ref VARCHAR(255),
    jurisdiction_refs JSONB NOT NULL DEFAULT '{}'::jsonb,
    attributes JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX ix_workspaces_tenant_id ON workspaces (tenant_id);

CREATE TABLE content_programs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    slug VARCHAR(128) NOT NULL,
    name VARCHAR(255) NOT NULL,
    niche TEXT NOT NULL,
    constraints JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    dry_run_default BOOLEAN NOT NULL DEFAULT TRUE,
    data_classification VARCHAR(32) NOT NULL DEFAULT 'internal',
    retention_policy VARCHAR(128),
    delete_after TIMESTAMPTZ,
    domain_policy_ref VARCHAR(255),
    CONSTRAINT uq_content_programs_workspace_slug UNIQUE (workspace_id, slug)
);
CREATE INDEX ix_content_programs_tenant_id ON content_programs (tenant_id);
CREATE INDEX ix_content_programs_workspace_id ON content_programs (workspace_id);

CREATE TABLE external_identity_mappings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    subject_type VARCHAR(64) NOT NULL,
    subject_id UUID NOT NULL,
    external_system VARCHAR(128) NOT NULL,
    external_id VARCHAR(255) NOT NULL,
    attributes JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT uq_external_identity_workspace_system_external_id
        UNIQUE (workspace_id, external_system, external_id)
);
CREATE INDEX ix_external_identity_mappings_tenant_id ON external_identity_mappings (tenant_id);
CREATE INDEX ix_external_identity_mappings_workspace_id ON external_identity_mappings (workspace_id);

CREATE TABLE policy_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    workspace_id UUID REFERENCES workspaces(id) ON DELETE CASCADE,
    policy_name VARCHAR(128) NOT NULL,
    version VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'draft',
    document JSONB NOT NULL,
    document_hash VARCHAR(128) NOT NULL,
    jurisdiction_refs JSONB NOT NULL DEFAULT '{}'::jsonb,
    effective_at TIMESTAMPTZ,
    CONSTRAINT uq_policy_versions_workspace_name_version
        UNIQUE (workspace_id, policy_name, version)
);
CREATE INDEX ix_policy_versions_tenant_id ON policy_versions (tenant_id);
CREATE INDEX ix_policy_versions_workspace_id ON policy_versions (workspace_id);

CREATE TABLE budgets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    content_program_id UUID REFERENCES content_programs(id) ON DELETE CASCADE,
    name VARCHAR(128) NOT NULL,
    scope VARCHAR(64) NOT NULL,
    currency VARCHAR(3) NOT NULL DEFAULT 'USD',
    limit_amount NUMERIC(18, 6) NOT NULL,
    period_start TIMESTAMPTZ,
    period_end TIMESTAMPTZ,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    CONSTRAINT uq_budgets_workspace_name UNIQUE (workspace_id, name)
);
CREATE INDEX ix_budgets_tenant_id ON budgets (tenant_id);
CREATE INDEX ix_budgets_workspace_id ON budgets (workspace_id);
CREATE INDEX ix_budgets_content_program_id ON budgets (content_program_id);

CREATE TABLE plugin_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    plugin_id VARCHAR(128) NOT NULL,
    version VARCHAR(64) NOT NULL,
    display_name VARCHAR(255) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'enabled',
    adapter_contract_version VARCHAR(64) NOT NULL,
    provider_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    protocol_compatibility JSONB NOT NULL DEFAULT '{}'::jsonb,
    trust_classification VARCHAR(64) NOT NULL DEFAULT 'unclassified',
    delegated_authority_scopes JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT uq_plugin_versions_plugin_id_version UNIQUE (plugin_id, version)
);
CREATE INDEX ix_plugin_versions_tenant_id ON plugin_versions (tenant_id);

CREATE TABLE plugin_capabilities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    plugin_version_id UUID NOT NULL REFERENCES plugin_versions(id) ON DELETE CASCADE,
    capability_name VARCHAR(128) NOT NULL,
    contract_version VARCHAR(64) NOT NULL,
    effect_classification VARCHAR(64) NOT NULL,
    input_schema JSONB NOT NULL DEFAULT '{}'::jsonb,
    output_schema JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT uq_plugin_capabilities_version_capability
        UNIQUE (plugin_version_id, capability_name)
);
CREATE INDEX ix_plugin_capabilities_tenant_id ON plugin_capabilities (tenant_id);
CREATE INDEX ix_plugin_capabilities_plugin_version_id ON plugin_capabilities (plugin_version_id);

CREATE TABLE jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    content_program_id UUID REFERENCES content_programs(id) ON DELETE RESTRICT,
    parent_job_id UUID REFERENCES jobs(id),
    job_type VARCHAR(128) NOT NULL,
    state VARCHAR(32) NOT NULL DEFAULT 'queued',
    workflow_run_id VARCHAR(255) NOT NULL UNIQUE,
    task_queue VARCHAR(128) NOT NULL,
    idempotency_key VARCHAR(255) NOT NULL,
    input_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    output_payload JSONB,
    retry_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
    attempt INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    timeout_seconds INTEGER,
    scheduled_for TIMESTAMPTZ,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    dry_run BOOLEAN NOT NULL DEFAULT TRUE,
    trace_id VARCHAR(64) NOT NULL,
    span_id VARCHAR(32),
    policy_version_id UUID REFERENCES policy_versions(id) ON DELETE RESTRICT,
    budget_id UUID REFERENCES budgets(id) ON DELETE RESTRICT,
    actor_kind VARCHAR(64) NOT NULL DEFAULT 'system',
    actor_id VARCHAR(255),
    delegated_authority JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT uq_jobs_workspace_idempotency UNIQUE (workspace_id, idempotency_key)
);
CREATE INDEX ix_jobs_tenant_id ON jobs (tenant_id);
CREATE INDEX ix_jobs_workspace_id ON jobs (workspace_id);
CREATE INDEX ix_jobs_content_program_id ON jobs (content_program_id);
CREATE INDEX ix_jobs_parent_job_id ON jobs (parent_job_id);
CREATE INDEX ix_jobs_scheduled_for ON jobs (scheduled_for);
CREATE INDEX ix_jobs_trace_id ON jobs (trace_id);

CREATE TABLE job_checkpoints (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    job_id UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    sequence_no BIGINT NOT NULL,
    state VARCHAR(64) NOT NULL,
    checkpoint_payload JSONB NOT NULL,
    resume_reason VARCHAR(255),
    trace_id VARCHAR(64) NOT NULL,
    span_id VARCHAR(32),
    CONSTRAINT uq_job_checkpoints_job_sequence UNIQUE (job_id, sequence_no)
);
CREATE INDEX ix_job_checkpoints_tenant_id ON job_checkpoints (tenant_id);
CREATE INDEX ix_job_checkpoints_job_id ON job_checkpoints (job_id);

CREATE TABLE job_dead_letters (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    job_id UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    attempt INTEGER NOT NULL,
    error_type VARCHAR(255) NOT NULL,
    error_message TEXT NOT NULL,
    retryable BOOLEAN NOT NULL,
    failed_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_job_dead_letters_job_attempt UNIQUE (job_id, attempt)
);
CREATE INDEX ix_job_dead_letters_tenant_id ON job_dead_letters (tenant_id);
CREATE INDEX ix_job_dead_letters_job_id ON job_dead_letters (job_id);

CREATE TABLE job_schedules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    content_program_id UUID REFERENCES content_programs(id) ON DELETE CASCADE,
    name VARCHAR(128) NOT NULL,
    schedule_expression VARCHAR(255) NOT NULL,
    timezone VARCHAR(64) NOT NULL DEFAULT 'UTC',
    job_type VARCHAR(128) NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    next_run_at TIMESTAMPTZ,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    CONSTRAINT uq_job_schedules_workspace_name UNIQUE (workspace_id, name)
);
CREATE INDEX ix_job_schedules_tenant_id ON job_schedules (tenant_id);
CREATE INDEX ix_job_schedules_workspace_id ON job_schedules (workspace_id);
CREATE INDEX ix_job_schedules_content_program_id ON job_schedules (content_program_id);
CREATE INDEX ix_job_schedules_next_run_at ON job_schedules (next_run_at);

CREATE TABLE external_effects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    job_id UUID NOT NULL REFERENCES jobs(id) ON DELETE RESTRICT,
    effect_type VARCHAR(128) NOT NULL,
    effect_classification VARCHAR(64) NOT NULL,
    idempotency_key VARCHAR(255) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'planned',
    provider_name VARCHAR(128),
    provider_reference VARCHAR(255),
    request_fingerprint VARCHAR(128) NOT NULL,
    reconciliation_state JSONB NOT NULL DEFAULT '{}'::jsonb,
    effect_result JSONB,
    attempted_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    CONSTRAINT uq_external_effects_workspace_idempotency
        UNIQUE (workspace_id, idempotency_key)
);
CREATE INDEX ix_external_effects_tenant_id ON external_effects (tenant_id);
CREATE INDEX ix_external_effects_workspace_id ON external_effects (workspace_id);
CREATE INDEX ix_external_effects_job_id ON external_effects (job_id);

CREATE TABLE artifacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    job_id UUID REFERENCES jobs(id) ON DELETE SET NULL,
    artifact_type VARCHAR(128) NOT NULL,
    storage_bucket VARCHAR(128) NOT NULL,
    storage_key VARCHAR(1024) NOT NULL UNIQUE,
    content_hash VARCHAR(128) NOT NULL,
    media_type VARCHAR(255) NOT NULL,
    byte_size BIGINT NOT NULL,
    data_classification VARCHAR(32) NOT NULL,
    retention_policy VARCHAR(128),
    delete_after TIMESTAMPTZ,
    origin_metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX ix_artifacts_tenant_id ON artifacts (tenant_id);
CREATE INDEX ix_artifacts_workspace_id ON artifacts (workspace_id);
CREATE INDEX ix_artifacts_job_id ON artifacts (job_id);
CREATE INDEX ix_artifacts_content_hash ON artifacts (content_hash);

CREATE TABLE audit_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    job_id UUID REFERENCES jobs(id) ON DELETE SET NULL,
    run_id VARCHAR(255) NOT NULL,
    sequence_no BIGINT NOT NULL,
    actor_kind VARCHAR(64) NOT NULL,
    actor_id VARCHAR(255),
    action VARCHAR(255) NOT NULL,
    resource_type VARCHAR(128) NOT NULL,
    resource_id VARCHAR(255),
    outcome VARCHAR(64) NOT NULL,
    trace_id VARCHAR(64) NOT NULL,
    span_id VARCHAR(32),
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT uq_audit_events_run_sequence UNIQUE (run_id, sequence_no)
);
CREATE INDEX ix_audit_events_tenant_id ON audit_events (tenant_id);
CREATE INDEX ix_audit_events_workspace_id ON audit_events (workspace_id);
CREATE INDEX ix_audit_events_job_id ON audit_events (job_id);
CREATE INDEX ix_audit_events_run_id ON audit_events (run_id);
CREATE INDEX ix_audit_events_trace_id ON audit_events (trace_id);

CREATE FUNCTION assert_audit_sequence_monotonic() RETURNS trigger AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended(NEW.run_id, 0));
    IF EXISTS (
        SELECT 1 FROM audit_events
        WHERE run_id = NEW.run_id AND sequence_no >= NEW.sequence_no
    ) THEN
        RAISE EXCEPTION 'audit sequence must increase for run %', NEW.run_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER audit_sequence_monotonic
BEFORE INSERT ON audit_events
FOR EACH ROW EXECUTE FUNCTION assert_audit_sequence_monotonic();

CREATE TABLE provenance_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    job_id UUID REFERENCES jobs(id) ON DELETE SET NULL,
    artifact_id UUID REFERENCES artifacts(id) ON DELETE SET NULL,
    parent_provenance_id UUID REFERENCES provenance_records(id) ON DELETE SET NULL,
    origin_type VARCHAR(64) NOT NULL,
    source_uri TEXT,
    source_hash VARCHAR(128),
    verification_status VARCHAR(64) NOT NULL,
    captured_at TIMESTAMPTZ,
    c2pa_manifest JSONB NOT NULL DEFAULT '{}'::jsonb,
    lineage JSONB NOT NULL DEFAULT '{}'::jsonb,
    trace_id VARCHAR(64),
    span_id VARCHAR(32)
);
CREATE INDEX ix_provenance_records_tenant_id ON provenance_records (tenant_id);
CREATE INDEX ix_provenance_records_workspace_id ON provenance_records (workspace_id);
CREATE INDEX ix_provenance_records_job_id ON provenance_records (job_id);
CREATE INDEX ix_provenance_records_artifact_id ON provenance_records (artifact_id);
CREATE INDEX ix_provenance_records_parent_provenance_id ON provenance_records (parent_provenance_id);
CREATE INDEX ix_provenance_records_trace_id ON provenance_records (trace_id);

CREATE TABLE secret_references (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name VARCHAR(128) NOT NULL,
    reference_uri VARCHAR(1024) NOT NULL,
    required_scopes JSONB NOT NULL DEFAULT '{}'::jsonb,
    data_classification VARCHAR(32) NOT NULL,
    rotation_due_at TIMESTAMPTZ,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    CONSTRAINT uq_secret_references_workspace_name UNIQUE (workspace_id, name)
);
CREATE INDEX ix_secret_references_tenant_id ON secret_references (tenant_id);
CREATE INDEX ix_secret_references_workspace_id ON secret_references (workspace_id);

CREATE TABLE permission_grants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    principal_type VARCHAR(64) NOT NULL,
    principal_id VARCHAR(255) NOT NULL,
    scope VARCHAR(255) NOT NULL,
    effect VARCHAR(16) NOT NULL DEFAULT 'allow',
    constraints JSONB NOT NULL DEFAULT '{}'::jsonb,
    expires_at TIMESTAMPTZ,
    CONSTRAINT uq_permission_grants_workspace_principal_scope
        UNIQUE (workspace_id, principal_type, principal_id, scope)
);
CREATE INDEX ix_permission_grants_tenant_id ON permission_grants (tenant_id);
CREATE INDEX ix_permission_grants_workspace_id ON permission_grants (workspace_id);

CREATE TABLE approval_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    job_id UUID REFERENCES jobs(id) ON DELETE SET NULL,
    policy_version_id UUID REFERENCES policy_versions(id) ON DELETE SET NULL,
    effect_type VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    request_context JSONB NOT NULL DEFAULT '{}'::jsonb,
    requested_by VARCHAR(255) NOT NULL,
    decided_by VARCHAR(255),
    decided_at TIMESTAMPTZ,
    decision_reason TEXT
);
CREATE INDEX ix_approval_requests_tenant_id ON approval_requests (tenant_id);
CREATE INDEX ix_approval_requests_workspace_id ON approval_requests (workspace_id);
CREATE INDEX ix_approval_requests_job_id ON approval_requests (job_id);
CREATE INDEX ix_approval_requests_policy_version_id ON approval_requests (policy_version_id);

CREATE TABLE budget_reservations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    budget_id UUID NOT NULL REFERENCES budgets(id) ON DELETE RESTRICT,
    job_id UUID REFERENCES jobs(id) ON DELETE SET NULL,
    external_effect_id UUID REFERENCES external_effects(id) ON DELETE SET NULL,
    reservation_key VARCHAR(255) NOT NULL,
    estimated_amount NUMERIC(18, 6) NOT NULL,
    reserved_amount NUMERIC(18, 6) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'reserved',
    expires_at TIMESTAMPTZ,
    CONSTRAINT uq_budget_reservations_budget_key UNIQUE (budget_id, reservation_key)
);
CREATE INDEX ix_budget_reservations_tenant_id ON budget_reservations (tenant_id);
CREATE INDEX ix_budget_reservations_budget_id ON budget_reservations (budget_id);
CREATE INDEX ix_budget_reservations_job_id ON budget_reservations (job_id);
CREATE INDEX ix_budget_reservations_external_effect_id ON budget_reservations (external_effect_id);

CREATE TABLE cost_ledger_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    budget_reservation_id UUID NOT NULL REFERENCES budget_reservations(id) ON DELETE RESTRICT,
    job_id UUID REFERENCES jobs(id) ON DELETE SET NULL,
    provider_name VARCHAR(128),
    provider_reference VARCHAR(255),
    estimated_amount NUMERIC(18, 6) NOT NULL,
    actual_amount NUMERIC(18, 6),
    currency VARCHAR(3) NOT NULL DEFAULT 'USD',
    usage JSONB NOT NULL DEFAULT '{}'::jsonb,
    recorded_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX ix_cost_ledger_entries_tenant_id ON cost_ledger_entries (tenant_id);
CREATE INDEX ix_cost_ledger_entries_budget_reservation_id ON cost_ledger_entries (budget_reservation_id);
CREATE INDEX ix_cost_ledger_entries_job_id ON cost_ledger_entries (job_id);
"""


TABLES = (
    "cost_ledger_entries",
    "budget_reservations",
    "approval_requests",
    "permission_grants",
    "secret_references",
    "provenance_records",
    "audit_events",
    "artifacts",
    "external_effects",
    "job_schedules",
    "job_dead_letters",
    "job_checkpoints",
    "jobs",
    "plugin_capabilities",
    "plugin_versions",
    "budgets",
    "policy_versions",
    "external_identity_mappings",
    "content_programs",
    "workspaces",
)


def sql_statements(sql: str) -> list[str]:
    statements: list[str] = []
    statement_start = 0
    index = 0
    in_dollar_quoted_block = False

    while index < len(sql):
        if sql.startswith("$$", index):
            in_dollar_quoted_block = not in_dollar_quoted_block
            index += 2
            continue
        if sql[index] == ";" and not in_dollar_quoted_block:
            statement = sql[statement_start : index + 1].strip()
            if statement:
                statements.append(statement)
            statement_start = index + 1
        index += 1

    return statements


def upgrade() -> None:
    for statement in sql_statements(SCHEMA_SQL):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TRIGGER audit_sequence_monotonic ON audit_events")
    op.execute("DROP FUNCTION assert_audit_sequence_monotonic()")
    for table_name in TABLES:
        op.drop_table(table_name)
