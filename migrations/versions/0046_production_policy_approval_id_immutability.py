"""Keep decided tagged effect approval identity and history immutable."""

from alembic import op


revision = "0046_production_policy_approval_id_immutability"
down_revision = "0045_production_policy_approval_guard_repair"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""CREATE OR REPLACE FUNCTION public.v4_guard_effect_approval_identity_v1()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF OLD.request_context->>'schema_version' =
                       'salience.p1-effect-approval.v1'
                   AND (OLD.status <> 'pending'
                        OR OLD.decided_by IS NOT NULL
                        OR OLD.decided_at IS NOT NULL
                        OR OLD.decision_reason IS NOT NULL) THEN
                    RAISE EXCEPTION
                        'tagged effect approval decision history cannot be deleted';
                END IF;
                RETURN OLD;
            END IF;

            IF OLD.request_context->>'schema_version' =
                   'salience.p1-effect-approval.v1'
               AND (OLD.status <> 'pending'
                    OR OLD.decided_by IS NOT NULL
                    OR OLD.decided_at IS NOT NULL
                    OR OLD.decision_reason IS NOT NULL)
               AND NEW.id IS DISTINCT FROM OLD.id THEN
                RAISE EXCEPTION
                    'decided effect approval identity is immutable';
            END IF;
            RETURN NEW;
        END
        $$""")
    op.execute(
        "REVOKE ALL ON FUNCTION public.v4_guard_effect_approval_identity_v1() FROM PUBLIC"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS guard_effect_approval_identity_v1 ON public.approval_requests"
    )
    op.execute(
        """CREATE TRIGGER guard_effect_approval_identity_v1
           BEFORE UPDATE OR DELETE ON public.approval_requests
           FOR EACH ROW
           EXECUTE FUNCTION public.v4_guard_effect_approval_identity_v1()"""
    )


def downgrade() -> None:
    # Keep identity protection installed when only the Alembic marker moves
    # back. Dropping it would permit reuse or deletion of decided evidence.
    pass
