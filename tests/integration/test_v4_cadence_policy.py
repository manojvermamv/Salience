from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
import pytest
from pydantic import ValidationError

from salience.cycles.admission import CycleAdmission
from salience.cycles.contracts import CadencePolicy, CycleRequest, GoalSpecV2
from salience.cycles.outbox import CycleOutbox
from test_v4_cycle_admission import approved_goal, cycles, rows


@pytest.fixture
def policy(cycles):
    service, _, legacy, database = cycles
    program = uuid4()
    with psycopg.connect(database) as connection:
        connection.execute("INSERT INTO content_programs (id,workspace_id,slug,name,niche) VALUES (%s,%s,%s,'Fixture','Fixture')", (program, service.workspace_id, str(program)))
    anchor = datetime.now(timezone.utc).replace(microsecond=0)-timedelta(seconds=600)
    spec = GoalSpecV2(**(legacy.model_dump() | {
        "schema_version": "GoalSpec.local.v2", "content_program_id": program, "cadence_seconds": None,
        "account_refs": ("fixture-account",), "channel_refs": ("fixture-channel",),
        "content_scope": "fixture-only", "policy_refs": ("fixture-policy@1",),
        "retention_policy": "fixture-retention@1", "cadence": CadencePolicy(anchor=anchor),
    }))
    return service, approved_goal(service, spec), spec, database


def request(spec, key="request", **changes):
    return CycleRequest(origin="manual", idempotency_key=key, expected_revision=1,
                        slot_time=spec.cadence.anchor+timedelta(seconds=600), **changes)


def test_policy_contract_rejects_unsafe_or_inconsistent_fields(policy):
    _, _, spec, _ = policy
    for changes in [{"account_refs": ("live-account",)}, {"max_spend": 1},
                    {"research_limit": 1}, {"exploration_allocation": 1},
                    {"cadence": spec.cadence.model_copy(update={"overlap": "forbid"}), "max_concurrent": 2}]:
        with pytest.raises(ValidationError):
            GoalSpecV2.model_validate(spec.model_dump() | changes)
    with pytest.raises(ValidationError):
        CycleRequest(origin="event", idempotency_key="event", expected_revision=1, slot_time=spec.cadence.anchor)


def test_context_is_typed_and_execution_window_is_bounded(policy):
    from salience.cycles.contracts import RunContextV2
    import time
    service, _, spec, database = policy
    goal = approved_goal(service, spec.model_copy(update={"wall_time_seconds": 1}))
    cycle = service.admit(service.request_cycle(goal, request(spec)))["cycle_id"]
    payload = next(row["payload"] for row in rows(database, "v4_run_contexts") if row["cycle_id"] == cycle)
    context = RunContextV2.model_validate(payload)
    assert context.operation_id and context.authority.grant_id
    with pytest.raises(ValidationError):
        RunContextV2.model_validate(payload | {"assignment_id": str(uuid4())})
    time.sleep(1.05)
    with pytest.raises(PermissionError, match="authority"):
        service.recover(cycle, state="retry_due")
    consumer = CycleOutbox(database, workspace_id=service.workspace_id)
    assert consumer.consume(consumer.claim()["id"])["state"] == "held"


def test_concurrent_origins_coalesce_and_context_is_complete_immutable(policy):
    service, goal, spec, database = policy
    base = request(spec)
    commands = [base.model_copy(update={"origin": origin, "idempotency_key": origin,
                 "event_at": datetime.now(timezone.utc) if origin == "event" else None,
                 "event_id": "fixture-event" if origin == "event" else None})
                for origin in ("manual", "event", "scheduled")]
    with ThreadPoolExecutor(max_workers=3) as pool:
        intents = list(pool.map(lambda command: service.request_cycle(goal, command), commands))
        admitted = list(pool.map(service.admit, intents))
    assert len(set(intents)) == len({entry["cycle_id"] for entry in admitted}) == 1
    context = next(row for row in rows(database, "v4_run_contexts") if row["cycle_id"] == admitted[0]["cycle_id"])
    payload = context["payload"]
    assert context["schema_version"] == payload["schema_version"] == "RunContext.local.v2"
    assert payload["content_program_id"] == str(spec.content_program_id)
    assert payload["context_id"] == str(context["id"]) and payload["intent_id"] == str(intents[0])
    assert payload["authority"]["subject_id"] == str(service.subject_id)
    assert payload["authority"]["scope"] == "cycles:write"
    assert payload["assignment_id"] is None and payload["production_effects_enabled"] is False
    with psycopg.connect(database) as connection, pytest.raises(psycopg.Error, match="immutable"):
        connection.execute("UPDATE v4_run_contexts SET payload='{}' WHERE id=%s", (context["id"],))
    consumer = CycleOutbox(database, workspace_id=service.workspace_id)
    assert consumer.consume(consumer.claim()["id"])["state"] == "recorded"


