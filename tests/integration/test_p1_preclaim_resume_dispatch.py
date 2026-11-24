"""Production preclaim recovery uses the canonical permit dispatcher."""

from copy import deepcopy
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest

from salience.cycles import p1_recovery_rollback as rollback
from salience.cycles import production_recovery as recovery
from salience.cycles.production_dispatch import (
    ProductionAuthorizationDenied, ProductionEffectDispatch, RouteFenceDecision,
)
from test_p1_production_policy_revocation import _production_case, accounting, cycles, policy
from test_p1_production_recovery_case_opening import _opening_database, opening_source
from test_p1_production_recovery import _rollback_v2_readbacks, _task9_set_stop_scope


@pytest.fixture(scope="module")
def preclaim_opening_database():
    with _opening_database(rollback.ROLLBACK_MIGRATION_HEAD_V5) as database:
        yield database


def test_preclaim_renewal_requires_a_typed_original_publication_binding(accounting):
    case = _production_case(accounting)
    dispatch = case["dispatch"]
    request = case["request"]
    with pytest.raises(ValueError, match="original production publication permit"):
        dispatch.issue_preclaim_resume_permit(request, predecessor_permit_id=uuid4())
    with pytest.raises(ValueError, match="original production publication permit"):
        dispatch.issue_preclaim_resume_permit(
            replace(request, generation=2, recovery_command_id=uuid4()), predecessor_permit_id=uuid4(),
        )
    assert isinstance(dispatch, ProductionEffectDispatch)


@pytest.mark.parametrize("opening_source", ["preclaim_live"], indirect=True)
def test_route_authority_cannot_supply_the_local_expiry_hint(opening_source, monkeypatch):
    case, _, _ = opening_source
    request = case["request"]
    predecessor = case["opening_permit_id"]

    def spoofed_expiry(action):
        return RouteFenceDecision(
            authorized=False, decision_id=None, reason_code="permit_expired",
            request=action, preclaim_permit_expired=True,
        )

    monkeypatch.setattr(case["route"], "authorize_action", spoofed_expiry)
    with pytest.raises(ProductionAuthorizationDenied, match="route_fence_invalid") as denied:
        case["dispatch"].claim_permit(request.operation_id, predecessor)
    assert not denied.value.preclaim_permit_expired


def _operator_resume(case, authority, opening_fields):
    controller = recovery.ProductionRecoveryCaseController(
        recovery.ProductionRecoveryWorkRepository(case["database"]), authority=authority, commands=None,
    )
    opened = controller.open_case(recovery.ProductionRecoveryCaseOpenCommand(
        **opening_fields, preclaim_permit_id=case["opening_permit_id"],
    ))
    common = dict(
        workspace_id=opening_fields["workspace_id"], actor_id=opening_fields["actor_id"],
        work_id=opened.work_id, case_id=opened.case_id, approval_request_id=case["request"].approval_request_id,
    )
    reviewed = controller.execute_case_command(recovery.ProductionRecoveryCaseCommand(
        **common, action="review_approve", expected_revision=1,
        idempotency_key="only-original-approval", reason="resume the proven unclaimed publication",
    ))
    assert (reviewed.state, reviewed.resulting_revision) == ("retry_due", 2)
    return controller.execute_case_command(recovery.ProductionRecoveryCaseCommand(
        **common, action="resume", expected_revision=2, idempotency_key="original-workflow-resume",
    ))


