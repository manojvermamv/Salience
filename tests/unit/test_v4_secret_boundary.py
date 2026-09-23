from datetime import datetime, timedelta, timezone
import pickle
from uuid import uuid4

import pytest

from salience.governance.secrets import SecretAccessDenied, SecretPolicy, SecretResolver


def test_lease_pins_scope_destination_audience_provider_and_expiry():
    now = datetime.now(timezone.utc)
    workspace = uuid4()
    policy = SecretPolicy(workspace_id=workspace, destination="https://fixture.invalid", audience="fixture-mcp", provider_id="fixture", provider_version="1.0", required_scopes=frozenset({"fixture:read"}), expires_at=now + timedelta(seconds=5))
    clock = [now]
    resolver = SecretResolver({"env://FIXTURE": "secret-canary"}, policies_by_uri={"env://FIXTURE": policy}, clock=lambda: clock[0])
    context = {"workspace_id": workspace, "destination": policy.destination, "audience": policy.audience, "provider_id": "fixture", "provider_version": "1.0", "scopes": frozenset({"fixture:read"})}
    lease = resolver.resolve_scoped("env://FIXTURE", **context)
    assert lease.reveal() == "secret-canary"
    assert "secret-canary" not in repr(lease)
    with pytest.raises(TypeError):
        pickle.dumps(lease)
    for mismatch in [{"workspace_id": uuid4()}, {"destination": "https://other.invalid"}, {"audience": "other"}, {"provider_id": "fallback"}, {"provider_version": "2.0"}, {"scopes": frozenset()}, {"scopes": frozenset({"fixture:read", "fixture:write"})}]:
        with pytest.raises(SecretAccessDenied):
            resolver.resolve_scoped("env://FIXTURE", **(context | mismatch))
    clock[0] = now + timedelta(seconds=5)
    with pytest.raises(SecretAccessDenied):
        lease.reveal()
    with pytest.raises(SecretAccessDenied):
        resolver.resolve_scoped("env://FIXTURE", **context)
    clock[0] = now
    resolver.revoke("env://FIXTURE")
    with pytest.raises(SecretAccessDenied):
        lease.reveal()
    assert resolver.redact({"message": "Bearer secret-canary"}) == {"message": "Bearer [REDACTED]"}


def test_scoped_secret_cannot_bypass_policy_using_legacy_reference():
    from salience.governance.secrets import SecretReference

    resolver = SecretResolver({"env://FIXTURE": "secret-canary"}, policies_by_uri={})
    with pytest.raises(SecretAccessDenied):
        resolver.resolve(SecretReference("env://FIXTURE", frozenset()), frozenset())