def test_revision_retry_and_cross_revision_slot_never_create_second_cycle(policy):
    service, goal, spec, database = policy
    command = request(spec)
    original = service.request_cycle(goal, command)
    cycle = service.admit(original)["cycle_id"]
    service.revise_goal(goal, spec.model_copy(update={"objective": "New fixture objective"}), expected_revision=1, idempotency_key="rev", reason="Explicit revision")
    assert service.request_cycle(goal, command) == original
    assert service.request_cycle(goal, command.model_copy(update={"expected_revision": 2, "idempotency_key": "new"})) == original
    with pytest.raises(ValueError, match="fingerprint"):
        service.request_cycle(goal, command.model_copy(update={"expected_revision": 2}))
    with pytest.raises(ValueError, match="stale"):
        service.request_cycle(goal, command.model_copy(update={"idempotency_key": "stale"}))
    assert len([row for row in rows(database, "v4_cycles") if row["id"] == cycle]) == 1
    with pytest.raises(ValueError, match="V2"):
        service.request_intent(goal, slot="bypass", due_at=command.slot_time, expires_at=spec.horizon_end)


def test_pending_cap_and_overlap_are_enforced(policy):
    service, _, spec, _ = policy
    spec = GoalSpecV2.model_validate(spec.model_dump() | {"cadence": spec.cadence.model_copy(update={"max_pending": 1})})
    goal = approved_goal(service, spec)
    first = service.request_cycle(goal, request(spec))
    next_command = request(spec, "next").model_copy(update={"slot_time": request(spec).slot_time+timedelta(seconds=60)})
    with pytest.raises(ValueError, match="pending"):
        service.request_cycle(goal, next_command)
    service.admit(first)
    second = service.request_cycle(goal, next_command)
    assert service.admit(second)["reason"] == "not_due"


def test_bounded_overlap_admits_only_configured_concurrent_cycles(policy):
    service, _, spec, _ = policy
    spec = GoalSpecV2.model_validate(spec.model_dump() | {"max_concurrent": 2, "cadence": spec.cadence.model_copy(update={"overlap": "bounded"})})
    goal = approved_goal(service, spec)
    commands = [request(spec, f"overlap-{index}").model_copy(update={"slot_time": request(spec).slot_time-timedelta(seconds=60*index)}) for index in range(3)]
    pending = [service.request_cycle(goal, command) for command in commands]
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(service.admit, pending))
    assert sorted(result["disposition"] for result in results) == ["admitted", "admitted", "deferred"]
    assert [result["reason"] for result in results if result["disposition"] == "deferred"] == ["capacity"]


def test_backfill_bounds_and_receipts_are_immutable(policy):
    service, goal, spec, database = policy
    with psycopg.connect(database) as connection:
        connection.execute("INSERT INTO permission_grants (workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at) VALUES (%s,'identity',%s,'cycles:backfill','allow','{}',now()+interval '1 hour')", (service.workspace_id, str(service.subject_id)))
    ancient = spec.model_copy(update={"cadence": spec.cadence.model_copy(update={"anchor": spec.cadence.anchor-timedelta(days=7)})})
    ancient_goal = approved_goal(service, ancient)
    with pytest.raises(ValueError, match="backfill"):
        service.request_cycle(ancient_goal, request(ancient, backfill=True))
    with pytest.raises(ValueError, match="next scheduled"):
        service.request_cycle(goal, request(spec).model_copy(update={"slot_time": request(spec).slot_time+timedelta(hours=1)}))
    requested = service.request_cycle(goal, request(spec))
    with psycopg.connect(database) as connection, pytest.raises(psycopg.Error, match="immutable"):
        connection.execute("DELETE FROM v4_cycle_requests WHERE intent_id=%s", (requested,))


