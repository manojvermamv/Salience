from concurrent.futures import ThreadPoolExecutor
import asyncio
from uuid import uuid4

import psycopg
import pytest

from test_v4_cycle_admission import cycles, intent, rows
from salience.cycles.outbox import CycleOutbox


def test_admission_outbox_is_atomic_and_recovery_keeps_stream(cycles, monkeypatch):
    service, goal, _, database = cycles
    requested = intent(service,goal)
    original = service._event
    def fail(*args,**kwargs):
        raise RuntimeError("business commit lost")
    monkeypatch.setattr(service,"_event",fail)
    with pytest.raises(RuntimeError):
        service.admit(requested)
    assert not [row for row in rows(database,"v4_cycle_outbox") if row["intent_id"]==requested]
    monkeypatch.setattr(service,"_event",original)
    result = service.admit(requested)
    service.admit(requested)
    service.recover(result["cycle_id"],state="retry_due")
    service.recover(result["cycle_id"],state="runnable")
    messages = [row for row in rows(database,"v4_cycle_outbox") if row["intent_id"]==requested]
    assert [row["sequence"] for row in messages] == [1,2,3]
    assert len({row["cycle_id"] for row in messages}) == 1
    assert messages[0]["kind"] == "start"
    assert messages[0]["traceparent"] == service.trace.to_carrier()["traceparent"]


def test_concurrent_claim_ack_loss_and_consumption_are_idempotent(cycles):
    service, goal, _, database = cycles
    admitted = service.admit(intent(service,goal))
    service.recover(admitted["cycle_id"],state="retry_due")
    outbox = CycleOutbox(database,workspace_id=service.workspace_id)
    with ThreadPoolExecutor(max_workers=4) as pool:
        claimed = list(pool.map(lambda _:outbox.claim(),range(4)))
    messages = [message for message in claimed if message]
    assert len(messages)==1
    first = messages[0]
    receipt = outbox.consume(first["id"])
    assert receipt["state"]=="recorded"
    assert outbox.consume(first["id"])["id"]==receipt["id"]
    with psycopg.connect(database) as connection:
        connection.execute("UPDATE v4_cycle_outbox SET lease_until=now()-interval '1 second' WHERE id=%s",(first["id"],))
    reclaimed = outbox.claim()
    assert reclaimed["id"]==first["id"] and reclaimed["lease_token"]!=first["lease_token"]
    assert outbox.ack(first["id"],first["lease_token"]) is False
    assert outbox.ack(reclaimed["id"],reclaimed["lease_token"]) is True
    assert outbox.claim()["sequence"]==2
    assert len([row for row in rows(database,"v4_cycle_inbox") if row["message_id"]==first["id"]])==1


def test_retry_backoff_deadletter_and_order_are_bounded(cycles):
    service, goal, _, database = cycles
    admitted = service.admit(intent(service,goal))
    service.recover(admitted["cycle_id"],state="retry_due")
    outbox = CycleOutbox(database,workspace_id=service.workspace_id,max_attempts=2)
    first = outbox.claim()
    assert outbox.fail(first["id"],first["lease_token"],"temporary")=="pending"
    assert outbox.claim() is None
    with psycopg.connect(database) as connection:
        connection.execute("UPDATE v4_cycle_outbox SET next_attempt_at=now() WHERE id=%s",(first["id"],))
    second = outbox.claim()
    assert second["attempts"]==2
    assert outbox.fail(second["id"],second["lease_token"],"temporary")=="dead_letter"
    assert outbox.claim() is None
    with pytest.raises(ValueError):
        CycleOutbox(database,workspace_id=service.workspace_id,max_attempts=1000)


def test_stale_claims_cannot_mutate_delivery_and_foreign_consumer_denies(cycles):
    service, goal, _, database = cycles
    service.admit(intent(service,goal))
    outbox=CycleOutbox(database,workspace_id=service.workspace_id)
    message=outbox.claim()
    assert outbox.ack(message["id"],uuid4()) is False
    with pytest.raises(PermissionError):
        CycleOutbox(database,workspace_id=uuid4()).consume(message["id"])
    with psycopg.connect(database) as connection:
        connection.execute("UPDATE identity_subjects SET enabled=false WHERE id=%s",(service.subject_id,))
    assert outbox.consume(message["id"])["state"]=="held"


