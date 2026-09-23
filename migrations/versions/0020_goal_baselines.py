"""Require explicit revision-bound no-effects baseline approvals."""

from alembic import op


revision = "0020_goal_baselines"
down_revision = "0019_goal_revision_commands"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE v4_goal_baselines (
            approval_id uuid PRIMARY KEY,
            goal_id uuid NOT NULL,
            goal_revision integer NOT NULL,
            subject_id uuid NOT NULL REFERENCES identity_subjects(id),
            bundle jsonb NOT NULL,
            expires_at timestamptz NOT NULL,
            reason text NOT NULL CHECK (length(reason) BETWEEN 1 AND 2000),
            traceparent text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (goal_id,goal_revision),
            FOREIGN KEY (goal_id,goal_revision) REFERENCES v4_goal_revisions(goal_id,revision)
        )
    """)
    op.execute("""
        CREATE TABLE v4_baseline_revocations (
            approval_id uuid PRIMARY KEY REFERENCES v4_goal_baselines(approval_id),
            subject_id uuid NOT NULL REFERENCES identity_subjects(id),
            reason text NOT NULL CHECK (length(reason) BETWEEN 1 AND 2000),
            traceparent text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    for table in ["v4_goal_baselines","v4_baseline_revocations"]:
        op.execute(f"CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION v4_immutable()")


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM v4_goal_baselines) THEN
                RAISE EXCEPTION 'preserve baseline approval and revocation history';
            END IF;
        END $$
    """)
    op.execute("DROP TABLE v4_baseline_revocations")
    op.execute("DROP TABLE v4_goal_baselines")
