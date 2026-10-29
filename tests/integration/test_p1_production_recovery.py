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
def _production_recovery_operation(prefix="task7_recovery_", *, task8_dispatch=False):
    migration_tests = import_module("test_v4_migrations")
    source = os.environ["TEST_DATABASE_URL"]
    name, database, _before, fixture, authority = (
        migration_tests._prepare_0047_0046_database(prefix)
    )
    try:
        upgraded = migration_tests._migration(
            database,
            "upgrade",
            (
                "0050_p1_production_dispatch_reissue"
                if task8_dispatch
                else "0049_p1_production_recovery_decision_evidence"
            ),
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


def _alert_request(
    repository,
    module,
    work,
    policy,
    *,
    maximum_attempts=2,
    claim_ttl=timedelta(seconds=15),
):
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
            maximum_attempts=maximum_attempts,
            retry_intervals=tuple(
                timedelta(seconds=1) for _ in range(maximum_attempts - 1)
            ),
            claim_ttl=claim_ttl,
            owner_response_window=timedelta(minutes=2),
            escalation_after=(timedelta(minutes=2),),
        ),
        policy_decision_id=uuid4(),
    )


def _alert_event_time(database, request):
    with psycopg.connect(database) as connection:
        return connection.execute(
            "SELECT occurred_at FROM v4_production_recovery_events WHERE event_id=%s",
            (request.alert_event_id,),
        ).fetchone()[0]


