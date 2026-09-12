import os

import psycopg
def test_agent_migration_creates_runtime_neutral_canonical_tables() -> None:
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public'
                """
            )
            tables = {row[0] for row in cursor.fetchall()}

    assert {
        "agent_versions",
        "agent_runs",
        "agent_delegations",
        "team_versions",
        "team_members",
        "agent_events",
    } <= tables
