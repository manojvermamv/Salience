"""Preserve original native schedules and separately retain cutover progress."""

from alembic import op
from sqlalchemy import text

revision = "0037_native_schedule_sources"
down_revision = "0036_legacy_cycle_dispatch"
branch_labels = None
depends_on = None

LEGACY_ROLLBACK = "AND (schedule.payload->>'v4_resume_after')::timestamptz=NEW.rollback_after_slot"
NATIVE_ROLLBACK = """AND ((schedule.payload->>'v4_resume_after')::timestamptz=NEW.rollback_after_slot
 OR EXISTS (SELECT 1 FROM v4_native_schedule_sources source JOIN v4_native_schedule_progress progress USING(schedule_id)
 WHERE source.schedule_id=schedule.id AND source.goal_id=NEW.goal_id AND source.workspace_id=NEW.workspace_id
 AND source.first_v4_slot=NEW.first_v4_slot AND progress.resume_after=NEW.rollback_after_slot
 AND progress.next_slot=schedule.next_run_at))"""


def _guard(before, after):
    definition = op.get_bind().execute(text("SELECT pg_get_functiondef('v4_preserve_schedule_cutover()'::regprocedure)")).scalar_one()
    if definition.count(before) != 1:
        raise RuntimeError("unknown cutover guard; refuse native schedule conversion")
    op.execute(definition.replace(before, after, 1))


