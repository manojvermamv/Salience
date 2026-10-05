"""Original governed-publication schedule identity and independent cutover progress."""

import re
from uuid import UUID

PUBLICATION_SCHEDULE_CONTRACT = "PublicationSchedule@v1"
PUBLICATION_REQUEST_CONTRACT = "PublicationWorkflowRequest@v1"


def original_publication_schedule(
    connection,
    schedule_id,
    workspace_id,
    *,
    publication_schedule_id=None,
    lock=False,
    require_current_budget=True,
):
    """Read one original job schedule and its distinct immutable publication schedule."""
    clauses = ["schedule.id=%s", "schedule.workspace_id=%s"]
    parameters = [UUID(str(schedule_id)), UUID(str(workspace_id))]
    if publication_schedule_id is not None:
        clauses.append("publication.id=%s")
        parameters.append(UUID(str(publication_schedule_id)))
    query = """
        SELECT schedule.*,
               to_jsonb(schedule)-ARRAY['status','next_run_at','updated_at'] AS original_identity,
               publication.id AS publication_schedule_id,
               to_jsonb(publication) AS publication_identity,
               publication.status AS publication_status,
               publication.timezone AS publication_timezone,
               publication.scheduled_for,
               publication.version AS publication_version,
               publication.schedule_fingerprint,
               request.id AS publication_request_id,
               request.workspace_id AS request_workspace_id,
               request.content_program_id AS request_content_program_id,
               request.ready_package_id,
               request.publisher_account_id,
               request.publication_approval_request_id,
               request.platform AS request_platform,
               request.destination AS request_destination,
               request.locale AS request_locale,
               request.territory AS request_territory,
               request.visibility AS request_visibility,
               request.capability_profile_version,
               request.request_key AS publication_request_key,
               plan.id AS publication_plan_id,
               plan.publisher_id AS plan_publisher_id,
               plan.publisher_version AS plan_publisher_version,
               publication.budget_id AS publication_budget_id,
               budget.status AS budget_status,
               budget.scope AS budget_scope,
               budget.limit_amount AS budget_limit_amount
        FROM job_schedules schedule
        JOIN publication_schedules publication
          ON publication.job_schedule_id=schedule.id
        JOIN publication_requests request
          ON request.id=publication.publication_request_id
        JOIN publication_plans plan
          ON plan.id=publication.publication_plan_id
         AND plan.publication_request_id=request.id
        JOIN budgets budget
          ON budget.id=publication.budget_id
         AND budget.workspace_id=request.workspace_id
         AND budget.content_program_id=request.content_program_id
        WHERE """ + " AND ".join(clauses)
    if lock:
        query += " FOR UPDATE OF schedule"
    found = connection.execute(query, tuple(parameters)).fetchone()
    if not found:
        raise PermissionError("original paired publication and job schedules required")
    row = dict(found)
    if (
        row["job_type"] != "governed_publication"
        or row["timezone"] != "UTC"
        or row["publication_timezone"] != "UTC"
        or row["publication_status"] != "scheduled"
        or row["content_program_id"] != row["request_content_program_id"]
        or row["workspace_id"] != row["request_workspace_id"]
    ):
        raise PermissionError("original UTC governed-publication schedule required")
    match = re.fullmatch(r"every ([1-9][0-9]*)s", row["schedule_expression"])
    if not match:
        raise ValueError("compatible native publication interval required")
    interval_seconds = int(match.group(1))
    if interval_seconds > 31_536_000:
        raise ValueError("bounded native publication interval required")
    expected_schedule_payload = {
        "publication_request_id": str(row["publication_request_id"]),
        "publication_plan_id": str(row["publication_plan_id"]),
        "budget_id": str(row["publication_budget_id"]),
        "schedule_version": row["publication_version"],
        "contract_version": PUBLICATION_SCHEDULE_CONTRACT,
    }
    if row["payload"] != expected_schedule_payload:
        raise PermissionError("original immutable publication schedule payload required")
    command_payload = {
        "ready_package_id": str(row["ready_package_id"]),
        "publisher_account_id": str(row["publisher_account_id"]),
        "publication_approval_request_id": str(row["publication_approval_request_id"]),
        "budget_id": str(row["publication_budget_id"]),
        "platform": row["request_platform"],
        "destination": row["request_destination"],
        "locale": row["request_locale"],
        "territory": row["request_territory"],
        "visibility": row["request_visibility"],
        "capability_profile_version": row["capability_profile_version"],
        "contract_version": PUBLICATION_REQUEST_CONTRACT,
    }
    if (
        command_payload["platform"] != "fixture"
        or command_payload["destination"] != "fixture://account"
        or command_payload["locale"] != "en"
        or command_payload["territory"] != "global"
        or command_payload["visibility"] != "private"
        or command_payload["capability_profile_version"] != 1
        or (
            require_current_budget
            and (row["budget_scope"] != "publication" or row["budget_status"] != "active" or row["budget_limit_amount"] != 0)
        )
    ):
        raise PermissionError("original exact fixture publication scope and zero budget required")
    row["interval_seconds"] = interval_seconds
    row["command_payload"] = command_payload
    row["publication_schedule_id"] = UUID(str(row["publication_schedule_id"]))
    return row


