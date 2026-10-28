"""Task 6 enrollment-to-recovery handoff against the immutable 0047 schema."""

from importlib import import_module
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import os
import subprocess
import sys
import time
from threading import Event, Lock
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
import pytest


def test_task6_enrollment_is_the_exact_pre_admission_recovery_handoff():
    schedule_tests = import_module("test_p1_production_schedule_fencing")
    migration_tests = import_module("test_v4_migrations")
    module = import_module("salience.cycles.production_schedule_control")

    with schedule_tests._production_enrollment_fixture("task6_recovery_handoff_") as (
        database,
        control,
        binding,
        key,
        _authority,
        _route_authority,
    ):
        writer = module.ProductionIntentEnrollmentWriter(control, binding)
        result = writer.enroll_and_admit(key)
        assert result["admission"]["disposition"] == "review_required"

        recovery_owner = {
            "workspace_id": binding.workspace_id,
            "subject_id": binding.source_owner_subject_id,
            "cycle": {"goal_id": binding.goal_id, "intent_id": key.intent_id},
        }
        with psycopg.connect(database) as connection:
            intent = connection.execute(
                """SELECT due_at,expires_at,fingerprint,eligibility_revision
                   FROM v4_cycle_intents WHERE id=%s""",
                (key.intent_id,),
            ).fetchone()
            enrollment = connection.execute(
                """SELECT intent_id,eligibility_revision,workspace_id,goal_id,
                          source_owner_id,route_fence_decision_id
                   FROM v4_production_intent_enrollments
                   WHERE intent_id=%s AND eligibility_revision=%s""",
                (key.intent_id, key.eligibility_revision),
            ).fetchone()
            admission = connection.execute(
                """SELECT disposition,cycle_id FROM v4_admissions
                   WHERE intent_id=%s AND eligibility_revision=%s""",
                (key.intent_id, key.eligibility_revision),
            ).fetchone()
            assert enrollment is not None
            assert admission == ("review_required", None)
            assert enrollment[4] == binding.source_owner_subject_id

            handoff = migration_tests._insert_0047_recovery_work(
                connection,
                recovery_owner,
                source_discriminant="pre_admission_intent_case",
                source_id=key.intent_id,
                source_revision=key.eligibility_revision,
                eligibility_revision=key.eligibility_revision,
                intent_id=key.intent_id,
                reason="admission_review_required",
                state="unadmitted",
                deadline=intent[1],
                due_at=intent[0],
                expires_at=intent[1],
                request_fingerprint=intent[2],
            )
            assert handoff["source_id"] == enrollment[0] == key.intent_id
            assert handoff["source_revision"] == enrollment[1]
            assert handoff["state"] == "unadmitted"
            assert handoff["owner_id"] == enrollment[4]
            assert handoff["source_fingerprint"]
            assert handoff["cycle_id"] is None
            assert handoff["operation_id"] is None
            assert handoff["snapshot"] is None

            joined = connection.execute(
                """SELECT work.source_id,work.source_revision,
                          enrollment.route_fence_decision_id,
                          admission.disposition
                   FROM v4_production_recovery_work work
                   JOIN v4_production_intent_enrollments enrollment
                     ON enrollment.intent_id=work.intent_id
                    AND enrollment.eligibility_revision=work.eligibility_revision
                    AND enrollment.workspace_id=work.workspace_id
                    AND enrollment.goal_id=work.goal_id
                   JOIN v4_admissions admission
                     ON admission.intent_id=work.intent_id
                    AND admission.eligibility_revision=work.eligibility_revision
                   WHERE work.work_id=%s""",
                (handoff["work_id"],),
            ).fetchone()
            assert joined == (
                key.intent_id,
                key.eligibility_revision,
                enrollment[5],
                "review_required",
            )


def _recovery_policy(module):
    return module.ProductionRecoveryPolicyBinding(
        policy_decision_id=uuid4(),
        recipient_profile_id=uuid4(),
        recipient_profile_revision=7,
        recipient_profile_sha256="1" * 64,
        retry_policy_id=uuid4(),
        retry_policy_revision=4,
        retry_policy_sha256="2" * 64,
    )


def _enroll_production_intent(control, binding, key):
    schedule_control = import_module("salience.cycles.production_schedule_control")
    return schedule_control.ProductionIntentEnrollmentWriter(
        control, binding
    ).enroll_and_admit(key)


@contextmanager
def _production_recovery_operation(prefix="task7_recovery_"):
    migration_tests = import_module("test_v4_migrations")
    source = os.environ["TEST_DATABASE_URL"]
    name, database, _before, fixture, authority = (
        migration_tests._prepare_0047_0046_database(prefix)
    )
    try:
        upgraded = migration_tests._migration(
            database, "upgrade", "0049_p1_production_recovery_decision_evidence"
        )
        assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
        with psycopg.connect(database) as connection:
            enrollment = migration_tests._insert_0047_enrollment(
                connection, authority
            )
        yield database, fixture, authority, enrollment
    finally:
        migration_tests._drop_migration_database(source, name)


def _insert_production_recovery_case(connection, authority, *, deadline=None):
    cycle = authority["cycle"]
    case_id = uuid4()
    deadline = deadline or connection.execute(
        "SELECT clock_timestamp()+interval '1 day'"
    ).fetchone()[0]
    connection.execute(
        """INSERT INTO v4_recovery_cases(
                  id,workspace_id,goal_id,target_cycle_id,context_id,operation_id,
                  kind,open_fingerprint,failure_class,state,revision,owner_id,
                  reason,deadline,max_actions)
           VALUES(%s,%s,%s,%s,%s,%s,'reconciliation',%s,'unknown_effect',
                  'reconciling',1,%s,'production recovery source',%s,3)""",
        (
            case_id,
            authority["workspace_id"],
            cycle["goal_id"],
            cycle["cycle_id"],
            cycle["context_id"],
            cycle["operation_id"],
            "d" * 64,
            authority["subject_id"],
            deadline,
        ),
    )
    return case_id, deadline


class _RecoveryAuthority:
    def __init__(self, module, *, deny_at=None):
        self.module = module
        self.deny_at = deny_at
        self.calls = []

    def authorize_remote_read(self, work, context):
        self.calls.append(("authorize_remote_read", work.work_id))
        if self.deny_at == "before_read":
            raise PermissionError("current read authority unavailable")
        return self.module.ProductionScopedReadGrant(
            grant_id=uuid4(),
            provider_id=work.target_alias,
            account_id=work.remote_account_id,
            resource_id=work.remote_resource_id,
            operation_id=work.operation_id,
            idempotency_key=context.idempotency_key,
            request_fingerprint=work.request_fingerprint,
            scope="original_operation_read",
            policy_decision_id=uuid4(),
            policy_revision=9,
            stop_revision=3,
            route_decision_id=uuid4(),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=2),
        )

    def recheck_remote_read(self, work, grant):
        self.calls.append(("recheck_remote_read", work.work_id))
        if self.deny_at == "after_read":
            raise PermissionError("route or stop changed during read")

    def authorize_alert(self, work, request):
        self.calls.append(("authorize_alert", work.work_id))
        if self.deny_at == "alert":
            raise PermissionError("alert authority unavailable")
        return self.module.ProductionRecoveryDecision(
            allowed=True,
            decision_id=uuid4(),
            route_decision_id=uuid4(),
            policy_revision=9,
            stop_revision=3,
        )

    def authorize_acknowledgment(self, work, command):
        self.calls.append(("authorize_acknowledgment", work.work_id))
        if self.deny_at == "acknowledgment":
            raise PermissionError("acknowledgment authority unavailable")
        return self.module.ProductionRecoveryDecision(
            allowed=True,
            decision_id=command.policy_decision_id,
            route_decision_id=uuid4(),
            policy_revision=9,
            stop_revision=3,
        )

    def authorize_deadline_wake(self, work, source, action_kind, idempotency_key):
        self.calls.append(("authorize_deadline_wake", work.work_id))
        if self.deny_at == "deadline":
            raise PermissionError("current deadline authority unavailable")
        return self.module.ProductionRecoveryDecision(
            allowed=True,
            decision_id=uuid5(NAMESPACE_URL, "deadline-policy:" + idempotency_key),
            route_decision_id=uuid5(NAMESPACE_URL, "deadline-route:" + idempotency_key),
            policy_revision=9,
            stop_revision=3,
        )

    def authorize_no_effect_reissue(self, work, command, observation):
        self.calls.append(("authorize_no_effect_reissue", work.work_id))
        if self.deny_at == "no_effect":
            raise PermissionError("stage or policy authorization changed")
        return self.module.ProductionNoEffectAuthorization(
            stage_decision_id=command.stage_decision_id,
            policy_decision_id=command.policy_decision_id,
            workspace_stop_revision=command.workspace_stop_revision,
            goal_stop_revision=command.goal_stop_revision,
            route_decision_id=command.route_decision_id,
        )


