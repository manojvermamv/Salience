"""Qualify legacy publication through canonical V4 admission and Temporal."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
import subprocess
import sys
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import psycopg
import pytest
import pytest_asyncio


def _allow(database, workspace_id, actor_id, scopes):
    with psycopg.connect(database) as connection:
        for scope in scopes:
            connection.execute(
                """INSERT INTO permission_grants
                   (workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at)
                   VALUES(%s,'identity',%s,%s,'allow','{}',now()+interval '1 hour')""",
                (workspace_id, str(actor_id), scope),
            )


async def _publication_fixture(database, *, publication_proofs=True):
    from test_creative_release_gate_migration import (
        _approved_publication_approval,
        _approved_ready_package,
    )
    from test_v4_cycle_admission import approved_goal

    from salience.cycles.contracts import CadencePolicy, CycleRequest, GoalSpecV2
    from salience.cycles.legacy_dispatch import LegacyDispatch, LegacyPublicationCommand
    from salience.cycles.legacy_publication import account_scope
    from salience.publication.repository import PublicationRepository

    ready = await _approved_ready_package(publication_proofs=publication_proofs)
    workspace_id = UUID(ready["workspace_id"])
    actor_id = uuid4()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    account = await PublicationRepository(database).create_account(
        workspace_id=str(workspace_id),
        platform="fixture",
        account_key=f"legacy-publication-{uuid4()}",
        account_type="creator",
        external_account_reference=f"fixture:legacy-publication:{uuid4()}",
    )
    approval_id = await _approved_publication_approval(ready, account.id)
    scopes = (
        "goals:write",
        "goals:approve",
        "cycles:write",
        "cycles:read",
        "cycles:permit",
        "cycles:stop",
        "legacy:publication",
        account_scope(account.id),
    )
    with psycopg.connect(database) as connection:
        connection.execute(
            """INSERT INTO identity_subjects
               (id,workspace_id,issuer,subject,expires_at)
               VALUES(%s,%s,'https://fixture.invalid',%s,now()+interval '1 hour')""",
            (actor_id, workspace_id, str(actor_id)),
        )
    _allow(database, workspace_id, actor_id, scopes)
    with psycopg.connect(database) as connection:
        budget_id = connection.execute(
            """INSERT INTO budgets
               (workspace_id,content_program_id,name,scope,limit_amount,status)
               VALUES(%s,%s,%s,'publication',0,'active') RETURNING id""",
            (workspace_id, ready["program_id"], f"zero-publication-{uuid4()}"),
        ).fetchone()[0]

    queue = "salience-v4-local-legacy-publication-" + str(uuid4())
    bridge = LegacyDispatch(
        database, workspace_id=workspace_id, subject_id=actor_id, task_queue=queue
    )
    spec = GoalSpecV2(
        objective="Explicit controlled synthetic fixture publication only",
        channel_refs=("fixture-channel",),
        content_scope="fixture-only",
        policy_refs=("fixture-policy@1",),
        retention_policy="fixture-retention@1",
        metric_versions=("fixture-quality@1",),
        audience="internal",
        account_refs=("fixture-account",),
        brand_scope="fixture-brand",
        source_policy="fixture-only",
        horizon_end=now + timedelta(days=1),
        content_program_id=UUID(ready["program_id"]),
        cadence_seconds=None,
        cadence=CadencePolicy(anchor=now - timedelta(minutes=10), interval_seconds=60),
    )
    goal_id = approved_goal(bridge, spec)
    mapping = bridge.bind_publication_account(
        goal_id, expected_revision=1, publisher_account_id=account.id
    )
    with psycopg.connect(database) as connection:
        baseline_approval_id = connection.execute(
            "SELECT baseline_approval_id FROM v4_legacy_publication_accounts WHERE goal_id=%s",
            (goal_id,),
        ).fetchone()[0]
    command = LegacyPublicationCommand(
        goal_id=goal_id,
        request=CycleRequest(
            origin="manual",
            idempotency_key="legacy-publication-" + str(uuid4()),
            expected_revision=1,
            slot_time=now,
        ),
        ready_package_id=UUID(ready["ready_package_id"]),
        publisher_account_id=UUID(account.id),
        publication_approval_request_id=UUID(approval_id),
        budget_id=budget_id,
    )
    return {
        "database": database,
        "workspace_id": workspace_id,
        "actor_id": actor_id,
        "ready": ready,
        "account": account,
        "approval_id": approval_id,
        "budget_id": budget_id,
        "queue": queue,
        "bridge": bridge,
        "goal_id": goal_id,
        "spec": spec,
        "mapping": mapping,
        "baseline_approval_id": baseline_approval_id,
        "command": command,
    }


@pytest_asyncio.fixture
async def legacy_publication(monkeypatch):
    monkeypatch.setenv("SALIENCE_DEPLOYMENT_MODE", "fixture")
    monkeypatch.setenv("SALIENCE_EFFECTS_ENABLED", "false")
    return await _publication_fixture(os.environ["TEST_DATABASE_URL"])


async def _wait_started(client, workflow_id):
    from temporalio.service import RPCError, RPCStatusCode

    handle = client.get_workflow_handle(workflow_id)
    async with asyncio.timeout(10):
        while True:
            try:
                await handle.describe()
                return handle
            except RPCError as error:
                if error.status != RPCStatusCode.NOT_FOUND:
                    raise
                await asyncio.sleep(0.05)


async def _wait_activity_started(client, workflow_id, activity_type, *, attempt, timeout):
    """Wait for Temporal to report the named retry actually started on a worker."""
    from temporalio.api.enums.v1 import PendingActivityState
    from temporalio.client import WorkflowExecutionStatus
    from temporalio.service import RPCError, RPCStatusCode

    handle = client.get_workflow_handle(workflow_id)
    deadline = asyncio.get_running_loop().time() + timeout
    last_seen = None
    while asyncio.get_running_loop().time() < deadline:
        try:
            description = await handle.describe(rpc_timeout=timedelta(seconds=2))
        except RPCError as error:
            if error.status != RPCStatusCode.NOT_FOUND:
                raise
        else:
            for pending in description.raw_description.pending_activities:
                name = getattr(pending.activity_type, "name", None)
                if name != activity_type:
                    continue
                last_seen = {
                    "attempt": pending.attempt,
                    "state": pending.state,
                    "last_worker_identity": pending.last_worker_identity,
                    "last_started_time": pending.last_started_time,
                }
                if (
                    pending.attempt >= attempt
                    and pending.state == PendingActivityState.PENDING_ACTIVITY_STATE_STARTED
                    and pending.last_worker_identity
                    and pending.last_started_time.seconds > 0
                ):
                    return pending
            if description.status in {
                WorkflowExecutionStatus.COMPLETED,
                WorkflowExecutionStatus.FAILED,
                WorkflowExecutionStatus.CANCELED,
                WorkflowExecutionStatus.TERMINATED,
                WorkflowExecutionStatus.TIMED_OUT,
            }:
                raise AssertionError(
                    f"workflow became {description.status.name} before {activity_type} attempt {attempt}; "
                    f"last pending activity={last_seen}"
                )
        await asyncio.sleep(0.1)
    raise AssertionError(
        f"Temporal did not report {activity_type} attempt {attempt} started within {timeout}s; "
        f"last pending activity={last_seen}"
    )


def _exception_chain(error):
    seen = set()
    current = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)


async def _hard_exit_publication_worker(fixture, tmp_path, *, after_acceptance):
    worker_script = tmp_path / ("publication_worker_" + str(uuid4()) + ".py")
    worker_script.write_text(
        """import asyncio, os
