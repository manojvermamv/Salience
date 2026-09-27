import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import psycopg
import pytest
from temporalio.client import Client
from temporalio.worker import Replayer

from salience.cycles.admission import CycleAdmission
from salience.cycles.contracts import AllocationPolicy, CadencePolicy, CycleRequest, GoalSpec, GoalSpecV2, GoalSpecV3
from salience.cycles.outbox import CycleOutbox
from salience.cycles.runtime import TemporalCycleTransport, build_local_cycle_worker
from salience.cycles.workflow import LocalCycleWorkflow


def scenario(policy_version=1, trace_context_factory=None):
    database=os.environ["TEST_DATABASE_URL"]
    workspace, subject=uuid4(),uuid4()
    with psycopg.connect(database) as connection:
        connection.execute("INSERT INTO workspaces (id,slug,display_name) VALUES (%s,%s,'outbox fixture')",(workspace,str(workspace)))
        connection.execute("INSERT INTO identity_subjects (id,workspace_id,issuer,subject,expires_at) VALUES (%s,%s,'https://fixture.invalid',%s,now()+interval '1 hour')",(subject,workspace,str(subject)))
        for scope in ["goals:write","goals:approve","cycles:write"]:
            connection.execute("INSERT INTO permission_grants (workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at) VALUES (%s,'identity',%s,%s,'allow','{}',now()+interval '1 hour')",(workspace,str(subject),scope))
    trace_context = trace_context_factory(database, workspace, subject) if trace_context_factory else None
    service=CycleAdmission(database,workspace_id=workspace,subject_id=subject,trace_context=trace_context)
    now=datetime.now(timezone.utc)
    spec=GoalSpec(objective="outbox crash fixture",metric_versions=("fixture@1",),audience="internal",account_refs=("fixture",),brand_scope="fixture",source_policy="fixture-only",horizon_end=now+timedelta(hours=1))
    if policy_version >= 2:
        program = uuid4()
        with psycopg.connect(database) as connection:
            connection.execute("INSERT INTO content_programs (id,workspace_id,slug,name,niche) VALUES (%s,%s,%s,'Fixture','Fixture')", (program, workspace, str(program)))
        spec = GoalSpecV2.model_validate(spec.model_dump() | {"schema_version":"GoalSpec.local.v2", "cadence_seconds":None,
            "content_program_id":program, "account_refs":("fixture-account",), "channel_refs":("fixture-channel",),
            "content_scope":"fixture-only", "policy_refs":("fixture-policy@1",), "retention_policy":"fixture-retention@1",
            "cadence":CadencePolicy(anchor=now.replace(microsecond=0))})
        if policy_version == 3:
            budget = uuid4()
            end = now+timedelta(hours=1)
            with psycopg.connect(database) as connection:
                connection.execute("INSERT INTO budgets (id,workspace_id,content_program_id,name,scope,limit_amount,period_start,period_end) VALUES (%s,%s,%s,'restart','program',1,%s,%s)", (budget,workspace,program,now,end))
            spec = GoalSpecV3.model_validate(spec.model_dump() | {"schema_version":"GoalSpec.local.v3", "allocation":AllocationPolicy(budget_ids=(budget,),currency="USD",period_start=now,period_end=end,ceiling_micros=80)})
    goal=service.create_goal(spec)
    service.approve_baseline(goal,expected_revision=1,expires_at=now+timedelta(hours=1),reason="Explicit process-recovery fixture baseline")
    requested = (service.request_cycle(goal, CycleRequest(origin="scheduled", idempotency_key="restart", expected_revision=1, slot_time=spec.cadence.anchor))
                 if policy_version >= 2 else service.request_intent(goal,slot="one",due_at=now,expires_at=now+timedelta(minutes=5)))
    admitted=service.admit(requested)
    return service,CycleOutbox(database,workspace_id=workspace),admitted


def inbox_count(database,cycle_id):
    with psycopg.connect(database) as connection:
        return connection.execute("SELECT count(*) FROM v4_cycle_inbox WHERE cycle_id=%s",(cycle_id,)).fetchone()[0]


async def wait_consumed(database,cycle_id,count):
    async with asyncio.timeout(30):
        while await asyncio.to_thread(inbox_count,database,cycle_id)<count:
            await asyncio.sleep(0.1)