@pytest.mark.parametrize("mode,expected", [("skip", 1), ("latest", 1), ("bounded", 3)])
def test_bounded_catchup_is_durable_idempotent_and_audited(policy, mode, expected):
    service, _, spec, database = policy
    spec = GoalSpecV2.model_validate(spec.model_dump() | {"cadence": spec.cadence.model_copy(update={"catch_up": mode, "max_catch_up": 3, "stale_after_seconds": 1200})})
    goal = approved_goal(service, spec)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: service.request_due(goal, expected_revision=1, idempotency_key="poll"), range(3)))
    assert results[0] == results[1] == results[2]
    assert len(results[0]["intent_ids"]) == expected and results[0]["skipped"] == 11-expected
    restarted = CycleAdmission(database, workspace_id=service.workspace_id, subject_id=service.subject_id)
    assert restarted.request_due(goal, expected_revision=1, idempotency_key="poll") == results[0]
    assert restarted.request_due(goal, expected_revision=1, idempotency_key="poll-next")["intent_ids"] == []


def test_stale_event_backfill_and_current_authority(policy):
    service, goal, spec, database = policy
    stale = request(spec).model_copy(update={"slot_time": spec.cadence.anchor})
    with pytest.raises(ValueError, match="stale"):
        service.request_cycle(goal, stale)
    with pytest.raises(PermissionError):
        service.request_cycle(goal, stale.model_copy(update={"backfill": True}))
    with psycopg.connect(database) as connection:
        connection.execute("INSERT INTO permission_grants (workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at) VALUES (%s,'identity',%s,'cycles:backfill','allow','{}',now()+interval '1 hour')", (service.workspace_id, str(service.subject_id)))
    intent = service.request_cycle(goal, stale.model_copy(update={"backfill": True}))
    assert service.admit(intent)["disposition"] == "review_required"
    service.wake(intent, expected_revision=1, review=True)
    cycle = service.admit(intent)["cycle_id"]
    with pytest.raises(ValueError, match="fresh"):
        service.request_cycle(goal, request(spec, "event").model_copy(update={"origin": "event", "event_at": spec.cadence.anchor, "event_id": "old"}))
    with psycopg.connect(database) as connection:
        connection.execute("UPDATE permission_grants SET expires_at=now()+interval '2 hours' WHERE workspace_id=%s AND scope='cycles:write'", (service.workspace_id,))
    with pytest.raises(PermissionError, match="authority"):
        service.recover(cycle, state="retry_due")
    consumer = CycleOutbox(database, workspace_id=service.workspace_id)
    assert consumer.consume(consumer.claim()["id"])["state"] == "held"


def test_policy_transaction_failure_preserves_cursor_and_receipts(policy, monkeypatch):
    service, goal, _, database = policy
    original = service._event
    def fail(*args, **kwargs):
        raise RuntimeError("simulated process failure before commit")
    monkeypatch.setattr(service, "_event", fail)
    with pytest.raises(RuntimeError):
        service.request_due(goal, expected_revision=1, idempotency_key="crash")
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM v4_schedule_cursors WHERE goal_id=%s", (goal,)).fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM v4_cycle_requests WHERE goal_id=%s", (goal,)).fetchone()[0] == 0
    monkeypatch.setattr(service, "_event", original)
    assert service.request_due(goal, expected_revision=1, idempotency_key="crash")["intent_ids"]


def test_event_identity_cannot_move_to_another_slot(policy):
    service, goal, spec, _ = policy
    command = request(spec).model_copy(update={"origin": "event", "event_at": datetime.now(timezone.utc), "event_id": "source-event"})
    original = service.request_cycle(goal, command)
    assert service.request_cycle(goal, command.model_copy(update={"idempotency_key": "transport-retry"})) == original
    with pytest.raises(ValueError, match="event identity"):
        service.request_cycle(goal, command.model_copy(update={"idempotency_key": "moved", "slot_time": command.slot_time+timedelta(seconds=60)}))


def test_program_scope_and_current_program_state(policy):
    service, goal, spec, database = policy
    with pytest.raises(PermissionError, match="program"):
        service.create_goal(spec.model_copy(update={"content_program_id": uuid4()}))
    cycle = service.admit(service.request_cycle(goal, request(spec)))["cycle_id"]
    with psycopg.connect(database) as connection:
        connection.execute("UPDATE content_programs SET status='paused' WHERE id=%s", (spec.content_program_id,))
    with pytest.raises(PermissionError, match="authority"):
        service.recover(cycle, state="retry_due")
    consumer = CycleOutbox(database, workspace_id=service.workspace_id)
    assert consumer.consume(consumer.claim()["id"])["state"] == "held"


