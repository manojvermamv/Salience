"""Read native legacy schedule identities without rewriting their contracts."""

from datetime import datetime, timedelta
import re


def schedule_row(connection, schedule_id, workspace_id, *, lock=False, temporal=False):
    row = connection.execute("SELECT schedule.*,to_jsonb(schedule)-ARRAY['status','next_run_at','updated_at'] AS original_identity FROM job_schedules schedule WHERE id=%s AND workspace_id=%s" + (" FOR UPDATE" if lock else ""), (schedule_id, workspace_id)).fetchone()
    if not row:
        raise PermissionError("scoped legacy schedule required")
    if row["job_type"] == "intelligence_research":
        if not temporal or not connection.execute("SELECT to_regclass('public.v4_native_schedule_sources') AS relation").fetchone()["relation"]:
            raise PermissionError("native schedule requires qualified Temporal source mapping")
        source = connection.execute("SELECT source.*,progress.last_slot,progress.resume_after,progress.next_slot FROM v4_native_schedule_sources source JOIN v4_native_schedule_progress progress USING(schedule_id) WHERE source.schedule_id=%s AND source.workspace_id=%s", (schedule_id, workspace_id)).fetchone()
        if not source or source["original_identity"] != row["original_identity"]:
            raise PermissionError("original native schedule binding required")
        row["native_source"] = source
    elif row["job_type"] == "governed_publication":
        if not temporal:
            raise PermissionError("native publication schedule requires qualified Temporal source mapping")
        from salience.cycles.native_publication_schedules import load_native_publication_source
        row["native_publication_source"] = load_native_publication_source(
            connection, schedule_id, workspace_id, lock=lock
        )
    elif row["job_type"] != "v4_fixture_legacy":
        raise PermissionError("scoped original legacy schedule required")
    if (not row.get("native_publication_source") and row["payload"].get("dry_run") is not True) or row["timezone"] != "UTC":
        raise PermissionError("scoped UTC no-effects legacy schedule required")
    expression = r"every [1-9][0-9]*s" if row.get("native_source") or row.get("native_publication_source") else r"every:[1-9][0-9]*s"
    if not re.fullmatch(expression, row["schedule_expression"]):
        raise ValueError("compatible legacy interval required")
    return row


def schedule_metadata(row):
    if row.get("native_publication_source"):
        from salience.cycles.native_publication_schedules import publication_schedule_metadata
        return publication_schedule_metadata(row)
    source = row.get("native_source")
    if source:
        return {"interval_seconds": source["interval_seconds"], "remote_id": source["remote_id"],
            "last_slot": source["last_slot"] if source["last_slot"] is not None else source["first_v4_slot"]-timedelta(seconds=source["interval_seconds"]),
            "last_slot_is_boundary": source["last_slot"] is None, "next_run_at": source["next_slot"],
            "resume_after": source["resume_after"].isoformat() if source["resume_after"] else None}
    try:
        last_slot = datetime.fromisoformat(row["payload"]["last_slot"])
    except (KeyError, ValueError, TypeError) as error:
        raise ValueError("verified last legacy slot required") from error
    if last_slot.tzinfo is None:
        raise ValueError("aware last legacy slot required")
    return {"interval_seconds": int(row["schedule_expression"][6:-1]), "remote_id": row["payload"].get("temporal_schedule_id"),
        "last_slot": last_slot, "last_slot_is_boundary": False, "next_run_at": row["next_run_at"],
        "resume_after": row["payload"].get("v4_resume_after")}


def require_native_binding(row, *, goal_id, actor_id, task_queue=None, first_v4_slot=None):
    if row.get("native_publication_source"):
        from salience.cycles.native_publication_schedules import require_native_publication_binding
        require_native_publication_binding(
            row, goal_id=goal_id, actor_id=actor_id,
            task_queue=task_queue, first_v4_slot=first_v4_slot,
        )
        return
    source = row.get("native_source")
    if source and (str(source["goal_id"]) != str(goal_id) or str(source["actor_id"]) != str(actor_id)
            or (task_queue is not None and source["task_queue"] != task_queue)
            or (first_v4_slot is not None and source["first_v4_slot"] != first_v4_slot)):
        raise PermissionError("original native goal, actor, slot and worker binding required")
