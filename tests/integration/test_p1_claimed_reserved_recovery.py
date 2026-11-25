"""Exercise claimed recovery from the canonical publication reservation path."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
from uuid import UUID, uuid4

import psycopg
import pytest

from salience.cycles import p1_recovery_rollback as rollback
from salience.cycles import production_recovery as recovery
from salience.governance.cost_repository import CostReservationRepository
from salience.observability.tracing import TraceContext
from salience.workflows.persistence import CanonicalJobStore, CanonicalRun
import test_p1_production_recovery_case_opening as opening
from test_p1_production_policy_revocation import accounting, cycles, policy
from test_p1_production_recovery import (
    _ReadTarget,
    _exact_observation,
    _read_context,
    _rollback_v2_readbacks,
    _task9_set_stop_scope,
)


@pytest.fixture(scope="module")
def claimed_database():
    with opening._opening_database(rollback.ROLLBACK_MIGRATION_HEAD_V6) as database:
        yield database


@pytest.fixture
def claimed_publication(claimed_database, monkeypatch, request):
    monkeypatch.setenv("TEST_DATABASE_URL", claimed_database)
    accounting_state = request.getfixturevalue("accounting")
    return seed_claimed_reserved_publication(accounting_state, claimed_database)


def seed_claimed_reserved_publication(accounting_state, database):
    seed_production_case = opening._production_case

    def seed_with_canonical_reservation(*args, **kwargs):
        case = seed_production_case(*args, **kwargs)
        permit = case["request"]
        binding = permit.publication_binding
        effect_key = f"{permit.idempotency_key}:publish"
        with psycopg.connect(database) as connection:
            # Undo the shared fixture's placeholder reservation and use the workflow keys.
            connection.execute("SET LOCAL session_replication_role=replica")
            connection.execute(
                "UPDATE publication_plans SET budget_reservation_id=NULL WHERE id=%s",
                (binding.publication_plan_id,),
            )
            connection.execute(
                "DELETE FROM budget_reservations WHERE id=%s", (permit.budget_reservation_id,),
            )
            connection.execute(
                "UPDATE publication_requests SET request_key=%s WHERE id=%s",
                (permit.idempotency_key, binding.publication_request_id),
            )
            connection.execute(
                "UPDATE publication_attempts SET idempotency_key=%s WHERE id=%s",
                (f"{permit.idempotency_key}:attempt:1", binding.publication_attempt_id),
            )
            connection.execute(
                "UPDATE external_effects SET idempotency_key=%s WHERE id=%s",
                (effect_key, binding.external_effect_id),
            )
        reservation = asyncio.run(CostReservationRepository(database).reserve_for_publication_effect(
            budget_id=str(permit.budget_id), reservation_key=effect_key,
            job_id=str(binding.job_id), external_effect_id=str(binding.external_effect_id),
            publication_plan_id=str(binding.publication_plan_id), estimated_micros=100_000,
        ))
        reservation_id = UUID(reservation.reservation_id)
        with psycopg.connect(database) as connection:
            # The production permit requires a finite reservation. Bind the already seeded
            # fixture approval to the real ledger reservation before issuing any permit.
            connection.execute(
                "UPDATE budget_reservations SET expires_at=clock_timestamp()+interval '1 hour' WHERE id=%s",
                (reservation_id,),
            )
            connection.execute("SET LOCAL session_replication_role=replica")
            connection.execute(
                """UPDATE approval_requests SET request_context=jsonb_set(
                     request_context,'{budget_reservation_id}',to_jsonb(%s::text)) WHERE id=%s""",
                (str(reservation_id), permit.approval_request_id),
            )
        case["request"] = replace(permit, budget_reservation_id=reservation_id)
        case["production_reservation_id"] = reservation_id
        return case

    with pytest.MonkeyPatch.context() as fixture_patch:
        fixture_patch.setattr(opening, "_production_case", seed_with_canonical_reservation)
        case, authority, request_fields = opening.seed_opening_source(accounting_state, database, "unclaimed")
    permit = case["request"]
    binding = permit.publication_binding
    claimed = case["dispatch"].claim_permit(permit.operation_id, case["opening_permit_id"])
    assert claimed.dispatch_allowed and claimed.claim_id is not None
    case["opening_claim_id"] = claimed.claim_id
    run = CanonicalRun(
        permit.workspace_id, permit.program_id, binding.job_id, binding.workflow_run_id,
        TraceContext.new_root(),
    )
    assert asyncio.run(CanonicalJobStore(database).begin_effect_submission(
        run, f"{permit.idempotency_key}:publish",
    ))
    return case, authority, recovery.ProductionRecoveryCaseOpenCommand(**request_fields)


@pytest.mark.parametrize("reservation_expired", [False, True], ids=["current", "expired-after-attempt"])
def test_claimed_reserved_publication_opens_exact_reconciliation_case(claimed_publication, reservation_expired):
    case, authority, command = claimed_publication
    database = case["database"]
    controller = opening._controller(database, authority)
    with psycopg.connect(database) as connection:
        assert connection.execute(
            """SELECT plan.state,reservation.status,effect.status
               FROM publication_plans plan
               JOIN budget_reservations reservation ON reservation.id=plan.budget_reservation_id
               JOIN external_effects effect ON effect.id=plan.external_effect_id
               WHERE plan.id=%s""",
            (case["publication_binding"].publication_plan_id,),
        ).fetchone() == ("reserved", "reserved", "submitting")
        if reservation_expired:
            connection.execute(
                """UPDATE budget_reservations reservation
                   SET expires_at=effect.attempted_at+interval '1 microsecond'
                   FROM external_effects effect
                   WHERE reservation.id=%s AND effect.id=%s""",
                (case["production_reservation_id"], case["publication_binding"].external_effect_id),
            )
    before = opening._publication_liabilities(database, command.operation_id)

    receipt = controller.open_case(command)

    assert (receipt.operation_id, receipt.resulting_revision, receipt.state) == (
        command.operation_id, 1, "reconciling",
    )
    assert controller.open_case(command) == receipt
    assert opening._case_count(database, command.operation_id) == 1
    assert opening._publication_liabilities(database, command.operation_id) == before
    with psycopg.connect(database) as connection:
        for scope in ("workspace", f"goal:{case['goal_id']}"):
            _task9_set_stop_scope(
                connection, command.workspace_id, scope, 2, True, command.actor_id, "rollback-stop",
            )
    with pytest.raises(rollback.RecoveryRollbackVerificationError, match="exact 0058"):
        rollback.read_p1_recovery_rollback_inventory_v5(database, workspace_id=command.workspace_id)
    inventory = rollback.read_p1_recovery_rollback_inventory_v6(database, workspace_id=command.workspace_id)
    assert inventory.migration_head == rollback.ROLLBACK_MIGRATION_HEAD_V6
    route, temporal = _rollback_v2_readbacks(inventory.route_claims)
    verified = rollback.verify_p1_recovery_rollback_v6(
        database, workspace_id=command.workspace_id, route_authority=route, temporal_readback=temporal,
    )
    assert verified.digest == inventory.digest and verified.readback_digest
    assert verified.inventory["relation_counts"]["production_case_opening_receipts"] == 1
    opening_receipt, = verified.inventory["opening_receipts"]
    assert {key: opening_receipt[key] for key in (
        "schema_version", "command_id", "case_id", "work_id", "alert_event_id", "permit_id",
        "claim_id", "external_effect_id", "evidence_state", "request_fingerprint",
    )} == {
        "schema_version": "salience.p1-production-recovery-case-opening.v1",
        "command_id": str(receipt.command_id),
        "case_id": str(receipt.case_id),
        "work_id": str(receipt.work_id),
        "alert_event_id": str(receipt.alert_event_id),
        "permit_id": str(case["opening_permit_id"]),
        "claim_id": str(case["opening_claim_id"]),
        "external_effect_id": str(case["publication_binding"].external_effect_id),
        "evidence_state": "submitting",
        "request_fingerprint": command.request_fingerprint,
    }


def test_rollback_v6_refuses_live_opener_configuration_drift(claimed_publication):
    case, _, command = claimed_publication
    with psycopg.connect(case["database"]) as connection, connection.transaction(force_rollback=True):
        connection.execute(
            "ALTER FUNCTION public.v4_open_production_recovery_case_v1(jsonb,jsonb) SET search_path=public"
        )
        inventory, = connection.execute(
            "SELECT public.v4_p1_recovery_rollback_inventory_v6(%s)", (command.workspace_id,),
        ).fetchone()
        assert {"kind": "production_claimed_case_opener_definition_mismatch"} in inventory["blockers"]
        with pytest.raises(rollback.RecoveryRollbackVerificationError, match="opener_definition_mismatch"):
            rollback.validate_recovery_rollback_inventory_v6(inventory, workspace_id=command.workspace_id)


@pytest.mark.parametrize("drift", ["wrong_reservation", "stale_at_attempt", "wrong_effect"])
def test_claimed_reserved_publication_rejects_reservation_drift(claimed_publication, drift):
    case, authority, command = claimed_publication
    database = case["database"]
    reservation_id = case["production_reservation_id"]
    with psycopg.connect(database) as connection:
        if drift == "wrong_reservation":
            previous_key = connection.execute(
                "SELECT reservation_key FROM budget_reservations WHERE id=%s", (reservation_id,),
            ).fetchone()[0]
            connection.execute(
                "UPDATE budget_reservations SET reservation_key=%s WHERE id=%s",
                (f"displaced:{reservation_id}", reservation_id),
            )
            lookalike_id = connection.execute(
                """INSERT INTO budget_reservations(
                       budget_id,job_id,external_effect_id,reservation_key,estimated_amount,
                       reserved_amount,status,expires_at)
                   SELECT budget_id,job_id,external_effect_id,%s,estimated_amount,reserved_amount,status,expires_at
                   FROM budget_reservations WHERE id=%s RETURNING id""",
                (previous_key, reservation_id),
            ).fetchone()[0]
            connection.execute("SET LOCAL session_replication_role=replica")
            connection.execute(
                "UPDATE publication_plans SET budget_reservation_id=%s WHERE id=%s",
                (lookalike_id, case["publication_binding"].publication_plan_id),
            )
        elif drift == "stale_at_attempt":
            connection.execute(
                """UPDATE budget_reservations reservation SET expires_at=effect.attempted_at
                   FROM external_effects effect
                   WHERE reservation.id=%s AND effect.id=%s""",
                (reservation_id, case["publication_binding"].external_effect_id),
            )
        else:
            connection.execute(
                "UPDATE budget_reservations SET external_effect_id=NULL WHERE id=%s", (reservation_id,),
            )
    with pytest.raises(PermissionError, match="no exact ambiguous submission evidence"):
        opening._controller(database, authority).open_case(command)

    assert opening._case_count(database, command.operation_id) == 0


def test_claimed_reserved_unknown_read_preserves_liability_and_denies_resume(claimed_publication):
    case, authority, command = claimed_publication
    database = case["database"]
    controller = opening._controller(database, authority)
    receipt = controller.open_case(command)
    repository = controller.repository
    work = repository.get_work(receipt.work_id)
    context = _read_context(repository, recovery, work)
    target = _ReadTarget(_exact_observation(recovery, work, context, result="unknown"))
    coordinator = recovery.ProductionRecoveryCoordinator(
        repository, remote_target_factory=lambda _: target, authority=authority,
        provider_contracts={work.target_alias: context.provider_contract},
    )
    before = opening._publication_liabilities(database, command.operation_id)

    read = coordinator.reconcile(work.work_id)

    assert read.result_class == "unknown"
    assert coordinator.reconcile(work.work_id) == read
    sent, = target.requests
    assert (sent.operation_id, sent.idempotency_key, sent.request_fingerprint, sent.read_grant.scope) == (
        command.operation_id, case["request"].idempotency_key, command.request_fingerprint,
        "original_operation_read",
    )
    with pytest.raises(PermissionError, match="not eligible for bounded forward recovery"):
        controller.execute_case_command(recovery.ProductionRecoveryCaseCommand(
            workspace_id=command.workspace_id, work_id=receipt.work_id, case_id=receipt.case_id,
            actor_id=command.actor_id, expected_revision=1, action="resume",
            idempotency_key="unresolved-claimed-reservation",
            approval_request_id=case["request"].approval_request_id,
        ))
    assert repository.get_work(work.work_id) == work
    assert opening._publication_liabilities(database, command.operation_id) == before


def _legacy_read(repository, work, context, result_class, remote_state):
    with repository._connect() as connection:
        return repository._append_event(
            connection, work, event_key=f"remote-read:{work.work_id}:visibility-elapsed",
            event_kind="remote_read_observation", policy_decision_id=uuid4(),
            payload={
                "schema_version": "salience.p1-production-recovery-read.v1",
                "reason_code": "remote_observation",
                "result_class": result_class,
                "remote_state": remote_state,
                "evidence_sha256": "a" * 64,
                "remote_identity_sha256": "b" * 64,
                "operation_id": str(work.operation_id),
                "intent_id": str(work.intent_id),
                "case_id": str(work.case_id),
                "source_id": str(work.source_id),
                "work_id": str(work.work_id),
                "request_fingerprint": work.request_fingerprint,
                "event_key": context.idempotency_key,
                "deadline": context.operation_started_at.isoformat(),
            },
        )


@pytest.mark.parametrize("legacy_observation", [False, True], ids=["fresh", "legacy-absence"])
def test_claimed_reserved_read_requires_finality_for_no_effect(claimed_publication, legacy_observation):
    case, authority, command = claimed_publication
    controller = opening._controller(case["database"], authority)
    repository = controller.repository
    work = repository.get_work(controller.open_case(command).work_id)
    context = _read_context(repository, recovery, work)
    previous = (
        _legacy_read(repository, work, context, "authoritative_no_effect", "not_found")
        if legacy_observation else None
    )
    provider_result = "succeeded" if legacy_observation else "not_found"
    target = _ReadTarget(_exact_observation(recovery, work, context, result=provider_result))
    coordinator = recovery.ProductionRecoveryCoordinator(
        repository, remote_target_factory=lambda _: target, authority=authority,
        provider_contracts={work.target_alias: context.provider_contract},
    )
    before = opening._publication_liabilities(case["database"], command.operation_id)

    read = coordinator.reconcile(work.work_id)

    assert read.result_class == ("terminal_success" if legacy_observation else "unknown")
    assert read.remote_state == provider_result
    assert coordinator.reconcile(work.work_id) == read
    assert len(target.requests) == 1
    if previous is not None:
        assert read.event_id != previous.event_id
        with repository._connect() as connection:
            assert repository._load_event(connection, previous.event_id) == previous
    assert opening._publication_liabilities(case["database"], command.operation_id) == before


def test_claimed_reserved_read_can_find_a_delayed_original_send(claimed_publication):
    case, authority, command = claimed_publication
    controller = opening._controller(case["database"], authority)
    repository = controller.repository
    work = repository.get_work(controller.open_case(command).work_id)
    context = _read_context(repository, recovery, work, visibility_window=timedelta(minutes=5))
    target = _ReadTarget(_exact_observation(recovery, work, context, result="not_found"))
    coordinator = recovery.ProductionRecoveryCoordinator(
        repository, remote_target_factory=lambda _: target, authority=authority,
        provider_contracts={work.target_alias: context.provider_contract},
    )
    first = coordinator.reconcile(work.work_id)
    assert first.result_class == "unknown"
    assert coordinator.reconcile(work.work_id) == first

    with psycopg.connect(case["database"]) as connection:
        connection.execute("SET LOCAL session_replication_role=replica")
        connection.execute(
            """UPDATE v4_production_effect_permit_claims SET claimed_at=clock_timestamp()-interval '6 minutes'
               WHERE claim_id=%s""",
            (case["opening_claim_id"],),
        )
    target.observation = _exact_observation(recovery, work, context, result="succeeded")
    second = coordinator.reconcile(work.work_id)

    assert second.result_class == "terminal_success" and second.event_id != first.event_id
    assert coordinator.reconcile(work.work_id) == second
    assert len(target.requests) == 2


def test_claimed_reserved_read_keeps_known_legacy_success(claimed_publication):
    case, authority, command = claimed_publication
    controller = opening._controller(case["database"], authority)
    repository = controller.repository
    work = repository.get_work(controller.open_case(command).work_id)
    context = _read_context(repository, recovery, work)
    previous = _legacy_read(repository, work, context, "terminal_success", "succeeded")
    target = _ReadTarget(fail=True)
    coordinator = recovery.ProductionRecoveryCoordinator(
        repository, remote_target_factory=lambda _: target, authority=authority,
        provider_contracts={work.target_alias: context.provider_contract},
    )

    read = coordinator.reconcile(work.work_id)

    assert read.result_class == "terminal_success"
    assert read.event_id == previous.event_id
    assert not target.requests


@pytest.mark.parametrize("recorded", [False, True], ids=["authorization", "replay"])
def test_claimed_reserved_legacy_evidence_cannot_authorize_forward_effect(claimed_publication, recorded):
    case, authority, open_command = claimed_publication
    controller = opening._controller(case["database"], authority)
    repository = controller.repository
    work = repository.get_work(controller.open_case(open_command).work_id)
    context = _read_context(repository, recovery, work)
    legacy = _legacy_read(repository, work, context, "authoritative_no_effect", "not_found")
    command = recovery.NoEffectGenerationReissueCommand(
        work_id=work.work_id, case_id=work.case_id, expected_case_revision=1,
        observation_event_id=legacy.event_id, observation_sha256=legacy.payload_sha256,
        operation_id=work.operation_id, request_fingerprint=work.request_fingerprint,
        original_idempotency_key=context.idempotency_key,
        stage_decision_id=uuid4(), policy_decision_id=uuid4(), route_decision_id=uuid4(),
        workspace_stop_revision=1, goal_stop_revision=1, idempotency_key=f"reissue:{work.work_id}:1",
    )
    if recorded:
        identity = sha256(f"{work.operation_id}:{context.idempotency_key}".encode()).hexdigest()
        evidence = sha256(":".join((
            str(legacy.event_id), legacy.payload_sha256, work.request_fingerprint, identity,
            str(command.stage_decision_id), str(command.policy_decision_id), "1", "1",
            str(command.route_decision_id),
        )).encode()).hexdigest()
        with repository._connect() as connection:
            repository._append_event(
                connection, work, event_key=command.idempotency_key,
                event_kind="same_operation_no_effect_reissue", policy_decision_id=command.policy_decision_id,
                payload={
                    "schema_version": "salience.p1-same-operation-no-effect.v1",
                    "reason_code": "authoritative_no_effect",
                    "result_class": "authoritative_no_effect",
                    "evidence_sha256": evidence,
                    "remote_identity_sha256": identity,
                    "operation_id": str(work.operation_id),
                    "case_id": str(work.case_id),
                    "source_id": str(work.source_id),
                    "work_id": str(work.work_id),
                    "state": work.state,
                    "request_fingerprint": work.request_fingerprint,
                    "event_key": command.idempotency_key,
                    "stage_decision_id": str(command.stage_decision_id),
                    "policy_decision_id": str(command.policy_decision_id),
                    "route_decision_id": str(command.route_decision_id),
                    "workspace_stop_revision": 1,
                    "goal_stop_revision": 1,
                    "observation_event_id": str(legacy.event_id),
                    "observation_sha256": legacy.payload_sha256,
                },
            )
    before = opening._publication_liabilities(case["database"], command.operation_id)
    with psycopg.connect(case["database"]) as connection:
        query = "SELECT * FROM v4_production_recovery_events WHERE work_id=%s ORDER BY event_id"
        events = connection.execute(query, (work.work_id,)).fetchall()

        with pytest.raises(PermissionError, match="predecessor finality"):
            controller.authorize_no_effect_generation_reissue(command)

        assert connection.execute(query, (work.work_id,)).fetchall() == events
    assert opening._publication_liabilities(case["database"], command.operation_id) == before
