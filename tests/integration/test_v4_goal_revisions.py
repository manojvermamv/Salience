from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
import pytest

from salience.cycles.admission import CycleAdmission
from salience.cycles.outbox import CycleOutbox
from tests.integration.test_v4_cycle_admission import cycles, intent, rows


def revise(service, goal, spec, key="revision-2", expected=1):
    return service.revise_goal(goal, spec, expected_revision=expected, idempotency_key=key, reason="Updated fixture objective")


def test_revision_cas_idempotency_and_frozen_context(cycles):
    service, goal, spec, database = cycles
    due = datetime.now(timezone.utc)-timedelta(seconds=1)
    expiry = due+timedelta(hours=1)
    requested = intent(service,goal,due_at=due,expires_at=expiry)
    admitted = service.admit(requested)
    original = [row for row in rows(database,"v4_run_contexts") if row["cycle_id"]==admitted["cycle_id"]][0]
    changed = spec.model_copy(update={"objective":"Revised fixture objective"})
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(lambda _: revise(service,goal,changed),range(4)))==[2]*4
    assert revise(service,goal,spec,key="revision-3",expected=2)==3
    assert revise(service,goal,changed)==2
    assert intent(service,goal,due_at=due,expires_at=expiry)==requested
    assert service.admit(requested)==admitted
    assert [row for row in rows(database,"v4_run_contexts") if row["cycle_id"]==admitted["cycle_id"]][0]==original
    assert len([row for row in rows(database,"v4_goal_commands") if row["goal_id"]==goal])==2
    with pytest.raises(ValueError,match="conflict"):
        revise(service,goal,spec)
    with pytest.raises(ValueError,match="revision"):
        revise(service,goal,changed,key="competing")


def test_concurrent_distinct_revision_commands_allow_one_winner(cycles):
    service, goal, spec, database = cycles
    def attempt(key):
        try:
            return revise(service,goal,spec,key=key)
        except ValueError:
            return "stale"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(attempt,["first","second"]))
    assert results.count(2)==1 and results.count("stale")==1
    assert len([row for row in rows(database,"v4_goal_revisions") if row["goal_id"]==goal])==2


def test_existing_slot_retry_survives_shorter_revised_horizon(cycles):
    service,goal,spec,_=cycles
    due=datetime.now(timezone.utc)-timedelta(seconds=1)
    expiry=due+timedelta(hours=1)
    requested=intent(service,goal,due_at=due,expires_at=expiry)
    revise(service,goal,spec.model_copy(update={"horizon_end":due+timedelta(minutes=30)}))
    assert intent(service,goal,due_at=due,expires_at=expiry)==requested
    with pytest.raises(ValueError,match="horizon"):
        intent(service,goal,"new",due_at=due,expires_at=expiry)
    service.set_goal_state(goal,"cancelled")
    assert intent(service,goal,due_at=due,expires_at=expiry)==requested
    assert service.admit(requested)["disposition"]=="denied"


def test_revision_rolls_back_with_audit_and_restarts_safely(cycles,monkeypatch):
    service, goal, spec, database = cycles
    original = service._event
    def crash(*args,**kwargs):
        raise RuntimeError("before commit")
    monkeypatch.setattr(service,"_event",crash)
    with pytest.raises(RuntimeError,match="before commit"):
        revise(service,goal,spec)
    assert len([row for row in rows(database,"v4_goal_revisions") if row["goal_id"]==goal])==1
    assert not [row for row in rows(database,"v4_goal_commands") if row["goal_id"]==goal]
    monkeypatch.setattr(service,"_event",original)
    restored=CycleAdmission(database,workspace_id=service.workspace_id,subject_id=service.subject_id)
    assert revise(restored,goal,spec)==2