class _ReadTarget:
    def __init__(self, observation=None, *, fail=False, on_read=None):
        self.observation = observation
        self.fail = fail
        self.on_read = on_read
        self.requests = []

    def read_operation(self, request):
        self.requests.append(request)
        if self.on_read:
            self.on_read(request)
        if self.fail:
            raise TimeoutError("target read timed out")
        return self.observation


class _AlertTarget:
    def __init__(self, module, *, fail_attempts=0):
        self.module = module
        self.fail_attempts = fail_attempts
        self.calls = []
        self.receipts = {}

    def send(self, request, idempotency_key):
        self.calls.append((request, idempotency_key))
        if idempotency_key in self.receipts:
            return self.receipts[idempotency_key]
        if self.fail_attempts:
            self.fail_attempts -= 1
            raise TimeoutError("temporary alert outage")
        receipt = self.module.ProductionAlertReceipt(
            alert_event_id=request.alert_event_id,
            idempotency_key=idempotency_key,
            provider_message_id="message-" + idempotency_key,
            status="accepted",
            accepted_at=datetime.now(timezone.utc),
        )
        self.receipts[idempotency_key] = receipt
        return receipt

    def lookup(self, idempotency_key):
        return self.receipts.get(idempotency_key)


def _case_work(repository, authority, module, *, deadline=None):
    with psycopg.connect(repository.database_url) as connection:
        case_id, _ = _insert_production_recovery_case(
            connection, authority, deadline=deadline
        )
    return repository.bind_cycle_case(
        case_id,
        policy=_recovery_policy(module),
    )


def _exact_observation(module, work, context, *, result="in_progress", observed_at=None):
    return module.ProductionRemoteObservation(
        provider_id=work.target_alias,
        account_id=work.remote_account_id,
        operation_id=work.operation_id,
        idempotency_key=context.idempotency_key,
        resource_id=work.remote_resource_id,
        request_fingerprint=work.request_fingerprint,
        result=result,
        remote_state=result,
        response_sha256="3" * 64,
        observed_at=observed_at or datetime.now(timezone.utc),
    )


def _read_context(repository, module, work, *, visibility_window=timedelta(0)):
    with psycopg.connect(repository.database_url, row_factory=dict_row) as connection:
        current = connection.execute(
            """SELECT dispatch.idempotency_key,claim.claimed_at
               FROM v4_production_current_dispatch dispatch
               JOIN v4_production_effect_permit_claims claim
                 ON claim.claim_id=dispatch.current_claim_id
                AND claim.operation_id=dispatch.operation_id
                AND claim.generation=dispatch.generation
               WHERE dispatch.operation_id=%s""",
            (work.operation_id,),
        ).fetchone()
    assert current is not None
    contract = module.ProductionProviderReadContract(
        provider_id=work.target_alias,
        exact_idempotency_lookup=True,
        authoritative_visibility_window=visibility_window,
    )
    return module.ProductionRecoveryReadContext(
        idempotency_key=current["idempotency_key"],
        operation_started_at=current["claimed_at"],
        provider_contract=contract,
    )


def test_exact_source_and_stable_work_identity():
    schedule_tests = import_module("test_p1_production_schedule_fencing")
    module = import_module("salience.cycles.production_recovery")
    with schedule_tests._production_enrollment_fixture("task7_stable_work_") as (
        database,
        control,
        binding,
        key,
        _authority,
        _route_authority,
    ):
        _enroll_production_intent(control, binding, key)
        repository = module.ProductionRecoveryWorkRepository(database)
        policy = _recovery_policy(module)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = list(
                pool.map(
                    lambda _: repository.bind_pre_admission_intent(
                        key.intent_id, policy=policy
                    ),
                    range(2),
                )
            )
        assert first.work_id == second.work_id
        assert first.source_id == key.intent_id
        assert first.source_discriminant == "pre_admission_intent_case"
        assert first.source_revision == key.eligibility_revision
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT count(*) FROM v4_production_recovery_work WHERE source_id=%s",
                (key.intent_id,),
            ).fetchone()[0] == 1
            assert connection.execute(
                """SELECT count(*) FROM v4_production_recovery_events
                   WHERE work_id=%s AND event_kind='alert_intent'""",
                (first.work_id,),
            ).fetchone()[0] == 1


def test_recovery_source_requires_matching_production_enrollment():
    schedule_tests = import_module("test_p1_production_schedule_fencing")
    module = import_module("salience.cycles.production_recovery")
    with schedule_tests._production_enrollment_fixture("task7_missing_enrollment_") as (
        database,
        _control,
        _binding,
        key,
        _authority,
        _route_authority,
    ):
        repository = module.ProductionRecoveryWorkRepository(database)
        with pytest.raises(module.RecoveryInventoryGap):
            repository.bind_pre_admission_intent(
                key.intent_id, policy=_recovery_policy(module)
            )
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT count(*) FROM v4_production_intent_enrollments WHERE intent_id=%s",
                (key.intent_id,),
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT count(*) FROM v4_production_recovery_work WHERE intent_id=%s",
                (key.intent_id,),
            ).fetchone()[0] == 0


def test_source_ids_reasons_states_and_production_hold_snapshot_are_exact():
    module = import_module("salience.cycles.production_recovery")
    migration_tests = import_module("test_v4_migrations")
    with _production_recovery_operation("task7_source_contract_") as (
        database,
        _fixture,
        authority,
        enrollment,
    ):
        repository = module.ProductionRecoveryWorkRepository(database)
        case = _case_work(repository, authority, module)
        assert case.source_discriminant == "cycle_case"
        assert case.source_id == case.case_id
        assert case.source_revision == case.case_revision == 1
        assert case.operation_id == authority["operation_id"]
        assert (case.reason, case.state) == ("unknown_effect", "reconciling")
        hold = repository.bind_production_publication_hold(
            authority["operation_id"],
            reason="expired_permit_receipt",
            deadline=datetime.now(timezone.utc) + timedelta(days=1),
            policy=_recovery_policy(module),
        )
        assert hold.source_discriminant == "production_publication_hold"
        assert hold.source_id == hold.operation_id == authority["operation_id"]
        assert hold.source_revision == 1
        assert (hold.reason, hold.state) == ("expired_permit_receipt", "held")
        assert hold.snapshot_version == "salience.p1-production-publication-liability.v1"
        assert set(hold.snapshot) == set(migration_tests._RECOVERY_V1_KEYS)
        with psycopg.connect(database) as connection:
            joined = connection.execute(
                """SELECT work.intent_id,work.eligibility_revision,
                          enrollment.intent_id,enrollment.eligibility_revision,
                          work.source_id,work.operation_id
                   FROM v4_production_recovery_work work
                   JOIN v4_production_intent_enrollments enrollment
                     ON enrollment.intent_id=work.intent_id
                    AND enrollment.eligibility_revision=work.eligibility_revision
                    AND enrollment.workspace_id=work.workspace_id
                    AND enrollment.goal_id=work.goal_id
                   WHERE work.work_id=%s""",
                (hold.work_id,),
            ).fetchone()
            assert joined == (
                enrollment["intent_id"],
                enrollment["eligibility_revision"],
                enrollment["intent_id"],
                enrollment["eligibility_revision"],
                authority["operation_id"],
                authority["operation_id"],
            )


