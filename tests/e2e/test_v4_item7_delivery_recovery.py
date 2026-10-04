"""Review regressions: runtime acceptance must not hide failed consumption."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
import subprocess
import sys
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg
import pytest
from temporalio import activity
from temporalio.client import Client, WorkflowFailureError, WorkflowExecutionStatus
from temporalio.worker import Worker

from test_v4_outbox_recovery import scenario, inbox_count, wait_consumed
from salience.cycles.contracts import parse_goal
from salience.cycles.runtime import LocalCycleActivities, TemporalCycleTransport, build_local_cycle_worker
from salience.cycles.workflow import LocalCycleWorkflow


class UnavailableBinding:
    @activity.defn(name="salience.v4.fixture_runtime_binding")
    async def binding(self, message: dict) -> dict:
        raise psycopg.OperationalError("review fixture database temporarily unavailable")


class UnavailableConsumption:
    def __init__(self, local, failure_kind):
        self.local, self.failure_kind = local, failure_kind

    @activity.defn(name="salience.v4.fixture_consume")
    async def consume(self, message: dict) -> dict:
        if message["kind"] == self.failure_kind:
            raise psycopg.OperationalError("review fixture consumer temporarily unavailable")
        return await self.local.consume(message)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_point", ["binding", "start", "recovery"])
async def test_acknowledged_start_failure_has_canonical_recovery_after_worker_restart(failure_point):
    service, outbox, admitted = scenario(2)
    client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    queue = "salience-v4-local-item7-review-" + uuid4().hex
    transport = TemporalCycleTransport(client, task_queue=queue)
    activities = LocalCycleActivities(outbox)
    unavailable = UnavailableBinding()
    consumption = UnavailableConsumption(activities, failure_point)
    registered = [activities.runtime_hold,
                  unavailable.binding if failure_point == "binding" else activities.runtime_binding,
                  consumption.consume if failure_point != "binding" else activities.consume]
    async with Worker(client, task_queue=queue, workflows=[LocalCycleWorkflow],
                      activities=registered):
        assert await outbox.dispatch_one(transport)
        handle = client.get_workflow_handle(transport.workflow_id(admitted["cycle_id"]))
        if failure_point == "recovery":
            await wait_consumed(service.database_url, admitted["cycle_id"], 1)
            service.recover(admitted["cycle_id"], state="retry_due")
            assert await outbox.dispatch_one(transport)
        with pytest.raises(WorkflowFailureError):
            await asyncio.wait_for(handle.result(), 15)
        failed_run = (await handle.describe()).run_id
    # The database is healthy throughout this isolated transient-failure model.
    # Registering the normal worker again must leave recoverable canonical work.
    async with build_local_cycle_worker(client, task_queue=queue, outbox=outbox):
        await outbox.dispatch_one(transport)
        # Repeated polling and a restarted dispatcher preserve one receipt.
        restarted = TemporalCycleTransport(client, task_queue=queue)
        for _ in range(3):
            await outbox.dispatch_one(restarted)
    with psycopg.connect(service.database_url) as connection:
        state = connection.execute("SELECT state FROM v4_cycle_outbox WHERE cycle_id=%s AND sequence=1", (admitted["cycle_id"],)).fetchone()[0]
        cycle_state = connection.execute("SELECT state FROM v4_cycles WHERE id=%s", (admitted["cycle_id"],)).fetchone()[0]
        holds = connection.execute("SELECT count(*) FROM v4_runtime_holds WHERE cycle_id=%s", (admitted["cycle_id"],)).fetchone()[0]
        cases = connection.execute("SELECT count(*) FROM v4_recovery_cases WHERE target_cycle_id=%s", (admitted["cycle_id"],)).fetchone()[0]
        deadletters = connection.execute("SELECT count(*) FROM v4_cycle_events WHERE cycle_id=%s AND kind='outbox_dead_letter'", (admitted["cycle_id"],)).fetchone()[0]
        binding = connection.execute("SELECT context_id,operation_id FROM v4_cycles WHERE id=%s", (admitted["cycle_id"],)).fetchone()
        owner_hold = connection.execute("SELECT context_id,operation_id,owner_id,reason FROM v4_runtime_holds WHERE cycle_id=%s", (admitted["cycle_id"],)).fetchone()
        hold_events = connection.execute("SELECT count(*) FROM v4_cycle_events WHERE cycle_id=%s AND kind='runtime_held'", (admitted["cycle_id"],)).fetchone()[0]
        assert connection.execute("SELECT count(*) FROM v4_cycles WHERE intent_id=%s", (admitted["intent_id"],)).fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM v4_cycle_outbox WHERE cycle_id=%s AND kind='start'", (admitted["cycle_id"],)).fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM v4_permit_claims WHERE cycle_id=%s", (admitted["cycle_id"],)).fetchone()[0] == 0
    receipts = inbox_count(service.database_url, admitted["cycle_id"])
    assert receipts or holds or cases or deadletters or state != "delivered", (
        f"terminal failed runtime stranded cycle: outbox={state}, cycle={cycle_state}, "
        f"inbox={receipts}, holds={holds}, cases={cases}, deadletters={deadletters}"
    )
    assert owner_hold == (*binding, service.subject_id, "held_runtime_failure")
    assert holds == hold_events == 1
    assert receipts == (2 if failure_point == "recovery" else 1)
    with psycopg.connect(service.database_url) as connection:
        assert connection.execute("SELECT count(*) FROM v4_cycle_inbox WHERE cycle_id=%s AND state='held'", (admitted["cycle_id"],)).fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM v4_cycle_events WHERE cycle_id=%s AND kind='fixture_adapter_accepted'", (admitted["cycle_id"],)).fetchone()[0] == 0
    assert (await handle.describe()).run_id == failed_run
    service.close(admitted["cycle_id"], disposition="cancelled", reason="Owner closes failed runtime fixture")
    assert await outbox.dispatch_one(restarted), "owner closure must reconcile ordered receipts after failed runtime without signalling it"
    with psycopg.connect(service.database_url) as connection:
        assert connection.execute("SELECT count(*) FROM v4_cycle_outbox WHERE cycle_id=%s AND state<>'delivered'", (admitted["cycle_id"],)).fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM v4_cycle_outbox m WHERE cycle_id=%s AND NOT EXISTS(SELECT 1 FROM v4_cycle_inbox i WHERE i.message_id=m.id)", (admitted["cycle_id"],)).fetchone()[0] == 0


def test_restricted_outbox_writer_cannot_commit_a_foreign_cycle_receipt():
    local, _, local_admitted = scenario(2)
    foreign, foreign_outbox, foreign_admitted = scenario(2)
    foreign_start = foreign_outbox.claim()
    foreign_outbox.consume(foreign_start["id"])
    role = "item7_runtime_writer_" + uuid4().hex
    message_id = uuid4()
    parts = urlsplit(local.database_url)
    restricted_url = urlunsplit(parts._replace(netloc=f"{role}:review-fixture@{parts.hostname}:{parts.port or 5432}"))
    with psycopg.connect(local.database_url, autocommit=True) as admin:
        identifier = psycopg.sql.Identifier(role)
        admin.execute(psycopg.sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'review-fixture' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS").format(identifier))
        try:
            admin.execute(psycopg.sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(identifier))
            admin.execute(psycopg.sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(identifier))
            admin.execute(psycopg.sql.SQL("GRANT INSERT ON v4_cycle_outbox,v4_cycle_inbox,v4_cycle_events TO {}").format(identifier))
            admin.execute(psycopg.sql.SQL("GRANT UPDATE ON v4_cycle_outbox,v4_goals TO {}").format(identifier))
            admin.execute(psycopg.sql.SQL("GRANT EXECUTE ON FUNCTION p0_lock_identity(text,text,uuid),v4_lock_program(uuid,uuid) TO {}").format(identifier))
            with psycopg.connect(restricted_url) as connection:
                assert connection.execute("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user").fetchone() == (False, False)
                with pytest.raises(psycopg.errors.InsufficientPrivilege), connection.transaction():
                    connection.execute("UPDATE identity_subjects SET enabled=false WHERE id=%s", (foreign.subject_id,))
                try:
                    with connection.transaction():
                        connection.execute("""INSERT INTO v4_cycle_outbox
                            (id,workspace_id,subject_id,goal_id,intent_id,cycle_id,sequence,kind,payload,traceparent)
                            SELECT %s,workspace_id,subject_id,goal_id,intent_id,%s,2,'recovery',payload,traceparent
                            FROM v4_cycle_outbox WHERE cycle_id=%s AND sequence=1""",
                            (message_id, foreign_admitted["cycle_id"], local_admitted["cycle_id"]))
                except (psycopg.errors.ForeignKeyViolation, psycopg.errors.RaiseException):
                    return  # A scoped SQL guard is a valid fix.
            scoped = type(foreign_outbox)(restricted_url, workspace_id=local.workspace_id)
            try:
                scoped.consume(message_id, expected_cycle_id=foreign_admitted["cycle_id"], expected_kind="recovery")
            except (PermissionError, ValueError):
                pass
            with psycopg.connect(restricted_url) as connection:
                committed = connection.execute("SELECT count(*) FROM v4_cycle_inbox WHERE message_id=%s AND cycle_id=%s", (message_id, foreign_admitted["cycle_id"])).fetchone()[0]
                assert committed == 0, "restricted workspace-local consumer committed immutable receipt into foreign cycle stream"
        finally:
            admin.execute(psycopg.sql.SQL("DROP OWNED BY {}").format(identifier))
            admin.execute(psycopg.sql.SQL("DROP ROLE {}").format(identifier))


@pytest.mark.asyncio
async def test_owner_closes_failed_cycle_before_dispatcher_observes_failure():
    service, outbox, admitted = scenario(2)
    client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    queue = "salience-v4-local-item7-close-first-"+uuid4().hex
    transport = TemporalCycleTransport(client, task_queue=queue)
    local = LocalCycleActivities(outbox)
    async with Worker(client, task_queue=queue, workflows=[LocalCycleWorkflow],
                      activities=[local.consume, UnavailableBinding().binding, local.runtime_hold]):
        assert await outbox.dispatch_one(transport)
        handle = client.get_workflow_handle(transport.workflow_id(admitted["cycle_id"]))
        with pytest.raises(WorkflowFailureError):
            await asyncio.wait_for(handle.result(), 15)
    failed_run = (await handle.describe()).run_id
    service.close(admitted["cycle_id"], disposition="cancelled", reason="Owner closes before scanner recovers")
    restarted = TemporalCycleTransport(client, task_queue=queue)
    assert await outbox.dispatch_one(restarted), "closed target still needs ordered receipt reconciliation after runtime failure"
    with psycopg.connect(service.database_url) as connection:
        assert connection.execute("SELECT state FROM v4_cycles WHERE id=%s", (admitted["cycle_id"],)).fetchone()[0] == "closed"
        assert connection.execute("SELECT count(*) FROM v4_cycle_outbox WHERE cycle_id=%s AND state<>'delivered'", (admitted["cycle_id"],)).fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM v4_cycle_outbox m WHERE cycle_id=%s AND NOT EXISTS(SELECT 1 FROM v4_cycle_inbox i WHERE i.message_id=m.id)", (admitted["cycle_id"],)).fetchone()[0] == 0
    assert (await handle.describe()).run_id == failed_run


@pytest.mark.asyncio
async def test_reconciliation_scans_fairly_and_observation_failure_never_proves_runtime_failure():
    service, outbox, admitted = scenario()
    with psycopg.connect(service.database_url) as connection:
        spec = parse_goal(connection.execute("SELECT payload FROM v4_goal_revisions WHERE goal_id=(SELECT goal_id FROM v4_cycle_intents WHERE id=%s)", (admitted["intent_id"],)).fetchone()[0])
    for index in range(2):
        goal = service.create_goal(spec)
        now = datetime.now(timezone.utc)
        service.approve_baseline(goal, expected_revision=1, expires_at=now+timedelta(minutes=30), reason="Fair scanner fixture")
        requested = service.request_intent(goal, slot="scan", due_at=now, expires_at=now+timedelta(minutes=5))
        service.admit(requested)
    messages = []
    while message := outbox.claim():
        outbox.ack(message["id"], message["lease_token"])
        messages.append(message)
    ordered = sorted(messages, key=lambda row: row["id"])
    status = {ordered[0]["cycle_id"]: WorkflowExecutionStatus.RUNNING,
              ordered[1]["cycle_id"]: OSError("fixture observation unavailable"),
              ordered[2]["cycle_id"]: WorkflowExecutionStatus.FAILED}
    seen = []

    class Description:
        workflow_type = "SalienceLocalCycleWorkflow"

        def __init__(self, message):
            self.message = message
            self.status = status[message["cycle_id"]]

        async def memo(self):
            return {"cycle_message_id": str(self.message["id"])}

    class FakeClient:
        def get_workflow_handle(self, runtime_id):
            message = next(row for row in messages if TemporalCycleTransport.workflow_id(row["cycle_id"]) == runtime_id)

            async def describe(**kwargs):
                seen.append(message["id"])
                if isinstance(status[message["cycle_id"]], Exception):
                    raise status[message["cycle_id"]]
                return Description(message)

            return SimpleNamespace(describe=describe)

    queue = "salience-v4-local-item7-scan-"+uuid4().hex
    transport = TemporalCycleTransport(FakeClient(), task_queue=queue)
    for _ in range(3):
        await transport.reconcile_one(outbox)
    assert seen == [row["id"] for row in ordered]
    assert not outbox.runtime_held(ordered[0]["cycle_id"])
    assert not outbox.runtime_held(ordered[1]["cycle_id"])
    assert outbox.runtime_held(ordered[2]["cycle_id"])
    # The wrapped cursor eventually observes the formerly unavailable runtime.
    status[ordered[1]["cycle_id"]] = WorkflowExecutionStatus.FAILED
    for _ in range(3):
        await transport.reconcile_one(outbox)
    assert outbox.runtime_held(ordered[1]["cycle_id"])
    restarted = TemporalCycleTransport(FakeClient(), task_queue=queue)
    for _ in range(3):
        await restarted.reconcile_one(outbox)
    assert not outbox.runtime_held(ordered[0]["cycle_id"])
    with psycopg.connect(service.database_url) as connection:
        assert connection.execute("SELECT count(*) FROM v4_runtime_holds WHERE workspace_id=%s", (service.workspace_id,)).fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM v4_cycle_events WHERE workspace_id=%s AND kind='runtime_held'", (service.workspace_id,)).fetchone()[0] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["type", "memo"])
async def test_failed_observation_requires_original_workflow_type_and_start_memo(mismatch):
    _, outbox, admitted = scenario()
    start = outbox.claim()
    outbox.ack(start["id"], start["lease_token"])

    async def memo():
        return {"cycle_message_id": str(uuid4() if mismatch == "memo" else start["id"])}

    async def describe(**kwargs):
        return SimpleNamespace(workflow_type="foreign-type" if mismatch == "type" else "SalienceLocalCycleWorkflow", status=WorkflowExecutionStatus.FAILED, memo=memo)

    client = SimpleNamespace(get_workflow_handle=lambda _: SimpleNamespace(describe=describe))
    transport = TemporalCycleTransport(client, task_queue="salience-v4-local-item7-identity-"+uuid4().hex)
    assert await transport.reconcile_one(outbox) is False
    assert not outbox.runtime_held(admitted["cycle_id"])


def test_inbox_scope_rejects_another_cycles_original_message():
    local, outbox, _ = scenario()
    _, _, foreign = scenario()
    start = outbox.claim()
    with psycopg.connect(local.database_url) as connection:
        with pytest.raises(psycopg.errors.RaiseException, match="original outbox cycle binding"), connection.transaction():
            connection.execute("INSERT INTO v4_cycle_inbox(id,message_id,cycle_id,state,traceparent) VALUES(%s,%s,%s,'held',%s)", (uuid4(), start["id"], foreign["cycle_id"], start["traceparent"]))
        assert connection.execute("SELECT count(*) FROM v4_cycle_inbox WHERE message_id=%s", (start["id"],)).fetchone()[0] == 0


@pytest.mark.parametrize("locked_sequence", [1, 2, 3])
def test_held_receipt_reconciliation_skips_locked_suffix_without_reordering(locked_sequence):
    service, outbox, admitted = scenario()
    start = outbox.claim()
    outbox.ack(start["id"], start["lease_token"])
    service.recover(admitted["cycle_id"], state="retry_due")
    service.recover(admitted["cycle_id"], state="runnable")
    assert outbox.hold_failed_runtime(start["id"], "FAILED")
    with psycopg.connect(service.database_url) as blocker:
        blocker.execute("SELECT id FROM v4_cycle_outbox WHERE cycle_id=%s AND sequence=%s FOR UPDATE", (admitted["cycle_id"], locked_sequence))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(outbox.reconcile_held_receipts, admitted["cycle_id"])
            try:
                assert future.result(timeout=1) == locked_sequence-1, "locked message stops the ordered prefix and cannot block on a message while holding its goal"
                with psycopg.connect(service.database_url) as connection:
                    sequences = connection.execute("SELECT m.sequence FROM v4_cycle_inbox i JOIN v4_cycle_outbox m ON m.id=i.message_id WHERE i.cycle_id=%s ORDER BY m.sequence", (admitted["cycle_id"],)).fetchall()
                    assert sequences == [(index,) for index in range(1, locked_sequence)]
            finally:
                blocker.rollback()
    assert outbox.reconcile_held_receipts(admitted["cycle_id"]) == 4-locked_sequence
    assert outbox.reconcile_held_receipts(admitted["cycle_id"]) == 0
    with psycopg.connect(service.database_url) as connection:
        assert connection.execute("SELECT count(*) FROM v4_cycle_inbox WHERE cycle_id=%s AND state='held'", (admitted["cycle_id"],)).fetchone()[0] == 3
        assert connection.execute("SELECT count(*) FROM v4_cycle_events WHERE cycle_id=%s AND kind='fixture_consumed'", (admitted["cycle_id"],)).fetchone()[0] == 3


@pytest.mark.asyncio
async def test_dispatch_poll_retries_unreceipted_held_cycle_after_temporary_message_lock():
    service, outbox, admitted = scenario()
    start = outbox.claim()
    outbox.ack(start["id"], start["lease_token"])

    async def memo():
        return {"cycle_message_id": str(start["id"])}

    async def describe(**kwargs):
        return SimpleNamespace(workflow_type="SalienceLocalCycleWorkflow", status=WorkflowExecutionStatus.FAILED, memo=memo)

    client = SimpleNamespace(get_workflow_handle=lambda _: SimpleNamespace(describe=describe))
    transport = TemporalCycleTransport(client, task_queue="salience-v4-local-item7-retry-prefix-"+uuid4().hex)
    with psycopg.connect(service.database_url) as blocker:
        blocker.execute("SELECT id FROM v4_cycle_outbox WHERE id=%s FOR UPDATE", (start["id"],))
        try:
            async with asyncio.timeout(1):
                await outbox.dispatch_one(transport)
            assert outbox.runtime_held(admitted["cycle_id"])
            assert inbox_count(service.database_url, admitted["cycle_id"]) == 0
        finally:
            blocker.rollback()
    # No new command is required just to finish the skipped held receipt.
    await outbox.dispatch_one(transport)
    assert inbox_count(service.database_url, admitted["cycle_id"]) == 1, "ordinary dispatcher polling must finish a temporarily skipped held receipt"


@pytest.mark.parametrize("history", ["empty", "valid", "invalid_outbox", "invalid_inbox"])
def test_delivery_scope_migration_preserves_or_holds_original_history(history, monkeypatch):
    source = os.environ["TEST_DATABASE_URL"]
    name = "item7_scope_migration_"+uuid4().hex
    database = urlunsplit(urlsplit(source)._replace(path="/"+name))

    def migrate(direction, target):
        return subprocess.run([sys.executable, "-m", "alembic", "-x", "database_url="+database, direction, target], capture_output=True, text=True, timeout=30)

    with psycopg.connect(source, autocommit=True) as admin:
        admin.execute(psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(name)))
        try:
            assert migrate("upgrade", "0033_goal_state_commands").returncode == 0
            if history != "empty":
                monkeypatch.setenv("TEST_DATABASE_URL", database)
                local, outbox, admitted = scenario()
                _, _, foreign = scenario()
                start = outbox.claim()
                with psycopg.connect(database) as connection:
                    if history == "invalid_outbox":
                        connection.execute("""INSERT INTO v4_cycle_outbox
                            (id,workspace_id,subject_id,goal_id,intent_id,cycle_id,sequence,kind,payload,traceparent)
                            SELECT %s,workspace_id,subject_id,goal_id,intent_id,%s,2,'recovery',payload,traceparent
                            FROM v4_cycle_outbox WHERE id=%s""", (uuid4(), foreign["cycle_id"], start["id"]))
                    elif history == "invalid_inbox":
                        connection.execute("INSERT INTO v4_cycle_inbox(id,message_id,cycle_id,state,traceparent) VALUES(%s,%s,%s,'held',%s)", (uuid4(), start["id"], foreign["cycle_id"], start["traceparent"]))
                    before = connection.execute("SELECT jsonb_agg(to_jsonb(m) ORDER BY id) FROM v4_cycle_outbox m").fetchone()[0]
                    before_inbox = connection.execute("SELECT jsonb_agg(to_jsonb(r) ORDER BY id) FROM v4_cycle_inbox r").fetchone()[0]
            upgraded = migrate("upgrade", "0034_runtime_delivery_scope")
            if history.startswith("invalid"):
                assert upgraded.returncode != 0 and "preserve and inspect delivery history" in upgraded.stderr
            else:
                assert upgraded.returncode == 0, upgraded.stderr
            if history == "empty":
                assert migrate("downgrade", "0033_goal_state_commands").returncode == 0
                assert migrate("upgrade", "0034_runtime_delivery_scope").returncode == 0
            else:
                if history == "valid":
                    rollback = migrate("downgrade", "0033_goal_state_commands")
                    assert rollback.returncode != 0 and "preserve canonical delivery scope" in rollback.stderr
                with psycopg.connect(database) as connection:
                    assert connection.execute("SELECT jsonb_agg(to_jsonb(m) ORDER BY id) FROM v4_cycle_outbox m").fetchone()[0] == before
                    assert connection.execute("SELECT jsonb_agg(to_jsonb(r) ORDER BY id) FROM v4_cycle_inbox r").fetchone()[0] == before_inbox
                    assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == ("0033_goal_state_commands" if history.startswith("invalid") else "0034_runtime_delivery_scope")
        finally:
            admin.execute(psycopg.sql.SQL("DROP DATABASE {} WITH (FORCE)").format(psycopg.sql.Identifier(name)))
