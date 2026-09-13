"""Deterministic provider-neutral publisher fixture contracts."""

from datetime import UTC, datetime, timedelta

import pytest

from salience.publication.contracts import CredentialLease, PublicationRequest, PublisherAdapter
from salience.publication.providers import FixturePublisherAdapter, FixturePublisherError


def _request(idempotency_key: str = "publication-effect-1") -> PublicationRequest:
    return PublicationRequest(
        id="publication-attempt-1",
        workspace_id="workspace-1",
        content_program_id="program-1",
        ready_package_id="ready-package-1",
        publisher_account_id="publisher-account-1",
        platform="fixture",
        destination="fixture://account-1",
        locale="en",
        territory="global",
        visibility="private",
        capability_profile_version=1,
        idempotency_key=idempotency_key,
        approval_reference="approval-1",
        publisher_id="fixture-publisher",
    )


def _lease() -> CredentialLease:
    return CredentialLease(
        "fixture-only-lease",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        granted_scopes=frozenset({"publish:create"}),
    )


@pytest.mark.asyncio
async def test_fixture_crash_after_acceptance_reconciles_one_remote_post() -> None:
    provider = FixturePublisherAdapter(scenario="crash_after_acceptance")
    request = _request()

    accepted = await provider.submit(request, _lease())
    reconciled = await provider.reconcile(request.idempotency_key)

    assert reconciled is not None
    assert reconciled.remote_id == accepted.remote_id
    assert provider.submit_count == 1


@pytest.mark.asyncio
async def test_fixture_denies_quota_before_creating_a_remote_post() -> None:
    provider = FixturePublisherAdapter(scenario="quota")

    with pytest.raises(FixturePublisherError, match="quota"):
        await provider.submit(_request("quota-effect"), _lease())

    assert provider.submit_count == 0


@pytest.mark.asyncio
async def test_fixture_duplicate_signed_webhook_has_one_stable_delivery_identity() -> None:
    provider = FixturePublisherAdapter()
    accepted = await provider.submit(_request(), _lease())
    payload = provider.signed_webhook(accepted, state="published", delivery_identity="delivery-1")

    first = await provider.verify_webhook(payload)
    replay = await provider.verify_webhook(payload)

    assert first == replay
    assert first.delivery_identity == "delivery-1"
    assert first.state == "published"


def test_fixture_satisfies_the_complete_replaceable_publisher_contract() -> None:
    assert isinstance(FixturePublisherAdapter(), PublisherAdapter)
