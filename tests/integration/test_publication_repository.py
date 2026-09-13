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
    first_account = await repository.create_account(
        workspace_id=ready["workspace_id"],
        platform="fixture",
        account_key="account-a",
        account_type="creator",
        external_account_reference="fixture:account-a",
    )
    second_account = await repository.create_account(
        workspace_id=ready["workspace_id"],
        platform="fixture",
        account_key="account-b",
        account_type="creator",
        external_account_reference="fixture:account-b",
    )
    first = await repository.create_request(
        ready_package_id=ready["ready_package_id"],
        workspace_id=ready["workspace_id"],
        content_program_id=ready["program_id"],
        publisher_account_id=first_account.id,
        idempotency_key="publication-request-a",
    )
    second = await repository.create_request(
        ready_package_id=ready["ready_package_id"],
        workspace_id=ready["workspace_id"],
        content_program_id=ready["program_id"],
        publisher_account_id=second_account.id,
        idempotency_key="publication-request-b",
    )

    assert first.ready_package_id == second.ready_package_id == ready["ready_package_id"]
    assert first.id != second.id


@pytest.mark.asyncio
async def test_repeated_publication_request_returns_original_identity() -> None:
    from test_creative_release_gate_migration import _approved_ready_package

    ready = await _approved_ready_package()
    repository = PublicationRepository(os.environ["TEST_DATABASE_URL"])
    account = await repository.create_account(
        workspace_id=ready["workspace_id"],
        platform="fixture",
        account_key="idempotent-account",
        account_type="creator",
        external_account_reference="fixture:idempotent-account",
    )
    request_arguments = {
        "ready_package_id": ready["ready_package_id"],
        "workspace_id": ready["workspace_id"],
        "content_program_id": ready["program_id"],
        "publisher_account_id": account.id,
        "idempotency_key": "publication-request-idempotent",
    }

    first = await repository.create_request(**request_arguments)
    replay = await repository.create_request(**request_arguments)

    assert replay.id == first.id


@pytest.mark.asyncio
async def test_remote_receipt_is_immutable_after_insert() -> None:
    from test_creative_release_gate_migration import _approved_ready_package

    ready = await _approved_ready_package()
    repository = PublicationRepository(os.environ["TEST_DATABASE_URL"])
    account = await repository.create_account(
        workspace_id=ready["workspace_id"],
        platform="fixture",
        account_key="receipt-account",
        account_type="creator",
        external_account_reference="fixture:receipt-account",
    )
    request = await repository.create_request(
        ready_package_id=ready["ready_package_id"],
        workspace_id=ready["workspace_id"],
        content_program_id=ready["program_id"],
        publisher_account_id=account.id,
        idempotency_key="publication-receipt-request",
    )
    plan = await repository.create_plan(
        publication_request_id=request.id,
        version=1,
        publisher_id="fixture",
        publisher_version="1",
    )
    attempt = await repository.create_attempt(
        publication_plan_id=plan.id,
        attempt_number=1,
        idempotency_key="publication-receipt-attempt",
    )
    receipt = await repository.record_remote_receipt(
        publication_attempt_id=attempt.id,
        publisher_id="fixture",
        remote_id=f"fixture-publication-{attempt.id}",
        state="accepted",
        safe_metadata_hash="f" * 64,
        remote_url="https://fixture.invalid/publications/1",
    )

    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as connection:
        with pytest.raises(
            psycopg.errors.RaiseException,
            match="immutable remote publication receipt",
        ):
            connection.execute(
                "UPDATE remote_publication_receipts SET remote_url = 'changed' WHERE id = %s",
                (receipt.id,),
            )
