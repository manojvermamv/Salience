"""Serialize goal state commands without changing frozen specification revisions."""

from alembic import op

revision = "0033_goal_state_commands"
down_revision = "0032_runtime_hold_owner"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout='3s'")
    op.execute("ALTER TABLE v4_goals ADD COLUMN state_revision integer NOT NULL DEFAULT 1 CHECK (state_revision > 0)")
    op.execute("""CREATE TABLE v4_goal_state_commands (
        goal_id uuid NOT NULL, workspace_id uuid NOT NULL, actor_id uuid NOT NULL,
        idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 256 AND length(btrim(idempotency_key)) > 0),
        fingerprint text NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
        expected_revision integer NOT NULL CHECK (expected_revision > 0),
        expected_state_revision integer NOT NULL CHECK (expected_state_revision > 0),
        resulting_state_revision integer NOT NULL CHECK (resulting_state_revision IN (expected_state_revision,expected_state_revision+1)),
        state text NOT NULL CHECK (state IN ('draft','active','paused','completed','cancelled')),
        reason text NOT NULL CHECK (length(reason) BETWEEN 1 AND 2000 AND length(btrim(reason)) > 0),
        traceparent text NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY (goal_id,idempotency_key),
        FOREIGN KEY (goal_id,workspace_id) REFERENCES v4_goals(id,workspace_id),
        FOREIGN KEY (actor_id,workspace_id) REFERENCES identity_subjects(id,workspace_id),
        FOREIGN KEY (goal_id,expected_revision) REFERENCES v4_goal_revisions(goal_id,revision)
    )""")
    op.execute("CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON v4_goal_state_commands FOR EACH ROW EXECUTE FUNCTION v4_immutable()")
    op.execute("""CREATE FUNCTION v4_guard_goal_state_revision() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF NEW.state IS DISTINCT FROM OLD.state THEN
            IF NEW.revision <> OLD.revision OR NEW.state_revision <> OLD.state_revision+1 OR NOT EXISTS (
                SELECT 1 FROM v4_goal_state_commands command WHERE command.goal_id=OLD.id
                AND command.workspace_id=OLD.workspace_id AND command.expected_revision=OLD.revision
                AND command.expected_state_revision=OLD.state_revision
                AND command.resulting_state_revision=NEW.state_revision AND command.state=NEW.state
            ) THEN RAISE EXCEPTION 'goal state requires its exact next command receipt'; END IF;
        ELSIF NEW.state_revision IS DISTINCT FROM OLD.state_revision THEN
            RAISE EXCEPTION 'goal state revision changes only with state';
        END IF;
        RETURN NEW;
    END $$""")
    op.execute("CREATE TRIGGER state_revision_guard BEFORE UPDATE ON v4_goals FOR EACH ROW EXECUTE FUNCTION v4_guard_goal_state_revision()")


def downgrade():
    op.execute("SET LOCAL lock_timeout='3s'")
    op.execute("LOCK TABLE v4_goals,v4_goal_state_commands IN SHARE ROW EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM v4_goal_state_commands) OR EXISTS (SELECT 1 FROM v4_goals WHERE state_revision<>1) THEN
            RAISE EXCEPTION 'preserve exact goal state command history';
        END IF;
    END $$""")
    op.execute("DROP TRIGGER state_revision_guard ON v4_goals")
    op.execute("DROP FUNCTION v4_guard_goal_state_revision()")
    op.execute("DROP TABLE v4_goal_state_commands")
    op.execute("ALTER TABLE v4_goals DROP COLUMN state_revision")
