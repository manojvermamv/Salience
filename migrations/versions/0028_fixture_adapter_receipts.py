"""Deduplicate accepted no-effects adapter fixture receipts."""

from alembic import op


revision = "0028_fixture_adapter_receipts"
down_revision = "0027_auto_dispatch_escalation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE UNIQUE INDEX v4_fixture_adapter_receipt_once
        ON v4_cycle_events ((payload->>'message_id'))
        WHERE kind='fixture_adapter_accepted'
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM v4_cycle_events WHERE kind='fixture_adapter_accepted') THEN
                RAISE EXCEPTION 'preserve V4 fixture adapter acceptance history';
            END IF;
        END $$
    """)
    op.execute("DROP INDEX v4_fixture_adapter_receipt_once")