def test_every_outbox_connection_has_a_statement_deadline(cycles):
    service,_,_,database=cycles
    with CycleOutbox(database,workspace_id=service.workspace_id)._connect() as connection:
        assert connection.execute("SHOW statement_timeout").fetchone()["statement_timeout"]=="3s"


def test_consumer_rejects_out_of_order_and_sql_payload_mutation(cycles):
    service, goal, _, database = cycles
    admitted=service.admit(intent(service,goal))
    service.recover(admitted["cycle_id"],state="retry_due")
    outbox=CycleOutbox(database,workspace_id=service.workspace_id)
    messages=[row for row in rows(database,"v4_cycle_outbox") if row["cycle_id"]==admitted["cycle_id"]]
    with pytest.raises(ValueError,match="ordered"):
        outbox.consume(messages[1]["id"])
    with psycopg.connect(database) as connection:
        with pytest.raises(psycopg.errors.RaiseException,match="immutable"):
            connection.execute("UPDATE v4_cycle_outbox SET kind='close' WHERE id=%s",(messages[0]["id"],))


def test_consumer_envelope_mismatch_has_no_committed_receipt(cycles):
    service,goal,_,database=cycles
    admitted=service.admit(intent(service,goal))
    outbox=CycleOutbox(database,workspace_id=service.workspace_id)
    message=outbox.claim()
    with pytest.raises(ValueError,match="binding"):
        outbox.consume(message["id"],expected_cycle_id=uuid4(),expected_kind="start")
    assert not [row for row in rows(database,"v4_cycle_inbox") if row["cycle_id"]==admitted["cycle_id"]]


@pytest.mark.asyncio
async def test_consumed_close_is_reconciled_without_resignalling_closed_workflow(cycles):
    service,goal,_,database=cycles
    admitted=service.admit(intent(service,goal))
    outbox=CycleOutbox(database,workspace_id=service.workspace_id)
    start=outbox.claim()
    outbox.consume(start["id"])
    outbox.ack(start["id"],start["lease_token"])
    service.close(admitted["cycle_id"],disposition="abstain",reason="fixture")
    close=outbox.claim()
    outbox.consume(close["id"])
    with psycopg.connect(database) as connection:
        connection.execute("UPDATE v4_cycle_outbox SET lease_until=now()-interval '1 second' WHERE id=%s",(close["id"],))
    class MustNotDeliver:
        async def deliver(self,message):
            raise AssertionError("closed workflow must not be signalled again")
    assert await outbox.dispatch_one(MustNotDeliver())


@pytest.mark.parametrize("when", ["before_timeout","after_timeout","expired_lease"])
def test_last_attempt_consumption_reconciles_before_deadletter_and_unblocks_stream(cycles,when):
    service,goal,_,database=cycles
    admitted=service.admit(intent(service,goal))
    service.recover(admitted["cycle_id"],state="retry_due")
    outbox=CycleOutbox(database,workspace_id=service.workspace_id,max_attempts=1)
    first=outbox.claim()
    if when != "after_timeout":
        outbox.consume(first["id"])
    if when == "expired_lease":
        with psycopg.connect(database) as connection:
            connection.execute("UPDATE v4_cycle_outbox SET lease_until=now()-interval '1 second' WHERE id=%s",(first["id"],))
    else:
        outbox.fail(first["id"],first["lease_token"],"timeout")
    if when == "after_timeout":
        outbox.consume(first["id"])
    next_message=outbox.claim()
    assert next_message and next_message["sequence"]==2
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT state FROM v4_cycle_outbox WHERE id=%s",(first["id"],)).fetchone()[0]=="delivered"


@pytest.mark.asyncio
async def test_transport_timeout_is_bounded_without_a_false_delivery_receipt(cycles):
    service,goal,_,database=cycles
    admitted=service.admit(intent(service,goal))
    outbox=CycleOutbox(database,workspace_id=service.workspace_id,max_attempts=1,lease_seconds=1)
    class NeverAcknowledges:
        async def deliver(self,message):
            await asyncio.sleep(30)
    async with asyncio.timeout(3):
        assert await outbox.dispatch_one(NeverAcknowledges()) is False
    assert outbox.claim() is None
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT state FROM v4_cycle_outbox WHERE cycle_id=%s",(admitted["cycle_id"],)).fetchone()[0]=="dead_letter"
        assert connection.execute("SELECT count(*) FROM v4_cycle_inbox WHERE cycle_id=%s",(admitted["cycle_id"],)).fetchone()[0]==0
