from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import time
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from salience.cycles.outbox import CycleOutbox
from tests.integration.test_v4_cycle_admission import cycles, intent, rows


def approve(service,goal,spec,**changes):
    return service.approve_baseline(goal,expected_revision=1,expires_at=spec.horizon_end,reason="Explicit no-effects fixture baseline",**changes)


def test_missing_baseline_blocks_admission_without_cycle(cycles):
    service,_,spec,database=cycles
    goal=service.create_goal(spec)
    requested=intent(service,goal)
    result=service.admit(requested)
    assert result["disposition"]=="review_required"
    assert result["reason"]=="baseline_approval_required"
    assert result["cycle_id"] is None
    assert not [row for row in rows(database,"v4_cycles") if row["intent_id"]==requested]


def test_explicit_approval_is_scoped_idempotent_and_frozen(cycles):
    service,_,spec,database=cycles
    goal=service.create_goal(spec)
    with ThreadPoolExecutor(max_workers=3) as pool:
        approval_ids=list(pool.map(lambda _:approve(service,goal,spec),range(3)))
    assert len(set(approval_ids))==1
    requested=intent(service,goal)
    admitted=service.admit(requested)
    context=next(row["payload"] for row in rows(database,"v4_run_contexts") if row["cycle_id"]==admitted["cycle_id"])
    assert context["baseline_approval_id"]==str(approval_ids[0])
    assert context["strategy_version"]=="fixture.baseline@1.0.0"
    assert context["prompt_version"]=="fixture.noop@1.0.0"
    assert context["capability_manifest"]["plugin_id"]=="fixture.dummy"
    assert context["capability_manifest"]["version"]=="1.0.0"
    assert context["capability_manifest"]["effect_classification"]=="none"
    assert context["capability_manifest"]["required_secret_scopes"]==[]
    with pytest.raises(ValueError,match="conflict"):
        service.approve_baseline(goal,expected_revision=1,expires_at=spec.horizon_end,reason="different")
    service.revise_goal(goal,spec,expected_revision=1,idempotency_key="new",reason="new material goal")
    assert approve(service,goal,spec)==approval_ids[0]
    assert service.admit(intent(service,goal,"new"))["reason"]=="baseline_approval_required"


def test_revoked_baseline_holds_delivery_and_cannot_be_reactivated(cycles):
    service,_,spec,database=cycles
    goal=service.create_goal(spec)
    approval_id=approve(service,goal,spec)
    admitted=service.admit(intent(service,goal))
    service.revoke_baseline(goal,approval_id=approval_id,reason="fixture stop")
    service.revoke_baseline(goal,approval_id=approval_id,reason="fixture stop")
    with pytest.raises(ValueError,match="conflict"):
        service.revoke_baseline(goal,approval_id=approval_id,reason="changed")
    assert approve(service,goal,spec)==approval_id
    message=next(row for row in rows(database,"v4_cycle_outbox") if row["cycle_id"]==admitted["cycle_id"])
    assert CycleOutbox(database,workspace_id=service.workspace_id).consume(message["id"])["state"]=="held"
    service.close(admitted["cycle_id"],disposition="cancelled",reason="fixture stop")
    assert service.admit(intent(service,goal,"later"))["reason"]=="baseline_approval_required"
    for table in ["v4_goal_baselines","v4_baseline_revocations"]:
        with psycopg.connect(database) as connection:
            with pytest.raises(psycopg.errors.RaiseException,match="immutable"):
                connection.execute(psycopg.sql.SQL("DELETE FROM {} WHERE approval_id=%s").format(psycopg.sql.Identifier(table)),(approval_id,))


def test_baseline_approval_requires_separate_current_grant(cycles):
    service,_,spec,database=cycles
    goal=service.create_goal(spec)
    with psycopg.connect(database) as connection:
        connection.execute("DELETE FROM permission_grants WHERE principal_id=%s AND scope='goals:approve'",(str(service.subject_id),))
    with pytest.raises(PermissionError):
        approve(service,goal,spec)
    assert service.admit(intent(service,goal))["cycle_id"] is None


