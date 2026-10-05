"""No-effects legacy intelligence compatibility over canonical V4 admission."""

from hashlib import sha256
import json
import os
from typing import Literal
from datetime import datetime, timezone
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from salience.cycles.admission import CycleAdmission
from salience.cycles.contracts import CycleRequest
from salience.cycles.authority import current_authority
from salience.observability.tracing import TraceContext
from salience.cycles.native_schedules import schedule_row, schedule_metadata, require_native_binding


class LegacyIntelligenceCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    goal_id: UUID
    request: CycleRequest
    niche: str = Field(min_length=1, max_length=256, pattern=r"\S")
    dry_run: Literal[True] = True
    contract_version: Literal["LegacyIntelligenceDispatch.local.v1"] = "LegacyIntelligenceDispatch.local.v1"


class LegacyBriefCommand(LegacyIntelligenceCommand):
    selected_opportunity_id: UUID
    contract_version: Literal["LegacyBriefDispatch.local.v1"] = "LegacyBriefDispatch.local.v1"


class LegacyDummyCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")
    goal_id: UUID
    request: CycleRequest
    dry_run: Literal[True] = True
    contract_version: Literal["LegacyDummyDispatch.local.v1"] = "LegacyDummyDispatch.local.v1"


class LegacyCreativeCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")
    goal_id: UUID
    request: CycleRequest
    brief_id: UUID
    dry_run: Literal[True] = True
    target_profile_key: Literal["fixture-short-video"] = "fixture-short-video"
    target_profile_version: Literal[1] = 1
    max_variants: int = Field(default=1, ge=1, le=3, strict=True)
    contract_version: Literal["LegacyCreativeDispatch.local.v1"] = "LegacyCreativeDispatch.local.v1"


def require_fixture():
    if os.environ.get("SALIENCE_DEPLOYMENT_MODE") != "fixture" or os.environ.get("SALIENCE_EFFECTS_ENABLED", "false") != "false":
        raise PermissionError("legacy dispatch requires explicit no-effects fixture mode")


from salience.cycles.legacy_publication import LegacyPublicationCommands, LegacyPublicationCommand


