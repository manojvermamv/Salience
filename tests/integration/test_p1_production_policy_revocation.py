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
    PublicationEffectBinding,
    ProductionPermitRequest,
    RouteBinding,
    RouteFenceDecision,
    RouteFenceRequest,
)
from salience.observability.production_authorization import ProductionAuthorizationMetrics
from test_v4_cycle_admission import cycles
from test_v4_cadence_policy import policy, request as cadence_request
from test_v4_cycle_accounting import accounting, admitted as accounting_admitted


class LocalRouteFence:
    """A local contract fake; it does not qualify any target route platform."""

    def __init__(self):
        self.calls: list[RouteFenceRequest] = []
        self.block_entered: Event | None = None
        self.block_release: Event | None = None
        self.delay_seconds = 0.0
        self.deny = False

    def authorize_action(self, request: RouteFenceRequest) -> RouteFenceDecision:
        self.calls.append(request)
        if self.block_entered is not None:
            self.block_entered.set()
            assert self.block_release is not None
            assert self.block_release.wait(timeout=5)
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        return RouteFenceDecision(
            authorized=not self.deny,
            decision_id=uuid4() if not self.deny else None,
            reason_code=None if not self.deny else "route_fence_denied",
            request=request,
        )


class RecordingAuthorizationMetrics(ProductionAuthorizationMetrics):
    def __init__(self, meter):
        super().__init__(meter)
        self.stop_to_denial_seconds: list[float] = []

    def record_stop_to_denial(self, seconds: float) -> None:
        self.stop_to_denial_seconds.append(seconds)
        super().record_stop_to_denial(seconds)


def _active_policy(
    connection, workspace_id: UUID, policy_id: UUID, *, publication_scope=None
) -> tuple[dict, str]:
    expires_at = datetime.now(timezone.utc) + timedelta(hours=2)
    document = {
        "schema_version": "salience.p1-production-policy.v1",
        "allowed_effects": ["write"],
        "approval_required_effects": ["write"],
        "expires_at": expires_at.isoformat(),
        "jurisdiction_refs": [],
    }
    if publication_scope is not None:
        document["publication_scope"] = publication_scope
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


