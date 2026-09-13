"""Private, expiring asset delivery capability contracts."""

from datetime import UTC, datetime, timedelta

import pytest

from salience.publication.delivery import PublicationDelivery


def test_delivery_url_is_short_lived_asset_scoped_and_nonpersistent() -> None:
    issued_at = datetime(2026, 9, 13, tzinfo=UTC)
    receipt = PublicationDelivery("delivery-test-key").create(
        asset_id="asset-1",
        expires_in=timedelta(minutes=5),
        now=issued_at,
    )

    assert receipt.asset_id == "asset-1"
    assert receipt.expires_at == issued_at + timedelta(minutes=5)
    assert receipt.delivery_url.startswith("https://delivery.invalid/v1/assets/asset-1?")
    assert "delivery-test-key" not in repr(receipt)
    assert receipt.canonical_record() == {"asset_id": "asset-1", "expires_at": receipt.expires_at}


def test_delivery_rejects_nonpositive_or_unbounded_expiry() -> None:
    delivery = PublicationDelivery("delivery-test-key")

    with pytest.raises(ValueError, match="positive"):
        delivery.create(asset_id="asset-1", expires_in=timedelta())
    with pytest.raises(ValueError, match="15 minutes"):
        delivery.create(asset_id="asset-1", expires_in=timedelta(minutes=16))