class LegacyDispatch(LegacyPublicationCommands, CycleAdmission):
    def __init__(self, database_url, *, workspace_id, subject_id, task_queue, trace_context=None):
        super().__init__(database_url, workspace_id=workspace_id, subject_id=subject_id, trace_context=trace_context)
        if not isinstance(task_queue, str) or not task_queue.startswith("salience-v4-local-legacy-") or len(task_queue) > 128:
            raise ValueError("isolated legacy fixture queue required")
        self.task_queue = task_queue

    def submit(self, command: LegacyIntelligenceCommand):
        require_fixture()
        command = LegacyIntelligenceCommand.model_validate(command)
        with self._command("cycles:write") as connection:
            return self._submit(connection, command)

    def submit_dummy(self, command: LegacyDummyCommand):
        require_fixture()
        command = LegacyDummyCommand.model_validate(command)
        with self._command("cycles:write") as connection:
            current_authority(connection, self.workspace_id, self.subject_id, "legacy:dummy")
            result = self._submit(connection, command)
            current_authority(connection, self.workspace_id, self.subject_id, "legacy:dummy")
            return result

    def submit_creative(self, command: LegacyCreativeCommand):
        require_fixture()
        command = LegacyCreativeCommand.model_validate(command)
        with self._command("cycles:write") as connection:
            current_authority(connection, self.workspace_id, self.subject_id, "legacy:creative")
            result = self._submit(connection, command)
            current_authority(connection, self.workspace_id, self.subject_id, "legacy:creative")
            return result

    def submit_brief(self, command: LegacyBriefCommand):
        require_fixture()
        command = LegacyBriefCommand.model_validate(command)
        with self._command("cycles:write") as connection:
            return self._submit(connection, command)

    def _submit(self, connection, command, *, cutover_poll=False):
        # All ingress paths take the canonical goal lock before the command key.
        # A resumed tick already holds this lock; reverse ordering deadlocks
        # with an API command using the same actor-scoped key.
        self._goal(connection, command.goal_id)
        digest = sha256(json.dumps(command.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        # Scope idempotency to the authenticated actor, not a global job key.
        lock_key = int.from_bytes(sha256(f"legacy:{self.workspace_id}:{self.subject_id}:{command.request.idempotency_key}".encode()).digest()[:8], "big", signed=True)
        connection.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key,))
        prior = connection.execute("SELECT * FROM v4_legacy_commands WHERE workspace_id=%s AND actor_id=%s AND idempotency_key=%s", (self.workspace_id, self.subject_id, command.request.idempotency_key)).fetchone()
        if prior:
            if prior["fingerprint"] != digest:
                raise ValueError("legacy command fingerprint conflict")
            return prior["response"]
        goal, spec = self._policy_goal(connection, command.goal_id)
        if isinstance(command, LegacyCreativeCommand):
            if not connection.execute("SELECT brief.id FROM content_brief_versions brief JOIN content_programs program ON program.id=brief.content_program_id WHERE brief.id=%s AND brief.workspace_id=%s AND brief.content_program_id=%s AND program.workspace_id=%s", (command.brief_id, self.workspace_id, spec.content_program_id, self.workspace_id)).fetchone():
                raise PermissionError("creative brief outside original workspace/program")
        if isinstance(command, LegacyBriefCommand):
            opportunity = connection.execute("SELECT opportunity.id FROM topic_opportunities opportunity JOIN content_programs program ON program.id=opportunity.content_program_id WHERE opportunity.id=%s AND opportunity.workspace_id=%s AND opportunity.content_program_id=%s AND program.workspace_id=%s AND program.niche=%s", (command.selected_opportunity_id, self.workspace_id, spec.content_program_id, self.workspace_id, command.niche)).fetchone()
            if not opportunity:
                raise PermissionError("selected opportunity outside original program/niche")
        intent_id = self._request_cycle(connection, goal, spec, command.request, cutover_poll=cutover_poll)
        admission = self._admit(connection, intent_id)
        result = {"goal_id": str(goal["id"]), "intent_id": str(intent_id), "disposition": admission["disposition"], "reason": admission["reason"], "dry_run": not isinstance(command,LegacyPublicationCommand)}
        if admission["cycle_id"]:
            result.update(self._materialize(connection, command, spec, admission["cycle_id"]))
        connection.execute("INSERT INTO v4_legacy_commands(workspace_id,actor_id,idempotency_key,fingerprint,response) VALUES(%s,%s,%s,%s,%s)", (self.workspace_id, self.subject_id, command.request.idempotency_key, digest, Jsonb(result)))
        return result

    def _materialize(self, connection, command, spec, cycle_id):
        _, _, intent, cycle = self._cycle(connection, cycle_id)
        stage = "dummy" if isinstance(command, LegacyDummyCommand) else "intelligence"
        job_type = "durable_dummy" if stage == "dummy" else "intelligence_research"
        workload = {"mode": "success", "contract_version": "DummyWorkflowRequest@v1"} if stage == "dummy" else {}
        if isinstance(command, LegacyIntelligenceCommand):
            workload = {"niche": command.niche, "contract_version": "IntelligenceRunRequest@v1"}
        if isinstance(command, LegacyCreativeCommand):
            stage, job_type = "creative", "creative_production"
            workload = {"brief_id": str(command.brief_id), "target_profile_key": command.target_profile_key,
                "target_profile_version": command.target_profile_version, "max_variants": command.max_variants,
                "contract_version": "CreativeProductionRequest@v1"}
        if isinstance(command, LegacyBriefCommand):
            workload["selected_opportunity_id"] = str(command.selected_opportunity_id)
        if isinstance(command, LegacyPublicationCommand):
            stage,job_type,workload="publication","governed_publication",command.native_payload()
        prior = connection.execute("SELECT * FROM v4_legacy_dispatches WHERE cycle_id=%s", (cycle_id,)).fetchone()
        if prior:
            if prior["actor_id"] != self.subject_id or prior["payload"] != workload or prior["task_queue"] != self.task_queue:
                raise ValueError("coalesced legacy workload conflict")
            return self._view(prior)
        # A pre-existing fixture cycle can be adopted only before any delivery.
        # The row lock prevents a concurrent dispatcher from choosing its lane.
        if stage != "intelligence" and not connection.execute("SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='v4_legacy_dispatches' AND column_name='stage'").fetchone():
            raise PermissionError("qualified legacy stage migration required")
        message = connection.execute("SELECT * FROM v4_cycle_outbox WHERE cycle_id=%s AND kind='start' AND sequence=1 FOR UPDATE", (cycle_id,)).fetchone()
        if not message or message["subject_id"] != self.subject_id or message["state"] != "pending" or message["attempts"] != 0:
            raise ValueError("legacy adoption requires an untouched original start")
        if connection.execute("SELECT 1 FROM v4_cycle_inbox WHERE cycle_id=%s", (cycle_id,)).fetchone():
            raise ValueError("consumed cycle cannot change delivery lane")
        job_id = uuid4()
        context = TraceContext.from_carrier({"traceparent":message["traceparent"]})
        workflow_id = "salience-v4-legacy-operation:" + str(cycle["operation_id"])
        connection.execute("""INSERT INTO jobs(id,workspace_id,content_program_id,job_type,state,workflow_run_id,
            task_queue,idempotency_key,input_payload,trace_id,span_id,dry_run,actor_kind,actor_id)
            VALUES(%s,%s,%s,%s,'queued',%s,%s,%s,%s,%s,%s,%s,'identity',%s)""",
            (job_id, self.workspace_id, spec.content_program_id, job_type, workflow_id, self.task_queue,
             "v4-legacy:" + str(cycle["operation_id"]), Jsonb(workload), context.trace_id, context.span_id, stage != "publication", str(self.subject_id)))
        insert_stage = stage != "intelligence"
        statement = """INSERT INTO v4_legacy_dispatches(cycle_id,context_id,operation_id,intent_id,
            goal_id,workspace_id,actor_id,content_program_id,job_id,task_queue,payload)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *"""
        parameters = (cycle_id,cycle["context_id"],cycle["operation_id"],intent["id"],command.goal_id,self.workspace_id,
             self.subject_id,spec.content_program_id,job_id,self.task_queue,Jsonb(workload))
        if insert_stage:
            statement = statement.replace("task_queue,payload)", "task_queue,payload,stage)").replace("%s) RETURNING *", "%s,%s) RETURNING *")
            parameters += (stage,)
        row = connection.execute(statement, parameters).fetchone()
        self._event(connection, command.goal_id, "legacy_dispatch_queued", self._view(row), intent["id"], cycle_id)
        return self._view(row)

    @staticmethod
    def _view(row):
        return {key: str(row[key]) for key in ("cycle_id", "context_id", "operation_id", "job_id")} | {"state": "queued"}

    def inspect(self, job_id):
        with self._command("cycles:read") as connection:
            row = connection.execute("""SELECT binding.*,job.state AS job_state,job.output_payload,
                cycle.state AS cycle_state FROM v4_legacy_dispatches binding
                JOIN jobs job ON job.id=binding.job_id JOIN v4_cycles cycle ON cycle.id=binding.cycle_id
                WHERE binding.job_id=%s AND binding.workspace_id=%s""", (job_id,self.workspace_id)).fetchone()
            if not row:
                raise LookupError("legacy job outside current scope")
            claim = connection.execute("SELECT state FROM v4_permit_claims WHERE cycle_id=%s",(row["cycle_id"],)).fetchone()
            return self._view(row) | {"state":row["job_state"],"cycle_state":row["cycle_state"],"dispatch_state":claim["state"] if claim else "pending","output":row["output_payload"] or {},"dry_run":row.get("stage")!="publication"}

    def bind_schedule(self, goal_id, *, expected_revision, niche):
        require_fixture()
        if not isinstance(niche,str) or not niche.strip() or len(niche)>256 or type(expected_revision) is not int or expected_revision<1:
            raise ValueError("bounded schedule workload and revision required")
        with self._command("cycles:schedule") as connection:
            current_authority(connection,self.workspace_id,self.subject_id)
            goal, _ = self._policy_goal(connection,goal_id)
            cutover = connection.execute("SELECT * FROM v4_schedule_cutovers WHERE goal_id=%s AND workspace_id=%s FOR UPDATE",(goal_id,self.workspace_id)).fetchone()
            if not cutover or cutover["actor_id"] != self.subject_id or cutover["goal_revision"] != expected_revision:
                raise PermissionError("original scoped schedule cutover required")
            schedule = schedule_row(connection, cutover["legacy_schedule_id"], self.workspace_id, temporal=True)
            require_native_binding(schedule, goal_id=goal_id, actor_id=self.subject_id, task_queue=self.task_queue, first_v4_slot=cutover["first_v4_slot"])
            if schedule.get("native_source") and schedule["payload"]["niche"] != niche:
                raise PermissionError("original native workload required")
            prior = connection.execute("SELECT * FROM v4_legacy_schedule_plans WHERE goal_id=%s",(goal_id,)).fetchone()
            if prior:
                if (prior["niche"],prior["task_queue"],prior["actor_id"],prior["goal_revision"]) != (niche,self.task_queue,self.subject_id,expected_revision):
                    raise ValueError("legacy schedule plan conflict")
                return {"goal_id":str(goal_id),"state":"bound"}
            if cutover["state"] != "pending" or goal["revision"] != expected_revision:
                raise ValueError("bind legacy workload before cutover activation")
            connection.execute("INSERT INTO v4_legacy_schedule_plans(goal_id,workspace_id,actor_id,goal_revision,task_queue,niche) VALUES(%s,%s,%s,%s,%s,%s)",(goal_id,self.workspace_id,self.subject_id,expected_revision,self.task_queue,niche))
            self._event(connection,goal_id,"legacy_schedule_bound",{"goal_revision":expected_revision,"task_queue":self.task_queue})
            current_authority(connection,self.workspace_id,self.subject_id)
            return {"goal_id":str(goal_id),"state":"bound"}

    def admit_scheduled(self, intent_id):
        require_fixture()
        with self._command("cycles:schedule") as connection:
            current_authority(connection,self.workspace_id,self.subject_id)
            goal, spec, intent = self._intent(connection,intent_id)
            plan = connection.execute("SELECT * FROM v4_legacy_schedule_plans WHERE goal_id=%s",(goal["id"],)).fetchone()
            cutover = connection.execute("SELECT * FROM v4_schedule_cutovers WHERE goal_id=%s FOR UPDATE",(goal["id"],)).fetchone()
            if not plan or plan["actor_id"] != self.subject_id or plan["task_queue"] != self.task_queue or not cutover or cutover["state"] != "active" or intent["due_at"] < cutover["first_v4_slot"]:
                raise PermissionError("current original active legacy schedule required")
            result = self._admit(connection,intent_id)
            if result["cycle_id"]:
                command = LegacyIntelligenceCommand(goal_id=goal["id"],niche=plan["niche"],request=CycleRequest(origin="scheduled",
                    expected_revision=intent["goal_revision"],slot_time=intent["due_at"],idempotency_key="legacy-schedule:"+str(intent_id)))
                self._materialize(connection,command,spec,result["cycle_id"])
            current_authority(connection,self.workspace_id,self.subject_id)
            return result

    def admit_legacy_tick(self, goal_id, *, schedule_id, scheduled_at, remote_id):
        """Commit a resumed Temporal tick, never send a workload from ingress."""
        require_fixture()
        if not isinstance(scheduled_at, datetime) or scheduled_at.tzinfo is None or scheduled_at.microsecond:
            raise ValueError("whole-second scheduled slot required")
        scheduled_at = scheduled_at.astimezone(timezone.utc)
        with self._command("cycles:schedule") as connection:
            goal, spec = self._policy_goal(connection, goal_id)
            from salience.cycles.governance import CycleGovernance
            CycleGovernance(self.database_url, workspace_id=self.workspace_id,
                subject_id=self.subject_id)._require_running(connection, goal_id)
            cutover = connection.execute("SELECT * FROM v4_schedule_cutovers WHERE goal_id=%s AND workspace_id=%s FOR UPDATE", (goal_id, self.workspace_id)).fetchone()
            plan = connection.execute("SELECT * FROM v4_legacy_schedule_plans WHERE goal_id=%s", (goal_id,)).fetchone()
            if not cutover or str(cutover["legacy_schedule_id"]) != str(schedule_id) or not plan or plan["actor_id"] != self.subject_id or plan["task_queue"] != self.task_queue:
                raise PermissionError("original scoped legacy schedule plan required")
            if cutover["state"] == "rollback_pending":
                raise RuntimeError("canonical rollback readback still pending")
            if cutover["state"] != "rolled_back" or scheduled_at <= cutover["rollback_after_slot"]:
                raise PermissionError("completed rollback and later scheduled slot required")
            schedule = schedule_row(connection, schedule_id, self.workspace_id, lock=True, temporal=True)
            require_native_binding(schedule, goal_id=goal_id, actor_id=self.subject_id, task_queue=self.task_queue, first_v4_slot=cutover["first_v4_slot"])
            metadata = schedule_metadata(schedule)
            expected_remote = str(schedule_id) if schedule.get("native_source") else f"salience-v4-legacy-fixture:{self.workspace_id}:{schedule_id}"
            if remote_id != expected_remote:
                raise PermissionError("current exact resumed Temporal schedule required")
            if schedule["status"] != "active" or schedule["content_program_id"] != spec.content_program_id or metadata["interval_seconds"] != spec.cadence.interval_seconds or metadata["remote_id"] != remote_id or metadata["resume_after"] != cutover["rollback_after_slot"].isoformat() or metadata["next_run_at"] is None or scheduled_at < metadata["next_run_at"]:
                raise PermissionError("current exact resumed Temporal schedule required")
            command = LegacyIntelligenceCommand(goal_id=goal_id, niche=plan["niche"], request=CycleRequest(
                origin="scheduled", expected_revision=goal["revision"], slot_time=scheduled_at,
                idempotency_key=f"legacy-temporal:{schedule_id}:{scheduled_at.isoformat()}"))
            result = self._submit(connection, command, cutover_poll=True)
            last_slot = schedule.get("native_source", {}).get("last_slot") if schedule.get("native_source") else metadata["last_slot"]
            if last_slot is None or scheduled_at > last_slot:
                if schedule.get("native_source"):
                    connection.execute("UPDATE v4_native_schedule_progress SET last_slot=%s,updated_at=clock_timestamp() WHERE schedule_id=%s", (scheduled_at, schedule_id))
                else:
                    connection.execute("UPDATE job_schedules SET payload=jsonb_set(payload,'{last_slot}',to_jsonb(%s::text)),updated_at=clock_timestamp() WHERE id=%s", (scheduled_at.isoformat(), schedule_id))
            current_authority(connection, self.workspace_id, self.subject_id)
            return result
