"""Scoped Temporal pause/readback for explicitly pinned no-effects fixtures."""

import asyncio
from datetime import timedelta, timezone

from temporalio.client import Client, ScheduleActionStartWorkflow, ScheduleUpdate

from salience.cycles.legacy_dispatch import require_fixture
from salience.cycles.schedule_cutover import FixtureLegacyScheduleControl
from salience.cycles.legacy_schedule_workflow import LEGACY_SCHEDULE_INGRESS
from salience.cycles.native_schedules import schedule_metadata


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
        native = row.get("native_source") or row.get("native_publication_source")
        expected = schedule_metadata(row)["remote_id"] if native else f"salience-v4-legacy-fixture:{self.workspace_id}:{schedule_id}"
        if schedule_metadata(row)["remote_id"] != expected:
            raise PermissionError("exact scoped Temporal fixture schedule pin required")
        if native and native["task_queue"] != self.task_queue:
            raise PermissionError("original native schedule worker route required")
        return row,expected

    def _plan(self, row, *, required=False):
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(self.database_url, row_factory=dict_row, connect_timeout=3) as connection:
            plan = connection.execute("""SELECT plan.goal_id,plan.workspace_id,plan.actor_id,plan.goal_revision,
                       plan.task_queue,plan.niche,NULL::uuid AS schedule_id,'intelligence' AS stage,
                       NULL::uuid AS publication_schedule_id
                FROM v4_legacy_schedule_plans plan JOIN v4_schedule_cutovers cutover ON cutover.goal_id=plan.goal_id
                WHERE cutover.legacy_schedule_id=%s AND plan.workspace_id=%s
                UNION ALL
                SELECT plan.goal_id,plan.workspace_id,plan.actor_id,plan.goal_revision,plan.task_queue,
                       NULL::text AS niche,plan.schedule_id,'publication' AS stage,plan.publication_schedule_id
                FROM v4_legacy_publication_schedule_plans plan
                JOIN v4_schedule_cutovers cutover ON cutover.goal_id=plan.goal_id
                WHERE cutover.legacy_schedule_id=%s AND plan.workspace_id=%s""",
                (row["id"], self.workspace_id, row["id"], self.workspace_id)).fetchone()
        if plan and plan["task_queue"] != self.task_queue:
            raise PermissionError("original schedule worker route required")
        if row.get("native_publication_source") and plan and (
            plan["stage"] != "publication"
            or str(plan["publication_schedule_id"]) != str(row["native_publication_source"]["publication_schedule_id"])
        ):
            raise PermissionError("original native publication schedule plan required")
        if required and not plan:
            raise PermissionError("bound legacy schedule plan required before remote resume")
        return plan

    def _ingress_payload(self, row, plan):
        payload = {"workspace_id": str(self.workspace_id), "content_program_id": str(row["content_program_id"]),
            "goal_id": str(plan["goal_id"]), "schedule_id": str(row["id"]), "dry_run": True}
        if row.get("native_publication_source"):
            source = row["native_publication_source"]
            if (not plan or plan.get("stage") != "publication"
                or str(plan.get("schedule_id")) != str(row["id"])
                or str(plan.get("publication_schedule_id")) != str(source["publication_schedule_id"])
                or source["remote_id"] != "publication:" + str(source["publication_schedule_id"])):
                raise PermissionError("original paired native publication ingress plan required")
            payload["publication_schedule_id"] = str(source["publication_schedule_id"])
        return payload

    async def _verified(self,client,handle,row,*,allow_running=False,description=None):
        description = description or await handle.describe(rpc_timeout=timedelta(seconds=2))
        action = description.schedule.action
        original_workflow = "GovernedPublicationWorkflow" if row.get("native_publication_source") else "IntelligenceLoopWorkflow"
        if not isinstance(action,ScheduleActionStartWorkflow) or action.workflow not in {original_workflow, LEGACY_SCHEDULE_INGRESS} or action.task_queue != self.task_queue or len(action.args)!=1:
            raise PermissionError("pinned no-effects legacy action required")
        from temporalio.common import RawValue
        from temporalio.api.common.v1 import Payload
        argument = action.args[0]
        if isinstance(argument,RawValue):
            argument = (await client.data_converter.decode([argument.payload],[dict]))[0]
        elif isinstance(argument,Payload):
            argument = (await client.data_converter.decode([argument],[dict]))[0]
        plan = self._plan(row, required=action.workflow == LEGACY_SCHEDULE_INGRESS)
        if row.get("native_publication_source") and plan and plan["stage"] != "publication":
            raise PermissionError("native publication schedule requires publication-stage binding")
        if action.workflow == LEGACY_SCHEDULE_INGRESS and argument != self._ingress_payload(row, plan):
            raise PermissionError("original legacy schedule ingress binding required")
        if action.workflow == LEGACY_SCHEDULE_INGRESS:
            if not isinstance(argument,dict) or argument.get("workspace_id") != str(self.workspace_id) or argument.get("content_program_id") != str(row["content_program_id"]) or argument.get("dry_run") is not True:
                raise PermissionError("scoped no-effects legacy ingress payload required")
        elif row.get("native_publication_source") and action.workflow == "GovernedPublicationWorkflow":
            original = row["native_publication_source"]["remote_snapshot"]
            if argument != original["payload"] or action.id != original["action_id"]:
                raise PermissionError("original native publication action identity required")
        elif action.workflow == "IntelligenceLoopWorkflow":
            if not isinstance(argument,dict) or argument.get("workspace_id") != str(self.workspace_id) or argument.get("content_program_id") != str(row["content_program_id"]) or argument.get("dry_run") is not True:
                raise PermissionError("scoped no-effects legacy payload required")
            if plan and argument.get("niche") != plan["niche"]:
                raise PermissionError("original legacy schedule workload required")
        if row.get("native_source") and action.workflow == "IntelligenceLoopWorkflow":
            original = row["native_source"]["remote_snapshot"]
            if argument != original["payload"] or action.id != original["action_id"]:
                raise PermissionError("original native action identity required")
        metadata = schedule_metadata(row)
        interval = metadata["interval_seconds"]
        spec = description.schedule.spec
        if len(spec.intervals)!=1 or spec.intervals[0].every != timedelta(seconds=interval) or spec.calendars or spec.cron_expressions:
            raise ValueError("compatible interval Temporal fixture required")
        if metadata["next_run_at"] is None or (metadata["next_run_at"].timestamp()-(spec.intervals[0].offset or timedelta()).total_seconds()) % interval:
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
            observed = max(action.scheduled_at for action in description.info.recent_actions)
            result["last_slot"] = observed if result["last_slot_is_boundary"] else max(result["last_slot"], observed)
            result["last_slot_is_boundary"] = False
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
                schedule.spec.start_at = max(after_slot + timedelta(seconds=1), schedule_metadata(row)["next_run_at"])
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

    async def _native_snapshot(self, row, first_v4_slot):
        async with asyncio.timeout(4):
            client = await Client.connect(self.temporal_target)
            description = await client.get_schedule_handle(str(row["id"])).describe(rpc_timeout=timedelta(seconds=2))
            action = description.schedule.action
            if not isinstance(action, ScheduleActionStartWorkflow) or action.workflow != "IntelligenceLoopWorkflow" or action.id != str(row["id"])+":execution" or action.task_queue != self.task_queue or len(action.args) != 1:
                raise PermissionError("original no-effects native Temporal action required")
            from temporalio.common import RawValue
            from temporalio.api.common.v1 import Payload
            argument = action.args[0]
            if isinstance(argument, RawValue): argument = (await client.data_converter.decode([argument.payload], [dict]))[0]
            elif isinstance(argument, Payload): argument = (await client.data_converter.decode([argument], [dict]))[0]
            expected = {"workspace_id": str(self.workspace_id), "content_program_id": str(row["content_program_id"]),
                "niche": row["payload"]["niche"], "idempotency_key": "schedule:"+str(row["id"]),
                "dry_run": True, "contract_version": "IntelligenceRunRequest@v1"}
            if argument != expected:
                raise PermissionError("original native schedule payload required")
            spec = description.schedule.spec
            interval = int(row["schedule_expression"][6:-1])
            if len(spec.intervals)!=1 or spec.intervals[0].every!=timedelta(seconds=interval) or spec.intervals[0].offset not in {None,timedelta()} or spec.calendars or spec.cron_expressions or spec.jitter:
                raise ValueError("original native interval specification required")
            if first_v4_slot not in description.info.next_action_times:
                raise ValueError("actual future native schedule slot required")
            recent = description.info.recent_actions
            if description.info.num_actions and not recent:
                raise ValueError("native schedule action history readback required")
            last_slot = max((action.scheduled_at for action in recent), default=None)
            if last_slot and last_slot >= first_v4_slot:
                raise ValueError("native action crosses first V4 slot")
            return {"remote_id":str(row["id"]), "workflow_type":action.workflow, "action_id":action.id,
                "task_queue":action.task_queue, "payload":argument,
                "action_count":description.info.num_actions,
                "recent_actions":[{"scheduled_at":a.scheduled_at.isoformat(),"started_at":a.started_at.isoformat(),"workflow_id":a.action.workflow_id,"first_execution_run_id":a.action.first_execution_run_id} for a in recent],
                "observed_last_slot":last_slot.isoformat() if last_slot else None}, last_slot

    def adopt_native(self, goal_id, *, schedule_id, subject_id, expected_revision, first_v4_slot):
        """Seal an authenticated original native row and actual remote readback."""
        import re
        from psycopg.types.json import Jsonb
        from salience.cycles.admission import CycleAdmission
        from salience.cycles.governance import CycleGovernance
        require_fixture()
        if first_v4_slot.tzinfo is None or first_v4_slot.microsecond or type(expected_revision) is not int or expected_revision<1:
            raise ValueError("whole-second future slot and exact revision required")
        first_v4_slot = first_v4_slot.astimezone(timezone.utc)
        service = CycleAdmission(self.database_url, workspace_id=self.workspace_id, subject_id=subject_id)
        def validate(connection):
            goal, spec = service._policy_goal(connection, goal_id)
            CycleGovernance(self.database_url, workspace_id=self.workspace_id, subject_id=subject_id)._require_running(connection, goal_id)
            row = connection.execute("SELECT schedule.*,to_jsonb(schedule)-ARRAY['status','next_run_at','updated_at'] AS original_identity FROM job_schedules schedule WHERE id=%s AND workspace_id=%s FOR UPDATE", (schedule_id, self.workspace_id)).fetchone()
            if not row or row["job_type"]!='intelligence_research' or row["content_program_id"]!=spec.content_program_id or row["timezone"]!='UTC' or row["payload"].get("dry_run") is not True or set(row["payload"])!={'dry_run','niche'} or not isinstance(row["payload"]["niche"],str) or not row["payload"]["niche"].strip() or len(row["payload"]["niche"])>256:
                raise PermissionError("original scoped no-effects native schedule required")
            if not re.fullmatch(r'every [1-9][0-9]*s',row["schedule_expression"]) or int(row["schedule_expression"][6:-1])!=spec.cadence.interval_seconds:
                raise ValueError("same native interval required")
            existing = connection.execute("SELECT * FROM v4_native_schedule_sources WHERE schedule_id=%s", (schedule_id,)).fetchone()
            if existing:
                if existing["goal_id"]!=goal["id"] or existing["actor_id"]!=service.subject_id or existing["goal_revision"]!=expected_revision or existing["task_queue"]!=self.task_queue or existing["first_v4_slot"]!=first_v4_slot or existing["original_identity"]!=row["original_identity"]:
                    raise ValueError("native source adoption conflict")
                return row, spec, existing
            now = connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
            if goal["state"]!='active' or goal["revision"]!=expected_revision or row["status"]!='active' or not now<first_v4_slot<spec.horizon_end or (first_v4_slot-spec.cadence.anchor).total_seconds()%spec.cadence.interval_seconds or first_v4_slot<spec.cadence.anchor:
                raise ValueError("current active aligned native source required")
            if connection.execute("SELECT 1 FROM v4_schedule_cutovers WHERE goal_id=%s OR legacy_schedule_id=%s", (goal_id,schedule_id)).fetchone():
                raise ValueError("adopt native source before cutover preparation")
            return row, spec, None
        def view(source):
            return {"goal_id":str(source["goal_id"]),"schedule_id":str(source["schedule_id"]),"remote_id":source["remote_id"],
                "first_v4_slot":source["first_v4_slot"].isoformat(),"observed_last_slot":source["observed_last_slot"].isoformat() if source["observed_last_slot"] else None,"state":"bound"}
        with service._command("cycles:schedule") as connection:
            row, spec, existing = validate(connection)
            if existing:return view(existing)
        snapshot, last_slot = asyncio.run(self._native_snapshot(row, first_v4_slot))
        with service._command("cycles:schedule") as connection:
            current, spec, existing = validate(connection)
            if existing:return view(existing)
            if current["original_identity"] != row["original_identity"]:
                raise ValueError("native source changed during remote readback")
            source = connection.execute("""INSERT INTO v4_native_schedule_sources(schedule_id,workspace_id,goal_id,actor_id,goal_revision,
                remote_id,task_queue,interval_seconds,first_v4_slot,observed_last_slot,remote_snapshot,original_identity)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""", (schedule_id,self.workspace_id,goal_id,subject_id,expected_revision,str(schedule_id),self.task_queue,spec.cadence.interval_seconds,first_v4_slot,last_slot,Jsonb(snapshot),Jsonb(row["original_identity"]))).fetchone()
            connection.execute("INSERT INTO v4_native_schedule_progress(schedule_id,last_slot,next_slot) VALUES(%s,%s,%s)", (schedule_id,last_slot,first_v4_slot))
            service._event(connection,goal_id,"native_schedule_adopted",view(source))
            return view(source)

    async def _native_publication_snapshot(self, original, first_v4_slot):
        from salience.cycles.native_publication_schedules import native_publication_workflow_payload
        from salience.workflows.publication import PUBLICATION_WORKFLOW_TYPE
        remote_id = "publication:" + str(original["publication_schedule_id"])
        async with asyncio.timeout(4):
            client = await Client.connect(self.temporal_target)
            description = await client.get_schedule_handle(remote_id).describe(rpc_timeout=timedelta(seconds=2))
            if description.schedule.state.paused:
                raise ValueError("active native publication Temporal schedule required")
            action = description.schedule.action
            if not isinstance(action, ScheduleActionStartWorkflow) or action.workflow != PUBLICATION_WORKFLOW_TYPE or action.id != remote_id+":execution" or action.task_queue != self.task_queue or len(action.args) != 1:
                raise PermissionError("original governed-publication Temporal action required")
            from temporalio.common import RawValue
            from temporalio.api.common.v1 import Payload
            argument = action.args[0]
            if isinstance(argument, RawValue): argument = (await client.data_converter.decode([argument.payload], [dict]))[0]
            elif isinstance(argument, Payload): argument = (await client.data_converter.decode([argument], [dict]))[0]
            if argument != native_publication_workflow_payload(original):
                raise PermissionError("original publication schedule identity payload required")
            spec = description.schedule.spec
            interval = timedelta(seconds=original["interval_seconds"])
            if len(spec.intervals) != 1 or spec.intervals[0].every != interval or spec.intervals[0].offset not in {None,timedelta()} or spec.calendars or spec.cron_expressions or spec.jitter:
                raise ValueError("original native publication interval specification required")
            if first_v4_slot not in description.info.next_action_times:
                raise ValueError("actual future native publication schedule slot required")
            recent = description.info.recent_actions
            if description.info.num_actions and not recent:
                raise ValueError("native publication action history readback required")
            slots = [item.scheduled_at for item in recent]
            last_slot = max(slots, default=None)
            if last_slot is not None and last_slot >= first_v4_slot:
                raise ValueError("native publication action crosses first V4 slot")
            ordered = sorted(recent, key=lambda item: item.scheduled_at)
            return {
                "remote_id": remote_id,
                "workflow_type": action.workflow,
                "action_id": action.id,
                "task_queue": action.task_queue,
                "payload": argument,
                "action_count": description.info.num_actions,
                "history_start_slot": ordered[0].scheduled_at.isoformat() if ordered else None,
                "recent_actions": [
                    {"scheduled_at": item.scheduled_at.isoformat(),
                     "started_at": item.started_at.isoformat(),
                     "workflow_id": item.action.workflow_id,
                     "first_execution_run_id": item.action.first_execution_run_id}
                    for item in ordered
                ],
                "next_action_times": [slot.isoformat() for slot in description.info.next_action_times],
            }, last_slot

    def adopt_native_publication(self, goal_id, *, schedule_id, publication_schedule_id, subject_id, expected_revision, first_v4_slot):
        """Seal original paired publication rows and the actual SDK action history."""
        from uuid import UUID
        from psycopg.types.json import Jsonb
        from salience.cycles.admission import CycleAdmission
        from salience.cycles.governance import CycleGovernance
        from salience.cycles.legacy_dispatch import LegacyDispatch
        from salience.cycles.legacy_publication import require_publication_proposal
        from salience.cycles.native_publication_schedules import (
            load_native_publication_source,
            original_publication_schedule,
        )
        require_fixture()
        if first_v4_slot.tzinfo is None or first_v4_slot.microsecond or type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("whole-second future slot and exact revision required")
        schedule_id = UUID(str(schedule_id))
        publication_schedule_id = UUID(str(publication_schedule_id))
        first_v4_slot = first_v4_slot.astimezone(timezone.utc)
        service = CycleAdmission(self.database_url, workspace_id=self.workspace_id, subject_id=subject_id)
        bridge = LegacyDispatch(self.database_url, workspace_id=self.workspace_id, subject_id=subject_id, task_queue=self.task_queue)

        def validate(connection):
            goal, spec = service._policy_goal(connection, goal_id)
            CycleGovernance(self.database_url, workspace_id=self.workspace_id, subject_id=subject_id)._require_running(connection, goal_id)
            if goal["state"] != "active" or goal["revision"] != expected_revision:
                raise ValueError("current active publication goal revision required")
            existing_row = connection.execute(
                "SELECT 1 FROM v4_native_publication_schedule_sources WHERE schedule_id=%s", (schedule_id,)
            ).fetchone()
            if existing_row:
                existing = load_native_publication_source(connection, schedule_id, self.workspace_id)
                original = existing["original_source"]
                if (existing["goal_id"] != goal["id"] or existing["actor_id"] != service.subject_id
                    or existing["goal_revision"] != expected_revision or existing["task_queue"] != self.task_queue
                    or existing["first_v4_slot"] != first_v4_slot
                    or existing["publication_schedule_id"] != publication_schedule_id):
                    raise ValueError("native publication source adoption conflict")
                require_publication_proposal(connection, bridge, goal, spec, original["command_payload"])
                return goal, spec, original, existing
            original = original_publication_schedule(
                connection, schedule_id, self.workspace_id,
                publication_schedule_id=publication_schedule_id, lock=True,
            )
            if original["content_program_id"] != spec.content_program_id:
                raise PermissionError("same-program native publication schedule required")
            if original["status"] != "active" or original["interval_seconds"] != spec.cadence.interval_seconds:
                raise ValueError("active compatible native publication schedule required")
            now = connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
            elapsed = (first_v4_slot - spec.cadence.anchor).total_seconds()
            if not now < first_v4_slot < spec.horizon_end or elapsed < 0 or elapsed % spec.cadence.interval_seconds:
                raise ValueError("current active aligned native publication slot required")
            require_publication_proposal(connection, bridge, goal, spec, original["command_payload"])
            if connection.execute("SELECT 1 FROM v4_schedule_cutovers WHERE goal_id=%s OR legacy_schedule_id=%s", (goal_id, schedule_id)).fetchone():
                raise ValueError("adopt native publication schedule before cutover preparation")
            return goal, spec, original, None

        def view(source):
            return {"goal_id": str(source["goal_id"]), "schedule_id": str(source["schedule_id"]),
                "publication_schedule_id": str(source["publication_schedule_id"]), "remote_id": source["remote_id"],
                "first_v4_slot": source["first_v4_slot"].isoformat(),
                "observed_last_slot": source["observed_last_slot"].isoformat() if source["observed_last_slot"] else None,
                "state": "bound"}

        with service._command("cycles:schedule") as connection:
            goal, spec, original, existing = validate(connection)
            if existing:
                return view(existing)
        snapshot, last_slot = asyncio.run(self._native_publication_snapshot(original, first_v4_slot))
        with service._command("cycles:schedule") as connection:
            goal, spec, current, existing = validate(connection)
            if existing:
                return view(existing)
            if current["original_identity"] != original["original_identity"] or current["publication_identity"] != original["publication_identity"]:
                raise ValueError("native publication source changed during remote readback")
            source = connection.execute("""INSERT INTO v4_native_publication_schedule_sources
                (schedule_id,publication_schedule_id,workspace_id,goal_id,actor_id,goal_revision,remote_id,task_queue,
                 interval_seconds,first_v4_slot,observed_last_slot,remote_snapshot,original_identity,publication_identity)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (schedule_id,publication_schedule_id,self.workspace_id,goal_id,subject_id,expected_revision,
                 snapshot["remote_id"],self.task_queue,spec.cadence.interval_seconds,first_v4_slot,last_slot,
                 Jsonb(snapshot),Jsonb(current["original_identity"]),Jsonb(current["publication_identity"]))).fetchone()
            connection.execute("INSERT INTO v4_native_publication_schedule_progress(schedule_id,last_slot,next_slot) VALUES(%s,%s,%s)",
                (schedule_id,last_slot,first_v4_slot))
            service._event(connection,goal_id,"native_publication_schedule_adopted",view(source))
            return view(source)
