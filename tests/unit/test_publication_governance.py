"""Fail-closed reauthorization contracts for publication effects."""

from dataclasses import replace

import pytest

from salience.publication.contracts import PublicationRequest, PublisherCapabilityProfile
from salience.publication.governance import PublicationAuthorizationContext, PublicationAuthorizer


def _request() -> PublicationRequest:
    return PublicationRequest(
        id="publication-request-1",
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
        idempotency_key="publication-request-key",
        approval_reference="approval-1",
        publisher_id="fixture-publisher",
    )


def _profile() -> PublisherCapabilityProfile:
    return PublisherCapabilityProfile(
        publisher_id="fixture-publisher",
        version="1",
        platform="fixture",
        account_types=("creator",),
        contract_compatibility={"publication": "1.0"},
        enabled=True,
        audit_state="verified",
        granted_scopes=("publish:create",),
        supported_content_types=("video",),
        supported_visibilities=("private",),
        disclosure_support=True,
        scheduling_support=True,
        cancellation_support=True,
        reconciliation_support=True,
        quota_state="available",
        health_state="healthy",
    )


def _context() -> PublicationAuthorizationContext:
    return PublicationAuthorizationContext(
        request=_request(),
        profile=_profile(),
        ready_package_id="ready-package-1",
        ready_package_workspace_id="workspace-1",
        ready_package_program_id="program-1",
        ready_package_approval_state="approved",
        account_workspace_id="workspace-1",
        connection_account_id="publisher-account-1",
        account_type="creator",
        account_status="active",
        connection_status="active",
        connection_scopes=frozenset({"publish:create"}),
        content_type="video",
        authorized_destination="fixture://account-1",
        authorized_locale="en",
        authorized_territory="global",
        authorized_visibility="private",
        policy_allowed=True,
        rights_allowed=True,
        disclosure_allowed=True,
        publishing_approval_state="approved",
        budget_status="reserved",
        rate_quota_available=True,
        capability_profile_version=1,
    )


@pytest.mark.asyncio
async def test_reauthorization_denies_revoked_connection_before_submission() -> None:
    decision = await PublicationAuthorizer().reauthorize(
        replace(_context(), connection_status="revoked")
    )

    assert decision.allowed is False
    assert "connection_status" in decision.reasons


@pytest.mark.asyncio
async def test_reauthorization_requires_exact_workspace_and_budget_reservation() -> None:
    decision = await PublicationAuthorizer().reauthorize(
        replace(
            _context(),
            account_workspace_id="other-workspace",
            budget_status="pending_actual",
        )
    )

    assert decision.allowed is False
    assert {"account_workspace", "budget"} <= set(decision.reasons)


@pytest.mark.asyncio
async def test_reauthorization_denies_a_connection_bound_to_a_different_account() -> None:
    decision = await PublicationAuthorizer().reauthorize(
        replace(_context(), connection_account_id="other-publisher-account")
    )

    assert decision.allowed is False
    assert "connection_account" in decision.reasons


@pytest.mark.asyncio
async def test_reauthorization_denies_destination_coordinates_outside_current_authority() -> None:
    decision = await PublicationAuthorizer().reauthorize(
        replace(_context(), authorized_territory="US")
    )

    assert decision.allowed is False
    assert "territory" in decision.reasons


@pytest.mark.asyncio
async def test_reauthorization_allows_only_a_complete_current_governed_context() -> None:
    decision = await PublicationAuthorizer().reauthorize(_context())

    assert decision.allowed is True
    assert decision.reasons == ()
