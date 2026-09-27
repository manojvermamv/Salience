"""Canonical G0 parent reservations and G1 transfers, without production effects."""

from alembic import op


revision = "0023_cycle_allocations"
down_revision = "0022_program_policy_lock"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE v4_cycle_allocations (
            id uuid PRIMARY KEY,
            cycle_id uuid NOT NULL UNIQUE REFERENCES v4_cycles(id),
            workspace_id uuid NOT NULL REFERENCES workspaces(id),
            content_program_id uuid NOT NULL REFERENCES content_programs(id),
            account_ref text NOT NULL,
            currency text NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
            period_start timestamptz NOT NULL,
            period_end timestamptz NOT NULL CHECK (period_end > period_start),
            ceiling_micros bigint NOT NULL CHECK (ceiling_micros BETWEEN 0 AND 1000000000000000),
            budget_ids uuid[] NOT NULL,
            fingerprint text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute("""
        CREATE TABLE v4_cost_operations (
            id uuid PRIMARY KEY,
            allocation_id uuid NOT NULL REFERENCES v4_cycle_allocations(id),
            estimated_micros bigint NOT NULL CHECK (estimated_micros >= 0),
            reserved_micros bigint NOT NULL CHECK (reserved_micros >= estimated_micros AND reserved_micros <= 1000000000000000),
            category text NOT NULL CHECK (category IN ('research','generation','publishing','observation','learning')),
            provider text NOT NULL CHECK (provider='fixture.accounting'),
            fingerprint text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute("""
        CREATE TABLE v4_allocation_reservations (
            reservation_id uuid PRIMARY KEY REFERENCES budget_reservations(id),
            allocation_id uuid NOT NULL REFERENCES v4_cycle_allocations(id),
            budget_id uuid NOT NULL REFERENCES budgets(id),
            operation_id uuid REFERENCES v4_cost_operations(id),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute("CREATE UNIQUE INDEX v4_parent_budget ON v4_allocation_reservations (allocation_id,budget_id) WHERE operation_id IS NULL")
    op.execute("CREATE UNIQUE INDEX v4_operation_budget ON v4_allocation_reservations (operation_id,budget_id) WHERE operation_id IS NOT NULL")
    op.execute("""
        CREATE TABLE v4_cost_commands (
            id uuid PRIMARY KEY,
            allocation_id uuid NOT NULL REFERENCES v4_cycle_allocations(id),
            operation_id uuid REFERENCES v4_cost_operations(id),
            idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 256),
            action text NOT NULL,
            fingerprint text NOT NULL,
            payload jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (allocation_id,idempotency_key)
        )
    """)
    for table in ["v4_cycle_allocations", "v4_cost_operations", "v4_allocation_reservations", "v4_cost_commands"]:
        op.execute(f"CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION v4_immutable()")
    op.execute("""
        CREATE FUNCTION public.v4_preserve_cost_ledger() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF EXISTS (SELECT 1 FROM public.v4_allocation_reservations WHERE reservation_id=OLD.budget_reservation_id)
               OR (TG_OP='UPDATE' AND EXISTS (SELECT 1 FROM public.v4_allocation_reservations WHERE reservation_id=NEW.budget_reservation_id)) THEN
                RAISE EXCEPTION 'V4 cost ledger is immutable';
            END IF;
            IF TG_OP='DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("CREATE TRIGGER v4_ledger_history BEFORE UPDATE OR DELETE ON cost_ledger_entries FOR EACH ROW EXECUTE FUNCTION public.v4_preserve_cost_ledger()")


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM v4_cycle_allocations)
               OR EXISTS (SELECT 1 FROM v4_goal_revisions WHERE payload->>'schema_version'='GoalSpec.local.v3') THEN
                RAISE EXCEPTION 'preserve V3 allocation and liability history';
            END IF;
        END $$
    """)
    op.execute("DROP TRIGGER v4_ledger_history ON cost_ledger_entries")
    op.execute("DROP FUNCTION public.v4_preserve_cost_ledger()")
    op.execute("DROP TABLE v4_cost_commands")
    op.execute("DROP TABLE v4_allocation_reservations")
    op.execute("DROP TABLE v4_cost_operations")
    op.execute("DROP TABLE v4_cycle_allocations")
