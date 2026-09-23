"""Persist object verification, holds and reference-safe collection."""

from alembic import op


revision = "0014_object_inventory"
down_revision = "0013_identity_boundary"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE object_inventory (
            storage_key TEXT PRIMARY KEY,
            workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
            content_hash VARCHAR(64) NOT NULL,
            byte_size BIGINT NOT NULL CHECK (byte_size >= 0),
            content_type TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'unverified' CHECK (state IN ('unverified', 'verified', 'quarantined', 'deleted')),
            retain_until TIMESTAMPTZ NOT NULL,
            legal_hold BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, storage_key)
        )
    """)
    op.execute("""
        CREATE TABLE object_references (
            workspace_id UUID NOT NULL,
            storage_key TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, storage_key, owner_id),
            FOREIGN KEY (workspace_id, storage_key) REFERENCES object_inventory(workspace_id, storage_key) ON DELETE RESTRICT
        )
    """)
    op.execute("""
        CREATE FUNCTION protect_object_identity() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'retain object tombstones';
            END IF;
            IF ROW(NEW.storage_key, NEW.workspace_id, NEW.content_hash, NEW.byte_size, NEW.content_type)
                IS DISTINCT FROM ROW(OLD.storage_key, OLD.workspace_id, OLD.content_hash, OLD.byte_size, OLD.content_type)
                OR (OLD.state = 'deleted' AND NEW.state <> 'deleted') THEN
                RAISE EXCEPTION 'immutable object identity';
            END IF;
            RETURN NEW;
        END; $$
    """)
    op.execute("CREATE TRIGGER object_identity_immutable BEFORE UPDATE OR DELETE ON object_inventory FOR EACH ROW EXECUTE FUNCTION protect_object_identity()")


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM object_inventory) THEN
                RAISE EXCEPTION 'preserve object identity and retention records; use configuration rollback';
            END IF;
        END $$
    """)
    op.execute("DROP TABLE object_references")
    op.execute("DROP TABLE object_inventory")
    op.execute("DROP FUNCTION protect_object_identity()")
