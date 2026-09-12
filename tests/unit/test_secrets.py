import pytest

from salience.governance.secrets import (
    SecretAccessDenied,
    SecretReference,
    SecretResolver,
)


def test_secret_resolution_requires_scopes_and_never_reprs_the_value() -> None:
    resolver = SecretResolver({"env://PUBLISH_TOKEN": "very-secret"})
    reference = SecretReference(
        uri="env://PUBLISH_TOKEN",
        required_scopes=frozenset({"publish.write"}),
    )

    with pytest.raises(SecretAccessDenied):
        resolver.resolve(reference, frozenset())

    secret = resolver.resolve(reference, frozenset({"publish.write"}))
    assert secret.reveal() == "very-secret"
    assert "very-secret" not in repr(secret)
    assert resolver.redact({"token": "very-secret"}) == {"token": "[REDACTED]"}

