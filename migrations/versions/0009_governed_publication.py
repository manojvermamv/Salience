"""Add canonical provider-neutral governed publication records."""

from alembic import op


revision = "0009_governed_publication"
down_revision = "0008_creative_release_gate"
branch_labels = None
depends_on = None


SCHEMA_SQL = """
CREATE TABLE publisher_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    content_program_id UUID REFERENCES content_programs(id) ON DELETE SET NULL,
    platform VARCHAR(64) NOT NULL,
    account_key VARCHAR(255) NOT NULL,
    account_type VARCHAR(64) NOT NULL,
    external_account_reference VARCHAR(255) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    data_classification VARCHAR(32) NOT NULL DEFAULT 'confidential',
    retention_policy VARCHAR(128),
    attributes JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT uq_publisher_accounts_workspace_platform_key UNIQUE (workspace_id, platform, account_key)
);
CREATE INDEX ix_publisher_accounts_workspace_id ON publisher_accounts (workspace_id);
CREATE INDEX ix_publisher_accounts_content_program_id ON publisher_accounts (content_program_id);

CREATE TABLE publisher_connections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    publisher_account_id UUID NOT NULL REFERENCES publisher_accounts(id) ON DELETE RESTRICT,
    version INTEGER NOT NULL,
    secret_reference_id UUID NOT NULL REFERENCES secret_references(id) ON DELETE RESTRICT,
    required_scopes JSONB NOT NULL DEFAULT '[]'::jsonb,
    granted_scopes JSONB NOT NULL DEFAULT '[]'::jsonb,
    status VARCHAR(32) NOT NULL,
    expires_at TIMESTAMPTZ,
    refresh_after TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    CONSTRAINT uq_publisher_connections_account_version UNIQUE (publisher_account_id, version)
);
CREATE INDEX ix_publisher_connections_account_id ON publisher_connections (publisher_account_id);

CREATE TABLE publisher_capability_profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    publisher_account_id UUID REFERENCES publisher_accounts(id) ON DELETE RESTRICT,
    publisher_id VARCHAR(128) NOT NULL,
    publisher_version VARCHAR(64) NOT NULL,
    profile_version INTEGER NOT NULL,
    platform VARCHAR(64) NOT NULL,
    audit_state VARCHAR(32) NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ,
    source_reference TEXT NOT NULL,
    capability_facts JSONB NOT NULL,
    compatibility JSONB NOT NULL,
    CONSTRAINT uq_publisher_capability_profile_version
        UNIQUE (workspace_id, publisher_id, publisher_version, profile_version)
);
CREATE INDEX ix_publisher_capability_profiles_account_id ON publisher_capability_profiles (publisher_account_id);

CREATE TABLE publication_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    content_program_id UUID NOT NULL REFERENCES content_programs(id) ON DELETE CASCADE,
    ready_package_id UUID NOT NULL REFERENCES ready_to_publish_packages(id) ON DELETE RESTRICT,
    publisher_account_id UUID NOT NULL REFERENCES publisher_accounts(id) ON DELETE RESTRICT,
    publisher_capability_profile_id UUID REFERENCES publisher_capability_profiles(id) ON DELETE RESTRICT,
    approval_request_id UUID REFERENCES approval_requests(id) ON DELETE RESTRICT,
    request_key VARCHAR(255) NOT NULL,
    version INTEGER NOT NULL,
    publisher_id VARCHAR(128) NOT NULL,
    platform VARCHAR(64) NOT NULL,
    destination VARCHAR(255) NOT NULL,
    locale VARCHAR(32) NOT NULL,
    territory VARCHAR(64) NOT NULL,
    visibility VARCHAR(32) NOT NULL,
    capability_profile_version INTEGER NOT NULL,
    approval_reference VARCHAR(255) NOT NULL,
    scheduled_for TIMESTAMPTZ,
    state VARCHAR(64) NOT NULL DEFAULT 'planned',
    request_fingerprint VARCHAR(64) NOT NULL,
    disclosure_projection JSONB NOT NULL,
    policy_references JSONB NOT NULL DEFAULT '[]'::jsonb,
    rights_references JSONB NOT NULL DEFAULT '[]'::jsonb,
    trace_id VARCHAR(64),
    span_id VARCHAR(32),
    provenance_record_id UUID REFERENCES provenance_records(id) ON DELETE SET NULL,
    CONSTRAINT uq_publication_requests_program_key_version
        UNIQUE (content_program_id, request_key, version)
);
CREATE INDEX ix_publication_requests_workspace_id ON publication_requests (workspace_id);
CREATE INDEX ix_publication_requests_ready_package_id ON publication_requests (ready_package_id);
CREATE INDEX ix_publication_requests_account_id ON publication_requests (publisher_account_id);
CREATE INDEX ix_publication_requests_schedule ON publication_requests (scheduled_for);

CREATE TABLE publication_plans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    publication_request_id UUID NOT NULL REFERENCES publication_requests(id) ON DELETE RESTRICT,
    version INTEGER NOT NULL,
    publisher_id VARCHAR(128) NOT NULL,
    publisher_version VARCHAR(64) NOT NULL,
    external_effect_id UUID REFERENCES external_effects(id) ON DELETE RESTRICT,
    budget_reservation_id UUID REFERENCES budget_reservations(id) ON DELETE RESTRICT,
    state VARCHAR(64) NOT NULL DEFAULT 'planned',
    estimated_cost_micros BIGINT NOT NULL DEFAULT 0,
    actual_cost_micros BIGINT,
    cost_currency VARCHAR(3) NOT NULL DEFAULT 'USD',
    trace_id VARCHAR(64),
    span_id VARCHAR(32),
    audit_event_id UUID REFERENCES audit_events(id) ON DELETE SET NULL,
    provenance_record_id UUID REFERENCES provenance_records(id) ON DELETE SET NULL,
    CONSTRAINT uq_publication_plans_request_version UNIQUE (publication_request_id, version),
    CONSTRAINT uq_publication_plans_external_effect UNIQUE (external_effect_id),
    CONSTRAINT uq_publication_plans_reservation UNIQUE (budget_reservation_id)
);
CREATE INDEX ix_publication_plans_request_id ON publication_plans (publication_request_id);

CREATE TABLE publication_schedules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    publication_request_id UUID NOT NULL REFERENCES publication_requests(id) ON DELETE RESTRICT,
    publication_plan_id UUID NOT NULL REFERENCES publication_plans(id) ON DELETE RESTRICT,
    job_schedule_id UUID REFERENCES job_schedules(id) ON DELETE RESTRICT,
    version INTEGER NOT NULL,
    scheduled_for TIMESTAMPTZ NOT NULL,
    timezone VARCHAR(64) NOT NULL DEFAULT 'UTC',
    schedule_fingerprint VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'scheduled',
    CONSTRAINT uq_publication_schedules_request_version UNIQUE (publication_request_id, version),
    CONSTRAINT uq_publication_schedules_plan UNIQUE (publication_plan_id)
);
CREATE INDEX ix_publication_schedules_due ON publication_schedules (scheduled_for);

CREATE TABLE publication_attempts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    publication_plan_id UUID NOT NULL REFERENCES publication_plans(id) ON DELETE RESTRICT,
    attempt_number INTEGER NOT NULL,
    idempotency_key VARCHAR(255) NOT NULL,
    state VARCHAR(64) NOT NULL,
    request_fingerprint VARCHAR(64) NOT NULL,
    timeout_at TIMESTAMPTZ,
    retry_after TIMESTAMPTZ,
    submitted_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    trace_id VARCHAR(64),
    span_id VARCHAR(32),
    reconciliation JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT uq_publication_attempts_plan_number UNIQUE (publication_plan_id, attempt_number),
    CONSTRAINT uq_publication_attempts_plan_key UNIQUE (publication_plan_id, idempotency_key)
);
CREATE INDEX ix_publication_attempts_plan_id ON publication_attempts (publication_plan_id);

CREATE TABLE publication_status_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    publication_attempt_id UUID NOT NULL REFERENCES publication_attempts(id) ON DELETE RESTRICT,
    sequence_no INTEGER NOT NULL,
    state VARCHAR(64) NOT NULL,
    source VARCHAR(32) NOT NULL,
    safe_payload_hash VARCHAR(64) NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    trace_id VARCHAR(64),
    span_id VARCHAR(32),
    provenance_record_id UUID REFERENCES provenance_records(id) ON DELETE SET NULL,
    CONSTRAINT uq_publication_status_events_attempt_sequence UNIQUE (publication_attempt_id, sequence_no),
    CONSTRAINT uq_publication_status_events_attempt_source_hash
        UNIQUE (publication_attempt_id, source, safe_payload_hash)
);

CREATE TABLE publisher_webhook_receipts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    publication_attempt_id UUID NOT NULL REFERENCES publication_attempts(id) ON DELETE RESTRICT,
    publication_status_event_id UUID REFERENCES publication_status_events(id) ON DELETE RESTRICT,
    publisher_id VARCHAR(128) NOT NULL,
    delivery_identity VARCHAR(255) NOT NULL,
    safe_payload_hash VARCHAR(64) NOT NULL,
    signature_verified BOOLEAN NOT NULL,
    state VARCHAR(64) NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    trace_id VARCHAR(64),
    span_id VARCHAR(32),
    provenance_record_id UUID REFERENCES provenance_records(id) ON DELETE SET NULL,
    CONSTRAINT uq_publisher_webhook_provider_delivery UNIQUE (publisher_id, delivery_identity),
    CONSTRAINT uq_publisher_webhook_attempt_hash UNIQUE (publication_attempt_id, safe_payload_hash)
);

CREATE TABLE remote_publication_receipts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    publication_attempt_id UUID NOT NULL REFERENCES publication_attempts(id) ON DELETE RESTRICT,
    publisher_id VARCHAR(128) NOT NULL,
    remote_id VARCHAR(255) NOT NULL,
    state VARCHAR(64) NOT NULL,
    remote_url TEXT,
    safe_metadata_hash VARCHAR(64) NOT NULL,
    disclosure_projection JSONB NOT NULL DEFAULT '{}'::jsonb,
    actual_cost_micros BIGINT,
    cost_currency VARCHAR(3) NOT NULL DEFAULT 'USD',
    accepted_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    trace_id VARCHAR(64),
    span_id VARCHAR(32),
    provenance_record_id UUID REFERENCES provenance_records(id) ON DELETE SET NULL,
    CONSTRAINT uq_remote_publication_receipts_attempt UNIQUE (publication_attempt_id),
    CONSTRAINT uq_remote_publication_receipts_provider_remote UNIQUE (publisher_id, remote_id)
);

CREATE TABLE publications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    publication_request_id UUID NOT NULL REFERENCES publication_requests(id) ON DELETE RESTRICT,
    remote_publication_receipt_id UUID NOT NULL REFERENCES remote_publication_receipts(id) ON DELETE RESTRICT,
    state VARCHAR(32) NOT NULL,
    published_at TIMESTAMPTZ,
    trace_id VARCHAR(64),
    span_id VARCHAR(32),
    provenance_record_id UUID REFERENCES provenance_records(id) ON DELETE SET NULL,
    CONSTRAINT uq_publications_request UNIQUE (publication_request_id),
    CONSTRAINT uq_publications_remote_receipt UNIQUE (remote_publication_receipt_id)
);
"""


