"""Allow one same-operation successor for an expired, never-claimed publication permit."""

from alembic import op


revision = "0056_p1_preclaim_permit_resume"
down_revision = "0055_p1_recovery_open_rollback_v4"
branch_labels = None
depends_on = None

_FIND = "public.v4_find_preclaim_resume_v1(uuid,uuid,uuid,text,text)"
_RECORD = "public.v4_record_preclaim_resume_v1(uuid,uuid,uuid,text,text,uuid,uuid,jsonb)"
_ADVANCE = "public.v4_advance_preclaim_resume_dispatch_v1(uuid,uuid,uuid,text,uuid)"
_ASSERT_UNUSED = "public.v4_assert_preclaim_publication_unused_v1(jsonb,text,uuid)"
_KIND = "preclaim_permit_resume"
_EVENT_KINDS = """
    'remote_read_observation','alert_intent','delivery_attempt','delivery_receipt',
    'acknowledgment','retry_scheduled','escalation','owner_assignment','owner_unavailable',
    'stop_revocation_fence','held_conflict','same_operation_no_effect_reissue'
"""


def upgrade() -> None:
    op.execute(
        """DO $$ BEGIN
            IF (SELECT pg_get_userbyid(proowner) FROM pg_proc WHERE oid=to_regprocedure(
                'public.v4_advance_production_current_dispatch_v1(uuid,integer,uuid,uuid,text,uuid)'
            )) IS DISTINCT FROM current_user THEN
                RAISE EXCEPTION 'preclaim permit migration requires the existing dispatch function owner';
            END IF;
        END $$"""
    )
    op.execute(
        f"""ALTER TABLE public.v4_production_recovery_events
            DROP CONSTRAINT v4_production_recovery_events_event_kind_check,
            ADD CONSTRAINT v4_production_recovery_events_event_kind_check
                CHECK (event_kind IN ({_EVENT_KINDS},'{_KIND}'))"""
    )
    op.execute(
        """CREATE UNIQUE INDEX uq_v4_preclaim_resume_event_route_decision
           ON public.v4_production_recovery_events ((payload->>'route_decision_id'))
           WHERE event_kind='preclaim_permit_resume'"""
    )
    op.execute(
        """DO $$
        DECLARE definition text;
        BEGIN
            definition := pg_get_functiondef('public.v4_validate_production_recovery_event_v3()'::regprocedure);
            definition := replace(definition,
                'v4_validate_production_recovery_event_v3','v4_validate_production_recovery_event_v4');
            definition := replace(definition,
                'FOR event_key_name IN SELECT jsonb_object_keys(NEW.payload) LOOP',
                $keys$FOR event_key_name IN SELECT jsonb_object_keys(NEW.payload) LOOP
                IF NEW.event_kind='preclaim_permit_resume' AND event_key_name IN (
                    'predecessor_permit_id','successor_permit_id','approval_request_id',
                    'opening_command_id','case_revision','work_source_revision',
                    'job_id','workflow_id','workflow_run_id','publication_request_id',
                    'publication_plan_id','publication_attempt_id','external_effect_id',
                    'budget_reservation_id','predecessor_generation','command_sha256',
                    'renewal_stage_decision_id','renewal_policy_decision_id','renewal_expires_at',
                    'delivery_profile_id','delivery_profile_version','delivery_adapter_version'
                ) THEN
                    CONTINUE;
                END IF$keys$ || ';');
            IF position('predecessor_permit_id' IN definition)=0 THEN
                RAISE EXCEPTION 'failed to extend production recovery event validator';
            END IF;
            EXECUTE definition;
        END
        $$"""
    )
    op.execute(
        """DROP TRIGGER validate_v4_production_recovery_event_v3
           ON public.v4_production_recovery_events"""
    )
    op.execute(
        """CREATE TRIGGER validate_v4_production_recovery_event_v4
           BEFORE INSERT ON public.v4_production_recovery_events
           FOR EACH ROW EXECUTE FUNCTION public.v4_validate_production_recovery_event_v4()"""
    )
    op.execute(
        """CREATE FUNCTION public.v4_find_preclaim_resume_v1(
            p_operation uuid, p_predecessor uuid, p_approval uuid, p_key text, p_workflow_run text
        ) RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, public AS $$
            SELECT CASE WHEN count(*)=1 THEN jsonb_agg(match)->0 END
            FROM (
                SELECT jsonb_build_object(
                    'command_id',resume.id,'work_id',work.work_id,
                    'command_expires_at',resume.result->'authorization'->>'expires_at',
                    'proof_id',proof.event_id,'successor_permit_id',successor.permit_id
                ) AS match
                FROM public.v4_production_effect_permits prior
                JOIN public.v4_production_current_dispatch dispatch
                  ON dispatch.operation_id=prior.operation_id AND dispatch.idempotency_key=p_key
                JOIN public.v4_production_recovery_work work
                  ON work.operation_id=prior.operation_id AND work.source_discriminant='cycle_case'
                 AND (work.workspace_id,work.goal_id,work.intent_id,work.cycle_id,work.context_id)
                     = (prior.workspace_id,prior.goal_id,prior.intent_id,prior.cycle_id,prior.context_id)
                 AND work.state='awaiting_review' AND work.reason='manual_review'
                 AND work.source_revision=1 AND work.case_revision=1
                 AND work.target_alias=prior.provider_id AND work.remote_account_id=prior.account_id
                 AND work.remote_resource_id IS NULL
                 AND work.request_fingerprint=encode(public.digest(prior.request_snapshot::text,'sha256'),'hex')
                JOIN public.v4_cycles cycle ON cycle.id=work.cycle_id AND cycle.intent_id=work.intent_id
                 AND cycle.context_id=work.context_id AND cycle.operation_id=work.operation_id
                 AND cycle.state='runnable' AND cycle.closed_at IS NULL AND cycle.disposition IS NULL
                JOIN public.v4_recovery_cases recovery
                  ON recovery.id=work.source_id AND recovery.workspace_id=work.workspace_id
                 AND recovery.goal_id=work.goal_id AND recovery.target_cycle_id=work.cycle_id
                 AND recovery.target_intent_id IS NULL AND recovery.context_id=work.context_id
                 AND recovery.operation_id=work.operation_id AND recovery.owner_id=work.owner_id
                 AND recovery.kind='manual_review' AND recovery.failure_class='manual_review'
                 AND recovery.state='resolved' AND recovery.revision=3 AND recovery.action_count=2
                 AND recovery.deadline>statement_timestamp()
                JOIN public.v4_case_commands opened
                  ON opened.id=work.opening_command_id AND opened.case_id=recovery.id
                 AND opened.actor_id=work.owner_id AND opened.action='open'
                 AND opened.result @> jsonb_build_object(
                     'schema_version','salience.p1-production-preclaim-case-opening.v1',
                     'opening_kind','expired_unclaimed_permit','action','open','command_id',opened.id,
                     'work_id',work.work_id,'case_id',recovery.id,'actor_id',work.owner_id,
                     'workspace_id',work.workspace_id,'goal_id',work.goal_id,'intent_id',work.intent_id,
                     'cycle_id',work.cycle_id,'context_id',work.context_id,'operation_id',p_operation,
                     'eligibility_revision',work.eligibility_revision,'generation',1,'resulting_revision',1,
                     'request_fingerprint',work.request_fingerprint,'permit_id',p_predecessor,'claim_id',NULL,
                     'approval_request_id',p_approval,'provider_idempotency_key',p_key,
                     'state','awaiting_review','kind','manual_review','failure_class','manual_review',
                     'evidence_state','planned','workflow_run_id',p_workflow_run)
                 AND opened.result @> (
                     SELECT jsonb_object_agg(binding.key, binding.value)
                     FROM jsonb_each(prior.request_snapshot) binding
                     WHERE binding.key=ANY(ARRAY[
                         'publication_request_id','publication_plan_id','publication_attempt_id',
                         'external_effect_id','budget_reservation_id','job_id','workflow_id'])
                 )
                JOIN public.v4_case_commands review ON review.case_id=recovery.id
                 AND review.actor_id=work.owner_id AND review.action='review_approve'
                 AND review.result @> jsonb_build_object(
                     'schema_version','salience.p1-production-recovery-case-command.v1',
                     'command_id',review.id,'action','review_approve','work_id',work.work_id,
                     'case_id',recovery.id,'actor_id',work.owner_id,'workspace_id',work.workspace_id,
                     'goal_id',work.goal_id,'intent_id',work.intent_id,'cycle_id',work.cycle_id,
                     'context_id',work.context_id,'operation_id',p_operation,'work_source_revision',1,
                     'source_revision',1,'source_state','awaiting_review','resulting_revision',2,
                     'state','retry_due','approval_request_id',p_approval,
                     'request_fingerprint',work.request_fingerprint)
                JOIN public.v4_case_commands resume ON resume.case_id=recovery.id
                 AND resume.actor_id=work.owner_id AND resume.action='resume'
                 AND resume.result @> jsonb_build_object(
                     'schema_version','salience.p1-production-recovery-case-command.v1',
                     'command_id',resume.id,'action','resume','work_id',work.work_id,
                     'case_id',recovery.id,'actor_id',work.owner_id,'workspace_id',work.workspace_id,
                     'goal_id',work.goal_id,'intent_id',work.intent_id,'cycle_id',work.cycle_id,
                     'context_id',work.context_id,'operation_id',p_operation,'work_source_revision',1,
                     'source_revision',2,'source_state','retry_due','resulting_revision',3,
                     'state','resolved','approval_request_id',p_approval,'review_command_id',review.id,
                     'request_fingerprint',work.request_fingerprint)
                LEFT JOIN public.v4_production_effect_permits successor
                  ON successor.permit_id=dispatch.current_permit_id AND successor.operation_id=p_operation
                 AND successor.generation=2 AND dispatch.generation=2
                LEFT JOIN public.v4_production_recovery_events proof
                  ON proof.event_id=successor.recovery_command_id AND proof.work_id=work.work_id
                 AND proof.event_kind='preclaim_permit_resume'
                 AND proof.payload @> jsonb_build_object(
                     'command_id',resume.id,'predecessor_permit_id',p_predecessor,
                     'successor_permit_id',successor.permit_id,'approval_request_id',p_approval,
                     'workflow_run_id',p_workflow_run,'request_fingerprint',work.request_fingerprint)
                WHERE prior.operation_id=p_operation AND prior.permit_id=p_predecessor
                  AND prior.generation=1 AND prior.recovery_command_id IS NULL
                  AND prior.approval_request_id=p_approval AND prior.expires_at<=statement_timestamp()
                  AND prior.request_snapshot->>'idempotency_key'=p_key
                  AND prior.request_snapshot->>'workflow_run_id'=p_workflow_run
                  AND (
                      (dispatch.generation=1 AND dispatch.current_permit_id=p_predecessor
                         AND dispatch.current_claim_id IS NULL)
                      OR (proof.event_id IS NOT NULL AND successor.expires_at>statement_timestamp())
                  )
                  AND NOT EXISTS (SELECT 1 FROM public.v4_case_archives archive WHERE archive.case_id=recovery.id)
                  AND NOT EXISTS (
                      SELECT 1 FROM (
                          VALUES (opened.result,'opened','production_case_open',1),
                                 (review.result,'review_approve','production_case_review_approve',2),
                                 (resume.result,'resume','production_case_resume',3)
                      ) AS expected(receipt,case_action,cycle_kind,case_revision)
                      WHERE (SELECT count(*) FROM public.v4_case_events event
                             JOIN public.v4_cycle_events audit ON audit.workspace_id=work.workspace_id
                              AND audit.goal_id=work.goal_id AND audit.intent_id=work.intent_id
                              AND audit.cycle_id=work.cycle_id AND audit.subject_id=work.owner_id
                              AND audit.traceparent=event.traceparent AND audit.kind=expected.cycle_kind
                              AND audit.payload=expected.receipt
                             WHERE event.case_id=recovery.id AND event.actor_id=work.owner_id
                               AND event.action=expected.case_action AND event.case_revision=expected.case_revision
                               AND event.payload=expected.receipt)<>1
                  )
            ) exact_matches
        $$"""
    )
    _create_record_function()
    _create_unused_publication_check()
    _create_advance_function()
    _create_guards()
    for signature in (
        _FIND, _RECORD, _ADVANCE, _ASSERT_UNUSED, "public.v4_validate_production_recovery_event_v4()",
        "public.v4_guard_preclaim_resume_event_v1()", "public.v4_assert_preclaim_resume_committed_v1()",
        "public.v4_fence_preclaim_predecessor_claim_v1()",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")


def _create_record_function() -> None:
    op.execute(
        """CREATE FUNCTION public.v4_record_preclaim_resume_v1(
            p_operation uuid, p_predecessor uuid, p_approval uuid, p_key text,
            p_workflow_run text, p_successor uuid, p_route_decision uuid, p_authorization jsonb
        ) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public AS $$
        DECLARE
            exact_resume jsonb;
            work public.v4_production_recovery_work%ROWTYPE;
            prior public.v4_production_effect_permits%ROWTYPE;
            resume public.v4_case_commands%ROWTYPE;
            proof_id uuid := gen_random_uuid();
            proof_key text := 'preclaim-permit-resume:' || p_operation::text;
            material jsonb;
        BEGIN
            exact_resume := public.v4_find_preclaim_resume_v1(
                p_operation,p_predecessor,p_approval,p_key,p_workflow_run);
            SELECT * INTO work FROM public.v4_production_recovery_work
            WHERE work_id=(exact_resume->>'work_id')::uuid FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'preclaim_resume_requires_exact_committed_command';
            END IF;
            PERFORM pg_advisory_xact_lock(hashtextextended(p_operation::text,0));
            exact_resume := public.v4_find_preclaim_resume_v1(
                p_operation,p_predecessor,p_approval,p_key,p_workflow_run);
            IF exact_resume->>'work_id' IS DISTINCT FROM work.work_id::text
               OR exact_resume->>'proof_id' IS NOT NULL
               OR p_successor IS NULL OR p_successor=p_predecessor OR p_route_decision IS NULL THEN
                RAISE EXCEPTION 'preclaim_resume_requires_exact_committed_command';
            END IF;
            SELECT * INTO prior FROM public.v4_production_effect_permits WHERE permit_id=p_predecessor;
            SELECT * INTO resume FROM public.v4_case_commands WHERE id=(exact_resume->>'command_id')::uuid;
            IF jsonb_typeof(p_authorization) IS DISTINCT FROM 'object'
               OR NOT p_authorization ?& ARRAY['resume_command_id','stage_decision_id','policy_decision_id','expires_at',
                   'delivery_profile_id','delivery_profile_version','delivery_adapter_version']
               OR p_authorization-ARRAY['resume_command_id','stage_decision_id','policy_decision_id','expires_at',
                   'delivery_profile_id','delivery_profile_version','delivery_adapter_version']<>'{}'
               OR p_authorization->>'resume_command_id' IS DISTINCT FROM resume.id::text
               OR NOT COALESCE(p_authorization->>'stage_decision_id' ~ '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$',false)
               OR NOT COALESCE(p_authorization->>'policy_decision_id' ~ '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$',false)
               OR NOT COALESCE(p_authorization->>'delivery_profile_id' ~ '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$',false)
               OR jsonb_typeof(p_authorization->'delivery_profile_version') IS DISTINCT FROM 'number'
               OR NOT COALESCE(p_authorization->>'delivery_profile_version' ~ '^[1-9][0-9]*$',false)
               OR jsonb_typeof(p_authorization->'delivery_adapter_version') IS DISTINCT FROM 'string'
               OR length(p_authorization->>'delivery_adapter_version') NOT BETWEEN 1 AND 128
               OR p_authorization->>'stage_decision_id'=resume.result->'authorization'->>'stage_decision_id'
               OR p_authorization->>'policy_decision_id'=resume.result->'authorization'->>'policy_decision_id'
               OR NOT COALESCE(isfinite((p_authorization->>'expires_at')::timestamptz),false)
               OR least((p_authorization->>'expires_at')::timestamptz,
                        (resume.result->'authorization'->>'expires_at')::timestamptz)<=clock_timestamp() THEN
                RAISE EXCEPTION 'preclaim_resume_requires_fresh_stage_and_policy_authority';
            END IF;
            -- The caller holds the exact, current route issue decision for the proposed permit.
            -- The resume receipt supplies the historical policy, stage, and stop audit bindings.
            material := jsonb_build_object(
                'schema_version','salience.p1-preclaim-permit-resume.v1',
                'reason_code','expired_unclaimed_permit','state','resolved','event_key',proof_key,
                'operation_id',p_operation,'case_id',work.source_id,'source_id',work.source_id,
                'work_id',work.work_id,'case_revision',3,'work_source_revision',work.source_revision,
                'intent_id',work.intent_id,'request_fingerprint',work.request_fingerprint,
                'predecessor_permit_id',p_predecessor,'predecessor_generation',1,
                'successor_permit_id',p_successor,'opening_command_id',work.opening_command_id,
                'command_id',resume.id,'command_sha256',encode(public.digest(resume.result::text,'sha256'),'hex'),
                'approval_request_id',p_approval,'route_decision_id',p_route_decision,
                'workspace_stop_revision',resume.result->'authorization'->'workspace_stop_revision',
                'goal_stop_revision',resume.result->'authorization'->'goal_stop_revision',
                'policy_decision_id',resume.result->'authorization'->'policy_decision_id',
                'stage_decision_id',resume.result->'authorization'->'stage_decision_id',
                'renewal_stage_decision_id',p_authorization->'stage_decision_id',
                'renewal_policy_decision_id',p_authorization->'policy_decision_id',
                'renewal_expires_at',p_authorization->'expires_at',
                'delivery_profile_id',p_authorization->'delivery_profile_id',
                'delivery_profile_version',p_authorization->'delivery_profile_version',
                'delivery_adapter_version',p_authorization->'delivery_adapter_version',
                'remote_identity_sha256',encode(public.digest(p_operation::text || ':' || p_key,'sha256'),'hex')
            ) || (
                SELECT jsonb_object_agg(binding.key,binding.value)
                FROM jsonb_each(prior.request_snapshot) binding
                WHERE binding.key=ANY(ARRAY[
                    'publication_request_id','publication_plan_id','publication_attempt_id',
                    'external_effect_id','budget_reservation_id','job_id','workflow_id','workflow_run_id'])
            );
            PERFORM set_config('salience.preclaim_resume_proof_v1',proof_id::text,true);
            INSERT INTO public.v4_production_recovery_events(
                event_id,work_id,event_key,event_kind,actor_id,policy_decision_id,payload,payload_sha256
            ) VALUES (
                proof_id,work.work_id,proof_key,'preclaim_permit_resume',resume.actor_id,
                (material->>'policy_decision_id')::uuid,material,encode(public.digest(material::text,'sha256'),'hex')
            );
            PERFORM set_config('salience.preclaim_resume_proof_v1','',true);
            RETURN proof_id;
        END
        $$"""
    )


def _create_unused_publication_check() -> None:
    op.execute(
        """CREATE FUNCTION public.v4_assert_preclaim_publication_unused_v1(
            original jsonb, provider_key text, p_work uuid
        ) RETURNS void LANGUAGE plpgsql
        SET search_path = pg_catalog, public AS $$
        DECLARE
            graph record;
            bound_operation uuid := (original->>'operation_id')::uuid;
        BEGIN
            -- Foreign keys on attempt, plan, job, request, and reservation serialize new
            -- evidence with these locks. Evaluate its absence in a subsequent statement.
            SELECT publication.id AS publication_id,plan.id AS plan_id,attempt.id AS attempt_id,
                   reservation.id AS reservation_id,reservation.estimated_amount,job.id AS job_id,
                   prior.issued_at AS initial_issue_time
            INTO graph
            FROM public.publication_requests publication
            JOIN public.publication_plans plan ON plan.publication_request_id=publication.id
            JOIN public.publication_attempts attempt ON attempt.publication_plan_id=plan.id
            JOIN public.external_effects effect ON effect.id=plan.external_effect_id
            JOIN public.jobs job ON job.id=effect.job_id
            JOIN public.budget_reservations reservation ON reservation.id=plan.budget_reservation_id
            JOIN public.v4_production_effect_permits prior
              ON prior.operation_id=bound_operation AND prior.generation=1
            WHERE publication.id=(original->>'publication_request_id')::uuid
              AND publication.request_key=provider_key AND publication.state IN ('planned','authorized')
              AND plan.id=(original->>'publication_plan_id')::uuid
              AND plan.state='reserved' AND plan.actual_cost_micros IS NULL
              AND attempt.id=(original->>'publication_attempt_id')::uuid AND attempt.attempt_number=1
              AND attempt.idempotency_key=provider_key||':attempt:1'
              AND attempt.state IN ('planned','authorized')
              AND attempt.submitted_at IS NULL AND attempt.completed_at IS NULL
              AND effect.id=(original->>'external_effect_id')::uuid
              AND effect.idempotency_key=provider_key||chr(58)||'publish'
              AND effect.status='planned' AND effect.attempted_at IS NULL AND effect.completed_at IS NULL
              AND effect.provider_reference IS NULL AND effect.effect_result IS NULL
              AND job.id=(original->>'job_id')::uuid AND job.state IN ('queued','running') AND job.finished_at IS NULL
              AND job.workflow_run_id=original->>'workflow_run_id'
              AND job.input_payload->>'workflow_id'=original->>'workflow_id'
              AND reservation.id=(original->>'budget_reservation_id')::uuid
              AND reservation.budget_id=(original->>'budget_id')::uuid
              AND reservation.job_id=job.id AND reservation.external_effect_id=effect.id
              AND reservation.reservation_key=effect.idempotency_key
              AND reservation.reserved_amount>0 AND reservation.estimated_amount>=0
              AND reservation.estimated_amount<=reservation.reserved_amount
              AND reservation.status='reserved' AND reservation.expires_at>clock_timestamp()
            FOR UPDATE OF publication,plan,attempt,effect,job,reservation;
            IF NOT FOUND OR EXISTS (
                   SELECT 1 FROM public.publication_plans other
                   WHERE other.publication_request_id=graph.publication_id AND other.id<>graph.plan_id
               ) OR EXISTS (
                   SELECT 1 FROM public.publication_attempts historical
                   WHERE historical.publication_plan_id=graph.plan_id AND historical.id<>graph.attempt_id
               ) OR EXISTS (
                   SELECT 1 FROM public.remote_publication_receipts remote
                   WHERE remote.publication_attempt_id=graph.attempt_id
               ) OR EXISTS (
                   SELECT 1 FROM public.publisher_webhook_receipts webhook
                   WHERE webhook.publication_attempt_id=graph.attempt_id
               ) OR EXISTS (
                   SELECT 1 FROM public.publications receipt WHERE receipt.publication_request_id=graph.publication_id
               ) OR EXISTS (
                   SELECT 1 FROM public.publication_status_events event
                   WHERE event.publication_attempt_id=graph.attempt_id AND event.state NOT IN ('planned','authorized')
               ) OR (
                   SELECT count(*)=1 AND bool_and(
                       entry.job_id IS NOT DISTINCT FROM graph.job_id
                       AND entry.estimated_amount=graph.estimated_amount
                       AND entry.actual_amount IS NULL
                       AND entry.provider_name IS NULL AND entry.provider_reference IS NULL
                       AND entry.recorded_at<=graph.initial_issue_time
                       AND entry.usage=jsonb_build_object(
                           'kind','estimated','estimated_micros',graph.estimated_amount*1000000))
                   FROM (
                       SELECT * FROM public.cost_ledger_entries entry
                       WHERE entry.budget_reservation_id=graph.reservation_id FOR SHARE
                   ) entry
               ) IS NOT TRUE OR EXISTS (
                   SELECT 1 FROM public.v4_production_recovery_work related
                   WHERE related.operation_id=bound_operation
                     AND related.work_id IS DISTINCT FROM p_work
               ) OR EXISTS (
                   SELECT 1 FROM public.approval_requests approval
                   JOIN public.approval_requests later ON later.job_id=graph.job_id AND later.id<>approval.id
                    AND later.workspace_id=approval.workspace_id AND later.policy_version_id=approval.policy_version_id
                    AND later.effect_type=approval.effect_type
                    AND (later.created_at>=approval.created_at OR later.decided_at>=approval.decided_at)
                    AND (later.request_context-'expires_at')=(approval.request_context-'expires_at')
                   WHERE approval.id=(original->>'approval_request_id')::uuid
               ) THEN
                RAISE EXCEPTION 'preclaim_resume_requires_never_attempted_planned_publication';
            END IF;
        END
        $$"""
    )


def _create_advance_function() -> None:
    op.execute(
        """CREATE FUNCTION public.v4_advance_preclaim_resume_dispatch_v1(
            p_operation uuid, p_predecessor uuid, p_successor uuid, p_key text, p_proof uuid
        ) RETURNS void LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public AS $$
        DECLARE
            proof public.v4_production_recovery_events%ROWTYPE;
            work public.v4_production_recovery_work%ROWTYPE;
            resume public.v4_case_commands%ROWTYPE;
            pointer public.v4_production_current_dispatch%ROWTYPE;
            prior public.v4_production_effect_permits%ROWTYPE;
            successor public.v4_production_effect_permits%ROWTYPE;
            original jsonb;
            exact_resume jsonb;
            authority_deadline timestamptz;
            live_authority record;
            subject_grant_count integer;
            subject_grant_expiry timestamptz;
        BEGIN
            SELECT * INTO proof FROM public.v4_production_recovery_events
            WHERE event_id=p_proof AND event_kind='preclaim_permit_resume'
              AND payload @> jsonb_build_object('operation_id',p_operation,
                   'predecessor_permit_id',p_predecessor,'successor_permit_id',p_successor);
            SELECT * INTO work FROM public.v4_production_recovery_work
            WHERE work_id=proof.work_id FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'preclaim_resume_requires_protected_proof';
            END IF;
            PERFORM pg_advisory_xact_lock(hashtextextended(p_operation::text,0));
            PERFORM pg_advisory_xact_lock(hashtextextended('production_publication_hold:' || p_operation::text,0));
            SELECT * INTO pointer FROM public.v4_production_current_dispatch
            WHERE operation_id=p_operation FOR UPDATE;
            SELECT * INTO prior FROM public.v4_production_effect_permits
            WHERE permit_id=p_predecessor AND operation_id=p_operation AND generation=1 FOR UPDATE;
            SELECT * INTO successor FROM public.v4_production_effect_permits
            WHERE permit_id=p_successor AND operation_id=p_operation AND generation=2 FOR UPDATE;
            IF NOT FOUND OR prior.permit_id IS NULL OR pointer.operation_id IS NULL
               OR pointer.generation<>1 OR pointer.current_permit_id<>p_predecessor
               OR pointer.current_claim_id IS NOT NULL OR pointer.idempotency_key IS DISTINCT FROM p_key
               OR proof.payload->>'remote_identity_sha256' IS DISTINCT FROM
                    encode(public.digest(p_operation::text || ':' || p_key,'sha256'),'hex')
               OR successor.recovery_command_id IS DISTINCT FROM p_proof
               OR successor.issued_at<proof.occurred_at OR successor.expires_at<=clock_timestamp()
               OR successor.approval_request_id IS DISTINCT FROM prior.approval_request_id
               OR successor.route_fence_decision_id::text IS DISTINCT FROM proof.payload->>'route_decision_id'
               OR successor.route_fence_decision_id=prior.route_fence_decision_id
               OR (successor.request_snapshot - ARRAY[
                       'generation','recovery_command_id','route_fence_decision_id',
                       'workspace_stop_revision','goal_stop_revision'])
                   IS DISTINCT FROM (prior.request_snapshot - ARRAY[
                       'generation','recovery_command_id','route_fence_decision_id',
                       'workspace_stop_revision','goal_stop_revision'])
               OR EXISTS (
                   SELECT 1 FROM public.v4_production_effect_permits other
                   WHERE other.permit_id<>successor.permit_id
                     AND other.route_fence_decision_id=successor.route_fence_decision_id
               ) OR EXISTS (
                   SELECT 1 FROM public.v4_production_effect_permit_claims historical
                   WHERE historical.operation_id=p_operation
                      OR historical.route_fence_decision_id=successor.route_fence_decision_id
               ) OR EXISTS (
                   SELECT 1 FROM public.v4_production_recovery_work related
                   WHERE related.operation_id=p_operation AND related.work_id<>work.work_id
               ) OR EXISTS (
                   SELECT 1 FROM public.v4_production_recovery_events earlier
                   WHERE earlier.work_id=work.work_id AND earlier.event_kind IN (
                       'remote_read_observation','held_conflict','stop_revocation_fence',
                       'same_operation_no_effect_reissue')
               ) OR EXISTS (
                   -- Only delivery preparation can have a valid authorization before the first claim.
                   SELECT 1 FROM public.v4_cycle_events historical
                   WHERE historical.cycle_id=work.cycle_id AND historical.kind IN (
                       'production_permit_claimed','production_action_authorized',
                       'production_publication_cancelled_before_submit',
                       'production_publication_liability_held')
                     AND (
                         historical.kind='production_action_authorized'
                         AND (historical.workspace_id,historical.subject_id,historical.goal_id,historical.intent_id)
                             = (prior.workspace_id,prior.subject_id,prior.goal_id,prior.intent_id)
                         AND historical.created_at BETWEEN prior.issued_at AND prior.expires_at
                         AND historical.payload @> jsonb_build_object(
                             'schema_version','salience.p1-production-authorization-event.v1',
                             'action','delivery','outcome','allow','claim_id',NULL,'reason_code',NULL,
                             'operation_id',p_operation,'permit_id',p_predecessor,
                             'approval_request_id',prior.approval_request_id,'idempotency_key',p_key)
                         AND historical.payload->'publication_binding'=(
                             SELECT jsonb_object_agg(binding.key,binding.value)
                             FROM jsonb_each(prior.request_snapshot) binding
                             WHERE binding.key=ANY(ARRAY[
                                 'publication_request_id','publication_plan_id','publication_attempt_id',
                                 'external_effect_id','job_id','workflow_id','workflow_run_id']))
                         AND historical.payload->>'decision_id' ~
                             '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$'
                     ) IS NOT TRUE
               ) THEN
                RAISE EXCEPTION 'preclaim_resume_requires_never_claimed_exact_generation';
            END IF;

            original := prior.request_snapshot;
            SELECT * INTO resume FROM public.v4_case_commands
            WHERE id=(proof.payload->>'command_id')::uuid;
            exact_resume := public.v4_find_preclaim_resume_v1(
                p_operation,p_predecessor,prior.approval_request_id,p_key,original->>'workflow_run_id');
            IF exact_resume->>'command_id' IS DISTINCT FROM resume.id::text
               OR exact_resume->>'work_id' IS DISTINCT FROM work.work_id::text
               OR resume.id IS NULL
               OR proof.payload->>'command_sha256' IS DISTINCT FROM
                    encode(public.digest(resume.result::text,'sha256'),'hex')
               OR resume.result->'authorization'->>'route_decision_id'=successor.route_fence_decision_id::text
               OR original->>'remote_receipt_id' IS NOT NULL
               OR successor.expires_at>least((original->>'execution_deadline')::timestamptz,
                   (proof.payload->>'renewal_expires_at')::timestamptz,
                   (resume.result->'authorization'->>'expires_at')::timestamptz) THEN
                RAISE EXCEPTION 'preclaim_resume_requires_exact_committed_command';
            END IF;

            SELECT least(recovery.deadline,intent.expires_at,subject.expires_at,operator.expires_at,
                         operator_grant.expires_at,route_owner.expires_at,successor.approval_expires_at,
                         (context.payload->>'execution_deadline')::timestamptz,
                         (policy.document->>'expires_at')::timestamptz,
                         reservation.expires_at,profile.expires_at) AS latest_expiry
            INTO live_authority
            FROM public.workspaces workspace
            JOIN public.v4_goals goal ON goal.workspace_id=workspace.id AND goal.id=work.goal_id
            JOIN public.v4_cycle_intents intent ON intent.goal_id=goal.id AND intent.id=work.intent_id
            JOIN public.v4_cycles cycle ON cycle.intent_id=intent.id AND cycle.id=work.cycle_id
            JOIN public.v4_run_contexts context ON context.id=work.context_id AND context.cycle_id=cycle.id
            JOIN public.v4_recovery_cases recovery ON recovery.id=work.source_id
            JOIN public.v4_production_intent_enrollments enrollment ON enrollment.intent_id=intent.id
             AND enrollment.eligibility_revision=intent.eligibility_revision
             AND enrollment.eligibility_revision=work.eligibility_revision
             AND enrollment.workspace_id=workspace.id AND enrollment.goal_id=goal.id
             AND enrollment.goal_revision=prior.goal_revision AND enrollment.source_owner_id=work.owner_id
             AND enrollment.subject_id=prior.subject_id AND enrollment.subject_revision=prior.subject_revision
             AND enrollment.policy_version_id=prior.policy_version_id
             AND enrollment.policy_document_hash=prior.policy_document_hash
             AND enrollment.artifact_sha256=prior.artifact_sha256
             AND (enrollment.route_id,enrollment.slot_id,enrollment.route_owner_id,enrollment.fencing_epoch,
                  enrollment.route_manifest_sha256,enrollment.external_lock_sha256)
                 = (prior.route_id,prior.slot_id,prior.owner_id,prior.fencing_epoch,
                    prior.route_manifest_sha256,prior.artifact_lock_sha256)
            JOIN public.identity_subjects subject ON subject.id=prior.subject_id
             AND subject.workspace_id=workspace.id AND subject.enabled AND subject.revision=prior.subject_revision
            JOIN public.identity_subjects operator ON operator.id=work.owner_id
             AND operator.workspace_id=workspace.id AND operator.enabled
             AND operator.revision::text=resume.result->'authorization'->>'actor_revision'
            JOIN public.identity_subjects route_owner ON route_owner.id=prior.owner_id
             AND route_owner.workspace_id=workspace.id AND route_owner.enabled
            JOIN public.permission_grants operator_grant ON operator_grant.workspace_id=workspace.id
             AND operator_grant.principal_type='identity' AND operator_grant.principal_id=operator.id::text
             AND operator_grant.scope='production:recovery:case:resume'
             AND operator_grant.effect='allow' AND operator_grant.constraints='{}'::jsonb
             AND operator_grant.expires_at>clock_timestamp()
            JOIN public.v4_stop_scopes workspace_stop ON workspace_stop.workspace_id=workspace.id
             AND workspace_stop.scope_key='workspace' AND NOT workspace_stop.stopped
             AND workspace_stop.revision::text=successor.request_snapshot->>'workspace_stop_revision'
             AND workspace_stop.revision::text=proof.payload->>'workspace_stop_revision'
            JOIN public.v4_stop_scopes goal_stop ON goal_stop.workspace_id=workspace.id
             AND goal_stop.scope_key='goal:' || goal.id::text AND NOT goal_stop.stopped
             AND goal_stop.revision::text=successor.request_snapshot->>'goal_stop_revision'
             AND goal_stop.revision::text=proof.payload->>'goal_stop_revision'
            JOIN public.policy_versions policy ON policy.id=prior.policy_version_id
             AND policy.workspace_id=workspace.id AND policy.status='active'
             AND policy.document_hash=prior.policy_document_hash
             AND policy.effective_at<=clock_timestamp()
             AND policy.document->>'schema_version'='salience.p1-production-policy.v1'
             AND (policy.document->>'expires_at')::timestamptz>clock_timestamp()
             AND policy.document->'allowed_effects' ? prior.effect_type
            JOIN public.approval_requests approval ON approval.id=prior.approval_request_id
             AND approval.workspace_id=workspace.id AND approval.policy_version_id=policy.id
             AND approval.effect_type=prior.effect_type AND approval.status='approved'
             AND approval.job_id::text=original->>'job_id'
             AND approval.decided_at<=clock_timestamp() AND nullif(btrim(approval.decided_by),'') IS NOT NULL
             AND (approval.request_context->>'expires_at')::timestamptz=successor.approval_expires_at
            JOIN public.content_programs program ON program.id=prior.program_id
             AND program.workspace_id=workspace.id AND program.status='active' AND program.dry_run_default
            JOIN public.publication_requests publication ON publication.id=(original->>'publication_request_id')::uuid
             AND publication.workspace_id=workspace.id AND publication.content_program_id=program.id
             AND publication.publisher_id=prior.provider_id AND publication.publisher_account_id::text=prior.account_id
             AND publication.publication_approval_request_id=approval.id
             AND policy.document->'publication_scope' @> jsonb_build_object(
                 'platform',publication.platform,'destination',publication.destination,
                 'locale',publication.locale,'territory',publication.territory,'visibility',publication.visibility)
            JOIN public.publisher_accounts account ON account.id=publication.publisher_account_id
             AND account.workspace_id=workspace.id AND account.status='active' AND account.platform=publication.platform
            JOIN public.publisher_capability_profiles profile ON profile.id=publication.publisher_capability_profile_id
             AND profile.workspace_id=workspace.id AND profile.publisher_account_id=account.id
             AND profile.publisher_id=prior.provider_id AND profile.platform=publication.platform
             AND profile.audit_state='verified'
             AND profile.id::text=proof.payload->>'delivery_profile_id'
             AND profile.profile_version=publication.capability_profile_version
             AND profile.profile_version::text=proof.payload->>'delivery_profile_version'
             AND profile.publisher_version=proof.payload->>'delivery_adapter_version'
            JOIN public.publication_plans plan ON plan.id=(original->>'publication_plan_id')::uuid
             AND plan.publication_request_id=publication.id AND plan.publisher_id=prior.provider_id
             AND plan.publisher_version=profile.publisher_version
            JOIN public.publication_attempts attempt ON attempt.id=(original->>'publication_attempt_id')::uuid
             AND attempt.publication_plan_id=plan.id
            JOIN public.external_effects effect ON effect.id=(original->>'external_effect_id')::uuid
             AND effect.id=plan.external_effect_id AND effect.workspace_id=workspace.id
             AND effect.effect_type='publication' AND effect.effect_classification=prior.effect_type
            JOIN public.jobs job ON job.id=(original->>'job_id')::uuid AND job.id=effect.job_id
             AND job.workspace_id=workspace.id AND job.content_program_id=program.id
             AND job.job_type='publication' AND job.state IN ('queued','running')
             AND job.workflow_run_id=original->>'workflow_run_id'
             AND job.input_payload->>'workflow_id'=original->>'workflow_id'
            JOIN public.budget_reservations reservation ON reservation.id=(original->>'budget_reservation_id')::uuid
             AND reservation.id=plan.budget_reservation_id AND reservation.budget_id=(original->>'budget_id')::uuid
             AND reservation.job_id=job.id AND reservation.external_effect_id=effect.id AND reservation.status='reserved'
            WHERE workspace.id=work.workspace_id AND workspace.status='active'
              AND goal.state='active' AND goal.revision=prior.goal_revision AND intent.goal_revision=prior.goal_revision
              AND cycle.operation_id=p_operation AND cycle.context_id=context.id AND cycle.state='runnable'
              AND recovery.state='resolved' AND recovery.revision=3
              AND context.schema_version='RunContext.local.v3'
              AND context.payload->>'operation_id'=p_operation::text
              AND context.payload->>'subject_id'=prior.subject_id::text
              AND (context.payload->>'execution_deadline')::timestamptz=
                    (original->>'execution_deadline')::timestamptz
            FOR UPDATE OF workspace,goal,intent,cycle,recovery,publication,plan,attempt,effect,job,reservation
            FOR SHARE OF subject,operator,route_owner,operator_grant,workspace_stop,goal_stop,
                         policy,program,approval,account,profile;

            IF NOT FOUND OR live_authority.latest_expiry<=clock_timestamp() THEN
                RAISE EXCEPTION 'preclaim_resume_requires_current_exact_authority_and_planned_publication';
            END IF;
            SELECT count(*),min(bound.expires_at) INTO subject_grant_count,subject_grant_expiry
            FROM (
                SELECT grants.expires_at
                FROM public.permission_grants grants
                WHERE successor.grant_ids ? grants.id::text
                  AND grants.workspace_id=work.workspace_id AND grants.principal_type='identity'
                  AND grants.principal_id=successor.subject_id::text AND grants.effect='allow'
                  AND grants.constraints='{}'::jsonb AND grants.expires_at>clock_timestamp()
                FOR SHARE OF grants
            ) bound;
            authority_deadline := least(live_authority.latest_expiry,subject_grant_expiry);
            IF subject_grant_count<>jsonb_array_length(successor.grant_ids) OR subject_grant_count=0
               OR authority_deadline<=clock_timestamp()
               OR EXISTS (
                   SELECT 1 FROM public.v4_permit_claims historical WHERE historical.operation_id=p_operation
               ) THEN
                RAISE EXCEPTION 'preclaim_resume_requires_never_attempted_planned_publication';
            END IF;
            PERFORM public.v4_assert_preclaim_publication_unused_v1(original,p_key,work.work_id);
            IF least(authority_deadline,successor.expires_at)<=clock_timestamp() THEN
                RAISE EXCEPTION 'preclaim_resume_requires_fresh_stage_and_policy_authority';
            END IF;
            PERFORM set_config('salience.production_current_dispatch_v1','advance',true);
            UPDATE public.v4_production_current_dispatch
            SET generation=2,current_permit_id=p_successor
            WHERE operation_id=p_operation;
            PERFORM set_config('salience.production_current_dispatch_v1','',true);
        END
        $$"""
    )


def _create_guards() -> None:
    op.execute(
        """CREATE FUNCTION public.v4_guard_preclaim_resume_event_v1()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog, public AS $$
        DECLARE
            record_owner name;
        BEGIN
            IF NEW.event_kind='preclaim_permit_resume'
               OR NEW.payload->>'schema_version'='salience.p1-preclaim-permit-resume.v1' THEN
                SELECT pg_get_userbyid(proowner) INTO record_owner FROM pg_proc
                WHERE oid=to_regprocedure(
                    'public.v4_record_preclaim_resume_v1(uuid,uuid,uuid,text,text,uuid,uuid,jsonb)');
                IF NEW.event_kind<>'preclaim_permit_resume'
                   OR NEW.payload->>'schema_version' IS DISTINCT FROM 'salience.p1-preclaim-permit-resume.v1'
                   OR current_user IS DISTINCT FROM record_owner
                   OR current_setting('salience.preclaim_resume_proof_v1',true)
                        IS DISTINCT FROM NEW.event_id::text THEN
                    RAISE EXCEPTION 'preclaim_resume_proof_requires_authorized_command' USING ERRCODE='42501';
                END IF;
            END IF;
            RETURN NEW;
        END
        $$"""
    )
    op.execute(
        """CREATE TRIGGER preclaim_resume_event_write_guard
           BEFORE INSERT ON public.v4_production_recovery_events
           FOR EACH ROW EXECUTE FUNCTION public.v4_guard_preclaim_resume_event_v1()"""
    )
    op.execute(
        """CREATE FUNCTION public.v4_assert_preclaim_resume_committed_v1()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM public.v4_production_effect_permits successor
                JOIN public.v4_production_current_dispatch pointer
                  ON pointer.current_permit_id=successor.permit_id AND pointer.generation=successor.generation
                 AND pointer.operation_id=successor.operation_id
                WHERE successor.recovery_command_id=NEW.event_id AND successor.generation=2
                  AND successor.expires_at>clock_timestamp()
                  AND successor.operation_id::text=NEW.payload->>'operation_id'
                  AND successor.permit_id::text=NEW.payload->>'successor_permit_id'
            ) THEN
                RAISE EXCEPTION 'preclaim_resume_requires_atomic_generation_advance';
            END IF;
            RETURN NULL;
        END
        $$"""
    )
    op.execute(
        """CREATE CONSTRAINT TRIGGER preclaim_resume_requires_advance
           AFTER INSERT ON public.v4_production_recovery_events
           DEFERRABLE INITIALLY DEFERRED
           FOR EACH ROW WHEN (NEW.event_kind='preclaim_permit_resume')
           EXECUTE FUNCTION public.v4_assert_preclaim_resume_committed_v1()"""
    )
    op.execute(
        """CREATE FUNCTION public.v4_fence_preclaim_predecessor_claim_v1()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public AS $$
        DECLARE
            evidence record;
            request_snapshot jsonb;
        BEGIN
            IF NEW.generation NOT IN (1,2) THEN
                RETURN NEW;
            END IF;
            PERFORM pg_advisory_xact_lock(hashtextextended(NEW.operation_id::text,0));
            SELECT proof.payload,proof.work_id INTO evidence FROM public.v4_production_recovery_events proof
            WHERE proof.event_key='preclaim-permit-resume:' || NEW.operation_id::text
              AND proof.event_kind='preclaim_permit_resume';
            IF FOUND THEN
                IF NEW.generation=1 THEN
                    RAISE EXCEPTION 'preclaim_resume_predecessor_claim_is_permanently_fenced';
                END IF;
                SELECT permit.request_snapshot INTO request_snapshot
                FROM public.v4_production_current_dispatch pointer
                JOIN public.v4_production_effect_permits permit ON permit.permit_id=pointer.current_permit_id
                WHERE pointer.operation_id=NEW.operation_id AND pointer.generation=2
                  AND permit.permit_id=NEW.permit_id
                  AND permit.permit_id::text=evidence.payload->>'successor_permit_id';
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'preclaim_resume_claim_requires_current_successor';
                END IF;
                PERFORM public.v4_assert_preclaim_publication_unused_v1(
                    request_snapshot,request_snapshot->>'idempotency_key',evidence.work_id);
            END IF;
            RETURN NEW;
        END
        $$"""
    )
    op.execute(
        """CREATE TRIGGER fence_preclaim_predecessor_claim
           BEFORE INSERT ON public.v4_production_effect_permit_claims
           FOR EACH ROW EXECUTE FUNCTION public.v4_fence_preclaim_predecessor_claim_v1()"""
    )
    op.execute(
        """CREATE FUNCTION public.v4_fence_preclaim_fixture_claim_v1()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public AS $$
        BEGIN
            PERFORM 1 FROM public.v4_cycles cycle WHERE cycle.id=NEW.cycle_id FOR SHARE;
            IF EXISTS (
                SELECT 1 FROM public.v4_production_recovery_events proof
                WHERE proof.event_key='preclaim-permit-resume:' || NEW.operation_id::text
                  AND proof.event_kind='preclaim_permit_resume'
            ) THEN
                RAISE EXCEPTION 'preclaim_resume_fixture_claim_is_permanently_fenced';
            END IF;
            RETURN NEW;
        END
        $$"""
    )
    op.execute(
        """CREATE TRIGGER fence_preclaim_fixture_claim
           BEFORE INSERT OR UPDATE ON public.v4_permit_claims
           FOR EACH ROW EXECUTE FUNCTION public.v4_fence_preclaim_fixture_claim_v1()"""
    )
    op.execute("REVOKE ALL ON FUNCTION public.v4_fence_preclaim_fixture_claim_v1() FROM PUBLIC")


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout='3s'")
    op.execute(
        """LOCK TABLE public.v4_production_recovery_events,
                         public.v4_production_effect_permit_claims,
                         public.v4_permit_claims
           IN ACCESS EXCLUSIVE MODE"""
    )
    op.execute(
        """DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM public.v4_production_recovery_events
                       WHERE event_kind='preclaim_permit_resume') THEN
                RAISE EXCEPTION 'preserve production preclaim permit resume history';
            END IF;
        END
        $$"""
    )
    op.execute("DROP TRIGGER fence_preclaim_fixture_claim ON public.v4_permit_claims")
    op.execute("DROP TRIGGER fence_preclaim_predecessor_claim ON public.v4_production_effect_permit_claims")
    op.execute("DROP TRIGGER preclaim_resume_requires_advance ON public.v4_production_recovery_events")
    op.execute("DROP TRIGGER preclaim_resume_event_write_guard ON public.v4_production_recovery_events")
    op.execute("DROP TRIGGER validate_v4_production_recovery_event_v4 ON public.v4_production_recovery_events")
    op.execute(
        """CREATE TRIGGER validate_v4_production_recovery_event_v3
           BEFORE INSERT ON public.v4_production_recovery_events
           FOR EACH ROW EXECUTE FUNCTION public.v4_validate_production_recovery_event_v3()"""
    )
    op.execute("DROP INDEX public.uq_v4_preclaim_resume_event_route_decision")
    op.execute(
        f"""ALTER TABLE public.v4_production_recovery_events
            DROP CONSTRAINT v4_production_recovery_events_event_kind_check,
            ADD CONSTRAINT v4_production_recovery_events_event_kind_check
                CHECK (event_kind IN ({_EVENT_KINDS}))"""
    )
    for signature in (
        _ADVANCE, _RECORD, _FIND, _ASSERT_UNUSED, "public.v4_validate_production_recovery_event_v4()",
        "public.v4_guard_preclaim_resume_event_v1()", "public.v4_assert_preclaim_resume_committed_v1()",
        "public.v4_fence_preclaim_predecessor_claim_v1()", "public.v4_fence_preclaim_fixture_claim_v1()",
    ):
        op.execute(f"DROP FUNCTION {signature}")
