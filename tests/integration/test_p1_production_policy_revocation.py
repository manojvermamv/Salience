"""Effects-disabled integration contracts for production authorization."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import time
from threading import Event, Thread
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from salience.cycles.governance import CycleGovernance
from salience.cycles.production_dispatch import (
    ProductionAuthorizationDenied,
    ProductionEffectDispatch,
    ProductionPermitRequest,
    RouteBinding,
    RouteFenceDecision,
    RouteFenceRequest,
)
from salience.observability.production_authorization import ProductionAuthorizationMetrics
from test_v4_cycle_admission import cycles
from test_v4_cadence_policy import policy
from test_v4_cycle_accounting import accounting, admitted as accounting_admitted


class LocalRouteFence:
    """A local contract fake; it does not qualify any target route platform."""

    def __init__(self):
        self.calls: list[RouteFenceRequest] = []
        self.block_entered: Event | None = None
        self.block_release: Event | None = None
        self.deny = False

    def authorize_action(self, request: RouteFenceRequest) -> RouteFenceDecision:
        self.calls.append(request)
        if self.block_entered is not None:
            self.block_entered.set()
            assert self.block_release is not None
            assert self.block_release.wait(timeout=5)
        return RouteFenceDecision(
            authorized=not self.deny,
            decision_id=uuid4() if not self.deny else None,
            reason_code=None if not self.deny else "route_fence_denied",
            request=request,
        )


def _active_policy(connection, workspace_id: UUID, policy_id: UUID) -> tuple[dict, str]:
    expires_at = datetime.now(timezone.utc) + timedelta(hours=2)
    document = {
        "schema_version": "salience.p1-production-policy.v1",
        "allowed_effects": ["write"],
        "approval_required_effects": ["write"],
        "expires_at": expires_at.isoformat(),
        "jurisdiction_refs": [],
    }
    document_hash = sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    connection.execute(
        """INSERT INTO policy_versions(
                  id,workspace_id,policy_name,version,status,document,document_hash,
                  jurisdiction_refs,effective_at)
           VALUES(%s,%s,'p1-production-effect','1','draft',%s,%s,'[]',clock_timestamp())""",
        (policy_id, workspace_id, Jsonb(document), document_hash),
    )
    connection.execute(
        "UPDATE policy_versions SET status='active' WHERE id=%s", (policy_id,)
    )
    return document, document_hash


def _production_case(accounting, *, create_stops=True, approval_ttl_seconds=3600):
    service, goal_id, spec, database = accounting
    with psycopg.connect(database) as connection:
        for scope in ("effects:write", "cycles:stop"):
            connection.execute(
                """INSERT INTO permission_grants(
                          workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at)
                   VALUES(%s,'identity',%s,%s,'allow','{}',clock_timestamp()+interval '1 hour')""",
                (service.workspace_id, str(service.subject_id), scope),
            )
    cycle_id = accounting_admitted(accounting)
    workspace_id, intent_id, context_id, operation_id, context, goal_revision = _read_cycle(
        database, cycle_id
    )
    subject_id = UUID(context["subject_id"])
    program_id = UUID(context["content_program_id"])
    policy_id, approval_id, account_id = uuid4(), uuid4(), uuid4()
    provider_id = "p1-local-provider"
    purpose = "approved-production-effect"
    artifact_sha256 = "a" * 64
    approval_expires_at = datetime.now(timezone.utc) + timedelta(seconds=approval_ttl_seconds)
    required_scopes = ("effects:write",)
    route = RouteBinding(
        deployment_id=uuid4(),
        environment_id=uuid4(),
        route_id=uuid4(),
        slot_id=uuid4(),
        owner_id=uuid4(),
        fencing_epoch=7,
        route_manifest_sha256="d" * 64,
        artifact_lock_sha256="e" * 64,
    )
    route_fence = LocalRouteFence()

    with psycopg.connect(database) as connection:
        if create_stops:
            for scope_key in ("workspace", f"goal:{goal_id}"):
                connection.execute(
                    "INSERT INTO v4_stop_scopes(workspace_id,scope_key) VALUES(%s,%s)",
                    (workspace_id, scope_key),
                )
        _, document_hash = _active_policy(connection, workspace_id, policy_id)
        connection.execute(
            """INSERT INTO publisher_accounts(
                      id,workspace_id,content_program_id,platform,account_key,account_type,
                      external_account_reference,status)
               VALUES(%s,%s,%s,'p1-local',%s,'organization','fixture://account','active')""",
            (account_id, workspace_id, program_id, str(account_id)),
        )
        secret_id = connection.execute(
            """INSERT INTO secret_references(
                      workspace_id,name,reference_uri,required_scopes,data_classification,status)
               VALUES(%s,%s,'fixture://no-secret-material','["effects:write"]','confidential','active')
               RETURNING id""",
            (workspace_id, "p1-local-" + str(account_id)),
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO publisher_connections(
                      publisher_account_id,version,secret_reference_id,required_scopes,
                      granted_scopes,status,expires_at)
               VALUES(%s,1,%s,'["effects:write"]','["effects:write"]','active',
                      clock_timestamp()+interval '1 hour')""",
            (account_id, secret_id),
        )
        connection.execute(
            """INSERT INTO publisher_capability_profiles(
                      workspace_id,publisher_account_id,publisher_id,publisher_version,
                      profile_version,platform,audit_state,observed_at,source_reference,
                      capability_facts,compatibility,expires_at)
               VALUES(%s,%s,%s,'1',1,'p1-local','verified',clock_timestamp(),
                      'fixture://capability-profile','{}','{}',clock_timestamp()+interval '1 hour')""",
            (workspace_id, account_id, provider_id),
        )
        approval_context = {
            "schema_version": "salience.p1-effect-approval.v1",
            "workspace_id": str(workspace_id),
            "program_id": str(program_id),
            "goal_id": str(goal_id),
            "goal_revision": goal_revision,
            "intent_id": str(intent_id),
            "cycle_id": str(cycle_id),
            "context_id": str(context_id),
            "operation_id": str(operation_id),
            "subject_id": str(subject_id),
            "policy_version_id": str(policy_id),
            "policy_document_hash": document_hash,
            "effect_type": "write",
            "purpose": purpose,
            "artifact_sha256": artifact_sha256,
            "account_id": str(account_id),
            "provider_id": provider_id,
            "expires_at": approval_expires_at.isoformat(),
        }
        connection.execute(
            """INSERT INTO approval_requests(
                      id,workspace_id,policy_version_id,effect_type,status,request_context,
                      requested_by)
               VALUES(%s,%s,%s,'write','pending',%s,'p1-local-reviewer')""",
            (approval_id, workspace_id, policy_id, Jsonb(approval_context)),
        )
        connection.execute(
            """UPDATE approval_requests SET status='approved',decided_by='p1-local-reviewer',
                      decided_at=clock_timestamp() WHERE id=%s""",
            (approval_id,),
        )

    request = ProductionPermitRequest(
        workspace_id=workspace_id,
        goal_id=goal_id,
        goal_revision=goal_revision,
        program_id=program_id,
        intent_id=intent_id,
        cycle_id=cycle_id,
        context_id=context_id,
        operation_id=operation_id,
        subject_id=subject_id,
        policy_version_id=policy_id,
        approval_request_id=approval_id,
        required_scopes=required_scopes,
        effect_type="write",
        purpose=purpose,
        artifact_sha256=artifact_sha256,
        account_id=account_id,
        provider_id=provider_id,
        idempotency_key="p1-local-effect:" + str(operation_id),
        route=route,
        execution_deadline=datetime.fromisoformat(context["execution_deadline"]),
        ttl_seconds=30,
    )
    dispatch = ProductionEffectDispatch(database, route_fence)
    return {
        "dispatch": dispatch,
        "request": request,
        "route": route_fence,
        "database": database,
        "cycle_id": cycle_id,
        "operation_id": operation_id,
        "workspace_id": workspace_id,
        "goal_id": goal_id,
        "subject_id": subject_id,
        "service": service,
        "account_id": account_id,
    }


