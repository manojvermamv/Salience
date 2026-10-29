"""Versioned alert delivery, readback, acknowledgment, and hold sink."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime, timedelta
from hashlib import sha256
from uuid import UUID

from salience.cycles._production_recovery_contracts import (
    ProductionAlertAcknowledgment, ProductionAlertReceipt, ProductionAlertRequest,
    ProductionAlertRetryPolicy, ProductionAlertTarget, ProductionRecipientProfile,
    ProductionRecoveryAuthority, ProductionRecoveryDecision, ProductionRecoveryEvent,
    ProductionRecoveryWork, RecoveryEventConflict, RecoveryIdentityConflict,
    RecoveryInventoryGap, _require_aware, _require_sha256, _require_uuid, _safe_code,
)
from salience.cycles._production_recovery_work import (
    ProductionRecoveryWorkRepository,
    production_alert_escalation_due_at,
)


class ProductionRecoveryAlerts:
    """Explicit versioned alert delivery, readback, acknowledgment, and hold sink."""

    def __init__(
        self,
        repository: ProductionRecoveryWorkRepository,
        *,
        target: ProductionAlertTarget,
        authority: ProductionRecoveryAuthority,
        recipient_profile: ProductionRecipientProfile,
        retry_policy: ProductionAlertRetryPolicy,
        wait: Callable[[timedelta], None],
        now: Callable[[], datetime],
        schedule_hold_target: Callable[[UUID, UUID, str, str], None],
    ) -> None:
        if not isinstance(repository, ProductionRecoveryWorkRepository):
            raise ValueError("production recovery work repository required")
        if not callable(getattr(target, "send", None)) or not callable(
            getattr(target, "lookup", None)
        ):
            raise ValueError("alert target must support same-key send and read-only lookup")
        if not callable(getattr(authority, "authorize_alert", None)) or not callable(
            getattr(authority, "authorize_acknowledgment", None)
        ):
            raise ValueError("current alert and acknowledgment authority required")
        if not isinstance(recipient_profile, ProductionRecipientProfile):
            raise ValueError("explicit versioned recipient profile required")
        if not isinstance(retry_policy, ProductionAlertRetryPolicy):
            raise ValueError("explicit versioned bounded retry policy required")
        if not callable(wait):
            raise ValueError("explicit deterministic or qualified retry wait required")
        if not callable(now):
            raise ValueError("explicit deterministic or qualified alert clock required")
        if not callable(schedule_hold_target):
            raise ValueError("explicit schedule hold alert sink required")
        self.repository = repository
        self.target = target
        self.authority = authority
        self.recipient_profile = recipient_profile
        self.retry_policy = retry_policy
        self.wait = wait
        self.now = now
        self.schedule_hold_target = schedule_hold_target

    def emit_hold(
        self,
        workspace_id: UUID,
        source_id: UUID,
        reason_code: str,
        evidence_sha256: str,
    ) -> None:
        """Implement Task 6 ScheduleAlertSink through an explicit injected sink."""
        _require_uuid(workspace_id, "hold workspace ID")
        _require_uuid(source_id, "hold source ID")
        _safe_code(reason_code, "hold reason code")
        _require_sha256(evidence_sha256, "hold evidence SHA-256")
        self.schedule_hold_target(
            workspace_id, source_id, reason_code, evidence_sha256
        )

    @staticmethod
    def _event_payload(
        work: ProductionRecoveryWork,
        request: ProductionAlertRequest,
        *,
        status: str,
        attempt: int | None = None,
        delivery_id: str | None = None,
        error_code: str | None = None,
        retry_at: datetime | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": "salience.p1-production-recovery-alert.v1",
            "status": status,
            "reason": work.reason,
            "state": work.state,
            "source_id": str(work.source_id),
            "work_id": str(work.work_id),
            "intent_id": str(work.intent_id),
            "deadline": work.deadline.isoformat(),
            "severity": request.severity,
            "escalation_level": request.escalation_level,
            "recipient_profile_revision": request.recipient_profile.revision,
            "policy_revision": request.retry_policy.revision,
            "evidence_sha256": ProductionRecoveryWorkRepository._policy_evidence_from_profile(
                request.recipient_profile, request.retry_policy
            ),
            "event_key": request.idempotency_key,
        }
        if work.operation_id is not None:
            payload["operation_id"] = str(work.operation_id)
        if work.case_id is not None:
            payload["case_id"] = str(work.case_id)
        if work.target_alias is not None:
            payload["target_alias"] = work.target_alias
        if request.recipient_tier == "owner":
            payload["owner_id"] = str(request.recipient_profile.owner_subject_id)
        if attempt is not None:
            payload["attempt"] = attempt
        if delivery_id is not None:
            payload["delivery_id"] = delivery_id
        if error_code is not None:
            payload["error_code"] = error_code
        if retry_at is not None:
            payload["retry_at"] = retry_at.isoformat()
        return payload

    def _receipt_event(
        self,
        connection,
        work: ProductionRecoveryWork,
        request: ProductionAlertRequest,
        receipt: ProductionAlertReceipt,
        *,
        policy_decision_id: UUID,
        actor_id: UUID | None = None,
    ) -> ProductionRecoveryEvent:
        if (
            receipt.alert_event_id != request.alert_event_id
            or receipt.idempotency_key != request.idempotency_key
            or receipt.status != "accepted"
            or receipt.provider_message_id is None
            or receipt.accepted_at is None
        ):
            raise ValueError("provider alert receipt is not bound to the exact intent")
        payload = self._event_payload(
            work,
            request,
            status="accepted",
            delivery_id=receipt.provider_message_id,
        )
        return self.repository._append_event(
            connection,
            work,
            event_key=f"delivery-receipt:{request.alert_event_id}",
            event_kind="delivery_receipt",
            policy_decision_id=policy_decision_id,
            payload=payload,
            actor_id=actor_id,
        )

    @staticmethod
    def _receipt_from_event(
        event: ProductionRecoveryEvent, request: ProductionAlertRequest
    ) -> ProductionAlertReceipt:
        delivery_id = event.payload.get("delivery_id")
        status = str(event.payload.get("status"))
        return ProductionAlertReceipt(
            alert_event_id=request.alert_event_id,
            idempotency_key=request.idempotency_key,
            provider_message_id=str(delivery_id) if delivery_id else None,
            status=status,
            accepted_at=event.occurred_at if status == "accepted" else None,
        )

    def _owner_is_current(self, work: ProductionRecoveryWork) -> bool:
        with self.repository._connect() as connection:
            return connection.execute(
                """SELECT 1 FROM identity_subjects
                   WHERE id=%s AND workspace_id=%s AND enabled
                     AND expires_at>clock_timestamp()""",
                (work.owner_id, work.workspace_id),
            ).fetchone() is not None

    def _validate_intent_tier_and_policy(
        self,
        connection,
        work: ProductionRecoveryWork,
        alert: ProductionAlertRequest,
        intent,
    ) -> datetime | None:
        level = alert.escalation_level
        if level > len(alert.retry_policy.escalation_after):
            raise ValueError("escalation level exceeds the explicit retry policy")
        expected_tier = "owner" if level == 0 else "on_call"
        if alert.recipient_tier != expected_tier:
            raise RecoveryIdentityConflict(
                "alert recipient tier does not match its escalation level"
            )
        if level > 0 and not alert.recipient_profile.on_call_group_ids:
            raise ValueError("configured on-call recipients are required for escalation")

        expected_key = (
            f"alert-intent:{work.work_id}:initial"
            if level == 0
            else f"alert-intent:{work.work_id}:{work.source_revision}:{level}"
        )
        if intent["event_key"] != expected_key:
            raise RecoveryIdentityConflict(
                "alert request is bound to a different escalation intent"
            )

        payload = intent["payload"]
        expected_common = {
            "work_id": str(work.work_id),
            "source_id": str(work.source_id),
            "reason": work.reason,
            "state": work.state,
            "deadline": work.deadline.isoformat(),
            "recipient_profile_revision": alert.recipient_profile.revision,
            "policy_revision": alert.retry_policy.revision,
            "evidence_sha256": ProductionRecoveryWorkRepository._policy_evidence_from_profile(
                alert.recipient_profile, alert.retry_policy
            ),
        }
        if any(payload.get(name) != value for name, value in expected_common.items()):
            raise RecoveryIdentityConflict("alert intent does not match the exact request policy")
        if level == 0:
            return None
        if (
            payload.get("escalation_level") != level
            or payload.get("severity") != alert.severity
        ):
            raise RecoveryIdentityConflict(
                "alert intent does not match the exact escalation request"
            )

        initial = connection.execute(
            """SELECT * FROM v4_production_recovery_events
               WHERE work_id=%s AND event_kind='alert_intent'
                 AND event_key=%s""",
            (work.work_id, f"alert-intent:{work.work_id}:initial"),
        ).fetchone()
        if initial is None:
            raise RecoveryInventoryGap("initial alert intent is missing")
        initial_event = self.repository._event(initial)
        due_at = production_alert_escalation_due_at(
            initial_event, alert.retry_policy, level
        )

        return due_at

    def _require_escalation_due(self, due_at: datetime | None) -> None:
        if due_at is not None and _require_aware(self.now(), "alert delivery time") < due_at:
            raise ValueError("production alert escalation is not due")

    @staticmethod
    def _recorded_recipient_tier(
        request: ProductionAlertRequest, payload: Mapping[str, object]
    ) -> ProductionAlertRequest:
        owner_id = payload.get("owner_id")
        if owner_id is not None and owner_id != str(
            request.recipient_profile.owner_subject_id
        ):
            raise RecoveryIdentityConflict(
                "persisted alert event names a different recipient owner"
            )
        tier = "owner" if owner_id is not None else "on_call"
        if request.escalation_level > 0 and tier != "on_call":
            raise RecoveryIdentityConflict(
                "persisted alert event has a recipient tier inconsistent with escalation"
            )
        return replace(request, recipient_tier=tier)

    def _validate_attempt_event(
        self,
        work: ProductionRecoveryWork,
        request: ProductionAlertRequest,
        row: Mapping[str, object],
        *,
        attempt: int,
    ) -> ProductionAlertRequest:
        payload = row["payload"]
        if not isinstance(payload, Mapping):
            raise RecoveryIdentityConflict("persisted alert attempt payload is malformed")
        recorded_request = self._recorded_recipient_tier(request, payload)
        expected_key = f"delivery-attempt:{request.alert_event_id}:{attempt}"
        expected = self._event_payload(
            work, recorded_request, status="attempting", attempt=attempt
        )
        if (
            row["event_key"] != expected_key
            or type(payload.get("attempt")) is not int
            or payload != expected
        ):
            raise RecoveryIdentityConflict(
                "persisted alert attempt does not match its exact request and policy"
            )
        return recorded_request

    def _validate_retry_history(
        self,
        connection,
        work: ProductionRecoveryWork,
        request: ProductionAlertRequest,
    ) -> tuple[
        int,
        datetime | None,
        Mapping[str, object] | None,
        ProductionAlertRequest | None,
    ]:
        key = request.idempotency_key
        attempt_prefix = f"delivery-attempt:{request.alert_event_id}:%"
        retry_prefix = f"retry-scheduled:{request.alert_event_id}:%"
        attempts = connection.execute(
            """SELECT event_key,occurred_at,policy_decision_id,payload
               FROM v4_production_recovery_events
               WHERE work_id=%s AND event_kind='delivery_attempt'
                 AND (event_key LIKE %s OR payload->>'event_key'=%s)
               ORDER BY occurred_at,event_id""",
            (work.work_id, attempt_prefix, key),
        ).fetchall()
        retry_events = connection.execute(
            """SELECT event_key,occurred_at,policy_decision_id,payload
               FROM v4_production_recovery_events
               WHERE work_id=%s AND event_kind='retry_scheduled'
                 AND (event_key LIKE %s OR payload->>'event_key'=%s)
               ORDER BY occurred_at,event_id""",
            (work.work_id, retry_prefix, key),
        ).fetchall()
        if len(attempts) > request.retry_policy.maximum_attempts:
            raise RecoveryIdentityConflict("persisted alert attempt count exceeds its policy")

        attempt_requests = [
            self._validate_attempt_event(work, request, row, attempt=index)
            for index, row in enumerate(attempts, start=1)
        ]

        expected_retry_count = min(
            len(attempts), request.retry_policy.maximum_attempts - 1
        )
        retry_count_before_latest_attempt = min(
            max(len(attempts) - 1, 0),
            request.retry_policy.maximum_attempts - 1,
        )
        pending_attempt = None
        if len(retry_events) == retry_count_before_latest_attempt and attempts:
            # The last attempt may have committed before its send, lookup, or
            # retry schedule completed. Its same-key claim TTL determines when
            # a restart may safely resume that exact attempt.
            pending_attempt = attempts[-1]
        elif len(retry_events) != expected_retry_count:
            raise RecoveryIdentityConflict(
                "persisted alert retry schedule is missing or conflicting"
            )

        last_retry_at = None
        last_retry_request = None
        for failed_attempt, row in enumerate(retry_events, start=1):
            payload = row["payload"]
            if not isinstance(payload, Mapping):
                raise RecoveryIdentityConflict("persisted retry payload is malformed")
            recorded_request = self._recorded_recipient_tier(request, payload)
            previous_attempt_request = attempt_requests[failed_attempt - 1]
            if recorded_request.recipient_tier != previous_attempt_request.recipient_tier:
                if (
                    previous_attempt_request.recipient_tier != "owner"
                    or recorded_request.recipient_tier != "on_call"
                    or not recorded_request.recipient_profile.on_call_group_ids
                ):
                    raise RecoveryIdentityConflict(
                        "persisted retry changes the recipient tier without an authorized escalation"
                    )
                self._validate_owner_unavailable_escalation(
                    connection,
                    work,
                    previous_attempt_request,
                    attempts[failed_attempt - 1],
                    row,
                )
            elif previous_attempt_request.recipient_tier == "owner":
                self._reject_owner_unavailable_retry_conflict(
                    connection,
                    request,
                    attempts[failed_attempt - 1],
                    row,
                )
            expected = self._event_payload(
                work,
                recorded_request,
                status="retry_scheduled",
                attempt=failed_attempt + 1,
                error_code="target_unavailable",
            )
            retry_at_text = payload.get("retry_at")
            if (
                row["event_key"]
                != f"retry-scheduled:{request.alert_event_id}:{failed_attempt}"
                or type(payload.get("attempt")) is not int
                or set(payload) != set(expected) | {"retry_at"}
                or any(payload.get(name) != value for name, value in expected.items())
                or not isinstance(retry_at_text, str)
            ):
                raise RecoveryIdentityConflict(
                    "persisted alert retry does not match its exact request and policy"
                )
            try:
                retry_at = _require_aware(
                    datetime.fromisoformat(retry_at_text), "persisted alert retry time"
                )
            except (TypeError, ValueError) as error:
                raise RecoveryIdentityConflict(
                    "persisted alert retry time is malformed"
                ) from error
            expected_retry_at = _require_aware(
                row["occurred_at"], "persisted alert retry event time"
            ) + request.retry_policy.retry_intervals[failed_attempt - 1]
            if (
                retry_at != expected_retry_at
                or row["policy_decision_id"]
                != attempts[failed_attempt - 1]["policy_decision_id"]
            ):
                raise RecoveryIdentityConflict(
                    "persisted alert retry timing or decision conflicts with its attempt"
                )
            if failed_attempt < len(attempt_requests):
                next_attempt_request = attempt_requests[failed_attempt]
                if (
                    recorded_request.recipient_tier
                    != next_attempt_request.recipient_tier
                ):
                    raise RecoveryIdentityConflict(
                        "persisted retry recipient tier conflicts with its next attempt"
                    )
            last_retry_at = retry_at
            last_retry_request = recorded_request

        outstanding_retry_request = None
        if (
            pending_attempt is None
            and retry_events
            and len(retry_events) == len(attempts)
        ):
            # The latest retry event commits the exact request for the next
            # attempt even if that attempt has not yet been appended.
            assert last_retry_request is not None
            outstanding_retry_request = last_retry_request

        if (
            pending_attempt is not None
            or len(attempts) < request.retry_policy.maximum_attempts
        ):
            return (
                len(attempts),
                last_retry_at,
                pending_attempt,
                outstanding_retry_request,
            )
        return len(attempts), None, pending_attempt, None

    def _reject_owner_unavailable_retry_conflict(
        self,
        connection,
        request: ProductionAlertRequest,
        attempt_row: Mapping[str, object],
        retry_row: Mapping[str, object],
    ) -> None:
        event_key = f"owner-unavailable:{request.alert_event_id}"
        events = connection.execute(
            """SELECT event_key,work_id,occurred_at
               FROM v4_production_recovery_events
               WHERE event_key=%s OR payload->>'event_key'=%s
               ORDER BY occurred_at,event_id""",
            (event_key, event_key),
        ).fetchall()
        attempt_at = _require_aware(
            attempt_row["occurred_at"], "persisted alert attempt time"
        )
        retry_event_at = _require_aware(
            retry_row["occurred_at"], "persisted alert retry event time"
        )
        if any(
            event["event_key"] != event_key
            or event["work_id"] != request.work_id
            or attempt_at <= _require_aware(
                event["occurred_at"], "persisted owner-unavailable event time"
            ) <= retry_event_at
            for event in events
        ):
            raise RecoveryIdentityConflict(
                "persisted owner-tier retry conflicts with owner-unavailable evidence"
            )

    def _validate_owner_unavailable_escalation(
        self,
        connection,
        work: ProductionRecoveryWork,
        owner_request: ProductionAlertRequest,
        attempt_row: Mapping[str, object],
        retry_row: Mapping[str, object],
    ) -> None:
        event_key = f"owner-unavailable:{owner_request.alert_event_id}"
        events = connection.execute(
            """SELECT event_key,work_id,event_kind,occurred_at,payload
               FROM v4_production_recovery_events
               WHERE event_key=%s OR payload->>'event_key'=%s
               ORDER BY occurred_at,event_id""",
            (event_key, event_key),
        ).fetchall()
        expected = self._event_payload(
            work,
            owner_request,
            status="owner_unavailable",
            error_code="owner_unavailable_escalated",
        )
        if (
            len(events) != 1
            or events[0]["event_key"] != event_key
            or events[0]["work_id"] != work.work_id
            or events[0]["event_kind"] != "owner_unavailable"
            or events[0]["payload"] != expected
        ):
            raise RecoveryIdentityConflict(
                "persisted recipient escalation lacks exact owner-unavailable evidence"
            )
        unavailable_at = _require_aware(
            events[0]["occurred_at"], "persisted owner-unavailable event time"
        )
        attempt_at = _require_aware(
            attempt_row["occurred_at"], "persisted alert attempt time"
        )
        retry_event_at = _require_aware(
            retry_row["occurred_at"], "persisted alert retry event time"
        )
        if not attempt_at <= unavailable_at <= retry_event_at:
            raise RecoveryIdentityConflict(
                "persisted owner-unavailable evidence is outside its retry boundary"
            )

    def _wait_until_retry_at(self, retry_at: datetime) -> None:
        retry_at = _require_aware(retry_at, "persisted alert retry time")
        now = _require_aware(self.now(), "alert delivery time")
        if now < retry_at:
            self.wait(retry_at - now)
            if _require_aware(self.now(), "alert delivery time") < retry_at:
                raise RuntimeError("alert retry wait returned before persisted retry time")

    def _wait_until_attempt_claim_expires(
        self, attempted_at: datetime, claim_ttl: timedelta
    ) -> None:
        claim_expires_at = _require_aware(
            attempted_at, "persisted alert attempt time"
        ) + claim_ttl
        now = _require_aware(self.now(), "alert delivery time")
        if now < claim_expires_at:
            self.wait(claim_expires_at - now)
            if _require_aware(self.now(), "alert delivery time") < claim_expires_at:
                raise RuntimeError("alert attempt claim wait returned before claim TTL")

    def _send_and_lookup(
        self, request: ProductionAlertRequest, idempotency_key: str
    ) -> ProductionAlertReceipt | None:
        try:
            return self.target.send(request, idempotency_key)
        except Exception:
            try:
                return self.target.lookup(idempotency_key)
            except Exception:
                return None

    def _schedule_retry(
        self,
        work: ProductionRecoveryWork,
        request: ProductionAlertRequest,
        *,
        attempt: int,
        policy_decision_id: UUID,
    ) -> datetime:
        delay = request.retry_policy.retry_intervals[attempt - 1]
        with self.repository._connect() as connection:
            now = connection.execute(
                "SELECT clock_timestamp() AS now"
            ).fetchone()["now"]
            self.repository._append_event(
                connection,
                work,
                event_key=f"retry-scheduled:{request.alert_event_id}:{attempt}",
                event_kind="retry_scheduled",
                policy_decision_id=policy_decision_id,
                payload=self._event_payload(
                    work,
                    request,
                    status="retry_scheduled",
                    attempt=attempt + 1,
                    error_code="target_unavailable",
                    retry_at=now + delay,
                ),
                occurred_at=now,
            )
        with self.repository._connect() as connection:
            (
                attempt_count,
                retry_at,
                pending_attempt,
                outstanding_retry_request,
            ) = self._validate_retry_history(connection, work, request)
        if (
            attempt_count != attempt
            or retry_at is None
            or pending_attempt is not None
            or outstanding_retry_request != request
        ):
            raise RecoveryIdentityConflict(
                "persisted alert retry does not authorize the next attempt"
            )
        self._wait_until_retry_at(retry_at)
        return retry_at

    def _held_receipt(
        self,
        work: ProductionRecoveryWork,
        request: ProductionAlertRequest,
        *,
        reason_code: str,
        policy_decision_id: UUID,
    ) -> ProductionAlertReceipt:
        with self.repository._connect() as connection:
            self._record_owner_unavailable(
                connection, work, request, reason_code, policy_decision_id
            )
            self.repository._append_event(
                connection,
                work,
                event_key=f"held-alert:{request.alert_event_id}",
                event_kind="held_conflict",
                policy_decision_id=policy_decision_id,
                payload=self._event_payload(
                    work,
                    request,
                    status="held",
                    error_code=reason_code,
                    attempt=0,
                ),
            )
        return ProductionAlertReceipt(
            alert_event_id=request.alert_event_id,
            idempotency_key=request.idempotency_key,
            provider_message_id=None,
            status="held",
            accepted_at=None,
        )

    def _record_owner_unavailable(
        self,
        connection,
        work: ProductionRecoveryWork,
        request: ProductionAlertRequest,
        reason_code: str,
        policy_decision_id: UUID,
    ) -> None:
        event_key = f"owner-unavailable:{request.alert_event_id}"
        existing = connection.execute(
            "SELECT work_id FROM v4_production_recovery_events WHERE event_key=%s",
            (event_key,),
        ).fetchone()
        if existing is not None:
            if existing["work_id"] != work.work_id:
                raise RecoveryEventConflict("owner-unavailable event is bound to different work")
            return
        self.repository._append_event(
            connection,
            work,
            event_key=event_key,
            event_kind="owner_unavailable",
            policy_decision_id=policy_decision_id,
            payload=self._event_payload(
                work,
                request,
                status="owner_unavailable",
                error_code=reason_code,
            ),
        )

    def send(self, alert: ProductionAlertRequest) -> ProductionAlertReceipt:
        if not isinstance(alert, ProductionAlertRequest):
            raise ValueError("typed production alert request required")
        # Serialize local intent/replay checks around the external same-key
        # delivery. The target still provides provider-side idempotency, while
        # the database lock prevents parallel workers from colliding on the
        # same append-only attempt and receipt identities.
        lock_identity = f"production-alert-send:{alert.idempotency_key}"
        with self.repository._connect() as lock_connection:
            with lock_connection.transaction():
                lock_connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                    (lock_identity,),
                )
                return self._send_locked(alert)

    def _send_locked(self, alert: ProductionAlertRequest) -> ProductionAlertReceipt:
        if not isinstance(alert, ProductionAlertRequest):
            raise ValueError("typed production alert request required")
        if alert.recipient_profile != self.recipient_profile or alert.retry_policy != self.retry_policy:
            raise RecoveryIdentityConflict("alert request policy differs from explicit configured revisions")
        work = self.repository.get_work(alert.work_id)
        if (
            alert.workspace_id != work.workspace_id
            or alert.source_id != work.source_id
            or alert.operation_id != work.operation_id
            or alert.case_id != work.case_id
            or alert.reason != work.reason
            or alert.state != work.state
            or alert.deadline != work.deadline
            or alert.target_alias != work.target_alias
            or alert.recipient_profile.owner_subject_id != work.owner_id
            or alert.recipient_profile.revision != work.recipient_profile_revision
        ):
            raise RecoveryIdentityConflict("alert request is not bound to the exact work source")

        with self.repository._connect() as connection:
            intent = connection.execute(
                """SELECT * FROM v4_production_recovery_events
                   WHERE event_id=%s AND work_id=%s AND event_kind='alert_intent'""",
                (alert.alert_event_id, alert.work_id),
            ).fetchone()
            if intent is None:
                raise RecoveryInventoryGap("alert request lacks its committed alert intent")
            due_at = self._validate_intent_tier_and_policy(
                connection, work, alert, intent
            )
            local_receipt = connection.execute(
                """SELECT * FROM v4_production_recovery_events
                   WHERE event_key=%s AND work_id=%s AND event_kind='delivery_receipt'""",
                (f"delivery-receipt:{alert.alert_event_id}", alert.work_id),
            ).fetchone()
            if local_receipt is not None:
                attempts, _retry_at, _pending_attempt, _pending_retry = (
                    self._validate_retry_history(connection, work, alert)
                )
                if attempts == 0:
                    raise RecoveryInventoryGap(
                        "persisted provider receipt lacks a committed delivery attempt"
                    )
                return self._receipt_from_event(self.repository._event(local_receipt), alert)

        key = alert.idempotency_key
        # Reconcile a provider acceptance before applying a retry delay or
        # requiring a new current delivery decision.
        try:
            readback = self.target.lookup(key)
        except Exception:
            readback = None
        if readback is not None:
            attempt_prefix = f"delivery-attempt:{alert.alert_event_id}:%"
            with self.repository._connect() as connection:
                attempts, _retry_at, _pending_attempt, _pending_retry = (
                    self._validate_retry_history(connection, work, alert)
                )
                if attempts == 0:
                    raise RecoveryInventoryGap(
                        "provider alert receipt lacks a committed exact delivery attempt"
                    )
                prior_attempts = connection.execute(
                    """SELECT event_key,occurred_at,policy_decision_id,payload
                       FROM v4_production_recovery_events
                       WHERE work_id=%s AND event_kind='delivery_attempt'
                         AND (event_key LIKE %s OR payload->>'event_key'=%s)
                       ORDER BY occurred_at,event_id""",
                    (work.work_id, attempt_prefix, key),
                ).fetchall()
                if not prior_attempts:
                    raise RecoveryInventoryGap(
                        "provider alert receipt lacks a committed exact delivery attempt"
                    )
                latest_attempt = prior_attempts[-1]
                latest_payload = latest_attempt["payload"]
                if (
                    not isinstance(latest_payload, Mapping)
                    or type(latest_payload.get("attempt")) is not int
                ):
                    raise RecoveryIdentityConflict(
                        "provider alert receipt has malformed delivery attempt evidence"
                    )
                recorded_alert = self._validate_attempt_event(
                    work,
                    alert,
                    latest_attempt,
                    attempt=latest_payload["attempt"],
                )
                event = self._receipt_event(
                    connection,
                    work,
                    recorded_alert,
                    readback,
                    policy_decision_id=latest_attempt["policy_decision_id"],
                )
                return self._receipt_from_event(event, recorded_alert)

        with self.repository._connect() as connection:
            held_receipt = connection.execute(
                """SELECT * FROM v4_production_recovery_events
                   WHERE event_key=%s AND work_id=%s AND event_kind='held_conflict'""",
                (f"held-alert:{alert.alert_event_id}", alert.work_id),
            ).fetchone()
            if held_receipt is not None:
                self._validate_retry_history(connection, work, alert)
                return self._receipt_from_event(self.repository._event(held_receipt), alert)

        with self.repository._connect() as connection:
            (
                attempts,
                retry_at,
                pending_attempt,
                outstanding_retry_request,
            ) = self._validate_retry_history(connection, work, alert)
        if pending_attempt is not None:
            pending_payload = pending_attempt["payload"]
            assert isinstance(pending_payload, Mapping)
            pending_number = pending_payload["attempt"]
            pending_alert = self._validate_attempt_event(
                work, alert, pending_attempt, attempt=pending_number
            )
            if retry_at is not None:
                self._wait_until_retry_at(retry_at)
            self._wait_until_attempt_claim_expires(
                pending_attempt["occurred_at"], alert.retry_policy.claim_ttl
            )
            try:
                pending_readback = self.target.lookup(key)
            except Exception:
                pending_readback = None
            if pending_readback is not None:
                with self.repository._connect() as connection:
                    event = self._receipt_event(
                        connection,
                        work,
                        pending_alert,
                        pending_readback,
                        policy_decision_id=pending_attempt["policy_decision_id"],
                    )
                    return self._receipt_from_event(event, pending_alert)

            self._require_escalation_due(due_at)
            # A committed attempt binds the exact recipient request. Reauthorize
            # and resend that request after its claim TTL; a tier change can only
            # take effect at the following retry boundary.
            current = self.authority.authorize_alert(work, pending_alert)
            if not isinstance(current, ProductionRecoveryDecision) or not current.allowed:
                raise PermissionError("alert authority changed before resuming a delivery attempt")
            advanced_to_next_tier = False
            if pending_alert.recipient_tier == "owner" and not self._owner_is_current(work):
                if not pending_alert.recipient_profile.on_call_group_ids:
                    return self._held_receipt(
                        work,
                        pending_alert,
                        reason_code="owner_unavailable_no_backup",
                        policy_decision_id=current.decision_id,
                    )
                if pending_number >= pending_alert.retry_policy.maximum_attempts:
                    return self._held_receipt(
                        work,
                        pending_alert,
                        reason_code="owner_unavailable_attempts_exhausted",
                        policy_decision_id=current.decision_id,
                    )
                next_alert = replace(pending_alert, recipient_tier="on_call")
                with self.repository._connect() as connection:
                    self._record_owner_unavailable(
                        connection,
                        work,
                        pending_alert,
                        "owner_unavailable_escalated",
                        current.decision_id,
                    )
                self._schedule_retry(
                    work,
                    next_alert,
                    attempt=pending_number,
                    policy_decision_id=pending_attempt["policy_decision_id"],
                )
                alert = next_alert
                advanced_to_next_tier = True

            else:
                alert = pending_alert
            if not advanced_to_next_tier:
                receipt = self._send_and_lookup(pending_alert, key)
                if receipt is not None and receipt.status == "accepted":
                    with self.repository._connect() as connection:
                        event = self._receipt_event(
                            connection,
                            work,
                            pending_alert,
                            receipt,
                            policy_decision_id=current.decision_id,
                        )
                        return self._receipt_from_event(event, pending_alert)
                if pending_number < pending_alert.retry_policy.maximum_attempts:
                    self._schedule_retry(
                        work,
                        pending_alert,
                        attempt=pending_number,
                        policy_decision_id=pending_attempt["policy_decision_id"],
                    )
        else:
            if outstanding_retry_request is not None:
                alert = outstanding_retry_request
            decision = self.authority.authorize_alert(work, alert)
            if not isinstance(decision, ProductionRecoveryDecision) or not decision.allowed:
                raise PermissionError("current alert delivery authority denied")
            if (
                outstanding_retry_request is not None
                and alert.recipient_tier == "owner"
                and not self._owner_is_current(work)
            ):
                return self._held_receipt(
                    work,
                    alert,
                    reason_code="owner_unavailable_retry_tier",
                    policy_decision_id=decision.decision_id,
                )
            if (
                outstanding_retry_request is None
                and alert.recipient_tier == "owner"
                and not self._owner_is_current(work)
            ):
                if not alert.recipient_profile.on_call_group_ids:
                    return self._held_receipt(
                        work,
                        alert,
                        reason_code="owner_unavailable_no_backup",
                        policy_decision_id=decision.decision_id,
                    )
                with self.repository._connect() as connection:
                    self._record_owner_unavailable(
                        connection,
                        work,
                        alert,
                        "owner_unavailable_escalated",
                        decision.decision_id,
                    )
                alert = replace(alert, recipient_tier="on_call")
            self._require_escalation_due(due_at)
            if retry_at is not None:
                self._wait_until_retry_at(retry_at)
        next_attempt = attempts + 1
        for attempt in range(next_attempt, alert.retry_policy.maximum_attempts + 1):
            self._require_escalation_due(due_at)
            current = self.authority.authorize_alert(work, alert)
            if not isinstance(current, ProductionRecoveryDecision) or not current.allowed:
                raise PermissionError("alert authority changed before a delivery attempt")
            if alert.recipient_tier == "owner" and not self._owner_is_current(work):
                if not alert.recipient_profile.on_call_group_ids:
                    return self._held_receipt(
                        work,
                        alert,
                        reason_code="owner_unavailable_no_backup",
                        policy_decision_id=current.decision_id,
                    )
                with self.repository._connect() as connection:
                    self._record_owner_unavailable(
                        connection,
                        work,
                        alert,
                        "owner_unavailable_escalated",
                        current.decision_id,
                    )
                alert = replace(alert, recipient_tier="on_call")
            payload = self._event_payload(
                work, alert, status="attempting", attempt=attempt
            )
            with self.repository._connect() as connection:
                self.repository._append_event(
                    connection,
                    work,
                    event_key=f"delivery-attempt:{alert.alert_event_id}:{attempt}",
                    event_kind="delivery_attempt",
                    policy_decision_id=current.decision_id,
                    payload=payload,
                )
            receipt = self._send_and_lookup(alert, key)
            if receipt is not None and receipt.status == "accepted":
                with self.repository._connect() as connection:
                    event = self._receipt_event(
                        connection,
                        work,
                        alert,
                        receipt,
                        policy_decision_id=current.decision_id,
                    )
                    return self._receipt_from_event(event, alert)
            if attempt < alert.retry_policy.maximum_attempts:
                self._schedule_retry(
                    work,
                    alert,
                    attempt=attempt,
                    policy_decision_id=current.decision_id,
                )
        with self.repository._connect() as connection:
            last_decision = self.authority.authorize_alert(work, alert)
            if not isinstance(last_decision, ProductionRecoveryDecision) or not last_decision.allowed:
                raise PermissionError("current alert authority denied held evidence")
            self.repository._append_event(
                connection,
                work,
                event_key=f"held-alert:{alert.alert_event_id}",
                event_kind="held_conflict",
                policy_decision_id=last_decision.decision_id,
                payload=self._event_payload(
                    work,
                    alert,
                    status="held",
                    attempt=alert.retry_policy.maximum_attempts,
                    error_code="retry_exhausted",
                ),
            )
        return ProductionAlertReceipt(
            alert_event_id=alert.alert_event_id,
            idempotency_key=key,
            provider_message_id=None,
            status="held",
            accepted_at=None,
        )

    def lookup(self, idempotency_key: str) -> ProductionAlertReceipt | None:
        if not isinstance(idempotency_key, str) or not idempotency_key:
            raise ValueError("stable alert idempotency key required")
        return self.target.lookup(idempotency_key)

    def acknowledge(
        self, command: ProductionAlertAcknowledgment
    ) -> ProductionRecoveryEvent:
        if not isinstance(command, ProductionAlertAcknowledgment):
            raise ValueError("typed production alert acknowledgment required")
        work = self.repository.get_work(command.work_id)
        if work.source_id != command.source_id:
            raise ValueError("acknowledgment source differs from exact work binding")
        with self.repository._connect() as connection:
            intent = connection.execute(
                """SELECT event.* FROM v4_production_recovery_events event
                   WHERE event.event_id=%s AND event.work_id=%s
                     AND event.event_kind='alert_intent'""",
                (command.alert_event_id, work.work_id),
            ).fetchone()
            receipt = connection.execute(
                """SELECT event.* FROM v4_production_recovery_events event
                   WHERE event.event_key=%s AND event.work_id=%s
                     AND event.event_kind='delivery_receipt'
                     AND event.payload->>'delivery_id'=%s""",
                (
                    f"delivery-receipt:{command.alert_event_id}",
                    work.work_id,
                    command.delivery_id,
                ),
            ).fetchone()
            if intent is None or receipt is None:
                raise ValueError("acknowledgment must bind the exact alert intent and provider receipt")
            subject = connection.execute(
                """SELECT workspace_id,enabled,expires_at
                   FROM identity_subjects WHERE id=%s""",
                (command.actor_id,),
            ).fetchone()
            if (
                subject is None
                or subject["workspace_id"] != work.workspace_id
                or not subject["enabled"]
                or subject["expires_at"] <= connection.execute(
                    "SELECT clock_timestamp() AS now"
                ).fetchone()["now"]
            ):
                raise PermissionError("acknowledgment actor must be a current same-workspace identity")
            payload = {
                "schema_version": "salience.p1-production-recovery-ack.v1",
                "status": "received",
                "work_id": str(work.work_id),
                "source_id": str(work.source_id),
                "intent_id": str(work.intent_id),
                "delivery_id": command.delivery_id,
                "acknowledged_by": str(command.actor_id),
                "event_key": command.idempotency_key,
                "evidence_sha256": sha256(
                    f"{command.alert_event_id}:{command.delivery_id}:{command.actor_id}".encode("utf-8")
                ).hexdigest(),
            }
            if work.operation_id is not None:
                payload["operation_id"] = str(work.operation_id)
            if work.case_id is not None:
                payload["case_id"] = str(work.case_id)
            prior = connection.execute(
                "SELECT * FROM v4_production_recovery_events WHERE event_key=%s",
                (command.idempotency_key,),
            ).fetchone()
            if prior is not None:
                if prior["work_id"] != work.work_id or prior["payload"] != payload:
                    raise RecoveryEventConflict("acknowledgment idempotency key conflicts")
                return self.repository._event(prior)
        decision = self.authority.authorize_acknowledgment(work, command)
        if not isinstance(decision, ProductionRecoveryDecision) or not decision.allowed:
            raise PermissionError("current acknowledgment authority denied")
        with self.repository._connect() as connection:
            return self.repository._append_event(
                connection,
                work,
                event_key=command.idempotency_key,
                event_kind="acknowledgment",
                policy_decision_id=decision.decision_id,
                payload=payload,
                actor_id=command.actor_id,
            )
