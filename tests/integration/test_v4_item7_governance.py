"""Independent item-7 regressions for accepted versions and boundary races."""
from datetime import datetime, timedelta, timezone
import time
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from salience.cycles.contracts import AllocationPolicy, CadencePolicy, GoalSpecV3
from salience.cycles.governance import CycleGovernance
from salience.cycles.schedule_cutover import FixtureSchedulePoller
from test_v4_cycle_admission import approved_goal, cycles
from test_v4_cadence_policy import policy
from test_v4_cycle_governance import review_response
from test_v4_schedule_cutover import cutover


def _wait_until(value):
    time.sleep(max(0, (value-datetime.now(timezone.utc)).total_seconds())+.05)


def _prepare(scheduler, goal, spec, legacy, revision=1):
    return scheduler.prepare(goal, legacy_schedule_id=legacy, expected_revision=revision,
                             first_v4_slot=spec.cadence.anchor, idempotency_key='prepare')


@pytest.mark.parametrize('action', ['poll', 'rollback'])
def test_accepted_v3_schedule_can_auto_poll_and_rollback(cutover, monkeypatch, action):
    scheduler, service, goal, spec, legacy, database = cutover
    start=datetime.now(timezone.utc)-timedelta(hours=1)
    end=start+timedelta(days=1)
    budget=uuid4()
    with psycopg.connect(database) as connection:
        connection.execute("INSERT INTO budgets(id,workspace_id,content_program_id,name,scope,currency,limit_amount,period_start,period_end) VALUES(%s,%s,%s,%s,'program','USD',0.000100,%s,%s)",
                           (budget,service.workspace_id,spec.content_program_id,str(budget),start,end))
    v3=GoalSpecV3.model_validate(spec.model_dump() | {'schema_version':'GoalSpec.local.v3','allocation':AllocationPolicy(budget_ids=(budget,),currency='USD',period_start=start,period_end=end,ceiling_micros=80)})
    service.revise_goal(goal,v3,expected_revision=1,idempotency_key='v3',reason='explicit V3 fixture')
    service.approve_baseline(goal,expected_revision=2,expires_at=v3.horizon_end,reason='approved V3 fixture')
    _prepare(scheduler,goal,v3,legacy,revision=2)
    scheduler.activate(goal,idempotency_key='activate')
    if action=='rollback':
        assert scheduler.rollback(goal,idempotency_key='rollback')['state']=='rolled_back'
    else:
        _wait_until(v3.cadence.anchor)
        monkeypatch.setenv('SALIENCE_DEPLOYMENT_MODE','fixture')
        monkeypatch.setenv('SALIENCE_EFFECTS_ENABLED','false')
        assert FixtureSchedulePoller(database,workspace_id=service.workspace_id).poll_once()['admitted']==1


def _new_schedule(scheduler,service,spec,database):
    goal=approved_goal(service,spec)
    legacy=uuid4()
    with psycopg.connect(database) as connection:
        connection.execute("INSERT INTO job_schedules(id,workspace_id,content_program_id,name,schedule_expression,timezone,job_type,payload,next_run_at) VALUES(%s,%s,%s,%s,%s,'UTC','v4_fixture_legacy',%s,%s)",
            (legacy,service.workspace_id,spec.content_program_id,str(legacy),f'every:{spec.cadence.interval_seconds}s',Jsonb({'dry_run':True,'last_slot':(spec.cadence.anchor-timedelta(seconds=spec.cadence.interval_seconds)).isoformat()}),spec.cadence.anchor))
    _prepare(scheduler,goal,spec,legacy)
    scheduler.activate(goal,idempotency_key='activate')
    return goal


