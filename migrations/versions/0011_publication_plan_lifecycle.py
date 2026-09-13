"""Allow only durable cost lifecycle transitions for publication plans."""

from alembic import op


revision = "0011_publication_plan_lifecycle"
down_revision = "0010_publication_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_immutable_publication_plan() RETURNS trigger AS $$
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
               OR NEW.estimated_cost_micros IS DISTINCT FROM OLD.estimated_cost_micros
               OR NEW.cost_currency IS DISTINCT FROM OLD.cost_currency
               OR NEW.trace_id IS DISTINCT FROM OLD.trace_id
               OR NEW.span_id IS DISTINCT FROM OLD.span_id
               OR NEW.audit_event_id IS DISTINCT FROM OLD.audit_event_id
               OR NEW.provenance_record_id IS DISTINCT FROM OLD.provenance_record_id
               OR (NEW.external_effect_id IS DISTINCT FROM OLD.external_effect_id
                   AND NOT (OLD.external_effect_id IS NULL
                            AND NEW.external_effect_id IS NOT NULL))
               OR (NEW.budget_reservation_id IS DISTINCT FROM OLD.budget_reservation_id
                   AND NOT (OLD.budget_reservation_id IS NULL
                            AND NEW.budget_reservation_id IS NOT NULL))
               OR (NEW.actual_cost_micros IS DISTINCT FROM OLD.actual_cost_micros
                   AND NOT (OLD.actual_cost_micros IS NULL
                            AND NEW.actual_cost_micros IS NOT NULL)) THEN
                RAISE EXCEPTION 'immutable publication plan';
            END IF;
            IF NEW.state IS DISTINCT FROM OLD.state
               AND NOT (
                   (OLD.state = 'planned' AND NEW.state = 'reserved')
                   OR (OLD.state = 'reserved' AND NEW.state IN (
                       'pending_actual', 'settled', 'overage_pending_approval'
                   ))
                   OR (OLD.state = 'pending_actual' AND NEW.state IN (
                       'settled', 'overage_pending_approval'
                   ))
               ) THEN
                RAISE EXCEPTION 'immutable publication plan';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_immutable_publication_plan() RETURNS trigger AS $$
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
