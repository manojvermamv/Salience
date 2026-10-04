from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
import pytest

from salience.cycles.contracts import CadencePolicy, CycleRequest, GoalSpecV2
from salience.cycles.governance import CycleGovernance
from salience.cycles.outbox import CycleOutbox
from salience.cycles.schedule_cutover import CycleScheduleCutover, FixtureLegacyScheduleControl, FixtureSchedulePoller
from test_v4_cadence_policy import policy
from test_v4_cycle_admission import approved_goal, cycles


@pytest.fixture
def cutover(policy):
    service, _, original, database = policy
    with psycopg.connect(database) as connection:
        connection.execute(
            "INSERT INTO permission_grants (workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at) "
            "VALUES (%s,'identity',%s,'cycles:schedule','allow','{}',now()+interval '1 hour')",
            (service.workspace_id, str(service.subject_id)),
        )
    anchor = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=3)
    cadence = CadencePolicy(anchor=anchor, interval_seconds=1, catch_up="latest")
    spec = GoalSpecV2.model_validate(original.model_dump() | {"cadence": cadence})
    goal = approved_goal(service, spec)
    legacy_id = uuid4()
    with psycopg.connect(database) as connection:
        connection.execute(
            "INSERT INTO job_schedules (id,workspace_id,content_program_id,name,schedule_expression,timezone,job_type,payload,next_run_at) "
            "VALUES (%s,%s,%s,%s,%s,'UTC','v4_fixture_legacy',%s,%s)",
            (legacy_id, service.workspace_id, spec.content_program_id, str(legacy_id),
             "every:1s", Jsonb({"dry_run": True, "last_slot": (anchor - timedelta(seconds=1)).isoformat()}), anchor),
        )
    control = FixtureLegacyScheduleControl(database, workspace_id=service.workspace_id)
    scheduler = CycleScheduleCutover(database, workspace_id=service.workspace_id,
                                     subject_id=service.subject_id, legacy_control=control)
    return scheduler, service, goal, spec, legacy_id, database


def cutover_rows(database, goal):
    with psycopg.connect(database, row_factory=dict_row) as connection:
        return connection.execute("SELECT * FROM v4_schedule_cutovers WHERE goal_id=%s", (goal,)).fetchall()


def test_pending_fences_direct_poll_and_activation_reconciles_lost_pause_ack(cutover, monkeypatch):
    scheduler, service, goal, spec, legacy_id, database = cutover
    prepared = scheduler.prepare(goal, legacy_schedule_id=legacy_id, expected_revision=1,
                                 first_v4_slot=spec.cadence.anchor, idempotency_key="prepare")
    assert prepared["state"] == "pending"
    with psycopg.connect(database) as connection, pytest.raises(psycopg.Error, match="activation requires"):
        connection.execute("UPDATE v4_schedule_cutovers SET state='active',activation_key='bypass',activated_at=clock_timestamp() WHERE goal_id=%s", (goal,))
    assert scheduler.prepare(goal, legacy_schedule_id=legacy_id, expected_revision=1,
                             first_v4_slot=spec.cadence.anchor, idempotency_key="prepare") == prepared
    with pytest.raises(ValueError, match="conflict"):
        scheduler.prepare(goal, legacy_schedule_id=legacy_id, expected_revision=1,
                          first_v4_slot=spec.cadence.anchor, idempotency_key="other")
    with pytest.raises(ValueError, match="cutover"):
        service.request_due(goal, expected_revision=1, idempotency_key="unguarded")
    with pytest.raises(ValueError, match="cutover"):
        service.request_cycle(goal, CycleRequest(origin="scheduled", expected_revision=1,
                              slot_time=spec.cadence.anchor, idempotency_key="unguarded"))
    assert scheduler.poll(goal, expected_revision=1, idempotency_key="pending") ["state"] == "pending"
    assert scheduler.legacy_control.describe(legacy_id)["paused"] is False

    original = scheduler.legacy_control.pause
    calls = 0

    def lost_ack(schedule_id):
        nonlocal calls
        original(schedule_id)
        calls += 1
        if calls == 1:
            raise TimeoutError("ack lost after pause")

    monkeypatch.setattr(scheduler.legacy_control, "pause", lost_ack)
    active = scheduler.activate(goal, idempotency_key="activate")
    assert active["state"] == "active" and calls == 1
    assert scheduler.activate(goal, idempotency_key="activate") == active
    with psycopg.connect(database) as connection:
        cursor = connection.execute("SELECT last_slot FROM v4_schedule_cursors WHERE goal_id=%s", (goal,)).fetchone()[0]
        assert cursor == spec.cadence.anchor - timedelta(seconds=1)
        assert connection.execute("SELECT count(*) FROM v4_cycle_events WHERE goal_id=%s AND kind='schedule_cutover_activated'", (goal,)).fetchone()[0] == 1


