"""Typed existing case and intent commands plus deadline deduplication."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from uuid import UUID, uuid4

from salience.cycles._production_recovery_contracts import (
    NoEffectGenerationReissueCommand, ProductionCaseDeadlineCommand,
    ProductionDeadlineWakeReceipt, ProductionIntentDeadlineCommand,
    ProductionNoEffectAuthorization, ProductionNoEffectReissueReceipt,
    ProductionRecoveryAuthority, ProductionRecoveryDecision,
    RecoveryDeadlineNotDue, RecoveryEventConflict, RecoveryIdentityConflict,
    RecoveryInventoryGap, RecoveryProviderReadDenied, TypedCaseCommandReceipt,
    TypedIntentWakeReceipt, _require_aware, _require_uuid,
)
from salience.cycles._production_recovery_work import ProductionRecoveryWorkRepository
from salience.cycles.production_dispatch import ProductionRecoveryCommandReference


class ProductionRecoveryCaseController:
    """Typed command boundary for canonical case and intent paths only."""

    def __init__(
        self,
        repository: ProductionRecoveryWorkRepository,
        *,
        authority: ProductionRecoveryAuthority,
        commands: object,
    ) -> None:
        if not isinstance(repository, ProductionRecoveryWorkRepository):
            raise ValueError("production recovery work repository required")
        if not callable(getattr(authority, "authorize_deadline_wake", None)) or not callable(
            getattr(authority, "authorize_no_effect_reissue", None)
        ):
            raise ValueError("current case and no-effect authorization is required")
        self.repository = repository
        self.authority = authority
        self.commands = commands

    @staticmethod
    def _deadline_replay(
        repository: ProductionRecoveryWorkRepository, connection, event_row
    ) -> ProductionDeadlineWakeReceipt:
        event = repository._event(event_row)
        payload = event.payload
        if payload.get("status") == "deadline_suspend":
            result: TypedCaseCommandReceipt | TypedIntentWakeReceipt = TypedCaseCommandReceipt(
                case_id=UUID(str(payload["case_id"])),
                action="deadline_suspend",
                resulting_revision=int(payload["attempt"]),
                command_id=event.event_id,
            )
        else:
            cycle_id = payload.get("result_class")
            result = TypedIntentWakeReceipt(
                intent_id=UUID(str(payload["intent_id"])),
                eligibility_revision=int(payload["attempt"]),
                disposition=str(payload["status"]),
                cycle_id=UUID(str(cycle_id)) if cycle_id else None,
            )
        return ProductionDeadlineWakeReceipt(event.event_id, result)

    def wake_deadline(self, work_id: UUID) -> ProductionDeadlineWakeReceipt:
        work_id = _require_uuid(work_id, "work ID")
        with self.repository._connect() as connection:
            row = connection.execute(
                """SELECT work.*
                   FROM v4_production_recovery_work work
                   JOIN v4_production_intent_enrollments enrollment
                     ON enrollment.intent_id=work.intent_id
                    AND enrollment.eligibility_revision=work.eligibility_revision
                    AND enrollment.workspace_id=work.workspace_id
                    AND enrollment.goal_id=work.goal_id
                   WHERE work.work_id=%s FOR UPDATE OF work""",
                (work_id,),
            ).fetchone()
            if row is None:
                raise RecoveryInventoryGap("deadline source requires exact enrolled work")
            work = self.repository._work(row)
            if work.source_discriminant == "production_publication_hold":
                raise RecoveryProviderReadDenied(
                    "immutable publication holds do not dispatch case or intent commands"
                )
            now = connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
            if work.source_discriminant == "cycle_case":
                source = connection.execute(
                    """SELECT recovery.id AS case_id,recovery.revision,recovery.state,
                              recovery.deadline,recovery.workspace_id,recovery.goal_id,
                              recovery.owner_id,recovery.operation_id,recovery.context_id,
                              cycle.state AS cycle_state
                       FROM v4_recovery_cases recovery
                       JOIN v4_cycles cycle ON cycle.id=recovery.target_cycle_id
                       WHERE recovery.id=%s AND recovery.workspace_id=%s
                         AND recovery.goal_id=%s AND recovery.target_cycle_id=%s
                         AND recovery.context_id=%s AND recovery.operation_id=%s
                       FOR UPDATE OF recovery,cycle""",
                    (
                        work.source_id,
                        work.workspace_id,
                        work.goal_id,
                        work.cycle_id,
                        work.context_id,
                        work.operation_id,
                    ),
                ).fetchone()
                if source is None or source["revision"] != work.case_revision or source["state"] != work.state:
                    raise RecoveryIdentityConflict("canonical case revision or state changed")
                due_at = source["deadline"]
                if now < due_at:
                    raise RecoveryDeadlineNotDue("canonical case deadline is not due")
                action_kind = "deadline_suspend"
            elif work.source_discriminant == "pre_admission_intent_case":
                source = connection.execute(
                    """SELECT intent.id AS intent_id,intent.eligibility_revision,
                              intent.due_at,intent.expires_at,admission.disposition,
                              admission.cycle_id
                       FROM v4_cycle_intents intent
                       JOIN v4_admissions admission
                         ON admission.intent_id=intent.id
                        AND admission.eligibility_revision=intent.eligibility_revision
                       JOIN v4_production_intent_enrollments enrollment
                         ON enrollment.intent_id=intent.id
                        AND enrollment.eligibility_revision=intent.eligibility_revision
                        AND enrollment.workspace_id=intent.workspace_id
                        AND enrollment.goal_id=intent.goal_id
                       WHERE intent.id=%s AND intent.eligibility_revision=%s
                         AND admission.cycle_id IS NULL
                       FOR UPDATE OF intent,admission""",
                    (work.intent_id, work.eligibility_revision),
                ).fetchone()
                if source is None or source["eligibility_revision"] != work.source_revision:
                    raise RecoveryIdentityConflict("pre-admission intent eligibility changed")
                due_at = source["due_at"]
                if now < due_at:
                    raise RecoveryDeadlineNotDue("canonical intent due time is not due")
                if now >= source["expires_at"]:
                    raise RecoveryIdentityConflict("expired intent cannot be woken into admission")
                action_kind = "wake_intent"
            else:
                raise RecoveryProviderReadDenied("unsupported production recovery command source")

            event_key = (
                f"deadline-wake:{work.source_discriminant}:{work.source_id}:"
                f"{work.source_revision}:{due_at.isoformat()}:{action_kind}"
            )
            replay = connection.execute(
                "SELECT * FROM v4_production_recovery_events WHERE event_key=%s",
                (event_key,),
            ).fetchone()
            if replay is not None:
                return self._deadline_replay(self.repository, connection, replay)
            decision = self.authority.authorize_deadline_wake(work, source, action_kind)
            if not isinstance(decision, ProductionRecoveryDecision) or not decision.allowed:
                raise PermissionError("current deadline command authority denied")
            if action_kind == "deadline_suspend":
                if not callable(getattr(self.commands, "suspend_case", None)):
                    raise PermissionError("existing typed case suspension command is unavailable")
                if source["cycle_state"] == "closed":
                    raise RecoveryIdentityConflict("closed cycle cannot receive a deadline case command")
                command = ProductionCaseDeadlineCommand(
                    case_id=work.source_id,
                    expected_revision=source["revision"],
                    operation_id=work.operation_id,
                    context_id=work.context_id,
                    deadline=source["deadline"],
                    idempotency_key=event_key,
                    policy_decision_id=decision.decision_id,
                    stop_revision=decision.stop_revision,
                    route_decision_id=uuid4(),
                )
                receipt = self.commands.suspend_case(connection, command)
                if not isinstance(receipt, TypedCaseCommandReceipt) or (
                    receipt.case_id != work.source_id
                    or receipt.action != "deadline_suspend"
                    or receipt.resulting_revision != source["revision"] + 1
                ):
                    raise ValueError("typed deadline suspension receipt does not match its source revision")
                payload = {
                    "schema_version": "salience.p1-production-recovery-deadline.v1",
                    "status": "deadline_suspend",
                    "result_class": "suspended",
                    "attempt": receipt.resulting_revision,
                    "case_id": str(work.source_id),
                    "work_id": str(work.work_id),
                    "source_id": str(work.source_id),
                    "intent_id": str(work.intent_id),
                    "operation_id": str(work.operation_id),
                    "deadline": due_at.isoformat(),
                    "event_key": event_key,
                }
            else:
                if not callable(getattr(self.commands, "wake_intent", None)):
                    raise PermissionError("existing typed intent wake command is unavailable")
                command = ProductionIntentDeadlineCommand(
                    intent_id=work.intent_id,
                    expected_revision=source["eligibility_revision"],
                    due_at=source["due_at"],
                    expires_at=source["expires_at"],
                    idempotency_key=event_key,
                    policy_decision_id=decision.decision_id,
                    stop_revision=decision.stop_revision,
                    route_decision_id=uuid4(),
                )
                receipt = self.commands.wake_intent(connection, command)
                if not isinstance(receipt, TypedIntentWakeReceipt) or (
                    receipt.intent_id != work.intent_id
                    or receipt.eligibility_revision != source["eligibility_revision"]
                ):
                    raise ValueError("typed intent wake receipt does not match its enrollment revision")
                payload = {
                    "schema_version": "salience.p1-production-recovery-deadline.v1",
                    "status": receipt.disposition,
                    "result_class": str(receipt.cycle_id) if receipt.cycle_id else "",
                    "attempt": receipt.eligibility_revision,
                    "intent_id": str(work.intent_id),
                    "work_id": str(work.work_id),
                    "source_id": str(work.source_id),
                    "deadline": due_at.isoformat(),
                    "event_key": event_key,
                }
            event = self.repository._append_event(
                connection,
                work,
                event_key=event_key,
                event_kind="escalation",
                policy_decision_id=decision.decision_id,
                payload=payload,
            )
            if isinstance(receipt, TypedCaseCommandReceipt):
                result = receipt
            else:
                result = receipt
            return ProductionDeadlineWakeReceipt(event.event_id, result)

    def authorize_no_effect_generation_reissue(
        self, command: NoEffectGenerationReissueCommand
    ) -> ProductionNoEffectReissueReceipt:
        if not isinstance(command, NoEffectGenerationReissueCommand):
            raise ValueError("typed same-operation no-effect command required")
        with self.repository._connect() as connection:
            row = connection.execute(
                """SELECT work.*
                   FROM v4_production_recovery_work work
                   JOIN v4_production_intent_enrollments enrollment
                     ON enrollment.intent_id=work.intent_id
                    AND enrollment.eligibility_revision=work.eligibility_revision
                    AND enrollment.workspace_id=work.workspace_id
                    AND enrollment.goal_id=work.goal_id
                   WHERE work.work_id=%s FOR UPDATE OF work""",
                (command.work_id,),
            ).fetchone()
            if row is None:
                raise RecoveryInventoryGap("no-effect command requires exact enrolled work")
            work = self.repository._work(row)
            if (
                work.source_discriminant != "cycle_case"
                or work.case_id != command.case_id
                or work.operation_id != command.operation_id
                or work.request_fingerprint != command.request_fingerprint
                or work.case_revision != command.expected_case_revision
            ):
                raise ValueError("no-effect command is not bound to the exact production case and operation")
            observation = connection.execute(
                """SELECT * FROM v4_production_recovery_events
                   WHERE event_id=%s AND work_id=%s
                     AND event_kind='remote_read_observation'""",
                (command.observation_event_id, work.work_id),
            ).fetchone()
            if observation is None:
                raise ValueError("committed remote observation is missing from the exact work")
            payload = observation["payload"]
            if (
                observation["payload_sha256"] != command.observation_sha256
                or payload.get("result_class") != "authoritative_no_effect"
                or payload.get("remote_state") != "not_found"
                or payload.get("operation_id") != str(work.operation_id)
                or payload.get("request_fingerprint") != work.request_fingerprint
                or payload.get("event_key") != command.original_idempotency_key
                or not payload.get("deadline")
            ):
                raise ValueError("remote observation is not exact authoritative no-effect evidence")
            visibility_boundary = _require_aware(
                datetime.fromisoformat(str(payload["deadline"])),
                "provider visibility boundary",
            )
            if observation["occurred_at"] < visibility_boundary:
                raise ValueError("no-effect observation predates the provider visibility boundary")
            case = connection.execute(
                """SELECT recovery.id,recovery.revision,recovery.state,
                          recovery.operation_id,recovery.context_id,recovery.deadline,
                          cycle.state AS cycle_state
                   FROM v4_recovery_cases recovery
                   JOIN v4_cycles cycle ON cycle.id=recovery.target_cycle_id
                   WHERE recovery.id=%s AND recovery.target_cycle_id=%s
                     AND recovery.context_id=%s AND recovery.operation_id=%s
                     AND recovery.workspace_id=%s AND recovery.goal_id=%s
                   FOR UPDATE OF recovery,cycle""",
                (
                    work.source_id,
                    work.cycle_id,
                    work.context_id,
                    work.operation_id,
                    work.workspace_id,
                    work.goal_id,
                ),
            ).fetchone()
            if (
                case is None
                or case["revision"] != command.expected_case_revision
                or case["state"] != work.state
                or case["cycle_state"] == "closed"
            ):
                raise ValueError("current production case revision or open-cycle binding changed")
            dispatch = connection.execute(
                """SELECT permit.request_snapshot,dispatch.generation
                   FROM v4_production_current_dispatch dispatch
                   JOIN v4_production_effect_permits permit
                     ON permit.permit_id=dispatch.current_permit_id
                    AND permit.operation_id=dispatch.operation_id
                    AND permit.generation=dispatch.generation
                   WHERE dispatch.operation_id=%s AND permit.intent_id=%s
                     AND permit.cycle_id=%s AND permit.context_id=%s
                   FOR SHARE OF dispatch,permit""",
                (work.operation_id, work.intent_id, work.cycle_id, work.context_id),
            ).fetchone()
            if dispatch is None or (
                dispatch["request_snapshot"].get("idempotency_key")
                != command.original_idempotency_key
            ):
                raise ValueError("current dispatch no longer uses the original operation key")
            key = command.idempotency_key
            prior = connection.execute(
                "SELECT * FROM v4_production_recovery_events WHERE event_key=%s",
                (key,),
            ).fetchone()
            remote_identity_sha256 = sha256(
                f"{work.operation_id}:{command.original_idempotency_key}".encode("utf-8")
            ).hexdigest()
            proof_material = ":".join(
                (
                    command.observation_sha256,
                    command.request_fingerprint,
                    remote_identity_sha256,
                    str(command.stage_decision_id),
                    str(command.policy_decision_id),
                    str(command.stop_revision),
                    str(command.route_decision_id),
                )
            )
            proof_sha256 = sha256(proof_material.encode("utf-8")).hexdigest()
            event_payload = {
                "schema_version": "salience.p1-same-operation-no-effect.v1",
                "reason_code": "authoritative_no_effect",
                "evidence_sha256": proof_sha256,
                "remote_identity_sha256": remote_identity_sha256,
                "request_fingerprint": command.request_fingerprint,
                "operation_id": str(command.operation_id),
                "case_id": str(command.case_id),
                "work_id": str(command.work_id),
                "source_id": str(work.source_id),
                "state": work.state,
                "result_class": "authoritative_no_effect",
                "event_key": key,
            }
            if prior is not None:
                if (
                    prior["work_id"] != work.work_id
                    or prior["event_kind"] != "same_operation_no_effect_reissue"
                    or prior["payload"] != event_payload
                ):
                    raise ValueError("no-effect idempotency key conflicts with prior evidence")
                event = self.repository._event(prior)
            else:
                authorization = self.authority.authorize_no_effect_reissue(
                    work, command, payload
                )
                if not isinstance(authorization, ProductionNoEffectAuthorization) or (
                    authorization.stage_decision_id != command.stage_decision_id
                    or authorization.policy_decision_id != command.policy_decision_id
                    or authorization.stop_revision != command.stop_revision
                    or authorization.route_decision_id != command.route_decision_id
                ):
                    raise PermissionError("stage, policy, stop, or route authorization changed")
                event = self.repository._append_event(
                    connection,
                    work,
                    event_key=key,
                    event_kind="same_operation_no_effect_reissue",
                    policy_decision_id=authorization.policy_decision_id,
                    payload=event_payload,
                )
            reference = ProductionRecoveryCommandReference(
                event_id=event.event_id,
                work_id=work.work_id,
                operation_id=work.operation_id,
                request_fingerprint=work.request_fingerprint,
                original_idempotency_key=command.original_idempotency_key,
                case_id=work.source_id,
                case_revision=command.expected_case_revision,
            )
            return ProductionNoEffectReissueReceipt(
                event_id=event.event_id,
                work_id=work.work_id,
                operation_id=work.operation_id,
                idempotency_key=command.original_idempotency_key,
                case_revision=command.expected_case_revision,
                payload_sha256=event.payload_sha256,
                command_reference=reference,
            )
