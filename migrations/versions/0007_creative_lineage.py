"""Anchor retry-safe provider ownership and package asset lineage."""

from alembic import op


revision = "0007_creative_lineage"
down_revision = "0006_creative_production"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE provider_jobs
        ADD CONSTRAINT uq_provider_jobs_creative_provider
        UNIQUE (creative_job_id, provider_id)
        """
    )
    op.execute(
        """
        CREATE TABLE distribution_package_assets (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            distribution_package_id UUID NOT NULL REFERENCES distribution_packages(id) ON DELETE CASCADE,
            asset_id UUID NOT NULL REFERENCES assets(id) ON DELETE RESTRICT,
            asset_role VARCHAR(64) NOT NULL,
            selection_reason TEXT,
            CONSTRAINT uq_distribution_package_assets_role UNIQUE (
                distribution_package_id, asset_role
            )
        )
        """
    )


def downgrade() -> None:
    op.drop_table("distribution_package_assets")
    op.drop_constraint(
        "uq_provider_jobs_creative_provider", "provider_jobs", type_="unique"
    )
