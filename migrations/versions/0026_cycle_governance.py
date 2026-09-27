"""Add scoped stop authority, fixture permit claims and bound recovery cases."""

from alembic import op


revision = "0026_cycle_governance"
down_revision = "0025_accounting_categories"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE v4_stop_scopes (
            workspace_id uuid NOT NULL REFERENCES workspaces(id),
            scope_key text NOT NULL CHECK (scope_key='workspace' OR scope_key ~ '^goal:[0-9a-f-]{36}$'),
            revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
            stopped boolean NOT NULL DEFAULT false,
            changed_by uuid REFERENCES identity_subjects(id),
            reason text,
            changed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (workspace_id,scope_key)
        )
    """)
    op.execute("""
        CREATE TABLE v4_stop_commands (
            id uuid PRIMARY KEY,
            workspace_id uuid NOT NULL,
            scope_key text NOT NULL,
            idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 256),
            fingerprint text NOT NULL,
            actor_id uuid NOT NULL REFERENCES identity_subjects(id),
            resulting_revision integer NOT NULL,
            stopped boolean NOT NULL,
            traceparent text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            FOREIGN KEY (workspace_id,scope_key) REFERENCES v4_stop_scopes(workspace_id,scope_key),
            UNIQUE (workspace_id,scope_key,idempotency_key)
        )
    """)
    op.execute("""
        CREATE TABLE v4_permit_claims (
            id uuid PRIMARY KEY,
            cycle_id uuid NOT NULL UNIQUE REFERENCES v4_cycles(id),
            operation_id uuid NOT NULL UNIQUE,
            context_id uuid NOT NULL REFERENCES v4_run_contexts(id),
            request_fingerprint text NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
            state text NOT NULL DEFAULT 'issued' CHECK (state IN ('issued','claimed','unknown','resolved')),
            current_permit_id uuid,
            revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
            claimed_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute("""
        CREATE TABLE v4_dispatch_permits (
            id uuid PRIMARY KEY,
            claim_id uuid NOT NULL REFERENCES v4_permit_claims(id),
            version integer NOT NULL CHECK (version > 0),
            idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 256),
            fingerprint text NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
            subject_id uuid NOT NULL REFERENCES identity_subjects(id),
            goal_revision integer NOT NULL,
            workspace_stop_revision integer NOT NULL,
            goal_stop_revision integer NOT NULL,
            reservation_id uuid REFERENCES budget_reservations(id),
            request jsonb NOT NULL,
            expires_at timestamptz NOT NULL,
            traceparent text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (claim_id,version), UNIQUE (claim_id,idempotency_key),
            CHECK (expires_at > created_at)
        )
    """)
    op.execute("ALTER TABLE v4_permit_claims ADD CONSTRAINT v4_current_permit_fk FOREIGN KEY (current_permit_id) REFERENCES v4_dispatch_permits(id) DEFERRABLE INITIALLY DEFERRED")
    op.execute("""
        CREATE TABLE v4_recovery_cases (
            id uuid PRIMARY KEY,
            workspace_id uuid NOT NULL REFERENCES workspaces(id),
            goal_id uuid NOT NULL REFERENCES v4_goals(id),
            target_intent_id uuid REFERENCES v4_cycle_intents(id),
            target_cycle_id uuid REFERENCES v4_cycles(id),
            context_id uuid REFERENCES v4_run_contexts(id),
            operation_id uuid,
            kind text NOT NULL CHECK (kind IN ('admission_review','retry','reconciliation','rework','manual_review')),
            open_fingerprint text NOT NULL CHECK (open_fingerprint ~ '^[0-9a-f]{64}$'),
            failure_class text NOT NULL CHECK (failure_class IN ('policy_denial','technical_failure','quality_failure','unknown_effect','admission_review','deadline','manual_review')),
            state text NOT NULL CHECK (state IN ('retry_due','reconciling','rework_due','awaiting_review','suspended','terminal','resolved')),
            revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
            owner_id uuid NOT NULL REFERENCES identity_subjects(id),
            reason text NOT NULL CHECK (length(reason) BETWEEN 1 AND 2000),
            deadline timestamptz NOT NULL,
            max_actions integer NOT NULL CHECK (max_actions BETWEEN 1 AND 10),
            action_count integer NOT NULL DEFAULT 0 CHECK (action_count >= 0),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            CHECK ((target_intent_id IS NULL) != (target_cycle_id IS NULL)),
            CHECK ((target_cycle_id IS NULL) = (context_id IS NULL)),
            CHECK ((target_cycle_id IS NULL) = (operation_id IS NULL))
        )
    """)
    op.execute("CREATE UNIQUE INDEX v4_case_intent_fingerprint ON v4_recovery_cases(target_intent_id,kind,open_fingerprint) WHERE target_intent_id IS NOT NULL")
    op.execute("CREATE UNIQUE INDEX v4_case_cycle_fingerprint ON v4_recovery_cases(target_cycle_id,kind,open_fingerprint) WHERE target_cycle_id IS NOT NULL")
    op.execute("CREATE UNIQUE INDEX v4_case_intent_active ON v4_recovery_cases(target_intent_id,kind) WHERE target_intent_id IS NOT NULL AND state NOT IN ('terminal','resolved')")
    op.execute("CREATE UNIQUE INDEX v4_case_cycle_active ON v4_recovery_cases(target_cycle_id,kind) WHERE target_cycle_id IS NOT NULL AND state NOT IN ('terminal','resolved')")
    op.execute("""
        CREATE TABLE v4_case_commands (
            id uuid PRIMARY KEY,
            case_id uuid NOT NULL REFERENCES v4_recovery_cases(id),
            idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 256),
            fingerprint text NOT NULL,
            action text NOT NULL,
            actor_id uuid NOT NULL REFERENCES identity_subjects(id),
            result jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (case_id,idempotency_key)
        )
    """)
    op.execute("""
        CREATE TABLE v4_case_reviews (
            id uuid PRIMARY KEY,
            case_id uuid NOT NULL UNIQUE REFERENCES v4_recovery_cases(id),
            case_revision integer NOT NULL,
            context_id uuid REFERENCES v4_run_contexts(id),
            operation_id uuid,
            artifact_sha256 text NOT NULL CHECK (artifact_sha256 ~ '^[0-9a-f]{64}$'),
            account_ref text NOT NULL,
            purpose text NOT NULL,
            expires_at timestamptz NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute("""
        CREATE TABLE v4_review_decisions (
            id uuid PRIMARY KEY,
            review_id uuid NOT NULL UNIQUE REFERENCES v4_case_reviews(id),
            command_id uuid NOT NULL UNIQUE REFERENCES v4_case_commands(id),
            actor_id uuid NOT NULL REFERENCES identity_subjects(id),
            action text NOT NULL CHECK (action IN ('approve_resume','reject')),
            reason text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute("""
        CREATE TABLE v4_case_notifications (
            id uuid PRIMARY KEY,
            case_id uuid NOT NULL REFERENCES v4_recovery_cases(id),
            case_revision integer NOT NULL,
            kind text NOT NULL CHECK (kind IN ('review_due','escalation')),
            due_at timestamptz NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (case_id,case_revision,kind)
        )
    """)
    op.execute("""
        CREATE TABLE v4_case_acks (
            id uuid PRIMARY KEY,
            notification_id uuid NOT NULL UNIQUE REFERENCES v4_case_notifications(id),
            actor_id uuid NOT NULL REFERENCES identity_subjects(id),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute("""
        CREATE TABLE v4_case_archives (
            id uuid PRIMARY KEY,
            case_id uuid NOT NULL UNIQUE REFERENCES v4_recovery_cases(id),
            actor_id uuid NOT NULL REFERENCES identity_subjects(id),
            reason text NOT NULL CHECK (length(reason) BETWEEN 1 AND 2000),
            evidence jsonb NOT NULL,
            retain_until timestamptz NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute("""
        CREATE TABLE v4_case_events (
            id uuid PRIMARY KEY,
            case_id uuid NOT NULL REFERENCES v4_recovery_cases(id),
            case_revision integer NOT NULL,
            action text NOT NULL,
            actor_id uuid NOT NULL REFERENCES identity_subjects(id),
            traceparent text NOT NULL,
            payload jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    for table in ["v4_stop_commands","v4_dispatch_permits","v4_case_commands","v4_case_reviews","v4_review_decisions","v4_case_notifications","v4_case_acks","v4_case_archives","v4_case_events"]:
        op.execute(f"CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION v4_immutable()")
    op.execute("""
        CREATE FUNCTION v4_preserve_governance_projection() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP='DELETE' OR NEW.id IS DISTINCT FROM OLD.id THEN
                RAISE EXCEPTION 'V4 governance identity is immutable';
            END IF;
            IF TG_TABLE_NAME='v4_permit_claims' THEN
                IF (NEW.cycle_id,NEW.operation_id,NEW.context_id,NEW.request_fingerprint,NEW.created_at)
                   IS DISTINCT FROM (OLD.cycle_id,OLD.operation_id,OLD.context_id,OLD.request_fingerprint,OLD.created_at) THEN
                    RAISE EXCEPTION 'V4 permit claim binding is immutable';
                END IF;
                IF NOT ((OLD.state='issued' AND NEW.state='issued' AND NEW.current_permit_id IS DISTINCT FROM OLD.current_permit_id)
                    OR (OLD.state='issued' AND NEW.state='claimed' AND NEW.current_permit_id=OLD.current_permit_id AND NEW.claimed_at IS NOT NULL)
                    OR (OLD.state='claimed' AND NEW.state='unknown' AND NEW.current_permit_id=OLD.current_permit_id AND NEW.claimed_at=OLD.claimed_at)) THEN
                    RAISE EXCEPTION 'V4 permit claim transition is not permitted';
                END IF;
            ELSIF TG_TABLE_NAME='v4_recovery_cases' THEN
                IF (NEW.workspace_id,NEW.goal_id,NEW.target_intent_id,NEW.target_cycle_id,NEW.context_id,NEW.operation_id,NEW.kind,NEW.open_fingerprint,NEW.failure_class,NEW.owner_id,NEW.reason,NEW.deadline,NEW.max_actions,NEW.created_at)
                   IS DISTINCT FROM (OLD.workspace_id,OLD.goal_id,OLD.target_intent_id,OLD.target_cycle_id,OLD.context_id,OLD.operation_id,OLD.kind,OLD.open_fingerprint,OLD.failure_class,OLD.owner_id,OLD.reason,OLD.deadline,OLD.max_actions,OLD.created_at) THEN
                    RAISE EXCEPTION 'V4 case binding is immutable';
                END IF;
                IF NOT ((OLD.state='awaiting_review' AND NEW.state IN ('retry_due','suspended','terminal'))
                    OR (OLD.state IN ('retry_due','reconciling','rework_due','resolved','suspended') AND NEW.state='terminal')
                    OR (OLD.state='retry_due' AND NEW.state='resolved')) THEN
                    RAISE EXCEPTION 'V4 case transition is not permitted';
                END IF;
                IF OLD.failure_class='policy_denial' AND NEW.state='retry_due' THEN
                    RAISE EXCEPTION 'V4 policy denial cannot become retry';
                END IF;
                IF NEW.action_count > NEW.max_actions OR NEW.action_count < OLD.action_count OR NEW.action_count > OLD.action_count+1 THEN
                    RAISE EXCEPTION 'V4 case action limit exceeded';
                END IF;
            END IF;
            IF NEW.revision <> OLD.revision+1 THEN
                RAISE EXCEPTION 'V4 governance revision must advance once';
            END IF;
            RETURN NEW;
        END $$
    """)
    for table in ["v4_permit_claims","v4_recovery_cases"]:
        op.execute(f"CREATE TRIGGER preserved BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION v4_preserve_governance_projection()")
    op.execute("""
        CREATE FUNCTION v4_preserve_stop_scope() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP='DELETE' OR (NEW.workspace_id,NEW.scope_key) IS DISTINCT FROM (OLD.workspace_id,OLD.scope_key)
               OR NEW.revision <> OLD.revision+1 OR NEW.stopped=OLD.stopped THEN
                RAISE EXCEPTION 'V4 stop scope identity and revision are immutable';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("CREATE TRIGGER preserved BEFORE UPDATE OR DELETE ON v4_stop_scopes FOR EACH ROW EXECUTE FUNCTION v4_preserve_stop_scope()")
    op.execute("""
        CREATE FUNCTION v4_check_governance_scope() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_TABLE_NAME='v4_stop_scopes' THEN
                IF NEW.scope_key <> 'workspace' THEN
                    IF NOT EXISTS (
                        SELECT 1 FROM v4_goals WHERE id=substring(NEW.scope_key FROM 6)::uuid
                        AND workspace_id=NEW.workspace_id
                    ) THEN
                        RAISE EXCEPTION 'V4 stop scope must belong to workspace';
                    END IF;
                END IF;
            ELSIF TG_TABLE_NAME='v4_permit_claims' THEN
                IF NEW.state <> 'issued' OR NEW.current_permit_id IS NOT NULL OR NEW.claimed_at IS NOT NULL
                   OR NEW.revision <> 1 OR NOT EXISTS (
                    SELECT 1 FROM v4_cycles AS cycle JOIN v4_run_contexts AS context
                    ON context.id=cycle.context_id AND context.cycle_id=cycle.id
                    WHERE cycle.id=NEW.cycle_id AND cycle.operation_id=NEW.operation_id
                    AND cycle.context_id=NEW.context_id
                ) THEN
                    RAISE EXCEPTION 'V4 permit claim scope or initial state invalid';
                END IF;
            ELSIF TG_TABLE_NAME='v4_dispatch_permits' THEN
                IF NOT EXISTS (
                    SELECT 1 FROM v4_permit_claims AS claim JOIN v4_cycles AS cycle ON cycle.id=claim.cycle_id
                    JOIN v4_cycle_intents AS intent ON intent.id=cycle.intent_id
                    JOIN v4_goals AS goal ON goal.id=intent.goal_id
                    JOIN identity_subjects AS subject ON subject.id=NEW.subject_id
                    JOIN v4_run_contexts AS context ON context.id=claim.context_id
                    WHERE claim.id=NEW.claim_id AND goal.workspace_id=subject.workspace_id
                    AND intent.goal_revision=NEW.goal_revision
                    AND NEW.request->>'context_id'=claim.context_id::text
                    AND NEW.request->>'operation_id'=claim.operation_id::text
                    AND NEW.request->>'effect'='fixture.noop'
                    AND NEW.request->>'purpose'='fixture_execution'
                    AND NEW.request->>'account_ref'='fixture-account'
                    AND ((context.schema_version='RunContext.local.v3')=(NEW.reservation_id IS NOT NULL))
                ) OR (NEW.reservation_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM v4_allocation_reservations AS mapping
                    JOIN v4_permit_claims AS claim ON claim.operation_id=mapping.operation_id
                    WHERE claim.id=NEW.claim_id AND mapping.reservation_id=NEW.reservation_id
                )) THEN
                    RAISE EXCEPTION 'V4 dispatch permit binding invalid';
                END IF;
            ELSIF TG_TABLE_NAME='v4_recovery_cases' THEN
                IF NEW.revision <> 1 OR NEW.action_count <> 0 OR
                   ((NEW.target_intent_id IS NOT NULL) <> (NEW.kind='admission_review')) OR
                   (NEW.kind IN ('admission_review','manual_review') AND NEW.state <> 'awaiting_review') OR
                   (NEW.kind='retry' AND NEW.state <> 'retry_due') OR
                   (NEW.kind='reconciliation' AND NEW.state <> 'reconciling') OR
                   (NEW.kind='rework' AND NEW.state <> 'rework_due') OR
                   (NEW.kind='retry' AND NEW.failure_class NOT IN ('technical_failure','deadline')) OR
                   (NEW.kind='reconciliation' AND NEW.failure_class <> 'unknown_effect') OR
                   (NEW.kind='rework' AND NEW.failure_class <> 'quality_failure') OR
                   (NEW.kind='admission_review' AND NEW.failure_class NOT IN ('admission_review','policy_denial')) OR
                   (NEW.kind='manual_review' AND NEW.failure_class NOT IN ('manual_review','policy_denial')) OR
                   NOT EXISTS (SELECT 1 FROM v4_goals WHERE id=NEW.goal_id AND workspace_id=NEW.workspace_id) THEN
                    RAISE EXCEPTION 'V4 recovery case scope or initial state invalid';
                END IF;
                IF NEW.target_intent_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM v4_cycle_intents WHERE id=NEW.target_intent_id AND goal_id=NEW.goal_id
                ) THEN
                    RAISE EXCEPTION 'V4 recovery case intent binding invalid';
                END IF;
                IF NEW.target_cycle_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM v4_cycles AS cycle JOIN v4_cycle_intents AS intent ON intent.id=cycle.intent_id
                    WHERE cycle.id=NEW.target_cycle_id AND intent.goal_id=NEW.goal_id
                    AND cycle.context_id=NEW.context_id AND cycle.operation_id=NEW.operation_id
                ) THEN
                    RAISE EXCEPTION 'V4 recovery case cycle binding invalid';
                END IF;
            ELSIF TG_TABLE_NAME='v4_case_reviews' THEN
                IF NOT EXISTS (
                    SELECT 1 FROM v4_recovery_cases AS recovery
                    WHERE recovery.id=NEW.case_id AND recovery.state='awaiting_review'
                    AND recovery.revision=NEW.case_revision
                    AND recovery.context_id IS NOT DISTINCT FROM NEW.context_id
                    AND recovery.operation_id IS NOT DISTINCT FROM NEW.operation_id
                    AND recovery.deadline=NEW.expires_at
                ) OR NEW.account_ref <> 'fixture-account' OR NEW.purpose <> 'fixture_execution' THEN
                    RAISE EXCEPTION 'V4 review binding invalid';
                END IF;
            END IF;
            RETURN NEW;
        END $$
    """)
    for table in ["v4_stop_scopes","v4_permit_claims","v4_dispatch_permits","v4_recovery_cases","v4_case_reviews"]:
        op.execute(f"CREATE TRIGGER scoped BEFORE INSERT ON {table} FOR EACH ROW EXECUTE FUNCTION v4_check_governance_scope()")
    op.execute("""
        CREATE FUNCTION v4_assert_governance_audit() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_TABLE_NAME='v4_stop_scopes' THEN
                IF NOT EXISTS (
                    SELECT 1 FROM v4_stop_commands WHERE workspace_id=NEW.workspace_id
                    AND scope_key=NEW.scope_key AND resulting_revision=NEW.revision
                    AND stopped=NEW.stopped AND actor_id=NEW.changed_by
                ) THEN
                    RAISE EXCEPTION 'V4 stop transition lacks immutable command';
                END IF;
            ELSIF TG_TABLE_NAME='v4_permit_claims' THEN
                IF NEW.state='issued' THEN
                    IF NOT EXISTS (SELECT 1 FROM v4_dispatch_permits WHERE id=NEW.current_permit_id AND claim_id=NEW.id) THEN
                        RAISE EXCEPTION 'V4 permit renewal lacks immutable permit';
                    END IF;
                ELSIF NOT EXISTS (
                    SELECT 1 FROM v4_cycle_events WHERE cycle_id=NEW.cycle_id
                    AND kind=CASE NEW.state WHEN 'claimed' THEN 'permit_claimed' ELSE 'permit_unknown' END
                    AND payload->>'permit_id'=NEW.current_permit_id::text
                ) THEN
                    RAISE EXCEPTION 'V4 permit claim transition lacks immutable event';
                END IF;
            ELSIF TG_TABLE_NAME='v4_recovery_cases' THEN
                IF NOT EXISTS (SELECT 1 FROM v4_case_events WHERE case_id=NEW.id AND case_revision=NEW.revision) THEN
                    RAISE EXCEPTION 'V4 case transition lacks immutable event';
                END IF;
                IF OLD.state='awaiting_review' AND NEW.state='retry_due' AND NOT EXISTS (
                    SELECT 1 FROM v4_review_decisions AS decision
                    JOIN v4_case_reviews AS review ON review.id=decision.review_id
                    WHERE review.case_id=NEW.id AND decision.action='approve_resume'
                ) THEN
                    RAISE EXCEPTION 'V4 review approval lacks immutable decision';
                END IF;
            END IF;
            RETURN NULL;
        END $$
    """)
    for table in ["v4_stop_scopes","v4_permit_claims","v4_recovery_cases"]:
        op.execute(f"CREATE CONSTRAINT TRIGGER audited AFTER UPDATE ON {table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION v4_assert_governance_audit()")


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM v4_stop_scopes) OR EXISTS (SELECT 1 FROM v4_permit_claims)
               OR EXISTS (SELECT 1 FROM v4_recovery_cases) THEN
                RAISE EXCEPTION 'preserve V4 stop, permit and case history';
            END IF;
        END $$
    """)
    op.execute("ALTER TABLE v4_permit_claims DROP CONSTRAINT v4_current_permit_fk")
    for table in ["v4_case_events","v4_case_archives","v4_case_acks","v4_case_notifications","v4_review_decisions","v4_case_reviews","v4_case_commands","v4_recovery_cases","v4_dispatch_permits","v4_permit_claims","v4_stop_commands","v4_stop_scopes"]:
        op.execute(f"DROP TABLE {table}")
    op.execute("DROP FUNCTION v4_preserve_stop_scope()")
    op.execute("DROP FUNCTION v4_preserve_governance_projection()")
    op.execute("DROP FUNCTION v4_check_governance_scope()")
    op.execute("DROP FUNCTION v4_assert_governance_audit()")
