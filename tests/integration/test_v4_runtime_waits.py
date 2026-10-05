"""Durable deadline delivery, authority races and preservation on PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import time

import psycopg
import pytest

from test_v4_cycle_governance import governed, admitted, review_response
from test_v4_cadence_policy import policy
from test_v4_cycle_accounting import accounting
from test_v4_cycle_admission import cycles, approved_goal
from salience.cycles.runtime_waits import RuntimeWaits


def open_review(governed, seconds=.2, kind="manual_review", failure="manual_review"):
    service, _, _, database = governed
    intent, cycle = admitted(governed)
    opened = service.open_case(cycle, target="cycle", kind=kind, failure_class=failure,
        owner_id=service.subject_id, deadline=datetime.now(timezone.utc)+timedelta(seconds=seconds),
        reason="durable deadline fixture", artifact_sha256="c"*64, account_ref="fixture-account")
    waits = RuntimeWaits(database, workspace_id=service.workspace_id, subject_id=service.subject_id)
    return service, database, intent, cycle, opened, waits


def test_due_case_is_suspended_once_and_notification_has_separate_delivery_receipt(governed):
    service, database, _, cycle, opened, waits = open_review(governed)
    job = waits.pending()[0]
    assert waits.deliver_one() is True
    time.sleep(.25)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: waits.fire(job["id"]), range(2)))
    assert outcomes[0] == outcomes[1]
    assert outcomes[0]["state"] == "suspended"
    assert waits.deliver_one() is True
    assert waits.deliver_one() is False
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM v4_case_events WHERE case_id=%s AND action='escalated'", (opened["case_id"],)).fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM v4_notification_acceptances WHERE case_id=%s", (opened["case_id"],)).fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM v4_case_acks WHERE notification_id=%s", (opened["notification_id"],)).fetchone()[0] == 0
        assert connection.execute("SELECT context_id,operation_id FROM v4_cycles WHERE id=%s", (cycle,)).fetchone() == connection.execute("SELECT context_id,operation_id FROM v4_recovery_cases WHERE id=%s", (opened["case_id"],)).fetchone()


def test_review_wins_before_deadline_and_obsolete_notice_never_delivers(governed):
    service, database, _, cycle, opened, waits = open_review(governed, seconds=2)
    job = waits.pending()[0]
    response = review_response(database, opened)
    service.respond_review(opened["case_id"], response, idempotency_key="before-deadline")
    assert waits.fire(job["id"])["state"] == "obsolete"
    assert waits.deliver_one() is True  # records suppressed disposition
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM v4_notification_acceptances WHERE case_id=%s", (opened["case_id"],)).fetchone()[0] == 0
        assert connection.execute("SELECT state FROM v4_cycles WHERE id=%s", (cycle,)).fetchone()[0] != "runnable"


@pytest.mark.parametrize("kind,failure", [("retry","technical_failure"),("reconciliation","unknown_effect"),("rework","quality_failure")])
def test_recovery_deadlines_suspend_same_case_without_resuming_or_new_cycle(governed, kind, failure):
    _, database, intent, cycle, opened, waits = open_review(governed, kind=kind, failure=failure)
    job = waits.pending()[0]
    time.sleep(.25)
    assert waits.fire(job["id"])["state"] == "suspended"
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM v4_cycles WHERE intent_id=%s", (intent,)).fetchone()[0] == 1
        assert connection.execute("SELECT action_count FROM v4_recovery_cases WHERE id=%s", (opened["case_id"],)).fetchone()[0] == 0


def test_revoked_operator_cannot_fire_or_deliver_and_wait_remains_pending(governed):
    service, database, _, _, opened, waits = open_review(governed)
    job = waits.pending()[0]
    with psycopg.connect(database) as connection:
        connection.execute("UPDATE permission_grants SET expires_at=now()-interval '1 second' WHERE principal_id=%s AND scope='cycles:case_operator'", (str(service.subject_id),))
    time.sleep(.25)
    for command in (lambda: waits.fire(job["id"]), waits.deliver_one):
        with pytest.raises(PermissionError):
            command()
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT state FROM v4_runtime_waits WHERE id=%s", (job["id"],)).fetchone()[0] == "pending"


def intent_wait(cycles, *, review=False, successor=False):
    from salience.cycles.governance import CycleGovernance
    from test_v4_cycle_admission import intent
    service, goal, spec, database = cycles
    with psycopg.connect(database) as connection:
        connection.execute("INSERT INTO permission_grants(workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at) VALUES(%s,'identity',%s,'cycles:case_operator','allow','{}',now()+interval '1 hour')",(service.workspace_id,str(service.subject_id)))
    waits = RuntimeWaits(database,workspace_id=service.workspace_id,subject_id=service.subject_id)
    predecessor = None
    if successor:
        predecessor = service.admit(intent(service,goal))["cycle_id"]
        service.close(predecessor,disposition="defer",reason="later fixture evidence")
    if review:
        goal = approved_goal(service,spec.model_copy(update={"review_required":True}))
    due = datetime.now(timezone.utc)+timedelta(seconds=-.1 if review else .2)
    expiry = due+timedelta(seconds=3 if review else 20)
    requested = service.request_intent(goal,slot="later",due_at=due,expires_at=expiry,predecessor_cycle_id=predecessor)
    assert service.admit(requested)["cycle_id"] is None
    job = waits.pending()[0]
    return service,database,requested,job,waits,predecessor


@pytest.mark.parametrize("successor",[False,True])
def test_intent_wake_and_successor_timer_keep_the_three_deferral_identities(cycles,successor):
    service,database,intent,job,waits,predecessor = intent_wait(cycles,successor=successor)
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM v4_cycles WHERE intent_id=%s",(intent,)).fetchone()[0] == 0
    time.sleep(1.1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: waits.fire(job["id"]),range(2)))
    assert results[0] == results[1]
    assert results[0]["state"] == "admitted"
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM v4_cycles WHERE intent_id=%s",(intent,)).fetchone()[0] == 1
        if predecessor:
            assert connection.execute("SELECT state FROM v4_cycles WHERE id=%s",(predecessor,)).fetchone()[0] == "closed"
            assert str(predecessor) != results[0]["cycle_id"]
            goal,due,expiry = connection.execute("SELECT goal_id,due_at,expires_at FROM v4_cycle_intents WHERE id=%s",(intent,)).fetchone()
    if predecessor:
        assert service.request_intent(goal,slot="later",due_at=due,expires_at=expiry,predecessor_cycle_id=predecessor) == intent


def test_review_required_intent_expiry_never_approves_or_allocates(cycles):
    _,database,intent,job,waits,_ = intent_wait(cycles,review=True)
    time.sleep(3.3)
    assert waits.fire(job["id"])["state"] == "held_review"
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT reviewed,eligibility_revision FROM v4_cycle_intents WHERE id=%s",(intent,)).fetchone() == (False,1)
        assert connection.execute("SELECT count(*) FROM v4_cycles WHERE intent_id=%s",(intent,)).fetchone()[0] == 0


@pytest.mark.parametrize("fence",["stop","revoke","revision"])
def test_due_intent_rechecks_current_authority_without_half_wake(cycles,fence):
    service,database,intent,job,waits,_ = intent_wait(cycles)
    if fence == "stop":
        with psycopg.connect(database) as connection:
            connection.execute("INSERT INTO permission_grants(workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at) VALUES(%s,'identity',%s,'cycles:stop','allow','{}',now()+interval '1 hour')",(service.workspace_id,str(service.subject_id)))
        waits.set_stop(stopped=True,expected_revision=1,idempotency_key="wait-stop",reason="timer fence")
    elif fence == "revoke":
        with psycopg.connect(database) as connection:
            connection.execute("UPDATE permission_grants SET expires_at=now()-interval '1 second' WHERE principal_id=%s AND scope='cycles:write'",(str(service.subject_id),))
    else:
        with psycopg.connect(database) as connection:
            goal = connection.execute("SELECT goal_id FROM v4_cycle_intents WHERE id=%s",(intent,)).fetchone()[0]
            revision = connection.execute("SELECT payload FROM v4_goal_revisions WHERE goal_id=%s",(goal,)).fetchone()[0]
        from salience.cycles.contracts import parse_goal
        service.revise_goal(goal,parse_goal(revision).model_copy(update={"objective":"revised before wait"}),expected_revision=1,idempotency_key="revise-wait",reason="timer fence")
    time.sleep(1.1)
    assert waits.fire(job["id"])["state"] == "held_authority_or_limit"
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT eligibility_revision FROM v4_cycle_intents WHERE id=%s",(intent,)).fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM v4_cycles WHERE intent_id=%s",(intent,)).fetchone()[0] == 0


def test_notification_failure_has_finite_retries_and_visible_owner_hold(governed,monkeypatch):
    from salience.cycles.runtime_waits import FixtureNotificationSink
    _,database,_,_,opened,waits = open_review(governed,seconds=10)
    def unavailable(*args):
        raise OSError("fixture unavailable")
    monkeypatch.setattr(FixtureNotificationSink,"accept",unavailable)
    for _ in range(3):
        assert waits.deliver_one()
        time.sleep(1.05)
    assert not waits.deliver_one()
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT state,attempts FROM v4_notification_deliveries WHERE notification_id=%s",(opened["notification_id"],)).fetchone() == ("held",3)
        assert connection.execute("SELECT count(*) FROM v4_notification_acceptances WHERE notification_id=%s",(opened["notification_id"],)).fetchone()[0] == 0


def test_wait_binding_and_receipts_cannot_be_rewritten_or_destructively_downgraded(governed):
    import subprocess,sys
    _,database,_,_,opened,waits = open_review(governed)
    job = waits.pending()[0]
    with psycopg.connect(database) as connection:
        for sql in ("UPDATE v4_runtime_waits SET due_at=due_at+interval '1 day' WHERE id=%s","DELETE FROM v4_runtime_waits WHERE id=%s"):
            with pytest.raises(psycopg.Error,match="preserv"),connection.transaction():
                connection.execute(sql,(job["id"],))
    with psycopg.connect(database) as connection:
        version = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    rollback = subprocess.run([sys.executable,"-m","alembic","-x","database_url="+database,"downgrade","0030_v4_schedule_cutover"],capture_output=True,text=True)
    assert rollback.returncode != 0 and any(message in rollback.stderr for message in ("preserve durable waits","preserve runtime hold owner binding","preserve canonical delivery scope","preserve parallel agent execution history","preserve legacy dispatch identity"))
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == version
        assert connection.execute("SELECT state FROM v4_runtime_waits WHERE id=%s",(job["id"],)).fetchone()[0] == "pending"
    time.sleep(.25)
    assert waits.fire(job["id"])["state"] == "suspended"


def test_delivery_failure_and_case_wait_are_visible_through_existing_scoped_inspection(governed):
    from salience.api.routes.v4_cycles import _inspect_case
    service,database,_,_,opened,waits = open_review(governed,seconds=5)
    with psycopg.connect(database) as connection:
        connection.execute("INSERT INTO permission_grants(workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at) VALUES(%s,'identity',%s,'cycles:read','allow','{}',now()+interval '1 hour')",(service.workspace_id,str(service.subject_id)))
    assert waits.deliver_one()
    inspected = _inspect_case(service,opened["case_id"])
    assert inspected["owner_id"] == str(service.subject_id)
    assert inspected["runtime_waits"][0]["state"] == "pending"
    assert inspected["notifications"][0]["delivery_state"] == "accepted"
    assert inspected["notifications"][0]["acknowledged"] is False


@pytest.mark.asyncio
async def test_failed_runtime_handoffs_are_bounded_and_do_not_allocate(cycles,monkeypatch):
    from salience.cycles.runtime_waits import FixtureWaitDriver
    _,database,intent,job,waits,_ = intent_wait(cycles)
    monkeypatch.setenv("SALIENCE_DEPLOYMENT_MODE","fixture")
    class Unavailable:
        async def start_workflow(self,*args,**kwargs):
            raise OSError("fixture runtime unavailable")
    driver = FixtureWaitDriver(Unavailable(),waits,task_queue="salience-v4-local-unavailable")
    for _ in range(3):
        assert await driver.dispatch_one()
        time.sleep(1.05)
    assert not await driver.dispatch_one()
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT state,handoff_attempts,result->>'owner_id' FROM v4_runtime_waits WHERE id=%s",(job["id"],)).fetchone() == ("held",3,str(waits.subject_id))
        assert connection.execute("SELECT count(*) FROM v4_cycles WHERE intent_id=%s",(intent,)).fetchone()[0] == 0


def test_approved_but_unresumed_case_keeps_original_deadline_and_cannot_bypass_hold(governed):
    from test_v4_cycle_governance import permit_request
    service,database,_,cycle,opened,waits = open_review(governed,seconds=.8)
    first_job = waits.pending()[0]
    service.respond_review(opened["case_id"],review_response(database,opened),idempotency_key="approve-no-resume")
    assert waits.fire(first_job["id"])["state"] == "obsolete"
    next_job = waits.pending()[0]
    assert next_job["revision"] == 2 and next_job["due_at"] == first_job["due_at"]
    time.sleep(.85)
    assert waits.fire(next_job["id"])["state"] == "suspended"
    # A generic same-cycle recovery cannot supply the case-specific approval.
    service.recover(cycle,state="runnable")
    with pytest.raises(PermissionError,match="active recovery case"):
        service.issue_permit(cycle,permit_request(database,cycle),idempotency_key="cannot-bypass")


def test_expiry_preserves_unknown_liability_and_closure_releases_only_unused_parent(accounting):
    from test_v4_cycle_accounting import admitted as allocate, transfer, command, committed
    from salience.cycles.governance import CycleGovernance
    service,_,spec,database = accounting
    cycle = allocate(accounting)
    from uuid import uuid4
    operation = uuid4()
    transfer(service,cycle,operation)
    command(service,cycle,operation,"dispatch","sent-before-timeout")
    command(service,cycle,operation,"unknown","uncertain-before-timeout")
    with psycopg.connect(database) as connection:
        connection.execute("INSERT INTO permission_grants(workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at) VALUES(%s,'identity',%s,'cycles:case_operator','allow','{}',now()+interval '1 hour')",(service.workspace_id,str(service.subject_id)))
    governance = CycleGovernance(database,workspace_id=service.workspace_id,subject_id=service.subject_id)
    governance.open_case(cycle,target="cycle",kind="reconciliation",failure_class="unknown_effect",owner_id=service.subject_id,deadline=datetime.now(timezone.utc)+timedelta(seconds=.2),reason="retain unresolved liability",artifact_sha256="d"*64,account_ref="fixture-account")
    waits = RuntimeWaits(database,workspace_id=service.workspace_id,subject_id=service.subject_id)
    job = waits.pending()[0]
    time.sleep(.25)
    assert waits.fire(job["id"])["state"] == "suspended"
    assert all(committed(database,budget)==80 for budget in spec.allocation.budget_ids)
    service.close(cycle,disposition="cancelled",reason="owner ends uncertain fixture")
    assert all(committed(database,budget)==50 for budget in spec.allocation.budget_ids)


def test_expired_notification_owner_is_visible_hold_and_never_a_new_recipient(governed):
    from uuid import uuid4
    service,_,_,database = governed
    _,cycle = admitted(governed)
    owner = uuid4()
    with psycopg.connect(database) as connection:
        connection.execute("INSERT INTO identity_subjects(id,workspace_id,issuer,subject,expires_at) VALUES(%s,%s,'https://fixture.invalid',%s,now()+interval '1 hour')",(owner,service.workspace_id,str(owner)))
    opened = service.open_case(cycle,target="cycle",kind="manual_review",failure_class="manual_review",owner_id=owner,deadline=datetime.now(timezone.utc)+timedelta(seconds=5),reason="owner expires",artifact_sha256="a"*64,account_ref="fixture-account")
    waits = RuntimeWaits(database,workspace_id=service.workspace_id,subject_id=service.subject_id)
    with psycopg.connect(database) as connection:
        connection.execute("UPDATE identity_subjects SET enabled=false WHERE id=%s",(owner,))
    assert waits.deliver_one()
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT state,reason FROM v4_notification_deliveries WHERE notification_id=%s",(opened["notification_id"],)).fetchone() == ("held","owner_unavailable")
        assert connection.execute("SELECT count(*) FROM v4_notification_acceptances WHERE notification_id=%s",(opened["notification_id"],)).fetchone()[0] == 0


def test_capacity_waits_exhaust_original_wake_budget_without_allocating(cycles):
    from test_v4_cycle_admission import intent as request_intent
    service,database,intent,_,waits,_ = intent_wait(cycles)
    with psycopg.connect(database) as connection:
        goal = connection.execute("SELECT goal_id FROM v4_cycle_intents WHERE id=%s",(intent,)).fetchone()[0]
    active = service.admit(request_intent(service,goal,slot="occupy-capacity"))["cycle_id"]
    assert active
    for step in range(4):
        job = waits.pending()[0]
        time.sleep(1.1)
        result = waits.fire(job["id"])
        assert result["state"] == ("deferred" if step<3 else "held_authority_or_limit")
    assert waits.pending() == []
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT eligibility_revision FROM v4_cycle_intents WHERE id=%s",(intent,)).fetchone()[0] == 4
        assert connection.execute("SELECT count(*) FROM v4_cycles WHERE intent_id=%s",(intent,)).fetchone()[0] == 0


def test_direct_sql_cannot_forge_runtime_identity_or_completed_admission_receipt(cycles):
    _,database,_,job,_,_ = intent_wait(cycles)
    from psycopg.types.json import Jsonb
    with psycopg.connect(database) as connection:
        for sql,params in [
            ("UPDATE v4_runtime_waits SET runtime_id='another-runtime' WHERE id=%s",(job["id"],)),
            ("UPDATE v4_runtime_waits SET state='completed',result=%s WHERE id=%s",(Jsonb({"state":"admitted","cycle_id":"invented"}),job["id"])),
        ]:
            with pytest.raises(psycopg.Error,match="runtime"),connection.transaction():
                connection.execute(sql,params)


def test_separate_operator_records_expiry_actor_without_inheriting_revoked_owner_authority(cycles):
    from uuid import uuid4
    service,_,spec,database = cycles
    goal = approved_goal(service,spec.model_copy(update={"review_required":True}))
    now = datetime.now(timezone.utc)
    intent = service.request_intent(goal,slot="operator-expiry",due_at=now-timedelta(seconds=1),expires_at=now+timedelta(seconds=.5))
    assert service.admit(intent)["disposition"] == "review_required"
    operator = uuid4()
    with psycopg.connect(database) as connection:
        connection.execute("INSERT INTO identity_subjects(id,workspace_id,issuer,subject,expires_at) VALUES(%s,%s,'https://fixture.invalid',%s,now()+interval '1 hour')",(operator,service.workspace_id,str(operator)))
        connection.execute("INSERT INTO permission_grants(workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at) VALUES(%s,'identity',%s,'cycles:case_operator','allow','{}',now()+interval '1 hour')",(service.workspace_id,str(operator)))
        connection.execute("UPDATE identity_subjects SET enabled=false WHERE id=%s",(service.subject_id,))
    waits = RuntimeWaits(database,workspace_id=service.workspace_id,subject_id=operator)
    job = waits.pending()[0]
    time.sleep(.55)
    assert waits.fire(job["id"])["state"] == "held_review"
    with psycopg.connect(database) as connection:
        actor,owner = connection.execute("SELECT subject_id,payload->>'owner_id' FROM v4_cycle_events WHERE intent_id=%s AND kind='intent_wait_held'",(intent,)).fetchone()
        assert actor == operator
        assert owner == str(service.subject_id)
        assert connection.execute("SELECT count(*) FROM v4_cycles WHERE intent_id=%s",(intent,)).fetchone()[0] == 0


@pytest.mark.parametrize("later_start", [False, True])
def test_runtime_hold_owner_must_match_the_original_start_subject(governed, later_start):
    """A same-workspace worker cannot reassign an admitted cycle's hold."""
    from uuid import uuid4
    service, _, _, database = governed
    _, cycle = admitted(governed)
    other_owner = uuid4()
    with psycopg.connect(database) as connection:
        connection.execute("INSERT INTO identity_subjects(id,workspace_id,issuer,subject,expires_at) VALUES(%s,%s,'https://fixture.invalid',%s,now()+interval '1 hour')",(other_owner,service.workspace_id,str(other_owner)))
        if later_start:
            # A later malformed start must not redefine the original owner.
            connection.execute("""INSERT INTO v4_cycle_outbox
                (id,workspace_id,subject_id,goal_id,intent_id,cycle_id,sequence,kind,payload,traceparent)
                SELECT %s,workspace_id,%s,goal_id,intent_id,cycle_id,2,'start',payload,traceparent
                FROM v4_cycle_outbox WHERE cycle_id=%s AND sequence=1""",(uuid4(),other_owner,cycle))
        context, operation = connection.execute("SELECT context_id,operation_id FROM v4_cycles WHERE id=%s",(cycle,)).fetchone()
        statement = """INSERT INTO v4_runtime_holds(cycle_id,context_id,operation_id,workspace_id,owner_id,reason)
            VALUES(%s,%s,%s,%s,%s,'held_timeout')"""
        with pytest.raises(psycopg.Error,match="runtime hold owner binding"),connection.transaction():
            connection.execute(statement,(cycle,context,operation,service.workspace_id,other_owner))
        assert connection.execute("SELECT count(*) FROM v4_runtime_holds WHERE cycle_id=%s",(cycle,)).fetchone()[0] == 0
        connection.execute(statement,(cycle,context,operation,service.workspace_id,service.subject_id))
        assert connection.execute("SELECT owner_id FROM v4_runtime_holds WHERE cycle_id=%s",(cycle,)).fetchone()[0] == service.subject_id


