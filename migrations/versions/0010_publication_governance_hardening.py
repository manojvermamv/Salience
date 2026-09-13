"""Harden governed-publication approval, scheduling, and immutability."""

from alembic import op


revision = "0010_publication_hardening"
down_revision = "0009_governed_publication"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE publication_requests
            ADD COLUMN publication_approval_request_id UUID
                REFERENCES approval_requests(id) ON DELETE RESTRICT
        """
    )
    op.execute(
        """
        ALTER TABLE publication_schedules
            ADD COLUMN budget_id UUID REFERENCES budgets(id) ON DELETE RESTRICT
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_immutable_publication_plan() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'immutable publication plan';
            END IF;
            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
               OR NEW.created_at IS DISTINCT FROM OLD.created_at
               OR NEW.publication_request_id IS DISTINCT FROM OLD.publication_request_id
               OR NEW.version IS DISTINCT FROM OLD.version
               OR NEW.publisher_id IS DISTINCT FROM OLD.publisher_id
               OR NEW.publisher_version IS DISTINCT FROM OLD.publisher_version
               OR NEW.state IS DISTINCT FROM OLD.state
               OR NEW.estimated_cost_micros IS DISTINCT FROM OLD.estimated_cost_micros
               OR NEW.actual_cost_micros IS DISTINCT FROM OLD.actual_cost_micros
               OR NEW.cost_currency IS DISTINCT FROM OLD.cost_currency
               OR NEW.trace_id IS DISTINCT FROM OLD.trace_id
               OR NEW.span_id IS DISTINCT FROM OLD.span_id
               OR NEW.audit_event_id IS DISTINCT FROM OLD.audit_event_id
               OR NEW.provenance_record_id IS DISTINCT FROM OLD.provenance_record_id
               OR (OLD.external_effect_id IS NOT NULL
                   AND NEW.external_effect_id IS DISTINCT FROM OLD.external_effect_id)
               OR (OLD.budget_reservation_id IS NOT NULL
                   AND NEW.budget_reservation_id IS DISTINCT FROM OLD.budget_reservation_id) THEN
                RAISE EXCEPTION 'immutable publication plan';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_immutable_publication_schedule() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'immutable publication schedule';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_immutable_publication_attempt() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'immutable publication attempt';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_publication_plans_immutable
        BEFORE UPDATE OR DELETE ON publication_plans
        FOR EACH ROW EXECUTE FUNCTION reject_immutable_publication_plan()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_publication_schedules_immutable
        BEFORE UPDATE OR DELETE ON publication_schedules
        FOR EACH ROW EXECUTE FUNCTION reject_immutable_publication_schedule()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_publication_attempts_immutable
        BEFORE UPDATE OR DELETE ON publication_attempts
        FOR EACH ROW EXECUTE FUNCTION reject_immutable_publication_attempt()
        """
    )


def downgrade() -> None:
    for table_name in ("publication_attempts", "publication_schedules", "publication_plans"):
        op.execute(f"DROP TRIGGER trg_{table_name}_immutable ON {table_name}")
    for function_name in (
        "reject_immutable_publication_attempt",
        "reject_immutable_publication_schedule",
        "reject_immutable_publication_plan",
    ):
        op.execute(f"DROP FUNCTION {function_name}()")
    op.drop_column("publication_schedules", "budget_id")
    op.drop_column("publication_requests", "publication_approval_request_id")
