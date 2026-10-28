"""Effects-disabled integration contracts for production authorization."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import re
import time
from threading import Event, Thread
from types import SimpleNamespace
from psycopg.conninfo import make_conninfo
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from temporalio.exceptions import ApplicationError

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
from salience.cycles.production_rollback_reader import (
    ProductionPolicyRollbackInventoryV1,
    read_production_policy_rollback_inventory,
)
from salience.observability.production_authorization import ProductionAuthorizationMetrics
from salience.governance.costs import CostSettlementStatus
from salience.governance.cost_repository import CostReservationRepository
from salience.governance.policy import EffectClass
from salience.publication.contracts import PublicationRequest
from salience.publication.repository import PublicationRepository
from salience.workflows.publication import PublicationActivities, PublicationWorkflowState
from test_v4_cycle_admission import cycles
from test_v4_cadence_policy import policy, request as cadence_request
from test_v4_cycle_accounting import (
    accounting,
    admitted as accounting_admitted,
    transfer as accounting_transfer,
)


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
    seed_publication_rows=False,
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
        route_tuple_sha256="f" * 64,
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
                      'fixture://capability-profile',%s,'{}',clock_timestamp()+interval '1 hour')""",
            (
                workspace_id,
                account_id,
                provider_id,
                Jsonb({
                    "publisher_id": provider_id,
                    "version": "1",
                    "platform": "p1-local",
                    "account_types": ["organization"],
                    "contract_compatibility": {"publication": "v1"},
                    "enabled": True,
                    "audit_state": "verified",
                    "granted_scopes": ["effects:write"],
                    "supported_content_types": ["video"],
                    "supported_visibilities": ["private", "unlisted", "public"],
                    "disclosure_support": True,
                    "scheduling_support": True,
                    "cancellation_support": True,
                    "reconciliation_support": True,
                    "quota_state": "available",
                    "health_state": "healthy",
                }),
            ),
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
        if publication_binding is not None:
            connection.execute(
                "UPDATE jobs SET input_payload=%s WHERE id=%s",
                (Jsonb({"workflow_id": publication_binding.workflow_id}), publication_binding.job_id),
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
        publication_rows = None
        if seed_publication_rows:
            if publication_binding is None:
                raise ValueError("canonical publication rows require a publication binding")
            publication_rows = _seed_production_publication_rows(
                connection,
                binding=publication_binding,
                workspace_id=workspace_id,
                program_id=program_id,
                policy_id=policy_id,
                account_id=account_id,
                publisher_id=provider_id,
                approval_id=approval_id,
                production_reservation_id=production_reservation_id,
                operation_id=operation_id,
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
        "publication_rows": publication_rows,
    }


def _seed_production_publication_rows(
    connection,
    *,
    binding,
    workspace_id,
    program_id,
    policy_id,
    account_id,
    publisher_id,
    approval_id,
    production_reservation_id,
    operation_id,
):
    topic_id, strategic_id, brief_id, script_id, platform_profile_id, package_id, disclosure_id, ready_id = (
        uuid4() for _ in range(8)
    )
    profile_id = connection.execute(
        "SELECT id FROM publisher_capability_profiles WHERE publisher_account_id=%s AND publisher_id=%s",
        (account_id, publisher_id),
    ).fetchone()[0]
    connection.execute(
        """INSERT INTO topic_opportunities(
                  id,workspace_id,content_program_id,fingerprint,topic,status,score,explanation)
           VALUES(%s,%s,%s,%s,'approved publication fixture','selected',1,'canonical binding test')""",
        (topic_id, workspace_id, program_id, "topic-" + str(operation_id)),
    )
    connection.execute(
        """INSERT INTO strategic_packages(
                  id,workspace_id,content_program_id,topic_opportunity_id,diversity_fingerprint,
                  status,package)
           VALUES(%s,%s,%s,%s,%s,'selected','{}')""",
        (strategic_id, workspace_id, program_id, topic_id, "strategic-" + str(operation_id)),
    )
    connection.execute(
        """INSERT INTO content_brief_versions(
                  id,workspace_id,content_program_id,brief_key,version,topic_opportunity_id,
                  strategic_package_id,content)
           VALUES(%s,%s,%s,%s,1,%s,%s,'{}')""",
        (brief_id, workspace_id, program_id, "brief-" + str(operation_id), topic_id, strategic_id),
    )
    connection.execute(
        """INSERT INTO script_versions(
                  id,workspace_id,content_program_id,content_brief_id,script_key,version,status,
                  target_format,target_duration_seconds,script)
           VALUES(%s,%s,%s,%s,%s,1,'approved','video',30,'{}')""",
        (script_id, workspace_id, program_id, brief_id, "script-" + str(operation_id)),
    )
    connection.execute(
        """INSERT INTO platform_profiles(
                  id,workspace_id,content_program_id,profile_key,version,target_platform,rules,status)
           VALUES(%s,%s,%s,%s,1,'p1-local','{}','active')""",
        (platform_profile_id, workspace_id, program_id, "platform-" + str(operation_id)),
    )
    connection.execute(
        """INSERT INTO distribution_packages(
                  id,workspace_id,content_program_id,content_brief_id,script_version_id,
                  platform_profile_id,package_key,version,locale,metadata,status)
           VALUES(%s,%s,%s,%s,%s,%s,%s,1,'en-US','{}','ready')""",
        (
            package_id,
            workspace_id,
            program_id,
            brief_id,
            script_id,
            platform_profile_id,
            "distribution-" + str(operation_id),
        ),
    )
    connection.execute(
        """INSERT INTO synthetic_media_disclosures(distribution_package_id,decision,status)
           VALUES(%s,'{}','approved') RETURNING id""",
        (package_id,),
    )
    disclosure_id = connection.execute(
        "SELECT id FROM synthetic_media_disclosures WHERE distribution_package_id=%s",
        (package_id,),
    ).fetchone()[0]
    connection.execute(
        """INSERT INTO ready_to_publish_packages(
                  id,workspace_id,content_program_id,content_brief_id,script_version_id,
                  distribution_package_id,platform_profile_id,disclosure_id,ready_package_key,
                  version,approval_state,verifier_results,policy_versions,lineage)
           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,1,'approved','{}',%s,'{}')""",
        (
            ready_id,
            workspace_id,
            program_id,
            brief_id,
            script_id,
            package_id,
            platform_profile_id,
            disclosure_id,
            "ready-" + str(operation_id),
            Jsonb([str(policy_id)]),
        ),
    )
    connection.execute(
        """INSERT INTO publication_requests(
                  id,workspace_id,content_program_id,ready_package_id,publisher_account_id,
                  publisher_capability_profile_id,publication_approval_request_id,request_key,
                  version,publisher_id,platform,destination,locale,territory,visibility,
                  capability_profile_version,approval_reference,state,request_fingerprint,
                  disclosure_projection,policy_references,rights_references)
           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,1,%s,'p1-local','channel:approved','en-US','US',
                  'public',1,'production-approval','planned',%s,'{}',%s,'[]')""",
        (
            binding.publication_request_id,
            workspace_id,
            program_id,
            ready_id,
            account_id,
            profile_id,
            approval_id,
            "request-" + str(operation_id),
            publisher_id,
            "f" * 64,
            Jsonb([str(policy_id)]),
        ),
    )
    connection.execute(
        """INSERT INTO external_effects(
                  id,workspace_id,job_id,effect_type,effect_classification,idempotency_key,
                  status,request_fingerprint,reconciliation_state)
           VALUES(%s,%s,%s,'publication','write',%s,'planned',%s,'{}')""",
        (
            binding.external_effect_id,
            workspace_id,
            binding.job_id,
            "publication-effect-" + str(operation_id),
            "a" * 64,
        ),
    )
    connection.execute(
        """UPDATE budget_reservations SET job_id=%s,external_effect_id=%s WHERE id=%s""",
        (binding.job_id, binding.external_effect_id, production_reservation_id),
    )
    connection.execute(
        """INSERT INTO publication_plans(
                  id,publication_request_id,version,publisher_id,publisher_version,
                  external_effect_id,budget_reservation_id,state)
           VALUES(%s,%s,1,%s,'1',%s,%s,'planned')""",
        (
            binding.publication_plan_id,
            binding.publication_request_id,
            publisher_id,
            binding.external_effect_id,
            production_reservation_id,
        ),
    )
    connection.execute(
        """INSERT INTO publication_attempts(
                  id,publication_plan_id,attempt_number,idempotency_key,state,request_fingerprint)
           VALUES(%s,%s,1,%s,'planned',%s)""",
        (
            binding.publication_attempt_id,
            binding.publication_plan_id,
            "publication-attempt-" + str(operation_id),
            "b" * 64,
        ),
    )
    return {
        "publication_request_id": binding.publication_request_id,
        "publication_plan_id": binding.publication_plan_id,
        "publication_attempt_id": binding.publication_attempt_id,
        "external_effect_id": binding.external_effect_id,
        "ready_package_id": ready_id,
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


def _wait_for_pg_lock(database, application_name, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with psycopg.connect(database) as connection:
            waiting = connection.execute(
                """SELECT wait_event_type='Lock'
                   FROM pg_stat_activity
                   WHERE application_name=%s AND state='active'""",
                (application_name,),
            ).fetchone()
        if waiting and waiting[0]:
            return True
        time.sleep(0.02)
    return False


def _start_due_case_transition(case, proof, application_name, entered, release):
    service = CycleGovernance(
        make_conninfo(case["database"], application_name=application_name),
        workspace_id=case["workspace_id"],
        subject_id=case["subject_id"],
    )

    def transition():
        # Hold the canonical case transition's transaction open after its update
        # so dispatch must serialize against the case and cycle rows.
        with psycopg.connect(
            make_conninfo(case["database"], application_name=application_name),
            row_factory=dict_row,
        ) as connection:
            result = service._escalate_due(connection, proof["case_id"])
            entered.set()
            assert release.wait(timeout=5)
            return result

    return transition


def _insert_task7_no_effect_command(
    case,
    request,
    *,
    later_result=None,
    superseding_before_command=None,
    case_deadline=None,
):
    """Seed a migration-valid Task 7 event payload for a focused dispatch test."""
    work_id, case_id, event_id = uuid4(), uuid4(), uuid4()
    observation_id = uuid4()
    stage_decision_id, policy_decision_id, route_decision_id = (
        uuid4() for _ in range(3)
    )
    event_key = "reissue:" + str(work_id) + ":1"
    with psycopg.connect(case["database"], row_factory=dict_row) as connection:
        current = connection.execute(
            """SELECT permit.request_snapshot,dispatch.generation,
                      dispatch.idempotency_key,permit.provider_id,permit.account_id,
                      permit.route_id,permit.slot_id,permit.fencing_epoch
               FROM v4_production_current_dispatch dispatch
               JOIN v4_production_effect_permits permit
                 ON permit.permit_id=dispatch.current_permit_id
                AND permit.operation_id=dispatch.operation_id
                AND permit.generation=dispatch.generation
               WHERE dispatch.operation_id=%s""",
            (request.operation_id,),
        ).fetchone()
        assert current is not None
        fingerprint = connection.execute(
            "SELECT encode(public.digest(%s::jsonb::text,'sha256'),'hex') AS value",
            (Jsonb(current["request_snapshot"]),),
        ).fetchone()["value"]
        payload = {
            "schema_version": "salience.p1-same-operation-no-effect.v1",
            "reason_code": "authoritative_no_effect",
            "evidence_sha256": "1" * 64,
            "remote_identity_sha256": sha256(
                f"{request.operation_id}:{current['idempotency_key']}".encode("utf-8")
            ).hexdigest(),
            "request_fingerprint": fingerprint,
            "operation_id": str(request.operation_id),
            "case_id": str(case_id),
            "work_id": str(work_id),
            "source_id": str(case_id),
            "state": "reconciling",
            "result_class": "authoritative_no_effect",
            "event_key": event_key,
            "stage_decision_id": str(stage_decision_id),
            "policy_decision_id": str(policy_decision_id),
            "workspace_stop_revision": 1,
            "goal_stop_revision": 1,
            "route_decision_id": str(route_decision_id),
        }
        connection.execute("SET LOCAL session_replication_role='replica'")
        connection.execute(
            """INSERT INTO v4_recovery_cases(
                      id,workspace_id,goal_id,target_intent_id,target_cycle_id,context_id,
                      operation_id,kind,open_fingerprint,failure_class,state,revision,owner_id,
                      reason,deadline,max_actions)
               VALUES(%s,%s,%s,NULL,%s,%s,%s,'reconciliation',%s,'unknown_effect',
                      'reconciling',1,%s,'Task 8 typed no-effect dispatch fixture',
                      %s,5)""",
            (
                case_id,
                case["workspace_id"],
                case["goal_id"],
                request.cycle_id,
                request.context_id,
                request.operation_id,
                "3" * 64,
                request.subject_id,
                case_deadline or (datetime.now(timezone.utc) + timedelta(days=1)),
            ),
        )
        connection.execute(
            """INSERT INTO v4_production_recovery_work(
                      work_id,source_discriminant,source_id,workspace_id,goal_id,
                      intent_id,cycle_id,context_id,operation_id,source_revision,
                      case_revision,eligibility_revision,owner_id,target_alias,
                      remote_account_id,reason,state,request_fingerprint,
                      source_fingerprint,deadline,deadline_revision,
                      recipient_profile_revision)
               VALUES(%s,'cycle_case',%s,%s,%s,%s,%s,%s,%s,1,1,1,%s,%s,%s,
                      'unknown_effect','reconciling',%s,%s,
                      clock_timestamp()+interval '1 day',1,1)""",
            (
                work_id,
                case_id,
                case["workspace_id"],
                case["goal_id"],
                request.intent_id,
                request.cycle_id,
                request.context_id,
                request.operation_id,
                request.subject_id,
                current["provider_id"],
                current["account_id"],
                fingerprint,
                "3" * 64,
            ),
        )
        observation_payload = Jsonb(
            {
                "schema_version": "salience.p1-production-recovery-read.v1",
                "reason_code": "remote_observation",
                "result_class": "authoritative_no_effect",
                "evidence_sha256": "2" * 64,
                "remote_identity_sha256": "3" * 64,
                "request_fingerprint": fingerprint,
                "operation_id": str(request.operation_id),
                "intent_id": str(request.intent_id),
                "work_id": str(work_id),
                "source_id": str(case_id),
                "case_id": str(case_id),
                "remote_state": "not_found",
                "event_key": current["idempotency_key"],
                "deadline": (datetime.now(timezone.utc) - timedelta(seconds=3)).isoformat(),
            }
        )
        observation_hash = connection.execute(
            "SELECT encode(public.digest(%s::jsonb::text,'sha256'),'hex') AS value",
            (observation_payload,),
        ).fetchone()["value"]
        connection.execute(
            """INSERT INTO v4_production_recovery_events(
                      event_id,work_id,event_key,event_kind,actor_id,
                      policy_decision_id,payload,payload_sha256,occurred_at)
               VALUES(%s,%s,%s,'remote_read_observation',%s,%s,%s,%s,
                      clock_timestamp()-interval '2 seconds')""",
            (
                observation_id,
                work_id,
                "observation:" + str(work_id),
                request.subject_id,
                policy_decision_id,
                observation_payload,
            observation_hash,
        ),
    )
        if superseding_before_command is not None:
            result_class, remote_state = superseding_before_command
            later_payload = Jsonb(
                {
                    "schema_version": "salience.p1-production-recovery-read.v1",
                    "reason_code": "remote_observation",
                    "result_class": result_class,
                    "evidence_sha256": "4" * 64,
                    "remote_identity_sha256": "5" * 64,
                    "request_fingerprint": fingerprint,
                    "operation_id": str(request.operation_id),
                    "intent_id": str(request.intent_id),
                    "work_id": str(work_id),
                    "source_id": str(case_id),
                    "case_id": str(case_id),
                    "remote_state": remote_state,
                    "event_key": current["idempotency_key"],
                    "deadline": (datetime.now(timezone.utc) - timedelta(seconds=3)).isoformat(),
                }
            )
            later_hash = connection.execute(
                "SELECT encode(public.digest(%s::jsonb::text,'sha256'),'hex') AS value",
                (later_payload,),
            ).fetchone()["value"]
            connection.execute(
                """INSERT INTO v4_production_recovery_events(
                          event_id,work_id,event_key,event_kind,actor_id,
                          policy_decision_id,payload,payload_sha256,occurred_at)
                   VALUES(%s,%s,%s,'remote_read_observation',%s,%s,%s,%s,
                          clock_timestamp()-interval '1 second')""",
                (
                    uuid4(),
                    work_id,
                    "superseding-observation:" + str(uuid4()),
                    request.subject_id,
                    policy_decision_id,
                    later_payload,
                    later_hash,
                ),
            )
        payload.update(
            {
                "observation_event_id": str(observation_id),
                "observation_sha256": observation_hash,
            }
        )
        payload_json = Jsonb(payload)
        payload_hash = connection.execute(
            "SELECT encode(public.digest(%s::jsonb::text,'sha256'),'hex') AS value",
            (payload_json,),
        ).fetchone()["value"]
        connection.execute(
            """INSERT INTO v4_production_recovery_events(
                      event_id,work_id,event_key,event_kind,actor_id,
                      policy_decision_id,payload,payload_sha256)
               VALUES(%s,%s,%s,'same_operation_no_effect_reissue',%s,%s,%s,%s)""",
            (
                event_id,
                work_id,
                event_key,
                request.subject_id,
                policy_decision_id,
                payload_json,
                payload_hash,
            ),
        )
        if later_result is not None:
            if isinstance(later_result, tuple):
                later_class, later_state = later_result
            else:
                later_class, later_state = later_result, "unknown"
            later_payload = Jsonb(
                {"result_class": later_class, "remote_state": later_state}
            )
            later_hash = connection.execute(
                "SELECT encode(public.digest(%s::jsonb::text,'sha256'),'hex') AS value",
                (later_payload,),
            ).fetchone()["value"]
            connection.execute(
                """INSERT INTO v4_production_recovery_events(
                          event_id,work_id,event_key,event_kind,actor_id,
                          policy_decision_id,payload,payload_sha256)
                   VALUES(%s,%s,%s,'remote_read_observation',%s,%s,%s,%s)""",
                (
                    uuid4(),
                    work_id,
                    "later-observation:" + str(uuid4()),
                    request.subject_id,
                    policy_decision_id,
                    later_payload,
                    later_hash,
                ),
            )
    return {
        "event_id": event_id,
        "payload": payload,
        "work_id": work_id,
        "case_id": case_id,
        "observation_event_id": observation_id,
        "observation_sha256": observation_hash,
        "event_key": event_key,
        "request_fingerprint": fingerprint,
        "original_idempotency_key": current["idempotency_key"],
        "stage_decision_id": stage_decision_id,
        "policy_decision_id": policy_decision_id,
        "workspace_stop_revision": 1,
        "goal_stop_revision": 1,
        "route_decision_id": route_decision_id,
    }


def test_0050_new_permit_snapshot_v2_binds_route_tuple_hash(accounting):
    case = _production_case(accounting)
    issued = case["dispatch"].issue_permit(case["request"])
    with psycopg.connect(case["database"]) as connection:
        snapshot = connection.execute(
            """SELECT request_snapshot,recovery_command_id
               FROM v4_production_effect_permits WHERE permit_id=%s""",
            (issued.permit_id,),
        ).fetchone()
    assert snapshot[0]["schema_version"] == "salience.p1-production-effect-permit.v2"
    assert snapshot[0]["generation"] == 1
    assert snapshot[0]["route_tuple_sha256"] == case["request"].route.route_tuple_sha256
    assert snapshot[0]["recovery_command_id"] is None
    assert snapshot[1] is None


def test_historical_v1_permit_snapshot_remains_readable(accounting):
    case = _production_case(accounting)
    issued = case["dispatch"].issue_permit(case["request"])
    with psycopg.connect(case["database"]) as connection:
        snapshot = connection.execute(
            "SELECT request_snapshot FROM v4_production_effect_permits WHERE permit_id=%s",
            (issued.permit_id,),
        ).fetchone()[0]
    legacy = dict(snapshot)
    legacy["schema_version"] = "salience.p1-production-effect-permit.v1"
    legacy.pop("route_tuple_sha256")
    legacy.pop("recovery_command_id")
    parsed = ProductionEffectDispatch._request_from_snapshot(legacy)
    assert parsed.operation_id == case["operation_id"]
    assert parsed.generation == 1 and parsed.recovery_command_id is None
    assert parsed.route.route_tuple_sha256 is None


def test_no_effect_generation_advance_requires_one_typed_same_operation_command(
    accounting, monkeypatch
):
    case = _production_case(accounting)
    original = case["dispatch"].issue_permit(case["request"])
    proof = _insert_task7_no_effect_command(case, case["request"])
    changed = replace(
        case["request"], generation=2, recovery_command_id=proof["event_id"]
    )

    with pytest.raises(ProductionAuthorizationDenied):
        case["dispatch"].issue_permit(changed)
    case["dispatch"].claim_permit(case["operation_id"], original.permit_id)

    write_event = case["dispatch"]._write_event

    def crash_after_pointer_advance(*args, **kwargs):
        raise RuntimeError("simulated reissue crash before transaction commit")

    monkeypatch.setattr(case["dispatch"], "_write_event", crash_after_pointer_advance)
    with pytest.raises(RuntimeError, match="reissue crash"):
        case["dispatch"].issue_permit(changed)
    with psycopg.connect(case["database"]) as connection:
        assert connection.execute(
            "SELECT generation FROM v4_production_current_dispatch WHERE operation_id=%s",
            (case["operation_id"],),
        ).fetchone()[0] == 1
    assert _permit_count(case["database"], case["operation_id"]) == 1
    monkeypatch.setattr(case["dispatch"], "_write_event", write_event)

    issued = case["dispatch"].issue_permit(changed)
    assert issued.operation_id == original.operation_id
    assert issued.generation == 2
    assert issued.permit_id != original.permit_id
    assert case["route"].calls[-1].operation_id == original.operation_id
    with psycopg.connect(case["database"]) as connection:
        rows = connection.execute(
            """SELECT generation,recovery_command_id,request_snapshot
               FROM v4_production_effect_permits WHERE operation_id=%s
               ORDER BY generation""",
            (case["operation_id"],),
        ).fetchall()
        pointer = connection.execute(
            """SELECT generation,current_permit_id,current_claim_id,idempotency_key
               FROM v4_production_current_dispatch WHERE operation_id=%s""",
            (case["operation_id"],),
        ).fetchone()
    assert [row[0] for row in rows] == [1, 2]
    assert rows[0][1] is None
    assert rows[1][1] == proof["event_id"]
    assert pointer == (2, issued.permit_id, None, case["request"].idempotency_key)
    assert rows[0][2]["schema_version"] == "salience.p1-production-effect-permit.v2"
    assert rows[1][2]["schema_version"] == "salience.p1-production-effect-permit.v2"


def test_concurrent_same_no_effect_command_retries_return_one_permit(accounting):
    case = _production_case(accounting)
    original = case["dispatch"].issue_permit(case["request"])
    case["dispatch"].claim_permit(case["operation_id"], original.permit_id)
    proof = _insert_task7_no_effect_command(case, case["request"])
    changed = replace(
        case["request"], generation=2, recovery_command_id=proof["event_id"]
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(case["dispatch"].issue_permit, (changed, changed)))
    assert results[0].permit_id == results[1].permit_id
    assert results[0].generation == results[1].generation == 2
    assert _permit_count(case["database"], case["operation_id"]) == 2
    with psycopg.connect(case["database"]) as connection:
        assert connection.execute(
            "SELECT count(*) FROM v4_production_effect_permits "
            "WHERE operation_id=%s AND recovery_command_id=%s",
            (case["operation_id"], proof["event_id"]),
        ).fetchone()[0] == 1


def test_no_effect_command_cannot_be_replayed_or_cross_operation(accounting):
    case = _production_case(accounting)
    original = case["dispatch"].issue_permit(case["request"])
    case["dispatch"].claim_permit(case["operation_id"], original.permit_id)
    proof = _insert_task7_no_effect_command(case, case["request"])
    reissued = case["dispatch"].issue_permit(
        replace(case["request"], generation=2, recovery_command_id=proof["event_id"])
    )
    case["dispatch"].claim_permit(case["operation_id"], reissued.permit_id)
    with pytest.raises(ProductionAuthorizationDenied):
        case["dispatch"].issue_permit(
            replace(case["request"], generation=3, recovery_command_id=proof["event_id"])
        )

    other_operation = uuid4()
    with pytest.raises(ProductionAuthorizationDenied):
        case["dispatch"].issue_permit(
            replace(
                case["request"],
                operation_id=other_operation,
                generation=2,
                recovery_command_id=proof["event_id"],
            )
        )
    assert _permit_count(case["database"], case["operation_id"]) == 2
    assert _permit_count(case["database"], other_operation) == 0
    assert reissued.operation_id == case["operation_id"]


@pytest.mark.parametrize(
    "later_result",
    (
        "unknown",
        "accepted",
        ("in_progress", "in_progress"),
        ("terminal_success", "succeeded"),
        ("terminal_success", "accepted"),
        ("unknown", "conflicting"),
    ),
)
def test_unknown_or_accepted_generation_cannot_be_reissued(accounting, later_result):
    case = _production_case(accounting)
    original = case["dispatch"].issue_permit(case["request"])
    case["dispatch"].claim_permit(case["operation_id"], original.permit_id)
    proof = _insert_task7_no_effect_command(
        case, case["request"], later_result=later_result
    )
    with pytest.raises(ProductionAuthorizationDenied):
        case["dispatch"].issue_permit(
            replace(case["request"], generation=2, recovery_command_id=proof["event_id"])
        )
    assert _permit_count(case["database"], case["operation_id"]) == 1


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
def test_superseding_remote_observation_before_command_blocks_reissue(
    accounting, result_class, remote_state
):
    case = _production_case(accounting)
    original = case["dispatch"].issue_permit(case["request"])
    case["dispatch"].claim_permit(case["operation_id"], original.permit_id)
    proof = _insert_task7_no_effect_command(
        case,
        case["request"],
        superseding_before_command=(result_class, remote_state),
    )
    with pytest.raises(ProductionAuthorizationDenied):
        case["dispatch"].issue_permit(
            replace(case["request"], generation=2, recovery_command_id=proof["event_id"])
        )
    assert _permit_count(case["database"], case["operation_id"]) == 1


def _reissue_case_race(accounting, suffix):
    case = _production_case(accounting)
    original = case["dispatch"].issue_permit(case["request"])
    case["dispatch"].claim_permit(case["operation_id"], original.permit_id)
    proof = _insert_task7_no_effect_command(
        case,
        case["request"],
        case_deadline=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    app_name = "p1-reissue-" + suffix + "-" + uuid4().hex[:8]
    request = replace(
        case["request"], generation=2, recovery_command_id=proof["event_id"]
    )
    dispatch = ProductionEffectDispatch(
        make_conninfo(case["database"], application_name=app_name), case["route"]
    )
    return case, proof, request, dispatch, app_name


def test_case_transition_wins_before_generation_reissue(accounting):
    case, proof, request, dispatch, app_name = _reissue_case_race(
        accounting, "transition-wins"
    )
    transition_entered, release_transition = Event(), Event()
    transition_name = "p1-case-transition-wins-" + uuid4().hex[:8]
    transition = _start_due_case_transition(
        case, proof, transition_name, transition_entered, release_transition
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        transition_future = pool.submit(transition)
        assert transition_entered.wait(timeout=3)
        issue_future = pool.submit(dispatch.issue_permit, request)
        waited = _wait_for_pg_lock(case["database"], app_name)
        release_transition.set()
        transition_future.result(timeout=5)
        with pytest.raises(ProductionAuthorizationDenied):
            issue_future.result(timeout=5)
    assert waited, "reissue did not wait for the in-flight case transition"
    assert _permit_count(case["database"], case["operation_id"]) == 1
    with psycopg.connect(case["database"]) as connection:
        assert connection.execute(
            "SELECT state,revision FROM v4_recovery_cases WHERE id=%s",
            (proof["case_id"],),
        ).fetchone() == ("suspended", 2)


def test_generation_reissue_holds_case_lock_through_pointer_advance(accounting):
    case, proof, request, dispatch, app_name = _reissue_case_race(
        accounting, "reissue-wins"
    )
    route_entered, route_release = Event(), Event()
    case["route"].block_entered = route_entered
    case["route"].block_release = route_release
    transition_entered, release_transition = Event(), Event()
    transition_name = "p1-case-transition-after-reissue-" + uuid4().hex[:8]
    transition = _start_due_case_transition(
        case, proof, transition_name, transition_entered, release_transition
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        issue_future = pool.submit(dispatch.issue_permit, request)
        assert route_entered.wait(timeout=3), repr(issue_future.exception(timeout=1))
        transition_future = pool.submit(transition)
        try:
            waited = _wait_for_pg_lock(case["database"], transition_name)
        finally:
            route_release.set()
        issued = issue_future.result(timeout=5)
        release_transition.set()
        transition_future.result(timeout=5)
    assert waited, "case transition bypassed the generation pointer transaction"
    assert issued.generation == 2
    assert _permit_count(case["database"], case["operation_id"]) == 2
    with psycopg.connect(case["database"]) as connection:
        assert connection.execute(
            "SELECT generation FROM v4_production_current_dispatch WHERE operation_id=%s",
            (case["operation_id"],),
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT state,revision FROM v4_recovery_cases WHERE id=%s",
            (proof["case_id"],),
        ).fetchone() == ("suspended", 2)


def test_generation_advance_preserves_original_operation_fingerprint_and_key(accounting):
    case = _production_case(accounting)
    original = case["dispatch"].issue_permit(case["request"])
    case["dispatch"].claim_permit(case["operation_id"], original.permit_id)
    proof = _insert_task7_no_effect_command(case, case["request"])
    # The public recovery controller obtains the exact Task 7 typed receipt,
    # then delegates the generation-specific permit request to dispatch.
    recovery = __import__(
        "salience.cycles.production_recovery", fromlist=["ProductionRecoveryCaseController"]
    )
    from salience.cycles.production_dispatch import ProductionRecoveryCommandReference
    from salience.cycles._production_recovery_contracts import ProductionNoEffectReissueReceipt

    repository = recovery.ProductionRecoveryWorkRepository(case["database"])

    class Authority:
        def authorize_deadline_wake(self, *args):
            raise AssertionError("deadline authority is not part of no-effect reissue")

        def authorize_no_effect_reissue(self, *args):
            raise AssertionError("the typed event was already issued by the Task 7 boundary")

    controller = recovery.ProductionRecoveryCaseController(
        repository, authority=Authority(), commands=object()
    )
    reference = ProductionRecoveryCommandReference(
        event_id=proof["event_id"],
        work_id=proof["work_id"],
        operation_id=case["operation_id"],
        request_fingerprint=proof["request_fingerprint"],
        original_idempotency_key=proof["original_idempotency_key"],
        case_id=proof["case_id"],
        case_revision=1,
        stage_decision_id=proof["stage_decision_id"],
        policy_decision_id=proof["policy_decision_id"],
        workspace_stop_revision=proof["workspace_stop_revision"],
        goal_stop_revision=proof["goal_stop_revision"],
        route_decision_id=proof["route_decision_id"],
    )
    receipt = ProductionNoEffectReissueReceipt(
        event_id=proof["event_id"],
        work_id=proof["work_id"],
        operation_id=case["operation_id"],
        idempotency_key=proof["original_idempotency_key"],
        case_revision=1,
        payload_sha256="4" * 64,
        command_reference=reference,
    )
    controller.authorize_no_effect_generation_reissue = lambda _command: receipt

    command = recovery.NoEffectGenerationReissueCommand(
        work_id=proof["work_id"],
        case_id=proof["case_id"],
        expected_case_revision=1,
        observation_event_id=proof["observation_event_id"],
        observation_sha256=proof["observation_sha256"],
        operation_id=case["operation_id"],
        request_fingerprint=proof["request_fingerprint"],
        original_idempotency_key=proof["original_idempotency_key"],
        stage_decision_id=proof["stage_decision_id"],
        policy_decision_id=proof["policy_decision_id"],
        workspace_stop_revision=proof["workspace_stop_revision"],
        goal_stop_revision=proof["goal_stop_revision"],
        route_decision_id=proof["route_decision_id"],
        idempotency_key=proof["event_key"],
    )

    issued = controller.issue_no_effect_generation_permit(
        command, request=case["request"], dispatch=case["dispatch"]
    )
    retried = controller.issue_no_effect_generation_permit(
        command, request=case["request"], dispatch=case["dispatch"]
    )
    with psycopg.connect(case["database"]) as connection:
        permits = connection.execute(
            """SELECT request_snapshot,
                      encode(public.digest(request_snapshot::text,'sha256'),'hex'),
                      request_snapshot->>'operation_id',
                      request_snapshot->>'idempotency_key',
                      request_snapshot->>'generation',
                      request_snapshot->>'recovery_command_id'
               FROM v4_production_effect_permits WHERE operation_id=%s
               ORDER BY generation""",
            (case["operation_id"],),
        ).fetchall()
    assert issued.generation == 2
    assert retried.permit_id == issued.permit_id
    assert _permit_count(case["database"], case["operation_id"]) == 2
    assert permits[0][1] == proof["request_fingerprint"]
    assert permits[0][2] == permits[1][2] == str(case["operation_id"])
    assert permits[0][3] == permits[1][3] == case["request"].idempotency_key
    assert permits[0][4] == "1" and permits[1][4] == "2"
    assert permits[0][5] is None and permits[1][5] == str(proof["event_id"])
    dynamic_snapshot_fields = {
        "schema_version",
        "generation",
        "recovery_command_id",
        "subject_revision",
        "grant_ids",
        "policy_version_id",
        "policy_document_hash",
        "approval_request_id",
        "approval_expires_at",
        "route_fence_decision_id",
        "workspace_stop_revision",
        "goal_stop_revision",
        "baseline_approval_id",
        "budget_reservation_ids",
    }
    assert {
        key: value for key, value in permits[0][0].items()
        if key not in dynamic_snapshot_fields
    } == {
        key: value for key, value in permits[1][0].items()
        if key not in dynamic_snapshot_fields
    }


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


def test_postclaim_reconcile_authority_denial_holds_canonical_publication_liability_in_postgres(accounting):
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
        seed_publication_rows=True,
    )
    dispatch = case["dispatch"]
    issued = dispatch.issue_permit(case["request"])
    claim = dispatch.claim_permit(case["operation_id"], issued.permit_id)
    repository = PublicationRepository(case["database"])
    current = asyncio.run(
        repository.load_current_authorization(str(binding.publication_request_id))
    )

    assert current.publication_plan_id == str(binding.publication_plan_id)
    assert current.publication_attempt_id == str(binding.publication_attempt_id)
    assert current.external_effect_id == str(binding.external_effect_id)
    assert current.budget_reservation_id == str(case["production_reservation_id"])
    assert current.remote_receipt_id is None

    known_remote_id = "p1-known-" + uuid4().hex
    receipt = asyncio.run(
        repository.record_remote_receipt(
            publication_attempt_id=str(binding.publication_attempt_id),
            publisher_id=case["request"].provider_id,
            remote_id=known_remote_id,
            state="accepted",
            safe_metadata_hash="c" * 64,
            remote_url=None,
        )
    )
    current_with_receipt = asyncio.run(
        repository.load_current_authorization(str(binding.publication_request_id))
    )
    assert current_with_receipt.remote_receipt_id == receipt.id
    assert current_with_receipt.remote_id == known_remote_id
    publication = asyncio.run(
        repository.record_publication(
            publication_request_id=str(binding.publication_request_id),
            remote_receipt_id=receipt.id,
            state="published",
        )
    )
    current_with_publication = asyncio.run(
        repository.load_current_authorization(str(binding.publication_request_id))
    )
    assert current_with_publication.publication_id == publication.id
    assert current_with_publication.publication_state == "published"

    replacement_plan_id = uuid4()
    replacement_reservation_id = uuid4()
    with psycopg.connect(case["database"]) as connection:
        connection.execute(
            """INSERT INTO budget_reservations(
                      id,budget_id,reservation_key,estimated_amount,reserved_amount,status,expires_at)
               VALUES(%s,%s,%s,0.100000,0.100000,'reserved',
                      clock_timestamp()+interval '1 hour')""",
            (
                replacement_reservation_id,
                case["production_budget_id"],
                "p1-replacement-" + uuid4().hex,
            ),
        )
        connection.execute(
            """INSERT INTO publication_plans(
                      id,publication_request_id,version,publisher_id,publisher_version,
                      budget_reservation_id,state)
               VALUES(%s,%s,2,%s,'1',%s,'planned')""",
            (
                replacement_plan_id,
                binding.publication_request_id,
                case["request"].provider_id,
                replacement_reservation_id,
            ),
        )
    current_with_new_plan = asyncio.run(
        repository.load_current_authorization(str(binding.publication_request_id))
    )
    assert current_with_new_plan.publication_plan_id == str(replacement_plan_id)
    assert current_with_new_plan.publication_attempt_id is None
    assert current_with_new_plan.external_effect_id is None

    route_calls_before_denial = len(case["route"].calls)
    with psycopg.connect(case["database"]) as connection:
        connection.execute(
            "UPDATE permission_grants SET effect='deny' "
            "WHERE workspace_id=%s AND principal_id=%s AND scope='effects:write'",
            (case["workspace_id"], str(case["subject_id"])),
        )
    denial = dispatch.authorize_action(case["operation_id"], issued.permit_id, "reconcile")
    assert denial.authorized is False
    assert denial.reason_code == "stale_revision"
    assert len(case["route"].calls) == route_calls_before_denial

    assert dispatch.record_publication_liability_held(
        binding.publication_request_id, "reconcile", denial.reason_code
    ) is True

    with psycopg.connect(case["database"]) as connection:
        event = connection.execute(
            """SELECT id, payload FROM v4_cycle_events
               WHERE cycle_id=%s AND kind='production_publication_liability_held'""",
            (case["cycle_id"],),
        ).fetchone()
        reservation = connection.execute(
            "SELECT status FROM budget_reservations WHERE id=%s",
            (case["production_reservation_id"],),
        ).fetchone()[0]
        job_state = connection.execute(
            "SELECT state FROM jobs WHERE id=%s", (binding.job_id,)
        ).fetchone()[0]
        effect_state = connection.execute(
            "SELECT status FROM external_effects WHERE id=%s",
            (binding.external_effect_id,),
        ).fetchone()[0]
        attempt_state = connection.execute(
            "SELECT state FROM publication_attempts WHERE id=%s",
            (binding.publication_attempt_id,),
        ).fetchone()[0]

    assert event is not None
    event_payload = event[1]
    assert event_payload["operation_id"] == str(case["operation_id"])
    assert event_payload["permit_id"] == str(issued.permit_id)
    assert event_payload["claim_id"] == str(claim.claim_id)
    assert event_payload["publication_request_id"] == str(binding.publication_request_id)
    assert event_payload["publication_plan_id"] == str(binding.publication_plan_id)
    assert event_payload["publication_attempt_id"] == str(binding.publication_attempt_id)
    assert event_payload["external_effect_id"] == str(binding.external_effect_id)
    assert event_payload["action"] == "reconcile"
    assert event_payload["reason_code"] == "stale_revision"
    assert event_payload["current_publication_plan_id"] == str(replacement_plan_id)
    assert event_payload["current_external_effect_id"] is None
    assert event_payload["remote_receipt_id"] == receipt.id
    assert event_payload["remote_id"] == known_remote_id
    assert event_payload["publication_id"] == publication.id
    assert reservation == "reserved"
    assert job_state == "queued"
    assert effect_state == "planned"
    assert attempt_state == "planned"
    assert _permit_count(case["database"], case["operation_id"]) == 1
    assert _claim_count(case["database"], case["operation_id"]) == 1

    with pytest.raises(psycopg.Error):
        with psycopg.connect(case["database"]) as connection:
            connection.execute(
                "UPDATE v4_cycle_events SET payload='{}' WHERE id=%s", (event[0],)
            )


def test_postgres_unclaimed_no_receipt_publication_cancel_is_local_and_releases_liability(accounting):
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
        seed_publication_rows=True,
    )
    dispatch = case["dispatch"]
    issued = dispatch.issue_permit(case["request"])

    reservation_id = dispatch.cancel_unsubmitted_publication(
        case["operation_id"],
        issued.permit_id,
        binding.publication_request_id,
        binding.publication_plan_id,
        binding.publication_attempt_id,
        binding.external_effect_id,
    )

    assert reservation_id == str(case["production_reservation_id"])
    outcome = asyncio.run(CostReservationRepository(case["database"]).release_unused(reservation_id))
    assert outcome.status.value == "released"
    with psycopg.connect(case["database"]) as connection:
        states = connection.execute(
            """SELECT request.state,plan.state,attempt.state,effect.status,job.state,
                      reservation.status
               FROM publication_requests request
               JOIN publication_plans plan ON plan.publication_request_id=request.id
               JOIN publication_attempts attempt ON attempt.publication_plan_id=plan.id
               JOIN external_effects effect ON effect.id=plan.external_effect_id
               JOIN jobs job ON job.id=%s
               JOIN budget_reservations reservation ON reservation.id=plan.budget_reservation_id
               WHERE request.id=%s""",
            (binding.job_id, binding.publication_request_id),
        ).fetchone()
    assert states == ("planned", "planned", "planned", "cancelled", "cancelled", "released")
    with psycopg.connect(case["database"]) as connection:
        attempt_state_event = connection.execute(
            """SELECT state,source FROM publication_status_events
               WHERE publication_attempt_id=%s""",
            (binding.publication_attempt_id,),
        ).fetchone()
    assert attempt_state_event == ("cancelled", "cancellation")
    with pytest.raises(ProductionAuthorizationDenied):
        dispatch.claim_permit(case["operation_id"], issued.permit_id)
    assert _claim_count(case["database"], case["operation_id"]) == 0


@pytest.mark.parametrize("existing_liability", ["claim", "receipt"])
def test_postgres_local_cancel_refuses_claimed_or_receipted_publication(
    accounting, existing_liability
):
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
        seed_publication_rows=True,
    )
    dispatch = case["dispatch"]
    issued = dispatch.issue_permit(case["request"])
    if existing_liability == "claim":
        dispatch.claim_permit(case["operation_id"], issued.permit_id)
    else:
        asyncio.run(
            PublicationRepository(case["database"]).record_remote_receipt(
                publication_attempt_id=str(binding.publication_attempt_id),
                publisher_id=case["request"].provider_id,
                remote_id="p1-existing-" + uuid4().hex,
                state="accepted",
                safe_metadata_hash="d" * 64,
                remote_url=None,
            )
        )

    reservation_id = dispatch.cancel_unsubmitted_publication(
        case["operation_id"],
        issued.permit_id,
        binding.publication_request_id,
        binding.publication_plan_id,
        binding.publication_attempt_id,
        binding.external_effect_id,
    )

    assert reservation_id is None
    with psycopg.connect(case["database"]) as connection:
        states = connection.execute(
            """SELECT request.state,plan.state,attempt.state,effect.status,job.state,
                      reservation.status
               FROM publication_requests request
               JOIN publication_plans plan ON plan.publication_request_id=request.id
               JOIN publication_attempts attempt ON attempt.publication_plan_id=plan.id
               JOIN external_effects effect ON effect.id=plan.external_effect_id
               JOIN jobs job ON job.id=%s
               JOIN budget_reservations reservation ON reservation.id=plan.budget_reservation_id
               WHERE request.id=%s""",
            (binding.job_id, binding.publication_request_id),
        ).fetchone()
    assert states == ("planned", "planned", "planned", "planned", "queued", "reserved")


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


class _PublicationActivityAdapter:
    def __init__(self):
        self.capabilities = SimpleNamespace(publisher_id="production-publisher", version="1")
        self.submit_calls = 0
        self.reconcile_calls = 0
        self.delivery_calls = 0
        self.status_calls = 0
        self.cancel_calls = 0
        self.status_targets = []
        self.cancel_targets = []
        self.status_state = "processing"

    async def submit(self, request, credential_lease):
        self.submit_calls += 1
        return SimpleNamespace(
            publisher_id="production-publisher",
            remote_id="remote-publication-1",
            state="accepted",
            safe_metadata_hash="a" * 64,
            remote_url=None,
        )

    async def reconcile(self, idempotency_key):
        self.reconcile_calls += 1
        return SimpleNamespace(
            publisher_id="production-publisher",
            remote_id="remote-publication-1",
            state="accepted",
            safe_metadata_hash="a" * 64,
            remote_url=None,
        )

    async def prepare_delivery(self, request, delivery_url):
        self.delivery_calls += 1

    async def status(self, remote_id):
        self.status_calls += 1
        self.status_targets.append(remote_id)
        return SimpleNamespace(
            publisher_id="production-publisher",
            remote_id=remote_id,
            state=self.status_state,
            safe_metadata_hash="b" * 64,
            remote_url=None,
        )

    async def cancel(self, remote_id):
        self.cancel_calls += 1
        self.cancel_targets.append(remote_id)
        return SimpleNamespace(
            publisher_id="production-publisher",
            remote_id=remote_id,
            state="cancelled",
            safe_metadata_hash="c" * 64,
            remote_url=None,
        )


class _PublicationActivityStore:
    def __init__(self, submit_decisions=None):
        self.submit_decisions = list(submit_decisions or [True])
        self.begin_calls = []
        self.completed_effects = []
        self.completed_outputs = []
        self.terminal_states = []
        self.effect_state = "planned"

    async def effect(self, run, effect_key):
        return SimpleNamespace(effect_id="canonical-effect", status=self.effect_state)

    async def begin_effect_submission(self, run, effect_key):
        self.begin_calls.append(effect_key)
        if len(self.submit_decisions) > 1:
            submitting = self.submit_decisions.pop(0)
        else:
            submitting = self.submit_decisions[0]
        if not submitting or self.effect_state not in {"planned", "not_submitted"}:
            return False
        self.effect_state = "submitting"
        return True

    async def complete_effect(self, *args, **kwargs):
        self.completed_effects.append((args, kwargs))
        self.effect_state = "completed"
        return None

    async def complete_with_output(self, *args, **kwargs):
        self.completed_outputs.append((args, kwargs))

    async def terminal(self, run, state):
        self.terminal_states.append(state)


class _PublicationActivityRepository:
    def __init__(self, *, attempt_id=None, receipt_id=None):
        self.attempt_id = str(attempt_id or uuid4())
        self.receipt_id = str(receipt_id or uuid4())
        self.attempt_calls = []
        self.status_events = []
        self.publications = []

    async def create_attempt(self, **kwargs):
        self.attempt_calls.append(kwargs)
        return SimpleNamespace(id=self.attempt_id)

    async def record_remote_receipt(self, **kwargs):
        return SimpleNamespace(id=self.receipt_id, remote_id=kwargs["remote_id"])

    async def record_status_event(self, **kwargs):
        self.status_events.append(kwargs)
        return None

    async def record_publication(self, **kwargs):
        self.publications.append(kwargs)
        return SimpleNamespace(id="canonical-publication-1")


class _DenyingPublicationDispatch:
    def __init__(self, *, deny_action=None, deny_claim=True, claim_reason="approval_invalid"):
        self.claim_calls = []
        self.action_calls = []
        self.deny_action = deny_action
        self.deny_claim = deny_claim
        self.claim_reason = claim_reason

    def claim_permit(self, operation_id, permit_id):
        self.claim_calls.append((operation_id, permit_id))
        if self.deny_claim:
            raise ProductionAuthorizationDenied(self.claim_reason)
        return SimpleNamespace(dispatch_allowed=True, claim_id=uuid4())

    def authorize_action(self, operation_id, permit_id, action_kind):
        self.action_calls.append((operation_id, permit_id, action_kind))
        if action_kind == self.deny_action:
            return SimpleNamespace(authorized=False, reason_code="route_fence_denied")
        return SimpleNamespace(authorized=True, reason_code=None)

    def record_publication_liability_held(
        self, publication_request_id, action_kind, reason_code
    ):
        return False


class _LocalCancelPublicationDispatch(_DenyingPublicationDispatch):
    def __init__(self, reservation_id):
        super().__init__()
        self.reservation_id = reservation_id
        self.local_cancel_calls = []

    def cancel_unsubmitted_publication(
        self,
        operation_id,
        permit_id,
        publication_request_id,
        publication_plan_id,
        publication_attempt_id,
        external_effect_id,
    ):
        self.local_cancel_calls.append(
            (
                operation_id,
                permit_id,
                publication_request_id,
                publication_plan_id,
                publication_attempt_id,
                external_effect_id,
            )
        )
        return self.reservation_id


def _publication_activity_case(*, dispatch=None):
    request = PublicationRequest(
        id=str(uuid4()),
        workspace_id=str(uuid4()),
        content_program_id=str(uuid4()),
        ready_package_id=str(uuid4()),
        publisher_account_id=str(uuid4()),
        publication_approval_request_id=str(uuid4()),
        platform="production-platform",
        destination="channel:approved",
        locale="en-US",
        territory="US",
        visibility="public",
        capability_profile_version=1,
        idempotency_key="production-publication-activity-test",
        approval_reference="production-approval",
        publisher_id="production-publisher",
    )
    plan_id, attempt_id, effect_id, receipt_id, operation_id, permit_id, reservation_id = (
        uuid4() for _ in range(7)
    )
    adapter = _PublicationActivityAdapter()
    lease_calls = []
    store = _PublicationActivityStore()
    repository = _PublicationActivityRepository(
        attempt_id=attempt_id, receipt_id=receipt_id
    )
    state = PublicationWorkflowState(
        store=store,
        repository=repository,
        publisher_adapters={"production-publisher": adapter},
        credential_lease_factory=lambda publication_request: lease_calls.append(publication_request)
        or object(),
        cost_repository=None,
    )
    cost_calls = {"settle": [], "release": []}

    async def settle(reservation_id, *, actual_micros):
        cost_calls["settle"].append((reservation_id, actual_micros))
        return SimpleNamespace(status=CostSettlementStatus.SETTLED)

    async def release_unused(reservation_id):
        cost_calls["release"].append(reservation_id)

    state.cost_repository = SimpleNamespace(settle=settle, release_unused=release_unused)
    if dispatch is not None:
        state.production_dispatch = dispatch
    activities = PublicationActivities(state)
    run = SimpleNamespace(
        job_id=uuid4(),
        trace_context=SimpleNamespace(trace_id="a" * 32, span_id="b" * 16),
    )

    async def current_run():
        return run

    async def checkpoint(_name):
        return None

    async def current_authorization_denial(_request):
        return None

    activities._run = current_run
    activities._checkpoint = checkpoint
    activities._current_authorization_denial = current_authorization_denial
    payload = {
        "request": request.model_dump(mode="json"),
        "publication_request_id": request.id,
        "publication_plan_id": str(plan_id),
        "publication_attempt_id": str(attempt_id),
        "external_effect_id": str(effect_id),
        "production_operation_id": str(operation_id),
        "production_permit_id": str(permit_id),
        "budget_reservation_id": str(reservation_id),
    }

    canonical = SimpleNamespace(
        request=request,
        job_id=run.job_id,
        publication_plan_id=str(plan_id),
        publication_plan_state="authorized",
        publication_attempt_id=str(attempt_id),
        publication_attempt_state="authorized",
        external_effect_id=str(effect_id),
        external_effect_state="submitting",
        remote_receipt_id=None,
        remote_id=None,
        remote_receipt_state=None,
        publication_id=None,
        publication_state=None,
        budget_reservation_id=str(reservation_id),
        approval_binding=SimpleNamespace(
            operation_id=UUID(payload["production_operation_id"]),
            publication_request_id=UUID(request.id),
            job_id=run.job_id,
            publication_plan_id=plan_id,
            publication_attempt_id=attempt_id,
            external_effect_id=effect_id,
            remote_receipt_id=None,
            budget_reservation_id=reservation_id,
        ),
    )

    async def current_production_authorization(_request):
        return canonical, None

    activities._load_current_production_authorization = current_production_authorization
    activities._publication_test_canonical = canonical
    activities._publication_test_store = store
    activities._publication_test_repository = repository
    activities._publication_test_cost_calls = cost_calls
    return activities, adapter, lease_calls, payload


def test_production_publication_revoke_between_activities_never_submits():
    dispatch = _DenyingPublicationDispatch()
    activities, adapter, lease_calls, payload = _publication_activity_case(dispatch=dispatch)

    result = asyncio.run(activities.submit_or_reconcile(payload))

    assert adapter.submit_calls == 0
    assert lease_calls == []
    assert dispatch.claim_calls
    assert result["authorization_revoked"] is True


def test_production_publication_route_transfer_blocks_next_action():
    dispatch = _DenyingPublicationDispatch(deny_action="delivery")
    activities, adapter, _lease_calls, payload = _publication_activity_case(dispatch=dispatch)

    result = asyncio.run(activities.delivery(payload))

    assert adapter.delivery_calls == 0
    assert dispatch.action_calls[-1][2] == "delivery"
    assert result.get("authorization_revoked") is True


def test_successful_production_delivery_then_route_transfer_blocks_submit():
    dispatch = _DenyingPublicationDispatch(claim_reason="route_fence_denied")
    activities, adapter, lease_calls, payload = _publication_activity_case(dispatch=dispatch)

    delivered = asyncio.run(activities.delivery(payload))
    result = asyncio.run(activities.submit_or_reconcile(delivered))

    assert adapter.delivery_calls == 1
    assert dispatch.action_calls[-1][2] == "delivery"
    assert dispatch.claim_calls
    assert adapter.submit_calls == 0
    assert lease_calls == []
    assert result.get("authorization_revoked") is True


def test_missing_production_dispatch_never_calls_nonfixture_adapter():
    activities, adapter, lease_calls, payload = _publication_activity_case()

    with pytest.raises(ApplicationError) as raised:
        asyncio.run(activities.submit_or_reconcile(payload))

    assert raised.value.type == "ProductionPublicationLiabilityHeld"
    assert raised.value.non_retryable is True
    assert adapter.submit_calls == 0
    assert lease_calls == []


@pytest.mark.parametrize(
    "target_field",
    ["publication_plan_id", "publication_attempt_id", "external_effect_id"],
)
def test_production_submit_rejects_workflow_target_mismatch_before_write_or_claim(target_field):
    dispatch = _DenyingPublicationDispatch()
    activities, adapter, lease_calls, payload = _publication_activity_case(dispatch=dispatch)
    payload[target_field] = str(uuid4())

    result = asyncio.run(activities.submit_or_reconcile(payload))

    assert result.get("authorization_revoked") is True
    assert activities._publication_test_repository.attempt_calls == []
    assert activities._publication_test_store.begin_calls == []
    assert dispatch.claim_calls == []
    assert adapter.submit_calls == 0
    assert lease_calls == []


@pytest.mark.parametrize(
    ("activity_name", "target_field"),
    [
        ("await_publication", "remote_id"),
        ("await_publication", "publication_attempt_id"),
        ("await_publication", "remote_receipt_id"),
        ("cancel", "remote_id"),
        ("cancel", "publication_attempt_id"),
        ("cancel", "remote_receipt_id"),
    ],
)
def test_production_activity_rejects_mismatched_remote_attempt_or_receipt_before_provider_io(
    activity_name, target_field
):
    dispatch = _DenyingPublicationDispatch()
    activities, adapter, _lease_calls, payload = _publication_activity_case(dispatch=dispatch)
    canonical = activities._publication_test_canonical
    canonical.remote_receipt_id = activities._publication_test_repository.receipt_id
    canonical.remote_id = "canonical-remote-1"
    canonical.remote_receipt_state = "accepted"
    canonical.approval_binding.remote_receipt_id = UUID(canonical.remote_receipt_id)
    payload.update(
        {
            "remote_receipt_id": canonical.remote_receipt_id,
            "remote_id": canonical.remote_id,
        }
    )
    payload[target_field] = str(uuid4())

    if activity_name == "await_publication":
        result = asyncio.run(activities.await_publication(payload))
        assert result.get("authorization_revoked") is True
    else:
        result = asyncio.run(activities.cancel(payload))
        assert result.publication_state == "ambiguous_requires_reconciliation"

    assert adapter.status_calls == 0
    assert adapter.cancel_calls == 0
    assert activities._publication_test_repository.status_events == []
    assert activities._publication_test_repository.publications == []


def test_unclaimed_no_receipt_cancel_uses_local_terminal_path_without_provider_io():
    activities, adapter, _lease_calls, payload = _publication_activity_case()
    dispatch = _LocalCancelPublicationDispatch(payload["budget_reservation_id"])
    activities._state.production_dispatch = dispatch

    result = asyncio.run(activities.cancel(payload))

    assert result.publication_state == "cancelled"
    assert len(dispatch.local_cancel_calls) == 1
    assert dispatch.action_calls == []
    assert adapter.cancel_calls == 0
    assert activities._publication_test_cost_calls["release"] == [
        payload["budget_reservation_id"]
    ]
    assert activities._publication_test_store.terminal_states == ["cancelled"]


def test_cancel_with_canonical_receipt_uses_fresh_authorization_and_provider_target():
    dispatch = _LocalCancelPublicationDispatch(None)
    activities, adapter, _lease_calls, payload = _publication_activity_case(dispatch=dispatch)
    canonical = activities._publication_test_canonical
    canonical.remote_receipt_id = activities._publication_test_repository.receipt_id
    canonical.remote_id = "canonical-remote-1"
    canonical.remote_receipt_state = "accepted"
    canonical.approval_binding.remote_receipt_id = UUID(canonical.remote_receipt_id)
    canonical.approval_binding.provider_id = "production-publisher"
    payload.update(
        {
            "remote_receipt_id": canonical.remote_receipt_id,
            "remote_id": canonical.remote_id,
        }
    )

    result = asyncio.run(activities.cancel(payload))

    assert result.publication_state == "cancelled"
    assert dispatch.local_cancel_calls == []
    assert dispatch.action_calls[0][2] == "cancel"
    assert adapter.cancel_calls == 1
    assert adapter.cancel_targets == ["canonical-remote-1"]
    assert activities._publication_test_cost_calls["release"] == [payload["budget_reservation_id"]]
    assert activities._publication_test_store.terminal_states == ["cancelled"]


@pytest.mark.parametrize(
    "response_kind",
    ["none", "nonterminal", "provider_mismatch", "remote_mismatch"],
)
def test_production_cancel_unverified_provider_response_holds_claimed_liability(response_kind):
    dispatch = _LocalCancelPublicationDispatch(None)
    dispatch.hold_calls = []
    dispatch.record_publication_liability_held = (
        lambda *args: dispatch.hold_calls.append(args) or True
    )
    activities, adapter, _lease_calls, payload = _publication_activity_case(dispatch=dispatch)
    canonical = activities._publication_test_canonical
    canonical.remote_receipt_id = activities._publication_test_repository.receipt_id
    canonical.remote_id = "canonical-remote-1"
    canonical.remote_receipt_state = "accepted"
    canonical.approval_binding.remote_receipt_id = UUID(canonical.remote_receipt_id)
    canonical.approval_binding.provider_id = "production-publisher"
    payload.update(
        {
            "remote_receipt_id": canonical.remote_receipt_id,
            "remote_id": canonical.remote_id,
        }
    )
    response = {
        "none": None,
        "nonterminal": SimpleNamespace(
            publisher_id="production-publisher",
            remote_id="canonical-remote-1",
            state="processing",
            safe_metadata_hash="c" * 64,
        ),
        "provider_mismatch": SimpleNamespace(
            publisher_id="other-provider",
            remote_id="canonical-remote-1",
            state="cancelled",
            safe_metadata_hash="c" * 64,
        ),
        "remote_mismatch": SimpleNamespace(
            publisher_id="production-publisher",
            remote_id="other-remote",
            state="cancelled",
            safe_metadata_hash="c" * 64,
        ),
    }[response_kind]

    async def cancel(_remote_id):
        adapter.cancel_calls += 1
        adapter.cancel_targets.append(_remote_id)
        return response

    adapter.cancel = cancel

    with pytest.raises(ApplicationError) as raised:
        asyncio.run(activities.cancel(payload))

    assert raised.value.type == "ProductionPublicationLiabilityHeld"
    assert raised.value.non_retryable is True
    assert dispatch.action_calls[-1][2] == "cancel"
    assert dispatch.hold_calls == [
        (UUID(payload["request"]["id"]), "cancel", "reconciliation_unresolved")
    ]
    assert adapter.cancel_calls == 1
    assert adapter.cancel_targets == ["canonical-remote-1"]
    assert activities._publication_test_repository.status_events == []
    assert activities._publication_test_repository.publications == []
    assert activities._publication_test_cost_calls["release"] == []
    assert activities._publication_test_store.terminal_states == []


def test_cancel_rejects_unclaimed_workflow_remote_target_before_local_writes():
    dispatch = _LocalCancelPublicationDispatch("reservation-1")
    activities, adapter, _lease_calls, payload = _publication_activity_case(dispatch=dispatch)
    payload["remote_id"] = "forged-remote-target"

    result = asyncio.run(activities.cancel(payload))

    assert result.publication_state == "ambiguous_requires_reconciliation"
    assert result.denial_reason == "production_publication_receipt_target_mismatch"
    assert dispatch.local_cancel_calls == []
    assert adapter.cancel_calls == 0
    assert activities._publication_test_cost_calls["release"] == []
    assert activities._publication_test_store.terminal_states == []


def test_claimed_cancel_without_receipt_holds_instead_of_releasing_liability():
    dispatch = _LocalCancelPublicationDispatch(None)
    dispatch.record_publication_liability_held = lambda *args: dispatch.hold_calls.append(args) or True
    dispatch.hold_calls = []
    activities, adapter, _lease_calls, payload = _publication_activity_case(dispatch=dispatch)

    with pytest.raises(ApplicationError) as raised:
        asyncio.run(activities.cancel(payload))

    assert raised.value.type == "ProductionPublicationLiabilityHeld"
    assert raised.value.non_retryable is True
    assert len(dispatch.local_cancel_calls) == 1
    assert dispatch.action_calls == []
    assert len(dispatch.hold_calls) == 1
    assert adapter.cancel_calls == 0
    assert activities._publication_test_cost_calls["release"] == []
    assert activities._publication_test_store.terminal_states == []


@pytest.mark.parametrize(
    "binding_field",
    ["publication_plan_id", "publication_attempt_id", "external_effect_id"],
)
def test_production_action_rejects_approval_binding_mismatch_before_route_or_provider_io(
    binding_field,
):
    dispatch = _DenyingPublicationDispatch()
    activities, adapter, _lease_calls, payload = _publication_activity_case(dispatch=dispatch)
    canonical = activities._publication_test_canonical
    canonical.remote_receipt_id = activities._publication_test_repository.receipt_id
    canonical.remote_id = "canonical-remote-1"
    canonical.remote_receipt_state = "accepted"
    canonical.approval_binding.remote_receipt_id = UUID(canonical.remote_receipt_id)
    payload.update(
        {
            "remote_receipt_id": canonical.remote_receipt_id,
            "remote_id": canonical.remote_id,
        }
    )
    setattr(canonical.approval_binding, binding_field, uuid4())

    result = asyncio.run(activities.await_publication(payload))

    assert result.get("authorization_revoked") is True
    assert dispatch.action_calls == []
    assert adapter.status_calls == 0
    assert activities._publication_test_repository.status_events == []


class _HoldingPublicationDispatch(_DenyingPublicationDispatch):
    def __init__(self, *, deny_action=None):
        super().__init__(deny_action=deny_action)
        self.hold_calls = []

    def claim_permit(self, operation_id, permit_id):
        self.claim_calls.append((operation_id, permit_id))
        return SimpleNamespace(dispatch_allowed=True, claim_id=uuid4())

    def record_publication_liability_held(
        self, publication_request_id, action_kind, reason_code
    ):
        self.hold_calls.append((publication_request_id, action_kind, reason_code))
        return True


class _PauseAfterClaimPublicationDispatch(_HoldingPublicationDispatch):
    def __init__(self, durable_dispatch=None):
        super().__init__()
        self.durable_dispatch = durable_dispatch
        self.claim_entered = Event()
        self.claim_release = Event()
        self.claimed = False
        self.claim_id = None

    def claim_permit(self, operation_id, permit_id):
        self.claim_calls.append((operation_id, permit_id))
        if self.claimed and self.durable_dispatch is None:
            raise ProductionAuthorizationDenied("dispatch_already_claimed")
        result = (
            self.durable_dispatch.claim_permit(operation_id, permit_id)
            if self.durable_dispatch is not None
            else SimpleNamespace(dispatch_allowed=True, claim_id=uuid4())
        )
        self.claimed = True
        self.claim_id = result.claim_id
        self.claim_entered.set()
        if not self.claim_release.wait(timeout=10):
            raise TimeoutError("test did not release the claimed submit owner")
        return result

    def authorize_action(self, operation_id, permit_id, action_kind):
        self.action_calls.append((operation_id, permit_id, action_kind))
        if self.durable_dispatch is not None:
            return self.durable_dispatch.authorize_action(
                operation_id, permit_id, action_kind
            )
        return SimpleNamespace(
            authorized=self.claimed,
            reason_code=None if self.claimed else "dispatch_already_claimed",
        )

    def record_publication_liability_held(
        self, publication_request_id, action_kind, reason_code
    ):
        self.hold_calls.append((publication_request_id, action_kind, reason_code))
        if self.durable_dispatch is not None:
            return self.durable_dispatch.record_publication_liability_held(
                publication_request_id, action_kind, reason_code
            )
        return True


class _RevokeOnSecondReconcileDispatch(_HoldingPublicationDispatch):
    def authorize_action(self, operation_id, permit_id, action_kind):
        self.action_calls.append((operation_id, permit_id, action_kind))
        matching = [call for call in self.action_calls if call[2] == "reconcile"]
        if action_kind == "reconcile" and len(matching) == 2:
            return SimpleNamespace(authorized=False, reason_code="route_fence_denied")
        return SimpleNamespace(authorized=True, reason_code=None)


def test_reconcile_rechecks_current_authority_immediately_before_provider_dispatch():
    dispatch = _RevokeOnSecondReconcileDispatch()
    activities, adapter, _lease_calls, payload = _publication_activity_case(dispatch=dispatch)
    activities._publication_test_store.submit_decisions = [False]
    activities._publication_test_store.effect_state = "submitting"

    with pytest.raises(ApplicationError) as raised:
        asyncio.run(activities.submit_or_reconcile(payload))

    assert raised.value.type == "ProductionPublicationLiabilityHeld"
    assert [call[2] for call in dispatch.action_calls] == ["reconcile", "reconcile"]
    assert dispatch.hold_calls[0][1] == "reconcile"
    assert adapter.reconcile_calls == 0


def test_concurrent_retry_cannot_submit_or_release_between_claim_and_begin_transition():
    dispatch = _PauseAfterClaimPublicationDispatch()
    activities, adapter, _lease_calls, payload = _publication_activity_case(dispatch=dispatch)

    async def run_interleaving():
        owner = asyncio.create_task(activities.submit_or_reconcile(payload))
        assert await asyncio.to_thread(dispatch.claim_entered.wait, 5)
        effect_state_while_owner_paused = activities._publication_test_store.effect_state
        retry_error = None
        retry_result = None
        try:
            try:
                retry_result = await activities.submit_or_reconcile(payload)
            except ApplicationError as error:
                retry_error = error
        finally:
            dispatch.claim_release.set()
        owner_result = await owner
        return effect_state_while_owner_paused, retry_error, retry_result, owner_result

    paused_state, retry_error, retry_result, owner_result = asyncio.run(run_interleaving())

    assert paused_state == "planned"
    assert retry_error is not None
    assert retry_error.type == "ProductionPublicationLiabilityHeld"
    assert retry_error.non_retryable is True
    assert retry_result is None
    assert owner_result["publication_state"] == "accepted"
    assert dispatch.claimed is True
    assert dispatch.claim_id is not None
    assert len(dispatch.claim_calls) == 2
    assert len(dispatch.hold_calls) == 1
    assert dispatch.hold_calls[0][1] == "submit"
    assert activities._publication_test_store.begin_calls == [
        f'{payload["request"]["idempotency_key"]}:publish'
    ]
    assert adapter.submit_calls == 1
    assert adapter.reconcile_calls == 0
    assert activities._publication_test_cost_calls["release"] == []
    assert activities._publication_test_store.terminal_states == []


def test_postgres_concurrent_retry_persists_claim_and_hold_before_submit_owner_resumes(accounting):
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
        seed_publication_rows=True,
    )
    issued = case["dispatch"].issue_permit(case["request"])
    dispatch = _PauseAfterClaimPublicationDispatch(case["dispatch"])
    activities, adapter, _lease_calls, payload = _publication_activity_case(dispatch=dispatch)
    activities._state.publisher_adapters[case["request"].provider_id] = adapter
    request = PublicationRequest.model_validate(payload["request"]).model_copy(
        update={
            "id": str(binding.publication_request_id),
            "workspace_id": str(case["workspace_id"]),
            "content_program_id": str(case["request"].program_id),
            "ready_package_id": str(case["publication_rows"]["ready_package_id"]),
            "publisher_account_id": str(case["request"].account_id),
            "publication_approval_request_id": str(case["request"].approval_request_id),
            "platform": scope["platform"],
            "destination": scope["destination"],
            "locale": scope["locale"],
            "territory": scope["territory"],
            "visibility": scope["visibility"],
            "idempotency_key": case["request"].idempotency_key,
            "publisher_id": case["request"].provider_id,
        }
    )
    payload.update(
        {
            "request": request.model_dump(mode="json"),
            "publication_request_id": str(binding.publication_request_id),
            "publication_plan_id": str(binding.publication_plan_id),
            "publication_attempt_id": str(binding.publication_attempt_id),
            "external_effect_id": str(binding.external_effect_id),
            "budget_reservation_id": str(case["production_reservation_id"]),
            "production_operation_id": str(case["operation_id"]),
            "production_permit_id": str(issued.permit_id),
        }
    )
    canonical = activities._publication_test_canonical
    canonical.request = request
    canonical.job_id = binding.job_id
    canonical.publication_plan_id = str(binding.publication_plan_id)
    canonical.publication_attempt_id = str(binding.publication_attempt_id)
    canonical.external_effect_id = str(binding.external_effect_id)
    canonical.budget_reservation_id = str(case["production_reservation_id"])
    canonical.approval_binding = SimpleNamespace(
        operation_id=case["operation_id"],
        publication_request_id=binding.publication_request_id,
        job_id=binding.job_id,
        publication_plan_id=binding.publication_plan_id,
        publication_attempt_id=binding.publication_attempt_id,
        external_effect_id=binding.external_effect_id,
        remote_receipt_id=None,
        budget_reservation_id=case["production_reservation_id"],
    )

    async def current_production_authorization(_request):
        return canonical, None

    activities._load_current_production_authorization = current_production_authorization

    async def run_interleaving():
        owner = asyncio.create_task(activities.submit_or_reconcile(payload))
        assert await asyncio.to_thread(dispatch.claim_entered.wait, 5)
        effect_state_while_owner_paused = activities._publication_test_store.effect_state
        try:
            with pytest.raises(ApplicationError) as raised:
                await activities.submit_or_reconcile(payload)
        finally:
            dispatch.claim_release.set()
        owner_result = await owner
        return effect_state_while_owner_paused, raised.value, owner_result

    paused_state, retry_error, owner_result = asyncio.run(run_interleaving())

    assert paused_state == "planned"
    assert retry_error.type == "ProductionPublicationLiabilityHeld"
    assert retry_error.non_retryable is True
    assert owner_result["publication_state"] == "accepted"
    assert adapter.submit_calls == 1
    assert len(dispatch.hold_calls) == 1
    with psycopg.connect(case["database"]) as connection:
        pointer = connection.execute(
            "SELECT current_claim_id FROM v4_production_current_dispatch WHERE operation_id=%s",
            (case["operation_id"],),
        ).fetchone()
        claims = connection.execute(
            "SELECT count(*) FROM v4_production_effect_permit_claims WHERE operation_id=%s",
            (case["operation_id"],),
        ).fetchone()[0]
        permit_count = connection.execute(
            "SELECT count(*) FROM v4_production_effect_permits WHERE operation_id=%s",
            (case["operation_id"],),
        ).fetchone()[0]
        hold = connection.execute(
            """SELECT payload FROM v4_cycle_events
               WHERE cycle_id=%s AND kind='production_publication_liability_held'""",
            (case["cycle_id"],),
        ).fetchone()
        reservation_state = connection.execute(
            "SELECT status FROM budget_reservations WHERE id=%s",
            (case["production_reservation_id"],),
        ).fetchone()[0]
        job_state = connection.execute(
            "SELECT state FROM jobs WHERE id=%s", (binding.job_id,)
        ).fetchone()[0]
        effect_state = connection.execute(
            "SELECT status FROM external_effects WHERE id=%s", (binding.external_effect_id,)
        ).fetchone()[0]
    assert pointer[0] is not None
    assert claims == 1
    assert permit_count == 1
    assert hold is not None
    assert hold[0]["action"] == "submit"
    assert hold[0]["claim_id"] == str(pointer[0])
    assert reservation_state == "reserved"
    assert job_state == "queued"
    assert effect_state == "planned"
    assert activities._publication_test_cost_calls["release"] == []
    assert activities._publication_test_store.terminal_states == []


def _grant_fixture_permit_scope(accounting):
    service, _, _, database = accounting
    with psycopg.connect(database) as connection:
        connection.execute(
            """INSERT INTO permission_grants(
                      workspace_id,principal_type,principal_id,scope,effect,constraints)
               VALUES(%s,'identity',%s,'cycles:permit','allow','{}')
               ON CONFLICT (workspace_id,principal_type,principal_id,scope)
               DO UPDATE SET effect='allow',constraints='{}',
                             expires_at=clock_timestamp()+interval '1 hour'""",
            (service.workspace_id, str(service.subject_id)),
        )


def _seed_fixture_rollback_permit(case, *, final_state):
    database = case["database"]
    with psycopg.connect(database, row_factory=dict_row) as connection:
        cycle = connection.execute(
            """SELECT goal.workspace_id,goal.id AS goal_id,
                      cycle.intent_id,cycle.context_id,cycle.operation_id,
                      intent.goal_revision,
                      workspace_stop.revision AS workspace_stop_revision,
                      goal_stop.revision AS goal_stop_revision,
                      (SELECT mapping.reservation_id
                       FROM v4_allocation_reservations mapping
                       WHERE mapping.operation_id=cycle.operation_id
                       ORDER BY mapping.budget_id LIMIT 1) AS reservation_id
               FROM v4_cycles cycle
               JOIN v4_cycle_intents intent ON intent.id=cycle.intent_id
               JOIN v4_goals goal ON goal.id=intent.goal_id
               JOIN v4_stop_scopes workspace_stop
                 ON workspace_stop.workspace_id=goal.workspace_id
                AND workspace_stop.scope_key='workspace'
               JOIN v4_stop_scopes goal_stop
                 ON goal_stop.workspace_id=goal.workspace_id
                AND goal_stop.scope_key='goal:' || goal.id::text
               WHERE cycle.id=%s""",
            (case["cycle_id"],),
        ).fetchone()
        assert cycle is not None and cycle["reservation_id"] is not None
        request = {
            "context_id": str(cycle["context_id"]),
            "operation_id": str(cycle["operation_id"]),
            "expected_goal_revision": cycle["goal_revision"],
            "account_ref": "fixture-account",
            "purpose": "fixture_execution",
            "effect": "fixture.noop",
            "artifact_sha256": "f" * 64,
            "ttl_seconds": 30,
        }
        fingerprint = sha256(
            json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        claim_id, permit_id = uuid4(), uuid4()
        idempotency_key = "rollback-inventory-fixture-" + uuid4().hex
        connection.execute(
            """INSERT INTO v4_permit_claims(
                      id,cycle_id,operation_id,context_id,request_fingerprint)
               VALUES(%s,%s,%s,%s,%s)""",
            (claim_id, case["cycle_id"], cycle["operation_id"], cycle["context_id"], fingerprint),
        )
        connection.execute(
            """INSERT INTO v4_dispatch_permits(
                      id,claim_id,version,idempotency_key,fingerprint,subject_id,
                      goal_revision,workspace_stop_revision,goal_stop_revision,
                      reservation_id,request,expires_at,traceparent)
               VALUES(%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,
                      clock_timestamp()+interval '30 seconds',
                      '00-11111111111111111111111111111111-2222222222222222-01')""",
            (
                permit_id,
                claim_id,
                idempotency_key,
                fingerprint,
                case["subject_id"],
                cycle["goal_revision"],
                cycle["workspace_stop_revision"],
                cycle["goal_stop_revision"],
                cycle["reservation_id"],
                Jsonb(request),
            ),
        )
        connection.execute(
            "UPDATE v4_permit_claims SET current_permit_id=%s,revision=2 WHERE id=%s",
            (permit_id, claim_id),
        )
        if final_state in {"claimed", "unknown"}:
            connection.execute(
                """INSERT INTO v4_cycle_events(
                          id,workspace_id,subject_id,goal_id,intent_id,cycle_id,
                          kind,traceparent,payload)
                   VALUES(%s,%s,%s,%s,%s,%s,'permit_claimed',
                          '00-11111111111111111111111111111111-2222222222222222-01',%s)""",
                (
                    uuid4(),
                    cycle["workspace_id"],
                    case["subject_id"],
                    cycle["goal_id"],
                    cycle["intent_id"],
                    case["cycle_id"],
                    Jsonb({"permit_id": str(permit_id)}),
                ),
            )
            connection.execute(
                """UPDATE v4_permit_claims
                   SET state='claimed',claimed_at=clock_timestamp(),revision=3
                   WHERE id=%s""",
                (claim_id,),
            )
        if final_state == "unknown":
            connection.execute(
                """INSERT INTO v4_cycle_events(
                          id,workspace_id,subject_id,goal_id,intent_id,cycle_id,
                          kind,traceparent,payload)
                   VALUES(%s,%s,%s,%s,%s,%s,'permit_unknown',
                          '00-11111111111111111111111111111111-2222222222222222-01',%s)""",
                (
                    uuid4(),
                    cycle["workspace_id"],
                    case["subject_id"],
                    cycle["goal_id"],
                    cycle["intent_id"],
                    case["cycle_id"],
                    Jsonb({"permit_id": str(permit_id)}),
                ),
            )
            connection.execute(
                "UPDATE v4_permit_claims SET state='unknown',revision=4 WHERE id=%s",
                (claim_id,),
            )
    return {"permit_id": str(permit_id)}, {"claim_id": str(claim_id)}


def _production_rollback_case(accounting, *, publication=False):
    service, _, _, database = accounting
    _grant_fixture_permit_scope(accounting)
    binding = None
    scope = None
    if publication:
        binding = PublicationEffectBinding(
            publication_request_id=uuid4(),
            job_id=uuid4(),
            workflow_id="rollback-inventory-publication",
            workflow_run_id="rollback-inventory-run-" + uuid4().hex,
            publication_plan_id=uuid4(),
            publication_attempt_id=uuid4(),
            external_effect_id=uuid4(),
        )
        scope = {
            "platform": "p1-local",
            "destination": "channel:approved",
            "locale": "en-US",
            "territory": "US",
            "visibility": "public",
        }
    case = _production_case(
        accounting,
        publication_scope=scope,
        publication_binding=binding,
        seed_publication_rows=publication,
    )
    accounting_transfer(
        service,
        case["cycle_id"],
        operation=case["operation_id"],
        key="rollback-inventory-fixture-transfer",
    )
    return case, binding


def test_rollback_inventory_covers_fixture_and_production_modes(accounting):
    case, _ = _production_rollback_case(accounting)
    production_permit = case["dispatch"].issue_permit(case["request"])
    production_claim = case["dispatch"].claim_permit(
        case["operation_id"], production_permit.permit_id
    )
    fixture_permit, fixture_claim = _seed_fixture_rollback_permit(case, final_state="claimed")

    with psycopg.connect(case["database"]) as connection:
        inventory = read_production_policy_rollback_inventory(
            connection, workspace_id=case["workspace_id"]
        )

    assert isinstance(inventory, ProductionPolicyRollbackInventoryV1)
    assert inventory.workspace_id == case["workspace_id"]
    assert inventory.schema_version == "salience.p1-production-policy-rollback-inventory.v1"
    assert re.fullmatch(r"[0-9a-f]{64}", inventory.inventory_sha256)
    assert not hasattr(inventory, "safe")
    assert any(
        entry["source"] == "fixture"
        and entry["relation"] == "v4_dispatch_permits"
        and str(fixture_permit["permit_id"]) in entry["identity"]
        for entry in inventory.inventory
    )
    assert any(
        entry["source"] == "fixture"
        and entry["relation"] == "v4_permit_claims"
        and str(fixture_claim["claim_id"]) in entry["identity"]
        for entry in inventory.inventory
    )
    assert any(
        entry["source"] == "production"
        and entry["relation"] == "v4_production_effect_permits"
        and str(production_permit.permit_id) in entry["identity"]
        for entry in inventory.inventory
    )
    assert any(
        entry["source"] == "production"
        and entry["relation"] == "v4_production_effect_permit_claims"
        and str(production_claim.claim_id) in entry["identity"]
        for entry in inventory.inventory
    )


def test_rollback_inventory_is_deterministic_and_read_only(accounting):
    case, _ = _production_rollback_case(accounting)
    case["dispatch"].issue_permit(case["request"])
    relations = (
        "v4_production_effect_permits",
        "v4_production_effect_permit_claims",
        "v4_production_current_dispatch",
        "v4_dispatch_permits",
        "v4_permit_claims",
        "v4_cycles",
        "v4_run_contexts",
        "v4_cycle_events",
        "v4_cycle_outbox",
        "jobs",
        "publication_requests",
        "publication_attempts",
        "budget_reservations",
        "remote_publication_receipts",
    )

    def row_counts(connection):
        return tuple(
            connection.execute(f"SELECT count(*) FROM public.{relation}").fetchone()[0]
            for relation in relations
        )

    class RecordingConnection:
        def __init__(self, connection):
            self.connection = connection
            self.statements = []

        def execute(self, statement, parameters=None):
            self.statements.append((statement, parameters))
            return self.connection.execute(statement, parameters)

    with psycopg.connect(case["database"]) as connection:
        before = row_counts(connection)
        connection.execute("SET TRANSACTION READ ONLY")
        recorded = RecordingConnection(connection)
        first = read_production_policy_rollback_inventory(
            recorded, workspace_id=case["workspace_id"]
        )
        second = read_production_policy_rollback_inventory(
            recorded, workspace_id=case["workspace_id"]
        )
        after = row_counts(connection)

    assert len(recorded.statements) == 2
    assert after == before
    assert all(parameters == (case["workspace_id"],) for _, parameters in recorded.statements)
    assert first.inventory_sha256 == second.inventory_sha256
    assert first.inventory == second.inventory
    assert first.status == second.status


def test_rollback_inventory_retains_unknown_and_unmatched_liabilities(accounting):
    case, binding = _production_rollback_case(accounting, publication=True)
    production_permit = case["dispatch"].issue_permit(case["request"])
    fixture_permit, fixture_claim = _seed_fixture_rollback_permit(case, final_state="unknown")
    unmatched_reservation_id = uuid4()
    with psycopg.connect(case["database"]) as connection:
        connection.execute(
            """INSERT INTO budget_reservations(
                      id,budget_id,reservation_key,estimated_amount,reserved_amount,status,expires_at)
               VALUES(%s,%s,%s,0.020000,0.020000,'reserved',
                      clock_timestamp()+interval '1 hour')""",
            (
                unmatched_reservation_id,
                case["production_budget_id"],
                "rollback-unmatched-" + str(unmatched_reservation_id),
            ),
        )
        assert connection.execute(
            "SELECT 1 FROM remote_publication_receipts WHERE publication_attempt_id=%s",
            (binding.publication_attempt_id,),
        ).fetchone() is None

    with psycopg.connect(case["database"]) as connection:
        inventory = read_production_policy_rollback_inventory(
            connection, workspace_id=case["workspace_id"]
        )

    blocker_identities = {entry["identity"] for entry in inventory.blockers}
    assert inventory.status == "blocked"
    assert any(str(production_permit.permit_id) in identity for identity in blocker_identities)
    assert any(str(fixture_permit["permit_id"]) in identity for identity in blocker_identities)
    assert any(str(fixture_claim["claim_id"]) in identity for identity in blocker_identities)
    assert any(str(unmatched_reservation_id) in identity for identity in blocker_identities)
    assert any(str(binding.external_effect_id) in identity for identity in blocker_identities)
    assert any(
        entry["relation"] == "v4_permit_claims"
        and entry.get("row", {}).get("state") == "unknown"
        for entry in inventory.blockers
    )
    missing_receipts = [
        entry["relation"] == "remote_publication_receipts"
        and entry.get("inventory_status") == "empty"
        and entry["identity"] == "~empty"
        for entry in inventory.blockers
    ]
    assert any(missing_receipts)


@pytest.mark.parametrize(
    "malformation",
    (
        "version",
        "workspace",
        "entry_shape",
        "duplicate",
        "ordering",
        "uppercase_fingerprint",
        "database_digest_mismatch",
    ),
)
def test_rollback_inventory_rejects_malformed_responses(malformation):
    workspace_id = uuid4()
    empty = {
        "source": "authority",
        "relation": "policy_versions",
        "identity": "~empty",
        "inventory_status": "empty",
        "row": None,
    }
    second = {
        "source": "publication",
        "relation": "jobs",
        "identity": "~empty",
        "inventory_status": "empty",
        "row": None,
    }
    payload = {
        "schema_version": "salience.p1-production-policy-rollback-inventory.v1",
        "workspace_id": str(workspace_id),
        "inventory": [empty],
        "inventory_sha256": "a" * 64,
    }
    database_digest = "a" * 64
    if malformation == "version":
        payload["schema_version"] = "salience.p1-production-policy-rollback-inventory.v0"
    elif malformation == "workspace":
        payload["workspace_id"] = str(uuid4())
    elif malformation == "entry_shape":
        payload["inventory"] = [{"source": "authority"}]
    elif malformation == "duplicate":
        payload["inventory"] = [empty, dict(empty)]
    elif malformation == "ordering":
        payload["inventory"] = [second, empty]
    elif malformation == "uppercase_fingerprint":
        payload["inventory_sha256"] = "A" * 64
        database_digest = "A" * 64
    elif malformation == "database_digest_mismatch":
        database_digest = "b" * 64

    class Result:
        def fetchone(self):
            return payload, database_digest

    class Connection:
        def __init__(self):
            self.calls = []

        def execute(self, statement, parameters):
            self.calls.append((statement, parameters))
            return Result()

    connection = Connection()
    with pytest.raises(ValueError):
        read_production_policy_rollback_inventory(connection, workspace_id=workspace_id)
    assert len(connection.calls) == 1
    assert connection.calls[0][1] == (workspace_id,)


@pytest.mark.parametrize("denial_source", ["route", "current_authority"])
@pytest.mark.parametrize("action_name", ["reconcile", "await", "cancel", "finalize"])
def test_postclaim_publication_denial_persists_hold_and_is_nonretryable(
    denial_source, action_name
):
    denied_action = {
        "reconcile": "reconcile",
        "await": "await",
        "cancel": "cancel",
        "finalize": "finalize",
    }[action_name]
    dispatch = _HoldingPublicationDispatch(
        deny_action=denied_action if denial_source == "route" else None
    )
    activities, adapter, _lease_calls, payload = _publication_activity_case(dispatch=dispatch)
    canonical = activities._publication_test_canonical
    repository = activities._publication_test_repository
    canonical.remote_receipt_id = repository.receipt_id
    canonical.remote_id = "canonical-remote-1"
    canonical.remote_receipt_state = "accepted"
    canonical.approval_binding.remote_receipt_id = UUID(canonical.remote_receipt_id)
    payload.update(
        {
            "remote_receipt_id": canonical.remote_receipt_id,
            "remote_id": canonical.remote_id,
            "publication_state": "published",
            "publication_id": "canonical-publication-1",
        }
    )
    if denial_source == "current_authority":
        async def missing_current_authority(_request):
            return None, "canonical_production_authority_invalid"
        activities._load_current_production_authorization = missing_current_authority
    if action_name == "finalize":
        canonical.remote_receipt_state = "accepted"
        canonical.publication_id = "canonical-publication-1"
        canonical.publication_state = "published"

    async def run_action():
        if action_name == "reconcile":
            activities._publication_test_store.submit_decisions = [False]
            activities._publication_test_store.effect_state = "submitting"
            return await activities.submit_or_reconcile(payload)
        if action_name == "await":
            return await activities.await_publication(payload)
        if action_name == "cancel":
            return await activities.cancel(payload)
        return await activities.complete(payload)

    with pytest.raises(ApplicationError) as raised:
        asyncio.run(run_action())

    assert raised.value.type == "ProductionPublicationLiabilityHeld"
    assert raised.value.non_retryable is True
    assert len(dispatch.hold_calls) == 1
    assert dispatch.hold_calls[0][1] == denied_action
    assert adapter.reconcile_calls == 0
    assert adapter.status_calls == 0
    assert adapter.cancel_calls == 0
    assert activities._publication_test_store.completed_outputs == []
    assert activities._publication_test_store.terminal_states == []
    assert activities._publication_test_repository.status_events == []


def test_production_complete_requires_fresh_finalize_authorization():
    dispatch = _DenyingPublicationDispatch()
    activities, _adapter, _lease_calls, payload = _publication_activity_case(dispatch=dispatch)
    canonical = activities._publication_test_canonical
    canonical.remote_receipt_id = activities._publication_test_repository.receipt_id
    canonical.remote_id = "canonical-remote-1"
    canonical.remote_receipt_state = "accepted"
    canonical.publication_id = "canonical-publication-1"
    canonical.publication_state = "published"
    canonical.approval_binding.remote_receipt_id = UUID(canonical.remote_receipt_id)
    payload.update(
        {
            "remote_receipt_id": canonical.remote_receipt_id,
            "remote_id": canonical.remote_id,
            "publication_state": "published",
            "publication_id": canonical.publication_id,
        }
    )

    result = asyncio.run(activities.complete(payload))

    assert dispatch.action_calls[-1][2] == "finalize"
    assert len(activities._publication_test_store.completed_outputs) == 1
    assert activities._publication_test_cost_calls["settle"] == [
        (payload["budget_reservation_id"], 0)
    ]
    assert result.publication_state == "published"


def test_revoked_finalize_preserves_published_result_and_reserved_liability():
    dispatch = _HoldingPublicationDispatch(deny_action="finalize")
    activities, adapter, _lease_calls, payload = _publication_activity_case(dispatch=dispatch)
    canonical = activities._publication_test_canonical
    canonical.remote_receipt_id = activities._publication_test_repository.receipt_id
    canonical.remote_id = "canonical-remote-1"
    canonical.remote_receipt_state = "accepted"
    canonical.approval_binding.remote_receipt_id = UUID(canonical.remote_receipt_id)
    payload.update(
        {
            "remote_receipt_id": canonical.remote_receipt_id,
            "remote_id": canonical.remote_id,
            "publication_state": "accepted",
        }
    )
    adapter.status_state = "published"

    observed = asyncio.run(activities.await_publication(payload))
    canonical.remote_receipt_state = "accepted"
    canonical.publication_id = "canonical-publication-1"
    canonical.publication_state = "published"
    observed["publication_id"] = canonical.publication_id

    with pytest.raises(ApplicationError) as raised:
        asyncio.run(activities.complete(observed))

    assert raised.value.type == "ProductionPublicationLiabilityHeld"
    assert observed["publication_state"] == "published"
    assert activities._publication_test_cost_calls["settle"] == []
    assert activities._publication_test_store.completed_outputs == []
    assert activities._publication_test_store.terminal_states == []
    assert dispatch.hold_calls[-1][1] == "finalize"


def test_production_submit_claims_before_lease_resolution_and_provider_dispatch():
    events = []

    class Dispatch:
        def claim_permit(self, operation_id, permit_id):
            events.append("claim")
            return SimpleNamespace(dispatch_allowed=True)

        def authorize_action(self, operation_id, permit_id, action_kind):
            events.append("authorize:" + action_kind)
            return SimpleNamespace(authorized=True, reason_code=None)

    activities, adapter, _lease_calls, payload = _publication_activity_case(
        dispatch=Dispatch()
    )
    activities._state.credential_lease_factory = lambda _request: events.append("lease") or object()
    original_submit = adapter.submit

    async def submit(request, lease):
        events.append("submit")
        return await original_submit(request, lease)

    adapter.submit = submit

    result = asyncio.run(activities.submit_or_reconcile(payload))

    assert result["publication_state"] == "accepted"
    assert events == ["claim", "lease", "submit"]


def test_claimed_publication_route_revocation_holds_known_receipt_without_resubmit():
    dispatch = _HoldingPublicationDispatch(deny_action="await")
    activities, adapter, lease_calls, payload = _publication_activity_case(dispatch=dispatch)

    accepted = asyncio.run(activities.submit_or_reconcile(payload))
    canonical = activities._publication_test_canonical
    canonical.remote_receipt_id = accepted["remote_receipt_id"]
    canonical.remote_id = accepted["remote_id"]
    canonical.remote_receipt_state = accepted["publication_state"]
    accepted["publication_state"] = "accepted"

    with pytest.raises(ApplicationError) as raised:
        asyncio.run(activities.await_publication(accepted))

    assert raised.value.type == "ProductionPublicationLiabilityHeld"
    assert raised.value.non_retryable is True
    assert adapter.submit_calls == 1
    assert adapter.status_calls == 0
    assert adapter.reconcile_calls == 0
    assert len(lease_calls) == 1
    assert len(dispatch.hold_calls) == 1
    assert dispatch.hold_calls[0][1] == "await"
    assert activities._publication_test_store.terminal_states == []
    assert activities._publication_test_store.completed_outputs == []


def test_current_publication_authorization_projects_canonical_policy_and_approval():
    workspace_id, program_id, package_id, account_id = (uuid4() for _ in range(4))
    request_id, approval_id, policy_id, job_id = (uuid4() for _ in range(4))
    operation_ids = [uuid4() for _ in range(9)]
    now = datetime.now(timezone.utc)
    policy_expiry = now + timedelta(hours=2)
    approval_expiry = now + timedelta(hours=1)
    policy_document = {
        "schema_version": "salience.p1-production-policy.v1",
        "allowed_effects": ["write"],
        "approval_required_effects": ["write"],
        "expires_at": policy_expiry.isoformat(),
        "jurisdiction_refs": [],
        "publication_scope": {
            "platform": "production-platform",
            "destination": "channel:approved",
            "locale": "en-US",
            "territory": "US",
            "visibility": "public",
        },
    }
    policy_hash = sha256(
        json.dumps(policy_document, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    approval_context = {
        "schema_version": "salience.p1-effect-approval.v1",
        "workspace_id": str(workspace_id),
        "program_id": str(program_id),
        "goal_id": str(operation_ids[0]),
        "goal_revision": 1,
        "intent_id": str(operation_ids[1]),
        "cycle_id": str(operation_ids[2]),
        "context_id": str(operation_ids[3]),
        "operation_id": str(operation_ids[4]),
        "subject_id": str(operation_ids[5]),
        "policy_version_id": str(policy_id),
        "policy_document_hash": policy_hash,
        "effect_type": "write",
        "purpose": "approved-publication",
        "artifact_sha256": "a" * 64,
        "account_id": str(account_id),
        "provider_id": "production-publisher",
        "required_scopes": ["publish:create"],
        "budget_id": str(operation_ids[6]),
        "budget_reservation_id": str(operation_ids[7]),
        "expires_at": approval_expiry.isoformat(),
        "publication_request_id": str(request_id),
        "job_id": str(job_id),
        "workflow_id": "production-publication-workflow",
        "workflow_run_id": "production-publication-run",
        "publication_plan_id": str(operation_ids[8]),
        "publication_attempt_id": str(uuid4()),
        "external_effect_id": str(uuid4()),
    }
    row = (
        str(request_id),
        str(workspace_id),
        str(program_id),
        str(package_id),
        str(account_id),
        "production-platform",
        "channel:approved",
        "en-US",
        "US",
        "public",
        1,
        "production-publication-key",
        str(approval_id),
        "production-approval",
        "production-publisher",
        "approved",
        "approved",
        "approved",
        str(workspace_id),
        "organization",
        "active",
        str(account_id),
        "active",
        ["publish:create"],
        None,
        True,
        True,
        approval_context,
        "production-reviewer",
        now,
        str(policy_id),
        str(workspace_id),
        "active",
        policy_document,
        policy_hash,
        now - timedelta(minutes=1),
        now,
        True,
        "approved",
        str(job_id),
        "reserved",
        "write",
        str(workspace_id),
    )

    authorization = PublicationRepository._authorization_from_row(row, str(request_id))

    assert authorization.policy_id == str(policy_id)
    assert authorization.policy_document_hash == policy_hash
    assert authorization.policy_status == "active"
    assert authorization.production_policy is not None
    assert authorization.production_policy.expires_at == policy_expiry
    assert authorization.approval_binding is not None
    assert authorization.approval_binding.operation_id == operation_ids[4]
    assert authorization.approval_binding.publication_request_id == request_id


def test_production_authorize_issues_one_permit_from_current_publication_binding(monkeypatch):
    now = datetime.now(timezone.utc)
    request = PublicationRequest(
        id=str(uuid4()),
        workspace_id=str(uuid4()),
        content_program_id=str(uuid4()),
        ready_package_id=str(uuid4()),
        publisher_account_id=str(uuid4()),
        publication_approval_request_id=str(uuid4()),
        platform="production-platform",
        destination="channel:approved",
        locale="en-US",
        territory="US",
        visibility="public",
        capability_profile_version=1,
        idempotency_key="production-publication-authorize-test",
        approval_reference="production-approval",
        publisher_id="production-publisher",
    )
    job_id, plan_id, attempt_id, effect_id, permit_id = (uuid4() for _ in range(5))
    operation_id = uuid4()
    approval = SimpleNamespace(
        approval_request_id=UUID(request.publication_approval_request_id),
        workspace_id=UUID(request.workspace_id),
        goal_id=uuid4(),
        goal_revision=1,
        program_id=UUID(request.content_program_id),
        intent_id=uuid4(),
        cycle_id=uuid4(),
        context_id=uuid4(),
        operation_id=operation_id,
        subject_id=uuid4(),
        policy_version_id=uuid4(),
        policy_document_hash="a" * 64,
        effect_type="write",
        purpose="approved-publication",
        artifact_sha256="b" * 64,
        account_id=UUID(request.publisher_account_id),
        provider_id="production-publisher",
        required_scopes=("publish:create",),
        budget_id=uuid4(),
        budget_reservation_id=uuid4(),
        expires_at=now + timedelta(hours=1),
        publication_request_id=UUID(request.id),
        job_id=job_id,
        workflow_id="production-publication-workflow",
        workflow_run_id="production-publication-run",
        publication_plan_id=plan_id,
        publication_attempt_id=attempt_id,
        external_effect_id=effect_id,
        remote_receipt_id=None,
    )
    policy = SimpleNamespace(
        id=approval.policy_version_id,
        status="active",
        document_hash=approval.policy_document_hash,
        expires_at=now + timedelta(hours=2),
        allowed_effects=frozenset({EffectClass.WRITE}),
        approval_required_effects=frozenset({EffectClass.WRITE}),
        publication_scope=SimpleNamespace(
            platform=request.platform,
            destination=request.destination,
            locale=request.locale,
            territory=request.territory,
            visibility=request.visibility,
        ),
    )
    profile = SimpleNamespace(
        publisher_id="production-publisher", version="1", quota_state="available"
    )
    current = SimpleNamespace(
        request=request,
        ready_package_approval_state="approved",
        publication_approval_state="approved",
        disclosure_status="approved",
        account_workspace_id=request.workspace_id,
        account_type="organization",
        account_status="active",
        connection_account_id=request.publisher_account_id,
        connection_status="active",
        connection_scopes=frozenset({"publish:create"}),
        profile=profile,
        policy_allowed=True,
        rights_allowed=True,
        policy_id=str(policy.id),
        policy_document_hash=policy.document_hash,
        policy_status="active",
        production_policy=policy,
        approval_binding=approval,
        database_time=now,
        budget_status="reserved",
        publication_plan_id=str(plan_id),
        publication_plan_state="planned",
        external_effect_id=str(effect_id),
        external_effect_state="planned",
        publication_attempt_id=str(attempt_id),
        publication_attempt_state="planned",
        remote_receipt_id=None,
        remote_id=None,
        remote_receipt_state=None,
        publication_id=None,
        job_id=str(job_id),
        job_state="queued",
        budget_reservation_id=str(approval.budget_reservation_id),
    )

    class Store(_PublicationActivityStore):
        async def plan_effect(self, run, effect_key):
            return None

        async def effect(self, run, effect_key):
            return SimpleNamespace(effect_id=str(effect_id))

    class Repository:
        async def create_plan(self, **kwargs):
            return SimpleNamespace(id=str(plan_id))

        async def attach_reservation(self, **kwargs):
            return None

        async def load_current_authorization(self, publication_request_id):
            return current

        async def create_attempt(self, **kwargs):
            return SimpleNamespace(id=str(attempt_id))

    class Costs:
        async def reserve_for_publication_effect(self, **kwargs):
            return SimpleNamespace(
                reservation_id=str(approval.budget_reservation_id),
                status=SimpleNamespace(value="reserved"),
            )

    class Authorizer:
        def __init__(self):
            self.contexts = []

        async def reauthorize(self, context):
            self.contexts.append(context)
            return SimpleNamespace(allowed=True, reasons=())

    class Dispatch:
        def __init__(self):
            self.issue_calls = []

        def issue_permit(self, permit_request):
            self.issue_calls.append(permit_request)
            return SimpleNamespace(permit_id=permit_id, operation_id=operation_id)

    route = RouteBinding(
        deployment_id=uuid4(),
        environment_id=uuid4(),
        route_id=uuid4(),
        slot_id=uuid4(),
        owner_id=uuid4(),
        fencing_epoch=1,
        route_manifest_sha256="c" * 64,
        artifact_lock_sha256="d" * 64,
        route_tuple_sha256="e" * 64,
    )

    def make_permit(approved_binding, publication_binding):
        return ProductionPermitRequest(
            workspace_id=approved_binding.workspace_id,
            goal_id=approved_binding.goal_id,
            goal_revision=approved_binding.goal_revision,
            program_id=approved_binding.program_id,
            intent_id=approved_binding.intent_id,
            cycle_id=approved_binding.cycle_id,
            context_id=approved_binding.context_id,
            operation_id=approved_binding.operation_id,
            subject_id=approved_binding.subject_id,
            policy_version_id=approved_binding.policy_version_id,
            approval_request_id=approved_binding.approval_request_id,
            required_scopes=approved_binding.required_scopes,
            effect_type=approved_binding.effect_type,
            purpose=approved_binding.purpose,
            artifact_sha256=approved_binding.artifact_sha256,
            account_id=approved_binding.account_id,
            budget_id=approved_binding.budget_id,
            budget_reservation_id=approved_binding.budget_reservation_id,
            provider_id=approved_binding.provider_id,
            idempotency_key=request.idempotency_key,
            route=route,
            execution_deadline=now + timedelta(minutes=10),
            ttl_seconds=30,
            publication_binding=publication_binding,
        )

    dispatch = Dispatch()
    authorizer = Authorizer()
    state = PublicationWorkflowState(
        store=Store(),
        repository=Repository(),
        publisher_adapters={"production-publisher": _PublicationActivityAdapter()},
        cost_repository=Costs(),
        authorizer=authorizer,
        production_dispatch=dispatch,
        production_permit_request_factory=make_permit,
        policy_allowed=False,
    )
    activities = PublicationActivities(state)
    run = SimpleNamespace(
        job_id=job_id,
        trace_context=SimpleNamespace(trace_id="e" * 32, span_id="f" * 16),
    )

    async def current_run():
        return run

    async def checkpoint(_name):
        return None

    async def current_production_authorization(_request):
        return current, None

    activities._run = current_run
    activities._checkpoint = checkpoint
    activities._load_current_production_authorization = current_production_authorization
    monkeypatch.setattr(
        "salience.workflows.publication.activity.info",
        lambda: SimpleNamespace(
            workflow_id=approval.workflow_id,
            workflow_run_id=approval.workflow_run_id,
        ),
    )
    payload = {
        "workflow_request": {
            "workspace_id": request.workspace_id,
            "content_program_id": request.content_program_id,
            "ready_package_id": request.ready_package_id,
            "publisher_account_id": request.publisher_account_id,
            "publication_approval_request_id": request.publication_approval_request_id,
            "budget_id": str(approval.budget_id),
            "idempotency_key": request.idempotency_key,
            "platform": request.platform,
            "destination": request.destination,
            "locale": request.locale,
            "territory": request.territory,
            "visibility": request.visibility,
            "capability_profile_version": 1,
        },
        "request": request.model_dump(mode="json"),
    }

    result = asyncio.run(activities.authorize(payload))

    assert result["authorized"] is True
    assert result["production_operation_id"] == str(operation_id)
    assert result["production_permit_id"] == str(permit_id)
    assert len(dispatch.issue_calls) == 1
    assert authorizer.contexts[-1].policy_allowed is True


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


def test_issued_permit_can_authorize_delivery_before_claim_and_records_event(accounting):
    case = _production_case(accounting)
    issued = case["dispatch"].issue_permit(case["request"])

    decision = case["dispatch"].authorize_action(
        case["operation_id"], issued.permit_id, "delivery"
    )

    assert decision.authorized is True
    assert [call.action_kind for call in case["route"].calls] == ["issue", "delivery"]
    assert _claim_count(case["database"], case["operation_id"]) == 0
    with psycopg.connect(case["database"]) as connection:
        event = connection.execute(
            "SELECT payload FROM v4_cycle_events WHERE cycle_id=%s "
            "AND kind='production_action_authorized' AND payload->>'action'='delivery'",
            (case["cycle_id"],),
        ).fetchone()
    assert event is not None
    assert event[0]["claim_id"] is None
    assert event[0]["decision_id"] == str(decision.decision_id)


@pytest.mark.parametrize(
    "action_kind", ("submit", "reconcile", "await", "status", "cancel", "receipt", "finalize")
)
def test_provider_facing_actions_require_claim_before_route_authorization(accounting, action_kind):
    case = _production_case(accounting)
    issued = case["dispatch"].issue_permit(case["request"])

    decision = case["dispatch"].authorize_action(
        case["operation_id"], issued.permit_id, action_kind
    )

    assert decision.authorized is False
    assert decision.reason_code == "dispatch_already_claimed"
    assert [call.action_kind for call in case["route"].calls] == ["issue"]
    assert _claim_count(case["database"], case["operation_id"]) == 0


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
