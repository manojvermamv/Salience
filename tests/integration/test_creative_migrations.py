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


async def table_names(database_url: str) -> set[str]:
    connection = await asyncpg.connect(_driver_url(database_url))
    try:
        rows = await connection.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
    finally:
        await connection.close()
    return {row["table_name"] for row in rows}


async def constraint_names(database_url: str) -> set[str]:
    connection = await asyncpg.connect(_driver_url(database_url))
    try:
        rows = await connection.fetch(
            "SELECT conname FROM pg_constraint WHERE connamespace = 'public'::regnamespace"
        )
    finally:
        await connection.close()
    return {row["conname"] for row in rows}


@pytest.mark.asyncio
async def test_creative_migration_anchors_scripts_assets_and_final_packages() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    migration = apply_migrations(database_url)

    assert migration.returncode == 0, migration.stderr
    assert {
        "script_versions",
        "creative_briefs",
        "storyboards",
        "shot_plans",
        "creative_jobs",
        "provider_jobs",
        "assets",
        "asset_variants",
        "asset_relationships",
        "caption_tracks",
        "compositions",
        "asset_licenses",
        "consent_records",
        "likeness_identities",
        "voice_identities",
        "usage_restrictions",
        "asset_provenance",
        "platform_profiles",
        "distribution_packages",
        "distribution_package_variants",
        "title_thumbnail_candidates",
        "localizations",
        "originality_evaluations",
        "synthetic_media_disclosures",
        "ready_to_publish_packages",
    } <= await table_names(database_url)
    constraints = await constraint_names(database_url)
    assert {
        "uq_script_versions_program_key_version",
        "uq_creative_jobs_program_request_fingerprint",
        "uq_provider_jobs_provider_external",
        "uq_assets_program_hash_media_type",
        "uq_platform_profiles_program_key_version",
        "uq_distribution_packages_program_key_version",
        "uq_ready_package_program_key_version",
    } <= constraints
