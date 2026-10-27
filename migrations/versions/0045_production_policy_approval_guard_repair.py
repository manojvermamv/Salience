"""Repair the strict production effect approval transition guard."""

from alembic import op


revision = "0045_production_policy_approval_guard_repair"
down_revision = "0044_production_policy_revocation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Reinstall the canonical 0044 guard for databases already stamped 0044.
    # That revision's function was previously deployed with nullable tag logic
    # and without the current complete v1 binding checks.
    op.execute(r"""CREATE OR REPLACE FUNCTION public.v4_guard_effect_approval_transition_v1()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public AS $$
        DECLARE
            tagged boolean;
            allowed_keys text[] := ARRAY[
                'schema_version',
                'workspace_id',
                'program_id',
                'goal_id',
                'goal_revision',
                'intent_id',
                'cycle_id',
                'context_id',
                'operation_id',
                'subject_id',
                'effect_type',
                'purpose',
                'artifact_sha256',
                'account_id',
                'provider_id',
                'required_scopes',
                'budget_id',
                'budget_reservation_id',
                'policy_version_id',
                'policy_document_hash',
                'expires_at',
                'publication_request_id',
                'job_id',
                'workflow_id',
                'workflow_run_id',
                'run_id',
                'publication_plan_id',
                'publication_attempt_id',
                'external_effect_id',
                'effect_id',
                'remote_receipt_id',
                'receipt_id'
            ]::text[];
            required_keys text[] := ARRAY[
                'schema_version',
                'workspace_id',
                'program_id',
                'goal_id',
                'goal_revision',
                'intent_id',
                'cycle_id',
                'context_id',
                'operation_id',
                'subject_id',
                'effect_type',
                'purpose',
                'artifact_sha256',
                'account_id',
                'provider_id',
                'required_scopes',
                'budget_id',
                'budget_reservation_id',
                'policy_version_id',
                'policy_document_hash',
                'expires_at'
            ]::text[];
            identifier_key text;
            approval_expiry timestamptz;
            scope_count integer;
            unique_scope_count integer;
        BEGIN
            IF TG_RELID <> 'public.approval_requests'::regclass
               OR TG_TABLE_SCHEMA <> 'public'
               OR TG_TABLE_NAME <> 'approval_requests' THEN
                RAISE EXCEPTION 'effect approval guard only permits approval_requests';
            END IF;
            tagged := NEW.request_context->>'schema_version' IS NOT DISTINCT FROM
                      'salience.p1-effect-approval.v1';
            IF TG_OP = 'UPDATE'
               AND OLD.request_context->>'schema_version'
                   IS DISTINCT FROM 'salience.p1-effect-approval.v1'
               AND tagged THEN
                RAISE EXCEPTION
                    'tagged effect approval cannot be added to an existing row';
            END IF;
            IF TG_OP = 'UPDATE'
               AND OLD.request_context->>'schema_version' =
                   'salience.p1-effect-approval.v1'
               AND NOT tagged THEN
                RAISE EXCEPTION 'tagged effect approval identity is immutable';
            END IF;
            IF NOT tagged THEN
                RETURN NEW;
            END IF;
            IF jsonb_typeof(NEW.request_context) IS DISTINCT FROM 'object'
               OR NOT (NEW.request_context ?& required_keys)
               OR (NEW.request_context - allowed_keys) <> '{}'::jsonb THEN
                RAISE EXCEPTION 'tagged effect approval requires the exact v1 binding fields';
            END IF;
            IF jsonb_typeof(NEW.request_context->'required_scopes') IS DISTINCT FROM 'array' THEN
                RAISE EXCEPTION 'tagged effect approval requires a nonempty unique scope array';
            END IF;
            SELECT count(*), count(DISTINCT value #>> '{}')
              INTO scope_count, unique_scope_count
              FROM jsonb_array_elements(NEW.request_context->'required_scopes') AS scope(value)
             WHERE jsonb_typeof(value) = 'string'
               AND btrim(value #>> '{}') <> ''
               AND length(value #>> '{}') <= 128;
            IF scope_count = 0
               OR scope_count <> jsonb_array_length(NEW.request_context->'required_scopes')
               OR scope_count <> unique_scope_count THEN
                RAISE EXCEPTION 'tagged effect approval requires a nonempty unique scope array';
            END IF;
            IF NEW.policy_version_id IS NULL
               OR NEW.request_context->>'workspace_id' IS DISTINCT FROM NEW.workspace_id::text
               OR NEW.request_context->>'policy_version_id'
                    IS DISTINCT FROM NEW.policy_version_id::text
               OR NEW.request_context->>'effect_type' IS DISTINCT FROM NEW.effect_type
               OR NEW.request_context->>'goal_revision' !~ '^[1-9][0-9]*$'
               OR NEW.request_context->>'artifact_sha256' !~ '^[0-9a-f]{64}$'
               OR NEW.request_context->>'policy_document_hash' !~ '^[0-9a-f]{64}$'
               OR NEW.request_context->>'purpose' = ''
               OR NEW.request_context->>'account_id' = 'fixture-account'
               OR NEW.request_context->>'effect_type' = 'fixture_execution'
               OR NEW.request_context->>'provider_id' = 'fixture.noop' THEN
                RAISE EXCEPTION 'tagged effect approval binding does not match its row';
            END IF;
            FOREACH identifier_key IN ARRAY ARRAY[
                'workspace_id',
                'program_id',
                'goal_id',
                'intent_id',
                'cycle_id',
                'context_id',
                'operation_id',
                'subject_id',
                'policy_version_id',
                'account_id',
                'budget_id',
                'budget_reservation_id'
            ]::text[] LOOP
                IF NEW.request_context->>identifier_key
                     !~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$' THEN
                    RAISE EXCEPTION 'tagged effect approval contains an invalid % binding', identifier_key;
                END IF;
            END LOOP;
            FOREACH identifier_key IN ARRAY ARRAY[
                'publication_request_id',
                'job_id',
                'workflow_id',
                'workflow_run_id',
                'run_id',
                'publication_plan_id',
                'publication_attempt_id',
                'external_effect_id',
                'effect_id',
                'remote_receipt_id',
                'receipt_id'
            ]::text[] LOOP
                IF NEW.request_context ? identifier_key
                   AND (jsonb_typeof(NEW.request_context->identifier_key) <> 'string'
                        OR NEW.request_context->>identifier_key = '') THEN
                    RAISE EXCEPTION 'tagged effect approval contains an invalid optional identifier';
                END IF;
            END LOOP;
            IF (NEW.job_id IS NULL AND NEW.request_context ? 'job_id')
               OR (NEW.job_id IS NOT NULL
                   AND NEW.request_context->>'job_id' IS DISTINCT FROM NEW.job_id::text) THEN
                RAISE EXCEPTION 'tagged effect approval job binding mismatch';
            END IF;
            IF NEW.request_context->>'expires_at'
                   !~* '(Z|[+-][0-9]{2}:[0-9]{2})$' THEN
                RAISE EXCEPTION 'tagged effect approval expiry must be timezone-aware';
            END IF;
            BEGIN
                approval_expiry := (NEW.request_context->>'expires_at')::timestamptz;
            EXCEPTION WHEN others THEN
                RAISE EXCEPTION 'tagged effect approval expiry must be timezone-aware';
            END;
            IF approval_expiry IS NULL THEN
                RAISE EXCEPTION 'tagged effect approval expiry is required';
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM public.policy_versions policy
                WHERE policy.id = NEW.policy_version_id
                  AND policy.workspace_id = NEW.workspace_id
                  AND policy.document_hash =
                      NEW.request_context->>'policy_document_hash'
            ) THEN
                RAISE EXCEPTION 'tagged effect approval policy binding mismatch';
            END IF;
            IF NEW.status NOT IN ('pending', 'approved', 'rejected', 'revoked', 'expired') THEN
                RAISE EXCEPTION 'tagged effect approval status is invalid';
            END IF;
            IF TG_OP = 'UPDATE'
               AND OLD.request_context->>'schema_version' =
                   'salience.p1-effect-approval.v1' THEN
                IF OLD.status <> 'pending' THEN
                    IF ROW(OLD.workspace_id, OLD.job_id, OLD.policy_version_id,
                           OLD.effect_type, OLD.request_context, OLD.requested_by)
                       IS DISTINCT FROM
                       ROW(NEW.workspace_id, NEW.job_id, NEW.policy_version_id,
                           NEW.effect_type, NEW.request_context, NEW.requested_by)
                       OR ROW(OLD.decided_by, OLD.decided_at, OLD.decision_reason)
                          IS DISTINCT FROM
                          ROW(NEW.decided_by, NEW.decided_at, NEW.decision_reason) THEN
                        RAISE EXCEPTION 'decided effect approval binding is immutable';
                    END IF;
                    IF NEW.status IS DISTINCT FROM OLD.status THEN
                        IF OLD.status <> 'approved' OR NEW.status <> 'revoked' THEN
                            RAISE EXCEPTION 'terminal effect approval decision is immutable';
                        END IF;
                        IF NOT EXISTS (
                            SELECT 1
                            FROM public.v4_cycle_events event
                            WHERE event.workspace_id = OLD.workspace_id
                              AND event.cycle_id =
                                  (OLD.request_context->>'cycle_id')::uuid
                              AND event.kind = 'production_approval_revoked'
                              AND event.payload->>'schema_version' =
                                  'salience.p1-effect-approval-revocation.v1'
                              AND event.payload->>'approval_request_id' = OLD.id::text
                              AND event.payload->>'operation_id' =
                                  OLD.request_context->>'operation_id'
                              AND event.payload->>'policy_version_id' =
                                  OLD.policy_version_id::text
                              AND event.payload->>'transaction_id' =
                                  txid_current()::text
                        ) THEN
                            RAISE EXCEPTION 'approval revocation event required in the same transaction';
                        END IF;
                    END IF;
                ELSE
                    IF NEW.status = 'pending'
                       AND (NEW.decided_by IS NOT NULL
                            OR NEW.decided_at IS NOT NULL
                            OR NEW.decision_reason IS NOT NULL) THEN
                        RAISE EXCEPTION
                            'tagged effect approval decision requires pending-to-terminal transition';
                    END IF;
                    IF NEW.status <> 'pending'
                       AND (NEW.decided_by IS NULL OR NEW.decided_at IS NULL) THEN
                        RAISE EXCEPTION 'effect approval decision actor and time required';
                    END IF;
                END IF;
            ELSIF TG_OP = 'INSERT' THEN
                IF NEW.status <> 'pending'
                   OR NEW.decided_by IS NOT NULL
                   OR NEW.decided_at IS NOT NULL
                   OR NEW.decision_reason IS NOT NULL THEN
                    RAISE EXCEPTION
                        'tagged effect approval must be inserted pending and undecided';
                END IF;
            END IF;
            RETURN NEW;
        END
        $$""")
    op.execute(
        "REVOKE ALL ON FUNCTION public.v4_guard_effect_approval_transition_v1() FROM PUBLIC"
    )


def downgrade() -> None:
    # The revision repairs an installed function body and introduces no data
    # that can be removed. Keep the canonical strict guard installed when the
    # Alembic version moves back to 0044; never restore the stale body.
    upgrade()
