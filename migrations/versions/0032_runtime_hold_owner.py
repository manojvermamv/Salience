"""Bind runtime hold ownership to the immutable original start subject."""

from alembic import op

revision = "0032_runtime_hold_owner"
down_revision = "0031_runtime_waits"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout='3s'")
    op.execute("LOCK TABLE v4_runtime_holds IN SHARE ROW EXCLUSIVE MODE")
    # Existing history must be verified, never reassigned or silently repaired.
    op.execute("""DO $$ BEGIN
        IF EXISTS (
            SELECT 1 FROM v4_runtime_holds h
            WHERE NOT EXISTS (SELECT 1 FROM v4_cycle_outbox o
                WHERE o.cycle_id=h.cycle_id AND o.kind='start' AND o.sequence=1 AND o.subject_id=h.owner_id)
        ) THEN RAISE EXCEPTION 'runtime hold owner binding requires canonical original start; preserve and inspect history';
        END IF;
    END $$""")
    op.execute("""
        CREATE FUNCTION v4_check_runtime_hold_owner() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM v4_cycle_outbox
                WHERE cycle_id=NEW.cycle_id AND kind='start' AND sequence=1 AND subject_id=NEW.owner_id) THEN
                RAISE EXCEPTION 'runtime hold owner binding requires canonical original start';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("""CREATE TRIGGER owner_binding BEFORE INSERT ON v4_runtime_holds
        FOR EACH ROW EXECUTE FUNCTION v4_check_runtime_hold_owner()""")


def downgrade():
    op.execute("SET LOCAL lock_timeout='3s'")
    op.execute("LOCK TABLE v4_runtime_holds IN SHARE ROW EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM v4_runtime_holds) THEN
            RAISE EXCEPTION 'preserve runtime hold owner binding; disable fixture workers without weakening populated history';
        END IF;
    END $$""")
    op.execute("DROP TRIGGER owner_binding ON v4_runtime_holds")
    op.execute("DROP FUNCTION v4_check_runtime_hold_owner()")
