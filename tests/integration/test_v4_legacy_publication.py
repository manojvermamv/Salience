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


@pytest_asyncio.fixture
async def legacy_publication_current_schema(monkeypatch):
    from test_v4_publication_recovery_holds import _create_database, _drop_database, _migrate

    source = os.environ["TEST_DATABASE_URL"]
    name, database = _create_database(source, "legacy_publication_current_")
    monkeypatch.setenv("TEST_DATABASE_URL", database)
    monkeypatch.setenv("SALIENCE_DEPLOYMENT_MODE", "fixture")
    monkeypatch.setenv("SALIENCE_EFFECTS_ENABLED", "false")
    try:
        upgraded = _migrate(database, "upgrade", "head")
        assert upgraded.returncode == 0, upgraded.stderr
        yield await _publication_fixture(database)
    finally:
        _drop_database(source, name)


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


def _recovery_projection_counts(database, operation_id):
    with psycopg.connect(database) as connection:
        job_id, attempt_id = connection.execute(
            """SELECT binding.job_id,publication_attempt_row.id
               FROM v4_legacy_dispatches binding
               JOIN v4_legacy_publication_fixture_receipts receipt
                 ON receipt.operation_id=binding.operation_id
               JOIN publication_requests request
                 ON request.id=(receipt.request->>'id')::uuid
               JOIN publication_plans plan ON plan.publication_request_id=request.id
               JOIN publication_attempts publication_attempt_row
                 ON publication_attempt_row.publication_plan_id=plan.id
               WHERE binding.operation_id=%s""",
            (operation_id,),
        ).fetchone()
        remote_receipts = connection.execute(
            "SELECT count(*) FROM remote_publication_receipts WHERE publication_attempt_id=%s",
            (attempt_id,),
        ).fetchone()[0]
        status_events = connection.execute(
            "SELECT count(*) FROM publication_status_events WHERE publication_attempt_id=%s",
            (attempt_id,),
        ).fetchone()[0]
        checkpoints = connection.execute(
            """SELECT count(*),
                      count(*) FILTER (WHERE checkpoint_payload->>'checkpoint'='publication.accepted'),
                      count(*) FILTER (WHERE checkpoint_payload->>'checkpoint'='publication.receipt_recovered_requires_typed_recovery')
               FROM job_checkpoints WHERE job_id=%s""",
            (job_id,),
        ).fetchone()
        audit_events = connection.execute(
            "SELECT count(*) FROM audit_events WHERE job_id=%s", (job_id,)
        ).fetchone()[0]
        provenance_records = connection.execute(
            "SELECT count(*) FROM provenance_records WHERE job_id=%s", (job_id,)
        ).fetchone()[0]
    return {
        "remote_receipts": remote_receipts,
        "status_events": status_events,
        "checkpoints": checkpoints[0],
        "accepted_checkpoints": checkpoints[1],
        "recovery_checkpoints": checkpoints[2],
        "audit_events": audit_events,
        "provenance_records": provenance_records,
    }