def test_cutover_poll_coalesces_concurrent_origins_and_crash_recovery(cutover, monkeypatch):
    import time

    scheduler, service, goal, spec, legacy_id, database = cutover
    scheduler.prepare(goal, legacy_schedule_id=legacy_id, expected_revision=1,
                      first_v4_slot=spec.cadence.anchor, idempotency_key="prepare")
    scheduler.activate(goal, idempotency_key="activate")
    time.sleep(max(0, (spec.cadence.anchor - datetime.now(timezone.utc)).total_seconds()) + 0.1)
    commands = [CycleRequest(origin="manual", expected_revision=1, slot_time=spec.cadence.anchor,
                             idempotency_key="manual"),
                CycleRequest(origin="event", expected_revision=1, slot_time=spec.cadence.anchor,
                             idempotency_key="event", event_at=datetime.now(timezone.utc), event_id="source")]
    with ThreadPoolExecutor(max_workers=2) as pool:
        manually_requested = list(pool.map(lambda command: service.request_cycle(goal, command), commands))
    assert manually_requested[0] == manually_requested[1]
    original_admit = scheduler.admit
    monkeypatch.setattr(scheduler, "admit", lambda *_: (_ for _ in ()).throw(RuntimeError("process crash")))
    with pytest.raises(RuntimeError, match="process crash"):
        scheduler.poll(goal, expected_revision=1, idempotency_key="poll")
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM v4_schedule_batches WHERE goal_id=%s", (goal,)).fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM v4_admissions WHERE intent_id=%s", (manually_requested[0],)).fetchone()[0] == 0
    monkeypatch.setattr(scheduler, "admit", original_admit)
    restarted = CycleScheduleCutover(database, workspace_id=service.workspace_id,
                                     subject_id=service.subject_id, legacy_control=scheduler.legacy_control)
    with ThreadPoolExecutor(max_workers=3) as pool:
        polled = list(pool.map(lambda _: restarted.poll(goal, expected_revision=1,
                                                        idempotency_key="poll"), range(3)))
    assert all(result["intent_ids"] == [str(manually_requested[0])] for result in polled)
    assert all(result["admissions"][0]["disposition"] == "admitted" for result in polled)
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM v4_cycles WHERE intent_id=%s", (manually_requested[0],)).fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM v4_run_contexts AS context JOIN v4_cycles AS cycle ON cycle.id=context.cycle_id WHERE cycle.intent_id=%s", (manually_requested[0],)).fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM v4_cycle_outbox AS message JOIN v4_cycles AS cycle ON cycle.id=message.cycle_id WHERE cycle.intent_id=%s AND message.kind='start'", (manually_requested[0],)).fetchone()[0] == 1


