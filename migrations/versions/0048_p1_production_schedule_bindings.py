"""Persist complete production Temporal schedule inventories and bindings."""

from alembic import op


revision = "0048_p1_production_schedule_bindings"
down_revision = "0047_p1_production_recovery_work_events"
branch_labels = None
depends_on = None


_MEMBER_KEYS = """ARRAY[
    'schema_version','target_alias','namespace','remote_schedule_id',
    'classification','source_kind','schedule_id','publication_schedule_id',
    'workspace_id','goal_id','goal_revision','source_actor_id',
    'source_owner_subject_id','schedule_operator_subject_id','owner_ref',
    'exclusion_reason','workflow_type','action_id','argument_sha256',
    'task_queue','route_id','route_tuple_sha256','slot_id','owner_id',
    'fencing_epoch','route_manifest_sha256','artifact_lock_sha256',
    'cadence_policy_revision','interval_seconds','first_v4_slot',
    'observed_last_slot','remote_state','next_action','running_action_count',
    'history_page_count','history_action_count','history_page_chain_sha256',
    'history_action_set_sha256','history_watermark','history_watermark_sha256'
]::text[]"""

_APPROVAL_KEYS = """ARRAY[
    'schema_version','target_alias','namespace','inventory_id',
    'inventory_sha256','source_kind','schedule_id','publication_schedule_id',
    'workspace_id','goal_id','goal_revision','source_actor_id',
    'source_owner_subject_id','schedule_operator_subject_id',
    'remote_schedule_id','task_queue','workflow_type','action_id',
    'argument_sha256','route_id','route_tuple_sha256','slot_id','owner_id',
    'fencing_epoch','route_manifest_sha256','artifact_lock_sha256',
    'cadence_policy_revision','first_v4_slot','history_watermark_sha256',
    'policy_version_id','policy_document_hash','expires_at'
]::text[]"""


