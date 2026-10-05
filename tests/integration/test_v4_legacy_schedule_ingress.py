"""Actual resumed Temporal ticks must commit authority before dispatch."""

import asyncio
from datetime import datetime, timedelta, timezone
import os
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest
from temporalio.client import Client, Schedule, ScheduleActionStartWorkflow, ScheduleIntervalSpec, ScheduleSpec

from test_v4_cycle_admission import cycles, approved_goal
from test_v4_cadence_policy import policy
from test_v4_schedule_cutover import cutover


@pytest.fixture
def resumed_tick(cutover, monkeypatch):
    """Domain fixture only; actual remote conversion is tested below."""
    import time
    from salience.cycles.legacy_dispatch import LegacyDispatch
    scheduler, service, goal, spec, schedule_id, database = cutover
    monkeypatch.setenv("SALIENCE_DEPLOYMENT_MODE", "fixture")
    monkeypatch.setenv("SALIENCE_EFFECTS_ENABLED", "false")
    bridge = LegacyDispatch(database, workspace_id=service.workspace_id,
        subject_id=service.subject_id, task_queue="salience-v4-local-legacy-domain-"+str(uuid4()))
    scheduler.prepare(goal, legacy_schedule_id=schedule_id, expected_revision=1,
        first_v4_slot=spec.cadence.anchor, idempotency_key="prepare")
    bridge.bind_schedule(goal, expected_revision=1, niche="Fixture")
    scheduler.activate(goal, idempotency_key="activate")
    scheduler.rollback(goal, idempotency_key="rollback")
    remote_id = f"salience-v4-legacy-fixture:{service.workspace_id}:{schedule_id}"
    with psycopg.connect(database) as connection:
        connection.execute("UPDATE job_schedules SET payload=jsonb_set(payload,'{temporal_schedule_id}',to_jsonb(%s::text)) WHERE id=%s", (remote_id, schedule_id))
    time.sleep(max(0, (spec.cadence.anchor-datetime.now(timezone.utc)).total_seconds()))
    return bridge, service, goal, database, {"schedule_id": schedule_id,
        "scheduled_at": spec.cadence.anchor, "remote_id": remote_id}


def test_resumed_tick_crash_is_atomic_and_concurrent_replay_keeps_one_job(resumed_tick, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    bridge, service, goal, database, arguments = resumed_tick
    original = bridge._materialize
    def crash(*args):
        original(*args)
        raise RuntimeError("scheduled admission before commit")
    monkeypatch.setattr(bridge, "_materialize", crash)
    with pytest.raises(RuntimeError, match="before commit"):
        bridge.admit_legacy_tick(goal, **arguments)
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM v4_cycle_intents WHERE goal_id=%s", (goal,)).fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM jobs WHERE workspace_id=%s", (service.workspace_id,)).fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM v4_legacy_commands WHERE workspace_id=%s", (service.workspace_id,)).fetchone()[0] == 0
    monkeypatch.setattr(bridge, "_materialize", original)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: bridge.admit_legacy_tick(goal, **arguments), range(3)))
    assert results[0] == results[1] == results[2] and results[0]["state"] == "queued"
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM jobs WHERE workspace_id=%s", (service.workspace_id,)).fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM v4_cycle_outbox WHERE cycle_id=%s", (results[0]["cycle_id"],)).fetchone()[0] == 1


