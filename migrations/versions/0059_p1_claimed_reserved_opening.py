"""Recognize the canonical reserved plan when opening claimed publication recovery."""

from alembic import op


revision = "0059_p1_claimed_reserved_opening"
down_revision = "0058_p1_preclaim_rollback_v5"
branch_labels = None
depends_on = None


_OPENER = "public.v4_open_production_recovery_case_v1(jsonb,jsonb)"
_TRUSTED_CONFIGURATION = """routine.prosecdef AND language.lanname='plpgsql'
    AND routine.proconfig=ARRAY['search_path=pg_catalog, public','TimeZone=UTC']"""
_PREVIOUS_BODY_SHA256 = "880dd545259bb28d79271be49f7d64a9996dedcebea068201d5be64444ca87db"
_RESERVED_BODY_SHA256 = "9c570e6df73127448854eb5fc27ec9e4371bf54ea61aa760ba2064cab466dcbe"
_ORIGINAL_PREDICATE = """              AND plan.publisher_id=permit.provider_id AND plan.state IN ('planned','authorized')"""
_RESERVED_PREDICATE = """              AND plan.publisher_id=permit.provider_id
              AND (
                  plan.state IN ('planned','authorized') OR (
                      plan.state='reserved' AND plan.actual_cost_micros IS NULL
                      AND plan.budget_reservation_id::text=permit.request_snapshot->>'budget_reservation_id'
                      AND effect_row.idempotency_key=pointer.idempotency_key||chr(58)||'publish'
                      AND EXISTS (
                          SELECT 1 FROM public.budgets budget
                          JOIN public.budget_reservations reservation ON reservation.budget_id=budget.id
                          WHERE reservation.id=plan.budget_reservation_id
                            AND budget.id::text=permit.request_snapshot->>'budget_id'
                            AND budget.workspace_id=goal.workspace_id AND budget.content_program_id=permit.program_id
                            AND budget.scope='publisher_account:'||permit.account_id
                            AND reservation.job_id=job.id AND reservation.external_effect_id=effect_row.id
                            AND reservation.reservation_key=effect_row.idempotency_key
                            AND reservation.status='reserved' AND reservation.reserved_amount>0
                            AND reservation.estimated_amount BETWEEN 0 AND reservation.reserved_amount
                            -- The liability remains reserved after its original dispatch window ends.
                            AND isfinite(reservation.expires_at) AND reservation.expires_at>effect_row.attempted_at
                          FOR SHARE OF budget,reservation
                      )
                  )
              )"""


def _replace_opener(before: str, after: str, expected_hash: str, result_hash: str) -> None:
    # Replace the one predicate in the exact inherited body. Keeping the function
    # in place preserves its owner, grants, and the evidence guards that identify it.
    op.execute(
        f"""DO $migration$
        DECLARE
            opener oid := '{_OPENER}'::pg_catalog.regprocedure;
            definition text;
            source_hash text;
            trusted_configuration boolean;
            before_text text := $before${before}$before$;
        BEGIN
            SELECT pg_catalog.pg_get_functiondef(routine.oid),
                   pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(routine.prosrc,'UTF8')),'hex'),
                   {_TRUSTED_CONFIGURATION}
            INTO STRICT definition,source_hash,trusted_configuration
            FROM pg_catalog.pg_proc routine JOIN pg_catalog.pg_language language ON language.oid=routine.prolang
            WHERE routine.oid=opener;
            IF trusted_configuration IS DISTINCT FROM true OR source_hash IS DISTINCT FROM '{expected_hash}'
               OR pg_catalog.length(definition)-pg_catalog.length(pg_catalog.replace(definition,before_text,''))
                    <>pg_catalog.length(before_text) THEN
                RAISE EXCEPTION 'unexpected claimed production case opener definition';
            END IF;
            EXECUTE pg_catalog.replace(definition,before_text,$after${after}$after$);
            SELECT pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(prosrc,'UTF8')),'hex')
            INTO STRICT source_hash
            FROM pg_catalog.pg_proc WHERE oid=opener;
            IF source_hash IS DISTINCT FROM '{result_hash}' THEN
                RAISE EXCEPTION 'unexpected resulting claimed production case opener definition';
            END IF;
        END
        $migration$"""
    )


def upgrade() -> None:
    _replace_opener(_ORIGINAL_PREDICATE, _RESERVED_PREDICATE, _PREVIOUS_BODY_SHA256, _RESERVED_BODY_SHA256)
    op.execute(
        f"""CREATE FUNCTION public.v4_p1_recovery_rollback_inventory_v6(p_workspace_id uuid)
            RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER
            SET search_path = pg_catalog, public AS $$
            WITH prior AS (
                SELECT public.v4_p1_recovery_rollback_inventory_v5(p_workspace_id) AS inventory
            ), opener AS (
                SELECT 1 FROM pg_catalog.pg_proc routine
                JOIN pg_catalog.pg_language language ON language.oid=routine.prolang
                WHERE routine.oid=pg_catalog.to_regprocedure('{_OPENER}')
                  AND {_TRUSTED_CONFIGURATION}
                  AND pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(routine.prosrc,'UTF8')),'hex')
                      ='{_RESERVED_BODY_SHA256}'
            )
            SELECT inventory || jsonb_build_object(
                'schema_version','salience.p1-recovery-rollback-inventory.v6',
                'migration_head','{revision}',
                'blockers',(inventory->'blockers') || CASE WHEN EXISTS (SELECT 1 FROM opener)
                    THEN '[]'::jsonb ELSE jsonb_build_array(jsonb_build_object(
                        'kind','production_claimed_case_opener_definition_mismatch')) END)
            FROM prior
            $$"""
    )
    op.execute("REVOKE ALL ON FUNCTION public.v4_p1_recovery_rollback_inventory_v6(uuid) FROM PUBLIC")


def downgrade() -> None:
    op.execute("DROP FUNCTION public.v4_p1_recovery_rollback_inventory_v6(uuid)")
    _replace_opener(_RESERVED_PREDICATE, _ORIGINAL_PREDICATE, _RESERVED_BODY_SHA256, _PREVIOUS_BODY_SHA256)
