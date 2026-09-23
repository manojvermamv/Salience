"""Enforce stable business identities and terminal cycle dispositions."""

from alembic import op


revision = "0017_cycle_identity_guards"
down_revision = "0016_cycle_admission"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION v4_protect_cycle_identity() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'V4 identity is immutable';
            END IF;
            IF TG_TABLE_NAME = 'v4_cycles' THEN
                IF OLD.state='closed' OR
                   ROW(NEW.id,NEW.intent_id,NEW.context_id,NEW.operation_id,NEW.created_at)
                   IS DISTINCT FROM ROW(OLD.id,OLD.intent_id,OLD.context_id,OLD.operation_id,OLD.created_at) THEN
                    RAISE EXCEPTION 'V4 cycle identity and closure are immutable';
                END IF;
            ELSE
                IF ROW(NEW.id,NEW.goal_id,NEW.goal_revision,NEW.slot,NEW.fingerprint,NEW.due_at,NEW.expires_at,NEW.created_at)
                   IS DISTINCT FROM ROW(OLD.id,OLD.goal_id,OLD.goal_revision,OLD.slot,OLD.fingerprint,OLD.due_at,OLD.expires_at,OLD.created_at) THEN
                    RAISE EXCEPTION 'V4 intent identity is immutable';
                END IF;
            END IF;
            RETURN NEW;
        END; $$
    """)
    for table in ["v4_cycles", "v4_cycle_intents"]:
        op.execute(f"CREATE TRIGGER identity_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION v4_protect_cycle_identity()")


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM v4_goals) THEN
                RAISE EXCEPTION 'preserve V4 cycle guards; disable local admission instead';
            END IF;
        END $$
    """)
    for table in ["v4_cycles", "v4_cycle_intents"]:
        op.execute(f"DROP TRIGGER identity_immutable ON {table}")
    op.execute("DROP FUNCTION v4_protect_cycle_identity()")
