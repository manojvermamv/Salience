"""Production-bound facade over the existing durable cutover state machine."""

from __future__ import annotations

from uuid import UUID

from salience.cycles.schedule_cutover import CycleScheduleCutover
from salience.cycles._production_schedule_contracts import (
    ProductionIntentKey, ProductionScheduleBinding, _aware,
)
from salience.cycles._production_schedule_core import ProductionTemporalScheduleControl
from salience.cycles.production_intent_enrollment import ProductionIntentEnrollmentWriter

class _ProductionCycleScheduleCutover(CycleScheduleCutover):
    def __init__(self, *args, enrollment_writer, **kwargs):
        super().__init__(*args, **kwargs)
        self.enrollment_writer = enrollment_writer

    def admit(self, intent_id):
        return self.enrollment_writer.enroll_and_admit(
            self.enrollment_writer.current_key(UUID(str(intent_id)))
        )["admission"]


class ProductionScheduleCutover:
    """Production-bound facade over the existing durable cutover state machine."""

    def __init__(
        self,
        database_url: str,
        *,
        schedule_control: ProductionTemporalScheduleControl,
        binding: ProductionScheduleBinding,
        trace_context=None,
    ) -> None:
        if not isinstance(schedule_control, ProductionTemporalScheduleControl):
            raise ValueError("production schedule control is required")
        if not isinstance(binding, ProductionScheduleBinding):
            raise ValueError("immutable production schedule binding is required")
        schedule_control.assert_binding(binding, action_kind="schedule_prepare")
        self.schedule_control = schedule_control
        self.binding = binding
        self.enrollment_writer = ProductionIntentEnrollmentWriter(
            schedule_control, binding
        )
        self.state_machine = _ProductionCycleScheduleCutover(
            database_url,
            workspace_id=binding.workspace_id,
            subject_id=binding.schedule_operator_subject_id,
            legacy_control=schedule_control,
            trace_context=trace_context,
            enrollment_writer=self.enrollment_writer,
        )

    def _assert(self, goal_id, action_kind: str) -> None:
        if UUID(str(goal_id)) != self.binding.goal_id:
            raise PermissionError("production cutover goal does not match source binding")
        self.schedule_control.assert_binding(self.binding, action_kind=action_kind)

    def prepare(
        self,
        goal_id,
        *,
        legacy_schedule_id,
        expected_revision,
        first_v4_slot,
        idempotency_key,
    ):
        if UUID(str(legacy_schedule_id)) != self.binding.schedule_id:
            raise PermissionError("production cutover schedule does not match source binding")
        if (
            expected_revision != self.binding.goal_revision
            or _aware(first_v4_slot, "first V4 slot")
            != _aware(self.binding.first_v4_slot, "bound first V4 slot")
        ):
            raise PermissionError("production cutover revision and first slot must match binding")
        self._assert(goal_id, "schedule_prepare")
        self.schedule_control.describe(self.binding.schedule_id)
        return self.state_machine.prepare(
            goal_id,
            legacy_schedule_id=legacy_schedule_id,
            expected_revision=expected_revision,
            first_v4_slot=first_v4_slot,
            idempotency_key=idempotency_key,
        )

    def activate(self, goal_id, *, idempotency_key):
        self._assert(goal_id, "schedule_activate")
        return self.state_machine.activate(goal_id, idempotency_key=idempotency_key)

    def poll(self, goal_id, *, expected_revision, idempotency_key):
        self._assert(goal_id, "schedule_poll")
        if expected_revision != self.binding.goal_revision:
            raise PermissionError("production poll revision must match source binding")
        return self.state_machine.poll(
            goal_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
        )

    def rollback(self, goal_id, *, idempotency_key):
        self._assert(goal_id, "schedule_rollback")
        return self.state_machine.rollback(goal_id, idempotency_key=idempotency_key)

    def admit(self, key: ProductionIntentKey):
        if not isinstance(key, ProductionIntentKey):
            raise ValueError("typed canonical intent key required")
        self._assert(self.binding.goal_id, "schedule_admit")
        return self.enrollment_writer.enroll_and_admit(key)

    def inspect(self, goal_id):
        self._assert(goal_id, "schedule_readback")
        return self.state_machine.inspect(goal_id)