def test_unmarked_historical_rows_are_never_adopted():
    module = import_module("salience.cycles.production_recovery")
    with _production_recovery_operation("task7_unmarked_legacy_") as (
        database,
        fixture,
        _authority,
        _enrollment,
    ):
        repository = module.ProductionRecoveryWorkRepository(database)
        with pytest.raises(module.RecoveryInventoryGap):
            repository.bind_cycle_case(
                fixture["case_id"], policy=_recovery_policy(module)
            )
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT count(*) FROM v4_production_recovery_work WHERE source_id=%s",
                (fixture["case_id"],),
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT count(*) FROM v4_legacy_publication_recovery_holds WHERE id=%s",
                (fixture["fixture_hold_id"],),
            ).fetchone()[0] == 1


def test_original_operation_read_scope():
    module = import_module("salience.cycles.production_recovery")
    with _production_recovery_operation("task7_read_scope_") as (
        database,
        _fixture,
        authority,
        _enrollment,
    ):
        repository = module.ProductionRecoveryWorkRepository(database)
        work = _case_work(repository, authority, module)
        context = _read_context(repository, module, work)
        current = {"idempotency_key": context.idempotency_key}
        contract = context.provider_contract
        target = _ReadTarget(_exact_observation(module, work, context))
        factory_calls = []
        coordinator = module.ProductionRecoveryCoordinator(
            repository,
            remote_target_factory=lambda provider: (
                factory_calls.append(provider) or target
            ),
            authority=_RecoveryAuthority(module),
            provider_contracts={work.target_alias: contract},
        )
        result = coordinator.reconcile(work.work_id)
        assert result.result_class == "in_progress"
        assert factory_calls == [work.target_alias]
        request = target.requests[0]
        assert request.operation_id == work.operation_id
        assert request.idempotency_key == current["idempotency_key"]
        assert request.account_id == work.remote_account_id
        assert request.target_id == work.target_alias
        assert request.resource_id == work.remote_resource_id
        assert request.request_fingerprint == work.request_fingerprint
        assert request.read_grant.scope == "original_operation_read"
        assert {field.name for field in fields(request)} == {
            "operation_id",
            "idempotency_key",
            "account_id",
            "target_id",
            "resource_id",
            "request_fingerprint",
            "read_grant",
        }
        assert set(dir(target)) & {"write", "cancel", "list", "read_account"} == set()


def test_authority_and_stop_races():
    module = import_module("salience.cycles.production_recovery")
    with _production_recovery_operation("task7_authority_races_") as (
        database,
        _fixture,
        authority,
        _enrollment,
    ):
        repository = module.ProductionRecoveryWorkRepository(database)
        work = _case_work(repository, authority, module)
        contract = module.ProductionProviderReadContract(
            provider_id=work.target_alias,
            exact_idempotency_lookup=True,
            authoritative_visibility_window=timedelta(0),
        )
        context = _read_context(repository, module, work)
        target = _ReadTarget(_exact_observation(module, work, context))
        calls = []
        before = module.ProductionRecoveryCoordinator(
            repository,
            remote_target_factory=lambda provider: calls.append(provider) or target,
            authority=_RecoveryAuthority(module, deny_at="before_read"),
            provider_contracts={work.target_alias: contract},
        )
        with pytest.raises(PermissionError):
            before.reconcile(work.work_id)
        assert calls == []
        assert target.requests == []

        after_authority = _RecoveryAuthority(module, deny_at="after_read")
        after = module.ProductionRecoveryCoordinator(
            repository,
            remote_target_factory=lambda _provider: target,
            authority=after_authority,
            provider_contracts={work.target_alias: contract},
        )
        result = after.reconcile(work.work_id)
        assert result.result_class == "held"
        assert result.remote_state is None
        assert after_authority.calls[-1][0] == "recheck_remote_read"
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT state FROM v4_recovery_cases WHERE id=%s",
                (work.case_id,),
            ).fetchone()[0] == "reconciling"
            assert connection.execute(
                "SELECT state FROM v4_production_recovery_work WHERE work_id=%s",
                (work.work_id,),
            ).fetchone()[0] == "reconciling"


def test_result_classification():
    module = import_module("salience.cycles.production_recovery")
    cases = (
        ("in_progress", "in_progress"),
        ("succeeded", "terminal_success"),
        ("not_found", "authoritative_no_effect"),
        ("unknown", "unknown"),
    )
    for provider_result, expected in cases:
        with _production_recovery_operation("task7_classification_case_") as (
            database,
            _fixture,
            authority,
            _enrollment,
        ):
            repository = module.ProductionRecoveryWorkRepository(database)
            work = _case_work(repository, authority, module)
            context = _read_context(repository, module, work)
            contract = context.provider_contract
            observation = _exact_observation(module, work, context, result=provider_result)
            coordinator = module.ProductionRecoveryCoordinator(
                repository,
                remote_target_factory=lambda _provider, obs=observation: _ReadTarget(obs),
                authority=_RecoveryAuthority(module),
                provider_contracts={work.target_alias: contract},
            )
            assert coordinator.reconcile(work.work_id).result_class == expected


def test_provider_state_and_alert_reference_are_safe_scalars():
    module = import_module("salience.cycles.production_recovery")
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError):
        module.ProductionRemoteObservation(
            provider_id="provider",
            account_id="account",
            operation_id=uuid4(),
            idempotency_key="operation-key",
            resource_id="remote-123",
            request_fingerprint="a" * 64,
            result="unknown",
            remote_state="Bearer secret-value",
            response_sha256="b" * 64,
            observed_at=now,
        )
    with pytest.raises(ValueError):
        module.ProductionAlertReceipt(
            alert_event_id=uuid4(),
            idempotency_key="p1-recovery-alert:stable",
            provider_message_id="https://channel.example/private/token",
            status="accepted",
            accepted_at=now,
        )
    with pytest.raises(ValueError):
        module.ProductionAlertAcknowledgment(
            work_id=uuid4(),
            alert_event_id=uuid4(),
            source_id=uuid4(),
            delivery_id="Bearer secret-value",
            actor_id=uuid4(),
            idempotency_key="ack:stable",
            policy_decision_id=uuid4(),
        )


def test_crash_and_exact_replay():
    module = import_module("salience.cycles.production_recovery")
    with _production_recovery_operation("task7_crash_replay_") as (
        database,
        _fixture,
        authority,
        _enrollment,
    ):
        repository = module.ProductionRecoveryWorkRepository(database)
        work = _case_work(repository, authority, module)
        contract = module.ProductionProviderReadContract(
            provider_id=work.target_alias,
            exact_idempotency_lookup=True,
            authoritative_visibility_window=timedelta(0),
        )
        context = _read_context(repository, module, work)
        contract = context.provider_contract
        observation = _exact_observation(module, work, context)
        target = _ReadTarget(observation)
        coordinator = module.ProductionRecoveryCoordinator(
            repository,
            remote_target_factory=lambda _provider: target,
            authority=_RecoveryAuthority(module),
            provider_contracts={work.target_alias: contract},
        )
        append = repository._append_event
        failed = [False]

        def crash_before_event_commit(*args, **kwargs):
            if not failed[0]:
                failed[0] = True
                raise RuntimeError("simulated process death before evidence commit")
            return append(*args, **kwargs)

        repository._append_event = crash_before_event_commit
        with pytest.raises(RuntimeError, match="simulated process death"):
            coordinator.reconcile(work.work_id)
        repository._append_event = append
        replay = coordinator.reconcile(work.work_id)
        committed_replay = coordinator.reconcile(work.work_id)
        assert replay.event_id == committed_replay.event_id
        assert len(target.requests) == 2
        with psycopg.connect(database) as connection:
            assert connection.execute(
                """SELECT count(*) FROM v4_production_recovery_events
                   WHERE work_id=%s AND event_kind='remote_read_observation'""",
                (work.work_id,),
            ).fetchone()[0] == 1