def test_full_queue_holds_cursor_then_recovers_without_losing_slots(policy):
    service, _, spec, database = policy
    spec = GoalSpecV2.model_validate(spec.model_dump() | {"cadence": spec.cadence.model_copy(update={"max_pending": 1, "catch_up": "bounded"})})
    goal = approved_goal(service, spec)
    slot = request(spec).slot_time-timedelta(seconds=60)
    original = service.request_cycle(goal, request(spec).model_copy(update={"slot_time": slot}))
    result = service.request_due(goal, expected_revision=1, idempotency_key="full")
    assert result["held"] and not result["intent_ids"]
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM v4_schedule_cursors WHERE goal_id=%s", (goal,)).fetchone()[0] == 0
    service.admit(original)
    resumed = service.request_due(goal, expected_revision=1, idempotency_key="room")
    assert len(resumed["intent_ids"]) == 1
    assert service.admit(resumed["intent_ids"][0])["reason"] == "capacity"


def test_schedule_coalesces_full_manual_queue_without_extra_capacity(policy):
    service, _, spec, _ = policy
    spec = GoalSpecV2.model_validate(spec.model_dump() | {"cadence": spec.cadence.model_copy(update={"max_pending": 1})})
    goal = approved_goal(service, spec)
    original = service.request_cycle(goal, request(spec))
    result = service.request_due(goal, expected_revision=1, idempotency_key="coalesce")
    assert not result["held"] and result["intent_ids"] == [str(original)]


def test_stale_revision_pending_intent_does_not_block_current_revision(policy):
    service, _, spec, _ = policy
    spec = GoalSpecV2.model_validate(spec.model_dump() | {"cadence": spec.cadence.model_copy(update={"max_pending": 1})})
    goal = approved_goal(service, spec)
    old = service.request_cycle(goal, request(spec).model_copy(update={"slot_time": request(spec).slot_time-timedelta(seconds=60)}))
    service.revise_goal(goal, spec, expected_revision=1, idempotency_key="revision", reason="New baseline revision")
    fresh = service.request_cycle(goal, request(spec, "new").model_copy(update={"expected_revision": 2}))
    assert fresh != old and service.admit(old)["disposition"] == "denied"


@pytest.mark.parametrize("boundary", ["event", "slot"])
def test_admission_rechecks_policy_freshness_after_wait(policy, boundary):
    import time
    service, _, spec, _ = policy
    cadence = spec.cadence.model_copy(update={"event_freshness_seconds": 1} if boundary == "event" else {"stale_after_seconds": 1})
    spec = GoalSpecV2.model_validate(spec.model_dump() | {"cadence": cadence})
    goal = approved_goal(service, spec)
    command = request(spec)
    if boundary == "event":
        command = command.model_copy(update={"origin": "event", "event_at": datetime.now(timezone.utc), "event_id": "fresh-then-stale"})
    intent = service.request_cycle(goal, command)
    time.sleep(1.05)
    assert service.admit(intent)["disposition"] == "denied"


def test_v2_cannot_downgrade_or_move_anchor_and_requires_new_baseline(policy, cycles):
    service, goal, spec, _ = policy
    for changed in [cycles[2], spec.model_copy(update={"cadence": spec.cadence.model_copy(update={"anchor": spec.cadence.anchor-timedelta(seconds=60)})})]:
        with pytest.raises(ValueError, match="V2"):
            service.revise_goal(goal, changed, expected_revision=1, idempotency_key="invalid", reason="Cannot change identity")
    service.revise_goal(goal, spec.model_copy(update={"objective": "Revised"}), expected_revision=1, idempotency_key="valid", reason="New material goal")
    requested = service.request_cycle(goal, request(spec).model_copy(update={"expected_revision": 2}))
    assert service.admit(requested)["reason"] == "baseline_approval_required"


def test_legacy_upgrade_and_interval_revision_preserve_old_context(policy, cycles):
    from test_v4_cycle_admission import intent
    service, _, spec, database = policy
    legacy_goal = cycles[1]
    legacy_cycle = service.admit(intent(service, legacy_goal))["cycle_id"]
    original = next(row for row in rows(database, "v4_run_contexts") if row["cycle_id"] == legacy_cycle)
    service.revise_goal(legacy_goal, spec, expected_revision=1, idempotency_key="upgrade", reason="Explicit V2 policy")
    command = request(spec).model_copy(update={"expected_revision": 2})
    pending = service.request_cycle(legacy_goal, command)
    assert service.admit(pending)["reason"] == "baseline_approval_required"
    revised = spec.model_copy(update={"cadence": spec.cadence.model_copy(update={"interval_seconds": 120})})
    service.revise_goal(legacy_goal, revised, expected_revision=2, idempotency_key="interval", reason="Explicit cadence revision")
    assert service.request_cycle(legacy_goal, command.model_copy(update={"expected_revision": 3, "idempotency_key": "same-slot"})) == pending
    with pytest.raises(ValueError, match="aligned"):
        service.request_cycle(legacy_goal, command.model_copy(update={"expected_revision": 3, "idempotency_key": "misaligned", "slot_time": command.slot_time+timedelta(seconds=60)}))
    assert next(row for row in rows(database, "v4_run_contexts") if row["id"] == original["id"]) == original


