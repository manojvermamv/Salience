"""Add production effect permit history and terminal authority revisions."""

from alembic import op


revision = "0044_production_policy_revocation"
down_revision = "0043_complete_publication_recovery_liabilities"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE public.v4_production_effect_permits (
            permit_id uuid PRIMARY KEY,
            operation_id uuid NOT NULL,
            generation integer NOT NULL CHECK (generation > 0),
            workspace_id uuid NOT NULL REFERENCES public.workspaces(id) ON DELETE RESTRICT,
            goal_id uuid NOT NULL REFERENCES public.v4_goals(id) ON DELETE RESTRICT,
            goal_revision integer NOT NULL CHECK (goal_revision > 0),
            program_id uuid NOT NULL REFERENCES public.content_programs(id) ON DELETE RESTRICT,
            intent_id uuid NOT NULL REFERENCES public.v4_cycle_intents(id) ON DELETE RESTRICT,
            cycle_id uuid NOT NULL REFERENCES public.v4_cycles(id) ON DELETE RESTRICT,
            context_id uuid NOT NULL REFERENCES public.v4_run_contexts(id) ON DELETE RESTRICT,
            subject_id uuid NOT NULL REFERENCES public.identity_subjects(id) ON DELETE RESTRICT,
            subject_revision bigint NOT NULL CHECK (subject_revision > 0),
            grant_ids jsonb NOT NULL CHECK (jsonb_typeof(grant_ids) = 'array'),
            policy_version_id uuid NOT NULL REFERENCES public.policy_versions(id) ON DELETE RESTRICT,
            policy_document_hash text NOT NULL CHECK (policy_document_hash ~ '^[0-9a-f]{64}$'),
            approval_request_id uuid NOT NULL REFERENCES public.approval_requests(id) ON DELETE RESTRICT,
            approval_expires_at timestamptz NOT NULL,
            effect_type text NOT NULL CHECK (effect_type <> 'fixture_execution'),
            purpose text NOT NULL,
            account_id text NOT NULL CHECK (account_id <> 'fixture-account'),
            provider_id text NOT NULL CHECK (provider_id <> 'fixture.noop'),
            artifact_sha256 text NOT NULL CHECK (artifact_sha256 ~ '^[0-9a-f]{64}$'),
            deployment_id uuid NOT NULL,
            environment_id uuid NOT NULL,
            route_id uuid NOT NULL,
            slot_id uuid NOT NULL,
            owner_id uuid NOT NULL,
            fencing_epoch bigint NOT NULL CHECK (fencing_epoch > 0),
            route_manifest_sha256 text NOT NULL CHECK (route_manifest_sha256 ~ '^[0-9a-f]{64}$'),
            artifact_lock_sha256 text NOT NULL CHECK (artifact_lock_sha256 ~ '^[0-9a-f]{64}$'),
            route_fence_decision_id uuid NOT NULL,
            issued_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            expires_at timestamptz NOT NULL,
            request_snapshot jsonb NOT NULL,
            CONSTRAINT uq_v4_production_effect_permits_operation_generation
                UNIQUE (operation_id, generation),
            CONSTRAINT uq_v4_production_effect_permits_identity
                UNIQUE (permit_id, operation_id, generation),
            CONSTRAINT ck_v4_production_effect_permits_expiry
                CHECK (
                    expires_at > issued_at
                    AND expires_at <= issued_at + interval '60 seconds'
                ),
            CONSTRAINT ck_v4_production_effect_permits_snapshot
                CHECK (
                    jsonb_typeof(request_snapshot) = 'object'
                    AND request_snapshot->>'schema_version' =
                        'salience.p1-production-effect-permit.v1'
                    AND request_snapshot->>'generation' = generation::text
                    AND request_snapshot->>'operation_id' = operation_id::text
                    AND request_snapshot->>'workspace_id' = workspace_id::text
                    AND request_snapshot->>'idempotency_key' IS NOT NULL
                )
        )"""
    )
    op.execute(
        """CREATE TABLE public.v4_production_effect_permit_claims (
            claim_id uuid PRIMARY KEY,
            permit_id uuid NOT NULL,
            operation_id uuid NOT NULL,
            generation integer NOT NULL CHECK (generation > 0),
            route_fence_decision_id uuid NOT NULL,
            claimed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            CONSTRAINT uq_v4_production_effect_permit_claims_permit UNIQUE (permit_id),
            CONSTRAINT uq_v4_production_effect_permit_claims_operation_generation
                UNIQUE (operation_id, generation),
            CONSTRAINT uq_v4_production_effect_permit_claims_identity
                UNIQUE (claim_id, operation_id, generation),
            CONSTRAINT fk_v4_production_effect_permit_claims_permit
                FOREIGN KEY (permit_id, operation_id, generation)
                REFERENCES public.v4_production_effect_permits
                    (permit_id, operation_id, generation)
                ON DELETE RESTRICT
        )"""
    )
    op.execute(
        """CREATE TABLE public.v4_production_current_dispatch (
            operation_id uuid PRIMARY KEY,
            generation integer NOT NULL CHECK (generation > 0),
            current_permit_id uuid NOT NULL,
            current_claim_id uuid,
            idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 256),
            CONSTRAINT fk_v4_production_current_dispatch_permit
                FOREIGN KEY (current_permit_id, operation_id, generation)
                REFERENCES public.v4_production_effect_permits
                    (permit_id, operation_id, generation)
                ON DELETE RESTRICT,
            CONSTRAINT fk_v4_production_current_dispatch_claim
                FOREIGN KEY (current_claim_id, operation_id, generation)
                REFERENCES public.v4_production_effect_permit_claims
                    (claim_id, operation_id, generation)
                ON DELETE RESTRICT
        )"""
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON TABLE public.v4_production_effect_permits FROM PUBLIC"
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON TABLE public.v4_production_effect_permit_claims FROM PUBLIC"
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON TABLE public.v4_production_current_dispatch FROM PUBLIC"
    )

    op.execute(
        """CREATE FUNCTION public.v4_validate_production_effect_permit_v1()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public AS $$
        DECLARE
            snapshot jsonb;
            optional_identifier_key text;
            approval_expiry timestamptz;
        BEGIN
            IF TG_RELID <> 'public.v4_production_effect_permits'::regclass
               OR TG_TABLE_SCHEMA <> 'public'
               OR TG_TABLE_NAME <> 'v4_production_effect_permits' THEN
                RAISE EXCEPTION 'production permit validator only permits its target table';
            END IF;
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'production effect permits are immutable';
            END IF;
            IF NEW.expires_at > NEW.issued_at + interval '60 seconds' THEN
                RAISE EXCEPTION 'production permit expiry must not exceed 60 seconds';
            END IF;
            snapshot := NEW.request_snapshot;
            IF jsonb_typeof(snapshot) IS DISTINCT FROM 'object'
               OR snapshot->>'schema_version' IS DISTINCT FROM
                    'salience.p1-production-effect-permit.v1'
               OR snapshot->>'generation' IS DISTINCT FROM NEW.generation::text
               OR snapshot->>'workspace_id' IS DISTINCT FROM NEW.workspace_id::text
               OR snapshot->>'goal_id' IS DISTINCT FROM NEW.goal_id::text
               OR snapshot->>'goal_revision' IS DISTINCT FROM NEW.goal_revision::text
               OR snapshot->>'program_id' IS DISTINCT FROM NEW.program_id::text
               OR snapshot->>'intent_id' IS DISTINCT FROM NEW.intent_id::text
               OR snapshot->>'cycle_id' IS DISTINCT FROM NEW.cycle_id::text
               OR snapshot->>'context_id' IS DISTINCT FROM NEW.context_id::text
               OR snapshot->>'operation_id' IS DISTINCT FROM NEW.operation_id::text
               OR snapshot->>'subject_id' IS DISTINCT FROM NEW.subject_id::text
               OR snapshot->>'subject_revision' IS DISTINCT FROM NEW.subject_revision::text
               OR snapshot->'grant_ids' IS DISTINCT FROM NEW.grant_ids
               OR snapshot->>'policy_version_id' IS DISTINCT FROM NEW.policy_version_id::text
               OR snapshot->>'policy_document_hash' IS DISTINCT FROM NEW.policy_document_hash
               OR snapshot->>'approval_request_id' IS DISTINCT FROM NEW.approval_request_id::text
               OR (snapshot->>'approval_expires_at')::timestamptz
                    IS DISTINCT FROM NEW.approval_expires_at
               OR snapshot->>'effect_type' IS DISTINCT FROM NEW.effect_type
               OR snapshot->>'purpose' IS DISTINCT FROM NEW.purpose
               OR snapshot->>'account_id' IS DISTINCT FROM NEW.account_id
               OR snapshot->>'provider_id' IS DISTINCT FROM NEW.provider_id
               OR snapshot->>'artifact_sha256' IS DISTINCT FROM NEW.artifact_sha256
               OR snapshot->>'deployment_id' IS DISTINCT FROM NEW.deployment_id::text
               OR snapshot->>'environment_id' IS DISTINCT FROM NEW.environment_id::text
               OR snapshot->>'route_id' IS DISTINCT FROM NEW.route_id::text
               OR snapshot->>'slot_id' IS DISTINCT FROM NEW.slot_id::text
               OR snapshot->>'owner_id' IS DISTINCT FROM NEW.owner_id::text
               OR snapshot->>'fencing_epoch' IS DISTINCT FROM NEW.fencing_epoch::text
               OR snapshot->>'route_manifest_sha256' IS DISTINCT FROM NEW.route_manifest_sha256
               OR snapshot->>'artifact_lock_sha256' IS DISTINCT FROM NEW.artifact_lock_sha256
               OR snapshot->>'route_fence_decision_id'
                    IS DISTINCT FROM NEW.route_fence_decision_id::text
               OR snapshot->>'idempotency_key' IS NULL THEN
                RAISE EXCEPTION 'complete production permit request snapshot must match its bindings';
            END IF;
            IF NEW.effect_type = 'fixture_execution'
               OR NEW.account_id = 'fixture-account'
               OR NEW.provider_id = 'fixture.noop'
               OR snapshot::text LIKE '%fixture-account%'
               OR snapshot::text LIKE '%fixture_execution%'
               OR snapshot::text LIKE '%fixture.noop%' THEN
                RAISE EXCEPTION 'fixture identities cannot authorize production effects';
            END IF;
            FOREACH optional_identifier_key IN ARRAY ARRAY[
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
                IF snapshot ? optional_identifier_key
                   AND (jsonb_typeof(snapshot->optional_identifier_key)
                            IS DISTINCT FROM 'string'
                        OR length(btrim(snapshot->>optional_identifier_key)) = 0) THEN
                    RAISE EXCEPTION
                        'optional publication binding must be a string identifier';
                END IF;
            END LOOP;
            SELECT (approval.request_context->>'expires_at')::timestamptz
            INTO approval_expiry
            FROM public.approval_requests approval
            WHERE approval.id = NEW.approval_request_id
              AND approval.workspace_id = NEW.workspace_id
              AND approval.policy_version_id = NEW.policy_version_id
              AND approval.effect_type = NEW.effect_type
              AND approval.status = 'approved'
              AND approval.request_context->>'schema_version' =
                  'salience.p1-effect-approval.v1';
            IF NOT FOUND
               OR approval_expiry IS DISTINCT FROM NEW.approval_expires_at
               OR NEW.expires_at > approval_expiry THEN
                RAISE EXCEPTION
                    'production permit cannot expire after canonical approval';
            END IF;
            RETURN NEW;
        END
        $$"""
    )
    op.execute(
        """CREATE FUNCTION public.v4_reject_production_history_change_v1()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public AS $$
        BEGIN
            IF TG_RELID NOT IN (
                'public.v4_production_effect_permits'::regclass,
                'public.v4_production_effect_permit_claims'::regclass
            ) OR TG_TABLE_SCHEMA <> 'public' THEN
                RAISE EXCEPTION 'production history guard only permits its target tables';
            END IF;
            RAISE EXCEPTION 'production permit and claim history is append-only';
        END
        $$"""
    )
    op.execute(
        """CREATE TRIGGER validate_v4_production_effect_permit_v1
           BEFORE INSERT ON public.v4_production_effect_permits
           FOR EACH ROW EXECUTE FUNCTION public.v4_validate_production_effect_permit_v1()"""
    )
    op.execute(
        """CREATE TRIGGER immutable_v4_production_effect_permits_v1
           BEFORE UPDATE OR DELETE ON public.v4_production_effect_permits
           FOR EACH ROW EXECUTE FUNCTION public.v4_reject_production_history_change_v1()"""
    )
    op.execute(
        """CREATE TRIGGER immutable_v4_production_effect_permit_claims_v1
           BEFORE UPDATE OR DELETE ON public.v4_production_effect_permit_claims
           FOR EACH ROW EXECUTE FUNCTION public.v4_reject_production_history_change_v1()"""
    )
    op.execute(
        """CREATE FUNCTION public.v4_guard_production_current_dispatch_write_v1()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog, public AS $$
        DECLARE
            advance_function_owner text;
        BEGIN
            IF TG_RELID <> 'public.v4_production_current_dispatch'::regclass
               OR TG_TABLE_SCHEMA <> 'public'
               OR TG_TABLE_NAME <> 'v4_production_current_dispatch' THEN
                RAISE EXCEPTION 'current dispatch guard only permits its target table';
            END IF;
            IF current_setting('salience.production_current_dispatch_v1', true)
                 IS DISTINCT FROM 'advance' THEN
                RAISE EXCEPTION 'production current dispatch may only change through v1 function';
            END IF;
            SELECT pg_catalog.pg_get_userbyid(function.proowner)
            INTO advance_function_owner
            FROM pg_catalog.pg_proc function
            WHERE function.oid = pg_catalog.to_regprocedure(
                'public.v4_advance_production_current_dispatch_v1(uuid,integer,uuid,uuid,text,uuid)'
            );
            IF advance_function_owner IS NULL
               OR current_user IS DISTINCT FROM advance_function_owner THEN
                RAISE EXCEPTION
                    'production current dispatch writes require the v1 function owner';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'production current dispatch cannot be deleted';
            END IF;
            RETURN NEW;
        END
        $$"""
    )
    op.execute(
        """CREATE TRIGGER restricted_v4_production_current_dispatch_write_v1
           BEFORE INSERT OR UPDATE OR DELETE ON public.v4_production_current_dispatch
           FOR EACH ROW EXECUTE FUNCTION
             public.v4_guard_production_current_dispatch_write_v1()"""
    )
    op.execute(
        """CREATE FUNCTION public.v4_advance_production_current_dispatch_v1(
            operation_id uuid,
            expected_generation integer,
            permit_id uuid,
            claim_id uuid,
            idempotency_key text,
            recovery_command_id uuid DEFAULT NULL
        ) RETURNS void
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            permit_row public.v4_production_effect_permits%ROWTYPE;
            claim_row public.v4_production_effect_permit_claims%ROWTYPE;
            current_row public.v4_production_current_dispatch%ROWTYPE;
            has_current boolean;
        BEGIN
            IF operation_id IS NULL OR expected_generation IS NULL
               OR expected_generation < 0 OR permit_id IS NULL
               OR idempotency_key IS NULL
               OR length(idempotency_key) NOT BETWEEN 1 AND 256 THEN
                RAISE EXCEPTION 'valid production dispatch advance binding required';
            END IF;
            PERFORM pg_advisory_xact_lock(hashtextextended(operation_id::text, 0));
            SELECT permit.* INTO permit_row
            FROM public.v4_production_effect_permits permit
            WHERE permit.permit_id = v4_advance_production_current_dispatch_v1.permit_id
              AND permit.operation_id = v4_advance_production_current_dispatch_v1.operation_id
            FOR KEY SHARE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'production permit binding not found';
            END IF;
            IF permit_row.request_snapshot->>'idempotency_key'
                 IS DISTINCT FROM idempotency_key THEN
                RAISE EXCEPTION 'original production idempotency key required';
            END IF;
            IF permit_row.expires_at <= clock_timestamp() THEN
                RAISE EXCEPTION 'production permit has expired';
            END IF;
            IF claim_id IS NOT NULL THEN
                SELECT claim.* INTO claim_row
                FROM public.v4_production_effect_permit_claims claim
                WHERE claim.claim_id = v4_advance_production_current_dispatch_v1.claim_id
                  AND claim.permit_id = permit_row.permit_id
                  AND claim.operation_id = permit_row.operation_id
                  AND claim.generation = permit_row.generation
                FOR KEY SHARE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'production claim binding not found';
                END IF;
            END IF;
            SELECT dispatch.* INTO current_row
            FROM public.v4_production_current_dispatch dispatch
            WHERE dispatch.operation_id =
                  v4_advance_production_current_dispatch_v1.operation_id
            FOR UPDATE;
            has_current := FOUND;
            IF NOT has_current THEN
                IF expected_generation <> 0 THEN
                    RAISE EXCEPTION 'production dispatch generation changed';
                END IF;
                IF permit_row.generation > 1 THEN
                    RAISE EXCEPTION 'typed_no_effect_command_required';
                END IF;
                IF permit_row.generation <> 1 THEN
                    RAISE EXCEPTION 'first production dispatch generation must be one';
                END IF;
                IF recovery_command_id IS NOT NULL THEN
                    RAISE EXCEPTION 'typed_no_effect_command_required';
                END IF;
                PERFORM set_config(
                    'salience.production_current_dispatch_v1', 'advance', true
                );
                INSERT INTO public.v4_production_current_dispatch(
                    operation_id, generation, current_permit_id, current_claim_id,
                    idempotency_key
                ) VALUES (
                    operation_id, 1, permit_id, claim_id, idempotency_key
                );
                PERFORM set_config(
                    'salience.production_current_dispatch_v1', '', true
                );
                RETURN;
            END IF;
            IF current_row.generation <> expected_generation THEN
                RAISE EXCEPTION 'production dispatch generation changed';
            END IF;
            IF permit_row.generation > current_row.generation THEN
                RAISE EXCEPTION 'typed_no_effect_command_required';
            END IF;
            IF permit_row.generation <> current_row.generation
               OR permit_row.permit_id <> current_row.current_permit_id THEN
                RAISE EXCEPTION 'production current permit identity is immutable';
            END IF;
            IF current_row.idempotency_key IS DISTINCT FROM idempotency_key THEN
                RAISE EXCEPTION 'original production idempotency key required';
            END IF;
            IF current_row.current_claim_id IS NOT NULL
               AND current_row.current_claim_id IS DISTINCT FROM claim_id THEN
                RAISE EXCEPTION 'production generation already has its one claim';
            END IF;
            IF current_row.current_claim_id IS NOT DISTINCT FROM claim_id THEN
                RETURN;
            END IF;
            PERFORM set_config(
                'salience.production_current_dispatch_v1', 'advance', true
            );
            UPDATE public.v4_production_current_dispatch AS dispatch
            SET current_claim_id =
                v4_advance_production_current_dispatch_v1.claim_id
            WHERE dispatch.operation_id =
                  v4_advance_production_current_dispatch_v1.operation_id;
            PERFORM set_config(
                'salience.production_current_dispatch_v1', '', true
            );
        END
        $$"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.v4_advance_production_current_dispatch_v1(uuid,integer,uuid,uuid,text,uuid) FROM PUBLIC"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.v4_guard_production_current_dispatch_write_v1() FROM PUBLIC"
    )

    op.execute(
        """CREATE FUNCTION public.v4_advance_identity_subject_revision_from_grant_v1()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public AS $$
        DECLARE
            old_subject_id uuid;
            new_subject_id uuid;
            subject_to_advance uuid;
        BEGIN
            IF TG_RELID <> 'public.permission_grants'::regclass
               OR TG_TABLE_SCHEMA <> 'public'
               OR TG_TABLE_NAME <> 'permission_grants' THEN
                RAISE EXCEPTION 'identity revision trigger only permits permission_grants';
            END IF;
            IF TG_OP = 'UPDATE'
               AND ROW(OLD.workspace_id, OLD.principal_type, OLD.principal_id, OLD.scope,
                       OLD.effect, OLD.constraints, OLD.expires_at)
                   IS NOT DISTINCT FROM
                   ROW(NEW.workspace_id, NEW.principal_type, NEW.principal_id, NEW.scope,
                       NEW.effect, NEW.constraints, NEW.expires_at) THEN
                RETURN NEW;
            END IF;
            IF TG_OP <> 'INSERT' AND OLD.principal_type = 'identity' THEN
                SELECT identity.id INTO old_subject_id
                FROM public.identity_subjects identity
                WHERE identity.id::text = OLD.principal_id
                  AND identity.workspace_id = OLD.workspace_id;
            END IF;
            IF TG_OP <> 'DELETE' AND NEW.principal_type = 'identity' THEN
                SELECT identity.id INTO new_subject_id
                FROM public.identity_subjects identity
                WHERE identity.id::text = NEW.principal_id
                  AND identity.workspace_id = NEW.workspace_id;
            END IF;
            FOR subject_to_advance IN
                SELECT identity.id
                FROM public.identity_subjects identity
                WHERE identity.id = old_subject_id OR identity.id = new_subject_id
                ORDER BY identity.id
            LOOP
                UPDATE public.identity_subjects
                SET revision = revision + 1
                WHERE id = subject_to_advance;
            END LOOP;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END
        $$"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.v4_advance_identity_subject_revision_from_grant_v1() FROM PUBLIC"
    )
    op.execute(
        """CREATE TRIGGER v4_permission_grant_subject_revision_v1
           AFTER INSERT OR UPDATE OR DELETE ON public.permission_grants
           FOR EACH ROW EXECUTE FUNCTION
             public.v4_advance_identity_subject_revision_from_grant_v1()"""
    )

    op.execute(
        """CREATE FUNCTION public.v4_guard_policy_version_transition_v1()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public AS $$
        BEGIN
            IF TG_RELID <> 'public.policy_versions'::regclass
               OR TG_TABLE_SCHEMA <> 'public'
               OR TG_TABLE_NAME <> 'policy_versions' THEN
                RAISE EXCEPTION 'policy transition guard only permits policy_versions';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'policy version history is immutable';
            END IF;
            IF OLD.status <> 'draft'
               AND ROW(OLD.id, OLD.tenant_id, OLD.workspace_id, OLD.policy_name,
                       OLD.version, OLD.document, OLD.document_hash,
                       OLD.effective_at, OLD.jurisdiction_refs)
                   IS DISTINCT FROM
                   ROW(NEW.id, NEW.tenant_id, NEW.workspace_id, NEW.policy_name,
                       NEW.version, NEW.document, NEW.document_hash,
                       NEW.effective_at, NEW.jurisdiction_refs) THEN
                RAISE EXCEPTION 'activated policy fields are immutable';
            END IF;
            IF OLD.status <> 'draft' AND NEW.status IN ('draft', 'active') THEN
                RAISE EXCEPTION 'non-draft policy cannot return to draft or active';
            END IF;
            RETURN NEW;
        END
        $$"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.v4_guard_policy_version_transition_v1() FROM PUBLIC"
    )
    op.execute(
        """CREATE TRIGGER guard_policy_version_transition_v1
           BEFORE UPDATE OR DELETE ON public.policy_versions
           FOR EACH ROW EXECUTE FUNCTION public.v4_guard_policy_version_transition_v1()"""
    )
    op.execute(
        """CREATE FUNCTION public.v4_guard_effect_approval_transition_v1()
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
                'policy_version_id',
                'policy_document_hash',
                'expires_at'
            ]::text[];
            identifier_key text;
            approval_expiry timestamptz;
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
                'policy_version_id'
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
        $$"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.v4_guard_effect_approval_transition_v1() FROM PUBLIC"
    )
    op.execute(
        """CREATE TRIGGER guard_effect_approval_transition_v1
           BEFORE INSERT OR UPDATE ON public.approval_requests
           FOR EACH ROW EXECUTE FUNCTION public.v4_guard_effect_approval_transition_v1()"""
    )
    op.execute(
        """CREATE FUNCTION public.v4_production_policy_rollback_inventory_v1(
            workspace_id uuid
        ) RETURNS jsonb
        LANGUAGE plpgsql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
            inventory jsonb := '[]'::jsonb;
            relation_spec record;
            relation_rows jsonb;
            relation_count bigint;
            relation_identity text;
        BEGIN
            IF workspace_id IS NULL THEN
                RAISE EXCEPTION 'rollback inventory workspace_id is required';
            END IF;
            FOR relation_spec IN
                SELECT * FROM (VALUES
                    ('authority','approval_requests',
                     'item.workspace_id = $1',
                     'COALESCE(to_jsonb(item)->>''id'', to_jsonb(item)::text)'),
                    ('authority','identity_subjects',
                     'item.workspace_id = $1',
                     'COALESCE(to_jsonb(item)->>''id'', to_jsonb(item)::text)'),
                    ('authority','permission_grants',
                     'item.workspace_id = $1',
                     'COALESCE(to_jsonb(item)->>''id'', to_jsonb(item)::text)'),
                    ('authority','policy_versions',
                     '(item.workspace_id = $1 OR item.workspace_id IS NULL)',
                     'COALESCE(to_jsonb(item)->>''id'', to_jsonb(item)::text)'),
                    ('authority','v4_stop_commands',
                     'item.workspace_id = $1',
                     'COALESCE(to_jsonb(item)->>''id'', to_jsonb(item)::text)'),
                    ('authority','v4_stop_scopes',
                     'item.workspace_id = $1',
                     'COALESCE(to_jsonb(item)->>''scope_key'', to_jsonb(item)::text)'),
                    ('cycle','v4_baseline_revocations',
                     'EXISTS (SELECT 1 FROM public.identity_subjects subject
                       WHERE subject.id=item.subject_id AND subject.workspace_id=$1)',
                     'COALESCE(to_jsonb(item)->>''approval_id'', to_jsonb(item)::text)'),
                    ('cycle','v4_cycle_allocations',
                     'item.workspace_id = $1',
                     'COALESCE(to_jsonb(item)->>''id'', to_jsonb(item)::text)'),
                    ('cycle','v4_cost_commands',
                     'EXISTS (SELECT 1 FROM public.v4_cycle_allocations allocation
                       WHERE allocation.id=item.allocation_id
                         AND allocation.workspace_id=$1)',
                     'COALESCE(to_jsonb(item)->>''id'', to_jsonb(item)::text)'),
                    ('cycle','v4_cost_operations',
                     'EXISTS (SELECT 1 FROM public.v4_cycle_allocations allocation
                       WHERE allocation.id=item.allocation_id
                         AND allocation.workspace_id=$1)',
                     'COALESCE(to_jsonb(item)->>''id'', to_jsonb(item)::text)'),
                    ('cycle','v4_allocation_reservations',
                     'EXISTS (SELECT 1 FROM public.v4_cycle_allocations allocation
                       WHERE allocation.id=item.allocation_id
                         AND allocation.workspace_id=$1)',
                     'COALESCE(to_jsonb(item)->>''reservation_id'', to_jsonb(item)::text)'),
                    ('cycle','v4_cycle_inbox',
                     'EXISTS (SELECT 1 FROM public.v4_cycle_outbox outbox
                       WHERE outbox.id=item.message_id AND outbox.workspace_id=$1)',
                     'COALESCE(to_jsonb(item)->>''id'', to_jsonb(item)::text)'),
                    ('cycle','content_programs',
                     'item.workspace_id = $1',
                     'COALESCE(to_jsonb(item)->>''id'', to_jsonb(item)::text)'),
                    ('cycle','v4_admissions',
                     'EXISTS (SELECT 1 FROM public.v4_cycle_intents intent
                       JOIN public.v4_goals goal ON goal.id=intent.goal_id
                       WHERE intent.id=item.intent_id AND goal.workspace_id=$1)',
                     'COALESCE(to_jsonb(item)->>''id'', to_jsonb(item)::text)'),
                    ('cycle','v4_cycle_events',
                     'item.workspace_id = $1',
                     'COALESCE(to_jsonb(item)->>''id'', to_jsonb(item)::text)'),
                    ('cycle','v4_cycle_intents',
                     'EXISTS (SELECT 1 FROM public.v4_goals goal
                       WHERE goal.id=item.goal_id AND goal.workspace_id=$1)',
                     'COALESCE(to_jsonb(item)->>''id'', to_jsonb(item)::text)'),
                    ('cycle','v4_cycle_outbox',
                     'item.workspace_id = $1',
                     'COALESCE(to_jsonb(item)->>''id'', to_jsonb(item)::text)'),
                    ('cycle','v4_cycle_requests',
                     'EXISTS (SELECT 1 FROM public.v4_goals goal
                       WHERE goal.id=item.goal_id AND goal.workspace_id=$1)',
                     'COALESCE(to_jsonb(item)->>''idempotency_key'', to_jsonb(item)::text)'),
                    ('cycle','v4_cycles',
                     'EXISTS (SELECT 1 FROM public.v4_cycle_intents intent
                       JOIN public.v4_goals goal ON goal.id=intent.goal_id
                       WHERE intent.id=item.intent_id AND goal.workspace_id=$1)',
                     'COALESCE(to_jsonb(item)->>''id'', to_jsonb(item)::text)'),
                    ('cycle','v4_goal_baselines',
                     'EXISTS (SELECT 1 FROM public.v4_goals goal
                       WHERE goal.id=item.goal_id AND goal.workspace_id=$1)',
                     'COALESCE(to_jsonb(item)->>''approval_id'', to_jsonb(item)::text)'),
                    ('cycle','v4_goal_revisions',
                     'EXISTS (SELECT 1 FROM public.v4_goals goal
                       WHERE goal.id=item.goal_id AND goal.workspace_id=$1)',
                     'COALESCE(to_jsonb(item)->>''goal_id'', to_jsonb(item)::text)'),
                    ('cycle','v4_goal_commands',
                     'EXISTS (SELECT 1 FROM public.v4_goals goal
                       WHERE goal.id=item.goal_id AND goal.workspace_id=$1)',
                     'COALESCE(to_jsonb(item)->>''idempotency_key'', to_jsonb(item)::text)'),
                    ('cycle','v4_goal_create_commands',
                     'item.workspace_id = $1',
                     'COALESCE(to_jsonb(item)->>''idempotency_key'', to_jsonb(item)::text)'),
                    ('cycle','v4_goal_state_commands',
                     'item.workspace_id = $1',
                     'COALESCE(to_jsonb(item)->>''idempotency_key'', to_jsonb(item)::text)'),
                    ('cycle','v4_goals',
                     'item.workspace_id = $1',
                     'COALESCE(to_jsonb(item)->>''id'', to_jsonb(item)::text)'),
                    ('cycle','v4_intent_successors',
                     'EXISTS (SELECT 1 FROM public.v4_cycles cycle
                       JOIN public.v4_cycle_intents intent ON intent.id=cycle.intent_id
                       JOIN public.v4_goals goal ON goal.id=intent.goal_id
                       WHERE cycle.id=item.predecessor_cycle_id
                         AND goal.workspace_id=$1)',
                     'COALESCE(to_jsonb(item)->>''predecessor_cycle_id'', to_jsonb(item)::text)'),
                    ('cycle','v4_run_contexts',
                     'EXISTS (SELECT 1 FROM public.v4_cycles cycle
                       JOIN public.v4_cycle_intents intent ON intent.id=cycle.intent_id
                       JOIN public.v4_goals goal ON goal.id=intent.goal_id
                       WHERE cycle.id=item.cycle_id AND goal.workspace_id=$1)',
                     'COALESCE(to_jsonb(item)->>''id'', to_jsonb(item)::text)'),
                    ('fixture','v4_dispatch_permits',
                     'EXISTS (SELECT 1 FROM public.v4_permit_claims claim
                       JOIN public.v4_cycles cycle ON cycle.id=claim.cycle_id
                       JOIN public.v4_cycle_intents intent ON intent.id=cycle.intent_id
                       JOIN public.v4_goals goal ON goal.id=intent.goal_id
                       WHERE claim.id=item.claim_id AND goal.workspace_id=$1)',
                     'COALESCE(to_jsonb(item)->>''id'', to_jsonb(item)::text)'),
                    ('fixture','v4_permit_claims',
                     'EXISTS (SELECT 1 FROM public.v4_cycles cycle
                       JOIN public.v4_cycle_intents intent ON intent.id=cycle.intent_id
                       JOIN public.v4_goals goal ON goal.id=intent.goal_id
                       WHERE cycle.id=item.cycle_id AND goal.workspace_id=$1)',
                     'COALESCE(to_jsonb(item)->>''id'', to_jsonb(item)::text)'),
                    ('fixture','v4_legacy_dispatches',
                     'item.workspace_id = $1',
                     'COALESCE(to_jsonb(item)->>''operation_id'', to_jsonb(item)::text)'),
                    ('fixture','v4_legacy_commands',
                     'item.workspace_id = $1',
                     'COALESCE(to_jsonb(item)->>''idempotency_key'', to_jsonb(item)::text)'),
                    ('fixture','v4_legacy_publication_accounts',
                     'item.workspace_id = $1',
                     'COALESCE(to_jsonb(item)->>''goal_id'', to_jsonb(item)::text)'),
                    ('liability','budget_reservations',
                     'EXISTS (SELECT 1 FROM public.budgets budget
                       WHERE budget.id=item.budget_id AND budget.workspace_id=$1)',
                     'COALESCE(to_jsonb(item)->>''id'', to_jsonb(item)::text)'),
                    ('liability','external_effects',
                     'item.workspace_id = $1',
                     'COALESCE(to_jsonb(item)->>''id'', to_jsonb(item)::text)'),
                    ('liability','jobs',
                     'item.workspace_id = $1',
                     'COALESCE(to_jsonb(item)->>''id'', to_jsonb(item)::text)'),
                    ('liability','publication_attempts',
                     'EXISTS (SELECT 1 FROM public.publication_plans plan
                       JOIN public.publication_requests request
                         ON request.id=plan.publication_request_id
                       WHERE plan.id=item.publication_plan_id
                         AND request.workspace_id=$1)',
                     'COALESCE(to_jsonb(item)->>''id'', to_jsonb(item)::text)'),
                    ('liability','publication_plans',
                     'EXISTS (SELECT 1 FROM public.publication_requests request
                       WHERE request.id=item.publication_request_id
                         AND request.workspace_id=$1)',
                     'COALESCE(to_jsonb(item)->>''id'', to_jsonb(item)::text)'),
                    ('liability','publication_requests',
                     'item.workspace_id = $1',
                     'COALESCE(to_jsonb(item)->>''id'', to_jsonb(item)::text)'),
                    ('liability','publication_status_events',
                     'EXISTS (SELECT 1 FROM public.publication_attempts attempt
                       JOIN public.publication_plans plan
                         ON plan.id=attempt.publication_plan_id
                       JOIN public.publication_requests request
                         ON request.id=plan.publication_request_id
                       WHERE attempt.id=item.publication_attempt_id
                         AND request.workspace_id=$1)',
                     'COALESCE(to_jsonb(item)->>''id'', to_jsonb(item)::text)'),
                    ('liability','publications',
                     'EXISTS (SELECT 1 FROM public.publication_requests request
                       WHERE request.id=item.publication_request_id
                         AND request.workspace_id=$1)',
                     'COALESCE(to_jsonb(item)->>''id'', to_jsonb(item)::text)'),
                    ('liability','publisher_webhook_receipts',
                     'EXISTS (SELECT 1 FROM public.publication_attempts attempt
                       JOIN public.publication_plans plan
                         ON plan.id=attempt.publication_plan_id
                       JOIN public.publication_requests request
                         ON request.id=plan.publication_request_id
                       WHERE attempt.id=item.publication_attempt_id
                         AND request.workspace_id=$1)',
                     'COALESCE(to_jsonb(item)->>''id'', to_jsonb(item)::text)'),
                    ('liability','remote_publication_receipts',
                     'EXISTS (SELECT 1 FROM public.publication_attempts attempt
                       JOIN public.publication_plans plan
                         ON plan.id=attempt.publication_plan_id
                       JOIN public.publication_requests request
                         ON request.id=plan.publication_request_id
                       WHERE attempt.id=item.publication_attempt_id
                         AND request.workspace_id=$1)',
                     'COALESCE(to_jsonb(item)->>''id'', to_jsonb(item)::text)'),
                    ('schedule','v4_native_publication_schedule_progress',
                     'EXISTS (SELECT 1 FROM public.v4_native_publication_schedule_sources source
                       WHERE source.schedule_id=item.schedule_id
                         AND source.workspace_id=$1)',
                     'COALESCE(to_jsonb(item)->>''schedule_id'', to_jsonb(item)::text)'),
                    ('schedule','v4_native_publication_schedule_sources',
                     'item.workspace_id = $1',
                     'COALESCE(to_jsonb(item)->>''schedule_id'', to_jsonb(item)::text)'),
                    ('schedule','v4_native_schedule_progress',
                     'EXISTS (SELECT 1 FROM public.v4_native_schedule_sources source
                       WHERE source.schedule_id=item.schedule_id
                         AND source.workspace_id=$1)',
                     'COALESCE(to_jsonb(item)->>''schedule_id'', to_jsonb(item)::text)'),
                    ('schedule','v4_native_schedule_sources',
                     'item.workspace_id = $1',
                     'COALESCE(to_jsonb(item)->>''schedule_id'', to_jsonb(item)::text)'),
                    ('schedule','v4_legacy_publication_schedule_plans',
                     'item.workspace_id = $1',
                     'COALESCE(to_jsonb(item)->>''goal_id'', to_jsonb(item)::text)'),
                    ('schedule','v4_legacy_schedule_plans',
                     'item.workspace_id = $1',
                     'COALESCE(to_jsonb(item)->>''goal_id'', to_jsonb(item)::text)'),
                    ('schedule','v4_schedule_batches',
                     'EXISTS (SELECT 1 FROM public.v4_goals goal
                       WHERE goal.id=item.goal_id AND goal.workspace_id=$1)',
                     'COALESCE(to_jsonb(item)->>''idempotency_key'', to_jsonb(item)::text)'),
                    ('schedule','v4_schedule_cursors',
                     'EXISTS (SELECT 1 FROM public.v4_goals goal
                       WHERE goal.id=item.goal_id AND goal.workspace_id=$1)',
                     'COALESCE(to_jsonb(item)->>''goal_id'', to_jsonb(item)::text)'),
                    ('schedule','v4_schedule_cutovers',
                     'item.workspace_id = $1',
                     'COALESCE(to_jsonb(item)->>''goal_id'', to_jsonb(item)::text)'),
                    ('production','v4_production_current_dispatch',
                     'EXISTS (SELECT 1 FROM public.v4_production_effect_permits permit
                       WHERE permit.operation_id=item.operation_id
                         AND permit.workspace_id=$1)',
                     'COALESCE(to_jsonb(item)->>''operation_id'', to_jsonb(item)::text)'),
                    ('production','v4_production_effect_permit_claims',
                     'EXISTS (SELECT 1 FROM public.v4_production_effect_permits permit
                       WHERE permit.permit_id=item.permit_id
                         AND permit.workspace_id=$1)',
                     'COALESCE(to_jsonb(item)->>''claim_id'', to_jsonb(item)::text)'),
                    ('production','v4_production_effect_permits',
                     'item.workspace_id = $1',
                     'COALESCE(to_jsonb(item)->>''permit_id'', to_jsonb(item)::text)'),
                    ('recovery','v4_case_acks',
                     'EXISTS (SELECT 1 FROM public.v4_case_notifications notification
                       JOIN public.v4_recovery_cases recovery
                         ON recovery.id=notification.case_id
                       WHERE notification.id=item.notification_id
                         AND recovery.workspace_id=$1)',
                     'COALESCE(to_jsonb(item)->>''id'', to_jsonb(item)::text)'),
                    ('recovery','v4_case_archives',
                     'EXISTS (SELECT 1 FROM public.v4_recovery_cases recovery
                       WHERE recovery.id=item.case_id AND recovery.workspace_id=$1)',
                     'COALESCE(to_jsonb(item)->>''id'', to_jsonb(item)::text)'),
                    ('recovery','v4_case_commands',
                     'EXISTS (SELECT 1 FROM public.v4_recovery_cases recovery
                       WHERE recovery.id=item.case_id AND recovery.workspace_id=$1)',
                     'COALESCE(to_jsonb(item)->>''id'', to_jsonb(item)::text)'),
                    ('recovery','v4_case_events',
                     'EXISTS (SELECT 1 FROM public.v4_recovery_cases recovery
                       WHERE recovery.id=item.case_id AND recovery.workspace_id=$1)',
                     'COALESCE(to_jsonb(item)->>''id'', to_jsonb(item)::text)'),
                    ('recovery','v4_case_notifications',
                     'EXISTS (SELECT 1 FROM public.v4_recovery_cases recovery
                       WHERE recovery.id=item.case_id AND recovery.workspace_id=$1)',
                     'COALESCE(to_jsonb(item)->>''id'', to_jsonb(item)::text)'),
                    ('recovery','v4_case_reviews',
                     'EXISTS (SELECT 1 FROM public.v4_recovery_cases recovery
                       WHERE recovery.id=item.case_id AND recovery.workspace_id=$1)',
                     'COALESCE(to_jsonb(item)->>''id'', to_jsonb(item)::text)'),
                    ('recovery','v4_legacy_publication_fixture_progress',
                     'EXISTS (SELECT 1 FROM public.v4_legacy_publication_fixture_receipts receipt
                       WHERE receipt.operation_id=item.operation_id
                         AND receipt.workspace_id=$1)',
                     'COALESCE(to_jsonb(item)->>''operation_id'', to_jsonb(item)::text)'),
                    ('recovery','v4_legacy_publication_fixture_receipts',
                     'item.workspace_id = $1',
                     'COALESCE(to_jsonb(item)->>''operation_id'', to_jsonb(item)::text)'),
                    ('recovery','v4_legacy_publication_recovery_holds',
                     'item.workspace_id = $1',
                     'COALESCE(to_jsonb(item)->>''id'', to_jsonb(item)::text)'),
                    ('recovery','v4_notification_acceptances',
                     'EXISTS (SELECT 1 FROM public.v4_recovery_cases recovery
                       WHERE recovery.id=item.case_id AND recovery.workspace_id=$1)',
                     'COALESCE(to_jsonb(item)->>''notification_id'', to_jsonb(item)::text)'),
                    ('recovery','v4_notification_deliveries',
                     'EXISTS (SELECT 1 FROM public.v4_case_notifications notification
                       JOIN public.v4_recovery_cases recovery
                         ON recovery.id=notification.case_id
                       WHERE notification.id=item.notification_id
                         AND recovery.workspace_id=$1)',
                     'COALESCE(to_jsonb(item)->>''notification_id'', to_jsonb(item)::text)'),
                    ('recovery','v4_recovery_cases',
                     'item.workspace_id = $1',
                     'COALESCE(to_jsonb(item)->>''id'', to_jsonb(item)::text)'),
                    ('recovery','v4_review_decisions',
                     'EXISTS (SELECT 1 FROM public.v4_case_reviews review
                       JOIN public.v4_recovery_cases recovery ON recovery.id=review.case_id
                       WHERE review.id=item.review_id AND recovery.workspace_id=$1)',
                     'COALESCE(to_jsonb(item)->>''id'', to_jsonb(item)::text)'),
                    ('recovery','v4_runtime_holds',
                     'item.workspace_id = $1',
                     'COALESCE(to_jsonb(item)->>''cycle_id'', to_jsonb(item)::text)'),
                    ('recovery','v4_runtime_waits',
                     'item.workspace_id = $1',
                     'COALESCE(to_jsonb(item)->>''id'', to_jsonb(item)::text)')
                ) AS relations(source, relation, workspace_filter, identity_expression)
                ORDER BY source, relation
            LOOP
                IF to_regclass('public.' || relation_spec.relation) IS NULL THEN
                    inventory := inventory || jsonb_build_array(jsonb_build_object(
                        'source', 'unavailable',
                        'relation', relation_spec.relation,
                        'identity', 'absent',
                        'inventory_status', 'relation_absent',
                        'row', NULL
                    ));
                    CONTINUE;
                END IF;
                EXECUTE format(
                    'SELECT COALESCE(jsonb_agg(' ||
                    'jsonb_build_object(''source'', %L, ''relation'', %L, ' ||
                    '''identity'', %s, ''row'', to_jsonb(item)) ' ||
                    'ORDER BY (%s) COLLATE "C"), ''[]''::jsonb), count(*) ' ||
                    'FROM public.%I AS item WHERE %s',
                    relation_spec.source,
                    relation_spec.relation,
                    relation_spec.identity_expression || ' || '':'' || to_jsonb(item)::text',
                    relation_spec.identity_expression || ' || '':'' || to_jsonb(item)::text',
                    relation_spec.relation,
                    relation_spec.workspace_filter
                ) INTO relation_rows, relation_count USING workspace_id;
                IF relation_count = 0 THEN
                    relation_identity := '~empty';
                    inventory := inventory || jsonb_build_array(jsonb_build_object(
                        'source', relation_spec.source,
                        'relation', relation_spec.relation,
                        'identity', relation_identity,
                        'inventory_status', 'empty',
                        'row', NULL
                    ));
                ELSE
                    inventory := inventory || relation_rows;
                END IF;
            END LOOP;
            inventory := inventory || jsonb_build_array(
                jsonb_build_object(
                    'source', 'unavailable',
                    'relation', 'route_fence_authority',
                    'identity', 'external-route-service',
                    'inventory_status', 'external-authority-not-present-in-0044',
                    'row', NULL
                ),
                jsonb_build_object(
                    'source', 'unavailable',
                    'relation', 'typed_no_effect_recovery_command',
                    'identity', 'command-not-identified',
                    'inventory_status', 'generation-advance-blocked',
                    'row', NULL
                ),
                jsonb_build_object(
                    'source', 'unavailable',
                    'relation', 'v4_recovery_work_items',
                    'identity', 'relation-not-present',
                    'inventory_status', 'future-recovery-family-absent',
                    'row', NULL
                ),
                jsonb_build_object(
                    'source', 'unresolved',
                    'relation', 'unresolved_or_missing_links',
                    'identity', 'requires-owning-recovery-and-routing-readers',
                    'inventory_status', 'blocker',
                    'row', jsonb_build_object(
                        'details', jsonb_build_array(
                            'v4_recovery_work_items is not present in revision 0044',
                            'route fence decisions are owned by the external routing authority',
                            'typed no-effect recovery command has not been identified'
                        )
                    )
                )
            );
            SELECT COALESCE(
                jsonb_agg(item ORDER BY (item->>'source') COLLATE "C",
                                  (item->>'relation') COLLATE "C",
                                  (item->>'identity') COLLATE "C"),
                '[]'::jsonb
            )
            INTO inventory
            FROM jsonb_array_elements(inventory) AS inventory_rows(item);
            RETURN jsonb_build_object(
                'schema_version',
                    'salience.p1-production-policy-rollback-inventory.v1',
                'workspace_id', workspace_id,
                'inventory', inventory,
                'inventory_sha256',
                    encode(public.digest(inventory::text, 'sha256'), 'hex')
            );
        END
        $$"""
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.v4_production_policy_rollback_inventory_v1(uuid) FROM PUBLIC"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.v4_validate_production_effect_permit_v1() FROM PUBLIC"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.v4_reject_production_history_change_v1() FROM PUBLIC"
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '3s'")
    op.execute(
        """DO $rollback$ DECLARE relation_name text; BEGIN
          FOR relation_name IN
            SELECT relation.relname
            FROM pg_class relation
            JOIN pg_namespace schema ON schema.oid=relation.relnamespace
            WHERE schema.nspname='public'
              AND relation.relkind IN ('r','p')
              AND (
                left(relation.relname, 3)='v4_'
                OR relation.relname = ANY(ARRAY[
                  'approval_requests',
                  'budgets',
                  'budget_reservations',
                  'content_programs',
                  'external_effects',
                  'identity_subjects',
                  'jobs',
                  'permission_grants',
                  'policy_versions',
                  'publication_attempts',
                  'publication_plans',
                  'publication_requests',
                  'publication_status_events',
                  'publications',
                  'publisher_webhook_receipts',
                  'remote_publication_receipts'
                ]::text[])
              )
            ORDER BY relation.relname COLLATE "C"
          LOOP
            EXECUTE format(
              'LOCK TABLE public.%I IN SHARE ROW EXCLUSIVE MODE',
              relation_name
            );
          END LOOP;
        END $rollback$"""
    )
    op.execute(
        """DO $rollback$ DECLARE relation_name text; populated boolean; BEGIN
          FOR relation_name IN
            SELECT relation.relname
            FROM pg_class relation
            JOIN pg_namespace schema ON schema.oid=relation.relnamespace
            WHERE schema.nspname='public'
              AND relation.relkind IN ('r','p')
              AND (
                left(relation.relname, 3)='v4_'
                OR relation.relname = ANY(ARRAY[
                  'approval_requests',
                  'budgets',
                  'budget_reservations',
                  'content_programs',
                  'external_effects',
                  'identity_subjects',
                  'jobs',
                  'permission_grants',
                  'policy_versions',
                  'publication_attempts',
                  'publication_plans',
                  'publication_requests',
                  'publication_status_events',
                  'publications',
                  'publisher_webhook_receipts',
                  'remote_publication_receipts'
                ]::text[])
              )
            ORDER BY relation.relname COLLATE "C"
          LOOP
            EXECUTE format(
              'SELECT EXISTS (SELECT 1 FROM public.%I)',
              relation_name
            ) INTO populated;
            IF populated THEN
              RAISE EXCEPTION
                'preserve production policy and revocation authority history';
            END IF;
          END LOOP;
        END $rollback$"""
    )
    op.execute(
        """DROP TRIGGER guard_effect_approval_transition_v1
           ON public.approval_requests"""
    )
    op.execute(
        """DROP TRIGGER guard_policy_version_transition_v1
           ON public.policy_versions"""
    )
    op.execute(
        """DROP TRIGGER v4_permission_grant_subject_revision_v1
           ON public.permission_grants"""
    )
    op.execute(
        """DROP TRIGGER restricted_v4_production_current_dispatch_write_v1
           ON public.v4_production_current_dispatch"""
    )
    op.execute(
        """DROP TRIGGER immutable_v4_production_effect_permit_claims_v1
           ON public.v4_production_effect_permit_claims"""
    )
    op.execute(
        """DROP TRIGGER immutable_v4_production_effect_permits_v1
           ON public.v4_production_effect_permits"""
    )
    op.execute(
        """DROP TRIGGER validate_v4_production_effect_permit_v1
           ON public.v4_production_effect_permits"""
    )
    op.execute(
        "DROP FUNCTION public.v4_production_policy_rollback_inventory_v1(uuid)"
    )
    op.execute(
        "DROP FUNCTION public.v4_advance_production_current_dispatch_v1(uuid,integer,uuid,uuid,text,uuid)"
    )
    op.execute(
        "DROP FUNCTION public.v4_guard_production_current_dispatch_write_v1()"
    )
    op.execute(
        "DROP FUNCTION public.v4_advance_identity_subject_revision_from_grant_v1()"
    )
    op.execute("DROP FUNCTION public.v4_guard_policy_version_transition_v1()")
    op.execute("DROP FUNCTION public.v4_guard_effect_approval_transition_v1()")
    op.execute("DROP FUNCTION public.v4_validate_production_effect_permit_v1()")
    op.execute("DROP FUNCTION public.v4_reject_production_history_change_v1()")
    op.execute(
        "DROP TABLE public.v4_production_current_dispatch"
    )
    op.execute(
        "DROP TABLE public.v4_production_effect_permit_claims"
    )
    op.execute(
        "DROP TABLE public.v4_production_effect_permits"
    )