def _read_cycle(database, cycle_id):
    with psycopg.connect(database) as connection:
        row = connection.execute(
            """SELECT goal.workspace_id,cycle.intent_id,cycle.context_id,cycle.operation_id,
                      context.payload,intent.goal_revision
               FROM v4_cycles cycle
               JOIN v4_cycle_intents intent ON intent.id=cycle.intent_id
               JOIN v4_goals goal ON goal.id=intent.goal_id
               JOIN v4_run_contexts context ON context.id=cycle.context_id
               WHERE cycle.id=%s""",
            (cycle_id,),
        ).fetchone()
    return row


def _permit_count(database, operation_id):
    with psycopg.connect(database) as connection:
        return connection.execute(
            "SELECT count(*) FROM v4_production_effect_permits WHERE operation_id=%s",
            (operation_id,),
        ).fetchone()[0]


def _claim_count(database, operation_id):
    with psycopg.connect(database) as connection:
        return connection.execute(
            "SELECT count(*) FROM v4_production_effect_permit_claims WHERE operation_id=%s",
            (operation_id,),
        ).fetchone()[0]


def test_issue_denies_missing_or_wrong_current_authority(accounting):
    case = _production_case(accounting)
    dispatch, request, route = case["dispatch"], case["request"], case["route"]
    with psycopg.connect(case["database"]) as connection:
        connection.execute(
            "UPDATE permission_grants SET effect='deny' WHERE principal_id=%s AND scope='effects:write'",
            (str(case["subject_id"]),),
        )
    with pytest.raises(ProductionAuthorizationDenied):
        dispatch.issue_permit(request)
    assert route.calls == []
    assert _permit_count(case["database"], case["operation_id"]) == 0