def test_revoked_authority_before_admission_creates_no_cycle(policy):
    service, goal, spec, database = policy
    pending = service.request_cycle(goal, request(spec))
    with psycopg.connect(database) as connection:
        connection.execute("UPDATE permission_grants SET effect='deny' WHERE workspace_id=%s AND scope='cycles:write'", (service.workspace_id,))
    with pytest.raises(PermissionError):
        service.admit(pending)
    assert not [row for row in rows(database, "v4_cycles") if row["intent_id"] == pending]


def test_restricted_application_role_policy_and_authority_lock(policy):
    from urllib.parse import urlsplit, urlunsplit
    service, goal, spec, database = policy
    role, password = "v4_policy_"+uuid4().hex, uuid4().hex
    identifier = psycopg.sql.Identifier(role)
    with psycopg.connect(database, autocommit=True) as admin:
        admin.execute(psycopg.sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS").format(identifier, psycopg.sql.Literal(password)))
        try:
            admin.execute(psycopg.sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(identifier))
            admin.execute(psycopg.sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(identifier))
            admin.execute(psycopg.sql.SQL("GRANT INSERT ON v4_cycle_requests,v4_cycle_intents,v4_cycle_events,v4_cycles,v4_run_contexts,v4_cycle_outbox,v4_cycle_inbox,v4_admissions,v4_schedule_batches,v4_schedule_cursors TO {}").format(identifier))
            admin.execute(psycopg.sql.SQL("GRANT UPDATE ON v4_goals,v4_cycle_intents,v4_cycles,v4_cycle_outbox,v4_schedule_cursors TO {}").format(identifier))
            admin.execute(psycopg.sql.SQL("GRANT EXECUTE ON FUNCTION p0_lock_identity(text,text,uuid) TO {}").format(identifier))
            if admin.execute("SELECT to_regprocedure('v4_lock_program(uuid,uuid)')").fetchone()[0]:
                admin.execute(psycopg.sql.SQL("GRANT EXECUTE ON FUNCTION v4_lock_program(uuid,uuid) TO {}").format(identifier))
            parts = urlsplit(database)
            restricted = urlunsplit(parts._replace(netloc=f"{role}:{password}@{parts.hostname}:{parts.port or 5432}"))
            with psycopg.connect(restricted, row_factory=dict_row) as reader:
                assert not reader.execute("SELECT has_table_privilege(current_user,'permission_grants','UPDATE') AS allowed").fetchone()["allowed"]
                from salience.cycles.authority import current_authority, require_program
                current_authority(reader, service.workspace_id, service.subject_id)
                require_program(reader, service.workspace_id, spec.content_program_id)
                with psycopg.connect(database) as writer:
                    for statement, params in [("UPDATE permission_grants SET effect='deny' WHERE workspace_id=%s AND scope='cycles:write'", (service.workspace_id,)),
                                              ("DELETE FROM permission_grants WHERE workspace_id=%s AND scope='cycles:write'", (service.workspace_id,)),
                                              ("UPDATE content_programs SET status='paused' WHERE id=%s", (spec.content_program_id,))]:
                        with pytest.raises(psycopg.errors.LockNotAvailable), writer.transaction():
                            writer.execute("SET LOCAL lock_timeout='100ms'")
                            writer.execute(statement, params)
            local = CycleAdmission(restricted, workspace_id=service.workspace_id, subject_id=service.subject_id)
            admitted = local.admit(local.request_cycle(goal, request(spec)))
            consumer = CycleOutbox(restricted, workspace_id=service.workspace_id)
            assert consumer.consume(consumer.claim()["id"])["state"] == "recorded"
            with psycopg.connect(database) as writer:
                writer.execute("UPDATE permission_grants SET effect='deny' WHERE workspace_id=%s AND scope='cycles:write'", (service.workspace_id,))
            with pytest.raises(PermissionError):
                local.recover(admitted["cycle_id"], state="retry_due")
        finally:
            admin.execute(psycopg.sql.SQL("DROP OWNED BY {}").format(identifier))
            admin.execute(psycopg.sql.SQL("DROP ROLE {}").format(identifier))
