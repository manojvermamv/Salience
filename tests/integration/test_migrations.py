import asyncio
import os
import subprocess
import sys

import asyncpg
import pytest


async def list_tables(database_url: str) -> set[str]:
    connection = await asyncpg.connect(database_url)
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


@pytest.mark.asyncio
async def test_initial_migration_creates_workspace_program_and_job_tables() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    migration = subprocess.run(
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

    assert migration.returncode == 0, migration.stderr
    tables = await list_tables(database_url)
    assert {"workspaces", "content_programs", "jobs", "job_checkpoints"} <= tables