def test_committed_successor_retry_does_not_renew_authority(cycles):
    service,goal,spec,_=cycles
    first=service.admit(intent(service,goal,"first"))
    service.close(first["cycle_id"],disposition="defer",reason="fixture evidence")
    due=datetime.now(timezone.utc)
    expiry=due+timedelta(hours=1)
    successor=intent(service,goal,"next",due_at=due,expires_at=expiry,predecessor_cycle_id=first["cycle_id"])
    unlinked=intent(service,goal,"unlinked",due_at=due,expires_at=expiry)
    revise(service,goal,spec.model_copy(update={"horizon_end":due+timedelta(minutes=30)}))
    assert intent(service,goal,"next",due_at=due,expires_at=expiry,predecessor_cycle_id=first["cycle_id"])==successor
    with pytest.raises(ValueError):
        intent(service,goal,"unlinked",due_at=due,expires_at=expiry,predecessor_cycle_id=first["cycle_id"])
    assert service.admit(successor)["disposition"]=="denied"
    assert service.admit(unlinked)["disposition"]=="denied"


def test_stale_intent_review_recovery_and_delivery_hold(cycles):
    service, goal, spec, database = cycles
    admitted=service.admit(intent(service,goal,"admitted"))
    waiting=intent(service,goal,"waiting")
    assert service.admit(waiting)["disposition"]=="deferred"
    pending=intent(service,goal,"pending")
    revise(service,goal,spec)
    assert service.admit(pending)["disposition"]=="denied"
    with pytest.raises(ValueError,match="revision"):
        service.wake(waiting,expected_revision=1)
    with pytest.raises(ValueError,match="revision"):
        service.recover(admitted["cycle_id"],state="retry_due")
    message=next(row for row in rows(database,"v4_cycle_outbox") if row["cycle_id"]==admitted["cycle_id"])
    assert CycleOutbox(database,workspace_id=service.workspace_id).consume(message["id"])["state"]=="held"
    assert service.close(admitted["cycle_id"],disposition="cancelled",reason="stale revision")["state"]=="closed"


def test_revision_requires_current_scope_and_nonterminal_goal(cycles):
    service, goal, spec, database=cycles
    with pytest.raises(ValueError):
        revise(service,goal,spec,key=" ")
    with pytest.raises(ValueError):
        revise(service,goal,spec,expected=True)
    with pytest.raises(PermissionError):
        revise(CycleAdmission(database,workspace_id=uuid4(),subject_id=service.subject_id),goal,spec)
    service.set_goal_state(goal,"completed")
    with pytest.raises(ValueError,match="terminal"):
        revise(service,goal,spec)
    with psycopg.connect(database) as connection:
        connection.execute("UPDATE identity_subjects SET enabled=false WHERE id=%s",(service.subject_id,))
    with pytest.raises(PermissionError):
        revise(service,goal,spec)


def test_database_guards_goal_identity_revision_and_terminal_state(cycles):
    service, goal, spec, database=cycles
    for requested_revision in [2,99]:
        with psycopg.connect(database) as connection:
            with pytest.raises(psycopg.errors.RaiseException,match="next committed payload"):
                connection.execute("UPDATE v4_goals SET revision=%s WHERE id=%s",(requested_revision,goal))
    revise(service,goal,spec)
    with psycopg.connect(database) as connection:
        with pytest.raises(psycopg.errors.RaiseException,match="next committed payload"):
            connection.execute("UPDATE v4_goals SET revision=1 WHERE id=%s",(goal,))
    with psycopg.connect(database) as connection:
        connection.execute("INSERT INTO v4_goal_revisions (goal_id,revision,payload) VALUES (%s,4,'{}')",(goal,))
    with psycopg.connect(database) as connection:
        with pytest.raises(psycopg.errors.RaiseException,match="next committed payload"):
            connection.execute("UPDATE v4_goals SET revision=4 WHERE id=%s",(goal,))
    service.set_goal_state(goal,"cancelled")
    statements=[
        ("UPDATE v4_goals SET state='active' WHERE id=%s",(goal,)),
        ("UPDATE v4_goals SET revision=1 WHERE id=%s",(goal,)),
        ("UPDATE v4_goals SET revision=99 WHERE id=%s",(goal,)),
        ("UPDATE v4_goals SET id=%s WHERE id=%s",(uuid4(),goal)),
        ("DELETE FROM v4_goal_commands WHERE goal_id=%s",(goal,)),
    ]
    for sql,params in statements:
        with psycopg.connect(database) as connection:
            with pytest.raises(psycopg.errors.RaiseException):
                connection.execute(sql,params)
