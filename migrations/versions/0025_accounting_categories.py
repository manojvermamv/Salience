"""Keep all ArchV4 cost categories attributable without enabling their operations."""

from alembic import op


revision = "0025_accounting_categories"
down_revision = "0024_allocation_guards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE v4_cost_operations DROP CONSTRAINT v4_cost_operations_category_check")
    op.execute("ALTER TABLE v4_cost_operations ADD CONSTRAINT v4_cost_operations_category_check CHECK (category IN ('research','generation','publication','evaluation','training','publishing','observation','learning'))")


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM v4_cost_operations WHERE category IN ('publication','evaluation','training')) THEN
                RAISE EXCEPTION 'preserve V3 cost category history';
            END IF;
        END $$
    """)
    op.execute("ALTER TABLE v4_cost_operations DROP CONSTRAINT v4_cost_operations_category_check")
    op.execute("ALTER TABLE v4_cost_operations ADD CONSTRAINT v4_cost_operations_category_check CHECK (category IN ('research','generation','publishing','observation','learning'))")
