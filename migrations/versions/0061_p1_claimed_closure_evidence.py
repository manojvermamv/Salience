"""Record immutable closure evidence for the original claimed publication."""

from alembic import op


revision = "0061_p1_claimed_closure_evidence"
down_revision = "0060_p1_claimed_reissue_fence"
branch_labels = None
depends_on = None

_SCHEMA = "salience.p1-claimed-publication-closure.v1"
_TABLE = "public.v4_claimed_publication_closures"
_READER = "public.v4_read_claimed_publication_closure_source_v1"
_WRITER = "public.v4_record_claimed_publication_closure_v1"
_LINEAGE = "public.v4_has_claimed_publication_closure_lineage_v1"
_GUARD = "public.v4_guard_claimed_publication_closure_v1"
_INVENTORY = "public.v4_p1_recovery_rollback_inventory_v8"

_LINEAGE_SOURCE = """
    SELECT EXISTS (
        SELECT 1
        FROM public.v4_production_recovery_work work
        JOIN public.v4_recovery_cases recovery
          ON recovery.id=work.source_id AND recovery.workspace_id=work.workspace_id
         AND recovery.goal_id=work.goal_id AND recovery.target_cycle_id=work.cycle_id
         AND recovery.context_id=work.context_id AND recovery.operation_id=work.operation_id
         AND recovery.owner_id=work.owner_id AND recovery.deadline=work.deadline
         AND recovery.kind='reconciliation' AND recovery.failure_class='unknown_effect'
        JOIN public.v4_cycles cycle
          ON cycle.id=work.cycle_id AND cycle.operation_id=work.operation_id
         AND cycle.intent_id=work.intent_id AND cycle.context_id=work.context_id
        JOIN public.v4_production_effect_permits permit
          ON permit.permit_id=p_permit_id AND permit.operation_id=work.operation_id
         AND permit.workspace_id=work.workspace_id AND permit.goal_id=work.goal_id
         AND permit.intent_id=work.intent_id AND permit.cycle_id=work.cycle_id
         AND permit.context_id=work.context_id AND permit.generation=1
         AND permit.recovery_command_id IS NULL AND permit.provider_id=work.target_alias
         AND permit.account_id=work.remote_account_id AND permit.request_snapshot ? 'publication_request_id'
         AND work.request_fingerprint=encode(public.digest(permit.request_snapshot::text,'sha256'),'hex')
        JOIN public.v4_production_effect_permit_claims claim
          ON claim.claim_id=p_claim_id AND claim.permit_id=permit.permit_id
         AND claim.operation_id=permit.operation_id AND claim.generation=1
        JOIN public.v4_production_intent_enrollments enrollment
          ON enrollment.intent_id=work.intent_id AND enrollment.eligibility_revision=work.eligibility_revision
         AND enrollment.workspace_id=work.workspace_id AND enrollment.goal_id=work.goal_id
         AND enrollment.goal_revision=permit.goal_revision AND enrollment.subject_id=permit.subject_id
         AND enrollment.subject_revision=permit.subject_revision AND enrollment.source_owner_id=work.owner_id
         AND enrollment.policy_version_id=permit.policy_version_id
         AND enrollment.policy_document_hash=permit.policy_document_hash
         AND enrollment.artifact_sha256=permit.artifact_sha256 AND enrollment.route_id=permit.route_id
         AND enrollment.slot_id=permit.slot_id AND enrollment.route_owner_id=permit.owner_id
         AND enrollment.fencing_epoch=permit.fencing_epoch
         AND enrollment.route_manifest_sha256=permit.route_manifest_sha256
         AND enrollment.external_lock_sha256=permit.artifact_lock_sha256
        JOIN public.v4_case_commands opener
          ON opener.id=work.opening_command_id AND opener.case_id=recovery.id
         AND opener.actor_id=work.owner_id AND opener.action='open'
         AND opener.fingerprint=recovery.open_fingerprint
         AND opener.fingerprint=encode(public.digest((opener.result->'request')::text,'sha256'),'hex')
         AND opener.result @> jsonb_build_object(
             'schema_version','salience.p1-production-recovery-case-opening.v1',
             'state','reconciling','kind','reconciliation','failure_class','unknown_effect',
             'action','open','resulting_revision',1,'generation',1,'command_id',opener.id,
             'workspace_id',work.workspace_id,'goal_id',work.goal_id,'intent_id',work.intent_id,
             'cycle_id',work.cycle_id,'context_id',work.context_id,'operation_id',work.operation_id,
             'work_id',work.work_id,'case_id',work.source_id,'actor_id',work.owner_id,
             'request_fingerprint',work.request_fingerprint,'permit_id',permit.permit_id,'claim_id',claim.claim_id)
         AND opener.result->>'external_effect_id'=permit.request_snapshot->>'external_effect_id'
         AND opener.result->>'evidence_state' IN ('submitting','unknown')
        JOIN public.v4_case_events case_event
          ON case_event.case_id=opener.case_id AND case_event.case_revision=1 AND case_event.action='opened'
         AND case_event.actor_id=opener.actor_id AND case_event.payload=opener.result
        JOIN public.v4_cycle_events audit
          ON audit.workspace_id=work.workspace_id AND audit.goal_id=work.goal_id AND audit.intent_id=work.intent_id
         AND audit.cycle_id=work.cycle_id AND audit.subject_id=work.owner_id AND audit.kind='production_case_open'
         AND audit.traceparent=case_event.traceparent AND audit.payload=opener.result
        JOIN public.v4_production_recovery_events alert
          ON alert.work_id=work.work_id AND alert.event_kind='alert_intent'
         AND alert.event_id::text=opener.result->>'alert_event_id'
         AND alert.event_key=concat('alert-intent:',work.work_id,chr(58),'initial')
        WHERE work.work_id=p_work_id AND work.source_discriminant='cycle_case'
          AND work.reason='unknown_effect'
    )
"""

