"""Canonical finite waits and an explicitly no-effects notification sink.

Temporal owns elapsed waiting. PostgreSQL owns eligibility, deadlines and
receipts; waking never delegates review authority or changes recovery identity.
"""
import asyncio
from datetime import timedelta
import logging
import os

from psycopg.types.json import Jsonb
from temporalio import activity
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.client import WorkflowExecutionStatus
from temporalio.service import RPCError
import psycopg

from salience.cycles.wait_workflow import LocalWaitWorkflow
from salience.cycles.admission import CycleAdmission
from salience.cycles.governance import CycleGovernance
from salience.cycles.authority import current_authority

LOGGER = logging.getLogger(__name__)


class FixtureNotificationSink:
    effect_id = "fixture.notification"

    def accept(self, connection, notification, case, payload):
        connection.execute("""INSERT INTO v4_notification_acceptances(notification_id,case_id,owner_id,payload)
            VALUES(%s,%s,%s,%s) ON CONFLICT(notification_id) DO NOTHING""",
            (notification["id"], case["id"], case["owner_id"], Jsonb(payload)))
        stored = connection.execute("SELECT payload FROM v4_notification_acceptances WHERE notification_id=%s", (notification["id"],)).fetchone()
        if stored["payload"] != payload:
            raise ValueError("fixture notification receipt binding mismatch")


