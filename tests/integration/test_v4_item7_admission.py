"""Independent item-7 regressions for goal state command ordering."""

from concurrent.futures import ThreadPoolExecutor
import json
from urllib.parse import urlsplit
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
import pytest
from pydantic import ValidationError

from salience.api.routes.v4_cycles import GoalStateCommand
from salience.cycles.outbox import CycleOutbox, enqueue_cycle_message
from test_v4_cadence_policy import policy
from test_v4_cadence_policy import request
from test_v4_cycle_admission import cycles, intent, rows
from salience.cycles.authority import context_authorized
from test_v4_public_commands import public_fixture


def test_goal_state_requires_exact_command_binding():
    with pytest.raises(ValidationError):
        GoalStateCommand.model_validate({"state": "active"})


def test_lost_ack_state_retry_cannot_override_later_pause(policy):
    service, goal, _, database = policy
    service.set_goal_state(goal, "paused", expected_revision=1,
                           expected_state_revision=1, idempotency_key="pause-1",
                           reason="Prepare paused fixture")
    # The active command commits but its caller loses the response.
    active = dict(expected_revision=1, expected_state_revision=2,
                  idempotency_key="resume-lost-ack", reason="Explicit fixture resume")
    first = service.set_goal_state(goal, "active", **active)
    # A later operator command pauses admission again.
    service.set_goal_state(goal, "paused", expected_revision=1,
                           expected_state_revision=3, idempotency_key="pause-2",
                           reason="Operator pause supersedes earlier resume")
    # Exact retransmission must return the old result or conflict; it must not
    # constitute new authority to reverse the more recent pause.
    assert service.set_goal_state(goal, "active", **active) == first
    with psycopg.connect(database, row_factory=dict_row) as connection:
        current = connection.execute(
            "SELECT state,state_revision FROM v4_goals WHERE id=%s", (goal,)
        ).fetchone()
    assert current["state"] == "paused", "lost-ack retry reactivated a later pause"
    assert current["state_revision"] == 4


def test_state_cas_rejects_competing_commands_and_conflicting_retry(policy):
    service, goal, _, database = policy

    def pause(key):
        try:
            return service.set_goal_state(
                goal, "paused", expected_revision=1, expected_state_revision=1,
                idempotency_key=key, reason="Competing pause command"
            )
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(pause, ["competing-1", "competing-2"]))
    assert sum(value is not None for value in result) == 1
    with psycopg.connect(database, row_factory=dict_row) as connection:
        recorded = connection.execute(
            "SELECT * FROM v4_goal_state_commands WHERE goal_id=%s", (goal,)
        ).fetchall()
        assert len(recorded) == 1
        assert recorded[0]["actor_id"] == service.subject_id
        with pytest.raises(psycopg.Error, match="immutable"), connection.transaction():
            connection.execute(
                "UPDATE v4_goal_state_commands SET reason='changed' WHERE goal_id=%s", (goal,)
            )
    with pytest.raises(ValueError):
        service.set_goal_state(
            goal, "active", expected_revision=1, expected_state_revision=1,
            idempotency_key=recorded[0]["idempotency_key"], reason="Changed retry"
        )
    with pytest.raises(ValueError):
        service.set_goal_state(
            goal, "active", expected_revision=1, expected_state_revision=1,
            idempotency_key="stale-new-command", reason="Stale state revision"
        )


def test_state_command_must_bind_current_goal_revision(policy):
    service, goal, spec, _ = policy
    service.revise_goal(
        goal, spec.model_copy(update={"objective": "Changed fixture objective"}),
        expected_revision=1, idempotency_key="revise", reason="Goal changed"
    )
    with pytest.raises(ValueError):
        service.set_goal_state(
            goal, "paused", expected_revision=1, expected_state_revision=1,
            idempotency_key="stale-goal-pause", reason="Observed superseded goal"
        )


def test_unknown_canonical_context_schema_cannot_authorize_consumption(policy):
    service, goal, spec, database = policy
    intent = service.request_cycle(goal, request(spec))
    cycle, context, operation = uuid4(), uuid4(), uuid4()
    with psycopg.connect(database, row_factory=dict_row) as connection:
        approval = connection.execute(
            "SELECT approval_id FROM v4_goal_baselines WHERE goal_id=%s AND goal_revision=1", (goal,)
        ).fetchone()["approval_id"]
        # Application roles have INSERT on these canonical tables. Unsupported
        # persisted versions must hold even when common fixture fields survive.
        payload = spec.model_dump(mode="json") | {
            "schema_version": "RunContext.local.v99",
            "baseline_approval_id": str(approval), "production_effects_enabled": False
        }
        connection.execute(
            "INSERT INTO v4_cycles(id,intent_id,context_id,operation_id,state) VALUES(%s,%s,%s,%s,'runnable')",
            (cycle, intent, context, operation)
        )
        connection.execute(
            "INSERT INTO v4_run_contexts(id,cycle_id,schema_version,payload) VALUES(%s,%s,'RunContext.local.v99',%s)",
            (context, cycle, Jsonb(payload))
        )
        enqueue_cycle_message(
            connection, workspace_id=service.workspace_id, subject_id=service.subject_id,
            goal_id=goal, intent_id=intent, cycle_id=cycle, kind="start",
            payload={"context_id": str(context), "operation_id": str(operation)},
            traceparent=service.trace.to_carrier()["traceparent"]
        )
        message = connection.execute(
            "SELECT id FROM v4_cycle_outbox WHERE cycle_id=%s", (cycle,)
        ).fetchone()["id"]
    assert CycleOutbox(database, workspace_id=service.workspace_id).consume(message)["state"] == "held"


