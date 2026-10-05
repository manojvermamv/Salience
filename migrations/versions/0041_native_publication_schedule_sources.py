"""Preserve original governed-publication schedule identities and cutover progress."""

from alembic import op
from sqlalchemy import text


revision = "0041_native_publication_sources"
down_revision = "0040_legacy_publication_stage"
branch_labels = None
depends_on = None


NATIVE_ROLLBACK = """AND ((schedule.payload->>'v4_resume_after')::timestamptz=NEW.rollback_after_slot
 OR EXISTS (SELECT 1 FROM v4_native_schedule_sources source JOIN v4_native_schedule_progress progress USING(schedule_id)
 WHERE source.schedule_id=schedule.id AND source.goal_id=NEW.goal_id AND source.workspace_id=NEW.workspace_id
 AND source.first_v4_slot=NEW.first_v4_slot AND progress.resume_after=NEW.rollback_after_slot
 AND progress.next_slot=schedule.next_run_at))"""
PUBLICATION_ROLLBACK = NATIVE_ROLLBACK[:-1] + """
 OR EXISTS (SELECT 1 FROM v4_native_publication_schedule_sources source
 JOIN v4_native_publication_schedule_progress progress USING(schedule_id)
 WHERE source.schedule_id=schedule.id AND source.goal_id=NEW.goal_id
 AND source.workspace_id=NEW.workspace_id AND source.first_v4_slot=NEW.first_v4_slot
 AND progress.resume_after=NEW.rollback_after_slot AND progress.next_slot=schedule.next_run_at))"""


def _replace_rollback_guard(before, after):
    definition = op.get_bind().execute(
        text("SELECT pg_get_functiondef('v4_preserve_schedule_cutover()'::regprocedure)")
    ).scalar_one()
    if definition.count(before) != 1:
        raise RuntimeError("unknown schedule rollback guard; refuse publication schedule conversion")
    op.execute(definition.replace(before, after, 1))


