"""Fence publication successors backed by legacy claimed no-effect proof."""

from alembic import op


revision = "0060_p1_claimed_reissue_fence"
down_revision = "0059_p1_claimed_reserved_opening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE FUNCTION public.v4_is_legacy_claimed_publication_reissue_v1(
               p_operation_id uuid, p_generation integer, p_recovery_command_id uuid,
               p_request_snapshot jsonb
           ) RETURNS boolean LANGUAGE sql STABLE SET search_path = pg_catalog, public AS $$
           SELECT p_generation > 1 AND EXISTS (
               SELECT 1 FROM public.v4_production_recovery_events proof
               JOIN public.v4_production_recovery_work work ON work.work_id=proof.work_id
               JOIN public.v4_production_effect_permits predecessor
                 ON predecessor.operation_id=work.operation_id
                AND predecessor.generation=p_generation-1
                AND predecessor.workspace_id=work.workspace_id
                AND predecessor.cycle_id=work.cycle_id
                AND predecessor.context_id=work.context_id
               JOIN public.v4_production_effect_permit_claims claim
                 ON claim.permit_id=predecessor.permit_id
                AND claim.operation_id=predecessor.operation_id
                AND claim.generation=predecessor.generation
               WHERE proof.event_id=p_recovery_command_id
                 AND proof.event_kind='same_operation_no_effect_reissue'
                 AND proof.payload->>'schema_version'='salience.p1-same-operation-no-effect.v1'
                 AND work.source_discriminant='cycle_case' AND work.operation_id=p_operation_id
                 AND work.request_fingerprint=encode(
                     public.digest(predecessor.request_snapshot::text,'sha256'),'hex')
                 AND (predecessor.request_snapshot->>'publication_request_id' IS NOT NULL
                      OR p_request_snapshot->>'publication_request_id' IS NOT NULL)
           )
           $$"""
    )
    op.execute(
        """CREATE FUNCTION public.v4_fence_legacy_claimed_publication_reissue_v1()
           RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
           SET search_path = pg_catalog, public AS $$
           DECLARE
               successor public.v4_production_effect_permits%ROWTYPE;
           BEGIN
               IF TG_RELID='public.v4_production_effect_permits'::regclass THEN
                   successor := NEW;
               ELSE
                   IF TG_RELID='public.v4_production_current_dispatch'::regclass THEN
                       IF NEW.generation=OLD.generation AND (
                           OLD.current_claim_id IS NOT NULL OR NEW.current_claim_id IS NULL
                       ) THEN
                           RETURN NEW;
                       END IF;
                       SELECT permit.* INTO successor FROM public.v4_production_effect_permits permit
                       WHERE permit.permit_id=NEW.current_permit_id AND permit.operation_id=NEW.operation_id
                         AND permit.generation=NEW.generation;
                   ELSE
                       SELECT permit.* INTO successor FROM public.v4_production_effect_permits permit
                       WHERE permit.permit_id=NEW.permit_id AND permit.operation_id=NEW.operation_id
                         AND permit.generation=NEW.generation;
                   END IF;
               END IF;
               IF public.v4_is_legacy_claimed_publication_reissue_v1(
                   successor.operation_id,successor.generation,successor.recovery_command_id,
                   successor.request_snapshot
               ) THEN
                   RAISE EXCEPTION 'claimed_publication_reissue_requires_finality';
               END IF;
               RETURN NEW;
           END
           $$"""
    )
    for table, action in (
        ("v4_production_effect_permits", "INSERT"),
        ("v4_production_effect_permit_claims", "INSERT"),
        ("v4_production_current_dispatch", "UPDATE"),
    ):
        op.execute(
            f"""CREATE TRIGGER fence_legacy_claimed_publication_reissue
               BEFORE {action} ON public.{table}
               FOR EACH ROW WHEN (NEW.generation > 1)
               EXECUTE FUNCTION public.v4_fence_legacy_claimed_publication_reissue_v1()"""
        )
    op.execute(
        f"""CREATE FUNCTION public.v4_p1_recovery_rollback_inventory_v7(p_workspace_id uuid)
            RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER
            SET search_path = pg_catalog, public AS $$
            WITH prior AS (
                SELECT public.v4_p1_recovery_rollback_inventory_v6(p_workspace_id) AS inventory
            ), expected_routines(signature, definer, volatility, language, source_hash) AS (
                VALUES
                  ('public.v4_is_legacy_claimed_publication_reissue_v1(uuid,integer,uuid,jsonb)',false,'s','sql',
                   'c99608397773af17f7af1c31480980550a13c6b35a9a48bb477ee22f6dd9b82c'),
                  ('public.v4_fence_legacy_claimed_publication_reissue_v1()',true,'v','plpgsql',
                   'da1ef5706c948ae8dee91556351f91ed14d7dd83593be8c23169c352c6b0c566')
            ), expected_triggers(relation, type_mask) AS (
                VALUES ('public.v4_production_effect_permits',7),
                       ('public.v4_production_effect_permit_claims',7),
                       ('public.v4_production_current_dispatch',19)
            ), fence AS MATERIALIZED (
                SELECT (
                    SELECT count(*)=2 FROM expected_routines expected
                    JOIN pg_catalog.pg_proc routine ON routine.oid=to_regprocedure(expected.signature)
                    JOIN pg_catalog.pg_language language ON language.oid=routine.prolang
                    WHERE routine.prosecdef=expected.definer AND routine.provolatile::text=expected.volatility
                      AND language.lanname=expected.language
                      AND routine.proconfig=ARRAY['search_path=pg_catalog, public']
                      AND encode(sha256(convert_to(routine.prosrc,'UTF8')),'hex')=expected.source_hash
                ) AND (
                    SELECT count(*)=3 FROM expected_triggers expected
                    JOIN pg_catalog.pg_trigger guard ON guard.tgrelid=to_regclass(expected.relation)
                    WHERE guard.tgname='fence_legacy_claimed_publication_reissue'
                      AND guard.tgfoid=to_regprocedure('public.v4_fence_legacy_claimed_publication_reissue_v1()')
                      AND guard.tgtype=expected.type_mask AND guard.tgenabled='O'
                      AND NOT guard.tgisinternal AND guard.tgnargs=0
                      AND position(' WHEN ((new.generation > 1)) EXECUTE FUNCTION '
                                   IN pg_get_triggerdef(guard.oid,false))>0
                ) AS trusted
            ), held AS (
                SELECT COALESCE(jsonb_agg(jsonb_build_object(
                    'kind','production_claimed_publication_reissue_requires_finality',
                    'operation_id',permit.operation_id,'permit_id',permit.permit_id,
                    'generation',permit.generation) ORDER BY permit.operation_id,permit.generation),'[]')
                    AS blockers
                FROM public.v4_production_current_dispatch pointer
                JOIN public.v4_production_effect_permits permit
                  ON permit.permit_id=pointer.current_permit_id
                 AND permit.operation_id=pointer.operation_id AND permit.generation=pointer.generation
                WHERE permit.workspace_id=p_workspace_id
                  AND CASE WHEN (SELECT trusted FROM fence) THEN
                    public.v4_is_legacy_claimed_publication_reissue_v1(
                      permit.operation_id,permit.generation,permit.recovery_command_id,
                      permit.request_snapshot)
                    ELSE false END
                  AND (pointer.current_claim_id IS NULL OR NOT EXISTS (
                      SELECT 1 FROM public.v4_cycles cycle
                      JOIN public.publication_attempts attempt
                        ON attempt.id::text=permit.request_snapshot->>'publication_attempt_id'
                      JOIN public.publication_plans plan ON plan.id=attempt.publication_plan_id
                      JOIN public.remote_publication_receipts receipt ON receipt.publication_attempt_id=attempt.id
                      JOIN public.publications publication
                        ON publication.remote_publication_receipt_id=receipt.id
                       AND publication.publication_request_id=plan.publication_request_id
                      JOIN public.external_effects effect ON effect.id=plan.external_effect_id
                      WHERE cycle.id=permit.cycle_id AND cycle.operation_id=permit.operation_id
                        AND cycle.state='closed' AND cycle.disposition='completed'
                        AND plan.id::text=permit.request_snapshot->>'publication_plan_id'
                        AND plan.publication_request_id::text=permit.request_snapshot->>'publication_request_id'
                        AND effect.id::text=permit.request_snapshot->>'external_effect_id'
                        AND effect.workspace_id=permit.workspace_id AND effect.job_id::text=permit.request_snapshot->>'job_id'
                        AND effect.status='completed' AND effect.provider_reference=receipt.remote_id
                        AND receipt.publisher_id=permit.provider_id AND publication.state='published'
                  ))
            )
            SELECT inventory || jsonb_build_object(
                'schema_version','salience.p1-recovery-rollback-inventory.v7',
                'migration_head','{revision}',
                'blockers',(inventory->'blockers') || held.blockers || CASE WHEN fence.trusted
                    THEN '[]'::jsonb ELSE jsonb_build_array(jsonb_build_object(
                        'kind','production_claimed_reissue_fence_definition_mismatch')) END)
            FROM prior CROSS JOIN held CROSS JOIN fence
            $$"""
    )
    for function in (
        "v4_is_legacy_claimed_publication_reissue_v1(uuid,integer,uuid,jsonb)",
        "v4_fence_legacy_claimed_publication_reissue_v1()",
        "v4_p1_recovery_rollback_inventory_v7(uuid)",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION public.{function} FROM PUBLIC")


def downgrade() -> None:
    op.execute("DROP FUNCTION public.v4_p1_recovery_rollback_inventory_v7(uuid)")
    for table in (
        "v4_production_current_dispatch",
        "v4_production_effect_permit_claims",
        "v4_production_effect_permits",
    ):
        op.execute(f"DROP TRIGGER fence_legacy_claimed_publication_reissue ON public.{table}")
    op.execute("DROP FUNCTION public.v4_fence_legacy_claimed_publication_reissue_v1()")
    op.execute("DROP FUNCTION public.v4_is_legacy_claimed_publication_reissue_v1(uuid,integer,uuid,jsonb)")