@contextmanager
def _crashed_alert_retry_case(prefix):
    module = import_module("salience.cycles.production_recovery")
    schedule_tests = import_module("test_p1_production_schedule_fencing")
    with schedule_tests._production_enrollment_fixture(prefix) as (
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
        authority = _RecoveryAuthority(module)

        def crash_after_retry_is_persisted(_delay):
            raise RuntimeError("simulated process crash after retry persistence")

        first_process = module.ProductionRecoveryAlerts(
            repository,
            target=channel,
            authority=authority,
            recipient_profile=request.recipient_profile,
            retry_policy=request.retry_policy,
            wait=crash_after_retry_is_persisted,
            now=repository._now,
            schedule_hold_target=lambda *_args: None,
        )
        with pytest.raises(RuntimeError, match="simulated process crash"):
            first_process.send(request)
        with psycopg.connect(database, row_factory=dict_row) as connection:
            retry_event = connection.execute(
                """SELECT * FROM v4_production_recovery_events
                   WHERE work_id=%s AND event_kind='retry_scheduled'
                     AND payload->>'event_key'=%s""",
                (work.work_id, request.idempotency_key),
            ).fetchone()
        assert retry_event is not None
        yield {
            "module": module,
            "database": database,
            "repository": repository,
            "work": work,
            "request": request,
            "policy": policy,
            "channel": channel,
            "authority": authority,
            "retry_event": retry_event,
        }


class _SimulatedAlertProcessCrash(BaseException):
    pass


class _CrashBeforeProviderSendTarget(_AlertTarget):
    def __init__(self, module, *, outcome="accepted", lookup_error=False):
        super().__init__(module)
        self.outcomes = list(outcome) if isinstance(outcome, (tuple, list)) else [outcome]
        self.lookup_error = lookup_error
        self.crash_before_send = True
        self.lookup_calls = []

    def send(self, request, idempotency_key):
        self.calls.append((request, idempotency_key))
        if self.crash_before_send:
            self.crash_before_send = False
            raise _SimulatedAlertProcessCrash("crash after committed attempt event")
        if idempotency_key in self.receipts:
            return self.receipts[idempotency_key]
        outcome = self.outcomes.pop(0) if self.outcomes else "accepted"
        if outcome == "unavailable":
            return None
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
        self.lookup_calls.append(idempotency_key)
        if self.lookup_error:
            raise TimeoutError("synthetic alert lookup outage")
        return self.receipts.get(idempotency_key)


@contextmanager
def _crashed_alert_attempt_case(prefix, *, outcome="accepted", lookup_error=False,
                                maximum_attempts=2,
                                owner_unavailable_before_attempt=False):
    module = import_module("salience.cycles.production_recovery")
    schedule_tests = import_module("test_p1_production_schedule_fencing")
    with schedule_tests._production_enrollment_fixture(prefix) as (
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
        request = _alert_request(
            repository,
            module,
            work,
            policy,
            maximum_attempts=maximum_attempts,
            claim_ttl=timedelta(seconds=2),
        )
        target = _CrashBeforeProviderSendTarget(
            module, outcome=outcome, lookup_error=lookup_error
        )
        authority = _RecoveryAuthority(module)
        if owner_unavailable_before_attempt:
            with psycopg.connect(database) as connection:
                connection.execute(
                    "UPDATE identity_subjects SET enabled=false WHERE id=%s",
                    (work.owner_id,),
                )
        first_process = module.ProductionRecoveryAlerts(
            repository,
            target=target,
            authority=authority,
            recipient_profile=request.recipient_profile,
            retry_policy=request.retry_policy,
            wait=lambda _delay: pytest.fail("first attempt must not wait"),
            now=repository._now,
            schedule_hold_target=lambda *_args: None,
        )
        with pytest.raises(_SimulatedAlertProcessCrash, match="committed attempt"):
            first_process.send(request)
        with psycopg.connect(database, row_factory=dict_row) as connection:
            attempt = connection.execute(
                """SELECT * FROM v4_production_recovery_events
                   WHERE work_id=%s AND event_kind='delivery_attempt'
                     AND payload->>'event_key'=%s""",
                (work.work_id, request.idempotency_key),
            ).fetchone()
            assert connection.execute(
                """SELECT count(*) FROM v4_production_recovery_events
                   WHERE work_id=%s AND event_kind='retry_scheduled'""",
                (work.work_id,),
            ).fetchone()["count"] == 0
        assert attempt is not None
        assert len(target.calls) == 1
        yield {
            "module": module,
            "database": database,
            "repository": repository,
            "work": work,
            "request": request,
            "target": target,
            "authority": authority,
            "attempt": attempt,
        }


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
            wait=lambda delay: time.sleep(delay.total_seconds()),
            now=repository._now,
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


def test_alert_restart_waits_for_persisted_retry_at_before_sending():
    module = import_module("salience.cycles.production_recovery")
    with _crashed_alert_retry_case("task12_alert_retry_wait_") as case:
        request = case["request"]
        retry_at = datetime.fromisoformat(case["retry_event"]["payload"]["retry_at"])
        clock_value = [retry_at - timedelta(milliseconds=500)]
        observed_delays = []

        def stop_if_still_early(delay):
            observed_delays.append(delay)
            raise RuntimeError("restart attempted to wait until persisted retry time")

        early_restart = module.ProductionRecoveryAlerts(
            case["repository"],
            target=case["channel"],
            authority=case["authority"],
            recipient_profile=request.recipient_profile,
            retry_policy=request.retry_policy,
            wait=stop_if_still_early,
            now=lambda: clock_value[0],
            schedule_hold_target=lambda *_args: None,
        )
        with pytest.raises(RuntimeError, match="persisted retry time"):
            early_restart.send(request)
        assert observed_delays == [timedelta(milliseconds=500)]
        assert len(case["channel"].calls) == 1

        clock_value[0] = retry_at
        due_restart = module.ProductionRecoveryAlerts(
            case["repository"],
            target=case["channel"],
            authority=case["authority"],
            recipient_profile=request.recipient_profile,
            retry_policy=request.retry_policy,
            wait=lambda _delay: pytest.fail("due retry must not wait again"),
            now=lambda: clock_value[0],
            schedule_hold_target=lambda *_args: None,
        )
        receipt = due_restart.send(request)
        assert receipt.status == "accepted"
        assert len(case["channel"].calls) == 2
        assert {call[1] for call in case["channel"].calls} == {
            request.idempotency_key
        }


@pytest.mark.parametrize("lookup_error", [False, True], ids=["no-receipt", "lookup-outage"])
def test_alert_restart_after_attempt_commit_waits_for_claim_ttl_and_reuses_attempt(
    lookup_error,
):
    module = import_module("salience.cycles.production_recovery")
    with _crashed_alert_attempt_case(
        f"task14_alert_attempt_ttl_{lookup_error}_", lookup_error=lookup_error
    ) as case:
        request = case["request"]
        attempt = case["attempt"]
        attempt_at = attempt["occurred_at"]
        claim_expires_at = attempt_at + request.retry_policy.claim_ttl
        clock_value = [claim_expires_at - timedelta(milliseconds=500)]
        observed_delays = []

        def stop_if_claim_is_active(delay):
            observed_delays.append(delay)
            raise RuntimeError("attempt claim TTL has not elapsed")

        early_restart = module.ProductionRecoveryAlerts(
            case["repository"],
            target=case["target"],
            authority=case["authority"],
            recipient_profile=request.recipient_profile,
            retry_policy=request.retry_policy,
            wait=stop_if_claim_is_active,
            now=lambda: clock_value[0],
            schedule_hold_target=lambda *_args: None,
        )
        with pytest.raises(RuntimeError, match="attempt claim TTL"):
            early_restart.send(request)
        assert observed_delays == [timedelta(milliseconds=500)]
        assert len(case["target"].calls) == 1

        clock_value[0] = claim_expires_at
        due_restart = module.ProductionRecoveryAlerts(
            case["repository"],
            target=case["target"],
            authority=case["authority"],
            recipient_profile=request.recipient_profile,
            retry_policy=request.retry_policy,
            wait=lambda _delay: pytest.fail("expired attempt claim must not wait"),
            now=lambda: clock_value[0],
            schedule_hold_target=lambda *_args: None,
        )
        receipt = due_restart.send(request)

        assert receipt.status == "accepted"
        assert len(case["target"].calls) == 2
        assert {call[1] for call in case["target"].calls} == {
            request.idempotency_key
        }
        assert len(case["authority"].calls) >= 3
        with psycopg.connect(case["database"]) as connection:
            assert connection.execute(
                """SELECT count(*) FROM v4_production_recovery_events
                   WHERE work_id=%s AND event_kind='delivery_attempt'""",
                (case["work"].work_id,),
            ).fetchone()[0] == 1
            assert connection.execute(
                """SELECT count(*) FROM v4_production_recovery_events
                   WHERE work_id=%s AND event_kind='retry_scheduled'""",
                (case["work"].work_id,),
            ).fetchone()[0] == 0
            assert connection.execute(
                """SELECT count(*) FROM v4_production_recovery_events
                   WHERE work_id=%s AND event_kind='delivery_receipt'""",
                (case["work"].work_id,),
            ).fetchone()[0] == 1


def test_alert_restart_reconciles_receipt_that_appears_during_claim_ttl():
    module = import_module("salience.cycles.production_recovery")
    with _crashed_alert_attempt_case("task14_alert_attempt_receipt_") as case:
        request = case["request"]
        case["target"].receipts[request.idempotency_key] = module.ProductionAlertReceipt(
            alert_event_id=request.alert_event_id,
            idempotency_key=request.idempotency_key,
            provider_message_id="message-after-crash",
            status="accepted",
            accepted_at=case["attempt"]["occurred_at"] + timedelta(seconds=1),
        )
        restart = module.ProductionRecoveryAlerts(
            case["repository"],
            target=case["target"],
            authority=case["authority"],
            recipient_profile=request.recipient_profile,
            retry_policy=request.retry_policy,
            wait=lambda _delay: pytest.fail("existing receipt must be reconciled first"),
            now=lambda: case["attempt"]["occurred_at"],
            schedule_hold_target=lambda *_args: None,
        )

        receipt = restart.send(request)

        assert receipt.status == "accepted"
        assert receipt.provider_message_id == "message-after-crash"
        assert len(case["target"].calls) == 1
        with psycopg.connect(case["database"]) as connection:
            assert connection.execute(
                """SELECT count(*) FROM v4_production_recovery_events
                   WHERE work_id=%s AND event_kind='delivery_attempt'""",
                (case["work"].work_id,),
            ).fetchone()[0] == 1
            assert connection.execute(
                """SELECT count(*) FROM v4_production_recovery_events
                   WHERE work_id=%s AND event_kind='delivery_receipt'""",
                (case["work"].work_id,),
            ).fetchone()[0] == 1


def test_alert_restart_waits_for_claim_ttl_when_latest_retry_record_is_missing():
    module = import_module("salience.cycles.production_recovery")
    with _crashed_alert_retry_case("task14_alert_missing_latest_retry_") as case:
        request = case["request"]
        with psycopg.connect(case["database"], row_factory=dict_row) as connection:
            attempt = connection.execute(
                """SELECT * FROM v4_production_recovery_events
                   WHERE work_id=%s AND event_kind='delivery_attempt'
                     AND payload->>'event_key'=%s""",
                (case["work"].work_id, request.idempotency_key),
            ).fetchone()
            assert attempt is not None
            connection.execute("SET session_replication_role=replica")
            connection.execute(
                "DELETE FROM v4_production_recovery_events WHERE event_id=%s",
                (case["retry_event"]["event_id"],),
            )
            connection.execute("SET session_replication_role=origin")

        claim_expires_at = attempt["occurred_at"] + request.retry_policy.claim_ttl
        clock_value = [claim_expires_at - timedelta(milliseconds=500)]
        observed_delays = []

        def stop_if_claim_is_active(delay):
            observed_delays.append(delay)
            raise RuntimeError("attempt claim TTL has not elapsed")

        early_restart = module.ProductionRecoveryAlerts(
            case["repository"],
            target=case["channel"],
            authority=case["authority"],
            recipient_profile=request.recipient_profile,
            retry_policy=request.retry_policy,
            wait=stop_if_claim_is_active,
            now=lambda: clock_value[0],
            schedule_hold_target=lambda *_args: None,
        )
        with pytest.raises(RuntimeError, match="attempt claim TTL"):
            early_restart.send(request)
        assert observed_delays == [timedelta(milliseconds=500)]
        assert len(case["channel"].calls) == 1

        clock_value[0] = claim_expires_at
        due_restart = module.ProductionRecoveryAlerts(
            case["repository"],
            target=case["channel"],
            authority=case["authority"],
            recipient_profile=request.recipient_profile,
            retry_policy=request.retry_policy,
            wait=lambda _delay: pytest.fail("expired attempt claim must not wait"),
            now=lambda: clock_value[0],
            schedule_hold_target=lambda *_args: None,
        )

        receipt = due_restart.send(request)

        assert receipt.status == "accepted"
        assert len(case["channel"].calls) == 2
        assert {call[1] for call in case["channel"].calls} == {
            request.idempotency_key
        }
        with psycopg.connect(case["database"]) as connection:
            assert connection.execute(
                """SELECT count(*) FROM v4_production_recovery_events
                   WHERE work_id=%s AND event_kind='delivery_attempt'""",
                (case["work"].work_id,),
            ).fetchone()[0] == 1
            assert connection.execute(
                """SELECT count(*) FROM v4_production_recovery_events
                   WHERE work_id=%s AND event_kind='retry_scheduled'""",
                (case["work"].work_id,),
            ).fetchone()[0] == 0


def test_alert_restart_after_attempt_crash_preserves_bounded_retry_schedule():
    module = import_module("salience.cycles.production_recovery")
    with _crashed_alert_attempt_case(
        "task14_alert_attempt_retry_schedule_",
        outcome=("unavailable", "accepted"),
    ) as case:
        request = case["request"]
        clock_value = [
            case["attempt"]["occurred_at"] + request.retry_policy.claim_ttl
        ]
        restart = module.ProductionRecoveryAlerts(
            case["repository"],
            target=case["target"],
            authority=case["authority"],
            recipient_profile=request.recipient_profile,
            retry_policy=request.retry_policy,
            wait=lambda _delay: None,
            now=lambda: clock_value[0],
            schedule_hold_target=lambda *_args: None,
        )

        receipt = restart.send(request)

        assert receipt.status == "accepted"
        assert len(case["target"].calls) == 3
        assert {call[1] for call in case["target"].calls} == {
            request.idempotency_key
        }
        with psycopg.connect(case["database"]) as connection:
            assert connection.execute(
                """SELECT count(*) FROM v4_production_recovery_events
                   WHERE work_id=%s AND event_kind='delivery_attempt'""",
                (case["work"].work_id,),
            ).fetchone()[0] == 2
            assert connection.execute(
                """SELECT count(*) FROM v4_production_recovery_events
                   WHERE work_id=%s AND event_kind='retry_scheduled'""",
                (case["work"].work_id,),
            ).fetchone()[0] == 1


def test_alert_restart_holds_after_unavailable_final_attempt_reuses_committed_attempt():
    module = import_module("salience.cycles.production_recovery")
    with _crashed_alert_attempt_case(
        "task14_alert_attempt_held_",
        outcome="unavailable",
        maximum_attempts=1,
    ) as case:
        request = case["request"]
        clock_value = [
            case["attempt"]["occurred_at"] + request.retry_policy.claim_ttl
        ]
        restart = module.ProductionRecoveryAlerts(
            case["repository"],
            target=case["target"],
            authority=case["authority"],
            recipient_profile=request.recipient_profile,
            retry_policy=request.retry_policy,
            wait=lambda _delay: pytest.fail("expired final attempt claim must not wait"),
            now=lambda: clock_value[0],
            schedule_hold_target=lambda *_args: None,
        )

        receipt = restart.send(request)

        assert receipt.status == "held"
        assert len(case["target"].calls) == 2
        assert {call[1] for call in case["target"].calls} == {
            request.idempotency_key
        }
        with psycopg.connect(case["database"]) as connection:
            assert connection.execute(
                """SELECT count(*) FROM v4_production_recovery_events
                   WHERE work_id=%s AND event_kind='delivery_attempt'""",
                (case["work"].work_id,),
            ).fetchone()[0] == 1
            assert connection.execute(
                """SELECT count(*) FROM v4_production_recovery_events
                   WHERE work_id=%s AND event_kind='retry_scheduled'""",
                (case["work"].work_id,),
            ).fetchone()[0] == 0
            assert connection.execute(
                """SELECT count(*) FROM v4_production_recovery_events
                   WHERE work_id=%s AND event_kind='held_conflict'""",
                (case["work"].work_id,),
            ).fetchone()[0] == 1


def test_alert_restart_rechecks_unavailable_owner_after_committed_attempt_claim_ttl():
    from dataclasses import replace

    module = import_module("salience.cycles.production_recovery")
    with _crashed_alert_attempt_case("task14_alert_owner_ttl_") as case:
        request = case["request"]
        request = replace(
            request,
            recipient_profile=replace(
                request.recipient_profile, on_call_group_ids=()
            ),
        )
        with psycopg.connect(case["database"]) as connection:
            connection.execute(
                "UPDATE identity_subjects SET enabled=false WHERE id=%s",
                (case["work"].owner_id,),
            )

        claim_expires_at = case["attempt"]["occurred_at"] + request.retry_policy.claim_ttl
        clock_value = [case["attempt"]["occurred_at"]]
        observed_delays = []

        def advance_to_claim_expiry(delay):
            observed_delays.append(delay)
            clock_value[0] += delay

        restart = module.ProductionRecoveryAlerts(
            case["repository"],
            target=case["target"],
            authority=case["authority"],
            recipient_profile=request.recipient_profile,
            retry_policy=request.retry_policy,
            wait=advance_to_claim_expiry,
            now=lambda: clock_value[0],
            schedule_hold_target=lambda *_args: None,
        )

        receipt = restart.send(request)

        assert receipt.status == "held"
        assert observed_delays == [request.retry_policy.claim_ttl]
        assert clock_value[0] >= claim_expires_at
        assert len(case["target"].calls) == 1
        with psycopg.connect(case["database"]) as connection:
            assert connection.execute(
                """SELECT count(*) FROM v4_production_recovery_events
                   WHERE work_id=%s AND event_kind='delivery_attempt'""",
                (case["work"].work_id,),
            ).fetchone()[0] == 1
            assert connection.execute(
                """SELECT count(*) FROM v4_production_recovery_events
                   WHERE work_id=%s AND event_kind='held_conflict'""",
                (case["work"].work_id,),
            ).fetchone()[0] == 1


def test_alert_restart_preserves_committed_on_call_tier_when_owner_returns():
    module = import_module("salience.cycles.production_recovery")
    with _crashed_alert_attempt_case(
        "task14_alert_on_call_tier_resume_",
        owner_unavailable_before_attempt=True,
    ) as case:
        request = case["request"]
        assert case["target"].calls[0][0].recipient_tier == "on_call"
        with psycopg.connect(case["database"]) as connection:
            connection.execute(
                "UPDATE identity_subjects SET enabled=true WHERE id=%s",
                (case["work"].owner_id,),
            )

        claim_expires_at = case["attempt"]["occurred_at"] + request.retry_policy.claim_ttl
        clock_value = [claim_expires_at]
        authorized_tiers = []
        authorized_times = []
        authorize_alert = case["authority"].authorize_alert

        def track_authorization(work, alert):
            authorized_tiers.append(alert.recipient_tier)
            authorized_times.append(clock_value[0])
            return authorize_alert(work, alert)

        case["authority"].authorize_alert = track_authorization
        restart = module.ProductionRecoveryAlerts(
            case["repository"],
            target=case["target"],
            authority=case["authority"],
            recipient_profile=request.recipient_profile,
            retry_policy=request.retry_policy,
            wait=lambda _delay: pytest.fail("expired attempt claim must not wait"),
            now=lambda: clock_value[0],
            schedule_hold_target=lambda *_args: None,
        )

        receipt = restart.send(request)

        assert receipt.status == "accepted"
        assert authorized_tiers == ["on_call"]
        assert authorized_times == [claim_expires_at]
        assert [call[0].recipient_tier for call in case["target"].calls] == [
            "on_call",
            "on_call",
        ]
        assert {call[1] for call in case["target"].calls} == {
            request.idempotency_key
        }
        with psycopg.connect(case["database"]) as connection:
            assert connection.execute(
                """SELECT count(*) FROM v4_production_recovery_events
                   WHERE work_id=%s AND event_kind='delivery_attempt'""",
                (case["work"].work_id,),
            ).fetchone()[0] == 1


def test_alert_restart_changes_owner_tier_only_at_authorized_retry_boundary():
    module = import_module("salience.cycles.production_recovery")
    with _crashed_alert_attempt_case("task14_alert_owner_tier_retry_") as case:
        request = case["request"]
        with psycopg.connect(case["database"]) as connection:
            connection.execute(
                "UPDATE identity_subjects SET enabled=false WHERE id=%s",
                (case["work"].owner_id,),
            )

        claim_expires_at = case["attempt"]["occurred_at"] + request.retry_policy.claim_ttl
        clock_value = [case["attempt"]["occurred_at"]]
        waits = []
        authorized_tiers = []
        authorized_times = []
        authorize_alert = case["authority"].authorize_alert

        def advance_clock(delay):
            waits.append(delay)
            clock_value[0] += delay

        def track_authorization(work, alert):
            authorized_tiers.append(alert.recipient_tier)
            authorized_times.append(clock_value[0])
            return authorize_alert(work, alert)

        case["authority"].authorize_alert = track_authorization
        restart = module.ProductionRecoveryAlerts(
            case["repository"],
            target=case["target"],
            authority=case["authority"],
            recipient_profile=request.recipient_profile,
            retry_policy=request.retry_policy,
            wait=advance_clock,
            now=lambda: clock_value[0],
            schedule_hold_target=lambda *_args: None,
        )

        receipt = restart.send(request)

        assert receipt.status == "accepted"
        assert waits[0] == request.retry_policy.claim_ttl
        assert authorized_tiers == ["owner", "on_call"]
        assert authorized_times[0] >= claim_expires_at
        assert [call[0].recipient_tier for call in case["target"].calls] == [
            "owner",
            "on_call",
        ]
        assert {call[1] for call in case["target"].calls} == {
            request.idempotency_key
        }
        with psycopg.connect(case["database"], row_factory=dict_row) as connection:
            attempt_events = connection.execute(
                """SELECT event_key,payload,policy_decision_id,occurred_at
                   FROM v4_production_recovery_events
                   WHERE work_id=%s AND event_kind='delivery_attempt'
                   ORDER BY occurred_at,event_id""",
                (case["work"].work_id,),
            ).fetchall()
            retry_event = connection.execute(
                """SELECT * FROM v4_production_recovery_events
                   WHERE work_id=%s AND event_kind='retry_scheduled'""",
                (case["work"].work_id,),
            ).fetchone()
            owner_event = connection.execute(
                """SELECT * FROM v4_production_recovery_events
                   WHERE work_id=%s AND event_kind='owner_unavailable'""",
                (case["work"].work_id,),
            ).fetchone()
            assert len(attempt_events) == 2
            assert [event["event_key"] for event in attempt_events] == [
                f"delivery-attempt:{request.alert_event_id}:1",
                f"delivery-attempt:{request.alert_event_id}:2",
            ]
            assert "owner_id" in attempt_events[0]["payload"]
            assert "owner_id" not in attempt_events[1]["payload"]
            assert retry_event is not None
            assert retry_event["payload"]["attempt"] == 2
            assert "owner_id" not in retry_event["payload"]
            assert authorized_times[1] >= datetime.fromisoformat(
                retry_event["payload"]["retry_at"]
            )
            assert retry_event["policy_decision_id"] == attempt_events[0]["policy_decision_id"]
            assert owner_event is not None
            assert owner_event["payload"]["status"] == "owner_unavailable"
            assert owner_event["payload"]["error_code"] == "owner_unavailable_escalated"
            assert owner_event["occurred_at"] <= retry_event["occurred_at"]
            assert retry_event["occurred_at"] <= attempt_events[1]["occurred_at"]


@pytest.mark.parametrize("hold_reason", ["final_attempt", "no_backup"])
def test_alert_restart_holds_instead_of_changing_pending_owner_attempt_tier(hold_reason):
    from dataclasses import replace

    module = import_module("salience.cycles.production_recovery")
    maximum_attempts = 1 if hold_reason == "final_attempt" else 2
    with _crashed_alert_attempt_case(
        f"task14_alert_owner_tier_hold_{hold_reason}_",
        maximum_attempts=maximum_attempts,
    ) as case:
        request = case["request"]
        if hold_reason == "no_backup":
            request = replace(
                request,
                recipient_profile=replace(
                    request.recipient_profile, on_call_group_ids=()
                ),
            )
        with psycopg.connect(case["database"]) as connection:
            connection.execute(
                "UPDATE identity_subjects SET enabled=false WHERE id=%s",
                (case["work"].owner_id,),
            )

        claim_expires_at = case["attempt"]["occurred_at"] + request.retry_policy.claim_ttl
        clock_value = [case["attempt"]["occurred_at"]]
        waits = []

        def advance_clock(delay):
            waits.append(delay)
            clock_value[0] += delay

        restart = module.ProductionRecoveryAlerts(
            case["repository"],
            target=case["target"],
            authority=case["authority"],
            recipient_profile=request.recipient_profile,
            retry_policy=request.retry_policy,
            wait=advance_clock,
            now=lambda: clock_value[0],
            schedule_hold_target=lambda *_args: None,
        )

        receipt = restart.send(request)

        assert receipt.status == "held"
        assert waits == [request.retry_policy.claim_ttl]
        assert clock_value[0] >= claim_expires_at
        assert [call[0].recipient_tier for call in case["target"].calls] == ["owner"]
        assert {call[1] for call in case["target"].calls} == {
            request.idempotency_key
        }
        with psycopg.connect(case["database"]) as connection:
            assert connection.execute(
                """SELECT count(*) FROM v4_production_recovery_events
                   WHERE work_id=%s AND event_kind='delivery_attempt'""",
                (case["work"].work_id,),
            ).fetchone()[0] == 1
            assert connection.execute(
                """SELECT count(*) FROM v4_production_recovery_events
                   WHERE work_id=%s AND event_kind='retry_scheduled'""",
                (case["work"].work_id,),
            ).fetchone()[0] == 0
            assert connection.execute(
                """SELECT count(*) FROM v4_production_recovery_events
                   WHERE work_id=%s AND event_kind='owner_unavailable'""",
                (case["work"].work_id,),
            ).fetchone()[0] == 1
            assert connection.execute(
                """SELECT count(*) FROM v4_production_recovery_events
                   WHERE work_id=%s AND event_kind='held_conflict'""",
                (case["work"].work_id,),
            ).fetchone()[0] == 1


@pytest.mark.parametrize(
    "corruption",
    [
        "missing_prior_retry",
        "malformed_retry_at",
        "conflicting",
        "policy_mismatch",
        "unbacked_tier_transition",
    ],
)
def test_alert_restart_rejects_invalid_persisted_retry_evidence(corruption):
    module = import_module("salience.cycles.production_recovery")
    with _crashed_alert_retry_case(f"task12_alert_retry_{corruption}_") as case:
        event = case["retry_event"]
        repository = case["repository"]
        work = case["work"]
        request = case["request"]
        with psycopg.connect(case["database"], row_factory=dict_row) as connection:
            if corruption == "missing_prior_retry":
                attempt = connection.execute(
                    """SELECT * FROM v4_production_recovery_events
                       WHERE work_id=%s AND event_kind='delivery_attempt'
                         AND payload->>'event_key'=%s""",
                    (work.work_id, request.idempotency_key),
                ).fetchone()
                assert attempt is not None
                second_attempt_payload = dict(attempt["payload"])
                second_attempt_payload["attempt"] = 2
                with connection.transaction():
                    repository._append_event(
                        connection,
                        work,
                        event_key=f"delivery-attempt:{request.alert_event_id}:2",
                        event_kind="delivery_attempt",
                        policy_decision_id=attempt["policy_decision_id"],
                        payload=second_attempt_payload,
                    )
                connection.execute("SET session_replication_role=replica")
                connection.execute(
                    "DELETE FROM v4_production_recovery_events WHERE event_id=%s",
                    (event["event_id"],),
                )
                connection.execute("SET session_replication_role=origin")
            elif corruption == "unbacked_tier_transition":
                connection.execute("SET session_replication_role=replica")
                connection.execute(
                    """UPDATE v4_production_recovery_events
                       SET payload=payload-'owner_id',
                           payload_sha256=encode(public.digest(
                               (payload-'owner_id')::text,'sha256'),'hex')
                       WHERE event_id=%s""",
                    (event["event_id"],),
                )
                connection.execute("SET session_replication_role=origin")
            elif corruption in {"malformed_retry_at", "policy_mismatch"}:
                path = "{retry_at}" if corruption == "malformed_retry_at" else "{policy_revision}"
                value = "not-an-instant" if corruption == "malformed_retry_at" else "999"
                connection.execute("SET session_replication_role=replica")
                connection.execute(
                    """UPDATE v4_production_recovery_events
                       SET payload=jsonb_set(payload,%s,to_jsonb(%s::text)),
                           payload_sha256=encode(public.digest(
                               jsonb_set(payload,%s,to_jsonb(%s::text))::text,
                               'sha256'),'hex')
                       WHERE event_id=%s""",
                    (path, value, path, value, event["event_id"]),
                )
                connection.execute("SET session_replication_role=origin")
            else:
                existing = connection.execute(
                    "SELECT policy_decision_id FROM v4_production_recovery_events WHERE event_id=%s",
                    (event["event_id"],),
                ).fetchone()["policy_decision_id"]
                with connection.transaction():
                    repository._append_event(
                        connection,
                        work,
                        event_key=event["event_key"] + ":conflict",
                        event_kind="retry_scheduled",
                        policy_decision_id=existing,
                        payload=event["payload"],
                    )

        retry_at = datetime.fromisoformat(event["payload"]["retry_at"])
        restart = module.ProductionRecoveryAlerts(
            repository,
            target=case["channel"],
            authority=case["authority"],
            recipient_profile=request.recipient_profile,
            retry_policy=request.retry_policy,
            wait=lambda _delay: None,
            now=lambda: retry_at,
            schedule_hold_target=lambda *_args: None,
        )
        with pytest.raises((ValueError, module.RecoveryIdentityConflict)):
            restart.send(request)
        assert len(case["channel"].calls) == 1


def test_alert_request_rejects_escalation_before_policy_due():
    module = import_module("salience.cycles.production_recovery")
    schedule_tests = import_module("test_p1_production_schedule_fencing")
    with schedule_tests._production_enrollment_fixture("task7_alert_early_escalation_") as (
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
        initial = _alert_request(repository, module, work, policy)
        initial_at = _alert_event_time(database, initial)
        clock_value = [initial_at + timedelta(minutes=2) - timedelta(microseconds=1)]
        repository = module.ProductionRecoveryWorkRepository(
            database, now=lambda: clock_value[0]
        )
        event_key = f"alert-intent:{work.work_id}:{work.source_revision}:1"

        with pytest.raises(ValueError, match="not due"):
            repository.alert_request(
                work.work_id,
                severity="high",
                escalation_level=1,
                recipient_profile=initial.recipient_profile,
                retry_policy=initial.retry_policy,
                policy_decision_id=initial.policy_decision_id,
            )

        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT count(*) FROM v4_production_recovery_events WHERE event_key=%s",
                (event_key,),
            ).fetchone()[0] == 0


def test_due_escalation_routes_to_on_call_and_replays_same_delivery():
    from dataclasses import replace

    module = import_module("salience.cycles.production_recovery")
    schedule_tests = import_module("test_p1_production_schedule_fencing")
    with schedule_tests._production_enrollment_fixture("task7_alert_due_escalation_") as (
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
        initial = _alert_request(repository, module, work, policy)
        initial_at = _alert_event_time(database, initial)
        retry = replace(
            initial.retry_policy,
            escalation_after=(timedelta(minutes=2), timedelta(minutes=5)),
        )
        first_due_at = initial_at + timedelta(minutes=2)
        second_due_at = initial_at + timedelta(minutes=5)
        clock_value = [first_due_at]
        repository = module.ProductionRecoveryWorkRepository(
            database, now=lambda: clock_value[0]
        )
        first_request = repository.alert_request(
            work.work_id,
            severity="high",
            escalation_level=1,
            recipient_profile=initial.recipient_profile,
            retry_policy=retry,
            policy_decision_id=initial.policy_decision_id,
        )
        channel = _AlertTarget(module)
        alerts = module.ProductionRecoveryAlerts(
            repository,
            target=channel,
            authority=_RecoveryAuthority(module),
            recipient_profile=first_request.recipient_profile,
            retry_policy=first_request.retry_policy,
            wait=lambda _delay: None,
            now=repository._now,
            schedule_hold_target=lambda *_args: None,
        )

        first_receipt = alerts.send(first_request)
        clock_value[0] = second_due_at
        second_request = repository.alert_request(
            work.work_id,
            severity="high",
            escalation_level=2,
            recipient_profile=initial.recipient_profile,
            retry_policy=retry,
            policy_decision_id=initial.policy_decision_id,
        )

        with pytest.raises(module.RecoveryIdentityConflict, match="recipient tier"):
            alerts.send(replace(second_request, recipient_tier="owner"))
        clock_value[0] = second_due_at - timedelta(microseconds=1)
        with pytest.raises(ValueError, match="not due"):
            alerts.send(second_request)
        assert len(channel.calls) == 1
        clock_value[0] = second_due_at
        second_receipt = alerts.send(second_request)
        replay_request = repository.alert_request(
            work.work_id,
            severity="high",
            escalation_level=2,
            recipient_profile=initial.recipient_profile,
            retry_policy=retry,
            policy_decision_id=initial.policy_decision_id,
        )
        replay_receipt = alerts.send(replay_request)

        assert first_request.recipient_tier == "on_call"
        assert second_request.recipient_tier == "on_call"
        assert [call[0].recipient_tier for call in channel.calls] == [
            "on_call",
            "on_call",
        ]
        assert replay_request.alert_event_id == second_request.alert_event_id
        assert replay_receipt == second_receipt
        assert len(channel.calls) == 2
        assert channel.calls[0][1] == first_request.idempotency_key
        assert channel.calls[1][1] == second_request.idempotency_key
        assert first_receipt.status == second_receipt.status == "accepted"


def test_committed_escalation_receipt_replays_after_clock_rolls_back():
    from dataclasses import replace

    module = import_module("salience.cycles.production_recovery")
    schedule_tests = import_module("test_p1_production_schedule_fencing")
    with schedule_tests._production_enrollment_fixture("task7_alert_receipt_clock_rollback_") as (
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
        initial = _alert_request(repository, module, work, policy)
        initial_at = _alert_event_time(database, initial)
        retry = replace(
            initial.retry_policy,
            escalation_after=(timedelta(minutes=2),),
        )
        due_at = initial_at + timedelta(minutes=2)
        clock_value = [due_at]
        repository = module.ProductionRecoveryWorkRepository(
            database, now=lambda: clock_value[0]
        )
        request = repository.alert_request(
            work.work_id,
            severity="high",
            escalation_level=1,
            recipient_profile=initial.recipient_profile,
            retry_policy=retry,
            policy_decision_id=initial.policy_decision_id,
        )
        channel = _AlertTarget(module)
        alerts = module.ProductionRecoveryAlerts(
            repository,
            target=channel,
            authority=_RecoveryAuthority(module),
            recipient_profile=request.recipient_profile,
            retry_policy=request.retry_policy,
            wait=lambda _delay: None,
            now=repository._now,
            schedule_hold_target=lambda *_args: None,
        )

        receipt = alerts.send(request)
        clock_value[0] = due_at - timedelta(microseconds=1)

        replay = alerts.send(request)

        assert replay == receipt
        assert len(channel.calls) == 1


def test_provider_acceptance_reconciles_after_clock_rolls_back(monkeypatch):
    from dataclasses import replace

    module = import_module("salience.cycles.production_recovery")
    schedule_tests = import_module("test_p1_production_schedule_fencing")
    with schedule_tests._production_enrollment_fixture("task7_alert_provider_clock_rollback_") as (
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
        initial = _alert_request(repository, module, work, policy)
        initial_at = _alert_event_time(database, initial)
        retry = replace(
            initial.retry_policy,
            escalation_after=(timedelta(minutes=2),),
        )
        due_at = initial_at + timedelta(minutes=2)
        clock_value = [due_at]
        repository = module.ProductionRecoveryWorkRepository(
            database, now=lambda: clock_value[0]
        )
        request = repository.alert_request(
            work.work_id,
            severity="high",
            escalation_level=1,
            recipient_profile=initial.recipient_profile,
            retry_policy=retry,
            policy_decision_id=initial.policy_decision_id,
        )
        channel = _AlertTarget(module)
        alerts = module.ProductionRecoveryAlerts(
            repository,
            target=channel,
            authority=_RecoveryAuthority(module),
            recipient_profile=request.recipient_profile,
            retry_policy=request.retry_policy,
            wait=lambda _delay: None,
            now=repository._now,
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
        clock_value[0] = due_at - timedelta(microseconds=1)
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


def test_escalation_without_configured_on_call_group_is_rejected_before_intent():
    from dataclasses import replace

    module = import_module("salience.cycles.production_recovery")
    schedule_tests = import_module("test_p1_production_schedule_fencing")
    with schedule_tests._production_enrollment_fixture("task7_alert_no_on_call_") as (
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
        initial = _alert_request(repository, module, work, policy)
        profile_without_on_call = replace(
            initial.recipient_profile, on_call_group_ids=()
        )
        event_key = f"alert-intent:{work.work_id}:{work.source_revision}:1"

        with pytest.raises(ValueError, match="on-call"):
            repository.alert_request(
                work.work_id,
                severity="high",
                escalation_level=1,
                recipient_profile=profile_without_on_call,
                retry_policy=initial.retry_policy,
                policy_decision_id=initial.policy_decision_id,
            )

        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT count(*) FROM v4_production_recovery_events WHERE event_key=%s",
                (event_key,),
            ).fetchone()[0] == 0


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
            now=repository._now,
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
            now=repository._now,
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
            now=repository._now,
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


def test_0049_recovery_evidence_rejects_null_missing_wrong_type_and_malformed():
    module = import_module("salience.cycles.production_recovery")
    with _production_recovery_operation("task7_0049_evidence_types_") as (
        database,
        _fixture,
        authority,
        _enrollment,
    ):
        repository = module.ProductionRecoveryWorkRepository(database)
        work = _case_work(repository, authority, module)
        policy_decision_id = uuid4()
        missing = object()

        def insert_event(connection, event_kind, event_key, payload):
            encoded = Jsonb(payload)
            connection.execute(
                """INSERT INTO v4_production_recovery_events(
                           event_id,work_id,event_key,event_kind,actor_id,
                           policy_decision_id,payload,payload_sha256)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,
                          encode(public.digest(%s::jsonb::text,'sha256'),'hex'))""",
                (
                    uuid4(),
                    work.work_id,
                    event_key,
                    event_kind,
                    work.owner_id,
                    policy_decision_id,
                    encoded,
                    encoded,
                ),
            )

        def require_rejected(connection, event_kind, base, field, label, value):
            event_key = f"task7-0049:{label}:{uuid4()}"
            payload = dict(base)
            payload["event_key"] = event_key
            if value is missing:
                payload.pop(field, None)
            else:
                payload[field] = value
            with pytest.raises(psycopg.Error):
                with connection.transaction():
                    insert_event(connection, event_kind, event_key, payload)

        no_effect = {
            "stage_decision_id": str(uuid4()),
            "policy_decision_id": str(policy_decision_id),
            "workspace_stop_revision": 3,
            "goal_stop_revision": 5,
            "route_decision_id": str(uuid4()),
        }
        deadline_suspend = {
            "schema_version": "salience.p1-production-recovery-deadline.v1",
            "status": "deadline_suspend",
            "policy_decision_id": str(policy_decision_id),
            "route_decision_id": str(uuid4()),
            "command_id": str(uuid4()),
        }
        deadline_wake = {
            "schema_version": "salience.p1-production-recovery-deadline.v1",
            "status": "woken",
            "policy_decision_id": str(policy_decision_id),
            "route_decision_id": str(uuid4()),
        }
        uuid_evidence = (
            "stage_decision_id",
            "policy_decision_id",
            "route_decision_id",
        )
        stop_evidence = ("workspace_stop_revision", "goal_stop_revision")
        deadline_uuid_evidence = (
            "policy_decision_id",
            "route_decision_id",
            "command_id",
        )

        with psycopg.connect(database) as connection:
            # Valid controls preserve the exact expected JSON types. An intent
            # wake explicitly has no case command receipt, so its command_id
            # remains absent rather than JSON null.
            valid_no_effect_key = f"task7-0049:valid-no-effect:{uuid4()}"
            insert_event(
                connection,
                "same_operation_no_effect_reissue",
                valid_no_effect_key,
                {**no_effect, "event_key": valid_no_effect_key},
            )
            valid_deadline_key = f"task7-0049:valid-deadline:{uuid4()}"
            insert_event(
                connection,
                "escalation",
                valid_deadline_key,
                {**deadline_suspend, "event_key": valid_deadline_key},
            )
            valid_wake_key = f"task7-0049:valid-intent-wake:{uuid4()}"
            insert_event(
                connection,
                "escalation",
                valid_wake_key,
                {**deadline_wake, "event_key": valid_wake_key},
            )

            for field in uuid_evidence:
                for label, value in (
                    ("json-null", None),
                    ("missing", missing),
                    ("wrong-json-type", 17),
                    ("malformed-string", "not-a-uuid"),
                ):
                    require_rejected(
                        connection,
                        "same_operation_no_effect_reissue",
                        no_effect,
                        field,
                        f"no-effect-{field}-{label}",
                        value,
                    )
            for field in stop_evidence:
                for label, value in (
                    ("json-null", None),
                    ("missing", missing),
                    ("wrong-json-type", "3"),
                    ("malformed-string", "three"),
                    ("malformed-number", 3.5),
                ):
                    require_rejected(
                        connection,
                        "same_operation_no_effect_reissue",
                        no_effect,
                        field,
                        f"no-effect-{field}-{label}",
                        value,
                    )
            for label, value in (
                ("json-null", None),
                ("missing", missing),
                ("wrong-json-type", 17),
                ("mismatched-string", "other-event-key"),
            ):
                require_rejected(
                    connection,
                    "same_operation_no_effect_reissue",
                    no_effect,
                    "event_key",
                    f"no-effect-event-key-{label}",
                    value,
                )

            deadline_fields = {
                "schema_version": (
                    ("json-null", None),
                    ("missing", missing),
                    ("wrong-json-type", 17),
                    ("malformed-string", "wrong-schema"),
                ),
                "status": (
                    ("json-null", None),
                    ("missing", missing),
                    ("wrong-json-type", 17),
                    ("malformed-string", ""),
                ),
                "event_key": (
                    ("json-null", None),
                    ("missing", missing),
                    ("wrong-json-type", 17),
                    ("mismatched-string", "other-event-key"),
                ),
            }
            for field in deadline_uuid_evidence:
                deadline_fields[field] = (
                    ("json-null", None),
                    ("missing", missing),
                    ("wrong-json-type", 17),
                    ("malformed-string", "not-a-uuid"),
                )
            for field, variants in deadline_fields.items():
                for label, value in variants:
                    require_rejected(
                        connection,
                        "escalation",
                        deadline_suspend,
                        field,
                        f"deadline-{field}-{label}",
                        value,
                    )

            # An intent wake can omit a case-command receipt. A present JSON
            # null is never a valid command receipt.
            require_rejected(
                connection,
                "escalation",
                {**deadline_wake, "command_id": str(uuid4())},
                "command_id",
                "deadline-optional-command-id-json-null",
                None,
            )


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
    with _production_recovery_operation(
        "task7_no_effect_command_", task8_dispatch=True
    ) as (
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
            assert event["observation_event_id"] == str(command.observation_event_id)
            assert event["observation_sha256"] == command.observation_sha256


@pytest.mark.parametrize(
    "result_class,remote_state",
    (
        ("unknown", "unknown"),
        ("in_progress", "in_progress"),
        ("terminal_success", "succeeded"),
        ("terminal_success", "accepted"),
        ("unknown", "conflicting"),
    ),
)
def test_no_effect_command_rejects_superseded_remote_observation(
    result_class, remote_state
):
    module = import_module("salience.cycles.production_recovery")
    with _production_recovery_operation("task7_no_effect_superseded_") as (
        database,
        _fixture,
        authority,
        _enrollment,
    ):
        repository = module.ProductionRecoveryWorkRepository(database)
        work = _case_work(repository, authority, module)
        context = _read_context(repository, module, work)
        observation = _exact_observation(module, work, context, result="not_found")
        coordinator = module.ProductionRecoveryCoordinator(
            repository,
            remote_target_factory=lambda _provider: _ReadTarget(observation),
            authority=_RecoveryAuthority(module),
            provider_contracts={work.target_alias: context.provider_contract},
        )
        selected = coordinator.reconcile(work.work_id)
        with psycopg.connect(database, row_factory=dict_row) as connection:
            selected_row = connection.execute(
                "SELECT payload,policy_decision_id FROM v4_production_recovery_events "
                "WHERE event_id=%s",
                (selected.event_id,),
            ).fetchone()
            later_payload = dict(selected_row["payload"])
            later_payload.update(
                {
                    "reason_code": "remote_observation",
                    "result_class": result_class,
                    "remote_state": remote_state,
                    "evidence_sha256": "e" * 64,
                }
            )
            repository._append_event(
                connection,
                work,
                event_key="superseding-remote-read:" + str(work.work_id),
                event_kind="remote_read_observation",
                policy_decision_id=selected_row["policy_decision_id"],
                payload=later_payload,
            )
        command = module.NoEffectGenerationReissueCommand(
            work_id=work.work_id,
            case_id=work.case_id,
            expected_case_revision=1,
            observation_event_id=selected.event_id,
            observation_sha256=selected.payload_sha256,
            operation_id=work.operation_id,
            request_fingerprint=work.request_fingerprint,
            original_idempotency_key=context.idempotency_key,
            stage_decision_id=uuid4(),
            policy_decision_id=uuid4(),
            workspace_stop_revision=3,
            goal_stop_revision=5,
            route_decision_id=uuid4(),
            idempotency_key="reissue:" + str(work.case_id) + ":superseded",
        )
        controller = module.ProductionRecoveryCaseController(
            repository,
            authority=_RecoveryAuthority(module),
            commands=object(),
        )
        with pytest.raises(ValueError, match="latest remote observation"):
            controller.authorize_no_effect_generation_reissue(command)
        with psycopg.connect(database) as connection:
            assert connection.execute(
                """SELECT count(*) FROM v4_production_recovery_events
                   WHERE work_id=%s AND event_kind='same_operation_no_effect_reissue'""",
                (work.work_id,),
            ).fetchone()[0] == 0


def test_recovery_event_validator_rejects_superseded_observation_binding():
    module = import_module("salience.cycles.production_recovery")
    with _production_recovery_operation(
        "task8_event_validator_superseded_", task8_dispatch=True
    ) as (database, _fixture, authority, _enrollment):
        repository = module.ProductionRecoveryWorkRepository(database)
        work = _case_work(repository, authority, module)
        context = _read_context(repository, module, work)
        observation = _exact_observation(module, work, context, result="not_found")
        coordinator = module.ProductionRecoveryCoordinator(
            repository,
            remote_target_factory=lambda _provider: _ReadTarget(observation),
            authority=_RecoveryAuthority(module),
            provider_contracts={work.target_alias: context.provider_contract},
        )
        selected = coordinator.reconcile(work.work_id)
        with psycopg.connect(database, row_factory=dict_row) as connection:
            selected_row = connection.execute(
                """SELECT payload,policy_decision_id
                   FROM v4_production_recovery_events WHERE event_id=%s""",
                (selected.event_id,),
            ).fetchone()
            later_payload = dict(selected_row["payload"])
            later_payload.update(
                {
                    "reason_code": "remote_observation",
                    "result_class": "unknown",
                    "remote_state": "unknown",
                    "evidence_sha256": "f" * 64,
                }
            )
            repository._append_event(
                connection,
                work,
                event_key="superseding-before-command:" + str(work.work_id),
                event_kind="remote_read_observation",
                policy_decision_id=selected_row["policy_decision_id"],
                payload=later_payload,
            )

            event_key = "reissue-validator:" + str(work.work_id)
            remote_identity = sha256(
                f"{work.operation_id}:{context.idempotency_key}".encode("utf-8")
            ).hexdigest()
            decision_ids = (uuid4(), uuid4(), uuid4())
            proof = ":".join(
                (
                    str(selected.event_id),
                    selected.payload_sha256,
                    work.request_fingerprint,
                    remote_identity,
                    str(decision_ids[0]),
                    str(decision_ids[1]),
                    "3",
                    "5",
                    str(decision_ids[2]),
                )
            )
            event_payload = {
                "schema_version": "salience.p1-same-operation-no-effect.v1",
                "reason_code": "authoritative_no_effect",
                "evidence_sha256": sha256(proof.encode("utf-8")).hexdigest(),
                "remote_identity_sha256": remote_identity,
                "request_fingerprint": work.request_fingerprint,
                "operation_id": str(work.operation_id),
                "case_id": str(work.case_id),
                "work_id": str(work.work_id),
                "source_id": str(work.source_id),
                "state": work.state,
                "result_class": "authoritative_no_effect",
                "event_key": event_key,
                "stage_decision_id": str(decision_ids[0]),
                "policy_decision_id": str(decision_ids[1]),
                "workspace_stop_revision": 3,
                "goal_stop_revision": 5,
                "route_decision_id": str(decision_ids[2]),
                "observation_event_id": str(selected.event_id),
                "observation_sha256": selected.payload_sha256,
            }
            with pytest.raises(
                psycopg.errors.RaiseException,
                match="latest exact no-effect observation",
            ):
                with connection.transaction():
                    repository._append_event(
                        connection,
                        work,
                        event_key=event_key,
                        event_kind="same_operation_no_effect_reissue",
                        policy_decision_id=decision_ids[1],
                        payload=event_payload,
                    )


def test_rollback_v2_requires_complete_two_reason_source_inventory_before_exec():
    module = import_module("salience.cycles.p1_recovery_rollback")
    assert module.ROLLBACK_VERIFIER_ABI_V2 == "salience.p1-recovery-rollback-verifier.v2"
    with _rollback_v2_valid_operation("task9_complete_inventory_") as (
        database,
        authority,
        enrollment,
        work,
        route_authority,
    ):
        inventory = module.read_p1_recovery_rollback_inventory_v2(
            database, workspace_id=authority["workspace_id"]
        )
        route_authority, temporal_readback = _rollback_v2_readbacks(
            inventory.route_claims
        )
        verified = module.verify_p1_recovery_rollback_v2(
            database,
            workspace_id=authority["workspace_id"],
            route_authority=route_authority,
            temporal_readback=temporal_readback,
        )
        assert verified.digest == inventory.digest
        assert isinstance(verified.readback_digest, str)
        assert len(verified.readback_digest) == 64
        assert verified.temporal_readbacks
        assert all(
            item.route_id in {claim.route_id for claim in verified.route_claims}
            and item.temporal_endpoint_id == "temporal-production-1"
            and item.namespace == "salience-production"
            and item.task_queue == "salience-production-publication"
            for item in verified.temporal_readbacks
        )
        assert verified.inventory["enrollments"]
        assert any(
            row["intent_id"] == str(enrollment["intent_id"])
            for row in verified.inventory["enrollments"]
        )
        assert any(row["work_id"] == str(work.work_id) for row in verified.inventory["recovery_work"])
        assert any(row["work_id"] == str(work.work_id) for row in verified.inventory["recovery_events"])
        assert set(verified.reason_counts) == {
            "expired_permit_receipt",
            "published_during_cancel",
        }
        assert verified.reason_counts == {
            "expired_permit_receipt": {"production_work": 0, "fixture_holds": 0},
            "published_during_cancel": {"production_work": 0, "fixture_holds": 0},
        }


def _task9_set_stop_scope(connection, workspace_id, scope_key, revision, stopped, actor_id, key):
    command_id = uuid4()
    connection.execute(
        """INSERT INTO v4_stop_commands(
                   id,workspace_id,scope_key,idempotency_key,fingerprint,actor_id,
                   resulting_revision,stopped,traceparent)
           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            command_id,
            workspace_id,
            scope_key,
            key,
            sha256(key.encode()).hexdigest(),
            actor_id,
            revision,
            stopped,
            "00-" + uuid4().hex + "-" + uuid4().hex[:16] + "-01",
        ),
    )
    connection.execute(
        """UPDATE v4_stop_scopes
           SET revision=%s,stopped=%s,changed_by=%s,reason=%s,
               changed_at=clock_timestamp()
           WHERE workspace_id=%s AND scope_key=%s""",
        (revision, stopped, actor_id, key, workspace_id, scope_key),
    )


def test_rollback_v2_accepts_current_active_stops_after_historical_enrollment():
    with _rollback_v2_valid_operation(
        "task9_active_stop_scope_", active_stops=False
    ) as (
        database,
        authority,
        enrollment,
        _work,
        _route_authority,
    ):
        workspace_id = authority["workspace_id"]
        goal_id = authority["cycle"]["goal_id"]
        assert enrollment["workspace_stop_revision"] == 1
        assert enrollment["goal_stop_revision"] == 1
        with psycopg.connect(database) as connection:
            _task9_set_stop_scope(
                connection,
                workspace_id,
                "workspace",
                2,
                True,
                authority["subject_id"],
                "task9-active-workspace-stop",
            )
            _task9_set_stop_scope(
                connection,
                workspace_id,
                "goal:" + str(goal_id),
                2,
                True,
                authority["subject_id"],
                "task9-active-goal-stop",
            )

        with psycopg.connect(database) as connection:
            inventory = connection.execute(
                "SELECT public.v4_p1_recovery_rollback_inventory_v2(%s)",
                (workspace_id,),
            ).fetchone()[0]
        assert inventory["blockers"] == []
        assert inventory["enrollments"][0]["workspace_stop_revision"] == 1
        assert inventory["enrollments"][0]["goal_stop_revision"] == 1


def test_rollback_v2_accepts_revision_one_false_stop_noops():
    from salience.cycles.governance import CycleGovernance

    with _rollback_v2_valid_operation(
        "task12_revision_one_stop_noops_", active_stops=False
    ) as (database, authority, enrollment, _work, _route_authority):
        assert enrollment["workspace_stop_revision"] == 1
        assert enrollment["goal_stop_revision"] == 1
        governance = CycleGovernance(
            database,
            workspace_id=authority["workspace_id"],
            subject_id=authority["subject_id"],
        )
        for goal_scope_id in (None, authority["cycle"]["goal_id"]):
            for suffix in ("first", "second"):
                governance.set_stop(
                    goal_id=goal_scope_id,
                    stopped=False,
                    expected_revision=1,
                    idempotency_key=f"task12-rev1-{suffix}-{goal_scope_id}",
                    reason="Record an allowed same-state false stop command",
                )
        for goal_scope_id in (None, authority["cycle"]["goal_id"]):
            governance.set_stop(
                goal_id=goal_scope_id,
                stopped=True,
                expected_revision=1,
                idempotency_key=f"task12-rev1-commit-stop-{goal_scope_id}",
                reason="Commit a current active rollback stop",
            )

        with psycopg.connect(database) as connection:
            inventory = connection.execute(
                "SELECT public.v4_p1_recovery_rollback_inventory_v2(%s)",
                (authority["workspace_id"],),
            ).fetchone()[0]
        assert inventory["blockers"] == []


def test_rollback_v2_accepts_historical_and_active_stop_noops():
    from salience.cycles.governance import CycleGovernance

    with _rollback_v2_valid_operation(
        "task12_historical_stop_noops_",
        historical_stop_binding=True,
        active_stops=False,
    ) as (database, authority, enrollment, _work, _route_authority):
        assert enrollment["workspace_stop_revision"] == 3
        assert enrollment["goal_stop_revision"] == 3
        governance = CycleGovernance(
            database,
            workspace_id=authority["workspace_id"],
            subject_id=authority["subject_id"],
        )
        for goal_scope_id in (None, authority["cycle"]["goal_id"]):
            scope_key = (
                "workspace"
                if goal_scope_id is None
                else "goal:" + str(goal_scope_id)
            )
            governance.set_stop(
                goal_id=goal_scope_id,
                stopped=False,
                expected_revision=3,
                idempotency_key="task12-historical-noop-" + scope_key,
                reason="Record a historical same-state false stop command",
            )
            governance.set_stop(
                goal_id=goal_scope_id,
                stopped=True,
                expected_revision=3,
                idempotency_key="task12-historical-stop-transition-" + scope_key,
                reason="Commit the current active rollback stop",
            )
            governance.set_stop(
                goal_id=goal_scope_id,
                stopped=True,
                expected_revision=4,
                idempotency_key="task12-current-noop-" + scope_key,
                reason="Record a current same-state true stop command",
            )

        with psycopg.connect(database) as connection:
            inventory = connection.execute(
                "SELECT public.v4_p1_recovery_rollback_inventory_v2(%s)",
                (authority["workspace_id"],),
            ).fetchone()[0]
        assert inventory["blockers"] == []


def test_rollback_v2_validates_noninitial_historical_stop_receipts():
    with _rollback_v2_valid_operation(
        "task9_historical_stop_receipt_", historical_stop_binding=True
    ) as (database, authority, enrollment, _work, _route_authority):
        workspace_id = authority["workspace_id"]
        goal_scope_key = "goal:" + str(authority["cycle"]["goal_id"])
        assert enrollment["workspace_stop_revision"] == 3
        assert enrollment["goal_stop_revision"] == 3
        with psycopg.connect(database) as connection:
            inventory = connection.execute(
                "SELECT public.v4_p1_recovery_rollback_inventory_v2(%s)",
                (workspace_id,),
            ).fetchone()[0]
        assert inventory["blockers"] == []

        # Simulate missing immutable history while preserving the later active
        # stop receipt. The inventory must reject the enrollment's bound rev3.
        with psycopg.connect(database) as connection:
            connection.execute("SET session_replication_role = replica")
            connection.execute(
                """DELETE FROM v4_stop_commands
                   WHERE workspace_id=%s AND scope_key=%s
                     AND resulting_revision=3""",
                (workspace_id, "workspace"),
            )
        with psycopg.connect(database) as connection:
            inventory = connection.execute(
                "SELECT public.v4_p1_recovery_rollback_inventory_v2(%s)",
                (workspace_id,),
            ).fetchone()[0]
        assert any(
            blocker.get("kind") == "enrollment_historical_stop_binding_unresolved"
            and blocker.get("scope_key") == "workspace"
            and blocker.get("bound_revision") == 3
            for blocker in inventory["blockers"]
        )


def test_rollback_v2_denies_unstopped_current_scope_after_stop_revision_drift():
    with _rollback_v2_valid_operation(
        "task9_unstopped_current_scope_", active_stops=False
    ) as (
        database,
        authority,
        _enrollment,
        _work,
        _route_authority,
    ):
        workspace_id = authority["workspace_id"]
        goal_id = authority["cycle"]["goal_id"]
        with psycopg.connect(database) as connection:
            _task9_set_stop_scope(
                connection,
                workspace_id,
                "workspace",
                2,
                True,
                authority["subject_id"],
                "task9-drift-workspace-stop",
            )
            _task9_set_stop_scope(
                connection,
                workspace_id,
                "goal:" + str(goal_id),
                2,
                True,
                authority["subject_id"],
                "task9-drift-goal-stop",
            )
            _task9_set_stop_scope(
                connection,
                workspace_id,
                "goal:" + str(goal_id),
                3,
                False,
                authority["subject_id"],
                "task9-drift-goal-unstop",
            )

        with psycopg.connect(database) as connection:
            inventory = connection.execute(
                "SELECT public.v4_p1_recovery_rollback_inventory_v2(%s)",
                (workspace_id,),
            ).fetchone()[0]
        assert any(
            blocker.get("kind") == "rollback_stop_scope_not_active_or_receipted"
            and blocker.get("scope_key") == "goal:" + str(goal_id)
            for blocker in inventory["blockers"]
        )


def test_rollback_v2_requires_exact_deadline_suspend_case_command_receipt():
    migration_tests = import_module("test_v4_migrations")
    recovery = import_module("salience.cycles.production_recovery")
    with _production_recovery_operation(
        "task9_deadline_receipt_", task8_dispatch=True
    ) as (database, _fixture, authority, _enrollment):
        repository = recovery.ProductionRecoveryWorkRepository(database)
        work = _case_work(repository, authority, recovery)
        policy_decision_id = uuid4()
        route_decision_id = uuid4()
        valid_key = "task9-deadline-valid:" + str(work.work_id)
        valid_command_id = uuid4()
        invalid_cases = (
            "missing-command",
            "unrelated-case",
            "wrong-action",
            "wrong-result-revision",
            "mismatched-idempotency-key",
        )

        def append_deadline_event(
            connection,
            *,
            event_key,
            command_id,
            command_case_id,
            action,
            result_revision,
            insert_command,
        ):
            command_key = (
                event_key + ":different"
                if "mismatched-idempotency-key" in event_key
                else event_key
            )
            if insert_command:
                connection.execute("SET session_replication_role=replica")
                connection.execute(
                    """INSERT INTO v4_case_commands(
                               id,case_id,idempotency_key,fingerprint,action,actor_id,result)
                       VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        command_id,
                        command_case_id,
                        command_key,
                        sha256(command_key.encode()).hexdigest(),
                        action,
                        work.owner_id,
                        Jsonb({"resulting_revision": result_revision}),
                    ),
                )
                connection.execute("SET session_replication_role=origin")
            payload = {
                "schema_version": "salience.p1-production-recovery-deadline.v1",
                "status": "deadline_suspend",
                "result_class": "suspended",
                "attempt": work.case_revision + 1,
                "command_id": str(command_id),
                "case_id": str(work.case_id),
                "work_id": str(work.work_id),
                "source_id": str(work.source_id),
                "operation_id": str(work.operation_id),
                "intent_id": str(work.intent_id),
                "deadline": work.deadline.isoformat(),
                "event_key": event_key,
                "policy_decision_id": str(policy_decision_id),
                "route_decision_id": str(route_decision_id),
            }
            return repository._append_event(
                connection,
                work,
                event_key=event_key,
                event_kind="escalation",
                policy_decision_id=policy_decision_id,
                payload=payload,
            )

        with repository._connect() as connection:
            connection.execute(
                """INSERT INTO v4_case_commands(
                           id,case_id,idempotency_key,fingerprint,action,actor_id,result)
                   VALUES(%s,%s,%s,%s,'deadline_suspend',%s,%s)""",
                (
                    valid_command_id,
                    work.case_id,
                    valid_key,
                    sha256(valid_key.encode()).hexdigest(),
                    work.owner_id,
                    Jsonb({"resulting_revision": work.case_revision + 1}),
                ),
            )
            append_deadline_event(
                connection,
                event_key=valid_key,
                command_id=valid_command_id,
                command_case_id=work.case_id,
                action="deadline_suspend",
                result_revision=work.case_revision + 1,
                insert_command=False,
            )

        upgraded = migration_tests._migration(
            database, "upgrade", "0051_p1_recovery_rollback_v2"
        )
        assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
        with psycopg.connect(database, row_factory=dict_row) as connection:
            before = connection.execute(
                "SELECT public.v4_p1_recovery_rollback_inventory_v2(%s) AS inventory",
                (authority["workspace_id"],),
            ).fetchone()["inventory"]
            assert not any(
                blocker.get("kind") == "deadline_suspend_command_receipt_mismatch"
                for blocker in before["blockers"]
            )

            for invalid_case in invalid_cases:
                event_key = "task9-deadline-" + invalid_case + ":" + str(work.work_id)
                command_id = uuid4()
                insert_command = invalid_case != "missing-command"
                command_case_id = (
                    uuid4() if invalid_case == "unrelated-case" else work.case_id
                )
                action = "review" if invalid_case == "wrong-action" else "deadline_suspend"
                result_revision = (
                    work.case_revision + 2
                    if invalid_case == "wrong-result-revision"
                    else work.case_revision + 1
                )
                event = append_deadline_event(
                    connection,
                    event_key=event_key,
                    command_id=command_id,
                    command_case_id=command_case_id,
                    action=action,
                    result_revision=result_revision,
                    insert_command=insert_command,
                )
                current = connection.execute(
                    "SELECT public.v4_p1_recovery_rollback_inventory_v2(%s) AS inventory",
                    (authority["workspace_id"],),
                ).fetchone()["inventory"]
                assert any(
                    blocker.get("kind") == "deadline_suspend_command_receipt_mismatch"
                    and blocker.get("event_id") == str(event.event_id)
                    for blocker in current["blockers"]
                ), invalid_case
                connection.execute("SET session_replication_role=replica")
                connection.execute(
                    "DELETE FROM v4_production_recovery_events WHERE event_id=%s",
                    (event.event_id,),
                )
                if insert_command:
                    connection.execute(
                        "DELETE FROM v4_case_commands WHERE id=%s", (command_id,)
                    )
                connection.execute("SET session_replication_role=origin")


def test_rollback_v2_checks_zero_one_and_many_rows_for_each_reason():
    migration_tests = import_module("test_v4_migrations")
    recovery = import_module("salience.cycles.production_recovery")
    with _production_recovery_operation(
        "task9_reason_counts_", task8_dispatch=True
    ) as (database, fixture, authority, _enrollment):
        repository = recovery.ProductionRecoveryWorkRepository(database)
        production_hold = repository.bind_production_publication_hold(
            authority["operation_id"],
            reason="expired_permit_receipt",
            deadline=datetime.now(timezone.utc) + timedelta(days=1),
            policy=_recovery_policy(recovery),
        )
        upgraded = migration_tests._migration(
            database, "upgrade", "0051_p1_recovery_rollback_v2"
        )
        assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
        empty_workspace = uuid4()
        with psycopg.connect(database) as connection:
            empty = connection.execute(
                "SELECT public.v4_p1_recovery_rollback_inventory_v2(%s)",
                (empty_workspace,),
            ).fetchone()[0]
            assert empty["reason_counts"] == {
                "expired_permit_receipt": {"production_work": 0, "fixture_holds": 0},
                "published_during_cancel": {"production_work": 0, "fixture_holds": 0},
            }

            production_one = connection.execute(
                "SELECT public.v4_p1_recovery_rollback_inventory_v2(%s)",
                (authority["workspace_id"],),
            ).fetchone()[0]
            assert production_one["reason_counts"] == {
                "expired_permit_receipt": {"production_work": 1, "fixture_holds": 0},
                "published_during_cancel": {"production_work": 0, "fixture_holds": 0},
            }

            def clone_production_work(reason, revision):
                work_id = uuid4()
                operation_id = uuid4()
                connection.execute("SET session_replication_role=replica")
                inserted = connection.execute(
                    """INSERT INTO v4_production_recovery_work
                       SELECT (jsonb_populate_record(
                           NULL::public.v4_production_recovery_work,
                           to_jsonb(work) || jsonb_build_object(
                               'work_id',%s::text,
                               'source_id',%s::text,
                               'operation_id',%s::text,
                               'source_revision',%s::integer,
                               'reason',%s::text))).*
                       FROM v4_production_recovery_work work WHERE work_id=%s
                       RETURNING work_id""",
                    (
                        str(work_id),
                        str(operation_id),
                        str(operation_id),
                        revision,
                        reason,
                        production_hold.work_id,
                    ),
                ).fetchone()
                connection.execute("SET session_replication_role=origin")
                assert inserted[0] == work_id

            clone_production_work("published_during_cancel", 2)
            production_each_one = connection.execute(
                "SELECT public.v4_p1_recovery_rollback_inventory_v2(%s)",
                (authority["workspace_id"],),
            ).fetchone()[0]
            assert production_each_one["reason_counts"] == {
                "expired_permit_receipt": {"production_work": 1, "fixture_holds": 0},
                "published_during_cancel": {"production_work": 1, "fixture_holds": 0},
            }
            clone_production_work("expired_permit_receipt", 3)
            clone_production_work("published_during_cancel", 4)
            production_many = connection.execute(
                "SELECT public.v4_p1_recovery_rollback_inventory_v2(%s)",
                (authority["workspace_id"],),
            ).fetchone()[0]
            assert production_many["reason_counts"] == {
                "expired_permit_receipt": {"production_work": 2, "fixture_holds": 0},
                "published_during_cancel": {"production_work": 2, "fixture_holds": 0},
            }

            one = connection.execute(
                "SELECT public.v4_p1_recovery_rollback_inventory_v2(%s)",
                (fixture["workspace_id"],),
            ).fetchone()[0]
            assert one["reason_counts"]["expired_permit_receipt"] == {
                "production_work": 0,
                "fixture_holds": 1,
            }
            assert one["reason_counts"]["published_during_cancel"] == {
                "production_work": 0,
                "fixture_holds": 0,
            }

            connection.execute("SET session_replication_role=replica")
            connection.execute(
                """INSERT INTO v4_legacy_publication_recovery_holds(
                          id,operation_id,workspace_id,goal_id,intent_id,cycle_id,
                          context_id,job_id,publication_attempt_id,permit_id,owner_id,
                          execution_deadline,permit_expires_at,request_fingerprint,
                          receipt_fingerprint,liability_fingerprint,remote_id,remote_state,
                          reason,state,liability_snapshot,liability_snapshot_version,
                          liability_snapshot_v1,liability_fingerprint_v1)
                   SELECT gen_random_uuid(),gen_random_uuid(),workspace_id,goal_id,
                          intent_id,cycle_id,context_id,job_id,publication_attempt_id,
                          permit_id,owner_id,execution_deadline,permit_expires_at,
                          request_fingerprint,receipt_fingerprint,liability_fingerprint,
                          remote_id,remote_state,'published_during_cancel',state,
                          liability_snapshot,liability_snapshot_version,
                          liability_snapshot_v1,liability_fingerprint_v1
                   FROM v4_legacy_publication_recovery_holds
                   WHERE id=%s""",
                (fixture["fixture_hold_id"],),
            )
            one_published = connection.execute(
                "SELECT public.v4_p1_recovery_rollback_inventory_v2(%s)",
                (fixture["workspace_id"],),
            ).fetchone()[0]
            assert one_published["reason_counts"]["published_during_cancel"] == {
                "production_work": 0,
                "fixture_holds": 1,
            }
            connection.execute(
                """INSERT INTO v4_legacy_publication_recovery_holds(
                          id,operation_id,workspace_id,goal_id,intent_id,cycle_id,
                          context_id,job_id,publication_attempt_id,permit_id,owner_id,
                          execution_deadline,permit_expires_at,request_fingerprint,
                          receipt_fingerprint,liability_fingerprint,remote_id,remote_state,
                          reason,state,liability_snapshot,liability_snapshot_version,
                          liability_snapshot_v1,liability_fingerprint_v1)
                   SELECT gen_random_uuid(),gen_random_uuid(),workspace_id,goal_id,
                          intent_id,cycle_id,context_id,job_id,publication_attempt_id,
                          permit_id,owner_id,execution_deadline,permit_expires_at,
                          request_fingerprint,receipt_fingerprint,liability_fingerprint,
                          remote_id,remote_state,'expired_permit_receipt',state,
                          liability_snapshot,liability_snapshot_version,
                          liability_snapshot_v1,liability_fingerprint_v1
                   FROM v4_legacy_publication_recovery_holds
                   WHERE id=%s""",
                (fixture["fixture_hold_id"],),
            )
            connection.execute(
                """INSERT INTO v4_legacy_publication_recovery_holds(
                          id,operation_id,workspace_id,goal_id,intent_id,cycle_id,
                          context_id,job_id,publication_attempt_id,permit_id,owner_id,
                          execution_deadline,permit_expires_at,request_fingerprint,
                          receipt_fingerprint,liability_fingerprint,remote_id,remote_state,
                          reason,state,liability_snapshot,liability_snapshot_version,
                          liability_snapshot_v1,liability_fingerprint_v1)
                   SELECT gen_random_uuid(),gen_random_uuid(),workspace_id,goal_id,
                          intent_id,cycle_id,context_id,job_id,publication_attempt_id,
                          permit_id,owner_id,execution_deadline,permit_expires_at,
                          request_fingerprint,receipt_fingerprint,liability_fingerprint,
                          remote_id,remote_state,'published_during_cancel',state,
                          liability_snapshot,liability_snapshot_version,
                          liability_snapshot_v1,liability_fingerprint_v1
                   FROM v4_legacy_publication_recovery_holds
                   WHERE id=%s""",
                (fixture["fixture_hold_id"],),
            )
            connection.execute("SET session_replication_role=origin")
            many = connection.execute(
                "SELECT public.v4_p1_recovery_rollback_inventory_v2(%s)",
                (fixture["workspace_id"],),
            ).fetchone()[0]
            assert many["reason_counts"]["expired_permit_receipt"] == {
                "production_work": 0,
                "fixture_holds": 2,
            }
            assert many["reason_counts"]["published_during_cancel"] == {
                "production_work": 0,
                "fixture_holds": 2,
            }


def test_rollback_v2_rejects_partial_inventory_and_omitted_rows():
    module = import_module("salience.cycles.p1_recovery_rollback")
    from copy import deepcopy

    with _rollback_v2_valid_operation("task9_partial_inventory_") as (
        database,
        authority,
        _enrollment,
        _work,
        _route_authority,
    ):
        inventory = module.read_p1_recovery_rollback_inventory_v2(
            database, workspace_id=authority["workspace_id"]
        )
        partial = deepcopy(inventory.inventory)
        partial["enrollments"].clear()
        with pytest.raises(module.RecoveryRollbackVerificationError, match="partial"):
            module.validate_recovery_rollback_inventory_v2(
                partial, workspace_id=authority["workspace_id"]
            )
        omitted = deepcopy(inventory.inventory)
        del omitted["reason_counts"]["published_during_cancel"]
        with pytest.raises(module.RecoveryRollbackVerificationError, match="both hold reasons"):
            module.validate_recovery_rollback_inventory_v2(
                omitted, workspace_id=authority["workspace_id"]
            )


def test_rollback_v2_rejects_snapshot_or_liability_change():
    module = import_module("salience.cycles.p1_recovery_rollback")
    migration_tests = import_module("test_v4_migrations")
    recovery = import_module("salience.cycles.production_recovery")
    with _production_recovery_operation(
        "task9_liability_change_", task8_dispatch=True
    ) as (database, _fixture, authority, _enrollment):
        repository = recovery.ProductionRecoveryWorkRepository(database)
        repository.bind_production_publication_hold(
            authority["operation_id"],
            reason="expired_permit_receipt",
            deadline=datetime.now(timezone.utc) + timedelta(days=1),
            policy=_recovery_policy(recovery),
        )
        upgraded = migration_tests._migration(
            database, "upgrade", "0051_p1_recovery_rollback_v2"
        )
        assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
        with psycopg.connect(database) as connection:
            before = connection.execute(
                "SELECT public.v4_p1_recovery_rollback_inventory_v2(%s)",
                (authority["workspace_id"],),
            ).fetchone()[0]
            assert before["production_liabilities"]
            connection.execute("SET session_replication_role=replica")
            connection.execute(
                """UPDATE v4_production_effect_permits
                   SET request_snapshot=request_snapshot || '{\"changed\":true}'::jsonb
                   WHERE operation_id=%s""",
                (authority["operation_id"],),
            )
            connection.execute("SET session_replication_role=origin")
            after = connection.execute(
                "SELECT public.v4_p1_recovery_rollback_inventory_v2(%s)",
                (authority["workspace_id"],),
            ).fetchone()[0]
            assert any(
                blocker["kind"] == "production_liability_changed"
                for blocker in after["blockers"]
            )
        assert after["blockers"] != before["blockers"]
        with pytest.raises(
            module.RecoveryRollbackVerificationError,
            match="blockers|liability changed",
        ):
            module.read_p1_recovery_rollback_inventory_v2(
                database, workspace_id=authority["workspace_id"]
            )


@pytest.mark.parametrize("reason", ["expired_permit_receipt", "published_during_cancel"])
@pytest.mark.parametrize("drift", ["reason_swap", "fingerprint", "source_row"])
def test_rollback_v2_revalidates_production_hold_reason_and_source(reason, drift):
    migration_tests = import_module("test_v4_migrations")
    recovery = import_module("salience.cycles.production_recovery")
    with _production_recovery_operation(
        f"task14_hold_source_{reason}_{drift}_", task8_dispatch=True
    ) as (database, _fixture, authority, _enrollment):
        if reason == "published_during_cancel":
            with psycopg.connect(database) as connection:
                connection.execute("SET session_replication_role=replica")
                connection.execute(
                    "UPDATE v4_cycles SET state='closed',disposition='cancelled',"
                    "closed_at=clock_timestamp() WHERE id=%s",
                    (authority["cycle"]["cycle_id"],),
                )
                connection.execute("SET session_replication_role=origin")
        repository = recovery.ProductionRecoveryWorkRepository(database)
        work = repository.bind_production_publication_hold(
            authority["operation_id"],
            reason=reason,
            deadline=datetime.now(timezone.utc) + timedelta(days=1),
            policy=_recovery_policy(recovery),
        )
        upgraded = migration_tests._migration(
            database, "upgrade", "0051_p1_recovery_rollback_v2"
        )
        assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr

        with psycopg.connect(database, row_factory=dict_row) as connection:
            baseline = connection.execute(
                "SELECT public.v4_p1_recovery_rollback_inventory_v2(%s) AS inventory",
                (authority["workspace_id"],),
            ).fetchone()["inventory"]
            assert not any(
                blocker["kind"] == "production_publication_hold_source_mismatch"
                for blocker in baseline["blockers"]
            )
            liability = next(
                item for item in baseline["production_liabilities"]
                if item["work_id"] == str(work.work_id)
            )
            assert liability["stored_snapshot"] == liability["current_snapshot"]

            connection.execute("SET session_replication_role=replica")
            if drift == "reason_swap":
                swapped_reason = (
                    "published_during_cancel"
                    if reason == "expired_permit_receipt"
                    else "expired_permit_receipt"
                )
                connection.execute(
                    "UPDATE v4_production_recovery_work SET reason=%s WHERE work_id=%s",
                    (swapped_reason, work.work_id),
                )
            elif drift == "fingerprint":
                connection.execute(
                    "UPDATE v4_production_recovery_work SET source_fingerprint=%s "
                    "WHERE work_id=%s",
                    ("0" * 64, work.work_id),
                )
            else:
                connection.execute(
                    "UPDATE v4_production_effect_permits SET provider_id=%s "
                    "WHERE operation_id=%s",
                    ("drifted-provider", authority["operation_id"]),
                )
            connection.execute("SET session_replication_role=origin")

            corrupted = connection.execute(
                "SELECT public.v4_p1_recovery_rollback_inventory_v2(%s) AS inventory",
                (authority["workspace_id"],),
            ).fetchone()["inventory"]
            assert any(
                blocker["kind"] == "production_publication_hold_source_mismatch"
                and blocker["work_id"] == str(work.work_id)
                for blocker in corrupted["blockers"]
            ), (reason, drift)


def test_rollback_v2_rejects_unknown_route_claim_and_unavailable_readback():
    module = import_module("salience.cycles.p1_recovery_rollback")
    with _rollback_v2_valid_operation("task9_unknown_route_") as (
        database,
        authority,
        _enrollment,
        _work,
        _route_authority,
    ):
        inventory = module.read_p1_recovery_rollback_inventory_v2(
            database, workspace_id=authority["workspace_id"]
        )
        route_authority, temporal_readback = _rollback_v2_readbacks(
            inventory.route_claims
        )
        route_authority.unavailable = True
        with pytest.raises(module.RecoveryRollbackVerificationError, match="route claim"):
            module.verify_p1_recovery_rollback_v2(
                database,
                workspace_id=authority["workspace_id"],
                route_authority=route_authority,
                temporal_readback=temporal_readback,
            )
        route_authority.unavailable = False
        temporal_readback.unavailable = True
        with pytest.raises(module.RecoveryRollbackVerificationError, match="Temporal"):
            module.verify_p1_recovery_rollback_v2(
                database,
                workspace_id=authority["workspace_id"],
                route_authority=route_authority,
                temporal_readback=temporal_readback,
            )


def test_rollback_v2_inventory_and_schedule_validation_are_read_only():
    module = import_module("salience.cycles.p1_recovery_rollback")
    with _rollback_v2_valid_operation("task9_read_only_inventory_") as (
        database,
        authority,
        _enrollment,
        _work,
        _route_authority,
    ):
        with psycopg.connect(database) as connection:
            connection.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
            )
            schedule_id = connection.execute(
                "SELECT inventory_id FROM v4_production_schedule_inventories ORDER BY inventory_id LIMIT 1"
            ).fetchone()[0]
            assert connection.execute(
                "SELECT public.v4_validate_production_schedule_inventory_v1(%s)",
                (schedule_id,),
            ).fetchone()[0] == ""
            inventory = connection.execute(
                "SELECT public.v4_p1_recovery_rollback_inventory_v2(%s)",
                (authority["workspace_id"],),
            ).fetchone()[0]
            assert inventory["complete"] is True
            assert inventory["blockers"] == []
            assert inventory["migration_head"] == "0051_p1_recovery_rollback_v2"

            with pytest.raises(
                psycopg.errors.RaiseException,
                match="production schedule inventory does not exist",
            ):
                with connection.transaction():
                    connection.execute(
                        "SELECT public.v4_validate_production_schedule_inventory_v1(%s)",
                        (uuid4(),),
                    )
            with pytest.raises(
                psycopg.errors.ReadOnlySqlTransaction,
                match="cannot execute SELECT FOR UPDATE",
            ):
                with connection.transaction():
                    connection.execute(
                        "SELECT public.v4_finalize_production_schedule_inventory_v1(%s)",
                        (schedule_id,),
                    )

        read_inventory = module.read_p1_recovery_rollback_inventory_v2(
            database, workspace_id=authority["workspace_id"]
        )
        assert read_inventory.inventory["workspace_id"] == str(
            authority["workspace_id"]
        )


@contextmanager
def _rollback_v2_operation(prefix):
    migration_tests = import_module("test_v4_migrations")
    with _production_recovery_operation(prefix, task8_dispatch=True) as (
        database,
        _fixture,
        authority,
        enrollment,
    ):
        upgraded = migration_tests._migration(
            database, "upgrade", "0051_p1_recovery_rollback_v2"
        )
        assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
        yield database, authority, enrollment


@contextmanager
def _rollback_v2_valid_operation(
    prefix, *, active_stops=True, historical_stop_binding=False
):
    migration_tests = import_module("test_v4_migrations")
    schedule_tests = import_module("test_p1_production_schedule_fencing")
    with schedule_tests._production_enrollment_fixture(prefix) as (
        database,
        _control,
        _binding,
        _key,
        _fixture_authority,
        _fixture_route_authority,
    ):
        upgraded = migration_tests._migration(
            database, "upgrade", "0050_p1_production_dispatch_reissue"
        )
        assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
        with psycopg.connect(database) as connection:
            workspace_id, subject_id = migration_tests._seed_0044_workspace_subject(
                connection
            )
            goal_id = uuid4()
            intent_id = uuid4()
            policy_id = migration_tests._seed_0044_policy(
                connection, workspace_id, status="active"
            )
            grant_id = connection.execute(
                """INSERT INTO permission_grants(
                           workspace_id,principal_type,principal_id,scope,effect,
                           constraints,expires_at)
                   VALUES(%s,'identity',%s,'production:recovery','allow','{}',
                          clock_timestamp()+interval '1 day') RETURNING id""",
                (workspace_id, str(subject_id)),
            ).fetchone()[0]
            connection.execute(
                """INSERT INTO permission_grants(
                           workspace_id,principal_type,principal_id,scope,effect,
                           constraints,expires_at)
                   VALUES(%s,'identity',%s,'cycles:stop','allow','{}',
                          clock_timestamp()+interval '1 day')""",
                (workspace_id, str(subject_id)),
            )
            subject_revision = connection.execute(
                "SELECT revision FROM identity_subjects WHERE id=%s",
                (subject_id,),
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO v4_goals(id,workspace_id,state) VALUES(%s,%s,'active')",
                (goal_id, workspace_id),
            )
            connection.execute(
                """INSERT INTO v4_goal_revisions(goal_id,revision,payload)
                   VALUES(%s,1,%s)""",
                (
                    goal_id,
                    Jsonb(
                        {
                            "schema_version": "GoalSpec.local.v1",
                            "objective": "Task 9 rollback fixture",
                            "metric_versions": ["task9-fixture"],
                            "audience": "local test fixture",
                            "account_refs": ["fixture-account"],
                            "brand_scope": "fixture-only",
                            "source_policy": "fixture-only",
                            "horizon_end": (
                                datetime.now(timezone.utc)
                                + timedelta(days=2)
                            ).isoformat(),
                        }
                    ),
                ),
            )
            connection.execute(
                """INSERT INTO v4_cycle_intents(
                           id,goal_id,goal_revision,slot,fingerprint,due_at,expires_at)
                   VALUES(%s,%s,1,%s,%s,clock_timestamp()-interval '1 minute',
                          clock_timestamp()+interval '1 day')""",
                (intent_id, goal_id, "task9-slot-" + str(intent_id), "a" * 64),
            )
            connection.execute(
                """INSERT INTO v4_stop_scopes(
                           workspace_id,scope_key,revision,stopped,changed_by,reason)
                   VALUES(%s,'workspace',1,false,%s,'task9 active workspace'),
                         (%s,%s,1,false,%s,'task9 active goal')""",
                (
                    workspace_id,
                    subject_id,
                    workspace_id,
                    "goal:" + str(goal_id),
                    subject_id,
                ),
            )
            if historical_stop_binding:
                for scope_key in (
                    "workspace",
                    "goal:" + str(goal_id),
                ):
                    _task9_set_stop_scope(
                        connection,
                        workspace_id,
                        scope_key,
                        2,
                        True,
                        subject_id,
                        "task9-historical-stop-" + scope_key,
                    )
                    _task9_set_stop_scope(
                        connection,
                        workspace_id,
                        scope_key,
                        3,
                        False,
                        subject_id,
                        "task9-historical-unstop-" + scope_key,
                    )
            connection.execute(
                """INSERT INTO v4_admissions(
                           id,intent_id,eligibility_revision,disposition,reason,cycle_id)
                   VALUES(%s,%s,1,'review_required','task9 enrolled review',NULL)""",
                (uuid4(), intent_id),
            )
            authority = {
                "workspace_id": workspace_id,
                "subject_id": subject_id,
                "subject_revision": subject_revision,
                "policy_id": policy_id,
                "grant_id": grant_id,
                "cycle": {"goal_id": goal_id, "intent_id": intent_id},
            }
            enrollment = migration_tests._insert_0047_enrollment(
                connection,
                authority,
                intent_id=intent_id,
                overrides=(
                    {
                        "workspace_stop_revision": 3,
                        "goal_stop_revision": 3,
                    }
                    if historical_stop_binding
                    else None
                ),
            )
        recovery = import_module("salience.cycles.production_recovery")
        repository = recovery.ProductionRecoveryWorkRepository(database)
        work = repository.bind_pre_admission_intent(
            intent_id, policy=_recovery_policy(recovery)
        )
        with repository._connect() as connection:
            with connection.transaction():
                repository._append_event(
                    connection,
                    work,
                    event_key="task9-inventory:" + str(work.work_id),
                    event_kind="remote_read_observation",
                    policy_decision_id=uuid4(),
                    payload={
                        "reason_code": "task9_inventory_fixture",
                        "evidence_sha256": "a" * 64,
                        "result_class": "not_found",
                        "remote_state": "not_found",
                        "intent_id": str(intent_id),
                        "source_id": str(intent_id),
                        "work_id": str(work.work_id),
                    },
                )
        upgraded = migration_tests._migration(
            database, "upgrade", "0051_p1_recovery_rollback_v2"
        )
        assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
        if active_stops:
            from salience.cycles.governance import CycleGovernance

            governance = CycleGovernance(
                database,
                workspace_id=authority["workspace_id"],
                subject_id=authority["subject_id"],
            )
            for goal_scope_id in (None, authority["cycle"]["goal_id"]):
                scope_key = (
                    "workspace"
                    if goal_scope_id is None
                    else "goal:" + str(goal_scope_id)
                )
                with psycopg.connect(database) as connection:
                    current_revision = connection.execute(
                        "SELECT revision FROM v4_stop_scopes "
                        "WHERE workspace_id=%s AND scope_key=%s",
                        (authority["workspace_id"], scope_key),
                    ).fetchone()[0]
                governance.set_stop(
                    goal_id=goal_scope_id,
                    stopped=True,
                    expected_revision=current_revision,
                    idempotency_key="task9-current-stop-" + scope_key,
                    reason="Task 9 canonical active stop",
                )
        yield database, authority, enrollment, work, None


def _rollback_v2_readbacks(route_claims):
    from salience.cycles.production_routing import (
        PRODUCTION_ACTIVITY_ALLOWLIST,
        PRODUCTION_WORKFLOW_ALLOWLIST,
        ROUTE_FENCE_AUTHORITY_VERSION,
        RouteLease,
        RouteSlotReadback,
    )
    from salience.workflows.worker import WorkerReadback

    now = datetime.now(timezone.utc)
    route_rows = {}
    temporal_rows = {}
    for claim in route_claims:
        key = (claim.route_id, claim.slot_id)
        lease = RouteLease(
            route_id=claim.route_id,
            slot_id=claim.slot_id,
            owner_id=claim.owner_id,
            fencing_epoch=claim.fencing_epoch,
            expires_at=now + timedelta(minutes=5),
            route_manifest_sha256=claim.route_manifest_sha256,
            artifact_lock_sha256=claim.artifact_lock_sha256,
        )
        route_rows[key] = RouteSlotReadback(
            route_id=claim.route_id,
            slot_id=claim.slot_id,
            available=True,
            state="leased",
            lease=lease,
            fencing_epoch=claim.fencing_epoch,
        )
        temporal_rows[key] = WorkerReadback(
            route_id=claim.route_id,
            slot_id=claim.slot_id,
            owner_id=claim.owner_id,
            fencing_epoch=claim.fencing_epoch,
            route_manifest_sha256=claim.route_manifest_sha256,
            artifact_lock_sha256=claim.artifact_lock_sha256,
            temporal_endpoint_id="temporal-production-1",
            namespace="salience-production",
            task_queue="salience-production-publication",
            registered_workflow_types=frozenset(PRODUCTION_WORKFLOW_ALLOWLIST),
            registered_activity_types=frozenset(PRODUCTION_ACTIVITY_ALLOWLIST),
            poller_count=0,
            active_task_count=0,
            observed_at=now,
            complete=True,
        )

    class RouteAuthority:
        protocol_version = ROUTE_FENCE_AUTHORITY_VERSION

        def __init__(self):
            self.unavailable = False

        def read_slot(self, route_id, slot_id):
            if self.unavailable:
                return RouteSlotReadback(
                    route_id=route_id,
                    slot_id=slot_id,
                    available=False,
                    state="unavailable",
                    lease=None,
                    fencing_epoch=None,
                )
            return route_rows[(route_id, slot_id)]

    class TemporalReadback:
        def __init__(self):
            self.unavailable = False

        def read_worker(self, route_id, slot_id):
            if self.unavailable:
                raise RuntimeError("Temporal observation unavailable")
            return temporal_rows[(route_id, slot_id)]

    return RouteAuthority(), TemporalReadback()