def test_production_case_command_and_deadline_controller():
    module = import_module("salience.cycles.production_recovery")
    with _production_recovery_operation("task7_deadline_controller_") as (
        database,
        _fixture,
        authority,
        _enrollment,
    ):
        with psycopg.connect(database) as connection:
            legacy_notification_count = connection.execute(
                "SELECT count(*) FROM v4_case_notifications"
            ).fetchone()[0]
        repository = module.ProductionRecoveryWorkRepository(database)
        past = datetime.now(timezone.utc) - timedelta(seconds=5)
        work = _case_work(repository, authority, module, deadline=past)
        calls = []

        class Commands:
            def suspend_case(self, connection, command):
                calls.append(("suspend_case", command.case_id, command.expected_revision))
                return module.TypedCaseCommandReceipt(
                    case_id=command.case_id,
                    action="deadline_suspend",
                    resulting_revision=command.expected_revision + 1,
                    command_id=uuid4(),
                )

            def wake_intent(self, connection, command):
                calls.append(("wake_intent", command.intent_id, command.expected_revision))
                return module.TypedIntentWakeReceipt(
                    intent_id=command.intent_id,
                    eligibility_revision=command.expected_revision,
                    disposition="held",
                    cycle_id=None,
                )

        controller = module.ProductionRecoveryCaseController(
            repository,
            authority=_RecoveryAuthority(module),
            commands=Commands(),
        )
        first = controller.wake_deadline(work.work_id)
        replay = controller.wake_deadline(work.work_id)
        assert first.event_id == replay.event_id
        assert calls == [("suspend_case", work.case_id, 1)]
        assert first.command_result.action == "deadline_suspend"
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT count(*) FROM v4_runtime_waits"
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT count(*) FROM v4_case_notifications"
            ).fetchone()[0] == legacy_notification_count
            assert connection.execute(
                "SELECT count(*) FROM v4_case_commands WHERE case_id=%s",
                (work.case_id,),
            ).fetchone()[0] == 0


def test_deadline_command_replay_precedes_changed_case_source_after_crash():
    module = import_module("salience.cycles.production_recovery")
    with _production_recovery_operation("task7_deadline_case_replay_") as (
        database,
        _fixture,
        authority,
        _enrollment,
    ):
        repository = module.ProductionRecoveryWorkRepository(database)
        work = _case_work(
            repository,
            authority,
            module,
            deadline=datetime.now(timezone.utc) - timedelta(seconds=5),
        )

        class DurableCaseCommandPath:
            def __init__(self):
                self.calls = []

            def suspend_case(self, connection, command):
                self.calls.append(command)
                fingerprint = sha256(
                    f"{command.case_id}:{command.expected_revision}:{command.idempotency_key}".encode()
                ).hexdigest()
                prior = connection.execute(
                    """SELECT id,fingerprint,result FROM v4_case_commands
                       WHERE case_id=%s AND idempotency_key=%s""",
                    (command.case_id, command.idempotency_key),
                ).fetchone()
                if prior:
                    assert prior["fingerprint"] == fingerprint
                    return module.TypedCaseCommandReceipt(
                        case_id=command.case_id,
                        action="deadline_suspend",
                        resulting_revision=prior["result"]["resulting_revision"],
                        command_id=prior["id"],
                    )
                current = connection.execute(
                    "SELECT revision,state FROM v4_recovery_cases WHERE id=%s FOR UPDATE",
                    (command.case_id,),
                ).fetchone()
                assert current == {"revision": command.expected_revision, "state": "reconciling"}
                next_revision = command.expected_revision + 1
                command_id = uuid4()
                result = {"resulting_revision": next_revision}
                connection.execute(
                    """INSERT INTO v4_case_events(
                               id,case_id,case_revision,action,actor_id,traceparent,payload)
                       VALUES(%s,%s,%s,'escalated',%s,%s,%s)""",
                    (
                        uuid4(),
                        command.case_id,
                        next_revision,
                        authority["subject_id"],
                        "00-" + uuid4().hex + "-" + uuid4().hex[:16] + "-01",
                        Jsonb({"case_id": str(command.case_id), "state": "suspended"}),
                    ),
                )
                connection.execute(
                    "UPDATE v4_recovery_cases SET state='suspended',revision=%s WHERE id=%s",
                    (next_revision, command.case_id),
                )
                connection.execute(
                    """INSERT INTO v4_case_commands(
                               id,case_id,idempotency_key,fingerprint,action,actor_id,result)
                       VALUES(%s,%s,%s,%s,'deadline_suspend',%s,%s)""",
                    (
                        command_id,
                        command.case_id,
                        command.idempotency_key,
                        fingerprint,
                        authority["subject_id"],
                        Jsonb(result),
                    ),
                )
                # Model the separately committed canonical typed command boundary.
                connection.commit()
                return module.TypedCaseCommandReceipt(
                    case_id=command.case_id,
                    action="deadline_suspend",
                    resulting_revision=next_revision,
                    command_id=command_id,
                )

        commands = DurableCaseCommandPath()
        controller = module.ProductionRecoveryCaseController(
            repository,
            authority=_RecoveryAuthority(module),
            commands=commands,
        )
        append = repository._append_event
        failed = [False]

        def crash_after_command_before_event(*args, **kwargs):
            if kwargs.get("event_kind") == "escalation" and not failed[0]:
                failed[0] = True
                raise RuntimeError("simulated crash after canonical case command")
            return append(*args, **kwargs)

        repository._append_event = crash_after_command_before_event
        with pytest.raises(RuntimeError, match="after canonical case command"):
            controller.wake_deadline(work.work_id)
        repository._append_event = append
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT state,revision FROM v4_recovery_cases WHERE id=%s",
                (work.case_id,),
            ).fetchone() == ("suspended", 2)
            assert connection.execute(
                "SELECT count(*) FROM v4_production_recovery_events WHERE work_id=%s AND event_kind='escalation'",
                (work.work_id,),
            ).fetchone()[0] == 0

        replay = controller.wake_deadline(work.work_id)
        committed_replay = controller.wake_deadline(work.work_id)
        assert replay.event_id == committed_replay.event_id
        assert replay.command_result == committed_replay.command_result
        assert len(commands.calls) == 2
        assert commands.calls[0] == commands.calls[1]
        assert replay.command_result.resulting_revision == 2
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT count(*) FROM v4_case_commands WHERE case_id=%s",
                (work.case_id,),
            ).fetchone()[0] == 1
            assert connection.execute(
                "SELECT count(*) FROM v4_production_recovery_events WHERE work_id=%s AND event_kind='escalation'",
                (work.work_id,),
            ).fetchone()[0] == 1


