"""Bind legacy no-effects jobs to original canonical V4 dispatch identity."""

from alembic import op

revision = "0036_legacy_cycle_dispatch"
down_revision = "0035_parallel_agent_teams"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE v4_legacy_dispatches (
        cycle_id uuid PRIMARY KEY REFERENCES v4_cycles(id),
        context_id uuid NOT NULL UNIQUE REFERENCES v4_run_contexts(id),
        operation_id uuid NOT NULL UNIQUE,
        intent_id uuid NOT NULL REFERENCES v4_cycle_intents(id),
        goal_id uuid NOT NULL REFERENCES v4_goals(id),
        workspace_id uuid NOT NULL REFERENCES workspaces(id),
        actor_id uuid NOT NULL REFERENCES identity_subjects(id),
        content_program_id uuid NOT NULL REFERENCES content_programs(id),
        job_id uuid NOT NULL UNIQUE REFERENCES jobs(id),
        task_queue text NOT NULL CHECK (task_queue LIKE 'salience-v4-local-legacy-%' AND length(task_queue)<=128),
        payload jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
    );""")
    op.execute("""CREATE TABLE v4_legacy_commands (
        workspace_id uuid NOT NULL REFERENCES workspaces(id),
        actor_id uuid NOT NULL REFERENCES identity_subjects(id),
        idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 256),
        fingerprint text NOT NULL CHECK(fingerprint ~ '^[0-9a-f]{64}$'),
        response jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY(workspace_id,actor_id,idempotency_key)
    );""")
    op.execute("""CREATE TABLE v4_legacy_schedule_plans (
        goal_id uuid PRIMARY KEY REFERENCES v4_schedule_cutovers(goal_id),
        workspace_id uuid NOT NULL REFERENCES workspaces(id),
        actor_id uuid NOT NULL REFERENCES identity_subjects(id),
        goal_revision integer NOT NULL,
        task_queue text NOT NULL CHECK(task_queue LIKE 'salience-v4-local-legacy-%' AND length(task_queue)<=128),
        niche text NOT NULL CHECK(length(btrim(niche)) BETWEEN 1 AND 256),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
    )""")
    op.execute("""CREATE FUNCTION v4_guard_legacy_dispatch() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'legacy dispatch history is immutable'; END IF;
        IF TG_TABLE_NAME='v4_legacy_schedule_plans' THEN
            IF NOT EXISTS(SELECT 1 FROM v4_schedule_cutovers cutover WHERE cutover.goal_id=NEW.goal_id
                AND cutover.workspace_id=NEW.workspace_id AND cutover.actor_id=NEW.actor_id
                AND cutover.goal_revision=NEW.goal_revision AND cutover.state='pending') THEN
                RAISE EXCEPTION 'legacy schedule plan requires original pending cutover';
            END IF;
        ELSIF TG_TABLE_NAME='v4_legacy_commands' THEN
            IF NOT EXISTS(SELECT 1 FROM identity_subjects WHERE id=NEW.actor_id AND workspace_id=NEW.workspace_id) THEN
                RAISE EXCEPTION 'legacy command actor scope mismatch';
            END IF;
        ELSIF NOT EXISTS (
            SELECT 1 FROM v4_cycles c JOIN v4_cycle_intents i ON i.id=c.intent_id
            JOIN v4_goals g ON g.id=i.goal_id JOIN v4_run_contexts ctx ON ctx.id=c.context_id
            JOIN identity_subjects actor ON actor.id=NEW.actor_id AND actor.workspace_id=g.workspace_id
            JOIN content_programs p ON p.id=NEW.content_program_id AND p.workspace_id=g.workspace_id
            JOIN jobs job ON job.id=NEW.job_id
            JOIN v4_cycle_outbox msg ON msg.cycle_id=c.id AND msg.kind='start' AND msg.sequence=1
            WHERE c.id=NEW.cycle_id AND c.context_id=NEW.context_id AND c.operation_id=NEW.operation_id
            AND i.id=NEW.intent_id AND g.id=NEW.goal_id AND g.workspace_id=NEW.workspace_id
            AND ctx.payload->>'content_program_id'=NEW.content_program_id::text
            AND ctx.payload->>'subject_id'=NEW.actor_id::text AND msg.subject_id=NEW.actor_id
            AND msg.state='pending' AND msg.attempts=0
            AND job.workspace_id=NEW.workspace_id AND job.content_program_id=NEW.content_program_id
            AND job.job_type='intelligence_research' AND job.dry_run AND job.state='queued'
            AND job.actor_id=NEW.actor_id::text AND job.actor_kind='identity'
            AND job.input_payload=NEW.payload AND job.task_queue=NEW.task_queue
            AND job.trace_id=split_part(msg.traceparent,'-',2) AND job.span_id=split_part(msg.traceparent,'-',3)
            AND job.workflow_run_id='salience-v4-legacy-operation:'||NEW.operation_id::text
        ) THEN RAISE EXCEPTION 'legacy dispatch requires original canonical identity'; END IF;
        RETURN NEW;
    END $$;""")
    op.execute("""CREATE TRIGGER immutable_legacy_dispatch BEFORE INSERT OR UPDATE OR DELETE ON v4_legacy_dispatches
        FOR EACH ROW EXECUTE FUNCTION v4_guard_legacy_dispatch();""")
    op.execute("""CREATE TRIGGER immutable_legacy_command BEFORE INSERT OR UPDATE OR DELETE ON v4_legacy_commands
        FOR EACH ROW EXECUTE FUNCTION v4_guard_legacy_dispatch();""")
    op.execute("""CREATE TRIGGER immutable_legacy_schedule BEFORE INSERT OR UPDATE OR DELETE ON v4_legacy_schedule_plans
        FOR EACH ROW EXECUTE FUNCTION v4_guard_legacy_dispatch()""")
    op.execute("""CREATE FUNCTION v4_guard_legacy_job() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF EXISTS(SELECT 1 FROM v4_legacy_dispatches WHERE job_id=OLD.id) THEN
            IF TG_OP='DELETE' OR ROW(NEW.id,NEW.workspace_id,NEW.content_program_id,NEW.job_type,
                NEW.workflow_run_id,NEW.task_queue,NEW.idempotency_key,NEW.input_payload,NEW.dry_run,
                NEW.actor_id,NEW.actor_kind,NEW.trace_id,NEW.span_id)
                IS DISTINCT FROM ROW(OLD.id,OLD.workspace_id,OLD.content_program_id,OLD.job_type,
                OLD.workflow_run_id,OLD.task_queue,OLD.idempotency_key,OLD.input_payload,OLD.dry_run,
                OLD.actor_id,OLD.actor_kind,OLD.trace_id,OLD.span_id) THEN
                RAISE EXCEPTION 'legacy canonical job identity is immutable';
            END IF;
        END IF;
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
    END $$;""")
    op.execute("""CREATE TRIGGER legacy_job_identity BEFORE UPDATE OR DELETE ON jobs
        FOR EACH ROW EXECUTE FUNCTION v4_guard_legacy_job();""")


def downgrade():
    op.execute("SET LOCAL lock_timeout='3s'")
    op.execute("LOCK TABLE v4_legacy_dispatches,v4_legacy_commands,v4_legacy_schedule_plans,jobs IN SHARE ROW EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM v4_legacy_dispatches) OR EXISTS(SELECT 1 FROM v4_legacy_commands) OR EXISTS(SELECT 1 FROM v4_legacy_schedule_plans) THEN
            RAISE EXCEPTION 'preserve legacy dispatch identity and command history';
        END IF;
    END $$;""")
    op.execute("""DROP TRIGGER legacy_job_identity ON jobs;""")
    op.execute("""DROP FUNCTION v4_guard_legacy_job();""")
    op.execute("""DROP TABLE v4_legacy_commands,v4_legacy_dispatches,v4_legacy_schedule_plans;""")
    op.execute("""DROP FUNCTION v4_guard_legacy_dispatch();""")
