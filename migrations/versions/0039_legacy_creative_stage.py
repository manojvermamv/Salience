"""Qualify fixed dry creative starts without rewriting original legacy history."""

from alembic import op
from sqlalchemy import text

revision = "0039_legacy_creative_stage"
down_revision = "0038_legacy_dummy_stage"
branch_labels = None
depends_on = None

OLD_JOB = """AND job.job_type=CASE NEW.stage WHEN 'intelligence' THEN 'intelligence_research' WHEN 'dummy' THEN 'durable_dummy' END
            AND (NEW.stage='intelligence' OR NEW.payload='{"contract_version":"DummyWorkflowRequest@v1","mode":"success"}'::jsonb)
            AND job.dry_run AND job.state='queued'"""
NEW_JOB = OLD_JOB.replace("WHEN 'dummy' THEN 'durable_dummy' END", "WHEN 'dummy' THEN 'durable_dummy' WHEN 'creative' THEN 'creative_production' END").replace(
    "AND (NEW.stage='intelligence' OR NEW.payload=", "AND (NEW.stage IN ('intelligence','creative') OR NEW.payload=") + """
            AND (NEW.stage<>'creative' OR (
                NEW.payload->>'contract_version'='CreativeProductionRequest@v1'
                AND NEW.payload->>'target_profile_key'='fixture-short-video'
                AND NEW.payload->'target_profile_version'='1'::jsonb
                AND NEW.payload->'max_variants' IN ('1'::jsonb,'2'::jsonb,'3'::jsonb)
                AND (SELECT count(*) FROM jsonb_object_keys(NEW.payload))=5
                AND EXISTS(SELECT 1 FROM content_brief_versions brief
                    WHERE brief.id::text=NEW.payload->>'brief_id'
                    AND brief.workspace_id=NEW.workspace_id
                    AND brief.content_program_id=NEW.content_program_id)))"""


def replace_guard(before, after):
    definition = op.get_bind().execute(text("SELECT pg_get_functiondef('v4_guard_legacy_dispatch()'::regprocedure)")).scalar_one()
    if definition.count(before) != 1:
        raise RuntimeError("unknown original legacy guard; refuse creative conversion")
    op.execute(definition.replace(before, after, 1))


def upgrade():
    op.execute("ALTER TABLE v4_legacy_dispatches DROP CONSTRAINT v4_legacy_dispatches_stage_check")
    op.execute("ALTER TABLE v4_legacy_dispatches ADD CONSTRAINT v4_legacy_dispatches_stage_check CHECK(stage IN ('intelligence','dummy','creative'))")
    replace_guard(OLD_JOB, NEW_JOB)


def downgrade():
    op.execute("SET LOCAL lock_timeout='3s'")
    op.execute("LOCK TABLE v4_legacy_dispatches,jobs IN SHARE ROW EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM v4_legacy_dispatches WHERE stage='creative') THEN
            RAISE EXCEPTION 'preserve original legacy creative history';
        END IF;
    END $$""")
    replace_guard(NEW_JOB, OLD_JOB)
    op.execute("ALTER TABLE v4_legacy_dispatches DROP CONSTRAINT v4_legacy_dispatches_stage_check")
    op.execute("ALTER TABLE v4_legacy_dispatches ADD CONSTRAINT v4_legacy_dispatches_stage_check CHECK(stage IN ('intelligence','dummy'))")
