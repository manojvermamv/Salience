"""Secret-reference-only credential resolution for publisher adapters."""

from __future__ import annotations

from typing import Protocol

from salience.publication.contracts import CredentialLease


class PublisherCredentialResolver(Protocol):
    def lease(self, secret_reference: str, required_scopes: frozenset[str]) -> CredentialLease: ...

