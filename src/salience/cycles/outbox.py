"""Local-only, ordered cycle handoff; delivery is never proof of an effect."""

import asyncio
from uuid import UUID, uuid4

from opentelemetry import trace
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from salience.observability.tracing import OpenTelemetryTraceEmitter, TraceContext


def enqueue_cycle_message(connection, *, workspace_id, subject_id, goal_id, intent_id, cycle_id, kind, payload, traceparent):
    sequence = connection.execute("SELECT COALESCE(max(sequence),0)+1 AS next FROM v4_cycle_outbox WHERE cycle_id=%s", (cycle_id,)).fetchone()["next"]
    connection.execute("INSERT INTO v4_cycle_outbox (id,workspace_id,subject_id,goal_id,intent_id,cycle_id,sequence,kind,payload,traceparent) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", (uuid4(),workspace_id,subject_id,goal_id,intent_id,cycle_id,sequence,kind,Jsonb(payload),traceparent))


class CycleOutbox:
    def __init__(self, database_url, *, workspace_id, max_attempts=5, lease_seconds=10, tracer=None):
        if type(max_attempts) is not int or not 1 <= max_attempts <= 10 or type(lease_seconds) is not int or not 1 <= lease_seconds <= 60:
            raise ValueError("bounded delivery settings required")
        self.database_url = database_url
        self.workspace_id = UUID(str(workspace_id))
        self.max_attempts = max_attempts
        self.lease_seconds = lease_seconds
        self.emitter = OpenTelemetryTraceEmitter(tracer or trace.get_tracer("salience.cycles"))

    def _connect(self):
        return psycopg.connect(self.database_url, row_factory=dict_row, connect_timeout=3, options="-c statement_timeout=3000")

    def claim(self):
        with self._connect() as connection:
            connection.execute("SET LOCAL statement_timeout='3s'")
            connection.execute("UPDATE v4_cycle_outbox AS message SET state='delivered',delivered_at=clock_timestamp(),lease_until=NULL WHERE message.workspace_id=%s AND message.state!='delivered' AND (message.state='dead_letter' OR message.attempts >= %s) AND EXISTS (SELECT 1 FROM v4_cycle_inbox WHERE message_id=message.id)", (self.workspace_id,self.max_attempts))
            connection.execute("UPDATE v4_cycle_outbox SET state='dead_letter',last_error='lease_exhausted' WHERE workspace_id=%s AND state='leased' AND lease_until <= clock_timestamp() AND attempts >= %s", (self.workspace_id,self.max_attempts))
            message = connection.execute("""
                SELECT message.* FROM v4_cycle_outbox AS message
                WHERE message.workspace_id=%s AND message.attempts < %s
                  AND ((message.state='pending' AND message.next_attempt_at<=clock_timestamp())
                    OR (message.state='leased' AND message.lease_until<=clock_timestamp()))
                  AND NOT EXISTS (SELECT 1 FROM v4_cycle_outbox AS prior
                    WHERE prior.cycle_id=message.cycle_id AND prior.sequence < message.sequence AND prior.state!='delivered')
                ORDER BY message.created_at,message.sequence
                FOR UPDATE OF message SKIP LOCKED LIMIT 1
            """, (self.workspace_id,self.max_attempts)).fetchone()
            if not message:
                return None
            return connection.execute("UPDATE v4_cycle_outbox SET state='leased',attempts=attempts+1,lease_token=%s,lease_until=clock_timestamp()+(%s * interval '1 second') WHERE id=%s RETURNING *", (uuid4(),self.lease_seconds,message["id"])).fetchone()

    def ack(self, message_id, lease_token):
        with self._connect() as connection:
            return connection.execute("UPDATE v4_cycle_outbox SET state='delivered',delivered_at=clock_timestamp(),lease_until=NULL WHERE id=%s AND workspace_id=%s AND state='leased' AND lease_token=%s AND lease_until>clock_timestamp()", (message_id,self.workspace_id,lease_token)).rowcount == 1

    def fail(self, message_id, lease_token, reason):
        if reason not in {"temporary","timeout","invalid_message"}:
            raise ValueError("safe classified failure code required")
        if self.receipt_exists(message_id):
            return "delivered" if self.ack(message_id,lease_token) else "stale_claim"
        with self._connect() as connection:
            row = connection.execute("UPDATE v4_cycle_outbox SET state=CASE WHEN attempts >= %s THEN 'dead_letter' ELSE 'pending' END, next_attempt_at=clock_timestamp()+(LEAST(60, power(2,attempts)) * interval '1 second'),lease_until=NULL,last_error=%s WHERE id=%s AND workspace_id=%s AND state='leased' AND lease_token=%s AND lease_until>clock_timestamp() RETURNING state", (self.max_attempts,reason,message_id,self.workspace_id,lease_token)).fetchone()
            return row["state"] if row else "stale_claim"

    async def dispatch_one(self, transport):
        message = await asyncio.to_thread(self.claim)
        if not message:
            return False
        if await asyncio.to_thread(self.receipt_exists,message["id"]):
            return await asyncio.to_thread(self.ack,message["id"],message["lease_token"])
        try:
            async with asyncio.timeout(min(5,self.lease_seconds)):
                context = TraceContext.from_carrier({"traceparent":message["traceparent"]})
                with self.emitter.active_span(context,"cycle.outbox.delivery") as emitted:
                    await transport.deliver(message | {"delivery_traceparent":emitted.to_carrier()["traceparent"]})
        except TimeoutError:
            await asyncio.to_thread(self.fail,message["id"],message["lease_token"],"timeout")
            return False
        except Exception:
            await asyncio.to_thread(self.fail,message["id"],message["lease_token"],"temporary")
            return False
        return await asyncio.to_thread(self.ack,message["id"],message["lease_token"])

    def receipt_exists(self, message_id):
        with self._connect() as connection:
            return bool(connection.execute("SELECT 1 FROM v4_cycle_inbox AS receipt JOIN v4_cycle_outbox AS message ON message.id=receipt.message_id WHERE message.id=%s AND message.workspace_id=%s", (message_id,self.workspace_id)).fetchone())

    def consume(self, message_id, *, traceparent=None, expected_cycle_id=None, expected_kind=None):
        with self._connect() as connection:
            connection.execute("SET LOCAL statement_timeout='3s'")
            message = connection.execute("SELECT * FROM v4_cycle_outbox WHERE id=%s AND workspace_id=%s", (message_id,self.workspace_id)).fetchone()
            if not message:
                raise PermissionError("message outside consumer scope")
            if (expected_cycle_id is not None and str(expected_cycle_id)!=str(message["cycle_id"])) or (expected_kind is not None and expected_kind!=message["kind"]):
                raise ValueError("message envelope binding mismatch")
            if traceparent and TraceContext.from_carrier({"traceparent":traceparent}).trace_id != TraceContext.from_carrier({"traceparent":message["traceparent"]}).trace_id:
                raise ValueError("trace binding mismatch")
            connection.execute("SELECT id FROM v4_goals WHERE id=%s FOR UPDATE", (message["goal_id"],))
            existing = connection.execute("SELECT * FROM v4_cycle_inbox WHERE message_id=%s", (message_id,)).fetchone()
            if existing:
                return existing
            missing = connection.execute("SELECT 1 FROM v4_cycle_outbox AS prior WHERE prior.cycle_id=%s AND prior.sequence<%s AND NOT EXISTS (SELECT 1 FROM v4_cycle_inbox WHERE message_id=prior.id)", (message["cycle_id"],message["sequence"])).fetchone()
            if missing:
                raise ValueError("ordered consumption required")
            current = connection.execute("SELECT goal.state AS goal_state, cycle.state AS cycle_state, context.payload FROM v4_goals AS goal JOIN v4_cycle_intents AS intent ON intent.goal_id=goal.id JOIN v4_cycles AS cycle ON cycle.intent_id=intent.id JOIN v4_run_contexts AS context ON context.id=cycle.context_id WHERE cycle.id=%s", (message["cycle_id"],)).fetchone()
            identity = connection.execute("SELECT issuer,subject FROM identity_subjects WHERE id=%s AND workspace_id=%s", (message["subject_id"],self.workspace_id)).fetchone()
            authorized = bool(identity and connection.execute("SELECT * FROM public.p0_lock_identity(%s,%s,%s)", (identity["issuer"],identity["subject"],self.workspace_id)).fetchone())
            grants = connection.execute("SELECT effect FROM permission_grants WHERE workspace_id=%s AND principal_type='identity' AND principal_id=%s AND scope='cycles:write' AND constraints='{}'::jsonb AND expires_at>clock_timestamp()", (self.workspace_id,str(message["subject_id"]))).fetchall()
            effects = {row["effect"] for row in grants}
            authorized = authorized and "allow" in effects and "deny" not in effects
            payload = current["payload"]
            if payload.get("production_effects_enabled") is not False or payload.get("dry_run") is not True or payload.get("provider") != "fixture.dummy@1.0.0":
                raise ValueError("only the frozen no-effects fixture is supported")
            state = "recorded" if authorized and current["goal_state"]=="active" else "held"
            if current["cycle_state"]=="closed" and message["kind"]!="close":
                state="held"
            context = TraceContext.from_carrier({"traceparent":traceparent or message["traceparent"]})
            with self.emitter.active_span(context,"cycle.fixture.consume") as emitted:
                carrier = emitted.to_carrier()["traceparent"]
                receipt = connection.execute("INSERT INTO v4_cycle_inbox (id,message_id,cycle_id,state,traceparent) VALUES (%s,%s,%s,%s,%s) RETURNING *", (uuid4(),message_id,message["cycle_id"],state,carrier)).fetchone()
                connection.execute("INSERT INTO v4_cycle_events (id,workspace_id,subject_id,goal_id,intent_id,cycle_id,kind,traceparent,payload) VALUES (%s,%s,%s,%s,%s,%s,'fixture_consumed',%s,%s)", (uuid4(),self.workspace_id,message["subject_id"],message["goal_id"],message["intent_id"],message["cycle_id"],carrier,Jsonb({"message_id":str(message_id),"sequence":message["sequence"],"state":state,"production_effects_enabled":False})))
                return receipt
