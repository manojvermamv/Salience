"""Add one-use typed no-effect proof to production dispatch generations."""

from alembic import op


revision = "0050_p1_production_dispatch_reissue"
down_revision = "0049_p1_production_recovery_decision_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """ALTER TABLE public.v4_production_effect_permits
           ADD COLUMN recovery_command_id uuid,
           ADD CONSTRAINT fk_v4_production_effect_permits_recovery_command
             FOREIGN KEY (recovery_command_id)
             REFERENCES public.v4_production_recovery_events(event_id)
             ON DELETE RESTRICT"""
    )
    op.execute(
        """CREATE UNIQUE INDEX uq_v4_production_effect_permits_recovery_command
           ON public.v4_production_effect_permits(recovery_command_id)
           WHERE recovery_command_id IS NOT NULL"""
    )
    op.execute(
        """ALTER TABLE public.v4_production_effect_permits
           DROP CONSTRAINT ck_v4_production_effect_permits_snapshot,
           ADD CONSTRAINT ck_v4_production_effect_permits_snapshot CHECK (
               jsonb_typeof(request_snapshot) = 'object'
               AND request_snapshot->>'generation' = generation::text
               AND request_snapshot->>'operation_id' = operation_id::text
               AND request_snapshot->>'workspace_id' = workspace_id::text
               AND request_snapshot->>'idempotency_key' IS NOT NULL
               AND (
                   (request_snapshot->>'schema_version' =
                        'salience.p1-production-effect-permit.v1'
                    AND generation = 1
                    AND recovery_command_id IS NULL
                    AND NOT (request_snapshot ? 'recovery_command_id'))
                   OR
                   (request_snapshot->>'schema_version' =
                        'salience.p1-production-effect-permit.v2'
                    AND request_snapshot->>'route_tuple_sha256' ~ '^[0-9a-f]{64}$'
                    AND request_snapshot ? 'recovery_command_id'
                    AND (
                        (generation = 1
                         AND recovery_command_id IS NULL
                         AND request_snapshot->'recovery_command_id' = 'null'::jsonb)
                        OR
                        (generation > 1
                         AND recovery_command_id IS NOT NULL
                         AND request_snapshot->>'recovery_command_id' =
                             recovery_command_id::text)
                    ))
               )
           )"""
    )
    op.execute(
        """DO $$
        DECLARE
            definition text;
        BEGIN
            definition := pg_get_functiondef(
                'public.v4_validate_production_effect_permit_v1()'::regprocedure
            );
            definition := replace(
                definition,
                'v4_validate_production_effect_permit_v1',
                'v4_validate_production_effect_permit_v2'
            );
            definition := replace(
                definition,
                'salience.p1-production-effect-permit.v1',
                'salience.p1-production-effect-permit.v2'
            );
            definition := replace(
                definition,
                '            IF NEW.effect_type = ''fixture_execution''',
                $validator$
            IF NEW.generation < 1
               OR jsonb_typeof(snapshot->'route_tuple_sha256') IS DISTINCT FROM 'string'
               OR snapshot->>'route_tuple_sha256' !~ '^[0-9a-f]{64}$'
               OR NOT (snapshot ? 'recovery_command_id')
               OR (NEW.generation = 1 AND (
                      NEW.recovery_command_id IS NOT NULL
                      OR snapshot->'recovery_command_id' IS DISTINCT FROM 'null'::jsonb
                  ))
               OR (NEW.generation > 1 AND (
                      NEW.recovery_command_id IS NULL
                      OR snapshot->>'recovery_command_id'
                           IS DISTINCT FROM NEW.recovery_command_id::text
                  )) THEN
                RAISE EXCEPTION 'production permit v2 requires exact route and recovery bindings';
            END IF;
            IF NEW.effect_type = 'fixture_execution'$validator$
            );
            IF definition = pg_get_functiondef(
                    'public.v4_validate_production_effect_permit_v1()'::regprocedure
               )
               OR position('production permit v2 requires exact route' IN definition) = 0
               OR position('salience.p1-production-effect-permit.v2' IN definition) = 0 THEN
                RAISE EXCEPTION 'failed to extend production permit validator';
            END IF;
            EXECUTE definition;
            DROP TRIGGER validate_v4_production_effect_permit_v1
                ON public.v4_production_effect_permits;
            CREATE TRIGGER validate_v4_production_effect_permit_v2
                BEFORE INSERT ON public.v4_production_effect_permits
                FOR EACH ROW
                EXECUTE FUNCTION public.v4_validate_production_effect_permit_v2();
            REVOKE ALL ON FUNCTION
                public.v4_validate_production_effect_permit_v2() FROM PUBLIC;
        END
        $$"""
    )
    op.execute(
        """DO $$
        DECLARE
            definition text;
        BEGIN
            definition := pg_get_functiondef(
                'public.v4_advance_production_current_dispatch_v1(uuid,integer,uuid,uuid,text,uuid)'::regprocedure
            );
            IF to_regprocedure(
                'public.v4_advance_production_current_dispatch_v1_pre_0050(uuid,integer,uuid,uuid,text,uuid)'
            ) IS NOT NULL THEN
                RAISE EXCEPTION 'pre-0050 dispatch function backup already exists';
            END IF;
            EXECUTE replace(
                definition,
                'v4_advance_production_current_dispatch_v1',
                'v4_advance_production_current_dispatch_v1_pre_0050'
            );
            REVOKE ALL ON FUNCTION
                public.v4_advance_production_current_dispatch_v1_pre_0050(uuid,integer,uuid,uuid,text,uuid)
                FROM PUBLIC;

            definition := replace(
                definition,
                '            has_current boolean;',
                '            has_current boolean;'
                || E'\n            prior_permit_row public.v4_production_effect_permits%ROWTYPE;'
                || E'\n            prior_claim_row public.v4_production_effect_permit_claims%ROWTYPE;'
                || E'\n            recovery_event_row public.v4_production_recovery_events%ROWTYPE;'
                || E'\n            recovery_work_row public.v4_production_recovery_work%ROWTYPE;'
            );
            definition := replace(
                definition,
                '            IF permit_row.generation > current_row.generation THEN',
                $dispatch$
            IF permit_row.generation = current_row.generation + 1 THEN
                IF recovery_command_id IS NULL
                   OR permit_row.recovery_command_id IS DISTINCT FROM recovery_command_id
                   OR claim_id IS NOT NULL
                   OR current_row.current_claim_id IS NULL
                   OR current_row.idempotency_key IS DISTINCT FROM idempotency_key THEN
                    RAISE EXCEPTION 'typed_no_effect_command_required';
                END IF;
                SELECT permit.* INTO prior_permit_row
                FROM public.v4_production_effect_permits permit
                WHERE permit.permit_id = current_row.current_permit_id
                  AND permit.operation_id = current_row.operation_id
                  AND permit.generation = current_row.generation
                FOR KEY SHARE;
                IF NOT FOUND
                   OR prior_permit_row.request_snapshot->>'idempotency_key'
                       IS DISTINCT FROM idempotency_key
                   OR encode(public.digest(prior_permit_row.request_snapshot::text, 'sha256'), 'hex')
                       IS DISTINCT FROM (
                           SELECT work.request_fingerprint
                           FROM public.v4_production_recovery_events event
                           JOIN public.v4_production_recovery_work work
                             ON work.work_id = event.work_id
                           WHERE event.event_id = recovery_command_id
                       ) THEN
                    RAISE EXCEPTION 'typed_no_effect_command_required';
                END IF;
                SELECT claim.* INTO prior_claim_row
                FROM public.v4_production_effect_permit_claims claim
                WHERE claim.claim_id = current_row.current_claim_id
                  AND claim.permit_id = prior_permit_row.permit_id
                  AND claim.operation_id = current_row.operation_id
                  AND claim.generation = current_row.generation
                FOR KEY SHARE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'current production liability is not claimed';
                END IF;
                SELECT event.* INTO recovery_event_row
                FROM public.v4_production_recovery_events event
                WHERE event.event_id = recovery_command_id
                  AND event.event_kind = 'same_operation_no_effect_reissue'
                FOR KEY SHARE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'typed_no_effect_command_required';
                END IF;
                SELECT work.* INTO recovery_work_row
                FROM public.v4_production_recovery_work work
                WHERE work.work_id = recovery_event_row.work_id
                  AND work.source_discriminant = 'cycle_case'
                  AND work.source_id::text = recovery_event_row.payload->>'case_id'
                  AND work.operation_id = current_row.operation_id
                  AND work.request_fingerprint = encode(
                      public.digest(prior_permit_row.request_snapshot::text, 'sha256'), 'hex'
                  )
                FOR KEY SHARE;
                IF NOT FOUND
                   OR recovery_event_row.payload->>'schema_version'
                        IS DISTINCT FROM 'salience.p1-same-operation-no-effect.v1'
                   OR recovery_event_row.payload->>'reason_code'
                        IS DISTINCT FROM 'authoritative_no_effect'
                   OR recovery_event_row.payload->>'result_class'
                        IS DISTINCT FROM 'authoritative_no_effect'
                   OR recovery_event_row.payload->>'operation_id'
                        IS DISTINCT FROM current_row.operation_id::text
                   OR recovery_event_row.payload->>'work_id'
                        IS DISTINCT FROM recovery_work_row.work_id::text
                   OR recovery_event_row.payload->>'case_id'
                        IS DISTINCT FROM recovery_work_row.source_id::text
                   OR recovery_event_row.payload->>'source_id'
                        IS DISTINCT FROM recovery_work_row.source_id::text
                   OR recovery_event_row.payload->>'state'
                        IS DISTINCT FROM recovery_work_row.state
                   OR recovery_event_row.payload->>'request_fingerprint'
                        IS DISTINCT FROM recovery_work_row.request_fingerprint
                   OR recovery_event_row.payload->>'event_key'
                        IS DISTINCT FROM recovery_event_row.event_key
                   OR recovery_event_row.payload->>'remote_identity_sha256'
                        IS DISTINCT FROM encode(
                            public.digest(
                                current_row.operation_id::text || ':' ||
                                v4_advance_production_current_dispatch_v1.idempotency_key,
                                'sha256'
                            ), 'hex'
                        )
                   OR recovery_event_row.payload->>'policy_decision_id'
                        IS DISTINCT FROM recovery_event_row.policy_decision_id::text
                   OR permit_row.request_snapshot->>'idempotency_key'
                        IS DISTINCT FROM current_row.idempotency_key
                   OR NOT EXISTS (
                       SELECT 1
                       FROM public.v4_recovery_cases recovery
                       JOIN public.v4_cycles cycle
                         ON cycle.id = recovery.target_cycle_id
                       WHERE recovery.id = recovery_work_row.source_id
                         AND recovery.workspace_id = recovery_work_row.workspace_id
                         AND recovery.goal_id = recovery_work_row.goal_id
                         AND recovery.target_cycle_id = recovery_work_row.cycle_id
                         AND recovery.context_id = recovery_work_row.context_id
                         AND recovery.operation_id = recovery_work_row.operation_id
                         AND recovery.revision = recovery_work_row.case_revision
                         AND recovery.failure_class = recovery_work_row.reason
                         AND recovery.state = recovery_work_row.state
                         AND cycle.state <> 'closed'
                   )
                   OR EXISTS (
                       SELECT 1
                       FROM public.v4_production_recovery_events later
                       WHERE later.work_id = recovery_work_row.work_id
                         AND later.event_kind = 'remote_read_observation'
                         AND later.occurred_at > recovery_event_row.occurred_at
                   ) THEN
                    RAISE EXCEPTION 'typed_no_effect_command_required';
                END IF;
                IF (prior_permit_row.request_snapshot - ARRAY[
                        'schema_version','generation','recovery_command_id',
                        'subject_revision','grant_ids','policy_version_id',
                        'policy_document_hash','approval_request_id','approval_expires_at',
                        'route_fence_decision_id','workspace_stop_revision',
                        'goal_stop_revision','baseline_approval_id','budget_reservation_ids'
                    ]::text[])
                   IS DISTINCT FROM
                   (permit_row.request_snapshot - ARRAY[
                        'schema_version','generation','recovery_command_id',
                        'subject_revision','grant_ids','policy_version_id',
                        'policy_document_hash','approval_request_id','approval_expires_at',
                        'route_fence_decision_id','workspace_stop_revision',
                        'goal_stop_revision','baseline_approval_id','budget_reservation_ids'
                    ]::text[]) THEN
                    RAISE EXCEPTION 'production generation request identity changed';
                END IF;
                PERFORM set_config(
                    'salience.production_current_dispatch_v1', 'advance', true
                );
                UPDATE public.v4_production_current_dispatch AS dispatch
                SET generation = permit_row.generation,
                    current_permit_id = permit_row.permit_id,
                    current_claim_id = NULL,
                    idempotency_key =
                        v4_advance_production_current_dispatch_v1.idempotency_key
                WHERE dispatch.operation_id = current_row.operation_id;
                PERFORM set_config(
                    'salience.production_current_dispatch_v1', '', true
                );
                RETURN;
            END IF;
            IF permit_row.generation > current_row.generation THEN$dispatch$
            );
            IF position('same_operation_no_effect_reissue' IN definition) = 0
               OR position('production generation request identity changed' IN definition) = 0 THEN
                RAISE EXCEPTION 'failed to extend production current dispatch function';
            END IF;
            EXECUTE definition;
            REVOKE ALL ON FUNCTION
                public.v4_advance_production_current_dispatch_v1(uuid,integer,uuid,uuid,text,uuid)
                FROM PUBLIC;
        END
        $$"""
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout='3s'")
    op.execute(
        """LOCK TABLE public.v4_production_effect_permits,
                         public.v4_production_current_dispatch
           IN ACCESS EXCLUSIVE MODE"""
    )
    op.execute(
        """DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM public.v4_production_effect_permits
                WHERE request_snapshot->>'schema_version' =
                          'salience.p1-production-effect-permit.v2'
                   OR recovery_command_id IS NOT NULL
            ) THEN
                RAISE EXCEPTION 'preserve production permit reissue history';
            END IF;
        END
        $$"""
    )
    op.execute(
        """DROP TRIGGER validate_v4_production_effect_permit_v2
           ON public.v4_production_effect_permits"""
    )
    op.execute(
        """DROP FUNCTION public.v4_validate_production_effect_permit_v2()"""
    )
    op.execute(
        """DO $$
        DECLARE
            definition text;
        BEGIN
            definition := pg_get_functiondef(
                'public.v4_advance_production_current_dispatch_v1_pre_0050(uuid,integer,uuid,uuid,text,uuid)'::regprocedure
            );
            EXECUTE replace(
                definition,
                'v4_advance_production_current_dispatch_v1_pre_0050',
                'v4_advance_production_current_dispatch_v1'
            );
        END
        $$"""
    )
    op.execute(
        """DROP FUNCTION
           public.v4_advance_production_current_dispatch_v1_pre_0050(uuid,integer,uuid,uuid,text,uuid)"""
    )
    op.execute(
        """DROP INDEX public.uq_v4_production_effect_permits_recovery_command"""
    )
    op.execute(
        """ALTER TABLE public.v4_production_effect_permits
           DROP CONSTRAINT fk_v4_production_effect_permits_recovery_command,
           DROP CONSTRAINT ck_v4_production_effect_permits_snapshot,
           DROP COLUMN recovery_command_id,
           ADD CONSTRAINT ck_v4_production_effect_permits_snapshot CHECK (
               jsonb_typeof(request_snapshot) = 'object'
               AND request_snapshot->>'schema_version' =
                   'salience.p1-production-effect-permit.v1'
               AND request_snapshot->>'generation' = generation::text
               AND request_snapshot->>'operation_id' = operation_id::text
               AND request_snapshot->>'workspace_id' = workspace_id::text
               AND request_snapshot->>'idempotency_key' IS NOT NULL
           )"""
    )
    op.execute(
        """CREATE TRIGGER validate_v4_production_effect_permit_v1
           BEFORE INSERT ON public.v4_production_effect_permits
           FOR EACH ROW
           EXECUTE FUNCTION public.v4_validate_production_effect_permit_v1()"""
    )
