"""Legacy compatibility commands must enter canonical admission and outbox."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import os
from uuid import uuid4

import psycopg
import pytest

from test_v4_cadence_policy import policy, request
from test_v4_cycle_admission import cycles
from test_v4_schedule_cutover import cutover


@pytest.fixture
def legacy(policy, monkeypatch):
    from salience.cycles.legacy_dispatch import LegacyDispatch, LegacyIntelligenceCommand

    monkeypatch.setenv("SALIENCE_DEPLOYMENT_MODE", "fixture")
    monkeypatch.setenv("SALIENCE_EFFECTS_ENABLED", "false")
    service, goal, spec, database = policy
    bridge = LegacyDispatch(database, workspace_id=service.workspace_id,
                            subject_id=service.subject_id,
                            task_queue="salience-v4-local-legacy-" + str(uuid4()))
    with psycopg.connect(database) as connection:
        for scope in ("cycles:permit", "cycles:read", "cycles:stop"):
            connection.execute("INSERT INTO permission_grants(workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at) VALUES(%s,'identity',%s,%s,'allow','{}',now()+interval '1 hour')", (service.workspace_id,str(service.subject_id),scope))
    command = LegacyIntelligenceCommand(goal_id=goal, request=request(spec), niche="Fixture")
    return bridge, command, service, database


def _seed_legacy_stage_for_migration_refusal(database, *, stage):
    """Create one canonical legacy stage row in an otherwise empty 0042 DB."""
    from datetime import datetime, timedelta, timezone
    from uuid import uuid4

    from psycopg.types.json import Jsonb

    from salience.cycles.admission import CycleAdmission
    from salience.cycles.contracts import (
        CadencePolicy,
        CycleRequest,
        GoalSpec,
        GoalSpecV2,
    )
    from salience.cycles.legacy_dispatch import (
        LegacyCreativeCommand,
        LegacyDispatch,
        LegacyDummyCommand,
    )
    from test_v4_cycle_admission import approved_goal

    if stage not in {"creative", "dummy"}:
        raise ValueError("migration refusal fixture requires creative or dummy stage")

    workspace_id, subject_id, program_id = uuid4(), uuid4(), uuid4()
    scopes = (
        "goals:write",
        "goals:approve",
        "cycles:write",
        "cycles:review",
        "cycles:permit",
        "cycles:read",
        "cycles:stop",
        "legacy:" + stage,
    )
    with psycopg.connect(database) as connection:
        head = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        assert head == "0042_publication_recovery_holds"
        connection.execute(
            "INSERT INTO workspaces(id,slug,display_name) VALUES(%s,%s,'Migration fixture')",
            (workspace_id, str(workspace_id)),
        )
        connection.execute(
            "INSERT INTO identity_subjects(id,workspace_id,issuer,subject,expires_at) "
            "VALUES(%s,%s,'https://fixture.invalid',%s,now()+interval '1 hour')",
            (subject_id, workspace_id, str(subject_id)),
        )
        for scope in scopes:
            connection.execute(
                "INSERT INTO permission_grants(workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at) "
                "VALUES(%s,'identity',%s,%s,'allow','{}',now()+interval '1 hour')",
                (workspace_id, str(subject_id), scope),
            )
        connection.execute(
            "INSERT INTO content_programs(id,workspace_id,slug,name,niche) "
            "VALUES(%s,%s,%s,'Migration fixture','Fixture')",
            (program_id, workspace_id, str(program_id)),
        )

    service = CycleAdmission(
        database, workspace_id=workspace_id, subject_id=subject_id
    )
    now = datetime.now(timezone.utc).replace(microsecond=0)
    anchor = now - timedelta(minutes=10)
    legacy_spec = GoalSpec(
        objective="Isolated migration guard fixture",
        metric_versions=("fixture-quality@1",),
        audience="internal",
        account_refs=("fixture-account",),
        brand_scope="fixture-brand",
        source_policy="fixture-only",
        horizon_end=now + timedelta(days=1),
    )
    spec = GoalSpecV2(
        **(
            legacy_spec.model_dump()
            | {
                "schema_version": "GoalSpec.local.v2",
                "content_program_id": program_id,
                "cadence_seconds": None,
                "account_refs": ("fixture-account",),
                "channel_refs": ("fixture-channel",),
                "content_scope": "fixture-only",
                "policy_refs": ("fixture-policy@1",),
                "retention_policy": "fixture-retention@1",
                "cadence": CadencePolicy(anchor=anchor),
            }
        )
    )
    goal_id = approved_goal(service, spec)
    command_request = CycleRequest(
        origin="manual",
        idempotency_key="migration-refusal-" + stage,
        expected_revision=1,
        slot_time=anchor + timedelta(minutes=10),
    )
    bridge = LegacyDispatch(
        database,
        workspace_id=workspace_id,
        subject_id=subject_id,
        task_queue="salience-v4-local-legacy-" + str(uuid4()),
    )
    if stage == "dummy":
        submitted = bridge.submit_dummy(
            LegacyDummyCommand(goal_id=goal_id, request=command_request)
        )
    else:
        topic_id, package_id, brief_id = uuid4(), uuid4(), uuid4()
        with psycopg.connect(database) as connection:
            connection.execute(
                "INSERT INTO topic_opportunities(id,workspace_id,content_program_id,fingerprint,topic,score,explanation) "
                "VALUES(%s,%s,%s,%s,'Fixture topic',1,'Migration guard fixture')",
                (topic_id, workspace_id, program_id, str(topic_id)),
            )
            connection.execute(
                "INSERT INTO strategic_packages(id,workspace_id,content_program_id,topic_opportunity_id,diversity_fingerprint,package) "
                "VALUES(%s,%s,%s,%s,%s,%s)",
                (
                    package_id,
                    workspace_id,
                    program_id,
                    topic_id,
                    str(package_id),
                    Jsonb({"fixture": "migration-guard"}),
                ),
            )
            connection.execute(
                "INSERT INTO content_brief_versions(id,workspace_id,content_program_id,brief_key,version,topic_opportunity_id,strategic_package_id,content) "
                "VALUES(%s,%s,%s,'migration-guard',1,%s,%s,%s)",
                (
                    brief_id,
                    workspace_id,
                    program_id,
                    topic_id,
                    package_id,
                    Jsonb({"fixture": "migration-guard"}),
                ),
            )
        submitted = bridge.submit_creative(
            LegacyCreativeCommand(
                goal_id=goal_id,
                request=command_request,
                brief_id=brief_id,
                max_variants=1,
            )
        )

    with psycopg.connect(database) as connection:
        rows = connection.execute(
            "SELECT binding.stage,job.job_type,job.state,job.dry_run "
            "FROM v4_legacy_dispatches binding JOIN jobs job ON job.id=binding.job_id "
            "WHERE binding.operation_id=%s",
            (submitted["operation_id"],),
        ).fetchall()
        unrelated_history = {
            table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "job_schedules",
                "v4_native_publication_schedule_sources",
                "v4_native_publication_schedule_progress",
                "v4_legacy_publication_schedule_plans",
                "v4_legacy_publication_recovery_holds",
            )
        }
    expected_job = "creative_production" if stage == "creative" else "durable_dummy"
    assert rows == [(stage, expected_job, "queued", True)]
    assert unrelated_history == {table: 0 for table in unrelated_history}
    return submitted


def test_legacy_submit_commits_one_canonical_job_and_start_under_concurrent_replay(legacy):
    bridge, command, service, database = legacy
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: bridge.submit(command), range(3)))
    assert results[0] == results[1] == results[2]
    result = results[0]
    assert result["state"] == "queued" and result["disposition"] == "admitted"
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM v4_cycle_outbox WHERE cycle_id=%s", (result["cycle_id"],)).fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM jobs WHERE id=%s AND dry_run AND state='queued'", (result["job_id"],)).fetchone()[0] == 1
        identity = connection.execute("SELECT context_id,operation_id FROM v4_cycles WHERE id=%s", (result["cycle_id"],)).fetchone()
    assert tuple(map(str, identity)) == (result["context_id"], result["operation_id"])


def test_legacy_submit_rolls_back_everything_before_commit(legacy, monkeypatch):
    bridge, command, service, database = legacy
    original = bridge._materialize
    def crash(*args):
        original(*args)
        raise RuntimeError("crash before commit")
    monkeypatch.setattr(bridge, "_materialize", crash)
    with pytest.raises(RuntimeError, match="crash before commit"):
        bridge.submit(command)
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM v4_cycle_intents WHERE goal_id=%s", (command.goal_id,)).fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM jobs WHERE workspace_id=%s", (service.workspace_id,)).fetchone()[0] == 0
    monkeypatch.setattr(bridge, "_materialize", original)
    assert bridge.submit(command)["state"] == "queued"


def test_legacy_replay_conflict_and_current_revocation(legacy):
    bridge, command, service, database = legacy
    result = bridge.submit(command)
    with pytest.raises(ValueError, match="conflict"):
        bridge.submit(command.model_copy(update={"niche": "changed"}))
    with psycopg.connect(database) as connection:
        connection.execute("UPDATE identity_subjects SET enabled=false WHERE id=%s", (service.subject_id,))
    with pytest.raises(PermissionError):
        bridge.submit(command)


def test_legacy_binding_direct_sql_identity_guard_and_delivery_lane(legacy):
    from salience.cycles.outbox import CycleOutbox
    bridge, command, service, database = legacy
    result = bridge.submit(command)
    assert CycleOutbox(database, workspace_id=service.workspace_id).claim() is None
    box = CycleOutbox(database, workspace_id=service.workspace_id, delivery_lane="legacy")
    message = box.claim()
    assert str(message["cycle_id"]) == result["cycle_id"]
    with psycopg.connect(database) as connection, pytest.raises(psycopg.Error, match="immutable"):
        connection.execute("UPDATE jobs SET workflow_run_id='changed' WHERE id=%s", (result["job_id"],))
    with psycopg.connect(database) as connection, pytest.raises(psycopg.Error, match="immutable"):
        connection.execute("DELETE FROM v4_legacy_dispatches WHERE cycle_id=%s", (result["cycle_id"],))


def test_legacy_dispatch_rejects_effects_and_cross_program(legacy, monkeypatch):
    bridge, command, service, database = legacy
    monkeypatch.setenv("SALIENCE_EFFECTS_ENABLED", "true")
    with pytest.raises(PermissionError):
        bridge.submit(command)
    monkeypatch.setenv("SALIENCE_EFFECTS_ENABLED", "false")
    foreign = command.model_copy(update={"goal_id": uuid4()})
    with pytest.raises(PermissionError):
        bridge.submit(foreign)


def test_legacy_lane_cannot_be_consumed_or_exhausted_by_fixture_dispatcher(legacy):
    from salience.cycles.outbox import CycleOutbox
    bridge, command, service, database = legacy
    result=bridge.submit(command)
    box=CycleOutbox(database,workspace_id=service.workspace_id,delivery_lane="legacy",max_attempts=2)
    claimed=box.claim()
    with psycopg.connect(database) as connection:
        connection.execute("UPDATE v4_cycle_outbox SET lease_until=now()-interval '1 second' WHERE id=%s",(claimed["id"],))
    fixture=CycleOutbox(database,workspace_id=service.workspace_id,max_attempts=1)
    assert fixture.claim() is None
    with pytest.raises(PermissionError,match="delivery lane"):
        fixture.consume(claimed["id"])
    retry=box.claim()
    assert retry["id"]==claimed["id"] and retry["attempts"]==2


def test_adopting_undelivered_cycle_keeps_original_context_trace(legacy):
    bridge,command,service,database=legacy
    intent_id=service.request_cycle(command.goal_id,command.request)
    cycle=service.admit(intent_id)["cycle_id"]
    result=bridge.submit(command)
    assert result["cycle_id"]==str(cycle)
    with psycopg.connect(database) as connection:
        context_trace=connection.execute("SELECT traceparent FROM v4_cycle_outbox WHERE cycle_id=%s AND kind='start'",(cycle,)).fetchone()[0]
        trace=connection.execute("SELECT trace_id FROM jobs WHERE id=%s",(result["job_id"],)).fetchone()[0]
    assert trace==context_trace.split("-")[1]


@pytest.mark.asyncio
async def test_cancel_before_delivery_never_starts_legacy_intelligence(legacy):
    import asyncio
    from temporalio.client import Client
    from salience.cycles.outbox import CycleOutbox
    from salience.cycles.runtime import TemporalCycleTransport
    from salience.cycles.legacy_runtime import build_legacy_worker

    bridge,command,service,database=legacy
    result=bridge.submit(command)
    service.close(result["cycle_id"],disposition="cancelled",reason="Cancel before delivery")
    client=await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    box=CycleOutbox(database,workspace_id=service.workspace_id,delivery_lane="legacy")
    transport=TemporalCycleTransport(client,task_queue=bridge.task_queue)
    async with build_legacy_worker(client,task_queue=bridge.task_queue,outbox=box):
        assert await box.dispatch_one(transport)
        assert await box.dispatch_one(transport)
        await asyncio.wait_for(client.get_workflow_handle(transport.workflow_id(result["cycle_id"])).result(),10)
    assert bridge.inspect(result["job_id"])["state"]=="cancelled"
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM v4_permit_claims WHERE cycle_id=%s",(result["cycle_id"],)).fetchone()[0]==0


def test_legacy_migration_empty_rollback_and_populated_history_preservation(legacy):
    import subprocess,sys
    from urllib.parse import urlsplit,urlunsplit
    from salience.cycles.legacy_dispatch import LegacyDispatch
    from salience.cycles.admission import CycleAdmission
    from test_v4_cycle_admission import approved_goal

    bridge,command,service,source=legacy
    name="legacy_migration_"+uuid4().hex[:12]
    database=urlunsplit(urlsplit(source)._replace(path="/"+name))
    with psycopg.connect(source,autocommit=True) as admin:
        admin.execute(psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(name)))
        try:
            def migrate(direction,target):
                return subprocess.run([sys.executable,"-m","alembic","-x",f"database_url={database}",direction,target],capture_output=True,text=True)
            assert migrate("upgrade","0050_p1_production_dispatch_reissue").returncode==0
            assert migrate("downgrade","0035_parallel_agent_teams").returncode==0
            assert migrate("upgrade","0050_p1_production_dispatch_reissue").returncode==0
            with psycopg.connect(database) as c:
                head_revision=c.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            with psycopg.connect(source) as c:
                spec=c.execute("SELECT payload FROM v4_goal_revisions WHERE goal_id=%s AND revision=1",(command.goal_id,)).fetchone()[0]
            with psycopg.connect(database) as c:
                c.execute("INSERT INTO workspaces(id,slug,display_name) VALUES(%s,%s,'Migration')",(service.workspace_id,str(service.workspace_id)))
                c.execute("INSERT INTO content_programs(id,workspace_id,slug,name,niche) VALUES(%s,%s,%s,'Migration','Fixture')",(spec["content_program_id"],service.workspace_id,str(uuid4())))
                c.execute("INSERT INTO identity_subjects(id,workspace_id,issuer,subject,expires_at) VALUES(%s,%s,'https://fixture.invalid',%s,now()+interval '1 hour')",(service.subject_id,service.workspace_id,str(service.subject_id)))
                for scope in ("goals:write","goals:approve","cycles:write"):
                    c.execute("INSERT INTO permission_grants(workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at) VALUES(%s,'identity',%s,%s,'allow','{}',now()+interval '1 hour')",(service.workspace_id,str(service.subject_id),scope))
            from salience.cycles.contracts import parse_goal
            isolated=CycleAdmission(database,workspace_id=service.workspace_id,subject_id=service.subject_id)
            goal=approved_goal(isolated,parse_goal(spec))
            binding=LegacyDispatch(database,workspace_id=service.workspace_id,subject_id=service.subject_id,task_queue=bridge.task_queue).submit(command.model_copy(update={"goal_id":goal}))
            with psycopg.connect(database) as c:
                binding_snapshot=c.execute("SELECT to_jsonb(dispatch) FROM v4_legacy_dispatches dispatch WHERE job_id=%s",(binding["job_id"],)).fetchone()[0]
                job_snapshot=c.execute("SELECT to_jsonb(job) FROM jobs job WHERE id=%s",(binding["job_id"],)).fetchone()[0]
            rolled=migrate("downgrade","0035_parallel_agent_teams")
            assert rolled.returncode!=0 and any(message in rolled.stderr for message in (
                "preserve legacy dispatch",
                "preserve production policy and revocation authority history",
            ))
            with psycopg.connect(database) as c:
                assert c.execute("SELECT version_num FROM alembic_version").fetchone()[0]==head_revision
                assert c.execute("SELECT to_jsonb(dispatch) FROM v4_legacy_dispatches dispatch WHERE job_id=%s",(binding["job_id"],)).fetchone()[0] == binding_snapshot
                assert c.execute("SELECT to_jsonb(job) FROM jobs job WHERE id=%s",(binding["job_id"],)).fetchone()[0] == job_snapshot
        finally:
            admin.execute(psycopg.sql.SQL("DROP DATABASE {} WITH (FORCE)").format(psycopg.sql.Identifier(name)))


@pytest.mark.asyncio
async def test_legacy_runtime_executes_actual_intelligence_once_and_ordered_close(legacy):
    import asyncio
    from temporalio.client import Client
    from salience.cycles.outbox import CycleOutbox
    from salience.cycles.runtime import TemporalCycleTransport
    from salience.cycles.legacy_runtime import build_legacy_worker

    bridge, command, service, database = legacy
    result = bridge.submit(command)
    client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    outbox = CycleOutbox(database,workspace_id=service.workspace_id,delivery_lane="legacy")
    transport = TemporalCycleTransport(client,task_queue=bridge.task_queue)
    async with build_legacy_worker(client,task_queue=bridge.task_queue,outbox=outbox):
        assert await outbox.dispatch_one(transport)
        execution = client.get_workflow_handle("salience-v4-legacy-operation:" + result["operation_id"])
        async with asyncio.timeout(20):
            while True:
                try:
                    await execution.describe()
                    break
                except Exception:
                    await asyncio.sleep(0.05)
        output = await asyncio.wait_for(execution.result(),20)
        assert output["state"] == "completed" and output["job_id"] == result["job_id"]
        assert bridge.inspect(result["job_id"])["state"] == "succeeded"
        # Canonical recovery changes control state, never starts another stage.
        service.recover(result["cycle_id"],state="retry_due")
        service.recover(result["cycle_id"],state="runnable")
        service.close(result["cycle_id"],disposition="completed",reason="Fixture execution complete")
        for _ in range(3):
            assert await outbox.dispatch_one(transport)
        await asyncio.wait_for(client.get_workflow_handle(transport.workflow_id(result["cycle_id"])).result(),10)
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM v4_cycle_inbox WHERE cycle_id=%s",(result["cycle_id"],)).fetchone()[0] == 4
        assert connection.execute("SELECT count(*) FROM v4_permit_claims WHERE cycle_id=%s",(result["cycle_id"],)).fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM jobs WHERE id=%s",(result["job_id"],)).fetchone()[0] == 1


@pytest.mark.asyncio
async def test_legacy_adapter_lost_ack_holds_original_without_second_start(legacy):
    import asyncio
    from salience.cycles.outbox import CycleOutbox
    from salience.cycles.legacy_runtime import LegacyIntelligenceAdapter, LegacyCycleActivities

    bridge, command, service, database = legacy
    result = bridge.submit(command)
    outbox = CycleOutbox(database,workspace_id=service.workspace_id,delivery_lane="legacy")
    message = outbox.claim()
    calls = []
    class LostAck:
        async def start_workflow(self,*args,**kwargs):
            calls.append(kwargs["id"])
            raise TimeoutError("remote start acknowledgment unknown")
    activities = LegacyCycleActivities(outbox,adapter=LegacyIntelligenceAdapter(LostAck(),outbox,task_queue=bridge.task_queue))
    envelope = {"message_id":str(message["id"]),"cycle_id":result["cycle_id"],"kind":"start","traceparent":message["traceparent"]}
    with pytest.raises(TimeoutError):
        await activities.consume(envelope)
    assert (await activities.consume(envelope))["state"] == "held"
    assert calls == ["salience-v4-legacy-operation:" + result["operation_id"]]
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT state FROM v4_permit_claims WHERE cycle_id=%s",(result["cycle_id"],)).fetchone()[0] == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("changed_scope", "expected_state", "expected_start"),
    [
        ("cycles:reconcile", "recorded", True),
        ("cycles:write", "held", False),
    ],
)
async def test_fixture_cycle_start_rechecks_only_its_current_start_authority(
    legacy, changed_scope, expected_state, expected_start
):
    from salience.cycles.legacy_runtime import (
        LegacyCycleActivities,
        LegacyIntelligenceAdapter,
    )
    from salience.cycles.outbox import CycleOutbox

    bridge, command, service, database = legacy
    submitted = bridge.submit(command)
    with psycopg.connect(database) as connection:
        if changed_scope == "cycles:write":
            connection.execute(
                "UPDATE permission_grants SET effect='deny' "
                "WHERE workspace_id=%s AND principal_id=%s AND scope=%s",
                (service.workspace_id, str(service.subject_id), changed_scope),
            )
        else:
            connection.execute(
                "INSERT INTO permission_grants "
                "(workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at) "
                "VALUES(%s,'identity',%s,%s,'allow','{}',now()+interval '1 hour')",
                (service.workspace_id, str(service.subject_id), changed_scope),
            )

    outbox = CycleOutbox(
        database, workspace_id=service.workspace_id, delivery_lane="legacy"
    )
    message = outbox.claim()

    class RecordingTemporalClient:
        def __init__(self):
            self.started = []

        async def start_workflow(self, workflow, payload, **options):
            self.started.append((options["id"], options["task_queue"]))

    client = RecordingTemporalClient()
    adapter = LegacyIntelligenceAdapter(
        client, outbox, task_queue=bridge.task_queue
    )
    activities = LegacyCycleActivities(outbox, adapter=adapter)
    result = await activities.consume(
        {
            "message_id": str(message["id"]),
            "cycle_id": str(submitted["cycle_id"]),
            "kind": "start",
            "traceparent": message["traceparent"],
        }
    )

    assert result["state"] == expected_state
    assert client.started == (
        [("salience-v4-legacy-operation:" + submitted["operation_id"], bridge.task_queue)]
        if expected_start
        else []
    )
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT state FROM v4_cycle_inbox WHERE message_id=%s",
            (message["id"],),
        ).fetchone()[0] == expected_state
        claim = connection.execute(
            "SELECT state FROM v4_permit_claims WHERE cycle_id=%s",
            (submitted["cycle_id"],),
        ).fetchone()
    assert (claim[0] if claim else None) == ("claimed" if expected_start else None)


def test_legacy_stage_guard_rechecks_stop_after_claim(legacy):
    from hashlib import sha256
    from salience.cycles.governance import CycleGovernance, PermitRequest
    from salience.cycles.legacy_runtime import binding_for_operation, require_current_stage
    from salience.cycles.outbox import CycleOutbox

    bridge, command, service, database = legacy
    result = bridge.submit(command)
    outbox = CycleOutbox(database,workspace_id=service.workspace_id,delivery_lane="legacy")
    governance = CycleGovernance(database,workspace_id=service.workspace_id,subject_id=service.subject_id)
    permit = governance.issue_permit(result["cycle_id"],PermitRequest(context_id=result["context_id"],operation_id=result["operation_id"],
        expected_goal_revision=1,account_ref="fixture-account",purpose="fixture_execution",effect="fixture.noop",artifact_sha256=sha256(b"stage").hexdigest(),ttl_seconds=30),idempotency_key="claim")
    assert governance.claim_permit(permit["permit_id"])["dispatch_allowed"]
    binding = binding_for_operation(outbox,result["operation_id"])
    require_current_stage(outbox,binding)
    governance.set_stop(goal_id=command.goal_id,stopped=True,expected_revision=1,idempotency_key="stop",reason="Fixture stop")
    with pytest.raises(PermissionError):
        require_current_stage(outbox,binding)


def test_legacy_cutover_poll_atomically_materializes_scheduled_job_and_watermark(cutover, monkeypatch):
    import time
    from salience.cycles.legacy_dispatch import LegacyDispatch
    scheduler, service, goal, spec, legacy_id, database = cutover
    monkeypatch.setenv("SALIENCE_DEPLOYMENT_MODE","fixture")
    scheduler.prepare(goal,legacy_schedule_id=legacy_id,expected_revision=1,first_v4_slot=spec.cadence.anchor,idempotency_key="prepare")
    bridge = LegacyDispatch(database,workspace_id=service.workspace_id,subject_id=service.subject_id,task_queue="salience-v4-local-legacy-schedule")
    bridge.bind_schedule(goal,expected_revision=1,niche="Fixture")
    scheduler.activate(goal,idempotency_key="activate")
    time.sleep(max(0,(spec.cadence.anchor-datetime.now(timezone.utc)).total_seconds())+.05)
    original = bridge._materialize
    monkeypatch.setattr(LegacyDispatch,"_materialize",lambda *args: (_ for _ in ()).throw(RuntimeError("before job commit")))
    with pytest.raises(RuntimeError):
        scheduler.poll(goal,expected_revision=1,idempotency_key="poll")
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM v4_cycles c JOIN v4_cycle_intents i ON i.id=c.intent_id WHERE i.goal_id=%s",(goal,)).fetchone()[0] == 0
    monkeypatch.setattr(LegacyDispatch,"_materialize",lambda self,*args: original(*args))
    result = scheduler.poll(goal,expected_revision=1,idempotency_key="poll")
    assert result["admissions"][0]["disposition"] == "admitted"
    repeated = scheduler.poll(goal,expected_revision=1,idempotency_key="poll")
    assert repeated["admissions"] == result["admissions"]
    with psycopg.connect(database) as connection:
        binding = connection.execute("SELECT cycle_id,job_id FROM v4_legacy_dispatches WHERE goal_id=%s",(goal,)).fetchall()
        assert len(binding)==1
    with pytest.raises(ValueError,match="unresolved"):
        scheduler.rollback(goal,idempotency_key="rollback")


def test_signed_legacy_api_commits_without_temporal_and_denies_old_unbound_body(legacy,monkeypatch,capsys):
    from cryptography.hazmat.primitives.asymmetric import rsa
    from datetime import timedelta
    from fastapi.testclient import TestClient
    import jwt
    from salience.api.p0 import create_p0_app

    bridge, command, service, database = legacy
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    app=create_p0_app(database_url=database,workspace_id=service.workspace_id,issuer="https://fixture.invalid",audience="fixture",
        public_key=key.public_key(),enable_legacy_dispatch=True,legacy_fixture_queue=bridge.task_queue)
    now=datetime.now(timezone.utc)
    token=jwt.encode({"iss":"https://fixture.invalid","aud":"fixture","sub":str(service.subject_id),"iat":now,"nbf":now,"exp":now+timedelta(minutes=5)},key,algorithm="RS256")
    headers={"Authorization":"Bearer "+token,"X-Salience-Scopes":"admin:*,control:write"}
    with TestClient(app) as client:
        first=client.post("/v1/intelligence/runs",headers=headers,json=command.model_dump(mode="json"))
        assert first.status_code==202, first.text
        assert client.post("/v1/intelligence/runs",headers=headers,json=command.model_dump(mode="json")).json()==first.json()
        job=first.json()["job_id"]
        import httpx,json
        from salience.sdk.client import SalienceClient
        from salience.cli import main
        monkeypatch.setattr(httpx,"request",lambda method,url,**kwargs:client.request(method,url,**{k:v for k,v in kwargs.items() if k!="timeout"}))
        sdk=SalienceClient("http://testserver",token).legacy_intelligence
        assert sdk.submit(command.model_dump(mode="json"))==first.json()
        assert sdk.inspect(job)["state"]=="queued"
        monkeypatch.setenv("SALIENCE_CONTROL_JWT",token)
        monkeypatch.setenv("SALIENCE_CONTROL_URL","http://testserver")
        main(["legacy-intelligence","submit","--command-json",json.dumps(command.model_dump(mode="json"))])
        assert json.loads(capsys.readouterr().out)["job_id"]==job
        assert client.get("/v1/intelligence/runs/"+job,headers=headers).json()["state"]=="queued"
        assert client.post("/v1/intelligence/runs",headers=headers,json={"workspace_id":str(service.workspace_id),"content_program_id":str(uuid4()),"niche":"Fixture","idempotency_key":"old"}).status_code==422
        assert client.post("/v1/intelligence/runs/"+job+"/cancel",headers=headers,json={"reason":"Cancel before dispatch"}).status_code==200
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT kind FROM v4_cycle_outbox WHERE cycle_id=%s ORDER BY sequence",(first.json()["cycle_id"],)).fetchall()==[("start",),("close",)]


@pytest.mark.asyncio
async def test_actual_temporal_schedule_pause_and_rollback_watermark(policy, monkeypatch):
    import asyncio
    from datetime import timedelta
    from psycopg.types.json import Jsonb
    from temporalio.client import Client,Schedule,ScheduleActionStartWorkflow,ScheduleSpec,ScheduleIntervalSpec
    from salience.cycles.contracts import CadencePolicy
    from salience.cycles.schedule_cutover import CycleScheduleCutover
    from salience.cycles.temporal_schedule_control import TemporalFixtureLegacyScheduleControl
    from salience.cycles.legacy_dispatch import LegacyDispatch
    from test_v4_cycle_admission import approved_goal

    monkeypatch.setenv("SALIENCE_DEPLOYMENT_MODE","fixture")
    service, _, old_spec, database = policy
    anchor=datetime.now(timezone.utc).replace(microsecond=0)+timedelta(minutes=10)
    spec=old_spec.model_copy(update={"cadence":CadencePolicy(anchor=anchor,interval_seconds=60)})
    goal=approved_goal(service,spec)
    legacy_id=uuid4()
    remote_id=f"salience-v4-legacy-fixture:{service.workspace_id}:{legacy_id}"
    queue="salience-v4-local-legacy-temporal-"+str(uuid4())
    with psycopg.connect(database) as connection:
        connection.execute("INSERT INTO permission_grants(workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at) VALUES(%s,'identity',%s,'cycles:schedule','allow','{}',now()+interval '1 hour')",(service.workspace_id,str(service.subject_id)))
        connection.execute("INSERT INTO job_schedules(id,workspace_id,content_program_id,name,schedule_expression,timezone,job_type,payload,next_run_at) VALUES(%s,%s,%s,%s,'every:60s','UTC','v4_fixture_legacy',%s,%s)",(legacy_id,service.workspace_id,spec.content_program_id,str(legacy_id),Jsonb({"dry_run":True,"last_slot":(anchor-timedelta(seconds=60)).isoformat(),"temporal_schedule_id":remote_id}),anchor))
    client=await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    payload={"workspace_id":str(service.workspace_id),"content_program_id":str(spec.content_program_id),"niche":"Fixture","idempotency_key":"old-fixture","dry_run":True}
    await client.create_schedule(remote_id,Schedule(action=ScheduleActionStartWorkflow("IntelligenceLoopWorkflow",payload,id=remote_id+":run",task_queue=queue),spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=timedelta(seconds=60),offset=timedelta(seconds=anchor.timestamp()%60))],start_at=anchor)))
    control=TemporalFixtureLegacyScheduleControl(database,workspace_id=service.workspace_id,temporal_target=os.environ["TEST_TEMPORAL_TARGET"],task_queue=queue)
    scheduler=CycleScheduleCutover(database,workspace_id=service.workspace_id,subject_id=service.subject_id,legacy_control=control)
    try:
        await asyncio.to_thread(scheduler.prepare,goal,legacy_schedule_id=legacy_id,expected_revision=1,first_v4_slot=anchor,idempotency_key="prepare")
        await asyncio.to_thread(LegacyDispatch(database, workspace_id=service.workspace_id,
            subject_id=service.subject_id, task_queue=queue).bind_schedule, goal, expected_revision=1, niche="Fixture")
        # A real old schedule execution must drain before V4 activation.
        handle = client.get_schedule_handle(remote_id)
        await handle.trigger()
        async with asyncio.timeout(10):
            while not (description := await handle.describe()).info.running_actions:
                await asyncio.sleep(0.05)
        old_execution = description.info.running_actions[0]
        with pytest.raises(ValueError, match="in-flight legacy actions"):
            await asyncio.to_thread(scheduler.activate, goal, idempotency_key="activate")
        assert (await handle.describe()).schedule.state.paused
        with psycopg.connect(database) as connection:
            assert connection.execute("SELECT state FROM v4_schedule_cutovers WHERE goal_id=%s", (goal,)).fetchone()[0] == "pending"
        await client.get_workflow_handle(old_execution.workflow_id).terminate(reason="Disposable no-effects fixture drain")
        async with asyncio.timeout(10):
            while (await handle.describe()).info.running_actions:
                await asyncio.sleep(0.05)
        active=await asyncio.to_thread(scheduler.activate,goal,idempotency_key="activate")
        assert active["state"]=="active"
        assert (await client.get_schedule_handle(remote_id).describe()).schedule.state.paused
        rolled=await asyncio.to_thread(scheduler.rollback,goal,idempotency_key="rollback")
        assert rolled["state"]=="rolled_back"
        description=await client.get_schedule_handle(remote_id).describe()
        assert not description.schedule.state.paused
        assert description.schedule.spec.start_at > anchor-timedelta(seconds=60)
    finally:
        await client.get_schedule_handle(remote_id).delete()


@pytest.mark.asyncio
async def test_real_legacy_worker_exit_after_start_preserves_unknown_without_new_run(
    request, task8_candidate_state
):
    import asyncio
    import hashlib
    import importlib.util
    import json
    import subprocess
    from pathlib import Path

    from temporalio.client import Client

    # Load the database-backed legacy fixture only after the shared exact-image
    # prerequisite gate has passed.
    bridge, command, service, database = request.getfixturevalue("legacy")
    result = bridge.submit(command)
    root = Path(__file__).resolve().parents[2]
    candidate_root = task8_candidate_state["candidate_root"]
    attestation = task8_candidate_state["attestation"]
    control_bundle = task8_candidate_state["control_bundle"]
    tracked_lock = (root / "deployment/v4/publication-worker-launch-lock.json").read_bytes()
    trusted_digest = hashlib.sha256(tracked_lock).hexdigest()
    assert attestation["image_id"]
    assert attestation["image_ref"]
    assert attestation["application_commit"] == json.loads(tracked_lock)["pin"]["application_commit"]
    assert (control_bundle / "publication-worker-launch-lock.json").read_bytes() == tracked_lock
    assert (control_bundle / "publication-worker-launch-lock.sha256").read_text().strip() == trusted_digest

    harness_path = root / "tests/fixtures/v4_publication_floor_worker.py"
    spec = importlib.util.spec_from_file_location("v4_publication_floor_worker", harness_path)
    assert spec is not None and spec.loader is not None
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    harness_image_id = harness.image_id(attestation["image_ref"])
    assert harness_image_id == attestation["image_id"]
    crash_fixture = root / "tests/fixtures/legacy_dispatch_process.py"
    temporal_target = os.environ["TEST_TEMPORAL_TARGET"]

    def start_worker(name, *, crash_after_start=False):
        return harness.start_current_legacy_worker(
            image=attestation["image_ref"],
            database_url=database,
            temporal_target=temporal_target,
            workspace_id=str(service.workspace_id),
            task_queue=bridge.task_queue,
            control_bundle=control_bundle,
            name=name,
            crash_fixture=crash_fixture if crash_after_start else None,
        )

    first_name = "legacy-hard-exit-" + uuid4().hex[:12]
    first_exit = None
    try:
        first = start_worker(first_name, crash_after_start=True)
        assert first["image_id"] == harness.image_id(attestation["image_ref"]) == harness_image_id
        first_inspect = harness.inspect_worker(first_name)
        assert first_inspect["image_id"] == harness_image_id
        assert first_inspect["path"] == "/usr/local/bin/python"
        assert first_inspect["read_only_rootfs"] is True
        first_argv = json.loads(first["launcher_argv"])
        assert first_argv == [
            "/usr/local/bin/python",
            "-I",
            "-B",
            "-S",
            "-c",
            harness._LEGACY_DISPATCH_CRASH_LAUNCH_SHIM,
            "/run/salience-control/launch-v4-publication-worker.py",
            "--mode",
            "current",
            "--worker-kind",
            "legacy-fixture",
        ]
        assert first_inspect["args"] == first_argv[1:]
        first_mounts = {mount["destination"]: mount for mount in first_inspect["mounts"]}
        assert set(first_mounts) == {
            "/run/salience-control/launch-v4-publication-worker.py",
            "/run/salience-control/publication-worker-launch-lock.json",
            "/run/salience-control/publication-worker-launch-lock.sha256",
            "/run/salience-control/legacy-dispatch-crash-fixture.py",
        }
        assert all(not mount["read_write"] for mount in first_mounts.values())
        first_wait = await asyncio.to_thread(
            subprocess.run,
            ["docker", "wait", first_name],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        first_exit = int(first_wait.stdout.strip())
    finally:
        first_logs = harness.stop_worker(first_name)
    assert first_logs["state"] == "exited"
    assert first_exit == 137, first_logs["logs"]

    client = await Client.connect(temporal_target)
    handle = client.get_workflow_handle(
        "salience-v4-legacy-operation:" + result["operation_id"]
    )
    original = (await handle.describe()).run_id

    second_name = "legacy-restart-" + uuid4().hex[:12]
    try:
        second = start_worker(second_name)
        assert second["image_id"] == harness_image_id
        second_inspect = harness.inspect_worker(second_name)
        assert second_inspect["image_id"] == harness_image_id
        assert second_inspect["path"] == "/usr/local/bin/python"
        assert second_inspect["read_only_rootfs"] is True
        assert second_inspect["args"] == [
            "-I",
            "-B",
            "-S",
            "/run/salience-control/launch-v4-publication-worker.py",
            "--mode",
            "current",
            "--worker-kind",
            "legacy-fixture",
        ]
        async with asyncio.timeout(20):
            while (await asyncio.to_thread(bridge.inspect, result["job_id"]))[
                "dispatch_state"
            ] != "unknown":
                await asyncio.sleep(.05)
        assert (await handle.describe()).run_id == original
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT count(*) FROM v4_permit_claims WHERE cycle_id=%s",
                (result["cycle_id"],),
            ).fetchone()[0] == 1
            assert connection.execute(
                "SELECT count(*) FROM jobs WHERE id=%s", (result["job_id"],)
            ).fetchone()[0] == 1
            assert connection.execute(
                "SELECT count(*) FROM v4_cycle_events WHERE cycle_id=%s AND kind='fixture_adapter_accepted'",
                (result["cycle_id"],),
            ).fetchone()[0] == 0
    finally:
        second_logs = harness.stop_worker(second_name)
        assert second_logs["state"] in {"exited", "already-removed"}
        from temporalio.client import WorkflowExecutionStatus

        if (await handle.describe()).status == WorkflowExecutionStatus.RUNNING:
            await handle.terminate(reason="End disposable legacy hard-exit fixture")