def test_resumed_tick_rejects_watermark_foreign_schedule_stop_and_revocation(resumed_tick):
    from salience.cycles.governance import CycleGovernance
    bridge, service, goal, database, arguments = resumed_tick
    with pytest.raises(PermissionError, match="later scheduled slot"):
        bridge.admit_legacy_tick(goal, **(arguments | {"scheduled_at": arguments["scheduled_at"]-timedelta(seconds=1)}))
    with pytest.raises(PermissionError, match="resumed Temporal"):
        bridge.admit_legacy_tick(goal, **(arguments | {"remote_id": "foreign"}))
    with psycopg.connect(database) as connection:
        connection.execute("INSERT INTO permission_grants(workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at) VALUES(%s,'identity',%s,'cycles:stop','allow','{}',now()+interval '1 hour')", (service.workspace_id, str(service.subject_id)))
    governance = CycleGovernance(database, workspace_id=service.workspace_id, subject_id=service.subject_id)
    governance.set_stop(goal_id=goal, stopped=True, expected_revision=1, idempotency_key="stop", reason="Deny scheduled tick")
    with pytest.raises(PermissionError, match="stop"):
        bridge.admit_legacy_tick(goal, **arguments)
    with psycopg.connect(database) as connection:
        connection.execute("UPDATE identity_subjects SET enabled=false WHERE id=%s", (service.subject_id,))
    with pytest.raises(PermissionError, match="subject authority"):
        bridge.admit_legacy_tick(goal, **arguments)
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM jobs WHERE workspace_id=%s", (service.workspace_id,)).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_real_resumed_schedule_tick_commits_original_outbox_then_executes(policy, monkeypatch):
    from salience.cycles.contracts import CadencePolicy
    from salience.cycles.legacy_dispatch import LegacyDispatch
    from salience.cycles.legacy_runtime import build_legacy_worker
    from salience.cycles.legacy_schedule_workflow import LEGACY_SCHEDULE_INGRESS
    from salience.cycles.outbox import CycleOutbox
    from salience.cycles.runtime import TemporalCycleTransport
    from salience.cycles.schedule_cutover import CycleScheduleCutover
    from salience.cycles.temporal_schedule_control import TemporalFixtureLegacyScheduleControl

    monkeypatch.setenv("SALIENCE_DEPLOYMENT_MODE", "fixture")
    monkeypatch.setenv("SALIENCE_EFFECTS_ENABLED", "false")
    service, _, old_spec, database = policy
    anchor = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=10)
    spec = old_spec.model_copy(update={"cadence": CadencePolicy(anchor=anchor, interval_seconds=2)})
    goal = approved_goal(service, spec)
    schedule_id = uuid4()
    remote_id = f"salience-v4-legacy-fixture:{service.workspace_id}:{schedule_id}"
    queue = "salience-v4-local-legacy-ingress-" + str(uuid4())
    with psycopg.connect(database) as connection:
        for scope in ("cycles:schedule", "cycles:permit", "cycles:read"):
            connection.execute("INSERT INTO permission_grants(workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at) VALUES(%s,'identity',%s,%s,'allow','{}',now()+interval '1 hour')", (service.workspace_id, str(service.subject_id), scope))
        connection.execute("INSERT INTO job_schedules(id,workspace_id,content_program_id,name,schedule_expression,timezone,job_type,payload,next_run_at) VALUES(%s,%s,%s,%s,'every:2s','UTC','v4_fixture_legacy',%s,%s)", (schedule_id, service.workspace_id, spec.content_program_id, str(schedule_id), Jsonb({"dry_run": True, "last_slot": (anchor-timedelta(seconds=2)).isoformat(), "temporal_schedule_id": remote_id}), anchor))
    client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    payload = {"workspace_id": str(service.workspace_id), "content_program_id": str(spec.content_program_id), "niche": "Fixture", "idempotency_key": "old-schedule", "dry_run": True}
    await client.create_schedule(remote_id, Schedule(action=ScheduleActionStartWorkflow("IntelligenceLoopWorkflow", payload, id=remote_id+":old", task_queue=queue), spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=timedelta(seconds=2), offset=timedelta(seconds=anchor.timestamp()%2))], start_at=anchor)))
    control = TemporalFixtureLegacyScheduleControl(database, workspace_id=service.workspace_id, temporal_target=os.environ["TEST_TEMPORAL_TARGET"], task_queue=queue)
    scheduler = CycleScheduleCutover(database, workspace_id=service.workspace_id, subject_id=service.subject_id, legacy_control=control)
    bridge = LegacyDispatch(database, workspace_id=service.workspace_id, subject_id=service.subject_id, task_queue=queue)
    box = CycleOutbox(database, workspace_id=service.workspace_id, delivery_lane="legacy")
    try:
        await asyncio.to_thread(scheduler.prepare, goal, legacy_schedule_id=schedule_id, expected_revision=1, first_v4_slot=anchor, idempotency_key="prepare")
        await asyncio.to_thread(bridge.bind_schedule, goal, expected_revision=1, niche="Fixture")
        await asyncio.to_thread(scheduler.activate, goal, idempotency_key="activate")
        # Active V4 fencing denies a legacy tick, regardless of a plausible slot.
        with pytest.raises(PermissionError, match="completed rollback"):
            bridge.admit_legacy_tick(goal, schedule_id=schedule_id, scheduled_at=anchor, remote_id=remote_id)
        from temporalio.client import ScheduleHandle
        original_update = ScheduleHandle.update
        update_calls = []
        async def lost_ack(handle, updater, **kwargs):
            await original_update(handle, updater, **kwargs)
            update_calls.append(handle.id)
            raise TimeoutError("Remote schedule update accepted; acknowledgment lost")
        monkeypatch.setattr(ScheduleHandle, "update", lost_ack)
        rolled = await asyncio.to_thread(scheduler.rollback, goal, idempotency_key="rollback")
        assert rolled["state"] == "rolled_back" and update_calls == [remote_id]
        assert await asyncio.to_thread(scheduler.rollback, goal, idempotency_key="rollback") == rolled
        assert update_calls == [remote_id]
        monkeypatch.setattr(ScheduleHandle, "update", original_update)
        description = await client.get_schedule_handle(remote_id).describe()
        assert description.schedule.action.workflow == LEGACY_SCHEDULE_INGRESS
        assert description.schedule.spec.start_at >= anchor
        assert scheduler.poll(goal, expected_revision=1, idempotency_key="fenced-poll")["state"] == "rolled_back"
        async with build_legacy_worker(client, task_queue=queue, outbox=box):
            from temporalio.common import SearchAttributeKey, SearchAttributePair, TypedSearchAttributes
            from temporalio.client import WorkflowFailureError
            ingress_payload = {"workspace_id": str(service.workspace_id), "content_program_id": str(spec.content_program_id), "goal_id": str(goal), "schedule_id": str(schedule_id), "dry_run": True}
            for attributes in (None, TypedSearchAttributes([
                    SearchAttributePair(SearchAttributeKey.for_datetime("TemporalScheduledStartTime"), anchor),
                    SearchAttributePair(SearchAttributeKey.for_keyword("TemporalScheduledById"), remote_id)])):
                forged = await client.start_workflow(LEGACY_SCHEDULE_INGRESS, ingress_payload,
                    id="forged-legacy-ingress-"+str(uuid4()), task_queue=queue,
                    search_attributes=attributes, execution_timeout=timedelta(seconds=35))
                with pytest.raises(WorkflowFailureError):
                    await asyncio.wait_for(forged.result(), 15)
            async with asyncio.timeout(25):
                while True:
                    with psycopg.connect(database) as connection:
                        row = connection.execute("SELECT binding.cycle_id,binding.operation_id,binding.job_id,intent.due_at FROM v4_legacy_dispatches binding JOIN v4_cycle_intents intent ON intent.id=binding.intent_id WHERE binding.goal_id=%s ORDER BY intent.due_at LIMIT 1", (goal,)).fetchone()
                    if row:
                        break
                    await asyncio.sleep(0.05)
            await client.get_schedule_handle(remote_id).pause()
            cycle_id, operation_id, job_id, slot = row
            assert slot >= anchor
            first = bridge.admit_legacy_tick(goal, schedule_id=schedule_id, scheduled_at=slot, remote_id=remote_id)
            replay = bridge.admit_legacy_tick(goal, schedule_id=schedule_id, scheduled_at=slot, remote_id=remote_id)
            assert first == replay and first["cycle_id"] == str(cycle_id)
            with psycopg.connect(database) as connection:
                assert connection.execute("SELECT count(*) FROM v4_cycle_outbox WHERE cycle_id=%s AND kind='start'", (cycle_id,)).fetchone()[0] == 1
                assert connection.execute("SELECT state FROM jobs WHERE id=%s", (job_id,)).fetchone()[0] == "queued"
            transport = TemporalCycleTransport(client, task_queue=queue)
            assert await box.dispatch_one(transport)
            execution = client.get_workflow_handle("salience-v4-legacy-operation:"+str(operation_id))
            async with asyncio.timeout(10):
                while True:
                    try:
                        await execution.describe()
                        break
                    except Exception:
                        await asyncio.sleep(0.05)
            result = await asyncio.wait_for(execution.result(), 20)
            assert result["job_id"] == str(job_id) and result["state"] == "completed"
            assert bridge.inspect(job_id)["state"] == "succeeded"
            service.close(cycle_id, disposition="completed", reason="Resumed schedule fixture verified")
            assert await box.dispatch_one(transport)
            await asyncio.wait_for(client.get_workflow_handle(transport.workflow_id(cycle_id)).result(), 10)
            assert (await execution.describe()).run_id == (await client.get_workflow_handle("salience-v4-legacy-operation:"+str(operation_id)).describe()).run_id
    finally:
        await client.get_schedule_handle(remote_id).delete()
