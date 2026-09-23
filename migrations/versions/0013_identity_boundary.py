"""Add isolated subject identity and append-only access decisions."""

from alembic import op


revision = "0013_identity_boundary"
down_revision = "0012_publication_profile_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE identity_subjects (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
            issuer TEXT NOT NULL,
            subject TEXT NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT true,
            expires_at TIMESTAMPTZ NOT NULL,
            revision BIGINT NOT NULL DEFAULT 1 CHECK (revision > 0),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (issuer, subject)
        );
    """)
    op.execute("""
        CREATE TABLE identity_access_events (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            subject_id UUID REFERENCES identity_subjects(id) ON DELETE RESTRICT,
            workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
            trace_id VARCHAR(32) NOT NULL,
            span_id VARCHAR(16) NOT NULL,
            required_scope TEXT NOT NULL,
            outcome TEXT NOT NULL CHECK (outcome IN ('allow', 'deny', 'unauthenticated')),
            subject_revision BIGINT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)
    op.execute("""
        CREATE INDEX ix_identity_access_subject ON identity_access_events(subject_id, created_at);
    """)
    op.execute("""
        CREATE FUNCTION protect_identity_access() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'identity access decisions are append-only';
        END; $$;
    """)
    op.execute("""
        CREATE TRIGGER identity_access_immutable BEFORE UPDATE OR DELETE ON identity_access_events
            FOR EACH ROW EXECUTE FUNCTION protect_identity_access();
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM identity_access_events) OR EXISTS (SELECT 1 FROM identity_subjects) THEN
                RAISE EXCEPTION 'preserve identity and audit data; roll back configuration, not populated identity tables';
            END IF;
        END $$;
    """)
    op.execute("DROP TABLE identity_access_events")
    op.execute("DROP FUNCTION protect_identity_access()")
    op.execute("DROP TABLE identity_subjects")