def test_cutover_activation_crash_and_revocation_leave_old_paused(cutover, monkeypatch):
    scheduler, service, goal, spec, legacy_id, database = cutover
    scheduler.prepare(goal, legacy_schedule_id=legacy_id, expected_revision=1,
                      first_v4_slot=spec.cadence.anchor, idempotency_key="prepare")
    original_event = scheduler._event

    def crash(connection, goal_id, kind, payload, *args):
        if kind == "schedule_cutover_activated":
            raise RuntimeError("before commit")
        return original_event(connection, goal_id, kind, payload, *args)

    monkeypatch.setattr(scheduler, "_event", crash)
    with pytest.raises(RuntimeError, match="before commit"):
        scheduler.activate(goal, idempotency_key="activate")
    assert cutover_rows(database, goal)[0]["state"] == "pending"
    assert scheduler.legacy_control.describe(legacy_id)["paused"] is True
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM v4_schedule_cursors WHERE goal_id=%s", (goal,)).fetchone()[0] == 0
    monkeypatch.setattr(scheduler, "_event", original_event)
    with psycopg.connect(database) as connection:
        connection.execute("UPDATE permission_grants SET expires_at=now()-interval '1 second' WHERE workspace_id=%s AND scope='cycles:schedule'", (service.workspace_id,))
    with pytest.raises(PermissionError):
        scheduler.activate(goal, idempotency_key="activate")
    assert cutover_rows(database, goal)[0]["state"] == "pending"
    with psycopg.connect(database) as connection:
        connection.execute("UPDATE permission_grants SET expires_at=now()+interval '1 hour' WHERE workspace_id=%s AND scope='cycles:schedule'", (service.workspace_id,))
    assert scheduler.activate(goal, idempotency_key="activate")["state"] == "active"
    service.revise_goal(goal, spec.model_copy(update={"objective": "Changed"}), expected_revision=1,
                        idempotency_key="revision", reason="Explicit change")
    with pytest.raises(ValueError, match="revision"):
        scheduler.poll(goal, expected_revision=1, idempotency_key="stale")


def test_rollback_fences_poll_and_retries_after_resume_ack_loss(cutover, monkeypatch):
    scheduler, service, goal, spec, legacy_id, database = cutover
    scheduler.prepare(goal, legacy_schedule_id=legacy_id, expected_revision=1,
                      first_v4_slot=spec.cadence.anchor, idempotency_key="prepare")
    scheduler.activate(goal, idempotency_key="activate")
    original_event = scheduler._event

    def crash(connection, goal_id, kind, payload, *args):
        if kind == "schedule_cutover_rolled_back":
            raise RuntimeError("after external resume")
        return original_event(connection, goal_id, kind, payload, *args)

    monkeypatch.setattr(scheduler, "_event", crash)
    with pytest.raises(RuntimeError, match="external resume"):
        scheduler.rollback(goal, idempotency_key="rollback")
    assert cutover_rows(database, goal)[0]["state"] == "rollback_pending"
    assert scheduler.legacy_control.describe(legacy_id)["paused"] is False
    assert scheduler.poll(goal, expected_revision=1, idempotency_key="blocked")["state"] == "rollback_pending"
    monkeypatch.setattr(scheduler, "_event", original_event)
    rolled_back = scheduler.rollback(goal, idempotency_key="rollback")
    assert rolled_back["state"] == "rolled_back"
    assert scheduler.rollback(goal, idempotency_key="rollback") == rolled_back
    assert scheduler.legacy_control.describe(legacy_id)["next_run_at"] > spec.cadence.anchor - timedelta(seconds=1)
    with pytest.raises(ValueError, match="conflict"):
        scheduler.rollback(goal, idempotency_key="different")
    with pytest.raises(ValueError, match="rollback"):
        service.request_cycle(goal, CycleRequest(origin="manual", expected_revision=1,
                              slot_time=spec.cadence.anchor, idempotency_key="after-rollback"))