def upgrade():
    op.execute(
        """CREATE TABLE v4_native_publication_schedule_sources (
            schedule_id uuid PRIMARY KEY,
            publication_schedule_id uuid NOT NULL UNIQUE REFERENCES publication_schedules(id),
            workspace_id uuid NOT NULL,
            goal_id uuid NOT NULL UNIQUE,
            actor_id uuid NOT NULL,
            goal_revision integer NOT NULL CHECK(goal_revision>0),
            remote_id text NOT NULL UNIQUE,
            task_queue text NOT NULL CHECK(task_queue LIKE 'salience-v4-local-legacy-%' AND length(task_queue)<=128),
            interval_seconds integer NOT NULL CHECK(interval_seconds BETWEEN 1 AND 31536000),
            first_v4_slot timestamptz NOT NULL,
            observed_last_slot timestamptz,
            remote_snapshot jsonb NOT NULL,
            original_identity jsonb NOT NULL,
            publication_identity jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            FOREIGN KEY(schedule_id,workspace_id) REFERENCES job_schedules(id,workspace_id),
            FOREIGN KEY(goal_id,workspace_id) REFERENCES v4_goals(id,workspace_id),
            FOREIGN KEY(goal_id,goal_revision) REFERENCES v4_goal_revisions(goal_id,revision),
            FOREIGN KEY(actor_id,workspace_id) REFERENCES identity_subjects(id,workspace_id),
            CHECK(remote_id='publication:'||publication_schedule_id::text),
            CHECK(observed_last_slot IS NULL OR observed_last_slot<first_v4_slot)
        )"""
    )
    op.execute(
        """CREATE TABLE v4_native_publication_schedule_progress (
            schedule_id uuid PRIMARY KEY REFERENCES v4_native_publication_schedule_sources(schedule_id),
            last_slot timestamptz,
            resume_after timestamptz,
            next_slot timestamptz NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )"""
    )
    op.execute(
        """CREATE FUNCTION v4_guard_native_publication_schedule_source() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'native publication schedule source history is immutable'; END IF;
          IF NEW.remote_id<>'publication:'||NEW.publication_schedule_id::text THEN
            RAISE EXCEPTION 'original publication schedule SDK identity required';
          END IF;
          IF NOT EXISTS(
            SELECT 1 FROM job_schedules schedule
            JOIN publication_schedules publication ON publication.id=NEW.publication_schedule_id
              AND publication.job_schedule_id=schedule.id
            JOIN publication_requests request ON request.id=publication.publication_request_id
            JOIN publication_plans plan ON plan.id=publication.publication_plan_id
              AND plan.publication_request_id=request.id
            JOIN ready_to_publish_packages ready ON ready.id=request.ready_package_id
              AND ready.workspace_id=request.workspace_id
              AND ready.content_program_id=request.content_program_id
            JOIN publisher_accounts account ON account.id=request.publisher_account_id
              AND account.workspace_id=request.workspace_id
            JOIN publisher_capability_profiles profile ON profile.id=request.publisher_capability_profile_id
              AND profile.workspace_id=request.workspace_id
              AND profile.publisher_account_id=account.id
              AND profile.publisher_id=request.publisher_id
              AND profile.platform=request.platform
              AND profile.profile_version=request.capability_profile_version
            JOIN budgets budget ON budget.id=publication.budget_id
              AND budget.workspace_id=request.workspace_id
              AND budget.content_program_id=request.content_program_id
            JOIN approval_requests approval ON approval.id=request.publication_approval_request_id
              AND approval.workspace_id=request.workspace_id
            JOIN v4_goals goal ON goal.id=NEW.goal_id
            JOIN v4_goal_revisions revision ON revision.goal_id=goal.id AND revision.revision=NEW.goal_revision
            JOIN v4_legacy_publication_accounts mapping ON mapping.goal_id=goal.id
              AND mapping.goal_revision=NEW.goal_revision AND mapping.workspace_id=NEW.workspace_id
            WHERE schedule.id=NEW.schedule_id AND schedule.workspace_id=NEW.workspace_id
              AND schedule.job_type='governed_publication' AND schedule.timezone='UTC'
              AND schedule.schedule_expression='every '||NEW.interval_seconds::text||'s'
              AND schedule.payload=jsonb_build_object(
                'publication_request_id',request.id::text,
                'publication_plan_id',plan.id::text,
                'budget_id',budget.id::text,
                'schedule_version',publication.version,
                'contract_version','PublicationSchedule@v1')
              AND (to_jsonb(schedule)-ARRAY['status','next_run_at','updated_at'])=NEW.original_identity
              AND to_jsonb(publication)=NEW.publication_identity
              AND publication.status='scheduled' AND publication.timezone='UTC'
              AND goal.workspace_id=NEW.workspace_id AND goal.revision=NEW.goal_revision
              AND goal.state='active'
              AND revision.payload->>'content_program_id'=schedule.content_program_id::text
              AND mapping.content_program_id=schedule.content_program_id
              AND mapping.publisher_account_id=request.publisher_account_id
              AND request.workspace_id=NEW.workspace_id
              AND request.content_program_id=schedule.content_program_id
              AND request.platform='fixture' AND request.destination='fixture://account'
              AND request.locale='en' AND request.territory='global'
              AND request.visibility='private' AND request.capability_profile_version=1
              AND request.publisher_id='fixture-publisher'
              AND plan.publisher_id='fixture-publisher'
              AND plan.publisher_version='1' AND profile.publisher_version='1'
              AND ready.approval_state='approved'
              AND account.platform='fixture' AND account.account_type='creator'
              AND account.status='active' AND account.external_account_reference LIKE 'fixture:%'
              AND profile.audit_state='verified'
              AND (profile.expires_at IS NULL OR profile.expires_at>clock_timestamp())
              AND approval.effect_type='publication' AND approval.status='approved'
              AND approval.request_context @> (jsonb_build_object(
                'ready_package_id',ready.id::text,'publisher_account_id',account.id::text,
                'platform',request.platform,'destination',request.destination,'locale',request.locale,
                'territory',request.territory,'visibility',request.visibility,
                'capability_profile_version',request.capability_profile_version))
              AND budget.scope='publication' AND budget.status='active' AND budget.limit_amount=0
              AND NEW.workspace_id=schedule.workspace_id
              AND NEW.goal_id IS NOT NULL AND NEW.actor_id=mapping.actor_id
              AND NEW.goal_revision=mapping.goal_revision
          ) THEN RAISE EXCEPTION 'original approved native publication schedule required'; END IF;
          RETURN NEW;
        END $$"""
    )
    op.execute(
        "CREATE TRIGGER native_publication_source_identity BEFORE INSERT OR UPDATE OR DELETE ON v4_native_publication_schedule_sources FOR EACH ROW EXECUTE FUNCTION v4_guard_native_publication_schedule_source()"
    )
    op.execute(
        """CREATE FUNCTION v4_guard_native_publication_schedule_job() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF EXISTS(SELECT 1 FROM v4_native_publication_schedule_sources WHERE schedule_id=OLD.id) THEN
            IF TG_OP='DELETE' OR (to_jsonb(NEW)-ARRAY['status','next_run_at','updated_at']) IS DISTINCT FROM
              (to_jsonb(OLD)-ARRAY['status','next_run_at','updated_at']) THEN
              RAISE EXCEPTION 'original native publication schedule identity is immutable';
            END IF;
          END IF;
          IF TG_OP='DELETE' THEN RETURN OLD; END IF;
          RETURN NEW;
        END $$"""
    )
    op.execute(
        "CREATE TRIGGER native_publication_schedule_identity BEFORE UPDATE OR DELETE ON job_schedules FOR EACH ROW EXECUTE FUNCTION v4_guard_native_publication_schedule_job()"
    )
    op.execute(
        """CREATE FUNCTION v4_guard_native_publication_schedule_progress() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE source v4_native_publication_schedule_sources%ROWTYPE;
        BEGIN
          IF TG_OP='DELETE' THEN RAISE EXCEPTION 'preserve native publication schedule progress'; END IF;
          SELECT * INTO source FROM v4_native_publication_schedule_sources WHERE schedule_id=NEW.schedule_id;
          IF TG_OP='INSERT' THEN
            IF NEW.last_slot IS DISTINCT FROM source.observed_last_slot OR NEW.resume_after IS NOT NULL
               OR NEW.next_slot<>source.first_v4_slot THEN
              RAISE EXCEPTION 'original native publication schedule progress required';
            END IF;
          ELSE
            IF NEW.schedule_id<>OLD.schedule_id
              OR (OLD.last_slot IS NOT NULL AND (NEW.last_slot IS NULL OR NEW.last_slot<OLD.last_slot))
              OR NEW.next_slot<OLD.next_slot
              OR (OLD.resume_after IS NOT NULL AND NEW.resume_after IS DISTINCT FROM OLD.resume_after)
              OR (OLD.resume_after IS NOT NULL AND NEW.next_slot<>OLD.next_slot) THEN
              RAISE EXCEPTION 'native publication schedule progress cannot regress';
            END IF;
            IF NEW.resume_after IS NOT NULL AND NOT EXISTS(
              SELECT 1 FROM v4_schedule_cutovers cutover
              WHERE cutover.goal_id=source.goal_id AND cutover.legacy_schedule_id=source.schedule_id
                AND cutover.state IN ('rollback_pending','rolled_back')
                AND cutover.rollback_after_slot=NEW.resume_after AND NEW.next_slot>NEW.resume_after
            ) THEN RAISE EXCEPTION 'original publication rollback watermark required'; END IF;
          END IF;
          RETURN NEW;
        END $$"""
    )
    op.execute(
        "CREATE TRIGGER native_publication_schedule_progress BEFORE INSERT OR UPDATE OR DELETE ON v4_native_publication_schedule_progress FOR EACH ROW EXECUTE FUNCTION v4_guard_native_publication_schedule_progress()"
    )
    op.execute(
        """CREATE FUNCTION v4_guard_native_publication_cutover_binding() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF EXISTS(SELECT 1 FROM v4_native_publication_schedule_sources WHERE schedule_id=NEW.legacy_schedule_id)
            AND NOT EXISTS(SELECT 1 FROM v4_native_publication_schedule_sources source
              WHERE source.schedule_id=NEW.legacy_schedule_id AND source.goal_id=NEW.goal_id
                AND source.workspace_id=NEW.workspace_id AND source.actor_id=NEW.actor_id
                AND source.goal_revision=NEW.goal_revision AND source.first_v4_slot=NEW.first_v4_slot)
          THEN RAISE EXCEPTION 'original native publication cutover binding required'; END IF;
          RETURN NEW;
        END $$"""
    )
    op.execute(
        "CREATE TRIGGER native_publication_cutover_binding BEFORE INSERT ON v4_schedule_cutovers FOR EACH ROW EXECUTE FUNCTION v4_guard_native_publication_cutover_binding()"
    )
    op.execute(
        """CREATE FUNCTION v4_guard_native_publication_intelligence_plan() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF EXISTS(SELECT 1 FROM v4_native_publication_schedule_sources source
              WHERE source.goal_id=NEW.goal_id AND source.schedule_id=(
                SELECT legacy_schedule_id FROM v4_schedule_cutovers WHERE goal_id=NEW.goal_id))
          THEN RAISE EXCEPTION 'native publication source requires publication-stage plan'; END IF;
          RETURN NEW;
        END $$"""
    )
    op.execute(
        "CREATE TRIGGER native_publication_intelligence_plan BEFORE INSERT ON v4_legacy_schedule_plans FOR EACH ROW EXECUTE FUNCTION v4_guard_native_publication_intelligence_plan()"
    )
    op.execute(
        """CREATE TABLE v4_legacy_publication_schedule_plans (
            goal_id uuid PRIMARY KEY REFERENCES v4_schedule_cutovers(goal_id),
            workspace_id uuid NOT NULL REFERENCES workspaces(id),
            actor_id uuid NOT NULL REFERENCES identity_subjects(id),
            goal_revision integer NOT NULL,
            schedule_id uuid NOT NULL UNIQUE,
            publication_schedule_id uuid NOT NULL UNIQUE REFERENCES publication_schedules(id),
            task_queue text NOT NULL CHECK(task_queue LIKE 'salience-v4-local-legacy-%' AND length(task_queue)<=128),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            FOREIGN KEY(goal_id,goal_revision) REFERENCES v4_goal_revisions(goal_id,revision)
        )"""
    )
    op.execute(
        """CREATE FUNCTION v4_guard_legacy_publication_schedule_plan() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'legacy publication schedule plan is immutable'; END IF;
          IF NOT EXISTS(SELECT 1 FROM v4_schedule_cutovers cutover
              JOIN v4_native_publication_schedule_sources source ON source.schedule_id=NEW.schedule_id
              WHERE cutover.goal_id=NEW.goal_id AND cutover.workspace_id=NEW.workspace_id
                AND cutover.actor_id=NEW.actor_id AND cutover.goal_revision=NEW.goal_revision
                AND cutover.legacy_schedule_id=NEW.schedule_id AND cutover.state='pending'
                AND source.goal_id=NEW.goal_id AND source.workspace_id=NEW.workspace_id
                AND source.actor_id=NEW.actor_id AND source.goal_revision=NEW.goal_revision
                AND source.publication_schedule_id=NEW.publication_schedule_id
                AND source.task_queue=NEW.task_queue)
          THEN RAISE EXCEPTION 'original pending native publication cutover required'; END IF;
          RETURN NEW;
        END $$"""
    )
    op.execute(
        "CREATE TRIGGER immutable_legacy_publication_schedule_plan BEFORE INSERT OR UPDATE OR DELETE ON v4_legacy_publication_schedule_plans FOR EACH ROW EXECUTE FUNCTION v4_guard_legacy_publication_schedule_plan()"
    )
    _replace_rollback_guard(NATIVE_ROLLBACK, PUBLICATION_ROLLBACK)


