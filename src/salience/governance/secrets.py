from dataclasses import dataclass
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any
from uuid import UUID


class SecretAccessDenied(PermissionError):
    pass


class SecretNotFound(KeyError):
    pass


@dataclass(frozen=True)
class SecretPolicy:
    workspace_id: UUID
    destination: str
    audience: str
    provider_id: str
    provider_version: str
    required_scopes: frozenset[str]
    expires_at: datetime

    def __post_init__(self) -> None:
        if not self.destination.startswith("https://") or not all((self.audience, self.provider_id, self.provider_version, self.required_scopes)) or self.expires_at.tzinfo is None:
            raise ValueError("explicit destination, audience, provider pin, scopes and aware expiry are required")


class ScopedSecretLease:
    __slots__ = ("_resolve",)

    def __init__(self, resolve: Callable[[], str]) -> None:
        self._resolve = resolve

    def reveal(self) -> str:
        return self._resolve()

    def __repr__(self) -> str:
        return "ScopedSecretLease([REDACTED])"

    __str__ = __repr__

    def __reduce__(self):
        raise TypeError("credential leases must not be serialized")

    def model_dump(self, *args, **kwargs):
        raise TypeError("credential leases must not be serialized")


@dataclass(frozen=True)
class SecretReference:
    uri: str
    required_scopes: frozenset[str]


@dataclass(frozen=True, repr=False)
class SecretValue:
    _value: str

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "SecretValue([REDACTED])"

    def __str__(self) -> str:
        return "[REDACTED]"


class SecretResolver:
    def __init__(self, values_by_uri: dict[str, str], *, policies_by_uri: Mapping[str, SecretPolicy] | None = None, clock: Callable[[], datetime] | None = None) -> None:
        self._values_by_uri = dict(values_by_uri)
        self._policies = dict(policies_by_uri) if policies_by_uri is not None else None
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def resolve(self, reference: SecretReference, scopes: frozenset[str]) -> SecretValue:
        if self._policies is not None:
            raise SecretAccessDenied("scoped resolver requires complete edge context")
        missing_scopes = reference.required_scopes - scopes
        if missing_scopes:
            raise SecretAccessDenied(f"missing secret scopes: {sorted(missing_scopes)}")
        try:
            return SecretValue(self._values_by_uri[reference.uri])
        except KeyError as error:
            raise SecretNotFound(reference.uri) from error

    def resolve_scoped(self, uri: str, *, workspace_id: UUID, destination: str, audience: str, provider_id: str, provider_version: str, scopes: frozenset[str]) -> ScopedSecretLease:
        policy = (self._policies or {}).get(uri)

        def reveal() -> str:
            current = (self._policies or {}).get(uri)
            if policy is None or current != policy or self._clock() >= policy.expires_at:
                raise SecretAccessDenied("credential policy is missing, revoked or expired")
            if (workspace_id, destination, audience, provider_id, provider_version) != (policy.workspace_id, policy.destination, policy.audience, policy.provider_id, policy.provider_version) or policy.required_scopes != scopes:
                raise SecretAccessDenied("credential context is not authorized")
            try:
                return self._values_by_uri[uri]
            except KeyError as error:
                raise SecretNotFound(uri) from error

        reveal()
        return ScopedSecretLease(reveal)

    def revoke(self, uri: str) -> None:
        if self._policies is not None:
            self._policies.pop(uri, None)

    def redact(self, value: Any) -> Any:
        known_values = frozenset(self._values_by_uri.values())
        return self._redact(value, known_values)

    def _redact(self, value: Any, known_values: frozenset[str]) -> Any:
        if isinstance(value, dict):
            return {key: self._redact(nested_value, known_values) for key, nested_value in value.items()}
        if isinstance(value, list):
            return [self._redact(item, known_values) for item in value]
        if isinstance(value, tuple):
            return tuple(self._redact(item, known_values) for item in value)
        if isinstance(value, str):
            for secret in sorted(filter(None, known_values), key=len, reverse=True):
                value = value.replace(secret, "[REDACTED]")
        return value
