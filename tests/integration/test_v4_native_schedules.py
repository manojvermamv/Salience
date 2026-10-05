"""Qualify conversion of schedules created by the original control plane."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import json
import os
import subprocess
import sys
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
import pytest
from temporalio.client import Client

from test_v4_cycle_admission import cycles, approved_goal
from test_v4_cadence_policy import policy


@asynccontextmanager
async def native_schedule(policy, monkeypatch, *, interval=60):
    from salience.api.dependencies import TemporalControlPlane
    from salience.cycles.contracts import CadencePolicy
    from salience.cycles.legacy_dispatch import LegacyDispatch
    from salience.cycles.schedule_cutover import CycleScheduleCutover
    from salience.cycles.temporal_schedule_control import TemporalFixtureLegacyScheduleControl
    monkeypatch.setenv("SALIENCE_DEPLOYMENT_MODE", "fixture")
    monkeypatch.setenv("SALIENCE_EFFECTS_ENABLED", "false")
    service, _, old_spec, database = policy
    queue = "salience-v4-local-legacy-native-" + str(uuid4())
    plane = TemporalControlPlane(database_url=database.replace("postgresql://", "postgresql+asyncpg://"),
        temporal_target=os.environ["TEST_TEMPORAL_TARGET"], task_queue=queue)
    created = await plane.create_intelligence_schedule(workspace_id=str(service.workspace_id),
        content_program_id=str(old_spec.content_program_id), name="Native qualification " + str(uuid4()),
        every_seconds=interval, niche="Fixture")
    schedule_id = UUID(created.schedule_id)
    client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    handle = client.get_schedule_handle(str(schedule_id))
    try:
        description = await handle.describe()
        # Pick a genuine remote slot with enough time for authenticated setup.
        first = next(slot for slot in description.info.next_action_times
            if slot > datetime.now(timezone.utc) + timedelta(seconds=12))
        spec = old_spec.model_copy(update={"cadence": CadencePolicy(anchor=first, interval_seconds=interval)})
        goal = approved_goal(service, spec)
        with psycopg.connect(database, row_factory=dict_row) as connection:
            for scope in ("cycles:schedule", "cycles:permit", "cycles:read", "cycles:stop"):
                connection.execute("INSERT INTO permission_grants(workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at) VALUES(%s,'identity',%s,%s,'allow','{}',now()+interval '1 hour')", (service.workspace_id, str(service.subject_id), scope))
            original = connection.execute("SELECT to_jsonb(schedule)-ARRAY['status','next_run_at','updated_at'] AS identity FROM job_schedules schedule WHERE id=%s", (schedule_id,)).fetchone()["identity"]
        control = TemporalFixtureLegacyScheduleControl(database, workspace_id=service.workspace_id,
            temporal_target=os.environ["TEST_TEMPORAL_TARGET"], task_queue=queue)
        scheduler = CycleScheduleCutover(database, workspace_id=service.workspace_id,
            subject_id=service.subject_id, legacy_control=control)
        bridge = LegacyDispatch(database, workspace_id=service.workspace_id,
            subject_id=service.subject_id, task_queue=queue)
        yield {"service": service, "database": database, "client": client, "handle": handle,
            "schedule_id": schedule_id, "goal": goal, "spec": spec, "first": first,
            "control": control, "scheduler": scheduler, "bridge": bridge,
            "original": original, "plane": plane}
    finally:
        await handle.pause()
        description = await handle.describe()
        for action in description.info.running_actions:
            await client.get_workflow_handle(action.workflow_id).terminate(reason="End disposable native fixture")
        await handle.delete()


def adopt_arguments(native):
    return {"schedule_id": native["schedule_id"], "subject_id": native["service"].subject_id,
        "expected_revision": 1, "first_v4_slot": native["first"]}


@pytest.mark.asyncio
async def test_native_adoption_atomic_replay_scope_and_identity(policy, monkeypatch):
    async with native_schedule(policy, monkeypatch) as n:
        service, control, database = n["service"], n["control"], n["database"]
        original_event = service.__class__._event
        def crash(*args, **kwargs):
            original_event(*args, **kwargs)
            raise RuntimeError("Native adoption before commit")
        monkeypatch.setattr(service.__class__, "_event", crash)
        with pytest.raises(RuntimeError, match="before commit"):
            await asyncio.to_thread(control.adopt_native, n["goal"], **adopt_arguments(n))
        with psycopg.connect(database) as connection:
            assert connection.execute("SELECT count(*) FROM v4_native_schedule_sources WHERE schedule_id=%s", (n["schedule_id"],)).fetchone()[0] == 0
            assert connection.execute("SELECT count(*) FROM v4_native_schedule_progress WHERE schedule_id=%s", (n["schedule_id"],)).fetchone()[0] == 0
        monkeypatch.setattr(service.__class__, "_event", original_event)
        results = await asyncio.gather(*(asyncio.to_thread(control.adopt_native, n["goal"], **adopt_arguments(n)) for _ in range(3)))
        assert results[0] == results[1] == results[2]
        assert results[0]["schedule_id"] == results[0]["remote_id"] == str(n["schedule_id"])
        with psycopg.connect(database, row_factory=dict_row) as connection:
            source = connection.execute("SELECT * FROM v4_native_schedule_sources WHERE schedule_id=%s", (n["schedule_id"],)).fetchone()
            assert source["original_identity"] == n["original"]
            assert source["observed_last_slot"] is None and source["remote_snapshot"]["action_count"] == 0
            assert connection.execute("SELECT last_slot FROM v4_native_schedule_progress WHERE schedule_id=%s", (n["schedule_id"],)).fetchone()["last_slot"] is None
            for statement in ("UPDATE job_schedules SET payload='{}' WHERE id=%s", "DELETE FROM job_schedules WHERE id=%s",
                    "UPDATE v4_native_schedule_sources SET remote_snapshot='{}' WHERE schedule_id=%s"):
                with pytest.raises(psycopg.Error, match="immutable"), connection.transaction():
                    connection.execute(statement, (n["schedule_id"],))
        other_goal = approved_goal(service, n["spec"])
        with pytest.raises(ValueError, match="conflict"):
            await asyncio.to_thread(control.adopt_native, other_goal, **adopt_arguments(n))
        with pytest.raises(PermissionError, match="original native"):
            n["scheduler"].prepare(other_goal, legacy_schedule_id=n["schedule_id"], expected_revision=1,
                first_v4_slot=n["first"], idempotency_key="foreign-goal")
        await asyncio.to_thread(n["scheduler"].prepare, n["goal"], legacy_schedule_id=n["schedule_id"],
            expected_revision=1, first_v4_slot=n["first"], idempotency_key="prepare")
        with pytest.raises(PermissionError, match="original native workload"):
            n["bridge"].bind_schedule(n["goal"], expected_revision=1, niche="Changed")
        n["bridge"].bind_schedule(n["goal"], expected_revision=1, niche="Fixture")
        # The original database upsert cannot silently change a sealed native identity.
        with pytest.raises(psycopg.Error, match="immutable"):
            with psycopg.connect(database) as connection:
                connection.execute("UPDATE job_schedules SET name='Changed' WHERE id=%s", (n["schedule_id"],))
        result = await asyncio.to_thread(subprocess.run, [sys.executable, "-m", "alembic", "-x", "database_url="+database,
            "downgrade", "0036_legacy_cycle_dispatch"], capture_output=True, text=True)
        assert result.returncode != 0 and any(reason in result.stderr for reason in ("preserve original native schedule history", "preserve original legacy stage history"))
        with psycopg.connect(database) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0038_legacy_dummy_stage"
            assert connection.execute("SELECT count(*) FROM v4_native_schedule_sources WHERE schedule_id=%s", (n["schedule_id"],)).fetchone()[0] == 1
        with psycopg.connect(database) as connection:
            connection.execute("UPDATE identity_subjects SET enabled=false WHERE id=%s", (service.subject_id,))
        with pytest.raises(PermissionError, match="subject authority"):
            await asyncio.to_thread(control.adopt_native, n["goal"], **adopt_arguments(n))


@pytest.mark.asyncio
async def test_actual_native_schedule_pause_rollback_tick_and_outbox(policy, monkeypatch):
    from salience.cycles.legacy_runtime import build_legacy_worker
    from salience.cycles.legacy_schedule_workflow import LEGACY_SCHEDULE_INGRESS
    from salience.cycles.outbox import CycleOutbox
    from salience.cycles.runtime import TemporalCycleTransport
    async with native_schedule(policy, monkeypatch, interval=5) as n:
        await asyncio.to_thread(n["control"].adopt_native, n["goal"], **adopt_arguments(n))
        await asyncio.to_thread(n["scheduler"].prepare, n["goal"], legacy_schedule_id=n["schedule_id"],
            expected_revision=1, first_v4_slot=n["first"], idempotency_key="prepare")
        n["bridge"].bind_schedule(n["goal"], expected_revision=1, niche="Fixture")
        # A real native old execution must physically drain before activation.
        await n["handle"].trigger()
        async with asyncio.timeout(10):
            while not (description := await n["handle"].describe()).info.running_actions:
                await asyncio.sleep(.05)
        with pytest.raises(ValueError, match="in-flight legacy actions"):
            await asyncio.to_thread(n["scheduler"].activate, n["goal"], idempotency_key="activate")
        for old in description.info.running_actions:
            await n["client"].get_workflow_handle(old.workflow_id).terminate(reason="Disposable native fixture drain")
        async with asyncio.timeout(10):
            while (await n["handle"].describe()).info.running_actions:
                await asyncio.sleep(.05)
        assert (await asyncio.to_thread(n["scheduler"].activate, n["goal"], idempotency_key="activate"))["state"] == "active"
        assert (await n["handle"].describe()).schedule.action.workflow == "IntelligenceLoopWorkflow"
        assert (await n["handle"].describe()).schedule.state.paused
        from temporalio.client import ScheduleHandle
        original_update = ScheduleHandle.update
        calls = []
        async def lost_ack(handle, updater, **kwargs):
            await original_update(handle, updater, **kwargs)
            calls.append(handle.id)
            raise TimeoutError("Native update committed; acknowledgment lost")
        monkeypatch.setattr(ScheduleHandle, "update", lost_ack)
        rolled = await asyncio.to_thread(n["scheduler"].rollback, n["goal"], idempotency_key="rollback")
        assert rolled["state"] == "rolled_back" and calls == [str(n["schedule_id"])]
        assert await asyncio.to_thread(n["scheduler"].rollback, n["goal"], idempotency_key="rollback") == rolled
        assert calls == [str(n["schedule_id"])]
        monkeypatch.setattr(ScheduleHandle, "update", original_update)
        assert (await n["handle"].describe()).schedule.action.workflow == LEGACY_SCHEDULE_INGRESS
        assert n["scheduler"].poll(n["goal"], expected_revision=1, idempotency_key="fenced")["state"] == "rolled_back"
        box = CycleOutbox(n["database"], workspace_id=n["service"].workspace_id, delivery_lane="legacy")
        async with build_legacy_worker(n["client"], task_queue=n["bridge"].task_queue, outbox=box):
            async with asyncio.timeout(35):
                while True:
                    with psycopg.connect(n["database"]) as connection:
                        row = connection.execute("SELECT binding.cycle_id,binding.operation_id,binding.job_id,intent.due_at FROM v4_legacy_dispatches binding JOIN v4_cycle_intents intent ON intent.id=binding.intent_id WHERE binding.goal_id=%s ORDER BY intent.due_at LIMIT 1", (n["goal"],)).fetchone()
                    if row: break
                    await asyncio.sleep(.05)
            await n["handle"].pause()
            cycle_id, operation_id, job_id, slot = row
            replay = n["bridge"].admit_legacy_tick(n["goal"], schedule_id=n["schedule_id"], scheduled_at=slot, remote_id=str(n["schedule_id"]))
            assert replay["cycle_id"] == str(cycle_id) and slot >= n["first"]
            transport = TemporalCycleTransport(n["client"], task_queue=n["bridge"].task_queue)
            assert await box.dispatch_one(transport)
            execution = n["client"].get_workflow_handle("salience-v4-legacy-operation:"+str(operation_id))
            async with asyncio.timeout(10):
                while True:
                    try:
                        await execution.describe()
                        break
                    except Exception: await asyncio.sleep(.05)
            result = await asyncio.wait_for(execution.result(), 20)
            assert result["job_id"] == str(job_id) and result["state"] == "completed"
            assert n["bridge"].inspect(job_id)["state"] == "succeeded"
            n["service"].close(cycle_id, disposition="completed", reason="Original native schedule verified")
            assert await box.dispatch_one(transport)
            await asyncio.wait_for(n["client"].get_workflow_handle(transport.workflow_id(cycle_id)).result(), 10)
        with psycopg.connect(n["database"], row_factory=dict_row) as connection:
            identity = connection.execute("SELECT to_jsonb(schedule)-ARRAY['status','next_run_at','updated_at'] AS identity FROM job_schedules schedule WHERE id=%s", (n["schedule_id"],)).fetchone()["identity"]
            assert identity == n["original"] and identity["job_type"] == "intelligence_research"
            assert identity["schedule_expression"] == "every 5s" and identity["payload"] == {"niche": "Fixture", "dry_run": True}
            progress = connection.execute("SELECT * FROM v4_native_schedule_progress WHERE schedule_id=%s", (n["schedule_id"],)).fetchone()
            assert progress["last_slot"] == slot and progress["next_slot"] > progress["resume_after"]
            assert connection.execute("SELECT count(*) FROM v4_cycle_outbox WHERE cycle_id=%s AND kind='start'", (cycle_id,)).fetchone()["count"] == 1


@pytest.mark.asyncio
async def test_native_adoption_denies_wrong_queue_slot_and_live_effects(policy, monkeypatch):
    from salience.cycles.temporal_schedule_control import TemporalFixtureLegacyScheduleControl
    async with native_schedule(policy, monkeypatch) as n:
        wrong = TemporalFixtureLegacyScheduleControl(n["database"], workspace_id=n["service"].workspace_id,
            temporal_target=os.environ["TEST_TEMPORAL_TARGET"], task_queue="salience-v4-local-legacy-wrong")
        with pytest.raises(PermissionError, match="native Temporal action"):
            await asyncio.to_thread(wrong.adopt_native, n["goal"], **adopt_arguments(n))
        with pytest.raises(ValueError, match="aligned native source"):
            await asyncio.to_thread(n["control"].adopt_native, n["goal"], **(adopt_arguments(n)|{"first_v4_slot": n["first"]+timedelta(seconds=1)}))
        monkeypatch.setenv("SALIENCE_EFFECTS_ENABLED", "true")
        with pytest.raises(PermissionError, match="effects"):
            await asyncio.to_thread(n["control"].adopt_native, n["goal"], **adopt_arguments(n))
        with psycopg.connect(n["database"]) as connection:
            assert connection.execute("SELECT count(*) FROM v4_native_schedule_sources WHERE schedule_id=%s", (n["schedule_id"],)).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_signed_native_schedule_sdk_cli_and_current_grants(policy, monkeypatch, capsys):
    import httpx
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    from fastapi.testclient import TestClient
    from salience.api.p0 import create_p0_app
    from salience.sdk.client import SalienceClient
    from salience.cli import main
    async with native_schedule(policy, monkeypatch) as n:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        app = create_p0_app(database_url=n["database"], workspace_id=n["service"].workspace_id,
            issuer="https://fixture.invalid", audience="fixture", public_key=key.public_key(),
            enable_legacy_dispatch=True, legacy_fixture_queue=n["bridge"].task_queue,
            legacy_temporal_target=os.environ["TEST_TEMPORAL_TARGET"])
        now = datetime.now(timezone.utc)
        token = jwt.encode({"iss":"https://fixture.invalid", "aud":"fixture", "sub":str(n["service"].subject_id),
            "iat":now, "nbf":now, "exp":now+timedelta(minutes=5)}, key, algorithm="RS256")
        command = {"legacy_schedule_id":str(n["schedule_id"]), "expected_revision":1, "first_v4_slot":n["first"].isoformat()}
        def request_and_replay():
            with TestClient(app) as client:
                monkeypatch.setattr(httpx, "request", lambda method,url,**kwargs:client.request(method,url,**{k:v for k,v in kwargs.items() if k!="timeout"}))
                sdk = SalienceClient("http://testserver",token).legacy_intelligence
                result = sdk.adopt_native_schedule(str(n["goal"]), command)
                monkeypatch.setenv("SALIENCE_CONTROL_JWT",token)
                monkeypatch.setenv("SALIENCE_CONTROL_URL","http://testserver")
                main(["legacy-intelligence", "adopt-native-schedule", str(n["goal"]), "--command-json", json.dumps(command)])
                assert json.loads(capsys.readouterr().out) == result
                headers = {"Authorization":"Bearer "+token, "X-Salience-Scopes":"admin:*,cycles:schedule"}
                path = f'/v1/intelligence/schedules/{n["goal"]}/adopt-native'
                assert client.post(path, headers=headers, json=command|{"expected_revision":True}).status_code == 422
                assert client.post(path, headers=headers, json=command|{"legacy_schedule_id":str(uuid4())}).status_code == 403
                assert client.post('/v1/intelligence/schedules/'+str(uuid4())+'/adopt-native', headers=headers,json=command).status_code == 403
                with psycopg.connect(n["database"]) as connection:
                    connection.execute("UPDATE permission_grants SET effect='deny' WHERE principal_id=%s AND scope='cycles:schedule'",(str(n["service"].subject_id),))
                assert client.post(path,headers=headers,json=command).status_code == 403
                assert client.post('/v1/intelligence/schedules',headers=headers,json={}).status_code == 403
                return result
        result = await asyncio.to_thread(request_and_replay)
        assert result["remote_id"] == str(n["schedule_id"]) and result["state"] == "bound"


@pytest.mark.asyncio
async def test_native_snapshot_denies_remote_payload_drift_and_local_readback_race(policy, monkeypatch):
    from temporalio.client import ScheduleUpdate
    async with native_schedule(policy, monkeypatch) as n:
        description = await n["handle"].describe()
        action = description.schedule.action
        original_args = action.args
        action.args = [{"workspace_id":str(n["service"].workspace_id), "dry_run":True}]
        await n["handle"].update(lambda _: ScheduleUpdate(schedule=description.schedule))
        with pytest.raises(PermissionError, match="native schedule payload"):
            await asyncio.to_thread(n["control"].adopt_native, n["goal"], **adopt_arguments(n))
        action.args = original_args
        await n["handle"].update(lambda _: ScheduleUpdate(schedule=description.schedule))
        original_snapshot = n["control"]._native_snapshot
        async def drift(row, slot):
            result = await original_snapshot(row, slot)
            with psycopg.connect(n["database"]) as connection:
                connection.execute("UPDATE job_schedules SET name=name||' changed' WHERE id=%s",(n["schedule_id"],))
            return result
        monkeypatch.setattr(n["control"],"_native_snapshot",drift)
        with pytest.raises(ValueError, match="changed during remote readback"):
            await asyncio.to_thread(n["control"].adopt_native,n["goal"],**adopt_arguments(n))
        with psycopg.connect(n["database"]) as connection:
            assert connection.execute("SELECT count(*) FROM v4_native_schedule_sources WHERE schedule_id=%s",(n["schedule_id"],)).fetchone()[0] == 0
            assert connection.execute("SELECT count(*) FROM v4_native_schedule_progress WHERE schedule_id=%s",(n["schedule_id"],)).fetchone()[0] == 0


def test_native_migration_empty_rollback_restores_original_cutover_guard():
    from urllib.parse import urlsplit, urlunsplit
    source = os.environ["TEST_DATABASE_URL"]
    name = "native_migration_"+uuid4().hex
    database = urlunsplit(urlsplit(source)._replace(path="/"+name))
    with psycopg.connect(source,autocommit=True) as admin:
        admin.execute(psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(name)))
        try:
            def migrate(direction,target):
                result = subprocess.run([sys.executable,"-m","alembic","-x","database_url="+database,direction,target],capture_output=True,text=True)
                assert result.returncode == 0, result.stderr
            def guard():
                with psycopg.connect(database) as connection:
                    return connection.execute("SELECT pg_get_functiondef('v4_preserve_schedule_cutover()'::regprocedure)").fetchone()[0]
            migrate("upgrade","0036_legacy_cycle_dispatch")
            original = guard()
            migrate("upgrade","head")
            assert guard() != original
            migrate("downgrade","0036_legacy_cycle_dispatch")
            assert guard() == original
            migrate("upgrade","head")
        finally:
            admin.execute(psycopg.sql.SQL("DROP DATABASE {} WITH (FORCE)").format(psycopg.sql.Identifier(name)))
