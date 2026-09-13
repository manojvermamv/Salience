"""Idempotent canonical publication persistence contracts."""

import os

import psycopg
import pytest

from salience.publication.repository import PublicationRepository


@pytest.mark.asyncio
async def test_ready_package_can_create_multiple_workspace_bound_publication_requests() -> None:
    from test_creative_release_gate_migration import _approved_ready_package

    ready = await _approved_ready_package()
    repository = PublicationRepository(os.environ["TEST_DATABASE_URL"])
    first = await repository.create_request(
        ready_package_id=ready["ready_package_id"],
        workspace_id=ready["workspace_id"],
        content_program_id=ready["program_id"],
        publisher_account_id="account-a",
        idempotency_key="publication-request-a",
    )
    second = await repository.create_request(
        ready_package_id=ready["ready_package_id"],
        workspace_id=ready["workspace_id"],
        content_program_id=ready["program_id"],
        publisher_account_id="account-b",
        idempotency_key="publication-request-b",
    )

    assert first.ready_package_id == second.ready_package_id == ready["ready_package_id"]
    assert first.id != second.id


def test_remote_receipt_is_immutable_after_insert() -> None:
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as connection:
        with pytest.raises(psycopg.errors.UndefinedTable):
            connection.execute("SELECT id FROM remote_publication_receipts")