@pytest.mark.asyncio
@pytest.mark.parametrize("policy_version", [1, 2, 3])
async def test_process_kill_ack_loss_and_queued_signal_resume_original_cycle(tmp_path, policy_version):
    service,outbox,admitted=scenario(policy_version)
    client=await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    queue="salience-v4-local-"+uuid4().hex
    transport=TemporalCycleTransport(client,task_queue=queue)
    environment=os.environ | {"SALIENCE_DEPLOYMENT_MODE":"fixture","V4_FIXTURE_WORKSPACE":str(service.workspace_id),"V4_FIXTURE_QUEUE":queue,"PYTHONPATH":str(Path(__file__).resolve().parents[2] / "src")}
    logs=tmp_path / "worker.log"
    def worker():
        with logs.open("a") as output:
            return subprocess.Popen([sys.executable,"-m","salience.cycles.runtime"],env=environment,stdout=output,stderr=subprocess.STDOUT)
    first=worker()
    second=None
    try:
        message=outbox.claim()
        await transport.deliver(message)
        await wait_consumed(service.database_url,admitted["cycle_id"],1)
        with psycopg.connect(service.database_url) as connection:
            assert connection.execute("SELECT state FROM v4_cycle_inbox WHERE message_id=%s", (message["id"],)).fetchone()[0] == "recorded"
        first.kill()
        await asyncio.to_thread(first.wait,10)
        await transport.deliver(message)
        with psycopg.connect(service.database_url) as connection:
            connection.execute("UPDATE v4_cycle_outbox SET lease_until=now()-interval '1 second' WHERE id=%s",(message["id"],))
        assert await outbox.dispatch_one(transport)
        service.close(admitted["cycle_id"],disposition="abstain",reason="fixture complete")
        assert await outbox.dispatch_one(transport)
        second=worker()
        result=await asyncio.wait_for(client.get_workflow_handle(transport.workflow_id(admitted["cycle_id"])).result(),30)
        assert result["cycle_id"]==str(admitted["cycle_id"])
        assert result["state"] == "recorded"
        history=await client.get_workflow_handle(transport.workflow_id(admitted["cycle_id"])).fetch_history()
        await Replayer(workflows=[LocalCycleWorkflow]).replay_workflow(history)
        assert inbox_count(service.database_url,admitted["cycle_id"])==2
        assert outbox.consume(message["id"])["cycle_id"]==admitted["cycle_id"]
        with psycopg.connect(service.database_url) as connection:
            assert connection.execute("SELECT DISTINCT state FROM v4_cycle_inbox WHERE cycle_id=%s", (admitted["cycle_id"],)).fetchall() == [("recorded",)]
            assert connection.execute("SELECT count(DISTINCT payload->>'message_id') FROM v4_cycle_events WHERE cycle_id=%s AND kind='fixture_consumed'",(admitted["cycle_id"],)).fetchone()[0]==2
            if policy_version == 3:
                assert connection.execute("SELECT count(*) FROM v4_cycle_allocations WHERE cycle_id=%s",(admitted["cycle_id"],)).fetchone()[0] == 1
                assert connection.execute("SELECT reservation.status,reservation.reserved_amount FROM budget_reservations AS reservation JOIN v4_allocation_reservations AS mapping ON mapping.reservation_id=reservation.id JOIN v4_cycle_allocations AS allocation ON allocation.id=mapping.allocation_id WHERE allocation.cycle_id=%s",(admitted["cycle_id"],)).fetchall() == [("released",0)]
    finally:
        for process in [first,second]:
            if process and process.poll() is None:
                process.kill()
                await asyncio.to_thread(process.wait,10)
        print(logs.read_text())


