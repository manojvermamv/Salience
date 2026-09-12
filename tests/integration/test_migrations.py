import asyncio
import os
import subprocess
import sys
from uuid import uuid4

import asyncpg
import pytest


async def list_tables(database_url: str) -> set[str]:
    connection = await asyncpg.connect(driver_database_url(database_url))
    try:
        rows = await connection.fetch(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            """
        )
    finally:
        await connection.close()

    return {row["table_name"] for row in rows}


async def list_columns(database_url: str, table_name: str) -> set[str]:
    connection = await asyncpg.connect(driver_database_url(database_url))
    try:
        rows = await connection.fetch(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = $1
            """,
            table_name,
        )
    finally:
        await connection.close()

    return {row["column_name"] for row in rows}


async def list_constraint_names(database_url: str) -> set[str]:
    connection = await asyncpg.connect(driver_database_url(database_url))
    try:
        rows = await connection.fetch(
            """
            SELECT conname
            FROM pg_constraint
            WHERE connamespace = 'public'::regnamespace
            """
        )
    finally:
        await connection.close()

    return {row["conname"] for row in rows}


async def list_index_names(database_url: str) -> set[str]:
    connection = await asyncpg.connect(driver_database_url(database_url))
    try:
        rows = await connection.fetch(
            """
            SELECT indexname
            FROM pg_indexes
            WHERE schemaname = 'public'
            """
        )
    finally:
        await connection.close()

    return {row["indexname"] for row in rows}


def driver_database_url(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


def apply_migrations(database_url: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-x",
            f"database_url={database_url}",
            "upgrade",
            "head",
        ],
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.asyncio
async def test_initial_migration_creates_workspace_program_and_job_tables() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    migration = apply_migrations(database_url)

    assert migration.returncode == 0, migration.stderr
    tables = await list_tables(database_url)
    assert {
        "workspaces",
        "content_programs",
        "jobs",
        "job_checkpoints",
        "external_effects",
        "audit_events",
        "provenance_records",
        "secret_references",
        "budget_reservations",
        "cost_ledger_entries",
        "policy_versions",
        "approval_requests",
        "plugin_versions",
    } <= tables

    assert {
        "tenant_id",
        "data_classification",
        "retention_policy",
        "delete_after",
        "domain_policy_ref",
    } <= await list_columns(database_url, "workspaces")
    assert {
        "workflow_run_id",
        "idempotency_key",
        "trace_id",
        "span_id",
        "dry_run",
    } <= await list_columns(database_url, "jobs")
    assert {"c2pa_manifest", "origin_type", "verification_status"} <= await list_columns(
        database_url, "provenance_records"
    )
    assert "value" not in await list_columns(database_url, "secret_references")

    assert {
        "uq_external_identity_workspace_system_external_id",
        "uq_external_effects_workspace_idempotency",
        "uq_audit_events_run_sequence",
        "uq_budget_reservations_budget_key",
    } <= await list_constraint_names(database_url)
    assert {
        "ix_jobs_scheduled_for",
        "ix_jobs_trace_id",
        "ix_external_effects_job_id",
        "ix_audit_events_trace_id",
    } <= await list_index_names(database_url)


@pytest.mark.asyncio
async def test_initial_migration_enforces_monotonic_audit_sequences() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    migration = apply_migrations(database_url)
    assert migration.returncode == 0, migration.stderr

    workspace_id = uuid4()
    run_id = f"audit-contract-{uuid4()}"
    connection = await asyncpg.connect(driver_database_url(database_url))
    try:
        await connection.execute(
            """
            INSERT INTO workspaces (id, slug, display_name)
            VALUES ($1, $2, $3)
            """,
            workspace_id,
            f"workspace-{workspace_id}",
            "Audit contract workspace",
        )
        for sequence_no in (1, 2):
            await connection.execute(
                """
                INSERT INTO audit_events (
                    workspace_id, run_id, sequence_no, actor_kind, action,
                    resource_type, outcome, trace_id
                ) VALUES ($1, $2, $3, 'system', 'contract.test', 'workspace', 'allowed', 'trace')
                """,
                workspace_id,
                run_id,
                sequence_no,
            )
        with pytest.raises(asyncpg.RaiseError, match="audit sequence must increase"):
            await connection.execute(
                """
                INSERT INTO audit_events (
                    workspace_id, run_id, sequence_no, actor_kind, action,
                    resource_type, outcome, trace_id
                ) VALUES ($1, $2, 2, 'system', 'contract.test', 'workspace', 'allowed', 'trace')
                """,
                workspace_id,
                run_id,
            )
    finally:
        await connection.execute("DELETE FROM audit_events WHERE workspace_id = $1", workspace_id)
        await connection.execute("DELETE FROM workspaces WHERE id = $1", workspace_id)
        await connection.close()
