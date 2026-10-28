"""Add the self-discovered, read-only production recovery rollback inventory."""

from alembic import op


revision = "0051_p1_recovery_rollback_v2"
down_revision = "0050_p1_production_dispatch_reissue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The validator is the existing schedule authority. Its only row lock is
    # taken by callers that are validating a mutable candidate; finalization
    # already locks the header explicitly before calling it. Removing this
    # validator-local lock permits the complete read-only inventory to reuse
    # all existing page, history, binding, source, and approval checks.
    op.execute(
        """DO $$
        DECLARE
            definition text;
            original_definition text;
        BEGIN
            original_definition := pg_get_functiondef(
                'public.v4_validate_production_schedule_inventory_v1(uuid)'::regprocedure
            );
            definition := replace(
                original_definition,
                'WHERE inventory_id = p_inventory_id FOR UPDATE;',
                'WHERE inventory_id = p_inventory_id;'
            );
            IF definition = original_definition
               OR position('FOR UPDATE' IN definition) > 0 THEN
                RAISE EXCEPTION
                    'failed to remove the validator-local schedule inventory lock';
            END IF;
            EXECUTE definition;
        END
        $$"""
    )

    op.execute(
        """CREATE FUNCTION public.v4_p1_recovery_rollback_inventory_v2(
            p_workspace_id uuid
        ) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public AS $$
        DECLARE
            result jsonb;
            blockers jsonb := '[]'::jsonb;
            schedule_ids uuid[] := ARRAY[]::uuid[];
            schedule_id uuid;
            enrollment_row public.v4_production_intent_enrollments%ROWTYPE;
            recovery_row public.v4_production_recovery_work%ROWTYPE;
            event_row public.v4_production_recovery_events%ROWTYPE;
            permit_row public.v4_production_effect_permits%ROWTYPE;
            claim_row public.v4_production_effect_permit_claims%ROWTYPE;
            dispatch_row public.v4_production_current_dispatch%ROWTYPE;
            hold_row public.v4_legacy_publication_recovery_holds%ROWTYPE;
            liability_snapshot jsonb;
            liabilities jsonb := '[]'::jsonb;
            fixture_rows jsonb;
            enrollment_rows jsonb;
            recovery_rows jsonb;
            event_rows jsonb;
            permit_rows jsonb;
            claim_rows jsonb;
            pointer_rows jsonb;
            inventory_rows jsonb;
            member_rows jsonb;
            page_rows jsonb;
            binding_rows jsonb;
            approval_rows jsonb;
            route_rows jsonb;
            source_rows jsonb;
            reason_counts jsonb;
            relation_counts jsonb;
        BEGIN
            IF p_workspace_id IS NULL THEN
                RAISE EXCEPTION 'rollback inventory requires an exact workspace UUID';
            END IF;

            SELECT COALESCE(array_agg(scope.inventory_id ORDER BY scope.inventory_id),
                            ARRAY[]::uuid[])
            INTO schedule_ids
            FROM (
                SELECT DISTINCT binding.inventory_id
                FROM public.v4_production_schedule_source_bindings binding
                WHERE binding.workspace_id = p_workspace_id
                UNION
                SELECT DISTINCT member.inventory_id
                FROM public.v4_production_schedule_inventory_members member
                WHERE member.member_snapshot->>'workspace_id' = p_workspace_id::text
            ) scope;

            FOR schedule_id IN SELECT unnest(schedule_ids) LOOP
                IF NOT EXISTS (
                    SELECT 1
                    FROM public.v4_production_schedule_inventories inventory
                    WHERE inventory.inventory_id = schedule_id
                      AND inventory.finalized_at IS NOT NULL
                ) THEN
                    blockers := blockers || jsonb_build_array(jsonb_build_object(
                        'kind', 'schedule_inventory_not_finalized',
                        'inventory_id', schedule_id));
                END IF;
                BEGIN
                    PERFORM public.v4_validate_production_schedule_inventory_v1(
                        schedule_id
                    );
                EXCEPTION WHEN OTHERS THEN
                    blockers := blockers || jsonb_build_array(jsonb_build_object(
                        'kind', 'schedule_inventory_invalid',
                        'inventory_id', schedule_id,
                        'detail', SQLERRM));
                END;
            END LOOP;

            FOR enrollment_row IN
                SELECT enrollment.*
                FROM public.v4_production_intent_enrollments enrollment
                WHERE enrollment.workspace_id = p_workspace_id
                ORDER BY enrollment.intent_id, enrollment.eligibility_revision
            LOOP
                IF NOT EXISTS (
                    SELECT 1
                    FROM public.v4_cycle_intents intent
                    JOIN public.v4_goals goal
                      ON goal.id = enrollment_row.goal_id
                     AND goal.workspace_id = p_workspace_id
                    JOIN public.v4_goal_revisions goal_revision
                      ON goal_revision.goal_id = enrollment_row.goal_id
                     AND goal_revision.revision = enrollment_row.goal_revision
                    JOIN public.identity_subjects source_owner
                      ON source_owner.id = enrollment_row.source_owner_id
                     AND source_owner.workspace_id = p_workspace_id
                    JOIN public.identity_subjects subject
                      ON subject.id = enrollment_row.subject_id
                     AND subject.workspace_id = p_workspace_id
                     AND subject.revision = enrollment_row.subject_revision
                     AND subject.enabled IS TRUE
                    JOIN public.identity_subjects route_owner
                      ON route_owner.id = enrollment_row.route_owner_id
                     AND route_owner.workspace_id = p_workspace_id
                    JOIN public.policy_versions policy
                      ON policy.id = enrollment_row.policy_version_id
                     AND policy.workspace_id = p_workspace_id
                     AND policy.status = 'active'
                     AND policy.document_hash = enrollment_row.policy_document_hash
                    JOIN public.v4_stop_scopes workspace_stop
                      ON workspace_stop.workspace_id = p_workspace_id
                     AND workspace_stop.scope_key = 'workspace'
                     AND workspace_stop.revision = enrollment_row.workspace_stop_revision
                     AND workspace_stop.stopped IS FALSE
                    JOIN public.v4_stop_scopes goal_stop
                      ON goal_stop.workspace_id = p_workspace_id
                     AND goal_stop.scope_key = 'goal:' || enrollment_row.goal_id::text
                     AND goal_stop.revision = enrollment_row.goal_stop_revision
                     AND goal_stop.stopped IS FALSE
                    WHERE intent.id = enrollment_row.intent_id
                      AND intent.goal_id = enrollment_row.goal_id
                      AND intent.goal_revision = enrollment_row.goal_revision
                      AND intent.eligibility_revision = enrollment_row.eligibility_revision
                      AND goal.revision = enrollment_row.goal_revision
                      AND (SELECT count(*)
                           FROM public.permission_grants grant_row
                           WHERE grant_row.id IN (
                               SELECT value::uuid
                               FROM jsonb_array_elements_text(enrollment_row.grant_ids)
                                    grant_ids(value)
                           )
                             AND grant_row.workspace_id = p_workspace_id
                             AND grant_row.principal_type = 'identity'
                             AND grant_row.principal_id = enrollment_row.subject_id::text
                             AND grant_row.effect = 'allow'
                             AND (grant_row.expires_at IS NULL
                                  OR grant_row.expires_at > clock_timestamp()))
                          = jsonb_array_length(enrollment_row.grant_ids)
                ) THEN
                    blockers := blockers || jsonb_build_array(jsonb_build_object(
                        'kind', 'enrollment_binding_stale_or_unresolved',
                        'intent_id', enrollment_row.intent_id,
                        'eligibility_revision', enrollment_row.eligibility_revision));
                END IF;
            END LOOP;

            FOR recovery_row IN
                SELECT work.*
                FROM public.v4_production_recovery_work work
                WHERE work.workspace_id = p_workspace_id
                ORDER BY work.source_discriminant, work.source_id,
                         work.source_revision, work.work_id
            LOOP
                IF NOT EXISTS (
                    SELECT 1
                    FROM public.v4_production_intent_enrollments enrollment
                    JOIN public.v4_cycle_intents intent
                      ON intent.id = recovery_row.intent_id
                     AND intent.goal_id = recovery_row.goal_id
                     AND intent.goal_revision = enrollment.goal_revision
                     AND intent.eligibility_revision = recovery_row.eligibility_revision
                    JOIN public.v4_goals goal
                      ON goal.id = recovery_row.goal_id
                     AND goal.workspace_id = p_workspace_id
                    WHERE enrollment.intent_id = recovery_row.intent_id
                      AND enrollment.eligibility_revision = recovery_row.eligibility_revision
                      AND enrollment.workspace_id = p_workspace_id
                      AND enrollment.goal_id = recovery_row.goal_id
                      AND goal.revision = enrollment.goal_revision
                      AND (
                        (recovery_row.source_discriminant = 'cycle_case'
                         AND EXISTS (
                             SELECT 1 FROM public.v4_recovery_cases recovery_case
                             WHERE recovery_case.id = recovery_row.source_id
                               AND recovery_case.workspace_id = p_workspace_id
                               AND recovery_case.goal_id = recovery_row.goal_id
                               AND recovery_case.target_cycle_id = recovery_row.cycle_id
                               AND recovery_case.context_id = recovery_row.context_id
                               AND recovery_case.operation_id = recovery_row.operation_id
                               AND recovery_case.revision = recovery_row.case_revision
                         ))
                        OR
                        (recovery_row.source_discriminant = 'pre_admission_intent_case'
                         AND recovery_row.source_id = recovery_row.intent_id
                         AND recovery_row.source_revision = recovery_row.eligibility_revision
                         AND EXISTS (
                             SELECT 1 FROM public.v4_admissions admission
                             WHERE admission.intent_id = recovery_row.intent_id
                               AND admission.eligibility_revision = recovery_row.eligibility_revision
                               AND admission.disposition IN ('deferred','review_required')
                         ))
                        OR
                        (recovery_row.source_discriminant = 'production_publication_hold'
                         AND recovery_row.source_id = recovery_row.operation_id
                         AND EXISTS (
                             SELECT 1
                             FROM public.v4_production_current_dispatch dispatch
                             JOIN public.v4_production_effect_permits permit
                               ON permit.permit_id = dispatch.current_permit_id
                              AND permit.operation_id = dispatch.operation_id
                              AND permit.generation = dispatch.generation
                             WHERE dispatch.operation_id = recovery_row.operation_id
                               AND permit.workspace_id = p_workspace_id
                               AND permit.intent_id = recovery_row.intent_id
                               AND permit.goal_id = recovery_row.goal_id
                               AND permit.cycle_id = recovery_row.cycle_id
                               AND permit.context_id = recovery_row.context_id
                         ))
                      )
                ) THEN
                    blockers := blockers || jsonb_build_array(jsonb_build_object(
                        'kind', 'recovery_source_join_missing_or_mismatched',
                        'work_id', recovery_row.work_id,
                        'source_discriminant', recovery_row.source_discriminant,
                        'source_id', recovery_row.source_id));
                END IF;
            END LOOP;

            FOR permit_row IN
                SELECT permit.*
                FROM public.v4_production_effect_permits permit
                LEFT JOIN public.v4_cycle_intents intent
                  ON intent.id = permit.intent_id
                LEFT JOIN public.v4_production_intent_enrollments enrollment
                  ON enrollment.intent_id = permit.intent_id
                 AND enrollment.eligibility_revision = intent.eligibility_revision
                 AND enrollment.workspace_id = permit.workspace_id
                 AND enrollment.goal_id = permit.goal_id
                WHERE permit.workspace_id = p_workspace_id
                  AND (
                    intent.id IS NULL
                    OR enrollment.intent_id IS NULL
                    OR enrollment.goal_revision IS DISTINCT FROM permit.goal_revision
                    OR enrollment.subject_id IS DISTINCT FROM permit.subject_id
                    OR enrollment.subject_revision IS DISTINCT FROM permit.subject_revision
                    OR enrollment.route_id IS DISTINCT FROM permit.route_id
                    OR enrollment.slot_id IS DISTINCT FROM permit.slot_id
                    OR enrollment.route_owner_id IS DISTINCT FROM permit.owner_id
                    OR enrollment.fencing_epoch IS DISTINCT FROM permit.fencing_epoch
                    OR enrollment.route_manifest_sha256 IS DISTINCT FROM
                       permit.route_manifest_sha256
                    OR enrollment.external_lock_sha256 IS DISTINCT FROM
                       permit.artifact_lock_sha256
                  )
                ORDER BY permit.operation_id, permit.generation, permit.permit_id
            LOOP
                blockers := blockers || jsonb_build_array(jsonb_build_object(
                    'kind', 'production_permit_enrollment_route_or_owner_mismatch',
                    'permit_id', permit_row.permit_id,
                    'operation_id', permit_row.operation_id,
                    'intent_id', permit_row.intent_id,
                    'generation', permit_row.generation));
            END LOOP;

            FOR claim_row IN
                SELECT claim.*
                FROM public.v4_production_effect_permit_claims claim
                JOIN public.v4_production_effect_permits permit
                  ON permit.permit_id = claim.permit_id
                 AND permit.operation_id = claim.operation_id
                 AND permit.generation = claim.generation
                WHERE permit.workspace_id = p_workspace_id
                  AND claim.route_fence_decision_id IS DISTINCT FROM
                      permit.route_fence_decision_id
                ORDER BY claim.operation_id, claim.generation, claim.claimed_at,
                         claim.claim_id
            LOOP
                blockers := blockers || jsonb_build_array(jsonb_build_object(
                    'kind', 'production_permit_claim_mismatch',
                    'claim_id', claim_row.claim_id,
                    'permit_id', claim_row.permit_id,
                    'operation_id', claim_row.operation_id,
                    'generation', claim_row.generation));
            END LOOP;

            FOR dispatch_row IN
                SELECT dispatch.*
                FROM public.v4_production_current_dispatch dispatch
                JOIN public.v4_production_effect_permits permit
                  ON permit.permit_id = dispatch.current_permit_id
                 AND permit.operation_id = dispatch.operation_id
                 AND permit.generation = dispatch.generation
                WHERE permit.workspace_id = p_workspace_id
                  AND dispatch.current_claim_id IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1
                      FROM public.v4_production_effect_permit_claims claim
                      WHERE claim.claim_id = dispatch.current_claim_id
                        AND claim.permit_id = permit.permit_id
                        AND claim.operation_id = permit.operation_id
                        AND claim.generation = permit.generation
                        AND claim.route_fence_decision_id = permit.route_fence_decision_id
                  )
                ORDER BY dispatch.operation_id, dispatch.generation
            LOOP
                blockers := blockers || jsonb_build_array(jsonb_build_object(
                    'kind', 'production_current_pointer_claim_mismatch',
                    'operation_id', dispatch_row.operation_id,
                    'current_permit_id', dispatch_row.current_permit_id,
                    'current_claim_id', dispatch_row.current_claim_id,
                    'generation', dispatch_row.generation));
            END LOOP;

            FOR event_row IN
                SELECT event.*
                FROM public.v4_production_recovery_events event
                JOIN public.v4_production_recovery_work work
                  ON work.work_id = event.work_id
                WHERE work.workspace_id = p_workspace_id
                  AND (
                    event.payload_sha256 IS DISTINCT FROM
                      encode(public.digest(event.payload::text, 'sha256'), 'hex')
                    OR (event.event_kind = 'same_operation_no_effect_reissue' AND (
                         event.payload->>'event_key' IS DISTINCT FROM event.event_key
                         OR event.payload->>'policy_decision_id' IS DISTINCT FROM
                            event.policy_decision_id::text
                         OR event.payload->>'stage_decision_id' IS NULL
                         OR event.payload->>'route_decision_id' IS NULL
                         OR event.payload->>'workspace_stop_revision' !~ '^[1-9][0-9]*$'
                         OR event.payload->>'goal_stop_revision' !~ '^[1-9][0-9]*$'))
                    OR (event.event_kind = 'escalation' AND (
                         event.payload->>'schema_version' IS DISTINCT FROM
                            'salience.p1-production-recovery-deadline.v1'
                         OR event.payload->>'event_key' IS DISTINCT FROM event.event_key
                         OR event.payload->>'policy_decision_id' IS DISTINCT FROM
                            event.policy_decision_id::text
                         OR event.payload->>'route_decision_id' IS NULL
                         OR (event.payload->>'status' = 'deadline_suspend'
                             AND event.payload->>'command_id' IS NULL)))
                  )
                ORDER BY event.event_id
            LOOP
                blockers := blockers || jsonb_build_array(jsonb_build_object(
                    'kind', 'recovery_event_digest_or_decision_evidence_mismatch',
                    'event_id', event_row.event_id,
                    'work_id', event_row.work_id,
                    'event_key', event_row.event_key));
            END LOOP;

            FOR recovery_row IN
                SELECT work.*
                FROM public.v4_production_recovery_work work
                WHERE work.workspace_id = p_workspace_id
                  AND work.source_discriminant = 'production_publication_hold'
                  AND (
                    work.snapshot_version IS DISTINCT FROM
                      'salience.p1-production-publication-liability.v1'
                    OR work.snapshot_sha256 IS DISTINCT FROM
                      encode(public.digest(work.snapshot::text, 'sha256'), 'hex')
                    OR jsonb_typeof(work.snapshot) IS DISTINCT FROM 'object'
                    OR NOT (work.snapshot ?& ARRAY[
                       'production_intent_enrollment','goal','intent','cycle','context','job',
                       'production_current_dispatch','production_permits',
                       'production_permit_claims','publication_request','publication_plan',
                       'publication_attempt','external_effect','budget_reservation','output',
                       'remote_publication_receipts','publication_status_events','cycle_events',
                       'cycle_outbox','job_checkpoints','audit_events','provenance_records']::text[])
                    OR (work.snapshot - ARRAY[
                       'production_intent_enrollment','goal','intent','cycle','context','job',
                       'production_current_dispatch','production_permits',
                       'production_permit_claims','publication_request','publication_plan',
                       'publication_attempt','external_effect','budget_reservation','output',
                       'remote_publication_receipts','publication_status_events','cycle_events',
                       'cycle_outbox','job_checkpoints','audit_events','provenance_records']::text[])
                       <> '{}'::jsonb
                  )
                ORDER BY work.operation_id, work.work_id
            LOOP
                blockers := blockers || jsonb_build_array(jsonb_build_object(
                    'kind', 'production_snapshot_contract_or_fingerprint_mismatch',
                    'work_id', recovery_row.work_id,
                    'operation_id', recovery_row.operation_id,
                    'reason', recovery_row.reason));
            END LOOP;

            FOR recovery_row IN
                SELECT work.*
                FROM public.v4_production_recovery_work work
                WHERE work.workspace_id = p_workspace_id
                  AND work.source_discriminant = 'production_publication_hold'
                ORDER BY work.operation_id, work.reason, work.work_id
            LOOP
                BEGIN
                    liability_snapshot :=
                        public.v4_production_recovery_publication_snapshot_v1(
                            recovery_row.operation_id
                        );
                    IF liability_snapshot IS NULL
                       OR liability_snapshot IS DISTINCT FROM recovery_row.snapshot THEN
                        blockers := blockers || jsonb_build_array(jsonb_build_object(
                            'kind', 'production_liability_changed',
                            'operation_id', recovery_row.operation_id,
                            'work_id', recovery_row.work_id));
                    END IF;
                    liabilities := liabilities || jsonb_build_array(jsonb_build_object(
                        'kind', 'production',
                        'work_id', recovery_row.work_id,
                        'operation_id', recovery_row.operation_id,
                        'reason', recovery_row.reason,
                        'snapshot_version', recovery_row.snapshot_version,
                        'snapshot_sha256', recovery_row.snapshot_sha256,
                        'source_fingerprint', recovery_row.source_fingerprint,
                        'stored_snapshot', recovery_row.snapshot,
                        'current_snapshot', liability_snapshot,
                        'current_sha256', CASE WHEN liability_snapshot IS NULL THEN NULL
                            ELSE encode(public.digest(liability_snapshot::text, 'sha256'), 'hex')
                            END));
                EXCEPTION WHEN OTHERS THEN
                    blockers := blockers || jsonb_build_array(jsonb_build_object(
                        'kind', 'production_liability_read_failed',
                        'operation_id', recovery_row.operation_id,
                        'work_id', recovery_row.work_id,
                        'detail', SQLERRM));
                    liabilities := liabilities || jsonb_build_array(jsonb_build_object(
                        'kind', 'production',
                        'work_id', recovery_row.work_id,
                        'operation_id', recovery_row.operation_id,
                        'reason', recovery_row.reason,
                        'snapshot_version', recovery_row.snapshot_version,
                        'snapshot_sha256', recovery_row.snapshot_sha256,
                        'source_fingerprint', recovery_row.source_fingerprint,
                        'stored_snapshot', recovery_row.snapshot,
                        'current_snapshot', NULL,
                        'current_sha256', NULL));
                END;
            END LOOP;

            FOR hold_row IN
                SELECT hold.*
                FROM public.v4_legacy_publication_recovery_holds hold
                WHERE hold.workspace_id = p_workspace_id
                  AND (
                    hold.liability_snapshot_version IS DISTINCT FROM
                       'salience.publication-liability.v1'
                    OR hold.liability_snapshot_v1 IS NULL
                    OR hold.liability_fingerprint_v1 IS DISTINCT FROM
                       encode(public.digest(hold.liability_snapshot_v1::text, 'sha256'), 'hex')
                    OR jsonb_typeof(hold.liability_snapshot_v1) IS DISTINCT FROM 'object'
                    OR NOT (hold.liability_snapshot_v1 ?& ARRAY[
                       'dispatch','context','cycle','job','permit_claim','permit',
                       'fixture_receipt','fixture_progress','publication_request',
                       'publication_plan','publication_attempt','external_effect',
                       'budget_reservation','output','remote_publication_receipts',
                       'publication_status_events','cycle_events','cycle_outbox',
                       'job_checkpoints','audit_events','provenance_records']::text[])
                    OR (hold.liability_snapshot_v1 - ARRAY[
                       'dispatch','context','cycle','job','permit_claim','permit',
                       'fixture_receipt','fixture_progress','publication_request',
                       'publication_plan','publication_attempt','external_effect',
                       'budget_reservation','output','remote_publication_receipts',
                       'publication_status_events','cycle_events','cycle_outbox',
                       'job_checkpoints','audit_events','provenance_records']::text[])
                       <> '{}'::jsonb
                  )
                ORDER BY hold.reason, hold.operation_id, hold.id
            LOOP
                blockers := blockers || jsonb_build_array(jsonb_build_object(
                    'kind', 'fixture_liability_contract_or_fingerprint_mismatch',
                    'hold_id', hold_row.id,
                    'operation_id', hold_row.operation_id,
                    'reason', hold_row.reason));
            END LOOP;

            SELECT COALESCE(jsonb_agg(to_jsonb(hold)
                          ORDER BY hold.reason, hold.operation_id, hold.id), '[]'::jsonb)
            INTO fixture_rows
            FROM public.v4_legacy_publication_recovery_holds hold
            WHERE hold.workspace_id = p_workspace_id;
            SELECT COALESCE(jsonb_agg(to_jsonb(enrollment)
                          ORDER BY enrollment.intent_id, enrollment.eligibility_revision), '[]'::jsonb)
            INTO enrollment_rows
            FROM public.v4_production_intent_enrollments enrollment
            WHERE enrollment.workspace_id = p_workspace_id;
            SELECT COALESCE(jsonb_agg(to_jsonb(work)
                          ORDER BY work.source_discriminant, work.source_id,
                                   work.source_revision, work.work_id), '[]'::jsonb)
            INTO recovery_rows
            FROM public.v4_production_recovery_work work
            WHERE work.workspace_id = p_workspace_id;
            SELECT COALESCE(jsonb_agg(to_jsonb(event)
                          ORDER BY work.source_discriminant, work.source_id,
                                   event.occurred_at, event.event_id), '[]'::jsonb)
            INTO event_rows
            FROM public.v4_production_recovery_events event
            JOIN public.v4_production_recovery_work work ON work.work_id = event.work_id
            WHERE work.workspace_id = p_workspace_id;
            SELECT COALESCE(jsonb_agg(to_jsonb(permit)
                          ORDER BY permit.operation_id, permit.generation, permit.permit_id), '[]'::jsonb)
            INTO permit_rows
            FROM public.v4_production_effect_permits permit
            WHERE permit.workspace_id = p_workspace_id;
            SELECT COALESCE(jsonb_agg(to_jsonb(claim)
                          ORDER BY claim.operation_id, claim.generation, claim.claimed_at, claim.claim_id), '[]'::jsonb)
            INTO claim_rows
            FROM public.v4_production_effect_permit_claims claim
            WHERE EXISTS (
                SELECT 1 FROM public.v4_production_effect_permits permit
                WHERE permit.permit_id = claim.permit_id
                  AND permit.workspace_id = p_workspace_id
            );
            SELECT COALESCE(jsonb_agg(to_jsonb(dispatch)
                          ORDER BY dispatch.operation_id, dispatch.generation), '[]'::jsonb)
            INTO pointer_rows
            FROM public.v4_production_current_dispatch dispatch
            WHERE EXISTS (
                SELECT 1 FROM public.v4_production_effect_permits permit
                WHERE permit.operation_id = dispatch.operation_id
                  AND permit.workspace_id = p_workspace_id
            );
            SELECT COALESCE(jsonb_agg(to_jsonb(inventory)
                          ORDER BY inventory.inventory_id), '[]'::jsonb)
            INTO inventory_rows
            FROM public.v4_production_schedule_inventories inventory
            WHERE inventory.inventory_id = ANY(schedule_ids);
            SELECT COALESCE(jsonb_agg(to_jsonb(member)
                          ORDER BY member.inventory_id, member.remote_schedule_id), '[]'::jsonb)
            INTO member_rows
            FROM public.v4_production_schedule_inventory_members member
            WHERE member.inventory_id = ANY(schedule_ids);
            SELECT COALESCE(jsonb_agg(to_jsonb(page)
                          ORDER BY page.inventory_id, page.remote_schedule_id NULLS FIRST,
                                   page.page_index), '[]'::jsonb)
            INTO page_rows
            FROM public.v4_production_schedule_inventory_pages page
            WHERE page.inventory_id = ANY(schedule_ids);
            SELECT COALESCE(jsonb_agg(to_jsonb(binding)
                          ORDER BY binding.inventory_id, binding.remote_schedule_id), '[]'::jsonb)
            INTO binding_rows
            FROM public.v4_production_schedule_source_bindings binding
            WHERE binding.inventory_id = ANY(schedule_ids);
            SELECT COALESCE(jsonb_agg(to_jsonb(approval)
                          ORDER BY approval.id), '[]'::jsonb)
            INTO approval_rows
            FROM public.approval_requests approval
            WHERE approval.id IN (
                SELECT binding.source_approval_id
                FROM public.v4_production_schedule_source_bindings binding
                WHERE binding.inventory_id = ANY(schedule_ids)
            );

            SELECT COALESCE(jsonb_agg(to_jsonb(route_claim)
                          ORDER BY route_claim.route_id, route_claim.slot_id,
                                   route_claim.source_kind, route_claim.owner_id), '[]'::jsonb)
            INTO route_rows
            FROM (
                SELECT 'enrollment'::text AS source_kind, enrollment.workspace_id,
                       enrollment.route_id, enrollment.slot_id,
                       enrollment.route_owner_id AS owner_id,
                       enrollment.fencing_epoch, enrollment.route_manifest_sha256,
                       enrollment.external_lock_sha256 AS artifact_lock_sha256
                FROM public.v4_production_intent_enrollments enrollment
                WHERE enrollment.workspace_id = p_workspace_id
                UNION ALL
                SELECT 'permit', permit.workspace_id, permit.route_id, permit.slot_id,
                       permit.owner_id, permit.fencing_epoch,
                       permit.route_manifest_sha256, permit.artifact_lock_sha256
                FROM public.v4_production_effect_permits permit
                WHERE permit.workspace_id = p_workspace_id
                UNION ALL
                SELECT 'schedule_binding', binding.workspace_id, binding.route_id,
                       binding.slot_id, binding.owner_id, binding.fencing_epoch,
                       binding.route_manifest_sha256, binding.artifact_lock_sha256
                FROM public.v4_production_schedule_source_bindings binding
                WHERE binding.workspace_id = p_workspace_id
            ) route_claim;

            SELECT COALESCE(jsonb_agg(identity_row.value
                          ORDER BY identity_row.source_kind, identity_row.source_id,
                                   identity_row.source_revision, identity_row.identity_id),
                            '[]'::jsonb)
            INTO source_rows
            FROM (
                SELECT 'enrollment'::text AS source_kind,
                       enrollment.intent_id AS source_id,
                       enrollment.eligibility_revision AS source_revision,
                       enrollment.intent_id AS identity_id,
                       jsonb_build_object(
                           'source_kind', 'enrollment',
                           'source_id', enrollment.intent_id,
                           'source_revision', enrollment.eligibility_revision,
                           'workspace_id', enrollment.workspace_id,
                           'goal_id', enrollment.goal_id) AS value
                FROM public.v4_production_intent_enrollments enrollment
                WHERE enrollment.workspace_id = p_workspace_id
                UNION ALL
                SELECT work.source_discriminant, work.source_id, work.source_revision,
                       work.work_id,
                       jsonb_build_object(
                           'source_kind', work.source_discriminant,
                           'source_id', work.source_id,
                           'source_revision', work.source_revision,
                           'work_id', work.work_id,
                           'workspace_id', work.workspace_id,
                           'goal_id', work.goal_id,
                           'intent_id', work.intent_id)
                FROM public.v4_production_recovery_work work
                WHERE work.workspace_id = p_workspace_id
                UNION ALL
                SELECT 'fixture_publication_hold'::text, hold.operation_id, 1,
                       hold.id,
                       jsonb_build_object(
                           'source_kind', 'fixture_publication_hold',
                           'source_id', hold.operation_id,
                           'source_revision', 1,
                           'hold_id', hold.id,
                           'workspace_id', hold.workspace_id,
                           'goal_id', hold.goal_id,
                           'intent_id', hold.intent_id,
                           'reason', hold.reason)
                FROM public.v4_legacy_publication_recovery_holds hold
                WHERE hold.workspace_id = p_workspace_id
                UNION ALL
                SELECT 'production_permit'::text, permit.permit_id,
                       permit.generation, permit.permit_id,
                       jsonb_build_object(
                           'source_kind', 'production_permit',
                           'source_id', permit.permit_id,
                           'source_revision', permit.generation,
                           'operation_id', permit.operation_id,
                           'intent_id', permit.intent_id,
                           'workspace_id', permit.workspace_id,
                           'goal_id', permit.goal_id)
                FROM public.v4_production_effect_permits permit
                WHERE permit.workspace_id = p_workspace_id
                UNION ALL
                SELECT 'schedule_binding'::text, binding.inventory_id,
                       binding.goal_revision, binding.inventory_id,
                       jsonb_build_object(
                           'source_kind', 'schedule_binding',
                           'source_id', binding.inventory_id,
                           'source_revision', binding.goal_revision,
                           'inventory_id', binding.inventory_id,
                           'remote_schedule_id', binding.remote_schedule_id,
                           'workspace_id', binding.workspace_id,
                           'goal_id', binding.goal_id,
                           'goal_revision', binding.goal_revision)
                FROM public.v4_production_schedule_source_bindings binding
                WHERE binding.workspace_id = p_workspace_id
            ) identity_row;

            SELECT jsonb_build_object(
                'expired_permit_receipt', jsonb_build_object(
                    'production_work', (
                        SELECT count(*) FROM public.v4_production_recovery_work work
                        WHERE work.workspace_id = p_workspace_id
                          AND work.source_discriminant = 'production_publication_hold'
                          AND work.reason = 'expired_permit_receipt'),
                    'fixture_holds', (
                        SELECT count(*) FROM public.v4_legacy_publication_recovery_holds hold
                        WHERE hold.workspace_id = p_workspace_id
                          AND hold.reason = 'expired_permit_receipt')),
                'published_during_cancel', jsonb_build_object(
                    'production_work', (
                        SELECT count(*) FROM public.v4_production_recovery_work work
                        WHERE work.workspace_id = p_workspace_id
                          AND work.source_discriminant = 'production_publication_hold'
                          AND work.reason = 'published_during_cancel'),
                    'fixture_holds', (
                        SELECT count(*) FROM public.v4_legacy_publication_recovery_holds hold
                        WHERE hold.workspace_id = p_workspace_id
                          AND hold.reason = 'published_during_cancel')))
            INTO reason_counts;

            relation_counts := jsonb_build_object(
                'v4_production_intent_enrollments', jsonb_array_length(enrollment_rows),
                'v4_production_recovery_work', jsonb_array_length(recovery_rows),
                'v4_production_recovery_events', jsonb_array_length(event_rows),
                'v4_legacy_publication_recovery_holds', jsonb_array_length(fixture_rows),
                'v4_production_effect_permits', jsonb_array_length(permit_rows),
                'v4_production_effect_permit_claims', jsonb_array_length(claim_rows),
                'v4_production_current_dispatch', jsonb_array_length(pointer_rows),
                'v4_production_schedule_inventories', jsonb_array_length(inventory_rows),
                'v4_production_schedule_inventory_members', jsonb_array_length(member_rows),
                'v4_production_schedule_inventory_pages', jsonb_array_length(page_rows),
                'v4_production_schedule_source_bindings', jsonb_array_length(binding_rows),
                'schedule_binding_approvals', jsonb_array_length(approval_rows),
                'source_identities', jsonb_array_length(source_rows),
                'production_liabilities', jsonb_array_length(liabilities),
                'route_claims', jsonb_array_length(route_rows));

            result := jsonb_build_object(
                'schema_version', 'salience.p1-recovery-rollback-inventory.v2',
                'migration_head', '0051_p1_recovery_rollback_v2',
                'workspace_id', p_workspace_id,
                'complete', true,
                'relation_counts', relation_counts,
                'source_identities', source_rows,
                'reason_counts', reason_counts,
                'enrollments', enrollment_rows,
                'recovery_work', recovery_rows,
                'recovery_events', event_rows,
                'fixture_holds', fixture_rows,
                'production_permits', permit_rows,
                'production_claims', claim_rows,
                'production_current_pointers', pointer_rows,
                'production_liabilities', liabilities,
                'schedule_inventories', inventory_rows,
                'schedule_members', member_rows,
                'schedule_pages', page_rows,
                'schedule_bindings', binding_rows,
                'schedule_approvals', approval_rows,
                'route_claims', route_rows,
                'blockers', blockers);
            RETURN result;
        END
        $$"""
    )

    op.execute(
        "REVOKE ALL ON FUNCTION public.v4_p1_recovery_rollback_inventory_v2(uuid) FROM PUBLIC"
    )


def downgrade() -> None:
    raise RuntimeError(
        "0051_p1_recovery_rollback_v2 is irreversible; preserve recovery, schedule, "
        "permit, claim, pointer, and liability evidence"
    )
