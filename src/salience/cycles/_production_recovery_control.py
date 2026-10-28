"""Typed existing case and intent commands plus deadline deduplication."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from hashlib import sha256
from uuid import UUID

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
from salience.cycles.production_dispatch import (
    ProductionEffectDispatch,
    ProductionPermitRequest,
    ProductionPermitResult,
    ProductionRecoveryCommandReference,
)


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
                command_id=UUID(str(payload["command_id"])),
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
            if work.source_discriminant == "cycle_case":
                due_at = work.deadline
                action_kind = "deadline_suspend"
            elif work.source_discriminant == "pre_admission_intent_case":
                due_at = work.due_at
                action_kind = "wake_intent"
            else:
                raise RecoveryProviderReadDenied("unsupported production recovery command source")

            # Work identity and its canonical due time are immutable. Find the
            # recorded wake before checking mutable source revision/state so a
            # committed typed command can survive a crash at the ledger boundary.
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

            now = connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
            if now < due_at:
                raise RecoveryDeadlineNotDue("canonical recovery deadline is not due")

            if action_kind == "deadline_suspend":
                current = connection.execute(
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
                if current is None or (
                    current["workspace_id"] != work.workspace_id
                    or current["goal_id"] != work.goal_id
                    or current["operation_id"] != work.operation_id
                    or current["context_id"] != work.context_id
                    or current["deadline"] != work.deadline
                ):
                    raise RecoveryIdentityConflict("canonical case identity or deadline changed")
                # The typed command owns stale-revision rejection and exact
                # idempotency replay if it committed before the recovery event.
                source = {
                    "case_id": work.source_id,
                    "revision": work.case_revision,
                    "state": work.state,
                    "deadline": work.deadline,
                    "workspace_id": work.workspace_id,
                    "goal_id": work.goal_id,
                    "owner_id": work.owner_id,
                    "operation_id": work.operation_id,
                    "context_id": work.context_id,
                    "cycle_state": current["cycle_state"],
                }
            else:
                current = connection.execute(
                    """SELECT intent.id AS intent_id,intent.goal_id,
                              intent.eligibility_revision,intent.due_at,intent.expires_at,
                              goal.workspace_id,admission.disposition,admission.cycle_id
                       FROM v4_cycle_intents intent
                       JOIN v4_goals goal ON goal.id=intent.goal_id
                       LEFT JOIN v4_admissions admission
                         ON admission.intent_id=intent.id
                        AND admission.eligibility_revision=%s
                       WHERE intent.id=%s
                       FOR UPDATE OF intent""",
                    (work.eligibility_revision, work.intent_id),
                ).fetchone()
                if current is None or (
                    current["workspace_id"] != work.workspace_id
                    or current["goal_id"] != work.goal_id
                    or current["due_at"] != work.due_at
                    or current["expires_at"] != work.expires_at
                ):
                    raise RecoveryIdentityConflict("canonical intent identity or deadline changed")
                disposition = {
                    "admission_deferred": "deferred",
                    "admission_review_required": "review_required",
                }[work.reason]
                source = {
                    "intent_id": work.intent_id,
                    "eligibility_revision": work.source_revision,
                    "due_at": work.due_at,
                    "expires_at": work.expires_at,
                    "disposition": disposition,
                    "cycle_id": None,
                }
            decision = self.authority.authorize_deadline_wake(
                work, source, action_kind, event_key
            )
            if not isinstance(decision, ProductionRecoveryDecision) or not decision.allowed:
                raise PermissionError("current deadline command authority denied")
            if action_kind == "deadline_suspend":
                if not callable(getattr(self.commands, "suspend_case", None)):
                    raise PermissionError("existing typed case suspension command is unavailable")
                command = ProductionCaseDeadlineCommand(
                    case_id=work.source_id,
                    expected_revision=work.case_revision,
                    operation_id=work.operation_id,
                    context_id=work.context_id,
                    deadline=source["deadline"],
                    idempotency_key=event_key,
                    policy_decision_id=decision.decision_id,
                    stop_revision=decision.stop_revision,
                    route_decision_id=decision.route_decision_id,
                )
                receipt = self.commands.suspend_case(connection, command)
                if not isinstance(receipt, TypedCaseCommandReceipt) or (
                    receipt.case_id != work.source_id
                    or receipt.action != "deadline_suspend"
                    or receipt.resulting_revision != work.case_revision + 1
                ):
                    raise ValueError("typed deadline suspension receipt does not match its source revision")
                payload = {
                    "schema_version": "salience.p1-production-recovery-deadline.v1",
                    "status": "deadline_suspend",
                    "result_class": "suspended",
                    "attempt": receipt.resulting_revision,
                    "command_id": str(receipt.command_id),
                    "case_id": str(work.source_id),
                    "work_id": str(work.work_id),
                    "source_id": str(work.source_id),
                    "intent_id": str(work.intent_id),
                    "operation_id": str(work.operation_id),
                    "deadline": due_at.isoformat(),
                    "event_key": event_key,
                    "policy_decision_id": str(decision.decision_id),
                    "route_decision_id": str(decision.route_decision_id),
                }
            else:
                if not callable(getattr(self.commands, "wake_intent", None)):
                    raise PermissionError("existing typed intent wake command is unavailable")
                command = ProductionIntentDeadlineCommand(
                    intent_id=work.intent_id,
                    expected_revision=work.eligibility_revision,
                    due_at=work.due_at,
                    expires_at=work.expires_at,
                    idempotency_key=event_key,
                    policy_decision_id=decision.decision_id,
                    stop_revision=decision.stop_revision,
                    route_decision_id=decision.route_decision_id,
                )
                receipt = self.commands.wake_intent(connection, command)
                if not isinstance(receipt, TypedIntentWakeReceipt) or (
                    receipt.intent_id != work.intent_id
                    or receipt.eligibility_revision != work.eligibility_revision
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
                    "policy_decision_id": str(decision.decision_id),
                    "route_decision_id": str(decision.route_decision_id),
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
            superseding_observation = connection.execute(
                """SELECT event_id
                   FROM v4_production_recovery_events
                   WHERE work_id=%s AND event_kind='remote_read_observation'
                     AND event_id<>%s AND occurred_at >= %s
                   LIMIT 1""",
                (work.work_id, observation["event_id"], observation["occurred_at"]),
            ).fetchone()
            if superseding_observation is not None:
                raise ValueError("selected remote observation is not the latest remote observation")
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
            canonical_cycle = connection.execute(
                """SELECT cycle.id,cycle.state
                   FROM v4_recovery_cases recovery
                   JOIN v4_cycles cycle ON cycle.id=recovery.target_cycle_id
                   WHERE recovery.id=%s AND recovery.target_cycle_id=%s
                     AND recovery.context_id=%s AND recovery.operation_id=%s
                     AND recovery.workspace_id=%s AND recovery.goal_id=%s
                   FOR UPDATE OF cycle""",
                (
                    work.source_id,
                    work.cycle_id,
                    work.context_id,
                    work.operation_id,
                    work.workspace_id,
                    work.goal_id,
                ),
            ).fetchone()
            case = connection.execute(
                """SELECT recovery.id,recovery.revision,recovery.state,
                          recovery.operation_id,recovery.context_id,recovery.deadline
                   FROM v4_recovery_cases recovery
                   WHERE recovery.id=%s AND recovery.target_cycle_id=%s
                     AND recovery.context_id=%s AND recovery.operation_id=%s
                     AND recovery.workspace_id=%s AND recovery.goal_id=%s
                   FOR UPDATE OF recovery""",
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
                canonical_cycle is None
                or case is None
                or case["revision"] != command.expected_case_revision
                or case["state"] != work.state
                or canonical_cycle["state"] == "closed"
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
                    str(command.observation_event_id),
                    command.observation_sha256,
                    command.request_fingerprint,
                    remote_identity_sha256,
                    str(command.stage_decision_id),
                    str(command.policy_decision_id),
                    str(command.workspace_stop_revision),
                    str(command.goal_stop_revision),
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
                "stage_decision_id": str(command.stage_decision_id),
                "policy_decision_id": str(command.policy_decision_id),
                "workspace_stop_revision": command.workspace_stop_revision,
                "goal_stop_revision": command.goal_stop_revision,
                "route_decision_id": str(command.route_decision_id),
                "observation_event_id": str(command.observation_event_id),
                "observation_sha256": command.observation_sha256,
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
                    or authorization.workspace_stop_revision != command.workspace_stop_revision
                    or authorization.goal_stop_revision != command.goal_stop_revision
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
                stage_decision_id=command.stage_decision_id,
                policy_decision_id=command.policy_decision_id,
                workspace_stop_revision=command.workspace_stop_revision,
                goal_stop_revision=command.goal_stop_revision,
                route_decision_id=command.route_decision_id,
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

    def issue_no_effect_generation_permit(
        self,
        command: NoEffectGenerationReissueCommand,
        *,
        request: ProductionPermitRequest,
        dispatch: ProductionEffectDispatch,
    ) -> ProductionPermitResult:
        """Replay the exact typed no-effect event, then delegate its one use to dispatch."""
        if not isinstance(command, NoEffectGenerationReissueCommand):
            raise ValueError("typed same-operation no-effect command required")
        if not isinstance(request, ProductionPermitRequest):
            raise ValueError("typed production permit request required")
        if not isinstance(dispatch, ProductionEffectDispatch):
            raise ValueError("production effect dispatch is required")
        receipt = self.authorize_no_effect_generation_reissue(command)
        if not isinstance(receipt, ProductionNoEffectReissueReceipt):
            raise ValueError("typed no-effect command receipt is required")
        reference = receipt.command_reference
        if not isinstance(reference, ProductionRecoveryCommandReference):
            raise ValueError("typed no-effect command reference is required")
        if (
            receipt.event_id != reference.event_id
            or receipt.work_id != command.work_id
            or reference.work_id != command.work_id
            or receipt.operation_id != command.operation_id
            or reference.operation_id != command.operation_id
            or receipt.idempotency_key != command.original_idempotency_key
            or reference.original_idempotency_key != command.original_idempotency_key
            or reference.request_fingerprint != command.request_fingerprint
            or reference.case_id != command.case_id
            or reference.case_revision != command.expected_case_revision
            or reference.stage_decision_id != command.stage_decision_id
            or reference.policy_decision_id != command.policy_decision_id
            or reference.workspace_stop_revision != command.workspace_stop_revision
            or reference.goal_stop_revision != command.goal_stop_revision
            or reference.route_decision_id != command.route_decision_id
            or request.operation_id != command.operation_id
            or request.idempotency_key != command.original_idempotency_key
        ):
            raise ValueError("typed no-effect receipt does not match the original operation")
        generation = dispatch._current_generation_for_reissue(
            request, reference.event_id
        )
        next_request = replace(
            request,
            generation=generation,
            recovery_command_id=reference.event_id,
        )
        return dispatch.issue_permit(next_request)
