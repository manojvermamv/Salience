"""Add dry-run V4 intent, context and lifecycle identities."""

from alembic import op


revision = "0016_cycle_admission"
down_revision = "0015_identity_lock"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE v4_goals (
            id uuid PRIMARY KEY,
            workspace_id uuid NOT NULL REFERENCES workspaces(id),
            revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
            state text NOT NULL CHECK (state IN ('draft','active','paused','completed','cancelled')),
            created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE TABLE v4_goal_revisions (
            goal_id uuid NOT NULL REFERENCES v4_goals(id), revision integer NOT NULL,
            schema_version text NOT NULL DEFAULT 'GoalSpec.local.v1',
            payload jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (goal_id,revision)
        )
    """)
    op.execute("""
        CREATE TABLE v4_cycle_intents (
            id uuid PRIMARY KEY, goal_id uuid NOT NULL REFERENCES v4_goals(id),
            goal_revision integer NOT NULL, slot text NOT NULL CHECK (length(slot) BETWEEN 1 AND 256),
            fingerprint text NOT NULL, due_at timestamptz NOT NULL, expires_at timestamptz NOT NULL,
            eligibility_revision integer NOT NULL DEFAULT 1 CHECK (eligibility_revision > 0),
            reviewed boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (goal_id,slot), CHECK (expires_at > due_at),
            FOREIGN KEY (goal_id,goal_revision) REFERENCES v4_goal_revisions(goal_id,revision)
        )
    """)
    op.execute("""
        CREATE TABLE v4_cycles (
            id uuid PRIMARY KEY, intent_id uuid NOT NULL UNIQUE REFERENCES v4_cycle_intents(id),
            context_id uuid NOT NULL UNIQUE, operation_id uuid NOT NULL UNIQUE,
            state text NOT NULL CHECK (state IN ('runnable','retry_due','reconciling','closed')),
            recovery_count integer NOT NULL DEFAULT 0 CHECK (recovery_count >= 0),
            disposition text CHECK (disposition IN ('defer','abstain','completed','cancelled')),
            reason text, closed_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CHECK ((state='closed') = (closed_at IS NOT NULL)),
            CHECK ((state='closed') = (disposition IS NOT NULL))
        )
    """)
    op.execute("""
        CREATE TABLE v4_run_contexts (
            id uuid PRIMARY KEY, cycle_id uuid NOT NULL UNIQUE REFERENCES v4_cycles(id),
            schema_version text NOT NULL DEFAULT 'RunContext.local.v1',
            payload jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("ALTER TABLE v4_cycles ADD FOREIGN KEY (context_id) REFERENCES v4_run_contexts(id) DEFERRABLE INITIALLY DEFERRED")
    op.execute("""
        CREATE TABLE v4_admissions (
            id uuid PRIMARY KEY, intent_id uuid NOT NULL REFERENCES v4_cycle_intents(id),
            eligibility_revision integer NOT NULL,
            disposition text NOT NULL CHECK (disposition IN ('admitted','deferred','denied','review_required')),
            reason text NOT NULL, cycle_id uuid REFERENCES v4_cycles(id),
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (intent_id,eligibility_revision),
            CHECK ((disposition='admitted') = (cycle_id IS NOT NULL))
        )
    """)
    op.execute("""
        CREATE TABLE v4_intent_successors (
            predecessor_cycle_id uuid PRIMARY KEY REFERENCES v4_cycles(id),
            successor_intent_id uuid NOT NULL REFERENCES v4_cycle_intents(id),
            created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE TABLE v4_cycle_events (
            id uuid PRIMARY KEY, workspace_id uuid NOT NULL REFERENCES workspaces(id),
            subject_id uuid NOT NULL REFERENCES identity_subjects(id),
            goal_id uuid NOT NULL REFERENCES v4_goals(id),
            intent_id uuid REFERENCES v4_cycle_intents(id), cycle_id uuid REFERENCES v4_cycles(id),
            schema_version text NOT NULL DEFAULT 'CycleEvent.local.v1',
            kind text NOT NULL, traceparent text NOT NULL, payload jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE FUNCTION v4_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'V4 record is immutable'; END; $$
    """)
    for table in ["v4_goal_revisions", "v4_run_contexts", "v4_admissions", "v4_intent_successors", "v4_cycle_events"]:
        op.execute(f"CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION v4_immutable()")


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM v4_goals) THEN
                RAISE EXCEPTION 'preserve V4 cycle history; disable local admission instead';
            END IF;
        END $$
    """)
    op.execute("ALTER TABLE v4_cycles DROP CONSTRAINT v4_cycles_context_id_fkey")
    for table in ["v4_cycle_events", "v4_intent_successors", "v4_admissions", "v4_run_contexts", "v4_cycles", "v4_cycle_intents", "v4_goal_revisions", "v4_goals"]:
        op.execute(f"DROP TABLE {table}")
    op.execute("DROP FUNCTION v4_immutable()")
