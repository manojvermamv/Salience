"""Delegate a scoped program read lock without granting program mutation."""

from alembic import op


revision = "0022_program_policy_lock"
down_revision = "0021_cycle_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION public.v4_lock_program(expected_program uuid, expected_workspace uuid)
        RETURNS TABLE(id uuid) LANGUAGE sql VOLATILE SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp AS $$
            SELECT program.id FROM public.content_programs AS program
            WHERE program.id=expected_program AND program.workspace_id=expected_workspace
              AND program.status='active' AND program.dry_run_default
            FOR SHARE OF program
        $$
    """)
    op.execute("REVOKE ALL ON FUNCTION public.v4_lock_program(uuid,uuid) FROM PUBLIC")
    op.execute("CREATE INDEX v4_request_event_lookup ON v4_cycle_requests (goal_id,(payload->>'event_id')) WHERE origin='event'")


def downgrade() -> None:
    op.execute("DROP INDEX v4_request_event_lookup")
    op.execute("DROP FUNCTION public.v4_lock_program(uuid,uuid)")
