"""Bind canonical production case-opening receipts in workspace rollback."""

from alembic import op


revision = "0055_p1_recovery_open_rollback_v4"
down_revision = "0054_p1_recovery_case_opening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE FUNCTION public.v4_p1_recovery_rollback_inventory_v4(p_workspace_id uuid)
           RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER
           SET search_path = pg_catalog, public AS $$
           WITH origins(work_id) AS (
               SELECT work.work_id::text FROM public.v4_production_recovery_work work
               WHERE work.workspace_id=p_workspace_id AND work.opening_command_id IS NOT NULL
               UNION
               SELECT command.result->>'work_id' FROM public.v4_case_commands command
               JOIN public.v4_recovery_cases recovery ON recovery.id=command.case_id
               WHERE recovery.workspace_id=p_workspace_id AND command.action='open'
               UNION
               SELECT opened.payload->>'work_id' FROM public.v4_case_events opened
               JOIN public.v4_recovery_cases recovery ON recovery.id=opened.case_id
               WHERE recovery.workspace_id=p_workspace_id AND opened.action='opened'
                 AND opened.payload->>'schema_version'='salience.p1-production-recovery-case-opening.v1'
               UNION
               SELECT audit.payload->>'work_id' FROM public.v4_cycle_events audit
               WHERE audit.workspace_id=p_workspace_id AND audit.kind='production_case_open'
           ), opening AS (
               SELECT origins.work_id, proof.receipt
               FROM origins LEFT JOIN public.v4_production_recovery_work work
                 ON work.work_id::text=origins.work_id AND work.workspace_id=p_workspace_id
               LEFT JOIN LATERAL (
                   SELECT CASE WHEN count(*) = 1 THEN jsonb_agg(command.result)->0 END AS receipt
                   FROM public.v4_case_commands command
                   JOIN public.v4_recovery_cases recovery ON recovery.id=command.case_id
                   JOIN public.v4_case_events opened ON opened.case_id=command.case_id
                    AND opened.action='opened' AND opened.case_revision=1
                    AND opened.actor_id=command.actor_id AND opened.payload=command.result
                   JOIN public.v4_cycle_events audit ON audit.workspace_id=work.workspace_id
                    AND audit.goal_id=work.goal_id AND audit.intent_id=work.intent_id
                    AND audit.cycle_id=work.cycle_id AND audit.subject_id=command.actor_id
                    AND audit.kind='production_case_open' AND audit.traceparent=opened.traceparent
                    AND audit.payload=command.result
                   JOIN public.v4_production_intent_enrollments enrollment
                     ON enrollment.intent_id=work.intent_id
                    AND enrollment.eligibility_revision=work.eligibility_revision
                    AND enrollment.workspace_id=work.workspace_id AND enrollment.goal_id=work.goal_id
                   JOIN public.v4_production_effect_permits permit
                     ON permit.permit_id::text=command.result->>'permit_id'
                    AND permit.operation_id=work.operation_id AND permit.intent_id=work.intent_id
                    AND permit.cycle_id=work.cycle_id AND permit.context_id=work.context_id
                    AND permit.workspace_id=work.workspace_id AND permit.goal_id=work.goal_id
                    AND permit.goal_revision=enrollment.goal_revision AND permit.subject_id=enrollment.subject_id
                    AND permit.subject_revision=enrollment.subject_revision
                    AND permit.policy_version_id=enrollment.policy_version_id
                    AND permit.policy_document_hash=enrollment.policy_document_hash
                    AND permit.artifact_sha256=enrollment.artifact_sha256
                    AND (permit.route_id,permit.slot_id,permit.owner_id,permit.fencing_epoch,
                         permit.route_manifest_sha256,permit.artifact_lock_sha256)
                        =(enrollment.route_id,enrollment.slot_id,enrollment.route_owner_id,enrollment.fencing_epoch,
                          enrollment.route_manifest_sha256,enrollment.external_lock_sha256)
                   JOIN public.v4_production_effect_permit_claims claim
                     ON claim.claim_id::text=command.result->>'claim_id'
                    AND claim.permit_id=permit.permit_id AND claim.operation_id=permit.operation_id
                    AND claim.generation=permit.generation
                    AND claim.claimed_at>=permit.issued_at AND claim.claimed_at<=command.created_at
                   JOIN public.external_effects effect
                     ON effect.id::text=permit.request_snapshot->>'external_effect_id'
                    AND effect.workspace_id=work.workspace_id AND effect.effect_type='publication'
                    AND effect.attempted_at BETWEEN claim.claimed_at AND command.created_at
                   JOIN public.v4_production_recovery_events alert
                     ON alert.event_id::text=command.result->>'alert_event_id' AND alert.work_id=work.work_id
                    AND alert.event_kind='alert_intent'
                    AND alert.event_key=concat('alert-intent:',work.work_id,':','initial')
                    AND (alert.route_id,alert.slot_id,alert.fencing_epoch)
                        =(enrollment.route_id,enrollment.slot_id,enrollment.fencing_epoch)
                   WHERE command.id=work.opening_command_id AND command.case_id=work.source_id
                     AND command.action='open' AND command.actor_id=work.owner_id
                     AND enrollment.source_owner_id=command.actor_id
                     AND recovery.workspace_id=work.workspace_id AND recovery.goal_id=work.goal_id
                     AND recovery.target_cycle_id=work.cycle_id AND recovery.context_id=work.context_id
                     AND recovery.operation_id=work.operation_id AND recovery.owner_id=work.owner_id
                     AND work.source_discriminant='cycle_case' AND work.case_revision=1
                     AND work.source_revision=1 AND work.deadline=recovery.deadline
                     AND recovery.kind='reconciliation' AND work.reason='unknown_effect'
                     AND work.state='reconciling' AND command.result->>'evidence_state' IN ('submitting','unknown')
                     AND command.fingerprint=encode(public.digest((command.result->'request')::text,'sha256'),'hex')
                     AND recovery.open_fingerprint=command.fingerprint
                     AND work.request_fingerprint=encode(public.digest(permit.request_snapshot::text,'sha256'),'hex')
                     AND work.target_alias=permit.provider_id AND work.remote_account_id=permit.account_id
                     AND NOT EXISTS (
                         SELECT 1 FROM public.v4_production_effect_permits later
                         WHERE later.operation_id=work.operation_id AND later.generation>permit.generation
                           AND later.issued_at<=command.created_at)
                     AND command.result->'request' @> jsonb_build_object(
                         'workspace_id',work.workspace_id,'actor_id',command.actor_id,'intent_id',work.intent_id,
                         'cycle_id',work.cycle_id,'context_id',work.context_id,'operation_id',work.operation_id,
                         'eligibility_revision',work.eligibility_revision,'generation',permit.generation,
                         'request_fingerprint',work.request_fingerprint,'idempotency_key',command.idempotency_key)
                     AND (command.result->'request'->>'deadline')::timestamptz=work.deadline
                     AND command.result @> jsonb_build_object(
                         'schema_version','salience.p1-production-recovery-case-opening.v1',
                         'command_id',command.id,'case_id',command.case_id,'work_id',work.work_id,
                         'workspace_id',work.workspace_id,'goal_id',work.goal_id,'intent_id',work.intent_id,
                         'cycle_id',work.cycle_id,'context_id',work.context_id,'operation_id',work.operation_id,
                         'actor_id',command.actor_id,'action','open','resulting_revision',1,
                         'state',work.state,'kind',recovery.kind,'failure_class',work.reason,
                         'eligibility_revision',work.eligibility_revision,'generation',permit.generation,
                         'request_fingerprint',work.request_fingerprint,
                         'external_effect_id',permit.request_snapshot->>'external_effect_id')
                     AND command.result->'authorization' @> jsonb_build_object(
                         'policy_version_id',enrollment.policy_version_id,
                         'policy_document_hash',enrollment.policy_document_hash,
                         'route_id',enrollment.route_id,'slot_id',enrollment.slot_id,
                         'fencing_epoch',enrollment.fencing_epoch,'policy_decision_id',alert.policy_decision_id,
                         'recipient_profile_revision',work.recipient_profile_revision)
                     AND NOT EXISTS (
                         SELECT 1 FROM unnest(ARRAY['policy_decision_id','stage_decision_id','route_decision_id']) key
                         WHERE (command.result->'authorization'->>key ~
                             '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$') IS DISTINCT FROM true)
                     AND NOT EXISTS (
                         SELECT 1 FROM unnest(ARRAY['workspace_stop_revision','goal_stop_revision',
                             'actor_revision','retry_policy_revision']) key
                         WHERE (command.result->'authorization'->>key ~ '^[1-9][0-9]*$') IS DISTINCT FROM true)
                     AND NOT EXISTS (
                         SELECT 1 FROM (VALUES
                             ('workspace',command.result->'authorization'->>'workspace_stop_revision'),
                             (concat('goal:',work.goal_id),command.result->'authorization'->>'goal_stop_revision')
                         ) AS expected(scope_key, bound_revision)
                         LEFT JOIN LATERAL (
                             SELECT resulting_revision, stopped FROM public.v4_stop_commands stop
                             WHERE stop.workspace_id=work.workspace_id AND stop.scope_key=expected.scope_key
                               AND stop.created_at<=command.created_at
                             ORDER BY resulting_revision DESC LIMIT 1
                         ) AS prior_stop ON true
                         WHERE COALESCE(prior_stop.resulting_revision,1)::text IS DISTINCT FROM expected.bound_revision
                            OR prior_stop.stopped IS TRUE)
                     AND (command.result->'authorization'->>'expires_at')::timestamptz>command.created_at
                     AND command.result->'authorization'->>'alert_policy_sha256' ~ '^[0-9a-f]{64}$'
                     AND alert.payload_sha256=encode(public.digest(alert.payload::text,'sha256'),'hex')
                     AND alert.payload=jsonb_build_object(
                         'schema_version','salience.p1-production-recovery-alert.v1','status','pending',
                         'work_id',work.work_id,'case_id',work.source_id,'source_id',work.source_id,
                         'intent_id',work.intent_id,'operation_id',work.operation_id,
                         'reason',work.reason,'state',work.state,'target_alias',work.target_alias,
                         'deadline',command.result->'request'->>'deadline',
                         'recipient_profile_revision',work.recipient_profile_revision,
                         'policy_revision',command.result->'authorization'->'retry_policy_revision',
                         'evidence_sha256',command.result->'authorization'->>'alert_policy_sha256')
               ) AS proof ON true
           ), opening_inventory AS (
               SELECT COALESCE(jsonb_agg(receipt ORDER BY work_id) FILTER (WHERE receipt IS NOT NULL),'[]') AS receipts,
                      COALESCE(jsonb_agg(jsonb_build_object(
                          'kind','production_case_opening_missing_or_mismatched','work_id',work_id
                      ) ORDER BY work_id) FILTER (WHERE receipt IS NULL),'[]') AS blockers
               FROM opening
           ), prior AS (
               SELECT public.v4_p1_recovery_rollback_inventory_v3(p_workspace_id) AS inventory
           )
           SELECT inventory || jsonb_build_object(
               'schema_version','salience.p1-recovery-rollback-inventory.v4',
               'migration_head','0055_p1_recovery_open_rollback_v4',
               'relation_counts',(inventory->'relation_counts') || jsonb_build_object(
                   'production_case_opening_receipts',jsonb_array_length(receipts)),
               'opening_receipts',receipts,
               'blockers',(inventory->'blockers') || blockers)
           FROM prior CROSS JOIN opening_inventory
           $$"""
    )
    op.execute("REVOKE ALL ON FUNCTION public.v4_p1_recovery_rollback_inventory_v4(uuid) FROM PUBLIC")


def downgrade() -> None:
    op.execute("DROP FUNCTION public.v4_p1_recovery_rollback_inventory_v4(uuid)")
