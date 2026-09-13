"""Provider-neutral publication capability selection contracts."""

import pytest

from salience.publication.contracts import PublicationRequest, PublisherCapabilityProfile
from salience.publication.registry import PublisherCapabilityDenied, PublisherRegistry


def _request(*, visibility: str = "private") -> PublicationRequest:
    return PublicationRequest(
        id="request-1",
        workspace_id="workspace-1",
        content_program_id="program-1",
        ready_package_id="ready-1",
        publisher_account_id="account-1",
        platform="fixture",
        destination="fixture-channel-1",
        locale="en",
        territory="US",
        visibility=visibility,
        capability_profile_version=1,
        idempotency_key="publication-request-1",
        approval_reference="approval-1",
    )


def _profile(*, audited_visibilities: tuple[str, ...] = ("private",)) -> PublisherCapabilityProfile:
    return PublisherCapabilityProfile(
        publisher_id="fixture-publisher",
        version="1.0.0",
        platform="fixture",
        account_types=("channel",),
        contract_compatibility={"publication": "1.0"},
        enabled=True,
        audit_state="verified",
        granted_scopes=("publish:create",),
        supported_content_types=("video/mp4",),
        supported_visibilities=audited_visibilities,
        disclosure_support=True,
        scheduling_support=True,
        cancellation_support=True,
        reconciliation_support=True,
        quota_state="available",
        health_state="healthy",
    )


def test_registry_rejects_public_visibility_for_an_unaudited_profile() -> None:
    registry = PublisherRegistry((_profile(),))

    with pytest.raises(PublisherCapabilityDenied, match="visibility"):
        registry.resolve(_request(visibility="public"), account_type="channel", content_type="video/mp4")


def test_registry_selects_an_exact_enabled_compatible_profile() -> None:
    registry = PublisherRegistry((_profile(),))

    selected = registry.resolve(_request(), account_type="channel", content_type="video/mp4")

    assert selected.publisher_id == "fixture-publisher"
    assert selected.version == "1.0.0"