def test_expired_and_foreign_approval_cannot_authorize(cycles):
    service,goal,spec,database=cycles
    other=service.create_goal(spec)
    with pytest.raises(ValueError,match="expiry"):
        service.approve_baseline(other,expected_revision=1,expires_at=datetime.now(timezone.utc)-timedelta(seconds=1),reason="expired")
    approval_id=approve(service,other,spec)
    with pytest.raises(PermissionError):
        service.revoke_baseline(goal,approval_id=approval_id,reason="wrong goal")
    with psycopg.connect(database) as connection:
        connection.execute("UPDATE identity_subjects SET enabled=false WHERE id=%s",(service.subject_id,))
    with pytest.raises(PermissionError):
        approve(service,other,spec)


def test_baseline_expiry_is_rechecked_before_consumption_and_recovery(cycles):
    service,_,spec,database=cycles
    goal=service.create_goal(spec)
    service.approve_baseline(goal,expected_revision=1,expires_at=datetime.now(timezone.utc)+timedelta(seconds=5),reason="short-lived fixture")
    admitted=service.admit(intent(service,goal))
    assert admitted["cycle_id"] is not None
    time.sleep(5.1)
    message=next(row for row in rows(database,"v4_cycle_outbox") if row["cycle_id"]==admitted["cycle_id"])
    assert CycleOutbox(database,workspace_id=service.workspace_id).consume(message["id"])["state"]=="held"
    with pytest.raises(ValueError,match="approved baseline"):
        service.recover(admitted["cycle_id"],state="retry_due")


def test_baseline_approval_rollback_preserves_atomic_audit(cycles,monkeypatch):
    service,_,spec,database=cycles
    goal=service.create_goal(spec)
    def crash(*args,**kwargs):
        raise RuntimeError("approval before commit")
    monkeypatch.setattr(service,"_event",crash)
    with pytest.raises(RuntimeError,match="before commit"):
        approve(service,goal,spec)
    assert not [row for row in rows(database,"v4_goal_baselines") if row["goal_id"]==goal]


def test_admission_racing_revocation_never_consumes_under_revoked_baseline(cycles):
    service,_,spec,database=cycles
    goal=service.create_goal(spec)
    approval_id=approve(service,goal,spec)
    requested=intent(service,goal)
    with ThreadPoolExecutor(max_workers=2) as pool:
        admission=pool.submit(service.admit,requested)
        revocation=pool.submit(service.revoke_baseline,goal,approval_id=approval_id,reason="concurrent stop")
        result=admission.result()
        revocation.result()
    assert result["disposition"] in {"admitted","review_required"}
    if result["cycle_id"]:
        message=next(row for row in rows(database,"v4_cycle_outbox") if row["cycle_id"]==result["cycle_id"])
        assert CycleOutbox(database,workspace_id=service.workspace_id).consume(message["id"])["state"]=="held"


def test_non_fixture_bundle_is_not_accepted_as_approved(cycles):
    service,_,spec,database=cycles
    goal=service.create_goal(spec)
    with psycopg.connect(database) as connection:
        connection.execute("INSERT INTO v4_goal_baselines (approval_id,goal_id,goal_revision,subject_id,bundle,expires_at,reason,traceparent) VALUES (%s,%s,1,%s,'{\"capability_manifest\":{\"plugin_id\":\"unapproved\"}}',%s,'invalid fixture',%s)",(uuid4(),goal,service.subject_id,spec.horizon_end,service.trace.to_carrier()["traceparent"]))
    result=service.admit(intent(service,goal))
    assert result["reason"]=="baseline_approval_required" and result["cycle_id"] is None


def test_historical_context_cannot_inherit_new_goal_approval(cycles):
    service,_,spec,database=cycles
    goal=service.create_goal(spec)
    requested=intent(service,goal)
    cycle_id,context_id=uuid4(),uuid4()
    payload=spec.model_dump(mode="json") | {"production_effects_enabled":False,"goal_revision":1}
    with psycopg.connect(database) as connection:
        connection.execute("INSERT INTO v4_cycles (id,intent_id,context_id,operation_id,state) VALUES (%s,%s,%s,%s,'runnable')",(cycle_id,requested,context_id,uuid4()))
        connection.execute("INSERT INTO v4_run_contexts (id,cycle_id,payload) VALUES (%s,%s,%s)",(context_id,cycle_id,Jsonb(payload)))
    approve(service,goal,spec)
    with pytest.raises(ValueError,match="approved baseline"):
        service.recover(cycle_id,state="retry_due")
    assert not [row for row in rows(database,"v4_cycle_outbox") if row["cycle_id"]==cycle_id]
