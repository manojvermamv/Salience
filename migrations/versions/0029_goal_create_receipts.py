"""Preserve exact public goal-creation command identity."""

from alembic import op


revision = "0029_goal_create_receipts"
down_revision = "0028_fixture_adapter_receipts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE UNIQUE INDEX v4_goals_workspace_identity ON v4_goals (id,workspace_id)")
    op.execute("CREATE UNIQUE INDEX identity_subjects_workspace_identity ON identity_subjects (id,workspace_id)")
    op.execute("""
        CREATE TABLE v4_goal_create_commands (
            workspace_id uuid NOT NULL,
            subject_id uuid NOT NULL,
            idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 256 AND length(btrim(idempotency_key)) > 0),
            fingerprint text NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
            goal_id uuid NOT NULL UNIQUE,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id,subject_id,idempotency_key),
            FOREIGN KEY (goal_id,workspace_id) REFERENCES v4_goals(id,workspace_id),
            FOREIGN KEY (subject_id,workspace_id) REFERENCES identity_subjects(id,workspace_id)
        )
    """)
    op.execute("CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON v4_goal_create_commands FOR EACH ROW EXECUTE FUNCTION v4_immutable()")


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM v4_goal_create_commands) THEN
                RAISE EXCEPTION 'preserve exact V4 goal creation receipts';
            END IF;
        END $$
    """)
    op.execute("DROP TABLE v4_goal_create_commands")
    op.execute("DROP INDEX identity_subjects_workspace_identity")
    op.execute("DROP INDEX v4_goals_workspace_identity")
