"""Internal fixture stop, permit and bound recovery commands."""

from datetime import datetime, timedelta
from hashlib import sha256
import json
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from psycopg.types.json import Jsonb

from salience.cycles.admission import CycleAdmission
from salience.cycles.authority import context_authorized
from salience.cycles.baselines import resolve_baseline
from salience.cycles.contracts import GoalSpecV2, GoalSpecV3
from salience.cycles.outbox import enqueue_cycle_message


def _fingerprint(values):
    return sha256(json.dumps(values, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def _bounded_key(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError("bounded idempotency key required")
    return value


def _bounded_reason(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 2000:
        raise ValueError("bounded reason required")
    return value


class PermitRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    context_id: UUID
    operation_id: UUID
    expected_goal_revision: int = Field(strict=True, ge=1)
    account_ref: Literal["fixture-account"]
    purpose: Literal["fixture_execution"]
    effect: Literal["fixture.noop"]
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    ttl_seconds: int = Field(strict=True, ge=1, le=60)


class ReviewResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    review_id: UUID
    expected_revision: int = Field(strict=True, ge=1)
    context_id: UUID | None
    operation_id: UUID | None
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    account_ref: Literal["fixture-account"]
    purpose: Literal["fixture_execution"]
    action: Literal["approve_resume", "reject"]
    reason: str = Field(min_length=1, max_length=2000)


class CycleGovernance(CycleAdmission):
    def _stop_scope(self, connection, scope_key):
        connection.execute("INSERT INTO v4_stop_scopes (workspace_id,scope_key) VALUES (%s,%s) ON CONFLICT DO NOTHING", (self.workspace_id, scope_key))
        return connection.execute("SELECT * FROM v4_stop_scopes WHERE workspace_id=%s AND scope_key=%s FOR UPDATE", (self.workspace_id,scope_key)).fetchone()

    def _stop_rows(self, connection, goal_id):
        workspace = self._stop_scope(connection,"workspace")
        goal = self._stop_scope(connection,"goal:"+str(goal_id))
        return workspace,goal

    def _require_running(self, connection, goal_id):
        workspace,goal = self._stop_rows(connection,goal_id)
        if workspace["stopped"] or goal["stopped"]:
            raise PermissionError("current stop blocks new permit or resume")
        return workspace,goal

    def set_stop(self, *, goal_id=None, stopped, expected_revision, idempotency_key, reason):
        _bounded_key(idempotency_key)
        _bounded_reason(reason)
        if type(stopped) is not bool or type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("explicit stop state and expected revision required")
        scope_key = "workspace" if goal_id is None else "goal:"+str(UUID(str(goal_id)))
        fingerprint = _fingerprint([str(self.subject_id),scope_key,stopped,expected_revision,reason])
        with self._command("cycles:stop") as connection:
            if goal_id is not None:
                self._goal(connection,goal_id)
            row = self._stop_scope(connection,scope_key)
            prior = connection.execute("SELECT * FROM v4_stop_commands WHERE workspace_id=%s AND scope_key=%s AND idempotency_key=%s",(self.workspace_id,scope_key,idempotency_key)).fetchone()
            if prior:
                if prior["fingerprint"] != fingerprint:
                    raise ValueError("stop command fingerprint conflict")
                return {"scope_key":scope_key,"revision":prior["resulting_revision"],"stopped":prior["stopped"]}
            if row["revision"] != expected_revision:
                raise ValueError("stale stop revision")
            revision = row["revision"]
            if row["stopped"] != stopped:
                revision += 1
                connection.execute("UPDATE v4_stop_scopes SET stopped=%s,revision=%s,changed_by=%s,reason=%s,changed_at=clock_timestamp() WHERE workspace_id=%s AND scope_key=%s",(stopped,revision,self.subject_id,reason,self.workspace_id,scope_key))
            connection.execute("INSERT INTO v4_stop_commands (id,workspace_id,scope_key,idempotency_key,fingerprint,actor_id,resulting_revision,stopped,traceparent) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",(uuid4(),self.workspace_id,scope_key,idempotency_key,fingerprint,self.subject_id,revision,stopped,self.trace.to_carrier()["traceparent"]))
            return {"scope_key":scope_key,"revision":revision,"stopped":stopped}

    def _eligible_cycle(self, connection, cycle_id, request=None):
        goal,spec,intent,cycle = self._cycle(connection,cycle_id)
        context = connection.execute("SELECT payload FROM v4_run_contexts WHERE id=%s AND cycle_id=%s",(cycle["context_id"],cycle_id)).fetchone()
        now = connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
        if not isinstance(spec,GoalSpecV2) or not context or cycle["state"] != "runnable" or goal["state"] != "active" or goal["revision"] != intent["goal_revision"] or now >= spec.horizon_end:
            raise ValueError("current runnable V2 or V3 cycle required")
        if not context_authorized(connection,context["payload"],self.workspace_id,self.subject_id):
            raise PermissionError("current frozen context authority required")
        baseline_id = context["payload"].get("baseline_approval_id")
        if baseline_id is None or resolve_baseline(connection,goal["id"],goal["revision"],approval_id=baseline_id) is None:
            raise PermissionError("current approved baseline required")
        if request is not None:
            if request.expected_goal_revision != goal["revision"] or request.context_id != cycle["context_id"] or request.operation_id != cycle["operation_id"] or request.account_ref not in spec.account_refs:
                raise PermissionError("permit request binding mismatch")
        return goal,spec,intent,cycle,context["payload"],now

    def issue_permit(self, cycle_id, request: PermitRequest, *, idempotency_key):
        _bounded_key(idempotency_key)
        request = PermitRequest.model_validate(request)
        fingerprint = _fingerprint(request.model_dump(mode="json"))
        with self._command("cycles:permit") as connection:
            self._cycle(connection,cycle_id)
            claim = connection.execute("SELECT * FROM v4_permit_claims WHERE cycle_id=%s FOR UPDATE",(cycle_id,)).fetchone()
            if claim:
                current = connection.execute("SELECT * FROM v4_dispatch_permits WHERE id=%s",(claim["current_permit_id"],)).fetchone()
                if current["subject_id"] != self.subject_id:
                    raise PermissionError("permit subject binding mismatch")
                if claim["request_fingerprint"] != fingerprint:
                    raise ValueError("permit request fingerprint conflict")
                prior = connection.execute("SELECT * FROM v4_dispatch_permits WHERE claim_id=%s AND idempotency_key=%s",(claim["id"],idempotency_key)).fetchone()
                if prior:
                    return self._permit_result(prior,claim)
            goal,spec,intent,cycle,context,now = self._eligible_cycle(connection,cycle_id,request)
            workspace_stop,goal_stop = self._require_running(connection,goal["id"])
            now = connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
            if now >= datetime.fromisoformat(context["execution_deadline"]):
                raise PermissionError("frozen context execution window expired")
            if claim:
                current = connection.execute("SELECT * FROM v4_dispatch_permits WHERE id=%s",(claim["current_permit_id"],)).fetchone()
                if claim["state"] != "issued" or current["expires_at"] > now:
                    raise ValueError("operation already has a live or uncertain claim")
                version = current["version"]+1
            else:
                claim = connection.execute("INSERT INTO v4_permit_claims (id,cycle_id,operation_id,context_id,request_fingerprint) VALUES (%s,%s,%s,%s,%s) RETURNING *",(uuid4(),cycle_id,cycle["operation_id"],cycle["context_id"],fingerprint)).fetchone()
                version = 1
            reservation_id = None
            if isinstance(spec,GoalSpecV3):
                allocation = connection.execute("SELECT * FROM v4_cycle_allocations WHERE cycle_id=%s AND workspace_id=%s",(cycle_id,self.workspace_id)).fetchone()
                if not allocation:
                    raise PermissionError("canonical allocation required")
                budgets = connection.execute("SELECT * FROM public.v4_lock_budgets(%s,%s)",(list(sorted(allocation["budget_ids"],key=str)),self.workspace_id)).fetchall()
                if len(budgets) != len(allocation["budget_ids"]) or any(budget["status"] != "active" for budget in budgets) or not allocation["period_start"] <= now < allocation["period_end"]:
                    raise PermissionError("current scoped allocation budgets required")
                reservations = connection.execute("SELECT mapping.reservation_id,reservation.status FROM v4_allocation_reservations AS mapping JOIN budget_reservations AS reservation ON reservation.id=mapping.reservation_id JOIN v4_cost_operations AS operation ON operation.id=mapping.operation_id AND operation.allocation_id=mapping.allocation_id WHERE mapping.allocation_id=%s AND mapping.operation_id=%s ORDER BY mapping.budget_id FOR UPDATE OF reservation",(allocation["id"],cycle["operation_id"])).fetchall()
                if len(reservations) != len(allocation["budget_ids"]) or any(reservation["status"] != "reserved" for reservation in reservations):
                    raise PermissionError("all current operation reservations required")
                reservation_id = reservations[0]["reservation_id"]
            expiry = min(now+timedelta(seconds=request.ttl_seconds),datetime.fromisoformat(context["execution_deadline"]))
            if expiry <= now:
                raise ValueError("permit execution window expired")
            permit = connection.execute("INSERT INTO v4_dispatch_permits (id,claim_id,version,idempotency_key,fingerprint,subject_id,goal_revision,workspace_stop_revision,goal_stop_revision,reservation_id,request,expires_at,traceparent) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",(uuid4(),claim["id"],version,idempotency_key,fingerprint,self.subject_id,goal["revision"],workspace_stop["revision"],goal_stop["revision"],reservation_id,Jsonb(request.model_dump(mode="json")),expiry,self.trace.to_carrier()["traceparent"])).fetchone()
            connection.execute("UPDATE v4_permit_claims SET current_permit_id=%s,revision=revision+1 WHERE id=%s",(permit["id"],claim["id"]))
            self._event(connection,goal["id"],"permit_issued",{"permit_id":str(permit["id"]),"claim_id":str(claim["id"]),"version":version},intent["id"],cycle_id)
            return self._permit_result(permit,claim)

    @staticmethod
    def _permit_result(permit,claim):
        return {"permit_id":str(permit["id"]),"claim_id":str(claim["id"]),"cycle_id":str(claim["cycle_id"]),"operation_id":str(claim["operation_id"]),"expires_at":permit["expires_at"].isoformat(),"version":permit["version"]}

    def claim_permit(self, permit_id):
        with self._command("cycles:permit") as connection:
            identity = connection.execute("SELECT claim.cycle_id FROM v4_dispatch_permits AS permit JOIN v4_permit_claims AS claim ON claim.id=permit.claim_id WHERE permit.id=%s",(permit_id,)).fetchone()
            if not identity:
                raise PermissionError("permit outside scope")
            self._cycle(connection,identity["cycle_id"])
            claim = connection.execute("SELECT * FROM v4_permit_claims WHERE cycle_id=%s FOR UPDATE",(identity["cycle_id"],)).fetchone()
            permit = connection.execute("SELECT * FROM v4_dispatch_permits WHERE id=%s AND claim_id=%s",(permit_id,claim["id"])).fetchone()
            if permit["subject_id"] != self.subject_id:
                raise PermissionError("permit subject binding mismatch")
            if permit["id"] == claim["current_permit_id"] and claim["state"] in {"claimed","unknown"}:
                return self._permit_result(permit,claim) | {"dispatch_allowed":False,"state":claim["state"]}
            goal,spec,intent,cycle,_,now = self._eligible_cycle(connection,identity["cycle_id"])
            workspace_stop,goal_stop = self._require_running(connection,goal["id"])
            now = connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
            if permit["id"] != claim["current_permit_id"] or now >= permit["expires_at"] or permit["goal_revision"] != goal["revision"] or (permit["workspace_stop_revision"],permit["goal_stop_revision"]) != (workspace_stop["revision"],goal_stop["revision"]):
                raise PermissionError("permit expired or authority changed")
            if claim["state"] != "issued":
                raise PermissionError("uncertain or resolved claim cannot dispatch")
            if isinstance(spec,GoalSpecV3):
                self._cost_ledger(connection)._command(cycle["id"],operation_id=cycle["operation_id"],action="dispatch",idempotency_key="permit-dispatch:"+str(permit_id),proof_ref="fixture-receipt:permit-claim:"+str(permit_id),scope="cycles:permit")
            if connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"] >= permit["expires_at"]:
                raise PermissionError("permit expired before claim commit")
            connection.execute("UPDATE v4_permit_claims SET state='claimed',claimed_at=clock_timestamp(),revision=revision+1 WHERE id=%s",(claim["id"],))
            self._event(connection,goal["id"],"permit_claimed",{"permit_id":str(permit_id)},intent["id"],cycle["id"])
            return self._permit_result(permit,claim) | {"dispatch_allowed":True,"state":"claimed"}

    def mark_unknown(self, permit_id):
        with self._command("cycles:permit") as connection:
            identity = connection.execute("SELECT claim.cycle_id FROM v4_dispatch_permits AS permit JOIN v4_permit_claims AS claim ON claim.id=permit.claim_id WHERE permit.id=%s",(permit_id,)).fetchone()
            if not identity:
                raise PermissionError("permit outside scope")
            goal,spec,intent,cycle = self._cycle(connection,identity["cycle_id"])
            claim = connection.execute("SELECT * FROM v4_permit_claims WHERE cycle_id=%s FOR UPDATE",(cycle["id"],)).fetchone()
            permit = connection.execute("SELECT subject_id FROM v4_dispatch_permits WHERE id=%s AND claim_id=%s",(permit_id,claim["id"])).fetchone()
            if permit["subject_id"] != self.subject_id:
                raise PermissionError("permit subject binding mismatch")
            if claim["current_permit_id"] != UUID(str(permit_id)) or claim["state"] not in {"claimed","unknown"}:
                raise ValueError("only the current claimed permit can become unknown")
            if claim["state"] == "claimed":
                if isinstance(spec,GoalSpecV3):
                    self._cost_ledger(connection)._command(cycle["id"],operation_id=cycle["operation_id"],action="unknown",idempotency_key="permit-unknown:"+str(permit_id),proof_ref="fixture-receipt:permit-unknown:"+str(permit_id),scope="cycles:permit")
                connection.execute("UPDATE v4_permit_claims SET state='unknown',revision=revision+1 WHERE id=%s",(claim["id"],))
                self._event(connection,goal["id"],"permit_unknown",{"permit_id":str(permit_id)},intent["id"],cycle["id"])
            return {"claim_id":str(claim["id"]),"state":"unknown"}

    def _case(self, connection, case_id):
        identity = connection.execute("SELECT goal_id,target_intent_id,target_cycle_id FROM v4_recovery_cases WHERE id=%s AND workspace_id=%s",(case_id,self.workspace_id)).fetchone()
        if not identity:
            raise PermissionError("case outside workspace scope")
        if identity["target_cycle_id"]:
            goal,spec,intent,cycle = self._cycle(connection,identity["target_cycle_id"])
        else:
            goal,spec,intent = self._intent(connection,identity["target_intent_id"])
            cycle = None
        case = connection.execute("SELECT * FROM v4_recovery_cases WHERE id=%s FOR UPDATE",(case_id,)).fetchone()
        if case["goal_id"] != goal["id"] or case["workspace_id"] != self.workspace_id:
            raise PermissionError("case target binding mismatch")
        return case,goal,spec,intent,cycle

    def _case_receipt(self, connection, case, key, fingerprint):
        prior = connection.execute("SELECT fingerprint,result FROM v4_case_commands WHERE case_id=%s AND idempotency_key=%s",(case["id"],key)).fetchone()
        if prior and prior["fingerprint"] != fingerprint:
            raise ValueError("case command fingerprint conflict")
        return prior["result"] if prior else None

    def _case_record(self, connection, case, goal, intent, cycle, *, key, fingerprint, action, result, revision):
        command_id = uuid4()
        connection.execute("INSERT INTO v4_case_commands (id,case_id,idempotency_key,fingerprint,action,actor_id,result) VALUES (%s,%s,%s,%s,%s,%s,%s)",(command_id,case["id"],key,fingerprint,action,self.subject_id,Jsonb(result)))
        connection.execute("INSERT INTO v4_case_events (id,case_id,case_revision,action,actor_id,traceparent,payload) VALUES (%s,%s,%s,%s,%s,%s,%s)",(uuid4(),case["id"],revision,action,self.subject_id,self.trace.to_carrier()["traceparent"],Jsonb(result)))
        self._event(connection,goal["id"],"case_"+action,result,intent["id"],cycle["id"] if cycle else None)
        return command_id

    def open_case(self, target_id, *, target, kind, failure_class, owner_id, deadline, reason, artifact_sha256, account_ref):
        _bounded_reason(reason)
        if target not in {"intent","cycle"} or kind not in {"admission_review","retry","reconciliation","rework","manual_review"} or failure_class not in {"policy_denial","technical_failure","quality_failure","unknown_effect","admission_review","deadline","manual_review"}:
            raise ValueError("typed case target, kind and failure class required")
        allowed_failures = {"admission_review":{"admission_review","policy_denial"},"retry":{"technical_failure","deadline"},"reconciliation":{"unknown_effect"},"rework":{"quality_failure"},"manual_review":{"manual_review","policy_denial"}}
        if failure_class not in allowed_failures[kind]:
            raise ValueError("case failure class cannot bypass typed recovery")
        if (target == "intent") != (kind == "admission_review"):
            raise ValueError("admission review must target an intent")
        if not isinstance(deadline,datetime) or deadline.tzinfo is None or not isinstance(artifact_sha256,str) or len(artifact_sha256)!=64 or any(character not in "0123456789abcdef" for character in artifact_sha256) or account_ref!="fixture-account":
            raise ValueError("bounded review binding required")
        target_id = UUID(str(target_id))
        owner_id = UUID(str(owner_id))
        fingerprint = _fingerprint([str(self.subject_id),str(target_id),target,kind,failure_class,str(owner_id),deadline.isoformat(),reason,artifact_sha256,account_ref])
        with self._command("cycles:write") as connection:
            if target == "cycle":
                goal,spec,intent,cycle = self._cycle(connection,target_id)
                context_id,operation_id = cycle["context_id"],cycle["operation_id"]
            else:
                goal,spec,intent = self._intent(connection,target_id)
                cycle = None
                context_id = operation_id = None
            column = "target_cycle_id" if cycle else "target_intent_id"
            existing = connection.execute(f"SELECT * FROM v4_recovery_cases WHERE {column}=%s AND kind=%s AND open_fingerprint=%s",(target_id,kind,fingerprint)).fetchone()
            if existing:
                review = connection.execute("SELECT id FROM v4_case_reviews WHERE case_id=%s",(existing["id"],)).fetchone()
                notification = connection.execute("SELECT id FROM v4_case_notifications WHERE case_id=%s AND kind='review_due'",(existing["id"],)).fetchone()
                return {"case_id":existing["id"],"review_id":review["id"] if review else None,"notification_id":notification["id"] if notification else None,"deadline":existing["deadline"]}
            active = connection.execute(f"SELECT 1 FROM v4_recovery_cases WHERE {column}=%s AND kind=%s AND state NOT IN ('resolved','terminal')",(target_id,kind)).fetchone()
            if active:
                raise ValueError("active case opening fingerprint conflict")
            if cycle:
                if cycle["state"] == "closed":
                    raise ValueError("closed cycle cannot open case")
            else:
                prior = connection.execute("SELECT disposition FROM v4_admissions WHERE intent_id=%s AND eligibility_revision=%s",(target_id,intent["eligibility_revision"])).fetchone()
                if not prior or prior["disposition"] not in {"deferred","review_required"}:
                    raise ValueError("intent must be waiting before review")
            now = connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
            if deadline <= now or deadline > now+timedelta(days=7):
                raise ValueError("future bounded case deadline required")
            if not connection.execute("SELECT 1 FROM identity_subjects WHERE id=%s AND workspace_id=%s AND enabled AND expires_at>clock_timestamp()",(owner_id,self.workspace_id)).fetchone():
                raise PermissionError("current case owner in workspace required")
            state = {"admission_review":"awaiting_review","manual_review":"awaiting_review","retry":"retry_due","reconciliation":"reconciling","rework":"rework_due"}[kind]
            if cycle:
                recovery_state = "retry_due" if kind == "retry" else "reconciling"
                if cycle["state"] != recovery_state:
                    if cycle["recovery_count"] >= spec.max_wakes:
                        raise ValueError("cycle recovery limit exceeded")
                    connection.execute("UPDATE v4_cycles SET state=%s,recovery_count=recovery_count+1 WHERE id=%s",(recovery_state,cycle["id"]))
                    enqueue_cycle_message(connection,workspace_id=self.workspace_id,subject_id=self.subject_id,goal_id=goal["id"],intent_id=intent["id"],cycle_id=cycle["id"],kind="recovery",payload={"state":recovery_state,"operation_id":str(cycle["operation_id"])},traceparent=self.trace.to_carrier()["traceparent"])
            case = connection.execute("INSERT INTO v4_recovery_cases (id,workspace_id,goal_id,target_intent_id,target_cycle_id,context_id,operation_id,kind,open_fingerprint,failure_class,state,owner_id,reason,deadline,max_actions) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",(uuid4(),self.workspace_id,goal["id"],intent["id"] if not cycle else None,cycle["id"] if cycle else None,context_id,operation_id,kind,fingerprint,failure_class,state,owner_id,reason,deadline,min(10,spec.max_wakes+1))).fetchone()
            review_id = notification_id = None
            if state == "awaiting_review":
                review_id,notification_id = uuid4(),uuid4()
                connection.execute("INSERT INTO v4_case_reviews (id,case_id,case_revision,context_id,operation_id,artifact_sha256,account_ref,purpose,expires_at) VALUES (%s,%s,1,%s,%s,%s,%s,'fixture_execution',%s)",(review_id,case["id"],context_id,operation_id,artifact_sha256,account_ref,deadline))
                connection.execute("INSERT INTO v4_case_notifications (id,case_id,case_revision,kind,due_at) VALUES (%s,%s,1,'review_due',%s)",(notification_id,case["id"],deadline))
            result = {"case_id":str(case["id"]),"target":target,"state":state,"review_id":str(review_id) if review_id else None,"notification_id":str(notification_id) if notification_id else None}
            connection.execute("INSERT INTO v4_case_events (id,case_id,case_revision,action,actor_id,traceparent,payload) VALUES (%s,%s,1,'opened',%s,%s,%s)",(uuid4(),case["id"],self.subject_id,self.trace.to_carrier()["traceparent"],Jsonb(result)))
            self._event(connection,goal["id"],"case_opened",result,intent["id"],cycle["id"] if cycle else None)
            return {"case_id":case["id"],"review_id":review_id,"notification_id":notification_id,"deadline":deadline}

    def respond_review(self, case_id, response: ReviewResponse, *, idempotency_key):
        _bounded_key(idempotency_key)
        response = ReviewResponse.model_validate(response)
        _bounded_reason(response.reason)
        fingerprint = _fingerprint([str(self.subject_id),response.model_dump(mode="json")])
        with self._command("cycles:review") as connection:
            case,goal,spec,intent,cycle = self._case(connection,case_id)
            receipt = self._case_receipt(connection,case,idempotency_key,fingerprint)
            if receipt:
                return receipt
            review = connection.execute("SELECT * FROM v4_case_reviews WHERE id=%s AND case_id=%s",(response.review_id,case_id)).fetchone()
            now = connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
            if not review or review["case_revision"] != response.expected_revision or case["revision"] != response.expected_revision:
                raise ValueError("stale case review revision or binding")
            if (review["context_id"],review["operation_id"],review["artifact_sha256"],review["account_ref"],review["purpose"]) != (response.context_id,response.operation_id,response.artifact_sha256,response.account_ref,response.purpose):
                raise PermissionError("review context, artifact, account or purpose binding mismatch")
            if case["owner_id"] != self.subject_id or case["state"] != "awaiting_review" or now >= min(review["expires_at"],case["deadline"]):
                raise PermissionError("case review owner, state or expiry invalid")
            if goal["state"] != "active" or goal["revision"] != intent["goal_revision"] or (cycle and cycle["state"] == "closed") or (not cycle and now >= intent["expires_at"]):
                raise PermissionError("current target does not allow review")
            if response.action == "approve_resume" and case["failure_class"] == "policy_denial":
                raise PermissionError("policy denial cannot become a retry approval")
            if resolve_baseline(connection,goal["id"],goal["revision"]) is None:
                raise PermissionError("current approved baseline required")
            if cycle:
                context = connection.execute("SELECT payload FROM v4_run_contexts WHERE id=%s",(case["context_id"],)).fetchone()
                if case["operation_id"] != cycle["operation_id"] or case["context_id"] != cycle["context_id"] or not context or not context_authorized(connection,context["payload"],self.workspace_id,UUID(context["payload"]["authority"]["subject_id"])):
                    raise PermissionError("current frozen review binding required")
            state = "retry_due" if response.action == "approve_resume" else "terminal"
            revision = response.expected_revision+1
            connection.execute("UPDATE v4_recovery_cases SET state=%s,revision=%s,action_count=action_count+1 WHERE id=%s",(state,revision,case_id))
            result = {"case_id":str(case_id),"review_id":str(response.review_id),"state":state,"revision":revision,"action":response.action}
            command_id = self._case_record(connection,case,goal,intent,cycle,key=idempotency_key,fingerprint=fingerprint,action="review",result=result,revision=revision)
            connection.execute("INSERT INTO v4_review_decisions (id,review_id,command_id,actor_id,action,reason) VALUES (%s,%s,%s,%s,%s,%s)",(uuid4(),response.review_id,command_id,self.subject_id,response.action,response.reason))
            return result

    def resume_case(self, case_id, *, expected_revision, idempotency_key):
        _bounded_key(idempotency_key)
        if type(expected_revision) is not int or expected_revision<1:
            raise ValueError("explicit case revision required")
        fingerprint = _fingerprint([str(self.subject_id),expected_revision,"resume"])
        with self._command("cycles:write") as connection:
            case,goal,spec,intent,cycle = self._case(connection,case_id)
            receipt = self._case_receipt(connection,case,idempotency_key,fingerprint)
            if receipt:
                return receipt
            now = connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
            if case["revision"] != expected_revision or case["state"] != "retry_due":
                raise ValueError("stale or unapproved case resume")
            if case["action_count"] >= case["max_actions"] or now >= min(case["deadline"],spec.horizon_end) or goal["state"] != "active" or goal["revision"] != intent["goal_revision"]:
                raise PermissionError("current bounded case authority required")
            self._require_running(connection,goal["id"])
            if cycle:
                if cycle["state"] == "closed" or case["context_id"] != cycle["context_id"] or case["operation_id"] != cycle["operation_id"]:
                    raise ValueError("original case operation or context unavailable")
                context = connection.execute("SELECT payload FROM v4_run_contexts WHERE id=%s",(case["context_id"],)).fetchone()
                if not context_authorized(connection,context["payload"],self.workspace_id,self.subject_id) or resolve_baseline(connection,goal["id"],goal["revision"],approval_id=context["payload"].get("baseline_approval_id")) is None:
                    raise PermissionError("current frozen stage authority required")
                if cycle["recovery_count"] > spec.max_wakes or cycle["state"] not in {"retry_due","reconciling"}:
                    raise ValueError("cycle recovery state or limit exceeded")
                connection.execute("UPDATE v4_cycles SET state='runnable' WHERE id=%s",(cycle["id"],))
                enqueue_cycle_message(connection,workspace_id=self.workspace_id,subject_id=self.subject_id,goal_id=goal["id"],intent_id=intent["id"],cycle_id=cycle["id"],kind="recovery",payload={"state":"runnable","operation_id":str(cycle["operation_id"]),"case_id":str(case_id)},traceparent=self.trace.to_carrier()["traceparent"])
            else:
                if now < intent["due_at"] or now >= intent["expires_at"]:
                    raise ValueError("intent not due or expired")
                admission = connection.execute("SELECT disposition FROM v4_admissions WHERE intent_id=%s AND eligibility_revision=%s",(intent["id"],intent["eligibility_revision"])).fetchone()
                if not admission or admission["disposition"] not in {"deferred","review_required"} or intent["eligibility_revision"] > spec.max_wakes or connection.execute("SELECT 1 FROM v4_cycles WHERE intent_id=%s",(intent["id"],)).fetchone():
                    raise ValueError("only an unadmitted waiting intent can resume")
                connection.execute("UPDATE v4_cycle_intents SET eligibility_revision=eligibility_revision+1,reviewed=true WHERE id=%s",(intent["id"],))
            revision = expected_revision+1
            connection.execute("UPDATE v4_recovery_cases SET state='resolved',revision=%s,action_count=action_count+1 WHERE id=%s",(revision,case_id))
            result = {"case_id":str(case_id),"intent_id":str(intent["id"]),"cycle_id":str(cycle["id"]) if cycle else None,"operation_id":str(cycle["operation_id"]) if cycle else None,"state":"resolved","revision":revision}
            self._case_record(connection,case,goal,intent,cycle,key=idempotency_key,fingerprint=fingerprint,action="resume",result=result,revision=revision)
            return result

    def ack_notification(self, notification_id):
        with self._command("cycles:review") as connection:
            identity = connection.execute("SELECT case_id FROM v4_case_notifications WHERE id=%s",(notification_id,)).fetchone()
            if not identity:
                raise PermissionError("notification outside case scope")
            case,goal,_,intent,cycle = self._case(connection,identity["case_id"])
            if case["owner_id"] != self.subject_id:
                raise PermissionError("case owner required")
            existing = connection.execute("SELECT id FROM v4_case_acks WHERE notification_id=%s",(notification_id,)).fetchone()
            if existing:
                return {"ack_id":str(existing["id"]),"notification_id":str(notification_id)}
            ack_id = uuid4()
            connection.execute("INSERT INTO v4_case_acks (id,notification_id,actor_id) VALUES (%s,%s,%s)",(ack_id,notification_id,self.subject_id))
            self._event(connection,goal["id"],"case_notification_ack",{"notification_id":str(notification_id)},intent["id"],cycle["id"] if cycle else None)
            return {"ack_id":str(ack_id),"notification_id":str(notification_id)}

    def escalate_due(self, case_id):
        with self._command("cycles:case_operator") as connection:
            case,goal,_,intent,cycle = self._case(connection,case_id)
            now = connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
            if case["state"] == "suspended":
                prior = connection.execute("SELECT id FROM v4_case_notifications WHERE case_id=%s AND kind='escalation'",(case_id,)).fetchone()
                return {"case_id":str(case_id),"state":"suspended","notification_id":str(prior["id"])}
            if case["state"] != "awaiting_review" or now < case["deadline"]:
                raise ValueError("review deadline not due")
            revision = case["revision"]+1
            notification_id = uuid4()
            connection.execute("UPDATE v4_recovery_cases SET state='suspended',revision=%s WHERE id=%s",(revision,case_id))
            connection.execute("INSERT INTO v4_case_notifications (id,case_id,case_revision,kind,due_at) VALUES (%s,%s,%s,'escalation',%s)",(notification_id,case_id,revision,now))
            result = {"case_id":str(case_id),"state":"suspended","notification_id":str(notification_id)}
            connection.execute("INSERT INTO v4_case_events (id,case_id,case_revision,action,actor_id,traceparent,payload) VALUES (%s,%s,%s,'escalated',%s,%s,%s)",(uuid4(),case_id,revision,self.subject_id,self.trace.to_carrier()["traceparent"],Jsonb(result)))
            self._event(connection,goal["id"],"case_escalated",result,intent["id"],cycle["id"] if cycle else None)
            return result

    def _terminal_disposition(self, connection, case, intent, cycle):
        if cycle:
            return cycle["disposition"] if cycle["state"] == "closed" else None
        admission = connection.execute("SELECT disposition FROM v4_admissions WHERE intent_id=%s ORDER BY eligibility_revision DESC LIMIT 1",(intent["id"],)).fetchone()
        now = connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
        if admission and admission["disposition"] == "denied":
            return "denied"
        return "expired" if now >= intent["expires_at"] else None

    def terminalize_case(self, case_id, *, expected_revision, idempotency_key):
        _bounded_key(idempotency_key)
        if type(expected_revision) is not int or expected_revision<1:
            raise ValueError("explicit case revision required")
        fingerprint = _fingerprint([str(self.subject_id),expected_revision,"terminalize"])
        with self._command("cycles:write") as connection:
            case,goal,_,intent,cycle = self._case(connection,case_id)
            receipt = self._case_receipt(connection,case,idempotency_key,fingerprint)
            if receipt:
                return receipt
            disposition = self._terminal_disposition(connection,case,intent,cycle)
            if not disposition:
                raise ValueError("committed terminal disposition required")
            if case["revision"] != expected_revision or case["state"] == "terminal":
                raise ValueError("stale case revision")
            revision = expected_revision+1
            connection.execute("UPDATE v4_recovery_cases SET state='terminal',revision=%s WHERE id=%s",(revision,case_id))
            result = {"case_id":str(case_id),"state":"terminal","revision":revision,"disposition":disposition}
            self._case_record(connection,case,goal,intent,cycle,key=idempotency_key,fingerprint=fingerprint,action="terminalize",result=result,revision=revision)
            return result

    def archive_case(self, case_id, *, reason, evidence, retain_until):
        _bounded_reason(reason)
        if not isinstance(evidence,dict) or not evidence or not isinstance(retain_until,datetime) or retain_until.tzinfo is None:
            raise ValueError("bounded archive evidence and aware retention required")
        fingerprint = _fingerprint([reason,evidence,retain_until.isoformat()])
        with self._command("cycles:write") as connection:
            case,goal,_,intent,cycle = self._case(connection,case_id)
            existing = connection.execute("SELECT * FROM v4_case_archives WHERE case_id=%s",(case_id,)).fetchone()
            if existing:
                if _fingerprint([existing["reason"],existing["evidence"],existing["retain_until"].isoformat()]) != fingerprint:
                    raise ValueError("archive fingerprint conflict")
                return {"archive_id":str(existing["id"]),"case_id":str(case_id)}
            disposition = self._terminal_disposition(connection,case,intent,cycle)
            now = connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
            if case["state"] != "terminal" or not disposition:
                raise ValueError("committed terminal disposition required before archive")
            if retain_until <= now:
                raise ValueError("future retention required")
            archive_id = uuid4()
            connection.execute("INSERT INTO v4_case_archives (id,case_id,actor_id,reason,evidence,retain_until) VALUES (%s,%s,%s,%s,%s,%s)",(archive_id,case_id,self.subject_id,reason,Jsonb(evidence),retain_until))
            result = {"archive_id":str(archive_id),"case_id":str(case_id),"disposition":disposition}
            connection.execute("INSERT INTO v4_case_events (id,case_id,case_revision,action,actor_id,traceparent,payload) VALUES (%s,%s,%s,'archived',%s,%s,%s)",(uuid4(),case_id,case["revision"],self.subject_id,self.trace.to_carrier()["traceparent"],Jsonb(result)))
            self._event(connection,goal["id"],"case_archived",result,intent["id"],cycle["id"] if cycle else None)
            return result
