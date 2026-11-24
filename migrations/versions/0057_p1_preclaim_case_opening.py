"""Open an operator review for a known expired, never-claimed publication permit."""

from alembic import op


revision = "0057_p1_preclaim_case_opening"
down_revision = "0056_p1_preclaim_permit_resume"
branch_labels = None
depends_on = None


_LEGACY_OPEN = "v4_open_production_recovery_case_v1"
_OPEN = "v4_open_production_preclaim_case_v1"
_LEGACY_SCHEMA = "salience.p1-production-recovery-case-opening.v1"
_SCHEMA = "salience.p1-production-preclaim-case-opening.v1"
_KIND = "expired_unclaimed_permit"
_LEGACY_GUARD = "v4_guard_production_case_opening_v1"
_GUARD = "v4_guard_production_case_opening_v2"
_LEGACY_SOURCE = "v4_require_production_case_opening_v1"
_SOURCE = "v4_require_production_case_opening_v2"
_REVIEW_GUARD = "v4_require_production_preclaim_original_approval_v1"
_LEGACY_MARKER = "salience.production_case_opening_v1"
_MARKER = "salience.production_preclaim_case_opening_v1"
_EVIDENCE_RELATIONS = (
    "v4_case_commands", "v4_case_events", "v4_cycle_events", "v4_production_recovery_work",
)


def _body(block: str) -> str:
    from textwrap import dedent, indent

    return indent(dedent(block).strip("\n"), " " * 12)