@pytest.mark.asyncio
async def test_automatic_fixture_dispatch_recovers_after_worker_kill(tmp_path):
    service, _, admitted = scenario(2)
    client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    queue = "salience-v4-local-" + uuid4().hex
    environment = os.environ | {
        "SALIENCE_DEPLOYMENT_MODE": "fixture",
        "V4_FIXTURE_AUTODISPATCH": "1",
        "V4_FIXTURE_WORKSPACE": str(service.workspace_id),
        "V4_FIXTURE_QUEUE": queue,
        "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
    }
    logs = tmp_path / "auto-worker.log"

    def worker():
        with logs.open("a") as output:
            return subprocess.Popen(
                [sys.executable, "-m", "salience.cycles.runtime"],
                env=environment,
                stdout=output,
                stderr=subprocess.STDOUT,
            )

    first = worker()
    second = None
    try:
        await wait_consumed(service.database_url, admitted["cycle_id"], 1)
        first.kill()
        await asyncio.to_thread(first.wait, 10)
        service.close(admitted["cycle_id"], disposition="abstain", reason="fixture complete")
        second = worker()
        await wait_consumed(service.database_url, admitted["cycle_id"], 2)
        result = await asyncio.wait_for(
            client.get_workflow_handle(
                TemporalCycleTransport.workflow_id(admitted["cycle_id"])
            ).result(),
            30,
        )
        assert result == {"cycle_id": str(admitted["cycle_id"]), "state": "recorded"}
        with psycopg.connect(service.database_url) as connection:
            assert connection.execute(
                "SELECT state FROM v4_cycle_outbox WHERE cycle_id=%s ORDER BY sequence",
                (admitted["cycle_id"],),
            ).fetchall() == [("delivered",), ("delivered",)]
            assert connection.execute(
                "SELECT count(*) FROM v4_cycle_events WHERE cycle_id=%s AND kind='outbox_dead_letter'",
                (admitted["cycle_id"],),
            ).fetchone()[0] == 0
    finally:
        for process in (first, second):
            if process and process.poll() is None:
                process.kill()
                await asyncio.to_thread(process.wait, 10)
        print(logs.read_text())


@pytest.mark.asyncio
async def test_outbox_workflow_activity_trace_matches_canonical_consumption():
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    service,_,admitted=scenario()
    provider=TracerProvider()
    exporter=InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    outbox=CycleOutbox(service.database_url,workspace_id=service.workspace_id,tracer=provider.get_tracer("fixture"))
    client=await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    queue="salience-v4-local-"+uuid4().hex
    transport=TemporalCycleTransport(client,task_queue=queue)
    worker=build_local_cycle_worker(client,task_queue=queue,outbox=outbox)
    async with worker:
        assert await outbox.dispatch_one(transport)
        await wait_consumed(service.database_url,admitted["cycle_id"],1)
        service.close(admitted["cycle_id"],disposition="completed",reason="fixture")
        assert await outbox.dispatch_one(transport)
        await asyncio.wait_for(client.get_workflow_handle(transport.workflow_id(admitted["cycle_id"])).result(),20)
    spans=exporter.get_finished_spans()
    assert {span.name for span in spans} >= {"cycle.outbox.delivery","cycle.fixture.consume"}
    assert all(f"{span.context.trace_id:032x}"==service.trace.trace_id for span in spans)
    with psycopg.connect(service.database_url) as connection:
        carriers=connection.execute("SELECT traceparent FROM v4_cycle_inbox WHERE cycle_id=%s",(admitted["cycle_id"],)).fetchall()
    ids={f"{span.context.span_id:016x}" for span in spans}
    assert all(carrier[0].split("-")[2] in ids for carrier in carriers)
    provider.shutdown()


