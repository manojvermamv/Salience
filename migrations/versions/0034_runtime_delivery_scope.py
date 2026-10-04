"""Protect canonical outbox scope and terminal-runtime hold evidence."""

from alembic import op

revision = "0034_runtime_delivery_scope"
down_revision = "0033_goal_state_commands"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout='3s'")
    op.execute("LOCK TABLE v4_cycle_outbox,v4_cycle_inbox,v4_runtime_holds IN SHARE ROW EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM v4_cycle_outbox message WHERE NOT EXISTS (
            SELECT 1 FROM v4_cycles cycle JOIN v4_cycle_intents intent ON intent.id=cycle.intent_id
            JOIN v4_goals goal ON goal.id=intent.goal_id
            JOIN identity_subjects actor ON actor.id=message.subject_id AND actor.workspace_id=goal.workspace_id
            WHERE cycle.id=message.cycle_id AND intent.id=message.intent_id
            AND goal.id=message.goal_id AND goal.workspace_id=message.workspace_id
        )) OR EXISTS (SELECT 1 FROM v4_cycle_inbox receipt JOIN v4_cycle_outbox message ON message.id=receipt.message_id
            WHERE receipt.cycle_id<>message.cycle_id) THEN
            RAISE EXCEPTION 'outbox scope mismatch; preserve and inspect delivery history';
        END IF;
    END $$""")
    op.execute("""CREATE FUNCTION v4_guard_delivery_scope() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_TABLE_NAME='v4_cycle_outbox' THEN
            IF NOT EXISTS (SELECT 1 FROM v4_cycles cycle JOIN v4_cycle_intents intent ON intent.id=cycle.intent_id
                JOIN v4_goals goal ON goal.id=intent.goal_id
                JOIN identity_subjects actor ON actor.id=NEW.subject_id AND actor.workspace_id=goal.workspace_id
                WHERE cycle.id=NEW.cycle_id AND intent.id=NEW.intent_id
                AND goal.id=NEW.goal_id AND goal.workspace_id=NEW.workspace_id) THEN
                RAISE EXCEPTION 'outbox requires canonical workspace goal intent cycle binding';
            END IF;
        ELSIF NOT EXISTS (SELECT 1 FROM v4_cycle_outbox message WHERE message.id=NEW.message_id AND message.cycle_id=NEW.cycle_id) THEN
            RAISE EXCEPTION 'inbox requires original outbox cycle binding';
        END IF;
        RETURN NEW;
    END $$""")
    for table in ('v4_cycle_outbox','v4_cycle_inbox'):
        op.execute(f"CREATE TRIGGER scope_binding BEFORE INSERT ON {table} FOR EACH ROW EXECUTE FUNCTION v4_guard_delivery_scope()")
    op.execute("ALTER TABLE v4_runtime_holds DROP CONSTRAINT v4_runtime_holds_reason_check")
    op.execute("ALTER TABLE v4_runtime_holds ADD CONSTRAINT v4_runtime_holds_reason_check CHECK (reason IN ('held_timeout','held_message_limit','held_runtime_failure'))")


def downgrade():
    op.execute("SET LOCAL lock_timeout='3s'")
    op.execute("LOCK TABLE v4_cycle_outbox,v4_cycle_inbox,v4_runtime_holds IN SHARE ROW EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM v4_cycle_outbox) OR EXISTS (SELECT 1 FROM v4_cycle_inbox)
            OR EXISTS (SELECT 1 FROM v4_runtime_holds WHERE reason='held_runtime_failure') THEN
            RAISE EXCEPTION 'preserve canonical delivery scope and runtime failure history';
        END IF;
    END $$""")
    op.execute("ALTER TABLE v4_runtime_holds DROP CONSTRAINT v4_runtime_holds_reason_check")
    op.execute("ALTER TABLE v4_runtime_holds ADD CONSTRAINT v4_runtime_holds_reason_check CHECK (reason IN ('held_timeout','held_message_limit'))")
    for table in ('v4_cycle_outbox','v4_cycle_inbox'):
        op.execute(f"DROP TRIGGER scope_binding ON {table}")
    op.execute("DROP FUNCTION v4_guard_delivery_scope()")
