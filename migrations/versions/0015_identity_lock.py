"""Delegate only the identity read lock, not authority mutation."""

from alembic import op


revision = "0015_identity_lock"
down_revision = "0014_object_inventory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION public.p0_lock_identity(expected_issuer text, expected_subject text, expected_workspace uuid)
        RETURNS TABLE(id uuid, revision bigint)
        LANGUAGE sql VOLATILE SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $$
            SELECT identity.id, identity.revision
            FROM public.identity_subjects AS identity
            WHERE identity.issuer=expected_issuer AND identity.subject=expected_subject
              AND identity.workspace_id=expected_workspace AND identity.enabled
              AND identity.expires_at > clock_timestamp()
              AND EXISTS (SELECT 1 FROM public.workspaces
                          WHERE workspaces.id=identity.workspace_id AND workspaces.status='active')
            FOR SHARE OF identity
        $$;
    """)
    op.execute("REVOKE ALL ON FUNCTION public.p0_lock_identity(text,text,uuid) FROM PUBLIC")


def downgrade() -> None:
    op.execute("DROP FUNCTION public.p0_lock_identity(text,text,uuid)")
