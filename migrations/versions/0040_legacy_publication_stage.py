"""Original publication identity with explicit fixture account approval and durable simulation."""

from alembic import op
from sqlalchemy import text

revision = "0040_legacy_publication_stage"
down_revision = "0039_legacy_creative_stage"
branch_labels = None
depends_on = None

OLD_JOB = """AND job.job_type=CASE NEW.stage WHEN 'intelligence' THEN 'intelligence_research' WHEN 'dummy' THEN 'durable_dummy' WHEN 'creative' THEN 'creative_production' END
            AND (NEW.stage IN ('intelligence','creative') OR NEW.payload='{"contract_version":"DummyWorkflowRequest@v1","mode":"success"}'::jsonb)
            AND job.dry_run AND job.state='queued'
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
NEW_JOB = """AND job.job_type=CASE NEW.stage WHEN 'intelligence' THEN 'intelligence_research' WHEN 'dummy' THEN 'durable_dummy' WHEN 'creative' THEN 'creative_production' WHEN 'publication' THEN 'governed_publication' END
            AND (NEW.stage IN ('intelligence','creative','publication') OR NEW.payload='{"contract_version":"DummyWorkflowRequest@v1","mode":"success"}'::jsonb)
            AND ((NEW.stage='publication' AND NOT job.dry_run) OR (NEW.stage<>'publication' AND job.dry_run)) AND job.state='queued'
            AND (NEW.stage<>'creative' OR (
                NEW.payload->>'contract_version'='CreativeProductionRequest@v1'
                AND NEW.payload->>'target_profile_key'='fixture-short-video'
                AND NEW.payload->'target_profile_version'='1'::jsonb
                AND NEW.payload->'max_variants' IN ('1'::jsonb,'2'::jsonb,'3'::jsonb)
                AND (SELECT count(*) FROM jsonb_object_keys(NEW.payload))=5
                AND EXISTS(SELECT 1 FROM content_brief_versions brief
                    WHERE brief.id::text=NEW.payload->>'brief_id'
                    AND brief.workspace_id=NEW.workspace_id
                    AND brief.content_program_id=NEW.content_program_id)))
            AND (NEW.stage<>'publication' OR (
                NEW.payload->>'contract_version'='PublicationWorkflowRequest@v1'
                AND NEW.payload->>'platform'='fixture'
                AND NEW.payload->>'destination'='fixture://account'
                AND NEW.payload->>'locale'='en' AND NEW.payload->>'territory'='global'
                AND NEW.payload->>'visibility'='private'
                AND NEW.payload->'capability_profile_version'='1'::jsonb
                AND (SELECT count(*) FROM jsonb_object_keys(NEW.payload))=11
                AND EXISTS(SELECT 1 FROM v4_legacy_publication_accounts mapping
                  WHERE mapping.goal_id=NEW.goal_id AND mapping.goal_revision=i.goal_revision
                    AND mapping.workspace_id=NEW.workspace_id AND mapping.content_program_id=NEW.content_program_id
                    AND mapping.publisher_account_id::text=NEW.payload->>'publisher_account_id'
                    AND mapping.baseline_approval_id::text=ctx.payload->>'baseline_approval_id')
                AND EXISTS(SELECT 1 FROM ready_to_publish_packages ready
                  JOIN publisher_accounts account ON account.id::text=NEW.payload->>'publisher_account_id'
                  JOIN approval_requests approval ON approval.id::text=NEW.payload->>'publication_approval_request_id'
                  JOIN budgets budget ON budget.id::text=NEW.payload->>'budget_id'
                  WHERE ready.id::text=NEW.payload->>'ready_package_id'
                    AND ready.workspace_id=NEW.workspace_id AND ready.content_program_id=NEW.content_program_id
                    AND ready.approval_state='approved' AND account.workspace_id=NEW.workspace_id
                    AND account.platform='fixture' AND account.account_type='creator' AND account.status='active'
                    AND account.external_account_reference LIKE 'fixture:%'
                    AND approval.workspace_id=NEW.workspace_id AND approval.effect_type='publication' AND approval.status='approved'
                    AND approval.request_context @> (NEW.payload-ARRAY['contract_version','budget_id','publication_approval_request_id'])
                    AND budget.workspace_id=NEW.workspace_id AND budget.content_program_id=NEW.content_program_id
                    AND budget.scope='publication' AND budget.status='active' AND budget.limit_amount=0)))"""


def replace_guard(before, after):
    definition = op.get_bind().execute(text("SELECT pg_get_functiondef('v4_guard_legacy_dispatch()'::regprocedure)")).scalar_one()
    if definition.count(before) != 1:
        raise RuntimeError("unknown original legacy guard; refuse publication conversion")
    op.execute(definition.replace(before, after, 1))


def upgrade():
    op.execute("""CREATE TABLE v4_legacy_publication_accounts (
        goal_id uuid NOT NULL REFERENCES v4_goals(id), goal_revision integer NOT NULL,
        workspace_id uuid NOT NULL REFERENCES workspaces(id),
        content_program_id uuid NOT NULL REFERENCES content_programs(id),
        publisher_account_id uuid NOT NULL REFERENCES publisher_accounts(id),
        actor_id uuid NOT NULL REFERENCES identity_subjects(id),
        baseline_approval_id uuid NOT NULL REFERENCES v4_goal_baselines(approval_id),
        account_ref text NOT NULL CHECK(account_ref='fixture-account'),
        account_identity jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY(goal_id,goal_revision),
        FOREIGN KEY(goal_id,goal_revision) REFERENCES v4_goal_revisions(goal_id,revision)
    )""")
    op.execute("""CREATE FUNCTION v4_guard_legacy_publication_mapping() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'legacy publication account mapping is immutable'; END IF;
      IF NOT EXISTS(SELECT 1 FROM v4_goals goal
          JOIN v4_goal_revisions revision ON revision.goal_id=goal.id AND revision.revision=NEW.goal_revision
          JOIN v4_goal_baselines baseline ON baseline.goal_id=goal.id AND baseline.goal_revision=NEW.goal_revision
          JOIN publisher_accounts account ON account.id=NEW.publisher_account_id
          JOIN identity_subjects actor ON actor.id=NEW.actor_id AND actor.workspace_id=NEW.workspace_id
          WHERE goal.id=NEW.goal_id AND goal.workspace_id=NEW.workspace_id AND goal.revision=NEW.goal_revision
            AND goal.state='active' AND revision.payload->>'content_program_id'=NEW.content_program_id::text
            AND revision.payload->'account_refs' @> '["fixture-account"]'::jsonb
            AND baseline.approval_id=NEW.baseline_approval_id AND baseline.expires_at>clock_timestamp()
            AND NOT EXISTS(SELECT 1 FROM v4_baseline_revocations WHERE approval_id=baseline.approval_id)
            AND account.workspace_id=NEW.workspace_id AND account.platform='fixture'
            AND account.account_type='creator' AND account.status='active'
            AND account.external_account_reference LIKE 'fixture:%'
            AND NEW.account_identity=(to_jsonb(account)-ARRAY['status','updated_at']))
      THEN RAISE EXCEPTION 'original approved fixture publication account required'; END IF;
      RETURN NEW;
    END $$""")
    op.execute("CREATE TRIGGER immutable_legacy_publication_mapping BEFORE INSERT OR UPDATE OR DELETE ON v4_legacy_publication_accounts FOR EACH ROW EXECUTE FUNCTION v4_guard_legacy_publication_mapping()")
    op.execute("""CREATE FUNCTION v4_guard_mapped_publication_account() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF EXISTS(SELECT 1 FROM v4_legacy_publication_accounts WHERE publisher_account_id=OLD.id)
          AND (TG_OP='DELETE' OR (to_jsonb(NEW)-ARRAY['status','updated_at']) IS DISTINCT FROM
                                (to_jsonb(OLD)-ARRAY['status','updated_at']))
      THEN RAISE EXCEPTION 'original mapped publication account identity is immutable'; END IF;
      IF TG_OP='DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END $$""")
    op.execute("CREATE TRIGGER mapped_publication_account_identity BEFORE UPDATE OR DELETE ON publisher_accounts FOR EACH ROW EXECUTE FUNCTION v4_guard_mapped_publication_account()")
    op.execute("""CREATE TABLE v4_legacy_publication_fixture_receipts (
        operation_id uuid PRIMARY KEY REFERENCES v4_legacy_dispatches(operation_id),
        workspace_id uuid NOT NULL REFERENCES workspaces(id),
        idempotency_key text NOT NULL, fingerprint text NOT NULL CHECK(fingerprint ~ '^[0-9a-f]{64}$'),
        remote_id text NOT NULL UNIQUE, request jsonb NOT NULL, receipt jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(workspace_id,idempotency_key)
    )""")
    op.execute("""CREATE TABLE v4_legacy_publication_fixture_progress (
        operation_id uuid PRIMARY KEY REFERENCES v4_legacy_publication_fixture_receipts(operation_id),
        state text NOT NULL CHECK(state IN ('accepted','processing','published','cancelled')),
        polls integer NOT NULL DEFAULT 0 CHECK(polls>=0),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
    )""")
    op.execute("""CREATE FUNCTION v4_guard_legacy_publication_fixture_receipt() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'preserve original synthetic publication receipt'; END IF;
      IF NOT EXISTS(SELECT 1 FROM v4_legacy_dispatches binding
        JOIN publication_requests request ON request.id::text=NEW.request->>'id'
        WHERE binding.operation_id=NEW.operation_id AND binding.workspace_id=NEW.workspace_id AND binding.stage='publication'
          AND NEW.idempotency_key='v4-legacy:'||binding.operation_id::text
          AND request.request_key=NEW.idempotency_key AND request.workspace_id=NEW.workspace_id
          AND request.content_program_id=binding.content_program_id
          AND request.publisher_account_id::text=binding.payload->>'publisher_account_id'
          AND request.ready_package_id::text=binding.payload->>'ready_package_id'
          AND NEW.request->>'workspace_id'=NEW.workspace_id::text
          AND NEW.request->>'content_program_id'=binding.content_program_id::text
          AND NEW.request->>'publisher_account_id'=request.publisher_account_id::text
          AND NEW.request->>'ready_package_id'=request.ready_package_id::text
          AND NEW.request->>'idempotency_key'=NEW.idempotency_key
          AND NEW.fingerprint=encode(digest(NEW.request::text,'sha256'),'hex')
          AND NEW.remote_id='fixture-v4-publication:'||NEW.operation_id::text
          AND NEW.receipt->>'remote_id'=NEW.remote_id AND NEW.receipt->>'publisher_id'='fixture-publisher'
          AND NEW.receipt->>'state'='accepted')
      THEN RAISE EXCEPTION 'original scoped fixture publication receipt required'; END IF;
      RETURN NEW;
    END $$""")
    op.execute("CREATE TRIGGER immutable_legacy_publication_fixture_receipt BEFORE INSERT OR UPDATE OR DELETE ON v4_legacy_publication_fixture_receipts FOR EACH ROW EXECUTE FUNCTION v4_guard_legacy_publication_fixture_receipt()")
    op.execute("""CREATE FUNCTION v4_guard_legacy_publication_fixture_progress() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'preserve synthetic publication progress'; END IF;
      IF TG_OP='INSERT' AND (NEW.state<>'accepted' OR NEW.polls<>0) THEN RAISE EXCEPTION 'original synthetic accepted state required'; END IF;
      IF TG_OP='UPDATE' AND (NEW.operation_id<>OLD.operation_id OR NEW.polls<OLD.polls
        OR (OLD.state IN ('published','cancelled') AND NEW.state<>OLD.state)
        OR (OLD.state='processing' AND NEW.state='accepted'))
      THEN RAISE EXCEPTION 'preserve monotonic synthetic publication outcome'; END IF;
      RETURN NEW;
    END $$""")
    op.execute("CREATE TRIGGER legacy_publication_fixture_progress BEFORE INSERT OR UPDATE OR DELETE ON v4_legacy_publication_fixture_progress FOR EACH ROW EXECUTE FUNCTION v4_guard_legacy_publication_fixture_progress()")
    op.execute("ALTER TABLE v4_legacy_dispatches DROP CONSTRAINT v4_legacy_dispatches_stage_check")
    op.execute("ALTER TABLE v4_legacy_dispatches ADD CONSTRAINT v4_legacy_dispatches_stage_check CHECK(stage IN ('intelligence','dummy','creative','publication'))")
    replace_guard(OLD_JOB, NEW_JOB)