@pytest.mark.parametrize('first_kind',['idle','stopped','revoked'])
def test_bounded_poller_eventually_considers_goal_after_idle_or_held_window(cutover,monkeypatch,first_kind):
    scheduler,service,_,original,_,database=cutover
    anchor=datetime.now(timezone.utc).replace(microsecond=0)+timedelta(seconds=2)
    spec=original.model_copy(update={'cadence':CadencePolicy(anchor=anchor,interval_seconds=60)})
    first=_new_schedule(scheduler,service,spec,database)
    # A second cutover must use a separately authorized actor for revocation isolation.
    second_service=service
    second_scheduler=scheduler
    if first_kind=='revoked':
        subject=uuid4()
        with psycopg.connect(database) as connection:
            connection.execute("INSERT INTO identity_subjects(id,workspace_id,issuer,subject,expires_at) VALUES(%s,%s,'https://fixture.invalid',%s,now()+interval '1 hour')",(subject,service.workspace_id,str(subject)))
            for scope in ('goals:write','goals:approve','cycles:write','cycles:schedule'):
                connection.execute("INSERT INTO permission_grants(workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at) VALUES(%s,'identity',%s,%s,'allow','{}',now()+interval '1 hour')",(service.workspace_id,str(subject),scope))
        from salience.cycles.admission import CycleAdmission
        from salience.cycles.schedule_cutover import CycleScheduleCutover
        second_service=CycleAdmission(database,workspace_id=service.workspace_id,subject_id=subject)
        second_scheduler=CycleScheduleCutover(database,workspace_id=service.workspace_id,subject_id=subject)
    second=_new_schedule(second_scheduler,second_service,spec,database)
    _wait_until(anchor)
    if first_kind=='idle':
        scheduler.poll(first,expected_revision=1,idempotency_key='make-idle')
    elif first_kind=='stopped':
        with psycopg.connect(database) as connection:
            connection.execute("INSERT INTO permission_grants(workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at) VALUES(%s,'identity',%s,'cycles:stop','allow','{}',now()+interval '1 hour')",(service.workspace_id,str(service.subject_id)))
        CycleGovernance(database,workspace_id=service.workspace_id,subject_id=service.subject_id).set_stop(goal_id=first,stopped=True,expected_revision=1,idempotency_key='stop',reason='fixture')
    else:
        with psycopg.connect(database) as connection:
            connection.execute("UPDATE permission_grants SET effect='deny' WHERE principal_id=%s AND scope='cycles:schedule'",(str(service.subject_id),))
    monkeypatch.setenv('SALIENCE_DEPLOYMENT_MODE','fixture')
    monkeypatch.setenv('SALIENCE_EFFECTS_ENABLED','false')
    poller=FixtureSchedulePoller(database,workspace_id=service.workspace_id,batch_size=1)
    for _ in range(3):
        poller.poll_once()
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM v4_cycles c JOIN v4_cycle_intents i ON i.id=c.intent_id WHERE i.goal_id=%s",(second,)).fetchone()[0]==1


@pytest.mark.parametrize('scope',['workspace','goal'])
def test_stop_committed_at_legacy_resume_boundary_keeps_schedule_paused(cutover,monkeypatch,scope):
    scheduler,service,goal,spec,legacy,database=cutover
    with psycopg.connect(database) as connection:
        connection.execute("INSERT INTO permission_grants(workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at) VALUES(%s,'identity',%s,'cycles:stop','allow','{}',now()+interval '1 hour')",(service.workspace_id,str(service.subject_id)))
    _prepare(scheduler,goal,spec,legacy)
    scheduler.activate(goal,idempotency_key='activate')
    original=scheduler.legacy_control.resume_after
    def stop_then_resume(schedule_id,after_slot,**kwargs):
        CycleGovernance(database,workspace_id=service.workspace_id,subject_id=service.subject_id).set_stop(goal_id=goal if scope=='goal' else None,stopped=True,expected_revision=1,idempotency_key='stop',reason='fixture boundary stop')
        return original(schedule_id,after_slot,**kwargs)
    monkeypatch.setattr(scheduler.legacy_control,'resume_after',stop_then_resume)
    with pytest.raises(PermissionError,match='stop'):
        scheduler.rollback(goal,idempotency_key='rollback')
    assert scheduler.legacy_control.describe(legacy)['paused'] is True


def test_archive_replay_keeps_original_expired_disposition_after_late_denial(cycles):
    service,_,original,database=cycles
    service=CycleGovernance(database,workspace_id=service.workspace_id,subject_id=service.subject_id)
    goal=approved_goal(service,original.model_copy(update={'review_required':True}))
    expiry=datetime.now(timezone.utc)+timedelta(seconds=1)
    target=service.request_intent(goal,slot='archive-boundary',due_at=datetime.now(timezone.utc)-timedelta(seconds=1),expires_at=expiry)
    assert service.admit(target)['disposition']=='review_required'
    opened=service.open_case(target,target='intent',kind='admission_review',failure_class='admission_review',owner_id=service.subject_id,deadline=expiry+timedelta(minutes=1),reason='review',artifact_sha256='a'*64,account_ref='fixture-account')
    service.respond_review(opened['case_id'],review_response(database,opened),idempotency_key='approve')
    service.resume_case(opened['case_id'],expected_revision=2,idempotency_key='resume')
    _wait_until(expiry)
    service.terminalize_case(opened['case_id'],expected_revision=3,idempotency_key='terminal')
    args=dict(reason='expired fixture',evidence={'fixture':True},retain_until=expiry+timedelta(days=1))
    archived=service.archive_case(opened['case_id'],**args)
    assert archived['disposition']=='expired'
    assert service.admit(target)['disposition']=='denied'
    assert service.archive_case(opened['case_id'],**args)==archived


