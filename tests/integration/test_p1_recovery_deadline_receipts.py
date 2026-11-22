from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from time import monotonic, sleep
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from salience.cycles.admission import CycleAdmission
from salience.cycles import production_recovery as recovery
from test_p1_production_recovery import (
    _RecoveryAuthority, _case_work, _enroll_production_intent,
    _production_recovery_operation, _recovery_policy,
)
from test_p1_production_schedule_fencing import _production_enrollment_fixture
from test_v4_migrations import _migration


def test_historical_deadline_event_without_a_case_command_cannot_replay():
    with _production_recovery_operation("deadline_case_history_") as (
        database, _, authority, _,
    ):
        repository = recovery.ProductionRecoveryWorkRepository(database)
        work = _case_work(
            repository, authority, recovery,
            deadline=datetime.now(timezone.utc) - timedelta(seconds=5),
        )
        key = (
            f"deadline-wake:cycle_case:{work.case_id}:"
            f"{work.source_revision}:{work.deadline.isoformat()}:deadline_suspend"
        )
        policy_decision_id = uuid4()
        with repository._connect() as connection:
            repository._append_event(
                connection, work, event_key=key, event_kind="escalation",
                policy_decision_id=policy_decision_id, payload={
                    "schema_version": "salience.p1-production-recovery-deadline.v1",
                    "status": "deadline_suspend", "result_class": "suspended",
                    "attempt": 2, "command_id": str(uuid4()),
                    "case_id": str(work.case_id), "work_id": str(work.work_id),
                    "source_id": str(work.source_id), "intent_id": str(work.intent_id),
                    "operation_id": str(work.operation_id), "deadline": work.deadline.isoformat(),
                    "event_key": key, "policy_decision_id": str(policy_decision_id),
                    "route_decision_id": str(uuid4()),
                },
            )
        controller = recovery.ProductionRecoveryCaseController(
            repository, authority=_RecoveryAuthority(recovery), commands=object(),
        )
        with pytest.raises(ValueError, match="canonical case suspension"):
            controller.wake_deadline(work.work_id)


@pytest.mark.parametrize("disposition", ["woken", "admitted"])
def test_intent_deadline_callback_cannot_invent_a_wake_or_admission(disposition):
    with _production_enrollment_fixture("deadline_intent_proof_") as (
        database, control, binding, key, _, _,
    ):
        upgraded = _migration(database, "upgrade", "0049_p1_production_recovery_decision_evidence")
        assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
        _enroll_production_intent(control, binding, key)
        repository = recovery.ProductionRecoveryWorkRepository(database)
        work = repository.bind_pre_admission_intent(
            key.intent_id, policy=_recovery_policy(recovery),
        )

        class Commands:
            def wake_intent(self, connection, command):
                return recovery.TypedIntentWakeReceipt(
                    intent_id=work.intent_id, eligibility_revision=work.source_revision,
                    disposition=disposition, cycle_id=uuid4() if disposition == "admitted" else None,
                )

        controller = recovery.ProductionRecoveryCaseController(
            repository, authority=_RecoveryAuthority(recovery), commands=Commands(),
        )
        with pytest.raises(ValueError, match="canonical intent wake"):
            controller.wake_deadline(work.work_id)
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT eligibility_revision FROM v4_cycle_intents WHERE id=%s", (work.intent_id,),
            ).fetchone() == (work.source_revision,)
            assert connection.execute(
                "SELECT count(*) FROM v4_cycles WHERE intent_id=%s", (work.intent_id,),
            ).fetchone() == (0,)
            assert connection.execute(
                """SELECT count(*) FROM v4_production_recovery_events
                   WHERE work_id=%s AND event_kind='escalation'""", (work.work_id,),
            ).fetchone() == (0,)


def test_intent_deadline_wake_waits_for_goal_before_locking_intent():
    with _production_enrollment_fixture("deadline_intent_lock_order_") as (
        database, control, binding, key, authority, _,
    ):
        upgraded = _migration(database, "upgrade", "0049_p1_production_recovery_decision_evidence")
        assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
        _enroll_production_intent(control, binding, key)
        application_name = "salience-deadline-lock-order"
        repository = recovery.ProductionRecoveryWorkRepository(
            make_conninfo(database, application_name=application_name)
        )
        work = repository.bind_pre_admission_intent(key.intent_id, policy=_recovery_policy(recovery))
        admission = CycleAdmission(
            database, workspace_id=binding.workspace_id, subject_id=authority["subject_id"],
        )

        class Commands:
            def wake_intent(self, connection, command):
                admission._wake(
                    connection, command.intent_id,
                    expected_revision=command.expected_revision, review=True,
                )
                admission._event(
                    connection, work.goal_id, "production_recovery_intent_wake_command",
                    {"idempotency_key": command.idempotency_key,
                     "expected_revision": command.expected_revision, "disposition": "woken"},
                    intent_id=command.intent_id,
                )
                return recovery.TypedIntentWakeReceipt(
                    command.intent_id, command.expected_revision, "woken", None,
                )

        controller = recovery.ProductionRecoveryCaseController(
            repository, authority=_RecoveryAuthority(recovery), commands=Commands(),
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            with (
                psycopg.connect(database) as goal_holder,
                psycopg.connect(database, autocommit=True) as observer,
            ):
                goal_holder.execute("SELECT id FROM v4_goals WHERE id=%s FOR UPDATE", (work.goal_id,))
                wake = pool.submit(controller.wake_deadline, work.work_id)
                deadline = monotonic() + 2
                while not observer.execute(
                    """SELECT 1 FROM pg_stat_activity
                       WHERE application_name=%s AND %s=ANY(pg_blocking_pids(pid))""",
                    (application_name, goal_holder.info.backend_pid),
                ).fetchone():
                    assert monotonic() < deadline, "deadline wake did not wait for the canonical goal lock"
                    sleep(0.01)

                # An admission holding the goal must still be able to lock its intent.
                assert goal_holder.execute(
                    "SELECT id FROM v4_cycle_intents WHERE id=%s FOR UPDATE NOWAIT", (work.intent_id,),
                ).fetchone() == (work.intent_id,)

            assert wake.result(timeout=5).command_result.disposition == "woken"
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT eligibility_revision FROM v4_cycle_intents WHERE id=%s", (work.intent_id,),
            ).fetchone() == (work.source_revision + 1,)
            assert connection.execute(
                """SELECT count(*) FROM v4_production_recovery_events
                   WHERE work_id=%s AND event_kind='escalation'""", (work.work_id,),
            ).fetchone() == (1,)


def test_existing_work_can_rebind_while_deadline_owns_work_lock():
    with _production_enrollment_fixture("deadline_rebind_lock_order_") as (
        database, control, binding, key, _, _,
    ):
        _enroll_production_intent(control, binding, key)
        repository = recovery.ProductionRecoveryWorkRepository(
            make_conninfo(database, options="-c lock_timeout=1000")
        )
        policy = _recovery_policy(recovery)
        work = repository.bind_pre_admission_intent(key.intent_id, policy=policy)

        with psycopg.connect(database) as deadline:
            deadline.execute(
                "SELECT work_id FROM v4_production_recovery_work WHERE work_id=%s FOR UPDATE",
                (work.work_id,),
            )
            assert repository.bind_pre_admission_intent(key.intent_id, policy=policy) == work
            assert deadline.execute(
                "SELECT id FROM v4_cycle_intents WHERE id=%s FOR UPDATE NOWAIT",
                (key.intent_id,),
            ).fetchone() == (key.intent_id,)
