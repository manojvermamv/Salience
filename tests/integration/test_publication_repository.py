"""Idempotent canonical publication persistence contracts."""

import asyncio
import os
from uuid import uuid4

import psycopg
import pytest

from salience.publication.contracts import PublicationRequest
from salience.publication.repository import ImmutablePublicationConflict, PublicationRepository
from salience.workflows.persistence import CanonicalJobStore


async def _active_budget(database_url: str, workspace_id: str, program_id: str) -> str:
    def insert() -> str:
        with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO budgets (workspace_id, content_program_id, name, scope, limit_amount, status)
                VALUES (%s, %s, %s, 'publication', 1.000000, 'active')
                RETURNING id::text
                """,
                (workspace_id, program_id, f"publication-budget-{uuid4()}"),
            )
            return str(cursor.fetchone()[0])

    return await asyncio.to_thread(insert)


@pytest.mark.asyncio
async def test_publication_request_rejects_creative_approval_as_publish_authority() -> None:
    from test_creative_release_gate_migration import _approved_ready_package

    ready = await _approved_ready_package()
    repository = PublicationRepository(os.environ["TEST_DATABASE_URL"])
    account = await repository.create_account(
        workspace_id=ready["workspace_id"],
        platform="fixture",
        account_key="creative-approval-account",
        account_type="creator",
        external_account_reference="fixture:creative-approval",
    )

    with pytest.raises(KeyError, match="publication approval"):
        await repository.create_request(
            ready_package_id=ready["ready_package_id"],
            workspace_id=ready["workspace_id"],
            content_program_id=ready["program_id"],
            publisher_account_id=account.id,
            publication_approval_request_id=ready["ready_package_id"],
            idempotency_key="creative-approval-is-not-publish-approval",
        )


@pytest.mark.asyncio
async def test_current_authorization_fails_closed_without_policy_and_asset_rights_proof() -> None:
    from test_creative_release_gate_migration import _approved_publication_approval, _approved_ready_package

    ready = await _approved_ready_package(publication_proofs=False)
    repository = PublicationRepository(os.environ["TEST_DATABASE_URL"])
    account = await repository.create_account(
        workspace_id=ready["workspace_id"],
        platform="fixture",
        account_key="missing-proof-account",
        account_type="creator",
        external_account_reference="fixture:missing-proof",
    )
    request = await repository.create_request(
        ready_package_id=ready["ready_package_id"],
        workspace_id=ready["workspace_id"],
        content_program_id=ready["program_id"],
        publisher_account_id=account.id,
        publication_approval_request_id=await _approved_publication_approval(ready, account.id),
        idempotency_key="missing-policy-and-rights-proof",
    )

    current = await repository.load_current_authorization(request.id)

    assert current.policy_allowed is False
    assert current.rights_allowed is False


@pytest.mark.asyncio
async def test_ready_package_can_create_multiple_workspace_bound_publication_requests() -> None:
    from test_creative_release_gate_migration import _approved_publication_approval, _approved_ready_package

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
        publication_approval_request_id=await _approved_publication_approval(ready, first_account.id),
        idempotency_key="publication-request-a",
    )
    second = await repository.create_request(
        ready_package_id=ready["ready_package_id"],
        workspace_id=ready["workspace_id"],
        content_program_id=ready["program_id"],
        publisher_account_id=second_account.id,
        publication_approval_request_id=await _approved_publication_approval(ready, second_account.id),
        idempotency_key="publication-request-b",
    )

    assert first.ready_package_id == second.ready_package_id == ready["ready_package_id"]
    assert first.id != second.id


@pytest.mark.asyncio
async def test_repeated_publication_request_returns_original_identity() -> None:
    from test_creative_release_gate_migration import _approved_publication_approval, _approved_ready_package

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
        "publication_approval_request_id": await _approved_publication_approval(ready, account.id),
        "idempotency_key": "publication-request-idempotent",
    }

    first = await repository.create_request(**request_arguments)
    replay = await repository.create_request(**request_arguments)

    assert replay.id == first.id


@pytest.mark.asyncio
async def test_loaded_publication_request_is_the_exact_canonical_effect_input() -> None:
    from test_creative_release_gate_migration import _approved_publication_approval, _approved_ready_package

    ready = await _approved_ready_package()
    repository = PublicationRepository(os.environ["TEST_DATABASE_URL"])
    account = await repository.create_account(
        workspace_id=ready["workspace_id"],
        platform="fixture",
        account_key="canonical-request-account",
        account_type="creator",
        external_account_reference="fixture:canonical-request",
    )
    publication_approval_request_id = await _approved_publication_approval(ready, account.id)
    persisted = await repository.create_request(
        ready_package_id=ready["ready_package_id"],
        workspace_id=ready["workspace_id"],
        content_program_id=ready["program_id"],
        publisher_account_id=account.id,
        publication_approval_request_id=publication_approval_request_id,
        idempotency_key="canonical-request-key",
        platform="fixture",
        destination="fixture://canonical-destination",
        locale="fr-CA",
        territory="CA",
        visibility="private",
        capability_profile_version=1,
    )

    assert await repository.load_request(persisted.id) == PublicationRequest(
        id=persisted.id,
        workspace_id=ready["workspace_id"],
        content_program_id=ready["program_id"],
        ready_package_id=ready["ready_package_id"],
        publisher_account_id=account.id,
        publication_approval_request_id=publication_approval_request_id,
        platform="fixture",
        destination="fixture://canonical-destination",
        locale="fr-CA",
        territory="CA",
        visibility="private",
        capability_profile_version=1,
        idempotency_key="canonical-request-key",
        approval_reference=f"publication-approval:{publication_approval_request_id}",
        publisher_id="fixture-publisher",
    )


@pytest.mark.asyncio
async def test_current_authorization_facts_follow_the_persisted_connection_and_profile() -> None:
    from test_creative_release_gate_migration import _approved_publication_approval, _approved_ready_package

    ready = await _approved_ready_package()
    database_url = os.environ["TEST_DATABASE_URL"]
    repository = PublicationRepository(database_url)
    account = await repository.create_account(
        workspace_id=ready["workspace_id"],
        platform="fixture",
        account_key="authorization-facts-account",
        account_type="creator",
        external_account_reference="fixture:authorization-facts",
    )
    persisted = await repository.create_request(
        ready_package_id=ready["ready_package_id"],
        workspace_id=ready["workspace_id"],
        content_program_id=ready["program_id"],
        publisher_account_id=account.id,
        publication_approval_request_id=await _approved_publication_approval(ready, account.id),
        idempotency_key="authorization-facts-key",
    )

    active = await repository.load_current_authorization(persisted.id)
    assert active.connection_status == "active"
    assert active.connection_scopes == frozenset({"publish:create"})
    assert active.profile.publisher_id == "fixture-publisher"

    with psycopg.connect(database_url) as connection:
        connection.execute(
            "UPDATE publisher_connections SET status = 'revoked' WHERE publisher_account_id = %s",
            (account.id,),
        )

    assert (await repository.load_current_authorization(persisted.id)).connection_status == "revoked"


@pytest.mark.asyncio
async def test_remote_receipt_is_immutable_after_insert() -> None:
    from test_creative_release_gate_migration import _approved_publication_approval, _approved_ready_package

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
        publication_approval_request_id=await _approved_publication_approval(ready, account.id),
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


@pytest.mark.asyncio
async def test_publication_schedule_persists_exact_request_plan_and_job_schedule() -> None:
    from test_creative_release_gate_migration import _approved_publication_approval, _approved_ready_package

    ready = await _approved_ready_package()
    database_url = os.environ["TEST_DATABASE_URL"]
    repository = PublicationRepository(database_url)
    account = await repository.create_account(
        workspace_id=ready["workspace_id"],
        platform="fixture",
        account_key="schedule-account",
        account_type="creator",
        external_account_reference="fixture:schedule-account",
    )
    request = await repository.create_request(
        ready_package_id=ready["ready_package_id"],
        workspace_id=ready["workspace_id"],
        content_program_id=ready["program_id"],
        publisher_account_id=account.id,
        publication_approval_request_id=await _approved_publication_approval(ready, account.id),
        idempotency_key="publication-schedule-request",
    )
    plan = await repository.create_plan(
        publication_request_id=request.id,
        version=1,
        publisher_id="fixture-publisher",
        publisher_version="1",
    )
    job_schedule = await CanonicalJobStore(database_url).create_schedule(
        workspace_id=ready["workspace_id"],
        content_program_id=ready["program_id"],
        name="weekday-private-release",
        schedule_expression="every 86400s",
        job_type="governed_publication",
        payload={"publication_request_id": request.id, "publication_plan_id": plan.id},
    )
    budget_id = await _active_budget(database_url, ready["workspace_id"], ready["program_id"])

    first = await repository.create_schedule(
        publication_request_id=request.id,
        publication_plan_id=plan.id,
        job_schedule_id=job_schedule.schedule_id,
        budget_id=budget_id,
        version=1,
        schedule_fingerprint="a" * 64,
    )
    replay = await repository.create_schedule(
        publication_request_id=request.id,
        publication_plan_id=plan.id,
        job_schedule_id=job_schedule.schedule_id,
        budget_id=budget_id,
        version=1,
        schedule_fingerprint="a" * 64,
    )

    assert replay.id == first.id
    assert first.publication_plan_id == plan.id
    with pytest.raises(ImmutablePublicationConflict):
        await repository.create_schedule(
            publication_request_id=request.id,
            publication_plan_id=plan.id,
            job_schedule_id=job_schedule.schedule_id,
            budget_id=budget_id,
            version=1,
            schedule_fingerprint="b" * 64,
        )


@pytest.mark.asyncio
async def test_scheduled_execution_loads_only_its_exact_immutable_request_and_plan() -> None:
    from test_creative_release_gate_migration import _approved_publication_approval, _approved_ready_package

    ready = await _approved_ready_package()
    database_url = os.environ["TEST_DATABASE_URL"]
    repository = PublicationRepository(database_url)
    account = await repository.create_account(
        workspace_id=ready["workspace_id"],
        platform="fixture",
        account_key="scheduled-execution-account",
        account_type="creator",
        external_account_reference="fixture:scheduled-execution",
    )
    request = await repository.create_request(
        ready_package_id=ready["ready_package_id"],
        workspace_id=ready["workspace_id"],
        content_program_id=ready["program_id"],
        publisher_account_id=account.id,
        publication_approval_request_id=await _approved_publication_approval(ready, account.id),
        idempotency_key="scheduled-execution-key",
        destination="fixture://scheduled-canonical",
    )
    plan = await repository.create_plan(
        publication_request_id=request.id,
        version=1,
        publisher_id="fixture-publisher",
        publisher_version="1",
    )

    job_schedule = await CanonicalJobStore(database_url).create_schedule(
        workspace_id=ready["workspace_id"],
        content_program_id=ready["program_id"],
        name=f"scheduled-execution-{uuid4()}",
        schedule_expression="every 60s",
        job_type="governed_publication",
        payload={"publication_request_id": request.id, "publication_plan_id": plan.id},
    )
    schedule = await repository.create_schedule(
        publication_request_id=request.id,
        publication_plan_id=plan.id,
        job_schedule_id=job_schedule.schedule_id,
        budget_id=await _active_budget(database_url, ready["workspace_id"], ready["program_id"]),
        version=1,
        schedule_fingerprint="c" * 64,
    )

    execution = await repository.load_scheduled_execution(schedule.id)

    assert execution.request.id == request.id
    assert execution.request.destination == "fixture://scheduled-canonical"
    assert execution.plan_id == plan.id
    with pytest.raises(KeyError):
        await repository.load_scheduled_execution(str(uuid4()))
