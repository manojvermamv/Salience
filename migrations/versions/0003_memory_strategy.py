"""Add scoped durable memory, evidence, and strategy records."""

from alembic import op


revision = "0003_memory_strategy"
down_revision = "0002_agents"
branch_labels = None
depends_on = None


SCHEMA_SQL = """
CREATE TABLE research_evidence (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    content_program_id UUID NOT NULL REFERENCES content_programs(id) ON DELETE CASCADE,
    source_uri TEXT NOT NULL,
    source_hash VARCHAR(128),
    fetched_at TIMESTAMPTZ NOT NULL,
    content JSONB NOT NULL,
    trust_level VARCHAR(64) NOT NULL,
    verification_status VARCHAR(64) NOT NULL,
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
    data_classification VARCHAR(32) NOT NULL DEFAULT 'internal',
    retention_policy VARCHAR(128),
    expires_at TIMESTAMPTZ
);

CREATE TABLE strategy_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    content_program_id UUID NOT NULL REFERENCES content_programs(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'provisional',
    strategy JSONB NOT NULL,
    assumptions JSONB NOT NULL DEFAULT '[]'::jsonb,
    evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT uq_strategy_versions_program_version UNIQUE (content_program_id, version)
);

CREATE TABLE memory_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    content_program_id UUID NOT NULL REFERENCES content_programs(id) ON DELETE CASCADE,
    scope VARCHAR(32) NOT NULL,
    content JSONB NOT NULL,
    trust_level VARCHAR(64) NOT NULL,
    confidence NUMERIC(5,4) NOT NULL,
    evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_uri TEXT,
    verification_status VARCHAR(64),
    expires_at TIMESTAMPTZ,
    superseded_by UUID REFERENCES memory_records(id),
    conflict_set JSONB NOT NULL DEFAULT '[]'::jsonb,
    writer_identity VARCHAR(255),
    data_classification VARCHAR(32) NOT NULL DEFAULT 'internal',
    retention_policy VARCHAR(128),
    delete_after TIMESTAMPTZ,
    provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
    trace_id VARCHAR(64),
    span_id VARCHAR(32)
);
CREATE INDEX ix_memory_records_program_scope ON memory_records(content_program_id, scope);
CREATE INDEX ix_research_evidence_program ON research_evidence(content_program_id);
"""


def upgrade() -> None:
    for statement in SCHEMA_SQL.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    op.drop_table("memory_records")
    op.drop_table("strategy_versions")
    op.drop_table("research_evidence")
