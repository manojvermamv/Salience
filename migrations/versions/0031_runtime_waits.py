"""Preserved local wait handoffs, owner holds and fixture notification receipts."""
from alembic import op

revision = "0031_runtime_waits"
down_revision = "0030_v4_schedule_cutover"
branch_labels = None
depends_on = None

# Extend the existing guarded transition without weakening any binding checks.
OLD_TRANSITION = "OR (OLD.state='retry_due' AND NEW.state='resolved')"
NEW_TRANSITION = OLD_TRANSITION + " OR (OLD.state IN ('retry_due','reconciling','rework_due') AND NEW.state='suspended' AND clock_timestamp()>=OLD.deadline AND NEW.action_count=OLD.action_count)"


def replace_transition(old, new):
    # Fail closed if the installed guard differs from the reviewed predecessor.
    op.execute("""
        DO $migration$ DECLARE definition text; BEGIN
            SELECT pg_get_functiondef('v4_preserve_governance_projection()'::regprocedure) INTO definition;
            IF position('%s' IN definition)=0 THEN RAISE EXCEPTION 'unexpected governance guard'; END IF;
            EXECUTE replace(definition,'%s','%s');
        END $migration$
    """ % tuple(value.replace("'", "''") for value in (old, old, new)))


def upgrade():
    replace_transition(OLD_TRANSITION, NEW_TRANSITION)
    op.execute("""
        CREATE TABLE v4_runtime_waits (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(), workspace_id uuid NOT NULL REFERENCES workspaces(id),
            subject_id uuid NOT NULL REFERENCES identity_subjects(id), owner_id uuid NOT NULL REFERENCES identity_subjects(id),
            case_id uuid REFERENCES v4_recovery_cases(id), intent_id uuid REFERENCES v4_cycle_intents(id),
            revision integer NOT NULL CHECK (revision>0), kind text NOT NULL CHECK (kind IN ('case','deferred','review_required')),
            due_at timestamptz NOT NULL, state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','completed','held')),
            runtime_id text, result jsonb,
            handoff_attempts integer NOT NULL DEFAULT 0 CHECK(handoff_attempts BETWEEN 0 AND 3),
            available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            inspected_at timestamptz NOT NULL DEFAULT clock_timestamp(), created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            CHECK ((case_id IS NOT NULL) = (kind='case')), CHECK ((intent_id IS NOT NULL) = (kind<>'case')),
            CHECK ((state='pending') = (result IS NULL)), UNIQUE(case_id,revision), UNIQUE(intent_id,revision)
        )
    """)
    op.execute("""
        CREATE TABLE v4_runtime_holds (
            cycle_id uuid PRIMARY KEY REFERENCES v4_cycles(id), context_id uuid NOT NULL REFERENCES v4_run_contexts(id),
            operation_id uuid NOT NULL, workspace_id uuid NOT NULL REFERENCES workspaces(id),
            owner_id uuid NOT NULL REFERENCES identity_subjects(id),
            reason text NOT NULL CHECK (reason IN ('held_timeout','held_message_limit')),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute("""
        CREATE TABLE v4_notification_deliveries (
            notification_id uuid PRIMARY KEY REFERENCES v4_case_notifications(id),
            state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','accepted','suppressed','held')),
            attempts integer NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 3),
            available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            reason text CHECK (reason IN ('fixture_accepted','obsolete','acknowledged','fixture_unavailable','owner_unavailable')),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute("""
        CREATE TABLE v4_notification_acceptances (
            notification_id uuid PRIMARY KEY REFERENCES v4_case_notifications(id),
            case_id uuid NOT NULL REFERENCES v4_recovery_cases(id), owner_id uuid NOT NULL REFERENCES identity_subjects(id),
            payload jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute("""
        CREATE FUNCTION v4_enqueue_runtime_wait() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE target v4_cycle_intents; recovery v4_recovery_cases; BEGIN
            IF TG_TABLE_NAME='v4_case_events' THEN
                IF NEW.action NOT IN ('opened','review') THEN RETURN NEW; END IF;
                SELECT * INTO STRICT recovery FROM v4_recovery_cases WHERE id=NEW.case_id;
                IF recovery.state NOT IN ('awaiting_review','retry_due','reconciling','rework_due') THEN RETURN NEW; END IF;
                INSERT INTO v4_runtime_waits(workspace_id,subject_id,owner_id,case_id,revision,kind,due_at)
                VALUES(recovery.workspace_id,NEW.actor_id,recovery.owner_id,recovery.id,recovery.revision,'case',recovery.deadline);
            ELSIF TG_TABLE_NAME='v4_cycle_events' THEN
                IF NEW.kind<>'admission' OR NEW.payload->>'disposition' NOT IN ('deferred','review_required') THEN RETURN NEW; END IF;
                SELECT * INTO STRICT target FROM v4_cycle_intents WHERE id=NEW.intent_id;
                INSERT INTO v4_runtime_waits(workspace_id,subject_id,owner_id,intent_id,revision,kind,due_at)
                VALUES(NEW.workspace_id,NEW.subject_id,NEW.subject_id,target.id,
                    (NEW.payload->>'eligibility_revision')::integer,NEW.payload->>'disposition',
                    CASE WHEN NEW.payload->>'disposition'='review_required' THEN LEAST(target.expires_at,clock_timestamp()+interval '7 days')
                    ELSE LEAST(target.expires_at,clock_timestamp()+interval '7 days',GREATEST(target.due_at,clock_timestamp()+interval '1 second')) END);
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("CREATE TRIGGER runtime_wait AFTER INSERT ON v4_case_events FOR EACH ROW EXECUTE FUNCTION v4_enqueue_runtime_wait()")
    op.execute("CREATE TRIGGER runtime_wait AFTER INSERT ON v4_cycle_events FOR EACH ROW EXECUTE FUNCTION v4_enqueue_runtime_wait()")
    op.execute("""
        CREATE FUNCTION v4_enqueue_notification_delivery() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN INSERT INTO v4_notification_deliveries(notification_id) VALUES(NEW.id); RETURN NEW; END $$
    """)
    op.execute("CREATE TRIGGER delivery AFTER INSERT ON v4_case_notifications FOR EACH ROW EXECUTE FUNCTION v4_enqueue_notification_delivery()")
    op.execute("""
        CREATE FUNCTION v4_check_runtime_binding() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_TABLE_NAME='v4_runtime_holds' THEN
                IF NOT EXISTS (
                    SELECT 1 FROM v4_cycles c JOIN v4_cycle_intents i ON i.id=c.intent_id
                    JOIN v4_goals g ON g.id=i.goal_id JOIN identity_subjects s ON s.id=NEW.owner_id
                    WHERE c.id=NEW.cycle_id AND c.context_id=NEW.context_id AND c.operation_id=NEW.operation_id
                      AND g.workspace_id=NEW.workspace_id AND s.workspace_id=NEW.workspace_id
                ) THEN RAISE EXCEPTION 'runtime hold binding invalid'; END IF;
            ELSIF TG_TABLE_NAME='v4_runtime_waits' THEN
                IF NOT EXISTS (SELECT 1 FROM identity_subjects WHERE id=NEW.subject_id AND workspace_id=NEW.workspace_id)
                OR NOT EXISTS (SELECT 1 FROM identity_subjects WHERE id=NEW.owner_id AND workspace_id=NEW.workspace_id)
                OR (NEW.kind='case' AND NOT EXISTS (SELECT 1 FROM v4_recovery_cases
                    WHERE id=NEW.case_id AND workspace_id=NEW.workspace_id AND revision=NEW.revision
                    AND owner_id=NEW.owner_id AND deadline=NEW.due_at))
                OR (NEW.kind<>'case' AND NOT EXISTS (SELECT 1 FROM v4_cycle_intents i JOIN v4_goals g ON g.id=i.goal_id
                    JOIN v4_admissions a ON a.intent_id=i.id AND a.eligibility_revision=NEW.revision
                    WHERE i.id=NEW.intent_id AND g.workspace_id=NEW.workspace_id
                    AND a.disposition=NEW.kind AND NEW.due_at<=i.expires_at)) THEN
                    RAISE EXCEPTION 'runtime wait binding invalid';
                END IF;
            ELSE
                IF NOT EXISTS (SELECT 1 FROM v4_case_notifications n JOIN v4_recovery_cases c ON c.id=n.case_id
                    WHERE n.id=NEW.notification_id AND c.id=NEW.case_id AND c.owner_id=NEW.owner_id
                    AND c.revision=n.case_revision AND c.state IN ('awaiting_review','suspended')) THEN
                    RAISE EXCEPTION 'notification acceptance binding invalid';
                END IF;
            END IF;
            RETURN NEW;
        END $$
    """)
    for table in ("v4_runtime_holds", "v4_runtime_waits", "v4_notification_acceptances"):
        op.execute(f"CREATE TRIGGER scoped BEFORE INSERT ON {table} FOR EACH ROW EXECUTE FUNCTION v4_check_runtime_binding()")
    for table in ("v4_runtime_holds", "v4_notification_acceptances"):
        op.execute(f"CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION v4_immutable()")
    op.execute("""
        CREATE FUNCTION v4_preserve_runtime_projection() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP='DELETE' THEN RAISE EXCEPTION 'preserve runtime history'; END IF;
            IF TG_TABLE_NAME='v4_runtime_waits' THEN
                IF (NEW.id,NEW.workspace_id,NEW.subject_id,NEW.owner_id,NEW.case_id,NEW.intent_id,NEW.revision,NEW.kind,NEW.due_at,NEW.created_at)
                   IS DISTINCT FROM (OLD.id,OLD.workspace_id,OLD.subject_id,OLD.owner_id,OLD.case_id,OLD.intent_id,OLD.revision,OLD.kind,OLD.due_at,OLD.created_at)
                   OR OLD.state<>'pending' OR (OLD.runtime_id IS NOT NULL AND NEW.runtime_id IS DISTINCT FROM OLD.runtime_id)
                   OR (NEW.state='pending' AND NEW.result IS NOT NULL)
                   OR (NEW.runtime_id IS NOT NULL AND NEW.runtime_id<>'salience-v4-wait:'||NEW.id::text)
                   OR NEW.handoff_attempts<OLD.handoff_attempts OR NEW.handoff_attempts>OLD.handoff_attempts+1
                   OR (NEW.state='pending' AND NEW.handoff_attempts>=3) THEN
                    RAISE EXCEPTION 'runtime wait identity and receipt are preserved';
                END IF;
                IF NEW.state='held' AND (NEW.result->>'id' IS DISTINCT FROM NEW.id::text
                    OR NEW.result->>'owner_id' IS DISTINCT FROM NEW.owner_id::text) THEN
                    RAISE EXCEPTION 'runtime owner hold binding invalid';
                END IF;
                IF NEW.state='completed' THEN
                    IF NEW.kind='case' THEN
                        IF NOT EXISTS (SELECT 1 FROM v4_recovery_cases c LEFT JOIN v4_cycles cycle ON cycle.id=c.target_cycle_id
                            WHERE c.id=NEW.case_id AND (
                                (NEW.result->>'state'='suspended' AND NEW.result->>'case_id'=c.id::text AND c.state='suspended'
                                 AND EXISTS(SELECT 1 FROM v4_case_events e WHERE e.case_id=c.id AND e.case_revision=c.revision AND e.action='escalated'))
                                OR (NEW.result->>'state'='obsolete' AND (c.revision<>NEW.revision OR c.state IN ('terminal','resolved','suspended') OR cycle.state='closed'))
                            )) THEN RAISE EXCEPTION 'runtime case receipt lacks canonical disposition'; END IF;
                    ELSE
                        IF NEW.result->>'state'='obsolete' THEN
                            IF NOT EXISTS(SELECT 1 FROM v4_cycle_intents i WHERE i.id=NEW.intent_id AND (
                                i.eligibility_revision<>NEW.revision OR EXISTS(SELECT 1 FROM v4_cycles WHERE intent_id=i.id))) THEN
                                RAISE EXCEPTION 'runtime obsolete receipt lacks canonical target change';
                            END IF;
                        ELSIF NOT EXISTS(SELECT 1 FROM v4_admissions a WHERE a.intent_id=NEW.intent_id
                            AND a.eligibility_revision=NEW.revision+1 AND a.disposition=NEW.result->>'state'
                            AND NEW.result->>'intent_id'=NEW.intent_id::text
                            AND a.cycle_id::text IS NOT DISTINCT FROM NEW.result->>'cycle_id') THEN
                            RAISE EXCEPTION 'runtime admission receipt lacks canonical disposition';
                        END IF;
                    END IF;
                END IF;
            ELSE
                IF (NEW.notification_id,NEW.created_at) IS DISTINCT FROM (OLD.notification_id,OLD.created_at)
                   OR OLD.state<>'pending' OR NEW.attempts<>OLD.attempts+1
                   OR (NEW.state='pending' AND NEW.attempts>=3)
                   OR (NEW.state='accepted' AND NOT EXISTS (SELECT 1 FROM v4_notification_acceptances WHERE notification_id=NEW.notification_id)) THEN
                    RAISE EXCEPTION 'notification delivery transition invalid';
                END IF;
            END IF;
            RETURN NEW;
        END $$
    """)
    for table in ("v4_runtime_waits", "v4_notification_deliveries"):
        op.execute(f"CREATE TRIGGER preserved BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION v4_preserve_runtime_projection()")
    # Existing records are intentionally not backfilled with guessed authority.


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM v4_runtime_waits) OR EXISTS(SELECT 1 FROM v4_runtime_holds)
          OR EXISTS(SELECT 1 FROM v4_notification_deliveries) THEN
            RAISE EXCEPTION 'preserve durable waits, holds and notification receipts; disable fixture workers';
        END IF;
    END $$""")
    op.execute("DROP TRIGGER runtime_wait ON v4_case_events")
    op.execute("DROP TRIGGER runtime_wait ON v4_cycle_events")
    op.execute("DROP TRIGGER delivery ON v4_case_notifications")
    for table in ("v4_notification_acceptances", "v4_notification_deliveries", "v4_runtime_holds", "v4_runtime_waits"):
        op.execute(f"DROP TABLE {table}")
    for function in ("v4_enqueue_runtime_wait", "v4_enqueue_notification_delivery", "v4_check_runtime_binding", "v4_preserve_runtime_projection"):
        op.execute(f"DROP FUNCTION {function}()")
    replace_transition(NEW_TRANSITION, OLD_TRANSITION)
