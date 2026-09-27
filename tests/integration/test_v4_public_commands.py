"""Signed fixture command parity over the existing canonical V4 transactions."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
from uuid import uuid4
from urllib.parse import urlsplit, urlunsplit

from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
import jwt
import psycopg
import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from temporalio.client import Client

from salience.api.p0 import create_p0_app
from salience.cycles.admission import CycleAdmission
from salience.cycles.contracts import CadencePolicy, GoalSpec, GoalSpecV2
from salience.cycles.outbox import CycleOutbox
from salience.cycles.runtime import TemporalCycleTransport, build_local_cycle_worker


@pytest.fixture
def public_fixture(monkeypatch):
    database = os.environ["TEST_DATABASE_URL"]
    monkeypatch.setenv("SALIENCE_DEPLOYMENT_MODE", "fixture")
    workspace, subject, program = uuid4(), uuid4(), uuid4()
    with psycopg.connect(database) as connection:
        connection.execute(
            "INSERT INTO workspaces (id,slug,display_name) VALUES (%s,%s,'V4 public fixture')",
            (workspace, str(workspace)),
        )
        connection.execute(
            "INSERT INTO content_programs (id,workspace_id,slug,name,niche) VALUES (%s,%s,%s,'Fixture','Fixture')",
            (program, workspace, str(program)),
        )
        connection.execute(
            "INSERT INTO identity_subjects (id,workspace_id,issuer,subject,expires_at) VALUES (%s,%s,'https://fixture.invalid',%s,now()+interval '1 hour')",
            (subject, workspace, str(subject)),
        )
        for scope in ("goals:write", "goals:approve", "cycles:write", "cycles:read"):
            connection.execute(
                "INSERT INTO permission_grants (workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at) VALUES (%s,'identity',%s,%s,'allow','{}',now()+interval '1 hour')",
                (workspace, str(subject), scope),
            )
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    app = create_p0_app(
        database_url=database,
        workspace_id=workspace,
        issuer="https://fixture.invalid",
        audience="salience-p0",
        public_key=key.public_key(),
        enable_v4_fixture_commands=True,
    )
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {"iss": "https://fixture.invalid", "aud": "salience-p0", "sub": str(subject),
         "iat": now, "nbf": now, "exp": now + timedelta(minutes=5)},
        key,
        algorithm="RS256",
    )
    headers = {"Authorization": "Bearer " + token, "X-Salience-Scopes": "admin:*,control:write"}
    legacy = GoalSpec(
        objective="Public fixture", metric_versions=("fixture@1",), audience="internal",
        account_refs=("fixture-account",), brand_scope="fixture-brand",
        source_policy="fixture-only", horizon_end=now + timedelta(hours=1),
    )
    anchor = now.replace(microsecond=0) - timedelta(seconds=600)
    spec = GoalSpecV2(**(legacy.model_dump() | {
        "schema_version": "GoalSpec.local.v2", "content_program_id": program,
        "cadence_seconds": None, "channel_refs": ("fixture-channel",),
        "content_scope": "fixture-only", "policy_refs": ("fixture-policy@1",),
        "retention_policy": "fixture-retention@1", "cadence": CadencePolicy(anchor=anchor),
    }))
    with TestClient(app) as client:
        yield client, headers, workspace, subject, spec, database


def test_public_fixture_commands_preserve_identity_and_outbox(public_fixture):
    client, headers, workspace, subject, spec, database = public_fixture
    created = client.post(
        f"/v1/workspaces/{workspace}/v4/goals",
        headers=headers | {"Idempotency-Key": "lifecycle-goal"},
        json=spec.model_dump(mode="json"),
    )
    assert created.status_code == 201, created.text
    goal_id = created.json()["goal_id"]
    approved = client.post(
        f"/v1/v4/goals/{goal_id}/baseline",
        headers=headers,
        json={"expected_revision": 1, "expires_at": spec.horizon_end.isoformat(),
              "reason": "Bounded fixture approval"},
    )
    assert approved.status_code == 200, approved.text
    slot = (spec.cadence.anchor + timedelta(seconds=600)).isoformat()
    requests = [
        {"origin": "manual", "idempotency_key": "manual", "expected_revision": 1,
         "slot_time": slot},
        {"origin": "event", "idempotency_key": "event", "expected_revision": 1,
         "slot_time": slot, "event_at": datetime.now(timezone.utc).isoformat(),
         "event_id": "external-event"},
        {"origin": "scheduled", "idempotency_key": "schedule", "expected_revision": 1,
         "slot_time": slot},
    ]
    intent_ids = []
    for payload in requests:
        response = client.post(f"/v1/v4/goals/{goal_id}/requests", headers=headers, json=payload)
        assert response.status_code == 200, response.text
        intent_ids.append(response.json()["intent_id"])
    assert len(set(intent_ids)) == 1
    repeated = client.post(f"/v1/v4/goals/{goal_id}/requests", headers=headers, json=requests[0])
    assert repeated.json()["intent_id"] == intent_ids[0]
    conflict = client.post(
        f"/v1/v4/goals/{goal_id}/requests", headers=headers,
        json=requests[0] | {"slot_time": (spec.cadence.anchor + timedelta(seconds=660)).isoformat()},
    )
    assert conflict.status_code == 409
    admitted = client.post(f"/v1/v4/intents/{intent_ids[0]}/admit", headers=headers, json={})
    assert admitted.status_code == 200, admitted.text
    assert admitted.json()["disposition"] == "admitted"
    assert client.post(f"/v1/v4/intents/{intent_ids[0]}/admit", headers=headers, json={}).json()["cycle_id"] == admitted.json()["cycle_id"]
    cycle_id = admitted.json()["cycle_id"]
    inspected = client.get(f"/v1/v4/cycles/{cycle_id}", headers=headers)
    assert inspected.status_code == 200, inspected.text
    assert inspected.json()["cycle_id"] == cycle_id
    assert inspected.json()["dry_run"] is True
    events = client.get(f"/v1/v4/cycles/{cycle_id}/events?limit=10", headers=headers)
    assert events.status_code == 200, events.text
    assert {event["kind"] for event in events.json()["events"]} >= {"admission"}
    assert all("payload" not in event for event in events.json()["events"])
    with psycopg.connect(database) as connection:
        context_before = connection.execute(
            "SELECT payload FROM v4_run_contexts WHERE cycle_id=%s", (cycle_id,),
        ).fetchone()[0]
        outbox_trace = connection.execute(
            "SELECT traceparent FROM v4_cycle_outbox WHERE cycle_id=%s AND kind='start'",
            (cycle_id,),
        ).fetchone()[0]
    assert outbox_trace == admitted.headers["traceparent"]
    cancelled = client.post(
        f"/v1/v4/cycles/{cycle_id}/cancel", headers=headers, json={"reason": "Fixture cancellation"}
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["disposition"] == "cancelled"
    assert client.post(
        f"/v1/v4/cycles/{cycle_id}/cancel", headers=headers, json={"reason": "Fixture cancellation"}
    ).status_code == 200
    assert client.post(
        f"/v1/v4/cycles/{cycle_id}/cancel", headers=headers, json={"reason": "Different reason"}
    ).status_code == 409
    first_page = client.get(f"/v1/v4/cycles/{cycle_id}/events?limit=1", headers=headers).json()
    assert first_page["next_cursor"]
    second_page = client.get(
        f"/v1/v4/cycles/{cycle_id}/events?limit=1&after={first_page['next_cursor']}",
        headers=headers,
    ).json()
    assert first_page["events"][0]["event_id"] != second_page["events"][0]["event_id"]
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT kind FROM v4_cycle_outbox WHERE cycle_id=%s ORDER BY sequence", (cycle_id,)
        ).fetchall() == [("start",), ("close",)]
        assert connection.execute(
            "SELECT payload FROM v4_run_contexts WHERE cycle_id=%s", (cycle_id,),
        ).fetchone()[0] == context_before
        connection.execute(
            "UPDATE permission_grants SET expires_at=now()-interval '1 second' WHERE principal_id=%s AND scope='cycles:read'",
            (str(subject),),
        )
    assert client.get(f"/v1/v4/cycles/{cycle_id}", headers=headers).status_code == 403


def test_public_fixture_authorization_and_opt_in_fail_closed(public_fixture):
    client, headers, workspace, subject, spec, database = public_fixture
    path = f"/v1/workspaces/{workspace}/v4/goals"
    assert client.post(path, json=spec.model_dump(mode="json")).status_code == 401
    assert client.post(path.replace(str(workspace), str(uuid4())), headers=headers,
                       json=spec.model_dump(mode="json")).status_code == 403
    with psycopg.connect(database) as connection:
        connection.execute(
            "UPDATE permission_grants SET expires_at=now()-interval '1 second' WHERE principal_id=%s AND scope='goals:write'",
            (str(subject),),
        )
    assert client.post(path, headers=headers, json=spec.model_dump(mode="json")).status_code == 403
    assert client.post("/v1/jobs/dummy", headers=headers, json={"dry_run": False}).status_code == 403


def test_public_goal_creation_requires_exact_idempotency(public_fixture):
    client, headers, workspace, _, spec, database = public_fixture
    path = f"/v1/workspaces/{workspace}/v4/goals"
    payload = spec.model_dump(mode="json")
    assert client.post(path, headers=headers, json=payload).status_code == 422
    keyed = headers | {"Idempotency-Key": "goal-create:exact"}
    with ThreadPoolExecutor(max_workers=3) as pool:
        responses = list(pool.map(
            lambda _: client.post(path, headers=keyed, json=payload), range(3),
        ))
    assert all(response.status_code == 201 for response in responses)
    goal_ids = {response.json()["goal_id"] for response in responses}
    assert len(goal_ids) == 1
    assert client.post(path, headers=keyed, json=payload).json()["goal_id"] in goal_ids
    conflict = client.post(
        path, headers=keyed, json=payload | {"objective": "Changed objective"},
    )
    assert conflict.status_code == 409
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT count(*) FROM v4_goal_create_commands WHERE goal_id=%s",
            (next(iter(goal_ids)),),
        ).fetchone()[0] == 1


def test_goal_create_receipt_rolls_back_with_failed_business_transaction(public_fixture, monkeypatch):
    _, _, workspace, subject, spec, database = public_fixture
    service = CycleAdmission(database, workspace_id=workspace, subject_id=subject)
    with psycopg.connect(database) as connection:
        before = connection.execute(
            "SELECT count(*) FROM v4_goals WHERE workspace_id=%s", (workspace,),
        ).fetchone()[0]
    original_event = service._event

    def interrupted(*args, **kwargs):
        raise RuntimeError("injected pre-commit crash")

    monkeypatch.setattr(service, "_event", interrupted)
    with pytest.raises(RuntimeError, match="pre-commit"):
        service.create_goal(spec, idempotency_key="rollback-goal")
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT count(*) FROM v4_goals WHERE workspace_id=%s", (workspace,),
        ).fetchone()[0] == before
        assert connection.execute(
            "SELECT count(*) FROM v4_goal_create_commands WHERE workspace_id=%s AND idempotency_key='rollback-goal'",
            (workspace,),
        ).fetchone()[0] == 0
    monkeypatch.setattr(service, "_event", original_event)
    created = service.create_goal(spec, idempotency_key="rollback-goal")
    assert service.create_goal(spec, idempotency_key="rollback-goal") == created


def test_public_fixture_rejects_invalid_spec_and_coalesces_concurrent_origins(public_fixture):
    client, headers, workspace, _, spec, database = public_fixture
    path = f"/v1/workspaces/{workspace}/v4/goals"
    goal_headers = headers | {"Idempotency-Key": "coalesce-goal"}
    invalid = client.post(path, headers=goal_headers,
                          json=spec.model_dump(mode="json") | {"dry_run": False})
    assert invalid.status_code == 422
    created = client.post(path, headers=goal_headers, json=spec.model_dump(mode="json"))
    assert created.status_code == 201
    goal_id = created.json()["goal_id"]
    client.post(f"/v1/v4/goals/{goal_id}/baseline", headers=headers,
                json={"expected_revision": 1, "expires_at": spec.horizon_end.isoformat(),
                      "reason": "Fixture approval"})
    slot = (spec.cadence.anchor + timedelta(seconds=600)).isoformat()
    commands = [
        {"origin": origin, "idempotency_key": origin,
         "expected_revision": 1, "slot_time": slot}
        for origin in ("manual", "scheduled")
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(
            lambda command: client.post(f"/v1/v4/goals/{goal_id}/requests",
                                        headers=headers, json=command), commands,
        ))
    assert all(response.status_code == 200 for response in responses)
    assert len({response.json()["intent_id"] for response in responses}) == 1
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT count(*) FROM v4_cycle_intents WHERE goal_id=%s", (goal_id,),
        ).fetchone()[0] == 1


def test_sdk_and_cli_use_same_signed_fixture_contract(public_fixture, monkeypatch, capsys):
    from salience.cli import main
    from salience.sdk.client import SalienceClient

    client, headers, workspace, _, spec, database = public_fixture
    observed_headers = []

    def bridge(method, url, **kwargs):
        observed_headers.append(kwargs["headers"])
        split = urlsplit(url)
        path = split.path + ("?" + split.query if split.query else "")
        return client.request(method, path, headers=kwargs["headers"], json=kwargs.get("json"))

    monkeypatch.setattr("httpx.request", bridge)
    monkeypatch.setenv("SALIENCE_CONTROL_URL", "http://fixture.invalid")
    monkeypatch.setenv("SALIENCE_CONTROL_JWT", headers["Authorization"][7:])
    sdk = SalienceClient("http://fixture.invalid", headers["Authorization"][7:]).cycles
    goal_id = sdk.create_goal(
        str(workspace), spec.model_dump(mode="json"), idempotency_key="sdk-goal",
    )["goal_id"]
    main(["cycles", "goal-create", "--workspace-id", str(workspace),
          "--spec-json", json.dumps(spec.model_dump(mode="json")),
          "--idempotency-key", "cli-goal"])
    assert json.loads(capsys.readouterr().out)["goal_id"] != goal_id
    main(["cycles", "baseline-approve", "--goal-id", goal_id,
          "--expected-revision", "1", "--expires-at", spec.horizon_end.isoformat(),
          "--reason", "Fixture approval"])
    approval_id = json.loads(capsys.readouterr().out)["approval_id"]
    assert sdk.approve_baseline(
        goal_id, expected_revision=1, expires_at=spec.horizon_end.isoformat(),
        reason="Fixture approval",
    )["approval_id"] == approval_id
    command = {"origin": "manual", "expected_revision": 1, "idempotency_key": "sdk-cli",
               "slot_time": (spec.cadence.anchor + timedelta(seconds=600)).isoformat()}
    intent_id = sdk.request_cycle(goal_id, command)["intent_id"]
    main(["cycles", "request", "--goal-id", goal_id,
          "--request-json", json.dumps(command)])
    assert json.loads(capsys.readouterr().out)["intent_id"] == intent_id
    main(["cycles", "admit", "--intent-id", intent_id])
    cycle_id = json.loads(capsys.readouterr().out)["cycle_id"]
    assert sdk.admit(intent_id)["cycle_id"] == cycle_id
    assert sdk.inspect(cycle_id)["cycle_id"] == cycle_id
    main(["cycles", "inspect", "--cycle-id", cycle_id])
    assert json.loads(capsys.readouterr().out)["cycle_id"] == cycle_id
    assert sdk.events(cycle_id, limit=1)["events"]
    main(["cycles", "events", "--cycle-id", cycle_id, "--limit", "1"])
    assert json.loads(capsys.readouterr().out)["events"]
    main(["cycles", "cancel", "--cycle-id", cycle_id,
          "--reason", "SDK CLI cancellation"])
    assert json.loads(capsys.readouterr().out)["disposition"] == "cancelled"
    assert sdk.cancel(cycle_id, reason="SDK CLI cancellation")["disposition"] == "cancelled"
    assert all("X-Salience-Scopes" not in sent for sent in observed_headers)
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT count(*) FROM v4_cycles WHERE id=%s", (cycle_id,),
        ).fetchone()[0] == 1


def test_public_fixture_commands_use_restricted_nonowner_role(public_fixture):
    client, headers, workspace, subject, spec, database = public_fixture
    goal_id = client.post(
        f"/v1/workspaces/{workspace}/v4/goals",
        headers=headers | {"Idempotency-Key": "restricted-goal"},
        json=spec.model_dump(mode="json"),
    ).json()["goal_id"]
    client.post(
        f"/v1/v4/goals/{goal_id}/baseline", headers=headers,
        json={"expected_revision": 1, "expires_at": spec.horizon_end.isoformat(),
              "reason": "Fixture approval"},
    )
    intent_id = client.post(
        f"/v1/v4/goals/{goal_id}/requests", headers=headers,
        json={"origin": "manual", "idempotency_key": "restricted-read",
              "expected_revision": 1,
              "slot_time": (spec.cadence.anchor + timedelta(seconds=600)).isoformat()},
    ).json()["intent_id"]
    cycle_id = client.post(
        f"/v1/v4/intents/{intent_id}/admit", headers=headers, json={},
    ).json()["cycle_id"]
    role = "v4_public_reader_" + uuid4().hex
    password = uuid4().hex
    with psycopg.connect(database, autocommit=True) as admin:
        admin.execute(psycopg.sql.SQL(
            "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS"
        ).format(psycopg.sql.Identifier(role), psycopg.sql.Literal(password)))
        try:
            admin.execute(psycopg.sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(psycopg.sql.Identifier(role)))
            admin.execute(psycopg.sql.SQL(
                "GRANT SELECT ON workspaces,identity_subjects,permission_grants,v4_goals,v4_cycle_intents,v4_cycles,v4_run_contexts,v4_cycle_events,v4_goal_create_commands TO {}"
            ).format(psycopg.sql.Identifier(role)))
            admin.execute(psycopg.sql.SQL("GRANT INSERT ON identity_access_events TO {}").format(psycopg.sql.Identifier(role)))
            admin.execute(psycopg.sql.SQL(
                "GRANT INSERT ON v4_goals,v4_goal_revisions,v4_cycle_events,v4_goal_create_commands TO {}"
            ).format(psycopg.sql.Identifier(role)))
            admin.execute(psycopg.sql.SQL(
                "GRANT EXECUTE ON FUNCTION p0_lock_identity(text,text,uuid) TO {}"
            ).format(psycopg.sql.Identifier(role)))
            admin.execute(psycopg.sql.SQL(
                "GRANT EXECUTE ON FUNCTION v4_lock_program(uuid,uuid) TO {}"
            ).format(psycopg.sql.Identifier(role)))
            split = urlsplit(database)
            restricted = urlunsplit(split._replace(
                netloc=f"{role}:{password}@{split.hostname}:{split.port or 5432}"
            ))
            with psycopg.connect(restricted) as connection:
                assert connection.execute(
                    "SELECT has_table_privilege(current_user,'v4_goals','UPDATE'),has_table_privilege(current_user,'permission_grants','UPDATE')"
                ).fetchone() == (False, False)
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            now = datetime.now(timezone.utc)
            token = jwt.encode(
                {"iss": "https://fixture.invalid", "aud": "salience-p0",
                 "sub": str(subject), "iat": now, "nbf": now,
                 "exp": now + timedelta(minutes=5)}, key, algorithm="RS256",
            )
            app = create_p0_app(
                database_url=restricted, workspace_id=workspace,
                issuer="https://fixture.invalid", audience="salience-p0",
                public_key=key.public_key(), enable_v4_fixture_commands=True,
            )
            with TestClient(app) as restricted_client:
                response = restricted_client.get(
                    f"/v1/v4/cycles/{cycle_id}",
                    headers={"Authorization": "Bearer " + token},
                )
                assert response.status_code == 200, response.text
                assert response.json()["cycle_id"] == cycle_id
                direct_goal_id = CycleAdmission(
                    restricted, workspace_id=workspace, subject_id=subject,
                ).create_goal(spec, idempotency_key="restricted-direct")
                assert direct_goal_id
                created = restricted_client.post(
                    f"/v1/workspaces/{workspace}/v4/goals",
                    headers={"Authorization": "Bearer " + token,
                             "Idempotency-Key": "restricted-goal-create"},
                    json=spec.model_dump(mode="json"),
                )
                assert created.status_code == 201, created.text
                assert restricted_client.post(
                    f"/v1/workspaces/{workspace}/v4/goals",
                    headers={"Authorization": "Bearer " + token,
                             "Idempotency-Key": "restricted-goal-create"},
                    json=spec.model_dump(mode="json"),
                ).json()["goal_id"] == created.json()["goal_id"]
        finally:
            admin.execute(psycopg.sql.SQL("DROP OWNED BY {}").format(psycopg.sql.Identifier(role)))
            admin.execute(psycopg.sql.SQL("DROP ROLE {}").format(psycopg.sql.Identifier(role)))


@pytest.mark.asyncio
async def test_public_api_commits_trace_through_mock_adapter(public_fixture):
    client, headers, workspace, subject, spec, database = public_fixture
    exporter = InMemorySpanExporter()
    client.app.state.trace_provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = client.app.state.trace_provider.get_tracer("v4.public.fixture")
    with psycopg.connect(database) as connection:
        connection.execute(
            "INSERT INTO permission_grants (workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at) VALUES (%s,'identity',%s,'cycles:permit','allow','{}',now()+interval '1 hour')",
            (workspace, str(subject)),
        )
    goal_id = client.post(
        f"/v1/workspaces/{workspace}/v4/goals",
        headers=headers | {"Idempotency-Key": "trace-goal"},
        json=spec.model_dump(mode="json"),
    ).json()["goal_id"]
    assert client.post(
        f"/v1/v4/goals/{goal_id}/baseline", headers=headers,
        json={"expected_revision": 1, "expires_at": spec.horizon_end.isoformat(),
              "reason": "Fixture approval"},
    ).status_code == 200
    intent_id = client.post(
        f"/v1/v4/goals/{goal_id}/requests", headers=headers,
        json={"origin": "manual", "idempotency_key": "public-trace",
              "expected_revision": 1,
              "slot_time": (spec.cadence.anchor + timedelta(seconds=600)).isoformat()},
    ).json()["intent_id"]
    secret_canary = "V4_PUBLIC_SECRET_CANARY"
    admitted = client.post(
        f"/v1/v4/intents/{intent_id}/admit",
        headers=headers | {"X-Secret-Canary": secret_canary}, json={},
    )
    assert admitted.status_code == 200, admitted.text
    cycle_id = admitted.json()["cycle_id"]
    with psycopg.connect(database) as connection:
        start_id, start_trace = connection.execute(
            "SELECT id,traceparent FROM v4_cycle_outbox WHERE cycle_id=%s AND kind='start'",
            (cycle_id,),
        ).fetchone()
        operation_id = connection.execute(
            "SELECT operation_id FROM v4_cycles WHERE id=%s", (cycle_id,),
        ).fetchone()[0]
    assert start_trace == admitted.headers["traceparent"]

    class MockAdapter:
        effect_id = "fixture.noop"

        def __init__(self):
            self.calls = []

        async def invoke(self, *, operation_id, idempotency_key, traceparent):
            self.calls.append((operation_id, idempotency_key, traceparent))
            return {"accepted": True, "operation_id": operation_id,
                    "idempotency_key": idempotency_key}

    adapter = MockAdapter()
    outbox = CycleOutbox(database, workspace_id=workspace, tracer=tracer)
    temporal = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    queue = "salience-v4-local-" + uuid4().hex
    transport = TemporalCycleTransport(temporal, task_queue=queue)
    worker = build_local_cycle_worker(
        temporal, task_queue=queue, outbox=outbox, adapter=adapter, tracer=tracer,
    )
    async with worker:
        assert await outbox.dispatch_one(transport)
        async with asyncio.timeout(10):
            while not await asyncio.to_thread(outbox.accepted_fixture, start_id, operation_id):
                await asyncio.sleep(0.05)
        assert client.post(
            f"/v1/v4/cycles/{cycle_id}/cancel", headers=headers,
            json={"reason": "Fixture trace complete"},
        ).status_code == 200
        assert await outbox.dispatch_one(transport)
        await asyncio.wait_for(
            temporal.get_workflow_handle(transport.workflow_id(cycle_id)).result(), 20,
        )
    assert len(adapter.calls) == 1
    spans = exporter.get_finished_spans()
    api_span_id = int(admitted.headers["traceparent"].split("-")[2], 16)
    delivery = next(span for span in spans if span.name == "cycle.outbox.delivery")
    consumed = next(
        span for span in spans
        if span.name == "cycle.fixture.consume" and span.parent.span_id == delivery.context.span_id
    )
    effect = next(span for span in spans if span.name == "cycle.fixture.adapter")
    assert delivery.parent.span_id == api_span_id
    assert effect.parent.span_id == consumed.context.span_id
    assert len({span.context.trace_id for span in (delivery, consumed, effect)}) == 1
    assert secret_canary not in str([(span.name, span.attributes) for span in spans])
    with psycopg.connect(database) as connection:
        accepted = connection.execute(
            "SELECT traceparent FROM v4_cycle_events WHERE cycle_id=%s AND kind='fixture_adapter_accepted'",
            (cycle_id,),
        ).fetchall()
        serialized = connection.execute(
            "SELECT payload::text FROM v4_cycle_events WHERE cycle_id=%s",
            (cycle_id,),
        ).fetchall()
    assert accepted == [(adapter.calls[0][2],)]
    assert secret_canary not in str(serialized)


def test_fixture_commands_do_not_load_in_production_mode(monkeypatch, public_fixture):
    _, _, workspace, _, _, database = public_fixture
    monkeypatch.setenv("SALIENCE_DEPLOYMENT_MODE", "p0")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(ValueError, match="fixture"):
        create_p0_app(
            database_url=database, workspace_id=workspace, issuer="https://fixture.invalid",
            audience="salience-p0", public_key=key.public_key(),
            enable_v4_fixture_commands=True,
        )
    monkeypatch.setenv("SALIENCE_DEPLOYMENT_MODE", "fixture")
    disabled = create_p0_app(
        database_url=database, workspace_id=workspace, issuer="https://fixture.invalid",
        audience="salience-p0", public_key=key.public_key(),
    )
    assert not any(getattr(route, "path", "").startswith("/v1/v4/") for route in disabled.routes)
