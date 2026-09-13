"""Direct PostgreSQL contracts for the governed-publication migration."""

import asyncio
import os
from uuid import uuid4

import psycopg
import pytest

from salience.publication.repository import PublicationRepository
from salience.workflows.persistence import CanonicalJobStore


def _column_names(connection: psycopg.Connection, table_name: str) -> set[str]:
    rows = connection.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        """,
        (table_name,),
    ).fetchall()
    return {str(name) for (name,) in rows}


def _immutable_trigger_tables(connection: psycopg.Connection) -> set[str]:
    rows = connection.execute(
        """
        SELECT relation.relname
        FROM pg_trigger trigger
        JOIN pg_class relation ON relation.oid = trigger.tgrelid
        WHERE trigger.tgname LIKE 'trg_publication_%_immutable'
          AND NOT trigger.tgisinternal
        """
    ).fetchall()
    return {str(name) for (name,) in rows}


def test_governed_publication_tables_are_canonical_and_additive() -> None:
    expected_tables = {
        "publisher_accounts",
        "publisher_connections",
        "publisher_capability_profiles",
        "publication_requests",
        "publication_plans",
        "publication_schedules",
        "publication_attempts",
        "publication_status_events",
        "publisher_webhook_receipts",
        "remote_publication_receipts",
        "publications",
    }
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as connection:
        rows = connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        ).fetchall()

    assert expected_tables.issubset({str(name) for (name,) in rows})


def test_publication_hardening_adds_distinct_approval_budget_and_immutable_decisions() -> None:
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as connection:
        assert "publication_approval_request_id" in _column_names(
            connection, "publication_requests"
        )
        assert "budget_id" in _column_names(connection, "publication_schedules")
        assert {"publication_plans", "publication_schedules", "publication_attempts"}.issubset(
            _immutable_trigger_tables(connection)
        )


@pytest.mark.asyncio
async def test_publication_plan_schedule_and_attempt_reject_direct_mutation() -> None:
    from test_creative_release_gate_migration import _approved_publication_approval, _approved_ready_package

    ready = await _approved_ready_package()
    repository = PublicationRepository(os.environ["TEST_DATABASE_URL"])
    account = await repository.create_account(
        workspace_id=ready["workspace_id"],
        platform="fixture",
        account_key=f"immutable-{uuid4()}",
        account_type="creator",
        external_account_reference=f"fixture:immutable:{uuid4()}",
    )
    request = await repository.create_request(
        ready_package_id=ready["ready_package_id"],
        workspace_id=ready["workspace_id"],
        content_program_id=ready["program_id"],
        publisher_account_id=account.id,
        publication_approval_request_id=await _approved_publication_approval(ready, account.id),
        idempotency_key=f"immutable-{uuid4()}",
    )
    plan = await repository.create_plan(
        publication_request_id=request.id,
        version=1,
        publisher_id="fixture-publisher",
        publisher_version="1",
    )
    attempt = await repository.create_attempt(
        publication_plan_id=plan.id,
        attempt_number=1,
        idempotency_key=f"immutable-attempt-{uuid4()}",
    )
    store = CanonicalJobStore(os.environ["TEST_DATABASE_URL"])
    job_schedule = await store.create_schedule(
        workspace_id=ready["workspace_id"],
        content_program_id=ready["program_id"],
        name=f"immutable-{uuid4()}",
        schedule_expression="every 60s",
        job_type="governed_publication",
        payload={"publication_request_id": request.id, "publication_plan_id": plan.id},
    )
    budget_id = await _active_budget(
        os.environ["TEST_DATABASE_URL"], ready["workspace_id"], ready["program_id"]
    )
    schedule = await repository.create_schedule(
        publication_request_id=request.id,
        publication_plan_id=plan.id,
        job_schedule_id=job_schedule.schedule_id,
        budget_id=budget_id,
        version=1,
        schedule_fingerprint="a" * 64,
    )

    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as connection:
        for statement, identifier, message in (
            ("UPDATE publication_plans SET publisher_id = 'other' WHERE id = %s", plan.id, "plan"),
            (
                "UPDATE publication_schedules SET status = 'cancelled' WHERE id = %s",
                schedule.id,
                "schedule",
            ),
            ("UPDATE publication_attempts SET state = 'accepted' WHERE id = %s", attempt.id, "attempt"),
        ):
            with pytest.raises(
                psycopg.errors.RaiseException, match=f"immutable publication {message}"
            ):
                with connection.transaction():
                    connection.execute(statement, (identifier,))


async def _active_budget(database_url: str, workspace_id: str, program_id: str) -> str:
    def insert() -> str:
        with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO budgets (workspace_id, content_program_id, name, scope, limit_amount, status)
                VALUES (%s, %s, %s, 'publication', 1.000000, 'active')
                RETURNING id::text
                """,
                (workspace_id, program_id, f"immutable-budget-{uuid4()}"),
            )
            return str(cursor.fetchone()[0])

    return await asyncio.to_thread(insert)