def downgrade():
    op.execute("SET LOCAL lock_timeout='3s'")
    op.execute("LOCK TABLE v4_legacy_dispatches,v4_legacy_publication_accounts,v4_legacy_publication_fixture_receipts,v4_legacy_publication_fixture_progress,jobs,publisher_accounts IN SHARE ROW EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM v4_legacy_dispatches WHERE stage='publication')
        OR EXISTS(SELECT 1 FROM v4_legacy_publication_accounts)
        OR EXISTS(SELECT 1 FROM v4_legacy_publication_fixture_receipts)
      THEN RAISE EXCEPTION 'preserve original legacy publication history'; END IF;
    END $$""")
    replace_guard(NEW_JOB, OLD_JOB)
    op.execute("ALTER TABLE v4_legacy_dispatches DROP CONSTRAINT v4_legacy_dispatches_stage_check")
    op.execute("ALTER TABLE v4_legacy_dispatches ADD CONSTRAINT v4_legacy_dispatches_stage_check CHECK(stage IN ('intelligence','dummy','creative'))")
    op.execute("DROP TRIGGER mapped_publication_account_identity ON publisher_accounts")
    op.execute("DROP FUNCTION v4_guard_mapped_publication_account()")
    op.execute("DROP TABLE v4_legacy_publication_fixture_progress,v4_legacy_publication_fixture_receipts,v4_legacy_publication_accounts")
    op.execute("DROP FUNCTION v4_guard_legacy_publication_mapping(),v4_guard_legacy_publication_fixture_receipt(),v4_guard_legacy_publication_fixture_progress()")
