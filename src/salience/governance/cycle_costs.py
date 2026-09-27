"""Caller-transaction G0/G1 accounting over canonical reservations and ledger."""

from hashlib import sha256
import json
from uuid import UUID, uuid4

from psycopg.rows import dict_row, tuple_row
from psycopg.types.json import Jsonb

from salience.cycles.authority import context_authorized, current_authority
from salience.cycles.baselines import resolve_baseline
from salience.cycles.contracts import GoalSpecV3, parse_goal
from salience.governance.cost_repository import CostReservationRepository, _to_amount, _to_micros
from salience.governance.costs import BudgetExceeded, IdempotencyConflict


def _fingerprint(value):
    return sha256(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def _micros(value):
    if type(value) is not int or not 0 <= value <= 10**15:
        raise ValueError("bounded integer micros required")
    return value


class CycleCostLedger:
    def __init__(self, connection, *, workspace_id, subject_id, traceparent):
        if connection.autocommit:
            raise ValueError("caller-owned transaction required")
        self.connection = connection
        self.workspace_id = UUID(str(workspace_id))
        self.subject_id = UUID(str(subject_id))
        self.traceparent = traceparent
        self._query("SET LOCAL statement_timeout='3s'")

    def _query(self, statement, parameters=()):
        cursor = self.connection.cursor(row_factory=dict_row)
        cursor.execute(statement, parameters)
        return cursor

    def _cycle(self, cycle_id, scope):
        self._authority(scope)
        identity = self._query("SELECT intent.goal_id FROM v4_cycles AS cycle JOIN v4_cycle_intents AS intent ON intent.id=cycle.intent_id WHERE cycle.id=%s", (cycle_id,)).fetchone()
        if not identity or not self._query("SELECT id FROM v4_goals WHERE id=%s AND workspace_id=%s FOR UPDATE", (identity["goal_id"], self.workspace_id)).fetchone():
            raise PermissionError("cycle outside accounting scope")
        return self._query("""
            SELECT cycle.*, goal.id AS goal_id, goal.state AS goal_state, goal.revision,
                   intent.id AS bound_intent, intent.goal_revision, revision.payload AS spec
            FROM v4_cycles AS cycle JOIN v4_cycle_intents AS intent ON intent.id=cycle.intent_id
            JOIN v4_goals AS goal ON goal.id=intent.goal_id
            JOIN v4_goal_revisions AS revision ON revision.goal_id=goal.id AND revision.revision=goal.revision
            WHERE cycle.id=%s
        """, (cycle_id,)).fetchone()

    def _budgets(self, identities):
        budgets = self._query("SELECT * FROM public.v4_lock_budgets(%s,%s)", (list(sorted(identities,key=str)),self.workspace_id)).fetchall()
        if len(budgets) != len(identities):
            raise PermissionError("budget outside accounting scope")
        return budgets

    def _authority(self, scope):
        with self.connection.cursor(row_factory=dict_row) as cursor:
            return current_authority(cursor,self.workspace_id,self.subject_id,scope)

    def _entry(self, reservation, kind, amount, *, actual=None, details=None):
        self._query("""
            INSERT INTO cost_ledger_entries (budget_reservation_id,estimated_amount,actual_amount,currency,usage,recorded_at)
            VALUES (%s,%s,%s,%s,%s,clock_timestamp())
        """, (reservation["reservation_id"], _to_amount(amount), _to_amount(actual) if actual is not None else None,
              reservation["currency"], Jsonb({"kind":kind, "accounting_mode":"fixture-only"} | (details or {}))))

    def _event(self, cycle, action, payload):
        self._query("""
            INSERT INTO v4_cycle_events (id,workspace_id,subject_id,goal_id,intent_id,cycle_id,kind,traceparent,payload)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (uuid4(),self.workspace_id,self.subject_id,cycle["goal_id"],cycle["bound_intent"],cycle["id"],
              "cost."+action,self.traceparent,Jsonb(payload)))

    def _reservation(self, allocation, budget_id, amount, estimate, operation_id=None):
        identity = uuid4()
        self._query("""
            INSERT INTO budget_reservations (id,budget_id,reservation_key,estimated_amount,reserved_amount,status,expires_at)
            VALUES (%s,%s,%s,%s,%s,'reserved',%s)
        """, (identity,budget_id,f"v4:{allocation['id']}:{operation_id or 'parent'}",_to_amount(estimate),_to_amount(amount),allocation["period_end"]))
        self._query("INSERT INTO v4_allocation_reservations (reservation_id,allocation_id,budget_id,operation_id) VALUES (%s,%s,%s,%s)", (identity,allocation["id"],budget_id,operation_id))
        reservation = {"reservation_id":identity,"currency":allocation["currency"]}
        self._entry(reservation,"estimated",estimate,details={"allocation_id":str(allocation["id"]),"operation_id":str(operation_id) if operation_id else None})
        return str(identity)

    def allocate(self, cycle_id):
        cycle = self._cycle(cycle_id, "cycles:write")
        spec = parse_goal(cycle["spec"])
        if not isinstance(spec, GoalSpecV3):
            raise ValueError("V3 allocation policy required")
        policy = spec.allocation
        budgets = self._budgets(policy.budget_ids)
        digest = _fingerprint([str(cycle_id),spec.model_dump(mode="json")])
        existing = self._query("SELECT * FROM v4_cycle_allocations WHERE cycle_id=%s", (cycle_id,)).fetchone()
        if existing:
            if existing["fingerprint"] != digest:
                raise IdempotencyConflict("allocation identity conflicts with approved inputs")
            return self._allocation_result(existing)
        now = self._query("SELECT clock_timestamp() AS now").fetchone()["now"]
        if cycle["goal_state"] != "active" or cycle["goal_revision"] != cycle["revision"] or cycle["state"] == "closed":
            raise ValueError("current runnable goal required")
        if not policy.period_start <= now < policy.period_end:
            raise ValueError("current allocation period required")
        for budget in budgets:
            allowed_scope = budget["scope"] == "workspace" and budget["content_program_id"] is None
            allowed_scope = allowed_scope or (budget["scope"] in {"program","account:"+policy.account_ref} and budget["content_program_id"] == spec.content_program_id)
            if not allowed_scope or budget["currency"] != policy.currency or (budget["period_start"],budget["period_end"]) != (policy.period_start,policy.period_end):
                raise PermissionError("budget dimension, currency or period mismatch")
            with self.connection.cursor(row_factory=tuple_row) as cursor:
                committed = CostReservationRepository._committed_micros(cursor,str(budget["id"]))
            if budget["status"] != "active" or _to_micros(budget["limit_amount"])-committed < policy.ceiling_micros:
                raise BudgetExceeded("G0 budget capacity exhausted")
        allocation = self._query("""
            INSERT INTO v4_cycle_allocations (id,cycle_id,workspace_id,content_program_id,account_ref,currency,period_start,period_end,ceiling_micros,budget_ids,fingerprint)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *
        """, (uuid4(),cycle_id,self.workspace_id,spec.content_program_id,policy.account_ref,policy.currency,policy.period_start,policy.period_end,policy.ceiling_micros,list(sorted(policy.budget_ids,key=str)),digest)).fetchone()
        for budget in budgets:
            self._reservation(allocation,budget["id"],policy.ceiling_micros,policy.ceiling_micros)
        result = self._allocation_result(allocation)
        self._event(cycle,"allocated",result)
        return result

    def _allocation_result(self, allocation):
        parents = self._reservations(allocation, None)
        return {"allocation_id":str(allocation["id"]),"reservation_ids":[str(parent["reservation_id"]) for parent in parents]}

    def _reservations(self, allocation, operation_id):
        return self._query("""
            SELECT mapping.reservation_id,reservation.reserved_amount,reservation.status,%s::text AS currency
            FROM v4_allocation_reservations AS mapping JOIN budget_reservations AS reservation ON reservation.id=mapping.reservation_id
            WHERE mapping.allocation_id=%s AND mapping.operation_id IS NOT DISTINCT FROM %s::uuid
            ORDER BY mapping.budget_id FOR UPDATE OF reservation
        """, (allocation["currency"],allocation["id"],operation_id)).fetchall()

    def _load(self, cycle_id, scope="cycles:accounting"):
        cycle = self._cycle(cycle_id,scope)
        allocation = self._query("SELECT * FROM v4_cycle_allocations WHERE cycle_id=%s AND workspace_id=%s", (cycle_id,self.workspace_id)).fetchone()
        if not allocation:
            raise ValueError("cycle has no allocation")
        budgets = self._budgets(allocation["budget_ids"])
        return cycle,allocation,budgets

    def _receipt(self, allocation, key, digest):
        if not isinstance(key,str) or not key.strip() or len(key)>256:
            raise ValueError("bounded accounting command key required")
        receipt = self._query("SELECT fingerprint,payload FROM v4_cost_commands WHERE allocation_id=%s AND idempotency_key=%s", (allocation["id"],key)).fetchone()
        if receipt and receipt["fingerprint"] != digest:
            raise IdempotencyConflict("accounting command fingerprint conflict")
        return receipt["payload"] if receipt else None

    def _record(self, cycle, allocation, operation_id, action, key, digest, result, scope="cycles:accounting"):
        self._query("INSERT INTO v4_cost_commands (id,allocation_id,operation_id,idempotency_key,action,fingerprint,payload) VALUES (%s,%s,%s,%s,%s,%s,%s)", (uuid4(),allocation["id"],operation_id,key,action,digest,Jsonb(result)))
        self._event(cycle,action,result | {"idempotency_key":key})
        self._authority(scope)
        return result

    def _require_current(self, cycle, allocation):
        now = self._query("SELECT clock_timestamp() AS now").fetchone()["now"]
        if cycle["state"] == "closed" or cycle["goal_state"] != "active" or cycle["goal_revision"] != cycle["revision"] or not allocation["period_start"] <= now < allocation["period_end"]:
            raise ValueError("current open cycle and allocation period required")
        context = self._query("SELECT payload FROM v4_run_contexts WHERE id=%s", (cycle["context_id"],)).fetchone()
        with self.connection.cursor(row_factory=dict_row) as cursor:
            if not context or not context_authorized(cursor,context["payload"],self.workspace_id,self.subject_id) or resolve_baseline(cursor,cycle["goal_id"],cycle["revision"],approval_id=context["payload"]["baseline_approval_id"]) is None:
                raise PermissionError("current frozen authority and baseline required")

    def transfer(self, cycle_id, *, operation_id, idempotency_key, estimated_micros, reserved_micros, category):
        if idempotency_key == "cycle-close":
            raise ValueError("reserved closure command key")
        operation_id = UUID(str(operation_id))
        if _micros(estimated_micros)>_micros(reserved_micros) or category not in {"research","generation","publication","evaluation","training","publishing","observation","learning"}:
            raise ValueError("estimate must fit bound and typed category required")
        cycle,allocation,budgets = self._load(cycle_id)
        inputs = [str(self.subject_id),"transfer",str(operation_id),estimated_micros,reserved_micros,category]
        digest = _fingerprint(inputs)
        receipt = self._receipt(allocation,idempotency_key,digest)
        if receipt:
            return receipt
        operation = self._query("SELECT * FROM v4_cost_operations WHERE id=%s", (operation_id,)).fetchone()
        if operation:
            if operation["allocation_id"] != allocation["id"] or operation["fingerprint"] != digest:
                raise IdempotencyConflict("operation identity conflicts with transfer inputs")
            raise IdempotencyConflict("operation already transferred under another command key")
        self._require_current(cycle, allocation)
        if any(budget["status"] != "active" for budget in budgets):
            raise BudgetExceeded("budget is inactive")
        parents = self._reservations(allocation,None)
        if any(parent["status"] != "reserved" or _to_micros(parent["reserved_amount"])<reserved_micros for parent in parents):
            raise BudgetExceeded("transfer exceeds unused allocation")
        self._query("INSERT INTO v4_cost_operations (id,allocation_id,estimated_micros,reserved_micros,category,provider,fingerprint) VALUES (%s,%s,%s,%s,%s,'fixture.accounting',%s)", (operation_id,allocation["id"],estimated_micros,reserved_micros,category,digest))
        for parent in parents:
            self._query("UPDATE budget_reservations SET reserved_amount=reserved_amount-%s WHERE id=%s", (_to_amount(reserved_micros),parent["reservation_id"]))
            self._entry(parent,"transferred",reserved_micros,details={"operation_id":str(operation_id)})
        identities = [self._reservation(allocation,budget["id"],reserved_micros,estimated_micros,operation_id) for budget in budgets]
        result = {"allocation_id":str(allocation["id"]),"operation_id":str(operation_id),"reservation_ids":identities,"state":"reserved"}
        return self._record(cycle,allocation,operation_id,"transfer",idempotency_key,digest,result)

    def command(self, cycle_id, *, operation_id, action, idempotency_key, proof_ref, actual_micros=None):
        return self._command(cycle_id,operation_id=operation_id,action=action,idempotency_key=idempotency_key,proof_ref=proof_ref,actual_micros=actual_micros,scope="cycles:accounting")

    def release_closed(self, cycle_id):
        return self._command(cycle_id,operation_id=None,action="release_allocation",idempotency_key="cycle-close",proof_ref="fixture-receipt:canonical-cycle-close",scope="cycles:write")

    def _command(self, cycle_id, *, operation_id, action, idempotency_key, proof_ref, actual_micros=None, scope):
        if scope == "cycles:accounting" and idempotency_key == "cycle-close":
            raise ValueError("reserved closure command key")
        if action not in {"dispatch","unknown","settle","release","release_allocation"} or not isinstance(proof_ref,str) or not proof_ref.startswith("fixture-receipt:") or len(proof_ref)>256:
            raise ValueError("typed fixture accounting action and proof reference required")
        if (action == "settle") != (actual_micros is not None):
            raise ValueError("actual micros are required only for settlement")
        if actual_micros is not None:
            _micros(actual_micros)
        if (action == "release_allocation") != (operation_id is None):
            raise ValueError("operation identity required except parent release")
        operation_id = UUID(str(operation_id)) if operation_id is not None else None
        cycle,allocation,budgets = self._load(cycle_id,scope)
        if scope == "cycles:permit":
            if action not in {"dispatch","unknown"} or (action == "dispatch" and cycle["state"] != "runnable"):
                raise PermissionError("permit liability transition requires a runnable cycle for dispatch")
        elif scope != "cycles:accounting" and (scope != "cycles:write" or cycle["state"] != "closed" or action != "release_allocation"):
            raise PermissionError("closure release requires a canonically closed cycle")
        digest = _fingerprint([str(self.subject_id),action,str(operation_id),proof_ref,actual_micros])
        receipt = self._receipt(allocation,idempotency_key,digest)
        if receipt:
            return receipt
        operation = self._query("SELECT * FROM v4_cost_operations WHERE id=%s AND allocation_id=%s", (operation_id,allocation["id"])).fetchone() if operation_id else None
        if operation_id and not operation:
            raise PermissionError("operation outside allocation scope")
        reservations = self._reservations(allocation,operation_id)
        state = reservations[0]["status"]
        terminal = state in {"settled","overage_pending_approval","released"}
        if action == "settle":
            prior = self._query("SELECT payload FROM v4_cost_commands WHERE allocation_id=%s AND operation_id=%s AND action='settle' LIMIT 1", (allocation["id"],operation_id)).fetchone()
            if prior:
                if prior["payload"]["actual_micros"] != actual_micros:
                    raise IdempotencyConflict("operation already settled with different actual")
                return self._record(cycle,allocation,operation_id,action,idempotency_key,digest,prior["payload"])
            if state == "released":
                raise ValueError("released operation cannot settle")
            state = "overage_pending_approval" if actual_micros>operation["reserved_micros"] else "settled"
        elif action in {"dispatch","unknown"}:
            if terminal or operation_id is None:
                raise ValueError("released or settled operation cannot dispatch")
            if action == "dispatch":
                self._require_current(cycle, allocation)
                if any(budget["status"] != "active" for budget in budgets):
                    raise BudgetExceeded("budget is inactive")
            state = "pending_actual"
        elif action == "release":
            if state != "reserved":
                raise ValueError("dispatched or unknown liability cannot be released")
            state = "released"
        else:
            state = "released"
        for reservation in reservations:
            amount = _to_micros(reservation["reserved_amount"])
            cleared = state in {"settled","overage_pending_approval","released"}
            self._query("UPDATE budget_reservations SET status=%s,reserved_amount=%s WHERE id=%s", (state,0 if cleared else reservation["reserved_amount"],reservation["reservation_id"]))
            self._entry(reservation,"settlement" if action=="settle" else action,amount,actual=actual_micros,details={"proof_ref":proof_ref,"operation_id":str(operation_id) if operation_id else None,"status":state})
        result = {"allocation_id":str(allocation["id"]),"operation_id":str(operation_id) if operation_id else None,"state":state,"actual_micros":actual_micros,"proof_ref":proof_ref}
        return self._record(cycle,allocation,operation_id,action,idempotency_key,digest,result,scope)