def test_deadline_command_replay_precedes_changed_pre_admission_source_after_crash():
    schedule_tests = import_module("test_p1_production_schedule_fencing")
    module = import_module("salience.cycles.production_recovery")
    admission_module = import_module("salience.cycles.admission")
    with schedule_tests._production_enrollment_fixture("task7_deadline_intent_replay_") as (
        database,
        control,
        binding,
        key,
        authority,
        _route_authority,
    ):
        upgraded = import_module("test_v4_migrations")._migration(
            database, "upgrade", "0049_p1_production_recovery_decision_evidence"
        )
        assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
        _enroll_production_intent(control, binding, key)
        repository = module.ProductionRecoveryWorkRepository(database)
        work = repository.bind_pre_admission_intent(
            key.intent_id, policy=_recovery_policy(module)
        )

        class DurableIntentCommandPath:
            def __init__(self):
                self.calls = []

            def wake_intent(self, connection, command):
                self.calls.append(command)
                prior = connection.execute(
                    """SELECT payload FROM v4_cycle_events
                       WHERE intent_id=%s AND kind='production_recovery_intent_wake_command'
                         AND payload->>'idempotency_key'=%s""",
                    (command.intent_id, command.idempotency_key),
                ).fetchone()
                if prior:
                    payload = prior["payload"]
                    assert payload["expected_revision"] == command.expected_revision
                    return module.TypedIntentWakeReceipt(
                        intent_id=command.intent_id,
                        eligibility_revision=payload["expected_revision"],
                        disposition=payload["disposition"],
                        cycle_id=None,
                    )
                current = connection.execute(
                    "SELECT eligibility_revision FROM v4_cycle_intents WHERE id=%s FOR UPDATE",
                    (command.intent_id,),
                ).fetchone()["eligibility_revision"]
                assert current == command.expected_revision
                service = admission_module.CycleAdmission(
                    database,
                    workspace_id=binding.workspace_id,
                    subject_id=authority["subject_id"],
                )
                service._wake(
                    connection,
                    command.intent_id,
                    expected_revision=command.expected_revision,
                    review=True,
                )
                connection.execute(
                    """INSERT INTO v4_cycle_events(
                               id,workspace_id,subject_id,goal_id,intent_id,kind,
                               traceparent,payload)
                       VALUES(%s,%s,%s,%s,%s,'production_recovery_intent_wake_command',%s,%s)""",
                    (
                        uuid4(),
                        binding.workspace_id,
                        authority["subject_id"],
                        binding.goal_id,
                        command.intent_id,
                        "00-" + uuid4().hex + "-" + uuid4().hex[:16] + "-01",
                        Jsonb(
                            {
                                "idempotency_key": command.idempotency_key,
                                "expected_revision": command.expected_revision,
                                "disposition": "woken",
                            }
                        ),
                    ),
                )
                connection.commit()
                return module.TypedIntentWakeReceipt(
                    intent_id=command.intent_id,
                    eligibility_revision=command.expected_revision,
                    disposition="woken",
                    cycle_id=None,
                )

        commands = DurableIntentCommandPath()
        controller = module.ProductionRecoveryCaseController(
            repository,
            authority=_RecoveryAuthority(module),
            commands=commands,
        )
        append = repository._append_event
        failed = [False]

        def crash_after_command_before_event(*args, **kwargs):
            if kwargs.get("event_kind") == "escalation" and not failed[0]:
                failed[0] = True
                raise RuntimeError("simulated crash after canonical intent command")
            return append(*args, **kwargs)

        repository._append_event = crash_after_command_before_event
        with pytest.raises(RuntimeError, match="after canonical intent command"):
            controller.wake_deadline(work.work_id)
        repository._append_event = append
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT eligibility_revision FROM v4_cycle_intents WHERE id=%s",
                (work.intent_id,),
            ).fetchone()[0] == work.eligibility_revision + 1
            assert connection.execute(
                "SELECT count(*) FROM v4_cycles WHERE intent_id=%s",
                (work.intent_id,),
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT count(*) FROM v4_production_recovery_events WHERE work_id=%s AND event_kind='escalation'",
                (work.work_id,),
            ).fetchone()[0] == 0

        replay = controller.wake_deadline(work.work_id)
        committed_replay = controller.wake_deadline(work.work_id)
        assert replay.event_id == committed_replay.event_id
        assert replay.command_result == committed_replay.command_result
        assert len(commands.calls) == 2
        assert commands.calls[0] == commands.calls[1]
        assert replay.command_result.eligibility_revision == work.eligibility_revision
        assert replay.command_result.disposition == "woken"
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT count(*) FROM v4_cycle_events WHERE intent_id=%s AND kind='intent_wake'",
                (work.intent_id,),
            ).fetchone()[0] == 1
            assert connection.execute(
                "SELECT count(*) FROM v4_production_recovery_events WHERE work_id=%s AND event_kind='escalation'",
                (work.work_id,),
            ).fetchone()[0] == 1


def _alert_request(repository, module, work, policy):
    return repository.alert_request(
        work.work_id,
        severity="high",
        escalation_level=0,
        recipient_profile=module.ProductionRecipientProfile(
            profile_id=policy.recipient_profile_id,
            revision=policy.recipient_profile_revision,
            profile_sha256=policy.recipient_profile_sha256,
            owner_subject_id=work.owner_id,
            on_call_group_ids=(uuid4(),),
        ),
        retry_policy=module.ProductionAlertRetryPolicy(
            policy_id=policy.retry_policy_id,
            revision=policy.retry_policy_revision,
            policy_sha256=policy.retry_policy_sha256,
            maximum_attempts=2,
            retry_intervals=(timedelta(seconds=1),),
            claim_ttl=timedelta(seconds=15),
            owner_response_window=timedelta(minutes=2),
            escalation_after=(timedelta(minutes=2),),
        ),
        policy_decision_id=uuid4(),
    )


def test_alert_delivery_and_acknowledgment():
    module = import_module("salience.cycles.production_recovery")
    schedule_tests = import_module("test_p1_production_schedule_fencing")
    with schedule_tests._production_enrollment_fixture("task7_alert_ack_") as (
        database,
        control,
        binding,
        key,
        _authority,
        _route_authority,
    ):
        _enroll_production_intent(control, binding, key)
        repository = module.ProductionRecoveryWorkRepository(database)
        policy = _recovery_policy(module)
        work = repository.bind_pre_admission_intent(key.intent_id, policy=policy)
        request = _alert_request(repository, module, work, policy)
        channel = _AlertTarget(module, fail_attempts=1)
        hold_calls = []
        alerts = module.ProductionRecoveryAlerts(
            repository,
            target=channel,
            authority=_RecoveryAuthority(module),
            recipient_profile=request.recipient_profile,
            retry_policy=request.retry_policy,
            wait=lambda _delay: None,
            schedule_hold_target=lambda *args: hold_calls.append(args),
        )
        schedule_contracts = import_module("salience.cycles._production_schedule_contracts")
        assert isinstance(alerts, schedule_contracts.ScheduleAlertSink)
        alerts.emit_hold(work.workspace_id, work.source_id, "unknown_effect", "a" * 64)
        assert hold_calls == [(work.workspace_id, work.source_id, "unknown_effect", "a" * 64)]
        receipt = alerts.send(request)
        replay = alerts.send(request)
        assert receipt == replay
        assert len(channel.calls) == 2
        assert len({call[1] for call in channel.calls}) == 1
        assert channel.calls[0][1] == receipt.idempotency_key
        ack = module.ProductionAlertAcknowledgment(
            work_id=work.work_id,
            alert_event_id=request.alert_event_id,
            source_id=work.source_id,
            delivery_id=receipt.provider_message_id,
            actor_id=work.owner_id,
            idempotency_key="ack:" + str(request.alert_event_id),
            policy_decision_id=uuid4(),
        )
        event = alerts.acknowledge(ack)
        assert event.work_id == work.work_id
        assert event.event_kind == "acknowledgment"
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT disposition FROM v4_admissions WHERE intent_id=%s",
                (key.intent_id,),
            ).fetchone()[0] == "review_required"
            assert connection.execute(
                """SELECT count(*) FROM v4_production_recovery_events
                   WHERE work_id=%s AND event_kind='acknowledgment'""",
                (work.work_id,),
            ).fetchone()[0] == 1