def upgrade() -> None:
    for statement in SCHEMA_SQL.split(";"):
        if statement.strip():
            op.execute(statement)
    op.execute(
        """
        CREATE FUNCTION reject_immutable_publication_record() RETURNS trigger AS $$
        BEGIN
            IF TG_TABLE_NAME = 'remote_publication_receipts' THEN
                RAISE EXCEPTION 'immutable remote publication receipt';
            ELSIF TG_TABLE_NAME = 'publication_requests' THEN
                RAISE EXCEPTION 'immutable publication request version';
            ELSIF TG_TABLE_NAME = 'publications' THEN
                RAISE EXCEPTION 'immutable publication receipt reference';
            ELSIF TG_TABLE_NAME = 'publisher_webhook_receipts' THEN
                RAISE EXCEPTION 'immutable publisher webhook receipt';
            ELSIF TG_TABLE_NAME = 'publication_status_events' THEN
                RAISE EXCEPTION 'immutable publication status event';
            END IF;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table_name in (
        "publication_requests",
        "publication_status_events",
        "publisher_webhook_receipts",
        "remote_publication_receipts",
        "publications",
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_immutable
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION reject_immutable_publication_record()
            """
        )


def downgrade() -> None:
    for table_name in (
        "publications",
        "remote_publication_receipts",
        "publisher_webhook_receipts",
        "publication_status_events",
        "publication_requests",
    ):
        op.execute(f"DROP TRIGGER trg_{table_name}_immutable ON {table_name}")
    op.execute("DROP FUNCTION reject_immutable_publication_record()")
    for table_name in (
        "publications",
        "remote_publication_receipts",
        "publisher_webhook_receipts",
        "publication_status_events",
        "publication_attempts",
        "publication_schedules",
        "publication_plans",
        "publication_requests",
        "publisher_capability_profiles",
        "publisher_connections",
        "publisher_accounts",
    ):
        op.drop_table(table_name)
