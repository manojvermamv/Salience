"""Direct PostgreSQL contracts for the governed-publication migration."""

import os

import psycopg


def test_governed_publication_tables_are_canonical_and_additive() -> None:
    expected_tables = {
        "publisher_accounts",
        "publisher_connections",
        "publisher_capability_profiles",
        "publication_requests",
        "publication_plans",
        "publication_attempts",
        "remote_publication_receipts",
        "publications",
    }
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as connection:
        rows = connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        ).fetchall()

    assert expected_tables.issubset({str(name) for (name,) in rows})
