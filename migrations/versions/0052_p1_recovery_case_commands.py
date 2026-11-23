"""Authorize production commands on the canonical recovery case ledger."""

from alembic import op


revision = "0052_p1_recovery_case_commands"
down_revision = "0051_p1_recovery_rollback_v2"
branch_labels = None
depends_on = None

_APPLY_SIGNATURE = (
    "public.v4_apply_production_recovery_case_command_v1"
    "(uuid,uuid,uuid,uuid,integer,text,text,jsonb,jsonb)"
)

_DEADLINE_READ_SIGNATURE = "public.v4_read_production_case_deadline_v1(uuid)"
_DEADLINE_SIGNATURE = "public.v4_suspend_production_recovery_case_v1(uuid,text,jsonb)"


def upgrade() -> None:
    op.execute(
        """CREATE FUNCTION public.v4_apply_production_recovery_case_command_v1(
            p_workspace uuid, p_work uuid, p_case uuid, p_actor uuid,
            p_revision integer, p_action text, p_key text, p_request jsonb, p_authority jsonb
        ) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public AS $$
        DECLARE
            work public.v4_production_recovery_work%ROWTYPE;
            recovery public.v4_recovery_cases%ROWTYPE;
            cycle public.v4_cycles%ROWTYPE;
            goal public.v4_goals%ROWTYPE;
            intent public.v4_cycle_intents%ROWTYPE;
            enrollment public.v4_production_intent_enrollments%ROWTYPE;
            actor public.identity_subjects%ROWTYPE;
            stage_subject public.identity_subjects%ROWTYPE;
            prior public.v4_case_commands%ROWTYPE;
            review public.v4_case_commands%ROWTYPE;
            origin public.v4_production_effect_permits%ROWTYPE;
            pointer public.v4_production_current_dispatch%ROWTYPE;
            current_permit public.v4_production_effect_permits%ROWTYPE;
            approval public.approval_requests%ROWTYPE;
            policy public.policy_versions%ROWTYPE;
            workspace_stop public.v4_stop_scopes%ROWTYPE;
            goal_stop public.v4_stop_scopes%ROWTYPE;
            run_context jsonb;
            required_scope text;
            command_fingerprint text;
            approval_id uuid;
            command_id uuid := gen_random_uuid();
            archive_id uuid;
            resulting_state text;
            disposition text;
            resulting_revision integer;
            current_time_at_check timestamptz;
            command_grant_expiry timestamptz;
            stage_grant_expiry timestamptz;
            stage_grant_count integer;
            receipt jsonb;
            review_required boolean;
        BEGIN
            IF p_workspace IS NULL OR p_work IS NULL OR p_case IS NULL OR p_actor IS NULL
               OR p_action IS NULL OR p_action NOT IN (
                   'review_approve','review_reject','resume','terminalize','archive')
               OR p_revision IS NULL OR p_revision < 1
               OR p_key IS NULL OR p_key !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$'
               OR jsonb_typeof(p_request) IS DISTINCT FROM 'object'
               OR NOT p_request ?& ARRAY['approval_request_id','reason','evidence_sha256','retain_until']
               OR (p_request - ARRAY['approval_request_id','reason','evidence_sha256','retain_until']) <> '{}'::jsonb
               OR (p_action IN ('review_approve','resume')) <> (p_request->>'approval_request_id' IS NOT NULL)
               OR (p_action IN ('review_approve','review_reject','archive')) <> (p_request->>'reason' IS NOT NULL)
               OR (p_action = 'archive') <> (p_request->>'evidence_sha256' IS NOT NULL)
               OR (p_action = 'archive') <> (p_request->>'retain_until' IS NOT NULL)
               OR (p_request->>'reason' IS NOT NULL AND (
                   jsonb_typeof(p_request->'reason') <> 'string'
                   OR length(btrim(p_request->>'reason')) NOT BETWEEN 1 AND 2000
                   OR length(p_request->>'reason') > 2000))
               OR (p_request->>'evidence_sha256' IS NOT NULL AND
                   p_request->>'evidence_sha256' !~ '^[0-9a-f]{64}$') THEN
                RAISE EXCEPTION 'exact bounded production case command required' USING ERRCODE='22023';
            END IF;

            approval_id := (p_request->>'approval_request_id')::uuid;
            required_scope := 'production:recovery:case:' ||
                CASE WHEN p_action IN ('review_approve','review_reject') THEN 'review' ELSE p_action END;
            command_fingerprint := encode(public.digest(
                jsonb_build_array(p_workspace,p_work,p_case,p_actor,p_revision,p_action,p_request)::text,
                'sha256'), 'hex');

            SELECT * INTO work FROM public.v4_production_recovery_work
            WHERE work_id=p_work AND workspace_id=p_workspace AND source_id=p_case
              AND source_discriminant='cycle_case' AND owner_id=p_actor
            FOR SHARE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'case command requires the exact enrolled production work and owner'
                    USING ERRCODE='42501';
            END IF;
            PERFORM pg_advisory_xact_lock(hashtextextended(work.operation_id::text,0));

            SELECT * INTO goal FROM public.v4_goals
            WHERE id=work.goal_id AND workspace_id=p_workspace FOR UPDATE;
            SELECT * INTO intent FROM public.v4_cycle_intents
            WHERE id=work.intent_id AND goal_id=work.goal_id FOR UPDATE;
            SELECT * INTO cycle FROM public.v4_cycles
            WHERE id=work.cycle_id AND intent_id=work.intent_id
              AND context_id=work.context_id AND operation_id=work.operation_id FOR UPDATE;
            SELECT * INTO recovery FROM public.v4_recovery_cases
            WHERE id=p_case AND workspace_id=p_workspace AND goal_id=work.goal_id
              AND target_cycle_id=cycle.id AND target_intent_id IS NULL
              AND context_id=cycle.context_id AND operation_id=cycle.operation_id
              AND owner_id=p_actor FOR UPDATE;
            SELECT * INTO enrollment FROM public.v4_production_intent_enrollments
            WHERE intent_id=work.intent_id AND eligibility_revision=work.eligibility_revision
              AND workspace_id=p_workspace AND goal_id=work.goal_id AND goal_revision=intent.goal_revision;
            SELECT * INTO actor FROM public.identity_subjects
            WHERE id=p_actor AND workspace_id=p_workspace AND enabled FOR SHARE;
            IF goal.id IS NULL OR intent.id IS NULL OR cycle.id IS NULL OR recovery.id IS NULL
               OR enrollment.intent_id IS NULL OR actor.id IS NULL
               OR actor.expires_at <= clock_timestamp() THEN
                RAISE EXCEPTION 'case command requires current scoped ownership and an exact production source'
                    USING ERRCODE='42501';
            END IF;

            SELECT expires_at INTO command_grant_expiry FROM public.permission_grants
            WHERE workspace_id=p_workspace AND principal_type='identity'
              AND principal_id=p_actor::text AND scope=required_scope
              AND effect='allow' AND constraints='{}'::jsonb AND expires_at>clock_timestamp()
            FOR SHARE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'current production case command scope required' USING ERRCODE='42501';
            END IF;

            SELECT * INTO prior FROM public.v4_case_commands WHERE case_id=p_case AND idempotency_key=p_key;
            IF FOUND THEN
                IF (prior.actor_id,prior.action,prior.fingerprint)
                       IS DISTINCT FROM (p_actor,p_action,command_fingerprint)
                   OR prior.result->>'schema_version' IS DISTINCT FROM
                       'salience.p1-production-recovery-case-command.v1'
                   OR NOT EXISTS (
                       SELECT 1 FROM public.v4_case_events event
                       WHERE event.case_id=p_case AND event.actor_id=p_actor AND event.action=p_action
                         AND event.payload=prior.result
                         AND event.case_revision::text=prior.result->>'resulting_revision')
                   OR (p_action='archive' AND NOT EXISTS (
                       SELECT 1 FROM public.v4_case_archives archive
                       WHERE archive.case_id=p_case AND archive.actor_id=p_actor
                         AND archive.id::text=prior.result->>'archive_id'
                         AND archive.reason=p_request->>'reason'
                         AND archive.evidence=jsonb_build_object('sha256',p_request->>'evidence_sha256')
                         AND archive.retain_until=(p_request->>'retain_until')::timestamptz)) THEN
                    RAISE EXCEPTION 'case command idempotency or immutable receipt conflict'
                        USING ERRCODE='22023';
                END IF;
                RETURN prior.result;
            END IF;
            IF recovery.revision <> p_revision OR work.source_revision > p_revision THEN
                RAISE EXCEPTION 'stale production case revision' USING ERRCODE='22023';
            END IF;
            IF EXISTS (SELECT 1 FROM public.v4_case_archives WHERE case_id=p_case) THEN
                RAISE EXCEPTION 'archived case identity cannot be reused' USING ERRCODE='22023';
            END IF;

            IF p_authority IS NULL THEN
                RETURN NULL;
            END IF;

            SELECT * INTO workspace_stop FROM public.v4_stop_scopes
            WHERE workspace_id=p_workspace AND scope_key='workspace' FOR SHARE;
            SELECT * INTO goal_stop FROM public.v4_stop_scopes
            WHERE workspace_id=p_workspace AND scope_key='goal:' || goal.id::text FOR SHARE;
            SELECT * INTO policy FROM public.policy_versions
            WHERE id=enrollment.policy_version_id AND workspace_id=p_workspace FOR SHARE;
            current_time_at_check := clock_timestamp();
            IF jsonb_typeof(p_authority) IS DISTINCT FROM 'object'
               OR NOT p_authority ?& ARRAY[
                   'policy_decision_id','policy_version_id','policy_document_hash','stage_decision_id',
                   'route_decision_id','route_id','slot_id','fencing_epoch','workspace_stop_revision',
                   'goal_stop_revision','actor_revision','expires_at','traceparent']
               OR (p_authority - ARRAY[
                   'policy_decision_id','policy_version_id','policy_document_hash','stage_decision_id',
                   'route_decision_id','route_id','slot_id','fencing_epoch','workspace_stop_revision',
                   'goal_stop_revision','actor_revision','expires_at','traceparent']) <> '{}'::jsonb
               OR num_nonnulls((p_authority->>'policy_decision_id')::uuid,
                               (p_authority->>'stage_decision_id')::uuid,
                               (p_authority->>'route_decision_id')::uuid) <> 3
               OR p_authority->>'traceparent' IS NULL
               OR p_authority->>'traceparent' !~ '^00-[0-9a-f]{32}-[0-9a-f]{16}-01$'
               OR (p_authority->>'expires_at')::timestamptz IS NULL
               OR (p_authority->>'expires_at')::timestamptz<=current_time_at_check
               OR (p_authority->>'actor_revision')::bigint IS DISTINCT FROM actor.revision
               OR workspace_stop.stopped IS DISTINCT FROM false OR goal_stop.stopped IS DISTINCT FROM false
               OR (p_authority->>'workspace_stop_revision')::integer IS DISTINCT FROM workspace_stop.revision
               OR (p_authority->>'goal_stop_revision')::integer IS DISTINCT FROM goal_stop.revision
               OR (p_authority->>'route_id')::uuid IS DISTINCT FROM enrollment.route_id
               OR (p_authority->>'slot_id')::uuid IS DISTINCT FROM enrollment.slot_id
               OR (p_authority->>'fencing_epoch')::bigint IS DISTINCT FROM enrollment.fencing_epoch
               OR (p_authority->>'policy_version_id')::uuid IS DISTINCT FROM enrollment.policy_version_id
               OR p_authority->>'policy_document_hash' IS DISTINCT FROM enrollment.policy_document_hash
               OR policy.id IS NULL OR policy.status <> 'active'
               OR policy.document_hash IS DISTINCT FROM enrollment.policy_document_hash
               OR policy.effective_at IS NULL OR policy.effective_at > current_time_at_check
               OR policy.document->>'schema_version' IS DISTINCT FROM 'salience.p1-production-policy.v1'
               OR (policy.document->>'expires_at')::timestamptz IS NULL
               OR (policy.document->>'expires_at')::timestamptz <= current_time_at_check THEN
                RAISE EXCEPTION 'current policy, stage, route, owner and unstopped revisions required'
                    USING ERRCODE='42501';
            END IF;

            SELECT permit.* INTO origin FROM public.v4_production_effect_permits permit
            WHERE permit.workspace_id=p_workspace AND permit.goal_id=goal.id
              AND permit.goal_revision=enrollment.goal_revision AND permit.intent_id=intent.id
              AND permit.cycle_id=cycle.id AND permit.context_id=cycle.context_id
              AND permit.operation_id=cycle.operation_id AND permit.subject_id=enrollment.subject_id
              AND permit.provider_id=work.target_alias AND permit.account_id=work.remote_account_id
              AND permit.artifact_sha256=enrollment.artifact_sha256
              AND (permit.route_id,permit.slot_id,permit.owner_id,permit.fencing_epoch,
                   permit.route_manifest_sha256,permit.artifact_lock_sha256)
                  = (enrollment.route_id,enrollment.slot_id,enrollment.route_owner_id,
                     enrollment.fencing_epoch,enrollment.route_manifest_sha256,
                     enrollment.external_lock_sha256)
              AND encode(public.digest(permit.request_snapshot::text,'sha256'),'hex')=work.request_fingerprint;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'original operation request is not the exact production work source'
                    USING ERRCODE='42501';
            END IF;

            review_required := recovery.kind = 'manual_review';
            IF p_action IN ('review_approve','resume') THEN
                SELECT * INTO stage_subject FROM public.identity_subjects
                WHERE id=enrollment.subject_id AND workspace_id=p_workspace AND enabled
                  AND revision=enrollment.subject_revision FOR SHARE;
                SELECT min(grants.expires_at),count(*) INTO stage_grant_expiry,stage_grant_count
                FROM (
                    SELECT grant_row.expires_at FROM public.permission_grants grant_row
                    JOIN jsonb_array_elements_text(enrollment.grant_ids) AS bound(id)
                      ON grant_row.id=bound.id::uuid
                    WHERE grant_row.workspace_id=p_workspace AND grant_row.principal_type='identity'
                      AND grant_row.principal_id=stage_subject.id::text
                      AND grant_row.effect='allow' AND grant_row.expires_at>clock_timestamp()
                    FOR SHARE OF grant_row
                ) grants;
                SELECT payload INTO run_context FROM public.v4_run_contexts
                WHERE id=cycle.context_id AND cycle_id=cycle.id AND schema_version='RunContext.local.v3';
                IF goal.state <> 'active' OR goal.revision <> enrollment.goal_revision
                   OR intent.eligibility_revision <> enrollment.eligibility_revision
                   OR intent.expires_at <= current_time_at_check OR cycle.state='closed'
                   OR current_time_at_check >= recovery.deadline OR recovery.action_count>=recovery.max_actions
                   OR recovery.failure_class IN ('policy_denial','unknown_effect')
                   OR run_context IS NULL
                   OR run_context->>'operation_id' IS DISTINCT FROM cycle.operation_id::text
                   OR run_context->>'subject_id' IS DISTINCT FROM enrollment.subject_id::text
                   OR (run_context->>'execution_deadline')::timestamptz IS NULL
                   OR (run_context->>'execution_deadline')::timestamptz <= current_time_at_check
                   OR stage_subject.id IS NULL OR stage_subject.expires_at<=current_time_at_check
                   OR stage_grant_count<>jsonb_array_length(enrollment.grant_ids)
                   OR NOT COALESCE(policy.document->'allowed_effects' ? origin.effect_type, false) THEN
                    RAISE EXCEPTION 'production case is not eligible for bounded forward recovery'
                        USING ERRCODE='42501';
                END IF;

                SELECT * INTO approval FROM public.approval_requests
                WHERE id=approval_id AND workspace_id=p_workspace AND policy_version_id=policy.id
                  AND effect_type=origin.effect_type AND status='approved'
                FOR SHARE;
                IF approval.id IS NULL OR nullif(btrim(approval.decided_by),'') IS NULL
                   OR approval.decided_at IS NULL OR approval.decided_at > current_time_at_check
                   OR (approval.request_context->>'expires_at')::timestamptz IS NULL
                   OR (approval.request_context->>'expires_at')::timestamptz<=current_time_at_check
                   OR NOT approval.request_context @> jsonb_build_object(
                       'schema_version','salience.p1-effect-approval.v1',
                       'workspace_id',p_workspace, 'program_id',origin.program_id,
                       'goal_id',goal.id, 'goal_revision',origin.goal_revision,
                       'intent_id',intent.id, 'cycle_id',cycle.id, 'context_id',cycle.context_id,
                       'operation_id',cycle.operation_id,'subject_id',origin.subject_id,
                       'policy_version_id',policy.id, 'policy_document_hash',policy.document_hash,
                       'effect_type',origin.effect_type, 'purpose',origin.purpose,
                       'artifact_sha256',origin.artifact_sha256, 'account_id',origin.account_id,
                       'provider_id',origin.provider_id
                   )
                   OR EXISTS (
                       SELECT 1 FROM jsonb_each(origin.request_snapshot) item(key,value)
                       WHERE item.key = ANY(ARRAY[
                           'required_scopes','budget_id','budget_reservation_id','publication_request_id',
                           'job_id','workflow_id','workflow_run_id','publication_plan_id',
                           'publication_attempt_id','external_effect_id','remote_receipt_id'])
                         AND approval.request_context->item.key IS DISTINCT FROM item.value) THEN
                    RAISE EXCEPTION 'current operation-bound production approval required' USING ERRCODE='42501';
                END IF;
            END IF;

            resulting_revision := p_revision + 1;
            CASE p_action
            WHEN 'review_approve' THEN
                IF NOT review_required OR recovery.state <> 'awaiting_review' THEN
                    RAISE EXCEPTION 'awaiting production review required' USING ERRCODE='22023';
                END IF;
                resulting_state := 'retry_due';
            WHEN 'resume' THEN
                IF recovery.state <> 'retry_due' OR cycle.state NOT IN ('retry_due','reconciling')
                   OR (run_context->>'max_wakes')::integer IS NULL
                   OR cycle.recovery_count > (run_context->>'max_wakes')::integer THEN
                    RAISE EXCEPTION 'bounded authorized retry state required' USING ERRCODE='22023';
                END IF;
                IF review_required THEN
                    SELECT command.* INTO review FROM public.v4_case_commands command
                    JOIN public.v4_case_events event
                      ON event.case_id=command.case_id AND event.action=command.action
                     AND event.actor_id=command.actor_id AND event.payload=command.result
                     AND event.case_revision=p_revision
                    WHERE command.case_id=p_case AND command.action='review_approve'
                      AND command.actor_id=p_actor
                      AND command.result->>'schema_version'='salience.p1-production-recovery-case-command.v1'
                      AND command.result->>'approval_request_id'=approval_id::text
                      AND command.result->>'resulting_revision'=p_revision::text;
                    IF NOT FOUND THEN
                        RAISE EXCEPTION 'exact prior production review approval required' USING ERRCODE='42501';
                    END IF;
                END IF;
                SELECT * INTO pointer FROM public.v4_production_current_dispatch
                WHERE operation_id=cycle.operation_id FOR SHARE;
                SELECT * INTO current_permit FROM public.v4_production_effect_permits
                WHERE permit_id=pointer.current_permit_id AND operation_id=cycle.operation_id
                  AND generation=pointer.generation AND intent_id=intent.id
                  AND cycle_id=cycle.id AND context_id=cycle.context_id FOR SHARE;
                IF current_permit.permit_id IS NULL OR pointer.current_claim_id IS NOT NULL
                   OR current_permit.expires_at > current_time_at_check
                   OR EXISTS (
                       SELECT 1 FROM public.v4_production_recovery_work liability
                       WHERE liability.operation_id=cycle.operation_id
                         AND liability.source_discriminant='production_publication_hold')
                   OR EXISTS (
                       SELECT 1 FROM public.v4_permit_claims claim WHERE claim.operation_id=cycle.operation_id
                       AND claim.state IN ('claimed','unknown'))
                   OR (current_permit.request_snapshot ? 'external_effect_id' AND NOT EXISTS (
                       SELECT 1 FROM public.external_effects effect
                       WHERE effect.id::text=current_permit.request_snapshot->>'external_effect_id'
                         AND effect.workspace_id=p_workspace AND effect.status='planned'
                         AND effect.attempted_at IS NULL FOR SHARE)) THEN
                    RAISE EXCEPTION 'live permit, claimed operation or unresolved production liability prevents resume'
                        USING ERRCODE='42501';
                END IF;
                resulting_state := 'resolved';
            ELSE
                IF cycle.state <> 'closed' OR cycle.disposition IS NULL OR cycle.closed_at IS NULL THEN
                    RAISE EXCEPTION 'committed terminal business disposition required'
                        USING ERRCODE='42501';
                END IF;
                disposition := cycle.disposition;
                IF p_action='archive' THEN
                    IF recovery.state <> 'terminal'
                       OR NOT isfinite((p_request->>'retain_until')::timestamptz)
                       OR (p_request->>'retain_until')::timestamptz<=current_time_at_check THEN
                        RAISE EXCEPTION 'terminal case and future archive retention required'
                            USING ERRCODE='42501';
                    END IF;
                    archive_id := gen_random_uuid();
                    resulting_revision := p_revision;
                ELSIF recovery.state='terminal' OR (p_action='review_reject' AND (
                    recovery.state <> 'awaiting_review' OR NOT review_required
                    OR recovery.action_count>=recovery.max_actions OR current_time_at_check>=recovery.deadline)
                ) THEN
                    RAISE EXCEPTION 'stale or unreviewable production case state' USING ERRCODE='22023';
                END IF;
                resulting_state := 'terminal';
            END CASE;

            current_time_at_check := clock_timestamp();
            IF current_time_at_check >= LEAST(
                actor.expires_at, command_grant_expiry, (p_authority->>'expires_at')::timestamptz,
                (policy.document->>'expires_at')::timestamptz
            ) OR (p_action IN ('review_approve','resume') AND current_time_at_check >= LEAST(
                recovery.deadline, intent.expires_at, stage_subject.expires_at, stage_grant_expiry,
                (run_context->>'execution_deadline')::timestamptz,
                (approval.request_context->>'expires_at')::timestamptz
            )) THEN
                RAISE EXCEPTION 'production case command authority expired before the write'
                    USING ERRCODE='42501';
            END IF;

            receipt := jsonb_build_object(
                'schema_version','salience.p1-production-recovery-case-command.v1',
                'command_id',command_id, 'work_id',work.work_id, 'case_id',p_case,
                'workspace_id',p_workspace, 'goal_id',goal.id, 'intent_id',intent.id,
                'cycle_id',cycle.id, 'context_id',cycle.context_id, 'operation_id',cycle.operation_id,
                'actor_id',p_actor, 'action',p_action, 'work_source_revision',work.source_revision,
                'source_revision',p_revision, 'source_state',recovery.state,
                'resulting_revision',resulting_revision, 'state',resulting_state,
                'request_fingerprint',work.request_fingerprint,
                'authorization',p_authority - 'traceparent', 'request',p_request,
                'approval_request_id',approval_id, 'review_command_id',review.id,
                'disposition',disposition, 'archive_id',archive_id
            );

            PERFORM set_config('salience.production_case_command_v1',p_case::text,true);
            INSERT INTO public.v4_case_commands(id,case_id,idempotency_key,fingerprint,action,actor_id,result)
            VALUES(command_id,p_case,p_key,command_fingerprint,p_action,p_actor,receipt);
            INSERT INTO public.v4_case_events(id,case_id,case_revision,action,actor_id,traceparent,payload)
            VALUES(gen_random_uuid(),p_case,resulting_revision,p_action,p_actor,p_authority->>'traceparent',receipt);
            IF p_action='archive' THEN
                INSERT INTO public.v4_case_archives(id,case_id,actor_id,reason,evidence,retain_until)
                VALUES(archive_id,p_case,p_actor,p_request->>'reason',
                    jsonb_build_object('sha256',p_request->>'evidence_sha256'),
                    (p_request->>'retain_until')::timestamptz);
            ELSE
                IF p_action='resume' THEN
                    UPDATE public.v4_cycles SET state='runnable' WHERE id=cycle.id;
                END IF;
                UPDATE public.v4_recovery_cases SET state=resulting_state,revision=resulting_revision,
                    action_count=action_count+CASE WHEN p_action='terminalize' THEN 0 ELSE 1 END
                WHERE id=p_case;
            END IF;
            INSERT INTO public.v4_cycle_events(
                id,workspace_id,subject_id,goal_id,intent_id,cycle_id,kind,traceparent,payload
            ) VALUES(gen_random_uuid(),p_workspace,p_actor,goal.id,intent.id,cycle.id,
                'production_case_' || p_action,p_authority->>'traceparent',receipt);
            PERFORM set_config('salience.production_case_command_v1','',true);
            RETURN receipt;
        END
        $$"""
    )
    op.execute("REVOKE ALL ON FUNCTION " + _APPLY_SIGNATURE + " FROM PUBLIC")

    op.execute(
        """CREATE FUNCTION public.v4_read_production_case_deadline_v1(p_work uuid)
        RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public AS $$
        DECLARE
            work public.v4_production_recovery_work%ROWTYPE;
            recovery public.v4_recovery_cases%ROWTYPE;
            source record;
            result jsonb;
            wake_count bigint;
        BEGIN
            SELECT * INTO work FROM public.v4_production_recovery_work
            WHERE work_id=p_work AND source_discriminant='cycle_case';
            IF NOT FOUND THEN RETURN NULL; END IF;
            SELECT recovery_row AS recovery,cycle.state AS cycle_state INTO source
            FROM public.v4_recovery_cases recovery_row
            JOIN public.v4_cycles cycle ON cycle.id=recovery_row.target_cycle_id
              AND cycle.id=work.cycle_id AND cycle.intent_id=work.intent_id
              AND cycle.context_id=work.context_id AND cycle.operation_id=work.operation_id
            JOIN public.v4_production_intent_enrollments enrollment
              ON enrollment.intent_id=work.intent_id AND enrollment.workspace_id=work.workspace_id
             AND enrollment.goal_id=work.goal_id AND enrollment.eligibility_revision=work.eligibility_revision
            WHERE recovery_row.id=work.source_id AND recovery_row.workspace_id=work.workspace_id
              AND recovery_row.goal_id=work.goal_id AND recovery_row.owner_id=work.owner_id
              AND recovery_row.context_id=work.context_id AND recovery_row.operation_id=work.operation_id
              AND recovery_row.deadline=work.deadline;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'production deadline has no exact canonical source' USING ERRCODE='42501';
            END IF;
            recovery := source.recovery;
            SELECT count(*),jsonb_agg(jsonb_build_object(
                     'event_id',wake.event_id,'command_id',command.id,
                     'resulting_revision',transition.case_revision))->0
            INTO wake_count,result
            FROM public.v4_production_recovery_events wake
            JOIN public.v4_case_commands command
              ON command.id::text=wake.payload->>'command_id' AND command.idempotency_key=wake.event_key
             AND command.action='deadline_suspend' AND command.case_id=work.source_id
             AND command.result @> jsonb_build_object('resulting_revision',work.case_revision+1)
            JOIN public.v4_case_events transition ON transition.case_id=command.case_id
             AND transition.actor_id=command.actor_id AND transition.case_revision=work.case_revision+1
             AND transition.action='escalated' AND transition.payload @> jsonb_build_object(
                 'state','suspended','case_id',work.source_id)
             AND (NOT (command.result ? 'schema_version') OR transition.payload=command.result)
            WHERE wake.work_id=p_work AND wake.event_kind='escalation'
              AND wake.event_key LIKE 'deadline-wake:cycle_case:' || work.source_id::text || ':' ||
                  work.source_revision::text || ':%' || ':' || 'deadline_suspend'
              AND wake.payload @> jsonb_build_object(
                  'schema_version','salience.p1-production-recovery-deadline.v1',
                  'status','deadline_suspend','result_class','suspended','attempt',work.case_revision+1,
                  'case_id',work.source_id,'work_id',p_work,'source_id',work.source_id,
                  'intent_id',work.intent_id,'operation_id',work.operation_id,'event_key',wake.event_key)
              AND wake.payload_sha256=encode(public.digest(wake.payload::text,'sha256'),'hex')
              AND wake.occurred_at>=work.deadline
              AND (wake.payload->>'deadline')::timestamptz=work.deadline;
            IF wake_count>1 OR (wake_count=1 AND NOT (
                (recovery.revision=work.case_revision+1 AND recovery.state='suspended')
                OR (recovery.revision=work.case_revision+2 AND recovery.state='terminal')
            )) THEN
                RAISE EXCEPTION 'production deadline receipt has conflicting canonical evidence'
                    USING ERRCODE='42501';
            END IF;
            RETURN jsonb_build_object('work',to_jsonb(work),'cycle_state',source.cycle_state,
                'due',clock_timestamp()>=work.deadline,'replay',result);
        END $$"""
    )
    op.execute("REVOKE ALL ON FUNCTION " + _DEADLINE_READ_SIGNATURE + " FROM PUBLIC")

    op.execute(
        """CREATE FUNCTION public.v4_suspend_production_recovery_case_v1(
            p_work uuid, p_key text, p_authority jsonb
        ) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public AS $$
        DECLARE
            work public.v4_production_recovery_work%ROWTYPE;
            recovery public.v4_recovery_cases%ROWTYPE;
            owner public.identity_subjects%ROWTYPE;
            enrollment public.v4_production_intent_enrollments%ROWTYPE;
            current_policy public.policy_versions%ROWTYPE;
            goal_stop public.v4_stop_scopes%ROWTYPE;
            workspace_stop public.v4_stop_scopes%ROWTYPE;
            policy_decision uuid;
            route_decision uuid;
            command_id uuid := gen_random_uuid();
            next_revision integer;
            traceparent text := p_authority->>'traceparent';
            canonical_deadline text;
            existing_wake jsonb;
            event_id uuid := gen_random_uuid();
            canonical_result jsonb;
            wake_evidence jsonb;
        BEGIN
            SELECT * INTO work FROM public.v4_production_recovery_work
            WHERE work_id=p_work AND source_discriminant='cycle_case' FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'production deadline requires the exact bound work' USING ERRCODE='42501';
            END IF;
            existing_wake := public.v4_read_production_case_deadline_v1(p_work)->'replay';
            IF jsonb_typeof(existing_wake)='object' THEN
                RETURN (existing_wake->>'event_id')::uuid;
            END IF;
            PERFORM 1 FROM public.v4_goals WHERE id=work.goal_id AND workspace_id=work.workspace_id FOR UPDATE;
            SELECT recovery_row.* INTO recovery FROM public.v4_recovery_cases recovery_row
            JOIN public.v4_cycles cycle ON cycle.id=recovery_row.target_cycle_id
              AND cycle.id=work.cycle_id AND cycle.intent_id=work.intent_id
              AND cycle.context_id=work.context_id AND cycle.operation_id=work.operation_id
              AND cycle.state<>'closed'
            WHERE recovery_row.id=work.source_id AND recovery_row.workspace_id=work.workspace_id
              AND recovery_row.goal_id=work.goal_id AND recovery_row.context_id=work.context_id
              AND recovery_row.operation_id=work.operation_id AND recovery_row.owner_id=work.owner_id
              AND recovery_row.revision=work.case_revision AND recovery_row.deadline=work.deadline
              AND recovery_row.state=work.state FOR UPDATE OF cycle,recovery_row;
            canonical_deadline := to_char(work.deadline AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS')
                || CASE WHEN to_char(work.deadline AT TIME ZONE 'UTC','US')='000000'
                   THEN '' ELSE '.' || to_char(work.deadline AT TIME ZONE 'UTC','US') END || '+00:00';
            IF recovery.id IS NULL OR recovery.deadline>clock_timestamp()
               OR recovery.state NOT IN ('awaiting_review','retry_due','reconciling','rework_due')
               OR recovery.action_count >= recovery.max_actions
               OR p_key IS DISTINCT FROM concat(
                   'deadline-wake:cycle_case:', work.source_id, ':', work.source_revision, ':',
                   canonical_deadline, ':', 'deadline_suspend') THEN
                RAISE EXCEPTION 'production deadline is not due for the exact case revision'
                    USING ERRCODE='42501';
            END IF;

            SELECT * INTO enrollment FROM public.v4_production_intent_enrollments
            WHERE intent_id=work.intent_id AND eligibility_revision=work.eligibility_revision
              AND goal_id=work.goal_id AND workspace_id=work.workspace_id;
            SELECT * INTO owner FROM public.identity_subjects
            WHERE id=work.owner_id AND workspace_id=work.workspace_id AND enabled FOR SHARE;
            SELECT * INTO current_policy FROM public.policy_versions
            WHERE id=enrollment.policy_version_id AND workspace_id=work.workspace_id FOR SHARE;
            SELECT * INTO workspace_stop FROM public.v4_stop_scopes
            WHERE workspace_id=work.workspace_id AND scope_key='workspace' FOR SHARE;
            SELECT * INTO goal_stop FROM public.v4_stop_scopes
            WHERE workspace_id=work.workspace_id AND scope_key='goal:'||work.goal_id::text FOR SHARE;
            policy_decision := (p_authority->>'policy_decision_id')::uuid;
            route_decision := (p_authority->>'route_decision_id')::uuid;
            IF enrollment.intent_id IS NULL OR owner.id IS NULL OR owner.expires_at<=clock_timestamp()
               OR current_policy.id IS NULL OR current_policy.status<>'active'
               OR current_policy.document_hash IS DISTINCT FROM enrollment.policy_document_hash
               OR current_policy.effective_at IS NULL OR current_policy.effective_at>clock_timestamp()
               OR current_policy.document->>'schema_version' IS DISTINCT FROM 'salience.p1-production-policy.v1'
               OR (current_policy.document->>'expires_at')::timestamptz IS NULL
               OR (current_policy.document->>'expires_at')::timestamptz<=clock_timestamp()
               OR workspace_stop.stopped IS DISTINCT FROM false OR goal_stop.stopped IS DISTINCT FROM false
               OR (p_authority->>'workspace_stop_revision')::integer IS DISTINCT FROM workspace_stop.revision
               OR (p_authority->>'stop_revision')::integer IS DISTINCT FROM goal_stop.revision
               OR (p_authority->>'policy_revision')::integer IS NULL
               OR (p_authority->>'policy_revision')::integer<1
               OR policy_decision IS NULL OR route_decision IS NULL
               OR traceparent IS NULL OR traceparent !~ '^00-[0-9a-f]{32}-[0-9a-f]{16}-01$' THEN
                RAISE EXCEPTION 'current production deadline owner, policy, route and stops required'
                    USING ERRCODE='42501';
            END IF;

            next_revision := work.case_revision + 1;
            canonical_result := jsonb_build_object(
                'schema_version','salience.p1-production-recovery-deadline.v1',
                'case_id',work.source_id,'work_id',work.work_id,'operation_id',work.operation_id,
                'context_id',work.context_id,'source_revision',work.source_revision,
                'source_state',work.state,'resulting_revision',next_revision,'state','suspended'
            );
            wake_evidence := jsonb_build_object(
                'schema_version','salience.p1-production-recovery-deadline.v1',
                'status','deadline_suspend','result_class','suspended','attempt',next_revision,
                'command_id',command_id,'case_id',work.source_id,'work_id',work.work_id,
                'source_id',work.source_id,'intent_id',work.intent_id,'operation_id',work.operation_id,
                'deadline',canonical_deadline,'event_key',p_key,
                'policy_decision_id',policy_decision,'route_decision_id',route_decision,
                'policy_revision',(p_authority->>'policy_revision')::integer,
                'stop_revision',goal_stop.revision,'workspace_stop_revision',workspace_stop.revision,
                'goal_stop_revision',goal_stop.revision
            );
            PERFORM set_config('salience.production_case_deadline_v1',work.source_id::text,true);
            INSERT INTO public.v4_case_commands(id,case_id,idempotency_key,fingerprint,action,actor_id,result)
            VALUES(command_id,work.source_id,p_key,encode(public.digest(p_key,'sha256'),'hex'),
                'deadline_suspend',work.owner_id,canonical_result);
            INSERT INTO public.v4_case_events(id,case_id,case_revision,action,actor_id,traceparent,payload)
            VALUES(gen_random_uuid(),work.source_id,next_revision,'escalated',work.owner_id,traceparent,
                canonical_result);
            INSERT INTO public.v4_production_recovery_events(
                event_id,work_id,event_key,event_kind,occurred_at,actor_id,route_id,slot_id,fencing_epoch,
                policy_decision_id,payload,payload_sha256
            ) VALUES (
                event_id,work.work_id,p_key,'escalation',clock_timestamp(),work.owner_id,
                enrollment.route_id,enrollment.slot_id,enrollment.fencing_epoch,policy_decision,
                wake_evidence,encode(public.digest(wake_evidence::text,'sha256'),'hex')
            );
            UPDATE public.v4_recovery_cases SET state='suspended',revision=next_revision WHERE id=work.source_id;
            PERFORM set_config('salience.production_case_deadline_v1','',true);
            RETURN event_id;
        END $$"""
    )
    op.execute("REVOKE ALL ON FUNCTION " + _DEADLINE_SIGNATURE + " FROM PUBLIC")

    op.execute(
        """CREATE FUNCTION public.v4_guard_production_case_evidence_write_v1()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog, public AS $$
        DECLARE
            apply_owner name;
            evidence jsonb;
            affected_case uuid;
            reserved_deadline_command boolean := false;
        BEGIN
            IF TG_TABLE_NAME='v4_production_recovery_events' THEN
                evidence := NEW.payload;
                affected_case := (evidence->>'case_id')::uuid;
            ELSIF TG_TABLE_NAME='v4_case_commands' THEN
                evidence := NEW.result;
                affected_case := NEW.case_id;
                reserved_deadline_command := NEW.action='deadline_suspend'
                    OR starts_with(NEW.idempotency_key,'deadline-wake:cycle_case:');
            ELSE
                evidence := NEW.payload;
                affected_case := NEW.case_id;
            END IF;
            IF reserved_deadline_command OR (
                evidence->>'schema_version'='salience.p1-production-recovery-deadline.v1'
                AND (TG_TABLE_NAME<>'v4_production_recovery_events' OR evidence->>'status'='deadline_suspend')
            ) THEN
                SELECT pg_get_userbyid(proowner) INTO apply_owner FROM pg_proc
                WHERE oid=to_regprocedure('public.v4_suspend_production_recovery_case_v1(uuid,text,jsonb)');
                IF current_user IS DISTINCT FROM apply_owner
                   OR current_setting('salience.production_case_deadline_v1',true)
                       IS DISTINCT FROM affected_case::text THEN
                    RAISE EXCEPTION 'production deadline evidence requires the authorized command'
                        USING ERRCODE='42501';
                END IF;
            END IF;
            IF evidence->>'schema_version'='salience.p1-production-recovery-case-command.v1' THEN
                SELECT pg_get_userbyid(proowner) INTO apply_owner FROM pg_proc
                WHERE oid=to_regprocedure(
                    'public.v4_apply_production_recovery_case_command_v1(uuid,uuid,uuid,uuid,integer,text,text,jsonb,jsonb)');
                IF current_user IS DISTINCT FROM apply_owner
                   OR current_setting('salience.production_case_command_v1',true)
                       IS DISTINCT FROM affected_case::text THEN
                    RAISE EXCEPTION 'production case evidence requires the authorized command'
                        USING ERRCODE='42501';
                END IF;
            END IF;
            RETURN NEW;
        END $$"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.v4_guard_production_case_evidence_write_v1() FROM PUBLIC"
    )
    for relation in ("v4_case_commands", "v4_case_events", "v4_production_recovery_events"):
        op.execute(
            f"""CREATE TRIGGER production_case_evidence_write BEFORE INSERT ON public.{relation}
                FOR EACH ROW EXECUTE FUNCTION public.v4_guard_production_case_evidence_write_v1()"""
        )

    op.execute(
        """CREATE FUNCTION public.v4_guard_production_case_identity_v1()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public AS $$
        DECLARE
            affected_case uuid;
        BEGIN
            IF TG_TABLE_NAME='v4_recovery_cases' THEN
                IF NEW.operation_id IS NOT NULL AND EXISTS (
                    SELECT 1 FROM public.v4_recovery_cases prior
                    JOIN public.v4_case_archives archive ON archive.case_id=prior.id
                    JOIN public.v4_production_recovery_work work ON work.source_id=prior.id
                      AND work.source_discriminant='cycle_case'
                    WHERE prior.operation_id=NEW.operation_id AND prior.workspace_id=NEW.workspace_id
                ) THEN
                    RAISE EXCEPTION 'archived production recovery identity cannot be reused'
                        USING ERRCODE='42501';
                END IF;
                RETURN NEW;
            END IF;

            affected_case := NEW.case_id;
            IF NOT EXISTS (
                SELECT 1 FROM public.v4_recovery_cases recovery
                JOIN public.v4_cycles cycle ON cycle.id=recovery.target_cycle_id
                JOIN public.v4_production_intent_enrollments enrollment
                  ON enrollment.intent_id=cycle.intent_id AND enrollment.workspace_id=recovery.workspace_id
                 AND enrollment.goal_id=recovery.goal_id
                WHERE recovery.id=affected_case
            ) THEN
                RETURN NEW;
            END IF;
            IF TG_TABLE_NAME='v4_case_reviews' THEN
                RAISE EXCEPTION 'fixture review cannot bind an enrolled production case'
                    USING ERRCODE='42501';
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM public.v4_case_commands command
                JOIN public.v4_case_events event
                  ON event.case_id=command.case_id AND event.action=command.action
                 AND event.actor_id=command.actor_id AND event.payload=command.result
                WHERE command.case_id=affected_case AND command.action='archive'
                  AND command.actor_id=NEW.actor_id
                  AND command.result->>'schema_version'='salience.p1-production-recovery-case-command.v1'
                  AND command.result->>'archive_id'=NEW.id::text
                  AND command.result->'request'->>'reason'=NEW.reason
                  AND (command.result->'request'->>'retain_until')::timestamptz=NEW.retain_until
                  AND NEW.evidence=jsonb_build_object('sha256',command.result->'request'->>'evidence_sha256')
                  AND event.case_revision::text=command.result->>'resulting_revision'
            ) THEN
                RAISE EXCEPTION 'production archive requires the exact authorized command'
                    USING ERRCODE='42501';
            END IF;
            RETURN NEW;
        END $$"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.v4_guard_production_case_identity_v1() FROM PUBLIC"
    )
    for relation in ("v4_recovery_cases", "v4_case_reviews", "v4_case_archives"):
        op.execute(
            f"""CREATE TRIGGER production_case_identity BEFORE INSERT ON public.{relation}
                FOR EACH ROW EXECUTE FUNCTION public.v4_guard_production_case_identity_v1()"""
        )

    op.execute(
        """CREATE FUNCTION public.v4_assert_recovery_case_audit_v2()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM public.v4_case_events
                WHERE case_id=NEW.id AND case_revision=NEW.revision
            ) THEN
                RAISE EXCEPTION 'V4 case transition lacks immutable event';
            END IF;
            IF EXISTS (
                SELECT 1 FROM public.v4_cycles cycle
                JOIN public.v4_production_intent_enrollments enrollment
                  ON enrollment.intent_id=cycle.intent_id AND enrollment.goal_id=NEW.goal_id
                 AND enrollment.workspace_id=NEW.workspace_id
                WHERE cycle.id=NEW.target_cycle_id
            ) THEN
                IF NEW.state='suspended' THEN
                    IF NOT EXISTS (
                        SELECT 1 FROM public.v4_case_commands command
                        JOIN public.v4_case_events event ON event.case_id=command.case_id
                          AND event.actor_id=command.actor_id AND event.case_revision=NEW.revision
                          AND event.action='escalated' AND event.payload->>'state'=NEW.state
                          AND event.payload->>'case_id'=NEW.id::text
                        JOIN public.v4_production_recovery_work work
                          ON work.work_id::text=command.result->>'work_id'
                         AND work.source_discriminant='cycle_case' AND work.source_id=NEW.id
                         AND work.workspace_id=NEW.workspace_id AND work.goal_id=NEW.goal_id
                         AND work.case_revision=OLD.revision AND work.source_revision=OLD.revision
                         AND work.operation_id=NEW.operation_id AND work.context_id=NEW.context_id
                         AND work.cycle_id=NEW.target_cycle_id AND work.owner_id=NEW.owner_id
                         AND work.state=OLD.state AND work.deadline=NEW.deadline
                        JOIN public.v4_production_intent_enrollments enrollment
                          ON enrollment.intent_id=work.intent_id
                         AND enrollment.eligibility_revision=work.eligibility_revision
                         AND enrollment.workspace_id=work.workspace_id
                         AND enrollment.goal_id=work.goal_id
                        JOIN public.v4_production_recovery_events wake ON wake.work_id=work.work_id
                         AND wake.event_kind='escalation' AND wake.event_key=command.idempotency_key
                         AND wake.actor_id=command.actor_id AND wake.occurred_at>=work.deadline
                         AND (wake.route_id,wake.slot_id,wake.fencing_epoch)
                             = (enrollment.route_id,enrollment.slot_id,enrollment.fencing_epoch)
                        WHERE command.case_id=NEW.id AND command.action='deadline_suspend'
                          AND command.actor_id=NEW.owner_id AND command.created_at>=work.deadline
                          AND command.result @> jsonb_build_object(
                            'schema_version','salience.p1-production-recovery-deadline.v1',
                            'resulting_revision',NEW.revision,'source_revision',OLD.revision,
                            'state','suspended','source_state',OLD.state,'case_id',NEW.id,
                            'operation_id',NEW.operation_id,'context_id',NEW.context_id)
                          AND event.payload=command.result
                          AND wake.payload @> jsonb_build_object(
                            'schema_version','salience.p1-production-recovery-deadline.v1',
                            'command_id',command.id,'status','deadline_suspend','attempt',NEW.revision,
                            'case_id',NEW.id,'source_id',NEW.id,'work_id',work.work_id,
                            'operation_id',work.operation_id,'intent_id',work.intent_id,
                            'event_key',command.idempotency_key,'policy_decision_id',wake.policy_decision_id)
                          AND (wake.payload->>'deadline')::timestamptz=work.deadline
                    ) THEN
                        RAISE EXCEPTION 'production deadline requires immutable exact command and event'
                            USING ERRCODE='42501';
                    END IF;
                ELSIF NOT EXISTS (
                    SELECT 1 FROM public.v4_case_commands command
                    JOIN public.v4_case_events event
                      ON event.case_id=command.case_id AND event.action=command.action
                     AND event.actor_id=command.actor_id AND event.payload=command.result
                     AND event.case_revision=NEW.revision
                    WHERE command.case_id=NEW.id AND command.actor_id=NEW.owner_id
                      AND ((NEW.state='retry_due' AND command.action='review_approve')
                        OR (NEW.state='resolved' AND command.action='resume')
                        OR (NEW.state='terminal' AND command.action IN ('terminalize','review_reject')))
                      AND command.result->>'schema_version'='salience.p1-production-recovery-case-command.v1'
                      AND command.result->>'source_revision'=OLD.revision::text
                      AND command.result->>'source_state'=OLD.state
                      AND command.result->>'resulting_revision'=NEW.revision::text
                      AND command.result->>'state'=NEW.state
                ) THEN
                    RAISE EXCEPTION 'production case transition requires the exact authorized command'
                        USING ERRCODE='42501';
                END IF;
            ELSIF OLD.state='awaiting_review' AND NEW.state='retry_due' AND NOT EXISTS (
                SELECT 1 FROM public.v4_review_decisions decision
                JOIN public.v4_case_reviews review ON review.id=decision.review_id
                WHERE review.case_id=NEW.id AND decision.action='approve_resume'
            ) THEN
                RAISE EXCEPTION 'V4 review approval lacks immutable decision';
            END IF;
            RETURN NULL;
        END $$"""
    )
    op.execute("DROP TRIGGER audited ON public.v4_recovery_cases")
    op.execute(
        """CREATE CONSTRAINT TRIGGER audited AFTER UPDATE ON public.v4_recovery_cases
           DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
           EXECUTE FUNCTION public.v4_assert_recovery_case_audit_v2()"""
    )