_READER_SOURCE = """
    DECLARE
        work public.v4_production_recovery_work%ROWTYPE;
        permit public.v4_production_effect_permits%ROWTYPE;
        pointer public.v4_production_current_dispatch%ROWTYPE;
        claim public.v4_production_effect_permit_claims%ROWTYPE;
        policy public.policy_versions%ROWTYPE;
        effect public.external_effects%ROWTYPE;
        workspace_stop public.v4_stop_scopes%ROWTYPE;
        goal_stop public.v4_stop_scopes%ROWTYPE;
        snapshot jsonb;
        now_at_check timestamptz;
    BEGIN
        SELECT * INTO work FROM public.v4_production_recovery_work
        WHERE work_id=p_work_id AND source_discriminant='cycle_case'
          AND opening_command_id IS NOT NULL AND source_revision=1 AND case_revision=1
          AND state='reconciling' AND reason='unknown_effect'
        FOR UPDATE;
        IF work.work_id IS NULL THEN
            RAISE EXCEPTION 'closure requires opener-linked reconciliation work' USING ERRCODE='42501';
        END IF;

        -- Work precedes the operation lock, as in production reissue. The cycle
        -- precedes the case and pointer, as in the existing recovery controller.
        PERFORM pg_advisory_xact_lock(hashtextextended(work.operation_id::text,0));
        PERFORM 1 FROM public.v4_cycles
        WHERE id=work.cycle_id AND context_id=work.context_id AND operation_id=work.operation_id
          AND intent_id=work.intent_id AND state='reconciling'
        FOR UPDATE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'closure requires the current reconciling cycle' USING ERRCODE='42501';
        END IF;
        PERFORM 1 FROM public.v4_recovery_cases
        WHERE id=work.source_id AND revision=work.case_revision AND state='reconciling'
        FOR UPDATE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'closure requires the current reconciling case' USING ERRCODE='42501';
        END IF;
        SELECT * INTO pointer FROM public.v4_production_current_dispatch
        WHERE operation_id=work.operation_id AND generation=1 FOR SHARE;
        IF NOT public.v4_has_claimed_publication_closure_lineage_v1(
            work.work_id,pointer.current_permit_id,pointer.current_claim_id
        ) THEN
            RAISE EXCEPTION 'closure requires the exact opener-linked original claim' USING ERRCODE='42501';
        END IF;
        SELECT * INTO STRICT permit FROM public.v4_production_effect_permits
        WHERE permit_id=pointer.current_permit_id;
        SELECT * INTO STRICT claim FROM public.v4_production_effect_permit_claims
        WHERE claim_id=pointer.current_claim_id;
        snapshot := permit.request_snapshot;
        IF snapshot->>'idempotency_key' IS DISTINCT FROM pointer.idempotency_key
           OR (snapshot ? 'environment_id' AND
               snapshot->>'environment_id' IS DISTINCT FROM permit.environment_id::text)
           OR snapshot->>'remote_receipt_id' IS NOT NULL THEN
            RAISE EXCEPTION 'closure requires the exact original dispatch scope' USING ERRCODE='42501';
        END IF;

        -- These locks serialize with canonical receipts and newer plans or attempts,
        -- whose foreign keys reference the publication request, plan, or attempt.
        SELECT effect_row.* INTO effect
        FROM public.publication_requests publication
        JOIN public.publication_plans plan ON plan.publication_request_id=publication.id
        JOIN public.publication_attempts attempt ON attempt.publication_plan_id=plan.id
        JOIN public.external_effects effect_row ON effect_row.id=plan.external_effect_id
        JOIN public.jobs job ON job.id=effect_row.job_id
        WHERE publication.id=(snapshot->>'publication_request_id')::uuid
          AND publication.workspace_id=work.workspace_id AND publication.content_program_id=permit.program_id
          AND publication.publisher_account_id::text=permit.account_id AND publication.publisher_id=permit.provider_id
          AND publication.state IN ('planned','authorized')
          AND plan.id=(snapshot->>'publication_plan_id')::uuid
          AND plan.publisher_id=permit.provider_id AND plan.state IN ('planned','authorized','reserved')
          AND attempt.id=(snapshot->>'publication_attempt_id')::uuid
          AND attempt.state IN ('planned','authorized','submitting','unknown')
          AND effect_row.id=(snapshot->>'external_effect_id')::uuid
          AND effect_row.workspace_id=work.workspace_id AND effect_row.effect_type='publication'
          AND job.id=(snapshot->>'job_id')::uuid
          AND job.workspace_id=work.workspace_id AND job.content_program_id=permit.program_id
          AND job.job_type='publication' AND job.workflow_run_id=snapshot->>'workflow_run_id'
          AND job.input_payload->>'workflow_id'=snapshot->>'workflow_id'
        FOR UPDATE OF publication,plan,attempt,effect_row,job;
        IF effect.id IS NULL OR effect.status NOT IN ('submitting','unknown')
           OR effect.attempted_at IS NULL OR effect.attempted_at<claim.claimed_at
           OR effect.completed_at IS NOT NULL OR effect.provider_reference IS NOT NULL
           OR EXISTS (
               SELECT 1 FROM public.publication_plans later
               JOIN public.publication_plans original ON original.publication_request_id=later.publication_request_id
               WHERE original.id=(snapshot->>'publication_plan_id')::uuid AND later.version>original.version
           ) OR EXISTS (
               SELECT 1 FROM public.publication_attempts later
               JOIN public.publication_attempts original ON original.publication_plan_id=later.publication_plan_id
               WHERE original.id=(snapshot->>'publication_attempt_id')::uuid
                 AND later.attempt_number>original.attempt_number
           ) OR EXISTS (
               SELECT 1 FROM public.remote_publication_receipts receipt
               WHERE receipt.publication_attempt_id=(snapshot->>'publication_attempt_id')::uuid
           ) OR EXISTS (
               SELECT 1 FROM public.publications publication
               WHERE publication.publication_request_id=(snapshot->>'publication_request_id')::uuid
           ) OR EXISTS (
               SELECT 1 FROM public.publication_status_events status_event
               WHERE status_event.publication_attempt_id=(snapshot->>'publication_attempt_id')::uuid
                 AND status_event.state NOT IN ('planned','authorized','submitting','unknown')
           ) OR EXISTS (
               SELECT 1 FROM public.v4_production_recovery_events observation
               WHERE observation.work_id=work.work_id AND observation.event_kind='remote_read_observation'
                 AND observation.payload->>'result_class' IN ('in_progress','terminal_success')
           ) THEN
            RAISE EXCEPTION 'closure conflicts with current publication evidence' USING ERRCODE='42501';
        END IF;

        -- Active stop and canonical policy state remain binding even though this
        -- service can only retain evidence and the route decision is verified by the caller.
        PERFORM 1 FROM public.workspaces
        WHERE id=work.workspace_id AND status='active' FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'current closure workspace required' USING ERRCODE='42501';
        END IF;
        SELECT * INTO goal_stop FROM public.v4_stop_scopes
        WHERE workspace_id=work.workspace_id AND scope_key='goal:' || work.goal_id::text FOR SHARE;
        SELECT * INTO workspace_stop FROM public.v4_stop_scopes
        WHERE workspace_id=work.workspace_id AND scope_key='workspace' FOR SHARE;
        SELECT * INTO policy FROM public.policy_versions
        WHERE id=permit.policy_version_id AND workspace_id=work.workspace_id FOR SHARE;
        now_at_check := clock_timestamp();
        IF goal_stop.stopped IS DISTINCT FROM false OR workspace_stop.stopped IS DISTINCT FROM false
           OR policy.id IS NULL OR policy.status<>'active'
           OR policy.document_hash IS DISTINCT FROM permit.policy_document_hash
           OR policy.effective_at IS NULL OR policy.effective_at>now_at_check
           OR policy.document->>'schema_version' IS DISTINCT FROM 'salience.p1-production-policy.v1'
           OR COALESCE((policy.document->>'expires_at')::timestamptz > now_at_check,false) IS NOT TRUE
           OR NOT COALESCE(policy.document->'allowed_effects' ? permit.effect_type,false)
           OR work.deadline<=now_at_check THEN
            RAISE EXCEPTION 'current closure policy, stops and case deadline required' USING ERRCODE='42501';
        END IF;

        RETURN jsonb_build_object(
            'operation_id',work.operation_id,'permit_id',permit.permit_id,'claim_id',claim.claim_id,
            'generation',permit.generation,'request_fingerprint',work.request_fingerprint,
            'environment_id',permit.environment_id,'namespace',snapshot->>'namespace',
            'provider_id',permit.provider_id,'account_id',permit.account_id,'original_key',pointer.idempotency_key,
            'work_id',work.work_id,'workspace_id',work.workspace_id,'case_id',work.source_id,
            'case_revision',work.case_revision,'workspace_stop_revision',workspace_stop.revision,
            'goal_stop_revision',goal_stop.revision,
            'source_expires_at',least(work.deadline,(policy.document->>'expires_at')::timestamptz)
        );
    END
"""

