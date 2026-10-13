"""Add the complete immutable publication recovery liability baseline."""

from alembic import op


revision = "0043_complete_publication_recovery_liabilities"
down_revision = "0042_publication_recovery_holds"
branch_labels = None
depends_on = None


_V1_KEYS = """ARRAY[
    'dispatch',
    'context',
    'cycle',
    'job',
    'permit_claim',
    'permit',
    'fixture_receipt',
    'fixture_progress',
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


def upgrade():
    # Alembic creates this column as varchar(32), shorter than this required
    # revision identifier. Widen it before Alembic records the new head.
    op.execute(
        "ALTER TABLE alembic_version ALTER COLUMN version_num TYPE varchar(64)"
    )
    op.execute(
        """ALTER TABLE public.v4_legacy_publication_recovery_holds
           ADD COLUMN liability_snapshot_version text,
           ADD COLUMN liability_snapshot_v1 jsonb,
           ADD COLUMN liability_fingerprint_v1 text"""
    )
    op.execute(
        """ALTER TABLE public.v4_legacy_publication_recovery_holds
           ADD CONSTRAINT ck_v4_legacy_publication_recovery_holds_v1_all_or_none
           CHECK (
             (liability_snapshot_version IS NULL
              AND liability_snapshot_v1 IS NULL
              AND liability_fingerprint_v1 IS NULL)
             OR
             (liability_snapshot_version IS NOT NULL
              AND liability_snapshot_v1 IS NOT NULL
              AND liability_fingerprint_v1 IS NOT NULL)
           )"""
    )
    op.execute(
        """ALTER TABLE public.v4_legacy_publication_recovery_holds
           ADD CONSTRAINT ck_v4_legacy_publication_recovery_holds_v1_version
           CHECK (
             liability_snapshot_version IS NULL
             OR liability_snapshot_version = 'salience.publication-liability.v1'
           )"""
    )
    op.execute(
        f"""ALTER TABLE public.v4_legacy_publication_recovery_holds
           ADD CONSTRAINT ck_v4_legacy_publication_recovery_holds_v1_snapshot_keys
           CHECK (
             liability_snapshot_v1 IS NULL
             OR (
               jsonb_typeof(liability_snapshot_v1) = 'object'
               AND liability_snapshot_v1 ?& {_V1_KEYS}
               AND (liability_snapshot_v1 - {_V1_KEYS}) = '{{}}'::jsonb
             )
           )"""
    )
    op.execute(
        """ALTER TABLE public.v4_legacy_publication_recovery_holds
           ADD CONSTRAINT ck_v4_legacy_publication_recovery_holds_v1_fingerprint
           CHECK (
             liability_fingerprint_v1 IS NULL
             OR liability_fingerprint_v1 ~ '^[0-9a-f]{64}$'
           )"""
    )
    op.execute(
        f"""CREATE FUNCTION public.v4_guard_legacy_publication_recovery_hold_v1()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE expected_snapshot jsonb;
        BEGIN
          IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION 'legacy publication recovery hold is immutable';
          END IF;
          IF NEW.liability_snapshot_version IS NULL
             OR NEW.liability_snapshot_v1 IS NULL
             OR NEW.liability_fingerprint_v1 IS NULL THEN
            RAISE EXCEPTION 'complete v1 publication recovery evidence required';
          END IF;
          IF NEW.liability_snapshot_version IS DISTINCT FROM 'salience.publication-liability.v1' THEN
            RAISE EXCEPTION 'publication recovery v1 version tag required';
          END IF;
          IF jsonb_typeof(NEW.liability_snapshot_v1) IS DISTINCT FROM 'object'
             OR NOT (NEW.liability_snapshot_v1 ?& {_V1_KEYS})
             OR (NEW.liability_snapshot_v1 - {_V1_KEYS}) <> '{{}}'::jsonb THEN
            RAISE EXCEPTION 'publication recovery v1 snapshot must contain exactly 21 keys';
          END IF;
          IF NEW.liability_fingerprint_v1 !~ '^[0-9a-f]{{64}}$' THEN
            RAISE EXCEPTION 'publication recovery v1 fingerprint must be lowercase SHA-256 hex';
          END IF;
          IF NEW.liability_fingerprint_v1 IS DISTINCT FROM
             encode(public.digest(NEW.liability_snapshot_v1::text, 'sha256'), 'hex') THEN
            RAISE EXCEPTION 'publication recovery v1 fingerprint mismatch';
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
            'output', to_jsonb(job.output_payload),
            'remote_publication_receipts', COALESCE((
              SELECT jsonb_agg(to_jsonb(remote_receipt)
                               ORDER BY remote_receipt.created_at, remote_receipt.id)
              FROM public.remote_publication_receipts remote_receipt
              WHERE remote_receipt.publication_attempt_id=publication_attempt_row.id
            ), '[]'::jsonb),
            'publication_status_events', COALESCE((
              SELECT jsonb_agg(to_jsonb(status_event)
                               ORDER BY status_event.sequence_no, status_event.id)
              FROM public.publication_status_events status_event
              WHERE status_event.publication_attempt_id=publication_attempt_row.id
            ), '[]'::jsonb),
            'cycle_events', COALESCE((
              SELECT jsonb_agg(to_jsonb(cycle_event)
                               ORDER BY cycle_event.created_at, cycle_event.id)
              FROM public.v4_cycle_events cycle_event
              WHERE cycle_event.cycle_id=cycle.id
            ), '[]'::jsonb),
            'cycle_outbox', COALESCE((
              SELECT jsonb_agg(to_jsonb(outbox)
                               ORDER BY outbox.sequence, outbox.id)
              FROM public.v4_cycle_outbox outbox
              WHERE outbox.cycle_id=cycle.id
            ), '[]'::jsonb),
            'job_checkpoints', COALESCE((
              SELECT jsonb_agg(to_jsonb(checkpoint)
                               ORDER BY checkpoint.sequence_no, checkpoint.id)
              FROM public.job_checkpoints checkpoint
              WHERE checkpoint.job_id=job.id
            ), '[]'::jsonb),
            'audit_events', COALESCE((
              SELECT jsonb_agg(to_jsonb(audit_event)
                               ORDER BY audit_event.created_at,
                                        audit_event.sequence_no, audit_event.id)
              FROM public.audit_events audit_event
              WHERE audit_event.job_id=job.id
            ), '[]'::jsonb),
            'provenance_records', COALESCE((
              SELECT jsonb_agg(to_jsonb(provenance)
                               ORDER BY provenance.created_at, provenance.id)
              FROM public.provenance_records provenance
              WHERE provenance.job_id=job.id
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
            ON receipt_row.operation_id=binding.operation_id
              AND receipt_row.workspace_id=binding.workspace_id
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
          IF NEW.liability_snapshot_v1 IS DISTINCT FROM expected_snapshot THEN
            RAISE EXCEPTION 'publication recovery v1 snapshot mismatch';
          END IF;
          RETURN NEW;
        END
        $$"""
    )
    op.execute(
        """CREATE TRIGGER validate_legacy_publication_recovery_hold_v1
           BEFORE INSERT ON public.v4_legacy_publication_recovery_holds
           FOR EACH ROW EXECUTE FUNCTION
             public.v4_guard_legacy_publication_recovery_hold_v1()"""
    )


def downgrade():
    op.execute("SET LOCAL lock_timeout='3s'")
    op.execute(
        "LOCK TABLE public.v4_legacy_publication_recovery_holds IN SHARE ROW EXCLUSIVE MODE"
    )
    op.execute(
        """DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM public.v4_legacy_publication_recovery_holds
            WHERE liability_snapshot_version IS NOT NULL
               OR liability_snapshot_v1 IS NOT NULL
               OR liability_fingerprint_v1 IS NOT NULL
          ) THEN
            RAISE EXCEPTION 'preserve publication recovery v1 liability baselines';
          END IF;
        END $$"""
    )
    op.execute(
        """DROP TRIGGER validate_legacy_publication_recovery_hold_v1
           ON public.v4_legacy_publication_recovery_holds"""
    )
    op.execute(
        "DROP FUNCTION public.v4_guard_legacy_publication_recovery_hold_v1()"
    )
    op.execute(
        """ALTER TABLE public.v4_legacy_publication_recovery_holds
           DROP CONSTRAINT ck_v4_legacy_publication_recovery_holds_v1_all_or_none,
           DROP CONSTRAINT ck_v4_legacy_publication_recovery_holds_v1_version,
           DROP CONSTRAINT ck_v4_legacy_publication_recovery_holds_v1_snapshot_keys,
           DROP CONSTRAINT ck_v4_legacy_publication_recovery_holds_v1_fingerprint,
           DROP COLUMN liability_snapshot_version,
           DROP COLUMN liability_snapshot_v1,
           DROP COLUMN liability_fingerprint_v1"""
    )
