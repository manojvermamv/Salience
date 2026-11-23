"""Verify production case lineage without changing the v2 rollback ABI."""

from alembic import op


revision = "0053_p1_recovery_rollback_v3"
down_revision = "0052_p1_recovery_case_commands"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE FUNCTION public.v4_p1_rollback_case_receipt_v3(
               p_case_id uuid, p_revision integer, p_state text, p_archive boolean
           ) RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER
           SET search_path = pg_catalog, public AS $$
            SELECT CASE WHEN count(*) = 1 THEN jsonb_agg(cmd.result)->0 END
            FROM public.v4_case_commands cmd
            JOIN public.v4_recovery_cases recovery ON recovery.id = cmd.case_id
            JOIN public.v4_production_recovery_work work
              ON work.work_id::text = cmd.result->>'work_id'
             AND work.source_discriminant = 'cycle_case' AND work.source_id = cmd.case_id
             AND work.workspace_id = recovery.workspace_id AND work.goal_id = recovery.goal_id
             AND work.cycle_id = recovery.target_cycle_id AND work.context_id = recovery.context_id
             AND work.operation_id = recovery.operation_id AND work.owner_id = recovery.owner_id
            JOIN public.v4_production_intent_enrollments enrollment
              ON enrollment.intent_id = work.intent_id
             AND enrollment.eligibility_revision = work.eligibility_revision
             AND enrollment.workspace_id = work.workspace_id AND enrollment.goal_id = work.goal_id
            JOIN public.v4_cycles cycle ON cycle.id = work.cycle_id AND cycle.intent_id = work.intent_id
            JOIN public.v4_case_events event ON event.case_id = cmd.case_id
             AND event.actor_id = cmd.actor_id AND event.action = cmd.action AND event.payload = cmd.result
             AND event.case_revision = p_revision + CASE WHEN p_archive THEN 0 ELSE 1 END
            JOIN public.v4_cycle_events audit ON audit.workspace_id = work.workspace_id
             AND audit.goal_id = work.goal_id AND audit.intent_id = work.intent_id AND audit.cycle_id = work.cycle_id
             AND audit.subject_id = cmd.actor_id AND audit.kind = 'production_case_' || cmd.action
             AND audit.payload = cmd.result AND audit.traceparent = event.traceparent
            WHERE cmd.case_id = p_case_id
              AND cmd.result->>'schema_version' = 'salience.p1-production-recovery-case-command.v1'
              AND cmd.actor_id = recovery.owner_id
              AND cmd.fingerprint = encode(public.digest(jsonb_build_array(
                work.workspace_id, work.work_id, cmd.case_id, cmd.actor_id, p_revision,
                cmd.action, cmd.result->'request')::text, 'sha256'), 'hex')
              AND cmd.result @> jsonb_build_object(
                'command_id', cmd.id, 'case_id', cmd.case_id, 'workspace_id', work.workspace_id,
                'goal_id', work.goal_id, 'intent_id', work.intent_id, 'cycle_id', work.cycle_id,
                'context_id', work.context_id, 'operation_id', work.operation_id,
                'actor_id', cmd.actor_id, 'action', cmd.action,
                'work_source_revision', work.source_revision, 'source_revision', p_revision,
                'source_state', p_state, 'resulting_revision', event.case_revision,
                'request_fingerprint', work.request_fingerprint)
              AND work.source_revision <= p_revision
              AND cmd.result->'authorization' @> jsonb_build_object(
                'route_id', enrollment.route_id, 'slot_id', enrollment.slot_id,
                'fencing_epoch', enrollment.fencing_epoch, 'policy_version_id', enrollment.policy_version_id,
                'policy_document_hash', enrollment.policy_document_hash)
              AND NOT EXISTS (
                SELECT 1 FROM unnest(ARRAY['policy_decision_id','stage_decision_id','route_decision_id']) key
                WHERE (cmd.result->'authorization'->>key ~ '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$')
                  IS DISTINCT FROM true)
              AND NOT EXISTS (
                SELECT 1 FROM unnest(ARRAY['workspace_stop_revision','goal_stop_revision','actor_revision']) key
                WHERE (cmd.result->'authorization'->>key ~ '^[1-9][0-9]*$') IS DISTINCT FROM true)
              AND (cmd.result->'authorization'->>'expires_at')::timestamptz > cmd.created_at
              AND (
                (p_archive AND cmd.action = 'archive' AND p_state = 'terminal'
                  AND cmd.result->>'state' = 'terminal' AND cycle.state = 'closed'
                  AND cmd.result->>'disposition' = cycle.disposition
                  AND EXISTS (
                    SELECT 1 FROM public.v4_case_archives archive
                    WHERE archive.case_id = cmd.case_id AND archive.actor_id = cmd.actor_id
                      AND archive.id::text = cmd.result->>'archive_id'
                      AND archive.reason = cmd.result->'request'->>'reason'
                      AND archive.evidence = jsonb_build_object(
                        'sha256', cmd.result->'request'->>'evidence_sha256')
                      AND archive.retain_until = (cmd.result->'request'->>'retain_until')::timestamptz))
                OR (NOT p_archive AND (
                  (cmd.action = 'review_approve' AND p_state = 'awaiting_review'
                    AND cmd.result->>'state' = 'retry_due')
                  OR (cmd.action = 'resume' AND p_state = 'retry_due'
                    AND cmd.result->>'state' = 'resolved')
                  OR (cmd.action IN ('review_reject', 'terminalize')
                    AND p_state IN ('awaiting_review','retry_due','reconciling','rework_due','resolved','suspended')
                    AND (cmd.action <> 'review_reject' OR p_state = 'awaiting_review')
                    AND cmd.result->>'state' = 'terminal' AND cycle.state = 'closed'
                    AND cmd.result->>'disposition' = cycle.disposition)))
              )
           $$"""
    )
    op.execute(
        """CREATE FUNCTION public.v4_p1_rollback_case_lineage_v3(p_workspace_id uuid, p_work_id uuid)
           RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER
           SET search_path = pg_catalog, public AS $$
           DECLARE
               work_row public.v4_production_recovery_work%ROWTYPE;
               case_row public.v4_recovery_cases%ROWTYPE;
               prior_revision integer;
               prior_state text;
               command_receipt jsonb;
               receipts jsonb := '[]'::jsonb;
               archive_record jsonb;
               archive_receipt jsonb;
           BEGIN
               SELECT work.* INTO work_row
               FROM public.v4_production_recovery_work work
               JOIN public.v4_production_intent_enrollments enrollment
                 ON enrollment.intent_id = work.intent_id AND enrollment.eligibility_revision = work.eligibility_revision
                AND enrollment.workspace_id = work.workspace_id AND enrollment.goal_id = work.goal_id
               JOIN public.v4_cycle_intents intent ON intent.id = work.intent_id AND intent.goal_id = work.goal_id
                AND intent.goal_revision = enrollment.goal_revision AND intent.eligibility_revision = work.eligibility_revision
               JOIN public.v4_goals goal ON goal.id = work.goal_id AND goal.workspace_id = work.workspace_id
                AND goal.revision = enrollment.goal_revision
               JOIN public.v4_cycles cycle ON cycle.id = work.cycle_id AND cycle.intent_id = intent.id
                AND cycle.context_id = work.context_id AND cycle.operation_id = work.operation_id
               WHERE work.work_id = p_work_id AND work.workspace_id = p_workspace_id
                 AND work.source_discriminant = 'cycle_case' AND work.source_revision = work.case_revision;
               IF NOT FOUND THEN RETURN NULL; END IF;

               SELECT recovery.* INTO case_row FROM public.v4_recovery_cases recovery
               WHERE recovery.id = work_row.source_id AND recovery.workspace_id = p_workspace_id
                 AND recovery.goal_id = work_row.goal_id AND recovery.target_cycle_id = work_row.cycle_id
                 AND recovery.context_id = work_row.context_id AND recovery.operation_id = work_row.operation_id
                 AND recovery.owner_id = work_row.owner_id AND recovery.failure_class = work_row.reason
                 AND recovery.deadline = work_row.deadline;
               IF NOT FOUND OR case_row.revision < work_row.case_revision
                   OR case_row.revision - work_row.case_revision > case_row.max_actions + 2
               THEN RETURN NULL; END IF;

               prior_state := work_row.state;
               FOR prior_revision IN work_row.case_revision..case_row.revision - 1 LOOP
                   command_receipt := public.v4_p1_rollback_case_receipt_v3(
                       case_row.id, prior_revision, prior_state, false);
                   IF command_receipt IS NULL AND prior_state IN (
                       'awaiting_review','retry_due','reconciling','rework_due'
                   ) THEN
                       -- A pre-0052 deadline has a typed recovery event and canonical case event.
                       SELECT CASE WHEN count(*) = 1 THEN jsonb_agg(jsonb_build_object(
                           'action', cmd.action, 'command_id', cmd.id, 'state', 'suspended',
                           'source_revision', prior_revision, 'resulting_revision', transition.case_revision,
                           'recovery_event', to_jsonb(wake), 'case_event', to_jsonb(transition)))->0 END
                       INTO command_receipt
                       FROM public.v4_production_recovery_work due_work
                       JOIN public.v4_production_recovery_events wake ON wake.work_id = due_work.work_id
                       JOIN public.v4_case_commands cmd ON cmd.id::text = wake.payload->>'command_id'
                        AND cmd.idempotency_key = wake.event_key AND cmd.case_id = due_work.source_id
                       JOIN public.v4_case_events transition ON transition.case_id = cmd.case_id
                        AND transition.actor_id = cmd.actor_id AND transition.case_revision = prior_revision + 1
                       WHERE due_work.source_discriminant = 'cycle_case' AND due_work.source_id = case_row.id
                         AND due_work.workspace_id = p_workspace_id AND due_work.goal_id = work_row.goal_id
                         AND due_work.case_revision = prior_revision AND due_work.source_revision = prior_revision
                         AND due_work.owner_id = work_row.owner_id AND due_work.operation_id = work_row.operation_id
                         AND due_work.intent_id = work_row.intent_id AND due_work.cycle_id = work_row.cycle_id
                         AND due_work.context_id = work_row.context_id AND due_work.state = prior_state
                         AND wake.event_kind = 'escalation' AND wake.payload @> jsonb_build_object(
                             'schema_version', 'salience.p1-production-recovery-deadline.v1',
                             'status', 'deadline_suspend', 'result_class', 'suspended', 'attempt', prior_revision + 1,
                             'work_id', due_work.work_id, 'source_id', case_row.id, 'case_id', case_row.id,
                             'operation_id', due_work.operation_id, 'intent_id', due_work.intent_id,
                             'event_key', wake.event_key, 'policy_decision_id', wake.policy_decision_id)
                         AND cmd.action = 'deadline_suspend'
                         AND cmd.fingerprint = encode(public.digest(wake.event_key, 'sha256'), 'hex')
                         AND cmd.result @> jsonb_build_object('resulting_revision', prior_revision + 1)
                         AND transition.action = 'escalated' AND transition.payload @> jsonb_build_object(
                             'state', 'suspended', 'case_id', case_row.id)
                         AND wake.occurred_at >= due_work.deadline
                         AND (wake.payload->>'deadline')::timestamptz = due_work.deadline
                         AND (
                           NOT (cmd.result ? 'schema_version')
                           OR (
                             cmd.result @> jsonb_build_object(
                               'schema_version', 'salience.p1-production-recovery-deadline.v1',
                               'work_id', due_work.work_id, 'source_revision', prior_revision,
                               'source_state', prior_state, 'operation_id', due_work.operation_id,
                               'context_id', due_work.context_id)
                             AND transition.payload = cmd.result
                             AND wake.actor_id = cmd.actor_id AND cmd.actor_id = due_work.owner_id
                             AND cmd.created_at >= due_work.deadline
                           )
                         );
                   END IF;
                   IF command_receipt IS NULL THEN RETURN NULL; END IF;
                   prior_state := command_receipt->>'state';
                   receipts := receipts || jsonb_build_array(command_receipt);
               END LOOP;
               IF prior_state IS DISTINCT FROM case_row.state THEN RETURN NULL; END IF;

               SELECT to_jsonb(archive) INTO archive_record
               FROM public.v4_case_archives archive WHERE archive.case_id = case_row.id;
               IF archive_record IS NOT NULL OR EXISTS (
                   SELECT 1 FROM public.v4_case_commands cmd
                   WHERE cmd.case_id = case_row.id AND cmd.action = 'archive'
               ) OR EXISTS (
                   SELECT 1 FROM public.v4_case_events event
                   WHERE event.case_id = case_row.id AND event.action IN ('archive', 'archived')
               ) THEN
                   archive_receipt := public.v4_p1_rollback_case_receipt_v3(
                       case_row.id, case_row.revision, prior_state, true);
                   IF archive_receipt IS NULL THEN RETURN NULL; END IF;
                   archive_record := jsonb_build_object('record', archive_record, 'command_receipt', archive_receipt);
               END IF;
               RETURN jsonb_build_object(
                   'work_id', work_row.work_id, 'case_id', case_row.id, 'source_revision', work_row.case_revision,
                   'current_revision', case_row.revision, 'current_state', case_row.state,
                   'command_receipts', receipts, 'archive', archive_record);
           END
           $$"""
    )
    op.execute(
        """CREATE FUNCTION public.v4_p1_recovery_rollback_inventory_v3(p_workspace_id uuid)
           RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER
           SET search_path = pg_catalog, public AS $$
           DECLARE
               inventory jsonb := public.v4_p1_recovery_rollback_inventory_v2(p_workspace_id);
               lineage jsonb := '[]'::jsonb;
               blockers jsonb;
               proof jsonb;
               work_id uuid;
           BEGIN
               blockers := inventory->'blockers';
               FOR work_id IN
                   SELECT work.work_id FROM public.v4_production_recovery_work work
                   WHERE work.workspace_id = p_workspace_id AND work.source_discriminant = 'cycle_case'
                   ORDER BY work.source_id, work.source_revision, work.work_id
               LOOP
                   proof := public.v4_p1_rollback_case_lineage_v3(p_workspace_id, work_id);
                   IF proof IS NULL THEN
                       blockers := blockers || jsonb_build_array(jsonb_build_object(
                           'kind', 'production_case_lineage_missing_or_mismatched', 'work_id', work_id));
                   ELSE
                       lineage := lineage || jsonb_build_array(proof);
                       -- Replace only the source check for the work whose old join and
                       -- advanced revision are both independently proved.
                       SELECT COALESCE(jsonb_agg(item.value ORDER BY item.position), '[]'::jsonb) INTO blockers
                       FROM jsonb_array_elements(blockers) WITH ORDINALITY AS item(value, position)
                       WHERE NOT item.value @> jsonb_build_object(
                           'kind', 'recovery_source_join_missing_or_mismatched',
                           'work_id', work_id, 'source_discriminant', 'cycle_case');
                   END IF;
               END LOOP;
               RETURN inventory || jsonb_build_object(
                   'schema_version', 'salience.p1-recovery-rollback-inventory.v3',
                   'migration_head', '0053_p1_recovery_rollback_v3',
                   'relation_counts', (inventory->'relation_counts') || jsonb_build_object(
                       'production_case_lineage', jsonb_array_length(lineage)),
                   'case_lineage', lineage,
                   'blockers', blockers);
           END
           $$"""
    )
    for function in (
        "v4_p1_rollback_case_receipt_v3(uuid,integer,text,boolean)",
        "v4_p1_rollback_case_lineage_v3(uuid,uuid)",
        "v4_p1_recovery_rollback_inventory_v3(uuid)",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION public.{function} FROM PUBLIC")


def downgrade() -> None:
    op.execute("DROP FUNCTION public.v4_p1_recovery_rollback_inventory_v3(uuid)")
    op.execute("DROP FUNCTION public.v4_p1_rollback_case_lineage_v3(uuid,uuid)")
    op.execute("DROP FUNCTION public.v4_p1_rollback_case_receipt_v3(uuid,integer,text,boolean)")