_WRITER_SOURCE = """
    DECLARE
        evidence_keys constant text[] := ARRAY[
            'operation_id','permit_id','claim_id','generation','request_fingerprint','environment_id',
            'namespace','barrier_id','closure_id','signer_sha256','signature_hex',
            'policy_decision_id','route_decision_id','authority_expires_at','case_revision',
            'workspace_stop_revision','goal_stop_revision'];
        uuid_keys constant text[] := ARRAY[
            'operation_id','permit_id','claim_id','environment_id',
            'barrier_id','closure_id','policy_decision_id','route_decision_id'];
        integer_keys constant text[] := ARRAY[
            'generation','case_revision','workspace_stop_revision','goal_stop_revision'];
        authority_keys constant text[] := ARRAY['policy_decision_id','route_decision_id','authority_expires_at'];
        source jsonb;
        expires_at timestamptz;
        valid_until timestamptz;
        prior public.v4_claimed_publication_closures%ROWTYPE;
        candidate public.v4_claimed_publication_closures%ROWTYPE;
        result jsonb;
    BEGIN
        IF jsonb_typeof(p_evidence) IS DISTINCT FROM 'object'
           OR NOT p_evidence ?& evidence_keys OR p_evidence - evidence_keys <> '{}'::jsonb
           OR EXISTS (
               SELECT 1 FROM jsonb_each(p_evidence) field
               WHERE field.key<>ALL(integer_keys) AND jsonb_typeof(field.value)<>'string'
           ) OR EXISTS (
               SELECT 1 FROM unnest(uuid_keys) field(key)
               WHERE p_evidence->>field.key !~ '^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$'
           ) OR EXISTS (
               SELECT 1 FROM unnest(integer_keys) field(key)
               WHERE jsonb_typeof(p_evidence->field.key)<>'number'
                  OR p_evidence->>field.key !~ '^[1-9][0-9]*$'
           ) OR p_evidence->>'generation'<>'1'
           OR p_evidence->>'request_fingerprint' !~ '^[0-9a-f]{64}$'
           OR p_evidence->>'signer_sha256' !~ '^[0-9a-f]{64}$'
           OR p_evidence->>'signature_hex' !~ '^[0-9a-f]{128}$'
           OR length(p_evidence->>'namespace') NOT BETWEEN 1 AND 512
           OR p_evidence->>'namespace' ~ '[[:cntrl:]]'
           OR p_evidence->>'authority_expires_at'
                  !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}([.][0-9]{1,6})?(Z|[+]00:00)$' THEN
            RAISE EXCEPTION 'exact typed closure evidence required' USING ERRCODE='22023';
        END IF;

        source := public.v4_read_claimed_publication_closure_source_v1(p_work_id);
        IF EXISTS (
            SELECT 1 FROM unnest(ARRAY[
                'operation_id','permit_id','claim_id','generation','request_fingerprint','environment_id',
                'case_revision','workspace_stop_revision','goal_stop_revision'
            ]) field(key)
            WHERE p_evidence->field.key IS DISTINCT FROM source->field.key
        ) OR (source->>'namespace' IS NOT NULL AND source->'namespace' IS DISTINCT FROM p_evidence->'namespace') THEN
            RAISE EXCEPTION 'closure evidence does not match the current original claim, case and stops'
                USING ERRCODE='42501';
        END IF;

        expires_at := (p_evidence->>'authority_expires_at')::timestamptz;
        valid_until := least(expires_at,(source->>'source_expires_at')::timestamptz);
        IF NOT isfinite(expires_at) OR valid_until<=clock_timestamp() THEN
            RAISE EXCEPTION 'fresh closure authority required' USING ERRCODE='42501';
        END IF;

        candidate := jsonb_populate_record(NULL::public.v4_claimed_publication_closures,p_evidence || jsonb_build_object(
            'schema_version','salience.p1-claimed-publication-closure.v1',
            'work_id',p_work_id,'workspace_id',source->'workspace_id',
            'case_id',source->'case_id','recorded_at',clock_timestamp()
        ));
        SELECT * INTO prior FROM public.v4_claimed_publication_closures WHERE claim_id=candidate.claim_id;
        IF FOUND THEN
            IF (to_jsonb(prior) - authority_keys - 'recorded_at')
                   IS DISTINCT FROM (to_jsonb(candidate) - authority_keys - 'recorded_at') THEN
                RAISE EXCEPTION 'closure evidence conflict for the original claim' USING ERRCODE='22023';
            END IF;
            result := to_jsonb(prior);
        ELSE
            PERFORM set_config('salience.claimed_publication_closure_v1',candidate.claim_id::text,true);
            INSERT INTO public.v4_claimed_publication_closures AS closure SELECT (candidate).*
            RETURNING to_jsonb(closure) INTO result;
            PERFORM set_config('salience.claimed_publication_closure_v1','',true);
        END IF;
        IF valid_until<=clock_timestamp() THEN
            RAISE EXCEPTION 'closure authority expired during the write' USING ERRCODE='42501';
        END IF;
        RETURN result;
    END
"""

