"""Private, expiring asset delivery capabilities for publisher adapters."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import quote


@dataclass(frozen=True, repr=False)
class PrivateDeliveryReceipt:
    asset_id: str
    expires_at: datetime
    delivery_url: str

    def canonical_record(self) -> dict[str, object]:
        return {"asset_id": self.asset_id, "expires_at": self.expires_at}

    def __repr__(self) -> str:
        return f"PrivateDeliveryReceipt(asset_id={self.asset_id!r}, expires_at={self.expires_at.isoformat()})"


class PublicationDelivery:
    """Mint edge-only, short-lived delivery URLs without storage credentials."""

    _MAX_EXPIRY = timedelta(minutes=15)

    def __init__(self, signing_key: str, *, base_url: str = "https://delivery.invalid") -> None:
        if not signing_key:
            raise ValueError("delivery signing key is required")
        if not base_url.startswith("https://"):
            raise ValueError("delivery base URL must use HTTPS")
        self._signing_key = signing_key.encode()
        self._base_url = base_url.rstrip("/")

    def create(
        self,
        *,
        asset_id: str,
        expires_in: timedelta,
        now: datetime | None = None,
    ) -> PrivateDeliveryReceipt:
        if not asset_id:
            raise ValueError("asset identity is required")
        if expires_in <= timedelta():
            raise ValueError("delivery expiry must be positive")
        if expires_in > self._MAX_EXPIRY:
            raise ValueError("delivery expiry may not exceed 15 minutes")
        issued_at = now or datetime.now(UTC)
        if issued_at.tzinfo is None:
            raise ValueError("delivery issue time must be timezone-aware")
        expires_at = issued_at + expires_in
        nonce = secrets.token_urlsafe(16)
        message = f"{asset_id}:{int(expires_at.timestamp())}:{nonce}".encode()
        signature = base64.urlsafe_b64encode(
            hmac.new(self._signing_key, message, hashlib.sha256).digest()
        ).decode().rstrip("=")
        delivery_url = (
            f"{self._base_url}/v1/assets/{quote(asset_id, safe='')}?"
            f"expires={int(expires_at.timestamp())}&nonce={quote(nonce, safe='')}&signature={signature}"
        )
        return PrivateDeliveryReceipt(asset_id, expires_at, delivery_url)
