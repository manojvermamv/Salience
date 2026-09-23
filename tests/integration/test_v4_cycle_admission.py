import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
import pytest
from pydantic import ValidationError

from salience.cycles.admission import CycleAdmission
from salience.cycles.contracts import GoalSpec


@pytest.fixture
def cycles():
    database = os.environ["TEST_DATABASE_URL"]
    workspace, subject = uuid4(), uuid4()
    with psycopg.connect(database) as connection:
        connection.execute("INSERT INTO workspaces (id,slug,display_name) VALUES (%s,%s,'P1 fixture')", (workspace, str(workspace)))
        connection.execute("INSERT INTO identity_subjects (id,workspace_id,issuer,subject,expires_at) VALUES (%s,%s,'https://fixture.invalid',%s,now()+interval '1 hour')", (subject, workspace, str(subject)))
        for scope in ["goals:write", "cycles:write", "cycles:review"]:
            connection.execute("INSERT INTO permission_grants (workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at) VALUES (%s,'identity',%s,%s,'allow','{}',now()+interval '1 hour')", (workspace,str(subject),scope))
    service = CycleAdmission(database, workspace_id=workspace, subject_id=subject)
    spec = GoalSpec(objective="Fixture evidence only", metric_versions=("fixture-quality@1",), audience="internal", account_refs=("fixture-account",), brand_scope="fixture-brand", source_policy="fixture-only", horizon_end=datetime.now(timezone.utc)+timedelta(days=1))
    goal = service.create_goal(spec)
    return service, goal, spec, database


def intent(service, goal, slot="slot", **changes):
    fields = {"due_at": datetime.now(timezone.utc)-timedelta(seconds=1), "expires_at": datetime.now(timezone.utc)+timedelta(hours=1)} | changes
    return service.request_intent(goal, slot=slot, **fields)


def rows(database, table):
    with psycopg.connect(database, row_factory=dict_row) as connection:
        return connection.execute(psycopg.sql.SQL("SELECT * FROM {} ORDER BY created_at").format(psycopg.sql.Identifier(table))).fetchall()


def test_pre_admission_defer_reuses_intent_without_cycle(cycles):
    service, goal, _, database = cycles
    future = datetime.now(timezone.utc)+timedelta(minutes=1)
    requested = intent(service, goal, due_at=future)
    result = service.admit(requested)
    assert result["disposition"] == "deferred" and result["cycle_id"] is None
    assert service.admit(requested) == result
    with pytest.raises(ValueError, match="not due"):
        service.wake(requested, expected_revision=1)
    assert not [row for row in rows(database, "v4_cycles") if row["intent_id"] == requested]


def test_review_readmission_is_cas_and_creates_only_one_cycle(cycles):
    service, _, spec, database = cycles
    goal = service.create_goal(spec.model_copy(update={"review_required": True}))
    requested = intent(service, goal)
    assert service.admit(requested)["disposition"] == "review_required"
    with pytest.raises(PermissionError):
        service.wake(requested, expected_revision=1)
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(lambda _: service.wake(requested, expected_revision=1, review=True), range(4))) == [2]*4
        results = list(pool.map(lambda _: service.admit(requested), range(4)))
    assert len({result["cycle_id"] for result in results}) == 1
    assert results[0]["disposition"] == "admitted"
    assert len([row for row in rows(database,"v4_admissions") if row["intent_id"] == requested]) == 2


@pytest.mark.parametrize("state", ["retry_due", "reconciling"])
def test_recovery_preserves_cycle_operation_and_context(cycles, state):
    service, goal, _, database = cycles
    requested = intent(service, goal)
    admitted = service.admit(requested)
    cycle = service.recover(admitted["cycle_id"], state=state)
    resumed = service.recover(admitted["cycle_id"], state="runnable")
    assert (cycle["id"],cycle["operation_id"],cycle["context_id"]) == (resumed["id"],resumed["operation_id"],resumed["context_id"])
    assert len([row for row in rows(database,"v4_cycles") if row["intent_id"] == requested]) == 1


