"""Add canonical callable-agent and team records."""

from alembic import op


revision = "0002_agents"
down_revision = "0001_canonical_foundation"
branch_labels = None
depends_on = None


SCHEMA_SQL = """
CREATE TABLE agent_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    agent_id VARCHAR(128) NOT NULL,
    version VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'enabled',
    input_schema JSONB NOT NULL,
    output_schema JSONB NOT NULL,
    tool_scopes JSONB NOT NULL DEFAULT '[]'::jsonb,
    memory_scopes JSONB NOT NULL DEFAULT '[]'::jsonb,
    effect_classification VARCHAR(64) NOT NULL,
    supports_sync BOOLEAN NOT NULL,
    supports_async BOOLEAN NOT NULL,
    timeout_seconds INTEGER NOT NULL,
    protocol_compatibility JSONB NOT NULL DEFAULT '{}'::jsonb,
    trust_classification VARCHAR(64) NOT NULL DEFAULT 'unclassified',
    delegated_authority_scopes JSONB NOT NULL DEFAULT '[]'::jsonb,
    health JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT uq_agent_versions_agent_version UNIQUE (agent_id, version)
);

CREATE TABLE agent_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    content_program_id UUID REFERENCES content_programs(id) ON DELETE RESTRICT,
    agent_version_id UUID NOT NULL REFERENCES agent_versions(id) ON DELETE RESTRICT,
    job_id UUID REFERENCES jobs(id) ON DELETE SET NULL,
    parent_agent_run_id UUID REFERENCES agent_runs(id),
    state VARCHAR(32) NOT NULL DEFAULT 'queued',
    input_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    output_payload JSONB,
    runtime_mapping JSONB NOT NULL DEFAULT '{}'::jsonb,
    artifact_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    trace_id VARCHAR(64) NOT NULL,
    span_id VARCHAR(32),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
);

CREATE TABLE agent_delegations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    parent_agent_run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    child_agent_run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    delegated_scopes JSONB NOT NULL DEFAULT '[]'::jsonb,
    delegated_authority JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT uq_agent_delegations_parent_child UNIQUE (parent_agent_run_id, child_agent_run_id)
);

CREATE TABLE team_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    team_id VARCHAR(128) NOT NULL,
    version VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'enabled',
    manifest JSONB NOT NULL,
    CONSTRAINT uq_team_versions_team_version UNIQUE (team_id, version)
);

CREATE TABLE team_members (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    team_version_id UUID NOT NULL REFERENCES team_versions(id) ON DELETE CASCADE,
    agent_version_id UUID NOT NULL REFERENCES agent_versions(id) ON DELETE RESTRICT,
    role VARCHAR(128) NOT NULL,
    CONSTRAINT uq_team_members_version_role UNIQUE (team_version_id, role)
);

CREATE TABLE agent_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    agent_run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    event_type VARCHAR(128) NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    trace_id VARCHAR(64) NOT NULL,
    span_id VARCHAR(32)
);
"""


def upgrade() -> None:
    for statement in SCHEMA_SQL.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    for table_name in (
        "agent_events",
        "team_members",
        "team_versions",
        "agent_delegations",
        "agent_runs",
        "agent_versions",
    ):
        op.drop_table(table_name)
