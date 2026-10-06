"""Record immutable evidence when a legacy publication must remain held."""

from alembic import op


revision = "0042_publication_recovery_holds"
down_revision = "0041_native_publication_sources"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """CREATE TABLE v4_legacy_publication_recovery_holds (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            operation_id uuid NOT NULL UNIQUE,
            workspace_id uuid NOT NULL REFERENCES workspaces(id),
            goal_id uuid NOT NULL,
            intent_id uuid NOT NULL REFERENCES v4_cycle_intents(id),
            cycle_id uuid NOT NULL REFERENCES v4_cycles(id),
            context_id uuid NOT NULL REFERENCES v4_run_contexts(id),
            job_id uuid NOT NULL REFERENCES jobs(id),
            publication_attempt_id uuid NOT NULL REFERENCES publication_attempts(id),
            permit_id uuid NOT NULL REFERENCES v4_dispatch_permits(id),
            owner_id uuid NOT NULL,
            execution_deadline timestamptz NOT NULL,
            permit_expires_at timestamptz NOT NULL,
            request_fingerprint text NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
            receipt_fingerprint text NOT NULL CHECK (receipt_fingerprint ~ '^[0-9a-f]{64}$'),
            liability_fingerprint text NOT NULL CHECK (liability_fingerprint ~ '^[0-9a-f]{64}$'),
            remote_id text NOT NULL,
            remote_state text NOT NULL CHECK (remote_state IN ('accepted','processing','published','cancelled')),
            reason text NOT NULL CHECK (reason IN ('expired_permit_receipt','published_during_cancel')),
            state text NOT NULL DEFAULT 'held' CHECK (state='held'),
            liability_snapshot jsonb NOT NULL CHECK (jsonb_typeof(liability_snapshot)='object'),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            FOREIGN KEY (operation_id) REFERENCES v4_legacy_dispatches(operation_id),
            FOREIGN KEY (operation_id) REFERENCES v4_legacy_publication_fixture_receipts(operation_id),
            FOREIGN KEY (goal_id,workspace_id) REFERENCES v4_goals(id,workspace_id),
            FOREIGN KEY (owner_id,workspace_id) REFERENCES identity_subjects(id,workspace_id)
        )"""
    )
    op.execute(
        """CREATE FUNCTION v4_guard_legacy_publication_recovery_hold()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE expected_snapshot jsonb;
        BEGIN
          IF TG_OP<>'INSERT' THEN
            RAISE EXCEPTION 'legacy publication recovery hold is immutable';
          END IF;
          IF NEW.liability_fingerprint<>encode(public.digest(NEW.liability_snapshot::text,'sha256'),'hex') THEN
            RAISE EXCEPTION 'publication recovery hold liability fingerprint mismatch';
          END IF;

          SELECT jsonb_build_object(
            'dispatch', to_jsonb(binding),
            'context', to_jsonb(context),
            'cycle', to_jsonb(cycle),
            'job', to_jsonb(job),
            'permit_claim', to_jsonb(claim),
            'permit', to_jsonb(permit),
            'fixture_receipt', to_jsonb(receipt_row),
            'fixture_progress', to_jsonb(progress),
            'publication_request', to_jsonb(pub_request),
            'publication_plan', to_jsonb(plan),
            'publication_attempt', to_jsonb(publication_attempt_row.*),
            'external_effect', COALESCE(to_jsonb(effect), 'null'::jsonb),
            'budget_reservation', COALESCE(to_jsonb(reservation), 'null'::jsonb),
            'publication_status_events', COALESCE((
              SELECT jsonb_agg(to_jsonb(status_event) ORDER BY status_event.sequence_no,status_event.id)
              FROM public.publication_status_events status_event
              WHERE status_event.publication_attempt_id=publication_attempt_row.id
            ), '[]'::jsonb),
            'cycle_events', COALESCE((
              SELECT jsonb_agg(to_jsonb(cycle_event) ORDER BY cycle_event.created_at,cycle_event.id)
              FROM public.v4_cycle_events cycle_event WHERE cycle_event.cycle_id=cycle.id
            ), '[]'::jsonb),
            'cycle_outbox', COALESCE((
              SELECT jsonb_agg(to_jsonb(outbox) ORDER BY outbox.sequence,outbox.id)
              FROM public.v4_cycle_outbox outbox WHERE outbox.cycle_id=cycle.id
            ), '[]'::jsonb)
          ) INTO expected_snapshot
          FROM public.v4_legacy_dispatches binding
          JOIN public.v4_cycles cycle
            ON cycle.id=binding.cycle_id AND cycle.intent_id=binding.intent_id
              AND cycle.context_id=binding.context_id AND cycle.operation_id=binding.operation_id
          JOIN public.v4_cycle_intents intent
            ON intent.id=binding.intent_id AND intent.goal_id=binding.goal_id
          JOIN public.v4_goals goal
            ON goal.id=binding.goal_id AND goal.workspace_id=binding.workspace_id
          JOIN public.v4_run_contexts context
            ON context.id=binding.context_id AND context.cycle_id=cycle.id
          JOIN public.identity_subjects owner
            ON owner.id=binding.actor_id AND owner.workspace_id=binding.workspace_id
          JOIN public.jobs job
            ON job.id=binding.job_id AND job.workspace_id=binding.workspace_id
          JOIN public.v4_permit_claims claim
            ON claim.cycle_id=cycle.id AND claim.operation_id=binding.operation_id
              AND claim.context_id=context.id
          JOIN public.v4_dispatch_permits permit
            ON permit.id=claim.current_permit_id AND permit.claim_id=claim.id
          JOIN public.v4_legacy_publication_fixture_receipts receipt_row
            ON receipt_row.operation_id=binding.operation_id AND receipt_row.workspace_id=binding.workspace_id
          JOIN public.v4_legacy_publication_fixture_progress progress
            ON progress.operation_id=receipt_row.operation_id
          JOIN public.publication_requests pub_request
            ON pub_request.id=(receipt_row.request->>'id')::uuid
              AND receipt_row.receipt->>'publication_attempt_id'=pub_request.id::text
          JOIN public.publication_plans plan
            ON plan.publication_request_id=pub_request.id
          JOIN public.publication_attempts publication_attempt_row
            ON publication_attempt_row.publication_plan_id=plan.id
              AND publication_attempt_row.idempotency_key=receipt_row.idempotency_key||':attempt:1'
          LEFT JOIN public.external_effects effect
            ON effect.id=plan.external_effect_id
          LEFT JOIN public.budget_reservations reservation
            ON reservation.id=plan.budget_reservation_id
          WHERE binding.operation_id=NEW.operation_id
            AND binding.stage='publication'
            AND binding.workspace_id=NEW.workspace_id
            AND binding.goal_id=NEW.goal_id
            AND binding.intent_id=NEW.intent_id
            AND binding.cycle_id=NEW.cycle_id
            AND binding.context_id=NEW.context_id
            AND binding.job_id=NEW.job_id
            AND binding.actor_id=NEW.owner_id
            AND binding.payload=job.input_payload
            AND job.job_type='governed_publication'
            AND job.actor_kind='identity'
            AND job.actor_id=NEW.owner_id::text
            AND job.state='running'
            AND (context.payload->>'subject_id')::uuid=NEW.owner_id
            AND (context.payload->>'execution_deadline')::timestamptz=NEW.execution_deadline
            AND claim.state IN ('claimed','unknown')
            AND claim.current_permit_id=NEW.permit_id
            AND permit.subject_id=NEW.owner_id
            AND permit.expires_at=NEW.permit_expires_at
            AND permit.request->>'operation_id'=binding.operation_id::text
            AND permit.request->>'context_id'=context.id::text
            AND receipt_row.idempotency_key='v4-legacy:'||binding.operation_id::text
            AND receipt_row.fingerprint=encode(public.digest(receipt_row.request::text,'sha256'),'hex')
            AND receipt_row.fingerprint=NEW.request_fingerprint
            AND receipt_row.request->>'workspace_id'=binding.workspace_id::text
            AND receipt_row.request->>'content_program_id'=binding.content_program_id::text
            AND pub_request.workspace_id=binding.workspace_id
            AND pub_request.content_program_id=binding.content_program_id
            AND pub_request.request_key=receipt_row.idempotency_key
            AND pub_request.publisher_account_id::text=binding.payload->>'publisher_account_id'
            AND pub_request.ready_package_id::text=binding.payload->>'ready_package_id'
            AND publication_attempt_row.id=NEW.publication_attempt_id
            AND receipt_row.receipt->>'state'='accepted'
            AND receipt_row.receipt->>'remote_id'=receipt_row.remote_id
            AND receipt_row.remote_id='fixture-v4-publication:'||binding.operation_id::text
            AND receipt_row.receipt->>'publisher_id'='fixture-publisher'
            AND encode(public.digest(receipt_row.receipt::text,'sha256'),'hex')=NEW.receipt_fingerprint
            AND receipt_row.remote_id=NEW.remote_id
            AND progress.state=NEW.remote_state
            AND (
              (NEW.reason='expired_permit_receipt'
                AND permit.expires_at<=clock_timestamp()
                AND progress.state IN ('accepted','processing')
                AND cycle.state<>'closed')
              OR
              (NEW.reason='published_during_cancel'
                AND cycle.state='closed' AND cycle.disposition='cancelled'
                AND progress.state='published')
            );

          IF expected_snapshot IS NULL THEN
            RAISE EXCEPTION 'original scoped publication recovery evidence and reason required';
          END IF;
          IF NEW.liability_snapshot IS DISTINCT FROM expected_snapshot THEN
            RAISE EXCEPTION 'publication recovery hold liability snapshot mismatch';
          END IF;
          RETURN NEW;
        END
        $$"""
    )
    op.execute(
        "CREATE TRIGGER validate_legacy_publication_recovery_hold BEFORE INSERT ON v4_legacy_publication_recovery_holds FOR EACH ROW EXECUTE FUNCTION v4_guard_legacy_publication_recovery_hold()"
    )
    op.execute(
        """CREATE FUNCTION v4_reject_legacy_publication_recovery_hold_change()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'legacy publication recovery hold is immutable';
        END
        $$"""
    )
    op.execute(
        "CREATE TRIGGER immutable_legacy_publication_recovery_hold BEFORE UPDATE OR DELETE ON v4_legacy_publication_recovery_holds FOR EACH ROW EXECUTE FUNCTION v4_reject_legacy_publication_recovery_hold_change()"
    )
    op.execute("REVOKE ALL PRIVILEGES ON TABLE public.v4_legacy_publication_recovery_holds FROM PUBLIC")


def downgrade():
    op.execute("SET LOCAL lock_timeout='3s'")
    op.execute(
        "LOCK TABLE v4_legacy_publication_recovery_holds IN SHARE ROW EXCLUSIVE MODE"
    )
    op.execute(
        """DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM v4_legacy_publication_recovery_holds) THEN
            RAISE EXCEPTION 'preserve immutable publication recovery hold history';
          END IF;
        END $$"""
    )
    op.execute(
        "DROP TRIGGER immutable_legacy_publication_recovery_hold ON v4_legacy_publication_recovery_holds"
    )
    op.execute(
        "DROP TRIGGER validate_legacy_publication_recovery_hold ON v4_legacy_publication_recovery_holds"
    )
    op.execute("DROP TABLE v4_legacy_publication_recovery_holds")
    op.execute("DROP FUNCTION v4_reject_legacy_publication_recovery_hold_change()")
    op.execute("DROP FUNCTION v4_guard_legacy_publication_recovery_hold()")
