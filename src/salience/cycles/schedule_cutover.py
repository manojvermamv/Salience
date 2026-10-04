"""Fixture-only, one-goal legacy schedule handoff with durable fencing."""

import asyncio
from datetime import datetime, timedelta, timezone
import logging
import os
import re
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from salience.cycles.admission import CycleAdmission
from salience.cycles.authority import current_authority
from salience.cycles.cadence import fingerprint
from salience.cycles.contracts import GoalSpecV2, parse_goal


LOGGER = logging.getLogger(__name__)


class FixtureLegacyScheduleControl:
    def __init__(self, database_url, *, workspace_id):
        self.database_url = database_url
        self.workspace_id = UUID(str(workspace_id))

    def _schedule(self, connection, schedule_id, *, lock=False):
        connection.execute("SET LOCAL statement_timeout='3s'")
        row = connection.execute(
            "SELECT * FROM job_schedules WHERE id=%s AND workspace_id=%s" + (" FOR UPDATE" if lock else ""),
            (schedule_id, self.workspace_id),
        ).fetchone()
        if not row or row["job_type"] != "v4_fixture_legacy" or row["payload"].get("dry_run") is not True:
            raise PermissionError("scoped dry-run fixture legacy schedule required")
        if row["timezone"] != "UTC" or not re.fullmatch(r"every:[1-9][0-9]*s", row["schedule_expression"]):
            raise ValueError("compatible UTC fixture interval required")
        return row

    def describe(self, schedule_id):
        with psycopg.connect(self.database_url, row_factory=dict_row, connect_timeout=3) as connection:
            row = self._schedule(connection, schedule_id)
            try:
                last_slot = datetime.fromisoformat(row["payload"]["last_slot"])
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError("verified last legacy slot required") from error
            if last_slot.tzinfo is None:
                raise ValueError("aware last legacy slot required")
            return {"paused": row["status"] == "paused", "last_slot": last_slot,
                    "next_run_at": row["next_run_at"], "interval_seconds": int(row["schedule_expression"][6:-1]),
                    "resume_after": row["payload"].get("v4_resume_after")}

    def pause(self, schedule_id):
        with psycopg.connect(self.database_url, row_factory=dict_row, connect_timeout=3) as connection:
            row = self._schedule(connection, schedule_id, lock=True)
            if row["status"] not in {"active", "paused"}:
                raise ValueError("active or already paused fixture schedule required")
            if row["status"] == "active":
                connection.execute("UPDATE job_schedules SET status='paused',updated_at=clock_timestamp() WHERE id=%s", (schedule_id,))

    def resume_after(self, schedule_id, after_slot, *, subject_id=None):
        with psycopg.connect(self.database_url, row_factory=dict_row, connect_timeout=3) as connection:
            connection.execute("SET LOCAL statement_timeout='3s'")
            # This is the actual fixture resume transaction, after any lost
            # acknowledgment/callback boundary. Serialize its write with stop.
            from salience.cycles.governance import CycleGovernance
            cutover = connection.execute("SELECT * FROM v4_schedule_cutovers WHERE legacy_schedule_id=%s AND workspace_id=%s", (schedule_id,self.workspace_id)).fetchone()
            if not cutover or cutover["state"] not in {"rollback_pending","rolled_back"}:
                raise PermissionError("canonical rollback fence required before legacy resume")
            actor = subject_id or cutover["actor_id"]
            current_authority(connection,self.workspace_id,actor,"cycles:schedule")
            governance = CycleGovernance(self.database_url,workspace_id=self.workspace_id,subject_id=actor)
            scheduler = CycleScheduleCutover(self.database_url,workspace_id=self.workspace_id,subject_id=actor,legacy_control=self)
            goal, spec = scheduler._policy_goal(connection,cutover["goal_id"])
            governance._require_running(connection,goal["id"])
            cutover = scheduler._row(connection,goal["id"],lock=True)
            if cutover['state'] not in {'rollback_pending','rolled_back'} or cutover['rollback_after_slot'] != after_slot:
                raise PermissionError("exact current rollback watermark required")
            if scheduler._unresolved(connection,goal["id"],cutover["first_v4_slot"]):
                raise ValueError("unresolved canonical work holds legacy resume")
            original = connection.execute("SELECT payload FROM v4_goal_revisions WHERE goal_id=%s AND revision=%s", (goal["id"],cutover["goal_revision"])).fetchone()
            if goal["state"] != "active" or spec.cadence.interval_seconds != parse_goal(original["payload"]).cadence.interval_seconds:
                raise PermissionError("compatible active goal required at legacy resume")
            row = self._schedule(connection, schedule_id, lock=True)
            if row['content_program_id'] != spec.content_program_id or row['schedule_expression'] != f'every:{spec.cadence.interval_seconds}s':
                raise PermissionError("same-program compatible legacy resume required")
            if row["status"] == "active":
                if row["payload"].get("v4_resume_after") != after_slot.isoformat() or row["next_run_at"] is None or row["next_run_at"] <= after_slot:
                    raise ValueError("active legacy schedule lacks exact rollback readback")
                current_authority(connection,self.workspace_id,actor,"cycles:schedule")
                current_authority(connection,self.workspace_id,actor)
                return
            if row["status"] != "paused" or row["next_run_at"] is None:
                raise ValueError("paused legacy schedule with anchor required")
            interval = timedelta(seconds=int(row["schedule_expression"][6:-1]))
            anchor = row["next_run_at"]
            now = connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
            boundary = max(after_slot, now)
            steps = max(0, (boundary - anchor) // interval + 1)
            next_slot = anchor + steps * interval
            if next_slot <= boundary:
                next_slot += interval
            connection.execute("UPDATE job_schedules SET status='active',next_run_at=%s,payload=jsonb_set(payload,'{v4_resume_after}',to_jsonb(%s::text),true),updated_at=clock_timestamp() WHERE id=%s",
                               (next_slot, after_slot.isoformat(), schedule_id))
            current_authority(connection,self.workspace_id,actor,"cycles:schedule")
            current_authority(connection,self.workspace_id,actor)


class CycleScheduleCutover(CycleAdmission):
    def __init__(self, database_url, *, workspace_id, subject_id, legacy_control=None, trace_context=None):
        super().__init__(database_url, workspace_id=workspace_id, subject_id=subject_id, trace_context=trace_context)
        self.legacy_control = legacy_control or FixtureLegacyScheduleControl(database_url, workspace_id=workspace_id)

    @staticmethod
    def _key(value):
        if not isinstance(value, str) or not value.strip() or len(value) > 256:
            raise ValueError("bounded cutover idempotency key required")

    @staticmethod
    def _view(row):
        return {"goal_id": str(row["goal_id"]), "legacy_schedule_id": str(row["legacy_schedule_id"]),
                "goal_revision": row["goal_revision"], "first_v4_slot": row["first_v4_slot"].isoformat(),
                "state": row["state"], "rollback_after_slot": row["rollback_after_slot"].isoformat() if row["rollback_after_slot"] else None}

    def _row(self, connection, goal_id, *, lock=False):
        return connection.execute("SELECT * FROM v4_schedule_cutovers WHERE goal_id=%s AND workspace_id=%s" + (" FOR UPDATE" if lock else ""),
                                  (goal_id, self.workspace_id)).fetchone()

    def _stopped(self, connection, goal_id):
        return bool(connection.execute(
            "SELECT 1 FROM v4_stop_scopes WHERE workspace_id=%s AND stopped "
            "AND scope_key IN ('workspace',%s) LIMIT 1",
            (self.workspace_id, "goal:" + str(goal_id)),
        ).fetchone())

    def prepare(self, goal_id, *, legacy_schedule_id, expected_revision, first_v4_slot, idempotency_key):
        self._key(idempotency_key)
        if type(expected_revision) is not int or expected_revision < 1 or first_v4_slot.tzinfo is None:
            raise ValueError("positive revision and aware first V4 slot required")
        first_v4_slot = first_v4_slot.astimezone(timezone.utc)
        if first_v4_slot.microsecond:
            raise ValueError("whole-second UTC cutover slot required")
        legacy_schedule_id = UUID(str(legacy_schedule_id))
        digest = fingerprint([str(self.subject_id), str(legacy_schedule_id), expected_revision,
                              first_v4_slot.isoformat(), idempotency_key])
        with self._command("cycles:schedule") as connection:
            goal, spec = self._policy_goal(connection, goal_id)
            existing = self._row(connection, goal_id, lock=True)
            if existing:
                if existing["fingerprint"] != digest:
                    raise ValueError("schedule cutover preparation conflict")
                return self._view(existing)
            now = connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
            if goal["state"] != "active" or goal["revision"] != expected_revision or not now < first_v4_slot < spec.horizon_end:
                raise ValueError("current active revision and future cutover slot required")
            elapsed = (first_v4_slot - spec.cadence.anchor).total_seconds()
            if elapsed < 0 or elapsed % spec.cadence.interval_seconds:
                raise ValueError("aligned first V4 slot required")
            schedule = connection.execute("SELECT * FROM job_schedules WHERE id=%s AND workspace_id=%s FOR UPDATE",
                                          (legacy_schedule_id, self.workspace_id)).fetchone()
            if not schedule or schedule["content_program_id"] != spec.content_program_id or schedule["job_type"] != "v4_fixture_legacy" or schedule["payload"].get("dry_run") is not True:
                raise PermissionError("same-program dry-run fixture schedule required")
            if schedule["status"] != "active" or schedule["timezone"] != "UTC" or schedule["schedule_expression"] != f"every:{spec.cadence.interval_seconds}s" or schedule["next_run_at"] != first_v4_slot:
                raise ValueError("compatible active legacy schedule and first slot required")
            try:
                last_slot = datetime.fromisoformat(schedule["payload"]["last_slot"])
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError("verified last legacy slot required") from error
            if last_slot.tzinfo is None or last_slot.astimezone(timezone.utc) >= first_v4_slot:
                raise ValueError("last legacy slot must precede V4 watermark")
            if connection.execute("SELECT 1 FROM v4_schedule_cursors WHERE goal_id=%s UNION ALL SELECT 1 FROM v4_schedule_batches WHERE goal_id=%s UNION ALL SELECT 1 FROM v4_cycle_requests WHERE goal_id=%s AND origin='scheduled' LIMIT 1", (goal_id, goal_id, goal_id)).fetchone():
                raise ValueError("existing canonical scheduled work cannot be cut over")
            row = connection.execute("""
                INSERT INTO v4_schedule_cutovers
                (goal_id,workspace_id,goal_revision,legacy_schedule_id,actor_id,prepare_key,fingerprint,first_v4_slot,traceparent)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *
            """, (goal_id, self.workspace_id, expected_revision, legacy_schedule_id, self.subject_id,
                   idempotency_key, digest, first_v4_slot, self.trace.to_carrier()["traceparent"])).fetchone()
            self._event(connection, goal_id, "schedule_cutover_prepared", self._view(row))
            return self._view(row)

    def activate(self, goal_id, *, idempotency_key):
        self._key(idempotency_key)
        with self._command("cycles:schedule") as connection:
            goal, _ = self._policy_goal(connection, goal_id)
            row = self._row(connection, goal_id)
            if not row:
                raise ValueError("prepared schedule cutover required")
            if row["state"] not in {"pending", "active"}:
                raise ValueError("schedule cutover cannot activate after rollback")
            if row["state"] == "active" and row["activation_key"] != idempotency_key:
                raise ValueError("schedule cutover activation conflict")
            if goal["revision"] != row["goal_revision"] or goal["state"] != "active":
                raise ValueError("current active schedule revision required")
        if row["state"] == "pending":
            try:
                self.legacy_control.pause(row["legacy_schedule_id"])
            except TimeoutError:
                pass
        snapshot = self.legacy_control.describe(row["legacy_schedule_id"])
        if not snapshot["paused"] or snapshot["last_slot"].astimezone(timezone.utc) >= row["first_v4_slot"]:
            raise ValueError("legacy schedule pause and predecessor slot must be verified")
        with self._command("cycles:schedule") as connection:
            goal, spec = self._policy_goal(connection, goal_id)
            current = self._row(connection, goal_id, lock=True)
            if current["state"] == "active":
                if current["activation_key"] != idempotency_key:
                    raise ValueError("schedule cutover activation conflict")
                return self._view(current)
            if current["state"] != "pending" or goal["revision"] != current["goal_revision"] or goal["state"] != "active":
                raise ValueError("current pending schedule revision required")
            if self._stopped(connection, goal_id):
                raise PermissionError("current stop blocks schedule activation")
            legacy = connection.execute("SELECT status FROM job_schedules WHERE id=%s AND workspace_id=%s FOR UPDATE", (current["legacy_schedule_id"], self.workspace_id)).fetchone()
            if not legacy or legacy["status"] != "paused":
                raise ValueError("legacy fixture schedule must remain paused")
            predecessor = current["first_v4_slot"] - timedelta(seconds=spec.cadence.interval_seconds)
            connection.execute("INSERT INTO v4_schedule_cursors (goal_id,goal_revision,last_slot) VALUES (%s,%s,%s)",
                               (goal_id, current["goal_revision"], predecessor))
            active = connection.execute("UPDATE v4_schedule_cutovers SET state='active',activation_key=%s,activated_at=clock_timestamp() WHERE goal_id=%s RETURNING *",
                                        (idempotency_key, goal_id)).fetchone()
            self._event(connection, goal_id, "schedule_cutover_activated", self._view(active))
            return self._view(active)

    def poll(self, goal_id, *, expected_revision, idempotency_key):
        self._key(idempotency_key)
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("positive schedule revision required")
        with self._command("cycles:schedule") as connection:
            goal, spec = self._policy_goal(connection, goal_id)
            row = self._row(connection, goal_id, lock=True)
            if not row:
                raise ValueError("prepared schedule cutover required")
            if row["state"] != "active":
                return {"state": row["state"], "intent_ids": [], "admissions": []}
            if goal["revision"] != expected_revision or row["goal_revision"] != expected_revision:
                raise ValueError("stale schedule cutover revision")
            if self._stopped(connection, goal_id):
                return {"state": "held", "reason": "stopped", "intent_ids": [], "admissions": []}
            snapshot = self.legacy_control.describe(row["legacy_schedule_id"])
            legacy = connection.execute("SELECT status FROM job_schedules WHERE id=%s AND workspace_id=%s FOR UPDATE",
                                        (row["legacy_schedule_id"], self.workspace_id)).fetchone()
            if not snapshot["paused"] or snapshot["last_slot"].astimezone(timezone.utc) >= row["first_v4_slot"] or not legacy or legacy["status"] != "paused":
                raise ValueError("legacy schedule is not fenced")
            batch = self._request_due(connection, goal, spec, expected_revision=expected_revision,
                                      idempotency_key=idempotency_key, cutover_poll=True)
            pending = connection.execute("""
                SELECT DISTINCT request.intent_id,request.slot_time FROM v4_cycle_requests AS request
                JOIN v4_cycle_intents AS intent ON intent.id=request.intent_id
                WHERE request.goal_id=%s AND request.origin='scheduled' AND request.goal_revision=%s
                  AND request.slot_time >= %s AND request.slot_time <= clock_timestamp()
                  AND NOT EXISTS (SELECT 1 FROM v4_admissions AS admission
                      WHERE admission.intent_id=intent.id AND admission.eligibility_revision=intent.eligibility_revision)
                ORDER BY request.slot_time,request.intent_id LIMIT 100
            """, (goal_id, expected_revision, row["first_v4_slot"])).fetchall()
        intent_ids = list(dict.fromkeys(batch["intent_ids"] + [str(entry["intent_id"]) for entry in pending]))
        admissions = []
        for intent_id in intent_ids:
            result = self.admit(intent_id)
            admissions.append({"intent_id": intent_id, "disposition": result["disposition"],
                               "cycle_id": str(result["cycle_id"]) if result["cycle_id"] else None})
        return {"state": "active", "intent_ids": batch["intent_ids"], "admissions": admissions,
                "skipped": batch["skipped"], "held": batch["held"]}

    def _unresolved(self, connection, goal_id, first_v4_slot):
        return bool(connection.execute("""
            SELECT 1 FROM v4_cycle_intents AS intent
            WHERE intent.goal_id=%s AND intent.due_at >= %s AND (
                NOT EXISTS (SELECT 1 FROM v4_admissions AS admission
                    WHERE admission.intent_id=intent.id AND admission.disposition IN ('admitted','denied'))
                OR EXISTS (SELECT 1 FROM v4_cycles AS cycle WHERE cycle.intent_id=intent.id AND cycle.state!='closed')
                OR EXISTS (SELECT 1 FROM v4_cycle_outbox AS message WHERE message.intent_id=intent.id AND message.state!='delivered')
                OR EXISTS (SELECT 1 FROM v4_permit_claims AS claim
                    JOIN v4_cycles AS cycle ON cycle.id=claim.cycle_id
                    WHERE cycle.intent_id=intent.id AND (claim.state!='claimed' OR NOT EXISTS (
                        SELECT 1 FROM v4_cycle_events AS event WHERE event.cycle_id=cycle.id
                          AND event.kind='fixture_adapter_accepted'
                          AND event.payload->>'operation_id'=cycle.operation_id::text)))
            ) LIMIT 1
        """, (goal_id, first_v4_slot)).fetchone())

    def rollback(self, goal_id, *, idempotency_key):
        self._key(idempotency_key)
        with self._command("cycles:schedule") as connection:
            _, spec = self._policy_goal(connection, goal_id)
            row = self._row(connection, goal_id, lock=True)
            if not row:
                raise ValueError("prepared schedule cutover required")
            if row["state"] == "rolled_back":
                if row["rollback_key"] != idempotency_key:
                    raise ValueError("schedule cutover rollback conflict")
                return self._view(row)
            if row["state"] == "rollback_pending":
                if row["rollback_key"] != idempotency_key:
                    raise ValueError("schedule cutover rollback conflict")
            else:
                original = connection.execute("SELECT payload FROM v4_goal_revisions WHERE goal_id=%s AND revision=%s",
                                              (goal_id, row["goal_revision"])).fetchone()
                original_spec = parse_goal(original["payload"])
                interval = timedelta(seconds=original_spec.cadence.interval_seconds)
                cursor = connection.execute("SELECT last_slot FROM v4_schedule_cursors WHERE goal_id=%s AND goal_revision=%s",
                                            (goal_id, row["goal_revision"])).fetchone()
                after_slot = cursor["last_slot"] if cursor else row["first_v4_slot"] - interval
                row = connection.execute("UPDATE v4_schedule_cutovers SET state='rollback_pending',rollback_key=%s,rollback_after_slot=%s,rollback_started_at=clock_timestamp() WHERE goal_id=%s RETURNING *",
                                         (idempotency_key, after_slot, goal_id)).fetchone()
                self._event(connection, goal_id, "schedule_cutover_rollback_started", self._view(row))
        with self._command("cycles:schedule") as connection:
            goal, current_spec = self._policy_goal(connection, goal_id)
            row = self._row(connection, goal_id, lock=True)
            original = connection.execute("SELECT payload FROM v4_goal_revisions WHERE goal_id=%s AND revision=%s",
                                          (goal_id, row["goal_revision"])).fetchone()
            original_spec = parse_goal(original["payload"])
            if goal["state"] != "active" or current_spec.cadence.interval_seconds != original_spec.cadence.interval_seconds:
                raise ValueError("compatible active goal required before legacy resume")
            if self._stopped(connection, goal_id):
                raise PermissionError("current stop blocks legacy resume")
            if self._unresolved(connection, goal_id, row["first_v4_slot"]):
                raise ValueError("unresolved intents, outbox or permits hold legacy resume")
        try:
            self.legacy_control.resume_after(row["legacy_schedule_id"], row["rollback_after_slot"], subject_id=self.subject_id)
        except TimeoutError:
            pass
        snapshot = self.legacy_control.describe(row["legacy_schedule_id"])
        if snapshot["paused"] or snapshot["resume_after"] != row["rollback_after_slot"].isoformat() or snapshot["next_run_at"] is None or snapshot["next_run_at"] <= row["rollback_after_slot"]:
            raise ValueError("legacy resume and later slot must be verified")
        with self._command("cycles:schedule") as connection:
            self._policy_goal(connection, goal_id)
            current = self._row(connection, goal_id, lock=True)
            legacy = connection.execute("SELECT status,next_run_at,payload FROM job_schedules WHERE id=%s AND workspace_id=%s FOR UPDATE",
                                        (current["legacy_schedule_id"], self.workspace_id)).fetchone()
            if current["state"] != "rollback_pending" or current["rollback_key"] != idempotency_key or not legacy or legacy["status"] != "active" or legacy["payload"].get("v4_resume_after") != current["rollback_after_slot"].isoformat() or legacy["next_run_at"] <= current["rollback_after_slot"]:
                raise ValueError("verified rollback state and later legacy slot required")
            if self._unresolved(connection, goal_id, current["first_v4_slot"]):
                raise ValueError("new unresolved work holds legacy resume")
            done = connection.execute("UPDATE v4_schedule_cutovers SET state='rolled_back',rolled_back_at=clock_timestamp() WHERE goal_id=%s RETURNING *",
                                      (goal_id,)).fetchone()
            self._event(connection, goal_id, "schedule_cutover_rolled_back", self._view(done))
            return self._view(done)

    def inspect(self, goal_id):
        with self._command("cycles:read") as connection:
            row = connection.execute("SELECT cutover.* FROM v4_schedule_cutovers AS cutover JOIN v4_goals AS goal ON goal.id=cutover.goal_id WHERE cutover.goal_id=%s AND goal.workspace_id=%s",
                                     (goal_id, self.workspace_id)).fetchone()
            if not row:
                raise PermissionError("schedule cutover outside current scope")
            return self._view(row)


class FixtureSchedulePoller:
    def __init__(self, database_url, *, workspace_id, batch_size=8):
        if type(batch_size) is not int or not 1 <= batch_size <= 32:
            raise ValueError("bounded fixture schedule batch required")
        self.database_url = database_url
        self.workspace_id = UUID(str(workspace_id))
        self.batch_size = batch_size
        self._after = None

    @staticmethod
    def _fixture_only():
        if os.environ.get("SALIENCE_DEPLOYMENT_MODE") != "fixture" or os.environ.get("SALIENCE_EFFECTS_ENABLED", "false") != "false":
            raise PermissionError("automatic V4 schedule polling requires no-effects fixture mode")

    def poll_once(self):
        self._fixture_only()
        with psycopg.connect(self.database_url, row_factory=dict_row, connect_timeout=3) as connection:
            connection.execute("SET LOCAL statement_timeout='3s'")
            now = connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
            selection = """
                SELECT cutover.goal_id,cutover.goal_revision,cutover.actor_id,cutover.first_v4_slot,
                       cutover.created_at,cursor.last_slot,revision.payload
                FROM v4_schedule_cutovers AS cutover
                JOIN v4_schedule_cursors AS cursor ON cursor.goal_id=cutover.goal_id
                    AND cursor.goal_revision=cutover.goal_revision
                JOIN v4_goal_revisions AS revision ON revision.goal_id=cutover.goal_id
                    AND revision.revision=cutover.goal_revision
                WHERE cutover.workspace_id=%s AND cutover.state='active' AND cutover.first_v4_slot<=%s
            """
            order = " ORDER BY cutover.created_at,cutover.goal_id LIMIT %s"
            if self._after is None:
                active = connection.execute(selection+order,(self.workspace_id,now,self.batch_size)).fetchall()
            else:
                active = connection.execute(selection+" AND (cutover.created_at,cutover.goal_id)>(%s,%s)"+order,
                                            (self.workspace_id,now,*self._after,self.batch_size)).fetchall()
                if len(active) < self.batch_size:
                    active += connection.execute(selection+" AND (cutover.created_at,cutover.goal_id)<=(%s,%s)"+order,
                                                 (self.workspace_id,now,*self._after,self.batch_size-len(active))).fetchall()
            if active:
                self._after = (active[-1]["created_at"],active[-1]["goal_id"])
            due = []
            invalid = 0
            for row in active:
                try:
                    spec = parse_goal(row["payload"])
                    if not isinstance(spec,GoalSpecV2):
                        raise ValueError("typed cadence policy required")
                except ValueError:
                    LOGGER.warning("fixture schedule policy held for goal %s",row["goal_id"])
                    invalid += 1
                    continue
                interval = timedelta(seconds=spec.cadence.interval_seconds)
                latest = spec.cadence.anchor + ((now - spec.cadence.anchor) // interval) * interval
                pending = connection.execute("""
                    SELECT 1 FROM v4_cycle_requests AS request
                    JOIN v4_cycle_intents AS intent ON intent.id=request.intent_id
                    WHERE request.goal_id=%s AND request.origin='scheduled'
                      AND request.goal_revision=%s AND request.slot_time >= %s
                      AND NOT EXISTS (SELECT 1 FROM v4_admissions AS admission
                          WHERE admission.intent_id=intent.id AND admission.eligibility_revision=intent.eligibility_revision)
                    LIMIT 1
                """, (row["goal_id"], row["goal_revision"], row["first_v4_slot"])).fetchone()
                if latest > row["last_slot"] or pending:
                    due.append((row, latest))
        results = {"selected": len(due), "polled": 0, "admitted": 0, "held": invalid}
        for row, latest in due:
            service = CycleScheduleCutover(self.database_url, workspace_id=self.workspace_id,
                                           subject_id=row["actor_id"])
            try:
                result = service.poll(row["goal_id"], expected_revision=row["goal_revision"],
                                      idempotency_key="fixture-auto:" + latest.isoformat())
            except (PermissionError, ValueError):
                LOGGER.warning("fixture schedule poll held for goal %s", row["goal_id"], exc_info=True)
                results["held"] += 1
                continue
            results["polled"] += 1
            results["admitted"] += sum(admission["disposition"] == "admitted" for admission in result["admissions"])
            results["held"] += result["state"] != "active"
        return results

    async def run_until_stopped(self, *, stop_event, poll_interval_ms=1000):
        self._fixture_only()
        if type(poll_interval_ms) is not int or not 1000 <= poll_interval_ms <= 60000:
            raise ValueError("bounded independent schedule poll interval required")
        while not stop_event.is_set():
            try:
                await asyncio.to_thread(self.poll_once)
            except psycopg.Error:
                LOGGER.exception("fixture schedule database poll failed")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=poll_interval_ms / 1000)
            except TimeoutError:
                pass
