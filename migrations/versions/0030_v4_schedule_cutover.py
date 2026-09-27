"""Fence one fixture legacy schedule before enabling a canonical goal cursor."""

from alembic import op


revision = "0030_v4_schedule_cutover"
down_revision = "0029_goal_create_receipts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE UNIQUE INDEX job_schedules_workspace_identity ON job_schedules (id,workspace_id)")
    op.execute("""
        CREATE TABLE v4_schedule_cutovers (
            goal_id uuid PRIMARY KEY,
            workspace_id uuid NOT NULL,
            goal_revision integer NOT NULL CHECK (goal_revision > 0),
            legacy_schedule_id uuid NOT NULL UNIQUE,
            actor_id uuid NOT NULL,
            prepare_key text NOT NULL CHECK (length(prepare_key) BETWEEN 1 AND 256),
            fingerprint text NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
            first_v4_slot timestamptz NOT NULL,
            state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','active','rollback_pending','rolled_back')),
            activation_key text CHECK (activation_key IS NULL OR length(activation_key) BETWEEN 1 AND 256),
            rollback_key text CHECK (rollback_key IS NULL OR length(rollback_key) BETWEEN 1 AND 256),
            rollback_after_slot timestamptz,
            traceparent text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            activated_at timestamptz,
            rollback_started_at timestamptz,
            rolled_back_at timestamptz,
            FOREIGN KEY (goal_id,workspace_id) REFERENCES v4_goals(id,workspace_id),
            FOREIGN KEY (goal_id,goal_revision) REFERENCES v4_goal_revisions(goal_id,revision),
            FOREIGN KEY (legacy_schedule_id,workspace_id) REFERENCES job_schedules(id,workspace_id),
            FOREIGN KEY (actor_id,workspace_id) REFERENCES identity_subjects(id,workspace_id),
            CHECK ((state='pending' AND activation_key IS NULL AND rollback_key IS NULL)
                OR (state='active' AND activation_key IS NOT NULL AND rollback_key IS NULL)
                OR (state='rollback_pending' AND rollback_key IS NOT NULL)
                OR (state='rolled_back' AND rollback_key IS NOT NULL AND rolled_back_at IS NOT NULL))
        )
    """)
    op.execute("""
        CREATE FUNCTION v4_preserve_schedule_cutover() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP='DELETE' THEN
                RAISE EXCEPTION 'preserve V4 schedule cutover identity';
            END IF;
            IF (NEW.goal_id,NEW.workspace_id,NEW.goal_revision,NEW.legacy_schedule_id,
                NEW.actor_id,NEW.prepare_key,NEW.fingerprint,NEW.first_v4_slot,
                NEW.traceparent,NEW.created_at) IS DISTINCT FROM
               (OLD.goal_id,OLD.workspace_id,OLD.goal_revision,OLD.legacy_schedule_id,
                OLD.actor_id,OLD.prepare_key,OLD.fingerprint,OLD.first_v4_slot,
                OLD.traceparent,OLD.created_at) THEN
                RAISE EXCEPTION 'V4 schedule cutover binding is immutable';
            END IF;
            IF NOT ((OLD.state='pending' AND NEW.state IN ('active','rollback_pending'))
                OR (OLD.state='active' AND NEW.state='rollback_pending')
                OR (OLD.state='rollback_pending' AND NEW.state='rolled_back')) THEN
                RAISE EXCEPTION 'V4 schedule cutover transition is not permitted';
            END IF;
            IF NEW.state='active' THEN
                IF NEW.activation_key IS NULL OR NEW.activated_at IS NULL
                   OR NEW.rollback_key IS NOT NULL OR NEW.rollback_after_slot IS NOT NULL
                   OR NOT EXISTS (SELECT 1 FROM job_schedules AS schedule
                       WHERE schedule.id=NEW.legacy_schedule_id AND schedule.workspace_id=NEW.workspace_id
                         AND schedule.status='paused')
                   OR NOT EXISTS (SELECT 1 FROM v4_schedule_cursors AS cursor
                       WHERE cursor.goal_id=NEW.goal_id AND cursor.goal_revision=NEW.goal_revision
                         AND cursor.last_slot<NEW.first_v4_slot) THEN
                    RAISE EXCEPTION 'V4 schedule activation requires paused legacy schedule and cursor';
                END IF;
            ELSIF NEW.state='rollback_pending' THEN
                IF NEW.rollback_key IS NULL OR NEW.rollback_after_slot IS NULL OR NEW.rollback_started_at IS NULL
                   OR NEW.activation_key IS DISTINCT FROM OLD.activation_key THEN
                    RAISE EXCEPTION 'V4 schedule rollback must fence active polling';
                END IF;
            ELSIF NEW.state='rolled_back' THEN
                IF NEW.activation_key IS DISTINCT FROM OLD.activation_key
                   OR NEW.rollback_key IS DISTINCT FROM OLD.rollback_key
                   OR NEW.rollback_after_slot IS DISTINCT FROM OLD.rollback_after_slot
                   OR NEW.rolled_back_at IS NULL
                   OR NOT EXISTS (SELECT 1 FROM job_schedules AS schedule
                       WHERE schedule.id=NEW.legacy_schedule_id AND schedule.workspace_id=NEW.workspace_id
                         AND schedule.status='active' AND schedule.next_run_at>NEW.rollback_after_slot
                         AND (schedule.payload->>'v4_resume_after')::timestamptz=NEW.rollback_after_slot) THEN
                    RAISE EXCEPTION 'V4 schedule rollback requires verified later legacy slot';
                END IF;
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("CREATE TRIGGER preserve_schedule_cutover BEFORE UPDATE OR DELETE ON v4_schedule_cutovers FOR EACH ROW EXECUTE FUNCTION v4_preserve_schedule_cutover()")


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM v4_schedule_cutovers) THEN
                RAISE EXCEPTION 'preserve V4 schedule cutover history';
            END IF;
        END $$
    """)
    op.execute("DROP TABLE v4_schedule_cutovers")
    op.execute("DROP FUNCTION v4_preserve_schedule_cutover()")
    op.execute("DROP INDEX job_schedules_workspace_identity")