@pytest.mark.asyncio
async def test_api_to_mock_adapter_trace_and_duplicate_activity_are_bound(monkeypatch):
    from cryptography.hazmat.primitives.asymmetric import rsa
    from fastapi.testclient import TestClient
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    import jwt

    from salience.api.p0 import create_p0_app
    from salience.cycles.runtime import LocalCycleActivities
    from salience.observability.tracing import TraceContext

    provider = TracerProvider()
    monkeypatch.setenv("SALIENCE_DEPLOYMENT_MODE", "fixture")
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("v4.fixture.chain")
    secret_canary = "SECRET_CANARY_P1_ITEM4"

    def api_parent(database, workspace, subject):
        with psycopg.connect(database) as connection:
            for scope in ("control:read", "cycles:permit"):
                connection.execute("INSERT INTO permission_grants (workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at) VALUES (%s,'identity',%s,%s,'allow','{}',now()+interval '1 hour')", (workspace, str(subject), scope))
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        app = create_p0_app(database_url=database, workspace_id=workspace,
                            issuer="https://fixture.invalid", audience="salience-p0",
                            public_key=key.public_key(), tracer=tracer)
        now = datetime.now(timezone.utc)
        token = jwt.encode({"iss": "https://fixture.invalid", "aud": "salience-p0",
                            "sub": str(subject), "iat": now, "nbf": now,
                            "exp": now + timedelta(minutes=5)}, key, algorithm="RS256")
        with TestClient(app) as client:
            response = client.get(f"/v1/workspaces/{workspace}/identity",
                                  headers={"Authorization": "Bearer " + token,
                                           "X-Secret-Canary": secret_canary})
        assert response.status_code == 200
        return TraceContext.from_carrier({"traceparent": response.headers["traceparent"]})

    service, _, admitted = scenario(2, trace_context_factory=api_parent)
    outbox = CycleOutbox(service.database_url, workspace_id=service.workspace_id, tracer=tracer)
    client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    queue = "salience-v4-local-" + uuid4().hex
    transport = TemporalCycleTransport(client, task_queue=queue)

    class MockAdapter:
        effect_id = "fixture.noop"

        def __init__(self):
            self.calls = []

        async def invoke(self, *, operation_id, idempotency_key, traceparent):
            self.calls.append((operation_id, idempotency_key, traceparent))
            return {"accepted": True, "operation_id": operation_id,
                    "idempotency_key": idempotency_key}

    adapter = MockAdapter()
    worker = build_local_cycle_worker(client, task_queue=queue, outbox=outbox,
                                      adapter=adapter, tracer=tracer)
    with psycopg.connect(service.database_url) as connection:
        start_id = connection.execute("SELECT id FROM v4_cycle_outbox WHERE cycle_id=%s AND kind='start'", (admitted["cycle_id"],)).fetchone()[0]
    async with worker:
        assert await outbox.dispatch_one(transport)
        await wait_consumed(service.database_url, admitted["cycle_id"], 1)
        service.close(admitted["cycle_id"], disposition="completed", reason="fixture")
        assert await outbox.dispatch_one(transport)
        await asyncio.wait_for(client.get_workflow_handle(transport.workflow_id(admitted["cycle_id"])).result(), 20)
    with psycopg.connect(service.database_url) as connection:
        accepted = connection.execute("SELECT traceparent,payload FROM v4_cycle_events WHERE cycle_id=%s AND kind='fixture_adapter_accepted'", (admitted["cycle_id"],)).fetchall()
        start = connection.execute("SELECT traceparent FROM v4_cycle_outbox WHERE id=%s", (start_id,)).fetchone()[0]
    assert len(adapter.calls) == len(accepted) == 1
    assert accepted[0][1]["message_id"] == str(start_id)
    assert start == service.trace.to_carrier()["traceparent"]
    duplicate = await LocalCycleActivities(outbox, adapter=adapter, tracer=tracer).consume(
        {"message_id": str(start_id), "cycle_id": str(admitted["cycle_id"]),
         "kind": "start", "traceparent": accepted[0][0]}
    )
    assert duplicate["state"] == "recorded" and len(adapter.calls) == 1
    spans = exporter.get_finished_spans()
    names = {span.name for span in spans}
    assert {"p0.control.request", "cycle.outbox.delivery", "cycle.fixture.consume", "cycle.fixture.adapter"} <= names
    assert all(f"{span.context.trace_id:032x}" == service.trace.trace_id for span in spans)
    api_span = next(span for span in spans if span.name == "p0.control.request")
    delivery_span = next(span for span in spans if span.name == "cycle.outbox.delivery")
    consume_span = next(span for span in spans if span.name == "cycle.fixture.consume")
    adapter_span = next(span for span in spans if span.name == "cycle.fixture.adapter")
    assert delivery_span.parent.span_id == api_span.context.span_id
    assert consume_span.parent.span_id == delivery_span.context.span_id
    assert adapter_span.parent.span_id == consume_span.context.span_id
    assert accepted[0][0].split("-")[2] == f"{adapter_span.context.span_id:016x}"
    assert secret_canary not in str([(span.name, span.attributes) for span in spans])
    with psycopg.connect(service.database_url) as connection:
        rows = connection.execute("SELECT payload::text FROM v4_cycle_events WHERE cycle_id=%s", (admitted["cycle_id"],)).fetchall()
        messages = connection.execute("SELECT payload::text FROM v4_cycle_outbox WHERE cycle_id=%s", (admitted["cycle_id"],)).fetchall()
    assert secret_canary not in str(rows + messages)
    provider.shutdown()


