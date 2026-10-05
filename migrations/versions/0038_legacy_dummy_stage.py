"""Retain legacy intelligence bindings while qualifying no-send dummy starts."""

from alembic import op
from sqlalchemy import text

revision = "0038_legacy_dummy_stage"
down_revision = "0037_native_schedule_sources"
branch_labels = None
depends_on = None

OLD_JOB = "AND job.job_type='intelligence_research' AND job.dry_run AND job.state='queued'"
NEW_JOB = """AND job.job_type=CASE NEW.stage WHEN 'intelligence' THEN 'intelligence_research' WHEN 'dummy' THEN 'durable_dummy' END
            AND (NEW.stage='intelligence' OR NEW.payload='{"contract_version":"DummyWorkflowRequest@v1","mode":"success"}'::jsonb)
            AND job.dry_run AND job.state='queued'"""


def replace_guard(before, after):
    definition = op.get_bind().execute(text("SELECT pg_get_functiondef('v4_guard_legacy_dispatch()'::regprocedure)")).scalar_one()
    if definition.count(before) != 1:
        raise RuntimeError("unknown original legacy guard; refuse stage conversion")
    op.execute(definition.replace(before, after, 1))


def upgrade():
    op.execute("ALTER TABLE v4_legacy_dispatches ADD COLUMN stage text NOT NULL DEFAULT 'intelligence' CHECK(stage IN ('intelligence','dummy'))")
    replace_guard(OLD_JOB, NEW_JOB)


def downgrade():
    op.execute("SET LOCAL lock_timeout='3s'")
    op.execute("LOCK TABLE v4_legacy_dispatches,jobs IN SHARE ROW EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM v4_legacy_dispatches WHERE stage<>'intelligence') THEN
            RAISE EXCEPTION 'preserve original legacy stage history';
        END IF;
    END $$""")
    replace_guard(NEW_JOB, OLD_JOB)
    op.execute("ALTER TABLE v4_legacy_dispatches DROP COLUMN stage")
