"""Exercise production case creation from durable publication evidence."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
from time import sleep
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb
import pytest

from salience.cycles import production_recovery as recovery
from salience.cycles.production_dispatch import ProductionPreclaimResumeAuthorization, PublicationEffectBinding
from salience.observability.tracing import TraceContext
from salience.publication.repository import PublicationRepository
from salience.workflows.persistence import CanonicalJobStore, CanonicalRun
from test_p1_production_policy_revocation import (
    _production_case, _wait_for_pg_lock, accounting, cycles, policy,
)
from test_p1_production_recovery import (
    _ReadTarget, _RecoveryAuthority, _exact_observation, _read_context, _recovery_policy,
    _task9_set_stop_scope,
)
from test_v4_migrations import (
    _create_migration_database, _drop_migration_database, _migration,
)


_OPEN_FUNCTION = "public.v4_open_production_recovery_case_v1(jsonb,jsonb)"
_PRECLAIM_OPEN_FUNCTION = "public.v4_open_production_preclaim_case_v1(jsonb,jsonb)"
_REVISION = "0054_p1_recovery_case_opening"
_PREVIOUS_REVISION = "0053_p1_recovery_rollback_v3"


class _OpenAuthority(_RecoveryAuthority):
    def __init__(self, bindings, *, database):
        super().__init__(recovery)
        self.bindings = bindings
        self.database = database
        self.available = True
        self.override = {}
        self.before_decision = lambda: None

    def authorize_case_opening(self, command):
        if not self.available:
            raise PermissionError("case opening authority denied")
        self.before_decision()
        return recovery.ProductionRecoveryCaseOpenAuthorization(
            **({
                "command": command,
                "alert_policy": _recovery_policy(recovery),
                "stage_decision_id": uuid4(),
                "route_decision_id": uuid4(),
                "expires_at": datetime.now(timezone.utc) + timedelta(seconds=45),
            } | self.bindings | self.override),
        )

    def authorize_preclaim_resume(self, request, *, predecessor_permit_id, resume_command_id):
        if not self.available:
            raise PermissionError("preclaim renewal stage authority denied")
        with psycopg.connect(self.database) as connection:
            profile_id, profile_version, adapter_version = connection.execute(
                """SELECT profile.id,profile.profile_version,profile.publisher_version
                   FROM publication_requests publication JOIN publisher_capability_profiles profile
                     ON profile.id=publication.publisher_capability_profile_id WHERE publication.id=%s""",
                (request.publication_binding.publication_request_id,),
            ).fetchone()
        return ProductionPreclaimResumeAuthorization(
            request, predecessor_permit_id, resume_command_id, uuid4(), uuid4(),
            profile_id, profile_version, adapter_version, datetime.now(timezone.utc) + timedelta(seconds=30),
        )

    def authorize_case_command(self, command):
        return recovery.ProductionRecoveryCaseAuthorization(
            command=command, policy_decision_id=uuid4(), stage_decision_id=uuid4(),
            route_decision_id=uuid4(), expires_at=datetime.now(timezone.utc) + timedelta(seconds=45),
            **self.bindings,
        )


@pytest.fixture(scope="module")
def opening_database():
    with _opening_database() as database:
        yield database


@pytest.fixture(scope="module")
def preclaim_opening_database():
    with _opening_database() as database:
        yield database


@contextmanager
def _opening_database(revision="head"):
    source = os.environ["TEST_DATABASE_URL"]
    name, database = _create_migration_database(source, "p1_case_open_")
    try:
        result = _migration(database, "upgrade", revision)
        assert result.returncode == 0, result.stdout + result.stderr
        yield database
    finally:
        _drop_migration_database(source, name)


@pytest.fixture
def opening_source(monkeypatch, request):
    source_state = getattr(request, "param", "submitting")
    database_fixture = "preclaim_opening_database" if source_state.startswith("preclaim") else "opening_database"
    opening_database = request.getfixturevalue(database_fixture)
    monkeypatch.setenv("TEST_DATABASE_URL", opening_database)
    accounting_state = request.getfixturevalue("accounting")
    return seed_opening_source(accounting_state, opening_database, source_state)


def seed_opening_source(
    accounting_state, opening_database, source_state, *,
    workflow_id="case-opening-workflow", workflow_run_id=None,
):
    binding = PublicationEffectBinding(
        publication_request_id=uuid4(),
        job_id=uuid4(),
        workflow_id=workflow_id,
        workflow_run_id=workflow_run_id or f"case-opening-run-{uuid4()}",
        publication_plan_id=uuid4(),
        publication_attempt_id=uuid4(),
        external_effect_id=uuid4(),
    )
    case = _production_case(
        accounting_state,
        publication_scope={
            "platform": "p1-local", "destination": "channel:approved",
            "locale": "en-US", "territory": "US", "visibility": "public",
        },
        publication_binding=binding,
        seed_publication_rows=True,
    )
    permit_request = case["request"]
    dispatch = case["dispatch"]
    workspace = permit_request.workspace_id
    actor = uuid4()
    route = permit_request.route
    with psycopg.connect(opening_database) as connection:
        connection.execute(
            """INSERT INTO identity_subjects(id,workspace_id,issuer,subject,expires_at)
               VALUES(%s,%s,'https://case-route.invalid',%s,clock_timestamp()+interval '1 day'),
                     (%s,%s,'https://case-actor.invalid',%s,clock_timestamp()+interval '1 day')""",
            (route.owner_id, workspace, str(route.owner_id), actor, workspace, str(actor)),
        )
        for scope in (
            "production:recovery:case:open",
            "production:recovery:case:review",
            "production:recovery:case:resume",
        ):
            connection.execute(
                """INSERT INTO permission_grants(workspace_id,principal_type,principal_id,
                                                 scope,effect,constraints,expires_at)
                   VALUES(%s,'identity',%s,%s,'allow','{}',clock_timestamp()+interval '1 hour')""",
                (workspace, str(actor), scope),
            )
        if source_state.startswith("preclaim"):
            connection.execute("SET LOCAL session_replication_role=replica")
            connection.execute(
                "UPDATE publication_requests SET request_key=%s WHERE id=%s",
                (permit_request.idempotency_key, binding.publication_request_id),
            )
            connection.execute(
                "UPDATE publication_attempts SET idempotency_key=%s WHERE id=%s",
                (f"{permit_request.idempotency_key}:attempt:1", binding.publication_attempt_id),
            )
            connection.execute(
                "UPDATE external_effects SET idempotency_key=%s WHERE id=%s",
                (f"{permit_request.idempotency_key}:publish", binding.external_effect_id),
            )
            connection.execute(
                """UPDATE budget_reservations
                   SET reservation_key=%s,estimated_amount=0 WHERE id=%s""",
                (f"{permit_request.idempotency_key}:publish", permit_request.budget_reservation_id),
            )
            connection.execute("SET LOCAL session_replication_role=origin")
            connection.execute(
                "UPDATE publication_plans SET state='reserved' WHERE id=%s",
                (binding.publication_plan_id,),
            )
            connection.execute(
                """INSERT INTO cost_ledger_entries(
                       budget_reservation_id,job_id,estimated_amount,usage,recorded_at)
                   VALUES(%s,%s,0,'{"kind":"estimated","estimated_micros":0}',CURRENT_TIMESTAMP)""",
                (permit_request.budget_reservation_id, binding.job_id),
            )
    issued = dispatch.issue_permit(permit_request)
    if source_state not in {"unclaimed", "preclaim_expired", "preclaim_live"}:
        dispatch.claim_permit(permit_request.operation_id, issued.permit_id)
    with psycopg.connect(opening_database) as connection:
        if source_state in {"preclaim_expired", "preclaim_claimed"}:
            # Use fixture-only aging of the immutable permit to avoid waiting for its TTL.
            connection.execute("SET LOCAL session_replication_role=replica")
            connection.execute(
                """UPDATE v4_production_effect_permits SET expires_at=clock_timestamp()
                   WHERE permit_id=%s""",
                (issued.permit_id,),
            )
            connection.execute("SET LOCAL session_replication_role=origin")
        fingerprint, policy_hash, actor_revision = connection.execute(
            """SELECT encode(public.digest(request_snapshot::text,'sha256'),'hex'),
                      policy_document_hash,
                      (SELECT revision FROM identity_subjects WHERE id=%s)
               FROM v4_production_effect_permits WHERE permit_id=%s""",
            (actor, issued.permit_id),
        ).fetchone()
        eligibility = connection.execute(
            "SELECT eligibility_revision FROM v4_cycle_intents WHERE id=%s",
            (permit_request.intent_id,),
        ).fetchone()[0]
        if source_state != "unenrolled":
            connection.execute(
                """INSERT INTO v4_production_intent_enrollments(
                          intent_id,eligibility_revision,workspace_id,goal_id,goal_revision,
                          source_owner_id,subject_id,subject_revision,grant_ids,policy_version_id,
                          policy_document_hash,workspace_stop_revision,goal_stop_revision,artifact_sha256,
                          route_id,slot_id,route_owner_id,fencing_epoch,route_manifest_sha256,
                          external_lock_sha256,route_fence_decision_id)
                   SELECT intent_id,%s,workspace_id,goal_id,goal_revision,%s,subject_id,
                          subject_revision,(SELECT jsonb_agg(grant_id ORDER BY grant_id)
                              FROM jsonb_array_elements_text(grant_ids) grant_id),
                          policy_version_id,policy_document_hash,1,1,
                          artifact_sha256,route_id,slot_id,owner_id,fencing_epoch,route_manifest_sha256,
                          artifact_lock_sha256,route_fence_decision_id
                   FROM v4_production_effect_permits WHERE permit_id=%s""",
                (eligibility, actor, issued.permit_id),
            )
    if source_state not in {"unclaimed", "not_attempted"} and not source_state.startswith("preclaim"):
        run = CanonicalRun(workspace, permit_request.program_id, binding.job_id,
                           binding.workflow_run_id, TraceContext.new_root())
        assert asyncio.run(CanonicalJobStore(opening_database).begin_effect_submission(
            run, f"publication-effect-{permit_request.operation_id}",
        ))
    authority = _OpenAuthority({
        "policy_version_id": permit_request.policy_version_id,
        "policy_document_hash": policy_hash,
        "route_id": route.route_id, "slot_id": route.slot_id, "fencing_epoch": route.fencing_epoch,
        "workspace_stop_revision": 1, "goal_stop_revision": 1, "actor_revision": actor_revision,
    }, database=opening_database)
    if source_state.startswith("preclaim"):
        dispatch.preclaim_resume_authority = authority
    case["opening_permit_id"] = issued.permit_id
    return case, authority, {
        "workspace_id": workspace, "actor_id": actor, "intent_id": permit_request.intent_id,
        "cycle_id": permit_request.cycle_id, "context_id": permit_request.context_id,
        "operation_id": permit_request.operation_id, "eligibility_revision": eligibility,
        "generation": 1, "request_fingerprint": fingerprint,
        "deadline": datetime.now(timezone.utc) + timedelta(minutes=2),
        "idempotency_key": "unknown-publication-case",
    }


def test_open_claimed_publication_creates_one_canonical_case_and_work(opening_source):
    case, authority, request_fields = opening_source
    database = case["database"]
    controller = recovery.ProductionRecoveryCaseController(
        recovery.ProductionRecoveryWorkRepository(database), authority=authority, commands=None,
    )
    open_case = controller.open_case
    command = recovery.ProductionRecoveryCaseOpenCommand(**request_fields)
    liabilities = _publication_liabilities(database, command.operation_id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: open_case(command), range(2)))
    assert second == first
    assert (first.operation_id, first.resulting_revision, first.state) == (
        case["operation_id"], 1, "reconciling",
    )
    authority.available = False
    assert open_case(command) == first
    with pytest.raises(recovery.RecoveryIdentityConflict):
        open_case(replace(command, deadline=command.deadline + timedelta(seconds=1)))
    with pytest.raises(recovery.RecoveryIdentityConflict):
        open_case(replace(command, idempotency_key="another-opening"))

    work = controller.repository.get_work(first.work_id)
    assert (work.case_id, work.context_id, work.operation_id, work.owner_id,
            work.request_fingerprint, work.reason, work.remote_resource_id) == (
        first.case_id, command.context_id, command.operation_id, command.actor_id,
        command.request_fingerprint, "unknown_effect", None,
    )
    with psycopg.connect(database) as connection:
        assert connection.execute(
            """SELECT recovery.kind,recovery.state,cycle.state,cycle.recovery_count,
                      command.action,event.action,work.opening_command_id,alert.event_kind
               FROM v4_recovery_cases recovery
               JOIN v4_cycles cycle ON cycle.id=recovery.target_cycle_id
               JOIN v4_case_commands command ON command.case_id=recovery.id
               JOIN v4_case_events event ON event.case_id=recovery.id
               JOIN v4_production_recovery_work work ON work.source_id=recovery.id
               JOIN v4_production_recovery_events alert ON alert.work_id=work.work_id
               WHERE recovery.operation_id=%s""",
            (command.operation_id,),
        ).fetchall() == [(
            "reconciliation", "reconciling", "reconciling", 1,
            "open", "opened", first.command_id, "alert_intent",
        )]
        assert connection.execute(
            "SELECT status FROM external_effects WHERE id=%s",
            (case["publication_binding"].external_effect_id,),
        ).fetchone() == ("submitting",)
        assert connection.execute(
            "SELECT count(*) FROM v4_production_effect_permits WHERE operation_id=%s",
            (command.operation_id,),
        ).fetchone() == (1,)
    assert _publication_liabilities(database, command.operation_id) == liabilities


@contextmanager
def _restricted_role(database, privileges=()):
    role_name = "p1_case_open_" + uuid4().hex
    role = sql.Identifier(role_name)
    with psycopg.connect(database) as connection:
        connection.execute(sql.SQL("CREATE ROLE {}").format(role))
        for privilege in privileges:
            connection.execute(sql.SQL("GRANT " + privilege + " TO {}").format(role))
    try:
        yield make_conninfo(database, options=f"-c role={role_name}")
    finally:
        with psycopg.connect(database) as connection:
            connection.execute(sql.SQL("DROP OWNED BY {}").format(role))
            connection.execute(sql.SQL("DROP ROLE {}").format(role))


def _controller(database, authority):
    return recovery.ProductionRecoveryCaseController(
        recovery.ProductionRecoveryWorkRepository(database), authority=authority, commands=None,
    )


def _case_count(database, operation_id):
    with psycopg.connect(database) as connection:
        return connection.execute(
            "SELECT count(*) FROM v4_recovery_cases WHERE operation_id=%s", (operation_id,),
        ).fetchone()[0]


def _publication_liabilities(database, operation_id):
    with psycopg.connect(database) as connection:
        return connection.execute(
            """SELECT public.v4_production_recovery_publication_snapshot_v1(%s)
                      - ARRAY['cycle','cycle_events']""",
            (operation_id,),
        ).fetchone()[0]


@pytest.mark.parametrize("opening_source", ["preclaim_expired"], indirect=True)
def test_expired_unclaimed_permit_opens_exact_manual_review_once(opening_source):
    case, authority, request_fields = opening_source
    database = case["database"]
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT to_regprocedure(%s)", (_PRECLAIM_OPEN_FUNCTION,),
        ).fetchone()[0] is not None
    command = recovery.ProductionRecoveryCaseOpenCommand(
        **request_fields, preclaim_permit_id=case["opening_permit_id"],
    )
    with _restricted_role(database) as ungranted:
        with pytest.raises(PermissionError, match="permission denied for function"):
            _controller(ungranted, authority).open_case(command)
    with _restricted_role(database, [f"EXECUTE ON FUNCTION {_PRECLAIM_OPEN_FUNCTION}"]) as granted:
        open_case = _controller(granted, authority).open_case
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = list(pool.map(lambda _: open_case(command), range(2)))
        assert second == first
        authority.available = False
        assert open_case(command) == first
        with pytest.raises(recovery.RecoveryIdentityConflict):
            open_case(replace(command, preclaim_permit_id=uuid4()))
    assert first.state == "awaiting_review"
    binding = case["publication_binding"]
    request = case["request"]
    with psycopg.connect(database) as connection:
        state, count, kind, reason, receipt, work_id, opened, audit, alert_status = connection.execute(
            """SELECT cycle.state,cycle.recovery_count,recovery.kind,recovery.failure_class,command.result,
                      work.work_id,event.payload,audit.payload,alert.payload->>'status'
               FROM v4_recovery_cases recovery
               JOIN v4_cycles cycle ON cycle.id=recovery.target_cycle_id
               JOIN v4_production_recovery_work work ON work.source_id=recovery.id
               JOIN v4_case_commands command ON command.id=work.opening_command_id
               JOIN v4_case_events event ON event.case_id=recovery.id AND event.action='opened'
               JOIN v4_cycle_events audit ON audit.cycle_id=cycle.id AND audit.kind='production_case_open'
               JOIN v4_production_recovery_events alert ON alert.work_id=work.work_id
               WHERE recovery.operation_id=%s""",
            (command.operation_id,),
        ).fetchone()
        assert (state, count, kind, reason, work_id, alert_status) == (
            "retry_due", 1, "manual_review", "manual_review", first.work_id, "pending",
        )
        assert receipt == opened == audit
        expected = {
            "schema_version": "salience.p1-production-preclaim-case-opening.v1",
            "opening_kind": "expired_unclaimed_permit",
            "permit_id": str(case["opening_permit_id"]),
            "claim_id": None,
            "generation": 1,
            "approval_request_id": str(request.approval_request_id),
            "publication_request_id": str(binding.publication_request_id),
            "publication_plan_id": str(binding.publication_plan_id),
            "publication_attempt_id": str(binding.publication_attempt_id),
            "external_effect_id": str(binding.external_effect_id),
            "job_id": str(binding.job_id),
            "workflow_id": binding.workflow_id,
            "workflow_run_id": binding.workflow_run_id,
            "provider_idempotency_key": request.idempotency_key,
            "budget_reservation_id": str(request.budget_reservation_id),
            "request_fingerprint": command.request_fingerprint,
            "evidence_state": "planned",
        }
        assert {key: receipt[key] for key in expected} == expected
        assert connection.execute(
            """SELECT pointer.generation,pointer.current_claim_id,effect.status,effect.attempted_at
               FROM v4_production_current_dispatch pointer
               JOIN external_effects effect ON effect.id=%s WHERE pointer.operation_id=%s""",
            (binding.external_effect_id, command.operation_id),
        ).fetchone() == (1, None, "planned", None)
        blockers = connection.execute(
            "SELECT v4_p1_recovery_rollback_inventory_v4(%s)->'blockers'",
            (command.workspace_id,),
        ).fetchone()[0]
        assert {
            "kind": "production_case_opening_missing_or_mismatched", "work_id": str(first.work_id),
        } in blockers


@pytest.mark.parametrize("opening_source", ["preclaim_live", "preclaim_claimed"], indirect=True)
def test_preclaim_opening_requires_expired_permit_with_no_claim(opening_source):
    case, authority, request_fields = opening_source
    command = recovery.ProductionRecoveryCaseOpenCommand(
        **request_fields, preclaim_permit_id=case["opening_permit_id"],
    )
    with pytest.raises(PermissionError, match="expired unclaimed initial production permit"):
        _controller(case["database"], authority).open_case(command)
    assert _case_count(case["database"], command.operation_id) == 0


@pytest.mark.parametrize("opening_source", ["preclaim_expired"], indirect=True)
@pytest.mark.parametrize("drift", [
    "prior_submission_status", "remote_receipt", "wrong_permit", "closed_run", "fixture_claim",
    "unexpected_cost_evidence",
])
def test_preclaim_opening_holds_when_canonical_publication_evidence_changes(opening_source, drift):
    case, authority, request_fields = opening_source
    database = case["database"]
    binding = case["publication_binding"]
    command = recovery.ProductionRecoveryCaseOpenCommand(
        **request_fields, preclaim_permit_id=case["opening_permit_id"],
    )
    if drift == "wrong_permit":
        command = replace(command, preclaim_permit_id=uuid4())
    elif drift == "remote_receipt":
        asyncio.run(PublicationRepository(database).record_remote_receipt(
            publication_attempt_id=str(binding.publication_attempt_id),
            publisher_id=case["request"].provider_id, remote_id=f"preclaim-known-{uuid4()}",
            state="accepted", safe_metadata_hash="b" * 64, remote_url=None,
        ))
    else:
        with psycopg.connect(database) as connection:
            if drift == "prior_submission_status":
                connection.execute(
                    """INSERT INTO publication_status_events
                         (publication_attempt_id,sequence_no,state,source,safe_payload_hash)
                       VALUES(%s,1,'submitting','workflow',%s)""",
                    (binding.publication_attempt_id, "f" * 64),
                )
            elif drift == "closed_run":
                connection.execute("UPDATE jobs SET state='cancelled' WHERE id=%s", (binding.job_id,))
            elif drift == "unexpected_cost_evidence":
                connection.execute(
                    """INSERT INTO cost_ledger_entries(
                           budget_reservation_id,job_id,estimated_amount,actual_amount,usage,recorded_at)
                       VALUES(%s,%s,0,0,'{"kind":"settlement"}',CURRENT_TIMESTAMP)""",
                    (case["request"].budget_reservation_id, binding.job_id),
                )
            else:
                connection.execute(
                    """INSERT INTO v4_permit_claims(id,cycle_id,operation_id,context_id,request_fingerprint)
                       VALUES(%s,%s,%s,%s,%s)""",
                    (uuid4(), command.cycle_id, command.operation_id, command.context_id, "f" * 64),
                )
    with pytest.raises(PermissionError):
        _controller(database, authority).open_case(command)
    assert _case_count(database, command.operation_id) == 0


@pytest.mark.parametrize("opening_source", ["preclaim_expired"], indirect=True)
def test_preclaim_review_and_resume_require_the_original_workflow_approval(opening_source):
    case, authority, request_fields = opening_source
    database = case["database"]
    command = recovery.ProductionRecoveryCaseOpenCommand(
        **request_fields, preclaim_permit_id=case["opening_permit_id"],
    )
    controller = _controller(database, authority)
    opened = controller.open_case(command)
    original_approval_id = case["request"].approval_request_id
    competing_id = uuid4()
    with psycopg.connect(database) as connection:
        connection.execute(
            """INSERT INTO approval_requests(
                   id,workspace_id,policy_version_id,effect_type,status,request_context,requested_by,job_id)
               SELECT %s,workspace_id,policy_version_id,effect_type,'pending',request_context,requested_by,job_id
               FROM approval_requests WHERE id=%s""",
            (competing_id, original_approval_id),
        )
        connection.execute(
            """UPDATE approval_requests SET status='approved',decided_by='p1-competing-reviewer',
                   decided_at=clock_timestamp() WHERE id=%s""",
            (competing_id,),
        )
    review = recovery.ProductionRecoveryCaseCommand(
        workspace_id=command.workspace_id, work_id=opened.work_id, case_id=opened.case_id,
        actor_id=command.actor_id, expected_revision=1, action="review_approve",
        idempotency_key="preclaim-review", approval_request_id=original_approval_id,
        reason="Confirm operator review of the never-claimed permit",
    )
    resume = replace(review, action="resume", idempotency_key="preclaim-resume", reason=None)
    with pytest.raises(recovery.RecoveryIdentityConflict):
        controller.execute_case_command(resume)
    with pytest.raises(PermissionError, match="requires the original publication workflow approval"):
        controller.execute_case_command(replace(review, approval_request_id=competing_id))
    assert controller.execute_case_command(review).state == "retry_due"
    resume = replace(resume, expected_revision=2)
    result = controller.execute_case_command(resume)
    assert (result.state, result.resulting_revision) == ("resolved", 3)
    with psycopg.connect(database) as connection:
        assert connection.execute(
            """SELECT state,recovery_count FROM v4_cycles WHERE id=%s""",
            (command.cycle_id,),
        ).fetchone() == ("runnable", 1)


@pytest.mark.parametrize("opening_source", ["preclaim_expired"], indirect=True)
def test_preclaim_opening_history_prevents_downgrade(opening_source):
    case, authority, request_fields = opening_source
    controller = _controller(case["database"], authority)
    command = recovery.ProductionRecoveryCaseOpenCommand(
        **request_fields, preclaim_permit_id=case["opening_permit_id"],
    )
    receipt = controller.open_case(command)
    result = _migration(case["database"], "downgrade", "0056_p1_preclaim_permit_resume")
    assert result.returncode != 0
    assert "preserve production preclaim case opening history" in result.stderr
    assert controller.open_case(command) == receipt


def test_only_granted_command_role_can_open_and_unknown_cannot_resume(opening_source):
    case, authority, request_fields = opening_source
    database = case["database"]
    command = recovery.ProductionRecoveryCaseOpenCommand(**request_fields)
    with _restricted_role(database) as ungranted:
        with pytest.raises(PermissionError, match="permission denied for function"):
            _controller(ungranted, authority).open_case(command)
    assert _case_count(database, command.operation_id) == 0

    with _restricted_role(database, [f"EXECUTE ON FUNCTION {_OPEN_FUNCTION}"]) as granted:
        receipt = _controller(granted, authority).open_case(command)
        assert receipt.state == "reconciling"
        with psycopg.connect(granted) as connection:
            with pytest.raises(psycopg.errors.InsufficientPrivilege, match="permission denied for table"):
                connection.execute("SELECT * FROM v4_recovery_cases")

    resume = recovery.ProductionRecoveryCaseCommand(
        workspace_id=command.workspace_id, work_id=receipt.work_id, case_id=receipt.case_id,
        actor_id=command.actor_id, expected_revision=1, action="resume",
        idempotency_key="unresolved-original-operation", approval_request_id=case["request"].approval_request_id,
    )
    with pytest.raises(PermissionError, match="not eligible for bounded forward recovery"):
        _controller(database, authority).execute_case_command(resume)
    assert _case_count(database, command.operation_id) == 1


def test_opening_supplies_the_original_operation_to_read_only_reconciliation(opening_source):
    case, authority, request_fields = opening_source
    command = recovery.ProductionRecoveryCaseOpenCommand(**request_fields)
    database = case["database"]
    controller = _controller(database, authority)
    receipt = controller.open_case(command)
    repository = controller.repository
    work = repository.get_work(receipt.work_id)
    context = _read_context(repository, recovery, work)
    target = _ReadTarget(_exact_observation(recovery, work, context, result="unknown"))
    coordinator = recovery.ProductionRecoveryCoordinator(
        repository, remote_target_factory=lambda _: target, authority=authority,
        provider_contracts={work.target_alias: context.provider_contract},
    )
    before = _publication_liabilities(database, command.operation_id)
    first = coordinator.reconcile(work.work_id)
    assert first.result_class == "unknown"
    assert coordinator.reconcile(work.work_id) == first
    assert len(target.requests) == 1
    sent = target.requests[0]
    assert (sent.operation_id, sent.idempotency_key, sent.request_fingerprint, sent.read_grant.scope) == (
        command.operation_id, case["request"].idempotency_key, command.request_fingerprint,
        "original_operation_read",
    )
    assert repository.get_work(work.work_id) == work
    assert _publication_liabilities(database, command.operation_id) == before


@pytest.mark.parametrize("opening_source", ["unclaimed", "not_attempted", "unenrolled"], indirect=True)
def test_case_opening_denies_incomplete_production_source(opening_source):
    case, authority, request_fields = opening_source
    command = recovery.ProductionRecoveryCaseOpenCommand(**request_fields)
    with pytest.raises(PermissionError):
        _controller(case["database"], authority).open_case(command)
    assert _case_count(case["database"], command.operation_id) == 0


@pytest.mark.parametrize("drift", ["receipt_exists", "closed_cycle", "wrong_generation", "wrong_actor"])
def test_case_opening_denies_changed_or_known_source(opening_source, drift):
    case, authority, request_fields = opening_source
    command = recovery.ProductionRecoveryCaseOpenCommand(**request_fields)
    database = case["database"]
    if drift == "receipt_exists":
        asyncio.run(PublicationRepository(database).record_remote_receipt(
            publication_attempt_id=str(case["publication_binding"].publication_attempt_id),
            publisher_id=case["request"].provider_id, remote_id=f"known-receipt-{uuid4()}",
            state="accepted", safe_metadata_hash="b" * 64, remote_url=None,
        ))
    elif drift == "closed_cycle":
        with psycopg.connect(database) as connection:
            connection.execute(
                """UPDATE v4_cycles SET state='closed',disposition='cancelled',closed_at=clock_timestamp()
                   WHERE id=%s""", (command.cycle_id,),
            )
    elif drift == "wrong_generation":
        command = replace(command, generation=2)
    elif drift == "wrong_actor":
        command = replace(command, actor_id=uuid4())
    with pytest.raises(PermissionError):
        _controller(database, authority).open_case(command)
    assert _case_count(database, command.operation_id) == 0


@pytest.mark.parametrize("deny", ["route", "policy", "expired", "stopped", "revoked", "wrong_command"])
def test_case_opening_rechecks_live_authority_before_writing(opening_source, deny):
    case, authority, request_fields = opening_source
    command = recovery.ProductionRecoveryCaseOpenCommand(**request_fields)
    database = case["database"]
    if deny == "route":
        authority.override = {"fencing_epoch": authority.bindings["fencing_epoch"] + 1}
    elif deny == "policy":
        authority.override = {"policy_document_hash": "f" * 64}
    elif deny == "expired":
        authority.override = {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}
    elif deny == "wrong_command":
        authority.override = {"command": replace(command, context_id=uuid4())}
    else:
        def authority_changed():
            with psycopg.connect(database) as connection:
                if deny == "stopped":
                    _task9_set_stop_scope(
                        connection, command.workspace_id, "workspace", 2, True,
                        command.actor_id, "stop-before-case-opening",
                    )
                else:
                    connection.execute(
                        "UPDATE identity_subjects SET enabled=false WHERE id=%s", (command.actor_id,),
                    )
        authority.before_decision = authority_changed
    with pytest.raises(PermissionError):
        _controller(database, authority).open_case(command)
    assert _case_count(database, command.operation_id) == 0


@pytest.mark.parametrize("change", ["scope", "constraints", "expiry"])
def test_case_opening_denies_drift_in_exact_saved_stage_grant(opening_source, change):
    case, authority, request_fields = opening_source
    command = recovery.ProductionRecoveryCaseOpenCommand(**request_fields)
    database = case["database"]
    assignments = {
        "scope": "scope='unrelated:scope'",
        "constraints": "constraints=jsonb_build_object('other_target',true)",
        "expiry": "expires_at=NULL",
    }
    with psycopg.connect(database) as connection:
        # Model a stale/imported grant that carries its original revision and UUID.
        connection.execute("SET LOCAL session_replication_role=replica")
        assert connection.execute(
            sql.SQL("""UPDATE permission_grants SET {} WHERE workspace_id=%s
                       AND principal_id=%s AND scope='effects:write'""").format(sql.SQL(assignments[change])),
            (command.workspace_id, str(case["request"].subject_id)),
        ).rowcount == 1

    with pytest.raises(PermissionError, match="current production opening policy, stage, route, owner and stop"):
        _controller(database, authority).open_case(command)
    assert _case_count(database, command.operation_id) == 0


def test_case_opening_rolls_back_if_a_blocked_insert_outlasts_authority(opening_source):
    case, authority, request_fields = opening_source
    command = recovery.ProductionRecoveryCaseOpenCommand(**request_fields)
    database = case["database"]
    worker = f"case-open-expiry-{uuid4().hex}"
    controller = _controller(make_conninfo(database, application_name=worker), authority)

    with ThreadPoolExecutor(max_workers=1) as pool, psycopg.connect(database) as blocker:
        blocker.execute("LOCK TABLE v4_case_commands IN SHARE MODE")
        expiry = datetime.now(timezone.utc) + timedelta(seconds=2)
        authority.override = {"expires_at": expiry}
        opening = pool.submit(controller.open_case, command)
        assert _wait_for_pg_lock(database, worker)
        sleep(max(0, (expiry - datetime.now(timezone.utc)).total_seconds()) + 0.02)
        blocker.commit()
        with pytest.raises(PermissionError, match="authority expired during the write"):
            opening.result(timeout=5)

    assert _case_count(database, command.operation_id) == 0
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT state,recovery_count FROM v4_cycles WHERE id=%s", (command.cycle_id,),
        ).fetchone() == ("runnable", 0)


def test_fixture_case_insert_cannot_preempt_production_opening(opening_source):
    case, authority, request_fields = opening_source
    command = recovery.ProductionRecoveryCaseOpenCommand(**request_fields)
    database = case["database"]
    with _restricted_role(database, ["INSERT ON v4_recovery_cases", "SELECT ON v4_goals, v4_cycles, v4_cycle_intents"]) as fixture_role:
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="authorized production case opening"):
            with psycopg.connect(fixture_role) as connection:
                connection.execute(
                    """INSERT INTO v4_recovery_cases(
                              id,workspace_id,goal_id,target_cycle_id,context_id,operation_id,kind,
                              open_fingerprint,failure_class,state,owner_id,reason,deadline,max_actions)
                       VALUES(%s,%s,%s,%s,%s,%s,'reconciliation',%s,'unknown_effect','reconciling',
                              %s,'fabricated case',%s,3)""",
                    (uuid4(), command.workspace_id, case["goal_id"], command.cycle_id, command.context_id,
                     command.operation_id, "c" * 64, command.actor_id, command.deadline),
                )
    assert _case_count(database, command.operation_id) == 0
    assert _controller(database, authority).open_case(command).state == "reconciling"


def test_direct_cycle_event_insert_cannot_counterfeit_opening_receipt(opening_source):
    case, authority, request_fields = opening_source
    command = recovery.ProductionRecoveryCaseOpenCommand(**request_fields)
    database = case["database"]
    fake_case_id = uuid4()
    with _restricted_role(database, ["INSERT ON v4_cycle_events"]) as fixture_role:
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="requires its authorized transaction"):
            with psycopg.connect(fixture_role) as connection:
                connection.execute(
                    "SELECT set_config('salience.production_case_opening_v1',%s,true)", (str(fake_case_id),),
                )

                connection.execute(
                    """INSERT INTO v4_cycle_events(id,workspace_id,subject_id,goal_id,intent_id,cycle_id,
                                                   kind,traceparent,payload)
                       VALUES(%s,%s,%s,%s,%s,%s,'production_case_open',%s,%s)""",
                    (uuid4(), command.workspace_id, command.actor_id, case["goal_id"], command.intent_id,
                     command.cycle_id, TraceContext.new_root().to_carrier()["traceparent"],
                     Jsonb({"case_id": str(fake_case_id)})),
                )


def test_empty_opening_migration_can_be_removed():
    source = os.environ["TEST_DATABASE_URL"]
    name, database = _create_migration_database(source, "p1_open_downgrade_")
    try:
        upgraded = _migration(database, "upgrade", _REVISION)
        assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
        downgraded = _migration(database, "downgrade", _PREVIOUS_REVISION)
        assert downgraded.returncode == 0, downgraded.stdout + downgraded.stderr
        with psycopg.connect(database) as connection:
            assert connection.execute("SELECT to_regprocedure(%s)", (_OPEN_FUNCTION,)).fetchone() == (None,)
    finally:
        _drop_migration_database(source, name)


def test_opening_history_prevents_downgrade(opening_source):
    case, authority, request_fields = opening_source
    command = recovery.ProductionRecoveryCaseOpenCommand(**request_fields)
    controller = _controller(case["database"], authority)
    receipt = controller.open_case(command)
    result = _migration(case["database"], "downgrade", _PREVIOUS_REVISION)
    assert result.returncode != 0
    assert "preserve production case opening history" in result.stderr
    assert controller.open_case(command) == receipt
