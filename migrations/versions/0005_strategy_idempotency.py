"""Make durable strategy proposals safe to retry."""

from alembic import op


revision = "0005_strategy_idempotency"
down_revision = "0004_intelligence_loop"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE strategy_versions ADD COLUMN idempotency_key VARCHAR(255)")
    op.execute(
        """
        ALTER TABLE strategy_versions
        ADD CONSTRAINT uq_strategy_versions_program_idempotency
        UNIQUE (content_program_id, idempotency_key)
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE strategy_versions
        DROP CONSTRAINT uq_strategy_versions_program_idempotency
        """
    )
    op.execute("ALTER TABLE strategy_versions DROP COLUMN idempotency_key")