def test_alert_acceptance_crash_replays_same_key(monkeypatch):
    module = import_module("salience.cycles.production_recovery")
    schedule_tests = import_module("test_p1_production_schedule_fencing")
    with schedule_tests._production_enrollment_fixture("task7_alert_crash_") as (
        database,
        control,
        binding,
        key,
        _authority,
        _route_authority,
    ):
        _enroll_production_intent(control, binding, key)
        repository = module.ProductionRecoveryWorkRepository(database)
        policy = _recovery_policy(module)
        work = repository.bind_pre_admission_intent(key.intent_id, policy=policy)
        request = _alert_request(repository, module, work, policy)
        channel = _AlertTarget(module)
        alerts = module.ProductionRecoveryAlerts(
            repository,
            target=channel,
            authority=_RecoveryAuthority(module),
            recipient_profile=request.recipient_profile,
            retry_policy=request.retry_policy,
            wait=lambda _delay: None,
            schedule_hold_target=lambda *_args: None,
        )
        append = repository._append_event
        failed = [False]

        def crash_after_external_acceptance(connection, *args, **kwargs):
            if kwargs.get("event_kind") == "delivery_receipt" and not failed[0]:
                failed[0] = True
                raise RuntimeError("simulated death before local receipt commit")
            return append(connection, *args, **kwargs)

        monkeypatch.setattr(repository, "_append_event", crash_after_external_acceptance)
        with pytest.raises(RuntimeError, match="before local receipt commit"):
            alerts.send(request)
        monkeypatch.setattr(repository, "_append_event", append)
        replay = alerts.send(request)
        assert replay.status == "accepted"
        assert replay.idempotency_key == request.idempotency_key
        assert len(channel.calls) == 1
        with psycopg.connect(database) as connection:
            assert connection.execute(
                """SELECT count(*) FROM v4_production_recovery_events
                   WHERE work_id=%s AND event_kind='delivery_receipt'""",
                (work.work_id,),
            ).fetchone()[0] == 1


def test_concurrent_duplicate_alert_send_is_one_local_delivery():
    module = import_module("salience.cycles.production_recovery")
    schedule_tests = import_module("test_p1_production_schedule_fencing")
    with schedule_tests._production_enrollment_fixture("task7_alert_concurrent_") as (
        database,
        control,
        binding,
        key,
        _authority,
        _route_authority,
    ):
        _enroll_production_intent(control, binding, key)
        repository = module.ProductionRecoveryWorkRepository(database)
        policy = _recovery_policy(module)
        work = repository.bind_pre_admission_intent(key.intent_id, policy=policy)
        request = _alert_request(repository, module, work, policy)
        channel = _AlertTarget(module)
        first_authorizing = Event()
        second_authorizing = Event()
        release_first = Event()
        authorization_lock = Lock()
        authorization_count = [0]

        class PausingAuthority(_RecoveryAuthority):
            def authorize_alert(self, authorized_work, authorized_request):
                with authorization_lock:
                    authorization_count[0] += 1
                    call_number = authorization_count[0]
                if call_number == 1:
                    first_authorizing.set()
                    assert release_first.wait(timeout=5)
                else:
                    second_authorizing.set()
                return super().authorize_alert(authorized_work, authorized_request)

        alerts = module.ProductionRecoveryAlerts(
            repository,
            target=channel,
            authority=PausingAuthority(module),
            recipient_profile=request.recipient_profile,
            retry_policy=request.retry_policy,
            wait=lambda _delay: None,
            schedule_hold_target=lambda *_args: None,
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(alerts.send, request)
            assert first_authorizing.wait(timeout=5)
            second = pool.submit(alerts.send, request)
            # Without per-alert serialization, the second send passes the
            # local-receipt check while the first is paused at authorization.
            second_authorizing.wait(timeout=1)
            release_first.set()
            first_receipt = first.result(timeout=10)
            second_receipt = second.result(timeout=10)
        assert first_receipt == second_receipt
        assert first_receipt.status == "accepted"
        assert len(channel.calls) == 1
        with psycopg.connect(database) as connection:
            assert connection.execute(
                """SELECT count(*) FROM v4_production_recovery_events
                   WHERE work_id=%s AND event_kind='delivery_receipt'""",
                (work.work_id,),
            ).fetchone()[0] == 1


def test_owner_and_outage_escalation():
    module = import_module("salience.cycles.production_recovery")
    schedule_tests = import_module("test_p1_production_schedule_fencing")
    with schedule_tests._production_enrollment_fixture("task7_alert_outage_") as (
        database,
        control,
        binding,
        key,
        _authority,
        _route_authority,
    ):
        _enroll_production_intent(control, binding, key)
        repository = module.ProductionRecoveryWorkRepository(database)
        policy = _recovery_policy(module)
        work = repository.bind_pre_admission_intent(key.intent_id, policy=policy)
        with psycopg.connect(database) as connection:
            connection.execute(
                "UPDATE identity_subjects SET enabled=false WHERE id=%s",
                (work.owner_id,),
            )
        profile = module.ProductionRecipientProfile(
            profile_id=policy.recipient_profile_id,
            revision=policy.recipient_profile_revision,
            profile_sha256=policy.recipient_profile_sha256,
            owner_subject_id=work.owner_id,
            on_call_group_ids=(),
        )
        retry = module.ProductionAlertRetryPolicy(
            policy_id=policy.retry_policy_id,
            revision=policy.retry_policy_revision,
            policy_sha256=policy.retry_policy_sha256,
            maximum_attempts=2,
            retry_intervals=(timedelta(seconds=1),),
            claim_ttl=timedelta(seconds=15),
            owner_response_window=timedelta(minutes=2),
            escalation_after=(timedelta(minutes=2),),
        )
        request = repository.alert_request(
            work.work_id,
            severity="high",
            escalation_level=0,
            recipient_profile=profile,
            retry_policy=retry,
            policy_decision_id=uuid4(),
        )
        channel = _AlertTarget(module, fail_attempts=5)
        alerts = module.ProductionRecoveryAlerts(
            repository,
            target=channel,
            authority=_RecoveryAuthority(module),
            recipient_profile=profile,
            retry_policy=retry,
            wait=lambda _delay: None,
            schedule_hold_target=lambda *_args: None,
        )
        result = alerts.send(request)
        assert result.status == "held"
        assert channel.calls == []
        with psycopg.connect(database) as connection:
            kinds = {
                row[0]
                for row in connection.execute(
                    "SELECT event_kind FROM v4_production_recovery_events WHERE work_id=%s",
                    (work.work_id,),
                )
            }
            assert "owner_unavailable" in kinds
            assert "held_conflict" in kinds


def test_two_reason_concurrent_hold_insert_and_crash():
    module = import_module("salience.cycles.production_recovery")
    for reason in ("expired_permit_receipt", "published_during_cancel"):
        with _production_recovery_operation("task7_hold_race_") as (
            database,
            _fixture,
            authority,
            _enrollment,
        ):
            if reason == "published_during_cancel":
                with psycopg.connect(database) as connection:
                    connection.execute("SET session_replication_role=replica")
                    connection.execute(
                        "UPDATE v4_cycles SET state='closed',disposition='cancelled',"
                        "closed_at=clock_timestamp() WHERE id=%s",
                        (authority["cycle"]["cycle_id"],),
                    )
                    connection.execute("SET session_replication_role=origin")
            repository = module.ProductionRecoveryWorkRepository(database)
            policy = _recovery_policy(module)
            deadline = datetime.now(timezone.utc) + timedelta(days=1)

            def create(_):
                return repository.bind_production_publication_hold(
                    authority["operation_id"],
                    reason=reason,
                    deadline=deadline,
                    policy=policy,
                )

            with ThreadPoolExecutor(max_workers=2) as pool:
                first, second = list(pool.map(create, range(2)))
            assert first.work_id == second.work_id
            assert first.snapshot_sha256 == second.snapshot_sha256
            assert first.reason == reason and first.state == "held"

            script = """
import os
from datetime import datetime
from uuid import UUID
from salience.cycles.production_recovery import ProductionRecoveryWorkRepository, ProductionRecoveryPolicyBinding
repo = ProductionRecoveryWorkRepository(os.environ['TASK7_DB'])
policy = ProductionRecoveryPolicyBinding(
    policy_decision_id=UUID(os.environ['TASK7_POLICY_DECISION']),
    recipient_profile_id=UUID(os.environ['TASK7_PROFILE_ID']),
    recipient_profile_revision=7,
    recipient_profile_sha256='1111111111111111111111111111111111111111111111111111111111111111',
    retry_policy_id=UUID(os.environ['TASK7_RETRY_ID']),
    retry_policy_revision=4,
    retry_policy_sha256='2222222222222222222222222222222222222222222222222222222222222222',
)
repo.bind_production_publication_hold(
    UUID(os.environ['TASK7_OPERATION_ID']),
    reason=os.environ['TASK7_REASON'],
    deadline=datetime.fromisoformat(os.environ['TASK7_DEADLINE']),
    policy=policy,
)
os._exit(23)
"""
            env = os.environ.copy()
            env.update(
                {
                    "TASK7_DB": database,
                    "TASK7_POLICY_DECISION": str(policy.policy_decision_id),
                    "TASK7_PROFILE_ID": str(policy.recipient_profile_id),
                    "TASK7_RETRY_ID": str(policy.retry_policy_id),
                    "TASK7_OPERATION_ID": str(authority["operation_id"]),
                    "TASK7_REASON": reason,
                    "TASK7_DEADLINE": deadline.isoformat(),
                }
            )
            child = subprocess.run(
                [sys.executable, "-c", script],
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            assert child.returncode == 23, child.stdout + child.stderr
            replay = repository.bind_production_publication_hold(
                authority["operation_id"],
                reason=reason,
                deadline=deadline,
                policy=policy,
            )
            assert replay.work_id == first.work_id
            assert replay.snapshot == first.snapshot


def test_operationless_intent_restart_and_concurrency():
    schedule_tests = import_module("test_p1_production_schedule_fencing")
    module = import_module("salience.cycles.production_recovery")
    with schedule_tests._production_enrollment_fixture("task7_intent_replay_") as (
        database,
        control,
        binding,
        key,
        _authority,
        _route_authority,
    ):
        _enroll_production_intent(control, binding, key)
        repository = module.ProductionRecoveryWorkRepository(database)
        policy = _recovery_policy(module)
        with ThreadPoolExecutor(max_workers=2) as pool:
            work1, work2 = list(
                pool.map(
                    lambda _: repository.bind_pre_admission_intent(
                        key.intent_id, policy=policy
                    ),
                    range(2),
                )
            )
        assert work1.work_id == work2.work_id
        assert work1.cycle_id is None
        assert work1.context_id is None
        assert work1.operation_id is None
        factory_calls = []
        coordinator = module.ProductionRecoveryCoordinator(
            repository,
            remote_target_factory=lambda provider: factory_calls.append(provider),
            authority=_RecoveryAuthority(module),
            provider_contracts={},
        )
        with pytest.raises(module.RecoveryProviderReadDenied):
            coordinator.reconcile(work1.work_id)
        assert factory_calls == []
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT count(*) FROM v4_cycles WHERE intent_id=%s",
                (key.intent_id,),
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT count(*) FROM v4_production_recovery_work WHERE source_id=%s",
                (key.intent_id,),
            ).fetchone()[0] == 1


def test_schema_and_migration_safety():
    module = import_module("salience.cycles.production_recovery")
    with _production_recovery_operation("task7_schema_safety_") as (
        database,
        fixture,
        authority,
        _enrollment,
    ):
        repository = module.ProductionRecoveryWorkRepository(database)
        case = _case_work(repository, authority, module)
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0] == "0049_p1_production_recovery_decision_evidence"
            assert connection.execute(
                "SELECT count(*) FROM v4_legacy_publication_recovery_holds WHERE id=%s",
                (fixture["fixture_hold_id"],),
            ).fetchone()[0] == 1
            assert connection.execute(
                "SELECT count(*) FROM v4_case_notifications WHERE case_id=%s",
                (fixture["case_id"],),
            ).fetchone()[0] == 1
            before = connection.execute(
                "SELECT count(*) FROM v4_production_recovery_events WHERE work_id=%s",
                (case.work_id,),
            ).fetchone()[0]
            with pytest.raises(psycopg.Error):
                with connection.transaction():
                    connection.execute(
                        "UPDATE v4_production_recovery_events SET event_key='changed' WHERE work_id=%s",
                        (case.work_id,),
                    )
            assert connection.execute(
                "SELECT count(*) FROM v4_production_recovery_events WHERE work_id=%s",
                (case.work_id,),
            ).fetchone()[0] == before
            assert connection.execute(
                """SELECT count(*) FROM pg_trigger
                   WHERE tgrelid='public.v4_production_recovery_work'::regclass
                     AND tgname='immutable_v4_production_recovery_work_v1'"""
            ).fetchone()[0] == 1
            assert connection.execute(
                """SELECT count(*) FROM pg_trigger
                   WHERE tgrelid='public.v4_production_recovery_events'::regclass
                     AND tgname IN (
                       'validate_v4_production_recovery_event_v2',
                       'validate_v4_production_recovery_deadline_evidence_v1')"""
            ).fetchone()[0] == 2