def downgrade():
    op.execute("SET LOCAL lock_timeout='3s'")
    op.execute(
        "LOCK TABLE v4_native_publication_schedule_sources,v4_native_publication_schedule_progress,v4_legacy_publication_schedule_plans,job_schedules IN SHARE ROW EXCLUSIVE MODE"
    )
    op.execute(
        """DO $$ BEGIN
          IF EXISTS(SELECT 1 FROM v4_native_publication_schedule_sources)
            OR EXISTS(SELECT 1 FROM v4_native_publication_schedule_progress)
            OR EXISTS(SELECT 1 FROM v4_legacy_publication_schedule_plans)
          THEN RAISE EXCEPTION 'preserve original native publication schedule history'; END IF;
        END $$"""
    )
    _replace_rollback_guard(PUBLICATION_ROLLBACK, NATIVE_ROLLBACK)
    op.execute("DROP TRIGGER immutable_legacy_publication_schedule_plan ON v4_legacy_publication_schedule_plans")
    op.execute("DROP FUNCTION v4_guard_legacy_publication_schedule_plan()")
    op.execute("DROP TABLE v4_legacy_publication_schedule_plans")
    op.execute("DROP TRIGGER native_publication_intelligence_plan ON v4_legacy_schedule_plans")
    op.execute("DROP FUNCTION v4_guard_native_publication_intelligence_plan()")
    op.execute("DROP TRIGGER native_publication_cutover_binding ON v4_schedule_cutovers")
    op.execute("DROP FUNCTION v4_guard_native_publication_cutover_binding()")
    op.execute("DROP TRIGGER native_publication_schedule_progress ON v4_native_publication_schedule_progress")
    op.execute("DROP FUNCTION v4_guard_native_publication_schedule_progress()")
    op.execute("DROP TABLE v4_native_publication_schedule_progress")
    op.execute("DROP TRIGGER native_publication_schedule_identity ON job_schedules")
    op.execute("DROP FUNCTION v4_guard_native_publication_schedule_job()")
    op.execute("DROP TRIGGER native_publication_source_identity ON v4_native_publication_schedule_sources")
    op.execute("DROP TABLE v4_native_publication_schedule_sources")
    op.execute("DROP FUNCTION v4_guard_native_publication_schedule_source()")
