"""Bind expired-unclaimed opening and successor receipts in rollback inventory."""

from alembic import op


revision = "0058_p1_preclaim_rollback_v5"
down_revision = "0057_p1_preclaim_case_opening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE FUNCTION public.v4_p1_rollback_preclaim_lineage_v5(
               p_workspace_id uuid, p_work_id text
           ) RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER
           SET search_path = pg_catalog, public AS $$
           WITH opening AS (
               SELECT command.result AS receipt, work, prior, pointer
               FROM public.v4_production_recovery_work work
               JOIN public.v4_recovery_cases recovery ON recovery.id=work.source_id
               JOIN public.v4_case_commands command ON command.id=work.opening_command_id
                   AND command.case_id=recovery.id AND command.actor_id=work.owner_id
                   AND command.action='open' AND command.created_at<work.deadline
               JOIN public.v4_case_events opened ON opened.case_id=recovery.id AND opened.case_revision=1
                   AND opened.action='opened' AND opened.actor_id=command.actor_id
                   AND opened.payload=command.result
               JOIN public.v4_cycle_events audit ON audit.workspace_id=work.workspace_id
                   AND audit.goal_id=work.goal_id AND audit.intent_id=work.intent_id
                   AND audit.cycle_id=work.cycle_id AND audit.subject_id=command.actor_id
                   AND audit.kind='production_case_open' AND audit.traceparent=opened.traceparent
                   AND audit.payload=command.result
               JOIN public.v4_production_effect_permits prior ON prior.operation_id=work.operation_id
                   AND prior.generation=1 AND prior.recovery_command_id IS NULL
                   AND prior.expires_at<=command.created_at AND isfinite(prior.expires_at)
                   AND (prior.workspace_id,prior.goal_id,prior.intent_id,prior.cycle_id,prior.context_id)
                       =(work.workspace_id,work.goal_id,work.intent_id,work.cycle_id,work.context_id)
               JOIN public.v4_production_current_dispatch pointer ON pointer.operation_id=work.operation_id
               JOIN public.v4_production_intent_enrollments enrollment ON enrollment.intent_id=work.intent_id
                   AND enrollment.eligibility_revision=work.eligibility_revision
                   AND (enrollment.workspace_id,enrollment.goal_id,enrollment.source_owner_id)
                       =(work.workspace_id,work.goal_id,work.owner_id)
                   AND (enrollment.route_id,enrollment.slot_id,enrollment.route_owner_id,enrollment.fencing_epoch,
                        enrollment.route_manifest_sha256,enrollment.external_lock_sha256)
                      =(prior.route_id,prior.slot_id,prior.owner_id,prior.fencing_epoch,
                        prior.route_manifest_sha256,prior.artifact_lock_sha256)
                   AND (enrollment.goal_revision,enrollment.subject_id,enrollment.subject_revision,
                        enrollment.policy_version_id,enrollment.policy_document_hash,enrollment.artifact_sha256)
                      =(prior.goal_revision,prior.subject_id,prior.subject_revision,
                        prior.policy_version_id,prior.policy_document_hash,prior.artifact_sha256)
               JOIN public.v4_production_recovery_events alert ON alert.work_id=work.work_id
                   AND alert.event_id::text=command.result->>'alert_event_id'
                   AND alert.event_kind='alert_intent'
                   AND alert.event_key=concat('alert-intent:',work.work_id,':','initial')
                   AND (alert.route_id,alert.slot_id,alert.fencing_epoch)
                       =(enrollment.route_id,enrollment.slot_id,enrollment.fencing_epoch)
               WHERE work.work_id::text=p_work_id AND work.workspace_id=p_workspace_id
                 AND work.source_discriminant='cycle_case' AND work.state='awaiting_review'
                 AND work.reason='manual_review' AND work.case_revision=1 AND work.source_revision=1
                 AND recovery.kind='manual_review' AND recovery.failure_class=work.reason
                 AND (recovery.workspace_id,recovery.goal_id,recovery.target_cycle_id,recovery.context_id,
                      recovery.operation_id,recovery.owner_id,recovery.deadline)
                     =(work.workspace_id,work.goal_id,work.cycle_id,work.context_id,work.operation_id,
                       work.owner_id,work.deadline)
                 AND recovery.open_fingerprint=command.fingerprint
                 AND command.fingerprint=encode(public.digest((command.result->'request')::text,'sha256'),'hex')
                 AND work.request_fingerprint=encode(public.digest(prior.request_snapshot::text,'sha256'),'hex')
                 AND (work.target_alias,work.remote_account_id)=(prior.provider_id,prior.account_id)
                 AND prior.request_snapshot->>'idempotency_key'=pointer.idempotency_key
                 AND command.result @> jsonb_build_object(
                     'schema_version','salience.p1-production-preclaim-case-opening.v1',
                     'opening_kind','expired_unclaimed_permit','action','open','command_id',command.id,
                     'work_id',work.work_id,'case_id',recovery.id,'actor_id',work.owner_id,
                     'workspace_id',work.workspace_id,'goal_id',work.goal_id,'intent_id',work.intent_id,
                     'cycle_id',work.cycle_id,'context_id',work.context_id,'operation_id',work.operation_id,
                     'eligibility_revision',work.eligibility_revision,'generation',1,'resulting_revision',1,
                     'request_fingerprint',work.request_fingerprint,'permit_id',prior.permit_id,'claim_id',NULL,
                     'approval_request_id',prior.approval_request_id,'provider_idempotency_key',pointer.idempotency_key,
                     'state',work.state,'kind',recovery.kind,'failure_class',work.reason,'evidence_state','planned')
                 AND command.result @> (
                     SELECT jsonb_object_agg(binding.key,binding.value)
                     FROM jsonb_each(prior.request_snapshot) binding
                     WHERE binding.key=ANY(ARRAY['external_effect_id','publication_request_id',
                       'publication_plan_id','publication_attempt_id','job_id','workflow_id','workflow_run_id',
                       'budget_reservation_id']))
                 AND command.result->'request' @> jsonb_build_object(
                     'workspace_id',work.workspace_id,'actor_id',command.actor_id,'intent_id',work.intent_id,
                     'cycle_id',work.cycle_id,'context_id',work.context_id,'operation_id',work.operation_id,
                     'eligibility_revision',work.eligibility_revision,'generation',1,'permit_id',prior.permit_id,
                     'request_fingerprint',work.request_fingerprint,'idempotency_key',command.idempotency_key,
                     'opening_kind','expired_unclaimed_permit')
                 AND (command.result->'request'->>'deadline')::timestamptz=work.deadline
                 AND command.result->'authorization' @> jsonb_build_object(
                     'route_id',enrollment.route_id,'slot_id',enrollment.slot_id,
                     'fencing_epoch',enrollment.fencing_epoch,'policy_version_id',enrollment.policy_version_id,
                     'policy_document_hash',enrollment.policy_document_hash,'policy_decision_id',alert.policy_decision_id,
                     'recipient_profile_revision',work.recipient_profile_revision)
                 AND NOT EXISTS (
                     SELECT 1 FROM unnest(ARRAY['policy_decision_id','stage_decision_id','route_decision_id']) key
                     WHERE (command.result->'authorization'->>key ~
                         '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$') IS DISTINCT FROM true)
                 AND command.result->'authorization'->>'workspace_stop_revision'
                     =prior.request_snapshot->>'workspace_stop_revision'
                 AND command.result->'authorization'->>'goal_stop_revision'
                     =prior.request_snapshot->>'goal_stop_revision'
                 AND NOT EXISTS (
                     SELECT 1 FROM (VALUES
                        ('workspace',command.result->'authorization'->>'workspace_stop_revision'),
                        (concat('goal:',work.goal_id),command.result->'authorization'->>'goal_stop_revision')
                     ) AS expected(scope_key,bound_revision)
                     LEFT JOIN LATERAL (
                        SELECT resulting_revision,stopped FROM public.v4_stop_commands stop
                        WHERE stop.workspace_id=work.workspace_id AND stop.scope_key=expected.scope_key
                            AND stop.created_at<=command.created_at
                        ORDER BY resulting_revision DESC LIMIT 1
                     ) AS prior_stop ON true
                     WHERE COALESCE(prior_stop.resulting_revision,1)::text IS DISTINCT FROM expected.bound_revision
                        OR prior_stop.stopped IS TRUE)
                 AND (command.result->'authorization'->>'expires_at')::timestamptz>command.created_at
                 AND alert.payload_sha256=encode(public.digest(alert.payload::text,'sha256'),'hex')
                 AND alert.payload=jsonb_build_object(
                     'schema_version','salience.p1-production-recovery-alert.v1','status','pending',
                     'work_id',work.work_id,'case_id',work.source_id,'source_id',work.source_id,
                     'intent_id',work.intent_id,'operation_id',work.operation_id,'reason',work.reason,
                     'state',work.state,'target_alias',work.target_alias,'deadline',command.result->'request'->>'deadline',
                     'recipient_profile_revision',work.recipient_profile_revision,
                     'policy_revision',command.result->'authorization'->'retry_policy_revision',
                     'evidence_sha256',command.result->'authorization'->>'alert_policy_sha256')
                 AND NOT EXISTS (
                     SELECT 1 FROM public.v4_production_effect_permit_claims claim
                     WHERE claim.operation_id=work.operation_id AND claim.generation=1)
                 AND NOT EXISTS (
                     SELECT 1 FROM public.v4_permit_claims claim WHERE claim.operation_id=work.operation_id)
           ), proofs AS (
               SELECT proof.*
               FROM public.v4_production_recovery_events proof
               JOIN opening ON (opening.work).work_id=proof.work_id
               WHERE proof.event_kind='preclaim_permit_resume'
           ), verified AS (
               SELECT opening.receipt, to_jsonb(proof) AS renewal
               FROM opening
               JOIN public.v4_production_effect_permits successor
                   ON successor.operation_id=(opening.work).operation_id AND successor.generation=2
                   AND successor.permit_id=(opening.pointer).current_permit_id
                   AND (opening.pointer).generation=2
               JOIN proofs proof ON proof.work_id=(opening.work).work_id
               JOIN public.v4_case_commands resume ON resume.id::text=proof.payload->>'command_id'
                   AND resume.case_id=(opening.work).source_id
                   AND resume.result=public.v4_p1_rollback_case_receipt_v3(
                       resume.case_id,2,'retry_due',false)
               JOIN public.v4_case_commands review ON review.id::text=resume.result->>'review_command_id'
                   AND review.case_id=resume.case_id
                   AND review.result=public.v4_p1_rollback_case_receipt_v3(
                       review.case_id,1,'awaiting_review',false)
               WHERE successor.recovery_command_id=proof.event_id
                 AND successor.approval_request_id=(opening.prior).approval_request_id
                 AND successor.route_fence_decision_id<>(opening.prior).route_fence_decision_id
                 AND successor.route_fence_decision_id::text<>resume.result->'authorization'->>'route_decision_id'
                 AND successor.request_snapshot->'workspace_stop_revision'=proof.payload->'workspace_stop_revision'
                 AND successor.request_snapshot->'goal_stop_revision'=proof.payload->'goal_stop_revision'
                 AND successor.issued_at>=proof.occurred_at AND proof.occurred_at>=resume.created_at
                 AND successor.expires_at<=least((successor.request_snapshot->>'execution_deadline')::timestamptz,
                     (proof.payload->>'renewal_expires_at')::timestamptz,
                     (resume.result->'authorization'->>'expires_at')::timestamptz)
                 AND (proof.payload->>'renewal_expires_at')::timestamptz>successor.issued_at
                 AND (resume.result->'authorization'->>'expires_at')::timestamptz>successor.issued_at
                 AND proof.payload->>'renewal_stage_decision_id' ~ '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$'
                 AND proof.payload->>'renewal_policy_decision_id' ~ '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$'
                 AND proof.payload->>'renewal_stage_decision_id'<>resume.result->'authorization'->>'stage_decision_id'
                 AND proof.payload->>'renewal_policy_decision_id'<>resume.result->'authorization'->>'policy_decision_id'
                 AND EXISTS (
                     SELECT 1 FROM public.publication_requests publication
                     JOIN public.publisher_capability_profiles profile ON profile.id=publication.publisher_capability_profile_id
                     JOIN public.publication_plans plan ON plan.publication_request_id=publication.id
                     WHERE publication.id::text=opening.receipt->>'publication_request_id'
                       AND publication.workspace_id=p_workspace_id AND plan.id::text=opening.receipt->>'publication_plan_id'
                       AND publication.publisher_id=successor.provider_id
                       AND profile.id::text=proof.payload->>'delivery_profile_id'
                       AND profile.profile_version=publication.capability_profile_version
                       AND profile.profile_version::text=proof.payload->>'delivery_profile_version'
                       AND profile.publisher_version=proof.payload->>'delivery_adapter_version'
                       AND plan.publisher_version=profile.publisher_version)
                 AND (successor.request_snapshot-ARRAY['generation','recovery_command_id',
                       'route_fence_decision_id','workspace_stop_revision','goal_stop_revision'])
                     =( (opening.prior).request_snapshot-ARRAY['generation','recovery_command_id',
                       'route_fence_decision_id','workspace_stop_revision','goal_stop_revision'])
                 AND resume.action='resume' AND resume.result->>'state'='resolved'
                 AND review.action='review_approve' AND review.result->>'state'='retry_due'
                 AND resume.result->>'approval_request_id'=opening.receipt->>'approval_request_id'
                 AND review.result->>'approval_request_id'=opening.receipt->>'approval_request_id'
                 AND proof.actor_id=resume.actor_id
                 AND proof.policy_decision_id::text=resume.result->'authorization'->>'policy_decision_id'
                 AND proof.event_key=concat('preclaim-permit-resume:',successor.operation_id)
                 AND proof.payload_sha256=encode(public.digest(proof.payload::text,'sha256'),'hex')
                 AND proof.payload @> jsonb_build_object(
                     'schema_version','salience.p1-preclaim-permit-resume.v1','command_id',resume.id,
                     'command_sha256',encode(public.digest(resume.result::text,'sha256'),'hex'),
                     'opening_command_id',(opening.work).opening_command_id,
                     'event_key',proof.event_key,'reason_code','expired_unclaimed_permit','state','resolved',
                     'work_id',proof.work_id,'case_id',resume.case_id,'source_id',resume.case_id,
                     'intent_id',successor.intent_id,'operation_id',successor.operation_id,
                     'request_fingerprint',(opening.work).request_fingerprint,
                     'predecessor_permit_id',(opening.prior).permit_id,'predecessor_generation',1,
                     'successor_permit_id',successor.permit_id,'approval_request_id',successor.approval_request_id,
                     'route_decision_id',successor.route_fence_decision_id,
                     'workspace_stop_revision',resume.result->'authorization'->'workspace_stop_revision',
                     'goal_stop_revision',resume.result->'authorization'->'goal_stop_revision',
                     'policy_decision_id',proof.policy_decision_id,
                     'stage_decision_id',resume.result->'authorization'->'stage_decision_id',
                     'case_revision',3,'work_source_revision',1,
                     'remote_identity_sha256',encode(public.digest(
                         successor.operation_id::text || ':' || (opening.pointer).idempotency_key,'sha256'),'hex'))
                 AND proof.payload @> (
                     SELECT jsonb_object_agg(binding.key,binding.value)
                     FROM jsonb_each((opening.prior).request_snapshot) binding
                     WHERE binding.key=ANY(ARRAY['external_effect_id','publication_request_id',
                       'publication_plan_id','publication_attempt_id','job_id','workflow_id','workflow_run_id',
                       'budget_reservation_id']))
                 AND NOT EXISTS (
                     SELECT 1 FROM public.v4_production_effect_permits later
                     WHERE later.operation_id=successor.operation_id AND later.generation>2)
               UNION ALL
               SELECT opening.receipt, NULL::jsonb FROM opening
               WHERE (opening.pointer).generation=1
                 AND (opening.pointer).current_permit_id=(opening.prior).permit_id
                 AND (opening.pointer).current_claim_id IS NULL
                 AND NOT EXISTS (SELECT 1 FROM proofs WHERE proofs.work_id=(opening.work).work_id)
                 AND NOT EXISTS (
                     SELECT 1 FROM public.v4_production_effect_permits later
                     WHERE later.operation_id=(opening.work).operation_id AND later.generation>1)
           )
           SELECT CASE WHEN count(*)=1 THEN jsonb_agg(jsonb_build_object(
               'opening',receipt,'renewal',renewal))->0 END FROM verified
           $$"""
    )
    op.execute(
        """CREATE FUNCTION public.v4_p1_recovery_rollback_inventory_v5(p_workspace_id uuid)
           RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER
           SET search_path = pg_catalog, public AS $$
           WITH prior AS (
               SELECT public.v4_p1_recovery_rollback_inventory_v4(p_workspace_id) AS inventory
           ), origins(work_id) AS (
               SELECT command.result->>'work_id' FROM public.v4_case_commands command
               JOIN public.v4_recovery_cases recovery ON recovery.id=command.case_id
               WHERE recovery.workspace_id=p_workspace_id AND command.action='open'
                 AND command.result->>'schema_version'='salience.p1-production-preclaim-case-opening.v1'
               UNION
               SELECT opened.payload->>'work_id' FROM public.v4_case_events opened
               JOIN public.v4_recovery_cases recovery ON recovery.id=opened.case_id
               WHERE recovery.workspace_id=p_workspace_id AND opened.action='opened'
                 AND opened.payload->>'schema_version'='salience.p1-production-preclaim-case-opening.v1'
               UNION
               SELECT audit.payload->>'work_id' FROM public.v4_cycle_events audit
               WHERE audit.workspace_id=p_workspace_id AND audit.kind='production_case_open'
                 AND audit.payload->>'schema_version'='salience.p1-production-preclaim-case-opening.v1'
               UNION
               SELECT work.work_id::text FROM public.v4_production_recovery_work work
               JOIN public.v4_production_recovery_events proof ON proof.work_id=work.work_id
               WHERE work.workspace_id=p_workspace_id AND proof.event_kind='preclaim_permit_resume'
           ), openings AS MATERIALIZED (
               SELECT work_id, public.v4_p1_rollback_preclaim_lineage_v5(p_workspace_id,work_id) AS lineage
               FROM origins
           ), aggregated AS (
               SELECT COALESCE(jsonb_agg(lineage ORDER BY work_id) FILTER (WHERE lineage IS NOT NULL),'[]') AS lineage,
                      COALESCE(jsonb_agg(jsonb_build_object(
                          'kind','production_preclaim_lineage_missing_or_mismatched','work_id',work_id)
                          ORDER BY work_id) FILTER (WHERE lineage IS NULL),'[]') AS blockers
               FROM openings
           )
           SELECT inventory || jsonb_build_object(
               'schema_version','salience.p1-recovery-rollback-inventory.v5',
               'migration_head','0058_p1_preclaim_rollback_v5',
               'relation_counts',(inventory->'relation_counts') || jsonb_build_object(
                   'production_preclaim_lineage',jsonb_array_length(lineage)),
               'preclaim_lineage',lineage,
               'blockers',(
                   SELECT COALESCE(jsonb_agg(item.value ORDER BY item.position),'[]')
                   FROM jsonb_array_elements(inventory->'blockers') WITH ORDINALITY AS item(value,position)
                   WHERE NOT (item.value->>'kind'='production_case_opening_missing_or_mismatched'
                     AND EXISTS (SELECT 1 FROM openings
                       WHERE lineage IS NOT NULL AND work_id=item.value->>'work_id'))
               ) || blockers)
           FROM prior CROSS JOIN aggregated
           $$"""
    )
    op.execute("REVOKE ALL ON FUNCTION public.v4_p1_recovery_rollback_inventory_v5(uuid) FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION public.v4_p1_rollback_preclaim_lineage_v5(uuid,text) FROM PUBLIC")


def downgrade() -> None:
    op.execute("DROP FUNCTION public.v4_p1_recovery_rollback_inventory_v5(uuid)")
    op.execute("DROP FUNCTION public.v4_p1_rollback_preclaim_lineage_v5(uuid,text)")
