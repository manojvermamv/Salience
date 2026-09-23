"""Commit local cycle messages with bounded ordered delivery and consumption."""

from alembic import op


revision = "0018_cycle_outbox"
down_revision = "0017_cycle_identity_guards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE v4_cycle_outbox (
            id uuid PRIMARY KEY, workspace_id uuid NOT NULL REFERENCES workspaces(id),
            subject_id uuid NOT NULL REFERENCES identity_subjects(id),
            goal_id uuid NOT NULL REFERENCES v4_goals(id),
            intent_id uuid NOT NULL REFERENCES v4_cycle_intents(id),
            cycle_id uuid NOT NULL REFERENCES v4_cycles(id),
            sequence integer NOT NULL CHECK (sequence > 0),
            kind text NOT NULL CHECK (kind IN ('start','recovery','close')),
            schema_version text NOT NULL DEFAULT 'CycleMessage.local.v1',
            payload jsonb NOT NULL, traceparent text NOT NULL,
            state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','leased','delivered','dead_letter')),
            attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
            lease_token uuid, lease_until timestamptz,
            next_attempt_at timestamptz NOT NULL DEFAULT now(),
            last_error text, delivered_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (cycle_id,sequence),
            CHECK ((state='delivered') = (delivered_at IS NOT NULL))
        )
    """)
    op.execute("CREATE INDEX v4_cycle_outbox_due ON v4_cycle_outbox(workspace_id,state,next_attempt_at)")
    op.execute("""
        CREATE TABLE v4_cycle_inbox (
            id uuid PRIMARY KEY, message_id uuid NOT NULL UNIQUE REFERENCES v4_cycle_outbox(id),
            cycle_id uuid NOT NULL REFERENCES v4_cycles(id),
            state text NOT NULL CHECK (state IN ('recorded','held')),
            traceparent text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON v4_cycle_inbox FOR EACH ROW EXECUTE FUNCTION v4_immutable()")
    op.execute("""
        CREATE FUNCTION v4_protect_outbox() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP='DELETE' THEN RAISE EXCEPTION 'outbox message is immutable'; END IF;
            IF ROW(NEW.id,NEW.workspace_id,NEW.subject_id,NEW.goal_id,NEW.intent_id,NEW.cycle_id,NEW.sequence,NEW.kind,NEW.schema_version,NEW.payload,NEW.traceparent,NEW.created_at)
               IS DISTINCT FROM ROW(OLD.id,OLD.workspace_id,OLD.subject_id,OLD.goal_id,OLD.intent_id,OLD.cycle_id,OLD.sequence,OLD.kind,OLD.schema_version,OLD.payload,OLD.traceparent,OLD.created_at) THEN
                RAISE EXCEPTION 'outbox message is immutable';
            END IF;
            RETURN NEW;
        END; $$
    """)
    op.execute("CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON v4_cycle_outbox FOR EACH ROW EXECUTE FUNCTION v4_protect_outbox()")


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM v4_cycle_outbox) THEN
                RAISE EXCEPTION 'preserve outbox delivery and consumption evidence';
            END IF;
        END $$
    """)
    op.execute("DROP TABLE v4_cycle_inbox")
    op.execute("DROP TABLE v4_cycle_outbox")
    op.execute("DROP FUNCTION v4_protect_outbox()")