@pytest.mark.parametrize(
    "missing",
    ("identity_expired", "grant", "policy", "approval", "baseline", "account", "budget", "stop", "permit"),
)
def test_current_expiry_and_missing_facts_deny_at_database_time(accounting, missing):
    fresh = _production_case(accounting, create_stops=missing != "stop")
    if missing == "permit":
        issued = fresh["dispatch"].issue_permit(
            replace(fresh["request"], ttl_seconds=1)
        )
        time.sleep(1.05)
        with pytest.raises(ProductionAuthorizationDenied, match="permit_expired"):
            fresh["dispatch"].claim_permit(fresh["operation_id"], issued.permit_id)
        assert _claim_count(fresh["database"], fresh["operation_id"]) == 0
        return

    with psycopg.connect(fresh["database"]) as connection:
        if missing == "identity_expired":
            connection.execute(
                "UPDATE identity_subjects SET expires_at=clock_timestamp()-interval '1 second' WHERE id=%s",
                (fresh["subject_id"],),
            )
        elif missing == "grant":
            connection.execute(
                "DELETE FROM permission_grants WHERE workspace_id=%s AND principal_id=%s AND scope='effects:write'",
                (fresh["workspace_id"], str(fresh["subject_id"])),
            )
        elif missing == "policy":
            connection.execute(
                "UPDATE policy_versions SET status='revoked' WHERE id=%s",
                (fresh["request"].policy_version_id,),
            )
        elif missing == "approval":
            operation = str(fresh["operation_id"])
            policy_id = str(fresh["request"].policy_version_id)
            transaction_id = str(connection.execute("SELECT txid_current()").fetchone()[0])
            connection.execute(
                """INSERT INTO v4_cycle_events(
                          id,workspace_id,subject_id,goal_id,intent_id,cycle_id,kind,
                          traceparent,payload)
                   VALUES(%s,%s,%s,%s,(SELECT intent_id FROM v4_cycles WHERE id=%s),%s,
                          'production_approval_revoked',
                          '00-11111111111111111111111111111111-2222222222222222-01',%s)""",
                (
                    uuid4(), fresh["workspace_id"], fresh["subject_id"], fresh["goal_id"],
                    fresh["cycle_id"], fresh["cycle_id"],
                    Jsonb({
                        "schema_version": "salience.p1-effect-approval-revocation.v1",
                        "approval_request_id": str(fresh["request"].approval_request_id),
                        "operation_id": operation,
                        "policy_version_id": policy_id,
                        "transaction_id": transaction_id,
                    }),
                ),
            )
            connection.execute(
                "UPDATE approval_requests SET status='revoked' WHERE id=%s",
                (fresh["request"].approval_request_id,),
            )
        elif missing == "baseline":
            connection.execute(
                "INSERT INTO v4_baseline_revocations(approval_id,subject_id,reason,traceparent) "
                "SELECT approval_id,subject_id,'revoked for test',traceparent "
                "FROM v4_goal_baselines WHERE goal_id=%s",
                (fresh["goal_id"],),
            )
        elif missing == "account":
            connection.execute(
                "UPDATE publisher_accounts SET status='disabled' WHERE id=%s",
                (fresh["account_id"],),
            )
        elif missing == "budget":
            connection.execute(
                """UPDATE budgets SET status='disabled'
                   WHERE id IN (SELECT unnest(budget_ids) FROM v4_cycle_allocations WHERE cycle_id=%s)""",
                (fresh["cycle_id"],),
            )

    with pytest.raises(ProductionAuthorizationDenied):
        fresh["dispatch"].issue_permit(fresh["request"])
    assert fresh["route"].calls == []
    assert _permit_count(fresh["database"], fresh["operation_id"]) == 0