def test_populated_owner_binding_cannot_be_rolled_back(governed):
    import subprocess
    import sys
    service, _, _, database = governed
    _, cycle = admitted(governed)
    with psycopg.connect(database) as connection:
        context, operation = connection.execute("SELECT context_id,operation_id FROM v4_cycles WHERE id=%s",(cycle,)).fetchone()
        connection.execute("""INSERT INTO v4_runtime_holds(cycle_id,context_id,operation_id,workspace_id,owner_id,reason)
            VALUES(%s,%s,%s,%s,%s,'held_timeout')""",(cycle,context,operation,service.workspace_id,service.subject_id))
        version = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    rollback = subprocess.run([sys.executable,"-m","alembic","-x","database_url="+database,"downgrade","0031_runtime_waits"],capture_output=True,text=True)
    assert rollback.returncode != 0 and any(message in rollback.stderr for message in ("preserve runtime hold owner binding","preserve canonical delivery scope","preserve parallel agent execution history","preserve legacy dispatch identity"))
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == version
        assert connection.execute("SELECT owner_id FROM v4_runtime_holds WHERE cycle_id=%s",(cycle,)).fetchone()[0] == service.subject_id
        assert connection.execute("SELECT count(*) FROM pg_trigger WHERE tgrelid='v4_runtime_holds'::regclass AND tgname='owner_binding'").fetchone()[0] == 1
