"""Add canonical Phase 5-6 intelligence-loop lineage."""

from alembic import op


revision = "0004_intelligence_loop"
down_revision = "0003_memory_strategy"
branch_labels = None
depends_on = None


SCHEMA_SQL = """
CREATE TABLE research_sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    content_program_id UUID NOT NULL REFERENCES content_programs(id) ON DELETE CASCADE,
    source_key VARCHAR(255) NOT NULL,
    version VARCHAR(64) NOT NULL,
    connector VARCHAR(128) NOT NULL,
    trust_level VARCHAR(64) NOT NULL,
    configuration JSONB NOT NULL DEFAULT '{}'::jsonb,
    network_scope JSONB NOT NULL DEFAULT '[]'::jsonb,
    rate_limit JSONB NOT NULL DEFAULT '{}'::jsonb,
    protocol_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (content_program_id, source_key, version)
);
CREATE INDEX ix_research_sources_program ON research_sources(content_program_id);

CREATE TABLE research_fetches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    content_program_id UUID NOT NULL REFERENCES content_programs(id) ON DELETE CASCADE,
    research_source_id UUID NOT NULL REFERENCES research_sources(id) ON DELETE RESTRICT,
    resource_identity VARCHAR(1024) NOT NULL,
    window_key VARCHAR(255) NOT NULL,
    request_fingerprint VARCHAR(128) NOT NULL,
    canonical_url TEXT NOT NULL,
    raw_content_hash VARCHAR(128),
    cursor_value VARCHAR(1024),
    status VARCHAR(64) NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
    trace_id VARCHAR(64),
    span_id VARCHAR(32),
    CONSTRAINT uq_research_fetch_source_resource_window UNIQUE (research_source_id, resource_identity, window_key)
);
CREATE INDEX ix_research_fetches_program ON research_fetches(content_program_id);

ALTER TABLE research_evidence ADD COLUMN research_source_id UUID REFERENCES research_sources(id) ON DELETE SET NULL;
ALTER TABLE research_evidence ADD COLUMN research_fetch_id UUID REFERENCES research_fetches(id) ON DELETE SET NULL;
ALTER TABLE research_evidence ADD COLUMN source_trust VARCHAR(64);
ALTER TABLE research_evidence ADD COLUMN content_hash VARCHAR(128);
ALTER TABLE research_evidence ADD COLUMN idempotency_key VARCHAR(255);
ALTER TABLE research_evidence ADD COLUMN published_at TIMESTAMPTZ;
ALTER TABLE research_evidence ADD COLUMN source_identity TEXT;
ALTER TABLE research_evidence ADD CONSTRAINT uq_research_evidence_program_idempotency UNIQUE (content_program_id, idempotency_key);

ALTER TABLE memory_records ADD COLUMN verified_at TIMESTAMPTZ;
ALTER TABLE memory_records ADD COLUMN valid_until TIMESTAMPTZ;
ALTER TABLE memory_records ADD COLUMN source_identity TEXT;
ALTER TABLE memory_records ADD COLUMN effect_classification VARCHAR(64);
ALTER TABLE memory_records ADD COLUMN tool_scope JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE memory_records ADD COLUMN network_scope JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE memory_records ADD COLUMN memory_write_authority JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE memory_records ADD COLUMN writer_actor JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE TABLE signals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    content_program_id UUID NOT NULL REFERENCES content_programs(id) ON DELETE CASCADE,
    fingerprint VARCHAR(128) NOT NULL,
    topic TEXT NOT NULL,
    features JSONB NOT NULL DEFAULT '{}'::jsonb,
    feature_availability JSONB NOT NULL DEFAULT '{}'::jsonb,
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
    trace_id VARCHAR(64),
    span_id VARCHAR(32),
    CONSTRAINT uq_signals_program_fingerprint UNIQUE (content_program_id, fingerprint)
);
CREATE INDEX ix_signals_program ON signals(content_program_id);

CREATE TABLE signal_support (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    signal_id UUID NOT NULL REFERENCES signals(id) ON DELETE CASCADE,
    research_evidence_id UUID NOT NULL REFERENCES research_evidence(id) ON DELETE RESTRICT,
    research_fetch_id UUID NOT NULL REFERENCES research_fetches(id) ON DELETE RESTRICT,
    research_source_id UUID NOT NULL REFERENCES research_sources(id) ON DELETE RESTRICT,
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
    trace_id VARCHAR(64),
    span_id VARCHAR(32),
    CONSTRAINT uq_signal_support_lineage UNIQUE (signal_id, research_evidence_id, research_fetch_id)
);

CREATE TABLE topic_opportunities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    content_program_id UUID NOT NULL REFERENCES content_programs(id) ON DELETE CASCADE,
    fingerprint VARCHAR(128) NOT NULL,
    topic TEXT NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'candidate',
    score NUMERIC(7,3) NOT NULL,
    base_score NUMERIC(7,3),
    semantic_adjustment NUMERIC(6,3),
    supporting_signal_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    feature_availability JSONB NOT NULL DEFAULT '{}'::jsonb,
    explanation TEXT NOT NULL,
    risks JSONB NOT NULL DEFAULT '[]'::jsonb,
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
    trace_id VARCHAR(64),
    span_id VARCHAR(32),
    CONSTRAINT uq_topic_opportunities_program_fingerprint UNIQUE (content_program_id, fingerprint)
);
CREATE INDEX ix_topic_opportunities_program ON topic_opportunities(content_program_id);

CREATE TABLE content_queue_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    content_program_id UUID NOT NULL REFERENCES content_programs(id) ON DELETE CASCADE,
    topic_opportunity_id UUID NOT NULL REFERENCES topic_opportunities(id) ON DELETE RESTRICT,
    strategy_version_id UUID REFERENCES strategy_versions(id) ON DELETE SET NULL,
    priority INTEGER NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'queued',
    scheduled_for TIMESTAMPTZ,
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (content_program_id, topic_opportunity_id)
);

CREATE TABLE strategic_packages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    content_program_id UUID NOT NULL REFERENCES content_programs(id) ON DELETE CASCADE,
    topic_opportunity_id UUID NOT NULL REFERENCES topic_opportunities(id) ON DELETE RESTRICT,
    diversity_fingerprint VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'candidate',
    package JSONB NOT NULL,
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
    trace_id VARCHAR(64),
    span_id VARCHAR(32),
    CONSTRAINT uq_strategic_packages_opportunity_fingerprint UNIQUE (topic_opportunity_id, diversity_fingerprint)
);

CREATE TABLE package_evaluations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    strategic_package_id UUID NOT NULL REFERENCES strategic_packages(id) ON DELETE CASCADE,
    evaluation_key VARCHAR(128) NOT NULL,
    deterministic_score NUMERIC(7,3) NOT NULL,
    semantic_score NUMERIC(7,3),
    status VARCHAR(32) NOT NULL DEFAULT 'evaluated',
    reason TEXT NOT NULL,
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
    trace_id VARCHAR(64),
    span_id VARCHAR(32),
    CONSTRAINT uq_package_evaluations_package_key UNIQUE (strategic_package_id, evaluation_key)
);

CREATE TABLE claims (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    content_program_id UUID NOT NULL REFERENCES content_programs(id) ON DELETE CASCADE,
    fingerprint VARCHAR(128) NOT NULL,
    claim_text TEXT NOT NULL,
    verification_status VARCHAR(64) NOT NULL DEFAULT 'unverified',
    confidence NUMERIC(5,4) NOT NULL DEFAULT 0.5000,
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
    trace_id VARCHAR(64),
    span_id VARCHAR(32),
    CONSTRAINT uq_claims_program_fingerprint UNIQUE (content_program_id, fingerprint)
);

CREATE TABLE claim_evidence (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    claim_id UUID NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    research_evidence_id UUID NOT NULL REFERENCES research_evidence(id) ON DELETE RESTRICT,
    relation VARCHAR(32) NOT NULL,
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
    trace_id VARCHAR(64),
    span_id VARCHAR(32),
    CONSTRAINT uq_claim_evidence_relation UNIQUE (claim_id, research_evidence_id, relation)
);

CREATE TABLE content_brief_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    content_program_id UUID NOT NULL REFERENCES content_programs(id) ON DELETE CASCADE,
    brief_key VARCHAR(255) NOT NULL,
    version INTEGER NOT NULL,
    topic_opportunity_id UUID NOT NULL REFERENCES topic_opportunities(id) ON DELETE RESTRICT,
    strategic_package_id UUID NOT NULL REFERENCES strategic_packages(id) ON DELETE RESTRICT,
    strategy_version_id UUID REFERENCES strategy_versions(id) ON DELETE SET NULL,
    content JSONB NOT NULL,
    claim_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
    trace_id VARCHAR(64),
    span_id VARCHAR(32),
    CONSTRAINT uq_content_brief_versions_program_key_version UNIQUE (content_program_id, brief_key, version)
);
CREATE INDEX ix_content_brief_versions_program ON content_brief_versions(content_program_id);

CREATE TABLE model_invocations (
    id UUID PRIMARY KEY,
    tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    workspace_id UUID REFERENCES workspaces(id) ON DELETE SET NULL,
    content_program_id UUID REFERENCES content_programs(id) ON DELETE SET NULL,
    job_id UUID REFERENCES jobs(id) ON DELETE SET NULL,
    parent_agent_run_id UUID REFERENCES agent_runs(id) ON DELETE SET NULL,
    capability VARCHAR(255) NOT NULL,
    status VARCHAR(64) NOT NULL,
    runtime_id VARCHAR(255),
    provider VARCHAR(255),
    model VARCHAR(255),
    provider_version VARCHAR(255),
    input_hash VARCHAR(128) NOT NULL,
    output_hash VARCHAR(128),
    input_artifact_reference TEXT,
    output_artifact_reference TEXT,
    usage JSONB NOT NULL DEFAULT '{}'::jsonb,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    actual_cost_micros BIGINT NOT NULL DEFAULT 0,
    error_type VARCHAR(255),
    trace_id VARCHAR(64),
    span_id VARCHAR(32)
);
CREATE INDEX ix_model_invocations_job ON model_invocations(job_id);
CREATE INDEX ix_model_invocations_trace ON model_invocations(trace_id);
"""


def upgrade() -> None:
    for statement in SCHEMA_SQL.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE research_evidence
        DROP CONSTRAINT uq_research_evidence_program_idempotency
        """
    )
    for column in (
        "writer_actor",
        "memory_write_authority",
        "network_scope",
        "tool_scope",
        "effect_classification",
        "source_identity",
        "valid_until",
        "verified_at",
    ):
        op.execute(f"ALTER TABLE memory_records DROP COLUMN {column}")
    for column in (
        "source_identity",
        "published_at",
        "idempotency_key",
        "content_hash",
        "source_trust",
        "research_fetch_id",
        "research_source_id",
    ):
        op.execute(f"ALTER TABLE research_evidence DROP COLUMN {column}")
    for table_name in (
        "model_invocations",
        "content_brief_versions",
        "claim_evidence",
        "claims",
        "package_evaluations",
        "strategic_packages",
        "content_queue_entries",
        "topic_opportunities",
        "signal_support",
        "signals",
        "research_fetches",
        "research_sources",
    ):
        op.drop_table(table_name)