class RuntimeWaits(CycleGovernance):
    def enroll_case(self, case_id):
        """Explicit current operator adoption of a retained pre-upgrade case."""
        with self._command("cycles:case_operator") as connection:
            case,goal,_,intent,cycle = self._case(connection,case_id)
            if case["state"] not in {"awaiting_review","retry_due","reconciling","rework_due"}:
                raise ValueError("active case required for deadline enrollment")
            existing = connection.execute("SELECT id FROM v4_runtime_waits WHERE case_id=%s AND revision=%s",(case_id,case["revision"])).fetchone()
            if existing:
                return existing["id"]
            job = connection.execute("""INSERT INTO v4_runtime_waits(workspace_id,subject_id,owner_id,case_id,revision,kind,due_at)
                VALUES(%s,%s,%s,%s,%s,'case',%s) RETURNING id""",(self.workspace_id,self.subject_id,case["owner_id"],case_id,case["revision"],case["deadline"])).fetchone()
            connection.execute("""INSERT INTO v4_notification_deliveries(notification_id)
                SELECT id FROM v4_case_notifications WHERE case_id=%s ON CONFLICT(notification_id) DO NOTHING""",(case_id,))
            self._event(connection,goal["id"],"case_timer_enrolled",{"wait_id":str(job["id"]),"case_id":str(case_id)},intent["id"],cycle["id"] if cycle else None)
            return job["id"]

    def pending(self, *, limit=32):
        if type(limit) is not int or not 1 <= limit <= 32:
            raise ValueError("bounded wait batch required")
        with self._command("cycles:case_operator") as connection:
            return connection.execute("""SELECT * FROM v4_runtime_waits WHERE workspace_id=%s
                AND state='pending' AND runtime_id IS NULL AND available_at<=clock_timestamp() ORDER BY due_at,id LIMIT %s""", (self.workspace_id,limit)).fetchall()

    def _job(self, connection, job_id, *, lock=False):
        job = connection.execute("SELECT * FROM v4_runtime_waits WHERE id=%s AND workspace_id=%s" + (" FOR UPDATE" if lock else ""), (job_id,self.workspace_id)).fetchone()
        if not job:
            raise PermissionError("wait outside current workspace")
        return job

    def binding(self, job_id):
        with self._command("cycles:case_operator") as connection:
            job = self._job(connection,job_id)
            return {"id":str(job["id"]),"deadline":job["due_at"].isoformat(),"state":job["state"]}

    def started(self, job_id, runtime_id):
        expected = "salience-v4-wait:"+str(job_id)
        if runtime_id != expected:
            raise ValueError("wait runtime binding mismatch")
        with self._command("cycles:case_operator") as connection:
            job = self._job(connection,job_id,lock=True)
            if job["state"] == "pending" and job["runtime_id"] is None:
                connection.execute("UPDATE v4_runtime_waits SET runtime_id=%s WHERE id=%s", (runtime_id,job_id))

    @staticmethod
    def _finish(connection, job, result, *, held=False):
        connection.execute("UPDATE v4_runtime_waits SET state=%s,result=%s WHERE id=%s", ("held" if held else "completed",Jsonb(result),job["id"]))
        return result

    def handoff_failed(self, job_id):
        with self._command("cycles:case_operator") as connection:
            job = self._job(connection,job_id,lock=True)
            if job["state"] != "pending" or job["runtime_id"] is not None:
                return
            attempts = job["handoff_attempts"]+1
            result = {"id":str(job_id),"state":"held_handoff","owner_id":str(job["owner_id"])} if attempts>=3 else None
            connection.execute("""UPDATE v4_runtime_waits SET handoff_attempts=%s,available_at=clock_timestamp()+interval '1 second',
                state=%s,result=%s WHERE id=%s""",(attempts,"held" if result else "pending",Jsonb(result) if result else None,job_id))

    def inspection_candidate(self):
        with self._command("cycles:case_operator") as connection:
            job = connection.execute("""SELECT * FROM v4_runtime_waits WHERE workspace_id=%s AND state='pending'
                AND runtime_id IS NOT NULL ORDER BY inspected_at,id LIMIT 1 FOR UPDATE SKIP LOCKED""",(self.workspace_id,)).fetchone()
            if job:
                connection.execute("UPDATE v4_runtime_waits SET inspected_at=clock_timestamp() WHERE id=%s",(job["id"],))
            return job

    def runtime_failed(self, job_id):
        with self._command("cycles:case_operator") as connection:
            job = self._job(connection,job_id,lock=True)
            if job["state"] == "pending":
                return self._finish(connection,job,{"id":str(job_id),"state":"held_runtime_failure","owner_id":str(job["owner_id"])},held=True)
            return job["result"]

    def fire(self, job_id):
        # Lock canonical goal/target before the job, matching review/close order.
        with self._command("cycles:case_operator") as connection:
            job = self._job(connection,job_id)
            if job["kind"] == "case":
                case,_,_,intent,cycle = self._case(connection,job["case_id"])
                job = self._job(connection,job_id,lock=True)
                if job["result"] is not None:
                    return job["result"]
                if case["revision"] != job["revision"] or case["state"] in {"terminal","resolved","suspended"} or (cycle and cycle["state"] == "closed"):
                    return self._finish(connection,job,{"id":str(job_id),"state":"obsolete"})
                now = connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
                if now < job["due_at"]:
                    raise ValueError("wait deadline not due")
                result = self._escalate_due(connection,case["id"])
                return self._finish(connection,job,result)
            # The timer operator cannot borrow its own grant to admit. Recheck
            # the recorded author's exact current grant in the same transaction.
            author = CycleAdmission(self.database_url,workspace_id=self.workspace_id,subject_id=job["subject_id"],trace_context=self.trace)
            goal,spec,intent = author._intent(connection,job["intent_id"])
            job = self._job(connection,job_id,lock=True)
            if job["result"] is not None:
                return job["result"]
            if intent["eligibility_revision"] != job["revision"] or connection.execute("SELECT 1 FROM v4_cycles WHERE intent_id=%s", (intent["id"],)).fetchone():
                return self._finish(connection,job,{"id":str(job_id),"state":"obsolete"})
            now = connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
            if now < job["due_at"]:
                raise ValueError("wait deadline not due")
            if job["kind"] == "review_required" or now < intent["due_at"] or now >= min(intent["expires_at"],spec.horizon_end):
                result = {"id":str(job_id),"state":"held_review" if job["kind"] == "review_required" else "held_expired_or_wait_limit", "owner_id":str(job["owner_id"])}
                author._event(connection,goal["id"],"intent_wait_held",result,intent["id"])
                return self._finish(connection,job,result,held=True)
            try:
                # A savepoint rolls back a wake if admission denies current
                # authority; no half-applied eligibility revision can escape.
                with connection.transaction():
                    current_authority(connection,self.workspace_id,job["subject_id"],"cycles:write")
                    self._require_running(connection,goal["id"])
                    author._wake(connection,intent["id"],expected_revision=job["revision"],review=False)
                    admitted = author._admit(connection,intent["id"])
                    current_authority(connection,self.workspace_id,job["subject_id"],"cycles:write")
            except (PermissionError,ValueError):
                result = {"id":str(job_id),"state":"held_authority_or_limit","owner_id":str(job["owner_id"])}
                self._event(connection,goal["id"],"intent_wait_held",result,intent["id"])
                return self._finish(connection,job,result,held=True)
            return self._finish(connection,job,{"id":str(job_id),"state":admitted["disposition"],"intent_id":str(intent["id"]),"cycle_id":str(admitted["cycle_id"]) if admitted["cycle_id"] else None})

    def deliver_one(self, *, sink=None):
        sink = sink or FixtureNotificationSink()
        if type(sink) is not FixtureNotificationSink:
            raise ValueError("only the database no-effects notification sink is supported")
        with self._command("cycles:case_operator") as connection:
            notification = connection.execute("""SELECT n.* FROM v4_case_notifications n
                JOIN v4_recovery_cases c ON c.id=n.case_id JOIN v4_notification_deliveries d ON d.notification_id=n.id
                WHERE c.workspace_id=%s AND d.state='pending' AND d.available_at<=clock_timestamp()
                ORDER BY n.created_at,n.id LIMIT 1""", (self.workspace_id,)).fetchone()
            if not notification:
                return False
            case,_,_,_,cycle = self._case(connection,notification["case_id"])
            delivery = connection.execute("SELECT * FROM v4_notification_deliveries WHERE notification_id=%s FOR UPDATE", (notification["id"],)).fetchone()
            if delivery["state"] != "pending":
                return True
            now = connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
            ack = connection.execute("SELECT 1 FROM v4_case_acks WHERE notification_id=%s", (notification["id"],)).fetchone()
            valid = notification["case_revision"] == case["revision"] and not (cycle and cycle["state"] == "closed") and (
                (notification["kind"] == "review_due" and case["state"] == "awaiting_review" and now < case["deadline"]) or
                (notification["kind"] == "escalation" and case["state"] == "suspended"))
            state,reason = ("suppressed","acknowledged" if ack else "obsolete")
            owner_available = connection.execute("SELECT 1 FROM identity_subjects WHERE id=%s AND workspace_id=%s AND enabled AND expires_at>clock_timestamp()",(case["owner_id"],self.workspace_id)).fetchone()
            if valid and not ack and not owner_available:
                state,reason = "held","owner_unavailable"
            if valid and not ack and owner_available:
                payload = {"notification_id":str(notification["id"]),"case_id":str(case["id"]),"case_revision":notification["case_revision"],"owner_id":str(case["owner_id"]),"kind":notification["kind"],"context_id":str(case["context_id"]) if case["context_id"] else None,"operation_id":str(case["operation_id"]) if case["operation_id"] else None,"deadline":case["deadline"].isoformat()}
                try:
                    with connection.transaction():
                        sink.accept(connection,notification,case,payload)
                    state,reason = "accepted","fixture_accepted"
                except (OSError,ValueError):
                    state,reason = ("held" if delivery["attempts"] >= 2 else "pending"),"fixture_unavailable"
            connection.execute("UPDATE v4_notification_deliveries SET state=%s,reason=%s,attempts=attempts+1,available_at=clock_timestamp()+interval '1 second' WHERE notification_id=%s", (state,reason,notification["id"]))
            return True




