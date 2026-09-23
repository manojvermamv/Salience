import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from urllib.parse import urlsplit, urlunsplit

from cryptography.hazmat.primitives.asymmetric import rsa
import jwt
import psycopg
import pytest
from fastapi.testclient import TestClient

from salience.api.p0 import create_p0_app


@pytest.fixture
def boundary():
    database_url = os.environ["TEST_DATABASE_URL"]
    workspace_id = uuid4()
    subject_id = uuid4()
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with psycopg.connect(database_url) as connection:
        connection.execute("INSERT INTO workspaces (id, slug, display_name, status, data_classification, jurisdiction_refs, attributes) VALUES (%s,%s,'P0','active','internal','{}','{}')", (workspace_id, f"p0-{workspace_id}"))
        connection.execute("INSERT INTO identity_subjects (id, workspace_id, issuer, subject, expires_at) VALUES (%s,%s,'https://fixture.invalid',%s,now() + interval '1 hour')", (subject_id, workspace_id, str(subject_id)))
        connection.execute("INSERT INTO permission_grants (workspace_id, principal_type, principal_id, scope, effect, constraints, expires_at) VALUES (%s,'identity',%s,'control:read','allow','{}',now() + interval '1 hour')", (workspace_id, str(subject_id)))
    app = create_p0_app(database_url=database_url, workspace_id=workspace_id, issuer="https://fixture.invalid", audience="salience-p0", public_key=private_key.public_key())
    now = datetime.now(timezone.utc)
    claims = {"iss": "https://fixture.invalid", "aud": "salience-p0", "sub": str(subject_id), "iat": now, "nbf": now, "exp": now + timedelta(minutes=5)}
    with TestClient(app) as client:
        yield client, private_key, claims, workspace_id, subject_id, database_url


def headers(key, claims):
    return {"Authorization": "Bearer " + jwt.encode(claims, key, algorithm="RS256"), "X-Salience-Scopes": "control:read,control:write,admin:*"}