def downgrade() -> None:
    op.execute(
        """DO $$ BEGIN
            LOCK TABLE public.v4_case_commands IN SHARE MODE;
            IF EXISTS (
                SELECT 1 FROM public.v4_case_commands
                WHERE result->>'schema_version' IN (
                    'salience.p1-production-recovery-case-command.v1',
                    'salience.p1-production-recovery-deadline.v1')
            ) THEN
                RAISE EXCEPTION 'preserve production case command history';
            END IF;
        END $$"""
    )
    op.execute("DROP TRIGGER audited ON public.v4_recovery_cases")
    op.execute(
        """CREATE CONSTRAINT TRIGGER audited AFTER UPDATE ON public.v4_recovery_cases
           DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
           EXECUTE FUNCTION public.v4_assert_governance_audit()"""
    )
    op.execute("DROP FUNCTION public.v4_assert_recovery_case_audit_v2()")
    for relation in ("v4_recovery_cases", "v4_case_reviews", "v4_case_archives"):
        op.execute(f"DROP TRIGGER production_case_identity ON public.{relation}")
    for relation in ("v4_case_commands", "v4_case_events", "v4_production_recovery_events"):
        op.execute(f"DROP TRIGGER production_case_evidence_write ON public.{relation}")
    op.execute("DROP FUNCTION public.v4_guard_production_case_identity_v1()")
    op.execute("DROP FUNCTION public.v4_guard_production_case_evidence_write_v1()")
    op.execute("DROP FUNCTION " + _DEADLINE_SIGNATURE)
    op.execute("DROP FUNCTION " + _DEADLINE_READ_SIGNATURE)
    op.execute("DROP FUNCTION " + _APPLY_SIGNATURE)
