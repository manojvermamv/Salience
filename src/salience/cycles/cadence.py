"""Bounded local schedule commands over canonical intents, not a scheduler daemon."""

from datetime import timedelta, timezone
from hashlib import sha256
import json

from psycopg.types.json import Jsonb

from salience.cycles.authority import current_authority, require_program
from salience.cycles.contracts import CycleRequest, GoalSpecV2


def fingerprint(payload):
    return sha256(json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


class CadenceCommands:
    def _policy_goal(self, connection, goal_id):
        goal, spec = self._goal(connection, goal_id)
        if not isinstance(spec, GoalSpecV2):
            raise ValueError("V2 goal policy required")
        current_authority(connection, self.workspace_id, self.subject_id)
        require_program(connection, self.workspace_id, spec.content_program_id)
        return goal, spec

    def _pending(self, connection, goal_id):
        return connection.execute("""
            SELECT count(*) AS count FROM v4_cycle_intents AS intent
            WHERE intent.goal_id=%s AND intent.expires_at>clock_timestamp()
              AND intent.goal_revision=(SELECT revision FROM v4_goals WHERE id=intent.goal_id)
              AND NOT EXISTS (SELECT 1 FROM v4_admissions AS admission
                  WHERE admission.intent_id=intent.id AND admission.disposition IN ('admitted','denied'))
        """, (goal_id,)).fetchone()["count"]

    def request_cycle(self, goal_id, command: CycleRequest):
        command = CycleRequest.model_validate(command.model_dump())
        with self._command("cycles:write") as connection:
            goal, spec = self._policy_goal(connection, goal_id)
            return self._request_cycle(connection, goal, spec, command)

    def _request_cycle(self, connection, goal, spec, command):
        payload = command.model_dump(mode="json") | {"subject_id": str(self.subject_id)}
        digest = fingerprint(payload)
        existing = connection.execute("SELECT * FROM v4_cycle_requests WHERE goal_id=%s AND origin=%s AND idempotency_key=%s", (goal["id"], command.origin, command.idempotency_key)).fetchone()
        if existing:
            if existing["fingerprint"] != digest:
                raise ValueError("cycle request fingerprint conflict")
            return existing["intent_id"]
        now = connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
        if command.expected_revision != goal["revision"]:
            raise ValueError("stale schedule revision")
        if goal["state"] != "active" or now >= spec.horizon_end:
            raise ValueError("active goal horizon required")
        cadence = spec.cadence
        slot_time = command.slot_time.astimezone(timezone.utc)
        elapsed = (slot_time-cadence.anchor).total_seconds()
        if elapsed < 0 or elapsed % cadence.interval_seconds or slot_time >= spec.horizon_end:
            raise ValueError("aligned slot within goal horizon required")
        if slot_time > now+timedelta(seconds=cadence.interval_seconds):
            raise ValueError("only the next scheduled slot may be queued")
        if command.origin == "event" and not 0 <= (now-command.event_at).total_seconds() <= cadence.event_freshness_seconds:
            raise ValueError("fresh event required")
        if command.origin == "event":
            prior_event = connection.execute("SELECT slot_time,payload FROM v4_cycle_requests WHERE goal_id=%s AND origin='event' AND payload->>'event_id'=%s LIMIT 1", (goal["id"], command.event_id)).fetchone()
            if prior_event and (prior_event["slot_time"] != slot_time or prior_event["payload"]["event_at"] != payload["event_at"]):
                raise ValueError("event identity cannot move or change timestamp")
        if command.backfill:
            current_authority(connection, self.workspace_id, self.subject_id, "cycles:backfill")
            if not 0 <= (now-slot_time).total_seconds() <= cadence.backfill_seconds:
                raise ValueError("bounded past backfill required")
        elif (now-slot_time).total_seconds() > cadence.stale_after_seconds:
            raise ValueError("stale slot requires explicit backfill")
        slot = "utc:"+slot_time.isoformat()
        committed = connection.execute("SELECT id FROM v4_cycle_intents WHERE goal_id=%s AND slot=%s", (goal["id"], slot)).fetchone()
        if not committed and self._pending(connection, goal["id"]) >= cadence.max_pending:
            raise ValueError("pending intent limit exceeded")
        expiry = min(slot_time+timedelta(seconds=cadence.intent_ttl_seconds), spec.horizon_end)
        intent_id = self._request_intent(connection, goal, spec, slot=slot, due_at=slot_time, expires_at=expiry, predecessor_cycle_id=command.predecessor_cycle_id, reuse_policy_slot=True)
        connection.execute("INSERT INTO v4_cycle_requests (goal_id,origin,idempotency_key,fingerprint,intent_id,goal_revision,slot_time,backfill,payload) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", (goal["id"], command.origin, command.idempotency_key, digest, intent_id, goal["revision"], slot_time, command.backfill, Jsonb(payload)))
        self._event(connection, goal["id"], "cycle_requested", payload, intent_id)
        return intent_id

    def request_due(self, goal_id, *, expected_revision, idempotency_key):
        if type(expected_revision) is not int or expected_revision < 1 or not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 256:
            raise ValueError("bounded poll key and positive revision required")
        digest = fingerprint([str(self.subject_id), expected_revision])
        with self._command("cycles:write") as connection:
            goal, spec = self._policy_goal(connection, goal_id)
            existing = connection.execute("SELECT fingerprint,payload FROM v4_schedule_batches WHERE goal_id=%s AND idempotency_key=%s", (goal_id, idempotency_key)).fetchone()
            if existing:
                if existing["fingerprint"] != digest:
                    raise ValueError("schedule batch fingerprint conflict")
                return existing["payload"]
            now = connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
            if goal["revision"] != expected_revision or goal["state"] != "active" or now >= spec.horizon_end:
                raise ValueError("current active schedule revision required")
            cadence = spec.cadence
            interval = timedelta(seconds=cadence.interval_seconds)
            last = connection.execute("SELECT last_slot FROM v4_schedule_cursors WHERE goal_id=%s AND goal_revision=%s", (goal_id, expected_revision)).fetchone()
            first = last["last_slot"]+interval if last else cadence.anchor
            latest = cadence.anchor+((now-cadence.anchor)//interval)*interval
            total = max(0, (latest-first)//interval+1)
            available = cadence.max_pending-self._pending(connection, goal_id)
            fresh = max(0, (now-timedelta(seconds=cadence.stale_after_seconds)-cadence.anchor)//interval+1)
            first_fresh = max(first, cadence.anchor+fresh*interval)
            limit = min(cadence.max_catch_up, cadence.max_pending) if cadence.catch_up == "bounded" else 1
            start = max(first_fresh, latest-(limit-1)*interval)
            selected = max(0, (latest-start)//interval+1) if total else 0
            slots = [start+index*interval for index in range(selected)]
            keys = ["utc:"+slot.astimezone(timezone.utc).isoformat() for slot in slots]
            coalesced = connection.execute("SELECT count(*) AS count FROM v4_cycle_intents WHERE goal_id=%s AND slot=ANY(%s)", (goal_id, keys)).fetchone()["count"]
            held = selected-coalesced > available
            result = {"goal_revision": expected_revision, "cutoff": now.isoformat(), "mode": cadence.catch_up,
                      "intent_ids": [], "skipped": 0, "held": held}
            if not held and total:
                for slot in slots:
                    command = CycleRequest(origin="scheduled", expected_revision=expected_revision, slot_time=slot,
                                           idempotency_key=f"schedule:{expected_revision}:{slot.astimezone(timezone.utc).isoformat()}")
                    result["intent_ids"].append(str(self._request_cycle(connection, goal, spec, command)))
                result["skipped"] = total-selected
                connection.execute("INSERT INTO v4_schedule_cursors (goal_id,goal_revision,last_slot) VALUES (%s,%s,%s) ON CONFLICT (goal_id,goal_revision) DO UPDATE SET last_slot=EXCLUDED.last_slot", (goal_id, expected_revision, latest))
            connection.execute("INSERT INTO v4_schedule_batches (goal_id,idempotency_key,fingerprint,payload) VALUES (%s,%s,%s,%s)", (goal_id, idempotency_key, digest, Jsonb(result)))
            self._event(connection, goal_id, "schedule_polled", result)
            return result
