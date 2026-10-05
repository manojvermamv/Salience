"""Scoped Temporal pause/readback for explicitly pinned no-effects fixtures."""

import asyncio
from datetime import timedelta

from temporalio.client import Client, ScheduleActionStartWorkflow, ScheduleUpdate

from salience.cycles.legacy_dispatch import require_fixture
from salience.cycles.schedule_cutover import FixtureLegacyScheduleControl
from salience.cycles.legacy_schedule_workflow import LEGACY_SCHEDULE_INGRESS


class TemporalFixtureLegacyScheduleControl(FixtureLegacyScheduleControl):
    supports_temporal = True
    def __init__(self,database_url,*,workspace_id,temporal_target,task_queue):
        super().__init__(database_url,workspace_id=workspace_id)
        if not task_queue.startswith("salience-v4-local-legacy-"):
            raise ValueError("isolated legacy schedule queue required")
        self.temporal_target,self.task_queue = temporal_target,task_queue

    def _pin(self,schedule_id):
        require_fixture()
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(self.database_url,row_factory=dict_row,connect_timeout=3) as connection:
            row = self._schedule(connection,schedule_id)
        expected = f"salience-v4-legacy-fixture:{self.workspace_id}:{schedule_id}"
        if row["payload"].get("temporal_schedule_id") != expected:
            raise PermissionError("exact scoped Temporal fixture schedule pin required")
        return row,expected

    def _plan(self, row, *, required=False):
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(self.database_url, row_factory=dict_row, connect_timeout=3) as connection:
            plan = connection.execute("SELECT plan.* FROM v4_legacy_schedule_plans plan JOIN v4_schedule_cutovers cutover ON cutover.goal_id=plan.goal_id WHERE cutover.legacy_schedule_id=%s AND plan.workspace_id=%s", (row["id"], self.workspace_id)).fetchone()
        if plan and plan["task_queue"] != self.task_queue:
            raise PermissionError("original schedule worker route required")
        if required and not plan:
            raise PermissionError("bound legacy schedule plan required before remote resume")
        return plan

    def _ingress_payload(self, row, plan):
        return {"workspace_id": str(self.workspace_id), "content_program_id": str(row["content_program_id"]),
            "goal_id": str(plan["goal_id"]), "schedule_id": str(row["id"]), "dry_run": True}

    async def _verified(self,client,handle,row,*,allow_running=False,description=None):
        description = description or await handle.describe(rpc_timeout=timedelta(seconds=2))
        action = description.schedule.action
        if not isinstance(action,ScheduleActionStartWorkflow) or action.workflow not in {"IntelligenceLoopWorkflow", LEGACY_SCHEDULE_INGRESS} or action.task_queue != self.task_queue or len(action.args)!=1:
            raise PermissionError("pinned no-effects legacy action required")
        from temporalio.common import RawValue
        from temporalio.api.common.v1 import Payload
        argument = action.args[0]
        if isinstance(argument,RawValue):
            argument = (await client.data_converter.decode([argument.payload],[dict]))[0]
        elif isinstance(argument,Payload):
            argument = (await client.data_converter.decode([argument],[dict]))[0]
        if not isinstance(argument,dict) or argument.get("workspace_id") != str(self.workspace_id) or argument.get("content_program_id") != str(row["content_program_id"]) or argument.get("dry_run") is not True:
            raise PermissionError("scoped no-effects legacy payload required")
        plan = self._plan(row, required=action.workflow == LEGACY_SCHEDULE_INGRESS)
        if action.workflow == LEGACY_SCHEDULE_INGRESS and argument != self._ingress_payload(row, plan):
            raise PermissionError("original legacy schedule ingress binding required")
        if action.workflow == "IntelligenceLoopWorkflow" and plan and argument.get("niche") != plan["niche"]:
            raise PermissionError("original legacy schedule workload required")
        interval = int(row["schedule_expression"][6:-1])
        spec = description.schedule.spec
        if len(spec.intervals)!=1 or spec.intervals[0].every != timedelta(seconds=interval) or spec.calendars or spec.cron_expressions:
            raise ValueError("compatible interval Temporal fixture required")
        if row["next_run_at"] is None or (row["next_run_at"].timestamp()-(spec.intervals[0].offset or timedelta()).total_seconds()) % interval:
            raise ValueError("aligned Temporal fixture interval required")
        if description.info.running_actions and not allow_running and action.workflow != LEGACY_SCHEDULE_INGRESS:
            raise ValueError("in-flight legacy actions hold cutover")
        return description

    async def _describe(self,row,remote_id):
        async with asyncio.timeout(4):
            client = await Client.connect(self.temporal_target)
            description = await self._verified(client,client.get_schedule_handle(remote_id),row)
            return description

    def describe(self,schedule_id):
        row,remote_id = self._pin(schedule_id)
        description = asyncio.run(self._describe(row,remote_id))
        result = super().describe(schedule_id)
        if description.info.recent_actions:
            result["last_slot"] = max(result["last_slot"],max(action.scheduled_at for action in description.info.recent_actions))
        result["paused"] = description.schedule.state.paused
        watermark = result["resume_after"]
        if watermark:
            from datetime import datetime
            bound = datetime.fromisoformat(watermark)
            if description.schedule.state.note != "V4 no-effects rollback watermark:"+watermark or description.schedule.spec.start_at is None or description.schedule.spec.start_at <= bound:
                result["resume_after"] = None
            if description.info.next_action_times:
                result["next_run_at"] = description.info.next_action_times[0]
        return result

    async def _pause(self,row,remote_id):
        async with asyncio.timeout(4):
            client = await Client.connect(self.temporal_target)
            handle = client.get_schedule_handle(remote_id)
            await self._verified(client,handle,row,allow_running=True)
            try:
                await handle.pause(note="V4 no-effects fixture cutover",rpc_timeout=timedelta(seconds=2))
            except TimeoutError:
                pass
            description = await self._verified(client,handle,row)
            if not description.schedule.state.paused:
                raise ValueError("Temporal pause readback required")

    def pause(self,schedule_id):
        row,remote_id = self._pin(schedule_id)
        asyncio.run(self._pause(row,remote_id))
        super().pause(schedule_id)

    async def _resume(self,row,remote_id,after_slot):
        async with asyncio.timeout(4):
            client = await Client.connect(self.temporal_target)
            handle = client.get_schedule_handle(remote_id)
            plan = self._plan(row, required=True)
            async def update(argument):
                await self._verified(client, handle, row, description=argument.description)
                schedule = argument.description.schedule
                if not schedule.state.paused and (schedule.action.workflow != LEGACY_SCHEDULE_INGRESS or schedule.state.note != "V4 no-effects rollback watermark:"+after_slot.isoformat()):
                    raise PermissionError("paused original schedule required before conversion")
                schedule.action = ScheduleActionStartWorkflow(LEGACY_SCHEDULE_INGRESS, self._ingress_payload(row, plan),
                    id=remote_id+":admission", task_queue=self.task_queue,
                    execution_timeout=timedelta(seconds=45))
                schedule.spec.start_at = max(after_slot + timedelta(seconds=1), row["next_run_at"])
                schedule.state.paused = False
                schedule.state.note = "V4 no-effects rollback watermark:"+after_slot.isoformat()
                return ScheduleUpdate(schedule=schedule)
            await handle.update(update,rpc_timeout=timedelta(seconds=2))
            description = await self._verified(client,handle,row)
            if description.schedule.state.paused or description.schedule.spec.start_at <= after_slot:
                raise ValueError("Temporal rollback watermark readback required")

    def resume_after(self,schedule_id,after_slot,*,subject_id=None):
        row,remote_id = self._pin(schedule_id)
        self._plan(row, required=True)
        # Canonical rollback_pending fences V4 throughout the remote boundary.
        # If the remote acknowledgment is unknown, keep that fence for retry.
        super().resume_after(schedule_id,after_slot,subject_id=subject_id)
        row,remote_id = self._pin(schedule_id)
        asyncio.run(self._resume(row,remote_id,after_slot))
