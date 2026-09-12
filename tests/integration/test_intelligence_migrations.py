import os
import subprocess
import sys

import asyncpg
import pytest


def _driver_url(database_url: str) -> str:
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


async def list_tables(database_url: str) -> set[str]:
    connection = await asyncpg.connect(_driver_url(database_url))
    try:
        rows = await connection.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
    finally:
        await connection.close()
    return {row["table_name"] for row in rows}


async def list_columns(database_url: str, table_name: str) -> set[str]:
    connection = await asyncpg.connect(_driver_url(database_url))
    try:
        rows = await connection.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = $1",
            table_name,
        )
    finally:
        await connection.close()
    return {row["column_name"] for row in rows}


async def list_constraint_names(database_url: str) -> set[str]:
    connection = await asyncpg.connect(_driver_url(database_url))
    try:
        rows = await connection.fetch(
            "SELECT conname FROM pg_constraint WHERE connamespace = 'public'::regnamespace"
        )
    finally:
        await connection.close()
    return {row["conname"] for row in rows}


@pytest.mark.asyncio
async def test_intelligence_migration_protects_full_brief_lineage() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    migration = apply_migrations(database_url)

    assert migration.returncode == 0, migration.stderr
    assert {
        "research_sources",
        "research_fetches",
        "signals",
        "signal_support",
        "topic_opportunities",
        "strategic_packages",
        "package_evaluations",
        "claims",
        "claim_evidence",
        "content_brief_versions",
        "model_invocations",
    } <= await list_tables(database_url)
    assert "uq_research_fetch_source_resource_window" in await list_constraint_names(
        database_url
    )
    assert {"research_source_id", "research_fetch_id", "source_trust"} <= await list_columns(
        database_url, "research_evidence"
    )
