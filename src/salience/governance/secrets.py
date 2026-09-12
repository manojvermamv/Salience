from dataclasses import dataclass
from typing import Any


class SecretAccessDenied(PermissionError):
    pass


class SecretNotFound(KeyError):
    pass


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
    def __init__(self, values_by_uri: dict[str, str]) -> None:
        self._values_by_uri = dict(values_by_uri)

    def resolve(self, reference: SecretReference, scopes: frozenset[str]) -> SecretValue:
        missing_scopes = reference.required_scopes - scopes
        if missing_scopes:
            raise SecretAccessDenied(f"missing secret scopes: {sorted(missing_scopes)}")
        try:
            return SecretValue(self._values_by_uri[reference.uri])
        except KeyError as error:
            raise SecretNotFound(reference.uri) from error

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
        if isinstance(value, str) and value in known_values:
            return "[REDACTED]"
        return value