def load_native_publication_source(connection, schedule_id, workspace_id, *, lock=False):
    """Read immutable adoption history joined to current source and progress."""
    if not connection.execute(
        "SELECT to_regclass('public.v4_native_publication_schedule_sources') AS relation"
    ).fetchone()["relation"]:
        raise PermissionError("qualified native publication schedule source migration required")
    query = """
        SELECT source.*, progress.last_slot, progress.resume_after, progress.next_slot,
               to_jsonb(schedule)-ARRAY['status','next_run_at','updated_at'] AS current_identity,
               to_jsonb(publication) AS current_publication_identity
        FROM v4_native_publication_schedule_sources source
        JOIN v4_native_publication_schedule_progress progress USING(schedule_id)
        JOIN job_schedules schedule ON schedule.id=source.schedule_id
                                    AND schedule.workspace_id=source.workspace_id
        JOIN publication_schedules publication ON publication.id=source.publication_schedule_id
                                               AND publication.job_schedule_id=schedule.id
        WHERE source.schedule_id=%s AND source.workspace_id=%s"""
    if lock:
        query += " FOR UPDATE OF schedule"
    source = connection.execute(query, (UUID(str(schedule_id)), UUID(str(workspace_id)))).fetchone()
    if not source or source["current_identity"] != source["original_identity"] or source["current_publication_identity"] != source["publication_identity"]:
        raise PermissionError("original native publication schedule binding required")
    result = dict(source)
    original = original_publication_schedule(
        connection,
        schedule_id,
        workspace_id,
        publication_schedule_id=result["publication_schedule_id"],
        lock=lock,
        require_current_budget=False,
    )
    if (
        original["original_identity"] != result["original_identity"]
        or original["publication_identity"] != result["publication_identity"]
        or result["remote_id"] != "publication:" + str(result["publication_schedule_id"])
    ):
        raise PermissionError("original native publication source identities required")
    result["original_source"] = original
    return result


def publication_schedule_metadata(row):
    source = row.get("native_publication_source")
    if source is None:
        original = row.get("original_publication_schedule")
        if original is None:
            raise PermissionError("original native publication source required")
        return {
            "interval_seconds": original["interval_seconds"],
            "remote_id": "publication:" + str(original["publication_schedule_id"]),
            "publication_schedule_id": str(original["publication_schedule_id"]),
            "last_slot": None,
            "last_slot_is_boundary": True,
            "next_run_at": row.get("next_run_at"),
            "resume_after": None,
        }
    return {
        "interval_seconds": source["interval_seconds"],
        "remote_id": source["remote_id"],
        "publication_schedule_id": str(source["publication_schedule_id"]),
        "last_slot": source["last_slot"],
        "last_slot_is_boundary": source["last_slot"] is None,
        "next_run_at": source["next_slot"],
        "resume_after": source["resume_after"].isoformat() if source["resume_after"] else None,
    }


def require_native_publication_binding(
    row,
    *,
    goal_id,
    actor_id,
    task_queue=None,
    first_v4_slot=None,
    publication_schedule_id=None,
):
    source = row.get("native_publication_source")
    if source is None:
        raise PermissionError("adopted native publication schedule source required")
    if (
        str(source["goal_id"]) != str(goal_id)
        or str(source["actor_id"]) != str(actor_id)
        or (task_queue is not None and source["task_queue"] != task_queue)
        or (first_v4_slot is not None and source["first_v4_slot"] != first_v4_slot)
        or (publication_schedule_id is not None and str(source["publication_schedule_id"]) != str(publication_schedule_id))
        or source["remote_id"] != "publication:" + str(source["publication_schedule_id"])
    ):
        raise PermissionError("original native publication goal, actor, schedule and worker binding required")
    return True


def native_publication_workflow_payload(source):
    """Return the original schedule argument without invoking scheduled job creation."""
    return {
        "scheduled_publication_schedule_id": str(source["publication_schedule_id"]),
        "contract_version": PUBLICATION_REQUEST_CONTRACT,
    }
