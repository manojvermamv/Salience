"""Add immutable production intent enrollment, recovery work, and event evidence."""

from alembic import op


revision = "0047_p1_production_recovery_work_events"
down_revision = "0046_production_policy_approval_id_immutability"
branch_labels = None
depends_on = None


_RECOVERY_SNAPSHOT_KEYS = """ARRAY[
    'production_intent_enrollment',
    'goal',
    'intent',
    'cycle',
    'context',
    'job',
    'production_current_dispatch',
    'production_permits',
    'production_permit_claims',
    'publication_request',
    'publication_plan',
    'publication_attempt',
    'external_effect',
    'budget_reservation',
    'output',
    'remote_publication_receipts',
    'publication_status_events',
    'cycle_events',
    'cycle_outbox',
    'job_checkpoints',
    'audit_events',
    'provenance_records'
]::text[]"""


def upgrade() -> None:
    op.execute(
        """CREATE TABLE public.v4_production_intent_enrollments (
            intent_id uuid NOT NULL
                REFERENCES public.v4_cycle_intents(id) ON DELETE RESTRICT,
            eligibility_revision integer NOT NULL CHECK (eligibility_revision > 0),
            workspace_id uuid NOT NULL
                REFERENCES public.workspaces(id) ON DELETE RESTRICT,
            goal_id uuid NOT NULL REFERENCES public.v4_goals(id) ON DELETE RESTRICT,
            goal_revision integer NOT NULL CHECK (goal_revision > 0),
            source_owner_id uuid NOT NULL
                REFERENCES public.identity_subjects(id) ON DELETE RESTRICT,
            subject_id uuid NOT NULL
                REFERENCES public.identity_subjects(id) ON DELETE RESTRICT,
            subject_revision bigint NOT NULL CHECK (subject_revision > 0),
            grant_ids jsonb NOT NULL
                CHECK (jsonb_typeof(grant_ids) = 'array'
                       AND jsonb_array_length(grant_ids) > 0),
            policy_version_id uuid NOT NULL
                REFERENCES public.policy_versions(id) ON DELETE RESTRICT,
            policy_document_hash text NOT NULL
                CHECK (policy_document_hash ~ '^[0-9a-f]{64}$'),
            workspace_stop_revision integer NOT NULL
                CHECK (workspace_stop_revision > 0),
            goal_stop_revision integer NOT NULL CHECK (goal_stop_revision > 0),
            artifact_sha256 text NOT NULL CHECK (artifact_sha256 ~ '^[0-9a-f]{64}$'),
            route_id uuid NOT NULL,
            slot_id uuid NOT NULL,
            route_owner_id uuid NOT NULL
                REFERENCES public.identity_subjects(id) ON DELETE RESTRICT,
            fencing_epoch bigint NOT NULL CHECK (fencing_epoch > 0),
            route_manifest_sha256 text NOT NULL
                CHECK (route_manifest_sha256 ~ '^[0-9a-f]{64}$'),
            external_lock_sha256 text NOT NULL
                CHECK (external_lock_sha256 ~ '^[0-9a-f]{64}$'),
            route_fence_decision_id uuid NOT NULL UNIQUE,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            CONSTRAINT pk_v4_production_intent_enrollments
                PRIMARY KEY (intent_id, eligibility_revision),
            CONSTRAINT uq_v4_production_intent_enrollments_scope
                UNIQUE (intent_id, eligibility_revision, workspace_id, goal_id),
            CONSTRAINT fk_v4_production_intent_enrollments_goal_revision
                FOREIGN KEY (goal_id, goal_revision)
                REFERENCES public.v4_goal_revisions(goal_id, revision)
                ON DELETE RESTRICT
        )"""
    )
    op.execute(
        """COMMENT ON TABLE public.v4_production_intent_enrollments IS
           'Immutable authorization and provenance envelope. Route tuple values are opaque external fence evidence: this migration has no route registry and does not treat a production permit as route-fence verification. The restricted Task 6 writer must verify the live fence and bind its route, slot, route owner, epoch, manifest digest, artifact-lock digest, and decision ID before insertion. PUBLIC has no write privilege.'"""
    )
    op.execute(
        """CREATE FUNCTION public.v4_validate_production_intent_enrollment_v1()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public AS $$
        DECLARE
            canonical_grants jsonb;
            grant_ids uuid[];
            grant_count integer;
            distinct_grant_count integer;
            valid_grant_count integer;
            current_workspace_stop integer;
            current_goal_stop integer;
            workspace_stopped boolean;
            goal_stopped boolean;
            identity_workspace uuid;
            source_owner_workspace uuid;
            route_owner_workspace uuid;
            current_subject_revision bigint;
            current_subject_enabled boolean;
            current_goal_workspace uuid;
            current_goal_revision integer;
            current_intent_goal uuid;
            current_intent_goal_revision integer;
            current_eligibility_revision integer;
            current_policy_workspace uuid;
            current_policy_status text;
            current_policy_hash text;
        BEGIN
            IF TG_RELID <> 'public.v4_production_intent_enrollments'::regclass
               OR TG_TABLE_SCHEMA <> 'public'
               OR TG_TABLE_NAME <> 'v4_production_intent_enrollments'
               OR TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION
                    'production intent enrollment guard only accepts its target insert';
            END IF;

            SELECT goal.workspace_id, goal.revision,
                   intent.goal_id, intent.goal_revision,
                   intent.eligibility_revision,
                   source_owner.workspace_id,
                   subject.workspace_id, subject.revision, subject.enabled,
                   route_owner.workspace_id,
                   policy.workspace_id, policy.status, policy.document_hash
            INTO current_goal_workspace, current_goal_revision,
                 current_intent_goal, current_intent_goal_revision,
                 current_eligibility_revision,
                 source_owner_workspace,
                 identity_workspace, current_subject_revision,
                 current_subject_enabled, route_owner_workspace,
                 current_policy_workspace, current_policy_status,
                 current_policy_hash
            FROM public.v4_cycle_intents intent
            JOIN public.v4_goals goal ON goal.id = intent.goal_id
            JOIN public.v4_goal_revisions goal_revision
              ON goal_revision.goal_id = goal.id
             AND goal_revision.revision = intent.goal_revision
            JOIN public.identity_subjects source_owner
              ON source_owner.id = NEW.source_owner_id
            JOIN public.identity_subjects subject
              ON subject.id = NEW.subject_id
            JOIN public.identity_subjects route_owner
              ON route_owner.id = NEW.route_owner_id
            JOIN public.policy_versions policy
              ON policy.id = NEW.policy_version_id
            WHERE intent.id = NEW.intent_id;

            IF NOT FOUND
               OR current_goal_workspace IS DISTINCT FROM NEW.workspace_id
               OR current_intent_goal IS DISTINCT FROM NEW.goal_id
               OR current_intent_goal_revision IS DISTINCT FROM NEW.goal_revision
               OR current_goal_revision IS DISTINCT FROM NEW.goal_revision
               OR current_eligibility_revision IS DISTINCT FROM NEW.eligibility_revision
               OR source_owner_workspace IS DISTINCT FROM NEW.workspace_id
               OR identity_workspace IS DISTINCT FROM NEW.workspace_id
               OR route_owner_workspace IS DISTINCT FROM NEW.workspace_id
               OR current_subject_revision IS DISTINCT FROM NEW.subject_revision
               OR current_subject_enabled IS DISTINCT FROM true
               OR current_policy_workspace IS DISTINCT FROM NEW.workspace_id
               OR current_policy_status IS DISTINCT FROM 'active'
               OR current_policy_hash IS DISTINCT FROM NEW.policy_document_hash THEN
                RAISE EXCEPTION
                    'production intent enrollment does not match current intent, scope, identity, or policy';
            END IF;

            SELECT array_agg(parsed.grant_id ORDER BY parsed.grant_id),
                   jsonb_agg(to_jsonb(parsed.grant_id::text)
                             ORDER BY parsed.grant_id::text),
                   count(*), count(DISTINCT parsed.grant_id)
            INTO grant_ids, canonical_grants, grant_count, distinct_grant_count
            FROM (
                SELECT value::uuid AS grant_id, value
                FROM jsonb_array_elements_text(NEW.grant_ids) AS input(value)
            ) parsed;

            IF grant_count IS NULL OR grant_count = 0
               OR grant_count <> distinct_grant_count
               OR canonical_grants IS DISTINCT FROM NEW.grant_ids
               OR EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements_text(NEW.grant_ids) AS input(value)
                    WHERE value::uuid::text IS DISTINCT FROM value
               ) THEN
                RAISE EXCEPTION
                    'canonical production grant IDs must be nonempty, sorted, unique UUIDs';
            END IF;

            SELECT count(*) INTO valid_grant_count
            FROM public.permission_grants grant_row
            WHERE grant_row.id = ANY(grant_ids)
              AND grant_row.workspace_id = NEW.workspace_id
              AND grant_row.principal_type = 'identity'
              AND grant_row.principal_id = NEW.subject_id::text
              AND grant_row.effect = 'allow'
              AND (grant_row.expires_at IS NULL
                   OR grant_row.expires_at > clock_timestamp());
            IF valid_grant_count <> grant_count THEN
                RAISE EXCEPTION
                    'production intent enrollment grants must be current and bound to its subject';
            END IF;

            SELECT scope.revision, scope.stopped
            INTO current_workspace_stop, workspace_stopped
            FROM public.v4_stop_scopes scope
            WHERE scope.workspace_id = NEW.workspace_id
              AND scope.scope_key = 'workspace';
            IF NOT FOUND
               OR current_workspace_stop IS DISTINCT FROM NEW.workspace_stop_revision
               OR workspace_stopped IS DISTINCT FROM false THEN
                RAISE EXCEPTION
                    'production intent enrollment workspace stop revision is stale or stopped';
            END IF;

            SELECT scope.revision, scope.stopped
            INTO current_goal_stop, goal_stopped
            FROM public.v4_stop_scopes scope
            WHERE scope.workspace_id = NEW.workspace_id
              AND scope.scope_key = 'goal:' || NEW.goal_id::text;
            IF NOT FOUND
               OR current_goal_stop IS DISTINCT FROM NEW.goal_stop_revision
               OR goal_stopped IS DISTINCT FROM false THEN
                RAISE EXCEPTION
                    'production intent enrollment goal stop revision is stale or stopped';
            END IF;

            IF NEW.created_at > clock_timestamp() THEN
                RAISE EXCEPTION 'production intent enrollment creation time is invalid';
            END IF;
            RETURN NEW;
        END
        $$"""
    )
    op.execute(
        """CREATE TRIGGER validate_v4_production_intent_enrollment_v1
           BEFORE INSERT ON public.v4_production_intent_enrollments
           FOR EACH ROW
           EXECUTE FUNCTION public.v4_validate_production_intent_enrollment_v1()"""
    )

    op.execute(
        """CREATE FUNCTION public.v4_production_recovery_publication_snapshot_v1(
            p_operation_id uuid
        ) RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, public AS $$
        DECLARE
            validation_binding record;
            snapshot_result jsonb;
        BEGIN
            SELECT dispatch.operation_id, dispatch.generation,
                   dispatch.current_permit_id, dispatch.current_claim_id,
                   permit.request_snapshot,
                   permit.workspace_id, permit.goal_id, permit.intent_id,
                   permit.cycle_id, permit.context_id, permit.program_id,
                   intent.eligibility_revision
            INTO validation_binding
            FROM public.v4_production_current_dispatch dispatch
            JOIN public.v4_production_effect_permits permit
              ON permit.permit_id = dispatch.current_permit_id
             AND permit.operation_id = dispatch.operation_id
             AND permit.generation = dispatch.generation
            JOIN public.v4_cycle_intents intent
              ON intent.id = permit.intent_id
            WHERE dispatch.operation_id = p_operation_id;
            IF NOT FOUND THEN
                RETURN NULL;
            END IF;

            IF validation_binding.request_snapshot ? 'job_id' THEN
                IF jsonb_typeof(validation_binding.request_snapshot->'job_id')
                       IS DISTINCT FROM 'string'
                   OR validation_binding.request_snapshot->>'job_id'
                       !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN
                    RAISE EXCEPTION
                        'malformed production snapshot job_id';
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM public.jobs job_row
                    WHERE job_row.id =
                              (validation_binding.request_snapshot->>'job_id')::uuid
                      AND job_row.workspace_id = validation_binding.workspace_id
                ) THEN
                    RAISE EXCEPTION
                        'production snapshot job_id is unresolved in its workspace';
                END IF;
            END IF;

            IF validation_binding.request_snapshot ? 'publication_request_id' THEN
                IF jsonb_typeof(
                       validation_binding.request_snapshot->'publication_request_id'
                   ) IS DISTINCT FROM 'string'
                   OR validation_binding.request_snapshot->>'publication_request_id'
                       !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN
                    RAISE EXCEPTION
                        'malformed production snapshot publication_request_id';
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM public.publication_requests request_row
                    WHERE request_row.id =
                              (validation_binding.request_snapshot->>'publication_request_id')::uuid
                      AND request_row.workspace_id = validation_binding.workspace_id
                      AND request_row.content_program_id = validation_binding.program_id
                ) THEN
                    RAISE EXCEPTION
                        'production snapshot publication_request_id is unresolved in its workspace and program';
                END IF;
            END IF;

            IF validation_binding.request_snapshot ? 'publication_plan_id' THEN
                IF jsonb_typeof(
                       validation_binding.request_snapshot->'publication_plan_id'
                   ) IS DISTINCT FROM 'string'
                   OR validation_binding.request_snapshot->>'publication_plan_id'
                       !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN
                    RAISE EXCEPTION
                        'malformed production snapshot publication_plan_id';
                END IF;
                IF NOT (validation_binding.request_snapshot ? 'publication_request_id')
                   OR NOT EXISTS (
                       SELECT 1
                       FROM public.publication_plans plan_row
                       JOIN public.publication_requests request_row
                         ON request_row.id = plan_row.publication_request_id
                       WHERE plan_row.id =
                                 (validation_binding.request_snapshot->>'publication_plan_id')::uuid
                         AND request_row.id =
                                 (validation_binding.request_snapshot->>'publication_request_id')::uuid
                         AND request_row.workspace_id = validation_binding.workspace_id
                         AND request_row.content_program_id = validation_binding.program_id
                   ) THEN
                    RAISE EXCEPTION
                        'production snapshot publication_plan_id is unresolved for its publication_request_id';
                END IF;
            END IF;

            IF validation_binding.request_snapshot ? 'job_id'
               AND validation_binding.request_snapshot ? 'publication_plan_id'
               AND NOT EXISTS (
                   SELECT 1
                   FROM public.publication_plans plan_row
                   JOIN public.external_effects effect_row
                     ON effect_row.id = plan_row.external_effect_id
                   WHERE plan_row.id =
                             (validation_binding.request_snapshot->>'publication_plan_id')::uuid
                     AND effect_row.job_id =
                             (validation_binding.request_snapshot->>'job_id')::uuid
                     AND effect_row.workspace_id = validation_binding.workspace_id
               ) THEN
                RAISE EXCEPTION
                    'production snapshot job_id does not match its publication plan external effect';
            END IF;

            IF validation_binding.request_snapshot ? 'publication_attempt_id' THEN
                IF jsonb_typeof(
                       validation_binding.request_snapshot->'publication_attempt_id'
                   ) IS DISTINCT FROM 'string'
                   OR validation_binding.request_snapshot->>'publication_attempt_id'
                       !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN
                    RAISE EXCEPTION
                        'malformed production snapshot publication_attempt_id';
                END IF;
                IF NOT (validation_binding.request_snapshot ? 'publication_plan_id')
                   OR NOT (validation_binding.request_snapshot ? 'publication_request_id')
                   OR NOT EXISTS (
                       SELECT 1
                       FROM public.publication_attempts attempt_row
                       JOIN public.publication_plans plan_row
                         ON plan_row.id = attempt_row.publication_plan_id
                       JOIN public.publication_requests request_row
                         ON request_row.id = plan_row.publication_request_id
                       WHERE attempt_row.id =
                                 (validation_binding.request_snapshot->>'publication_attempt_id')::uuid
                         AND plan_row.id =
                                 (validation_binding.request_snapshot->>'publication_plan_id')::uuid
                         AND request_row.id =
                                 (validation_binding.request_snapshot->>'publication_request_id')::uuid
                         AND request_row.workspace_id = validation_binding.workspace_id
                         AND request_row.content_program_id = validation_binding.program_id
                   ) THEN
                    RAISE EXCEPTION
                        'production snapshot publication_attempt_id is unresolved for its publication_plan_id';
                END IF;
            END IF;

            WITH binding AS (
                SELECT dispatch.operation_id, dispatch.generation,
                       dispatch.current_permit_id, dispatch.current_claim_id,
                       permit.request_snapshot,
                       permit.workspace_id, permit.goal_id, permit.intent_id,
                       permit.cycle_id, permit.context_id,
                       intent.eligibility_revision
                FROM public.v4_production_current_dispatch dispatch
                JOIN public.v4_production_effect_permits permit
                  ON permit.permit_id = dispatch.current_permit_id
                 AND permit.operation_id = dispatch.operation_id
                 AND permit.generation = dispatch.generation
                JOIN public.v4_cycle_intents intent
                  ON intent.id = permit.intent_id
                WHERE dispatch.operation_id = p_operation_id
            )
        SELECT jsonb_build_object(
            'production_intent_enrollment', to_jsonb(enrollment),
            'goal', to_jsonb(goal),
            'intent', to_jsonb(intent),
            'cycle', to_jsonb(cycle),
            'context', to_jsonb(context),
            'job', COALESCE(to_jsonb(job_row), 'null'::jsonb),
            'production_current_dispatch', to_jsonb(dispatch),
            'production_permits', COALESCE((
                SELECT jsonb_agg(to_jsonb(permit_row)
                                 ORDER BY permit_row.generation, permit_row.permit_id)
                FROM public.v4_production_effect_permits permit_row
                WHERE permit_row.operation_id = binding.operation_id
            ), '[]'::jsonb),
            'production_permit_claims', COALESCE((
                SELECT jsonb_agg(to_jsonb(claim_row)
                                 ORDER BY claim_row.claimed_at, claim_row.claim_id)
                FROM public.v4_production_effect_permit_claims claim_row
                WHERE claim_row.operation_id = binding.operation_id
            ), '[]'::jsonb),
            'publication_request', COALESCE(to_jsonb(publication_request), 'null'::jsonb),
            'publication_plan', COALESCE(to_jsonb(publication_plan), 'null'::jsonb),
            'publication_attempt', COALESCE(to_jsonb(publication_attempt), 'null'::jsonb),
            'external_effect', COALESCE(to_jsonb(external_effect), 'null'::jsonb),
            'budget_reservation', COALESCE(to_jsonb(budget_reservation), 'null'::jsonb),
            'output', COALESCE(to_jsonb(job_row.output_payload), 'null'::jsonb),
            'remote_publication_receipts', COALESCE((
                SELECT jsonb_agg(to_jsonb(receipt_row)
                                 ORDER BY receipt_row.created_at, receipt_row.id)
                FROM public.remote_publication_receipts receipt_row
                WHERE receipt_row.publication_attempt_id = publication_attempt.id
            ), '[]'::jsonb),
            'publication_status_events', COALESCE((
                SELECT jsonb_agg(to_jsonb(status_event)
                                 ORDER BY status_event.sequence_no, status_event.id)
                FROM public.publication_status_events status_event
                WHERE status_event.publication_attempt_id = publication_attempt.id
            ), '[]'::jsonb),
            'cycle_events', COALESCE((
                SELECT jsonb_agg(to_jsonb(cycle_event)
                                 ORDER BY cycle_event.created_at, cycle_event.id)
                FROM public.v4_cycle_events cycle_event
                WHERE cycle_event.cycle_id = cycle.id
            ), '[]'::jsonb),
            'cycle_outbox', COALESCE((
                SELECT jsonb_agg(to_jsonb(outbox)
                                 ORDER BY outbox.sequence, outbox.id)
                FROM public.v4_cycle_outbox outbox
                WHERE outbox.cycle_id = cycle.id
            ), '[]'::jsonb),
            'job_checkpoints', COALESCE((
                SELECT jsonb_agg(to_jsonb(checkpoint)
                                 ORDER BY checkpoint.sequence_no, checkpoint.id)
                FROM public.job_checkpoints checkpoint
                WHERE checkpoint.job_id = job_row.id
            ), '[]'::jsonb),
            'audit_events', COALESCE((
                SELECT jsonb_agg(to_jsonb(audit_event)
                                 ORDER BY audit_event.created_at,
                                          audit_event.sequence_no, audit_event.id)
                FROM public.audit_events audit_event
                WHERE audit_event.job_id = job_row.id
            ), '[]'::jsonb),
            'provenance_records', COALESCE((
                SELECT jsonb_agg(to_jsonb(provenance)
                                 ORDER BY provenance.created_at, provenance.id)
                FROM public.provenance_records provenance
                WHERE provenance.job_id = job_row.id
            ), '[]'::jsonb)
        ) INTO snapshot_result
        FROM binding
        JOIN public.v4_production_current_dispatch dispatch
          ON dispatch.operation_id = binding.operation_id
        JOIN public.v4_production_intent_enrollments enrollment
          ON enrollment.intent_id = binding.intent_id
         AND enrollment.eligibility_revision = binding.eligibility_revision
         AND enrollment.workspace_id = binding.workspace_id
         AND enrollment.goal_id = binding.goal_id
        JOIN public.v4_goals goal ON goal.id = binding.goal_id
        JOIN public.v4_cycle_intents intent ON intent.id = binding.intent_id
        JOIN public.v4_cycles cycle ON cycle.id = binding.cycle_id
        JOIN public.v4_run_contexts context ON context.id = binding.context_id
        LEFT JOIN public.jobs job_row
          ON job_row.id = CASE
               WHEN binding.request_snapshot->>'job_id'
                    ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
               THEN (binding.request_snapshot->>'job_id')::uuid END
         AND job_row.workspace_id = binding.workspace_id
        LEFT JOIN public.publication_requests publication_request
          ON publication_request.id = CASE
               WHEN binding.request_snapshot->>'publication_request_id'
                    ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
               THEN (binding.request_snapshot->>'publication_request_id')::uuid END
         AND publication_request.workspace_id = binding.workspace_id
         AND publication_request.content_program_id = validation_binding.program_id
        LEFT JOIN LATERAL (
            SELECT plan_row.*
            FROM public.publication_plans plan_row
            WHERE plan_row.publication_request_id = publication_request.id
              AND (binding.request_snapshot->>'publication_plan_id' IS NULL
                   OR plan_row.id = CASE
                       WHEN binding.request_snapshot->>'publication_plan_id'
                            ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                       THEN (binding.request_snapshot->>'publication_plan_id')::uuid
                   END)
            ORDER BY plan_row.version DESC, plan_row.id
            LIMIT 1
        ) publication_plan ON true
        LEFT JOIN LATERAL (
            SELECT attempt_row.*
            FROM public.publication_attempts attempt_row
            WHERE attempt_row.publication_plan_id = publication_plan.id
              AND (binding.request_snapshot->>'publication_attempt_id' IS NULL
                   OR attempt_row.id = CASE
                       WHEN binding.request_snapshot->>'publication_attempt_id'
                            ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                       THEN (binding.request_snapshot->>'publication_attempt_id')::uuid
                   END)
            ORDER BY attempt_row.attempt_number DESC, attempt_row.id
            LIMIT 1
        ) publication_attempt ON true
        LEFT JOIN public.external_effects external_effect
          ON external_effect.id = publication_plan.external_effect_id
        LEFT JOIN public.budget_reservations budget_reservation
          ON budget_reservation.id = publication_plan.budget_reservation_id
        WHERE binding.operation_id = p_operation_id
            ;
            RETURN snapshot_result;
        END
        $$"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.v4_production_recovery_publication_snapshot_v1(uuid) FROM PUBLIC"
    )

    op.execute(
        f"""CREATE TABLE public.v4_production_recovery_work (
            work_id uuid PRIMARY KEY,
            source_discriminant text NOT NULL
                CHECK (source_discriminant IN (
                    'cycle_case', 'production_publication_hold',
                    'pre_admission_intent_case')),
            source_id uuid NOT NULL,
            workspace_id uuid NOT NULL
                REFERENCES public.workspaces(id) ON DELETE RESTRICT,
            goal_id uuid NOT NULL REFERENCES public.v4_goals(id) ON DELETE RESTRICT,
            intent_id uuid NOT NULL
                REFERENCES public.v4_cycle_intents(id) ON DELETE RESTRICT,
            cycle_id uuid REFERENCES public.v4_cycles(id) ON DELETE RESTRICT,
            context_id uuid REFERENCES public.v4_run_contexts(id) ON DELETE RESTRICT,
            operation_id uuid
                REFERENCES public.v4_cycles(operation_id) ON DELETE RESTRICT,
            source_revision integer NOT NULL CHECK (source_revision > 0),
            case_revision integer CHECK (case_revision > 0),
            eligibility_revision integer NOT NULL CHECK (eligibility_revision > 0),
            owner_id uuid NOT NULL
                REFERENCES public.identity_subjects(id) ON DELETE RESTRICT,
            target_alias text CHECK (target_alias IS NULL
                                     OR length(target_alias) BETWEEN 1 AND 256),
            remote_account_id text CHECK (remote_account_id IS NULL
                                          OR length(remote_account_id) BETWEEN 1 AND 512),
            remote_resource_id text CHECK (remote_resource_id IS NULL
                                           OR length(remote_resource_id) BETWEEN 1 AND 512),
            reason text NOT NULL,
            state text NOT NULL,
            request_fingerprint text NOT NULL
                CHECK (request_fingerprint ~ '^[0-9a-f]{{64}}$'),
            source_fingerprint text NOT NULL
                CHECK (source_fingerprint ~ '^[0-9a-f]{{64}}$'),
            snapshot_version text,
            snapshot jsonb,
            snapshot_sha256 text,
            deadline timestamptz NOT NULL,
            due_at timestamptz,
            expires_at timestamptz,
            deadline_revision integer NOT NULL CHECK (deadline_revision > 0),
            recipient_profile_revision integer NOT NULL
                CHECK (recipient_profile_revision > 0),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            CONSTRAINT uq_v4_production_recovery_work_source_revision
                UNIQUE (source_discriminant, source_id, source_revision),
            CONSTRAINT fk_v4_production_recovery_work_enrollment
                FOREIGN KEY (intent_id, eligibility_revision, workspace_id, goal_id)
                REFERENCES public.v4_production_intent_enrollments
                    (intent_id, eligibility_revision, workspace_id, goal_id)
                ON DELETE RESTRICT,
            CONSTRAINT ck_v4_production_recovery_work_source_contract
                CHECK (
                    (source_discriminant = 'cycle_case'
                     AND case_revision IS NOT NULL
                     AND source_revision = case_revision
                     AND cycle_id IS NOT NULL AND context_id IS NOT NULL
                     AND operation_id IS NOT NULL
                     AND target_alias IS NOT NULL
                     AND remote_account_id IS NOT NULL
                     AND reason IN ('policy_denial','technical_failure',
                                    'quality_failure','unknown_effect',
                                    'admission_review','deadline','manual_review')
                     AND state IN ('retry_due','reconciling','rework_due',
                                   'awaiting_review','suspended')
                     AND snapshot_version IS NULL AND snapshot IS NULL
                     AND snapshot_sha256 IS NULL
                     AND due_at IS NULL AND expires_at IS NULL)
                    OR
                    (source_discriminant = 'production_publication_hold'
                     AND source_id = operation_id
                     AND case_revision IS NULL
                     AND cycle_id IS NOT NULL AND context_id IS NOT NULL
                     AND operation_id IS NOT NULL
                     AND target_alias IS NOT NULL
                     AND remote_account_id IS NOT NULL
                     AND reason IN ('expired_permit_receipt',
                                    'published_during_cancel')
                     AND state = 'held'
                     AND snapshot_version =
                         'salience.p1-production-publication-liability.v1'
                     AND snapshot IS NOT NULL AND snapshot_sha256 IS NOT NULL
                     AND due_at IS NULL AND expires_at IS NULL)
                    OR
                    (source_discriminant = 'pre_admission_intent_case'
                     AND source_id = intent_id
                     AND source_revision = eligibility_revision
                     AND case_revision IS NULL
                     AND cycle_id IS NULL AND context_id IS NULL
                     AND operation_id IS NULL
                     AND target_alias IS NULL
                     AND remote_account_id IS NULL
                     AND remote_resource_id IS NULL
                     AND reason IN ('admission_deferred',
                                    'admission_review_required')
                     AND state = 'unadmitted'
                     AND snapshot_version IS NULL AND snapshot IS NULL
                     AND snapshot_sha256 IS NULL
                     AND due_at IS NOT NULL AND expires_at IS NOT NULL)
                ),
            CONSTRAINT ck_v4_production_recovery_work_snapshot_hash
                CHECK (snapshot_sha256 IS NULL
                       OR snapshot_sha256 ~ '^[0-9a-f]{{64}}$'),
            CONSTRAINT ck_v4_production_recovery_work_exactly_the_22_production_liability_snapshot_keys
                CHECK (snapshot IS NULL OR (
                    jsonb_typeof(snapshot) = 'object'
                    AND snapshot ?& {_RECOVERY_SNAPSHOT_KEYS}
                    AND (snapshot - {_RECOVERY_SNAPSHOT_KEYS}) = '{{}}'::jsonb
                ))
        )"""
    )
    op.execute(
        """CREATE INDEX ix_v4_production_recovery_work_workspace_state
           ON public.v4_production_recovery_work(workspace_id, state, deadline)"""
    )
    op.execute(
        """CREATE INDEX ix_v4_production_recovery_work_operation
           ON public.v4_production_recovery_work(operation_id)
           WHERE operation_id IS NOT NULL"""
    )
    op.execute(
        """CREATE UNIQUE INDEX uq_v4_production_recovery_work_publication_hold_operation
           ON public.v4_production_recovery_work(operation_id)
           WHERE source_discriminant = 'production_publication_hold'"""
    )

    op.execute(
        f"""CREATE FUNCTION public.v4_validate_production_recovery_work_v1()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public AS $$
        DECLARE
            enrollment_row public.v4_production_intent_enrollments%ROWTYPE;
            intent_row public.v4_cycle_intents%ROWTYPE;
            case_row public.v4_recovery_cases%ROWTYPE;
            cycle_row public.v4_cycles%ROWTYPE;
            dispatch_row public.v4_production_current_dispatch%ROWTYPE;
            permit_row public.v4_production_effect_permits%ROWTYPE;
            admission_disposition text;
            expected_snapshot jsonb;
            source_material jsonb;
            expected_fingerprint text;
        BEGIN
            IF TG_RELID <> 'public.v4_production_recovery_work'::regclass
               OR TG_TABLE_SCHEMA <> 'public'
               OR TG_TABLE_NAME <> 'v4_production_recovery_work'
               OR TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION
                    'production recovery work guard only accepts its target insert';
            END IF;

            SELECT * INTO enrollment_row
            FROM public.v4_production_intent_enrollments enrollment
            WHERE enrollment.intent_id = NEW.intent_id
              AND enrollment.eligibility_revision = NEW.eligibility_revision
              AND enrollment.workspace_id = NEW.workspace_id
              AND enrollment.goal_id = NEW.goal_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'production recovery work requires the exact production intent enrollment';
            END IF;
            SELECT * INTO intent_row FROM public.v4_cycle_intents intent
            WHERE intent.id = NEW.intent_id AND intent.goal_id = NEW.goal_id
              AND intent.eligibility_revision = NEW.eligibility_revision;
            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'production recovery work does not match the current intent eligibility';
            END IF;

            IF NEW.target_alias IS NOT NULL
               AND (NEW.target_alias ~* '^(fixture|mock|noop)([-.:]|$)'
                    OR NEW.target_alias = 'fixture-account') THEN
                RAISE EXCEPTION
                    'fixture target identities cannot be adopted as production recovery work';
            END IF;
            IF NEW.remote_account_id = 'fixture-account'
               OR NEW.remote_resource_id LIKE 'fixture-v4-publication:%' THEN
                RAISE EXCEPTION
                    'fixture remote identities cannot be adopted as production recovery work';
            END IF;

            IF NEW.source_discriminant = 'cycle_case' THEN
                IF NEW.source_id IS NULL OR NEW.case_revision IS NULL
                   OR NEW.source_revision <> NEW.case_revision
                   OR NEW.source_id = NEW.intent_id THEN
                    RAISE EXCEPTION
                        'cycle recovery source_id and source revision must match case_id and case revision';
                END IF;
                SELECT * INTO case_row
                FROM public.v4_recovery_cases recovery
                WHERE recovery.id = NEW.source_id;
                IF NOT FOUND OR case_row.revision <> NEW.case_revision
                   OR case_row.state <> NEW.state
                   OR case_row.failure_class <> NEW.reason
                   OR case_row.workspace_id <> NEW.workspace_id
                   OR case_row.goal_id <> NEW.goal_id
                   OR case_row.owner_id <> NEW.owner_id
                   OR case_row.deadline <> NEW.deadline
                   OR case_row.state NOT IN (
                       'retry_due','reconciling','rework_due',
                       'awaiting_review','suspended') THEN
                    RAISE EXCEPTION
                        'production recovery cycle case does not match its exact current source';
                END IF;
                SELECT * INTO cycle_row FROM public.v4_cycles cycle
                WHERE cycle.id = NEW.cycle_id
                  AND cycle.intent_id = NEW.intent_id
                  AND cycle.context_id = NEW.context_id
                  AND cycle.operation_id = NEW.operation_id;
                IF NOT FOUND OR cycle_row.state = 'closed'
                   OR case_row.target_cycle_id IS DISTINCT FROM cycle_row.id
                   OR case_row.context_id IS DISTINCT FROM cycle_row.context_id
                   OR case_row.operation_id IS DISTINCT FROM cycle_row.operation_id
                   OR case_row.target_intent_id IS NOT NULL THEN
                    RAISE EXCEPTION
                        'production recovery cycle case source identity or operation changed';
                END IF;
                SELECT dispatch.* INTO dispatch_row
                FROM public.v4_production_current_dispatch dispatch
                WHERE dispatch.operation_id = NEW.operation_id
                  AND EXISTS (
                      SELECT 1 FROM public.v4_production_effect_permits permit
                      WHERE permit.permit_id = dispatch.current_permit_id
                        AND permit.operation_id = dispatch.operation_id
                        AND permit.generation = dispatch.generation
                        AND permit.intent_id = NEW.intent_id
                        AND permit.cycle_id = NEW.cycle_id
                        AND permit.context_id = NEW.context_id
                  );
                IF NOT FOUND THEN
                    RAISE EXCEPTION
                        'production recovery cycle case requires matching production dispatch authority';
                END IF;
                SELECT permit.* INTO permit_row
                FROM public.v4_production_effect_permits permit
                WHERE permit.permit_id = dispatch_row.current_permit_id
                  AND permit.operation_id = dispatch_row.operation_id
                  AND permit.generation = dispatch_row.generation;
                IF NOT FOUND THEN
                    RAISE EXCEPTION
                        'production recovery cycle case requires matching production permit authority';
                END IF;
                IF NEW.target_alias IS DISTINCT FROM permit_row.provider_id
                   OR NEW.remote_account_id IS DISTINCT FROM permit_row.account_id
                   OR NEW.request_fingerprint IS DISTINCT FROM
                      encode(public.digest(permit_row.request_snapshot::text,'sha256'),'hex') THEN
                    RAISE EXCEPTION
                        'production recovery cycle case target or request fingerprint changed';
                END IF;
                expected_snapshot :=
                    public.v4_production_recovery_publication_snapshot_v1(NEW.operation_id);
                IF expected_snapshot IS NULL
                   OR (NEW.remote_resource_id IS NOT NULL AND NOT EXISTS (
                       SELECT 1
                       FROM jsonb_array_elements(
                           expected_snapshot->'remote_publication_receipts'
                       ) receipt
                       WHERE receipt->>'remote_id' = NEW.remote_resource_id
                   )) THEN
                    RAISE EXCEPTION
                        'production recovery cycle case remote resource identity must match persisted production receipt evidence';
                END IF;
                source_material := jsonb_build_object(
                    'source_discriminant', NEW.source_discriminant,
                    'source_id', NEW.source_id,
                    'enrollment', to_jsonb(enrollment_row),
                    'case', to_jsonb(case_row),
                    'cycle', to_jsonb(cycle_row),
                    'context_id', NEW.context_id,
                    'operation_id', NEW.operation_id,
                    'target_alias', NEW.target_alias,
                    'remote_account_id', NEW.remote_account_id,
                    'remote_resource_id', NEW.remote_resource_id
                );
            ELSIF NEW.source_discriminant = 'pre_admission_intent_case' THEN
                IF NEW.source_id IS DISTINCT FROM NEW.intent_id
                   OR NEW.source_revision <> NEW.eligibility_revision
                   OR NEW.case_revision IS NOT NULL
                   OR NEW.cycle_id IS NOT NULL OR NEW.context_id IS NOT NULL
                   OR NEW.operation_id IS NOT NULL OR NEW.target_alias IS NOT NULL
                   OR NEW.remote_account_id IS NOT NULL
                   OR NEW.remote_resource_id IS NOT NULL
                   OR NEW.state <> 'unadmitted'
                   OR NEW.snapshot IS NOT NULL
                   OR NEW.snapshot_version IS NOT NULL
                   OR NEW.snapshot_sha256 IS NOT NULL
                   OR NEW.due_at IS DISTINCT FROM intent_row.due_at
                   OR NEW.expires_at IS DISTINCT FROM intent_row.expires_at
                   OR NEW.deadline IS DISTINCT FROM intent_row.expires_at
                   OR EXISTS (SELECT 1 FROM public.v4_cycles cycle
                              WHERE cycle.intent_id = NEW.intent_id) THEN
                    RAISE EXCEPTION
                        'pre-admission recovery source must preserve its operationless intent identity';
                END IF;
                SELECT admission.disposition INTO admission_disposition
                FROM public.v4_admissions admission
                WHERE admission.intent_id = NEW.intent_id
                  AND admission.eligibility_revision = NEW.eligibility_revision
                  AND admission.cycle_id IS NULL;
                IF NOT FOUND
                   OR (NEW.reason = 'admission_deferred'
                       AND admission_disposition <> 'deferred')
                   OR (NEW.reason = 'admission_review_required'
                       AND admission_disposition <> 'review_required') THEN
                    RAISE EXCEPTION
                        'pre-admission recovery reason must match the canonical admission disposition';
                END IF;
                IF NEW.request_fingerprint IS DISTINCT FROM intent_row.fingerprint
                   OR NEW.owner_id IS DISTINCT FROM enrollment_row.source_owner_id THEN
                    RAISE EXCEPTION
                        'pre-admission recovery request or owner binding changed';
                END IF;
                source_material := jsonb_build_object(
                    'source_discriminant', NEW.source_discriminant,
                    'source_id', NEW.source_id,
                    'enrollment', to_jsonb(enrollment_row),
                    'intent', to_jsonb(intent_row),
                    'admission_disposition', admission_disposition,
                    'due_at', NEW.due_at,
                    'expires_at', NEW.expires_at,
                    'owner_id', NEW.owner_id
                );
            ELSIF NEW.source_discriminant = 'production_publication_hold' THEN
                IF NEW.source_id IS DISTINCT FROM NEW.operation_id
                   OR NEW.cycle_id IS NULL OR NEW.context_id IS NULL
                   OR NEW.operation_id IS NULL OR NEW.case_revision IS NOT NULL
                   OR NEW.state <> 'held'
                   OR NEW.due_at IS NOT NULL OR NEW.expires_at IS NOT NULL THEN
                    RAISE EXCEPTION
                        'production publication hold source_id must equal its operation_id and remain held';
                END IF;
                SELECT * INTO cycle_row FROM public.v4_cycles cycle
                WHERE cycle.id = NEW.cycle_id
                  AND cycle.intent_id = NEW.intent_id
                  AND cycle.context_id = NEW.context_id
                  AND cycle.operation_id = NEW.operation_id;
                IF NOT FOUND THEN
                    RAISE EXCEPTION
                        'production publication hold does not match the exact cycle operation';
                END IF;
                SELECT dispatch.* INTO dispatch_row
                FROM public.v4_production_current_dispatch dispatch
                WHERE dispatch.operation_id = NEW.operation_id
                  AND dispatch.generation = NEW.source_revision
                  AND EXISTS (
                      SELECT 1
                      FROM public.v4_production_effect_permits permit
                      JOIN public.v4_production_effect_permit_claims claim
                        ON claim.claim_id = dispatch.current_claim_id
                       AND claim.permit_id = permit.permit_id
                       AND claim.operation_id = permit.operation_id
                       AND claim.generation = permit.generation
                      WHERE permit.permit_id = dispatch.current_permit_id
                        AND permit.operation_id = dispatch.operation_id
                        AND permit.generation = dispatch.generation
                        AND permit.intent_id = NEW.intent_id
                        AND permit.cycle_id = NEW.cycle_id
                        AND permit.context_id = NEW.context_id
                  );
                IF NOT FOUND THEN
                    RAISE EXCEPTION
                        'production publication hold does not match current production dispatch authority';
                END IF;
                SELECT permit.* INTO permit_row
                FROM public.v4_production_effect_permits permit
                WHERE permit.permit_id = dispatch_row.current_permit_id
                  AND permit.operation_id = dispatch_row.operation_id
                  AND permit.generation = dispatch_row.generation;
                IF NOT FOUND
                   OR NEW.target_alias IS DISTINCT FROM permit_row.provider_id
                   OR NEW.remote_account_id IS DISTINCT FROM permit_row.account_id
                   OR NEW.owner_id IS DISTINCT FROM permit_row.subject_id
                   OR NEW.request_fingerprint IS DISTINCT FROM
                      encode(public.digest(permit_row.request_snapshot::text,'sha256'),'hex') THEN
                    RAISE EXCEPTION
                        'production publication hold does not match current production dispatch authority';
                END IF;
                IF NOT (
                    (NEW.reason = 'expired_permit_receipt'
                     AND cycle_row.state <> 'closed'
                     AND permit_row.expires_at <= clock_timestamp())
                    OR
                    (NEW.reason = 'published_during_cancel'
                     AND cycle_row.state = 'closed'
                     AND cycle_row.disposition = 'cancelled')
                ) THEN
                    RAISE EXCEPTION
                        'production publication hold reason/state pair does not match cycle evidence';
                END IF;
                expected_snapshot :=
                    public.v4_production_recovery_publication_snapshot_v1(NEW.operation_id);
                IF expected_snapshot IS NULL
                   OR NEW.snapshot IS DISTINCT FROM expected_snapshot
                   OR NEW.snapshot_version IS DISTINCT FROM
                      'salience.p1-production-publication-liability.v1'
                   OR NEW.snapshot_sha256 IS DISTINCT FROM
                      encode(public.digest(NEW.snapshot::text,'sha256'),'hex')
                   OR jsonb_typeof(NEW.snapshot) IS DISTINCT FROM 'object'
                   OR NOT (NEW.snapshot ?& {_RECOVERY_SNAPSHOT_KEYS})
                   OR (NEW.snapshot - {_RECOVERY_SNAPSHOT_KEYS}) <> '{{}}'::jsonb THEN
                    RAISE EXCEPTION
                        'production publication hold requires the exact 22 production liability snapshot keys and canonical hash';
                END IF;
                IF NEW.remote_resource_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements(
                        expected_snapshot->'remote_publication_receipts'
                    ) receipt
                    WHERE receipt->>'remote_id' = NEW.remote_resource_id
                ) THEN
                    RAISE EXCEPTION
                        'production publication hold remote resource identity must match persisted production receipt evidence';
                END IF;
                source_material := jsonb_build_object(
                    'source_discriminant', NEW.source_discriminant,
                    'source_id', NEW.source_id,
                    'enrollment', to_jsonb(enrollment_row),
                    'cycle', to_jsonb(cycle_row),
                    'production_current_dispatch', to_jsonb(dispatch_row),
                    'production_permit', to_jsonb(permit_row),
                    'reason', NEW.reason,
                    'state', NEW.state,
                    'target_alias', NEW.target_alias,
                    'remote_account_id', NEW.remote_account_id,
                    'remote_resource_id', NEW.remote_resource_id,
                    'snapshot_sha256', NEW.snapshot_sha256
                );
            ELSE
                RAISE EXCEPTION 'unrecognized production recovery source discriminant';
            END IF;

            expected_fingerprint := encode(
                public.digest(source_material::text, 'sha256'), 'hex'
            );
            NEW.source_fingerprint := expected_fingerprint;
            IF NEW.created_at > clock_timestamp() THEN
                RAISE EXCEPTION 'production recovery work creation time is invalid';
            END IF;
            RETURN NEW;
        END
        $$"""
    )
    op.execute(
        """CREATE TRIGGER validate_v4_production_recovery_work_v1
           BEFORE INSERT ON public.v4_production_recovery_work
           FOR EACH ROW
           EXECUTE FUNCTION public.v4_validate_production_recovery_work_v1()"""
    )

    op.execute(
        """CREATE TABLE public.v4_production_recovery_events (
            event_id uuid PRIMARY KEY,
            work_id uuid NOT NULL
                REFERENCES public.v4_production_recovery_work(work_id)
                ON DELETE RESTRICT,
            event_key text NOT NULL UNIQUE
                CHECK (length(event_key) BETWEEN 1 AND 256),
            event_kind text NOT NULL CHECK (event_kind IN (
                'remote_read_observation','alert_intent','delivery_attempt',
                'delivery_receipt','acknowledgment','retry_scheduled',
                'escalation','owner_assignment','owner_unavailable',
                'stop_revocation_fence','held_conflict',
                'same_operation_no_effect_reissue')),
            occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            actor_id uuid REFERENCES public.identity_subjects(id) ON DELETE RESTRICT,
            route_id uuid,
            slot_id uuid,
            fencing_epoch bigint,
            policy_decision_id uuid NOT NULL,
            payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
            payload_sha256 text NOT NULL
                CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            CONSTRAINT ck_v4_production_recovery_events_actor_or_route
                CHECK (actor_id IS NOT NULL OR route_id IS NOT NULL),
            CONSTRAINT ck_v4_production_recovery_events_route_tuple
                CHECK ((route_id IS NULL AND slot_id IS NULL AND fencing_epoch IS NULL)
                    OR (route_id IS NOT NULL AND slot_id IS NOT NULL
                        AND fencing_epoch > 0))
        )"""
    )
    op.execute(
        """CREATE INDEX ix_v4_production_recovery_events_work_time
           ON public.v4_production_recovery_events(work_id, occurred_at, event_id)"""
    )
    op.execute(
        """CREATE FUNCTION public.v4_validate_production_recovery_event_v1()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public AS $$
        DECLARE
            work_row public.v4_production_recovery_work%ROWTYPE;
            enrollment_row public.v4_production_intent_enrollments%ROWTYPE;
            event_key_name text;
            actor_workspace_id uuid;
        BEGIN
            IF TG_RELID <> 'public.v4_production_recovery_events'::regclass
               OR TG_TABLE_SCHEMA <> 'public'
               OR TG_TABLE_NAME <> 'v4_production_recovery_events'
               OR TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION
                    'production recovery event guard only accepts its target insert';
            END IF;
            SELECT * INTO work_row
            FROM public.v4_production_recovery_work work
            WHERE work.work_id = NEW.work_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'production recovery event does not belong to the matching production recovery work';
            END IF;
            SELECT * INTO enrollment_row
            FROM public.v4_production_intent_enrollments enrollment
            WHERE enrollment.intent_id = work_row.intent_id
              AND enrollment.eligibility_revision = work_row.eligibility_revision
              AND enrollment.workspace_id = work_row.workspace_id
              AND enrollment.goal_id = work_row.goal_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'production recovery event work is missing its exact enrollment';
            END IF;
            IF NEW.actor_id IS NOT NULL THEN
                SELECT subject.workspace_id INTO actor_workspace_id
                FROM public.identity_subjects subject
                WHERE subject.id = NEW.actor_id;
                IF NOT FOUND OR actor_workspace_id IS DISTINCT FROM work_row.workspace_id THEN
                    RAISE EXCEPTION
                        'production recovery event actor must belong to its work workspace';
                END IF;
            END IF;
            IF jsonb_typeof(NEW.payload) IS DISTINCT FROM 'object'
               OR octet_length(NEW.payload::text) > 16384 THEN
                RAISE EXCEPTION
                    'production recovery event payload must be a bounded object';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM jsonb_each(NEW.payload) payload_entry
                WHERE jsonb_typeof(payload_entry.value) IN ('object','array')
            ) THEN
                RAISE EXCEPTION
                    'production recovery event payload values must be bounded JSON scalars';
            END IF;
            FOR event_key_name IN SELECT jsonb_object_keys(NEW.payload) LOOP
                IF event_key_name NOT IN (
                    'schema_version','reason_code','evidence_sha256',
                    'request_fingerprint','remote_identity_sha256','remote_state',
                    'operation_id','case_id','intent_id','work_id','source_id',
                    'state','reason','status','result_class','attempt',
                    'attempt_number','severity','escalation_level','deadline',
                    'target_alias','inspection_route','recipient_profile_revision',
                    'policy_revision','stop_revision','route_id','slot_id',
                    'fencing_epoch','provider_code','http_status','retry_at',
                    'error_code','owner_id','assigned_owner_id','acknowledged_by',
                    'delivery_id','event_key'
                ) THEN
                    RAISE EXCEPTION
                        'unsafe recovery event payload key: %', event_key_name;
                END IF;
            END LOOP;
            IF (NEW.payload ? 'operation_id'
                AND NEW.payload->>'operation_id'
                    IS DISTINCT FROM work_row.operation_id::text)
               OR (NEW.payload ? 'intent_id'
                   AND NEW.payload->>'intent_id'
                       IS DISTINCT FROM work_row.intent_id::text)
               OR (NEW.payload ? 'work_id'
                   AND NEW.payload->>'work_id'
                       IS DISTINCT FROM work_row.work_id::text)
               OR (NEW.payload ? 'source_id'
                   AND NEW.payload->>'source_id'
                       IS DISTINCT FROM work_row.source_id::text)
               OR (NEW.payload ? 'request_fingerprint'
                   AND NEW.payload->>'request_fingerprint'
                       IS DISTINCT FROM work_row.request_fingerprint)
               OR (NEW.payload ? 'target_alias'
                   AND NEW.payload->>'target_alias'
                       IS DISTINCT FROM work_row.target_alias)
               OR (NEW.payload ? 'owner_id'
                   AND NEW.payload->>'owner_id'
                       IS DISTINCT FROM work_row.owner_id::text)
               OR (NEW.payload ? 'route_id'
                   AND NEW.payload->>'route_id'
                       IS DISTINCT FROM enrollment_row.route_id::text)
               OR (NEW.payload ? 'slot_id'
                   AND NEW.payload->>'slot_id'
                       IS DISTINCT FROM enrollment_row.slot_id::text)
               OR (NEW.payload ? 'fencing_epoch'
                   AND NEW.payload->>'fencing_epoch'
                       IS DISTINCT FROM enrollment_row.fencing_epoch::text) THEN
                RAISE EXCEPTION
                    'production recovery event payload identity or route does not match its work scope';
            END IF;
            IF NEW.payload ? 'case_id'
               AND (work_row.source_discriminant <> 'cycle_case'
                    OR NEW.payload->>'case_id'
                       IS DISTINCT FROM work_row.source_id::text) THEN
                RAISE EXCEPTION
                    'production recovery event cycle case_id must match its work source';
            END IF;
            IF NEW.route_id IS NOT NULL
               AND (NEW.route_id IS DISTINCT FROM enrollment_row.route_id
                    OR NEW.slot_id IS DISTINCT FROM enrollment_row.slot_id
                    OR NEW.fencing_epoch IS DISTINCT FROM enrollment_row.fencing_epoch) THEN
                RAISE EXCEPTION
                    'production recovery event route, slot, and epoch do not match its enrollment';
            END IF;
            IF NEW.payload_sha256 IS DISTINCT FROM
               encode(public.digest(NEW.payload::text,'sha256'),'hex') THEN
                RAISE EXCEPTION
                    'production recovery event payload SHA-256 does not match payload';
            END IF;
            IF NEW.occurred_at > clock_timestamp()
               OR NEW.created_at > clock_timestamp() THEN
                RAISE EXCEPTION 'production recovery event timestamp cannot be in the future';
            END IF;
            RETURN NEW;
        END
        $$"""
    )
    op.execute(
        """CREATE TRIGGER validate_v4_production_recovery_event_v1
           BEFORE INSERT ON public.v4_production_recovery_events
           FOR EACH ROW
           EXECUTE FUNCTION public.v4_validate_production_recovery_event_v1()"""
    )

    op.execute(
        """CREATE FUNCTION public.v4_reject_production_recovery_history_change_v1()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public AS $$
        BEGIN
            IF TG_TABLE_SCHEMA <> 'public'
               OR TG_TABLE_NAME NOT IN (
                   'v4_production_intent_enrollments',
                   'v4_production_recovery_work',
                   'v4_production_recovery_events') THEN
                RAISE EXCEPTION
                    'production recovery history guard only permits its target tables';
            END IF;
            IF TG_TABLE_NAME = 'v4_production_intent_enrollments' THEN
                RAISE EXCEPTION 'production intent enrollment is immutable';
            ELSIF TG_TABLE_NAME = 'v4_production_recovery_work' THEN
                RAISE EXCEPTION 'production recovery work is immutable';
            END IF;
            RAISE EXCEPTION 'production recovery events are append-only';
        END
        $$"""
    )
    op.execute(
        """CREATE TRIGGER immutable_v4_production_intent_enrollments_v1
           BEFORE UPDATE OR DELETE ON public.v4_production_intent_enrollments
           FOR EACH ROW
           EXECUTE FUNCTION public.v4_reject_production_recovery_history_change_v1()"""
    )
    op.execute(
        """CREATE TRIGGER immutable_v4_production_recovery_work_v1
           BEFORE UPDATE OR DELETE ON public.v4_production_recovery_work
           FOR EACH ROW
           EXECUTE FUNCTION public.v4_reject_production_recovery_history_change_v1()"""
    )
    op.execute(
        """CREATE TRIGGER immutable_v4_production_recovery_events_v1
           BEFORE UPDATE OR DELETE ON public.v4_production_recovery_events
           FOR EACH ROW
           EXECUTE FUNCTION public.v4_reject_production_recovery_history_change_v1()"""
    )

    for table in (
        "v4_production_intent_enrollments",
        "v4_production_recovery_work",
        "v4_production_recovery_events",
    ):
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE public.{table} FROM PUBLIC")
    for function in (
        "v4_validate_production_intent_enrollment_v1()",
        "v4_validate_production_recovery_work_v1()",
        "v4_validate_production_recovery_event_v1()",
        "v4_reject_production_recovery_history_change_v1()",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION public.{function} FROM PUBLIC")


def downgrade() -> None:
    op.execute(
        """LOCK TABLE public.v4_production_recovery_events,
                          public.v4_production_recovery_work,
                          public.v4_production_intent_enrollments
           IN ACCESS EXCLUSIVE MODE"""
    )
    op.execute(
        """DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM public.v4_production_recovery_events)
               OR EXISTS (SELECT 1 FROM public.v4_production_recovery_work)
               OR EXISTS (SELECT 1 FROM public.v4_production_intent_enrollments) THEN
                RAISE EXCEPTION
                    'preserve production recovery enrollment and work history';
            END IF;
        END
        $$"""
    )
    op.execute("DROP TABLE public.v4_production_recovery_events")
    op.execute("DROP TABLE public.v4_production_recovery_work")
    op.execute("DROP TABLE public.v4_production_intent_enrollments")
    op.execute(
        "DROP FUNCTION public.v4_production_recovery_publication_snapshot_v1(uuid)"
    )
    op.execute("DROP FUNCTION public.v4_validate_production_recovery_event_v1()")
    op.execute("DROP FUNCTION public.v4_validate_production_recovery_work_v1()")
    op.execute("DROP FUNCTION public.v4_validate_production_intent_enrollment_v1()")
    op.execute(
        "DROP FUNCTION public.v4_reject_production_recovery_history_change_v1()"
    )
