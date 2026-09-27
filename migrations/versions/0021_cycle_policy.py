"""Add versioned cadence receipts without rewriting existing admission history."""

from alembic import op


revision = "0021_cycle_policy"
down_revision = "0020_goal_baselines"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE v4_cycle_intents ADD CONSTRAINT v4_intent_goal_identity UNIQUE (id,goal_id)")
    op.execute("""
        CREATE TABLE v4_cycle_requests (
            goal_id uuid NOT NULL REFERENCES v4_goals(id),
            origin text NOT NULL CHECK (origin IN ('manual','event','scheduled')),
            idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 256),
            fingerprint text NOT NULL,
            intent_id uuid NOT NULL,
            goal_revision integer NOT NULL,
            slot_time timestamptz NOT NULL,
            backfill boolean NOT NULL,
            payload jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (goal_id,origin,idempotency_key),
            FOREIGN KEY (intent_id,goal_id) REFERENCES v4_cycle_intents(id,goal_id),
            FOREIGN KEY (goal_id,goal_revision) REFERENCES v4_goal_revisions(goal_id,revision)
        )
    """)
    op.execute("""
        CREATE TABLE v4_schedule_cursors (
            goal_id uuid NOT NULL,
            goal_revision integer NOT NULL,
            last_slot timestamptz NOT NULL,
            PRIMARY KEY (goal_id,goal_revision),
            FOREIGN KEY (goal_id,goal_revision) REFERENCES v4_goal_revisions(goal_id,revision)
        )
    """)
    op.execute("""
        CREATE TABLE v4_schedule_batches (
            goal_id uuid NOT NULL REFERENCES v4_goals(id),
            idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 256),
            fingerprint text NOT NULL,
            payload jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (goal_id,idempotency_key)
        )
    """)
    for table in ["v4_cycle_requests", "v4_schedule_batches"]:
        op.execute(f"CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION v4_immutable()")
    op.execute("""
        CREATE FUNCTION public.v4_serialize_grant_edit() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
        BEGIN
            PERFORM identity.id FROM public.identity_subjects AS identity
            WHERE (TG_OP != 'INSERT' AND OLD.principal_type='identity'
                   AND identity.id::text=OLD.principal_id AND identity.workspace_id=OLD.workspace_id)
               OR (TG_OP != 'DELETE' AND NEW.principal_type='identity'
                   AND identity.id::text=NEW.principal_id AND identity.workspace_id=NEW.workspace_id)
            ORDER BY identity.id FOR UPDATE OF identity;
            IF TG_OP='DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("REVOKE ALL ON FUNCTION public.v4_serialize_grant_edit() FROM PUBLIC")
    op.execute("CREATE TRIGGER v4_grant_edit BEFORE INSERT OR UPDATE OR DELETE ON permission_grants FOR EACH ROW EXECUTE FUNCTION public.v4_serialize_grant_edit()")


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM v4_cycle_requests)
               OR EXISTS (SELECT 1 FROM v4_schedule_batches)
               OR EXISTS (SELECT 1 FROM v4_schedule_cursors)
               OR EXISTS (SELECT 1 FROM v4_goal_revisions WHERE payload->>'schema_version'='GoalSpec.local.v2') THEN
                RAISE EXCEPTION 'preserve V2 policy and context history';
            END IF;
        END $$
    """)
    op.execute("DROP TRIGGER v4_grant_edit ON permission_grants")
    op.execute("DROP FUNCTION public.v4_serialize_grant_edit()")
    op.execute("DROP TABLE v4_schedule_batches")
    op.execute("DROP TABLE v4_schedule_cursors")
    op.execute("DROP TABLE v4_cycle_requests")
    op.execute("ALTER TABLE v4_cycle_intents DROP CONSTRAINT v4_intent_goal_identity")
