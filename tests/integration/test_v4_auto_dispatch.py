import asyncio

import psycopg
import pytest

from salience.cycles.outbox import CycleOutbox
from test_v4_cycle_admission import cycles, intent


def outbox_rows(database, cycle_id):
    with psycopg.connect(database) as connection:
        return connection.execute(
            "SELECT id,state,kind FROM v4_cycle_outbox WHERE cycle_id=%s ORDER BY sequence",
            (cycle_id,),
        ).fetchall()


@pytest.mark.asyncio
async def test_fixture_driver_automatically_drains_and_stops(cycles, monkeypatch):
    monkeypatch.setenv("SALIENCE_DEPLOYMENT_MODE", "fixture")
    service, goal, _, database = cycles
    cycle_id = service.admit(intent(service, goal))["cycle_id"]
    outbox = CycleOutbox(database, workspace_id=service.workspace_id)

    class ConsumingTransport:
        async def deliver(self, message):
            await asyncio.to_thread(
                outbox.consume,
                message["id"],
                traceparent=message["delivery_traceparent"],
                expected_cycle_id=cycle_id,
                expected_kind=message["kind"],
            )

    stop = asyncio.Event()
    task = asyncio.create_task(
        outbox.run_until_stopped(
            ConsumingTransport(), stop_event=stop, poll_interval_ms=50, batch_size=2
        )
    )
    try:
        async with asyncio.timeout(5):
            while (await asyncio.to_thread(outbox_rows, database, cycle_id))[0][1] != "delivered":
                await asyncio.sleep(0.05)
        service.close(cycle_id, disposition="abstain", reason="fixture completed")
        async with asyncio.timeout(5):
            while any(row[1] != "delivered" for row in await asyncio.to_thread(outbox_rows, database, cycle_id)):
                await asyncio.sleep(0.05)
    finally:
        stop.set()
        await asyncio.wait_for(task, 2)
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT count(*) FROM v4_cycle_inbox WHERE cycle_id=%s", (cycle_id,)
        ).fetchone()[0] == 2


def test_final_attempt_records_one_durable_owner_escalation(cycles):
    service, goal, _, database = cycles
    cycle_id = service.admit(intent(service, goal))["cycle_id"]
    outbox = CycleOutbox(database, workspace_id=service.workspace_id, max_attempts=1)
    claimed = outbox.claim()
    assert outbox.fail(claimed["id"], claimed["lease_token"], "temporary") == "dead_letter"
    assert outbox.claim() is None
    with psycopg.connect(database) as connection:
        alerts = connection.execute(
            "SELECT subject_id,payload->>'message_id',payload->>'reason' FROM v4_cycle_events WHERE cycle_id=%s AND kind='outbox_dead_letter'",
            (cycle_id,),
        ).fetchall()
    assert alerts == [(service.subject_id, str(claimed["id"]), "temporary")]


def test_expired_final_lease_escalates_once_and_preserves_order(cycles):
    service, goal, _, database = cycles
    cycle_id = service.admit(intent(service, goal))["cycle_id"]
    service.recover(cycle_id, state="retry_due")
    outbox = CycleOutbox(database, workspace_id=service.workspace_id, max_attempts=1)
    claimed = outbox.claim()
    with psycopg.connect(database) as connection:
        connection.execute(
            "UPDATE v4_cycle_outbox SET lease_until=now()-interval '1 second' WHERE id=%s",
            (claimed["id"],),
        )
    assert outbox.claim() is None
    assert outbox.claim() is None
    with psycopg.connect(database) as connection:
        alerts = connection.execute(
            "SELECT payload->>'message_id',payload->>'reason' FROM v4_cycle_events WHERE cycle_id=%s AND kind='outbox_dead_letter'",
            (cycle_id,),
        ).fetchall()
        state = connection.execute(
            "SELECT state FROM v4_cycle_outbox WHERE id=%s", (claimed["id"],)
        ).fetchone()[0]
    assert state == "dead_letter"
    assert alerts == [(str(claimed["id"]), "lease_exhausted")]


def test_late_consumer_records_hold_after_dead_letter(cycles):
    service, goal, _, database = cycles
    cycle_id = service.admit(intent(service, goal))["cycle_id"]
    outbox = CycleOutbox(database, workspace_id=service.workspace_id, max_attempts=1)
    claimed = outbox.claim()
    assert outbox.fail(claimed["id"], claimed["lease_token"], "timeout") == "dead_letter"
    receipt = outbox.consume(claimed["id"])
    assert receipt["state"] == "held"
    assert outbox.claim() is None
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT state FROM v4_cycle_outbox WHERE id=%s", (claimed["id"],)
        ).fetchone()[0] == "delivered"
        assert connection.execute(
            "SELECT count(*) FROM v4_cycle_events WHERE cycle_id=%s AND kind='outbox_dead_letter'",
            (cycle_id,),
        ).fetchone()[0] == 1


@pytest.mark.asyncio
async def test_automatic_driver_rejects_nonfixture_mode(cycles, monkeypatch):
    from salience.cycles.runtime import LocalCycleActivities

    service, _, _, database = cycles
    outbox = CycleOutbox(database, workspace_id=service.workspace_id)
    monkeypatch.setenv("SALIENCE_DEPLOYMENT_MODE", "production")
    with pytest.raises(PermissionError, match="fixture mode"):
        await outbox.run_until_stopped(None, stop_event=asyncio.Event())
    with pytest.raises(ValueError, match="no-effects fixture adapter"):
        LocalCycleActivities(outbox, adapter=type("MockAdapter", (), {"effect_id": "fixture.noop"})())


@pytest.mark.asyncio
async def test_automatic_driver_reconciles_terminal_ack_loss_without_resignal(cycles, monkeypatch):
    monkeypatch.setenv("SALIENCE_DEPLOYMENT_MODE", "fixture")
    service, goal, _, database = cycles
    cycle_id = service.admit(intent(service, goal))["cycle_id"]
    outbox = CycleOutbox(database, workspace_id=service.workspace_id)
    started = outbox.claim()
    outbox.consume(started["id"])
    assert outbox.ack(started["id"], started["lease_token"])
    service.close(cycle_id, disposition="abstain", reason="fixture complete")
    terminal = outbox.claim()
    outbox.consume(terminal["id"])
    with psycopg.connect(database) as connection:
        connection.execute(
            "UPDATE v4_cycle_outbox SET lease_until=now()-interval '1 second' WHERE id=%s",
            (terminal["id"],),
        )

    class MustNotResignal:
        async def deliver(self, message):
            raise AssertionError("completed workflow received another signal")

    stop = asyncio.Event()
    task = asyncio.create_task(
        outbox.run_until_stopped(
            MustNotResignal(), stop_event=stop, poll_interval_ms=50, batch_size=2
        )
    )
    try:
        async with asyncio.timeout(3):
            while (await asyncio.to_thread(outbox_rows, database, cycle_id))[-1][1] != "delivered":
                await asyncio.sleep(0.05)
    finally:
        stop.set()
        await asyncio.wait_for(task, 2)
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT count(*) FROM v4_cycle_inbox WHERE cycle_id=%s", (cycle_id,)
        ).fetchone()[0] == 2