def test_genuine_legacy_context_remains_authorized(cycles):
    service, goal, _, database = cycles
    admitted = service.admit(intent(service, goal))
    context = next(row for row in rows(database, "v4_run_contexts")
                   if row["cycle_id"] == admitted["cycle_id"])
    assert context["schema_version"] == "RunContext.local.v1"
    assert context["payload"]["schema_version"] == "GoalSpec.local.v1"
    with psycopg.connect(database, row_factory=dict_row) as connection:
        assert context_authorized(connection, context["payload"], service.workspace_id, service.subject_id)
    outbox = CycleOutbox(database, workspace_id=service.workspace_id)
    message = next(row for row in rows(database, "v4_cycle_outbox")
                   if row["cycle_id"] == admitted["cycle_id"])
    assert outbox.consume(message["id"])["state"] == "recorded"


@pytest.mark.parametrize("schema", ["RunContext.local.v2", "RunContext.local.v3", "RunContext.local.v99", None])
def test_malformed_context_cannot_grant_authority(schema):
    class NoAuthorityDatabase:
        def execute(self, *args, **kwargs):
            raise AssertionError("malformed context should hold before authority reads")

    assert context_authorized(NoAuthorityDatabase(), {"schema_version": schema}, uuid4(), uuid4()) is False


def test_state_audit_failure_rolls_back_projection_and_command(policy, monkeypatch):
    service, goal, _, database = policy

    def fail(*args, **kwargs):
        raise RuntimeError("state audit failure")

    monkeypatch.setattr(service, "_event", fail)
    with pytest.raises(RuntimeError, match="state audit failure"):
        service.set_goal_state(
            goal, "paused", expected_revision=1, expected_state_revision=1,
            idempotency_key="rollback-state", reason="Atomic state command"
        )
    with psycopg.connect(database, row_factory=dict_row) as connection:
        goal_row = connection.execute("SELECT state,state_revision FROM v4_goals WHERE id=%s", (goal,)).fetchone()
        assert goal_row == {"state": "active", "state_revision": 1}
        assert connection.execute("SELECT count(*) AS count FROM v4_goal_state_commands WHERE goal_id=%s", (goal,)).fetchone()["count"] == 0


def test_signed_state_command_requires_binding_and_preserves_exact_retry(public_fixture, monkeypatch, capsys):
    client, headers, workspace, subject, spec, database = public_fixture
    created = client.post(
        f"/v1/workspaces/{workspace}/v4/goals", json=spec.model_dump(mode="json"),
        headers=headers | {"Idempotency-Key": "item7-state"}
    )
    assert created.status_code == 201
    goal = created.json()["goal_id"]
    path = f"/v1/v4/goals/{goal}/state"
    assert client.post(path, headers=headers, json={"state": "paused"}).status_code == 422
    command = {"state": "paused", "expected_revision": 1, "expected_state_revision": 1,
               "idempotency_key": "signed-pause", "reason": "Signed fixture pause"}
    first = client.post(path, headers=headers, json=command)
    assert first.status_code == 200, first.text
    resume = command | {"state": "active", "expected_state_revision": 2,
                        "idempotency_key": "signed-resume", "reason": "Signed fixture resume"}
    assert client.post(path, headers=headers, json=resume).status_code == 200
    retry = client.post(path, headers=headers, json=command)
    assert retry.status_code == 200 and retry.json() == first.json()
    expected = {"goal_id": goal, "state": "active", "state_revision": 3, "goal_revision": 1}
    # A competing writer's latest revision must be readable through the same
    # scoped API, SDK and CLI, even when this reader has no write grant.
    with psycopg.connect(database, row_factory=dict_row) as connection:
        assert connection.execute("SELECT state,state_revision FROM v4_goals WHERE id=%s", (goal,)).fetchone() == {"state": "active", "state_revision": 3}
        connection.execute(
            "DELETE FROM permission_grants WHERE workspace_id=%s AND principal_id=%s AND scope='goals:write'",
            (workspace, str(subject))
        )
    inspected = client.get(f"/v1/v4/goals/{goal}", headers=headers)
    assert inspected.status_code == 200, inspected.text
    assert inspected.json() == expected
    assert client.get(f"/v1/v4/goals/{uuid4()}", headers=headers).status_code == 403

    from salience.cli import main
    from salience.sdk.client import SalienceClient

    def bridge(method, url, **kwargs):
        return client.request(method, urlsplit(url).path, headers=kwargs["headers"], json=kwargs.get("json"))

    monkeypatch.setattr("httpx.request", bridge)
    monkeypatch.setenv("SALIENCE_CONTROL_URL", "http://fixture.invalid")
    monkeypatch.setenv("SALIENCE_CONTROL_JWT", headers["Authorization"][7:])
    assert SalienceClient("http://fixture.invalid", headers["Authorization"][7:]).cycles.inspect_goal(goal) == expected
    main(["cycles", "goal-inspect", "--goal-id", goal])
    assert json.loads(capsys.readouterr().out) == expected
    with psycopg.connect(database) as connection:
        connection.execute(
            "DELETE FROM permission_grants WHERE workspace_id=%s AND principal_id=%s AND scope='cycles:read'",
            (workspace, str(subject))
        )
    assert client.get(f"/v1/v4/goals/{goal}", headers=headers).status_code == 403


def test_direct_sql_state_change_requires_exact_command_receipt(policy):
    _, goal, _, database = policy
    with psycopg.connect(database) as connection:
        with pytest.raises(psycopg.Error, match="exact next command receipt"), connection.transaction():
            connection.execute("UPDATE v4_goals SET state='paused',state_revision=2 WHERE id=%s", (goal,))
        with pytest.raises(psycopg.Error, match="revision changes only with state"), connection.transaction():
            connection.execute("UPDATE v4_goals SET state_revision=2 WHERE id=%s", (goal,))
