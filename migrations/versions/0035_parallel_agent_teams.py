"""Bounded durable parallel teams over canonical agent runs."""
from alembic import op
revision='0035_parallel_agent_teams'
down_revision='0034_runtime_delivery_scope'
branch_labels=None
depends_on=None

def upgrade():
    op.execute("SET LOCAL lock_timeout='3s'")
    op.execute('''CREATE TABLE parallel_agent_limits (
        workspace_id uuid PRIMARY KEY REFERENCES workspaces(id),
        max_running integer NOT NULL DEFAULT 16 CHECK(max_running BETWEEN 1 AND 16))''')
    op.execute('''CREATE TABLE parallel_agent_teams (
        id uuid PRIMARY KEY REFERENCES agent_runs(id), workspace_id uuid NOT NULL REFERENCES workspaces(id),
        actor_id uuid NOT NULL, idempotency_key text NOT NULL CHECK(length(idempotency_key)<=256 AND length(btrim(idempotency_key))>=1),
        fingerprint text NOT NULL CHECK(fingerprint ~ '^[0-9a-f]{64}$'),
        plan jsonb NOT NULL, parent_context jsonb NOT NULL, max_concurrency integer NOT NULL CHECK(max_concurrency BETWEEN 1 AND 8),
        deadline timestamptz NOT NULL, cancelled boolean NOT NULL DEFAULT false,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(workspace_id,actor_id,idempotency_key), UNIQUE(id,workspace_id),
        FOREIGN KEY(actor_id,workspace_id) REFERENCES identity_subjects(id,workspace_id))''')
    op.execute('''CREATE TABLE parallel_agent_tasks (
        id uuid PRIMARY KEY REFERENCES agent_runs(id), team_run_id uuid NOT NULL,
        workspace_id uuid NOT NULL, ordinal integer NOT NULL CHECK(ordinal BETWEEN 0 AND 15),
        task_key text NOT NULL, agent_manifest jsonb NOT NULL, execution_context jsonb NOT NULL,
        state text NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','running','succeeded','failed','unknown','cancelled','timed_out','held')),
        claim_id uuid, lease_until timestamptz, output jsonb, error_type text,
        started_at timestamptz, finished_at timestamptz,
        UNIQUE(team_run_id,ordinal), UNIQUE(team_run_id,task_key),
        FOREIGN KEY(team_run_id,workspace_id) REFERENCES parallel_agent_teams(id,workspace_id),
        CHECK((state='running' AND claim_id IS NOT NULL AND lease_until IS NOT NULL) OR (state<>'running' AND claim_id IS NULL AND lease_until IS NULL)),
        CHECK(output IS NULL OR octet_length(output::text)<=65536),
        CHECK(state<>'succeeded' OR output IS NOT NULL))''')
    op.execute("CREATE INDEX parallel_agent_pending ON parallel_agent_tasks(workspace_id,state,team_run_id,ordinal)")
    op.execute('''CREATE FUNCTION parallel_agent_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE parent_workspace uuid; parent_id uuid; cap integer; team parallel_agent_teams%ROWTYPE;
    BEGIN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'preserve parallel agent history'; END IF;
      IF TG_TABLE_NAME='parallel_agent_teams' THEN
        IF TG_OP='UPDATE' THEN
          IF (to_jsonb(NEW)-'cancelled') IS DISTINCT FROM (to_jsonb(OLD)-'cancelled') OR OLD.cancelled AND NOT NEW.cancelled THEN
            RAISE EXCEPTION 'immutable parallel team plan';
          END IF;
        ELSE
          SELECT workspace_id,parent_agent_run_id INTO parent_workspace,parent_id FROM agent_runs WHERE id=NEW.id;
          IF parent_workspace IS DISTINCT FROM NEW.workspace_id OR parent_id IS NOT NULL THEN RAISE EXCEPTION 'canonical team parent required'; END IF;
        END IF;
      ELSE
        IF TG_OP='UPDATE' THEN
          IF OLD.state='running' AND NEW.state='running' AND to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD) THEN RAISE EXCEPTION 'immutable running claim'; END IF;
          IF ROW(NEW.id,NEW.team_run_id,NEW.workspace_id,NEW.ordinal,NEW.task_key,NEW.agent_manifest,NEW.execution_context)
              IS DISTINCT FROM ROW(OLD.id,OLD.team_run_id,OLD.workspace_id,OLD.ordinal,OLD.task_key,OLD.agent_manifest,OLD.execution_context) THEN
            RAISE EXCEPTION 'immutable parallel task identity';
          END IF;
          IF OLD.state<>'queued' AND NEW.state='queued' OR OLD.state NOT IN ('queued','running') AND NEW.state IS DISTINCT FROM OLD.state THEN
            RAISE EXCEPTION 'parallel task cannot be reexecuted';
          END IF;
          IF OLD.state NOT IN ('queued','running') AND to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD) THEN RAISE EXCEPTION 'immutable parallel task result'; END IF;
        ELSE
          IF NEW.state<>'queued' THEN RAISE EXCEPTION 'new task must be queued'; END IF;
          SELECT workspace_id,parent_agent_run_id INTO parent_workspace,parent_id FROM agent_runs WHERE id=NEW.id;
          IF parent_workspace IS DISTINCT FROM NEW.workspace_id OR parent_id IS DISTINCT FROM NEW.team_run_id THEN RAISE EXCEPTION 'canonical task parent required'; END IF;
        END IF;
      END IF;
      IF TG_TABLE_NAME='parallel_agent_tasks' THEN
       IF NEW.state='running' AND OLD.state='queued' THEN
        SELECT max_running INTO cap FROM parallel_agent_limits WHERE workspace_id=NEW.workspace_id FOR UPDATE;
        SELECT * INTO team FROM parallel_agent_teams WHERE id=NEW.team_run_id;
        IF cap IS NULL OR team.cancelled OR team.deadline<=clock_timestamp() OR NEW.lease_until>team.deadline OR NEW.lease_until<=clock_timestamp()
          OR (SELECT count(*) FROM parallel_agent_tasks WHERE workspace_id=NEW.workspace_id AND state='running')>=cap
          OR (SELECT count(*) FROM parallel_agent_tasks WHERE team_run_id=NEW.team_run_id AND state='running')>=team.max_concurrency
        THEN RAISE EXCEPTION 'parallel concurrency or deadline bound'; END IF;
      END IF;
      END IF;
      RETURN NEW;
    END $$''')
    for table in ['parallel_agent_teams','parallel_agent_tasks']:
        op.execute(f'CREATE TRIGGER immutable_identity BEFORE INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION parallel_agent_guard()')


    op.execute('''CREATE FUNCTION parallel_canonical_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE linked boolean;
    BEGIN
      IF TG_TABLE_NAME='agent_runs' THEN
        linked := EXISTS(SELECT 1 FROM parallel_agent_teams WHERE id=OLD.id) OR EXISTS(SELECT 1 FROM parallel_agent_tasks WHERE id=OLD.id);
        IF linked THEN
          IF TG_OP='DELETE' THEN RAISE EXCEPTION 'preserve canonical parallel run'; END IF;
          IF (to_jsonb(NEW)-ARRAY['state','output_payload','started_at','finished_at']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['state','output_payload','started_at','finished_at'])
            OR OLD.state NOT IN ('queued','running','delegating') AND to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD)
            OR EXISTS(SELECT 1 FROM parallel_agent_tasks WHERE id=OLD.id AND state<>NEW.state)
          THEN RAISE EXCEPTION 'immutable canonical parallel lineage or result'; END IF;
        END IF;
      ELSE
        IF TG_TABLE_NAME='agent_events' THEN linked := EXISTS(SELECT 1 FROM parallel_agent_teams WHERE id=OLD.agent_run_id) OR EXISTS(SELECT 1 FROM parallel_agent_tasks WHERE id=OLD.agent_run_id);
        ELSE linked := EXISTS(SELECT 1 FROM parallel_agent_teams WHERE id=OLD.parent_agent_run_id); END IF;
        IF linked THEN RAISE EXCEPTION 'preserve parallel events and delegations'; END IF;
      END IF;
      IF TG_OP='DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END $$''')
    for table in ['agent_runs','agent_events','agent_delegations']:
        op.execute(f'CREATE TRIGGER parallel_history BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION parallel_canonical_guard()')

def downgrade():
    op.execute("SET LOCAL lock_timeout='3s'")
    op.execute('LOCK TABLE parallel_agent_teams,parallel_agent_tasks IN SHARE ROW EXCLUSIVE MODE')
    op.execute("DO $$ BEGIN IF EXISTS(SELECT 1 FROM parallel_agent_teams) THEN RAISE EXCEPTION 'preserve parallel agent execution history'; END IF; END $$")
    for table in ['agent_runs','agent_events','agent_delegations']:
        op.execute(f'DROP TRIGGER parallel_history ON {table}')
    op.execute('DROP FUNCTION parallel_canonical_guard()')
    op.execute('DROP TABLE parallel_agent_tasks')
    op.execute('DROP TABLE parallel_agent_teams')
    op.execute('DROP TABLE parallel_agent_limits')
    op.execute('DROP FUNCTION parallel_agent_guard()')
