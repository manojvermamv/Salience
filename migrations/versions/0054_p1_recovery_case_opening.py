"""Open a production reconciliation case and its exact recovery work atomically."""

from alembic import op


revision = "0054_p1_recovery_case_opening"
down_revision = "0053_p1_recovery_rollback_v3"
branch_labels = None
depends_on = None


_OPEN = "public.v4_open_production_recovery_case_v1(jsonb,jsonb)"
_SCHEMA = "salience.p1-production-recovery-case-opening.v1"
_GUARD = "public.v4_guard_production_case_opening_v1()"
_SOURCE_GUARD = "public.v4_require_production_case_opening_v1()"
_EVIDENCE_RELATIONS = (
    "v4_case_commands", "v4_case_events", "v4_cycle_events", "v4_production_recovery_work",
)


def upgrade() -> None:
    op.execute(
        """ALTER TABLE public.v4_production_recovery_work
           ADD COLUMN opening_command_id uuid UNIQUE REFERENCES public.v4_case_commands(id),
           ADD CONSTRAINT ck_v4_production_recovery_work_opening
               CHECK (opening_command_id IS NULL OR (
                   source_discriminant='cycle_case' AND source_revision=1))"""
    )
    op.execute(
        f"""CREATE FUNCTION public.v4_open_production_recovery_case_v1(
                p_request jsonb, p_authority jsonb
            ) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
            SET search_path = pg_catalog, public SET timezone = 'UTC' AS $$
        DECLARE
            request_row record;
            decision record;
            goal public.v4_goals%ROWTYPE;
            intent public.v4_cycle_intents%ROWTYPE;
            cycle public.v4_cycles%ROWTYPE;
            enrollment public.v4_production_intent_enrollments%ROWTYPE;
            actor public.identity_subjects%ROWTYPE;
            stage_subject public.identity_subjects%ROWTYPE;
            route_owner public.identity_subjects%ROWTYPE;
            policy public.policy_versions%ROWTYPE;
            workspace_stop public.v4_stop_scopes%ROWTYPE;
            goal_stop public.v4_stop_scopes%ROWTYPE;
            pointer public.v4_production_current_dispatch%ROWTYPE;
            permit public.v4_production_effect_permits%ROWTYPE;
            claim public.v4_production_effect_permit_claims%ROWTYPE;
            publication_effect public.external_effects%ROWTYPE;
            prior public.v4_case_commands%ROWTYPE;
            run_context jsonb;
            now_at_check timestamptz;
            actor_scope_expiry timestamptz;
            stage_grant_expiry timestamptz;
            stage_grant_count integer;
            permit_scope_count integer;
            required_stage_scopes text[];
            max_wakes integer;
            request_fingerprint text;
            command_fingerprint text;
            new_case_id uuid := gen_random_uuid();
            new_work_id uuid := gen_random_uuid();
            new_command_id uuid := gen_random_uuid();
            new_alert_id uuid := gen_random_uuid();
            receipt jsonb;
            alert jsonb;
        BEGIN
            IF jsonb_typeof(p_request) IS DISTINCT FROM 'object'
               OR NOT p_request ?& ARRAY[
                   'workspace_id','actor_id','intent_id','cycle_id','context_id','operation_id',
                   'eligibility_revision','generation','request_fingerprint','deadline','idempotency_key']
               OR (p_request - ARRAY[
                   'workspace_id','actor_id','intent_id','cycle_id','context_id','operation_id',
                   'eligibility_revision','generation','request_fingerprint','deadline','idempotency_key'])
                   <> '{{}}'::jsonb THEN
                RAISE EXCEPTION 'exact typed production case opening required' USING ERRCODE='22023';
            END IF;
            SELECT * INTO request_row FROM jsonb_to_record(p_request) AS input(
                workspace_id uuid, actor_id uuid, intent_id uuid, cycle_id uuid, context_id uuid,
                operation_id uuid, eligibility_revision integer, generation integer,
                request_fingerprint text, deadline timestamptz, idempotency_key text);
            IF num_nonnulls(request_row.workspace_id,request_row.actor_id,request_row.intent_id,
                   request_row.cycle_id,request_row.context_id,request_row.operation_id,
                   request_row.eligibility_revision,request_row.generation,request_row.request_fingerprint,
                   request_row.deadline,request_row.idempotency_key) <> 11
               OR request_row.eligibility_revision < 1 OR request_row.generation < 1
               OR request_row.request_fingerprint !~ '^[0-9a-f]{{64}}$'
               OR NOT isfinite(request_row.deadline)
               OR request_row.idempotency_key !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{{0,255}}$' THEN
                RAISE EXCEPTION 'exact bounded production case opening required' USING ERRCODE='22023';
            END IF;
            command_fingerprint := encode(public.digest(p_request::text,'sha256'),'hex');

            -- Serialize with dispatch and with the immutable publication hold writer.
            PERFORM pg_advisory_xact_lock(hashtextextended(request_row.operation_id::text,0));
            PERFORM pg_advisory_xact_lock(hashtextextended(
                'production_publication_hold:' || request_row.operation_id::text,0));
            PERFORM 1 FROM public.workspaces
            WHERE id=request_row.workspace_id AND status='active' FOR SHARE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'current production case workspace required' USING ERRCODE='42501';
            END IF;
            SELECT * INTO actor FROM public.identity_subjects
            WHERE id=request_row.actor_id AND workspace_id=request_row.workspace_id AND enabled FOR SHARE;
            SELECT expires_at INTO actor_scope_expiry FROM public.permission_grants
            WHERE workspace_id=request_row.workspace_id AND principal_type='identity'
              AND principal_id=request_row.actor_id::text AND scope='production:recovery:case:open'
              AND effect='allow' AND constraints='{{}}'::jsonb FOR SHARE;
            IF actor.id IS NULL OR actor.expires_at<=clock_timestamp()
               OR actor_scope_expiry IS NULL OR actor_scope_expiry<=clock_timestamp() THEN
                RAISE EXCEPTION 'current scoped production case opener required' USING ERRCODE='42501';
            END IF;

            SELECT cmd.* INTO prior FROM public.v4_production_recovery_work work
            JOIN public.v4_case_commands cmd ON cmd.id=work.opening_command_id
            WHERE work.workspace_id=request_row.workspace_id AND work.operation_id=request_row.operation_id
              AND work.owner_id=actor.id AND cmd.idempotency_key=request_row.idempotency_key;
            IF FOUND THEN
                IF prior.actor_id IS DISTINCT FROM request_row.actor_id
                   OR prior.fingerprint IS DISTINCT FROM command_fingerprint OR prior.action<>'open'
                   OR prior.result->>'schema_version' IS DISTINCT FROM '{_SCHEMA}'
                   OR prior.result->'request' IS DISTINCT FROM p_request
                   OR NOT EXISTS (
                       SELECT 1 FROM public.v4_production_recovery_work work
                       JOIN public.v4_case_events case_event ON case_event.case_id=work.source_id
                         AND case_event.case_revision=1 AND case_event.action='opened'
                         AND case_event.actor_id=prior.actor_id AND case_event.payload=prior.result
                       JOIN public.v4_cycle_events audit ON audit.workspace_id=work.workspace_id
                         AND audit.goal_id=work.goal_id AND audit.intent_id=work.intent_id
                         AND audit.cycle_id=work.cycle_id AND audit.subject_id=prior.actor_id
                         AND audit.kind='production_case_open' AND audit.payload=prior.result
                         AND audit.traceparent=case_event.traceparent
                       JOIN public.v4_production_recovery_events alert_event
                         ON alert_event.work_id=work.work_id AND alert_event.event_kind='alert_intent'
                         AND alert_event.event_key=concat('alert-intent:',work.work_id,':','initial')
                         AND alert_event.event_id::text=prior.result->>'alert_event_id'
                       WHERE work.opening_command_id=prior.id AND work.source_id=prior.case_id
                         AND work.work_id::text=prior.result->>'work_id'
                         AND work.owner_id=prior.actor_id
                   ) THEN
                    RAISE EXCEPTION 'production case opening idempotency or receipt conflict' USING ERRCODE='22023';
                END IF;
                RETURN prior.result;
            END IF;

            SELECT goal_row.* INTO goal FROM public.v4_goals goal_row
            JOIN public.v4_cycle_intents intent_row ON intent_row.goal_id=goal_row.id
            WHERE intent_row.id=request_row.intent_id AND goal_row.workspace_id=request_row.workspace_id
              AND goal_row.state='active' FOR UPDATE OF goal_row;
            SELECT * INTO intent FROM public.v4_cycle_intents
            WHERE id=request_row.intent_id AND goal_id=goal.id AND goal_revision=goal.revision
              AND eligibility_revision=request_row.eligibility_revision FOR UPDATE;
            SELECT * INTO cycle FROM public.v4_cycles
            WHERE id=request_row.cycle_id AND intent_id=intent.id
              AND context_id=request_row.context_id AND operation_id=request_row.operation_id
              AND state<>'closed' FOR UPDATE;
            SELECT * INTO enrollment FROM public.v4_production_intent_enrollments
            WHERE intent_id=intent.id AND eligibility_revision=intent.eligibility_revision
              AND workspace_id=goal.workspace_id AND goal_id=goal.id AND goal_revision=goal.revision
              AND source_owner_id=actor.id;
            IF cycle.id IS NULL OR enrollment.intent_id IS NULL OR NOT EXISTS (
                SELECT 1 FROM public.v4_admissions admission
                WHERE admission.intent_id=intent.id AND admission.eligibility_revision=intent.eligibility_revision
                  AND admission.disposition='admitted' AND admission.cycle_id=cycle.id
            ) THEN
                RAISE EXCEPTION 'exact admitted open cycle and enrolled production owner required'
                    USING ERRCODE='42501';
            END IF;
            IF EXISTS (SELECT 1 FROM public.v4_recovery_cases WHERE operation_id=cycle.operation_id) THEN
                RAISE EXCEPTION 'operation already has a canonical case' USING ERRCODE='22023';
            END IF;
            IF EXISTS (
                SELECT 1 FROM public.v4_production_recovery_work work
                WHERE work.operation_id=cycle.operation_id AND work.source_discriminant='production_publication_hold'
            ) THEN
                RAISE EXCEPTION 'operation already has an immutable production publication hold' USING ERRCODE='42501';
            END IF;
            IF p_authority IS NULL THEN
                RETURN NULL;
            END IF;

            SELECT * INTO pointer FROM public.v4_production_current_dispatch
            WHERE operation_id=cycle.operation_id AND generation=request_row.generation FOR UPDATE;
            SELECT * INTO permit FROM public.v4_production_effect_permits
            WHERE permit_id=pointer.current_permit_id AND operation_id=cycle.operation_id
              AND generation=pointer.generation AND intent_id=intent.id AND cycle_id=cycle.id
              AND context_id=cycle.context_id AND workspace_id=goal.workspace_id AND goal_id=goal.id
              AND goal_revision=goal.revision AND subject_id=enrollment.subject_id
              AND subject_revision=enrollment.subject_revision AND policy_version_id=enrollment.policy_version_id
              AND policy_document_hash=enrollment.policy_document_hash AND artifact_sha256=enrollment.artifact_sha256
              AND (route_id,slot_id,owner_id,fencing_epoch,route_manifest_sha256,artifact_lock_sha256)
                  = (enrollment.route_id,enrollment.slot_id,enrollment.route_owner_id,enrollment.fencing_epoch,
                     enrollment.route_manifest_sha256,enrollment.external_lock_sha256)
              AND request_snapshot->>'idempotency_key'=pointer.idempotency_key;
            SELECT * INTO claim FROM public.v4_production_effect_permit_claims
            WHERE claim_id=pointer.current_claim_id AND permit_id=permit.permit_id
              AND operation_id=cycle.operation_id AND generation=pointer.generation;
            request_fingerprint := encode(public.digest(permit.request_snapshot::text,'sha256'),'hex');
            IF claim.claim_id IS NULL OR request_fingerprint IS DISTINCT FROM request_row.request_fingerprint THEN
                RAISE EXCEPTION 'exact current production dispatch claim and request required' USING ERRCODE='42501';
            END IF;

            -- Lock the real publication graph before classifying the attempted effect.
            -- A concurrent durable receipt has to acquire a foreign-key lock on the attempt.
            SELECT effect_row.* INTO publication_effect
            FROM public.publication_requests publication
            JOIN public.publication_plans plan ON plan.publication_request_id=publication.id
            JOIN public.publication_attempts attempt ON attempt.publication_plan_id=plan.id
            JOIN public.external_effects effect_row ON effect_row.id=plan.external_effect_id
            JOIN public.jobs job ON job.id=effect_row.job_id
            WHERE publication.id=(permit.request_snapshot->>'publication_request_id')::uuid
              AND publication.workspace_id=goal.workspace_id AND publication.content_program_id=permit.program_id
              AND publication.publisher_account_id::text=permit.account_id AND publication.publisher_id=permit.provider_id
              AND publication.state IN ('planned','authorized')
              AND plan.id=(permit.request_snapshot->>'publication_plan_id')::uuid
              AND plan.publisher_id=permit.provider_id AND plan.state IN ('planned','authorized')
              AND attempt.id=(permit.request_snapshot->>'publication_attempt_id')::uuid
              AND attempt.state IN ('planned','authorized','submitting','unknown')
              AND effect_row.id=(permit.request_snapshot->>'external_effect_id')::uuid
              AND effect_row.workspace_id=goal.workspace_id AND effect_row.effect_type='publication'
              AND job.id=(permit.request_snapshot->>'job_id')::uuid
              AND job.workspace_id=goal.workspace_id AND job.content_program_id=permit.program_id
              AND job.job_type='publication' AND job.workflow_run_id=permit.request_snapshot->>'workflow_run_id'
              AND job.input_payload->>'workflow_id'=permit.request_snapshot->>'workflow_id'
            FOR UPDATE OF publication,plan,attempt,effect_row,job;
            IF publication_effect.id IS NULL OR publication_effect.status NOT IN ('submitting','unknown')
               OR publication_effect.attempted_at IS NULL OR publication_effect.attempted_at<claim.claimed_at
               OR publication_effect.completed_at IS NOT NULL OR publication_effect.provider_reference IS NOT NULL
               OR permit.request_snapshot->>'remote_receipt_id' IS NOT NULL
               OR EXISTS (
                   SELECT 1 FROM public.publication_plans plan
                   JOIN public.publication_plans original ON original.publication_request_id=plan.publication_request_id
                   WHERE original.id=(permit.request_snapshot->>'publication_plan_id')::uuid
                     AND plan.version>original.version
               ) OR EXISTS (
                   SELECT 1 FROM public.publication_attempts attempt
                   JOIN public.publication_attempts original ON original.publication_plan_id=attempt.publication_plan_id
                   WHERE original.id=(permit.request_snapshot->>'publication_attempt_id')::uuid
                     AND attempt.attempt_number>original.attempt_number
               )
               OR EXISTS (
                   SELECT 1 FROM public.remote_publication_receipts remote_receipt
                   WHERE remote_receipt.publication_attempt_id=(permit.request_snapshot->>'publication_attempt_id')::uuid
               ) OR EXISTS (
                   SELECT 1 FROM public.publication_status_events status_event
                   WHERE status_event.publication_attempt_id=(permit.request_snapshot->>'publication_attempt_id')::uuid
                     AND status_event.state NOT IN ('planned','authorized','submitting','unknown')
               ) THEN
                RAISE EXCEPTION 'claimed publication has no exact ambiguous submission evidence' USING ERRCODE='42501';
            END IF;

            SELECT payload INTO run_context FROM public.v4_run_contexts
            WHERE id=cycle.context_id AND cycle_id=cycle.id AND schema_version='RunContext.local.v3';
            max_wakes := (run_context->>'max_wakes')::integer;
            IF run_context IS NULL OR run_context->>'subject_id' IS DISTINCT FROM permit.subject_id::text
               OR run_context->>'operation_id' IS DISTINCT FROM cycle.operation_id::text
               OR max_wakes IS NULL OR max_wakes NOT BETWEEN 1 AND 10 OR cycle.recovery_count>=max_wakes THEN
                RAISE EXCEPTION 'exact canonical context with available recovery actions required' USING ERRCODE='42501';
            END IF;

            SELECT * INTO stage_subject FROM public.identity_subjects
            WHERE id=enrollment.subject_id AND workspace_id=goal.workspace_id
              AND revision=enrollment.subject_revision AND enabled FOR SHARE;
            SELECT * INTO route_owner FROM public.identity_subjects
            WHERE id=enrollment.route_owner_id AND workspace_id=goal.workspace_id AND enabled FOR SHARE;
            SELECT array_agg(required.scope) INTO required_stage_scopes FROM (
                SELECT jsonb_array_elements_text(permit.request_snapshot->'required_scopes') AS scope
                UNION SELECT 'cycles:write'
            ) required;
            SELECT min(grants.expires_at),count(*),count(*) FILTER (
                WHERE permit.grant_ids ? grants.id::text AND grants.scope=ANY(required_stage_scopes)
            ) INTO stage_grant_expiry,stage_grant_count,permit_scope_count
            FROM (
                SELECT current_grant.id,current_grant.scope,current_grant.expires_at
                FROM public.permission_grants current_grant
                JOIN (
                    SELECT jsonb_array_elements_text(enrollment.grant_ids) AS id
                    UNION SELECT jsonb_array_elements_text(permit.grant_ids) AS id
                ) bound ON bound.id::uuid=current_grant.id
                WHERE current_grant.workspace_id=goal.workspace_id AND current_grant.principal_type='identity'
                  AND current_grant.principal_id=stage_subject.id::text AND current_grant.effect='allow'
                  AND current_grant.constraints='{{}}'::jsonb AND isfinite(current_grant.expires_at)
                  AND current_grant.expires_at>clock_timestamp()
                FOR SHARE OF current_grant
            ) grants;
            SELECT * INTO goal_stop FROM public.v4_stop_scopes
            WHERE workspace_id=goal.workspace_id AND scope_key='goal:' || goal.id::text FOR SHARE;
            SELECT * INTO workspace_stop FROM public.v4_stop_scopes
            WHERE workspace_id=goal.workspace_id AND scope_key='workspace' FOR SHARE;
            SELECT * INTO policy FROM public.policy_versions
            WHERE id=enrollment.policy_version_id AND workspace_id=goal.workspace_id FOR SHARE;

            IF jsonb_typeof(p_authority) IS DISTINCT FROM 'object'
               OR NOT p_authority ?& ARRAY[
                   'policy_decision_id','policy_version_id','policy_document_hash','stage_decision_id',
                   'route_decision_id','route_id','slot_id','fencing_epoch','workspace_stop_revision',
                   'goal_stop_revision','actor_revision','expires_at','traceparent','recipient_profile_revision',
                   'retry_policy_revision','alert_policy_sha256']
               OR (p_authority - ARRAY[
                   'policy_decision_id','policy_version_id','policy_document_hash','stage_decision_id',
                   'route_decision_id','route_id','slot_id','fencing_epoch','workspace_stop_revision',
                   'goal_stop_revision','actor_revision','expires_at','traceparent','recipient_profile_revision',
                   'retry_policy_revision','alert_policy_sha256']) <> '{{}}'::jsonb THEN
                RAISE EXCEPTION 'exact production case opening authorization required' USING ERRCODE='42501';
            END IF;
            SELECT * INTO decision FROM jsonb_to_record(p_authority) AS input(
                policy_decision_id uuid, policy_version_id uuid, policy_document_hash text, stage_decision_id uuid,
                route_decision_id uuid, route_id uuid, slot_id uuid, fencing_epoch bigint, workspace_stop_revision integer,
                goal_stop_revision integer, actor_revision bigint, expires_at timestamptz, traceparent text,
                recipient_profile_revision integer, retry_policy_revision integer, alert_policy_sha256 text);
            now_at_check := clock_timestamp();
            IF num_nonnulls(decision.policy_decision_id,decision.stage_decision_id,decision.route_decision_id,
                   decision.expires_at,decision.traceparent,decision.recipient_profile_revision,
                   decision.retry_policy_revision,decision.alert_policy_sha256) <> 8
               OR decision.traceparent !~ '^00-[0-9a-f]{{32}}-[0-9a-f]{{16}}-01$'
               OR decision.recipient_profile_revision<1 OR decision.retry_policy_revision<1
               OR decision.alert_policy_sha256 !~ '^[0-9a-f]{{64}}$'
               OR NOT isfinite(decision.expires_at) OR decision.expires_at<=now_at_check
               OR decision.actor_revision IS DISTINCT FROM actor.revision
               OR actor.expires_at<=now_at_check OR actor_scope_expiry<=now_at_check
               OR stage_subject.id IS NULL OR stage_subject.expires_at<=now_at_check
               OR route_owner.id IS NULL OR route_owner.expires_at<=now_at_check
               OR jsonb_typeof(permit.request_snapshot->'required_scopes') IS DISTINCT FROM 'array'
               OR permit_scope_count<>cardinality(required_stage_scopes)
               OR permit_scope_count<>jsonb_array_length(permit.grant_ids)
               OR stage_grant_expiry<=now_at_check OR stage_grant_count <> (
                   SELECT count(*) FROM (
                       SELECT jsonb_array_elements_text(enrollment.grant_ids)
                       UNION SELECT jsonb_array_elements_text(permit.grant_ids)
                   ) all_bound_grants)
               OR workspace_stop.stopped IS DISTINCT FROM false OR goal_stop.stopped IS DISTINCT FROM false
               OR decision.workspace_stop_revision IS DISTINCT FROM workspace_stop.revision
               OR decision.goal_stop_revision IS DISTINCT FROM goal_stop.revision
               OR decision.route_id IS DISTINCT FROM enrollment.route_id
               OR decision.slot_id IS DISTINCT FROM enrollment.slot_id
               OR decision.fencing_epoch IS DISTINCT FROM enrollment.fencing_epoch
               OR decision.policy_version_id IS DISTINCT FROM enrollment.policy_version_id
               OR decision.policy_document_hash IS DISTINCT FROM enrollment.policy_document_hash
               OR policy.id IS NULL OR policy.status<>'active'
               OR policy.document_hash IS DISTINCT FROM enrollment.policy_document_hash
               OR policy.effective_at IS NULL OR policy.effective_at>now_at_check
               OR policy.document->>'schema_version' IS DISTINCT FROM 'salience.p1-production-policy.v1'
               OR (policy.document->>'expires_at')::timestamptz IS NULL
               OR (policy.document->>'expires_at')::timestamptz<=now_at_check
               OR NOT COALESCE(policy.document->'allowed_effects' ? permit.effect_type,false) THEN
                RAISE EXCEPTION 'current production opening policy, stage, route, owner and stop authority required'
                    USING ERRCODE='42501';
            END IF;
            IF request_row.deadline<=now_at_check OR request_row.deadline>now_at_check+interval '7 days' THEN
                RAISE EXCEPTION 'future bounded production case deadline required' USING ERRCODE='22023';
            END IF;

            receipt := jsonb_build_object(
                'schema_version','{_SCHEMA}','command_id',new_command_id,'case_id',new_case_id,
                'work_id',new_work_id,'alert_event_id',new_alert_id,'workspace_id',goal.workspace_id,
                'goal_id',goal.id,'intent_id',intent.id,'cycle_id',cycle.id,'context_id',cycle.context_id,
                'operation_id',cycle.operation_id,'actor_id',actor.id,'action','open','resulting_revision',1,
                'state','reconciling','kind','reconciliation','failure_class','unknown_effect',
                'eligibility_revision',intent.eligibility_revision,'generation',pointer.generation,
                'request_fingerprint',request_fingerprint,'permit_id',permit.permit_id,'claim_id',claim.claim_id,
                'external_effect_id',publication_effect.id,'evidence_state',publication_effect.status,
                'request',p_request,'authorization',p_authority-'traceparent'
            );
            IF cycle.state<>'reconciling' THEN
                UPDATE public.v4_cycles SET state='reconciling',recovery_count=recovery_count+1 WHERE id=cycle.id;
            END IF;
            PERFORM set_config('salience.production_case_opening_v1',new_case_id::text,true);
            INSERT INTO public.v4_recovery_cases(
                id,workspace_id,goal_id,target_cycle_id,context_id,operation_id,
                kind,open_fingerprint,failure_class,state,owner_id,reason,deadline,max_actions
            ) VALUES (new_case_id,goal.workspace_id,goal.id,cycle.id,cycle.context_id,cycle.operation_id,
                'reconciliation',command_fingerprint,'unknown_effect','reconciling',actor.id,
                'Claimed publication outcome requires reconciliation',request_row.deadline,least(10,max_wakes+1));

            INSERT INTO public.v4_case_commands(id,case_id,idempotency_key,fingerprint,action,actor_id,result)
            VALUES(new_command_id,new_case_id,request_row.idempotency_key,command_fingerprint,'open',actor.id,receipt);
            INSERT INTO public.v4_case_events(id,case_id,case_revision,action,actor_id,traceparent,payload)
            VALUES(gen_random_uuid(),new_case_id,1,'opened',actor.id,decision.traceparent,receipt);
            INSERT INTO public.v4_cycle_events(id,workspace_id,subject_id,goal_id,intent_id,cycle_id,kind,traceparent,payload)
            VALUES(gen_random_uuid(),goal.workspace_id,actor.id,goal.id,intent.id,cycle.id,
                'production_case_open',decision.traceparent,receipt);
            INSERT INTO public.v4_production_recovery_work(
                work_id,source_discriminant,source_id,workspace_id,goal_id,intent_id,cycle_id,context_id,operation_id,
                source_revision,case_revision,eligibility_revision,owner_id,target_alias,remote_account_id,
                reason,state,request_fingerprint,source_fingerprint,deadline,deadline_revision,recipient_profile_revision,
                opening_command_id
            ) VALUES (new_work_id,'cycle_case',new_case_id,goal.workspace_id,goal.id,intent.id,cycle.id,
                cycle.context_id,cycle.operation_id,1,1,intent.eligibility_revision,actor.id,permit.provider_id,
                permit.account_id,'unknown_effect','reconciling',request_fingerprint,repeat('0',64),
                request_row.deadline,1,decision.recipient_profile_revision,new_command_id);

            alert := jsonb_build_object(
                'schema_version','salience.p1-production-recovery-alert.v1','status','pending',
                'reason','unknown_effect','state','reconciling','source_id',new_case_id,'work_id',new_work_id,
                'intent_id',intent.id,'operation_id',cycle.operation_id,'case_id',new_case_id,
                'deadline',p_request->>'deadline','target_alias',permit.provider_id,
                'recipient_profile_revision',decision.recipient_profile_revision,
                'policy_revision',decision.retry_policy_revision,'evidence_sha256',decision.alert_policy_sha256
            );
            INSERT INTO public.v4_production_recovery_events(
                event_id,work_id,event_key,event_kind,route_id,slot_id,fencing_epoch,policy_decision_id,payload,payload_sha256
            ) VALUES(new_alert_id,new_work_id,concat('alert-intent:',new_work_id,':','initial'),'alert_intent',
                enrollment.route_id,enrollment.slot_id,enrollment.fencing_epoch,decision.policy_decision_id,
                alert,encode(public.digest(alert::text,'sha256'),'hex'));
            IF clock_timestamp() >= least(
                decision.expires_at,actor.expires_at,actor_scope_expiry,stage_subject.expires_at,
                stage_grant_expiry,route_owner.expires_at,(policy.document->>'expires_at')::timestamptz,
                request_row.deadline
            ) THEN
                RAISE EXCEPTION 'production case opening authority expired during the write'
                    USING ERRCODE='42501';
            END IF;
            PERFORM set_config('salience.production_case_opening_v1','',true);
            RETURN receipt;
        END $$"""
    )
    op.execute(f"REVOKE ALL ON FUNCTION {_OPEN} FROM PUBLIC")
    op.execute(
        f"""CREATE FUNCTION {_GUARD}
            RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public AS $$
        DECLARE
            case_id uuid;
            opening_owner name;
        BEGIN
            CASE TG_RELID
            WHEN 'public.v4_case_commands'::regclass THEN
                IF NEW.action<>'open' AND NEW.result->>'schema_version' IS DISTINCT FROM '{_SCHEMA}' THEN
                    RETURN NEW;
                END IF;
                case_id := NEW.case_id;
            WHEN 'public.v4_case_events'::regclass THEN
                IF NEW.payload->>'schema_version' IS DISTINCT FROM '{_SCHEMA}' THEN
                    RETURN NEW;
                END IF;
                case_id := NEW.case_id;
            WHEN 'public.v4_cycle_events'::regclass THEN
                IF NEW.kind<>'production_case_open' AND NEW.payload->>'schema_version' IS DISTINCT FROM '{_SCHEMA}' THEN
                    RETURN NEW;
                END IF;
                case_id := (NEW.payload->>'case_id')::uuid;
            WHEN 'public.v4_production_recovery_work'::regclass THEN
                IF NEW.opening_command_id IS NULL THEN
                    RETURN NEW;
                END IF;
                case_id := NEW.source_id;
            ELSE
                RAISE EXCEPTION 'unexpected production case opening evidence relation' USING ERRCODE='42501';
            END CASE;
            SELECT pg_get_userbyid(proowner) INTO opening_owner FROM pg_proc
            WHERE oid=to_regprocedure('{_OPEN}');
            IF case_id IS NULL OR current_user IS DISTINCT FROM opening_owner
               OR current_setting('salience.production_case_opening_v1',true) IS DISTINCT FROM case_id::text THEN
                RAISE EXCEPTION 'production case opening evidence requires its authorized transaction' USING ERRCODE='42501';
            END IF;
            RETURN NEW;
        END $$"""
    )
    op.execute(f"REVOKE ALL ON FUNCTION {_GUARD} FROM PUBLIC")
    for relation in _EVIDENCE_RELATIONS:
        op.execute(
            f"""CREATE TRIGGER production_case_opening_evidence BEFORE INSERT ON public.{relation}
                FOR EACH ROW EXECUTE FUNCTION {_GUARD}"""
        )
    op.execute(
        f"""CREATE FUNCTION {_SOURCE_GUARD}
            RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
        BEGIN
            IF TG_RELID <> 'public.v4_recovery_cases'::regclass OR TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'unexpected production case source relation' USING ERRCODE='42501';
            END IF;
            IF EXISTS (
                SELECT 1 FROM public.v4_cycles cycle
                JOIN public.v4_production_intent_enrollments enrollment ON enrollment.intent_id=cycle.intent_id
                  AND enrollment.workspace_id=NEW.workspace_id AND enrollment.goal_id=NEW.goal_id
                WHERE cycle.id=NEW.target_cycle_id
            ) AND NOT EXISTS (
                SELECT 1 FROM public.v4_production_recovery_work work
                JOIN public.v4_case_commands command ON command.id=work.opening_command_id
                  AND command.case_id=NEW.id AND command.actor_id=NEW.owner_id
                  AND command.action='open' AND command.fingerprint=NEW.open_fingerprint
                JOIN public.v4_case_events event ON event.case_id=command.case_id
                  AND event.case_revision=1 AND event.action='opened'
                  AND event.actor_id=command.actor_id AND event.payload=command.result
                JOIN public.v4_cycle_events audit ON audit.workspace_id=work.workspace_id
                  AND audit.goal_id=work.goal_id AND audit.intent_id=work.intent_id
                  AND audit.cycle_id=work.cycle_id AND audit.subject_id=command.actor_id
                  AND audit.kind='production_case_open' AND audit.traceparent=event.traceparent
                  AND audit.payload=command.result
                JOIN public.v4_production_recovery_events alert ON alert.work_id=work.work_id
                  AND alert.event_kind='alert_intent' AND alert.event_id::text=command.result->>'alert_event_id'
                  AND alert.event_key=concat('alert-intent:',work.work_id,':','initial')
                WHERE work.source_discriminant='cycle_case' AND work.source_id=NEW.id AND work.source_revision=1
                  AND work.workspace_id=NEW.workspace_id AND work.goal_id=NEW.goal_id
                  AND work.cycle_id=NEW.target_cycle_id AND work.context_id=NEW.context_id
                  AND work.operation_id=NEW.operation_id AND work.owner_id=NEW.owner_id
                  AND work.reason=NEW.failure_class AND work.state=NEW.state AND work.deadline=NEW.deadline
                  AND command.result @> jsonb_build_object(
                      'schema_version','{_SCHEMA}','case_id',NEW.id,'command_id',command.id,'work_id',work.work_id)
            ) THEN
                RAISE EXCEPTION 'enrolled cycle requires the authorized production case opening'
                    USING ERRCODE='42501';
            END IF;
            RETURN NULL;
        END $$"""
    )
    op.execute(f"REVOKE ALL ON FUNCTION {_SOURCE_GUARD} FROM PUBLIC")
    op.execute(
        f"""CREATE CONSTRAINT TRIGGER production_case_opening_source AFTER INSERT ON public.v4_recovery_cases
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION {_SOURCE_GUARD}"""
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout='3s'")
    op.execute(
        f"""DO $$ BEGIN
            LOCK TABLE public.v4_recovery_cases IN ACCESS EXCLUSIVE MODE;
            LOCK TABLE public.v4_case_commands IN SHARE MODE;
            IF EXISTS (
                SELECT 1 FROM public.v4_case_commands WHERE action='open' OR result->>'schema_version'='{_SCHEMA}'
            ) OR EXISTS (
                SELECT 1 FROM public.v4_case_events WHERE payload->>'schema_version'='{_SCHEMA}'
            ) OR EXISTS (
                SELECT 1 FROM public.v4_cycle_events
                WHERE kind='production_case_open' OR payload->>'schema_version'='{_SCHEMA}'
            )
               OR EXISTS (SELECT 1 FROM public.v4_production_recovery_work WHERE opening_command_id IS NOT NULL) THEN
                RAISE EXCEPTION 'preserve production case opening history';
            END IF;
        END $$"""
    )
    op.execute("DROP TRIGGER production_case_opening_source ON public.v4_recovery_cases")
    op.execute(f"DROP FUNCTION {_SOURCE_GUARD}")
    for relation in _EVIDENCE_RELATIONS:
        op.execute(f"DROP TRIGGER production_case_opening_evidence ON public.{relation}")
    op.execute(f"DROP FUNCTION {_GUARD}")
    op.execute(f"DROP FUNCTION {_OPEN}")
    op.execute("ALTER TABLE public.v4_production_recovery_work DROP COLUMN opening_command_id")