def upgrade() -> None:
    op.execute(
        """CREATE TABLE public.v4_production_schedule_inventories (
            inventory_id uuid PRIMARY KEY,
            target_alias text NOT NULL CHECK (btrim(target_alias) <> ''),
            namespace text NOT NULL CHECK (btrim(namespace) <> ''),
            captured_at timestamptz NOT NULL,
            source_tool text NOT NULL CHECK (btrim(source_tool) <> ''),
            source_tool_version text NOT NULL CHECK (btrim(source_tool_version) <> ''),
            candidate_sha256 text NOT NULL CHECK (candidate_sha256 ~ '^[0-9a-f]{64}$'),
            configuration_sha256 text NOT NULL CHECK (configuration_sha256 ~ '^[0-9a-f]{64}$'),
            page_count integer NOT NULL CHECK (page_count > 0),
            remote_member_count integer NOT NULL CHECK (remote_member_count >= 0),
            bound_member_count integer NOT NULL CHECK (bound_member_count >= 0),
            out_of_scope_member_count integer NOT NULL CHECK (out_of_scope_member_count >= 0),
            inventory_page_chain_sha256 text NOT NULL CHECK (inventory_page_chain_sha256 ~ '^[0-9a-f]{64}$'),
            member_set_sha256 text NOT NULL CHECK (member_set_sha256 ~ '^[0-9a-f]{64}$'),
            inventory_sha256 text NOT NULL CHECK (inventory_sha256 ~ '^[0-9a-f]{64}$'),
            finalized_at timestamptz,
            CHECK (bound_member_count + out_of_scope_member_count = remote_member_count),
            CHECK (captured_at <= clock_timestamp()),
            CHECK (finalized_at IS NULL OR finalized_at >= captured_at)
        )"""
    )
    op.execute(
        """CREATE TABLE public.v4_production_schedule_inventory_members (
            inventory_id uuid NOT NULL
                REFERENCES public.v4_production_schedule_inventories(inventory_id) ON DELETE RESTRICT,
            remote_schedule_id text NOT NULL CHECK (btrim(remote_schedule_id) <> ''),
            page_index integer NOT NULL CHECK (page_index >= 0),
            classification text NOT NULL CHECK (classification IN ('bound','out_of_scope')),
            member_snapshot jsonb NOT NULL CHECK (jsonb_typeof(member_snapshot) = 'object'),
            member_sha256 text NOT NULL CHECK (member_sha256 ~ '^[0-9a-f]{64}$'),
            history_page_count integer NOT NULL CHECK (history_page_count >= 0),
            history_action_count integer NOT NULL CHECK (history_action_count >= 0),
            history_page_chain_sha256 text NOT NULL CHECK (history_page_chain_sha256 ~ '^[0-9a-f]{64}$'),
            history_action_set_sha256 text NOT NULL CHECK (history_action_set_sha256 ~ '^[0-9a-f]{64}$'),
            history_watermark timestamptz,
            history_watermark_sha256 text CHECK (history_watermark_sha256 IS NULL OR history_watermark_sha256 ~ '^[0-9a-f]{64}$'),
            PRIMARY KEY (inventory_id, remote_schedule_id),
            CHECK ((history_action_count = 0) = (history_watermark IS NULL AND history_watermark_sha256 IS NULL)),
            CHECK ((history_watermark IS NULL) = (history_watermark_sha256 IS NULL))
        )"""
    )
    op.execute(
        """CREATE TABLE public.v4_production_schedule_inventory_pages (
            inventory_id uuid NOT NULL
                REFERENCES public.v4_production_schedule_inventories(inventory_id) ON DELETE RESTRICT,
            remote_schedule_id text,
            page_index integer NOT NULL CHECK (page_index >= 0),
            CHECK (remote_schedule_id IS NULL OR btrim(remote_schedule_id) <> ''),
            request_cursor_sha256 text NOT NULL CHECK (request_cursor_sha256 ~ '^[0-9a-f]{64}$'),
            page_items jsonb NOT NULL CHECK (jsonb_typeof(page_items) = 'array'),
            response_sha256 text NOT NULL CHECK (response_sha256 ~ '^[0-9a-f]{64}$'),
            item_count integer NOT NULL CHECK (item_count >= 0 AND item_count = jsonb_array_length(page_items)),
            next_cursor_sha256 text CHECK (next_cursor_sha256 IS NULL OR next_cursor_sha256 ~ '^[0-9a-f]{64}$'),
            is_terminal boolean NOT NULL,
            item_set_sha256 text NOT NULL CHECK (item_set_sha256 ~ '^[0-9a-f]{64}$'),
            last_item_sha256 text CHECK (last_item_sha256 IS NULL OR last_item_sha256 ~ '^[0-9a-f]{64}$'),
            page_receipt_sha256 text NOT NULL CHECK (page_receipt_sha256 ~ '^[0-9a-f]{64}$')
        )"""
    )
    op.execute(
        """CREATE UNIQUE INDEX uq_v4_prod_schedule_page_scope_index
           ON public.v4_production_schedule_inventory_pages
              (inventory_id, COALESCE(remote_schedule_id, ''), page_index)"""
    )
    op.execute(
        """ALTER TABLE public.v4_production_schedule_inventory_pages
           ADD CONSTRAINT fk_v4_prod_schedule_history_member
           FOREIGN KEY (inventory_id, remote_schedule_id)
           REFERENCES public.v4_production_schedule_inventory_members(inventory_id, remote_schedule_id)
           ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )
    op.execute(
        """CREATE TABLE public.v4_production_schedule_source_bindings (
            inventory_id uuid NOT NULL,
            inventory_sha256 text NOT NULL CHECK (inventory_sha256 ~ '^[0-9a-f]{64}$'),
            target_alias text NOT NULL CHECK (btrim(target_alias) <> ''),
            namespace text NOT NULL CHECK (btrim(namespace) <> ''),
            remote_schedule_id text NOT NULL CHECK (btrim(remote_schedule_id) <> ''),
            member_sha256 text NOT NULL CHECK (member_sha256 ~ '^[0-9a-f]{64}$'),
            source_kind text NOT NULL CHECK (source_kind IN ('intelligence_research','governed_publication')),
            schedule_id uuid NOT NULL REFERENCES public.job_schedules(id) ON DELETE RESTRICT,
            publication_schedule_id uuid REFERENCES public.publication_schedules(id) ON DELETE RESTRICT,
            source_approval_id uuid NOT NULL UNIQUE
                REFERENCES public.approval_requests(id) ON DELETE RESTRICT,
            source_actor_id uuid NOT NULL,
            source_owner_subject_id uuid NOT NULL,
            schedule_operator_subject_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            goal_id uuid NOT NULL,
            goal_revision integer NOT NULL CHECK (goal_revision > 0),
            owner_ref text NOT NULL CHECK (btrim(owner_ref) <> ''),
            task_queue text NOT NULL CHECK (btrim(task_queue) <> '' AND task_queue NOT LIKE 'salience-v4-local-legacy-%'),
            workflow_type text NOT NULL CHECK (btrim(workflow_type) <> ''),
            action_id text NOT NULL CHECK (btrim(action_id) <> ''),
            argument_sha256 text NOT NULL CHECK (argument_sha256 ~ '^[0-9a-f]{64}$'),
            route_id uuid NOT NULL,
            route_tuple_sha256 text NOT NULL CHECK (route_tuple_sha256 ~ '^[0-9a-f]{64}$'),
            slot_id uuid NOT NULL,
            owner_id uuid NOT NULL,
            fencing_epoch bigint NOT NULL CHECK (fencing_epoch > 0),
            route_manifest_sha256 text NOT NULL CHECK (route_manifest_sha256 ~ '^[0-9a-f]{64}$'),
            artifact_lock_sha256 text NOT NULL CHECK (artifact_lock_sha256 ~ '^[0-9a-f]{64}$'),
            cadence_policy_revision text NOT NULL CHECK (btrim(cadence_policy_revision) <> ''),
            interval_seconds integer NOT NULL CHECK (interval_seconds BETWEEN 1 AND 31536000),
            first_v4_slot timestamptz NOT NULL,
            observed_last_slot timestamptz,
            remote_state text NOT NULL CHECK (btrim(remote_state) <> ''),
            next_action timestamptz,
            running_action_count integer NOT NULL CHECK (running_action_count >= 0),
            history_page_count integer NOT NULL CHECK (history_page_count >= 1),
            history_action_count integer NOT NULL CHECK (history_action_count >= 0),
            history_page_chain_sha256 text NOT NULL CHECK (history_page_chain_sha256 ~ '^[0-9a-f]{64}$'),
            history_action_set_sha256 text NOT NULL CHECK (history_action_set_sha256 ~ '^[0-9a-f]{64}$'),
            history_watermark timestamptz,
            history_watermark_sha256 text CHECK (history_watermark_sha256 IS NULL OR history_watermark_sha256 ~ '^[0-9a-f]{64}$'),
            member_snapshot jsonb NOT NULL CHECK (jsonb_typeof(member_snapshot) = 'object'),
            binding_fingerprint text NOT NULL CHECK (binding_fingerprint ~ '^[0-9a-f]{64}$'),
            PRIMARY KEY (goal_id),
            UNIQUE (inventory_id, remote_schedule_id),
            UNIQUE (target_alias, namespace, remote_schedule_id),
            UNIQUE (source_kind, schedule_id),
            FOREIGN KEY (inventory_id, remote_schedule_id)
                REFERENCES public.v4_production_schedule_inventory_members(inventory_id, remote_schedule_id)
                ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
            FOREIGN KEY (goal_id, workspace_id)
                REFERENCES public.v4_goals(id, workspace_id) ON DELETE RESTRICT,
            FOREIGN KEY (goal_id, goal_revision)
                REFERENCES public.v4_goal_revisions(goal_id, revision) ON DELETE RESTRICT,
            FOREIGN KEY (source_actor_id, workspace_id)
                REFERENCES public.identity_subjects(id, workspace_id) ON DELETE RESTRICT,
            FOREIGN KEY (source_owner_subject_id, workspace_id)
                REFERENCES public.identity_subjects(id, workspace_id) ON DELETE RESTRICT,
            FOREIGN KEY (schedule_operator_subject_id, workspace_id)
                REFERENCES public.identity_subjects(id, workspace_id) ON DELETE RESTRICT,
            FOREIGN KEY (owner_id, workspace_id)
                REFERENCES public.identity_subjects(id, workspace_id) ON DELETE RESTRICT,
            CHECK ((source_kind = 'intelligence_research' AND publication_schedule_id IS NULL)
                OR (source_kind = 'governed_publication' AND publication_schedule_id IS NOT NULL)),
            CHECK (source_actor_id <> source_owner_subject_id),
            CHECK ((history_action_count = 0) = (history_watermark IS NULL AND history_watermark_sha256 IS NULL)),
            CHECK ((history_watermark IS NULL) = (history_watermark_sha256 IS NULL)),
            CHECK (observed_last_slot IS NULL OR observed_last_slot < first_v4_slot)
        )"""
    )
    op.execute(
        """CREATE FUNCTION public.v4_production_schedule_jsonb_sha256_v1(p_value jsonb)
        RETURNS text LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
        SET search_path = pg_catalog, public AS $$
            SELECT encode(public.digest(convert_to(p_value::text, 'UTF8'), 'sha256'), 'hex')
        $$"""
    )
    op.execute(
        """CREATE FUNCTION public.v4_production_schedule_text_sha256_v1(p_value text)
        RETURNS text LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
        SET search_path = pg_catalog, public AS $$
            SELECT encode(public.digest(convert_to(p_value, 'UTF8'), 'sha256'), 'hex')
        $$"""
    )
    op.execute(
        """CREATE FUNCTION public.v4_guard_production_schedule_approval_v1()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public AS $$
        DECLARE
            tagged boolean;
            required_keys text[] := """ + _APPROVAL_KEYS + """;
            expiry timestamptz;
        BEGIN
            IF TG_RELID <> 'public.approval_requests'::regclass
               OR TG_TABLE_SCHEMA <> 'public' OR TG_TABLE_NAME <> 'approval_requests' THEN
                RAISE EXCEPTION 'production schedule approval guard only accepts approval_requests';
            END IF;
            IF TG_OP = 'DELETE' THEN
                IF OLD.request_context->>'schema_version' =
                       'salience.p1-production-schedule-binding-approval.v1'
                   AND (OLD.status <> 'pending' OR OLD.decided_by IS NOT NULL
                        OR OLD.decided_at IS NOT NULL OR OLD.decision_reason IS NOT NULL) THEN
                    RAISE EXCEPTION 'decided production schedule approval identity is immutable';
                END IF;
                RETURN OLD;
            END IF;
            tagged := NEW.request_context->>'schema_version' =
                      'salience.p1-production-schedule-binding-approval.v1';
            IF TG_OP = 'UPDATE'
               AND OLD.request_context->>'schema_version' IS DISTINCT FROM
                   'salience.p1-production-schedule-binding-approval.v1' AND tagged THEN
                RAISE EXCEPTION 'production schedule approval cannot be tagged onto an existing generic row';
            END IF;
            IF TG_OP = 'UPDATE'
               AND OLD.request_context->>'schema_version' =
                   'salience.p1-production-schedule-binding-approval.v1' AND NOT tagged THEN
                RAISE EXCEPTION 'production schedule approval identity is immutable';
            END IF;
            IF NOT tagged AND NEW.effect_type = 'production_schedule_binding' THEN
                RAISE EXCEPTION 'production schedule approval requires its typed v1 context';
            END IF;
            IF NOT tagged THEN RETURN NEW; END IF;
            IF jsonb_typeof(NEW.request_context) IS DISTINCT FROM 'object'
               OR NOT (NEW.request_context ?& required_keys)
               OR (NEW.request_context - required_keys) <> '{}'::jsonb THEN
                RAISE EXCEPTION 'production schedule approval requires exact v1 context fields';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM unnest(required_keys) AS required(key)
                WHERE NOT (
                    (required.key = ANY(ARRAY['publication_schedule_id',
                                               'history_watermark_sha256'])
                     AND NEW.request_context->required.key = 'null'::jsonb)
                    OR (required.key = ANY(ARRAY['goal_revision','fencing_epoch'])
                        AND jsonb_typeof(NEW.request_context->required.key) = 'number'
                        AND NEW.request_context->>required.key ~ '^[1-9][0-9]*$')
                    OR (required.key <> ALL(ARRAY['goal_revision','fencing_epoch'])
                        AND jsonb_typeof(NEW.request_context->required.key) = 'string'
                        AND btrim(NEW.request_context->>required.key) <> '')
                )
            ) THEN
                RAISE EXCEPTION 'production schedule approval context has invalid JSON types, nullability, or blank strings';
            END IF;
            IF NEW.effect_type IS DISTINCT FROM 'production_schedule_binding'
               OR NEW.policy_version_id IS NULL
               OR NEW.request_context->>'workspace_id' IS DISTINCT FROM NEW.workspace_id::text
               OR NEW.request_context->>'policy_version_id' IS DISTINCT FROM NEW.policy_version_id::text
               OR NEW.request_context->>'effect_type' IS NOT NULL
               OR NEW.request_context->>'policy_document_hash' !~ '^[0-9a-f]{64}$'
               OR NEW.request_context->>'inventory_sha256' !~ '^[0-9a-f]{64}$'
               OR NEW.request_context->>'argument_sha256' !~ '^[0-9a-f]{64}$'
               OR NEW.request_context->>'route_tuple_sha256' !~ '^[0-9a-f]{64}$'
               OR NEW.request_context->>'route_manifest_sha256' !~ '^[0-9a-f]{64}$'
               OR NEW.request_context->>'artifact_lock_sha256' !~ '^[0-9a-f]{64}$'
               OR NEW.request_context->>'history_watermark_sha256' IS NOT NULL
                   AND NEW.request_context->>'history_watermark_sha256' !~ '^[0-9a-f]{64}$'
               OR NEW.request_context->>'goal_revision' !~ '^[1-9][0-9]*$'
               OR NEW.request_context->>'fencing_epoch' !~ '^[1-9][0-9]*$'
               OR NEW.request_context->>'expires_at' !~* '(Z|[+-][0-9]{2}:[0-9]{2})$' THEN
                RAISE EXCEPTION 'production schedule approval row/context binding is malformed';
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM public.policy_versions policy
                WHERE policy.id = NEW.policy_version_id
                  AND policy.workspace_id = NEW.workspace_id
                  AND policy.document_hash = NEW.request_context->>'policy_document_hash'
            ) THEN
                RAISE EXCEPTION 'production schedule approval policy binding mismatch';
            END IF;
            BEGIN
                expiry := (NEW.request_context->>'expires_at')::timestamptz;
                PERFORM (NEW.request_context->>'inventory_id')::uuid,
                        (NEW.request_context->>'schedule_id')::uuid,
                        (NEW.request_context->>'workspace_id')::uuid,
                        (NEW.request_context->>'goal_id')::uuid,
                        (NEW.request_context->>'source_actor_id')::uuid,
                        (NEW.request_context->>'source_owner_subject_id')::uuid,
                        (NEW.request_context->>'schedule_operator_subject_id')::uuid,
                        (NEW.request_context->>'route_id')::uuid,
                        (NEW.request_context->>'slot_id')::uuid,
                        (NEW.request_context->>'owner_id')::uuid;
                IF NEW.request_context->>'publication_schedule_id' IS NOT NULL THEN
                    PERFORM (NEW.request_context->>'publication_schedule_id')::uuid;
                END IF;
            EXCEPTION WHEN others THEN
                RAISE EXCEPTION 'production schedule approval has an invalid typed identity or expiry';
            END;
            IF NEW.request_context->>'source_kind' NOT IN
                   ('intelligence_research','governed_publication')
               OR (NEW.request_context->>'source_kind' = 'intelligence_research'
                   AND NEW.request_context->'publication_schedule_id' <> 'null'::jsonb)
               OR (NEW.request_context->>'source_kind' = 'governed_publication'
                   AND jsonb_typeof(NEW.request_context->'publication_schedule_id') <> 'string') THEN
                RAISE EXCEPTION 'production schedule approval source identity matrix is invalid';
            END IF;
            IF NEW.status NOT IN ('pending','approved','rejected','revoked','expired') THEN
                RAISE EXCEPTION 'production schedule approval status is invalid';
            END IF;
            IF NEW.status = 'approved'
               AND (NEW.decided_by IS NULL OR NEW.decided_at IS NULL OR expiry <= clock_timestamp()) THEN
                RAISE EXCEPTION 'approved production schedule binding requires a current decided identity';
            END IF;
            IF TG_OP = 'UPDATE'
               AND OLD.request_context->>'schema_version' =
                   'salience.p1-production-schedule-binding-approval.v1' THEN
                IF ROW(OLD.id, OLD.workspace_id, OLD.job_id, OLD.policy_version_id,
                       OLD.effect_type, OLD.request_context, OLD.requested_by)
                   IS DISTINCT FROM
                   ROW(NEW.id, NEW.workspace_id, NEW.job_id, NEW.policy_version_id,
                       NEW.effect_type, NEW.request_context, NEW.requested_by) THEN
                    RAISE EXCEPTION 'production schedule approval identity is immutable';
                END IF;
                IF OLD.status <> 'pending' OR OLD.decided_by IS NOT NULL
                   OR OLD.decided_at IS NOT NULL OR OLD.decision_reason IS NOT NULL THEN
                    IF ROW(OLD.status, OLD.decided_by, OLD.decided_at, OLD.decision_reason)
                       IS DISTINCT FROM
                       ROW(NEW.status, NEW.decided_by, NEW.decided_at, NEW.decision_reason) THEN
                        RAISE EXCEPTION 'decided production schedule approval identity is immutable';
                    END IF;
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$"""
    )
    op.execute(
        """CREATE TRIGGER guard_production_schedule_approval_v1
           BEFORE INSERT OR UPDATE OR DELETE ON public.approval_requests
           FOR EACH ROW EXECUTE FUNCTION public.v4_guard_production_schedule_approval_v1()"""
    )
    op.execute(
        """CREATE FUNCTION public.v4_guard_production_schedule_inventory_header_v1()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.finalized_at IS NOT NULL THEN
                    RAISE EXCEPTION 'production schedule inventory must be finalized by its verifier';
                END IF;
                RETURN NEW;
            END IF;
            IF TG_OP = 'DELETE' THEN
                IF OLD.finalized_at IS NOT NULL THEN
                    RAISE EXCEPTION 'finalized production schedule inventory is immutable';
                END IF;
                RETURN OLD;
            END IF;
            IF OLD.finalized_at IS NOT NULL THEN
                RAISE EXCEPTION 'finalized production schedule inventory is immutable';
            END IF;
            IF NEW.inventory_id IS DISTINCT FROM OLD.inventory_id THEN
                RAISE EXCEPTION 'production schedule inventory identity is immutable';
            END IF;
            IF NEW.finalized_at IS DISTINCT FROM OLD.finalized_at
               AND (NEW.finalized_at IS NULL
                    OR current_setting('salience.p1_production_schedule_finalize',true)
                         IS DISTINCT FROM NEW.inventory_id::text
                    OR (to_jsonb(NEW)-'finalized_at') IS DISTINCT FROM
                       (to_jsonb(OLD)-'finalized_at')) THEN
                RAISE EXCEPTION 'production schedule inventory finalization must use the verifier';
            END IF;
            RETURN NEW;
        END;
        $$"""
    )
    op.execute(
        """CREATE TRIGGER guard_v4_production_schedule_inventory_header_v1
           BEFORE INSERT OR UPDATE OR DELETE ON public.v4_production_schedule_inventories
           FOR EACH ROW EXECUTE FUNCTION public.v4_guard_production_schedule_inventory_header_v1()"""
    )
    op.execute(
        """CREATE FUNCTION public.v4_guard_production_schedule_member_v1()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public AS $$
        DECLARE
            inventory_row public.v4_production_schedule_inventories%ROWTYPE;
            allowed_keys text[] := """ + _MEMBER_KEYS + """;
            snapshot jsonb;
            source_kind text;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                SELECT * INTO inventory_row FROM public.v4_production_schedule_inventories
                WHERE inventory_id = OLD.inventory_id FOR UPDATE;
                IF inventory_row.finalized_at IS NOT NULL THEN
                    RAISE EXCEPTION 'finalized production schedule member is immutable';
                END IF;
                RETURN OLD;
            END IF;
            SELECT * INTO inventory_row FROM public.v4_production_schedule_inventories
            WHERE inventory_id = NEW.inventory_id FOR UPDATE;
            IF NOT FOUND THEN RAISE EXCEPTION 'production schedule inventory is required'; END IF;
            IF inventory_row.finalized_at IS NOT NULL THEN
                RAISE EXCEPTION 'finalized production schedule member is immutable';
            END IF;
            IF TG_OP = 'UPDATE' AND NEW.inventory_id IS DISTINCT FROM OLD.inventory_id THEN
                RAISE EXCEPTION 'production schedule member inventory identity is immutable';
            END IF;
            snapshot := NEW.member_snapshot;
            IF snapshot ?& allowed_keys IS NOT TRUE
               OR (snapshot - allowed_keys) <> '{}'::jsonb
               OR snapshot->>'schema_version' IS DISTINCT FROM
                    'salience.p1-production-schedule-inventory-member.v1'
               OR snapshot->>'remote_schedule_id' IS DISTINCT FROM NEW.remote_schedule_id
               OR snapshot->>'classification' IS DISTINCT FROM NEW.classification
               OR snapshot->>'target_alias' IS DISTINCT FROM inventory_row.target_alias
               OR snapshot->>'namespace' IS DISTINCT FROM inventory_row.namespace
               OR jsonb_typeof(snapshot->'goal_revision') NOT IN ('number','null')
               OR jsonb_typeof(snapshot->'interval_seconds') IS DISTINCT FROM 'number'
               OR jsonb_typeof(snapshot->'fencing_epoch') IS DISTINCT FROM 'number'
               OR jsonb_typeof(snapshot->'running_action_count') IS DISTINCT FROM 'number'
               OR jsonb_typeof(snapshot->'history_page_count') IS DISTINCT FROM 'number'
               OR jsonb_typeof(snapshot->'history_action_count') IS DISTINCT FROM 'number'
               OR NEW.member_sha256 IS DISTINCT FROM public.v4_production_schedule_jsonb_sha256_v1(snapshot)
               OR snapshot->>'history_page_count' IS DISTINCT FROM NEW.history_page_count::text
               OR snapshot->>'history_action_count' IS DISTINCT FROM NEW.history_action_count::text
               OR snapshot->>'history_page_chain_sha256' IS DISTINCT FROM NEW.history_page_chain_sha256
               OR snapshot->>'history_action_set_sha256' IS DISTINCT FROM NEW.history_action_set_sha256
               OR snapshot->>'history_watermark_sha256' IS DISTINCT FROM NEW.history_watermark_sha256
               OR NULLIF(snapshot->>'history_watermark','')::timestamptz IS DISTINCT FROM NEW.history_watermark THEN
                RAISE EXCEPTION 'production schedule member snapshot or digest does not match its row';
            END IF;
            source_kind := snapshot->>'source_kind';
            IF NEW.classification = 'bound' THEN
                IF source_kind NOT IN ('intelligence_research','governed_publication')
                   OR jsonb_typeof(snapshot->'schedule_id') IS DISTINCT FROM 'string'
                   OR jsonb_typeof(snapshot->'workspace_id') IS DISTINCT FROM 'string'
                   OR jsonb_typeof(snapshot->'goal_id') IS DISTINCT FROM 'string'
                   OR jsonb_typeof(snapshot->'source_actor_id') IS DISTINCT FROM 'string'
                   OR jsonb_typeof(snapshot->'source_owner_subject_id') IS DISTINCT FROM 'string'
                   OR jsonb_typeof(snapshot->'schedule_operator_subject_id') IS DISTINCT FROM 'string'
                   OR btrim(COALESCE(snapshot->>'owner_ref','')) = ''
                   OR jsonb_typeof(snapshot->'exclusion_reason') IS DISTINCT FROM 'null'
                   OR snapshot->>'source_actor_id' = snapshot->>'source_owner_subject_id'
                   OR snapshot->>'task_queue' LIKE 'salience-v4-local-legacy-%' THEN
                    RAISE EXCEPTION 'production schedule bound member source identity matrix is invalid';
                END IF;
                IF (source_kind = 'intelligence_research'
                    AND jsonb_typeof(snapshot->'publication_schedule_id') IS DISTINCT FROM 'null')
                   OR (source_kind = 'governed_publication'
                    AND jsonb_typeof(snapshot->'publication_schedule_id') IS DISTINCT FROM 'string') THEN
                    RAISE EXCEPTION 'production schedule member publication source identity matrix is invalid';
                END IF;
            ELSE
                IF source_kind IS NOT NULL
                   OR jsonb_typeof(snapshot->'schedule_id') IS DISTINCT FROM 'null'
                   OR jsonb_typeof(snapshot->'publication_schedule_id') IS DISTINCT FROM 'null'
                   OR jsonb_typeof(snapshot->'workspace_id') IS DISTINCT FROM 'null'
                   OR jsonb_typeof(snapshot->'goal_id') IS DISTINCT FROM 'null'
                   OR jsonb_typeof(snapshot->'goal_revision') IS DISTINCT FROM 'null'
                   OR jsonb_typeof(snapshot->'source_actor_id') IS DISTINCT FROM 'null'
                   OR jsonb_typeof(snapshot->'source_owner_subject_id') IS DISTINCT FROM 'null'
                   OR jsonb_typeof(snapshot->'schedule_operator_subject_id') IS DISTINCT FROM 'null'
                   OR btrim(COALESCE(snapshot->>'owner_ref','')) = ''
                   OR btrim(COALESCE(snapshot->>'exclusion_reason','')) = '' THEN
                    RAISE EXCEPTION 'out-of-scope production schedule member requires only owner and reason';
                END IF;
            END IF;
            IF snapshot->>'argument_sha256' !~ '^[0-9a-f]{64}$'
               OR snapshot->>'route_tuple_sha256' !~ '^[0-9a-f]{64}$'
               OR snapshot->>'route_manifest_sha256' !~ '^[0-9a-f]{64}$'
               OR snapshot->>'artifact_lock_sha256' !~ '^[0-9a-f]{64}$'
               OR snapshot->>'interval_seconds' !~ '^[1-9][0-9]*$'
               OR snapshot->>'fencing_epoch' !~ '^[1-9][0-9]*$'
               OR snapshot->>'running_action_count' !~ '^[0-9]+$'
               OR snapshot->>'first_v4_slot' IS NULL
               OR snapshot->>'task_queue' IS NULL OR btrim(snapshot->>'task_queue') = '' THEN
                RAISE EXCEPTION 'production schedule member facts are malformed';
            END IF;
            RETURN NEW;
        END;
        $$"""
    )
    op.execute(
        """CREATE TRIGGER guard_v4_production_schedule_member_v1
           BEFORE INSERT OR UPDATE OR DELETE ON public.v4_production_schedule_inventory_members
           FOR EACH ROW EXECUTE FUNCTION public.v4_guard_production_schedule_member_v1()"""
    )
    op.execute(
        """CREATE FUNCTION public.v4_guard_production_schedule_page_v1()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public AS $$
        DECLARE
            inventory_row public.v4_production_schedule_inventories%ROWTYPE;
            expected_receipt text;
            item jsonb;
            previous_slot timestamptz;
            previous_action text;
            current_slot timestamptz;
            current_action text;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                SELECT * INTO inventory_row FROM public.v4_production_schedule_inventories
                WHERE inventory_id = OLD.inventory_id FOR UPDATE;
                IF inventory_row.finalized_at IS NOT NULL THEN
                    RAISE EXCEPTION 'finalized production schedule page is immutable';
                END IF;
                RETURN OLD;
            END IF;
            SELECT * INTO inventory_row FROM public.v4_production_schedule_inventories
            WHERE inventory_id = NEW.inventory_id FOR UPDATE;
            IF NOT FOUND THEN RAISE EXCEPTION 'production schedule inventory is required'; END IF;
            IF inventory_row.finalized_at IS NOT NULL THEN
                RAISE EXCEPTION 'finalized production schedule page is immutable';
            END IF;
            IF TG_OP = 'UPDATE' AND (NEW.inventory_id IS DISTINCT FROM OLD.inventory_id
                    OR NEW.remote_schedule_id IS DISTINCT FROM OLD.remote_schedule_id
                    OR NEW.page_index IS DISTINCT FROM OLD.page_index) THEN
                RAISE EXCEPTION 'production schedule page scope identity is immutable';
            END IF;
            IF NEW.item_count <> jsonb_array_length(NEW.page_items)
               OR NEW.response_sha256 IS DISTINCT FROM public.v4_production_schedule_jsonb_sha256_v1(NEW.page_items) THEN
                RAISE EXCEPTION 'production schedule page response count or digest mismatch';
            END IF;
            IF NEW.remote_schedule_id IS NULL THEN
                FOR item IN SELECT value FROM jsonb_array_elements(NEW.page_items) WITH ORDINALITY AS entry(value,ordinality)
                LOOP
                    IF jsonb_typeof(item) IS DISTINCT FROM 'object'
                       OR item ?& ARRAY['remote_schedule_id','member_sha256'] IS NOT TRUE
                       OR (item - ARRAY['remote_schedule_id','member_sha256']) <> '{}'::jsonb
                       OR btrim(COALESCE(item->>'remote_schedule_id','')) = ''
                       OR item->>'member_sha256' !~ '^[0-9a-f]{64}$' THEN
                        RAISE EXCEPTION 'production schedule listing page item is malformed';
                    END IF;
                END LOOP;
                IF NEW.last_item_sha256 IS NOT NULL THEN
                    RAISE EXCEPTION 'listing page last-item digest must be NULL';
                END IF;
            ELSE
                FOR item IN SELECT value FROM jsonb_array_elements(NEW.page_items) WITH ORDINALITY AS entry(value,ordinality)
                LOOP
                    IF jsonb_typeof(item) IS DISTINCT FROM 'object'
                       OR item ?& ARRAY['action_id_sha256','scheduled_slot','started_at',
                                      'completed_at','outcome_class','workflow_id_sha256'] IS NOT TRUE
                       OR (item - ARRAY['action_id_sha256','scheduled_slot','started_at',
                                        'completed_at','outcome_class','workflow_id_sha256']) <> '{}'::jsonb
                       OR item->>'action_id_sha256' !~ '^[0-9a-f]{64}$'
                       OR item->>'workflow_id_sha256' !~ '^[0-9a-f]{64}$'
                       OR btrim(COALESCE(item->>'outcome_class','')) = '' THEN
                        RAISE EXCEPTION 'production schedule history action item is malformed';
                    END IF;
                    BEGIN
                        current_slot := (item->>'scheduled_slot')::timestamptz;
                        IF jsonb_typeof(item->'started_at') = 'string' THEN
                            PERFORM (item->>'started_at')::timestamptz;
                        ELSIF jsonb_typeof(item->'started_at') <> 'null' THEN
                            RAISE EXCEPTION 'started_at must be a timestamp or JSON null';
                        END IF;
                        IF jsonb_typeof(item->'completed_at') = 'string' THEN
                            PERFORM (item->>'completed_at')::timestamptz;
                        ELSIF jsonb_typeof(item->'completed_at') <> 'null' THEN
                            RAISE EXCEPTION 'completed_at must be a timestamp or JSON null';
                        END IF;
                    EXCEPTION WHEN others THEN
                        RAISE EXCEPTION 'production schedule history action timestamp is malformed';
                    END;
                    IF previous_slot IS NOT NULL
                       AND ROW(current_slot, item->>'action_id_sha256')
                           < ROW(previous_slot, previous_action) THEN
                        RAISE EXCEPTION 'production schedule history page items must be sorted';
                    END IF;
                    previous_slot := current_slot;
                    previous_action := item->>'action_id_sha256';
                END LOOP;
                IF NEW.last_item_sha256 IS DISTINCT FROM (
                    SELECT action.value->>'action_id_sha256'
                    FROM jsonb_array_elements(NEW.page_items) AS action(value)
                    ORDER BY (action.value->>'scheduled_slot')::timestamptz DESC,
                             action.value->>'action_id_sha256' DESC
                    LIMIT 1
                ) THEN
                    RAISE EXCEPTION 'production schedule history page last item digest mismatch';
                END IF;
            END IF;
            expected_receipt := public.v4_production_schedule_jsonb_sha256_v1(
                jsonb_build_object(
                    'inventory_id',NEW.inventory_id,
                    'remote_schedule_id',NEW.remote_schedule_id,
                    'page_index',NEW.page_index,
                    'request_cursor_sha256',NEW.request_cursor_sha256,
                    'response_sha256',NEW.response_sha256,
                    'item_count',NEW.item_count,
                    'next_cursor_sha256',NEW.next_cursor_sha256,
                    'is_terminal',NEW.is_terminal,
                    'item_set_sha256',NEW.item_set_sha256,
                    'last_item_sha256',NEW.last_item_sha256));
            IF NEW.page_receipt_sha256 IS DISTINCT FROM expected_receipt THEN
                RAISE EXCEPTION 'production schedule page receipt digest mismatch';
            END IF;
            RETURN NEW;
        END;
        $$"""
    )
    op.execute(
        """CREATE TRIGGER guard_v4_production_schedule_page_v1
           BEFORE INSERT OR UPDATE OR DELETE ON public.v4_production_schedule_inventory_pages
           FOR EACH ROW EXECUTE FUNCTION public.v4_guard_production_schedule_page_v1()"""
    )
    op.execute(
        """CREATE FUNCTION public.v4_guard_production_schedule_binding_v1()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public AS $$
        DECLARE
            inventory_row public.v4_production_schedule_inventories%ROWTYPE;
            member_row public.v4_production_schedule_inventory_members%ROWTYPE;
            expected_fingerprint text;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                SELECT * INTO inventory_row FROM public.v4_production_schedule_inventories
                WHERE inventory_id = OLD.inventory_id FOR UPDATE;
                IF inventory_row.finalized_at IS NOT NULL THEN
                    RAISE EXCEPTION 'finalized production schedule binding is immutable';
                END IF;
                RETURN OLD;
            END IF;
            SELECT * INTO inventory_row FROM public.v4_production_schedule_inventories
            WHERE inventory_id = NEW.inventory_id FOR UPDATE;
            IF NOT FOUND OR inventory_row.finalized_at IS NOT NULL THEN
                RAISE EXCEPTION 'production schedule binding requires an unfinalized inventory';
            END IF;
            IF TG_OP = 'UPDATE' THEN
                RAISE EXCEPTION 'production schedule binding is immutable';
            END IF;
            SELECT * INTO member_row
            FROM public.v4_production_schedule_inventory_members
            WHERE inventory_id=NEW.inventory_id AND remote_schedule_id=NEW.remote_schedule_id;
            IF NOT FOUND OR member_row.classification <> 'bound'
               OR member_row.member_sha256 IS DISTINCT FROM NEW.member_sha256
               OR member_row.member_snapshot IS DISTINCT FROM NEW.member_snapshot
               OR NEW.inventory_sha256 IS DISTINCT FROM inventory_row.inventory_sha256
               OR NEW.target_alias IS DISTINCT FROM inventory_row.target_alias
               OR NEW.namespace IS DISTINCT FROM inventory_row.namespace THEN
                RAISE EXCEPTION 'production schedule binding must match its exact inventory member';
            END IF;
            IF NEW.source_kind NOT IN ('intelligence_research','governed_publication')
               OR (NEW.source_kind='intelligence_research' AND NEW.publication_schedule_id IS NOT NULL)
               OR (NEW.source_kind='governed_publication' AND NEW.publication_schedule_id IS NULL)
               OR NEW.source_actor_id = NEW.source_owner_subject_id
               OR NEW.member_snapshot->>'source_kind' IS DISTINCT FROM NEW.source_kind
               OR NEW.member_snapshot->>'schedule_id' IS DISTINCT FROM NEW.schedule_id::text
               OR NEW.member_snapshot->>'publication_schedule_id' IS DISTINCT FROM
                    NEW.publication_schedule_id::text
               OR NEW.member_snapshot->>'workspace_id' IS DISTINCT FROM NEW.workspace_id::text
               OR NEW.member_snapshot->>'goal_id' IS DISTINCT FROM NEW.goal_id::text
               OR NEW.member_snapshot->>'goal_revision' IS DISTINCT FROM NEW.goal_revision::text
               OR NEW.member_snapshot->>'source_actor_id' IS DISTINCT FROM NEW.source_actor_id::text
               OR NEW.member_snapshot->>'source_owner_subject_id' IS DISTINCT FROM NEW.source_owner_subject_id::text
               OR NEW.member_snapshot->>'schedule_operator_subject_id' IS DISTINCT FROM NEW.schedule_operator_subject_id::text
               OR NEW.member_snapshot->>'owner_ref' IS DISTINCT FROM NEW.owner_ref
               OR NEW.member_snapshot->>'task_queue' IS DISTINCT FROM NEW.task_queue
               OR NEW.member_snapshot->>'workflow_type' IS DISTINCT FROM NEW.workflow_type
               OR NEW.member_snapshot->>'action_id' IS DISTINCT FROM NEW.action_id
               OR NEW.member_snapshot->>'argument_sha256' IS DISTINCT FROM NEW.argument_sha256
               OR NEW.member_snapshot->>'route_id' IS DISTINCT FROM NEW.route_id::text
               OR NEW.member_snapshot->>'route_tuple_sha256' IS DISTINCT FROM NEW.route_tuple_sha256
               OR NEW.member_snapshot->>'slot_id' IS DISTINCT FROM NEW.slot_id::text
               OR NEW.member_snapshot->>'owner_id' IS DISTINCT FROM NEW.owner_id::text
               OR NEW.member_snapshot->>'fencing_epoch' IS DISTINCT FROM NEW.fencing_epoch::text
               OR NEW.member_snapshot->>'route_manifest_sha256' IS DISTINCT FROM NEW.route_manifest_sha256
               OR NEW.member_snapshot->>'artifact_lock_sha256' IS DISTINCT FROM NEW.artifact_lock_sha256
               OR NEW.member_snapshot->>'cadence_policy_revision' IS DISTINCT FROM NEW.cadence_policy_revision
               OR NEW.member_snapshot->>'interval_seconds' IS DISTINCT FROM NEW.interval_seconds::text
               OR (NEW.member_snapshot->>'first_v4_slot')::timestamptz IS DISTINCT FROM NEW.first_v4_slot
               OR NEW.member_snapshot->>'remote_state' IS DISTINCT FROM NEW.remote_state
               OR NEW.next_action IS DISTINCT FROM NULLIF(NEW.member_snapshot->>'next_action','')::timestamptz
               OR NEW.running_action_count::text IS DISTINCT FROM NEW.member_snapshot->>'running_action_count'
               OR NEW.member_snapshot->>'history_page_count' IS DISTINCT FROM NEW.history_page_count::text
               OR NEW.member_snapshot->>'history_action_count' IS DISTINCT FROM NEW.history_action_count::text
               OR NEW.member_snapshot->>'history_page_chain_sha256' IS DISTINCT FROM NEW.history_page_chain_sha256
               OR NEW.member_snapshot->>'history_action_set_sha256' IS DISTINCT FROM NEW.history_action_set_sha256
               OR NEW.member_snapshot->>'history_watermark_sha256' IS DISTINCT FROM NEW.history_watermark_sha256 THEN
                RAISE EXCEPTION 'production schedule source binding facts do not match its member';
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM public.approval_requests approval
                WHERE approval.id=NEW.source_approval_id
                  AND approval.workspace_id=NEW.workspace_id
                  AND approval.policy_version_id IS NOT NULL
                  AND approval.effect_type='production_schedule_binding'
                  AND approval.status='approved' AND approval.decided_by IS NOT NULL
                  AND approval.decided_at IS NOT NULL
                  AND (approval.request_context->>'expires_at')::timestamptz > clock_timestamp()
                  AND approval.request_context->>'schema_version' =
                      'salience.p1-production-schedule-binding-approval.v1'
                  AND approval.request_context->>'target_alias'=NEW.target_alias
                  AND approval.request_context->>'namespace'=NEW.namespace
                  AND approval.request_context->>'inventory_id'=NEW.inventory_id::text
                  AND approval.request_context->>'inventory_sha256'=NEW.inventory_sha256
                  AND approval.request_context->>'source_kind'=NEW.source_kind
                  AND approval.request_context->>'schedule_id'=NEW.schedule_id::text
                  AND approval.request_context->>'publication_schedule_id' IS NOT DISTINCT FROM
                      NEW.publication_schedule_id::text
                  AND approval.request_context->>'workspace_id'=NEW.workspace_id::text
                  AND approval.request_context->>'goal_id'=NEW.goal_id::text
                  AND approval.request_context->>'goal_revision'=NEW.goal_revision::text
                  AND approval.request_context->>'source_actor_id'=NEW.source_actor_id::text
                  AND approval.request_context->>'source_owner_subject_id'=NEW.source_owner_subject_id::text
                  AND approval.request_context->>'schedule_operator_subject_id'=NEW.schedule_operator_subject_id::text
                  AND approval.request_context->>'remote_schedule_id'=NEW.remote_schedule_id
                  AND approval.request_context->>'task_queue'=NEW.task_queue
                  AND approval.request_context->>'workflow_type'=NEW.workflow_type
                  AND approval.request_context->>'action_id'=NEW.action_id
                  AND approval.request_context->>'argument_sha256'=NEW.argument_sha256
                  AND approval.request_context->>'route_id'=NEW.route_id::text
                  AND approval.request_context->>'route_tuple_sha256'=NEW.route_tuple_sha256
                  AND approval.request_context->>'slot_id'=NEW.slot_id::text
                  AND approval.request_context->>'owner_id'=NEW.owner_id::text
                  AND approval.request_context->>'fencing_epoch'=NEW.fencing_epoch::text
                  AND approval.request_context->>'route_manifest_sha256'=NEW.route_manifest_sha256
                  AND approval.request_context->>'artifact_lock_sha256'=NEW.artifact_lock_sha256
                  AND approval.request_context->>'cadence_policy_revision'=NEW.cadence_policy_revision
                  AND (approval.request_context->>'first_v4_slot')::timestamptz=NEW.first_v4_slot
                  AND approval.request_context->>'history_watermark_sha256' IS NOT DISTINCT FROM
                      NEW.history_watermark_sha256
            ) THEN
                RAISE EXCEPTION 'production schedule binding requires exact current typed approval';
            END IF;
            expected_fingerprint := public.v4_production_schedule_jsonb_sha256_v1(
                to_jsonb(NEW) - 'binding_fingerprint');
            IF NEW.binding_fingerprint NOT IN ('0' || repeat('0',63), expected_fingerprint) THEN
                RAISE EXCEPTION 'production schedule binding fingerprint mismatch';
            END IF;
            NEW.binding_fingerprint := expected_fingerprint;
            RETURN NEW;
        END;
        $$"""
    )
    op.execute(
        """CREATE TRIGGER guard_v4_production_schedule_binding_v1
           BEFORE INSERT OR UPDATE OR DELETE ON public.v4_production_schedule_source_bindings
           FOR EACH ROW EXECUTE FUNCTION public.v4_guard_production_schedule_binding_v1()"""
    )
    op.execute(
        """CREATE FUNCTION public.v4_validate_production_schedule_inventory_v1(
            p_inventory_id uuid
        ) RETURNS void LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public AS $$
        DECLARE
            inventory_row public.v4_production_schedule_inventories%ROWTYPE;
            member_row public.v4_production_schedule_inventory_members%ROWTYPE;
            page_count_actual bigint;
            terminal_count bigint;
            min_page_index integer;
            max_page_index integer;
            member_count_actual bigint;
            bound_count_actual bigint;
            out_of_scope_count_actual bigint;
            history_page_count_actual bigint;
            history_action_count_actual bigint;
            history_terminal_count bigint;
            history_min_page integer;
            history_max_page integer;
            history_page_root text;
            history_action_root text;
            history_watermark timestamptz;
            history_watermark_sha256 text;
            expected_root text;
            expected_page_root text;
            expected_member_root text;
            expected_item_set text;
            expected_response text;
            expected_receipt text;
            expected_items jsonb;
        BEGIN
            SELECT * INTO inventory_row
            FROM public.v4_production_schedule_inventories
            WHERE inventory_id = p_inventory_id FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'production schedule inventory does not exist';
            END IF;
            IF inventory_row.page_count < 1
               OR inventory_row.remote_member_count < 0
               OR inventory_row.bound_member_count + inventory_row.out_of_scope_member_count
                    <> inventory_row.remote_member_count THEN
                RAISE EXCEPTION 'production schedule inventory counts are incomplete';
            END IF;

            SELECT count(*), count(*) FILTER (WHERE page.is_terminal),
                   min(page.page_index), max(page.page_index)
            INTO page_count_actual, terminal_count, min_page_index, max_page_index
            FROM public.v4_production_schedule_inventory_pages page
            WHERE page.inventory_id = p_inventory_id
              AND page.remote_schedule_id IS NULL;
            IF page_count_actual <> inventory_row.page_count
               OR min_page_index <> 0
               OR max_page_index <> inventory_row.page_count - 1
               OR terminal_count <> 1
               OR EXISTS (
                    SELECT 1 FROM generate_series(0, inventory_row.page_count - 1) index_value
                    WHERE NOT EXISTS (
                        SELECT 1 FROM public.v4_production_schedule_inventory_pages page
                        WHERE page.inventory_id=p_inventory_id
                          AND page.remote_schedule_id IS NULL
                          AND page.page_index=index_value
                    )
               ) THEN
                RAISE EXCEPTION 'production schedule inventory listing pages are incomplete or truncated';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM public.v4_production_schedule_inventory_pages page
                WHERE page.inventory_id=p_inventory_id
                  AND page.remote_schedule_id IS NULL
                  AND ((page.page_index=inventory_row.page_count-1
                        AND (page.is_terminal IS NOT TRUE OR page.next_cursor_sha256 IS NOT NULL))
                    OR (page.page_index<inventory_row.page_count-1
                        AND (page.is_terminal IS TRUE OR page.next_cursor_sha256 IS NULL
                          OR page.next_cursor_sha256 IS DISTINCT FROM (
                               SELECT following.request_cursor_sha256
                               FROM public.v4_production_schedule_inventory_pages following
                               WHERE following.inventory_id=page.inventory_id
                                 AND following.remote_schedule_id IS NULL
                                 AND following.page_index=page.page_index+1
                          ))))
            ) OR NOT EXISTS (
                SELECT 1 FROM public.v4_production_schedule_inventory_pages first_page
                WHERE first_page.inventory_id=p_inventory_id
                  AND first_page.remote_schedule_id IS NULL
                  AND first_page.page_index=0
                  AND first_page.request_cursor_sha256=
                    public.v4_production_schedule_text_sha256_v1(
                      'salience.p1-production-schedule-inventory.start.v1')
            ) THEN
                RAISE EXCEPTION 'production schedule listing cursor chain or terminal page mismatch';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM public.v4_production_schedule_inventory_pages page
                WHERE page.inventory_id=p_inventory_id
                  AND (page.item_count <> jsonb_array_length(page.page_items)
                    OR page.response_sha256 IS DISTINCT FROM
                         public.v4_production_schedule_jsonb_sha256_v1(page.page_items)
                    OR page.page_receipt_sha256 IS DISTINCT FROM
                         public.v4_production_schedule_jsonb_sha256_v1(jsonb_build_object(
                             'inventory_id',page.inventory_id,
                             'remote_schedule_id',page.remote_schedule_id,
                             'page_index',page.page_index,
                             'request_cursor_sha256',page.request_cursor_sha256,
                             'response_sha256',page.response_sha256,
                             'item_count',page.item_count,
                             'next_cursor_sha256',page.next_cursor_sha256,
                             'is_terminal',page.is_terminal,
                             'item_set_sha256',page.item_set_sha256,
                             'last_item_sha256',page.last_item_sha256)))
            ) THEN
                RAISE EXCEPTION 'production schedule page response or receipt digest mismatch';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM public.v4_production_schedule_inventory_pages page
                WHERE page.inventory_id=p_inventory_id
                  AND page.remote_schedule_id IS NOT NULL
                  AND page.last_item_sha256 IS DISTINCT FROM (
                      SELECT action.value->>'action_id_sha256'
                      FROM jsonb_array_elements(page.page_items) AS action(value)
                      ORDER BY (action.value->>'scheduled_slot')::timestamptz DESC,
                               action.value->>'action_id_sha256' DESC
                      LIMIT 1
                  )
            ) THEN
                RAISE EXCEPTION 'production schedule history page last item digest mismatch';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM public.v4_production_schedule_inventory_pages page
                WHERE page.inventory_id=p_inventory_id
                  AND page.remote_schedule_id IS NULL
                  AND (page.last_item_sha256 IS NOT NULL
                    OR page.item_set_sha256 IS DISTINCT FROM
                      public.v4_production_schedule_jsonb_sha256_v1(
                        COALESCE((
                          SELECT jsonb_agg(item.value->'member_sha256'
                                           ORDER BY item.value->>'remote_schedule_id')
                          FROM jsonb_array_elements(page.page_items) AS item(value)
                        ), '[]'::jsonb))
                    OR page.page_items IS DISTINCT FROM COALESCE((
                          SELECT jsonb_agg(jsonb_build_object(
                                   'remote_schedule_id',member.remote_schedule_id,
                                   'member_sha256',member.member_sha256)
                                   ORDER BY member.remote_schedule_id)
                          FROM public.v4_production_schedule_inventory_members member
                          WHERE member.inventory_id=p_inventory_id
                            AND member.page_index=page.page_index
                        ), '[]'::jsonb))
            ) THEN
                RAISE EXCEPTION 'production schedule listing page/member roots do not match';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM (
                    SELECT item.value->>'remote_schedule_id' AS remote_schedule_id,
                           lag(item.value->>'remote_schedule_id') OVER (
                               ORDER BY page.page_index,item.ordinality) AS previous_remote_id
                    FROM public.v4_production_schedule_inventory_pages page
                    CROSS JOIN LATERAL jsonb_array_elements(page.page_items)
                        WITH ORDINALITY AS item(value,ordinality)
                    WHERE page.inventory_id=p_inventory_id
                      AND page.remote_schedule_id IS NULL
                ) ordered_items
                WHERE previous_remote_id IS NOT NULL
                  AND remote_schedule_id <= previous_remote_id
            ) THEN
                RAISE EXCEPTION 'production schedule listing members are not uniquely sorted';
            END IF;

            SELECT count(*), count(*) FILTER (WHERE member.classification='bound'),
                   count(*) FILTER (WHERE member.classification='out_of_scope')
            INTO member_count_actual, bound_count_actual, out_of_scope_count_actual
            FROM public.v4_production_schedule_inventory_members member
            WHERE member.inventory_id=p_inventory_id;
            IF member_count_actual <> inventory_row.remote_member_count
               OR bound_count_actual <> inventory_row.bound_member_count
               OR out_of_scope_count_actual <> inventory_row.out_of_scope_member_count
               OR EXISTS (
                    SELECT 1 FROM public.v4_production_schedule_inventory_pages page
                    WHERE page.inventory_id=p_inventory_id
                      AND page.remote_schedule_id IS NOT NULL
                      AND NOT EXISTS (
                          SELECT 1 FROM public.v4_production_schedule_inventory_members member
                          WHERE member.inventory_id=page.inventory_id
                            AND member.remote_schedule_id=page.remote_schedule_id
                      )
               ) THEN
                RAISE EXCEPTION 'production schedule member counts or history scope mismatch';
            END IF;

            FOR member_row IN
                SELECT * FROM public.v4_production_schedule_inventory_members member
                WHERE member.inventory_id=p_inventory_id
                ORDER BY member.remote_schedule_id
            LOOP
                SELECT count(*), count(*) FILTER (WHERE page.is_terminal),
                       min(page.page_index), max(page.page_index),
                       COALESCE(sum(page.item_count),0)
                INTO history_page_count_actual, history_terminal_count,
                     history_min_page, history_max_page, history_action_count_actual
                FROM public.v4_production_schedule_inventory_pages page
                WHERE page.inventory_id=p_inventory_id
                  AND page.remote_schedule_id=member_row.remote_schedule_id;
                IF history_page_count_actual <> member_row.history_page_count
                   OR history_page_count_actual < 1
                   OR history_terminal_count <> 1
                   OR history_min_page <> 0
                   OR history_max_page <> member_row.history_page_count-1
                   OR history_action_count_actual <> member_row.history_action_count
                   OR EXISTS (
                        SELECT 1 FROM generate_series(0, member_row.history_page_count-1) index_value
                        WHERE NOT EXISTS (
                            SELECT 1 FROM public.v4_production_schedule_inventory_pages page
                            WHERE page.inventory_id=p_inventory_id
                              AND page.remote_schedule_id=member_row.remote_schedule_id
                              AND page.page_index=index_value
                        )
                   ) THEN
                    RAISE EXCEPTION 'production schedule member history pages or counts are incomplete';
                END IF;
                IF EXISTS (
                    SELECT 1
                    FROM public.v4_production_schedule_inventory_pages page
                    WHERE page.inventory_id=p_inventory_id
                      AND page.remote_schedule_id=member_row.remote_schedule_id
                      AND ((page.page_index=member_row.history_page_count-1
                            AND (page.is_terminal IS NOT TRUE OR page.next_cursor_sha256 IS NOT NULL))
                        OR (page.page_index<member_row.history_page_count-1
                            AND (page.is_terminal IS TRUE OR page.next_cursor_sha256 IS NULL
                              OR page.next_cursor_sha256 IS DISTINCT FROM (
                                   SELECT following.request_cursor_sha256
                                   FROM public.v4_production_schedule_inventory_pages following
                                   WHERE following.inventory_id=page.inventory_id
                                     AND following.remote_schedule_id=page.remote_schedule_id
                                     AND following.page_index=page.page_index+1
                              ))))
                ) OR NOT EXISTS (
                    SELECT 1 FROM public.v4_production_schedule_inventory_pages first_page
                    WHERE first_page.inventory_id=p_inventory_id
                      AND first_page.remote_schedule_id=member_row.remote_schedule_id
                      AND first_page.page_index=0
                      AND first_page.request_cursor_sha256=
                        public.v4_production_schedule_text_sha256_v1(
                          'salience.p1-production-schedule-history.start.v1:'||member_row.remote_schedule_id)
                ) THEN
                    RAISE EXCEPTION 'production schedule history cursor chain or terminal page mismatch';
                END IF;
                IF EXISTS (
                    SELECT 1
                    FROM public.v4_production_schedule_inventory_pages page
                    WHERE page.inventory_id=p_inventory_id
                      AND page.remote_schedule_id=member_row.remote_schedule_id
                      AND page.item_set_sha256 IS DISTINCT FROM
                        public.v4_production_schedule_jsonb_sha256_v1(COALESCE((
                          SELECT jsonb_agg(item.value->'action_id_sha256'
                            ORDER BY (item.value->>'scheduled_slot')::timestamptz,
                                     item.value->>'action_id_sha256')
                          FROM jsonb_array_elements(page.page_items) AS item(value)
                        ), '[]'::jsonb))
                ) OR EXISTS (
                    SELECT 1
                    FROM (
                        SELECT (item.value->>'scheduled_slot')::timestamptz AS scheduled_slot,
                               item.value->>'action_id_sha256' AS action_id_sha256,
                               lag((item.value->>'scheduled_slot')::timestamptz) OVER (
                                   ORDER BY page.page_index,item.ordinality) AS previous_slot,
                               lag(item.value->>'action_id_sha256') OVER (
                                   ORDER BY page.page_index,item.ordinality) AS previous_action_id
                        FROM public.v4_production_schedule_inventory_pages page
                        CROSS JOIN LATERAL jsonb_array_elements(page.page_items)
                            WITH ORDINALITY AS item(value,ordinality)
                        WHERE page.inventory_id=p_inventory_id
                          AND page.remote_schedule_id=member_row.remote_schedule_id
                    ) actions
                    WHERE previous_slot IS NOT NULL
                      AND ROW(scheduled_slot,action_id_sha256)
                          < ROW(previous_slot,previous_action_id)
                ) THEN
                    RAISE EXCEPTION 'production schedule history item-set root or ordering mismatch';
                END IF;
                SELECT public.v4_production_schedule_jsonb_sha256_v1(COALESCE(
                           jsonb_agg(item.value->'action_id_sha256'
                             ORDER BY (item.value->>'scheduled_slot')::timestamptz,
                                      item.value->>'action_id_sha256'),
                           '[]'::jsonb)),
                       (array_agg((item.value->>'scheduled_slot')::timestamptz
                             ORDER BY (item.value->>'scheduled_slot')::timestamptz DESC,
                                      item.value->>'action_id_sha256' DESC))[1],
                       (array_agg(item.value->>'action_id_sha256'
                             ORDER BY (item.value->>'scheduled_slot')::timestamptz DESC,
                                      item.value->>'action_id_sha256' DESC))[1]
                INTO history_action_root, history_watermark, history_watermark_sha256
                FROM public.v4_production_schedule_inventory_pages page
                CROSS JOIN LATERAL jsonb_array_elements(page.page_items) AS item(value)
                WHERE page.inventory_id=p_inventory_id
                  AND page.remote_schedule_id=member_row.remote_schedule_id;
                IF history_action_root IS NULL THEN
                    history_action_root := public.v4_production_schedule_jsonb_sha256_v1('[]'::jsonb);
                    history_watermark := NULL;
                    history_watermark_sha256 := NULL;
                END IF;
                SELECT public.v4_production_schedule_jsonb_sha256_v1(COALESCE(
                           jsonb_agg(to_jsonb(page.page_receipt_sha256)
                                     ORDER BY page.page_index),'[]'::jsonb))
                INTO history_page_root
                FROM public.v4_production_schedule_inventory_pages page
                WHERE page.inventory_id=p_inventory_id
                  AND page.remote_schedule_id=member_row.remote_schedule_id;
                IF history_action_root IS DISTINCT FROM member_row.history_action_set_sha256
                   OR history_page_root IS DISTINCT FROM member_row.history_page_chain_sha256
                   OR history_watermark IS DISTINCT FROM member_row.history_watermark
                   OR history_watermark_sha256 IS DISTINCT FROM member_row.history_watermark_sha256
                   OR (member_row.classification='bound' AND
                       (SELECT terminal_page.last_item_sha256
                        FROM public.v4_production_schedule_inventory_pages terminal_page
                        WHERE terminal_page.inventory_id=p_inventory_id
                          AND terminal_page.remote_schedule_id=member_row.remote_schedule_id
                          AND terminal_page.is_terminal)
                        IS DISTINCT FROM member_row.history_watermark_sha256)
                   OR member_row.member_snapshot->>'history_action_set_sha256'
                        IS DISTINCT FROM member_row.history_action_set_sha256
                   OR member_row.member_snapshot->>'history_page_chain_sha256'
                        IS DISTINCT FROM member_row.history_page_chain_sha256
                   OR member_row.member_snapshot->>'history_watermark_sha256'
                        IS DISTINCT FROM member_row.history_watermark_sha256
                   OR NULLIF(member_row.member_snapshot->>'history_watermark','')::timestamptz
                        IS DISTINCT FROM member_row.history_watermark THEN
                    RAISE EXCEPTION 'production schedule member action-set root or watermark mismatch';
                END IF;
                IF member_row.classification='bound'
                   AND member_row.history_page_count < 1 THEN
                    RAISE EXCEPTION 'bound production schedule member requires complete action history';
                END IF;
            END LOOP;

            SELECT public.v4_production_schedule_jsonb_sha256_v1(COALESCE(
                       jsonb_agg(to_jsonb(page.page_receipt_sha256)
                                 ORDER BY page.page_index),'[]'::jsonb))
            INTO expected_page_root
            FROM public.v4_production_schedule_inventory_pages page
            WHERE page.inventory_id=p_inventory_id AND page.remote_schedule_id IS NULL;
            SELECT public.v4_production_schedule_jsonb_sha256_v1(COALESCE(
                       jsonb_agg(jsonb_build_object(
                           'remote_schedule_id',member.remote_schedule_id,
                           'member_sha256',member.member_sha256)
                           ORDER BY member.remote_schedule_id),'[]'::jsonb))
            INTO expected_member_root
            FROM public.v4_production_schedule_inventory_members member
            WHERE member.inventory_id=p_inventory_id;
            IF expected_page_root IS DISTINCT FROM inventory_row.inventory_page_chain_sha256
               OR expected_member_root IS DISTINCT FROM inventory_row.member_set_sha256 THEN
                RAISE EXCEPTION 'production schedule inventory sorted page/member root mismatch';
            END IF;
            expected_root := public.v4_production_schedule_jsonb_sha256_v1(jsonb_build_object(
                'schema_version','salience.p1-production-schedule-inventory.v1',
                'inventory_id',inventory_row.inventory_id,
                'target_alias',inventory_row.target_alias,
                'namespace',inventory_row.namespace,
                'captured_at',inventory_row.captured_at,
                'source_tool',inventory_row.source_tool,
                'source_tool_version',inventory_row.source_tool_version,
                'candidate_sha256',inventory_row.candidate_sha256,
                'configuration_sha256',inventory_row.configuration_sha256,
                'page_count',inventory_row.page_count,
                'remote_member_count',inventory_row.remote_member_count,
                'bound_member_count',inventory_row.bound_member_count,
                'out_of_scope_member_count',inventory_row.out_of_scope_member_count,
                'inventory_page_chain_sha256',inventory_row.inventory_page_chain_sha256,
                'member_set_sha256',inventory_row.member_set_sha256));
            IF expected_root IS DISTINCT FROM inventory_row.inventory_sha256 THEN
                RAISE EXCEPTION 'production schedule inventory digest does not match its sorted roots';
            END IF;

            IF (SELECT count(*) FROM public.v4_production_schedule_source_bindings binding
                WHERE binding.inventory_id=p_inventory_id) <> inventory_row.bound_member_count
               OR EXISTS (
                    SELECT 1 FROM public.v4_production_schedule_inventory_members member
                    LEFT JOIN public.v4_production_schedule_source_bindings binding
                      ON binding.inventory_id=member.inventory_id
                     AND binding.remote_schedule_id=member.remote_schedule_id
                    WHERE member.inventory_id=p_inventory_id
                      AND ((member.classification='bound' AND binding.goal_id IS NULL)
                        OR (member.classification='out_of_scope' AND binding.goal_id IS NOT NULL))
               ) THEN
                RAISE EXCEPTION 'production schedule inventory requires exactly one binding per bound member and none out of scope';
            END IF;
            IF EXISTS (
                SELECT 1 FROM public.v4_production_schedule_inventory_members member
                JOIN public.v4_production_schedule_source_bindings binding
                  ON binding.inventory_id=member.inventory_id
                 AND binding.remote_schedule_id=member.remote_schedule_id
                WHERE member.inventory_id=p_inventory_id
                  AND (binding.member_sha256 IS DISTINCT FROM member.member_sha256
                    OR binding.member_snapshot IS DISTINCT FROM member.member_snapshot
                    OR binding.inventory_sha256 IS DISTINCT FROM inventory_row.inventory_sha256
                    OR binding.binding_fingerprint IS DISTINCT FROM
                       public.v4_production_schedule_jsonb_sha256_v1(
                          to_jsonb(binding)-'binding_fingerprint')
                    OR binding.source_kind IS DISTINCT FROM member.member_snapshot->>'source_kind'
                    OR binding.schedule_id::text IS DISTINCT FROM member.member_snapshot->>'schedule_id'
                    OR binding.publication_schedule_id::text IS DISTINCT FROM
                       member.member_snapshot->>'publication_schedule_id'
                    OR binding.workspace_id::text IS DISTINCT FROM member.member_snapshot->>'workspace_id'
                    OR binding.goal_id::text IS DISTINCT FROM member.member_snapshot->>'goal_id'
                    OR binding.goal_revision::text IS DISTINCT FROM member.member_snapshot->>'goal_revision'
                    OR binding.source_actor_id::text IS DISTINCT FROM member.member_snapshot->>'source_actor_id'
                    OR binding.source_owner_subject_id::text IS DISTINCT FROM member.member_snapshot->>'source_owner_subject_id'
                    OR binding.schedule_operator_subject_id::text IS DISTINCT FROM member.member_snapshot->>'schedule_operator_subject_id'
                    OR binding.owner_ref IS DISTINCT FROM member.member_snapshot->>'owner_ref'
                    OR binding.task_queue IS DISTINCT FROM member.member_snapshot->>'task_queue'
                    OR binding.workflow_type IS DISTINCT FROM member.member_snapshot->>'workflow_type'
                    OR binding.action_id IS DISTINCT FROM member.member_snapshot->>'action_id'
                    OR binding.argument_sha256 IS DISTINCT FROM member.member_snapshot->>'argument_sha256'
                    OR binding.route_id::text IS DISTINCT FROM member.member_snapshot->>'route_id'
                    OR binding.route_tuple_sha256 IS DISTINCT FROM member.member_snapshot->>'route_tuple_sha256'
                    OR binding.slot_id::text IS DISTINCT FROM member.member_snapshot->>'slot_id'
                    OR binding.owner_id::text IS DISTINCT FROM member.member_snapshot->>'owner_id'
                    OR binding.fencing_epoch::text IS DISTINCT FROM member.member_snapshot->>'fencing_epoch'
                    OR binding.route_manifest_sha256 IS DISTINCT FROM member.member_snapshot->>'route_manifest_sha256'
                    OR binding.artifact_lock_sha256 IS DISTINCT FROM member.member_snapshot->>'artifact_lock_sha256'
                    OR binding.cadence_policy_revision IS DISTINCT FROM member.member_snapshot->>'cadence_policy_revision'
                    OR binding.interval_seconds::text IS DISTINCT FROM member.member_snapshot->>'interval_seconds'
                    OR binding.first_v4_slot IS DISTINCT FROM (member.member_snapshot->>'first_v4_slot')::timestamptz
                    OR binding.observed_last_slot IS DISTINCT FROM NULLIF(member.member_snapshot->>'observed_last_slot','')::timestamptz
                    OR binding.remote_state IS DISTINCT FROM member.member_snapshot->>'remote_state'
                    OR binding.next_action IS DISTINCT FROM NULLIF(member.member_snapshot->>'next_action','')::timestamptz
                    OR binding.running_action_count::text IS DISTINCT FROM member.member_snapshot->>'running_action_count'
                    OR binding.history_page_count::text IS DISTINCT FROM member.member_snapshot->>'history_page_count'
                    OR binding.history_action_count::text IS DISTINCT FROM member.member_snapshot->>'history_action_count'
                    OR binding.history_page_chain_sha256 IS DISTINCT FROM member.member_snapshot->>'history_page_chain_sha256'
                    OR binding.history_action_set_sha256 IS DISTINCT FROM member.member_snapshot->>'history_action_set_sha256'
                    OR binding.history_watermark IS DISTINCT FROM NULLIF(member.member_snapshot->>'history_watermark','')::timestamptz
                    OR binding.history_watermark_sha256 IS DISTINCT FROM member.member_snapshot->>'history_watermark_sha256')
                    OR NOT EXISTS (
                        SELECT 1
                        FROM public.approval_requests approval
                        JOIN public.policy_versions policy
                          ON policy.id=approval.policy_version_id
                         AND policy.workspace_id=approval.workspace_id
                        WHERE approval.id=binding.source_approval_id
                          AND approval.workspace_id=binding.workspace_id
                          AND approval.policy_version_id IS NOT NULL
                          AND approval.effect_type='production_schedule_binding'
                          AND approval.status='approved'
                          AND approval.decided_by IS NOT NULL
                          AND approval.decided_at IS NOT NULL
                          AND (approval.request_context->>'expires_at')::timestamptz > clock_timestamp()
                          AND approval.request_context->>'schema_version' =
                              'salience.p1-production-schedule-binding-approval.v1'
                          AND approval.request_context->>'target_alias'=binding.target_alias
                          AND approval.request_context->>'namespace'=binding.namespace
                          AND approval.request_context->>'inventory_id'=binding.inventory_id::text
                          AND approval.request_context->>'inventory_sha256'=binding.inventory_sha256
                          AND approval.request_context->>'source_kind'=binding.source_kind
                          AND approval.request_context->>'schedule_id'=binding.schedule_id::text
                          AND approval.request_context->>'publication_schedule_id' IS NOT DISTINCT FROM
                              binding.publication_schedule_id::text
                          AND approval.request_context->>'workspace_id'=binding.workspace_id::text
                          AND approval.request_context->>'goal_id'=binding.goal_id::text
                          AND approval.request_context->>'goal_revision'=binding.goal_revision::text
                          AND approval.request_context->>'source_actor_id'=binding.source_actor_id::text
                          AND approval.request_context->>'source_owner_subject_id'=binding.source_owner_subject_id::text
                          AND approval.request_context->>'schedule_operator_subject_id'=binding.schedule_operator_subject_id::text
                          AND approval.request_context->>'remote_schedule_id'=binding.remote_schedule_id
                          AND approval.request_context->>'task_queue'=binding.task_queue
                          AND approval.request_context->>'workflow_type'=binding.workflow_type
                          AND approval.request_context->>'action_id'=binding.action_id
                          AND approval.request_context->>'argument_sha256'=binding.argument_sha256
                          AND approval.request_context->>'route_id'=binding.route_id::text
                          AND approval.request_context->>'route_tuple_sha256'=binding.route_tuple_sha256
                          AND approval.request_context->>'slot_id'=binding.slot_id::text
                          AND approval.request_context->>'owner_id'=binding.owner_id::text
                          AND approval.request_context->>'fencing_epoch'=binding.fencing_epoch::text
                          AND approval.request_context->>'route_manifest_sha256'=binding.route_manifest_sha256
                          AND approval.request_context->>'artifact_lock_sha256'=binding.artifact_lock_sha256
                          AND approval.request_context->>'cadence_policy_revision'=binding.cadence_policy_revision
                          AND (approval.request_context->>'first_v4_slot')::timestamptz=binding.first_v4_slot
                          AND approval.request_context->>'history_watermark_sha256' IS NOT DISTINCT FROM
                              binding.history_watermark_sha256
                          AND approval.request_context->>'policy_version_id'=approval.policy_version_id::text
                          AND approval.request_context->>'policy_document_hash'=policy.document_hash
                    )
              ) THEN
                RAISE EXCEPTION 'production schedule binding facts or fingerprint do not match its member';
            END IF;

            IF EXISTS (
                WITH eligible_sources AS (
                    SELECT 'intelligence_research'::text AS source_kind,
                           source.schedule_id, NULL::uuid AS publication_schedule_id,
                           source.remote_id, source.workspace_id, source.goal_id,
                           source.goal_revision, source.actor_id AS source_actor_id,
                           source.interval_seconds, source.first_v4_slot,
                           source.observed_last_slot
                    FROM public.v4_native_schedule_sources source
                    JOIN public.job_schedules schedule
                      ON schedule.id=source.schedule_id
                     AND schedule.workspace_id=source.workspace_id
                    JOIN public.v4_goals goal
                      ON goal.id=source.goal_id AND goal.workspace_id=source.workspace_id
                    JOIN public.v4_goal_revisions revision
                      ON revision.goal_id=goal.id AND revision.revision=source.goal_revision
                    WHERE schedule.status='active' AND goal.state='active'
                      AND goal.revision=source.goal_revision
                    UNION ALL
                    SELECT 'governed_publication'::text AS source_kind,
                           source.schedule_id, source.publication_schedule_id,
                           source.remote_id, source.workspace_id, source.goal_id,
                           source.goal_revision, source.actor_id AS source_actor_id,
                           source.interval_seconds, source.first_v4_slot,
                           source.observed_last_slot
                    FROM public.v4_native_publication_schedule_sources source
                    JOIN public.job_schedules schedule
                      ON schedule.id=source.schedule_id
                     AND schedule.workspace_id=source.workspace_id
                    JOIN public.publication_schedules publication
                      ON publication.id=source.publication_schedule_id
                     AND publication.job_schedule_id=source.schedule_id
                    JOIN public.v4_goals goal
                      ON goal.id=source.goal_id AND goal.workspace_id=source.workspace_id
                    JOIN public.v4_goal_revisions revision
                      ON revision.goal_id=goal.id AND revision.revision=source.goal_revision
                    WHERE schedule.status='active' AND publication.status='scheduled'
                      AND goal.state='active' AND goal.revision=source.goal_revision
                )
                SELECT 1 FROM eligible_sources source
                WHERE (SELECT count(*)
                       FROM public.v4_production_schedule_inventory_members member
                       WHERE member.inventory_id=p_inventory_id
                         AND member.classification='bound'
                         AND member.member_snapshot->>'source_kind'=source.source_kind
                         AND member.member_snapshot->>'schedule_id'=source.schedule_id::text
                         AND member.member_snapshot->>'publication_schedule_id' IS NOT DISTINCT FROM
                             source.publication_schedule_id::text
                         AND member.member_snapshot->>'remote_schedule_id'=source.remote_id
                         AND member.member_snapshot->>'workspace_id'=source.workspace_id::text
                         AND member.member_snapshot->>'goal_id'=source.goal_id::text
                         AND member.member_snapshot->>'goal_revision'=source.goal_revision::text
                         AND member.member_snapshot->>'source_actor_id'=source.source_actor_id::text
                         AND member.member_snapshot->>'interval_seconds'=source.interval_seconds::text
                         AND (member.member_snapshot->>'first_v4_slot')::timestamptz=source.first_v4_slot
                         AND NULLIF(member.member_snapshot->>'observed_last_slot','')::timestamptz
                               IS NOT DISTINCT FROM source.observed_last_slot) <> 1
            ) THEN
                RAISE EXCEPTION 'production schedule inventory omits or mismatches an eligible local source';
            END IF;
            IF EXISTS (
                SELECT 1 FROM public.v4_production_schedule_inventory_members member
                WHERE member.inventory_id=p_inventory_id AND member.classification='bound'
                  AND NOT EXISTS (
                      SELECT 1 FROM (
                          SELECT 'intelligence_research'::text AS source_kind,
                                 source.schedule_id,NULL::uuid AS publication_schedule_id,
                                 source.remote_id,source.workspace_id,source.goal_id,
                                 source.goal_revision,source.actor_id AS source_actor_id,
                                 source.interval_seconds,source.first_v4_slot,source.observed_last_slot
                          FROM public.v4_native_schedule_sources source
                          JOIN public.job_schedules schedule
                            ON schedule.id=source.schedule_id AND schedule.workspace_id=source.workspace_id
                          JOIN public.v4_goals goal
                            ON goal.id=source.goal_id AND goal.workspace_id=source.workspace_id
                          WHERE schedule.status='active' AND goal.state='active'
                            AND goal.revision=source.goal_revision
                          UNION ALL
                          SELECT 'governed_publication'::text,source.schedule_id,
                                 source.publication_schedule_id,source.remote_id,
                                 source.workspace_id,source.goal_id,source.goal_revision,
                                 source.actor_id,source.interval_seconds,source.first_v4_slot,
                                 source.observed_last_slot
                          FROM public.v4_native_publication_schedule_sources source
                          JOIN public.job_schedules schedule
                            ON schedule.id=source.schedule_id AND schedule.workspace_id=source.workspace_id
                          JOIN public.publication_schedules publication
                            ON publication.id=source.publication_schedule_id
                           AND publication.job_schedule_id=source.schedule_id
                          JOIN public.v4_goals goal
                            ON goal.id=source.goal_id AND goal.workspace_id=source.workspace_id
                          WHERE schedule.status='active' AND publication.status='scheduled'
                            AND goal.state='active' AND goal.revision=source.goal_revision
                      ) source
                      WHERE member.member_snapshot->>'source_kind'=source.source_kind
                        AND member.member_snapshot->>'schedule_id'=source.schedule_id::text
                        AND member.member_snapshot->>'publication_schedule_id' IS NOT DISTINCT FROM
                            source.publication_schedule_id::text
                        AND member.member_snapshot->>'remote_schedule_id'=source.remote_id
                        AND member.member_snapshot->>'workspace_id'=source.workspace_id::text
                        AND member.member_snapshot->>'goal_id'=source.goal_id::text
                        AND member.member_snapshot->>'goal_revision'=source.goal_revision::text
                        AND member.member_snapshot->>'source_actor_id'=source.source_actor_id::text
                        AND member.member_snapshot->>'interval_seconds'=source.interval_seconds::text
                        AND (member.member_snapshot->>'first_v4_slot')::timestamptz=source.first_v4_slot
                        AND NULLIF(member.member_snapshot->>'observed_last_slot','')::timestamptz
                              IS NOT DISTINCT FROM source.observed_last_slot
                  )
            ) THEN
                RAISE EXCEPTION 'production schedule binding does not identify exactly one current local source';
            END IF;
        END;
        $$"""
    )
    op.execute(
        """CREATE FUNCTION public.v4_finalize_production_schedule_inventory_v1(
            p_inventory_id uuid
        ) RETURNS timestamptz LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public AS $$
        DECLARE
            finalized timestamptz;
        BEGIN
            PERFORM 1 FROM public.v4_production_schedule_inventories
            WHERE inventory_id=p_inventory_id FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'production schedule inventory does not exist';
            END IF;
            IF EXISTS (SELECT 1 FROM public.v4_production_schedule_inventories
                       WHERE inventory_id=p_inventory_id AND finalized_at IS NOT NULL) THEN
                RAISE EXCEPTION 'production schedule inventory is already finalized and immutable';
            END IF;
            PERFORM public.v4_validate_production_schedule_inventory_v1(p_inventory_id);
            PERFORM set_config('salience.p1_production_schedule_finalize',p_inventory_id::text,true);
            UPDATE public.v4_production_schedule_inventories
            SET finalized_at=clock_timestamp()
            WHERE inventory_id=p_inventory_id
            RETURNING finalized_at INTO finalized;
            RETURN finalized;
        END;
        $$"""
    )
    op.execute(
        """CREATE FUNCTION public.v4_recheck_finalized_production_schedule_inventory_v1()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public AS $$
        DECLARE
            selected_inventory_id uuid;
            finalized timestamptz;
        BEGIN
            IF TG_OP='DELETE' THEN
                selected_inventory_id := OLD.inventory_id;
            ELSE
                selected_inventory_id := NEW.inventory_id;
            END IF;
            SELECT finalized_at INTO finalized
            FROM public.v4_production_schedule_inventories
            WHERE inventory_id=selected_inventory_id;
            IF finalized IS NOT NULL THEN
                PERFORM public.v4_validate_production_schedule_inventory_v1(selected_inventory_id);
            END IF;
            IF TG_OP='DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
        END;
        $$"""
    )
    for table in (
        "v4_production_schedule_inventories",
        "v4_production_schedule_inventory_pages",
        "v4_production_schedule_inventory_members",
        "v4_production_schedule_source_bindings",
    ):
        op.execute(
            f"""CREATE CONSTRAINT TRIGGER recheck_finalized_{table}_v1
               AFTER INSERT OR UPDATE OR DELETE ON public.{table}
               DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
               EXECUTE FUNCTION public.v4_recheck_finalized_production_schedule_inventory_v1()"""
        )
    for table in (
        "v4_production_schedule_inventories",
        "v4_production_schedule_inventory_pages",
        "v4_production_schedule_inventory_members",
        "v4_production_schedule_source_bindings",
    ):
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE public.{table} FROM PUBLIC")
    for function in (
        "v4_production_schedule_jsonb_sha256_v1(jsonb)",
        "v4_production_schedule_text_sha256_v1(text)",
        "v4_guard_production_schedule_approval_v1()",
        "v4_guard_production_schedule_inventory_header_v1()",
        "v4_guard_production_schedule_member_v1()",
        "v4_guard_production_schedule_page_v1()",
        "v4_guard_production_schedule_binding_v1()",
        "v4_validate_production_schedule_inventory_v1(uuid)",
        "v4_finalize_production_schedule_inventory_v1(uuid)",
        "v4_recheck_finalized_production_schedule_inventory_v1()",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION public.{function} FROM PUBLIC")


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout='3s'")
    op.execute(
        """LOCK TABLE public.v4_production_schedule_source_bindings,
                          public.v4_production_schedule_inventory_pages,
                          public.v4_production_schedule_inventory_members,
                          public.v4_production_schedule_inventories,
                          public.approval_requests
           IN ACCESS EXCLUSIVE MODE"""
    )
    op.execute(
        """DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM public.v4_production_schedule_inventories)
               OR EXISTS (SELECT 1 FROM public.v4_production_schedule_inventory_pages)
               OR EXISTS (SELECT 1 FROM public.v4_production_schedule_inventory_members)
               OR EXISTS (SELECT 1 FROM public.v4_production_schedule_source_bindings)
               OR EXISTS (
                   SELECT 1 FROM public.approval_requests
                   WHERE request_context->>'schema_version' =
                     'salience.p1-production-schedule-binding-approval.v1'
               ) THEN
                RAISE EXCEPTION
                    'preserve production schedule inventory and binding evidence';
            END IF;
        END;
        $$"""
    )
    op.execute(
        "DROP TRIGGER recheck_finalized_v4_production_schedule_source_bindings_v1 ON public.v4_production_schedule_source_bindings"
    )
    op.execute(
        "DROP TRIGGER recheck_finalized_v4_production_schedule_inventory_members_v1 ON public.v4_production_schedule_inventory_members"
    )
    op.execute(
        "DROP TRIGGER recheck_finalized_v4_production_schedule_inventory_pages_v1 ON public.v4_production_schedule_inventory_pages"
    )
    op.execute(
        "DROP TRIGGER recheck_finalized_v4_production_schedule_inventories_v1 ON public.v4_production_schedule_inventories"
    )
    op.execute(
        "DROP TRIGGER guard_v4_production_schedule_binding_v1 ON public.v4_production_schedule_source_bindings"
    )
    op.execute(
        "DROP TRIGGER guard_v4_production_schedule_page_v1 ON public.v4_production_schedule_inventory_pages"
    )
    op.execute(
        "DROP TRIGGER guard_v4_production_schedule_member_v1 ON public.v4_production_schedule_inventory_members"
    )
    op.execute(
        "DROP TRIGGER guard_v4_production_schedule_inventory_header_v1 ON public.v4_production_schedule_inventories"
    )
    op.execute(
        "DROP TRIGGER guard_production_schedule_approval_v1 ON public.approval_requests"
    )
    op.execute(
        "DROP TABLE public.v4_production_schedule_source_bindings"
    )
    op.execute(
        "DROP TABLE public.v4_production_schedule_inventory_pages"
    )
    op.execute(
        "DROP TABLE public.v4_production_schedule_inventory_members"
    )
    op.execute(
        "DROP TABLE public.v4_production_schedule_inventories"
    )
    for function in (
        "v4_recheck_finalized_production_schedule_inventory_v1()",
        "v4_finalize_production_schedule_inventory_v1(uuid)",
        "v4_validate_production_schedule_inventory_v1(uuid)",
        "v4_guard_production_schedule_binding_v1()",
        "v4_guard_production_schedule_page_v1()",
        "v4_guard_production_schedule_member_v1()",
        "v4_guard_production_schedule_inventory_header_v1()",
        "v4_guard_production_schedule_approval_v1()",
        "v4_production_schedule_text_sha256_v1(text)",
        "v4_production_schedule_jsonb_sha256_v1(jsonb)",
    ):
        op.execute(f"DROP FUNCTION public.{function}")