_GUARD_SOURCE = """
    BEGIN
        IF TG_OP<>'INSERT' THEN
            RAISE EXCEPTION 'claimed publication closure evidence is immutable' USING ERRCODE='42501';
        END IF;
        IF current_user IS DISTINCT FROM (
            SELECT pg_get_userbyid(proowner) FROM pg_proc
            WHERE oid=to_regprocedure('public.v4_record_claimed_publication_closure_v1(uuid,jsonb)')
        ) OR current_setting('salience.claimed_publication_closure_v1',true) IS DISTINCT FROM NEW.claim_id::text THEN
            RAISE EXCEPTION 'claimed publication closure evidence requires its protected writer' USING ERRCODE='42501';
        END IF;
        RETURN NEW;
    END
"""

_ROUTINES = (
    (_LINEAGE, "uuid,uuid,uuid", "p_work_id uuid, p_permit_id uuid, p_claim_id uuid",
     "boolean", "sql", "STABLE", False, _LINEAGE_SOURCE),
    (_READER, "uuid", "p_work_id uuid", "jsonb", "plpgsql", "VOLATILE", True, _READER_SOURCE),
    (_WRITER, "uuid,jsonb", "p_work_id uuid, p_evidence jsonb", "jsonb", "plpgsql", "VOLATILE", True, _WRITER_SOURCE),
    (_GUARD, "", "", "trigger", "plpgsql", "VOLATILE", False, _GUARD_SOURCE),
)