@pytest.mark.asyncio
async def test_uncertain_mock_effect_is_not_sent_twice(monkeypatch):
    from salience.cycles.runtime import LocalCycleActivities

    monkeypatch.setenv("SALIENCE_DEPLOYMENT_MODE", "fixture")

    def grant_permit(database, workspace, subject):
        with psycopg.connect(database) as connection:
            connection.execute("INSERT INTO permission_grants (workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at) VALUES (%s,'identity',%s,'cycles:permit','allow','{}',now()+interval '1 hour')", (workspace, str(subject)))

    service, outbox, admitted = scenario(2, trace_context_factory=grant_permit)
    with psycopg.connect(service.database_url) as connection:
        start_id = connection.execute("SELECT id FROM v4_cycle_outbox WHERE cycle_id=%s AND kind='start'", (admitted["cycle_id"],)).fetchone()[0]
    envelope = {"message_id": str(start_id), "cycle_id": str(admitted["cycle_id"]),
                "kind": "start", "traceparent": service.trace.to_carrier()["traceparent"]}

    class UncertainAdapter:
        effect_id = "fixture.noop"

        def __init__(self):
            self.calls = 0

        async def invoke(self, **kwargs):
            self.calls += 1
            raise RuntimeError("mock acceptance outcome unknown")

    adapter = UncertainAdapter()
    activity = LocalCycleActivities(outbox, adapter=adapter)
    with pytest.raises(RuntimeError, match="unknown"):
        await activity.consume(envelope)
    assert (await activity.consume(envelope))["state"] == "held"
    assert adapter.calls == 1
    with psycopg.connect(service.database_url) as connection:
        assert connection.execute("SELECT state FROM v4_permit_claims WHERE cycle_id=%s", (admitted["cycle_id"],)).fetchone()[0] == "unknown"
        assert connection.execute("SELECT count(*) FROM v4_cycle_events WHERE cycle_id=%s AND kind='fixture_adapter_accepted'", (admitted["cycle_id"],)).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_stopped_cycle_never_calls_mock_adapter(monkeypatch):
    from salience.cycles.governance import CycleGovernance
    from salience.cycles.runtime import LocalCycleActivities

    monkeypatch.setenv("SALIENCE_DEPLOYMENT_MODE", "fixture")

    def grant_permit_and_stop(database, workspace, subject):
        with psycopg.connect(database) as connection:
            for scope in ("cycles:permit", "cycles:stop"):
                connection.execute("INSERT INTO permission_grants (workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at) VALUES (%s,'identity',%s,%s,'allow','{}',now()+interval '1 hour')", (workspace, str(subject), scope))

    service, outbox, admitted = scenario(2, trace_context_factory=grant_permit_and_stop)
    governance = CycleGovernance(service.database_url, workspace_id=service.workspace_id,
                                 subject_id=service.subject_id, trace_context=service.trace)
    with psycopg.connect(service.database_url) as connection:
        start_id, goal_id = connection.execute("SELECT id,goal_id FROM v4_cycle_outbox WHERE cycle_id=%s AND kind='start'", (admitted["cycle_id"],)).fetchone()
    governance.set_stop(goal_id=goal_id, stopped=True,
                        expected_revision=1, idempotency_key="fixture-stop", reason="hold")

    class MustNotInvoke:
        effect_id = "fixture.noop"

        async def invoke(self, **kwargs):
            raise AssertionError("stopped fixture reached adapter")

    with pytest.raises(PermissionError, match="stop"):
        await LocalCycleActivities(outbox, adapter=MustNotInvoke()).consume(
            {"message_id": str(start_id), "cycle_id": str(admitted["cycle_id"]),
             "kind": "start", "traceparent": service.trace.to_carrier()["traceparent"]}
        )
    with psycopg.connect(service.database_url) as connection:
        assert connection.execute("SELECT count(*) FROM v4_permit_claims WHERE cycle_id=%s", (admitted["cycle_id"],)).fetchone()[0] == 0


def test_local_cycle_transport_rejects_production_queue():
    with pytest.raises(ValueError,match="local fixture"):
        TemporalCycleTransport(None,task_queue="production")