@pytest.mark.parametrize("expired", ["identity", "grant", "policy", "approval"])
def test_expired_current_authority_is_evaluated_with_database_time(accounting, expired):
    case = _production_case(
        accounting, approval_ttl_seconds=-1 if expired == "approval" else 3600
    )
    dispatch, request = case["dispatch"], case["request"]
    with psycopg.connect(case["database"]) as connection:
        if expired == "identity":
            connection.execute(
                "UPDATE identity_subjects SET expires_at=clock_timestamp()-interval '1 second' WHERE id=%s",
                (case["subject_id"],),
            )
        elif expired == "grant":
            connection.execute(
                "UPDATE permission_grants SET expires_at=clock_timestamp()-interval '1 second' WHERE workspace_id=%s AND principal_id=%s AND scope='effects:write'",
                (case["workspace_id"], str(case["subject_id"])),
            )
        elif expired == "policy":
            # The active document is immutable; insert a separately expired canonical version.
            expired_policy_id = uuid4()
            doc = {
                "schema_version": "salience.p1-production-policy.v1",
                "allowed_effects": ["write"],
                "approval_required_effects": ["write"],
                "expires_at": (datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),
                "jurisdiction_refs": [],
            }
            digest = sha256(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            connection.execute(
                """INSERT INTO policy_versions(
                          id,workspace_id,policy_name,version,status,document,document_hash,
                          jurisdiction_refs,effective_at)
                   VALUES(%s,%s,'expired-policy','1','draft',%s,%s,'[]',clock_timestamp())""",
                (expired_policy_id, case["workspace_id"], Jsonb(doc), digest),
            )
            connection.execute("UPDATE policy_versions SET status='active' WHERE id=%s", (expired_policy_id,))
        elif expired == "approval":
            pass
    if expired == "policy":
        request = replace(request, policy_version_id=expired_policy_id)
        with pytest.raises(ProductionAuthorizationDenied, match="policy_expired"):
            dispatch.issue_permit(request)
    elif expired == "approval":
        with pytest.raises(ProductionAuthorizationDenied, match="approval_expired"):
            dispatch.issue_permit(request)
    else:
        with pytest.raises(ProductionAuthorizationDenied):
            dispatch.issue_permit(request)
    assert case["route"].calls == []
    assert _permit_count(case["database"], case["operation_id"]) == 0

    # The remaining authority subcases are parameterized so each receives an isolated operation.
    # This explicit assertion prevents a passing stale snapshot from reaching route authorization.



def test_revoke_then_regrant_cannot_reuse_old_permit(accounting):
    case = _production_case(accounting)
    issued = case["dispatch"].issue_permit(case["request"])
    with psycopg.connect(case["database"]) as connection:
        connection.execute(
            "UPDATE permission_grants SET effect='deny' WHERE workspace_id=%s AND principal_id=%s AND scope='effects:write'",
            (case["workspace_id"], str(case["subject_id"])),
        )
        connection.execute(
            "UPDATE permission_grants SET effect='allow' WHERE workspace_id=%s AND principal_id=%s AND scope='effects:write'",
            (case["workspace_id"], str(case["subject_id"])),
        )
    with pytest.raises(ProductionAuthorizationDenied):
        case["dispatch"].claim_permit(case["operation_id"], issued.permit_id)
    assert _claim_count(case["database"], case["operation_id"]) == 0


def test_stop_revoke_claim_race_and_duplicate_workers(accounting):
    case = _production_case(accounting)
    governance = CycleGovernance(
        case["database"], workspace_id=case["workspace_id"], subject_id=case["subject_id"]
    )
    issued = case["dispatch"].issue_permit(case["request"])
    route_entered, route_release, stop_started = Event(), Event(), Event()
    case["route"].block_entered = route_entered
    case["route"].block_release = route_release
    outcome: dict[str, object] = {}

    def claim():
        try:
            outcome["claim"] = case["dispatch"].claim_permit(case["operation_id"], issued.permit_id)
        except PermissionError as error:
            outcome["claim_error"] = error

    def stop():
        stop_started.set()
        governance.set_stop(
            goal_id=case["goal_id"], stopped=True, expected_revision=1,
            idempotency_key="p1-stop-race", reason="stop wins next action",
        )

    claim_thread = Thread(target=claim)
    claim_thread.start()
    assert route_entered.wait(timeout=5)
    stop_thread = Thread(target=stop)
    stop_thread.start()
    assert stop_started.wait(timeout=5)
    route_release.set()
    claim_thread.join(timeout=5)
    stop_thread.join(timeout=5)
    assert not claim_thread.is_alive() and not stop_thread.is_alive()
    assert _claim_count(case["database"], case["operation_id"]) <= 1

    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(
            pool.map(
                lambda _: _claim_or_deny(case, issued.permit_id),
                range(4),
            )
        )
    assert sum(outcomes) <= 1
    if "claim" in outcome:
        denied = case["dispatch"].authorize_action(
            case["operation_id"], issued.permit_id, "submit"
        )
        assert denied.authorized is False
        assert denied.reason_code == "stop_active"


@pytest.mark.parametrize("boundary", ("claim", "action"))
def test_stop_clear_does_not_revive_old_permit(accounting, boundary):
    case = _production_case(accounting)
    governance = CycleGovernance(
        case["database"], workspace_id=case["workspace_id"], subject_id=case["subject_id"]
    )
    issued = case["dispatch"].issue_permit(case["request"])
    if boundary == "action":
        case["dispatch"].claim_permit(case["operation_id"], issued.permit_id)

    governance.set_stop(
        goal_id=case["goal_id"], stopped=True, expected_revision=1,
        idempotency_key="p1-stop-clear-revoke", reason="revoke old permit",
    )
    governance.set_stop(
        goal_id=case["goal_id"], stopped=False, expected_revision=2,
        idempotency_key="p1-stop-clear-command", reason="clear without reviving permit",
    )

    if boundary == "claim":
        with pytest.raises(ProductionAuthorizationDenied, match="stale_revision"):
            case["dispatch"].claim_permit(case["operation_id"], issued.permit_id)
        with psycopg.connect(case["database"]) as connection:
            event = connection.execute(
                "SELECT payload FROM v4_cycle_events WHERE cycle_id=%s "
                "AND kind='production_authorization_denied' ORDER BY created_at DESC LIMIT 1",
                (case["cycle_id"],),
            ).fetchone()
        assert event is not None
        assert event[0]["operation_id"] == str(case["operation_id"])
        assert event[0]["permit_id"] == str(issued.permit_id)
        assert event[0]["action"] == "claim"
        assert event[0]["reason_code"] == "stale_revision"
    else:
        decision = case["dispatch"].authorize_action(
            case["operation_id"], issued.permit_id, "submit"
        )
        assert decision.authorized is False and decision.reason_code == "stale_revision"
        with psycopg.connect(case["database"]) as connection:
            event = connection.execute(
                "SELECT payload FROM v4_cycle_events WHERE cycle_id=%s "
                "AND kind='production_authorization_denied' ORDER BY created_at DESC LIMIT 1",
                (case["cycle_id"],),
            ).fetchone()
            claim_id = connection.execute(
                "SELECT current_claim_id FROM v4_production_current_dispatch WHERE operation_id=%s",
                (case["operation_id"],),
            ).fetchone()[0]
        assert event is not None
        assert event[0]["operation_id"] == str(case["operation_id"])
        assert event[0]["permit_id"] == str(issued.permit_id)
        assert event[0]["claim_id"] == str(claim_id)
        assert event[0]["action"] == "submit"
        assert event[0]["reason_code"] == "stale_revision"
    assert [call.action_kind for call in case["route"].calls] == (
        ["issue"] if boundary == "claim" else ["issue", "claim"]
    )
    assert _claim_count(case["database"], case["operation_id"]) == (
        0 if boundary == "claim" else 1
    )


def _claim_or_deny(case, permit_id):
    try:
        return case["dispatch"].claim_permit(case["operation_id"], permit_id).dispatch_allowed
    except PermissionError:
        return False


def test_each_action_requires_a_fresh_route_decision(accounting):
    case = _production_case(accounting)
    issued = case["dispatch"].issue_permit(case["request"])
    claimed = case["dispatch"].claim_permit(case["operation_id"], issued.permit_id)
    assert claimed.dispatch_allowed is True
    first = case["dispatch"].authorize_action(case["operation_id"], issued.permit_id, "submit")
    second = case["dispatch"].authorize_action(case["operation_id"], issued.permit_id, "receipt")
    assert first.authorized and second.authorized
    assert first.decision_id != second.decision_id
    assert [call.action_kind for call in case["route"].calls] == ["issue", "claim", "submit", "receipt"]
    assert all(call.operation_id == case["operation_id"] for call in case["route"].calls)
    case["route"].deny = True
    denied = case["dispatch"].authorize_action(case["operation_id"], issued.permit_id, "finalize")
    assert denied.authorized is False and denied.reason_code == "route_fence_denied"
    assert _claim_count(case["database"], case["operation_id"]) == 1


def test_crash_before_claim_reloads_current_authority(accounting):
    case = _production_case(accounting)
    issued = case["dispatch"].issue_permit(case["request"])
    with psycopg.connect(case["database"]) as connection:
        connection.execute(
            "UPDATE permission_grants SET effect='deny' WHERE workspace_id=%s AND principal_id=%s AND scope='effects:write'",
            (case["workspace_id"], str(case["subject_id"])),
        )
    restarted = ProductionEffectDispatch(case["database"], case["route"])
    with pytest.raises(ProductionAuthorizationDenied):
        restarted.claim_permit(case["operation_id"], issued.permit_id)
    assert _permit_count(case["database"], case["operation_id"]) == 1
    assert _claim_count(case["database"], case["operation_id"]) == 0


def test_primary_outage_denies_and_preserves_claimed_liability(accounting):
    case = _production_case(accounting)
    issued = case["dispatch"].issue_permit(case["request"])
    claim = case["dispatch"].claim_permit(case["operation_id"], issued.permit_id)
    outage = ProductionEffectDispatch(
        "postgresql://127.0.0.1:1/unreachable?connect_timeout=1", case["route"]
    )
    with pytest.raises((psycopg.OperationalError, ProductionAuthorizationDenied)):
        outage.authorize_action(case["operation_id"], issued.permit_id, "submit")
    with psycopg.connect(case["database"]) as connection:
        pointer = connection.execute(
            "SELECT current_permit_id,current_claim_id,idempotency_key "
            "FROM v4_production_current_dispatch WHERE operation_id=%s",
            (case["operation_id"],),
        ).fetchone()
    assert pointer == (issued.permit_id, claim.claim_id, case["request"].idempotency_key)


def test_provider_substitution_and_cross_workspace_are_denied(accounting):
    case = _production_case(accounting)
    for changed in (
        replace(case["request"], provider_id="other-provider"),
        replace(case["request"], workspace_id=uuid4()),
    ):
        with pytest.raises(ProductionAuthorizationDenied):
            case["dispatch"].issue_permit(changed)
        assert case["route"].calls == []
        assert _permit_count(case["database"], case["operation_id"]) == 0


def test_issue_claim_event_is_atomic_with_permit_history(accounting, monkeypatch):
    case = _production_case(accounting)
    dispatch = case["dispatch"]
    original_event = dispatch._write_event

    def crash(*args, **kwargs):
        raise RuntimeError("simulated crash before permit transaction commit")

    monkeypatch.setattr(dispatch, "_write_event", crash)
    with pytest.raises(RuntimeError, match="before permit transaction"):
        dispatch.issue_permit(case["request"])
    assert _permit_count(case["database"], case["operation_id"]) == 0
    assert _claim_count(case["database"], case["operation_id"]) == 0
    with psycopg.connect(case["database"]) as connection:
        assert connection.execute(
            "SELECT count(*) FROM v4_production_current_dispatch WHERE operation_id=%s",
            (case["operation_id"],),
        ).fetchone()[0] == 0
    monkeypatch.setattr(dispatch, "_write_event", original_event)
    issued = dispatch.issue_permit(case["request"])
    claimed = dispatch.claim_permit(case["operation_id"], issued.permit_id)
    assert claimed.claim_id is not None
    with psycopg.connect(case["database"]) as connection:
        events = connection.execute(
            "SELECT kind,payload FROM v4_cycle_events WHERE cycle_id=%s "
            "AND kind LIKE 'production_%%' ORDER BY created_at",
            (case["cycle_id"],),
        ).fetchall()
        snapshot = connection.execute(
            "SELECT request_snapshot,route_fence_decision_id FROM v4_production_effect_permits WHERE permit_id=%s",
            (issued.permit_id,),
        ).fetchone()
        claim_row = connection.execute(
            "SELECT route_fence_decision_id FROM v4_production_effect_permit_claims WHERE claim_id=%s",
            (claimed.claim_id,),
        ).fetchone()
    assert [event[0] for event in events] == ["production_permit_issued", "production_permit_claimed"]
    assert all(event[1]["operation_id"] == str(case["operation_id"]) for event in events)
    assert snapshot[0]["policy_version_id"] == str(case["request"].policy_version_id)
    assert str(snapshot[1]) == events[0][1]["decision_id"]
    assert str(claim_row[0]) == events[1][1]["decision_id"]


@pytest.mark.parametrize("boundary", ("issue", "claim"))
def test_issue_and_claim_route_denials_are_audited(accounting, boundary):
    case = _production_case(accounting)
    if boundary == "issue":
        case["route"].deny = True
        with pytest.raises(ProductionAuthorizationDenied, match="route_fence_denied"):
            case["dispatch"].issue_permit(case["request"])
        expected_permits, expected_claims = 0, 0
    else:
        issued = case["dispatch"].issue_permit(case["request"])
        case["route"].deny = True
        with pytest.raises(ProductionAuthorizationDenied, match="route_fence_denied"):
            case["dispatch"].claim_permit(case["operation_id"], issued.permit_id)
        expected_permits, expected_claims = 1, 0

    with psycopg.connect(case["database"]) as connection:
        event = connection.execute(
            "SELECT payload FROM v4_cycle_events WHERE cycle_id=%s "
            "AND kind='production_authorization_denied' ORDER BY created_at DESC LIMIT 1",
            (case["cycle_id"],),
        ).fetchone()
    assert event is not None
    assert event[0]["operation_id"] == str(case["operation_id"])
    assert event[0]["action"] == boundary
    assert event[0]["outcome"] == "deny"
    assert event[0]["reason_code"] == "route_fence_denied"
    assert event[0]["decision_id"] is None
    assert _permit_count(case["database"], case["operation_id"]) == expected_permits
    assert _claim_count(case["database"], case["operation_id"]) == expected_claims


def test_production_authorization_metrics_use_bounded_redacted_labels():
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    metrics = ProductionAuthorizationMetrics(provider.get_meter("p1-test"))
    private_identifier = str(uuid4())
    metrics.record_decision(
        action=private_identifier,
        effect="write",
        outcome="deny",
        reason_code=private_identifier,
    )
    metrics.record_stop_to_denial(0.25)
    metrics.record_stale_revision()
    metrics.record_expiry()
    metrics.record_primary_outage()
    metrics.record_signal_lag()
    metrics.record_signal_failure()
    metrics.record_duplicate_claim()
    metrics.record_unknown_outcome()
    metrics.record_unresolved_liability()
    data = reader.get_metrics_data()
    encoded = repr(data)
    assert private_identifier not in encoded
    attributes = [
        point.attributes
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        for point in metric.data.data_points
    ]
    assert attributes
    assert all(len(point) <= 4 for point in attributes)
    assert all(
        value in {"other", "write", "deny", "stale_revision", "expiry", "primary_outage",
                  "signal_lag", "signal_failure", "duplicate_claim", "unknown_outcome",
                  "unresolved_liability"}
        for point in attributes
        for value in point.values()
    )