def test_rollback_holds_committed_unadmitted_work_and_unclaimed_liabilities(cutover, monkeypatch):
    import time

    scheduler, service, goal, spec, legacy_id, database = cutover
    scheduler.prepare(goal, legacy_schedule_id=legacy_id, expected_revision=1,
                      first_v4_slot=spec.cadence.anchor, idempotency_key="prepare")
    scheduler.activate(goal, idempotency_key="activate")
    time.sleep(max(0, (spec.cadence.anchor - datetime.now(timezone.utc)).total_seconds()) + 0.1)
    original_admit = scheduler.admit
    monkeypatch.setattr(scheduler, "admit", lambda *_: (_ for _ in ()).throw(RuntimeError("crash")))
    with pytest.raises(RuntimeError, match="crash"):
        scheduler.poll(goal, expected_revision=1, idempotency_key="poll")
    with pytest.raises(ValueError, match="unresolved"):
        scheduler.rollback(goal, idempotency_key="rollback")
    assert cutover_rows(database, goal)[0]["state"] == "rollback_pending"
    assert scheduler.legacy_control.describe(legacy_id)["paused"] is True
    monkeypatch.setattr(scheduler, "admit", original_admit)
    with psycopg.connect(database) as connection:
        intent_id = connection.execute("SELECT intent_id FROM v4_cycle_requests WHERE goal_id=%s AND origin='scheduled'", (goal,)).fetchone()[0]
    cycle_id = service.admit(intent_id)["cycle_id"]
    service.close(cycle_id, disposition="completed", reason="Fixture closed")
    with pytest.raises(ValueError, match="unresolved"):
        scheduler.rollback(goal, idempotency_key="rollback")
    with psycopg.connect(database) as connection:
        connection.execute("UPDATE v4_cycle_outbox SET state='delivered',delivered_at=clock_timestamp() WHERE cycle_id=%s", (cycle_id,))
        connection.execute("INSERT INTO v4_permit_claims (id,cycle_id,operation_id,context_id,request_fingerprint) SELECT %s,id,operation_id,context_id,%s FROM v4_cycles WHERE id=%s", (uuid4(),"a"*64,cycle_id))
    with pytest.raises(ValueError, match="unresolved"):
        scheduler.rollback(goal, idempotency_key="rollback")


