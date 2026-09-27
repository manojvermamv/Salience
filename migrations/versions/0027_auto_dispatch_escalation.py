"""Deduplicate durable local outbox dead-letter escalations."""

from alembic import op


revision = "0027_auto_dispatch_escalation"
down_revision = "0026_cycle_governance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE UNIQUE INDEX v4_outbox_dead_letter_once
        ON v4_cycle_events ((payload->>'message_id'))
        WHERE kind='outbox_dead_letter'
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM v4_cycle_events WHERE kind='outbox_dead_letter') THEN
                RAISE EXCEPTION 'preserve V4 outbox dead-letter escalation history';
            END IF;
        END $$
    """)
    op.execute("DROP INDEX v4_outbox_dead_letter_once")