def test_remote_read_not_found_without_authoritative_window_stays_held():
    module = import_module("salience.cycles.production_recovery")
    with _production_recovery_operation("task7_not_found_visibility_") as (
        database,
        _fixture,
        authority,
        _enrollment,
    ):
        repository = module.ProductionRecoveryWorkRepository(database)
        work = _case_work(repository, authority, module)
        context = _read_context(repository, module, work, visibility_window=None)
        contract = context.provider_contract
        coordinator = module.ProductionRecoveryCoordinator(
            repository,
            remote_target_factory=lambda _provider: _ReadTarget(
                _exact_observation(module, work, context, result="not_found")
            ),
            authority=_RecoveryAuthority(module),
            provider_contracts={work.target_alias: contract},
        )
        result = coordinator.reconcile(work.work_id)
        assert result.result_class == "unknown"
        assert result.remote_state == "not_found"
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT state FROM v4_recovery_cases WHERE id=%s",
                (work.case_id,),
            ).fetchone()[0] == "reconciling"


def test_preliminary_not_found_allows_one_post_visibility_authoritative_read():
    module = import_module("salience.cycles.production_recovery")
    with _production_recovery_operation("task7_read_visibility_phases_") as (
        database,
        _fixture,
        authority,
        _enrollment,
    ):
        repository = module.ProductionRecoveryWorkRepository(database)
        work = _case_work(repository, authority, module)
        first_context = _read_context(repository, module, work)
        with psycopg.connect(database) as connection:
            database_now = connection.execute("SELECT clock_timestamp()").fetchone()[0]
        # Place the positive boundary two seconds ahead of the first read while
        # respecting the immutable operation claim timestamp.
        window = database_now - first_context.operation_started_at + timedelta(seconds=2)
        contract = module.ProductionProviderReadContract(
            provider_id=work.target_alias,
            exact_idempotency_lookup=True,
            authoritative_visibility_window=window,
        )
        context = _read_context(repository, module, work, visibility_window=window)
        class Target:
            def __init__(self):
                self.requests = []

            def read_operation(self, request):
                self.requests.append(request)
                return _exact_observation(
                    module, work, context, result="not_found",
                    observed_at=datetime.now(timezone.utc),
                )

        target = Target()
        coordinator = module.ProductionRecoveryCoordinator(
            repository,
            remote_target_factory=lambda _provider: target,
            authority=_RecoveryAuthority(module),
            provider_contracts={work.target_alias: contract},
        )
        append = repository._append_event
        failed = [False]

        def crash_before_preliminary_commit(*args, **kwargs):
            if kwargs.get("event_kind") == "remote_read_observation" and not failed[0]:
                failed[0] = True
                raise RuntimeError("simulated crash before pre-visibility event commit")
            return append(*args, **kwargs)

        repository._append_event = crash_before_preliminary_commit
        with pytest.raises(RuntimeError, match="before pre-visibility event commit"):
            coordinator.reconcile(work.work_id)
        repository._append_event = append
        preliminary = coordinator.reconcile(work.work_id)
        assert preliminary.result_class == "unknown"
        assert preliminary.remote_state == "not_found"
        assert len(target.requests) == 2
        time.sleep(2.1)

        authoritative = coordinator.reconcile(work.work_id)
        replay = coordinator.reconcile(work.work_id)
        assert authoritative.result_class == "authoritative_no_effect"
        assert authoritative.event_id == replay.event_id
        assert preliminary.event_id != authoritative.event_id
        assert len(target.requests) == 3
        assert len({request.idempotency_key for request in target.requests}) == 1
        with psycopg.connect(database) as connection:
            events = connection.execute(
                """SELECT event_key,payload->>'result_class',payload->>'remote_state'
                   FROM v4_production_recovery_events
                   WHERE work_id=%s AND event_kind='remote_read_observation'
                   ORDER BY occurred_at,event_id""",
                (work.work_id,),
            ).fetchall()
            assert len(events) == 2
            assert events[0][1:] == ("unknown", "not_found")
            assert events[1][1:] == ("authoritative_no_effect", "not_found")
            assert events[0][0] != events[1][0]