def test_nonowner_cutover_has_no_authority_edit_privilege(cutover):
    from urllib.parse import urlsplit, urlunsplit

    scheduler, service, goal, spec, legacy_id, database = cutover
    role, password = "v4_cutover_" + uuid4().hex, uuid4().hex
    identifier = psycopg.sql.Identifier(role)
    with psycopg.connect(database, autocommit=True) as admin:
        admin.execute(psycopg.sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS").format(identifier, psycopg.sql.Literal(password)))
        try:
            admin.execute(psycopg.sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(identifier))
            admin.execute(psycopg.sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(identifier))
            admin.execute(psycopg.sql.SQL("GRANT INSERT ON v4_runtime_waits,v4_notification_deliveries,v4_schedule_cutovers,v4_schedule_cursors,v4_cycle_events TO {}").format(identifier))
            admin.execute(psycopg.sql.SQL("GRANT UPDATE ON v4_goals,v4_schedule_cutovers,job_schedules TO {}").format(identifier))
            admin.execute(psycopg.sql.SQL("GRANT EXECUTE ON FUNCTION p0_lock_identity(text,text,uuid),v4_lock_program(uuid,uuid) TO {}").format(identifier))
            parts = urlsplit(database)
            restricted = urlunsplit(parts._replace(netloc=f"{role}:{password}@{parts.hostname}:{parts.port or 5432}"))
            with psycopg.connect(restricted) as connection:
                assert not connection.execute("SELECT has_table_privilege(current_user,'permission_grants','UPDATE')").fetchone()[0]
            local = CycleScheduleCutover(restricted, workspace_id=service.workspace_id, subject_id=service.subject_id)
            assert local.prepare(goal, legacy_schedule_id=legacy_id, expected_revision=1,
                                 first_v4_slot=spec.cadence.anchor, idempotency_key="restricted")["state"] == "pending"
            assert local.activate(goal, idempotency_key="restricted-activate")["state"] == "active"
        finally:
            admin.execute(psycopg.sql.SQL("DROP OWNED BY {}").format(identifier))
            admin.execute(psycopg.sql.SQL("DROP ROLE {}").format(identifier))


def test_stop_holds_poll_and_legacy_resume(cutover):
    scheduler, service, goal, spec, legacy_id, database = cutover
    with psycopg.connect(database) as connection:
        connection.execute(
            "INSERT INTO permission_grants (workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at) "
            "VALUES (%s,'identity',%s,'cycles:stop','allow','{}',now()+interval '1 hour')",
            (service.workspace_id, str(service.subject_id)),
        )
    governance = CycleGovernance(database, workspace_id=service.workspace_id, subject_id=service.subject_id)
    scheduler.prepare(goal, legacy_schedule_id=legacy_id, expected_revision=1,
                      first_v4_slot=spec.cadence.anchor, idempotency_key="prepare")
    governance.set_stop(goal_id=goal, stopped=True, expected_revision=1,
                        idempotency_key="stop", reason="Fixture stop")
    with pytest.raises(PermissionError, match="stop"):
        scheduler.activate(goal, idempotency_key="activate")
    assert scheduler.legacy_control.describe(legacy_id)["paused"] is True
    governance.set_stop(goal_id=goal, stopped=False, expected_revision=2,
                        idempotency_key="run", reason="Fixture resume")
    scheduler.activate(goal, idempotency_key="activate")
    governance.set_stop(goal_id=goal, stopped=True, expected_revision=3,
                        idempotency_key="stop-again", reason="Fixture stop")
    assert scheduler.poll(goal, expected_revision=1, idempotency_key="held")["reason"] == "stopped"
    with pytest.raises(PermissionError, match="stop"):
        scheduler.rollback(goal, idempotency_key="rollback")
    assert cutover_rows(database, goal)[0]["state"] == "rollback_pending"
    governance.set_stop(goal_id=goal, stopped=False, expected_revision=4,
                        idempotency_key="run-again", reason="Fixture resume")
    assert scheduler.rollback(goal, idempotency_key="rollback")["state"] == "rolled_back"


def test_settled_fixture_cycle_allows_reconciled_rollback_after_lost_ack(cutover, monkeypatch):
    import time

    scheduler, service, goal, spec, legacy_id, database = cutover
    scheduler.prepare(goal, legacy_schedule_id=legacy_id, expected_revision=1,
                      first_v4_slot=spec.cadence.anchor, idempotency_key="prepare")
    scheduler.activate(goal, idempotency_key="activate")
    time.sleep(max(0, (spec.cadence.anchor - datetime.now(timezone.utc)).total_seconds()) + 0.1)
    result = scheduler.poll(goal, expected_revision=1, idempotency_key="poll")
    cycle_id = result["admissions"][0]["cycle_id"]
    service.close(cycle_id, disposition="completed", reason="Fixture settled")
    consumer = CycleOutbox(database, workspace_id=service.workspace_id)
    for _ in range(2):
        message = consumer.claim()
        assert message and str(message["cycle_id"]) == cycle_id
        consumer.consume(message["id"])
        assert consumer.ack(message["id"], message["lease_token"])
    original = scheduler.legacy_control.resume_after
    calls = 0

    def lost_ack(schedule_id, after_slot, **kwargs):
        nonlocal calls
        original(schedule_id, after_slot, **kwargs)
        calls += 1
        raise TimeoutError("resume acknowledgment lost")

    monkeypatch.setattr(scheduler.legacy_control, "resume_after", lost_ack)
    assert scheduler.rollback(goal, idempotency_key="rollback")["state"] == "rolled_back"
    assert calls == 1
    with psycopg.connect(database) as connection:
        cursor = connection.execute("SELECT last_slot FROM v4_schedule_cursors WHERE goal_id=%s", (goal,)).fetchone()[0]
        next_run = connection.execute("SELECT next_run_at FROM job_schedules WHERE id=%s", (legacy_id,)).fetchone()[0]
        assert next_run > cursor


def test_cutover_prepare_poll_and_rollback_start_crashes_preserve_fence(cutover, monkeypatch):
    import time

    scheduler, service, goal, spec, legacy_id, database = cutover
    original_event = scheduler._event
    fail_kind = "schedule_cutover_prepared"

    def fail_selected(connection, goal_id, kind, payload, *args):
        if kind == fail_kind:
            raise RuntimeError("before database commit")
        return original_event(connection, goal_id, kind, payload, *args)

    monkeypatch.setattr(scheduler, "_event", fail_selected)
    with pytest.raises(RuntimeError, match="before database commit"):
        scheduler.prepare(goal, legacy_schedule_id=legacy_id, expected_revision=1,
                          first_v4_slot=spec.cadence.anchor, idempotency_key="prepare")
    assert not cutover_rows(database, goal)
    assert scheduler.legacy_control.describe(legacy_id)["paused"] is False
    fail_kind = "none"
    scheduler.prepare(goal, legacy_schedule_id=legacy_id, expected_revision=1,
                      first_v4_slot=spec.cadence.anchor, idempotency_key="prepare")
    scheduler.activate(goal, idempotency_key="activate")
    time.sleep(max(0, (spec.cadence.anchor - datetime.now(timezone.utc)).total_seconds()) + 0.1)
    fail_kind = "schedule_polled"
    with pytest.raises(RuntimeError, match="before database commit"):
        scheduler.poll(goal, expected_revision=1, idempotency_key="poll")
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM v4_schedule_batches WHERE goal_id=%s", (goal,)).fetchone()[0] == 0
        assert connection.execute("SELECT last_slot FROM v4_schedule_cursors WHERE goal_id=%s", (goal,)).fetchone()[0] == spec.cadence.anchor - timedelta(seconds=1)
    fail_kind = "none"
    assert scheduler.poll(goal, expected_revision=1, idempotency_key="poll")["state"] == "active"
    fail_kind = "schedule_cutover_rollback_started"
    with pytest.raises(RuntimeError, match="before database commit"):
        scheduler.rollback(goal, idempotency_key="rollback")
    assert cutover_rows(database, goal)[0]["state"] == "active"
    assert scheduler.legacy_control.describe(legacy_id)["paused"] is True


def test_opt_in_fixture_poller_only_advances_active_due_goal(cutover, monkeypatch):
    import time

    scheduler, service, goal, spec, legacy_id, database = cutover
    poller = FixtureSchedulePoller(database, workspace_id=service.workspace_id)
    monkeypatch.delenv("SALIENCE_DEPLOYMENT_MODE", raising=False)
    with pytest.raises(PermissionError, match="no-effects fixture"):
        poller.poll_once()
    monkeypatch.setenv("SALIENCE_DEPLOYMENT_MODE", "fixture")
    monkeypatch.setenv("SALIENCE_EFFECTS_ENABLED", "false")
    scheduler.prepare(goal, legacy_schedule_id=legacy_id, expected_revision=1,
                      first_v4_slot=spec.cadence.anchor, idempotency_key="prepare")
    assert poller.poll_once()["selected"] == 0
    scheduler.activate(goal, idempotency_key="activate")
    time.sleep(max(0, (spec.cadence.anchor - datetime.now(timezone.utc)).total_seconds()) + 0.1)
    first = poller.poll_once()
    assert first["selected"] == first["polled"] == first["admitted"] == 1
    assert poller.poll_once()["admitted"] <= 1
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM v4_cycles AS cycle JOIN v4_cycle_intents AS intent ON intent.id=cycle.intent_id WHERE intent.goal_id=%s", (goal,)).fetchone()[0] == 1


def test_unverified_external_unpause_cannot_be_mistaken_for_resume_ack(cutover):
    scheduler, service, goal, spec, legacy_id, database = cutover
    scheduler.prepare(goal, legacy_schedule_id=legacy_id, expected_revision=1,
                      first_v4_slot=spec.cadence.anchor, idempotency_key="prepare")
    scheduler.activate(goal, idempotency_key="activate")
    with psycopg.connect(database) as connection:
        connection.execute("UPDATE job_schedules SET status='active' WHERE id=%s", (legacy_id,))
    with pytest.raises(ValueError, match="exact rollback readback"):
        scheduler.rollback(goal, idempotency_key="rollback")
    assert cutover_rows(database, goal)[0]["state"] == "rollback_pending"
    assert scheduler.poll(goal, expected_revision=1, idempotency_key="after")["state"] == "rollback_pending"
