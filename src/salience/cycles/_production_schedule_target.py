"""Remote readback and pause/resume logic for one bound schedule target."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from uuid import NAMESPACE_URL, UUID, uuid5

from salience.cycles._production_schedule_contracts import (
    ProductionScheduleBinding, TemporalScheduleReadback, _aware,
    _local_evidence_sha256, _text, _time_text,
)

class _ProductionScheduleTargetAdapter:
    """Mixin that limits remote mutation to bound pause/resume operations."""

    @staticmethod
    def _last_slot_precedes(metadata, first_v4_slot):
        last_slot = metadata.get("last_slot")
        if last_slot is None:
            return metadata.get("last_slot_is_boundary") is True
        return (
            last_slot.tzinfo is not None
            and last_slot.astimezone(timezone.utc) < first_v4_slot
        )


    def _schedule(self, connection, schedule_id, *, lock=False):
        from salience.cycles.native_schedules import schedule_row, schedule_metadata

        row = schedule_row(
            connection,
            schedule_id,
            self.workspace_id,
            lock=lock,
            temporal=True,
        )
        native = row.get("native_source") or row.get("native_publication_source")
        if native is None:
            raise PermissionError("production controller requires an original native source")
        binding = self._binding_row(connection, schedule_id=schedule_id)
        resolved = self._binding_value(binding)
        metadata = schedule_metadata(row)
        if (
            resolved.remote_schedule_id != metadata["remote_id"]
            or resolved.goal_id != native["goal_id"]
            or resolved.source_actor_id != native["actor_id"]
            or resolved.goal_revision != native["goal_revision"]
        ):
            raise PermissionError("exact finalized native production source binding required")
        return row


    def _require_rollback_watermark(self, connection, binding, after_slot):
        cursor = connection.execute(
            """SELECT last_slot FROM public.v4_schedule_cursors
               WHERE goal_id=%s AND goal_revision=%s FOR SHARE""",
            (binding.goal_id, binding.goal_revision),
        ).fetchone()
        expected = (
            cursor["last_slot"] if cursor is not None
            else binding.first_v4_slot - timedelta(seconds=binding.interval_seconds)
        )
        if after_slot != expected:
            raise PermissionError("rollback watermark must match the canonical cursor")

    @staticmethod
    def _is_later_cadence_slot(local_next, remote_next, interval_seconds):
        return (
            local_next is not None
            and remote_next is not None
            and remote_next >= local_next
            and (remote_next - local_next) % timedelta(seconds=interval_seconds) == timedelta(0)
        )

    @staticmethod
    def _require_source_owner(connection, binding):
        owner = connection.execute(
            """SELECT 1 FROM public.identity_subjects
               WHERE id=%s AND workspace_id=%s AND enabled IS TRUE
                 AND expires_at>clock_timestamp() FOR SHARE""",
            (binding.source_owner_subject_id, binding.workspace_id),
        ).fetchone()
        if owner is None:
            raise PermissionError("current enabled bound source owner required")

    def _unresolved_schedule_work(self, connection, binding):
        return bool(connection.execute(
            """SELECT 1 FROM public.v4_legacy_dispatches dispatch
               JOIN public.jobs job ON job.id=dispatch.job_id
               WHERE dispatch.workspace_id=%(workspace)s AND dispatch.goal_id=%(goal)s
                 AND job.state NOT IN ('succeeded','cancelled','dead_lettered','timed_out')
               UNION ALL
               SELECT 1 FROM public.v4_legacy_publication_recovery_holds hold
               WHERE hold.workspace_id=%(workspace)s AND hold.goal_id=%(goal)s
               UNION ALL
               SELECT 1 FROM public.v4_production_recovery_work work
               WHERE work.workspace_id=%(workspace)s AND work.goal_id=%(goal)s
                 AND (work.source_discriminant='production_publication_hold' OR EXISTS (
                     SELECT 1 FROM public.v4_recovery_cases recovery
                     WHERE work.source_discriminant='cycle_case' AND work.source_id=recovery.id
                       AND recovery.failure_class='unknown_effect'
                       AND recovery.state NOT IN ('terminal','resolved')))
               UNION ALL
               SELECT 1 FROM public.v4_production_effect_permits permit
               LEFT JOIN public.v4_production_current_dispatch dispatch
                 ON dispatch.operation_id=permit.operation_id
               LEFT JOIN public.v4_production_effect_permit_claims claim
                 ON (claim.permit_id,claim.operation_id,claim.generation)
                  = (permit.permit_id,permit.operation_id,permit.generation)
               LEFT JOIN public.external_effects effect
                 ON effect.id::text=permit.request_snapshot->>'external_effect_id'
                AND effect.workspace_id=permit.workspace_id
                AND effect.job_id::text=permit.request_snapshot->>'job_id'
               WHERE permit.workspace_id=%(workspace)s AND permit.goal_id=%(goal)s
                 -- A newer generation requires authoritative same-operation no-effect proof.
                 AND (dispatch.generation IS NULL OR dispatch.generation<=permit.generation)
                 AND (claim.claim_id IS NOT NULL OR permit.expires_at>clock_timestamp()
                      OR effect.status IN ('submitting','unknown','accepted','completed'))
                 AND NOT EXISTS (
                     SELECT 1 FROM public.v4_cycle_events event
                     WHERE claim.claim_id IS NULL AND effect.status='cancelled'
                       AND effect.attempted_at IS NULL AND event.cycle_id=permit.cycle_id
                       AND event.kind='production_publication_cancelled_before_submit'
                       AND event.payload->>'operation_id'=permit.operation_id::text
                       AND event.payload->>'permit_id'=permit.permit_id::text
                       AND event.payload->>'external_effect_id'=effect.id::text)
                 AND NOT EXISTS (
                     SELECT 1 FROM public.v4_cycles cycle
                     JOIN public.publication_attempts attempt
                       ON attempt.id::text=permit.request_snapshot->>'publication_attempt_id'
                     JOIN public.publication_plans plan ON plan.id=attempt.publication_plan_id
                     JOIN public.remote_publication_receipts receipt
                       ON receipt.publication_attempt_id=attempt.id
                     JOIN public.publications publication
                       ON publication.remote_publication_receipt_id=receipt.id
                      AND publication.publication_request_id=plan.publication_request_id
                     WHERE cycle.id=permit.cycle_id AND cycle.operation_id=permit.operation_id
                       AND cycle.state='closed' AND cycle.disposition='completed'
                       AND plan.id::text=permit.request_snapshot->>'publication_plan_id'
                       AND plan.publication_request_id::text=permit.request_snapshot->>'publication_request_id'
                       AND plan.external_effect_id=effect.id AND effect.status='completed'
                       AND receipt.publisher_id=permit.provider_id
                       AND effect.provider_reference=receipt.remote_id
                       AND publication.state='published')
               LIMIT 1""",
            {"workspace": binding.workspace_id, "goal": binding.goal_id},
        ).fetchone())

    def _assert_current_action_source(self, connection, binding, *, action, after_slot=None):
        """Hold exact local source/lifecycle facts before a remote action."""
        from salience.cycles.native_schedules import schedule_metadata

        # Match the state machine's goal-before-schedule lock order.
        goal = connection.execute(
            """SELECT revision,state FROM public.v4_goals
               WHERE id=%s AND workspace_id=%s FOR SHARE""",
            (binding.goal_id, binding.workspace_id),
        ).fetchone()
        if goal is None or goal["revision"] != binding.goal_revision or goal["state"] != "active":
            raise PermissionError("current bound active goal revision required")
        schedule = self._schedule(connection, binding.schedule_id, lock=True)
        status = schedule["status"]
        if action == "pause":
            if status not in {"active", "paused"}:
                raise PermissionError("invalid local schedule status for pause")
            required_cutover_states = {"pending", "active"}
        elif action == "resume_after":
            if status not in {"active", "paused"}:
                raise PermissionError("invalid local schedule status for resume")
            required_cutover_states = {"rollback_pending", "rolled_back"} if status == "active" else {"rollback_pending"}
        else:
            raise ValueError("bounded production schedule action required")

        rows = self._local_source(
            connection,
            binding.remote_schedule_id,
            schedule_statuses=(status,),
        )
        expected = (
            binding.source_kind,
            binding.schedule_id,
            binding.publication_schedule_id,
            binding.remote_schedule_id,
            binding.workspace_id,
            binding.goal_id,
            binding.goal_revision,
            binding.source_actor_id,
            binding.interval_seconds,
            binding.first_v4_slot,
        )
        if len(rows) != 1 or (
            rows[0]["source_kind"],
            rows[0]["schedule_id"],
            rows[0]["publication_schedule_id"],
            rows[0]["remote_id"],
            rows[0]["workspace_id"],
            rows[0]["goal_id"],
            rows[0]["goal_revision"],
            rows[0]["source_actor_id"],
            rows[0]["interval_seconds"],
            rows[0]["first_v4_slot"],
        ) != expected:
            raise PermissionError("current active bound schedule source and goal revision required")

        self._require_source_owner(connection, binding)

        cutover = connection.execute(
            """SELECT state,goal_revision,legacy_schedule_id,actor_id,first_v4_slot,
                      rollback_after_slot
               FROM public.v4_schedule_cutovers
               WHERE goal_id=%s AND workspace_id=%s FOR SHARE""",
            (binding.goal_id, binding.workspace_id),
        ).fetchone()
        if cutover is None or (
            cutover["state"],
            cutover["goal_revision"],
            cutover["legacy_schedule_id"],
            cutover["actor_id"],
            cutover["first_v4_slot"],
        )[1:] != (
            binding.goal_revision,
            binding.schedule_id,
            binding.schedule_operator_subject_id,
            binding.first_v4_slot,
        ) or cutover["state"] not in required_cutover_states:
            raise PermissionError("exact pending or rollback-pending cutover source required")

        if binding.publication_schedule_id is not None:
            publication = connection.execute(
                """SELECT id FROM public.publication_schedules
                   WHERE id=%s AND job_schedule_id=%s AND status='scheduled'
                   FOR SHARE""",
                (binding.publication_schedule_id, binding.schedule_id),
            ).fetchone()
            if publication is None:
                raise PermissionError("current bound publication schedule required")

        if action == "pause":
            stop_fence = self._assert_binding(
                connection, binding, action_kind="schedule_pause"
            )
            if cutover["state"] == "active" and (
                status != "paused" or stop_fence is None
            ):
                raise PermissionError(
                    "locally paused predecessor and committed stop required for active cutover"
                )
            return stop_fence

        if after_slot is None or cutover["rollback_after_slot"] != after_slot:
            raise PermissionError("exact current rollback watermark required before remote resume")
        self._require_rollback_watermark(connection, binding, after_slot)
        if status == "active":
            metadata = schedule_metadata(schedule)
            if (
                metadata["resume_after"] != after_slot.isoformat()
                or metadata["next_run_at"] is None
                or metadata["next_run_at"] <= after_slot
            ):
                raise PermissionError("active legacy schedule lacks exact rollback watermark readback")
        self._assert_binding(connection, binding, action_kind="schedule_resume")
        from salience.cycles.schedule_cutover import CycleScheduleCutover

        if (
            CycleScheduleCutover._unresolved(connection, binding.goal_id, binding.first_v4_slot)
            or self._unresolved_schedule_work(connection, binding)
        ):
            raise ValueError("unresolved schedule work holds legacy resume")
        return status


    def _command_key(self, binding: ProductionScheduleBinding, action: str, suffix: str = "") -> str:
        return str(
            uuid5(
                NAMESPACE_URL,
                "salience.p1-production-schedule-command.v1:"
                + ":".join(
                    (
                        binding.binding_fingerprint,
                        binding.remote_schedule_id,
                        action,
                        suffix,
                    )
                ),
            )
        )


    @staticmethod
    def _stop_pause_command_suffix(stop_fence) -> str:
        if stop_fence is None:
            return ""
        material = json.dumps(
            stop_fence, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return "stop:" + sha256(material).hexdigest()


    def _hold(self, binding: ProductionScheduleBinding, reason_code: str, evidence: object) -> None:
        self.alerts.emit_hold(
            binding.workspace_id,
            binding.schedule_id,
            reason_code,
            _local_evidence_sha256(evidence),
        )


    def _verify_readback(
        self,
        binding: ProductionScheduleBinding,
        readback: TemporalScheduleReadback,
    ) -> None:
        snapshot = binding.member_snapshot
        expected = (
            binding.remote_schedule_id,
            binding.target_alias,
            binding.namespace,
            snapshot["workflow_type"],
            snapshot["action_id"],
            snapshot["argument_sha256"],
            snapshot["task_queue"],
            binding.route_id,
            binding.route_tuple_sha256,
            binding.slot_id,
            binding.owner_id,
            binding.fencing_epoch,
            binding.route_manifest_sha256,
            binding.artifact_lock_sha256,
            snapshot["cadence_policy_revision"],
            binding.interval_seconds,
            _aware(binding.first_v4_slot, "first V4 slot"),
        )
        actual = (
            readback.remote_schedule_id,
            readback.target_alias,
            readback.namespace,
            readback.workflow_type,
            readback.action_id,
            readback.argument_sha256,
            readback.task_queue,
            readback.route_id,
            readback.route_tuple_sha256,
            readback.slot_id,
            readback.owner_id,
            readback.fencing_epoch,
            readback.route_manifest_sha256,
            readback.artifact_lock_sha256,
            readback.cadence_policy_revision,
            readback.interval_seconds,
            _aware(readback.first_v4_slot, "readback first V4 slot"),
        )
        if actual != expected:
            evidence = {
                "expected_remote_schedule_id": binding.remote_schedule_id,
                "actual_remote_schedule_id": readback.remote_schedule_id,
                "expected_target_alias": binding.target_alias,
                "actual_target_alias": readback.target_alias,
                "expected_namespace": binding.namespace,
                "actual_namespace": readback.namespace,
                "expected_member_sha256": binding.member_sha256,
                "actual_route_id": str(readback.route_id),
                "actual_route_tuple_sha256": readback.route_tuple_sha256,
                "actual_slot_id": str(readback.slot_id),
                "actual_owner_id": str(readback.owner_id),
                "actual_fencing_epoch": readback.fencing_epoch,
                "actual_route_manifest_sha256": readback.route_manifest_sha256,
                "actual_artifact_lock_sha256": readback.artifact_lock_sha256,
            }
            self._hold(binding, "schedule_source_or_route_drift", evidence)
            raise PermissionError("exact target, source action, and route readback required")


    def _readback(
        self,
        binding: ProductionScheduleBinding,
        *,
        action_kind: str = "schedule_readback",
    ) -> TemporalScheduleReadback:
        self.assert_binding(binding, action_kind=action_kind)
        try:
            readback = self.target.describe(binding.remote_schedule_id)
        except Exception as error:
            self._hold(
                binding,
                "schedule_readback_unknown",
                {"remote_schedule_id": binding.remote_schedule_id, "error_type": type(error).__name__},
            )
            raise PermissionError("exact target schedule readback is unavailable") from error
        if not isinstance(readback, TemporalScheduleReadback):
            self._hold(
                binding,
                "schedule_readback_unknown",
                {"remote_schedule_id": binding.remote_schedule_id, "response_type": type(readback).__name__},
            )
            raise PermissionError("typed target schedule readback is required")
        self._verify_readback(binding, readback)
        return readback


    def describe(self, schedule_id):
        binding = self.binding_for_schedule(UUID(str(schedule_id)))
        readback = self._readback(binding)
        with self._connect() as connection:
            connection.execute("SET LOCAL statement_timeout='3s'")
            cutover = connection.execute(
                """SELECT state,rollback_after_slot,schedule.status AS legacy_status,
                          schedule.next_run_at AS legacy_next,clock_timestamp() AS now
                   FROM public.v4_schedule_cutovers cutover
                   JOIN public.job_schedules schedule
                     ON schedule.id=cutover.legacy_schedule_id
                    AND schedule.workspace_id=cutover.workspace_id
                   WHERE cutover.goal_id=%s AND cutover.workspace_id=%s""",
                (binding.goal_id, binding.workspace_id),
            ).fetchone()
        if cutover and cutover["state"] == "active":
            if not readback.paused or readback.running_action_count != 0:
                self._hold(
                    binding,
                    "schedule_fence_violation",
                    {
                        "state": "active",
                        "paused": readback.paused,
                        "running_action_count": readback.running_action_count,
                        "last_slot": _time_text(readback.last_slot, "last slot"),
                    },
                )
                raise PermissionError("active V4 cutover requires a paused drained legacy schedule")
            if readback.last_slot is not None and _aware(
                readback.last_slot, "last slot"
            ) >= binding.first_v4_slot:
                self._hold(
                    binding,
                    "schedule_slot_overlap",
                    {"last_slot": _time_text(readback.last_slot, "last slot")},
                )
                raise PermissionError("legacy schedule action crossed the first V4 slot")
        if cutover and cutover["state"] in {"rollback_pending", "rolled_back"}:
            watermark = cutover["rollback_after_slot"]
            reconciled = (
                not readback.paused
                and (cutover["state"] == "rolled_back" or readback.running_action_count == 0)
                and readback.resume_after is not None
                and _aware(readback.resume_after, "resume watermark") == watermark
                and readback.next_action is not None
                and _aware(readback.next_action, "next action") > max(watermark, cutover["now"])
                and (
                    cutover["legacy_status"] != "active"
                    or self._is_later_cadence_slot(
                        cutover["legacy_next"], readback.next_action, binding.interval_seconds
                    )
                )
            )
            if readback.paused is False and not reconciled:
                self._hold(
                    binding,
                    "schedule_resume_readback_unknown",
                    {
                        "state": cutover["state"],
                        "resume_after": _time_text(readback.resume_after, "resume watermark"),
                        "next_action": _time_text(readback.next_action, "next action"),
                    },
                )
                raise PermissionError("out-of-band schedule resume cannot clear rollback fence")
        return {
            "paused": readback.paused,
            "remote_id": readback.remote_schedule_id,
            "interval_seconds": readback.interval_seconds,
            "next_run_at": readback.next_action,
            "last_slot": readback.last_slot,
            "last_slot_is_boundary": (
                readback.last_slot is None
                and binding.member_snapshot["history_action_count"] == 0
            ),
            "resume_after": (
                _time_text(readback.resume_after, "resume watermark")
                if readback.resume_after is not None
                else None
            ),
        }


    def pause(self, schedule_id):
        schedule_id = UUID(str(schedule_id))
        binding = self.binding_for_schedule(schedule_id)
        command_key = None
        mutation_error = None
        with self._connect() as connection:
            connection.execute("SET LOCAL statement_timeout='3s'")
            stop_fence = self._assert_current_action_source(
                connection, binding, action="pause"
            )
            command_key = self._command_key(
                binding,
                "pause",
                self._stop_pause_command_suffix(stop_fence),
            )
            # Read the exact remote schedule before the command. This prevents
            # an out-of-band source or route change from being discovered only
            # after a pause has already been sent.
            self._readback(binding, action_kind="schedule_pause")
            try:
                self.target.pause(binding.remote_schedule_id, command_key)
            except Exception as error:
                mutation_error = error
        try:
            readback = self._readback(binding)
        except Exception as error:
            self._hold(
                binding,
                "schedule_pause_reconciliation_unknown",
                {
                    "remote_schedule_id": binding.remote_schedule_id,
                    "command_key": command_key,
                    "mutation_error": type(mutation_error).__name__ if mutation_error else None,
                    "readback_error": type(error).__name__,
                },
            )
            raise PermissionError("pause acknowledgment has no exact schedule readback") from error
        if (
            not readback.paused
            or readback.running_action_count != 0
            or not self._last_slot_precedes(
                {"last_slot": readback.last_slot,
                 "last_slot_is_boundary": readback.last_slot is None
                 and binding.member_snapshot["history_action_count"] == 0},
                binding.first_v4_slot,
            )
        ):
            self._hold(
                binding,
                "schedule_pause_not_drained",
                {
                    "remote_schedule_id": binding.remote_schedule_id,
                    "command_key": command_key,
                    "paused": readback.paused,
                    "running_action_count": readback.running_action_count,
                    "last_slot": _time_text(readback.last_slot, "last slot"),
                },
            )
            raise PermissionError("paused predecessor readback with drained actions required") from mutation_error
        # The local status is only a mirror consumed by the existing cutover
        # state machine. It changes after target readback proves the pause.
        super().pause(schedule_id)


    def resume_after(self, schedule_id, after_slot, *, subject_id=None):
        schedule_id = UUID(str(schedule_id))
        after_slot = _aware(after_slot, "rollback watermark")
        binding = self.binding_for_schedule(schedule_id)
        if subject_id is not None and UUID(str(subject_id)) != binding.schedule_operator_subject_id:
            raise PermissionError("exact bound schedule operator required before remote resume")
        command_key = self._command_key(binding, "resume_after", after_slot.isoformat())
        mutation_error = None
        with self._connect() as connection:
            connection.execute("SET LOCAL statement_timeout='3s'")
            status = self._assert_current_action_source(
                connection, binding, action="resume_after", after_slot=after_slot
            )
            before = self._readback(binding, action_kind="schedule_resume")
            if before.running_action_count:
                self._hold(
                    binding,
                    "schedule_remote_liability",
                    {
                        "remote_schedule_id": binding.remote_schedule_id,
                        "running_action_count": before.running_action_count,
                        "watermark": after_slot.isoformat(),
                    },
                )
                raise PermissionError("unresolved remote schedule actions hold rollback")
            if (status == "active" or not before.paused) and not (
                not before.paused
                and before.resume_after is not None
                and _aware(before.resume_after, "resume watermark") == after_slot
                and before.next_action is not None
                and _aware(before.next_action, "next action") > after_slot
            ):
                self._hold(
                    binding,
                    "schedule_resume_precondition_unknown",
                    {"paused": before.paused, "resume_after": _time_text(before.resume_after, "resume watermark")},
                )
                raise PermissionError("exact paused schedule or prior resume reconciliation required")
            if before.paused:
                if not self._last_slot_precedes(
                    {"last_slot": before.last_slot,
                     "last_slot_is_boundary": before.last_slot is None
                     and binding.member_snapshot["history_action_count"] == 0},
                    binding.first_v4_slot,
                ):
                    self._hold(
                        binding, "schedule_resume_predecessor_not_fenced",
                        {"last_slot": _time_text(before.last_slot, "last slot"),
                         "first_v4_slot": binding.first_v4_slot.isoformat()},
                    )
                    raise PermissionError("exact paused predecessor readback required before resume")
                try:
                    self.target.resume_after(
                        binding.remote_schedule_id, command_key, after_slot
                    )
                except Exception as error:
                    mutation_error = error
        try:
            readback = self._readback(binding)
        except Exception as error:
            self._hold(
                binding,
                "schedule_resume_reconciliation_unknown",
                {
                    "remote_schedule_id": binding.remote_schedule_id,
                    "command_key": command_key,
                    "watermark": after_slot.isoformat(),
                    "mutation_error": type(mutation_error).__name__ if mutation_error else None,
                    "readback_error": type(error).__name__,
                },
            )
            raise PermissionError("resume acknowledgment has no exact schedule readback") from error
        if (
            readback.paused
            or readback.running_action_count != 0
            or readback.resume_after is None
            or _aware(readback.resume_after, "resume watermark") != after_slot
            or readback.next_action is None
            or _aware(readback.next_action, "next action") <= after_slot
        ):
            self._hold(
                binding,
                "schedule_resume_readback_invalid",
                {
                    "remote_schedule_id": binding.remote_schedule_id,
                    "command_key": command_key,
                    "watermark": after_slot.isoformat(),
                    "paused": readback.paused,
                    "resume_after": _time_text(readback.resume_after, "resume watermark"),
                    "next_action": _time_text(readback.next_action, "next action"),
                },
            )
            raise PermissionError("later exact rollback slot readback required") from mutation_error
        from salience.cycles.schedule_cutover import _ScheduleResumeSlotMismatch

        try:
            super().resume_after(
                schedule_id, after_slot, subject_id=subject_id, _verified_next_slot=readback.next_action
            )
        except _ScheduleResumeSlotMismatch as error:
            self._hold(
                binding, "schedule_resume_local_remote_mismatch",
                {"local_next_action": _time_text(error.expected_slot, "local next action"),
                 "remote_next_action": _time_text(readback.next_action, "remote next action")},
            )
            raise
        with self._connect() as connection:
            row = self._schedule(connection, schedule_id)
            local_next = row["next_run_at"]
        if local_next != readback.next_action and not self._is_later_cadence_slot(
            local_next, readback.next_action, binding.interval_seconds
        ):
            self._hold(
                binding,
                "schedule_resume_local_remote_mismatch",
                {
                    "local_next_action": _time_text(local_next, "local next action"),
                    "remote_next_action": _time_text(readback.next_action, "next action"),
                },
            )
            raise PermissionError("local and remote resumed schedule slots must match")