@pytest.mark.parametrize("disposition", ["defer", "abstain"])
def test_strategic_closure_needs_authorized_coalesced_successor(cycles, disposition):
    service, goal, _, database = cycles
    admitted = service.admit(intent(service, goal))
    service.close(admitted["cycle_id"], disposition=disposition, reason="insufficient fixture evidence")
    assert service.close(admitted["cycle_id"], disposition=disposition, reason="insufficient fixture evidence")["state"] == "closed"
    with pytest.raises(ValueError, match="closed"):
        service.recover(admitted["cycle_id"], state="runnable")
    due = datetime.now(timezone.utc)
    expiry = due+timedelta(hours=1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        scheduled = pool.submit(service.request_intent,goal,slot="next",due_at=due,expires_at=expiry)
        successor = pool.submit(service.request_intent,goal,slot="next",due_at=due,expires_at=expiry,predecessor_cycle_id=admitted["cycle_id"])
        assert scheduled.result() == successor.result()
    next_cycle = service.admit(successor.result())
    assert next_cycle["cycle_id"] != admitted["cycle_id"]
    with psycopg.connect(database) as connection:
        connection.execute("UPDATE identity_subjects SET enabled=false WHERE id=%s", (service.subject_id,))
    with pytest.raises(PermissionError):
        service.request_intent(goal,slot="unauthorized",due_at=due,expires_at=expiry,predecessor_cycle_id=admitted["cycle_id"])


def test_capacity_deferral_can_readmit_same_intent_after_close(cycles):
    service, goal, _, _ = cycles
    first = service.admit(intent(service,goal,"first"))
    second = intent(service,goal,"second")
    assert service.admit(second)["disposition"] == "deferred"
    service.close(first["cycle_id"],disposition="abstain",reason="fixture exhausted")
    service.wake(second,expected_revision=1)
    assert service.admit(second)["disposition"] == "admitted"


def test_slot_fingerprint_context_immutability_and_no_live_mode(cycles):
    service, goal, spec, database = cycles
    due = datetime.now(timezone.utc)
    expiry = due+timedelta(hours=1)
    requested = service.request_intent(goal,slot="one",due_at=due,expires_at=expiry)
    with pytest.raises(ValueError,match="fingerprint"):
        service.request_intent(goal,slot="one",due_at=due+timedelta(seconds=1),expires_at=expiry)
    admitted = service.admit(requested)
    context = [row for row in rows(database,"v4_run_contexts") if row["cycle_id"] == admitted["cycle_id"]][0]
    assert context["payload"]["provider"] == "fixture.dummy@1.0.0"
    assert context["payload"]["production_effects_enabled"] is False
    with psycopg.connect(database) as connection:
        with pytest.raises(psycopg.errors.RaiseException,match="immutable"):
            connection.execute("UPDATE v4_run_contexts SET payload=%s WHERE id=%s", (Jsonb({}),context["id"]))
    for changes in [{"dry_run":False},{"max_spend":1},{"provider":"unapproved@1"},{"fallback":"other"}]:
        with pytest.raises(ValidationError):
            GoalSpec.model_validate(spec.model_dump() | changes)


def test_stopped_goal_and_foreign_scope_cannot_admit(cycles):
    service, goal, _, database = cycles
    requested = intent(service,goal)
    service.set_goal_state(goal,"cancelled")
    assert service.admit(requested)["disposition"] == "denied"
    with pytest.raises(ValueError):
        service.set_goal_state(goal,"active")
    with pytest.raises(PermissionError):
        CycleAdmission(database,workspace_id=uuid4(),subject_id=service.subject_id).admit(requested)


def test_crash_before_commit_rolls_back_admission_and_restart_reuses_identity(cycles, monkeypatch):
    service, goal, _, database = cycles
    requested = intent(service,goal)
    original = service._event
    def fail(*args,**kwargs):
        raise RuntimeError("crash before commit")
    monkeypatch.setattr(service,"_event",fail)
    with pytest.raises(RuntimeError,match="crash"):
        service.admit(requested)
    monkeypatch.setattr(service,"_event",original)
    restored = CycleAdmission(database,workspace_id=service.workspace_id,subject_id=service.subject_id)
    result = restored.admit(requested)
    assert restored.admit(requested) == result
    assert len([row for row in rows(database,"v4_cycles") if row["intent_id"] == requested]) == 1


def test_due_wake_duplicate_and_expired_review(cycles):
    service, goal, spec, _ = cycles
    requested = intent(service,goal,due_at=datetime.now(timezone.utc)+timedelta(milliseconds=150))
    assert service.admit(requested)["disposition"] == "deferred"
    time.sleep(0.2)
    assert service.wake(requested,expected_revision=1) == 2
    assert service.wake(requested,expected_revision=1) == 2
    assert service.admit(requested)["cycle_id"] == service.admit(requested)["cycle_id"]
    review_goal = service.create_goal(spec.model_copy(update={"review_required":True}))
    review = intent(service,review_goal,expires_at=datetime.now(timezone.utc)+timedelta(milliseconds=150))
    assert service.admit(review)["disposition"] == "review_required"
    time.sleep(0.2)
    with pytest.raises(ValueError,match="expired"):
        service.wake(review,expected_revision=1,review=True)


def test_review_permission_and_recovery_limit_are_current(cycles):
    service, _, spec, database = cycles
    goal = service.create_goal(spec.model_copy(update={"review_required":True,"max_wakes":1}))
    requested = intent(service,goal)
    service.admit(requested)
    with psycopg.connect(database) as connection:
        connection.execute("UPDATE permission_grants SET effect='deny' WHERE principal_id=%s AND scope='cycles:review'", (str(service.subject_id),))
    with pytest.raises(PermissionError):
        service.wake(requested,expected_revision=1,review=True)
    plain = service.create_goal(spec.model_copy(update={"max_wakes":1}))
    admitted = service.admit(intent(service,plain))
    service.recover(admitted["cycle_id"],state="retry_due")
    service.recover(admitted["cycle_id"],state="runnable")
    with pytest.raises(ValueError,match="limit"):
        service.recover(admitted["cycle_id"],state="retry_due")


def test_database_rejects_identity_rewrite_and_reopening_closed_cycle(cycles):
    service, goal, _, database = cycles
    requested = intent(service,goal)
    admitted = service.admit(requested)
    service.close(admitted["cycle_id"],disposition="abstain",reason="no evidence")
    for sql, params in [("UPDATE v4_cycle_intents SET slot='rewritten' WHERE id=%s",(requested,)),("UPDATE v4_cycles SET operation_id=%s WHERE id=%s",(uuid4(),admitted["cycle_id"])),("UPDATE v4_cycles SET state='runnable',closed_at=NULL,disposition=NULL WHERE id=%s",(admitted["cycle_id"],))]:
        with psycopg.connect(database) as connection:
            with pytest.raises(psycopg.errors.RaiseException,match="immutable"):
                connection.execute(sql,params)