def _derive(
    name: str,
    arguments: str,
    successor: str,
    replacements: "tuple[tuple[str, str] | tuple[str, str, int], ...]",
) -> None:
    # Derive from the predecessor already installed by Alembic so the shared
    # canonical transaction and authority checks stay intact. Require every
    # patch site to occur in the expected number before installing the new definition.
    patches = (
        ("CREATE OR REPLACE FUNCTION", "CREATE FUNCTION"),
        (f"public.{name}(", f"public.{successor}("),
        *replacements,
    )
    values = ",\n".join(
        f"($before${old}$before$, $after${new}$after$, {count[0] if count else 1})"
        for old, new, *count in patches
    )
    op.execute(
        f"""DO $migration$
        DECLARE
            definition text := pg_get_functiondef('public.{name}({arguments})'::regprocedure);
            patch record;
        BEGIN
            FOR patch IN SELECT * FROM (VALUES {values}) AS edits(before_text,after_text,expected_sites)
            LOOP
                IF length(definition)-length(replace(definition,patch.before_text,''))
                    <> patch.expected_sites*length(patch.before_text) THEN
                    RAISE EXCEPTION 'ambiguous canonical opener migration site: %', patch.before_text;
                END IF;
                definition := replace(definition,patch.before_text,patch.after_text);
            END LOOP;
            EXECUTE definition;
        END
        $migration$"""
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{successor}({arguments}) FROM PUBLIC")


_APPROVAL_AND_UNCLAIMED = """
    SELECT * INTO approval FROM public.approval_requests
    WHERE id=permit.approval_request_id AND workspace_id=permit.workspace_id
      AND policy_version_id=permit.policy_version_id AND effect_type=permit.effect_type
      AND job_id::text=permit.request_snapshot->>'job_id' AND status='approved' FOR SHARE;
    request_fingerprint := encode(public.digest(permit.request_snapshot::text,'sha256'),'hex');
    IF permit.permit_id IS NULL OR pointer.current_claim_id IS NOT NULL
       OR permit.recovery_command_id IS NOT NULL OR permit.expires_at>clock_timestamp()
       OR request_fingerprint IS DISTINCT FROM request_row.request_fingerprint
       OR EXISTS (
           SELECT 1 FROM public.v4_production_effect_permit_claims existing
           WHERE existing.operation_id=cycle.operation_id
       ) OR EXISTS (
           SELECT 1 FROM public.v4_permit_claims existing
           WHERE existing.operation_id=cycle.operation_id
       ) OR EXISTS (
           SELECT 1 FROM public.v4_production_effect_permits existing
           WHERE existing.operation_id=cycle.operation_id AND existing.generation<>1
       ) THEN
        RAISE EXCEPTION 'exact expired unclaimed initial production permit required' USING ERRCODE='42501';
    END IF;
    IF approval.id IS NULL OR nullif(btrim(approval.decided_by),'') IS NULL
       OR approval.decided_at IS NULL OR approval.decided_at>permit.issued_at
       OR NOT isfinite(permit.approval_expires_at) OR permit.approval_expires_at<=clock_timestamp()
       OR (approval.request_context->>'expires_at')::timestamptz IS DISTINCT FROM permit.approval_expires_at
       OR NOT approval.request_context @> jsonb_build_object(
           'schema_version','salience.p1-effect-approval.v1',
           'workspace_id',permit.workspace_id,'program_id',permit.program_id,
           'goal_id',permit.goal_id,'goal_revision',permit.goal_revision,'intent_id',permit.intent_id,
           'cycle_id',permit.cycle_id,'context_id',permit.context_id,'operation_id',permit.operation_id,
           'subject_id',permit.subject_id,'policy_version_id',permit.policy_version_id,
           'policy_document_hash',permit.policy_document_hash,'effect_type',permit.effect_type,
           'purpose',permit.purpose,'artifact_sha256',permit.artifact_sha256,
           'account_id',permit.account_id,'provider_id',permit.provider_id
       ) OR EXISTS (
           SELECT 1 FROM unnest(ARRAY[
               'required_scopes','budget_id','budget_reservation_id','publication_request_id',
               'job_id','workflow_id','workflow_run_id','publication_plan_id',
               'publication_attempt_id','external_effect_id','remote_receipt_id'
           ]) AS bound(key)
           WHERE approval.request_context->bound.key IS DISTINCT FROM permit.request_snapshot->bound.key
       ) THEN
        RAISE EXCEPTION 'current exact original publication workflow approval required' USING ERRCODE='42501';
    END IF;

    -- Lock the real publication graph before testing absence of submissions and receipts.
    -- A concurrent durable receipt needs a foreign-key lock on the attempt.
"""


def _install_opener() -> None:
    _derive(_LEGACY_OPEN, "jsonb,jsonb", _OPEN, (
        (_LEGACY_SCHEMA, _SCHEMA, 2),
        (_LEGACY_MARKER, _MARKER, 2),
        ("claim public.v4_production_effect_permit_claims%ROWTYPE;",
         "approval public.approval_requests%ROWTYPE;\n            reservation_expiry timestamptz;"),
        ("'eligibility_revision','generation','request_fingerprint','deadline','idempotency_key']\n",
         "'eligibility_revision','generation','request_fingerprint','deadline','idempotency_key',\n"
         "                   'opening_kind','permit_id']\n"),
        ("'eligibility_revision','generation','request_fingerprint','deadline','idempotency_key'])",
         "'eligibility_revision','generation','request_fingerprint','deadline','idempotency_key',\n"
         "                   'opening_kind','permit_id'])"),
        ("request_fingerprint text, deadline timestamptz, idempotency_key text);",
         "request_fingerprint text, deadline timestamptz, idempotency_key text, opening_kind text, permit_id uuid);"),
        ("request_row.deadline,request_row.idempotency_key) <> 11\n"
         "               OR request_row.eligibility_revision < 1 OR request_row.generation < 1",
         f"request_row.deadline,request_row.idempotency_key,request_row.permit_id) <> 12\n"
         f"               OR request_row.opening_kind IS DISTINCT FROM '{_KIND}'\n"
         "               OR request_row.eligibility_revision < 1 OR request_row.generation <> 1"),
        ("AND state<>'closed' FOR UPDATE;", "AND state='runnable' FOR UPDATE;"),
        ("WHERE operation_id=cycle.operation_id AND generation=request_row.generation FOR UPDATE;",
         "WHERE operation_id=cycle.operation_id AND generation=1\n"
         "              AND current_permit_id=request_row.permit_id FOR UPDATE;"),
        (_body("""
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
        """), "              AND request_snapshot->>'idempotency_key'=pointer.idempotency_key FOR UPDATE;\n"
              + _body(_APPROVAL_AND_UNCLAIMED)),
        ("AND publication.state IN ('planned','authorized')",
         "AND publication.publication_approval_request_id=permit.approval_request_id\n"
         "              AND publication.request_key=pointer.idempotency_key\n"
         "              AND publication.state IN ('planned','authorized')"),
        ("AND plan.publisher_id=permit.provider_id AND plan.state IN ('planned','authorized')",
         "AND plan.publisher_id=permit.provider_id AND plan.state='reserved'\n"
         "              AND plan.budget_reservation_id::text=permit.request_snapshot->>'budget_reservation_id'"),
        ("AND attempt.state IN ('planned','authorized','submitting','unknown')",
         "AND attempt.state IN ('planned','authorized') AND attempt.attempt_number=1\n"
         "              AND attempt.idempotency_key=pointer.idempotency_key||':attempt:1'\n"
         "              AND attempt.submitted_at IS NULL AND attempt.completed_at IS NULL"),
        ("AND effect_row.workspace_id=goal.workspace_id AND effect_row.effect_type='publication'",
         "AND effect_row.workspace_id=goal.workspace_id AND effect_row.effect_type='publication'\n"
         "              AND effect_row.idempotency_key=pointer.idempotency_key||chr(58)||'publish'"),
        ("AND job.job_type='publication' AND job.workflow_run_id=permit.request_snapshot->>'workflow_run_id'",
         "AND job.job_type='publication' AND job.workflow_run_id=permit.request_snapshot->>'workflow_run_id'\n"
         "              AND job.state IN ('queued','running') AND job.finished_at IS NULL"),
        (_body("""
            IF publication_effect.id IS NULL OR publication_effect.status NOT IN ('submitting','unknown')
               OR publication_effect.attempted_at IS NULL OR publication_effect.attempted_at<claim.claimed_at
               OR publication_effect.completed_at IS NOT NULL OR publication_effect.provider_reference IS NOT NULL
        """), _body("""
            SELECT reservation.expires_at INTO reservation_expiry
            FROM public.budget_reservations reservation
            WHERE reservation.id::text=permit.request_snapshot->>'budget_reservation_id'
              AND reservation.budget_id::text=permit.request_snapshot->>'budget_id'
              AND reservation.job_id=publication_effect.job_id AND reservation.external_effect_id=publication_effect.id
              AND reservation.status='reserved' FOR SHARE;
            IF publication_effect.id IS NULL OR publication_effect.status<>'planned'
               OR publication_effect.attempted_at IS NOT NULL OR publication_effect.completed_at IS NOT NULL
               OR publication_effect.provider_reference IS NOT NULL OR publication_effect.effect_result IS NOT NULL
               OR reservation_expiry IS NULL OR NOT isfinite(reservation_expiry)
               OR reservation_expiry<=clock_timestamp()
        """)),
        ("status_event.state NOT IN ('planned','authorized','submitting','unknown')",
         "status_event.state NOT IN ('planned','authorized')"),
        (_body("""
               ) THEN
                RAISE EXCEPTION 'claimed publication has no exact ambiguous submission evidence' USING ERRCODE='42501';
            END IF;
        """), _body("""
               ) OR EXISTS (
                   SELECT 1 FROM public.publisher_webhook_receipts webhook
                   WHERE webhook.publication_attempt_id=(permit.request_snapshot->>'publication_attempt_id')::uuid
               ) OR EXISTS (
                   SELECT 1 FROM public.publications published
                   WHERE published.publication_request_id=(permit.request_snapshot->>'publication_request_id')::uuid
               ) THEN
                RAISE EXCEPTION 'preclaim publication requires exact planned attempt with no submission or receipt'
                    USING ERRCODE='42501';
            END IF;
            BEGIN
                PERFORM public.v4_assert_preclaim_publication_unused_v1(
                    permit.request_snapshot,pointer.idempotency_key,NULL);
            EXCEPTION WHEN raise_exception THEN
                RAISE EXCEPTION 'preclaim publication requires exact unused publication and reservation'
                    USING ERRCODE='42501';
            END;
        """)),
        ("OR max_wakes IS NULL OR max_wakes NOT BETWEEN 1 AND 10 OR cycle.recovery_count>=max_wakes THEN",
         "OR max_wakes IS NULL OR max_wakes NOT BETWEEN 1 AND 10 OR cycle.recovery_count>=max_wakes\n"
         "               OR isfinite((run_context->>'execution_deadline')::timestamptz) IS DISTINCT FROM true\n"
         "               OR isfinite((permit.request_snapshot->>'execution_deadline')::timestamptz) IS DISTINCT FROM true\n"
         "               OR clock_timestamp()>=least(intent.expires_at,\n"
         "                    (run_context->>'execution_deadline')::timestamptz,\n"
         "                    (permit.request_snapshot->>'execution_deadline')::timestamptz) THEN"),
        ("OR decision.goal_stop_revision IS DISTINCT FROM goal_stop.revision",
         "OR decision.goal_stop_revision IS DISTINCT FROM goal_stop.revision\n"
         "               OR permit.request_snapshot->>'workspace_stop_revision' IS DISTINCT FROM workspace_stop.revision::text\n"
         "               OR permit.request_snapshot->>'goal_stop_revision' IS DISTINCT FROM goal_stop.revision::text"),
        ("'request_fingerprint',request_fingerprint,'permit_id',permit.permit_id,'claim_id',claim.claim_id,\n"
         "                'external_effect_id',publication_effect.id,'evidence_state',publication_effect.status,",
         f"'opening_kind','{_KIND}','request_fingerprint',request_fingerprint,\n"
         "                'permit_id',permit.permit_id,'claim_id',NULL,'approval_request_id',permit.approval_request_id,\n"
         "                'external_effect_id',publication_effect.id,'evidence_state',publication_effect.status,\n"
         "                'publication_request_id',permit.request_snapshot->>'publication_request_id',\n"
         "                'publication_plan_id',permit.request_snapshot->>'publication_plan_id',\n"
         "                'publication_attempt_id',permit.request_snapshot->>'publication_attempt_id',\n"
         "                'job_id',publication_effect.job_id,'workflow_id',permit.request_snapshot->>'workflow_id',\n"
         "                'workflow_run_id',permit.request_snapshot->>'workflow_run_id',\n"
         "                'provider_idempotency_key',pointer.idempotency_key,\n"
         "                'budget_reservation_id',permit.request_snapshot->>'budget_reservation_id',"),
        (_body("""
            IF cycle.state<>'reconciling' THEN
                UPDATE public.v4_cycles SET state='reconciling',recovery_count=recovery_count+1 WHERE id=cycle.id;
            END IF;
        """), _body("""
            UPDATE public.v4_cycles SET state='retry_due',recovery_count=recovery_count+1 WHERE id=cycle.id;
        """)),
        ("'reconciling'", "'awaiting_review'", 4),
        ("'reconciliation',command_fingerprint,'unknown_effect'", "'manual_review',command_fingerprint,'manual_review'"),
        ("'kind','reconciliation','failure_class','unknown_effect'", "'kind','manual_review','failure_class','manual_review'"),
        ("'unknown_effect','awaiting_review'", "'manual_review','awaiting_review'"),
        ("'reason','unknown_effect','state','awaiting_review'", "'reason','manual_review','state','awaiting_review'"),
        ("'Claimed publication outcome requires reconciliation'", "'Expired publication permit requires operator review'"),
        ("request_row.deadline\n            ) THEN",
         "request_row.deadline,intent.expires_at,permit.approval_expires_at,reservation_expiry,\n"
         "                (run_context->>'execution_deadline')::timestamptz,\n"
         "                (permit.request_snapshot->>'execution_deadline')::timestamptz\n            ) THEN"),
    ))


def _install_evidence_guards() -> None:
    op.execute(
        f"""CREATE FUNCTION public.{_GUARD}()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public AS $$
        DECLARE
            case_id uuid;
            evidence_schema text;
            opening_owner name;
            preclaim_owner name;
            claimed_transaction boolean;
            preclaim_transaction boolean;
        BEGIN
            CASE TG_RELID
            WHEN 'public.v4_case_commands'::regclass THEN
                evidence_schema := NEW.result->>'schema_version';
                IF NEW.action<>'open' AND (evidence_schema IS NULL OR evidence_schema NOT IN
                    ('{_LEGACY_SCHEMA}','{_SCHEMA}')) THEN
                    RETURN NEW;
                END IF;
                case_id := NEW.case_id;
            WHEN 'public.v4_case_events'::regclass THEN
                evidence_schema := NEW.payload->>'schema_version';
                IF evidence_schema IS NULL OR evidence_schema NOT IN ('{_LEGACY_SCHEMA}','{_SCHEMA}') THEN
                    RETURN NEW;
                END IF;
                case_id := NEW.case_id;
            WHEN 'public.v4_cycle_events'::regclass THEN
                evidence_schema := NEW.payload->>'schema_version';
                IF NEW.kind<>'production_case_open' AND (evidence_schema IS NULL OR evidence_schema NOT IN
                    ('{_LEGACY_SCHEMA}','{_SCHEMA}')) THEN
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
            WHERE oid=to_regprocedure('public.{_LEGACY_OPEN}(jsonb,jsonb)');
            SELECT pg_get_userbyid(proowner) INTO preclaim_owner FROM pg_proc
            WHERE oid=to_regprocedure('public.{_OPEN}(jsonb,jsonb)');
            claimed_transaction := current_user IS NOT DISTINCT FROM opening_owner
                AND current_setting('{_LEGACY_MARKER}',true) IS NOT DISTINCT FROM case_id::text;
            preclaim_transaction := current_user IS NOT DISTINCT FROM preclaim_owner
                AND current_setting('{_MARKER}',true) IS NOT DISTINCT FROM case_id::text;
            IF case_id IS NULL OR NOT (
                (evidence_schema IS NOT DISTINCT FROM '{_SCHEMA}' AND preclaim_transaction)
                OR (evidence_schema IS DISTINCT FROM '{_SCHEMA}' AND claimed_transaction)
                OR (TG_RELID='public.v4_production_recovery_work'::regclass AND preclaim_transaction)
            ) THEN
                RAISE EXCEPTION 'production case opening evidence requires its authorized transaction'
                    USING ERRCODE='42501';
            END IF;
            RETURN NEW;
        END $$"""
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_GUARD}() FROM PUBLIC")
    _derive(_LEGACY_SOURCE, "", _SOURCE, (
        ("'schema_version','" + _LEGACY_SCHEMA + "',", ""),
        ("AND command.result @> jsonb_build_object(",
         f"""AND ((command.result->>'schema_version'='{_LEGACY_SCHEMA}'
                        AND NEW.kind='reconciliation' AND NEW.failure_class='unknown_effect'
                        AND NEW.state='reconciling')
                       OR (command.result->>'schema_version'='{_SCHEMA}'
                        AND NEW.kind='manual_review' AND NEW.failure_class='manual_review'
                        AND NEW.state='awaiting_review' AND command.result->>'opening_kind'='{_KIND}'))
                  AND command.result @> jsonb_build_object("""),
    ))
    _set_triggers(_GUARD, _SOURCE)
    op.execute(
        f"""CREATE FUNCTION public.{_REVIEW_GUARD}()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
        BEGIN
            IF TG_RELID <> 'public.v4_case_commands'::regclass OR TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'unexpected production preclaim review relation' USING ERRCODE='42501';
            END IF;
            IF NEW.action IN ('review_approve','resume') AND EXISTS (
                SELECT 1 FROM public.v4_production_recovery_work work
                JOIN public.v4_case_commands opened ON opened.id=work.opening_command_id
                WHERE work.source_id=NEW.case_id AND opened.case_id=NEW.case_id AND opened.action='open'
                  AND opened.result->>'schema_version'='{_SCHEMA}'
                  AND opened.result->>'approval_request_id' IS DISTINCT FROM NEW.result->>'approval_request_id'
            ) THEN
                RAISE EXCEPTION 'preclaim recovery requires the original publication workflow approval'
                    USING ERRCODE='42501';
            END IF;
            RETURN NEW;
        END $$"""
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_REVIEW_GUARD}() FROM PUBLIC")
    op.execute(
        f"""CREATE TRIGGER production_preclaim_original_approval BEFORE INSERT ON public.v4_case_commands
            FOR EACH ROW EXECUTE FUNCTION public.{_REVIEW_GUARD}()"""
    )


def _set_triggers(evidence_guard: str, source_guard: str) -> None:
    for relation in _EVIDENCE_RELATIONS:
        op.execute(f"DROP TRIGGER production_case_opening_evidence ON public.{relation}")
        op.execute(
            f"""CREATE TRIGGER production_case_opening_evidence BEFORE INSERT ON public.{relation}
                FOR EACH ROW EXECUTE FUNCTION public.{evidence_guard}()"""
        )
    op.execute("DROP TRIGGER production_case_opening_source ON public.v4_recovery_cases")
    op.execute(
        f"""CREATE CONSTRAINT TRIGGER production_case_opening_source AFTER INSERT ON public.v4_recovery_cases
            DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.{source_guard}()"""
    )


def upgrade() -> None:
    _install_opener()
    _install_evidence_guards()


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout='3s'")
    op.execute(
        f"""DO $$ BEGIN
            LOCK TABLE public.v4_recovery_cases IN ACCESS EXCLUSIVE MODE;
            LOCK TABLE public.v4_case_commands,public.v4_case_events,public.v4_cycle_events,
                public.v4_production_recovery_work IN SHARE MODE;
            IF EXISTS (SELECT 1 FROM public.v4_case_commands
                WHERE result->>'schema_version'='{_SCHEMA}' OR result->>'opening_kind'='{_KIND}')
               OR EXISTS (SELECT 1 FROM public.v4_case_events
                WHERE payload->>'schema_version'='{_SCHEMA}' OR payload->>'opening_kind'='{_KIND}')
               OR EXISTS (SELECT 1 FROM public.v4_cycle_events
                WHERE payload->>'schema_version'='{_SCHEMA}' OR payload->>'opening_kind'='{_KIND}') THEN
                RAISE EXCEPTION 'preserve production preclaim case opening history';
            END IF;
        END $$"""
    )
    op.execute("DROP TRIGGER production_preclaim_original_approval ON public.v4_case_commands")
    op.execute(f"DROP FUNCTION public.{_REVIEW_GUARD}()")
    _set_triggers(_LEGACY_GUARD, _LEGACY_SOURCE)
    op.execute(f"DROP FUNCTION public.{_GUARD}()")
    op.execute(f"DROP FUNCTION public.{_SOURCE}()")
    op.execute(f"DROP FUNCTION public.{_OPEN}(jsonb,jsonb)")
