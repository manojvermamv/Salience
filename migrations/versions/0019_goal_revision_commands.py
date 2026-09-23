"""Preserve atomic goal revision commands and guarded mutable pointers."""

from alembic import op


revision = "0019_goal_revision_commands"
down_revision = "0018_cycle_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE v4_goal_commands (
            goal_id uuid NOT NULL REFERENCES v4_goals(id),
            idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 256),
            subject_id uuid NOT NULL REFERENCES identity_subjects(id),
            fingerprint text NOT NULL,
            expected_revision integer NOT NULL CHECK (expected_revision > 0),
            resulting_revision integer NOT NULL CHECK (resulting_revision=expected_revision+1),
            reason text NOT NULL CHECK (length(reason) BETWEEN 1 AND 2000),
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (goal_id,idempotency_key),
            FOREIGN KEY (goal_id,resulting_revision) REFERENCES v4_goal_revisions(goal_id,revision)
        )
    """)
    op.execute("CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON v4_goal_commands FOR EACH ROW EXECUTE FUNCTION v4_immutable()")
    op.execute("""
        CREATE FUNCTION v4_protect_goal() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP='DELETE' THEN RAISE EXCEPTION 'goal identity is immutable'; END IF;
            IF ROW(NEW.id,NEW.workspace_id,NEW.created_at)
               IS DISTINCT FROM ROW(OLD.id,OLD.workspace_id,OLD.created_at) THEN
                RAISE EXCEPTION 'goal identity is immutable';
            END IF;
            IF OLD.state IN ('completed','cancelled') AND NEW IS DISTINCT FROM OLD THEN
                RAISE EXCEPTION 'terminal goal is immutable';
            END IF;
            IF NEW.revision<>OLD.revision AND
               (NEW.revision<>OLD.revision+1 OR NOT EXISTS (
                    SELECT 1 FROM v4_goal_revisions WHERE goal_id=OLD.id AND revision=NEW.revision
               )) THEN
                RAISE EXCEPTION 'goal revision must advance to its next committed payload';
            END IF;
            IF NEW.state<>OLD.state AND NOT (
                (OLD.state='draft' AND NEW.state IN ('active','cancelled')) OR
                (OLD.state='active' AND NEW.state IN ('paused','completed','cancelled')) OR
                (OLD.state='paused' AND NEW.state IN ('active','completed','cancelled'))
            ) THEN RAISE EXCEPTION 'illegal goal state transition'; END IF;
            RETURN NEW;
        END; $$
    """)
    op.execute("CREATE TRIGGER identity_immutable BEFORE UPDATE OR DELETE ON v4_goals FOR EACH ROW EXECUTE FUNCTION v4_protect_goal()")


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM v4_goal_commands) THEN
                RAISE EXCEPTION 'preserve goal revision command history';
            END IF;
        END $$
    """)
    op.execute("DROP TRIGGER identity_immutable ON v4_goals")
    op.execute("DROP FUNCTION v4_protect_goal()")
    op.execute("DROP TABLE v4_goal_commands")