@pytest.mark.parametrize("opening_source", ["preclaim_expired"], indirect=True)
def test_preclaim_operator_and_dispatch_renew_once_on_the_same_operation(opening_source):
    case, authority, opening_fields = opening_source
    dispatch = case["dispatch"]
    request = case["request"]
    predecessor = case["opening_permit_id"]

    def issue():
        return dispatch.issue_preclaim_resume_permit(request, predecessor_permit_id=predecessor)

    with pytest.raises(ProductionAuthorizationDenied, match="permit_expired") as expired:
        dispatch.claim_permit(request.operation_id, predecessor)
    assert expired.value.preclaim_permit_expired
    assert issue() is None
    resume = _operator_resume(case, authority, opening_fields)
    assert (resume.operation_id, resume.state, resume.resulting_revision) == (request.operation_id, "resolved", 3)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(lambda _: issue(), range(2))
    assert first == second == issue()
    assert (first.generation, first.operation_id, first.claim_id) == (2, request.operation_id, None)
    assert first.permit_id != predecessor
    case["route"].deny = True
    with pytest.raises(ProductionAuthorizationDenied, match="route_fence_denied"):
        issue()
    case["route"].deny = False
    with psycopg.connect(case["database"]) as connection:
        assert connection.execute(
            """SELECT permit_id,generation,request_snapshot->>'idempotency_key',
                      request_snapshot->>'workflow_run_id'
               FROM v4_production_effect_permits WHERE operation_id=%s ORDER BY generation""",
            (request.operation_id,),
        ).fetchall() == [
            (predecessor, 1, request.idempotency_key, request.publication_binding.workflow_run_id),
            (first.permit_id, 2, request.idempotency_key, request.publication_binding.workflow_run_id),
        ]
    assert dispatch.claim_permit(request.operation_id, first.permit_id).dispatch_allowed
    with pytest.raises(ProductionAuthorizationDenied, match="dispatch_already_claimed"):
        dispatch.claim_permit(request.operation_id, first.permit_id)


@pytest.mark.parametrize("opening_source", ["preclaim_expired"], indirect=True)
def test_preclaim_renewal_keeps_authority_atomic_and_checks_evidence_again_at_claim(opening_source, monkeypatch):
    case, authority, fields = opening_source
    dispatch = case["dispatch"]
    request = case["request"]
    predecessor = case["opening_permit_id"]
    _operator_resume(case, authority, fields)

    authority.available = False
    with pytest.raises(ProductionAuthorizationDenied, match="effect_not_allowed"):
        dispatch.issue_preclaim_resume_permit(request, predecessor_permit_id=predecessor)
    authority.available = True
    authorize = authority.authorize_preclaim_resume
    with monkeypatch.context() as patch:
        def wrong_profile(*args, **kwargs):
            return replace(authorize(*args, **kwargs), delivery_profile_id=uuid4())
        patch.setattr(authority, "authorize_preclaim_resume", wrong_profile)
        with pytest.raises(ProductionAuthorizationDenied, match="invalid_binding"):
            dispatch.issue_preclaim_resume_permit(request, predecessor_permit_id=predecessor)

    case["route"].deny = True
    with pytest.raises(ProductionAuthorizationDenied, match="route_fence_denied"):
        dispatch.issue_preclaim_resume_permit(request, predecessor_permit_id=predecessor)
    with psycopg.connect(case["database"]) as connection:
        assert connection.execute(
            """SELECT current_permit_id,generation,current_claim_id
               FROM v4_production_current_dispatch WHERE operation_id=%s""",
            (request.operation_id,),
        ).fetchone() == (predecessor, 1, None)
        assert connection.execute(
            """SELECT count(*) FROM v4_production_recovery_events
               WHERE event_kind='preclaim_permit_resume' AND payload->>'operation_id'=%s""",
            (str(request.operation_id),),
        ).fetchone() == (0,)

    case["route"].deny = False
    successor = dispatch.issue_preclaim_resume_permit(request, predecessor_permit_id=predecessor)
    with psycopg.connect(case["database"]) as connection:
        assert connection.execute(
            """UPDATE external_effects SET attempted_at=clock_timestamp()
               WHERE id=%s AND status='planned'""",
            (request.publication_binding.external_effect_id,),
        ).rowcount == 1

    with pytest.raises(ProductionAuthorizationDenied, match="invalid_binding"):
        dispatch.claim_permit(request.operation_id, successor.permit_id)
    with psycopg.connect(case["database"]) as connection:
        assert connection.execute(
            "SELECT count(*) FROM v4_production_effect_permit_claims WHERE operation_id=%s",
            (request.operation_id,),
        ).fetchone() == (0,)


