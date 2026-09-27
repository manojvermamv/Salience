"""Preserve allocation scope and delegate budget locks without cap-edit authority."""

from alembic import op


revision = "0024_allocation_guards"
down_revision = "0023_cycle_allocations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION public.v4_lock_budgets(identities uuid[], expected_workspace uuid)
        RETURNS SETOF public.budgets LANGUAGE sql VOLATILE SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp AS $$
            SELECT budget.* FROM public.budgets AS budget
            WHERE budget.id=ANY(identities) AND budget.workspace_id=expected_workspace
            ORDER BY budget.id FOR UPDATE OF budget
        $$
    """)
    op.execute("REVOKE ALL ON FUNCTION public.v4_lock_budgets(uuid[],uuid) FROM PUBLIC")
    op.execute("""
        CREATE FUNCTION public.v4_preserve_budget_scope() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF (NEW.id,NEW.workspace_id,NEW.content_program_id,NEW.scope,NEW.currency,NEW.period_start,NEW.period_end)
               IS DISTINCT FROM (OLD.id,OLD.workspace_id,OLD.content_program_id,OLD.scope,OLD.currency,OLD.period_start,OLD.period_end)
               AND EXISTS (SELECT 1 FROM public.v4_allocation_reservations WHERE budget_id=OLD.id) THEN
                RAISE EXCEPTION 'allocated budget scope and period are immutable';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("CREATE TRIGGER v4_budget_scope BEFORE UPDATE ON budgets FOR EACH ROW EXECUTE FUNCTION public.v4_preserve_budget_scope()")
    op.execute("""
        CREATE FUNCTION public.v4_preserve_reservation() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE mapping record;
        BEGIN
            SELECT operation_id INTO mapping FROM public.v4_allocation_reservations WHERE reservation_id=OLD.id;
            IF FOUND THEN
                IF TG_OP='DELETE' THEN RAISE EXCEPTION 'V4 reservation identity is immutable'; END IF;
                IF (NEW.id,NEW.budget_id,NEW.reservation_key,NEW.job_id,NEW.external_effect_id,NEW.estimated_amount,NEW.expires_at)
                    IS DISTINCT FROM (OLD.id,OLD.budget_id,OLD.reservation_key,OLD.job_id,OLD.external_effect_id,OLD.estimated_amount,OLD.expires_at)
                    OR NEW.reserved_amount<0 OR NEW.reserved_amount>OLD.reserved_amount THEN
                    RAISE EXCEPTION 'V4 reservation identity and ceiling are immutable';
                END IF;
                IF OLD.status IN ('settled','released','overage_pending_approval') AND (NEW.status,NEW.reserved_amount) IS DISTINCT FROM (OLD.status,OLD.reserved_amount) THEN
                    RAISE EXCEPTION 'V4 terminal liability is immutable';
                END IF;
                IF OLD.status='pending_actual' AND NEW.status NOT IN ('pending_actual','settled','overage_pending_approval') THEN
                    RAISE EXCEPTION 'V4 unknown liability cannot be released';
                END IF;
                IF mapping.operation_id IS NULL AND NEW.status NOT IN ('reserved','released') THEN
                    RAISE EXCEPTION 'V4 parent cannot settle an operation';
                END IF;
            END IF;
            IF TG_OP='DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("CREATE TRIGGER v4_reservation_identity BEFORE UPDATE OR DELETE ON budget_reservations FOR EACH ROW EXECUTE FUNCTION public.v4_preserve_reservation()")


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM v4_cycle_allocations)
               OR EXISTS (SELECT 1 FROM v4_goal_revisions WHERE payload->>'schema_version'='GoalSpec.local.v3') THEN
                RAISE EXCEPTION 'preserve V3 allocation guards and liability history';
            END IF;
        END $$
    """)
    op.execute("DROP TRIGGER v4_reservation_identity ON budget_reservations")
    op.execute("DROP FUNCTION public.v4_preserve_reservation()")
    op.execute("DROP TRIGGER v4_budget_scope ON budgets")
    op.execute("DROP FUNCTION public.v4_preserve_budget_scope()")
    op.execute("DROP FUNCTION public.v4_lock_budgets(uuid[],uuid)")
