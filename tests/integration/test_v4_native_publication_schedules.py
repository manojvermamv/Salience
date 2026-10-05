"""Qualify the original paired governed-publication schedule cutover."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import os
import subprocess
import sys
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
import pytest
from temporalio.client import Client

from test_v4_legacy_publication import _allow, _publication_fixture, _wait_started


def _token(actor_id, key):
    import jwt

    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "iss": "https://fixture.invalid",
            "aud": "fixture",
            "sub": str(actor_id),
            "iat": now,
            "nbf": now,
            "exp": now + timedelta(minutes=15),
        },
        key,
        algorithm="RS256",
    )


async def _decode_action_argument(client, action):
    from temporalio.common import RawValue
    from temporalio.api.common.v1 import Payload

    assert len(action.args) == 1
    argument = action.args[0]
    if isinstance(argument, RawValue):
        return (await client.data_converter.decode([argument.payload], [dict]))[0]
    if isinstance(argument, Payload):
        return (await client.data_converter.decode([argument], [dict]))[0]
    return argument


@asynccontextmanager
async def native_publication_schedule(
    monkeypatch, *, interval_seconds=60, seed_history=False
):
    from cryptography.hazmat.primitives.asymmetric import rsa
    from fastapi.testclient import TestClient

    from salience.api.dependencies import TemporalControlPlane
    from salience.api.p0 import create_p0_app
    from salience.cycles.contracts import CadencePolicy
    from salience.publication.repository import PublicationRepository
    from test_v4_cycle_admission import approved_goal

    monkeypatch.setenv("SALIENCE_DEPLOYMENT_MODE", "fixture")
    monkeypatch.setenv("SALIENCE_EFFECTS_ENABLED", "false")
    database = os.environ["TEST_DATABASE_URL"]
    fixture = await _publication_fixture(database)
    _allow(
        database,
        fixture["workspace_id"],
        fixture["actor_id"],
        (
            "cycles:schedule",
            "control:write",
            "legacy:publication:reconcile:account:" + str(fixture["account"].id),
        ),
    )

    repository = PublicationRepository(database)
    publication_request = await repository.create_request(
        ready_package_id=fixture["ready"]["ready_package_id"],
        workspace_id=str(fixture["workspace_id"]),
        content_program_id=fixture["ready"]["program_id"],
        publisher_account_id=fixture["account"].id,
        publication_approval_request_id=fixture["approval_id"],
        idempotency_key="native-publication-schedule-" + uuid4().hex,
    )
    publication_plan = await repository.create_plan(
        publication_request_id=publication_request.id,
        version=1,
        publisher_id="fixture-publisher",
        publisher_version="1",
    )
    queue = fixture["queue"]
    target = os.environ["TEST_TEMPORAL_TARGET"]
    plane = TemporalControlPlane(
        database_url=database.replace("postgresql://", "postgresql+asyncpg://"),
        temporal_target=target,
        task_queue=queue,
    )
    schedule = await plane.create_publication_schedule(
        workspace_id=str(fixture["workspace_id"]),
        content_program_id=fixture["ready"]["program_id"],
        publication_request_id=publication_request.id,
        publication_plan_id=publication_plan.id,
        schedule_version=1,
        name="native-publication-" + uuid4().hex,
        every_seconds=interval_seconds,
        budget_id=str(fixture["budget_id"]),
    )
    schedule_id = UUID(schedule.schedule_id)
    with psycopg.connect(database, row_factory=dict_row) as connection:
        paired = connection.execute(
            "SELECT id,job_schedule_id FROM publication_schedules WHERE job_schedule_id=%s",
            (schedule_id,),
        ).fetchone()
    assert paired is not None and paired["job_schedule_id"] == schedule_id
    publication_schedule_id = UUID(str(paired["id"]))

    client = await Client.connect(target)
    remote_id = "publication:" + str(publication_schedule_id)
    handle = client.get_schedule_handle(remote_id)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    app = create_p0_app(
        database_url=database,
        workspace_id=fixture["workspace_id"],
        issuer="https://fixture.invalid",
        audience="fixture",
        public_key=key.public_key(),
        enable_legacy_dispatch=True,
        legacy_fixture_queue=queue,
        legacy_temporal_target=target,
    )
    token = _token(fixture["actor_id"], key)
    headers = {"Authorization": "Bearer " + token}
    extra_handles = []
    try:
        description = await handle.describe()
        if seed_history:
            await handle.trigger()
            async with asyncio.timeout(10):
                while not (description := await handle.describe()).info.recent_actions:
                    await asyncio.sleep(0.05)
        first_v4_slot = next(
            slot
            for slot in description.info.next_action_times
            if slot > datetime.now(timezone.utc) + timedelta(seconds=12)
        )
        spec = fixture["spec"].model_copy(
            update={
                "cadence": CadencePolicy(
                    anchor=first_v4_slot, interval_seconds=interval_seconds
                )
            }
        )
        goal_id = approved_goal(fixture["bridge"], spec)
        fixture["bridge"].bind_publication_account(
            goal_id,
            expected_revision=1,
            publisher_account_id=fixture["account"].id,
        )
        with TestClient(app) as web:
            yield {
                **fixture,
                "app": app,
                "web": web,
                "headers": headers,
                "plane": plane,
                "client": client,
                "handle": handle,
                "schedule_id": schedule_id,
                "publication_schedule_id": publication_schedule_id,
                "remote_id": remote_id,
                "publication_request_id": UUID(publication_request.id),
                "publication_plan_id": UUID(publication_plan.id),
                "first_v4_slot": first_v4_slot,
                "goal_id": goal_id,
                "spec": spec,
                "extra_handles": extra_handles,
            }
    finally:
        for cleanup_handle in [handle, *extra_handles]:
            try:
                await cleanup_handle.pause()
            except Exception:
                pass
            try:
                current = await cleanup_handle.describe()
                for action in current.info.running_actions:
                    await client.get_workflow_handle(action.workflow_id).terminate(
                        reason="End disposable native publication schedule fixture"
                    )
            except Exception:
                pass
            try:
                await cleanup_handle.delete()
            except Exception:
                pass


def _auth_headers(native):
    return native["headers"]


def _api(native, method, path, **kwargs):
    response = getattr(native["web"], method)(
        path, headers=_auth_headers(native), **kwargs
    )
    assert response.status_code < 400, response.text
    return response.json()


@pytest.mark.asyncio
async def test_native_publication_adoption_snapshots_real_original_action_history(
    monkeypatch,
):
    async with native_publication_schedule(monkeypatch, seed_history=True) as native:
        before = await native["handle"].describe()
        assert before.info.num_actions > 0
        assert before.info.recent_actions
        original_actions = sorted(
            before.info.recent_actions, key=lambda action: action.scheduled_at
        )
        first_v4_slot = native["first_v4_slot"]
        assert original_actions[-1].scheduled_at < first_v4_slot

        adopted = _api(
            native,
            "post",
            f"/v1/publications/schedules/{native['goal_id']}/adopt-native",
            json={
                "legacy_schedule_id": str(native["schedule_id"]),
                "publication_schedule_id": str(native["publication_schedule_id"]),
                "expected_revision": 1,
                "first_v4_slot": first_v4_slot.isoformat(),
            },
        )
        assert adopted["observed_last_slot"] == original_actions[-1].scheduled_at.isoformat()

        with psycopg.connect(native["database"], row_factory=dict_row) as connection:
            source = connection.execute(
                "SELECT remote_snapshot,observed_last_slot FROM v4_native_publication_schedule_sources WHERE schedule_id=%s",
                (native["schedule_id"],),
            ).fetchone()
            progress = connection.execute(
                "SELECT last_slot FROM v4_native_publication_schedule_progress WHERE schedule_id=%s",
                (native["schedule_id"],),
            ).fetchone()
        expected_history = [
            {
                "scheduled_at": action.scheduled_at.isoformat(),
                "started_at": action.started_at.isoformat(),
                "workflow_id": action.action.workflow_id,
                "first_execution_run_id": action.action.first_execution_run_id,
            }
            for action in original_actions
        ]
        assert source["remote_snapshot"]["action_count"] == before.info.num_actions
        assert source["remote_snapshot"]["history_start_slot"] == (
            original_actions[0].scheduled_at.isoformat()
        )
        assert source["remote_snapshot"]["recent_actions"] == expected_history
        assert source["observed_last_slot"] == original_actions[-1].scheduled_at
        assert progress["last_slot"] == original_actions[-1].scheduled_at


@pytest.mark.asyncio
async def test_actual_native_publication_pair_is_adopted_fenced_and_resumed_through_outbox(
    monkeypatch,
):
    from salience.cycles.legacy_schedule_workflow import LEGACY_SCHEDULE_INGRESS
    from salience.cycles.outbox import CycleOutbox
    from salience.cycles.legacy_runtime import build_legacy_worker
    from salience.cycles.runtime import TemporalCycleTransport
    from temporalio.client import ScheduleHandle

    async with native_publication_schedule(monkeypatch) as native:
        database = native["database"]
        schedule_id = native["schedule_id"]
        publication_schedule_id = native["publication_schedule_id"]
        goal_id = native["goal_id"]
        first = native["first_v4_slot"]
        remote_id = native["remote_id"]
        handle = native["handle"]
        client = native["client"]

        # The canonical legacy profile replaces the original direct controller.
        rejected = native["web"].post(
            "/v1/publications/schedules",
            headers=_auth_headers(native),
            json={
                "workspace_id": str(native["workspace_id"]),
                "content_program_id": native["ready"]["program_id"],
                "publication_request_id": str(native["publication_request_id"]),
                "publication_plan_id": str(native["publication_plan_id"]),
                "schedule_version": 1,
                "name": "blocked-direct-publication-schedule",
                "every_seconds": 60,
                "budget_id": str(native["budget_id"]),
            },
        )
        assert rejected.status_code == 403

        before_remote = await handle.describe()
        original_action = before_remote.schedule.action
        assert original_action.workflow == "GovernedPublicationWorkflow"
        assert original_action.id == remote_id + ":execution"
        assert original_action.task_queue == native["queue"]
        assert await _decode_action_argument(client, original_action) == {
            "scheduled_publication_schedule_id": str(publication_schedule_id),
            "contract_version": "PublicationWorkflowRequest@v1",
        }
        assert before_remote.info.num_actions == 0
        assert before_remote.info.recent_actions == []

        with psycopg.connect(database, row_factory=dict_row) as connection:
            original_job = connection.execute(
                """SELECT to_jsonb(schedule)-ARRAY['status','next_run_at','updated_at'] AS identity,
                          schedule.payload,schedule.job_type,schedule.schedule_expression
                   FROM job_schedules schedule WHERE schedule.id=%s""",
                (schedule_id,),
            ).fetchone()
            original_publication = connection.execute(
                "SELECT to_jsonb(schedule) AS identity,job_schedule_id,status,budget_id,publication_request_id,publication_plan_id,version FROM publication_schedules schedule WHERE id=%s",
                (publication_schedule_id,),
            ).fetchone()
            authorization_facts = connection.execute(
                """SELECT request.publisher_account_id,request.publication_approval_request_id,
                          request.publisher_capability_profile_id,request.platform,request.destination,
                          request.locale,request.territory,request.visibility,
                          request.capability_profile_version,profile.publisher_id,profile.publisher_version,
                          profile.profile_version,profile.audit_state,account.status,approval.status AS approval_status,
                          budget.scope,budget.status AS budget_status,budget.limit_amount
                   FROM publication_schedules schedule
                   JOIN publication_requests request ON request.id=schedule.publication_request_id
                   JOIN publisher_accounts account ON account.id=request.publisher_account_id
                   JOIN publisher_capability_profiles profile
                     ON profile.id=request.publisher_capability_profile_id
                   JOIN approval_requests approval
                     ON approval.id=request.publication_approval_request_id
                   JOIN budgets budget ON budget.id=schedule.budget_id
                   WHERE schedule.id=%s""",
                (publication_schedule_id,),
            ).fetchone()
            assert original_job["job_type"] == "governed_publication"
            assert original_job["schedule_expression"] == "every 60s"
            assert original_job["payload"] == {
                "publication_request_id": str(native["publication_request_id"]),
                "publication_plan_id": str(native["publication_plan_id"]),
                "budget_id": str(native["budget_id"]),
                "schedule_version": 1,
                "contract_version": "PublicationSchedule@v1",
            }
            assert original_publication["job_schedule_id"] == schedule_id
            assert original_publication["status"] == "scheduled"
            assert original_publication["budget_id"] == native["budget_id"]
            assert original_publication["publication_request_id"] == native["publication_request_id"]
            assert original_publication["publication_plan_id"] == native["publication_plan_id"]
            assert original_publication["version"] == 1
            assert str(authorization_facts["publisher_account_id"]) == str(native["account"].id)
            assert str(authorization_facts["publication_approval_request_id"]) == str(native["approval_id"])
            assert authorization_facts["platform"] == "fixture"
            assert authorization_facts["destination"] == "fixture://account"
            assert authorization_facts["locale"] == "en"
            assert authorization_facts["territory"] == "global"
            assert authorization_facts["visibility"] == "private"
            assert authorization_facts["capability_profile_version"] == 1
            assert authorization_facts["publisher_id"] == "fixture-publisher"
            assert authorization_facts["publisher_version"] == "1"
            assert authorization_facts["profile_version"] == 1
            assert authorization_facts["audit_state"] == "verified"
            assert authorization_facts["status"] == "active"
            assert authorization_facts["approval_status"] == "approved"
            assert authorization_facts["scope"] == "publication"
            assert authorization_facts["budget_status"] == "active"
            assert authorization_facts["limit_amount"] == 0
            assert connection.execute(
                "SELECT count(*) AS job_count FROM jobs WHERE workspace_id=%s AND job_type='governed_publication'",
                (native["workspace_id"],),
            ).fetchone()["job_count"] == 0

        wrong_publication_id = uuid4()
        wrong_pair = native["web"].post(
            f"/v1/publications/schedules/{goal_id}/adopt-native",
            headers=_auth_headers(native),
            json={
                "legacy_schedule_id": str(schedule_id),
                "publication_schedule_id": str(wrong_publication_id),
                "expected_revision": 1,
                "first_v4_slot": first.isoformat(),
            },
        )
        assert wrong_pair.status_code == 403

        wrong_stage = await native["plane"].create_intelligence_schedule(
            workspace_id=str(native["workspace_id"]),
            content_program_id=native["ready"]["program_id"],
            name="wrong-stage-" + uuid4().hex,
            every_seconds=60,
            niche="Fixture",
        )
        wrong_stage_handle = client.get_schedule_handle(wrong_stage.schedule_id)
        native["extra_handles"].append(wrong_stage_handle)
        wrong_stage_response = native["web"].post(
            f"/v1/publications/schedules/{goal_id}/adopt-native",
            headers=_auth_headers(native),
            json={
                "legacy_schedule_id": wrong_stage.schedule_id,
                "publication_schedule_id": str(publication_schedule_id),
                "expected_revision": 1,
                "first_v4_slot": first.isoformat(),
            },
        )
        assert wrong_stage_response.status_code == 403
        await wrong_stage_handle.pause()
        await wrong_stage_handle.delete()
        native["extra_handles"].remove(wrong_stage_handle)
        adopted = _api(
            native,
            "post",
            f"/v1/publications/schedules/{goal_id}/adopt-native",
            json={
                "legacy_schedule_id": str(schedule_id),
                "publication_schedule_id": str(publication_schedule_id),
                "expected_revision": 1,
                "first_v4_slot": first.isoformat(),
            },
        )
        assert adopted["schedule_id"] == str(schedule_id)
        assert adopted["publication_schedule_id"] == str(publication_schedule_id)
        assert adopted["remote_id"] == remote_id
        assert adopted["observed_last_slot"] is None

        with psycopg.connect(database, row_factory=dict_row) as connection:
            source = connection.execute(
                "SELECT * FROM v4_native_publication_schedule_sources WHERE schedule_id=%s",
                (schedule_id,),
            ).fetchone()
            progress = connection.execute(
                "SELECT * FROM v4_native_publication_schedule_progress WHERE schedule_id=%s",
                (schedule_id,),
            ).fetchone()
            assert source["publication_schedule_id"] == publication_schedule_id
            assert source["remote_id"] == remote_id
            assert source["original_identity"] == original_job["identity"]
            assert source["publication_identity"] == original_publication["identity"]
            assert source["observed_last_slot"] is None
            assert source["remote_snapshot"]["action_count"] == 0
            assert source["remote_snapshot"]["history_start_slot"] is None
            assert source["remote_snapshot"]["recent_actions"] == []
            assert progress["last_slot"] is None
            assert progress["next_slot"] == first
            plan_guard_definitions = connection.execute(
                """SELECT proname,pg_get_functiondef(oid) AS definition FROM pg_proc
                   WHERE proname IN ('v4_guard_legacy_publication_mapping',
                       'v4_guard_mapped_publication_account',
                       'v4_guard_legacy_publication_fixture_receipt',
                       'v4_guard_legacy_publication_fixture_progress')
                   ORDER BY proname"""
            ).fetchall()
            assert {row["proname"] for row in plan_guard_definitions} == {
                "v4_guard_legacy_publication_mapping",
                "v4_guard_mapped_publication_account",
                "v4_guard_legacy_publication_fixture_receipt",
                "v4_guard_legacy_publication_fixture_progress",
            }
            before_migration_failure = {
                "source": connection.execute(
                    "SELECT to_jsonb(source) AS snapshot FROM v4_native_publication_schedule_sources source WHERE schedule_id=%s",
                    (schedule_id,),
                ).fetchone()["snapshot"],
                "progress": connection.execute(
                    "SELECT to_jsonb(progress) AS snapshot FROM v4_native_publication_schedule_progress progress WHERE schedule_id=%s",
                    (schedule_id,),
                ).fetchone()["snapshot"],
            }

        unprepared_bind = native["web"].post(
            f"/v1/publications/schedules/{goal_id}/bind",
            headers=_auth_headers(native),
            json={"expected_revision": 1},
        )
        assert unprepared_bind.status_code == 403
        prepared = _api(
            native,
            "post",
            f"/v1/v4/goals/{goal_id}/schedule-cutover",
            json={
                "legacy_schedule_id": str(schedule_id),
                "expected_revision": 1,
                "first_v4_slot": first.isoformat(),
                "idempotency_key": "native-publication-prepare-" + uuid4().hex,
            },
        )
        assert prepared["state"] == "pending"
        bound = _api(
            native,
            "post",
            f"/v1/publications/schedules/{goal_id}/bind",
            json={"expected_revision": 1},
        )
        assert bound["state"] == "bound"

        # A true original SDK action is drained before the schedule can be fenced.
        await handle.trigger()
        async with asyncio.timeout(10):
            while not (description := await handle.describe()).info.running_actions:
                await asyncio.sleep(0.05)
        activation_key = "native-publication-activate-" + uuid4().hex
        refused = native["web"].post(
            f"/v1/v4/goals/{goal_id}/schedule-cutover/activate",
            headers=_auth_headers(native),
            json={"idempotency_key": activation_key},
        )
        assert refused.status_code == 409
        for action in description.info.running_actions:
            await client.get_workflow_handle(action.workflow_id).terminate(
                reason="Drain original governed-publication schedule action"
            )
        async with asyncio.timeout(10):
            while (await handle.describe()).info.running_actions:
                await asyncio.sleep(0.05)
        active = _api(
            native,
            "post",
            f"/v1/v4/goals/{goal_id}/schedule-cutover/activate",
            json={"idempotency_key": activation_key},
        )
        assert active["state"] == "active"
        paused_remote = await handle.describe()
        assert paused_remote.schedule.state.paused
        assert paused_remote.schedule.action.workflow == "GovernedPublicationWorkflow"
        assert (await _decode_action_argument(client, paused_remote.schedule.action)) == {
            "scheduled_publication_schedule_id": str(publication_schedule_id),
            "contract_version": "PublicationWorkflowRequest@v1",
        }

        early_poll = _api(
            native,
            "post",
            f"/v1/v4/goals/{goal_id}/schedule-cutover/poll",
            json={"expected_revision": 1, "idempotency_key": "before-native-slot"},
        )
        assert early_poll["state"] == "active" and early_poll["intent_ids"] == []

        # Failed M41 downgrade is transactional and preserves the source, progress,
        # publication plan, original pair, and M40 authorization guard functions.
        with psycopg.connect(database, row_factory=dict_row) as connection:
            guard_snapshot = {
                row["proname"]: row["definition"] for row in connection.execute(
                    """SELECT proname,pg_get_functiondef(oid) AS definition FROM pg_proc
                       WHERE proname IN ('v4_guard_legacy_publication_mapping',
                           'v4_guard_mapped_publication_account',
                           'v4_guard_legacy_publication_fixture_receipt',
                           'v4_guard_legacy_publication_fixture_progress')"""
                ).fetchall()
            }
            cutover_plan = connection.execute(
                "SELECT to_jsonb(plan) AS plan_payload FROM v4_legacy_publication_schedule_plans plan WHERE goal_id=%s",
                (goal_id,),
            ).fetchone()["plan_payload"]
            assert cutover_plan["schedule_id"] == str(schedule_id)
            assert cutover_plan["publication_schedule_id"] == str(publication_schedule_id)
            expected_revision = connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()["version_num"]
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "alembic",
                "-x",
                "database_url=" + database,
                "downgrade",
                "0040_legacy_publication_stage",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode != 0
        assert "preserve original native publication schedule history" in result.stderr
        with psycopg.connect(database, row_factory=dict_row) as connection:
            assert connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()["version_num"] == expected_revision
            assert connection.execute(
                "SELECT to_jsonb(source) AS snapshot FROM v4_native_publication_schedule_sources source WHERE schedule_id=%s",
                (schedule_id,),
            ).fetchone()["snapshot"] == before_migration_failure["source"]
            assert connection.execute(
                "SELECT to_jsonb(progress) AS snapshot FROM v4_native_publication_schedule_progress progress WHERE schedule_id=%s",
                (schedule_id,),
            ).fetchone()["snapshot"] == before_migration_failure["progress"]
            assert connection.execute(
                "SELECT to_jsonb(plan) AS plan_payload FROM v4_legacy_publication_schedule_plans plan WHERE goal_id=%s",
                (goal_id,),
            ).fetchone()["plan_payload"] == cutover_plan
            after_guards = {
                row["proname"]: row["definition"] for row in connection.execute(
                    """SELECT proname,pg_get_functiondef(oid) AS definition FROM pg_proc
                       WHERE proname IN ('v4_guard_legacy_publication_mapping',
                           'v4_guard_mapped_publication_account',
                           'v4_guard_legacy_publication_fixture_receipt',
                           'v4_guard_legacy_publication_fixture_progress')"""
                ).fetchall()
            }
            assert after_guards == guard_snapshot
            assert connection.execute(
                "SELECT payload FROM job_schedules WHERE id=%s", (schedule_id,)
            ).fetchone()["payload"] == original_job["payload"]
            pair_before_rollback = connection.execute(
                "SELECT id,job_schedule_id,status FROM publication_schedules WHERE id=%s",
                (publication_schedule_id,),
            ).fetchone()
            assert pair_before_rollback["id"] == publication_schedule_id
            assert pair_before_rollback["job_schedule_id"] == schedule_id
            assert pair_before_rollback["status"] == "scheduled"

        rollback_key = "native-publication-rollback-" + uuid4().hex
        original_update = ScheduleHandle.update
        update_calls = []

        async def committed_update_lost_ack(schedule_handle, updater, **kwargs):
            await original_update(schedule_handle, updater, **kwargs)
            update_calls.append(schedule_handle.id)
            raise TimeoutError("Native publication schedule update committed; acknowledgment lost")

        monkeypatch.setattr(ScheduleHandle, "update", committed_update_lost_ack)
        rolled_back = _api(
            native,
            "post",
            f"/v1/v4/goals/{goal_id}/schedule-cutover/rollback",
            json={"idempotency_key": rollback_key},
        )
        assert rolled_back["state"] == "rolled_back"
        assert len(update_calls) == 1 and update_calls[0] == remote_id
        monkeypatch.setattr(ScheduleHandle, "update", original_update)
        rollback_readback = await handle.describe()
        assert rollback_readback.schedule.action.workflow == LEGACY_SCHEDULE_INGRESS
        assert rollback_readback.schedule.action.id == remote_id + ":admission"
        assert rollback_readback.schedule.action.task_queue == native["queue"]
        assert not rollback_readback.schedule.state.paused
        ingress_argument = await _decode_action_argument(
            client, rollback_readback.schedule.action
        )
        assert ingress_argument == {
            "workspace_id": str(native["workspace_id"]),
            "content_program_id": native["ready"]["program_id"],
            "goal_id": str(goal_id),
            "schedule_id": str(schedule_id),
            "dry_run": True,
            "publication_schedule_id": str(publication_schedule_id),
        }
        cutover = _api(native, "get", f"/v1/v4/goals/{goal_id}/schedule-cutover")
        rollback_after = datetime.fromisoformat(cutover["rollback_after_slot"])
        assert rollback_readback.schedule.state.note == (
            "V4 no-effects rollback watermark:" + rollback_after.isoformat()
        )
        assert rollback_readback.schedule.spec.start_at > rollback_after

        # The resumed original schedule now starts only bounded ingress. Its real
        # SDK action must write one canonical publication job through the outbox.
        outbox = CycleOutbox(database, workspace_id=native["workspace_id"], delivery_lane="legacy")
        transport = TemporalCycleTransport(client, task_queue=native["queue"])
        async with build_legacy_worker(client, task_queue=native["queue"], outbox=outbox):
            async with asyncio.timeout(90):
                while True:
                    with psycopg.connect(database, row_factory=dict_row) as connection:
                        binding = connection.execute(
                            """SELECT binding.*,job.dry_run,job.job_type,job.state,intent.due_at
                               FROM v4_legacy_dispatches binding
                               JOIN jobs job ON job.id=binding.job_id
                               JOIN v4_cycle_intents intent ON intent.id=binding.intent_id
                               JOIN v4_cycle_requests cycle_request
                                 ON cycle_request.intent_id=intent.id
                               WHERE binding.goal_id=%s AND binding.stage='publication'
                                 AND cycle_request.origin='scheduled'
                               ORDER BY binding.created_at DESC LIMIT 1""",
                            (goal_id,),
                        ).fetchone()
                    if binding:
                        break
                    await asyncio.sleep(0.05)
            assert binding["dry_run"] is False
            assert binding["job_type"] == "governed_publication"
            assert binding["due_at"] >= first
            assert binding["payload"]["budget_id"] == str(native["budget_id"])
            with psycopg.connect(database) as connection:
                assert connection.execute(
                    "SELECT count(*) FROM jobs WHERE workspace_id=%s AND job_type='governed_publication'",
                    (native["workspace_id"],),
                ).fetchone()[0] == 1
                assert connection.execute(
                    "SELECT count(*) FROM v4_cycle_outbox WHERE cycle_id=%s AND kind='start'",
                    (binding["cycle_id"],),
                ).fetchone()[0] == 1
                source_after_ingress = connection.execute(
                    "SELECT last_slot FROM v4_native_publication_schedule_progress WHERE schedule_id=%s",
                    (schedule_id,),
                ).fetchone()[0]
                assert source_after_ingress == binding["due_at"]

            assert await outbox.dispatch_one(transport)
            main_runtime = await _wait_started(
                client, transport.workflow_id(binding["cycle_id"])
            )
            assert (await main_runtime.describe()).workflow_type == "SalienceLocalCycleWorkflow"
            operation = await _wait_started(
                client,
                "salience-v4-legacy-operation:" + str(binding["operation_id"]),
            )
            async with asyncio.timeout(45):
                publication_result = await operation.result()
            assert publication_result["job_id"] == str(binding["job_id"])
            assert publication_result["publication_state"] == "published"
            native["bridge"].close(
                binding["cycle_id"],
                disposition="completed",
                reason="Original native publication ingress verified",
            )
            assert await outbox.dispatch_one(transport)
            await asyncio.wait_for(
                client.get_workflow_handle(transport.workflow_id(binding["cycle_id"])).result(),
                10,
            )
            assert native["bridge"].inspect(binding["job_id"])["state"] == "succeeded"

        after_remote = await handle.describe()
        assert after_remote.schedule.action.workflow == LEGACY_SCHEDULE_INGRESS
        assert after_remote.schedule.action.task_queue == native["queue"]
        with psycopg.connect(database, row_factory=dict_row) as connection:
            assert connection.execute(
                "SELECT payload FROM job_schedules WHERE id=%s", (schedule_id,)
            ).fetchone()["payload"] == original_job["payload"]
            pair_after = connection.execute(
                "SELECT id,job_schedule_id,status FROM publication_schedules WHERE id=%s",
                (publication_schedule_id,),
            ).fetchone()
            assert pair_after["id"] == publication_schedule_id
            assert pair_after["job_schedule_id"] == schedule_id
            assert pair_after["status"] == "scheduled"
            jobs = connection.execute(
                "SELECT id,dry_run,job_type FROM jobs WHERE workspace_id=%s AND job_type='governed_publication'",
                (native["workspace_id"],),
            ).fetchall()
            assert len(jobs) == 1
            assert jobs[0]["id"] == binding["job_id"]
            assert jobs[0]["dry_run"] is False
            assert jobs[0]["job_type"] == "governed_publication"
            assert connection.execute(
                "SELECT count(*) AS message_count FROM v4_cycle_outbox WHERE cycle_id=%s AND kind='start'",
                (binding["cycle_id"],),
            ).fetchone()["message_count"] == 1


def test_native_publication_migration_empty_downgrade_restores_m40_guards():
    source = os.environ["TEST_DATABASE_URL"]
    name = "native_publication_migration_" + uuid4().hex
    database = urlunsplit(urlsplit(source)._replace(path="/" + name))
    guard_names = (
        "v4_guard_legacy_publication_mapping",
        "v4_guard_mapped_publication_account",
        "v4_guard_legacy_publication_fixture_receipt",
        "v4_guard_legacy_publication_fixture_progress",
    )
    with psycopg.connect(source, autocommit=True) as admin:
        admin.execute(psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(name)))
        try:
            def migrate(direction, target):
                return subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "alembic",
                        "-x",
                        "database_url=" + database,
                        direction,
                        target,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )

            assert migrate("upgrade", "0040_legacy_publication_stage").returncode == 0
            with psycopg.connect(database, row_factory=dict_row) as connection:
                original = {
                    row["proname"]: row["definition"]
                    for row in connection.execute(
                        "SELECT proname,pg_get_functiondef(oid) AS definition FROM pg_proc WHERE proname::text=ANY(%s)",
                        (list(guard_names),),
                    ).fetchall()
                }
                assert set(original) == set(guard_names)
                original_cutover_guard = connection.execute(
                    "SELECT pg_get_functiondef('v4_preserve_schedule_cutover()'::regprocedure) AS definition"
                ).fetchone()["definition"]
            upgraded = migrate("upgrade", "head")
            assert upgraded.returncode == 0, upgraded.stderr
            with psycopg.connect(database, row_factory=dict_row) as connection:
                assert connection.execute(
                    "SELECT pg_get_functiondef('v4_preserve_schedule_cutover()'::regprocedure) AS definition"
                ).fetchone()["definition"] != original_cutover_guard
            downgraded = migrate("downgrade", "0040_legacy_publication_stage")
            assert downgraded.returncode == 0, downgraded.stderr
            with psycopg.connect(database, row_factory=dict_row) as connection:
                restored = {
                    row["proname"]: row["definition"]
                    for row in connection.execute(
                        "SELECT proname,pg_get_functiondef(oid) AS definition FROM pg_proc WHERE proname::text=ANY(%s)",
                        (list(guard_names),),
                    ).fetchall()
                }
                assert restored == original
                assert connection.execute(
                    "SELECT pg_get_functiondef('v4_preserve_schedule_cutover()'::regprocedure) AS definition"
                ).fetchone()["definition"] == original_cutover_guard
                assert connection.execute("SELECT version_num FROM alembic_version").fetchone()["version_num"] == "0040_legacy_publication_stage"
            reupgraded = migrate("upgrade", "head")
            assert reupgraded.returncode == 0, reupgraded.stderr
        finally:
            admin.execute(
                psycopg.sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    psycopg.sql.Identifier(name)
                )
            )