async def _hard_exit_publication_worker(
    fixture, tmp_path, *, after_acceptance, permit_ttl_seconds=None,
    after_hold_commit=False, precommit_snapshot_path=None,
    expire_after_first_submit=False, activity_log_path=None,
    activity_timing_path=None, operation_id=None, diagnostic_path=None,
    timeout_seconds=25, cancel_after_processing=False,
    cancel_ready_callback=None,
):
    worker_script = tmp_path / ("publication_worker_" + str(uuid4()) + ".py")
    worker_script.write_text(
        """import asyncio, json, os
from datetime import datetime, timezone
from pathlib import Path
import psycopg
from temporalio import activity
from temporalio.client import Client
from salience.cycles.outbox import CycleOutbox
from salience.cycles.legacy_publication_runtime import DurableLegacyFixturePublisher, GuardedPublicationActivities
from salience.cycles.legacy_runtime import build_legacy_worker
from salience.cycles.runtime import TemporalCycleTransport

def diagnostic(event, **fields):
    path = os.environ.get('PUB_DIAGNOSTIC_LOG')
    if path:
        with open(path, 'a', encoding='utf-8') as log:
            log.write(json.dumps({'event': event, **fields}) + '\\n')
            log.flush()

diagnostic('worker_imported')

if 'PUB_PERMIT_TTL_SECONDS' in os.environ:
    from salience.cycles.governance import CycleGovernance
    original_issue_permit = CycleGovernance.issue_permit
    def issue_short_lived_permit(self, cycle_id, request, *, idempotency_key):
        request = request.model_copy(update={'ttl_seconds': int(os.environ['PUB_PERMIT_TTL_SECONDS'])})
        return original_issue_permit(self, cycle_id, request, idempotency_key=idempotency_key)
    CycleGovernance.issue_permit = issue_short_lived_permit

if os.environ.get('PUB_CRASH_AFTER_HOLD_COMMIT') == 'true':
    import salience.cycles.publication_recovery as recovery
    original_record_hold = recovery.record_publication_recovery_hold
    def crash_after_hold_commit(*args, **kwargs):
        diagnostic('hold_record_started', reason=kwargs.get('reason'))
        snapshot_path = os.environ.get('PUB_HOLD_PRECOMMIT_SNAPSHOT')
        if snapshot_path:
            from test_v4_publication_recovery_holds import _SNAPSHOT_SQL
            with psycopg.connect(os.environ['TEST_DATABASE_URL']) as connection:
                snapshot = connection.execute(
                    _SNAPSHOT_SQL, (kwargs['operation_id'],)
                ).fetchone()[0]
            Path(snapshot_path).write_text(
                json.dumps(snapshot, default=str), encoding='utf-8'
            )
            diagnostic('hold_precommit_snapshot_captured')
        try:
            original_record_hold(*args, **kwargs)
        except BaseException as error:
            diagnostic(
                'hold_record_failed',
                error=type(error).__name__,
                message=str(error),
            )
            raise
        os._exit(74)
    recovery.record_publication_recovery_hold = crash_after_hold_commit

original_cancel = DurableLegacyFixturePublisher.cancel
async def track_cancel(self, remote_id):
    diagnostic('cancel_started', remote_id=remote_id)
    receipt = await original_cancel(self, remote_id)
    diagnostic('cancel_returned', state=receipt.state if receipt is not None else None)
    return receipt
DurableLegacyFixturePublisher.cancel = track_cancel

if os.environ.get('PUB_CANCEL_AFTER_PROCESSING') == 'true':
    original_status = DurableLegacyFixturePublisher.status
    async def pause_after_processing(self, remote_id):
        receipt = await original_status(self, remote_id)
        if receipt is not None and receipt.state == 'processing':
            diagnostic('processing_read_ready')
            with open(os.environ['PUB_CANCEL_READY'], 'w', encoding='utf-8') as ready:
                ready.write('ready')
        while receipt is not None and receipt.state == 'processing' and not os.path.exists(os.environ['PUB_CANCEL_RELEASE']):
            await asyncio.sleep(.02)
        return receipt
    DurableLegacyFixturePublisher.status = pause_after_processing

if os.environ.get('PUB_ACTIVITY_LOG'):
    original_activity = GuardedPublicationActivities.submit_or_reconcile
    def log_activity_event(event, attempt, **fields):
        with open(os.environ['PUB_ACTIVITY_LOG'], 'a', encoding='utf-8') as log:
            log.write(json.dumps({'event': event, 'attempt': attempt, **fields}) + '\\n')
            log.flush()
    @activity.defn(name='salience.publication.submit_or_reconcile')
    async def track_publication_attempt(self, payload):
        info = activity.info()
        started_at = datetime.now(timezone.utc).isoformat()
        log_activity_event('started', info.attempt, at=started_at)
        if (
            os.environ.get('PUB_HOLD_PRECOMMIT_SNAPSHOT')
            and info.attempt == int(os.environ.get('PUB_CAPTURE_ATTEMPT_TIMING', '0'))
        ):
            from test_v4_publication_recovery_holds import _SNAPSHOT_SQL
            with psycopg.connect(os.environ['TEST_DATABASE_URL']) as connection:
                snapshot = connection.execute(
                    _SNAPSHOT_SQL, (os.environ['PUB_OPERATION_ID'],)
                ).fetchone()[0]
            Path(os.environ['PUB_HOLD_PRECOMMIT_SNAPSHOT']).write_text(
                json.dumps(snapshot, default=str), encoding='utf-8'
            )
        if info.attempt == int(os.environ.get('PUB_CAPTURE_ATTEMPT_TIMING', '0')):
            with psycopg.connect(os.environ['TEST_DATABASE_URL']) as connection:
                timing = connection.execute(
                    '''SELECT clock_timestamp(),
                              context.payload->>'execution_deadline',
                              (context.payload->>'execution_deadline')::timestamptz > clock_timestamp(),
                              permit.expires_at,
                              permit.expires_at <= clock_timestamp(),
                              COALESCE((SELECT jsonb_agg(runtime_hold.reason ORDER BY runtime_hold.created_at)
                                        FROM v4_runtime_holds runtime_hold WHERE runtime_hold.cycle_id=binding.cycle_id),
                                       '[]'::jsonb)
                       FROM v4_legacy_dispatches binding
                       JOIN v4_run_contexts context ON context.id=binding.context_id
                       JOIN v4_permit_claims claim ON claim.cycle_id=binding.cycle_id
                           AND claim.operation_id=binding.operation_id
                       JOIN v4_dispatch_permits permit ON permit.id=claim.current_permit_id
                       WHERE binding.operation_id=%s''',
                    (os.environ['PUB_OPERATION_ID'],),
                ).fetchone()
            Path(os.environ['PUB_ACTIVITY_TIMING']).write_text(
                json.dumps({
                    'attempt': info.attempt,
                    'started_at': started_at,
                    'database_clock': timing[0].isoformat(),
                    'execution_deadline': timing[1],
                    'deadline_is_future': timing[2],
                    'permit_expires_at': timing[3].isoformat(),
                    'permit_is_expired': timing[4],
                    'runtime_hold_reasons': timing[5],
                }),
                encoding='utf-8',
            )
        try:
            result = await original_activity(self, payload)
        except BaseException as error:
            log_activity_event('failed', info.attempt, at=datetime.now(timezone.utc).isoformat(), error=type(error).__name__)
            raise
        log_activity_event('completed', info.attempt, at=datetime.now(timezone.utc).isoformat())
        return result
    GuardedPublicationActivities.submit_or_reconcile = track_publication_attempt
    original_reconcile = DurableLegacyFixturePublisher.reconcile
    async def track_publication_reconcile(self, idempotency_key):
        log_activity_event('reconcile', activity.info().attempt)
        return await original_reconcile(self, idempotency_key)
    DurableLegacyFixturePublisher.reconcile = track_publication_reconcile

async def main():
    client = await Client.connect(os.environ['TEST_TEMPORAL_TARGET'])
    box = CycleOutbox(os.environ['TEST_DATABASE_URL'], workspace_id=os.environ['PUB_WORKSPACE'], delivery_lane='legacy')
    queue = os.environ['PUB_QUEUE']
    with psycopg.connect(os.environ['TEST_DATABASE_URL']) as connection:
        outbox_before = connection.execute(
            '''SELECT kind,sequence,state,attempts,last_error
               FROM v4_cycle_outbox WHERE workspace_id=%s ORDER BY sequence''',
            (os.environ['PUB_WORKSPACE'],),
        ).fetchall()
    diagnostic('outbox_before_dispatch', rows=[list(row) for row in outbox_before])
    original = DurableLegacyFixturePublisher.submit
    async def crash_submit(self, request, lease):
        diagnostic('submit_started')
        if os.environ['PUB_CRASH_MODE'] == 'before':
            os._exit(72)
        receipt = await original(self, request, lease)
        diagnostic('submit_returned', state=receipt.state)
        if os.environ.get('PUB_EXPIRE_AFTER_FIRST_SUBMIT') == 'true':
            operation_id = os.environ['PUB_OPERATION_ID']
            with psycopg.connect(os.environ['TEST_DATABASE_URL']) as connection:
                expires_at = connection.execute(
                    '''SELECT permit.expires_at FROM v4_dispatch_permits permit
                       JOIN v4_permit_claims claim ON claim.current_permit_id=permit.id
                       WHERE claim.operation_id=%s''',
                    (operation_id,),
                ).fetchone()[0]
            remaining = (expires_at - datetime.now(timezone.utc)).total_seconds()
            if remaining > 0:
                await asyncio.sleep(remaining + 0.1)
            raise RuntimeError('simulated lost result after committed accepted receipt')
        if os.environ['PUB_CRASH_MODE'] == 'after':
            os._exit(73)
        return receipt
    DurableLegacyFixturePublisher.submit = crash_submit
    async with build_legacy_worker(client, task_queue=queue, outbox=box):
        delivered = await box.dispatch_one(TemporalCycleTransport(client, task_queue=queue))
        diagnostic('outbox_dispatch_returned', delivered=bool(delivered))
        if os.environ.get('PUB_OPERATION_ID'):
            try:
                description = await client.get_workflow_handle(
                    'salience-v4-legacy-operation:' + os.environ['PUB_OPERATION_ID']
                ).describe()
                diagnostic('workflow_after_dispatch', status=str(description.status))
            except Exception as error:
                diagnostic('workflow_describe_failed', error=type(error).__name__)
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
        "PUB_CRASH_AFTER_HOLD_COMMIT": "true" if after_hold_commit else "false",
        "PUB_EXPIRE_AFTER_FIRST_SUBMIT": "true" if expire_after_first_submit else "false",
        "PUB_CANCEL_AFTER_PROCESSING": "true" if cancel_after_processing else "false",
    }
    if cancel_after_processing:
        child_environment["PUB_CRASH_MODE"] = "none"
    if permit_ttl_seconds is not None:
        child_environment["PUB_PERMIT_TTL_SECONDS"] = str(permit_ttl_seconds)
    if precommit_snapshot_path is not None:
        child_environment["PUB_HOLD_PRECOMMIT_SNAPSHOT"] = str(precommit_snapshot_path)
    if activity_log_path is not None:
        child_environment["PUB_ACTIVITY_LOG"] = str(activity_log_path)
    if activity_timing_path is not None:
        child_environment["PUB_ACTIVITY_TIMING"] = str(activity_timing_path)
        child_environment["PUB_CAPTURE_ATTEMPT_TIMING"] = "2"
    if operation_id is not None:
        child_environment["PUB_OPERATION_ID"] = str(operation_id)
    if diagnostic_path is not None:
        child_environment["PUB_DIAGNOSTIC_LOG"] = str(diagnostic_path)
    cancel_ready_path = tmp_path / ("publication_cancel_ready_" + str(uuid4()) + ".txt")
    cancel_release_path = tmp_path / ("publication_cancel_release_" + str(uuid4()) + ".txt")
    if cancel_after_processing:
        child_environment["PUB_CANCEL_READY"] = str(cancel_ready_path)
        child_environment["PUB_CANCEL_RELEASE"] = str(cancel_release_path)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(worker_script),
        env=child_environment,
        stdout=subprocess.PIPE if diagnostic_path is not None else subprocess.DEVNULL,
        stderr=subprocess.PIPE if diagnostic_path is not None else subprocess.DEVNULL,
    )
    if cancel_after_processing:
        try:
            async with asyncio.timeout(20):
                while not cancel_ready_path.exists():
                    if process.returncode is not None:
                        raise RuntimeError(
                            f"publication worker exited before processing read: {process.returncode}"
                        )
                    await asyncio.sleep(.05)
            if cancel_ready_callback is not None:
                await cancel_ready_callback()
            cancel_release_path.write_text("release", encoding="utf-8")
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.communicate()
            raise
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=timeout_seconds
        )
    except TimeoutError:
        process.kill()
        stdout, stderr = await process.communicate()
        code = process.returncode
        if diagnostic_path is not None:
            with open(diagnostic_path, 'a', encoding='utf-8') as log:
                log.write(json.dumps({
                    'event': 'worker_timeout',
                    'stdout': stdout.decode(errors='replace'),
                    'stderr': stderr.decode(errors='replace'),
                }) + '\\n')
        if after_hold_commit:
            return code
        pytest.fail("publication worker did not exit at the requested receipt boundary")
    return process.returncode


async def _wait_for_publication_permit_expiry(database, operation_id):
    with psycopg.connect(database) as connection:
        expires_at = connection.execute(
            """SELECT permit.expires_at FROM v4_dispatch_permits permit
               JOIN v4_permit_claims claim ON claim.current_permit_id=permit.id
               WHERE claim.operation_id=%s""",
            (operation_id,),
        ).fetchone()[0]
    remaining = (expires_at - datetime.now(timezone.utc)).total_seconds()
    if remaining > 0:
        await asyncio.sleep(remaining + 0.05)
    with psycopg.connect(database) as connection:
        assert connection.execute(
            """SELECT permit.expires_at<=clock_timestamp()
               FROM v4_dispatch_permits permit
               JOIN v4_permit_claims claim ON claim.current_permit_id=permit.id
               WHERE claim.operation_id=%s""",
            (operation_id,),
        ).fetchone()[0]


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
async def test_expired_receipt_hold_requires_current_reconcile_scope_and_never_resubmits(
    legacy_publication_current_schema, monkeypatch, tmp_path
):
    """Expired-permit recovery records a hold without a canonical projection."""
    from temporalio import activity
    from temporalio.client import Client, WorkflowFailureError

    from salience.cycles.outbox import CycleOutbox
    from salience.cycles.legacy_publication_runtime import (
        DurableLegacyFixturePublisher,
        GuardedPublicationActivities,
    )
    from salience.cycles.legacy_runtime import build_legacy_worker
    from salience.cycles.runtime import TemporalCycleTransport
    from test_v4_publication_recovery_holds import _SNAPSHOT_SQL

    f = legacy_publication_current_schema
    bridge, database = f["bridge"], f["database"]
    submitted = bridge.submit_publication(f["command"])
    _allow(
        database,
        f["workspace_id"],
        f["actor_id"],
        ("legacy:publication:reconcile:account:" + f["account"].id,),
    )
    assert (
        await _hard_exit_publication_worker(
            f, tmp_path, after_acceptance=True, permit_ttl_seconds=10
        )
        == 73
    )

    operation_id = submitted["operation_id"]
    workflow_id = "salience-v4-legacy-operation:" + operation_id
    await _wait_for_publication_permit_expiry(database, operation_id)
    with psycopg.connect(database) as connection:
        connection.execute(
            "UPDATE publisher_accounts SET status='suspended' WHERE id=%s",
            (f["account"].id,),
        )
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
    status_calls = []
    authorization_rejected = asyncio.Event()
    valid_retry_entered = asyncio.Event()
    release_retry = asyncio.Event()
    retry_finished = asyncio.Event()
    original_submit = DurableLegacyFixturePublisher.submit
    original_reconcile = DurableLegacyFixturePublisher.reconcile
    original_status = DurableLegacyFixturePublisher.status
    original_activity = GuardedPublicationActivities.submit_or_reconcile

    async def counted_submit(self, request, lease):
        resubmit_calls.append(request.idempotency_key)
        return await original_submit(self, request, lease)

    async def counted_reconcile(self, idempotency_key):
        receipt = await original_reconcile(self, idempotency_key)
        reconcile_calls.append(receipt)
        return receipt

    async def counted_status(self, remote_id):
        status_calls.append(remote_id)
        return await original_status(self, remote_id)

    @activity.defn(name="salience.publication.submit_or_reconcile")
    async def tracked_activity(self, payload):
        attempt = activity.info().attempt
        if attempt >= 3:
            valid_retry_entered.set()
            await release_retry.wait()
        try:
            return await original_activity(self, payload)
        except PermissionError:
            if attempt == 2:
                authorization_rejected.set()
            raise
        finally:
            if attempt >= 3:
                retry_finished.set()

    monkeypatch.setattr(DurableLegacyFixturePublisher, "submit", counted_submit)
    monkeypatch.setattr(DurableLegacyFixturePublisher, "reconcile", counted_reconcile)
    monkeypatch.setattr(DurableLegacyFixturePublisher, "status", counted_status)
    monkeypatch.setattr(GuardedPublicationActivities, "submit_or_reconcile", tracked_activity)
    outbox = CycleOutbox(database, workspace_id=f["workspace_id"], delivery_lane="legacy")
    worker = build_legacy_worker(client, task_queue=f["queue"], outbox=outbox)
    worker_task = asyncio.create_task(worker.run())
    try:
        await asyncio.wait_for(valid_retry_entered.wait(), timeout=65)
        retry_activity = await _wait_activity_started(
            client,
            workflow_id,
            "salience.publication.submit_or_reconcile",
            attempt=3,
            timeout=5,
        )
        assert authorization_rejected.is_set()
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT count(*) FROM v4_legacy_publication_recovery_holds WHERE operation_id=%s",
                (operation_id,),
            ).fetchone()[0] == 0
            connection.execute(
                "UPDATE publisher_accounts SET status='active' WHERE id=%s",
                (f["account"].id,),
            )
            before_recovery_snapshot = connection.execute(
                _SNAPSHOT_SQL, (operation_id,)
            ).fetchone()[0]
        before_projection_counts = _recovery_projection_counts(database, operation_id)
        assert retry_activity.last_worker_identity
        assert retry_activity.last_worker_identity != first_worker_identity
        assert retry_activity.last_started_time.seconds > 0
        release_retry.set()
        with pytest.raises(WorkflowFailureError) as failure:
            await asyncio.wait_for(handle.result(), timeout=20)
        typed_failures = [
            error
            for error in _exception_chain(failure.value)
            if getattr(error, "type", None) == "LegacyPublicationRecoveryHeld"
        ]
        assert len(typed_failures) == 1
        await asyncio.wait_for(retry_finished.wait(), timeout=5)
    finally:
        release_retry.set()
        await asyncio.wait_for(worker.shutdown(), timeout=10)
        await asyncio.wait_for(worker_task, timeout=10)
    after = await handle.describe()
    assert after.run_id == original_description.run_id
    assert after.status.name == "FAILED"
    assert resubmit_calls == []
    assert status_calls == []
    assert reconcile_calls == []
    after_projection_counts = _recovery_projection_counts(database, operation_id)
    assert after_projection_counts == before_projection_counts
    assert after_projection_counts["remote_receipts"] == 0
    assert after_projection_counts["status_events"] == 0
    assert after_projection_counts["accepted_checkpoints"] == 0
    assert after_projection_counts["recovery_checkpoints"] == 0
    with psycopg.connect(database) as connection:
        hold_rows = connection.execute(
            """SELECT id,reason,liability_snapshot
               FROM v4_legacy_publication_recovery_holds WHERE operation_id=%s""",
            (operation_id,),
        ).fetchall()
        assert len(hold_rows) == 1
        hold_id, reason, hold_snapshot = hold_rows[0]
        assert reason == "expired_permit_receipt"
        assert typed_failures[0].details == ({"hold_id": str(hold_id)},)
        current_snapshot = connection.execute(_SNAPSHOT_SQL, (operation_id,)).fetchone()[0]
        assert before_recovery_snapshot == current_snapshot == hold_snapshot
        from test_v4_publication_recovery_holds import _assert_publication_attempt_snapshot

        publication_attempt_id = connection.execute(
            "SELECT publication_attempt_id FROM v4_legacy_publication_recovery_holds WHERE operation_id=%s",
            (operation_id,),
        ).fetchone()[0]
        _assert_publication_attempt_snapshot(hold_snapshot, publication_attempt_id)
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
        ).fetchone()[0] == 0
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
async def test_expired_hold_insert_failure_preserves_activity_entry_liability(
    legacy_publication_current_schema, monkeypatch, tmp_path
):
    from threading import Event
    from temporalio import activity
    from temporalio.client import Client, WorkflowFailureError

    from salience.cycles.outbox import CycleOutbox
    from salience.cycles.legacy_publication_runtime import (
        DurableLegacyFixturePublisher,
        GuardedPublicationActivities,
    )
    from salience.cycles.legacy_runtime import build_legacy_worker
    from test_v4_publication_recovery_holds import (
        _SNAPSHOT_SQL,
        _assert_publication_attempt_snapshot,
        _recovery_hold_count,
    )

    f = legacy_publication_current_schema
    submitted = f["bridge"].submit_publication(f["command"])
    _allow(
        f["database"],
        f["workspace_id"],
        f["actor_id"],
        ("legacy:publication:reconcile:account:" + f["account"].id,),
    )
    assert await _hard_exit_publication_worker(
        f, tmp_path, after_acceptance=True, permit_ttl_seconds=5
    ) == 73
    operation_id = submitted["operation_id"]
    workflow_id = "salience-v4-legacy-operation:" + operation_id
    await _wait_for_publication_permit_expiry(f["database"], operation_id)
    with psycopg.connect(f["database"]) as connection:
        before = connection.execute(_SNAPSHOT_SQL, (operation_id,)).fetchone()[0]
    before_projection_counts = _recovery_projection_counts(f["database"], operation_id)

    import salience.cycles.publication_recovery as recovery

    original_record = recovery.record_publication_recovery_hold
    original_submit = DurableLegacyFixturePublisher.submit
    original_reconcile = DurableLegacyFixturePublisher.reconcile
    original_status = DurableLegacyFixturePublisher.status
    original_activity = GuardedPublicationActivities.submit_or_reconcile
    record_calls = []
    first_insert_failed = Event()
    failed_attempt_entry_snapshots = []
    retry_entered = asyncio.Event()
    release_retry = asyncio.Event()
    attempts = []
    submit_calls, reconcile_calls, status_calls = [], [], []

    def fail_first_insert(*args, **kwargs):
        record_calls.append(kwargs["operation_id"])
        if len(record_calls) == 1:
            first_insert_failed.set()
            raise RuntimeError("injected recovery hold insert failure")
        return original_record(*args, **kwargs)

    async def counted_submit(self, request, lease):
        submit_calls.append(request.idempotency_key)
        return await original_submit(self, request, lease)

    async def counted_reconcile(self, idempotency_key):
        reconcile_calls.append(idempotency_key)
        return await original_reconcile(self, idempotency_key)

    async def counted_status(self, remote_id):
        status_calls.append(remote_id)
        return await original_status(self, remote_id)

    @activity.defn(name="salience.publication.submit_or_reconcile")
    async def tracked_activity(self, payload):
        attempt = activity.info().attempt
        attempts.append(attempt)
        if attempt == 2:
            with psycopg.connect(f["database"]) as connection:
                failed_attempt_entry_snapshots.append(
                    connection.execute(_SNAPSHOT_SQL, (operation_id,)).fetchone()[0]
                )
        if attempt >= 3:
            retry_entered.set()
            await release_retry.wait()
        return await original_activity(self, payload)

    monkeypatch.setattr(recovery, "record_publication_recovery_hold", fail_first_insert)
    monkeypatch.setattr(DurableLegacyFixturePublisher, "submit", counted_submit)
    monkeypatch.setattr(DurableLegacyFixturePublisher, "reconcile", counted_reconcile)
    monkeypatch.setattr(DurableLegacyFixturePublisher, "status", counted_status)
    monkeypatch.setattr(GuardedPublicationActivities, "submit_or_reconcile", tracked_activity)
    client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    handle = client.get_workflow_handle(workflow_id)
    outbox = CycleOutbox(
        f["database"], workspace_id=f["workspace_id"], delivery_lane="legacy"
    )
    worker = build_legacy_worker(client, task_queue=f["queue"], outbox=outbox)
    worker_task = asyncio.create_task(worker.run())
    try:
        await asyncio.wait_for(retry_entered.wait(), timeout=65)
        assert await asyncio.to_thread(first_insert_failed.wait, 5)
        assert attempts == [2, 3]
        assert len(record_calls) == 1
        assert failed_attempt_entry_snapshots == [before]
        assert _recovery_hold_count(f["database"], operation_id) == 0
        with psycopg.connect(f["database"]) as connection:
            after_insert_failure = connection.execute(
                _SNAPSHOT_SQL, (operation_id,)
            ).fetchone()[0]
        assert after_insert_failure == before
        assert _recovery_projection_counts(f["database"], operation_id) == before_projection_counts

        release_retry.set()
        with pytest.raises(WorkflowFailureError) as failure:
            await asyncio.wait_for(handle.result(), timeout=20)
        typed_failures = [
            error
            for error in _exception_chain(failure.value)
            if getattr(error, "type", None) == "LegacyPublicationRecoveryHeld"
        ]
        assert len(typed_failures) == 1
    finally:
        release_retry.set()
        await asyncio.wait_for(worker.shutdown(), timeout=10)
        await asyncio.wait_for(worker_task, timeout=10)

    assert record_calls == [UUID(operation_id), UUID(operation_id)]
    assert submit_calls == []
    assert reconcile_calls == []
    assert status_calls == []
    assert _recovery_hold_count(f["database"], operation_id) == 1
    assert _recovery_projection_counts(f["database"], operation_id) == before_projection_counts
    with psycopg.connect(f["database"]) as connection:
        hold_id, hold_snapshot, publication_attempt_id = connection.execute(
            """SELECT id,liability_snapshot,publication_attempt_id
               FROM v4_legacy_publication_recovery_holds WHERE operation_id=%s""",
            (operation_id,),
        ).fetchone()
        after_retry = connection.execute(_SNAPSHOT_SQL, (operation_id,)).fetchone()[0]
    assert typed_failures[0].details == ({"hold_id": str(hold_id)},)
    assert before == hold_snapshot == after_retry
    _assert_publication_attempt_snapshot(hold_snapshot, publication_attempt_id)


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
    assert (
        await _hard_exit_publication_worker(
            f, tmp_path, after_acceptance=False, permit_ttl_seconds=10
        )
        == 72
    )
    operation_id = submitted["operation_id"]
    workflow_id = "salience-v4-legacy-operation:" + operation_id
    await _wait_for_publication_permit_expiry(f["database"], operation_id)
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
            "SELECT count(*) FROM v4_legacy_publication_recovery_holds WHERE operation_id=%s",
            (operation_id,),
        ).fetchone()[0] == 0
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
async def test_post_close_cancel_reads_published_then_holds_original_liability(
    legacy_publication_current_schema, monkeypatch, record_testsuite_property
):
    from temporalio import activity
    from temporalio.client import Client, WorkflowExecutionStatus, WorkflowFailureError

    from salience.cycles.outbox import CycleOutbox
    from salience.cycles.legacy_publication_runtime import DurableLegacyFixturePublisher
    from salience.cycles.legacy_runtime import build_legacy_worker
    from salience.cycles.runtime import TemporalCycleTransport

    f = legacy_publication_current_schema
    processing_read, release_processing, cancel_readback = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    original_status = DurableLegacyFixturePublisher.status
    original_cancel = DurableLegacyFixturePublisher.cancel
    cancel_observations = []

    async def pause_after_processing_read(self, remote_id):
        receipt = await original_status(self, remote_id)
        if receipt is not None and receipt.state == "processing" and not processing_read.is_set():
            processing_read.set()
            await release_processing.wait()
        return receipt

    async def observe_original_cancel_readback(self, remote_id):
        info = activity.info()
        with psycopg.connect(f["database"]) as connection:
            before = connection.execute(
                """SELECT cycle.state, progress.state, receipt.receipt->>'state'
                   FROM v4_legacy_dispatches binding
                   JOIN v4_cycles cycle ON cycle.id=binding.cycle_id
                   JOIN v4_legacy_publication_fixture_progress progress
                     ON progress.operation_id=binding.operation_id
                   JOIN v4_legacy_publication_fixture_receipts receipt
                     ON receipt.operation_id=binding.operation_id
                   WHERE binding.operation_id=%s""",
                (submitted["operation_id"],),
            ).fetchone()
        receipt = None
        try:
            receipt = await original_cancel(self, remote_id)
            return receipt
        finally:
            cancel_observations.append(
                {
                    "activity_type": info.activity_type,
                    "workflow_id": info.workflow_id,
                    "workflow_run_id": info.workflow_run_id,
                    "cycle_state_before_read": before[0],
                    "progress_state_before_read": before[1],
                    "immutable_receipt_state": before[2],
                    "readback_state": receipt.state if receipt is not None else None,
                    "remote_id": remote_id,
                }
            )
            cancel_readback.set()

    monkeypatch.setattr(DurableLegacyFixturePublisher, "status", pause_after_processing_read)
    monkeypatch.setattr(DurableLegacyFixturePublisher, "cancel", observe_original_cancel_readback)
    _allow(
        f["database"],
        f["workspace_id"],
        f["actor_id"],
        ("legacy:publication:reconcile:account:" + f["account"].id,),
    )
    bridge, database = f["bridge"], f["database"]
    submitted = bridge.submit_publication(f["command"])
    client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    box = CycleOutbox(database, workspace_id=f["workspace_id"], delivery_lane="legacy")
    transport = TemporalCycleTransport(client, task_queue=f["queue"])
    workflow_error = None
    workflow_result = None
    initial_receipt = None
    before_cancel = None

    def durable_snapshot(connection):
        request_key = "v4-legacy:" + submitted["operation_id"]
        return {
            "receipt": connection.execute(
                """SELECT receipt.receipt, progress.state
                   FROM v4_legacy_publication_fixture_receipts receipt
                   JOIN v4_legacy_publication_fixture_progress progress USING(operation_id)
                   WHERE receipt.operation_id=%s""",
                (submitted["operation_id"],),
            ).fetchone(),
            "job": connection.execute(
                "SELECT state, output_payload, finished_at FROM jobs WHERE id=%s",
                (submitted["job_id"],),
            ).fetchone(),
            "effect": connection.execute(
                """SELECT status, provider_reference, effect_result
                   FROM external_effects WHERE job_id=%s AND idempotency_key=%s""",
                (submitted["job_id"], request_key + ":publish"),
            ).fetchone(),
            "reservation": connection.execute(
                """SELECT reservation.id, reservation.status, reservation.reserved_amount,
                          plan.state, plan.actual_cost_micros
                   FROM publication_requests request
                   JOIN publication_plans plan ON plan.publication_request_id=request.id
                   LEFT JOIN budget_reservations reservation
                     ON reservation.id=plan.budget_reservation_id
                   WHERE request.request_key=%s""",
                (request_key,),
            ).fetchone(),
            "linked_counts": (
                connection.execute(
                    """SELECT count(*) FROM v4_legacy_dispatches
                       WHERE operation_id=%s AND job_id=%s""",
                    (submitted["operation_id"], submitted["job_id"]),
                ).fetchone()[0],
                connection.execute(
                    "SELECT count(*) FROM v4_legacy_publication_fixture_receipts WHERE operation_id=%s",
                    (submitted["operation_id"],),
                ).fetchone()[0],
                connection.execute(
                    """SELECT count(*) FROM external_effects
                       WHERE job_id=%s AND idempotency_key=%s""",
                    (submitted["job_id"], request_key + ":publish"),
                ).fetchone()[0],
            ),
        }

    async with build_legacy_worker(client, task_queue=f["queue"], outbox=box):
        try:
            assert await box.dispatch_one(transport)
            child = await _wait_started(
                client, "salience-v4-legacy-operation:" + submitted["operation_id"]
            )
            original_run_id = (await child.describe()).run_id
            await asyncio.wait_for(processing_read.wait(), 15)
            with psycopg.connect(database) as connection:
                initial_receipt = connection.execute(
                    "SELECT receipt FROM v4_legacy_publication_fixture_receipts WHERE operation_id=%s",
                    (submitted["operation_id"],),
                ).fetchone()[0]
                progress = connection.execute(
                    """UPDATE v4_legacy_publication_fixture_progress
                       SET state='published', polls=2
                       WHERE operation_id=%s AND state='processing'
                       RETURNING state""",
                    (submitted["operation_id"],),
                ).fetchone()[0]
                assert progress == "published"

            bridge.close(
                submitted["cycle_id"],
                disposition="cancelled",
                reason="Preserve liability while post-close publication state is reconciled",
            )
            assert await box.dispatch_one(transport)

            async with asyncio.timeout(10):
                while True:
                    current_history = await child.fetch_history()
                    signal_seen = any(
                        event.HasField("workflow_execution_signaled_event_attributes")
                        and event.workflow_execution_signaled_event_attributes.signal_name
                        == "request_cancellation"
                        for event in current_history.events
                    )
                    if signal_seen:
                        break
                    description = await child.describe()
                    assert description.status == WorkflowExecutionStatus.RUNNING, (
                        "original workflow ended before its post-close cancellation signal was recorded"
                    )
                    await asyncio.sleep(0.05)

            with psycopg.connect(database) as connection:
                before_cancel = durable_snapshot(connection)
            release_processing.set()

            async with asyncio.timeout(15):
                while not cancel_readback.is_set():
                    description = await child.describe()
                    if description.status != WorkflowExecutionStatus.RUNNING:
                        break
                    await asyncio.sleep(0.05)
            if cancel_readback.is_set():
                try:
                    workflow_result = await asyncio.wait_for(child.result(), 10)
                except WorkflowFailureError as error:
                    workflow_error = error
        finally:
            release_processing.set()

    final_description = await child.describe()
    final_history = await child.fetch_history()
    with psycopg.connect(database) as connection:
        after_close = durable_snapshot(connection)
        terminal_status_event_count = connection.execute(
            """SELECT count(*)
               FROM publication_status_events event
               JOIN publication_attempts attempt ON attempt.id=event.publication_attempt_id
               JOIN publication_plans plan ON plan.id=attempt.publication_plan_id
               JOIN publication_requests request ON request.id=plan.publication_request_id
               WHERE request.request_key=%s
                 AND (event.state IN ('published','cancelled')
                      OR event.source IN ('cancellation','cancellation_readback'))""",
            ("v4-legacy:" + submitted["operation_id"],),
        ).fetchone()[0]
        publication_count = connection.execute(
            """SELECT count(*) FROM publications publication
               JOIN publication_requests request ON request.id=publication.publication_request_id
               WHERE request.request_key=%s""",
            ("v4-legacy:" + submitted["operation_id"],),
        ).fetchone()[0]
        outbox_kinds = connection.execute(
            "SELECT kind FROM v4_cycle_outbox WHERE cycle_id=%s ORDER BY sequence",
            (submitted["cycle_id"],),
        ).fetchall()
        recovery_holds = connection.execute(
            "SELECT id, reason FROM v4_legacy_publication_recovery_holds WHERE operation_id=%s",
            (submitted["operation_id"],),
        ).fetchall()

    cancel_scheduled_events = [
        {
            "event_id": event.event_id,
            "activity_id": event.activity_task_scheduled_event_attributes.activity_id,
            "activity_type": event.activity_task_scheduled_event_attributes.activity_type.name,
        }
        for event in final_history.events
        if event.HasField("activity_task_scheduled_event_attributes")
        and event.activity_task_scheduled_event_attributes.activity_type.name
        == "salience.publication.cancel"
    ]
    cancel_scheduled = [event["event_id"] for event in cancel_scheduled_events]
    cancel_started_events = [
        {
            "event_id": event.event_id,
            "scheduled_event_id": event.activity_task_started_event_attributes.scheduled_event_id,
            "worker_identity": event.activity_task_started_event_attributes.identity,
        }
        for event in final_history.events
        if event.HasField("activity_task_started_event_attributes")
        and event.activity_task_started_event_attributes.scheduled_event_id in cancel_scheduled
    ]
    cancel_started = [event["scheduled_event_id"] for event in cancel_started_events]
    cancel_failures = [
        (event.event_id, event.activity_task_failed_event_attributes.failure)
        for event in final_history.events
        if event.HasField("activity_task_failed_event_attributes")
        and event.activity_task_failed_event_attributes.scheduled_event_id in cancel_scheduled
    ]
    application_failures = []
    for event_id, failure in cancel_failures:
        current = failure
        while current is not None:
            if current.HasField("application_failure_info"):
                application = current.application_failure_info
                application_failures.append(
                    {
                        "event_id": event_id,
                        "type": application.type,
                        "non_retryable": application.non_retryable,
                    }
                )
            current = current.cause if current.HasField("cause") else None

    signal_event_ids = [
        event.event_id
        for event in final_history.events
        if event.HasField("workflow_execution_signaled_event_attributes")
        and event.workflow_execution_signaled_event_attributes.signal_name
        == "request_cancellation"
    ]
    activity_history = {
        "scheduled": cancel_scheduled_events,
        "started": cancel_started_events,
        "failed_event_ids": [event_id for event_id, _ in cancel_failures],
        "application_failures": application_failures,
    }
    evidence_prefix = "post_close_cancel_reads_published_then_holds_original_liability."
    record_testsuite_property(evidence_prefix + "workflow_id", child.id)
    record_testsuite_property(evidence_prefix + "workflow_run_id", original_run_id)
    record_testsuite_property(evidence_prefix + "operation_id", submitted["operation_id"])
    record_testsuite_property(evidence_prefix + "cycle_id", submitted["cycle_id"])
    record_testsuite_property(evidence_prefix + "job_id", submitted["job_id"])
    record_testsuite_property(evidence_prefix + "close_signal_event_ids", json.dumps(signal_event_ids))
    record_testsuite_property(
        evidence_prefix + "cancel_activity_history",
        json.dumps(activity_history, sort_keys=True),
    )
    record_testsuite_property(
        evidence_prefix + "cancel_adapter_observation",
        json.dumps(cancel_observations, sort_keys=True),
    )

    violations = []
    if len(cancel_observations) != 1:
        violations.append(f"cancel adapter observations={cancel_observations!r}")
    elif cancel_observations[0] != {
        "activity_type": "salience.publication.cancel",
        "workflow_id": "salience-v4-legacy-operation:" + submitted["operation_id"],
        "workflow_run_id": final_description.run_id,
        "cycle_state_before_read": "closed",
        "progress_state_before_read": "published",
        "immutable_receipt_state": "accepted",
        "readback_state": "published",
        "remote_id": "fixture-v4-publication:" + submitted["operation_id"],
    }:
        violations.append(f"post-close cancel readback={cancel_observations!r}")
    if len(cancel_scheduled) != 1 or len(cancel_started) != 1:
        violations.append(
            f"Temporal cancel activity scheduled={cancel_scheduled!r}, started={cancel_started!r}"
        )
    if workflow_error is None or workflow_result is not None:
        violations.append(f"workflow result={workflow_result!r}, failure={workflow_error!r}")
    has_typed_hold = workflow_error is not None and any(
        getattr(error, "type", None) == "LegacyPublicationRecoveryHeld"
        for error in _exception_chain(workflow_error)
    )
    if not has_typed_hold:
        violations.append("workflow failure is not LegacyPublicationRecoveryHeld")
    typed_failures = (
        [
            error
            for error in _exception_chain(workflow_error)
            if getattr(error, "type", None) == "LegacyPublicationRecoveryHeld"
        ]
        if workflow_error is not None
        else []
    )
    if len(recovery_holds) != 1:
        violations.append(f"persisted recovery holds={recovery_holds!r}")
    elif recovery_holds[0][1] != "published_during_cancel":
        violations.append(f"persisted recovery hold reason={recovery_holds[0][1]!r}")
    if len(typed_failures) != 1:
        violations.append(f"typed recovery failures={typed_failures!r}")
    elif len(recovery_holds) == 1 and typed_failures[0].details != (
        {"hold_id": str(recovery_holds[0][0])},
    ):
        violations.append(
            f"typed recovery failure details={typed_failures[0].details!r}, "
            f"persisted hold id={recovery_holds[0][0]}"
        )
    typed_nonretryable_failure = any(
        failure["type"] == "LegacyPublicationRecoveryHeld" and failure["non_retryable"]
        for failure in application_failures
    )
    if not typed_nonretryable_failure:
        violations.append(
            f"Temporal cancel application failures={application_failures!r}"
        )
    if final_description.run_id != original_run_id or final_description.status != WorkflowExecutionStatus.FAILED:
        violations.append(
            f"pinned workflow terminal readback run={final_description.run_id}, status={final_description.status}"
        )
    if after_close["receipt"] != (initial_receipt, "published"):
        violations.append(f"immutable receipt/progress after close={after_close['receipt']!r}")
    if before_cancel is None:
        violations.append("canonical state snapshot before the cancel activity is missing")
    else:
        if before_cancel["receipt"] != (initial_receipt, "published"):
            violations.append(f"pre-cancel immutable receipt/progress={before_cancel['receipt']!r}")
        if before_cancel["job"] != ("running", None, None):
            violations.append(f"pre-cancel original job state={before_cancel['job']!r}")
        if (
            before_cancel["reservation"] is None
            or before_cancel["reservation"][1] != "reserved"
            or before_cancel["reservation"][3] != "reserved"
        ):
            violations.append(f"pre-cancel publication liability={before_cancel['reservation']!r}")
        if before_cancel["linked_counts"] != (1, 1, 1):
            violations.append(f"pre-cancel original receipt/effect/job links={before_cancel['linked_counts']!r}")
        if after_close["job"] != before_cancel["job"]:
            violations.append(f"canonical job liability changed: {before_cancel!r} -> {after_close!r}")
        if after_close["effect"] != before_cancel["effect"]:
            violations.append(f"canonical external effect changed: {before_cancel!r} -> {after_close!r}")
        if after_close["reservation"] != before_cancel["reservation"]:
            violations.append(f"publication reservation changed: {before_cancel!r} -> {after_close!r}")
        if after_close["linked_counts"] != before_cancel["linked_counts"]:
            violations.append(f"original receipt/effect/job links changed: {before_cancel!r} -> {after_close!r}")
    if terminal_status_event_count != 0 or publication_count != 0:
        violations.append(
            f"terminal publication events={terminal_status_event_count}, publication rows={publication_count}"
        )
    if outbox_kinds != [("start",), ("close",)]:
        violations.append(f"cycle outbox contains unexpected work: {outbox_kinds!r}")

    assert not violations, "; ".join(violations)


@pytest.mark.asyncio
async def test_publication_revoked_reconcile_scope_holds_durable_acceptance(
    legacy_publication_current_schema, monkeypatch, tmp_path
):
    from temporalio.client import Client, WorkflowFailureError

    from salience.cycles.outbox import CycleOutbox
    from salience.cycles.legacy_publication_runtime import DurableLegacyFixturePublisher
    from salience.cycles.legacy_runtime import build_legacy_worker

    f = legacy_publication_current_schema
    scope = "legacy:publication:reconcile:account:" + f["account"].id
    _allow(f["database"], f["workspace_id"], f["actor_id"], (scope,))
    submitted = f["bridge"].submit_publication(f["command"])
    assert (
        await _hard_exit_publication_worker(
            f, tmp_path, after_acceptance=True, permit_ttl_seconds=10
        )
        == 73
    )
    operation_id = submitted["operation_id"]
    await _wait_for_publication_permit_expiry(f["database"], operation_id)
    with psycopg.connect(f["database"]) as connection:
        assert connection.execute(
            "SELECT count(*) FROM v4_legacy_publication_fixture_receipts WHERE operation_id=%s",
            (operation_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT state,polls FROM v4_legacy_publication_fixture_progress WHERE operation_id=%s",
            (operation_id,),
        ).fetchone() == ("accepted", 0)
        connection.execute(
            """UPDATE permission_grants SET effect='deny'
               WHERE workspace_id=%s AND principal_id=%s AND scope=%s""",
            (f["workspace_id"], str(f["actor_id"]), scope),
        )

    client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    box = CycleOutbox(f["database"], workspace_id=f["workspace_id"], delivery_lane="legacy")
    original_reconcile = DurableLegacyFixturePublisher.reconcile
    reconcile_calls = []

    async def counted_reconcile(self, idempotency_key):
        receipt = await original_reconcile(self, idempotency_key)
        reconcile_calls.append(receipt)
        return receipt

    monkeypatch.setattr(DurableLegacyFixturePublisher, "reconcile", counted_reconcile)
    handle = client.get_workflow_handle("salience-v4-legacy-operation:" + operation_id)
    worker = build_legacy_worker(client, task_queue=f["queue"], outbox=box)
    worker_task = asyncio.create_task(worker.run())
    try:
        await _wait_activity_started(
            client,
            "salience-v4-legacy-operation:" + operation_id,
            "salience.publication.submit_or_reconcile",
            attempt=2,
            timeout=65,
        )
        with pytest.raises(WorkflowFailureError):
            await asyncio.wait_for(handle.result(), timeout=35)
    finally:
        await asyncio.wait_for(worker.shutdown(), timeout=10)
        await asyncio.wait_for(worker_task, timeout=10)

    assert reconcile_calls == []
    with psycopg.connect(f["database"]) as connection:
        assert connection.execute(
            "SELECT count(*) FROM v4_legacy_publication_recovery_holds WHERE operation_id=%s",
            (operation_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT state FROM v4_legacy_publication_fixture_progress WHERE operation_id=%s",
            (operation_id,),
        ).fetchone()[0] == "accepted"
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
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT state FROM jobs WHERE id=%s", (submitted["job_id"],)
        ).fetchone()[0] != "succeeded"
        assert connection.execute(
            "SELECT count(*) FROM publications publication JOIN publication_requests request ON request.id=publication.publication_request_id WHERE request.request_key=%s",
            ("v4-legacy:" + operation_id,),
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
        assert inspected["recovery_hold"] is None
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


@pytest.mark.parametrize(
    "reason", ["expired_permit_receipt", "published_during_cancel"]
)
@pytest.mark.asyncio
async def test_signed_publication_inspection_projects_both_recovery_hold_reasons(
    legacy_publication_current_schema, monkeypatch, capsys, tmp_path, reason
):
    import httpx
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    from fastapi.encoders import jsonable_encoder
    from fastapi.testclient import TestClient

    from salience.api.p0 import create_p0_app
    from salience.cli import main
    from salience.sdk.client import SalienceClient
    from salience.cycles.publication_recovery import record_publication_recovery_hold

    f = legacy_publication_current_schema
    submitted = f["bridge"].submit_publication(f["command"])
    if reason == "expired_permit_receipt":
        _allow(
            f["database"],
            f["workspace_id"],
            f["actor_id"],
            ("legacy:publication:reconcile:account:" + f["account"].id,),
        )
        assert await _hard_exit_publication_worker(
            f, tmp_path, after_acceptance=True, permit_ttl_seconds=10
        ) == 73
        await _wait_for_publication_permit_expiry(
            f["database"], submitted["operation_id"]
        )
    else:
        assert await _hard_exit_publication_worker(
            f, tmp_path, after_acceptance=True
        ) == 73
        with psycopg.connect(f["database"]) as connection:
            connection.execute(
                """UPDATE v4_legacy_publication_fixture_progress
                   SET state='published', polls=2, updated_at=clock_timestamp()
                   WHERE operation_id=%s AND state IN ('accepted','processing')""",
                (submitted["operation_id"],),
            )
        f["bridge"].close(
            submitted["cycle_id"],
            disposition="cancelled",
            reason="Preserve accepted publication liability while reading final remote state",
        )
    hold = record_publication_recovery_hold(
        f["database"],
        workspace_id=f["workspace_id"],
        operation_id=UUID(submitted["operation_id"]),
        reason=reason,
    )
    expected_projection = jsonable_encoder(hold)

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
        api_response = client.get(
            f"/v1/publications/runs/{submitted['job_id']}",
            headers={"Authorization": "Bearer " + token},
        )
        assert api_response.status_code == 200
        api_projection = api_response.json()["recovery_hold"]
        sdk_projection = sdk.inspect(submitted["job_id"])["recovery_hold"]
        main(["legacy-publication", "inspect", submitted["job_id"]])
        cli_projection = json.loads(capsys.readouterr().out)["recovery_hold"]
        assert api_projection == sdk_projection == cli_projection == expected_projection
        assert set(api_projection) == {
            "id",
            "workspace_id",
            "operation_id",
            "goal_id",
            "intent_id",
            "cycle_id",
            "context_id",
            "job_id",
            "publication_attempt_id",
            "owner_id",
            "reason",
            "state",
            "execution_deadline",
            "permit_expires_at",
            "created_at",
        }
        assert api_projection["reason"] == reason
        assert api_projection["state"] == "held"
        assert not hasattr(sdk, "record_recovery_hold")
        assert (
            "/v1/publications/runs/{job_id}/recovery-hold"
            not in app.openapi()["paths"]
        )
        assert client.post(
            f"/v1/publications/runs/{submitted['job_id']}/recovery-hold",
            headers={"Authorization": "Bearer " + token},
            json={"reason": reason},
        ).status_code >= 400
        with pytest.raises(SystemExit) as unavailable_command:
            main(["legacy-publication", "recovery-hold", submitted["job_id"]])
        assert unavailable_command.value.code == 2
        capsys.readouterr()



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