@pytest.mark.parametrize("opening_source", ["preclaim_expired"], indirect=True)
@pytest.mark.parametrize("renewed", [False, True])
def test_rollback_v5_verifies_preclaim_and_refuses_missing_receipts(opening_source, renewed):
    case, authority, fields = opening_source
    request = case["request"]
    database = case["database"]
    predecessor = case["opening_permit_id"]
    if renewed:
        _operator_resume(case, authority, fields)
        successor = case["dispatch"].issue_preclaim_resume_permit(request, predecessor_permit_id=predecessor)
        assert successor.generation == 2
    else:
        controller = recovery.ProductionRecoveryCaseController(
            recovery.ProductionRecoveryWorkRepository(database), authority=authority, commands=None,
        )
        controller.open_case(recovery.ProductionRecoveryCaseOpenCommand(**fields, preclaim_permit_id=predecessor))

    with psycopg.connect(database) as connection:
        for scope in ("workspace", f"goal:{request.goal_id}"):
            _task9_set_stop_scope(connection, request.workspace_id, scope, 2, True, fields["actor_id"], "rollback-stop")

    inventory = rollback.read_p1_recovery_rollback_inventory_v5(database, workspace_id=request.workspace_id)
    route, temporal = _rollback_v2_readbacks(inventory.route_claims)
    verified = rollback.verify_p1_recovery_rollback_v5(
        database, workspace_id=request.workspace_id, route_authority=route, temporal_readback=temporal,
    )
    assert verified.digest == inventory.digest and verified.readback_digest
    lineage, = verified.inventory["preclaim_lineage"]
    assert lineage["opening"]["permit_id"] == str(predecessor)
    if renewed:
        assert lineage["renewal"]["payload"]["successor_permit_id"] == str(successor.permit_id)
        incomplete = deepcopy(verified.inventory)
        incomplete["preclaim_lineage"][0]["renewal"] = None
        with pytest.raises(rollback.RecoveryRollbackVerificationError, match="omits a renewal proof"):
            rollback.validate_recovery_rollback_inventory_v5(incomplete, workspace_id=request.workspace_id)
    else:
        assert lineage["renewal"] is None
    with pytest.raises(rollback.RecoveryRollbackVerificationError, match="exact 0055"):
        rollback.read_p1_recovery_rollback_inventory_v4(database, workspace_id=request.workspace_id)

    if renewed:
        with psycopg.connect(database) as connection, connection.transaction(force_rollback=True):
            connection.execute("SET LOCAL session_replication_role=replica")
            assert connection.execute(
                """UPDATE v4_production_effect_permits
                   SET request_snapshot=jsonb_set(request_snapshot,'{workspace_stop_revision}','37')
                   WHERE permit_id=%s""",
                (successor.permit_id,),
            ).rowcount == 1
            assert connection.execute(
                """UPDATE v4_cycle_events
                   SET payload=jsonb_set(payload,'{workspace_stop_revision}','37')
                   WHERE cycle_id=%s AND kind='production_permit_issued' AND payload->>'permit_id'=%s""",
                (request.cycle_id, str(successor.permit_id)),
            ).rowcount == 1
            assert connection.execute(
                "SELECT public.v4_p1_rollback_issue_event_matches_v1(%s)", (successor.permit_id,),
            ).fetchone() == (True,)
            assert connection.execute(
                "SELECT public.v4_p1_rollback_preclaim_lineage_v5(%s,%s)",
                (request.workspace_id, lineage["opening"]["work_id"]),
            ).fetchone() == (None,)

    with psycopg.connect(database) as connection:
        connection.execute("SET LOCAL session_replication_role=replica")
        if renewed:
            connection.execute(
                """DELETE FROM v4_production_recovery_events
                   WHERE event_id=%s AND event_kind='preclaim_permit_resume'""",
                (lineage["renewal"]["event_id"],),
            )
        else:
            connection.execute(
                "DELETE FROM v4_case_events WHERE case_id=%s AND action='opened'",
                (lineage["opening"]["case_id"],),
            )
        corrupted = connection.execute(
            "SELECT public.v4_p1_recovery_rollback_inventory_v5(%s)", (request.workspace_id,),
        ).fetchone()[0]
        assert corrupted["preclaim_lineage"] == []
        assert {
            "kind": "production_preclaim_lineage_missing_or_mismatched",
            "work_id": lineage["opening"]["work_id"],
        } in corrupted["blockers"]
