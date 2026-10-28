"""Remote readback and pause/resume logic for one bound schedule target."""

from __future__ import annotations

from datetime import datetime, timezone
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


    def _assert_current_action_source(self, connection, binding, *, action):
        """Hold exact local source/lifecycle facts before a remote action."""
        # Keep the active local schedule identity stable until the remote
        # command returns. The fixture mirror update runs after this transaction.
        schedule = self._schedule(connection, binding.schedule_id, lock=True)
        status = schedule["status"]
        if action == "pause":
            if status not in {"active", "paused"}:
                raise PermissionError("invalid local schedule status for pause")
            required_cutover_state = "pending"
        elif action == "resume_after":
            if status != "paused":
                raise PermissionError("invalid local schedule status for resume")
            required_cutover_state = "rollback_pending"
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

        goal = connection.execute(
            """SELECT id,workspace_id,revision,state FROM public.v4_goals
               WHERE id=%s AND workspace_id=%s FOR SHARE""",
            (binding.goal_id, binding.workspace_id),
        ).fetchone()
        if (
            goal is None
            or goal["revision"] != binding.goal_revision
            or goal["state"] != "active"
        ):
            raise PermissionError("current bound active goal revision required")

        owner = connection.execute(
            """SELECT id,workspace_id,enabled FROM public.identity_subjects
               WHERE id=%s FOR SHARE""",
            (binding.source_owner_subject_id,),
        ).fetchone()
        if (
            owner is None
            or owner["workspace_id"] != binding.workspace_id
            or owner["enabled"] is not True
        ):
            raise PermissionError("current enabled bound source owner required")

        cutover = connection.execute(
            """SELECT state,goal_revision,legacy_schedule_id,actor_id,first_v4_slot
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
        ) != (
            required_cutover_state,
            binding.goal_revision,
            binding.schedule_id,
            binding.schedule_operator_subject_id,
            binding.first_v4_slot,
        ):
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
                "SELECT state,rollback_after_slot FROM public.v4_schedule_cutovers "
                "WHERE goal_id=%s AND workspace_id=%s",
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
                and readback.resume_after is not None
                and _aware(readback.resume_after, "resume watermark") == watermark
                and readback.next_action is not None
                and _aware(readback.next_action, "next action") > watermark
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
        command_key = self._command_key(binding, "pause")
        mutation_error = None
        with self._connect() as connection:
            connection.execute("SET LOCAL statement_timeout='3s'")
            self._assert_current_action_source(connection, binding, action="pause")
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
        command_key = self._command_key(binding, "resume_after", after_slot.isoformat())
        mutation_error = None
        with self._connect() as connection:
            connection.execute("SET LOCAL statement_timeout='3s'")
            self._assert_current_action_source(
                connection, binding, action="resume_after"
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
            if not before.paused and not (
                before.resume_after is not None
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
        super().resume_after(schedule_id, after_slot, subject_id=subject_id)
        with self._connect() as connection:
            row = self._schedule(connection, schedule_id)
            local_next = row["next_run_at"]
        if local_next != readback.next_action:
            self._hold(
                binding,
                "schedule_resume_local_remote_mismatch",
                {
                    "local_next_action": _time_text(local_next, "local next action"),
                    "remote_next_action": _time_text(readback.next_action, "next action"),
                },
            )
            raise PermissionError("local and remote resumed schedule slots must match")
