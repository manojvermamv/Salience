"""Owned publication DTO contracts."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from salience.publication.contracts import (
    CredentialLease,
    PublicationRequest,
    PublisherAccount,
    PublisherConnection,
)


def test_publication_request_requires_an_explicit_workspace_bound_account() -> None:
    account = PublisherAccount(
        id="account-1",
        workspace_id="workspace-1",
        platform="fixture",
        account_type="channel",
        external_account_reference="fixture-channel-1",
    )

    with pytest.raises(ValidationError, match="publisher_account_id"):
        PublicationRequest(
            id="request-1",
            workspace_id=account.workspace_id,
            content_program_id="program-1",
            ready_package_id="ready-1",
            publisher_account_id="",
            platform="fixture",
            destination="fixture-channel-1",
            locale="en",
            territory="US",
            visibility="private",
            capability_profile_version=1,
            idempotency_key="publication-request-1",
            approval_reference="approval-1",
        )


def test_connection_exposes_only_a_secret_reference_and_scope_facts() -> None:
    connection = PublisherConnection(
        id="connection-1",
        publisher_account_id="account-1",
        version=1,
        secret_reference="secret://publisher/fixture-channel-1",
        required_scopes=("publish:create",),
        granted_scopes=("publish:create",),
        status="active",
    )

    payload = connection.model_dump(mode="json")
    assert payload["secret_reference"] == "secret://publisher/fixture-channel-1"
    assert "access_token" not in payload
    assert "refresh_token" not in payload


def test_credential_lease_is_redacted_and_cannot_be_serialized() -> None:
    lease = CredentialLease(
        "secret-token-value",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        granted_scopes=frozenset({"publish:create"}),
    )

    assert "secret-token-value" not in repr(lease)
    assert "secret-token-value" not in str(lease)
    with pytest.raises(TypeError, match="must not be serialized"):
        lease.model_dump()