def upgrade() -> None:
    op.execute(
        f"""CREATE TABLE {_TABLE} (
            schema_version text NOT NULL CHECK (schema_version='{_SCHEMA}'),
            work_id uuid NOT NULL UNIQUE REFERENCES public.v4_production_recovery_work(work_id) ON DELETE RESTRICT,
            workspace_id uuid NOT NULL REFERENCES public.workspaces(id) ON DELETE RESTRICT,
            case_id uuid NOT NULL REFERENCES public.v4_recovery_cases(id) ON DELETE RESTRICT,
            case_revision integer NOT NULL CHECK (case_revision=1),
            operation_id uuid NOT NULL,
            permit_id uuid NOT NULL,
            claim_id uuid PRIMARY KEY,
            generation integer NOT NULL CHECK (generation=1),
            request_fingerprint text NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{{64}}$'),
            environment_id uuid NOT NULL,
            namespace text NOT NULL CHECK (length(namespace) BETWEEN 1 AND 512 AND namespace !~ '[[:cntrl:]]'),
            barrier_id uuid NOT NULL,
            closure_id uuid NOT NULL,
            signer_sha256 text NOT NULL CHECK (signer_sha256 ~ '^[0-9a-f]{{64}}$'),
            signature_hex text NOT NULL CHECK (signature_hex ~ '^[0-9a-f]{{128}}$'),
            policy_decision_id uuid NOT NULL,
            route_decision_id uuid NOT NULL,
            authority_expires_at timestamptz NOT NULL,
            recorded_at timestamptz NOT NULL,
            CHECK (isfinite(recorded_at) AND isfinite(authority_expires_at) AND recorded_at<authority_expires_at),
            UNIQUE (environment_id,namespace,barrier_id,closure_id),
            FOREIGN KEY (permit_id,operation_id,generation)
              REFERENCES public.v4_production_effect_permits(permit_id,operation_id,generation) ON DELETE RESTRICT,
            FOREIGN KEY (claim_id,operation_id,generation)
              REFERENCES public.v4_production_effect_permit_claims(claim_id,operation_id,generation) ON DELETE RESTRICT
        )"""
    )
    op.execute(f"CREATE INDEX ix_v4_claimed_publication_closures_workspace ON {_TABLE}(workspace_id,claim_id)")
    op.execute(f"REVOKE ALL ON TABLE {_TABLE} FROM PUBLIC")
    for name, signature, params, returns, language, volatility, definer, body in _ROUTINES:
        security = "DEFINER" if definer else "INVOKER"
        op.execute(
            f"""CREATE FUNCTION {name}({params}) RETURNS {returns}
                LANGUAGE {language} {volatility} SECURITY {security}
                SET search_path=pg_catalog, public SET timezone='UTC'
                AS $function${body}$function$"""
        )
        op.execute(f"REVOKE ALL ON FUNCTION {name}({signature}) FROM PUBLIC")
    op.execute(
        f"""COMMENT ON FUNCTION {_WRITER}(uuid,jsonb) IS
            'Retains evidence only. Grant EXECUTE solely to the trusted intake service that verifies the signature and current route decision.'"""
    )
    for trigger, action, level in (
        ("guard_claimed_publication_closure_row", "INSERT OR UPDATE OR DELETE", "ROW"),
        ("guard_claimed_publication_closure_truncate", "TRUNCATE", "STATEMENT"),
    ):
        op.execute(
            f"""CREATE TRIGGER {trigger} BEFORE {action} ON {_TABLE}
                FOR EACH {level} EXECUTE FUNCTION {_GUARD}()"""
        )
        op.execute(f"ALTER TABLE {_TABLE} ENABLE ALWAYS TRIGGER {trigger}")
    _install_inventory()


