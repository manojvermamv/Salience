"""Exact enrollment-bound recovery work repository."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from hashlib import sha256
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from salience.cycles._production_recovery_contracts import (
    ProductionAlertRequest, ProductionAlertRetryPolicy, ProductionRecipientProfile,
    ProductionRecoveryEvent, ProductionRecoveryPolicyBinding, ProductionRecoveryWork,
    RecoveryEventConflict, RecoveryIdentityConflict, RecoveryInventoryGap,
    _EVENT_KINDS, _require_aware, _require_uuid, _scalar_payload,
)


class ProductionRecoveryWorkRepository:
    """Exact enrollment-bound source binding and append-only evidence access."""

    def __init__(self, database_url: str) -> None:
        if not isinstance(database_url, str) or not database_url.strip():
            raise ValueError("recovery database URL required")
        self.database_url = database_url

    def _connect(self):
        return psycopg.connect(self.database_url, row_factory=dict_row, connect_timeout=5)

    @staticmethod
    def _policy_evidence(policy: ProductionRecoveryPolicyBinding) -> str:
        material = ":".join(
            (
                str(policy.recipient_profile_id),
                str(policy.recipient_profile_revision),
                policy.recipient_profile_sha256,
                str(policy.retry_policy_id),
                str(policy.retry_policy_revision),
                policy.retry_policy_sha256,
            )
        )
        return sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def _work(row: Mapping[str, object]) -> ProductionRecoveryWork:
        return ProductionRecoveryWork(
            work_id=row["work_id"],
            source_discriminant=row["source_discriminant"],
            source_id=row["source_id"],
            workspace_id=row["workspace_id"],
            goal_id=row["goal_id"],
            intent_id=row["intent_id"],
            cycle_id=row["cycle_id"],
            context_id=row["context_id"],
            operation_id=row["operation_id"],
            source_revision=row["source_revision"],
            case_revision=row["case_revision"],
            eligibility_revision=row["eligibility_revision"],
            owner_id=row["owner_id"],
            target_alias=row["target_alias"],
            remote_account_id=row["remote_account_id"],
            remote_resource_id=row["remote_resource_id"],
            reason=row["reason"],
            state=row["state"],
            request_fingerprint=row["request_fingerprint"],
            source_fingerprint=row["source_fingerprint"],
            snapshot_version=row["snapshot_version"],
            snapshot=row["snapshot"],
            snapshot_sha256=row["snapshot_sha256"],
            deadline=row["deadline"],
            due_at=row["due_at"],
            expires_at=row["expires_at"],
            deadline_revision=row["deadline_revision"],
            recipient_profile_revision=row["recipient_profile_revision"],
        )

    def get_work(self, work_id: UUID) -> ProductionRecoveryWork:
        _require_uuid(work_id, "work ID")
        with self._connect() as connection:
            row = connection.execute(
                """SELECT work.*
                   FROM v4_production_recovery_work work
                   JOIN v4_production_intent_enrollments enrollment
                     ON enrollment.intent_id=work.intent_id
                    AND enrollment.eligibility_revision=work.eligibility_revision
                    AND enrollment.workspace_id=work.workspace_id
                    AND enrollment.goal_id=work.goal_id
                   WHERE work.work_id=%s""",
                (work_id,),
            ).fetchone()
            if row is None:
                raise RecoveryInventoryGap("production recovery work is missing its exact enrollment")
            return self._work(row)

    @staticmethod
    def _check_existing(
        connection,
        source_discriminant: str,
        source_id: UUID,
        source_revision: int,
        expected: Mapping[str, object],
        policy: ProductionRecoveryPolicyBinding,
    ) -> ProductionRecoveryWork | None:
        existing = connection.execute(
            """SELECT * FROM v4_production_recovery_work
               WHERE source_discriminant=%s AND source_id=%s AND source_revision=%s
               FOR UPDATE""",
            (source_discriminant, source_id, source_revision),
        ).fetchone()
        if existing is None:
            return None
        for name, value in expected.items():
            if existing[name] != value:
                raise RecoveryIdentityConflict(
                    f"existing production recovery source conflicts at {name}"
                )
        if existing["recipient_profile_revision"] != policy.recipient_profile_revision:
            raise RecoveryIdentityConflict("existing recovery recipient profile revision conflicts")
        return ProductionRecoveryWorkRepository._work(existing)

    def _append_event(
        self,
        connection,
        work: ProductionRecoveryWork,
        *,
        event_key: str,
        event_kind: str,
        policy_decision_id: UUID,
        payload: Mapping[str, object],
        actor_id: UUID | None = None,
    ) -> ProductionRecoveryEvent:
        if not isinstance(event_key, str) or not event_key or len(event_key) > 256:
            raise ValueError("bounded stable recovery event key required")
        if event_kind not in _EVENT_KINDS:
            raise ValueError("unsupported production recovery event kind")
        _require_uuid(policy_decision_id, "policy decision ID")
        if actor_id is not None:
            _require_uuid(actor_id, "event actor ID")
        safe_payload = _scalar_payload(payload)
        enrollment = connection.execute(
            """SELECT route_id,slot_id,fencing_epoch
               FROM v4_production_intent_enrollments
               WHERE intent_id=%s AND eligibility_revision=%s
                 AND workspace_id=%s AND goal_id=%s""",
            (
                work.intent_id,
                work.eligibility_revision,
                work.workspace_id,
                work.goal_id,
            ),
        ).fetchone()
        if enrollment is None:
            raise RecoveryInventoryGap("event work has no exact immutable enrollment")
        prior = connection.execute(
            """SELECT * FROM v4_production_recovery_events
               WHERE event_key=%s FOR UPDATE""",
            (event_key,),
        ).fetchone()
        if prior is not None:
            if (
                prior["work_id"] != work.work_id
                or prior["event_kind"] != event_kind
                or prior["payload"] != safe_payload
                or prior["policy_decision_id"] != policy_decision_id
                or prior["actor_id"] != actor_id
            ):
                raise RecoveryEventConflict("recovery event key was reused with different evidence")
            return self._event(prior)
        encoded = Jsonb(safe_payload)
        inserted = connection.execute(
            """INSERT INTO v4_production_recovery_events(
                      event_id,work_id,event_key,event_kind,actor_id,route_id,slot_id,
                      fencing_epoch,policy_decision_id,payload,payload_sha256)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                      encode(public.digest(%s::jsonb::text,'sha256'),'hex'))
               RETURNING *""",
            (
                uuid4(),
                work.work_id,
                event_key,
                event_kind,
                actor_id,
                enrollment["route_id"],
                enrollment["slot_id"],
                enrollment["fencing_epoch"],
                policy_decision_id,
                encoded,
                encoded,
            ),
        ).fetchone()
        return self._event(inserted)

    @staticmethod
    def _event(row: Mapping[str, object]) -> ProductionRecoveryEvent:
        return ProductionRecoveryEvent(
            event_id=row["event_id"],
            work_id=row["work_id"],
            event_key=row["event_key"],
            event_kind=row["event_kind"],
            payload=row["payload"],
            payload_sha256=row["payload_sha256"],
            occurred_at=row["occurred_at"],
        )

    def bind_cycle_case(
        self, case_id: UUID, *, policy: ProductionRecoveryPolicyBinding
    ) -> ProductionRecoveryWork:
        _require_uuid(case_id, "case ID")
        if not isinstance(policy, ProductionRecoveryPolicyBinding):
            raise ValueError("explicit recovery policy binding required")
        with self._connect() as connection:
            with connection.transaction():
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                    (f"cycle_case:{case_id}",),
                )
                facts = connection.execute(
                    """SELECT recovery.id AS case_id,recovery.workspace_id,
                              recovery.goal_id,recovery.state,recovery.revision,
                              recovery.owner_id,recovery.failure_class AS reason,
                              recovery.deadline,intent.id AS intent_id,
                              intent.eligibility_revision,cycle.id AS cycle_id,
                              cycle.context_id,cycle.operation_id,dispatch.generation,
                              permit.provider_id AS target_alias,
                              permit.account_id AS remote_account_id,
                              encode(public.digest(permit.request_snapshot::text,'sha256'),'hex')
                                  AS request_fingerprint,
                              enrollment.route_fence_decision_id,
                              (SELECT receipt.remote_id
                               FROM remote_publication_receipts receipt
                               WHERE receipt.publication_attempt_id = (
                                 SELECT attempt.id
                                 FROM publication_attempts attempt
                                 WHERE attempt.id = CASE
                                   WHEN permit.request_snapshot->>'publication_attempt_id'
                                        ~* '^[0-9a-f-]{36}$'
                                   THEN (permit.request_snapshot->>'publication_attempt_id')::uuid
                                   ELSE NULL END
                                 ORDER BY attempt.created_at DESC LIMIT 1)
                               ORDER BY receipt.created_at DESC,receipt.id DESC LIMIT 1)
                                  AS remote_resource_id
                       FROM v4_recovery_cases recovery
                       JOIN v4_cycles cycle ON cycle.id=recovery.target_cycle_id
                       JOIN v4_cycle_intents intent ON intent.id=cycle.intent_id
                       JOIN v4_production_intent_enrollments enrollment
                         ON enrollment.intent_id=intent.id
                        AND enrollment.eligibility_revision=intent.eligibility_revision
                        AND enrollment.workspace_id=recovery.workspace_id
                        AND enrollment.goal_id=recovery.goal_id
                       JOIN v4_production_current_dispatch dispatch
                         ON dispatch.operation_id=cycle.operation_id
                       JOIN v4_production_effect_permits permit
                         ON permit.permit_id=dispatch.current_permit_id
                        AND permit.operation_id=dispatch.operation_id
                        AND permit.generation=dispatch.generation
                        AND permit.intent_id=intent.id
                        AND permit.cycle_id=cycle.id
                        AND permit.context_id=cycle.context_id
                       WHERE recovery.id=%s AND recovery.target_intent_id IS NULL
                         AND recovery.target_cycle_id=cycle.id
                         AND cycle.state<>'closed'
                         AND recovery.state IN (
                           'retry_due','reconciling','rework_due','awaiting_review','suspended')
                       FOR UPDATE OF recovery,cycle""",
                    (case_id,),
                ).fetchone()
                if facts is None:
                    raise RecoveryInventoryGap(
                        "cycle case is not an exact current source joined to a production enrollment"
                    )
                expected = {
                    "workspace_id": facts["workspace_id"],
                    "goal_id": facts["goal_id"],
                    "intent_id": facts["intent_id"],
                    "cycle_id": facts["cycle_id"],
                    "context_id": facts["context_id"],
                    "operation_id": facts["operation_id"],
                    "source_revision": facts["revision"],
                    "case_revision": facts["revision"],
                    "eligibility_revision": facts["eligibility_revision"],
                    "owner_id": facts["owner_id"],
                    "target_alias": facts["target_alias"],
                    "remote_account_id": facts["remote_account_id"],
                    "remote_resource_id": facts["remote_resource_id"],
                    "reason": facts["reason"],
                    "state": facts["state"],
                    "request_fingerprint": facts["request_fingerprint"],
                    "deadline": facts["deadline"],
                    "due_at": None,
                    "expires_at": None,
                }
                existing = self._check_existing(
                    connection, "cycle_case", case_id, facts["revision"], expected, policy
                )
                if existing is not None:
                    return existing
                work = self._insert_work(
                    connection,
                    source_discriminant="cycle_case",
                    source_id=case_id,
                    source_revision=facts["revision"],
                    case_revision=facts["revision"],
                    eligibility_revision=facts["eligibility_revision"],
                    cycle_id=facts["cycle_id"],
                    context_id=facts["context_id"],
                    operation_id=facts["operation_id"],
                    workspace_id=facts["workspace_id"],
                    goal_id=facts["goal_id"],
                    intent_id=facts["intent_id"],
                    owner_id=facts["owner_id"],
                    target_alias=facts["target_alias"],
                    remote_account_id=facts["remote_account_id"],
                    remote_resource_id=facts["remote_resource_id"],
                    reason=facts["reason"],
                    state=facts["state"],
                    request_fingerprint=facts["request_fingerprint"],
                    snapshot_version=None,
                    snapshot=None,
                    snapshot_sha256=None,
                    deadline=facts["deadline"],
                    due_at=None,
                    expires_at=None,
                    deadline_revision=facts["revision"],
                    policy=policy,
                )
                return work

    def bind_pre_admission_intent(
        self, intent_id: UUID, *, policy: ProductionRecoveryPolicyBinding
    ) -> ProductionRecoveryWork:
        _require_uuid(intent_id, "intent ID")
        if not isinstance(policy, ProductionRecoveryPolicyBinding):
            raise ValueError("explicit recovery policy binding required")
        with self._connect() as connection:
            with connection.transaction():
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                    (f"pre_admission_intent_case:{intent_id}",),
                )
                facts = connection.execute(
                    """SELECT intent.id AS intent_id,goal.workspace_id,intent.goal_id,
                              intent.eligibility_revision,intent.fingerprint,
                              intent.due_at,intent.expires_at,
                              admission.disposition,enrollment.source_owner_id
                       FROM v4_cycle_intents intent
                       JOIN v4_goals goal ON goal.id=intent.goal_id
                       JOIN v4_admissions admission
                         ON admission.intent_id=intent.id
                        AND admission.eligibility_revision=intent.eligibility_revision
                        AND admission.cycle_id IS NULL
                       JOIN v4_production_intent_enrollments enrollment
                         ON enrollment.intent_id=intent.id
                        AND enrollment.eligibility_revision=intent.eligibility_revision
                        AND enrollment.workspace_id=goal.workspace_id
                        AND enrollment.goal_id=intent.goal_id
                       WHERE intent.id=%s
                         AND admission.disposition IN ('deferred','review_required')
                         AND NOT EXISTS (
                           SELECT 1 FROM v4_cycles cycle WHERE cycle.intent_id=intent.id)
                       FOR UPDATE OF intent""",
                    (intent_id,),
                ).fetchone()
                if facts is None:
                    raise RecoveryInventoryGap(
                        "pre-admission intent is missing its exact current Task 6 enrollment"
                    )
                reason = {
                    "deferred": "admission_deferred",
                    "review_required": "admission_review_required",
                }[facts["disposition"]]
                source_revision = facts["eligibility_revision"]
                expected = {
                    "workspace_id": facts["workspace_id"],
                    "goal_id": facts["goal_id"],
                    "intent_id": facts["intent_id"],
                    "cycle_id": None,
                    "context_id": None,
                    "operation_id": None,
                    "source_revision": source_revision,
                    "case_revision": None,
                    "eligibility_revision": source_revision,
                    "owner_id": facts["source_owner_id"],
                    "target_alias": None,
                    "remote_account_id": None,
                    "remote_resource_id": None,
                    "reason": reason,
                    "state": "unadmitted",
                    "request_fingerprint": facts["fingerprint"],
                    "deadline": facts["expires_at"],
                    "due_at": facts["due_at"],
                    "expires_at": facts["expires_at"],
                }
                existing = self._check_existing(
                    connection,
                    "pre_admission_intent_case",
                    intent_id,
                    source_revision,
                    expected,
                    policy,
                )
                if existing is not None:
                    return existing
                return self._insert_work(
                    connection,
                    source_discriminant="pre_admission_intent_case",
                    source_id=intent_id,
                    source_revision=source_revision,
                    case_revision=None,
                    eligibility_revision=source_revision,
                    cycle_id=None,
                    context_id=None,
                    operation_id=None,
                    workspace_id=facts["workspace_id"],
                    goal_id=facts["goal_id"],
                    intent_id=intent_id,
                    owner_id=facts["source_owner_id"],
                    target_alias=None,
                    remote_account_id=None,
                    remote_resource_id=None,
                    reason=reason,
                    state="unadmitted",
                    request_fingerprint=facts["fingerprint"],
                    snapshot_version=None,
                    snapshot=None,
                    snapshot_sha256=None,
                    deadline=facts["expires_at"],
                    due_at=facts["due_at"],
                    expires_at=facts["expires_at"],
                    deadline_revision=source_revision,
                    policy=policy,
                )

    def bind_production_publication_hold(
        self,
        operation_id: UUID,
        *,
        reason: str,
        deadline: datetime,
        policy: ProductionRecoveryPolicyBinding,
    ) -> ProductionRecoveryWork:
        _require_uuid(operation_id, "operation ID")
        if reason not in {"expired_permit_receipt", "published_during_cancel"}:
            raise ValueError("supported production publication hold reason required")
        deadline = _require_aware(deadline, "target-approved hold deadline")
        if not isinstance(policy, ProductionRecoveryPolicyBinding):
            raise ValueError("explicit recovery policy binding required")
        with self._connect() as connection:
            with connection.transaction():
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                    (f"production_publication_hold:{operation_id}",),
                )
                facts = connection.execute(
                    """SELECT dispatch.operation_id,dispatch.generation,
                              cycle.id AS cycle_id,cycle.context_id,cycle.state AS cycle_state,
                              cycle.disposition AS cycle_disposition,intent.id AS intent_id,
                              intent.eligibility_revision,permit.provider_id AS target_alias,
                              permit.account_id AS remote_account_id,
                              permit.subject_id AS owner_id,
                              permit.expires_at AS permit_expires_at,
                              permit.request_snapshot,
                              encode(public.digest(permit.request_snapshot::text,'sha256'),'hex')
                                AS request_fingerprint,
                              enrollment.workspace_id,enrollment.goal_id,
                              encode(public.digest(snapshot::text,'sha256'),'hex')
                                AS snapshot_sha256,snapshot AS liability_snapshot,
                              (SELECT receipt.remote_id
                               FROM remote_publication_receipts receipt
                               JOIN publication_attempts attempt
                                 ON attempt.id=receipt.publication_attempt_id
                               WHERE attempt.id = CASE
                                 WHEN permit.request_snapshot->>'publication_attempt_id'
                                      ~* '^[0-9a-f-]{36}$'
                                 THEN (permit.request_snapshot->>'publication_attempt_id')::uuid
                                 ELSE NULL END
                               ORDER BY receipt.created_at DESC,receipt.id DESC LIMIT 1)
                                AS remote_resource_id
                       FROM v4_production_current_dispatch dispatch
                       JOIN v4_production_effect_permits permit
                         ON permit.permit_id=dispatch.current_permit_id
                        AND permit.operation_id=dispatch.operation_id
                        AND permit.generation=dispatch.generation
                       JOIN v4_production_effect_permit_claims claim
                         ON claim.claim_id=dispatch.current_claim_id
                        AND claim.permit_id=permit.permit_id
                        AND claim.operation_id=dispatch.operation_id
                        AND claim.generation=dispatch.generation
                       JOIN v4_cycles cycle ON cycle.operation_id=dispatch.operation_id
                       JOIN v4_cycle_intents intent ON intent.id=permit.intent_id
                       JOIN v4_production_intent_enrollments enrollment
                         ON enrollment.intent_id=intent.id
                        AND enrollment.eligibility_revision=intent.eligibility_revision
                        AND enrollment.workspace_id=permit.workspace_id
                        AND enrollment.goal_id=permit.goal_id
                       CROSS JOIN LATERAL (
                         SELECT v4_production_recovery_publication_snapshot_v1(
                           dispatch.operation_id) AS snapshot) liability
                       WHERE dispatch.operation_id=%s
                       FOR UPDATE OF dispatch,cycle""",
                    (operation_id,),
                ).fetchone()
                if facts is None:
                    raise RecoveryInventoryGap(
                        "production hold requires exact current dispatch and enrollment"
                    )
                if reason == "expired_permit_receipt" and (
                    facts["cycle_state"] == "closed"
                    or facts["permit_expires_at"] > connection.execute(
                        "SELECT clock_timestamp() AS now"
                    ).fetchone()["now"]
                ):
                    raise ValueError("expired permit receipt hold requires an expired permit and open cycle")
                if reason == "published_during_cancel" and not (
                    facts["cycle_state"] == "closed"
                    and facts["cycle_disposition"] == "cancelled"
                ):
                    raise ValueError("cancel-race hold requires a closed cancelled cycle")
                source_revision = facts["generation"]
                expected = {
                    "workspace_id": facts["workspace_id"],
                    "goal_id": facts["goal_id"],
                    "intent_id": facts["intent_id"],
                    "cycle_id": facts["cycle_id"],
                    "context_id": facts["context_id"],
                    "operation_id": operation_id,
                    "source_revision": source_revision,
                    "case_revision": None,
                    "eligibility_revision": facts["eligibility_revision"],
                    "owner_id": facts["owner_id"],
                    "target_alias": facts["target_alias"],
                    "remote_account_id": facts["remote_account_id"],
                    "remote_resource_id": facts["remote_resource_id"],
                    "reason": reason,
                    "state": "held",
                    "request_fingerprint": facts["request_fingerprint"],
                    "snapshot_version": "salience.p1-production-publication-liability.v1",
                    "snapshot": facts["liability_snapshot"],
                    "snapshot_sha256": facts["snapshot_sha256"],
                    "deadline": deadline,
                    "due_at": None,
                    "expires_at": None,
                }
                existing = self._check_existing(
                    connection,
                    "production_publication_hold",
                    operation_id,
                    source_revision,
                    expected,
                    policy,
                )
                if existing is not None:
                    return existing
                return self._insert_work(
                    connection,
                    source_discriminant="production_publication_hold",
                    source_id=operation_id,
                    source_revision=source_revision,
                    case_revision=None,
                    eligibility_revision=facts["eligibility_revision"],
                    cycle_id=facts["cycle_id"],
                    context_id=facts["context_id"],
                    operation_id=operation_id,
                    workspace_id=facts["workspace_id"],
                    goal_id=facts["goal_id"],
                    intent_id=facts["intent_id"],
                    owner_id=facts["owner_id"],
                    target_alias=facts["target_alias"],
                    remote_account_id=facts["remote_account_id"],
                    remote_resource_id=facts["remote_resource_id"],
                    reason=reason,
                    state="held",
                    request_fingerprint=facts["request_fingerprint"],
                    snapshot_version="salience.p1-production-publication-liability.v1",
                    snapshot=facts["liability_snapshot"],
                    snapshot_sha256=facts["snapshot_sha256"],
                    deadline=deadline,
                    due_at=None,
                    expires_at=None,
                    deadline_revision=source_revision,
                    policy=policy,
                )

    def _insert_work(
        self,
        connection,
        *,
        source_discriminant: str,
        source_id: UUID,
        source_revision: int,
        case_revision: int | None,
        eligibility_revision: int,
        cycle_id: UUID | None,
        context_id: UUID | None,
        operation_id: UUID | None,
        workspace_id: UUID,
        goal_id: UUID,
        intent_id: UUID,
        owner_id: UUID,
        target_alias: str | None,
        remote_account_id: str | None,
        remote_resource_id: str | None,
        reason: str,
        state: str,
        request_fingerprint: str,
        snapshot_version: str | None,
        snapshot: Mapping[str, object] | None,
        snapshot_sha256: str | None,
        deadline: datetime,
        due_at: datetime | None,
        expires_at: datetime | None,
        deadline_revision: int,
        policy: ProductionRecoveryPolicyBinding,
    ) -> ProductionRecoveryWork:
        work_id = uuid4()
        row = connection.execute(
            """INSERT INTO v4_production_recovery_work(
                      work_id,source_discriminant,source_id,workspace_id,goal_id,intent_id,
                      cycle_id,context_id,operation_id,source_revision,case_revision,
                      eligibility_revision,owner_id,target_alias,remote_account_id,
                      remote_resource_id,reason,state,request_fingerprint,source_fingerprint,
                      snapshot_version,snapshot,snapshot_sha256,deadline,due_at,expires_at,
                      deadline_revision,recipient_profile_revision)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                      repeat('0',64),%s,%s,%s,%s,%s,%s,%s,%s)
               RETURNING *""",
            (
                work_id,
                source_discriminant,
                source_id,
                workspace_id,
                goal_id,
                intent_id,
                cycle_id,
                context_id,
                operation_id,
                source_revision,
                case_revision,
                eligibility_revision,
                owner_id,
                target_alias,
                remote_account_id,
                remote_resource_id,
                reason,
                state,
                request_fingerprint,
                snapshot_version,
                Jsonb(snapshot) if snapshot is not None else None,
                snapshot_sha256,
                deadline,
                due_at,
                expires_at,
                deadline_revision,
                policy.recipient_profile_revision,
            ),
        ).fetchone()
        work = self._work(row)
        payload = {
            "schema_version": "salience.p1-production-recovery-alert.v1",
            "status": "pending",
            "reason": work.reason,
            "state": work.state,
            "source_id": str(work.source_id),
            "work_id": str(work.work_id),
            "intent_id": str(work.intent_id),
            "deadline": work.deadline.isoformat(),
            "recipient_profile_revision": policy.recipient_profile_revision,
            "policy_revision": policy.retry_policy_revision,
            "evidence_sha256": self._policy_evidence(policy),
        }
        if work.operation_id is not None:
            payload["operation_id"] = str(work.operation_id)
        if work.case_id is not None:
            payload["case_id"] = str(work.case_id)
        if work.target_alias is not None:
            payload["target_alias"] = work.target_alias
        self._append_event(
            connection,
            work,
            event_key=f"alert-intent:{work.work_id}:initial",
            event_kind="alert_intent",
            policy_decision_id=policy.policy_decision_id,
            payload=payload,
        )
        return work

    def _load_event(self, connection, event_id: UUID) -> ProductionRecoveryEvent | None:
        row = connection.execute(
            "SELECT * FROM v4_production_recovery_events WHERE event_id=%s",
            (event_id,),
        ).fetchone()
        return self._event(row) if row else None

    def alert_request(
        self,
        work_id: UUID,
        *,
        severity: str,
        escalation_level: int,
        recipient_profile: ProductionRecipientProfile,
        retry_policy: ProductionAlertRetryPolicy,
        policy_decision_id: UUID,
    ) -> ProductionAlertRequest:
        work_id = _require_uuid(work_id, "work ID")
        if not isinstance(recipient_profile, ProductionRecipientProfile):
            raise ValueError("explicit versioned recipient profile required")
        if not isinstance(retry_policy, ProductionAlertRetryPolicy):
            raise ValueError("explicit versioned retry policy required")
        _require_uuid(policy_decision_id, "current alert policy decision ID")
        with self._connect() as connection:
            with connection.transaction():
                row = connection.execute(
                    """SELECT * FROM v4_production_recovery_work
                       WHERE work_id=%s FOR UPDATE""",
                    (work_id,),
                ).fetchone()
                if row is None:
                    raise RecoveryInventoryGap("alert work item does not exist")
                work = self._work(row)
                if recipient_profile.revision != work.recipient_profile_revision:
                    raise RecoveryIdentityConflict("recipient profile revision differs from work binding")
                if recipient_profile.owner_subject_id != work.owner_id:
                    raise RecoveryIdentityConflict("alert owner differs from exact recovery owner")
                initial = connection.execute(
                    """SELECT * FROM v4_production_recovery_events
                       WHERE work_id=%s AND event_kind='alert_intent'
                         AND event_key=%s""",
                    (work_id, f"alert-intent:{work_id}:initial"),
                ).fetchone()
                if initial is None:
                    raise RecoveryInventoryGap("initial alert intent is missing")
                binding_sha = self._policy_evidence_from_profile(
                    recipient_profile, retry_policy
                )
                if (
                    initial["payload"].get("recipient_profile_revision") != recipient_profile.revision
                    or initial["payload"].get("policy_revision") != retry_policy.revision
                    or initial["payload"].get("evidence_sha256") != binding_sha
                ):
                    raise RecoveryIdentityConflict("recipient or retry policy digest differs from work binding")
                if escalation_level > len(retry_policy.escalation_after):
                    raise ValueError("escalation level exceeds the explicit retry policy")
                if escalation_level == 0:
                    alert_event = self._event(initial)
                else:
                    payload = {
                        "schema_version": "salience.p1-production-recovery-alert.v1",
                        "work_id": str(work.work_id),
                        "source_id": str(work.source_id),
                        "intent_id": str(work.intent_id),
                        "reason": work.reason,
                        "state": work.state,
                        "deadline": work.deadline.isoformat(),
                        "severity": severity,
                        "escalation_level": escalation_level,
                        "recipient_profile_revision": recipient_profile.revision,
                        "policy_revision": retry_policy.revision,
                        "evidence_sha256": binding_sha,
                    }
                    if work.operation_id is not None:
                        payload["operation_id"] = str(work.operation_id)
                    if work.case_id is not None:
                        payload["case_id"] = str(work.case_id)
                    if work.target_alias is not None:
                        payload["target_alias"] = work.target_alias
                    alert_event = self._append_event(
                        connection,
                        work,
                        event_key=f"alert-intent:{work.work_id}:{work.source_revision}:{escalation_level}",
                        event_kind="alert_intent",
                        policy_decision_id=policy_decision_id,
                        payload=payload,
                    )
                return ProductionAlertRequest(
                    alert_event_id=alert_event.event_id,
                    work_id=work.work_id,
                    workspace_id=work.workspace_id,
                    source_id=work.source_id,
                    operation_id=work.operation_id,
                    case_id=work.case_id,
                    reason=work.reason,
                    state=work.state,
                    deadline=work.deadline,
                    target_alias=work.target_alias,
                    severity=severity,
                    escalation_level=escalation_level,
                    recipient_tier="owner",
                    recipient_profile=recipient_profile,
                    retry_policy=retry_policy,
                    policy_decision_id=policy_decision_id,
                )

    @staticmethod
    def _policy_evidence_from_profile(
        profile: ProductionRecipientProfile, retry: ProductionAlertRetryPolicy
    ) -> str:
        material = ":".join(
            (
                str(profile.profile_id),
                str(profile.revision),
                profile.profile_sha256,
                str(retry.policy_id),
                str(retry.revision),
                retry.policy_sha256,
            )
        )
        return sha256(material.encode("utf-8")).hexdigest()
