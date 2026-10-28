"""Scoped original-operation read reconciliation."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from hashlib import sha256
from uuid import UUID

from salience.cycles._production_recovery_contracts import (
    ProductionProviderReadContract, ProductionRecoveryAuthority,
    ProductionRecoveryReadContext, ProductionRecoveryReadResult,
    ProductionRemoteObservation, ProductionRemoteReadRequest,
    ProductionRemoteReadTarget, ProductionScopedReadGrant,
    RecoveryInventoryGap, RecoveryProviderReadDenied, _require_uuid,
)
from salience.cycles._production_recovery_work import ProductionRecoveryWorkRepository


class ProductionRecoveryCoordinator:
    """Serialize one exact, read-only remote reconciliation per work identity."""

    def __init__(
        self,
        repository: ProductionRecoveryWorkRepository,
        *,
        remote_target_factory: Callable[[str], ProductionRemoteReadTarget],
        authority: ProductionRecoveryAuthority,
        provider_contracts: Mapping[str, ProductionProviderReadContract],
    ) -> None:
        if not isinstance(repository, ProductionRecoveryWorkRepository):
            raise ValueError("production recovery work repository required")
        if not callable(remote_target_factory):
            raise ValueError("explicit remote read target factory required")
        if not callable(getattr(authority, "authorize_remote_read", None)) or not callable(
            getattr(authority, "recheck_remote_read", None)
        ):
            raise ValueError("current remote-read authority is required")
        if not isinstance(provider_contracts, Mapping) or any(
            not isinstance(contract, ProductionProviderReadContract)
            for contract in provider_contracts.values()
        ):
            raise ValueError("explicit provider read contracts are required")
        self.repository = repository
        self.remote_target_factory = remote_target_factory
        self.authority = authority
        self.provider_contracts = dict(provider_contracts)

    @staticmethod
    def _read_event_result(event: ProductionRecoveryEvent) -> ProductionRecoveryReadResult:
        payload = event.payload
        return ProductionRecoveryReadResult(
            event_id=event.event_id,
            payload_sha256=event.payload_sha256,
            result_class=str(payload["result_class"]),
            remote_state=payload.get("remote_state"),
            operation_id=UUID(str(payload["operation_id"])),
            idempotency_key=str(payload["event_key"]),
            occurred_at=event.occurred_at,
        )

    @staticmethod
    def _identity_digest(observation: ProductionRemoteObservation | None) -> str:
        if observation is None:
            return sha256(b"no-provider-observation").hexdigest()
        values = (
            observation.provider_id,
            observation.account_id,
            str(observation.operation_id),
            observation.idempotency_key,
            observation.resource_id or "",
            observation.request_fingerprint,
            observation.response_sha256,
        )
        return sha256("\n".join(values).encode("utf-8")).hexdigest()

    @staticmethod
    def _matches(work: ProductionRecoveryWork, context: ProductionRecoveryReadContext,
                 observation: ProductionRemoteObservation) -> bool:
        return (
            observation.provider_id == work.target_alias
            and observation.account_id == work.remote_account_id
            and observation.operation_id == work.operation_id
            and observation.idempotency_key == context.idempotency_key
            and observation.resource_id == work.remote_resource_id
            and observation.request_fingerprint == work.request_fingerprint
        )

    def reconcile(self, work_id: UUID) -> ProductionRecoveryReadResult:
        work_id = _require_uuid(work_id, "work ID")
        with self.repository._connect() as connection:
            work_row = connection.execute(
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
            if work_row is None:
                raise RecoveryInventoryGap("remote reconciliation requires exact enrolled work")
            work = self.repository._work(work_row)
            if work.source_discriminant == "pre_admission_intent_case":
                raise RecoveryProviderReadDenied(
                    "operationless pre-admission intents cannot construct a remote client"
                )
            if work.source_discriminant not in {
                "cycle_case", "production_publication_hold"
            } or work.operation_id is None or work.target_alias is None or work.remote_account_id is None:
                raise RecoveryProviderReadDenied("source lacks an exact original operation read scope")
            dispatch = connection.execute(
                """SELECT dispatch.generation,dispatch.current_permit_id,
                          dispatch.current_claim_id,permit.provider_id AS target_alias,
                          permit.account_id AS remote_account_id,permit.request_snapshot,
                          claim.claimed_at,
                          encode(public.digest(permit.request_snapshot::text,'sha256'),'hex')
                            AS request_fingerprint
                   FROM v4_production_current_dispatch dispatch
                   JOIN v4_production_effect_permits permit
                     ON permit.permit_id=dispatch.current_permit_id
                    AND permit.operation_id=dispatch.operation_id
                    AND permit.generation=dispatch.generation
                   JOIN v4_production_effect_permit_claims claim
                     ON claim.claim_id=dispatch.current_claim_id
                    AND claim.permit_id=permit.permit_id
                    AND claim.operation_id=permit.operation_id
                    AND claim.generation=permit.generation
                   WHERE dispatch.operation_id=%s
                     AND permit.intent_id=%s AND permit.cycle_id=%s
                     AND permit.context_id=%s
                   FOR SHARE OF dispatch,permit,claim""",
                (work.operation_id, work.intent_id, work.cycle_id, work.context_id),
            ).fetchone()
            if dispatch is None or (
                dispatch["target_alias"] != work.target_alias
                or dispatch["remote_account_id"] != work.remote_account_id
                or dispatch["request_fingerprint"] != work.request_fingerprint
            ):
                raise RecoveryProviderReadDenied("current dispatch no longer matches the bound operation")
            context = ProductionRecoveryReadContext(
                idempotency_key=dispatch["request_snapshot"].get("idempotency_key"),
                operation_started_at=dispatch["claimed_at"],
                provider_contract=self.provider_contracts.get(work.target_alias),
            )
            contract = context.provider_contract
            if (
                contract is None
                or contract.provider_id != work.target_alias
                or not contract.exact_idempotency_lookup
            ):
                raise RecoveryProviderReadDenied("provider has no exact idempotency-bound read contract")
            now = connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
            visibility_boundary = (
                context.operation_started_at + contract.authoritative_visibility_window
                if contract.authoritative_visibility_window is not None
                else None
            )
            if visibility_boundary is None:
                phase = "no-visibility-window"
            elif now < visibility_boundary:
                phase = "pre-visibility"
            else:
                phase = "visibility-elapsed"
            event_key = f"remote-read:{work.work_id}:{phase}"

            # A pre-window not-found is useful evidence, but it is not proof of
            # absence. It gets one replay-stable identity; after its boundary,
            # one separate authoritative read is permitted under a second key.
            prior = connection.execute(
                """SELECT * FROM v4_production_recovery_events
                   WHERE work_id=%s AND event_key=%s""",
                (work.work_id, event_key),
            ).fetchone()
            if prior is not None:
                return self._read_event_result(self.repository._event(prior))
            elapsed = connection.execute(
                """SELECT * FROM v4_production_recovery_events
                   WHERE work_id=%s AND event_key=%s""",
                (work.work_id, f"remote-read:{work.work_id}:visibility-elapsed"),
            ).fetchone()
            if elapsed is not None:
                return self._read_event_result(self.repository._event(elapsed))
            preliminary = connection.execute(
                """SELECT * FROM v4_production_recovery_events
                   WHERE work_id=%s AND event_key=%s""",
                (work.work_id, f"remote-read:{work.work_id}:pre-visibility"),
            ).fetchone()
            if phase == "visibility-elapsed" and preliminary is not None:
                previous = preliminary["payload"]
                if not (
                    previous.get("result_class") == "unknown"
                    and previous.get("remote_state") == "not_found"
                ):
                    return self._read_event_result(self.repository._event(preliminary))

            grant = self.authority.authorize_remote_read(work, context)
            if not isinstance(grant, ProductionScopedReadGrant):
                raise PermissionError("typed exact read grant required")
            if (
                grant.provider_id != work.target_alias
                or grant.account_id != work.remote_account_id
                or grant.resource_id != work.remote_resource_id
                or grant.operation_id != work.operation_id
                or grant.idempotency_key != context.idempotency_key
                or grant.request_fingerprint != work.request_fingerprint
                or grant.scope != "original_operation_read"
                or grant.expires_at <= now
            ):
                raise PermissionError("current grant is not bound to the exact original operation")
            request = ProductionRemoteReadRequest(
                operation_id=work.operation_id,
                idempotency_key=context.idempotency_key,
                account_id=work.remote_account_id,
                target_id=work.target_alias,
                resource_id=work.remote_resource_id,
                request_fingerprint=work.request_fingerprint,
                read_grant=grant,
            )
            # Construction occurs only after a current exact grant is available.
            target = self.remote_target_factory(work.target_alias)
            if not callable(getattr(target, "read_operation", None)):
                raise RecoveryProviderReadDenied("remote target exposes only the exact read operation")
            observation = None
            read_error = False
            try:
                observation = target.read_operation(request)
            except Exception:
                # A transport failure is evidence of uncertainty, never permission
                # to issue or retry a business effect.
                read_error = True
            try:
                self.authority.recheck_remote_read(work, grant)
            except Exception:
                payload = {
                    "schema_version": "salience.p1-production-recovery-read.v1",
                    "reason_code": "authority_changed_during_read",
                    "result_class": "held",
                    "evidence_sha256": self._identity_digest(observation),
                    "remote_identity_sha256": self._identity_digest(observation),
                    "request_fingerprint": work.request_fingerprint,
                    "operation_id": str(work.operation_id),
                    "intent_id": str(work.intent_id),
                    "work_id": str(work.work_id),
                    "source_id": str(work.source_id),
                    "event_key": context.idempotency_key,
                }
                if work.case_id is not None:
                    payload["case_id"] = str(work.case_id)
                event = self.repository._append_event(
                    connection,
                    work,
                    event_key=event_key,
                    event_kind="stop_revocation_fence",
                    policy_decision_id=grant.policy_decision_id,
                    payload=payload,
                )
                return self._read_event_result(event)

            current_time = connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
            exact = isinstance(observation, ProductionRemoteObservation) and self._matches(
                work, context, observation
            )
            observed_at = observation.observed_at if isinstance(observation, ProductionRemoteObservation) else None
            time_valid = (
                observed_at is not None
                and context.operation_started_at <= observed_at <= current_time
            )
            result_class = "unknown"
            remote_state = "unavailable" if read_error or observation is None else "unknown"
            evidence_sha256 = self._identity_digest(observation if isinstance(observation, ProductionRemoteObservation) else None)
            if isinstance(observation, ProductionRemoteObservation):
                remote_state = observation.remote_state
                evidence_sha256 = observation.response_sha256
                if exact and time_valid:
                    if observation.result == "in_progress":
                        result_class = "in_progress"
                    elif observation.result == "succeeded":
                        result_class = "terminal_success"
                    elif observation.result == "not_found":
                        if (
                            phase == "visibility-elapsed"
                            and visibility_boundary is not None
                            and current_time >= visibility_boundary
                            and observed_at >= visibility_boundary
                        ):
                            result_class = "authoritative_no_effect"
                    elif observation.result == "unknown":
                        result_class = "unknown"
            payload = {
                "schema_version": "salience.p1-production-recovery-read.v1",
                "reason_code": "remote_read_timeout" if read_error else "remote_observation",
                "result_class": result_class,
                "evidence_sha256": evidence_sha256,
                "remote_identity_sha256": self._identity_digest(
                    observation if isinstance(observation, ProductionRemoteObservation) else None
                ),
                "request_fingerprint": work.request_fingerprint,
                "operation_id": str(work.operation_id),
                "intent_id": str(work.intent_id),
                "work_id": str(work.work_id),
                "source_id": str(work.source_id),
                "remote_state": remote_state,
                "event_key": context.idempotency_key,
            }
            if visibility_boundary is not None:
                payload["deadline"] = visibility_boundary.isoformat()
            if work.case_id is not None:
                payload["case_id"] = str(work.case_id)
            event = self.repository._append_event(
                connection,
                work,
                event_key=event_key,
                event_kind="remote_read_observation",
                policy_decision_id=grant.policy_decision_id,
                payload=payload,
            )
            return self._read_event_result(event)