def _install_inventory() -> None:
    from hashlib import sha256

    expectations = ",\n".join(
        f"('{name}({signature})','{returns}'::regtype,'{language}','{volatility[0].lower()}',"
        f"{str(definer).lower()},'{sha256(body.encode()).hexdigest()}')"
        for name, signature, _, returns, language, volatility, definer, body in _ROUTINES
    )
    op.execute(
        f"""CREATE FUNCTION {_INVENTORY}(p_workspace_id uuid)
            RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER
            SET search_path=pg_catalog, public SET timezone='UTC' AS $inventory$
            WITH prior AS (
                SELECT public.v4_p1_recovery_rollback_inventory_v7(p_workspace_id) AS inventory
            ), expected_routines(signature,return_type,language,volatility,definer,source_hash) AS (
                VALUES {expectations}
            ), expected_triggers(name,type_mask) AS (
                VALUES ('guard_claimed_publication_closure_row',31),
                       ('guard_claimed_publication_closure_truncate',34)
            ), closure_table AS (
                SELECT oid,relowner,relacl FROM pg_class WHERE oid=to_regclass('{_TABLE}') AND relkind='r'
            ), trusted AS MATERIALIZED (
                SELECT (
                    SELECT count(*)={len(_ROUTINES)} FROM expected_routines expected
                    JOIN pg_proc routine ON routine.oid=to_regprocedure(expected.signature)
                    JOIN pg_language language ON language.oid=routine.prolang
                    JOIN closure_table relation ON relation.relowner=routine.proowner
                    WHERE routine.prorettype=expected.return_type AND NOT routine.proretset
                      AND routine.prosecdef=expected.definer AND routine.provolatile::text=expected.volatility
                      AND language.lanname=expected.language
                      AND routine.proconfig=ARRAY['search_path=pg_catalog, public','TimeZone=UTC']
                      AND encode(sha256(convert_to(routine.prosrc,'UTF8')),'hex')=expected.source_hash
                      AND NOT EXISTS (
                          SELECT 1 FROM aclexplode(COALESCE(routine.proacl,acldefault('f',routine.proowner))) acl
                          WHERE acl.grantee=0 OR (acl.grantee<>routine.proowner AND (
                              acl.is_grantable OR expected.signature NOT IN ('{_READER}(uuid)','{_WRITER}(uuid,jsonb)')
                          ))
                      )
                ) AND (
                    SELECT count(*)=2 FROM expected_triggers expected
                    JOIN pg_trigger guard ON guard.tgrelid=to_regclass('{_TABLE}') AND guard.tgname=expected.name
                    WHERE guard.tgfoid=to_regprocedure('{_GUARD}()')
                      AND guard.tgtype=expected.type_mask AND guard.tgenabled='A'
                      AND NOT guard.tgisinternal AND guard.tgnargs=0 AND guard.tgqual IS NULL
                ) AND NOT EXISTS (
                    SELECT 1 FROM closure_table relation
                    CROSS JOIN LATERAL aclexplode(COALESCE(relation.relacl,acldefault('r',relation.relowner))) acl
                    WHERE acl.grantee<>relation.relowner AND (acl.grantee=0 OR acl.privilege_type<>'SELECT')
                ) AS definition
            ), closures AS (
                SELECT COALESCE(jsonb_agg(to_jsonb(closure) ORDER BY closure.claim_id),'[]'::jsonb) AS receipts,
                       count(*) AS total,
                       COALESCE(jsonb_agg(jsonb_build_object(
                           'kind','production_claimed_publication_closure_lineage_mismatch',
                           'work_id',closure.work_id,'claim_id',closure.claim_id
                       ) ORDER BY closure.claim_id) FILTER (WHERE trusted.definition AND NOT CASE
                           WHEN trusted.definition THEN (
                               {_LINEAGE}(closure.work_id,closure.permit_id,closure.claim_id)
                               AND closure.workspace_id=work.workspace_id AND closure.case_id=work.source_id
                               AND closure.case_revision=work.case_revision
                               AND closure.operation_id=work.operation_id
                               AND closure.request_fingerprint=work.request_fingerprint
                               AND closure.environment_id=permit.environment_id
                               AND (permit.request_snapshot->>'namespace' IS NULL
                                    OR closure.namespace=permit.request_snapshot->>'namespace')
                           ) IS TRUE ELSE false END),'[]'::jsonb) AS blockers
                FROM {_TABLE} closure CROSS JOIN trusted
                LEFT JOIN public.v4_production_recovery_work work ON work.work_id=closure.work_id
                LEFT JOIN public.v4_production_effect_permits permit ON permit.permit_id=closure.permit_id
                WHERE closure.workspace_id=p_workspace_id OR work.workspace_id=p_workspace_id
            )
            SELECT inventory || jsonb_build_object(
                'schema_version','salience.p1-recovery-rollback-inventory.v8','migration_head','{revision}',
                'claimed_publication_closures',closures.receipts,
                'relation_counts',(inventory->'relation_counts')
                    || jsonb_build_object('claimed_publication_closures',closures.total),
                'blockers',(inventory->'blockers') || closures.blockers || CASE WHEN trusted.definition
                    THEN '[]'::jsonb ELSE jsonb_build_array(jsonb_build_object(
                        'kind','production_claimed_publication_closure_definition_mismatch')) END)
            FROM prior CROSS JOIN closures CROSS JOIN trusted
            $inventory$"""
    )
    op.execute(f"REVOKE ALL ON FUNCTION {_INVENTORY}(uuid) FROM PUBLIC")


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout='3s'")
    op.execute(
        f"""DO $downgrade$ BEGIN
            LOCK TABLE {_TABLE} IN ACCESS EXCLUSIVE MODE;
            IF EXISTS (SELECT 1 FROM {_TABLE}) THEN
                RAISE EXCEPTION 'claimed publication closure evidence prevents destructive downgrade';
            END IF;
            END $downgrade$"""
    )
    op.execute(f"DROP FUNCTION {_INVENTORY}(uuid)")
    op.execute(f"DROP TABLE {_TABLE}")
    for name, signature, *_ in reversed(_ROUTINES):
        op.execute(f"DROP FUNCTION {name}({signature})")