from temporalio.client import Client
from salience.cycles.outbox import CycleOutbox
from salience.cycles.legacy_publication_runtime import DurableLegacyFixturePublisher
from salience.cycles.legacy_runtime import build_legacy_worker
from salience.cycles.runtime import TemporalCycleTransport

async def main():
    client = await Client.connect(os.environ['TEST_TEMPORAL_TARGET'])
    box = CycleOutbox(os.environ['TEST_DATABASE_URL'], workspace_id=os.environ['PUB_WORKSPACE'], delivery_lane='legacy')
    queue = os.environ['PUB_QUEUE']
    original = DurableLegacyFixturePublisher.submit
    async def crash_submit(self, request, lease):
        if os.environ['PUB_CRASH_MODE'] == 'before':
            os._exit(72)
        await original(self, request, lease)
        os._exit(73)
    DurableLegacyFixturePublisher.submit = crash_submit
    async with build_legacy_worker(client, task_queue=queue, outbox=box):
        await box.dispatch_one(TemporalCycleTransport(client, task_queue=queue))
        await asyncio.Event().wait()

asyncio.run(main())
""",
        encoding="utf-8",
    )
    source_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
    child_environment = os.environ | {
        "PYTHONPATH": os.pathsep.join(
            (
                os.path.join(source_root, "src"),
                os.path.join(source_root, "tests", "integration"),
            )
        ),
        "TEST_DATABASE_URL": fixture["database"],
        "TEST_TEMPORAL_TARGET": os.environ["TEST_TEMPORAL_TARGET"],
        "SALIENCE_DEPLOYMENT_MODE": "fixture",
        "SALIENCE_EFFECTS_ENABLED": "false",
        "PUB_WORKSPACE": str(fixture["workspace_id"]),
        "PUB_QUEUE": fixture["queue"],
        "PUB_CRASH_MODE": "after" if after_acceptance else "before",
    }
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(worker_script),
        env=child_environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        code = await asyncio.wait_for(process.wait(), timeout=25)
    except TimeoutError:
        process.kill()
        await process.wait()
        pytest.fail("publication worker did not exit at the requested receipt boundary")
    return code


def test_publication_atomic_replay_current_facts_and_native_job_is_not_dry_run(
    legacy_publication, monkeypatch
):
    from salience.publication.repository import PublicationRepository

    f = legacy_publication
    bridge, command, database = f["bridge"], f["command"], f["database"]
    native_payload = command.native_payload() | {
        "workspace_id": str(f["workspace_id"]),
        "content_program_id": f["ready"]["program_id"],
        "idempotency_key": "proposal-only-" + str(uuid4()),
    }
    repository = PublicationRepository(database)
    with psycopg.connect(database) as connection:
        before = connection.execute("SELECT count(*) FROM publication_requests").fetchone()[0]
        proposal = repository.proposed_authorization(connection, native_payload)
        after = connection.execute("SELECT count(*) FROM publication_requests").fetchone()[0]
    assert before == after
    assert proposal.request.id == "00000000-0000-0000-0000-000000000000"
    assert proposal.request.ready_package_id == f["ready"]["ready_package_id"]
    assert proposal.request.publisher_account_id == f["account"].id
    assert proposal.publication_approval_state == "approved"
    assert proposal.ready_package_approval_state == "approved"
    assert proposal.connection_status == "active"
    assert proposal.profile.publisher_id == "fixture-publisher"
    assert proposal.policy_allowed and proposal.rights_allowed

    materialize = bridge._materialize

    def crash_after_materialization(*args):
        materialize(*args)
        raise RuntimeError("publication enqueue before commit")

    monkeypatch.setattr(bridge, "_materialize", crash_after_materialization)
    with pytest.raises(RuntimeError, match="before commit"):
        bridge.submit_publication(command)
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT count(*) FROM v4_cycle_intents WHERE goal_id=%s", (command.goal_id,)
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM jobs WHERE workspace_id=%s AND job_type='governed_publication'",
            (f["workspace_id"],),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM v4_legacy_commands WHERE actor_id=%s", (f["actor_id"],)
        ).fetchone()[0] == 0
    monkeypatch.setattr(bridge, "_materialize", materialize)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: bridge.submit_publication(command), range(3)))
    assert results[0] == results[1] == results[2]
    result = results[0]
    assert result["dry_run"] is False and result["effects_enabled"] is False

    with psycopg.connect(database) as connection:
        job = connection.execute(
            """SELECT job.workspace_id,job.content_program_id,job.job_type,job.dry_run,
                      binding.stage,binding.cycle_id,binding.context_id,binding.operation_id,
                      binding.payload,context.payload->>'baseline_approval_id'
               FROM jobs job
               JOIN v4_legacy_dispatches binding ON binding.job_id=job.id
               JOIN v4_run_contexts context ON context.id=binding.context_id
               WHERE job.id=%s""",
            (result["job_id"],),
        ).fetchone()
        assert job[0] == f["workspace_id"]
        assert str(job[1]) == f["ready"]["program_id"]
        assert job[2:5] == ("governed_publication", False, "publication")
        assert (str(job[5]), str(job[6]), str(job[7])) == (
            result["cycle_id"], result["context_id"], result["operation_id"]
        )
        assert job[8]["budget_id"] == str(f["budget_id"])
        assert UUID(str(job[9])) == UUID(str(f["baseline_approval_id"]))
        assert connection.execute(
            "SELECT limit_amount,scope,status FROM budgets WHERE id=%s", (f["budget_id"],)
        ).fetchone() == (0, "publication", "active")
        assert connection.execute(
            "SELECT count(*) FROM v4_cycle_outbox WHERE cycle_id=%s AND kind='start'",
            (result["cycle_id"],),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM v4_legacy_publication_fixture_receipts WHERE operation_id=%s",
            (result["operation_id"],),
        ).fetchone()[0] == 0

    from pydantic import ValidationError
    for extra in ({"dry_run": True}, {"access_token": "forbidden"}, {"provider_url": "https://fixture.invalid"}):
        with pytest.raises(ValidationError):
            type(command).model_validate(command.model_dump() | extra)


@pytest.mark.parametrize("revoked_scope", ["legacy:publication", "account"])
def test_publication_revocation_before_intake_creates_no_cycle(
    legacy_publication, revoked_scope
):
    f = legacy_publication
    scope = (
        "legacy:publication"
        if revoked_scope == "legacy:publication"
        else "legacy:publication:account:" + f["account"].id
    )
    with psycopg.connect(f["database"]) as connection:
        connection.execute(
            "UPDATE permission_grants SET effect='deny' WHERE workspace_id=%s AND principal_id=%s AND scope=%s",
            (f["workspace_id"], str(f["actor_id"]), scope),
        )
    with pytest.raises(PermissionError):
        f["bridge"].submit_publication(f["command"])
    with psycopg.connect(f["database"]) as connection:
        assert connection.execute(
            "SELECT count(*) FROM v4_cycle_intents WHERE goal_id=%s", (f["goal_id"],)
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM jobs WHERE workspace_id=%s", (f["workspace_id"],)
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM v4_cycle_outbox WHERE subject_id=%s", (f["actor_id"],)
        ).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_publication_current_policy_and_rights_are_required(monkeypatch):
    monkeypatch.setenv("SALIENCE_DEPLOYMENT_MODE", "fixture")
    monkeypatch.setenv("SALIENCE_EFFECTS_ENABLED", "false")
    f = await _publication_fixture(
        os.environ["TEST_DATABASE_URL"], publication_proofs=False
    )
    from salience.publication.repository import PublicationRepository

    native_payload = f["command"].native_payload() | {
        "workspace_id": str(f["workspace_id"]),
        "content_program_id": f["ready"]["program_id"],
        "idempotency_key": "missing-native-publication-proofs",
    }
    with psycopg.connect(f["database"]) as connection:
        proposal = PublicationRepository(f["database"]).proposed_authorization(connection, native_payload)
    assert proposal.policy_allowed is False
    assert proposal.rights_allowed is False
    with pytest.raises(PermissionError, match="native publication authorization"):
        f["bridge"].submit_publication(f["command"])
    with psycopg.connect(f["database"]) as connection:
        assert connection.execute(
            "SELECT count(*) FROM v4_cycle_intents WHERE goal_id=%s", (f["goal_id"],)
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM publication_requests WHERE workspace_id=%s",
            (f["workspace_id"],),
        ).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_publication_revoked_before_native_start_has_no_temporal_or_business_effect(
    legacy_publication, monkeypatch
):
    from temporalio import activity
    from temporalio.client import Client, WorkflowFailureError
    from temporalio.service import RPCError, RPCStatusCode

    from salience.cycles.outbox import CycleOutbox
    from salience.cycles.legacy_runtime import build_legacy_worker
    from salience.cycles.runtime import TemporalCycleTransport

    f = legacy_publication
    submitted = f["bridge"].submit_publication(f["command"])
    with psycopg.connect(f["database"]) as connection:
        connection.execute(
            "UPDATE permission_grants SET effect='deny' WHERE workspace_id=%s AND principal_id=%s AND scope='legacy:publication'",
            (f["workspace_id"], str(f["actor_id"])),
        )
    client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    box = CycleOutbox(f["database"], workspace_id=f["workspace_id"], delivery_lane="legacy")
    transport = TemporalCycleTransport(client, task_queue=f["queue"])
    async with build_legacy_worker(client, task_queue=f["queue"], outbox=box):
        assert await box.dispatch_one(transport)
        async with asyncio.timeout(10):
            while f["bridge"].inspect(submitted["job_id"])["dispatch_state"] != "unknown":
                await asyncio.sleep(0.05)
        child = client.get_workflow_handle(
            "salience-v4-legacy-operation:" + submitted["operation_id"]
        )
        with pytest.raises(RPCError) as missing:
            await child.describe()
        assert missing.value.status == RPCStatusCode.NOT_FOUND
    with psycopg.connect(f["database"]) as connection:
        assert connection.execute(
            "SELECT count(*) FROM publication_requests WHERE workspace_id=%s",
            (f["workspace_id"],),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM v4_legacy_publication_fixture_receipts WHERE operation_id=%s",
            (submitted["operation_id"],),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM external_effects WHERE job_id=%s", (submitted["job_id"],)
        ).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_forged_publication_stage_or_identity_is_rejected_before_business_read(
    legacy_publication, monkeypatch
):
    from temporalio.client import Client, WorkflowFailureError

    from salience.cycles.legacy_dispatch import LegacyIntelligenceCommand
    from salience.cycles.legacy_runtime import binding_for_job, build_legacy_worker, legacy_execution
    from salience.cycles.outbox import CycleOutbox
    from salience.cycles.runtime import TemporalCycleTransport
    from salience.publication.repository import PublicationRepository
    from salience.workflows.publication import GovernedPublicationWorkflow, PublicationWorkflowRequest

    f = legacy_publication
    cases = []
    submitted = f["bridge"].submit_publication(f["command"])
    box = CycleOutbox(f["database"], workspace_id=f["workspace_id"], delivery_lane="legacy")
    binding = binding_for_job(box, submitted["job_id"])
    expected = legacy_execution(binding)[2]
    cases.append((submitted, replace(expected, publisher_account_id=str(uuid4()))))

    from test_v4_cycle_admission import approved_goal

    wrong_stage_goal = approved_goal(f["bridge"], f["spec"])
    intelligence = f["bridge"].submit(
        LegacyIntelligenceCommand(
            goal_id=wrong_stage_goal,
            request=f["command"].request.model_copy(
                update={"idempotency_key": "wrong-stage-" + str(uuid4())}
            ),
            niche="Fixture",
        )
    )
    wrong_stage_request = PublicationWorkflowRequest(
        workspace_id=str(f["workspace_id"]),
        content_program_id=f["ready"]["program_id"],
        ready_package_id=f["ready"]["ready_package_id"],
        publisher_account_id=f["account"].id,
        publication_approval_request_id=f["approval_id"],
        budget_id=str(f["budget_id"]),
        idempotency_key="forged-stage-" + str(uuid4()),
    )
    cases.append((intelligence, wrong_stage_request))

    async def forbidden(*args, **kwargs):
        raise AssertionError("forged legacy publication must fail before native business reads")

    monkeypatch.setattr(PublicationRepository, "create_request", forbidden)
    monkeypatch.setattr(PublicationRepository, "load_scheduled_execution", forbidden)
    client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    async with build_legacy_worker(client, task_queue=f["queue"], outbox=box):
        for original, forged in cases:
            workflow_id = "salience-v4-legacy-operation:" + original["operation_id"]
            child = await client.start_workflow(
                GovernedPublicationWorkflow.run,
                forged,
                id=workflow_id,
                task_queue=f["queue"],
                execution_timeout=timedelta(seconds=25),
            )
            with pytest.raises(WorkflowFailureError):
                await asyncio.wait_for(child.result(), 15)
    with psycopg.connect(f["database"]) as connection:
        assert connection.execute(
            "SELECT count(*) FROM publication_requests WHERE workspace_id=%s",
            (f["workspace_id"],),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM external_effects WHERE job_id IN (%s,%s)",
            (cases[0][0]["job_id"], cases[1][0]["job_id"]),
        ).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_publication_revoked_between_native_activities_never_submits(
    legacy_publication, monkeypatch
):
    from temporalio import activity
    from temporalio.client import Client, WorkflowFailureError

    from salience.cycles.outbox import CycleOutbox
    from salience.cycles.legacy_publication_runtime import GuardedPublicationActivities
    from salience.cycles.legacy_runtime import build_legacy_worker
    from salience.cycles.runtime import TemporalCycleTransport

    f = legacy_publication
    reached, release = asyncio.Event(), asyncio.Event()
    original = GuardedPublicationActivities.delivery

    @activity.defn(name="salience.publication.delivery")
    async def blocked(self, payload):
        reached.set()
        await release.wait()
        return await original(self, payload)

    monkeypatch.setattr(GuardedPublicationActivities, "delivery", blocked)
    submitted = f["bridge"].submit_publication(f["command"])
    client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    box = CycleOutbox(f["database"], workspace_id=f["workspace_id"], delivery_lane="legacy")
    transport = TemporalCycleTransport(client, task_queue=f["queue"])
    async with build_legacy_worker(client, task_queue=f["queue"], outbox=box):
        try:
            assert await box.dispatch_one(transport)
            await asyncio.wait_for(reached.wait(), 10)
            with psycopg.connect(f["database"]) as connection:
                connection.execute(
                    "UPDATE permission_grants SET effect='deny' WHERE workspace_id=%s AND principal_id=%s AND scope='legacy:publication'",
                    (f["workspace_id"], str(f["actor_id"])),
                )
            release.set()
            child = client.get_workflow_handle(
                "salience-v4-legacy-operation:" + submitted["operation_id"]
            )
            with pytest.raises(WorkflowFailureError):
                await asyncio.wait_for(child.result(), 20)
        finally:
            release.set()
    with psycopg.connect(f["database"]) as connection:
        assert connection.execute(
            "SELECT count(*) FROM v4_legacy_publication_fixture_receipts WHERE operation_id=%s",
            (submitted["operation_id"],),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM jobs WHERE id=%s AND dry_run=false", (submitted["job_id"],)
        ).fetchone()[0] == 1


@pytest.mark.asyncio
async def test_publication_subprocess_crash_after_durable_acceptance_reconciles_original_run(
    legacy_publication, monkeypatch, tmp_path
):
    """A hard worker exit recovers only the accepted receipt, never a new outcome."""
    from temporalio import activity
    from temporalio.client import Client, WorkflowFailureError

    from salience.cycles.outbox import CycleOutbox
    from salience.cycles.legacy_publication_runtime import (
        DurableLegacyFixturePublisher,
        GuardedPublicationActivities,
    )
    from salience.cycles.legacy_runtime import build_legacy_worker
    from salience.cycles.runtime import TemporalCycleTransport

    f = legacy_publication
    bridge, database = f["bridge"], f["database"]
    submitted = bridge.submit_publication(f["command"])
    _allow(
        database,
        f["workspace_id"],
        f["actor_id"],
        ("legacy:publication:reconcile:account:" + f["account"].id,),
    )
    assert await _hard_exit_publication_worker(f, tmp_path, after_acceptance=True) == 73

    operation_id = submitted["operation_id"]
    workflow_id = "salience-v4-legacy-operation:" + operation_id
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT count(*) FROM v4_legacy_publication_fixture_receipts WHERE operation_id=%s",
            (operation_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT state FROM v4_legacy_publication_fixture_progress WHERE operation_id=%s",
            (operation_id,),
        ).fetchone()[0] == "accepted"
        assert connection.execute(
            """SELECT count(*) FROM remote_publication_receipts receipt
               JOIN publication_attempts attempt ON attempt.id=receipt.publication_attempt_id
               JOIN publication_plans plan ON plan.id=attempt.publication_plan_id
               JOIN publication_requests request ON request.id=plan.publication_request_id
               WHERE request.request_key=%s""",
            (workflow_id.replace("salience-v4-legacy-operation:", "v4-legacy:"),),
        ).fetchone()[0] == 0

    client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    handle = client.get_workflow_handle(workflow_id)
    original_description = await handle.describe()
    assert original_description.workflow_type == "GovernedPublicationWorkflow"
    initial_activity = await _wait_activity_started(
        client,
        workflow_id,
        "salience.publication.submit_or_reconcile",
        attempt=1,
        timeout=5,
    )
    first_worker_identity = initial_activity.last_worker_identity
    resubmit_calls = []
    reconcile_calls = []
    recovered = asyncio.Event()
    retry_entered = asyncio.Event()
    release_retry = asyncio.Event()
    retry_finished = asyncio.Event()
    original_submit = DurableLegacyFixturePublisher.submit
    original_reconcile = DurableLegacyFixturePublisher.reconcile
    original_activity = GuardedPublicationActivities.submit_or_reconcile

    async def counted_submit(self, request, lease):
        resubmit_calls.append(request.idempotency_key)
        return await original_submit(self, request, lease)

    async def counted_reconcile(self, idempotency_key):
        receipt = await original_reconcile(self, idempotency_key)
        reconcile_calls.append(receipt)
        recovered.set()
        return receipt

    @activity.defn(name="salience.publication.submit_or_reconcile")
    async def tracked_activity(self, payload):
        if activity.info().attempt >= 2:
            retry_entered.set()
            await release_retry.wait()
        try:
            return await original_activity(self, payload)
        finally:
            if activity.info().attempt >= 2:
                retry_finished.set()

    monkeypatch.setattr(DurableLegacyFixturePublisher, "submit", counted_submit)
    monkeypatch.setattr(DurableLegacyFixturePublisher, "reconcile", counted_reconcile)
    monkeypatch.setattr(GuardedPublicationActivities, "submit_or_reconcile", tracked_activity)
    outbox = CycleOutbox(database, workspace_id=f["workspace_id"], delivery_lane="legacy")
    worker = build_legacy_worker(client, task_queue=f["queue"], outbox=outbox)
    worker_task = asyncio.create_task(worker.run())
    try:
        await asyncio.wait_for(retry_entered.wait(), timeout=65)
        retry_activity = await _wait_activity_started(
            client,
            workflow_id,
            "salience.publication.submit_or_reconcile",
            attempt=2,
            timeout=5,
        )
        assert retry_activity.last_worker_identity
        assert retry_activity.last_worker_identity != first_worker_identity
        assert retry_activity.last_started_time.seconds > 0
        release_retry.set()
        await asyncio.wait_for(recovered.wait(), timeout=10)
        assert len(reconcile_calls) == 1
        assert reconcile_calls[0] is not None
        with pytest.raises(WorkflowFailureError) as failure:
            await asyncio.wait_for(handle.result(), timeout=20)
        assert any(
            getattr(error, "type", None) == "LegacyPublicationRecoveryHeld"
            for error in _exception_chain(failure.value)
        )
        await asyncio.wait_for(retry_finished.wait(), timeout=5)
    finally:
        release_retry.set()
        await asyncio.wait_for(worker.shutdown(), timeout=10)
        await asyncio.wait_for(worker_task, timeout=10)
    after = await handle.describe()
    assert after.run_id == original_description.run_id
    assert after.status.name == "FAILED"
    assert resubmit_calls == []
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT count(*) FROM v4_legacy_publication_fixture_receipts WHERE operation_id=%s",
            (operation_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            """SELECT count(*) FROM remote_publication_receipts receipt
               JOIN publication_attempts attempt ON attempt.id=receipt.publication_attempt_id
               JOIN publication_plans plan ON plan.id=attempt.publication_plan_id
               JOIN publication_requests request ON request.id=plan.publication_request_id
               WHERE request.request_key=%s""",
            ("v4-legacy:" + operation_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT state FROM v4_legacy_publication_fixture_progress WHERE operation_id=%s",
            (operation_id,),
        ).fetchone()[0] == "accepted"
        assert connection.execute(
            "SELECT state FROM jobs WHERE id=%s", (submitted["job_id"],)
        ).fetchone()[0] != "succeeded"
        assert connection.execute(
            "SELECT count(*) FROM publications publication JOIN publication_requests request ON request.id=publication.publication_request_id WHERE request.request_key=%s",
            ("v4-legacy:" + operation_id,),
        ).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_publication_subprocess_crash_without_receipt_never_retries_submit(
    legacy_publication, monkeypatch, tmp_path
):
    from temporalio import activity
    from temporalio.client import Client

    from salience.cycles.outbox import CycleOutbox
    from salience.cycles.legacy_publication_runtime import (
        DurableLegacyFixturePublisher,
        GuardedPublicationActivities,
    )
    from salience.cycles.legacy_runtime import build_legacy_worker
    from salience.cycles.runtime import TemporalCycleTransport

    f = legacy_publication
    submitted = f["bridge"].submit_publication(f["command"])
    _allow(
        f["database"],
        f["workspace_id"],
        f["actor_id"],
        ("legacy:publication:reconcile:account:" + f["account"].id,),
    )
    assert await _hard_exit_publication_worker(f, tmp_path, after_acceptance=False) == 72
    operation_id = submitted["operation_id"]
    workflow_id = "salience-v4-legacy-operation:" + operation_id
    with psycopg.connect(f["database"]) as connection:
        assert connection.execute(
            "SELECT count(*) FROM v4_legacy_publication_fixture_receipts WHERE operation_id=%s",
            (operation_id,),
        ).fetchone()[0] == 0

    client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    handle = client.get_workflow_handle(workflow_id)
    original = await handle.describe()
    initial_activity = await _wait_activity_started(
        client,
        workflow_id,
        "salience.publication.submit_or_reconcile",
        attempt=1,
        timeout=5,
    )
    first_worker_identity = initial_activity.last_worker_identity
    submit_calls, reconcile_calls = [], []
    retry_entered = asyncio.Event()
    release_retry = asyncio.Event()
    retry_finished = asyncio.Event()
    from salience.cycles.legacy_publication_runtime import GuardedPublicationActivities

    original_submit = DurableLegacyFixturePublisher.submit
    original_reconcile = DurableLegacyFixturePublisher.reconcile
    original_activity = GuardedPublicationActivities.submit_or_reconcile

    async def counted_submit(self, request, lease):
        submit_calls.append(request.idempotency_key)
        return await original_submit(self, request, lease)

    async def counted_reconcile(self, idempotency_key):
        receipt = await original_reconcile(self, idempotency_key)
        reconcile_calls.append(receipt)
        return receipt

    @activity.defn(name="salience.publication.submit_or_reconcile")
    async def tracked_activity(self, payload):
        if activity.info().attempt >= 2:
            retry_entered.set()
            await release_retry.wait()
        try:
            return await original_activity(self, payload)
        finally:
            if activity.info().attempt >= 2:
                retry_finished.set()

    monkeypatch.setattr(DurableLegacyFixturePublisher, "submit", counted_submit)
    monkeypatch.setattr(DurableLegacyFixturePublisher, "reconcile", counted_reconcile)
    monkeypatch.setattr(GuardedPublicationActivities, "submit_or_reconcile", tracked_activity)
    outbox = CycleOutbox(f["database"], workspace_id=f["workspace_id"], delivery_lane="legacy")
    worker = build_legacy_worker(client, task_queue=f["queue"], outbox=outbox)
    worker_task = asyncio.create_task(worker.run())
    try:
        await asyncio.wait_for(retry_entered.wait(), timeout=65)
        retry_activity = await _wait_activity_started(
            client,
            workflow_id,
            "salience.publication.submit_or_reconcile",
            attempt=2,
            timeout=5,
        )
        assert retry_activity.last_worker_identity
        assert retry_activity.last_worker_identity != first_worker_identity
        assert retry_activity.last_started_time.seconds > 0
        release_retry.set()
        await asyncio.wait_for(retry_finished.wait(), timeout=5)
        assert submit_calls == []
        assert reconcile_calls == []
    finally:
        release_retry.set()
        await asyncio.wait_for(worker.shutdown(), timeout=10)
        await asyncio.wait_for(worker_task, timeout=10)
    assert (await handle.describe()).run_id == original.run_id
    assert submit_calls == []
    with psycopg.connect(f["database"]) as connection:
        assert connection.execute(
            "SELECT count(*) FROM v4_legacy_publication_fixture_receipts WHERE operation_id=%s",
            (operation_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM remote_publication_receipts receipt JOIN publication_attempts attempt ON attempt.id=receipt.publication_attempt_id JOIN publication_plans plan ON plan.id=attempt.publication_plan_id JOIN publication_requests request ON request.id=plan.publication_request_id WHERE request.request_key=%s",
            ("v4-legacy:" + operation_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT state FROM jobs WHERE id=%s", (submitted["job_id"],)
        ).fetchone()[0] != "succeeded"


@pytest.mark.asyncio
async def test_publication_close_cancels_pinned_original_run_and_reads_back_terminal_state(
    legacy_publication, monkeypatch
):
    from temporalio import activity
    from temporalio.client import Client, WorkflowHandle, WorkflowExecutionStatus

    from salience.cycles.outbox import CycleOutbox
    from salience.cycles.legacy_publication_runtime import DurableLegacyFixturePublisher
    from salience.cycles.legacy_runtime import build_legacy_worker
    from salience.cycles.runtime import TemporalCycleTransport

    f = legacy_publication
    reached, release = asyncio.Event(), asyncio.Event()
    original_status = DurableLegacyFixturePublisher.status

    async def block_after_processing(self, remote_id):
        receipt = await original_status(self, remote_id)
        if receipt is not None and receipt.state == "processing":
            reached.set()
            await release.wait()
        return receipt

    monkeypatch.setattr(DurableLegacyFixturePublisher, "status", block_after_processing)
    bridge, database = f["bridge"], f["database"]
    _allow(
        database,
        f["workspace_id"],
        f["actor_id"],
        ("legacy:publication:reconcile:account:" + f["account"].id,),
    )
    submitted = bridge.submit_publication(f["command"])
    client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    box = CycleOutbox(database, workspace_id=f["workspace_id"], delivery_lane="legacy")
    transport = TemporalCycleTransport(client, task_queue=f["queue"])
    original_signal = WorkflowHandle.signal
    seen = []

    async def lost_ack(handle, *args, **kwargs):
        if handle.id != "salience-v4-legacy-operation:" + submitted["operation_id"]:
            return await original_signal(handle, *args, **kwargs)
        seen.append((handle.id, handle.run_id))
        accepted = await original_signal(handle, *args, **kwargs)
        release.set()
        async with asyncio.timeout(10):
            while (await handle.describe()).status == WorkflowExecutionStatus.RUNNING:
                await asyncio.sleep(0.05)
        raise TimeoutError("Original publication cancellation signal accepted; acknowledgment lost")

    monkeypatch.setattr(WorkflowHandle, "signal", lost_ack)
    async with build_legacy_worker(client, task_queue=f["queue"], outbox=box):
        try:
            assert await box.dispatch_one(transport)
            child = await _wait_started(
                client, "salience-v4-legacy-operation:" + submitted["operation_id"]
            )
            original_run_id = (await child.describe()).run_id
            await asyncio.wait_for(reached.wait(), 15)
            bridge.close(
                submitted["cycle_id"],
                disposition="cancelled",
                reason="Cancel the pinned synthetic publication run",
            )
            assert await box.dispatch_one(transport)
            result = await asyncio.wait_for(child.result(), 15)
            assert result["publication_state"] == "cancelled"
            assert (child.id, original_run_id) in seen
            assert (await child.describe()).run_id == original_run_id
            assert bridge.inspect(submitted["job_id"])["state"] == "cancelled"
        finally:
            release.set()
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT kind FROM v4_cycle_outbox WHERE cycle_id=%s ORDER BY sequence",
            (submitted["cycle_id"],),
        ).fetchall() == [("start",), ("close",)]
        assert connection.execute(
            "SELECT state FROM v4_legacy_publication_fixture_progress WHERE operation_id=%s",
            (submitted["operation_id"],),
        ).fetchone()[0] == "cancelled"
        assert connection.execute(
            """SELECT count(*) FROM publication_status_events event
               JOIN publication_attempts attempt ON attempt.id=event.publication_attempt_id
               JOIN publication_plans plan ON plan.id=attempt.publication_plan_id
               JOIN publication_requests request ON request.id=plan.publication_request_id
               WHERE request.request_key=%s AND event.source='cancellation' AND event.state='cancelled'""",
            ("v4-legacy:" + submitted["operation_id"],),
        ).fetchone()[0] == 1


@pytest.mark.asyncio
async def test_publication_cancel_race_does_not_fabricate_cancel_for_published_remote(
    legacy_publication, monkeypatch
):
    from temporalio.client import Client, WorkflowFailureError

    from salience.cycles.outbox import CycleOutbox
    from salience.cycles.legacy_publication_runtime import DurableLegacyFixturePublisher
    from salience.cycles.legacy_runtime import build_legacy_worker
    from salience.cycles.runtime import TemporalCycleTransport

    f = legacy_publication
    reached, release = asyncio.Event(), asyncio.Event()
    original_status = DurableLegacyFixturePublisher.status

    async def race_to_published(self, remote_id):
        receipt = await original_status(self, remote_id)
        if receipt is not None and receipt.state == "processing":
            reached.set()
            await release.wait()
        return receipt

    monkeypatch.setattr(DurableLegacyFixturePublisher, "status", race_to_published)
    _allow(
        f["database"],
        f["workspace_id"],
        f["actor_id"],
        ("legacy:publication:reconcile:account:" + f["account"].id,),
    )
    submitted = f["bridge"].submit_publication(f["command"])
    client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    box = CycleOutbox(f["database"], workspace_id=f["workspace_id"], delivery_lane="legacy")
    transport = TemporalCycleTransport(client, task_queue=f["queue"])
    async with build_legacy_worker(client, task_queue=f["queue"], outbox=box):
        try:
            assert await box.dispatch_one(transport)
            child = await _wait_started(
                client, "salience-v4-legacy-operation:" + submitted["operation_id"]
            )
            await asyncio.wait_for(reached.wait(), 15)
            with psycopg.connect(f["database"]) as connection:
                progress = connection.execute(
                    "UPDATE v4_legacy_publication_fixture_progress SET state='published',polls=2 WHERE operation_id=%s RETURNING state",
                    (submitted["operation_id"],),
                ).fetchone()[0]
                assert progress == "published"
            f["bridge"].close(
                submitted["cycle_id"],
                disposition="cancelled",
                reason="Remote publication completed while cancellation was pending",
            )
            assert await box.dispatch_one(transport)
            release.set()
            # Closing the cycle revokes the ordinary execution authority. If
            # its published readback races with that close, the workflow must
            # stay held for explicit recovery instead of fabricating either a
            # cancellation or a completed canonical publication.
            with pytest.raises(WorkflowFailureError):
                await asyncio.wait_for(child.result(), 20)
        finally:
            release.set()
    assert f["bridge"].inspect(submitted["job_id"])["state"] == "running"
    with psycopg.connect(f["database"]) as connection:
        assert connection.execute(
            """SELECT receipt.receipt->>'state',progress.state
               FROM v4_legacy_publication_fixture_receipts receipt
               JOIN v4_legacy_publication_fixture_progress progress USING(operation_id)
               WHERE receipt.operation_id=%s""",
            (submitted["operation_id"],),
        ).fetchone() == ("accepted", "published")
        assert connection.execute(
            """SELECT count(*) FROM publication_status_events event
               JOIN publication_attempts attempt ON attempt.id=event.publication_attempt_id
               JOIN publication_plans plan ON plan.id=attempt.publication_plan_id
               JOIN publication_requests request ON request.id=plan.publication_request_id
               WHERE request.request_key=%s AND event.source='cancellation'""",
            ("v4-legacy:" + submitted["operation_id"],),
        ).fetchone()[0] == 0
        assert connection.execute(
            """SELECT count(*) FROM publication_status_events event
               JOIN publication_attempts attempt ON attempt.id=event.publication_attempt_id
               JOIN publication_plans plan ON plan.id=attempt.publication_plan_id
               JOIN publication_requests request ON request.id=plan.publication_request_id
               WHERE request.request_key=%s AND event.state='published'""",
            ("v4-legacy:" + submitted["operation_id"],),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM publications publication JOIN publication_requests request ON request.id=publication.publication_request_id WHERE request.request_key=%s",
            ("v4-legacy:" + submitted["operation_id"],),
        ).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_publication_revoked_reconcile_scope_holds_durable_acceptance(
    legacy_publication, monkeypatch
):
    from temporalio import activity
    from temporalio.client import Client, WorkflowFailureError

    from salience.cycles.outbox import CycleOutbox
    from salience.cycles.legacy_publication_runtime import DurableLegacyFixturePublisher
    from salience.cycles.legacy_runtime import build_legacy_worker
    from salience.cycles.runtime import TemporalCycleTransport

    f = legacy_publication
    scope = "legacy:publication:reconcile:account:" + f["account"].id
    _allow(f["database"], f["workspace_id"], f["actor_id"], (scope,))
    reached, release = asyncio.Event(), asyncio.Event()
    from salience.cycles.legacy_publication_runtime import GuardedPublicationActivities

    @activity.defn(name="salience.publication.await")
    async def gated_status(self, payload):
        reached.set()
        await release.wait()
        return await original_await(self, payload)

    original_await = GuardedPublicationActivities.await_publication
    monkeypatch.setattr(GuardedPublicationActivities, "await_publication", gated_status)
    submitted = f["bridge"].submit_publication(f["command"])
    client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    box = CycleOutbox(f["database"], workspace_id=f["workspace_id"], delivery_lane="legacy")
    transport = TemporalCycleTransport(client, task_queue=f["queue"])
    async with build_legacy_worker(client, task_queue=f["queue"], outbox=box):
        try:
            assert await box.dispatch_one(transport)
            child = await _wait_started(
                client, "salience-v4-legacy-operation:" + submitted["operation_id"]
            )
            await asyncio.wait_for(reached.wait(), 15)
            with psycopg.connect(f["database"]) as connection:
                assert connection.execute(
                    "SELECT count(*) FROM v4_legacy_publication_fixture_receipts WHERE operation_id=%s",
                    (submitted["operation_id"],),
                ).fetchone()[0] == 1
                connection.execute(
                    "UPDATE permission_grants SET effect='deny' WHERE workspace_id=%s AND principal_id=%s AND scope=%s",
                    (f["workspace_id"], str(f["actor_id"]), scope),
                )
            release.set()
            with pytest.raises(WorkflowFailureError):
                await asyncio.wait_for(child.result(), 20)
        finally:
            release.set()
    with psycopg.connect(f["database"]) as connection:
        assert connection.execute(
            "SELECT state FROM v4_legacy_publication_fixture_progress WHERE operation_id=%s",
            (submitted["operation_id"],),
        ).fetchone()[0] == "accepted"
        assert connection.execute(
            "SELECT count(*) FROM v4_legacy_publication_fixture_receipts WHERE operation_id=%s",
            (submitted["operation_id"],),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT state FROM jobs WHERE id=%s", (submitted["job_id"],)
        ).fetchone()[0] != "succeeded"
        assert connection.execute(
            "SELECT count(*) FROM publications publication JOIN publication_requests request ON request.id=publication.publication_request_id WHERE request.request_key=%s",
            ("v4-legacy:" + submitted["operation_id"],),
        ).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_signed_publication_routes_sdk_cli_and_safe_default_denial(
    legacy_publication, monkeypatch, capsys
):
    import httpx
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    from fastapi.testclient import TestClient

    from salience.api.p0 import create_p0_app
    from salience.cli import main
    from salience.sdk.client import SalienceClient

    f = legacy_publication
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    options = {
        "database_url": f["database"],
        "workspace_id": f["workspace_id"],
        "issuer": "https://fixture.invalid",
        "audience": "fixture",
        "public_key": key.public_key(),
    }
    app = create_p0_app(
        **options,
        enable_legacy_dispatch=True,
        legacy_fixture_queue=f["queue"],
        legacy_temporal_target=os.environ["TEST_TEMPORAL_TARGET"],
    )
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "iss": "https://fixture.invalid",
            "aud": "fixture",
            "sub": str(f["actor_id"]),
            "iat": now,
            "nbf": now,
            "exp": now + timedelta(minutes=5),
        },
        key,
        algorithm="RS256",
    )
    command = f["command"].model_dump(mode="json")
    monkeypatch.setenv("SALIENCE_CONTROL_JWT", token)
    monkeypatch.setenv("SALIENCE_CONTROL_URL", "http://testserver")

    with TestClient(app) as client:
        monkeypatch.setattr(
            httpx,
            "request",
            lambda method, url, **kwargs: client.request(
                method, url, **{key: value for key, value in kwargs.items() if key != "timeout"}
            ),
        )
        sdk = SalienceClient("http://testserver", token).legacy_publication
        bound = sdk.bind_account(
            str(f["goal_id"]),
            expected_revision=1,
            publisher_account_id=f["account"].id,
        )
        assert bound["state"] == "bound"

        first = sdk.submit(command)
        main(["legacy-publication", "submit", "--command-json", json.dumps(command)])
        assert json.loads(capsys.readouterr().out) == first
        assert first["dry_run"] is False and first["effects_enabled"] is False
        inspected = sdk.inspect(first["job_id"])
        assert inspected["job_id"] == first["job_id"]
        main(["legacy-publication", "inspect", first["job_id"]])
        assert json.loads(capsys.readouterr().out) == inspected
        cli_cancelled = client.post(
            f"/v1/publications/runs/{first['job_id']}/cancel",
            headers={"Authorization": "Bearer " + token},
            json={"reason": "Signed original publication cancellation"},
        )
        assert cli_cancelled.status_code == 200
        main(
            [
                "legacy-publication",
                "cancel",
                first["job_id"],
                "--reason",
                "Signed original publication cancellation",
            ]
        )
        assert json.loads(capsys.readouterr().out) == cli_cancelled.json()
        assert sdk.cancel(
            first["job_id"], reason="Signed original publication cancellation"
        ) == cli_cancelled.json()

        assert client.post(
            "/v1/publications/requests",
            headers={"Authorization": "Bearer " + token},
            json=command | {"dry_run": True},
        ).status_code == 422
        with psycopg.connect(f["database"]) as connection:
            connection.execute(
                "UPDATE permission_grants SET effect='deny' WHERE workspace_id=%s AND principal_id=%s AND scope='legacy:publication'",
                (f["workspace_id"], str(f["actor_id"])),
            )
        assert client.post(
            "/v1/publications/requests",
            headers={"Authorization": "Bearer " + token},
            json=command | {"request": command["request"] | {"idempotency_key": "revoked-legacy-publication"}},
        ).status_code == 403

    disabled = create_p0_app(**options)
    with TestClient(disabled) as default_client:
        rejected = default_client.post(
            "/v1/publications/requests",
            headers={"Authorization": "Bearer " + token},
            json=command,
        )
        assert rejected.status_code >= 400
    with psycopg.connect(f["database"]) as connection:
        assert connection.execute(
            "SELECT count(*) FROM v4_legacy_commands WHERE actor_id=%s", (f["actor_id"],)
        ).fetchone()[0] == 1



def test_m0040_empty_downgrade_restores_exact_m0039_legacy_dispatch_guard():
    source = os.environ["TEST_DATABASE_URL"]
    database_name = "publication_m0040_empty_" + uuid4().hex[:16]
    parts = urlsplit(source)
    database = urlunsplit(parts._replace(path="/" + database_name))
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
    with psycopg.connect(source, autocommit=True) as admin:
        admin.execute(
            psycopg.sql.SQL("CREATE DATABASE {}").format(
                psycopg.sql.Identifier(database_name)
            )
        )
        try:
            def migrate(direction, revision):
                result = subprocess.run(
                    [sys.executable, "-m", "alembic", "-x", "database_url=" + database, direction, revision],
                    cwd=root,
                    env=os.environ.copy(),
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                assert result.returncode == 0, result.stderr

            def guard():
                with psycopg.connect(database) as connection:
                    return connection.execute(
                        "SELECT pg_get_functiondef('v4_guard_legacy_dispatch()'::regprocedure)"
                    ).fetchone()[0]

            migrate("upgrade", "0039_legacy_creative_stage")
            original_guard = guard()
            migrate("upgrade", "0040_legacy_publication_stage")
            assert guard() != original_guard
            with psycopg.connect(database) as connection:
                assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0040_legacy_publication_stage"
            migrate("downgrade", "0039_legacy_creative_stage")
            assert guard() == original_guard
            with psycopg.connect(database) as connection:
                assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0039_legacy_creative_stage"
                assert connection.execute("SELECT to_regclass('public.v4_legacy_publication_accounts')").fetchone()[0] is None
                stage_guard = connection.execute(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='v4_legacy_dispatches'::regclass AND conname='v4_legacy_dispatches_stage_check'"
                ).fetchone()[0]
                assert "publication" not in stage_guard
        finally:
            admin.execute(
                psycopg.sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    psycopg.sql.Identifier(database_name)
                )
            )


@pytest.mark.asyncio
async def test_m0040_populated_downgrade_refuses_and_preserves_publication_receipt(
    monkeypatch, tmp_path
):
    source = os.environ["TEST_DATABASE_URL"]
    temporal = os.environ["TEST_TEMPORAL_TARGET"]
    database_name = "publication_m0040_populated_" + uuid4().hex[:16]
    parts = urlsplit(source)
    database = urlunsplit(parts._replace(path="/" + database_name))
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
    with psycopg.connect(source, autocommit=True) as admin:
        admin.execute(
            psycopg.sql.SQL("CREATE DATABASE {}").format(
                psycopg.sql.Identifier(database_name)
            )
        )
        try:
            def migrate(direction, revision):
                return subprocess.run(
                    [sys.executable, "-m", "alembic", "-x", "database_url=" + database, direction, revision],
                    cwd=root,
                    env=os.environ.copy(),
                    capture_output=True,
                    text=True,
                    timeout=60,
                )

            upgraded = migrate("upgrade", "0040_legacy_publication_stage")
            assert upgraded.returncode == 0, upgraded.stderr
            monkeypatch.setenv("TEST_DATABASE_URL", database)
            monkeypatch.setenv("TEST_TEMPORAL_TARGET", temporal)
            monkeypatch.setenv("SALIENCE_DEPLOYMENT_MODE", "fixture")
            monkeypatch.setenv("SALIENCE_EFFECTS_ENABLED", "false")
            fixture = await _publication_fixture(database)
            submitted = fixture["bridge"].submit_publication(fixture["command"])
            assert await _hard_exit_publication_worker(
                fixture, tmp_path, after_acceptance=True
            ) == 73
            with psycopg.connect(database) as connection:
                assert connection.execute(
                    "SELECT count(*) FROM v4_legacy_publication_fixture_receipts WHERE operation_id=%s",
                    (submitted["operation_id"],),
                ).fetchone()[0] == 1
                dispatch = connection.execute(
                    "SELECT to_jsonb(binding) FROM v4_legacy_dispatches binding WHERE operation_id=%s",
                    (submitted["operation_id"],),
                ).fetchone()[0]
                mapping = connection.execute(
                    "SELECT to_jsonb(account) FROM v4_legacy_publication_accounts account WHERE goal_id=%s",
                    (fixture["goal_id"],),
                ).fetchone()[0]
                receipt = connection.execute(
                    "SELECT to_jsonb(receipt) FROM v4_legacy_publication_fixture_receipts receipt WHERE operation_id=%s",
                    (submitted["operation_id"],),
                ).fetchone()[0]
                job = connection.execute(
                    "SELECT to_jsonb(job) FROM jobs job WHERE id=%s", (submitted["job_id"],)
                ).fetchone()[0]
                guard = connection.execute(
                    "SELECT pg_get_functiondef('v4_guard_legacy_dispatch()'::regprocedure)"
                ).fetchone()[0]
            refused = migrate("downgrade", "0039_legacy_creative_stage")
            assert refused.returncode != 0
            assert "preserve original legacy publication history" in refused.stderr
            with psycopg.connect(database) as connection:
                assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0040_legacy_publication_stage"
                assert connection.execute(
                    "SELECT to_jsonb(binding) FROM v4_legacy_dispatches binding WHERE operation_id=%s",
                    (submitted["operation_id"],),
                ).fetchone()[0] == dispatch
                assert connection.execute(
                    "SELECT to_jsonb(account) FROM v4_legacy_publication_accounts account WHERE goal_id=%s",
                    (fixture["goal_id"],),
                ).fetchone()[0] == mapping
                assert connection.execute(
                    "SELECT to_jsonb(receipt) FROM v4_legacy_publication_fixture_receipts receipt WHERE operation_id=%s",
                    (submitted["operation_id"],),
                ).fetchone()[0] == receipt
                assert connection.execute(
                    "SELECT to_jsonb(job) FROM jobs job WHERE id=%s", (submitted["job_id"],)
                ).fetchone()[0] == job
                assert connection.execute(
                    "SELECT pg_get_functiondef('v4_guard_legacy_dispatch()'::regprocedure)"
                ).fetchone()[0] == guard
        finally:
            admin.execute(
                psycopg.sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    psycopg.sql.Identifier(database_name)
                )
            )