class WaitActivities:
    def __init__(self, waits):
        self.waits = waits

    @activity.defn(name="salience.v4.fixture_wait_binding")
    async def binding(self, job_id: str) -> dict:
        return await asyncio.to_thread(self.waits.binding,job_id)

    @activity.defn(name="salience.v4.fixture_wait_fire")
    async def fire(self, job_id: str) -> dict:
        return await asyncio.to_thread(self.waits.fire,job_id)


class FixtureWaitDriver:
    def __init__(self, client, waits, *, task_queue):
        if os.environ.get("SALIENCE_DEPLOYMENT_MODE") != "fixture" or not task_queue.startswith("salience-v4-local-") or os.environ.get("SALIENCE_EFFECTS_ENABLED","false").lower() != "false":
            raise ValueError("isolated effects-disabled fixture wait driver required")
        self.client,self.waits,self.task_queue = client,waits,task_queue

    async def dispatch_one(self):
        jobs = await asyncio.to_thread(self.waits.pending,limit=1)
        if not jobs:
            return False
        job = jobs[0]
        runtime_id = "salience-v4-wait:"+str(job["id"])
        try:
            await self.client.start_workflow(LocalWaitWorkflow.run,str(job["id"]),id=runtime_id,task_queue=self.task_queue,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,execution_timeout=timedelta(days=8),
                memo={"wait_id":str(job["id"]),"wait_workspace":str(self.waits.workspace_id)},rpc_timeout=timedelta(seconds=3))
        except WorkflowAlreadyStartedError:
            description = await self.client.get_workflow_handle(runtime_id).describe(rpc_timeout=timedelta(seconds=3))
            memo = await description.memo()
            if description.workflow_type != "SalienceLocalWaitWorkflow" or memo != {"wait_id":str(job["id"]),"wait_workspace":str(self.waits.workspace_id)}:
                raise ValueError("wait runtime already belongs to different work")
        except (OSError,TimeoutError,RPCError):
            await asyncio.to_thread(self.waits.handoff_failed,job["id"])
            return True
        await asyncio.to_thread(self.waits.started,job["id"],runtime_id)
        return True

    async def reconcile_one(self):
        job = await asyncio.to_thread(self.waits.inspection_candidate)
        if job is None:
            return False
        description = await self.client.get_workflow_handle(job["runtime_id"]).describe(rpc_timeout=timedelta(seconds=3))
        if description.status in {WorkflowExecutionStatus.FAILED,WorkflowExecutionStatus.CANCELED,
                                  WorkflowExecutionStatus.TERMINATED,WorkflowExecutionStatus.TIMED_OUT}:
            await asyncio.to_thread(self.waits.runtime_failed,job["id"])
        return True

    async def run_until_stopped(self, *, stop_event):
        while not stop_event.is_set():
            try:
                await self.dispatch_one()
                await self.reconcile_one()
                await asyncio.to_thread(self.waits.deliver_one)
            except (PermissionError,ValueError,OSError,TimeoutError,RPCError,psycopg.OperationalError):
                LOGGER.warning("fixture wait/delivery held; canonical records preserved")
            try:
                await asyncio.wait_for(stop_event.wait(),timeout=.25)
            except TimeoutError:
                pass