def test_pre_visibility_read_finishing_after_boundary_stays_unknown():
    module = import_module("salience.cycles.production_recovery")
    with _production_recovery_operation("task7_read_crosses_visibility_boundary_") as (
        database,
        _fixture,
        authority,
        _enrollment,
    ):
        repository = module.ProductionRecoveryWorkRepository(database)
        work = _case_work(repository, authority, module)
        first_context = _read_context(repository, module, work)
        with psycopg.connect(database) as connection:
            database_now = connection.execute("SELECT clock_timestamp()").fetchone()[0]
        window = database_now - first_context.operation_started_at + timedelta(seconds=0.7)
        contract = module.ProductionProviderReadContract(
            provider_id=work.target_alias,
            exact_idempotency_lookup=True,
            authoritative_visibility_window=window,
        )
        context = _read_context(repository, module, work, visibility_window=window)

        class Target:
            def __init__(self):
                self.requests = []

            def read_operation(self, request):
                self.requests.append(request)
                if len(self.requests) == 1:
                    time.sleep(0.9)
                return _exact_observation(
                    module,
                    work,
                    context,
                    result="not_found",
                    observed_at=datetime.now(timezone.utc),
                )

        target = Target()
        coordinator = module.ProductionRecoveryCoordinator(
            repository,
            remote_target_factory=lambda _provider: target,
            authority=_RecoveryAuthority(module),
            provider_contracts={work.target_alias: contract},
        )
        preliminary = coordinator.reconcile(work.work_id)
        assert preliminary.result_class == "unknown"
        assert preliminary.remote_state == "not_found"

        authoritative = coordinator.reconcile(work.work_id)
        assert authoritative.result_class == "authoritative_no_effect"
        assert preliminary.event_id != authoritative.event_id
        assert len(target.requests) == 2


def test_pre_admission_intent_never_constructs_remote_client():
    schedule_tests = import_module("test_p1_production_schedule_fencing")
    module = import_module("salience.cycles.production_recovery")
    with schedule_tests._production_enrollment_fixture("task7_no_remote_client_") as (
        database,
        control,
        binding,
        key,
        _authority,
        _route_authority,
    ):
        _enroll_production_intent(control, binding, key)
        repository = module.ProductionRecoveryWorkRepository(database)
        work = repository.bind_pre_admission_intent(
            key.intent_id, policy=_recovery_policy(module)
        )
        factory_calls = []
        coordinator = module.ProductionRecoveryCoordinator(
            repository,
            remote_target_factory=lambda provider: factory_calls.append(provider),
            authority=_RecoveryAuthority(module),
            provider_contracts={},
        )
        with pytest.raises(module.RecoveryProviderReadDenied):
            coordinator.reconcile(work.work_id)
        assert factory_calls == []


def test_no_effect_command_binds_same_operation_key_and_revision():
    module = import_module("salience.cycles.production_recovery")
    with _production_recovery_operation("task7_no_effect_command_") as (
        database,
        _fixture,
        authority,
        _enrollment,
    ):
        repository = module.ProductionRecoveryWorkRepository(database)
        work = _case_work(repository, authority, module)
        context = _read_context(repository, module, work)
        contract = context.provider_contract
        observation = _exact_observation(
            module, work, context, result="not_found"
        )
        coordinator = module.ProductionRecoveryCoordinator(
            repository,
            remote_target_factory=lambda _provider: _ReadTarget(observation),
            authority=_RecoveryAuthority(module),
            provider_contracts={work.target_alias: contract},
        )
        observed = coordinator.reconcile(work.work_id)
        command = module.NoEffectGenerationReissueCommand(
            work_id=work.work_id,
            case_id=work.case_id,
            expected_case_revision=1,
            observation_event_id=observed.event_id,
            observation_sha256=observed.payload_sha256,
            operation_id=work.operation_id,
            request_fingerprint=work.request_fingerprint,
            original_idempotency_key=context.idempotency_key,
            stage_decision_id=uuid4(),
            policy_decision_id=uuid4(),
            workspace_stop_revision=3,
            goal_stop_revision=5,
            route_decision_id=uuid4(),
            idempotency_key="reissue:" + str(work.case_id) + ":1",
        )
        controller = module.ProductionRecoveryCaseController(
            repository,
            authority=_RecoveryAuthority(module),
            commands=object(),
        )
        receipt = controller.authorize_no_effect_generation_reissue(command)
        replay = controller.authorize_no_effect_generation_reissue(command)
        assert receipt.event_id == replay.event_id
        assert receipt.operation_id == work.operation_id
        assert receipt.idempotency_key == context.idempotency_key
        assert receipt.case_revision == 1
        assert receipt.command_reference.stage_decision_id == command.stage_decision_id
        assert receipt.command_reference.policy_decision_id == command.policy_decision_id
        assert receipt.command_reference.workspace_stop_revision == 3
        assert receipt.command_reference.goal_stop_revision == 5
        assert receipt.command_reference.route_decision_id == command.route_decision_id
        changed = {
            field.name: (
                "changed-operation-key"
                if field.name == "original_idempotency_key"
                else getattr(command, field.name)
            )
            for field in fields(command)
        }
        with pytest.raises(ValueError):
            controller.authorize_no_effect_generation_reissue(
                module.NoEffectGenerationReissueCommand(**changed)
            )

        class SplitStopAuthority(_RecoveryAuthority):
            def authorize_no_effect_reissue(self, _work, proposed, _observation):
                return module.ProductionNoEffectAuthorization(
                    stage_decision_id=proposed.stage_decision_id,
                    policy_decision_id=proposed.policy_decision_id,
                    workspace_stop_revision=3,
                    goal_stop_revision=5,
                    route_decision_id=proposed.route_decision_id,
                )

        for field_name, changed_revision in (
            ("workspace_stop_revision", 4),
            ("goal_stop_revision", 6),
        ):
            fields_changed = {
                field.name: getattr(command, field.name) for field in fields(command)
            }
            fields_changed[field_name] = changed_revision
            fields_changed["idempotency_key"] += ":" + field_name
            mismatched = module.NoEffectGenerationReissueCommand(**fields_changed)
            mismatch_controller = module.ProductionRecoveryCaseController(
                repository,
                authority=SplitStopAuthority(module),
                commands=object(),
            )
            with pytest.raises(PermissionError, match="stop, or route"):
                mismatch_controller.authorize_no_effect_generation_reissue(mismatched)

        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT state,revision FROM v4_recovery_cases WHERE id=%s",
                (work.case_id,),
            ).fetchone() == ("reconciling", 1)
            assert connection.execute(
                "SELECT count(*) FROM v4_production_current_dispatch WHERE operation_id=%s",
                (work.operation_id,),
            ).fetchone()[0] == 1
            assert connection.execute(
                """SELECT count(*) FROM v4_production_recovery_events
                   WHERE work_id=%s AND event_kind='same_operation_no_effect_reissue'""",
                (work.work_id,),
            ).fetchone()[0] == 1
            event = connection.execute(
                """SELECT payload FROM v4_production_recovery_events
                   WHERE event_id=%s""",
                (receipt.event_id,),
            ).fetchone()[0]
            assert event["stage_decision_id"] == str(command.stage_decision_id)
            assert event["policy_decision_id"] == str(command.policy_decision_id)
            assert event["workspace_stop_revision"] == 3
            assert event["goal_stop_revision"] == 5
            assert event["route_decision_id"] == str(command.route_decision_id)