@pytest.mark.parametrize('fence',['write_grant','program','goal_state','interval'])
def test_current_write_and_program_revocation_at_resume_boundary_keeps_legacy_paused(cutover,monkeypatch,fence):
    scheduler,service,goal,spec,legacy,database=cutover
    _prepare(scheduler,goal,spec,legacy)
    scheduler.activate(goal,idempotency_key='activate')
    original=scheduler.legacy_control.resume_after
    def revoke_then_resume(schedule_id,after_slot,**kwargs):
        with psycopg.connect(database) as connection:
            if fence=='write_grant':
                connection.execute("UPDATE permission_grants SET effect='deny' WHERE principal_id=%s AND scope='cycles:write'",(str(service.subject_id),))
            elif fence=='program':
                connection.execute("UPDATE content_programs SET status='paused' WHERE id=%s",(spec.content_program_id,))
        if fence=='goal_state':
            service.set_goal_state(goal,'paused',expected_revision=1,expected_state_revision=1,idempotency_key='pause',reason='boundary pause')
        elif fence=='interval':
            changed=spec.model_copy(update={'cadence':spec.cadence.model_copy(update={'interval_seconds':2})})
            service.revise_goal(goal,changed,expected_revision=1,idempotency_key='interval',reason='boundary interval')
        return original(schedule_id,after_slot,**kwargs)
    monkeypatch.setattr(scheduler.legacy_control,'resume_after',revoke_then_resume)
    with pytest.raises(PermissionError):
        scheduler.rollback(goal,idempotency_key='rollback')
    assert scheduler.legacy_control.describe(legacy)['paused'] is True


@pytest.mark.parametrize('scope',['cycles:write','cycles:schedule'])
def test_grant_expiry_during_actual_resume_transaction_rolls_back_legacy_write(cutover,monkeypatch,scope):
    scheduler,service,goal,spec,legacy,database=cutover
    _prepare(scheduler,goal,spec,legacy)
    scheduler.activate(goal,idempotency_key='activate')
    original_resume=scheduler.legacy_control.resume_after
    original_schedule=scheduler.legacy_control._schedule
    def delayed_schedule(connection,schedule_id,*,lock=False):
        if lock:
            time.sleep(1.1)
        return original_schedule(connection,schedule_id,lock=lock)
    def expire_then_resume(schedule_id,after_slot,**kwargs):
        with psycopg.connect(database) as connection:
            connection.execute("UPDATE permission_grants SET expires_at=clock_timestamp()+interval '1 second' WHERE principal_id=%s AND scope=%s",(str(service.subject_id),scope))
        return original_resume(schedule_id,after_slot,**kwargs)
    monkeypatch.setattr(scheduler.legacy_control,'_schedule',delayed_schedule)
    monkeypatch.setattr(scheduler.legacy_control,'resume_after',expire_then_resume)
    with pytest.raises(PermissionError):
        scheduler.rollback(goal,idempotency_key='rollback')
    assert scheduler.legacy_control.describe(legacy)['paused'] is True


def test_goal_state_downgrade_serializes_noop_receipt_writer_before_history_check(cycles,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import importlib
    service,goal,_,database=cycles
    command=dict(expected_revision=1,expected_state_revision=1,reason='no-op preservation fixture')
    service.set_goal_state(goal,'active',idempotency_key='before-downgrade',**command)
    migration=importlib.import_module('migrations.versions.0033_goal_state_commands')
    with ThreadPoolExecutor(max_workers=1) as pool, psycopg.connect(database) as connection:
        future=None
        def execute(sql):
            nonlocal future
            if 'DO $$ BEGIN' in sql:
                future=pool.submit(service.set_goal_state,goal,'active',idempotency_key='during-downgrade',**command)
                deadline=time.monotonic()+1.5
                blocked=False
                while time.monotonic()<deadline:
                    connection.execute('SELECT pg_stat_clear_snapshot()')
                    blocked=connection.execute("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock' AND query LIKE '%%INSERT INTO v4_goal_state_commands%%')").fetchone()[0]
                    if blocked or future.done():
                        break
                    time.sleep(.025)
                assert blocked and not future.done(), 'no-op receipt writer must wait behind actual migration lock'
            return connection.execute(sql)
        monkeypatch.setattr(migration.op,'execute',execute)
        try:
            with pytest.raises(psycopg.Error,match='preserve exact goal state command history'):
                migration.downgrade()
        finally:
            connection.rollback()
        assert future.result(timeout=2)['state']=='active'
        with psycopg.connect(database) as read:
            assert read.execute("SELECT count(*) FROM v4_goal_state_commands WHERE goal_id=%s",(goal,)).fetchone()[0]==2
            assert read.execute("SELECT version_num FROM alembic_version").fetchone()[0]=='0034_runtime_delivery_scope'
