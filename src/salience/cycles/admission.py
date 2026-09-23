"""Transactional dry-run admission slice; no workflow or provider dispatch."""

from contextlib import contextmanager
from datetime import timezone
from hashlib import sha256
import json
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from salience.cycles.contracts import GoalSpec
from salience.cycles.baselines import fixture_bundle, resolve_baseline
from salience.cycles.outbox import enqueue_cycle_message
from salience.observability.tracing import TraceContext


class CycleAdmission:
    def __init__(self, database_url, *, workspace_id, subject_id, trace_context=None):
        self.database_url = database_url
        self.workspace_id = UUID(str(workspace_id))
        self.subject_id = UUID(str(subject_id))
        self.trace = trace_context or TraceContext.new_root()

    @contextmanager
    def _command(self, scope):
        with psycopg.connect(self.database_url, row_factory=dict_row, connect_timeout=3) as connection:
            connection.execute("SET LOCAL statement_timeout='3s'")
            identity = connection.execute("SELECT issuer,subject FROM identity_subjects WHERE id=%s AND workspace_id=%s", (self.subject_id,self.workspace_id)).fetchone()
            if not identity or not connection.execute("SELECT * FROM public.p0_lock_identity(%s,%s,%s)", (identity["issuer"],identity["subject"],self.workspace_id)).fetchone():
                raise PermissionError("current subject authority required")
            grants = connection.execute("SELECT effect FROM permission_grants WHERE workspace_id=%s AND principal_type='identity' AND principal_id=%s AND scope=%s AND constraints='{}'::jsonb AND expires_at > clock_timestamp()", (self.workspace_id,str(self.subject_id),scope)).fetchall()
            effects = {row["effect"] for row in grants}
            if "allow" not in effects or "deny" in effects:
                raise PermissionError("current scoped grant required")
            yield connection

    def _goal(self, connection, goal_id):
        goal = connection.execute("SELECT * FROM v4_goals WHERE id=%s AND workspace_id=%s FOR UPDATE", (goal_id,self.workspace_id)).fetchone()
        if not goal:
            raise PermissionError("goal outside current scope")
        revision = connection.execute("SELECT payload FROM v4_goal_revisions WHERE goal_id=%s AND revision=%s", (goal_id,goal["revision"])).fetchone()
        if not revision:
            raise ValueError("goal revision payload unavailable")
        return goal, GoalSpec.model_validate(revision["payload"])

    def _intent(self, connection, intent_id):
        identity = connection.execute("SELECT goal_id FROM v4_cycle_intents WHERE id=%s", (intent_id,)).fetchone()
        if not identity:
            raise ValueError("unknown intent")
        goal, spec = self._goal(connection,identity["goal_id"])
        intent = connection.execute("SELECT * FROM v4_cycle_intents WHERE id=%s FOR UPDATE", (intent_id,)).fetchone()
        return goal, spec, intent

    def _cycle(self, connection, cycle_id):
        identity = connection.execute("SELECT intent_id FROM v4_cycles WHERE id=%s", (cycle_id,)).fetchone()
        if not identity:
            raise ValueError("unknown cycle")
        goal, spec, intent = self._intent(connection,identity["intent_id"])
        cycle = connection.execute("SELECT * FROM v4_cycles WHERE id=%s FOR UPDATE", (cycle_id,)).fetchone()
        return goal, spec, intent, cycle

    def _event(self, connection, goal_id, kind, payload, intent_id=None, cycle_id=None):
        connection.execute("INSERT INTO v4_cycle_events (id,workspace_id,subject_id,goal_id,intent_id,cycle_id,kind,traceparent,payload) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", (uuid4(),self.workspace_id,self.subject_id,goal_id,intent_id,cycle_id,kind,self.trace.to_carrier()["traceparent"],Jsonb(json.loads(json.dumps(payload,default=str)))))

    def create_goal(self, spec: GoalSpec):
        spec = GoalSpec.model_validate(spec.model_dump())
        with self._command("goals:write") as connection:
            now = connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
            if spec.horizon_end <= now:
                raise ValueError("goal horizon expired")
            goal_id = uuid4()
            connection.execute("INSERT INTO v4_goals (id,workspace_id,state) VALUES (%s,%s,'active')", (goal_id,self.workspace_id))
            connection.execute("INSERT INTO v4_goal_revisions (goal_id,revision,payload) VALUES (%s,1,%s)", (goal_id,Jsonb(spec.model_dump(mode="json"))))
            self._event(connection,goal_id,"goal_created",{"revision":1,"dry_run":True})
            return goal_id

    def set_goal_state(self, goal_id, state):
        transitions = {"draft":{"active","cancelled"}, "active":{"paused","completed","cancelled"}, "paused":{"active","completed","cancelled"}, "completed":set(), "cancelled":set()}
        with self._command("goals:write") as connection:
            goal, _ = self._goal(connection,goal_id)
            if state == goal["state"]:
                return
            if state not in transitions[goal["state"]]:
                raise ValueError("illegal goal state transition")
            connection.execute("UPDATE v4_goals SET state=%s WHERE id=%s", (state,goal_id))
            self._event(connection,goal_id,"goal_state",{"from":goal["state"],"to":state})

    def revise_goal(self, goal_id, spec: GoalSpec, *, expected_revision, idempotency_key, reason):
        spec = GoalSpec.model_validate(spec.model_dump())
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("explicit positive expected revision required")
        if not isinstance(idempotency_key,str) or not idempotency_key.strip() or len(idempotency_key)>256:
            raise ValueError("bounded idempotency key required")
        if not isinstance(reason,str) or not reason.strip() or len(reason)>2000:
            raise ValueError("bounded revision reason required")
        payload = spec.model_dump(mode="json")
        fingerprint = sha256(json.dumps([str(self.subject_id),expected_revision,payload,reason],sort_keys=True,separators=(",",":")).encode()).hexdigest()
        with self._command("goals:write") as connection:
            goal, _ = self._goal(connection,goal_id)
            existing = connection.execute("SELECT * FROM v4_goal_commands WHERE goal_id=%s AND idempotency_key=%s",(goal_id,idempotency_key)).fetchone()
            if existing:
                if existing["fingerprint"]!=fingerprint:
                    raise ValueError("revision command fingerprint conflict")
                return existing["resulting_revision"]
            if goal["state"] in {"completed","cancelled"}:
                raise ValueError("terminal goal cannot be revised")
            if goal["revision"]!=expected_revision:
                raise ValueError("stale expected goal revision")
            now = connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
            if spec.horizon_end<=now:
                raise ValueError("goal horizon expired")
            resulting_revision=expected_revision+1
            connection.execute("INSERT INTO v4_goal_revisions (goal_id,revision,payload) VALUES (%s,%s,%s)",(goal_id,resulting_revision,Jsonb(payload)))
            connection.execute("UPDATE v4_goals SET revision=%s WHERE id=%s",(resulting_revision,goal_id))
            connection.execute("INSERT INTO v4_goal_commands (goal_id,idempotency_key,subject_id,fingerprint,expected_revision,resulting_revision,reason) VALUES (%s,%s,%s,%s,%s,%s,%s)",(goal_id,idempotency_key,self.subject_id,fingerprint,expected_revision,resulting_revision,reason))
            self._event(connection,goal_id,"goal_revised",{"from_revision":expected_revision,"to_revision":resulting_revision,"reason":reason,"idempotency_key":idempotency_key})
            return resulting_revision

    def approve_baseline(self, goal_id, *, expected_revision, expires_at, reason):
        if type(expected_revision) is not int or expected_revision<1 or not isinstance(reason,str) or not reason.strip() or len(reason)>2000:
            raise ValueError("explicit revision and bounded approval reason required")
        if expires_at.tzinfo is None:
            raise ValueError("aware approval expiry required")
        bundle=fixture_bundle()
        with self._command("goals:approve") as connection:
            goal,spec=self._goal(connection,goal_id)
            existing=connection.execute("SELECT * FROM v4_goal_baselines WHERE goal_id=%s AND goal_revision=%s",(goal_id,expected_revision)).fetchone()
            if existing:
                if existing["bundle"]!=bundle or existing["subject_id"]!=self.subject_id or existing["expires_at"]!=expires_at or existing["reason"]!=reason:
                    raise ValueError("baseline approval fingerprint conflict")
                return existing["approval_id"]
            now=connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
            if goal["state"] not in {"active","paused"} or goal["revision"]!=expected_revision:
                raise ValueError("current nonterminal goal revision required")
            if not now<expires_at<=spec.horizon_end:
                raise ValueError("approval expiry outside goal horizon")
            approval_id=uuid4()
            connection.execute("INSERT INTO v4_goal_baselines (approval_id,goal_id,goal_revision,subject_id,bundle,expires_at,reason,traceparent) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",(approval_id,goal_id,expected_revision,self.subject_id,Jsonb(bundle),expires_at,reason,self.trace.to_carrier()["traceparent"]))
            self._event(connection,goal_id,"baseline_approved",{"approval_id":str(approval_id),"goal_revision":expected_revision,"reason":reason,"expires_at":expires_at})
            return approval_id

    def revoke_baseline(self, goal_id, *, approval_id, reason):
        if not isinstance(reason,str) or not reason.strip() or len(reason)>2000:
            raise ValueError("bounded revocation reason required")
        with self._command("goals:approve") as connection:
            self._goal(connection,goal_id)
            approval=connection.execute("SELECT 1 FROM v4_goal_baselines WHERE approval_id=%s AND goal_id=%s",(approval_id,goal_id)).fetchone()
            if not approval:
                raise PermissionError("approval outside goal scope")
            existing=connection.execute("SELECT * FROM v4_baseline_revocations WHERE approval_id=%s",(approval_id,)).fetchone()
            if existing:
                if existing["subject_id"]!=self.subject_id or existing["reason"]!=reason:
                    raise ValueError("baseline revocation fingerprint conflict")
                return
            connection.execute("INSERT INTO v4_baseline_revocations (approval_id,subject_id,reason,traceparent) VALUES (%s,%s,%s,%s)",(approval_id,self.subject_id,reason,self.trace.to_carrier()["traceparent"]))
            self._event(connection,goal_id,"baseline_revoked",{"approval_id":str(approval_id),"reason":reason})

    def request_intent(self, goal_id, *, slot, due_at, expires_at, predecessor_cycle_id=None):
        if not isinstance(slot,str) or not slot.strip() or len(slot)>256 or due_at.tzinfo is None or expires_at.tzinfo is None or due_at>=expires_at:
            raise ValueError("bounded slot and aware valid time window required")
        with self._command("cycles:write") as connection:
            goal, spec = self._goal(connection,goal_id)
            existing = connection.execute("SELECT * FROM v4_cycle_intents WHERE goal_id=%s AND slot=%s", (goal_id,slot)).fetchone()
            bound_revision = existing["goal_revision"] if existing else goal["revision"]
            fingerprint = sha256(json.dumps([str(goal_id),bound_revision,slot,due_at.astimezone(timezone.utc).isoformat(),expires_at.astimezone(timezone.utc).isoformat()]).encode()).hexdigest()
            if existing and existing["fingerprint"] != fingerprint:
                raise ValueError("intent fingerprint conflict")
            if existing and not predecessor_cycle_id:
                return existing["id"]
            if existing and predecessor_cycle_id:
                linked = connection.execute("SELECT successor_intent_id FROM v4_intent_successors WHERE predecessor_cycle_id=%s", (predecessor_cycle_id,)).fetchone()
                if linked:
                    if linked["successor_intent_id"] != existing["id"]:
                        raise ValueError("strategic wake already coalesced")
                    return existing["id"]
            now = connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
            if goal["state"] != "active" or expires_at <= now or expires_at > spec.horizon_end:
                raise ValueError("intent outside active goal horizon")
            predecessor = None
            if predecessor_cycle_id:
                predecessor = connection.execute("SELECT cycle.*, intent.goal_id, intent.slot FROM v4_cycles AS cycle JOIN v4_cycle_intents AS intent ON intent.id=cycle.intent_id WHERE cycle.id=%s", (predecessor_cycle_id,)).fetchone()
                if not predecessor or predecessor["goal_id"] != goal_id or predecessor["state"] != "closed" or predecessor["disposition"] not in {"defer","abstain"} or predecessor["slot"] == slot or due_at < predecessor["closed_at"]:
                    raise ValueError("successor requires a closed strategic disposition and later slot")
            intent_id = existing["id"] if existing else uuid4()
            if not existing:
                connection.execute("INSERT INTO v4_cycle_intents (id,goal_id,goal_revision,slot,fingerprint,due_at,expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s)", (intent_id,goal_id,goal["revision"],slot,fingerprint,due_at,expires_at))
                self._event(connection,goal_id,"intent_created",{"slot":slot},intent_id)
            if predecessor:
                linked = connection.execute("SELECT successor_intent_id FROM v4_intent_successors WHERE predecessor_cycle_id=%s", (predecessor_cycle_id,)).fetchone()
                if linked and linked["successor_intent_id"] != intent_id:
                    raise ValueError("strategic wake already coalesced")
                if not linked:
                    connection.execute("INSERT INTO v4_intent_successors VALUES (%s,%s,DEFAULT)", (predecessor_cycle_id,intent_id))
                    self._event(connection,goal_id,"successor_authorized",{"predecessor_cycle_id":predecessor_cycle_id},intent_id)
            return intent_id

    def admit(self, intent_id):
        with self._command("cycles:write") as connection:
            goal, spec, intent = self._intent(connection,intent_id)
            existing = connection.execute("SELECT * FROM v4_admissions WHERE intent_id=%s AND eligibility_revision=%s", (intent_id,intent["eligibility_revision"])).fetchone()
            if existing:
                return existing
            now = connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
            counts = connection.execute("SELECT count(*) AS total, count(*) FILTER (WHERE cycle.state!='closed') AS active FROM v4_cycles AS cycle JOIN v4_cycle_intents AS intent ON intent.id=cycle.intent_id WHERE intent.goal_id=%s", (goal["id"],)).fetchone()
            baseline=resolve_baseline(connection,goal["id"],goal["revision"])
            disposition, reason = "admitted", "dry_run_baseline"
            if goal["state"] != "active" or goal["revision"] != intent["goal_revision"] or now >= min(intent["expires_at"],spec.horizon_end):
                disposition, reason = "denied", "inactive_stale_or_expired"
            elif counts["total"] >= spec.max_cycles:
                disposition, reason = "denied", "cycle_quota"
            elif now < intent["due_at"]:
                disposition, reason = "deferred", "not_due"
            elif baseline is None:
                disposition, reason = "review_required", "baseline_approval_required"
            elif spec.review_required and not intent["reviewed"]:
                disposition, reason = "review_required", "bound_review_required"
            elif counts["active"] >= spec.max_concurrent:
                disposition, reason = "deferred", "capacity"
            cycle_id = None
            if disposition == "admitted":
                cycle_id, context_id, operation_id = uuid4(),uuid4(),uuid4()
                connection.execute("INSERT INTO v4_cycles (id,intent_id,context_id,operation_id,state) VALUES (%s,%s,%s,%s,'runnable')", (cycle_id,intent_id,context_id,operation_id))
                payload = spec.model_dump(mode="json") | {"cycle_id":str(cycle_id),"goal_id":str(goal["id"]),"goal_revision":goal["revision"],"workspace_id":str(self.workspace_id),"subject_id":str(self.subject_id),"evidence_cutoff":now.isoformat(),"production_effects_enabled":False,"traceparent":self.trace.to_carrier()["traceparent"],"assignment_id":None}
                payload.update(baseline["bundle"] | {"baseline_approval_id":str(baseline["approval_id"])})
                connection.execute("INSERT INTO v4_run_contexts (id,cycle_id,payload) VALUES (%s,%s,%s)", (context_id,cycle_id,Jsonb(payload)))
                enqueue_cycle_message(connection,workspace_id=self.workspace_id,subject_id=self.subject_id,goal_id=goal["id"],intent_id=intent_id,cycle_id=cycle_id,kind="start",payload={"context_id":str(context_id),"operation_id":str(operation_id)},traceparent=self.trace.to_carrier()["traceparent"])
            result = connection.execute("INSERT INTO v4_admissions (id,intent_id,eligibility_revision,disposition,reason,cycle_id) VALUES (%s,%s,%s,%s,%s,%s) RETURNING *", (uuid4(),intent_id,intent["eligibility_revision"],disposition,reason,cycle_id)).fetchone()
            self._event(connection,goal["id"],"admission",{"disposition":disposition,"reason":reason,"eligibility_revision":intent["eligibility_revision"]},intent_id,cycle_id)
            return result

    def wake(self, intent_id, *, expected_revision, review=False):
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("explicit positive eligibility revision required")
        with self._command("cycles:review" if review else "cycles:write") as connection:
            goal, spec, intent = self._intent(connection,intent_id)
            if goal["revision"] != intent["goal_revision"]:
                raise ValueError("stale goal revision cannot wake")
            now = connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
            if goal["state"] != "active" or now >= min(intent["expires_at"],spec.horizon_end):
                raise ValueError("wake expired or cancelled")
            if intent["eligibility_revision"] > expected_revision:
                return intent["eligibility_revision"]
            if intent["eligibility_revision"] != expected_revision or expected_revision > spec.max_wakes:
                raise ValueError("wake revision or limit exceeded")
            record = connection.execute("SELECT disposition FROM v4_admissions WHERE intent_id=%s AND eligibility_revision=%s", (intent_id,expected_revision)).fetchone()
            if not record or record["disposition"] not in {"deferred","review_required"}:
                raise ValueError("only a pre-admission wait can wake")
            if record["disposition"] == "review_required" and not review:
                raise PermissionError("bound review required")
            if now < intent["due_at"]:
                raise ValueError("intent not due")
            connection.execute("UPDATE v4_cycle_intents SET eligibility_revision=eligibility_revision+1, reviewed=reviewed OR %s WHERE id=%s", (review,intent_id))
            self._event(connection,goal["id"],"intent_wake",{"expected_revision":expected_revision,"review":review},intent_id)
            return expected_revision+1

    def recover(self, cycle_id, *, state):
        if state not in {"runnable","retry_due","reconciling"}:
            raise ValueError("typed recovery state required")
        with self._command("cycles:write") as connection:
            goal, spec, intent, cycle = self._cycle(connection,cycle_id)
            now = connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
            if cycle["state"] == "closed":
                raise ValueError("closed cycles never resume")
            if goal["revision"] != intent["goal_revision"]:
                raise ValueError("stale goal revision requires renewed authority")
            context=connection.execute("SELECT payload FROM v4_run_contexts WHERE id=%s AND cycle_id=%s",(cycle["context_id"],cycle_id)).fetchone()
            baseline_id=context["payload"].get("baseline_approval_id") if context else None
            if baseline_id is None or resolve_baseline(connection,goal["id"],goal["revision"],approval_id=baseline_id) is None:
                raise ValueError("current approved baseline required for recovery")
            if goal["state"] != "active" or now >= spec.horizon_end:
                raise ValueError("current goal does not allow recovery")
            if cycle["state"] == state:
                return cycle
            count = cycle["recovery_count"] + (state != "runnable")
            if count > spec.max_wakes:
                raise ValueError("recovery limit exceeded")
            updated = connection.execute("UPDATE v4_cycles SET state=%s,recovery_count=%s WHERE id=%s RETURNING *", (state,count,cycle_id)).fetchone()
            enqueue_cycle_message(connection,workspace_id=self.workspace_id,subject_id=self.subject_id,goal_id=goal["id"],intent_id=intent["id"],cycle_id=cycle_id,kind="recovery",payload={"state":state,"operation_id":str(cycle["operation_id"])},traceparent=self.trace.to_carrier()["traceparent"])
            self._event(connection,goal["id"],"cycle_recovery",{"state":state},intent["id"],cycle_id)
            return updated

    def close(self, cycle_id, *, disposition, reason):
        if disposition not in {"defer","abstain","completed","cancelled"} or not reason.strip() or len(reason)>2000:
            raise ValueError("bounded terminal disposition and reason required")
        with self._command("cycles:write") as connection:
            goal, _, intent, cycle = self._cycle(connection,cycle_id)
            if cycle["state"] == "closed":
                if (cycle["disposition"],cycle["reason"]) != (disposition,reason):
                    raise ValueError("closed disposition is immutable")
                return cycle
            updated = connection.execute("UPDATE v4_cycles SET state='closed',disposition=%s,reason=%s,closed_at=clock_timestamp() WHERE id=%s RETURNING *", (disposition,reason,cycle_id)).fetchone()
            enqueue_cycle_message(connection,workspace_id=self.workspace_id,subject_id=self.subject_id,goal_id=goal["id"],intent_id=intent["id"],cycle_id=cycle_id,kind="close",payload={"disposition":disposition,"operation_id":str(cycle["operation_id"])},traceparent=self.trace.to_carrier()["traceparent"])
            self._event(connection,goal["id"],"cycle_closed",{"disposition":disposition,"reason":reason},intent["id"],cycle_id)
            return updated