@pytest.fixture
def restricted_boundary(boundary):
    _, key, claims, workspace, subject, database = boundary
    role = "p0_reader_" + uuid4().hex
    password = uuid4().hex
    with psycopg.connect(database, autocommit=True) as admin:
        admin.execute(psycopg.sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS").format(psycopg.sql.Identifier(role), psycopg.sql.Literal(password)))
        try:
            admin.execute(psycopg.sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(psycopg.sql.Identifier(role)))
            admin.execute(psycopg.sql.SQL("GRANT SELECT ON workspaces, identity_subjects, permission_grants, jobs TO {}").format(psycopg.sql.Identifier(role)))
            admin.execute(psycopg.sql.SQL("GRANT INSERT ON identity_access_events TO {}").format(psycopg.sql.Identifier(role)))
            if admin.execute("SELECT to_regprocedure('p0_lock_identity(text,text,uuid)')").fetchone()[0]:
                admin.execute(psycopg.sql.SQL("GRANT EXECUTE ON FUNCTION p0_lock_identity(text,text,uuid) TO {}").format(psycopg.sql.Identifier(role)))
            parts = urlsplit(database)
            restricted = urlunsplit(parts._replace(netloc=f"{role}:{password}@{parts.hostname}:{parts.port or 5432}"))
            app = create_p0_app(database_url=restricted, workspace_id=workspace, issuer=claims["iss"], audience=claims["aud"], public_key=key.public_key())
            with TestClient(app) as client:
                yield client, key, claims, workspace, subject, database, restricted
        finally:
            admin.execute(psycopg.sql.SQL("DROP OWNED BY {}").format(psycopg.sql.Identifier(role)))
            admin.execute(psycopg.sql.SQL("DROP ROLE {}").format(psycopg.sql.Identifier(role)))


def test_nonowner_authorization_without_authority_edit_privileges(restricted_boundary):
    client, key, claims, workspace, subject, database, restricted = restricted_boundary
    with psycopg.connect(restricted) as connection:
        assert connection.execute("SELECT rolsuper, rolcreaterole, rolbypassrls FROM pg_roles WHERE rolname=current_user").fetchone() == (False, False, False)
        assert connection.execute("SELECT has_table_privilege(current_user, 'identity_subjects', 'UPDATE'), has_table_privilege(current_user, 'permission_grants', 'INSERT'), has_table_privilege(current_user, 'identity_access_events', 'DELETE')").fetchone() == (False, False, False)
        for statement in ["UPDATE identity_subjects SET enabled=true", "DELETE FROM permission_grants", "ALTER TABLE identity_subjects ADD COLUMN unauthorized text"]:
            with pytest.raises(psycopg.errors.InsufficientPrivilege), connection.transaction():
                connection.execute(statement)
    assert client.get(f"/v1/workspaces/{workspace}/identity", headers=headers(key, claims)).status_code == 200
    with psycopg.connect(database) as admin:
        admin.execute("UPDATE identity_subjects SET enabled=false, revision=revision+1 WHERE id=%s", (subject,))
    assert client.get(f"/v1/workspaces/{workspace}/identity", headers=headers(key, claims)).status_code == 401


def test_restricted_identity_lock_serializes_revocation(restricted_boundary):
    _, _, claims, workspace, subject, database, restricted = restricted_boundary
    with psycopg.connect(restricted) as reader:
        assert reader.execute("SELECT * FROM p0_lock_identity(%s,%s,%s)", (claims["iss"], claims["sub"], workspace)).fetchone() == (subject, 1)
        with psycopg.connect(database) as admin:
            with pytest.raises(psycopg.errors.LockNotAvailable), admin.transaction():
                admin.execute("SET LOCAL lock_timeout='100ms'")
                admin.execute("UPDATE identity_subjects SET enabled=false WHERE id=%s", (subject,))
    with psycopg.connect(database) as admin:
        admin.execute("UPDATE identity_subjects SET enabled=false WHERE id=%s", (subject,))
    with psycopg.connect(restricted) as reader:
        assert reader.execute("SELECT * FROM p0_lock_identity(%s,%s,%s)", (claims["iss"], claims["sub"], workspace)).fetchone() is None


def test_scopes_are_server_authority_and_audit_is_durable(boundary):
    client, key, claims, workspace, subject, database = boundary
    response = client.get(f"/v1/workspaces/{workspace}/identity", headers=headers(key, claims))
    assert response.status_code == 200
    assert response.json()["scopes"] == ["control:read"]
    traceparent = response.headers["traceparent"]
    denied = client.post(f"/v1/workspaces/{workspace}/programs", headers=headers(key, claims), json={"slug": "x", "name": "x", "niche": "x"})
    assert denied.status_code == 403
    assert client.get(f"/v1/workspaces/{uuid4()}/identity", headers=headers(key, claims)).status_code == 403
    with psycopg.connect(database) as connection:
        events = connection.execute("SELECT subject_id, trace_id, outcome FROM identity_access_events WHERE subject_id=%s ORDER BY created_at", (subject,)).fetchall()
    assert events[0] == (subject, traceparent.split("-")[1], "allow")
    assert [event[2] for event in events] == ["allow", "deny", "deny"]


@pytest.mark.parametrize("change", [{"aud": "wrong"}, {"iss": "wrong"}, {"exp": 1}, {"sub": "unknown"}])
def test_bad_or_expired_identity_denies(boundary, change):
    client, key, claims, workspace, _, _ = boundary
    assert client.get(f"/v1/workspaces/{workspace}/identity", headers=headers(key, claims | change)).status_code == 401


def test_revocation_and_disabled_operations_fail_closed(boundary):
    client, key, claims, workspace, subject, database = boundary
    for path in ["/v1/jobs/dummy", "/v1/creative/runs", "/v1/publications/runs", "/v1/agents/research/runs", "/v1/creative/providers/mock/webhook"]:
        assert client.post(path, headers=headers(key, claims), json={"dry_run": False}).status_code == 403
    with psycopg.connect(database) as connection:
        connection.execute("UPDATE identity_subjects SET enabled=false WHERE id=%s", (subject,))
    assert client.get(f"/v1/workspaces/{workspace}/identity", headers=headers(key, claims)).status_code == 401


def test_expired_grant_and_wrong_key_deny(boundary):
    client, key, claims, workspace, subject, database = boundary
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert client.get(f"/v1/workspaces/{workspace}/identity", headers=headers(other_key, claims)).status_code == 401
    with psycopg.connect(database) as connection:
        connection.execute("UPDATE permission_grants SET expires_at=now() - interval '1 second' WHERE principal_id=%s", (str(subject),))
    assert client.get(f"/v1/workspaces/{workspace}/identity", headers=headers(key, claims)).status_code == 403


def test_authorized_program_write_is_scoped_and_bounded(boundary):
    client, key, claims, workspace, subject, database = boundary
    with psycopg.connect(database) as connection:
        connection.execute("INSERT INTO permission_grants (workspace_id, principal_type, principal_id, scope, effect, constraints, expires_at) VALUES (%s,'identity',%s,'control:write','allow','{}',now() + interval '1 hour')", (workspace, str(subject)))
    response = client.post(f"/v1/workspaces/{workspace}/programs", headers=headers(key, claims), json={"slug": "bounded", "name": "P0", "niche": "fixture"})
    assert response.status_code == 201
    assert response.json()["workspace_id"] == str(workspace)
    assert client.post(f"/v1/workspaces/{uuid4()}/programs", headers=headers(key, claims), json={"slug": "x", "name": "x", "niche": "x"}).status_code == 403
    assert client.post(f"/v1/workspaces/{workspace}/programs", headers=headers(key, claims), json={"slug": "oversized", "name": "P0", "niche": "x" * 9000}).status_code == 413


def test_disabled_dispatch_is_not_an_assignable_permission(boundary):
    client, key, claims, workspace, subject, database = boundary
    with psycopg.connect(database) as connection:
        connection.execute("INSERT INTO permission_grants (workspace_id, principal_type, principal_id, scope, effect, constraints, expires_at) VALUES (%s,'identity',%s,'p0:disabled','allow','{}',now() + interval '1 hour')", (workspace, str(subject)))
    assert client.post("/v1/publications/runs", headers=headers(key, claims), json={"dry_run": False}).status_code == 403


def test_access_decisions_cannot_be_erased(boundary):
    client, key, claims, workspace, subject, database = boundary
    assert client.get(f"/v1/workspaces/{workspace}/identity", headers=headers(key, claims)).status_code == 200
    with psycopg.connect(database) as connection:
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            connection.execute("DELETE FROM identity_access_events WHERE subject_id=%s", (subject,))


def test_api_audit_and_exported_span_match_without_secret_canary(boundary):
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    client, key, claims, workspace, subject, database = boundary
    exporter = InMemorySpanExporter()
    client.app.state.trace_provider.add_span_processor(SimpleSpanProcessor(exporter))
    request_headers = headers(key, claims)
    request_headers["X-Secret-Canary"] = "never-export-this-secret"
    request_headers["traceparent"] = "00-" + "a" * 32 + "-" + "b" * 16 + "-01"
    response = client.get(f"/v1/workspaces/{workspace}/identity", headers=request_headers)
    assert response.status_code == 200
    span = exporter.get_finished_spans()[0]
    assert f"{span.context.trace_id:032x}" == "a" * 32
    assert f"{span.parent.span_id:016x}" == "b" * 16
    with psycopg.connect(database) as connection:
        event = connection.execute("SELECT trace_id,span_id FROM identity_access_events WHERE subject_id=%s", (subject,)).fetchone()
    assert event == (f"{span.context.trace_id:032x}", f"{span.context.span_id:016x}")
    exported = span.to_json()
    assert "never-export-this-secret" not in exported
    assert request_headers["Authorization"] not in exported


def test_readiness_checks_database_and_identity_schema(boundary, monkeypatch):
    client, _, _, _, _, _ = boundary
    assert client.get("/health/ready").json() == {"status": "ready", "effects_enabled": False}
    monkeypatch.setattr(client.app.state.identity_boundary, "database_url", "postgresql://invalid:invalid@127.0.0.1:1/absent")
    assert client.get("/health/ready").status_code == 503
    assert client.get("/health/live").status_code == 200


def test_inactive_workspace_cannot_use_remaining_subject_grants(boundary):
    client, key, claims, workspace, _, database = boundary
    with psycopg.connect(database) as connection:
        connection.execute("UPDATE workspaces SET status='suspended' WHERE id=%s", (workspace,))
    assert client.get(f"/v1/workspaces/{workspace}/identity", headers=headers(key, claims)).status_code == 401
    assert client.get("/health/ready").status_code == 503