def _production_case(
    accounting,
    *,
    create_stops=True,
    approval_ttl_seconds=3600,
    intent_ttl_seconds=None,
    production_budget_ttl_seconds=3600,
    publication_scope=None,
    publication_binding=None,
):
    service, goal_id, spec, database = accounting
    with psycopg.connect(database) as connection:
        for scope in ("effects:write", "cycles:stop"):
            connection.execute(
                """INSERT INTO permission_grants(
                          workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at)
                   VALUES(%s,'identity',%s,%s,'allow','{}',clock_timestamp()+interval '1 hour')""",
                (service.workspace_id, str(service.subject_id), scope),
            )
    if intent_ttl_seconds is None:
        cycle_id = accounting_admitted(accounting)
    else:
        # Produce a short-lived intent through the supported cadence path. V4
        # intent identity is immutable, so tests must not rewrite expires_at.
        short_cadence = spec.cadence.model_copy(
            update={
                "interval_seconds": 1,
                "stale_after_seconds": 4,
                "backfill_seconds": 4,
                "intent_ttl_seconds": 5,
            }
        )
        spec = spec.model_copy(update={"cadence": short_cadence})
        service.revise_goal(
            goal_id,
            spec,
            expected_revision=1,
            idempotency_key="production-deadline-cadence",
            reason="Bound the fixture intent deadline for route-fence testing",
        )
        service.approve_baseline(
            goal_id,
            expected_revision=2,
            expires_at=spec.horizon_end,
            reason="Approve the bounded production-deadline fixture revision",
        )
        command = cadence_request(spec, key="production-deadline-cycle").model_copy(
            update={
                "expected_revision": 2,
                "slot_time": datetime.now(timezone.utc).replace(microsecond=0),
            }
        )
        cycle_id = service.admit(service.request_cycle(goal_id, command))["cycle_id"]
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
        _, document_hash = _active_policy(
            connection,
            workspace_id,
            policy_id,
            publication_scope=publication_scope,
        )
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
        production_budget_id, production_reservation_id = uuid4(), uuid4()
        connection.execute(
            """INSERT INTO budgets(
                      id,workspace_id,content_program_id,name,scope,currency,limit_amount,
                      period_start,period_end,status)
               VALUES(%s,%s,%s,%s,%s,'USD',1.000000,
                      clock_timestamp()-interval '1 hour',
                      clock_timestamp()+(%s * interval '1 second'),'active')""",
            (
                production_budget_id,
                workspace_id,
                program_id,
                "p1-production-" + str(account_id),
                "publisher_account:" + str(account_id),
                production_budget_ttl_seconds,
            ),
        )
        if publication_binding is not None:
            connection.execute(
                """INSERT INTO jobs(
                          id,workspace_id,content_program_id,job_type,state,workflow_run_id,
                          task_queue,idempotency_key,input_payload,dry_run,trace_id)
                   VALUES(%s,%s,%s,'publication','queued',%s,'p1-local',%s,'{}',TRUE,%s)""",
                (
                    publication_binding.job_id,
                    workspace_id,
                    program_id,
                    publication_binding.workflow_run_id,
                    "p1-publication-" + str(operation_id),
                    "a" * 32,
                ),
            )
        connection.execute(
            """INSERT INTO budget_reservations(
                      id,budget_id,reservation_key,estimated_amount,reserved_amount,status,expires_at)
               VALUES(%s,%s,%s,0.100000,0.100000,'reserved',
                      clock_timestamp()+(%s * interval '1 second'))""",
            (
                production_reservation_id,
                production_budget_id,
                "p1-production-" + str(operation_id),
                production_budget_ttl_seconds,
            ),
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
            "required_scopes": list(required_scopes),
            "budget_id": str(production_budget_id),
            "budget_reservation_id": str(production_reservation_id),
            "expires_at": approval_expires_at.isoformat(),
        }
        if publication_binding is not None:
            approval_context.update(
                {
                    "publication_request_id": str(
                        publication_binding.publication_request_id
                    ),
                    "job_id": str(publication_binding.job_id),
                    "workflow_id": publication_binding.workflow_id,
                    "workflow_run_id": publication_binding.workflow_run_id,
                    "publication_plan_id": str(
                        publication_binding.publication_plan_id
                    ),
                    "publication_attempt_id": str(
                        publication_binding.publication_attempt_id
                    ),
                    "external_effect_id": str(
                        publication_binding.external_effect_id
                    ),
                }
            )
        connection.execute(
            """INSERT INTO approval_requests(
                      id,workspace_id,policy_version_id,effect_type,status,request_context,
                      requested_by,job_id)
               VALUES(%s,%s,%s,'write','pending',%s,'p1-local-reviewer',%s)""",
            (
                approval_id,
                workspace_id,
                policy_id,
                Jsonb(approval_context),
                publication_binding.job_id if publication_binding else None,
            ),
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
        budget_id=production_budget_id,
        budget_reservation_id=production_reservation_id,
        idempotency_key="p1-local-effect:" + str(operation_id),
        route=route,
        execution_deadline=datetime.fromisoformat(context["execution_deadline"]),
        ttl_seconds=30,
        publication_binding=publication_binding,
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
        "production_budget_id": production_budget_id,
        "production_reservation_id": production_reservation_id,
        "publication_binding": publication_binding,
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
    with psycopg.connect(case["database"]) as connection:
        event = connection.execute(
            "SELECT payload FROM v4_cycle_events WHERE cycle_id=%s "
            "AND kind='production_authorization_denied' ORDER BY created_at DESC LIMIT 1",
            (case["cycle_id"],),
        ).fetchone()
    assert event is not None
    assert event[0]["action"] == "issue"
    assert event[0]["required_scopes"] == ["effects:write"]


def test_request_cannot_substitute_or_omit_approved_effect_scopes(accounting):
    case = _production_case(accounting)
    for scopes in (("cycles:write",), ("effects:write", "cycles:write")):
        changed = replace(case["request"], required_scopes=scopes)
        with pytest.raises(ProductionAuthorizationDenied):
            case["dispatch"].issue_permit(changed)
        assert case["route"].calls == []
        assert _permit_count(case["database"], case["operation_id"]) == 0


@pytest.mark.parametrize("deadline", ("intent", "production_budget"))
@pytest.mark.parametrize("boundary", ("issue", "claim", "action"))
def test_route_fence_return_after_authority_deadline_denies(accounting, deadline, boundary):
    kwargs = {
        "intent_ttl_seconds": 5 if deadline == "intent" else None,
        "production_budget_ttl_seconds": 1.0 if deadline == "production_budget" else 3600,
    }
    case = _production_case(accounting, **kwargs)
    dispatch, request, route = case["dispatch"], case["request"], case["route"]

    if boundary == "issue":
        route.delay_seconds = 5.2 if deadline == "intent" else 1.2
        with pytest.raises(ProductionAuthorizationDenied, match="permit_expired"):
            dispatch.issue_permit(request)
        assert _permit_count(case["database"], case["operation_id"]) == 0
        return

    issued = dispatch.issue_permit(request)
    if boundary == "claim":
        route.delay_seconds = 5.2 if deadline == "intent" else 1.2
        with pytest.raises(ProductionAuthorizationDenied, match="permit_expired"):
            dispatch.claim_permit(case["operation_id"], issued.permit_id)
        assert _claim_count(case["database"], case["operation_id"]) == 0
        return

    dispatch.claim_permit(case["operation_id"], issued.permit_id)
    route.delay_seconds = 5.2 if deadline == "intent" else 1.2
    decision = dispatch.authorize_action(case["operation_id"], issued.permit_id, "submit")
    assert decision.authorized is False
    assert decision.reason_code == "permit_expired"


def test_publication_scope_requires_a_publication_binding(accounting):
    scope = {
        "platform": "p1-local",
        "destination": "channel:approved",
        "locale": "en-US",
        "territory": "US",
        "visibility": "public",
    }
    case = _production_case(accounting, publication_scope=scope)
    with pytest.raises(ProductionAuthorizationDenied):
        case["dispatch"].issue_permit(case["request"])
    assert case["route"].calls == []
    assert _permit_count(case["database"], case["operation_id"]) == 0


def test_publication_binding_requires_current_canonical_publication_rows(accounting):
    scope = {
        "platform": "p1-local",
        "destination": "channel:approved",
        "locale": "en-US",
        "territory": "US",
        "visibility": "public",
    }
    binding = PublicationEffectBinding(
        publication_request_id=uuid4(),
        job_id=uuid4(),
        workflow_id="p1-publication-workflow",
        workflow_run_id="p1-publication-run-" + uuid4().hex,
        publication_plan_id=uuid4(),
        publication_attempt_id=uuid4(),
        external_effect_id=uuid4(),
    )
    case = _production_case(
        accounting,
        publication_scope=scope,
        publication_binding=binding,
    )
    with pytest.raises(ProductionAuthorizationDenied):
        case["dispatch"].issue_permit(case["request"])
    assert case["route"].calls == []
    assert _permit_count(case["database"], case["operation_id"]) == 0


def test_production_budget_binding_is_current_and_account_scoped(accounting):
    case = _production_case(accounting)
    with psycopg.connect(case["database"]) as connection:
        connection.execute(
            "UPDATE budgets SET scope='account:fixture-account' WHERE id=%s",
            (case["production_budget_id"],),
        )
    with pytest.raises(ProductionAuthorizationDenied):
        case["dispatch"].issue_permit(case["request"])
    assert case["route"].calls == []
    assert _permit_count(case["database"], case["operation_id"]) == 0


def test_active_stop_denial_records_db_derived_stop_latency(accounting):
    case = _production_case(accounting)
    governance = CycleGovernance(
        case["database"], workspace_id=case["workspace_id"], subject_id=case["subject_id"]
    )
    governance.set_stop(
        goal_id=case["goal_id"],
        stopped=True,
        expected_revision=1,
        idempotency_key="p1-stop-latency",
        reason="measure time from stop to denial",
    )
    with psycopg.connect(case["database"]) as connection:
        changed_at = connection.execute(
            "SELECT changed_at FROM v4_stop_scopes "
            "WHERE workspace_id=%s AND scope_key=%s AND stopped",
            (case["workspace_id"], "goal:" + str(case["goal_id"])),
        ).fetchone()[0]
    time.sleep(0.15)

    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    metrics = RecordingAuthorizationMetrics(
        provider.get_meter("p1-stop-latency-test")
    )
    dispatch = ProductionEffectDispatch(case["database"], case["route"], metrics=metrics)
    with pytest.raises(ProductionAuthorizationDenied, match="stop_active"):
        dispatch.issue_permit(case["request"])

    assert len(metrics.stop_to_denial_seconds) == 1
    latency = metrics.stop_to_denial_seconds[0]
    with psycopg.connect(case["database"]) as connection:
        elapsed_at_read = float(connection.execute(
            "SELECT extract(epoch FROM clock_timestamp() - %s::timestamptz)",
            (changed_at,),
        ).fetchone()[0])
    assert 0.1 <= latency <= 86_400
    assert 0 <= elapsed_at_read - latency < 0.5
    assert case["route"].calls == []
    assert "stop_to_denial" in repr(reader.get_metrics_data())


def test_repeated_denial_attempts_append_immutable_events(accounting):
    case = _production_case(accounting)
    with psycopg.connect(case["database"]) as connection:
        connection.execute(
            "UPDATE permission_grants SET effect='deny' "
            "WHERE workspace_id=%s AND principal_id=%s AND scope='effects:write'",
            (case["workspace_id"], str(case["subject_id"])),
        )
    for _ in range(2):
        with pytest.raises(ProductionAuthorizationDenied, match="stale_revision"):
            case["dispatch"].issue_permit(case["request"])

    with psycopg.connect(case["database"]) as connection:
        events = connection.execute(
            "SELECT id,payload FROM v4_cycle_events WHERE cycle_id=%s "
            "AND kind='production_authorization_denied' "
            "AND payload->>'action'='issue' AND payload->>'reason_code'='stale_revision' "
            "ORDER BY created_at",
            (case["cycle_id"],),
        ).fetchall()
        assert len(events) == 2
        assert events[0][0] != events[1][0]
        assert (
            events[0][1]["authorization_attempt_id"]
            != events[1][1]["authorization_attempt_id"]
        )
        with pytest.raises(psycopg.errors.RaiseException, match="immutable"):
            connection.execute(
                "UPDATE v4_cycle_events SET payload='{}' WHERE id=%s",
                (events[0][0],),
            )


def test_missing_permit_claim_records_event_without_approval_reference(accounting):
    case = _production_case(accounting)
    missing_permit_id = uuid4()
    with pytest.raises(ProductionAuthorizationDenied, match="permit_missing"):
        case["dispatch"].claim_permit(case["operation_id"], missing_permit_id)

    with psycopg.connect(case["database"]) as connection:
        event = connection.execute(
            "SELECT payload FROM v4_cycle_events WHERE cycle_id=%s "
            "AND kind='production_authorization_denied' "
            "AND payload->>'action'='claim' AND payload->>'permit_id'=%s",
            (case["cycle_id"], str(missing_permit_id)),
        ).fetchone()
    assert event is not None
    assert event[0]["reason_code"] == "permit_missing"
    assert event[0]["approval_request_id"] is None


def test_unrelated_same_workspace_approval_is_not_attached_to_denial(accounting):
    case = _production_case(accounting)
    unrelated_approval_id = uuid4()
    with psycopg.connect(case["database"]) as connection:
        canonical_context = connection.execute(
            "SELECT request_context FROM approval_requests WHERE id=%s",
            (case["request"].approval_request_id,),
        ).fetchone()[0]
        unrelated_context = dict(canonical_context)
        for field in ("operation_id", "cycle_id", "intent_id", "context_id"):
            unrelated_context[field] = str(uuid4())
        connection.execute(
            """INSERT INTO approval_requests(
                      id,workspace_id,policy_version_id,effect_type,status,request_context,
                      requested_by,decided_by,decided_at)
               VALUES(%s,%s,%s,'write','pending',%s,'p1-local-reviewer',NULL,NULL)""",
            (
                unrelated_approval_id,
                case["workspace_id"],
                case["request"].policy_version_id,
                Jsonb(unrelated_context),
            ),
        )
        connection.execute(
            "UPDATE approval_requests SET status='approved',"
            "decided_by='p1-local-reviewer',decided_at=clock_timestamp() WHERE id=%s",
            (unrelated_approval_id,),
        )

    untrusted_request = replace(
        case["request"], approval_request_id=unrelated_approval_id
    )
    with pytest.raises(ProductionAuthorizationDenied, match="approval_invalid"):
        case["dispatch"].issue_permit(untrusted_request)
    assert case["route"].calls == []

    with psycopg.connect(case["database"]) as connection:
        event = connection.execute(
            "SELECT payload FROM v4_cycle_events WHERE cycle_id=%s "
            "AND kind='production_authorization_denied' "
            "AND payload->>'action'='issue' ORDER BY created_at DESC LIMIT 1",
            (case["cycle_id"],),
        ).fetchone()
    assert event is not None
    assert event[0]["approval_request_id"] is None
    assert str(unrelated_approval_id) not in repr(event[0])


def test_dispatch_records_bounded_decisions_and_duplicate_claim_denial(accounting):
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    metrics = ProductionAuthorizationMetrics(provider.get_meter("p1-dispatch-test"))
    case = _production_case(accounting)
    dispatch = ProductionEffectDispatch(case["database"], case["route"], metrics=metrics)
    issued = dispatch.issue_permit(case["request"])
    claimed = dispatch.claim_permit(case["operation_id"], issued.permit_id)
    assert claimed.claim_id is not None
    assert dispatch.authorize_action(case["operation_id"], issued.permit_id, "submit").authorized
    with pytest.raises(ProductionAuthorizationDenied, match="dispatch_already_claimed"):
        dispatch.claim_permit(case["operation_id"], issued.permit_id)

    data = reader.get_metrics_data()
    encoded = repr(data)
    for private_id in (str(case["operation_id"]), str(issued.permit_id), str(case["account_id"])):
        assert private_id not in encoded
    metric_names = {
        metric.name
        for resource in data.resource_metrics
        for scope_metrics in resource.scope_metrics
        for metric in scope_metrics.metrics
    }
    assert "salience.production.authorization.decisions" in metric_names
    assert "salience.production.authorization.incidents" in metric_names
    assert _claim_count(case["database"], case["operation_id"]) == 1
    with psycopg.connect(case["database"]) as connection:
        event = connection.execute(
            "SELECT payload FROM v4_cycle_events WHERE cycle_id=%s "
            "AND kind='production_authorization_denied' ORDER BY created_at DESC LIMIT 1",
            (case["cycle_id"],),
        ).fetchone()
    assert event is not None and event[0]["action"] == "claim"
    assert event[0]["reason_code"] == "dispatch_already_claimed"
    assert event[0]["required_scopes"] == ["effects:write"]


def test_primary_outage_emits_bounded_metrics(accounting):
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    metrics = ProductionAuthorizationMetrics(provider.get_meter("p1-outage-test"))
    route = LocalRouteFence()
    dispatch = ProductionEffectDispatch(
        "postgresql://127.0.0.1:1/unreachable?connect_timeout=1",
        route,
        metrics=metrics,
    )
    with pytest.raises((psycopg.OperationalError, ProductionAuthorizationDenied)):
        dispatch.authorize_action(uuid4(), uuid4(), "submit")
    data = reader.get_metrics_data()
    assert "primary_outage" in repr(data)


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