def upgrade():
    op.execute("""CREATE TABLE v4_native_schedule_sources (
        schedule_id uuid PRIMARY KEY,
        workspace_id uuid NOT NULL,
        goal_id uuid NOT NULL UNIQUE,
        actor_id uuid NOT NULL,
        goal_revision integer NOT NULL CHECK(goal_revision>0),
        remote_id text NOT NULL UNIQUE CHECK(remote_id=schedule_id::text),
        task_queue text NOT NULL CHECK(task_queue LIKE 'salience-v4-local-legacy-%' AND length(task_queue)<=128),
        interval_seconds integer NOT NULL CHECK(interval_seconds BETWEEN 1 AND 31536000),
        first_v4_slot timestamptz NOT NULL,
        observed_last_slot timestamptz,
        remote_snapshot jsonb NOT NULL,
        original_identity jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        FOREIGN KEY(schedule_id,workspace_id) REFERENCES job_schedules(id,workspace_id),
        FOREIGN KEY(goal_id,workspace_id) REFERENCES v4_goals(id,workspace_id),
        FOREIGN KEY(goal_id,goal_revision) REFERENCES v4_goal_revisions(goal_id,revision),
        FOREIGN KEY(actor_id,workspace_id) REFERENCES identity_subjects(id,workspace_id),
        CHECK(observed_last_slot IS NULL OR observed_last_slot<first_v4_slot)
    )""")
    op.execute("""CREATE TABLE v4_native_schedule_progress (
        schedule_id uuid PRIMARY KEY REFERENCES v4_native_schedule_sources(schedule_id),
        last_slot timestamptz,
        resume_after timestamptz,
        next_slot timestamptz NOT NULL,
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
    )""")
    op.execute("""CREATE FUNCTION v4_guard_native_schedule_source() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'native schedule source history is immutable'; END IF;
      IF NOT EXISTS(SELECT 1 FROM job_schedules schedule JOIN v4_goals goal ON goal.id=NEW.goal_id
          JOIN v4_goal_revisions revision ON revision.goal_id=goal.id AND revision.revision=NEW.goal_revision
          WHERE schedule.id=NEW.schedule_id AND schedule.workspace_id=NEW.workspace_id
          AND schedule.job_type='intelligence_research' AND schedule.timezone='UTC'
          AND schedule.payload->'dry_run'='true'::jsonb
          AND schedule.schedule_expression='every '||NEW.interval_seconds::text||'s'
          AND (to_jsonb(schedule)-ARRAY['status','next_run_at','updated_at'])=NEW.original_identity
          AND goal.workspace_id=NEW.workspace_id
          AND goal.revision=NEW.goal_revision AND goal.state='active'
          AND revision.payload->>'content_program_id'=schedule.content_program_id::text
          AND (revision.payload->'cadence'->>'interval_seconds')::integer=NEW.interval_seconds)
      THEN RAISE EXCEPTION 'native schedule requires original current canonical scope'; END IF;
      RETURN NEW;
    END $$""")
    op.execute("CREATE TRIGGER native_source_identity BEFORE INSERT OR UPDATE OR DELETE ON v4_native_schedule_sources FOR EACH ROW EXECUTE FUNCTION v4_guard_native_schedule_source()")
    op.execute("""CREATE FUNCTION v4_guard_native_cutover_binding() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF EXISTS(SELECT 1 FROM v4_native_schedule_sources WHERE schedule_id=NEW.legacy_schedule_id)
        AND NOT EXISTS(SELECT 1 FROM v4_native_schedule_sources source
          WHERE source.schedule_id=NEW.legacy_schedule_id AND source.goal_id=NEW.goal_id
          AND source.workspace_id=NEW.workspace_id AND source.actor_id=NEW.actor_id
          AND source.goal_revision=NEW.goal_revision AND source.first_v4_slot=NEW.first_v4_slot)
      THEN RAISE EXCEPTION 'original native cutover binding required'; END IF;
      RETURN NEW;
    END $$""")
    op.execute("CREATE TRIGGER native_cutover_binding BEFORE INSERT ON v4_schedule_cutovers FOR EACH ROW EXECUTE FUNCTION v4_guard_native_cutover_binding()")
    op.execute("""CREATE FUNCTION v4_guard_native_plan_binding() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF EXISTS(SELECT 1 FROM v4_native_schedule_sources WHERE goal_id=NEW.goal_id)
        AND NOT EXISTS(SELECT 1 FROM v4_native_schedule_sources source JOIN job_schedules schedule ON schedule.id=source.schedule_id
          WHERE source.goal_id=NEW.goal_id AND source.workspace_id=NEW.workspace_id
          AND source.actor_id=NEW.actor_id AND source.goal_revision=NEW.goal_revision
          AND source.task_queue=NEW.task_queue AND schedule.payload->>'niche'=NEW.niche)
      THEN RAISE EXCEPTION 'original native workload binding required'; END IF;
      RETURN NEW;
    END $$""")
    op.execute("CREATE TRIGGER native_plan_binding BEFORE INSERT ON v4_legacy_schedule_plans FOR EACH ROW EXECUTE FUNCTION v4_guard_native_plan_binding()")
    op.execute("""CREATE FUNCTION v4_guard_native_schedule_job() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF EXISTS(SELECT 1 FROM v4_native_schedule_sources WHERE schedule_id=OLD.id) THEN
        IF TG_OP='DELETE' OR (to_jsonb(NEW)-ARRAY['status','next_run_at','updated_at']) IS DISTINCT FROM
          (to_jsonb(OLD)-ARRAY['status','next_run_at','updated_at']) THEN
          RAISE EXCEPTION 'original native schedule identity is immutable';
        END IF;
      END IF;
      IF TG_OP='DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END $$""")
    op.execute("CREATE TRIGGER native_schedule_identity BEFORE UPDATE OR DELETE ON job_schedules FOR EACH ROW EXECUTE FUNCTION v4_guard_native_schedule_job()")
    op.execute("""CREATE FUNCTION v4_guard_native_schedule_progress() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE source v4_native_schedule_sources%ROWTYPE;
    BEGIN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'preserve native schedule progress'; END IF;
      SELECT * INTO source FROM v4_native_schedule_sources WHERE schedule_id=NEW.schedule_id;
      IF TG_OP='INSERT' THEN
        IF NEW.last_slot IS DISTINCT FROM source.observed_last_slot OR NEW.resume_after IS NOT NULL
           OR NEW.next_slot<>source.first_v4_slot THEN RAISE EXCEPTION 'original native schedule progress required'; END IF;
      ELSE
        IF NEW.schedule_id<>OLD.schedule_id OR NEW.last_slot<OLD.last_slot
          OR (OLD.last_slot IS NOT NULL AND NEW.last_slot IS NULL)
          OR (OLD.resume_after IS NOT NULL AND NEW.resume_after IS DISTINCT FROM OLD.resume_after)
          OR (OLD.resume_after IS NOT NULL AND NEW.next_slot<>OLD.next_slot) THEN
          RAISE EXCEPTION 'native schedule progress cannot regress';
        END IF;
        IF NEW.resume_after IS NOT NULL AND NOT EXISTS(SELECT 1 FROM v4_schedule_cutovers cutover
          WHERE cutover.goal_id=source.goal_id AND cutover.legacy_schedule_id=source.schedule_id
          AND cutover.state IN ('rollback_pending','rolled_back') AND cutover.rollback_after_slot=NEW.resume_after
          AND NEW.next_slot>NEW.resume_after) THEN RAISE EXCEPTION 'original rollback watermark required'; END IF;
      END IF;
      RETURN NEW;
    END $$""")
    op.execute("CREATE TRIGGER native_schedule_progress BEFORE INSERT OR UPDATE OR DELETE ON v4_native_schedule_progress FOR EACH ROW EXECUTE FUNCTION v4_guard_native_schedule_progress()")
    _guard(LEGACY_ROLLBACK, NATIVE_ROLLBACK)


def downgrade():
    op.execute("SET LOCAL lock_timeout='3s'")
    op.execute("LOCK TABLE v4_native_schedule_sources,v4_native_schedule_progress,job_schedules IN SHARE ROW EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM v4_native_schedule_sources) OR EXISTS(SELECT 1 FROM v4_native_schedule_progress)
      THEN RAISE EXCEPTION 'preserve original native schedule history'; END IF; END $$""")
    _guard(NATIVE_ROLLBACK, LEGACY_ROLLBACK)
    op.execute("DROP TRIGGER native_plan_binding ON v4_legacy_schedule_plans")
    op.execute("DROP FUNCTION v4_guard_native_plan_binding()")
    op.execute("DROP TRIGGER native_cutover_binding ON v4_schedule_cutovers")
    op.execute("DROP FUNCTION v4_guard_native_cutover_binding()")
    op.execute("DROP TRIGGER native_schedule_identity ON job_schedules")
    op.execute("DROP FUNCTION v4_guard_native_schedule_job()")
    op.execute("DROP TABLE v4_native_schedule_progress,v4_native_schedule_sources")
    op.execute("DROP FUNCTION v4_guard_native_schedule_progress()")
    op.execute("DROP FUNCTION v4_guard_native_schedule_source()")
