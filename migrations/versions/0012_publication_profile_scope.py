"""Bind capability profile identity to its publisher account."""

from alembic import op


revision = "0012_publication_profile_scope"
down_revision = "0011_publication_plan_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE publisher_capability_profiles
            DROP CONSTRAINT uq_publisher_capability_profile_version
        """
    )
    op.execute(
        """
        ALTER TABLE publisher_capability_profiles
            ADD CONSTRAINT uq_publisher_capability_profile_account_version
            UNIQUE (workspace_id, publisher_account_id, publisher_id, publisher_version, profile_version)
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE publisher_capability_profiles
            DROP CONSTRAINT uq_publisher_capability_profile_account_version
        """
    )
    op.execute(
        """
        ALTER TABLE publisher_capability_profiles
            ADD CONSTRAINT uq_publisher_capability_profile_version
            UNIQUE (workspace_id, publisher_id, publisher_version, profile_version)
        """
    )
