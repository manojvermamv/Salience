"""Task 6 acceptance for the injected production schedule boundary."""

from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from dataclasses import replace
from hashlib import sha256
from importlib import import_module
import os
from types import MethodType
from uuid import UUID, uuid4
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
import pytest
import psycopg.sql


@contextmanager
def _disposable_database():
    """Clone the current migrated test DB and always drop the clone."""
    source = os.environ["TEST_DATABASE_URL"]
    parsed = urlsplit(source)
    template = parsed.path.lstrip("/")
    name = "task6_schedule_" + uuid4().hex[:12]
    database = urlunsplit(parsed._replace(path="/" + name))
    with psycopg.connect(source, autocommit=True) as admin:
        admin.execute(
            psycopg.sql.SQL("CREATE DATABASE {} WITH TEMPLATE {}").format(
                psycopg.sql.Identifier(name), psycopg.sql.Identifier(template)
            )
        )
    try:
        yield database
    finally:
        with psycopg.connect(source, autocommit=True) as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s",
                (name,),
            )
            admin.execute(
                psycopg.sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    psycopg.sql.Identifier(name)
                )
            )


@contextmanager
def _disposable_0048_database(prefix="task6_inventory_"):
    """Create and drop a clean 0048 schema with no adopted native schedules."""
    migration_tests = import_module("test_v4_migrations")
    source = os.environ["TEST_DATABASE_URL"]
    name, database, *_ = migration_tests._prepare_0048_database(prefix)
    try:
        migration_tests._upgrade_0048_or_fail(database)
        yield database
    finally:
        migration_tests._drop_migration_database(source, name)


def _route_control(
    target,
    database_url=None,
    *,
    workspace_id=UUID("00000000-0000-0000-0000-000000000615"),
    route_owner_id=UUID("00000000-0000-0000-0000-000000000612"),
):
    from salience.cycles.production_routing import (
        ProductionRouteManifestV1,
        RouteLeaseClaim,
        RouteTupleV1,
        route_manifest_sha256,
        route_tuple_sha256,
    )
    from support.production_route_authority import FakeRouteLeaseAuthority

    route_id = UUID("00000000-0000-0000-0000-000000000610")
    slot_id = UUID("00000000-0000-0000-0000-000000000611")
    route_tuple = RouteTupleV1(
        deployment_id=UUID("00000000-0000-0000-0000-000000000613"),
        environment_id=UUID("00000000-0000-0000-0000-000000000614"),
        temporal_endpoint_id="task-6-target",
        namespace="task-6-namespace",
        task_queue="task-6-production-queue",
        worker_kind="production-publication",
        workflow_types=("GovernedPublicationWorkflow",),
        activity_types=("salience.publication.delivery",),
        producer_bindings=(),
        compatibility_mode="exact-head",
    )
    route = ProductionRouteManifestV1(
        route_id=route_id,
        route_tuple=route_tuple,
        route_tuple_sha256=route_tuple_sha256(route_tuple),
        worker_build_id="task-6-build",
        slots=(slot_id,),
        artifact_sha256="e" * 64,
        image_sha256="f" * 64,
        artifact_lock_sha256="b" * 64,
    )
    authority = FakeRouteLeaseAuthority()
    lease = authority.claim_slot(
        RouteLeaseClaim(
            route_id=route_id,
            route_manifest_sha256=route_manifest_sha256(route),
            artifact_lock_sha256=route.artifact_lock_sha256,
            slot_id=slot_id,
            owner_id=route_owner_id,
            build_id=route.worker_build_id,
        )
    )
    module = import_module("salience.cycles.production_schedule_control")
    control = module.ProductionTemporalScheduleControl(
        database_url or os.environ["TEST_DATABASE_URL"],
        workspace_id=workspace_id,
        target_alias="task-6-target",
        namespace="task-6-namespace",
        route=route,
        route_lease=lease,
        route_authority=authority,
        target=target,
        alerts=_AlertSink(),
    )
    return control, route, lease, authority


@contextmanager
def _production_enrollment_fixture(prefix="task6_enroll_"):
    """Build a disposable 0048 database with live 0047 authorization facts."""
    migration_tests = import_module("test_v4_migrations")
    source = os.environ["TEST_DATABASE_URL"]
    name, database, _, _, authority = migration_tests._prepare_0048_database(prefix)
    try:
        migration_tests._upgrade_0048_or_fail(database)
        module = import_module("salience.cycles.production_schedule_control")
        workspace_id = authority["workspace_id"]
        operator_id = authority["subject_id"]
        owner_id = uuid4()
        goal_id = uuid4()
        schedule_id = uuid4()
        intent_id = uuid4()
        first_slot = datetime(2099, 1, 1, tzinfo=timezone.utc)
        with psycopg.connect(database, row_factory=dict_row) as connection:
            connection.execute(
                """INSERT INTO identity_subjects(
                       id,workspace_id,issuer,subject,expires_at)
                   VALUES(%s,%s,'https://task6.invalid',%s,
                          clock_timestamp()+interval '1 day')""",
                (owner_id, workspace_id, "source-owner:" + str(owner_id)),
            )
            for scope in ("cycles:schedule", "cycles:write", "cycles:stop"):
                connection.execute(
                    """INSERT INTO permission_grants(
                           workspace_id,principal_type,principal_id,scope,effect,
                           constraints,expires_at)
                       VALUES(%s,'identity',%s,%s,'allow','{}',
                              clock_timestamp()+interval '1 day')""",
                    (workspace_id, str(operator_id), scope),
                )
            from salience.cycles.contracts import GoalSpec

            goal_payload = GoalSpec(
                objective="Task 6 enrollment acceptance",
                metric_versions=("task6.metric.v1",),
                audience="local test audience",
                account_refs=("fixture-account",),
                brand_scope="fixture-only",
                source_policy="fixture-only",
                horizon_end=first_slot,
                cadence_seconds=60,
            ).model_dump(mode="json")
            connection.execute(
                "INSERT INTO v4_goals(id,workspace_id,state) VALUES(%s,%s,'active')",
                (goal_id, workspace_id),
            )
            connection.execute(
                """INSERT INTO v4_goal_revisions(goal_id,revision,payload)
                   VALUES(%s,1,%s)""",
                (goal_id, Jsonb(goal_payload)),
            )
            connection.execute(
                """INSERT INTO v4_cycle_intents(
                       id,goal_id,goal_revision,slot,fingerprint,due_at,expires_at)
                   VALUES(%s,%s,1,%s,%s,clock_timestamp()-interval '1 minute',
                          clock_timestamp()+interval '1 day')""",
                (
                    intent_id,
                    goal_id,
                    "task6-slot-" + str(intent_id),
                    sha256(str(intent_id).encode()).hexdigest(),
                ),
            )
            connection.execute(
                """INSERT INTO v4_stop_scopes(
                       workspace_id,scope_key,revision,stopped,changed_by,reason)
                   VALUES(%s,%s,1,false,%s,'active task6 fixture')""",
                (workspace_id, "goal:" + str(goal_id), operator_id),
            )
            connection.execute(
                """INSERT INTO job_schedules(
                       id,workspace_id,content_program_id,name,schedule_expression,
                       timezone,job_type,payload,next_run_at,status)
                   VALUES(%s,%s,%s,%s,'every 60s','UTC','intelligence_research',
                          %s,%s,'active')""",
                (
                    schedule_id,
                    workspace_id,
                    authority["cycle"]["program_id"],
                    "task6-enrollment-" + str(schedule_id),
                    Jsonb({"dry_run": True, "niche": "Task 6 enrollment source"}),
                    first_slot,
                ),
            )
            original_identity = connection.execute(
                """SELECT to_jsonb(schedule)-ARRAY['status','next_run_at','updated_at']
                          AS identity
                   FROM job_schedules schedule WHERE id=%s""",
                (schedule_id,),
            ).fetchone()["identity"]
            connection.execute("SET session_replication_role = replica")
            connection.execute(
                """INSERT INTO v4_native_schedule_sources(
                       schedule_id,workspace_id,goal_id,actor_id,goal_revision,
                       remote_id,task_queue,interval_seconds,first_v4_slot,
                       observed_last_slot,remote_snapshot,original_identity)
                   VALUES(%s,%s,%s,%s,1,%s,'salience-v4-local-legacy-task6',60,
                          %s,NULL,%s,%s)""",
                (
                    schedule_id,
                    workspace_id,
                    goal_id,
                    operator_id,
                    str(schedule_id),
                    first_slot,
                    Jsonb({"action_count": 0}),
                    Jsonb(original_identity),
                ),
            )
            connection.execute(
                """INSERT INTO v4_native_schedule_progress(
                       schedule_id,last_slot,resume_after,next_slot)
                   VALUES(%s,NULL,NULL,%s)""",
                (schedule_id, first_slot),
            )
            connection.execute("SET session_replication_role = origin")

        target = _PagedTarget()
        control, route, lease, route_authority = _route_control(
            target,
            database,
            workspace_id=workspace_id,
            route_owner_id=operator_id,
        )
        remote_schedule_id = str(schedule_id)
        descriptor = module.TemporalScheduleDescriptor(
            remote_schedule_id=remote_schedule_id,
            classification="bound",
            owner_ref="task6-owner:" + str(owner_id),
            exclusion_reason=None,
            workflow_type=route.route_tuple.workflow_types[0],
            action_id="task6-action:" + str(schedule_id),
            argument_sha256="a" * 64,
            task_queue=route.route_tuple.task_queue,
            route_id=route.route_id,
            route_tuple_sha256=route.route_tuple_sha256,
            slot_id=lease.slot_id,
            owner_id=lease.owner_id,
            fencing_epoch=lease.fencing_epoch,
            route_manifest_sha256=lease.route_manifest_sha256,
            artifact_lock_sha256=lease.artifact_lock_sha256,
            cadence_policy_revision="task6-cadence-v1",
            interval_seconds=60,
            first_v4_slot=first_slot,
            remote_state="running",
            next_action=first_slot,
            running_action_count=0,
            source_kind="intelligence_research",
            schedule_id=schedule_id,
            publication_schedule_id=None,
            workspace_id=workspace_id,
            goal_id=goal_id,
            goal_revision=1,
            source_actor_id=operator_id,
            source_owner_subject_id=owner_id,
            schedule_operator_subject_id=operator_id,
        )
        control.target = _PagedTarget((descriptor,))
        inventory_id = uuid4()
        control.capture_inventory(
            inventory_id=inventory_id,
            source_tool="task6-deterministic-fake",
            source_tool_version="1",
            candidate_sha256="c" * 64,
            configuration_sha256="d" * 64,
        )
        with psycopg.connect(database, row_factory=dict_row) as connection:
            inventory = connection.execute(
                "SELECT * FROM v4_production_schedule_inventories WHERE inventory_id=%s",
                (inventory_id,),
            ).fetchone()
            member = connection.execute(
                """SELECT member_snapshot FROM v4_production_schedule_inventory_members
                   WHERE inventory_id=%s AND remote_schedule_id=%s""",
                (inventory_id, remote_schedule_id),
            ).fetchone()
            facts = {
                "source_kind": "intelligence_research",
                "schedule_id": schedule_id,
                "publication_schedule_id": None,
                "workspace_id": workspace_id,
                "goal_id": goal_id,
                "goal_revision": 1,
                "source_actor_id": operator_id,
                "source_owner_subject_id": owner_id,
                "schedule_operator_subject_id": operator_id,
            }
            approval_id, _ = migration_tests._insert_0048_schedule_approval(
                connection,
                inventory | {"remote_schedule_id": remote_schedule_id},
                facts,
                member["member_snapshot"],
            )
        control.finalize_inventory(inventory_id)
        binding = control.binding_for_schedule(schedule_id)
        with psycopg.connect(database, row_factory=dict_row) as connection:
            policy = connection.execute(
                "SELECT policy_version_id FROM approval_requests WHERE id=%s",
                (approval_id,),
            ).fetchone()
        authority = authority | {"task6_policy_id": policy["policy_version_id"]}
        key = module.ProductionIntentKey(intent_id, 1)
        yield database, control, binding, key, authority, route_authority
    finally:
        migration_tests._drop_migration_database(source, name)


@contextmanager
def _production_cutover_fixture(prefix="task6_lifecycle_"):
    """Use the real source helper and cutover machine with a V2 fixture goal."""
    module = import_module("salience.cycles.production_schedule_control")
    from salience.cycles.contracts import CadencePolicy, GoalSpec, GoalSpecV2

    with _production_enrollment_fixture(prefix) as (
        database,
        control,
        binding,
        _key,
        authority,
        _route_authority,
    ):
        with psycopg.connect(database, row_factory=dict_row) as connection:
            payload = connection.execute(
                "SELECT payload FROM v4_goal_revisions WHERE goal_id=%s AND revision=%s",
                (binding.goal_id, binding.goal_revision),
            ).fetchone()["payload"]
            spec = GoalSpecV2.model_validate(
                GoalSpec.model_validate(payload).model_dump()
                | {
                    "schema_version": "GoalSpec.local.v2",
                    "horizon_end": binding.first_v4_slot + timedelta(days=1),
                    "cadence_seconds": None,
                    "content_program_id": authority["cycle"]["program_id"],
                    "channel_refs": ("fixture-channel",),
                    "content_scope": "fixture-only",
                    "policy_refs": ("task6-lifecycle-policy-v1",),
                    "retention_policy": "task6-lifecycle-fixture",
                    "cadence": CadencePolicy(
                        anchor=binding.first_v4_slot
                        - timedelta(seconds=binding.interval_seconds),
                        interval_seconds=binding.interval_seconds,
                    ),
                }
            )
            connection.execute("SET session_replication_role = replica")
            connection.execute(
                "UPDATE v4_goal_revisions SET payload=%s WHERE goal_id=%s AND revision=%s",
                (Jsonb(spec.model_dump(mode="json")), binding.goal_id, binding.goal_revision),
            )
            connection.execute("SET session_replication_role = origin")

        target = _MutationTarget(_readback(module, binding))
        control.target = target
        production = module.ProductionScheduleCutover(
            database, schedule_control=control, binding=binding
        )
        prepared = production.prepare(
            binding.goal_id,
            legacy_schedule_id=binding.schedule_id,
            expected_revision=binding.goal_revision,
            first_v4_slot=binding.first_v4_slot,
            idempotency_key="task6-lifecycle-prepare",
        )
        yield database, control, binding, target, production, prepared


class _AlertSink:
    def __init__(self):
        self.events = []

    def emit_hold(self, workspace_id, source_id, reason_code, evidence_sha256):
        self.events.append((workspace_id, source_id, reason_code, evidence_sha256))


class _PagedTarget:
    def __init__(self, descriptors=(), *, history=None, broken=None):
        module = import_module("salience.cycles.production_schedule_control")
        self.inventory_pages = (
            module.TemporalScheduleInventoryPage((), "inventory-next"),
            module.TemporalScheduleInventoryPage(tuple(descriptors), None),
        )
        self.history = history or {}
        self.broken = broken
        self.list_calls = []
        self.history_calls = []

    def list_page(self, page_token):
        self.list_calls.append(page_token)
        if self.broken == "inventory_cursor":
            return import_module(
                "salience.cycles.production_schedule_control"
            ).TemporalScheduleInventoryPage((), "inventory-next")
        return self.inventory_pages[0 if page_token is None else 1]

    def history_page(self, remote_schedule_id, page_token):
        self.history_calls.append((remote_schedule_id, page_token))
        if self.broken == "history_order":
            from salience.cycles.production_schedule_control import (
                TemporalScheduleAction,
                TemporalScheduleHistoryPage,
            )

            if page_token is None:
                action = TemporalScheduleAction(
                    action_id_sha256="f" * 64,
                    scheduled_slot=datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc),
                    started_at=None,
                    completed_at=None,
                    outcome_class="completed",
                    workflow_id_sha256="a" * 64,
                )
                return TemporalScheduleHistoryPage((action,), "history-next")
            action = TemporalScheduleAction(
                action_id_sha256="a" * 64,
                scheduled_slot=datetime(2026, 10, 28, 11, 0, tzinfo=timezone.utc),
                started_at=None,
                completed_at=None,
                outcome_class="completed",
                workflow_id_sha256="b" * 64,
            )
            return TemporalScheduleHistoryPage((action,), None)
        pages = self.history.get(remote_schedule_id)
        if isinstance(pages, (list, tuple)):
            return pages[0 if page_token is None else 1]
        return pages or import_module(
            "salience.cycles.production_schedule_control"
        ).TemporalScheduleHistoryPage((), None)

    def describe(self, remote_schedule_id):
        raise AssertionError("inventory capture must not need a remote describe")

    def pause(self, remote_schedule_id, command_key):
        raise AssertionError("inventory capture must not pause a schedule")

    def resume_after(self, remote_schedule_id, command_key, start_at):
        raise AssertionError("inventory capture must not resume a schedule")


def _out_of_scope_descriptor(remote_id="outside-scope"):
    module = import_module("salience.cycles.production_schedule_control")
    _, route, lease, _ = _route_control(_PagedTarget())
    return module.TemporalScheduleDescriptor(
        remote_schedule_id=remote_id,
        classification="out_of_scope",
        owner_ref="external-owner-ref",
        exclusion_reason="not-a-canonical-salience-source",
        workflow_type="UnknownWorkflow",
        action_id="unknown-action",
        argument_sha256="a" * 64,
        task_queue=route.route_tuple.task_queue,
        route_id=route.route_id,
        route_tuple_sha256=route.route_tuple_sha256,
        slot_id=lease.slot_id,
        owner_id=lease.owner_id,
        fencing_epoch=lease.fencing_epoch,
        route_manifest_sha256=lease.route_manifest_sha256,
        artifact_lock_sha256=lease.artifact_lock_sha256,
        cadence_policy_revision="opaque-cadence-v1",
        interval_seconds=60,
        first_v4_slot=datetime.now(timezone.utc).replace(microsecond=0)
        + timedelta(hours=1),
        remote_state="running",
        next_action=datetime.now(timezone.utc).replace(microsecond=0)
        + timedelta(hours=1),
        running_action_count=0,
    )


def _bound_binding(module, route, lease):
    workspace_id = UUID("00000000-0000-0000-0000-000000000615")
    schedule_id = UUID("00000000-0000-0000-0000-000000000616")
    goal_id = UUID("00000000-0000-0000-0000-000000000617")
    owner_id = UUID("00000000-0000-0000-0000-000000000618")
    operator_id = UUID("00000000-0000-0000-0000-000000000619")
    source_actor_id = UUID("00000000-0000-0000-0000-000000000620")
    first = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
    snapshot = {
        "schema_version": "salience.p1-production-schedule-inventory-member.v1",
        "target_alias": "task-6-target",
        "namespace": "task-6-namespace",
        "remote_schedule_id": "native-remote-616",
        "classification": "bound",
        "source_kind": "intelligence_research",
        "schedule_id": str(schedule_id),
        "publication_schedule_id": None,
        "workspace_id": str(workspace_id),
        "goal_id": str(goal_id),
        "goal_revision": 1,
        "source_actor_id": str(source_actor_id),
        "source_owner_subject_id": str(owner_id),
        "schedule_operator_subject_id": str(operator_id),
        "owner_ref": "owner-ref-616",
        "exclusion_reason": None,
        "workflow_type": "GovernedPublicationWorkflow",
        "action_id": "native-action-616",
        "argument_sha256": "a" * 64,
        "task_queue": route.route_tuple.task_queue,
        "route_id": str(route.route_id),
        "route_tuple_sha256": route.route_tuple_sha256,
        "slot_id": str(lease.slot_id),
        "owner_id": str(lease.owner_id),
        "fencing_epoch": lease.fencing_epoch,
        "route_manifest_sha256": lease.route_manifest_sha256,
        "artifact_lock_sha256": lease.artifact_lock_sha256,
        "cadence_policy_revision": "cadence-v1",
        "interval_seconds": 60,
        "first_v4_slot": first.isoformat(),
        "observed_last_slot": None,
        "remote_state": "running",
        "next_action": first.isoformat(),
        "running_action_count": 0,
        "history_page_count": 1,
        "history_action_count": 0,
        "history_page_chain_sha256": "b" * 64,
        "history_action_set_sha256": "c" * 64,
        "history_watermark": None,
        "history_watermark_sha256": None,
    }
    return module.ProductionScheduleBinding(
        inventory_id=UUID("00000000-0000-0000-0000-000000000621"),
        inventory_sha256="d" * 64,
        target_alias="task-6-target",
        namespace="task-6-namespace",
        remote_schedule_id="native-remote-616",
        source_approval_id=UUID("00000000-0000-0000-0000-000000000622"),
        member_sha256="e" * 64,
        binding_fingerprint="f" * 64,
        source_kind="intelligence_research",
        schedule_id=schedule_id,
        publication_schedule_id=None,
        workspace_id=workspace_id,
        goal_id=goal_id,
        goal_revision=1,
        source_actor_id=source_actor_id,
        source_owner_subject_id=owner_id,
        schedule_operator_subject_id=operator_id,
        route_id=route.route_id,
        route_tuple_sha256=route.route_tuple_sha256,
        slot_id=lease.slot_id,
        owner_id=lease.owner_id,
        fencing_epoch=lease.fencing_epoch,
        route_manifest_sha256=lease.route_manifest_sha256,
        artifact_lock_sha256=lease.artifact_lock_sha256,
        interval_seconds=60,
        first_v4_slot=first,
        member_snapshot=snapshot,
    )


class _MutationTarget:
    def __init__(self, readback, *, behavior=None):
        self.readback = readback
        self.behavior = behavior
        self.pause_calls = []
        self.resume_calls = []
        self.describe_calls = []

    def list_page(self, page_token):
        return import_module(
            "salience.cycles.production_schedule_control"
        ).TemporalScheduleInventoryPage((), None)

    def history_page(self, remote_schedule_id, page_token):
        return import_module(
            "salience.cycles.production_schedule_control"
        ).TemporalScheduleHistoryPage((), None)

    def describe(self, remote_schedule_id):
        self.describe_calls.append(remote_schedule_id)
        if remote_schedule_id != self.readback.remote_schedule_id:
            raise LookupError("unknown remote schedule")
        return self.readback

    def pause(self, remote_schedule_id, command_key):
        self.pause_calls.append((remote_schedule_id, command_key))
        self.readback = replace(self.readback, paused=True)
        if self.behavior == "lost_pause_ack":
            raise TimeoutError("pause committed; acknowledgment lost")

    def resume_after(self, remote_schedule_id, command_key, start_at):
        self.resume_calls.append((remote_schedule_id, command_key, start_at))
        if self.behavior == "lost_resume_ack":
            self.readback = replace(
                self.readback,
                paused=False,
                resume_after=start_at,
                next_action=start_at + timedelta(seconds=self.readback.interval_seconds),
            )
            raise TimeoutError("resume committed; acknowledgment lost")
        if self.behavior == "early_resume":
            self.readback = replace(
                self.readback,
                paused=False,
                resume_after=start_at,
                next_action=start_at,
            )
            return
        self.readback = replace(
            self.readback,
            paused=False,
            resume_after=start_at,
            next_action=start_at + timedelta(seconds=self.readback.interval_seconds),
        )


def _readback(module, binding, *, paused=False, running=0, overrides=None):
    snapshot = binding.member_snapshot
    values = {
        "remote_schedule_id": binding.remote_schedule_id,
        "target_alias": binding.target_alias,
        "namespace": binding.namespace,
        "workflow_type": snapshot["workflow_type"],
        "action_id": snapshot["action_id"],
        "argument_sha256": snapshot["argument_sha256"],
        "task_queue": snapshot["task_queue"],
        "route_id": binding.route_id,
        "route_tuple_sha256": binding.route_tuple_sha256,
        "slot_id": binding.slot_id,
        "owner_id": binding.owner_id,
        "fencing_epoch": binding.fencing_epoch,
        "route_manifest_sha256": binding.route_manifest_sha256,
        "artifact_lock_sha256": binding.artifact_lock_sha256,
        "cadence_policy_revision": snapshot["cadence_policy_revision"],
        "interval_seconds": binding.interval_seconds,
        "first_v4_slot": binding.first_v4_slot,
        "paused": paused,
        "next_action": binding.first_v4_slot,
        "last_slot": None,
        "resume_after": None,
        "running_action_count": running,
    }
    if overrides:
        values.update(overrides)
    return module.TemporalScheduleReadback(**values)


def _mutation_control(monkeypatch, *, paused=False, running=0, behavior=None, overrides=None):
    module = import_module("salience.cycles.production_schedule_control")
    target = _MutationTarget(
        _readback(module, _bound_binding(module, *_route_control(_PagedTarget())[1:3]),
                  paused=paused, running=running, overrides=overrides),
        behavior=behavior,
    )
    control, route, lease, authority = _route_control(target)
    binding = _bound_binding(module, route, lease)
    target.readback = _readback(
        module, binding, paused=paused, running=running, overrides=overrides
    )
    actions = []

    def binding_lookup(schedule_id):
        assert UUID(str(schedule_id)) == binding.schedule_id
        return binding

    def assert_binding(candidate, *, action_kind):
        assert candidate == binding
        actions.append(action_kind)

    control.binding_for_schedule = binding_lookup
    control.assert_binding = assert_binding
    control._assert_current_action_source = lambda *_, **__: None
    return module, control, binding, target, route, lease, authority, actions


def _bound_descriptor(module, binding):
    snapshot = binding.member_snapshot
    return module.TemporalScheduleDescriptor(
        remote_schedule_id=binding.remote_schedule_id,
        classification="bound",
        owner_ref=snapshot["owner_ref"],
        exclusion_reason=None,
        workflow_type=snapshot["workflow_type"],
        action_id=snapshot["action_id"],
        argument_sha256=snapshot["argument_sha256"],
        task_queue=snapshot["task_queue"],
        route_id=binding.route_id,
        route_tuple_sha256=binding.route_tuple_sha256,
        slot_id=binding.slot_id,
        owner_id=binding.owner_id,
        fencing_epoch=binding.fencing_epoch,
        route_manifest_sha256=binding.route_manifest_sha256,
        artifact_lock_sha256=binding.artifact_lock_sha256,
        cadence_policy_revision=snapshot["cadence_policy_revision"],
        interval_seconds=binding.interval_seconds,
        first_v4_slot=binding.first_v4_slot,
        remote_state="running",
        next_action=binding.first_v4_slot,
        running_action_count=0,
        source_kind=binding.source_kind,
        schedule_id=binding.schedule_id,
        publication_schedule_id=binding.publication_schedule_id,
        workspace_id=binding.workspace_id,
        goal_id=binding.goal_id,
        goal_revision=binding.goal_revision,
        source_actor_id=binding.source_actor_id,
        source_owner_subject_id=binding.source_owner_subject_id,
        schedule_operator_subject_id=binding.schedule_operator_subject_id,
    )


def _cleanup_inventory(database, inventory_id):
    with psycopg.connect(database) as connection:
        connection.execute(
            "DELETE FROM public.v4_production_schedule_inventory_pages WHERE inventory_id=%s",
            (inventory_id,),
        )
        connection.execute(
            "DELETE FROM public.v4_production_schedule_inventory_members WHERE inventory_id=%s",
            (inventory_id,),
        )
        connection.execute(
            "DELETE FROM public.v4_production_schedule_inventories WHERE inventory_id=%s",
            (inventory_id,),
        )


def test_production_inventory_writer_persists_listing_and_history_receipts():
    descriptor = _out_of_scope_descriptor()
    target = _PagedTarget((descriptor,))
    control, _, _, _ = _route_control(target)
    inventory_id = uuid4()
    database = os.environ["TEST_DATABASE_URL"]
    try:
        result = control.capture_inventory(
            inventory_id=inventory_id,
            source_tool="task-6-test-fake",
            source_tool_version="1",
            candidate_sha256="c" * 64,
            configuration_sha256="d" * 64,
        )
        assert (result.page_count, result.member_count, result.out_of_scope_member_count) == (2, 1, 1)
        with psycopg.connect(database, row_factory=dict_row) as connection:
            header = connection.execute(
                "SELECT inventory_sha256,finalized_at FROM v4_production_schedule_inventories WHERE inventory_id=%s",
                (inventory_id,),
            ).fetchone()
            listing = connection.execute(
                "SELECT page_index,request_cursor_sha256,next_cursor_sha256,is_terminal,item_count,page_items "
                "FROM v4_production_schedule_inventory_pages WHERE inventory_id=%s AND remote_schedule_id IS NULL ORDER BY page_index",
                (inventory_id,),
            ).fetchall()
            member = connection.execute(
                "SELECT classification,history_page_count,history_action_count,history_watermark "
                "FROM v4_production_schedule_inventory_members WHERE inventory_id=%s",
                (inventory_id,),
            ).fetchone()
            pages = connection.execute(
                "SELECT remote_schedule_id,page_index,page_items,request_cursor_sha256,is_terminal "
                "FROM v4_production_schedule_inventory_pages WHERE inventory_id=%s ORDER BY remote_schedule_id NULLS FIRST,page_index",
                (inventory_id,),
            ).fetchall()
        assert header["inventory_sha256"] == result.inventory_sha256 and header["finalized_at"] is None
        assert len(listing) == 2 and listing[0]["item_count"] == 0
        assert listing[0]["next_cursor_sha256"] == listing[1]["request_cursor_sha256"]
        assert listing[0]["is_terminal"] is False and listing[1]["is_terminal"] is True
        assert listing[1]["page_items"] == [{"remote_schedule_id": descriptor.remote_schedule_id,
                                             "member_sha256": listing[1]["page_items"][0]["member_sha256"]}]
        assert member == {
            "classification": "out_of_scope",
            "history_page_count": 1,
            "history_action_count": 0,
            "history_watermark": None,
        }
        history_rows = [page for page in pages if page["remote_schedule_id"] is not None]
        assert len(history_rows) == 1 and history_rows[0]["page_items"] == []
        assert history_rows[0]["is_terminal"] is True
        assert target.history_calls == [(descriptor.remote_schedule_id, None)]
    finally:
        _cleanup_inventory(database, inventory_id)


def test_production_inventory_writer_rejects_incomplete_or_mismatched_roots_before_finalize():
    descriptor = _out_of_scope_descriptor("broken-inventory")
    database = os.environ["TEST_DATABASE_URL"]
    inventory_id = uuid4()
    target = _PagedTarget((descriptor,), broken="inventory_cursor")
    control, _, _, _ = _route_control(target)
    with pytest.raises(ValueError, match="cursor repeated"):
        control.capture_inventory(
            inventory_id=inventory_id,
            source_tool="task-6-test-fake",
            source_tool_version="1",
            candidate_sha256="c" * 64,
            configuration_sha256="d" * 64,
        )
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT count(*) FROM v4_production_schedule_inventories WHERE inventory_id=%s",
            (inventory_id,),
        ).fetchone()[0] == 0

    complete_id = uuid4()
    complete_target = _PagedTarget((descriptor,), broken="history_order")
    complete_control, _, _, _ = _route_control(complete_target)
    with pytest.raises(ValueError, match="history must be ordered"):
        complete_control.capture_inventory(
            inventory_id=complete_id,
            source_tool="task-6-test-fake",
            source_tool_version="1",
            candidate_sha256="c" * 64,
            configuration_sha256="d" * 64,
        )
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT count(*) FROM v4_production_schedule_inventories WHERE inventory_id=%s",
            (complete_id,),
        ).fetchone()[0] == 0


def test_complete_out_of_scope_inventory_is_finalized_by_the_sql_verifier():
    descriptor = _out_of_scope_descriptor("inventory-to-finalize")
    target = _PagedTarget((descriptor,))
    inventory_id = uuid4()
    with _disposable_0048_database() as database:
        control, _, _, _ = _route_control(target, database)
        receipt = control.capture_inventory(
            inventory_id=inventory_id,
            source_tool="task-6-test-fake",
            source_tool_version="1",
            candidate_sha256="c" * 64,
            configuration_sha256="d" * 64,
        )
        with psycopg.connect(database) as connection:
            roots = connection.execute(
                """SELECT inventory.inventory_sha256,
                          public.v4_production_schedule_jsonb_sha256_v1(
                            jsonb_build_object(
                              'schema_version','salience.p1-production-schedule-inventory.v1',
                              'inventory_id',inventory.inventory_id,
                              'target_alias',inventory.target_alias,
                              'namespace',inventory.namespace,
                              'captured_at',inventory.captured_at,
                              'source_tool',inventory.source_tool,
                              'source_tool_version',inventory.source_tool_version,
                              'candidate_sha256',inventory.candidate_sha256,
                              'configuration_sha256',inventory.configuration_sha256,
                              'page_count',inventory.page_count,
                              'remote_member_count',inventory.remote_member_count,
                              'bound_member_count',inventory.bound_member_count,
                              'out_of_scope_member_count',inventory.out_of_scope_member_count,
                              'inventory_page_chain_sha256',inventory.inventory_page_chain_sha256,
                              'member_set_sha256',inventory.member_set_sha256)) AS expected,
                          to_jsonb(inventory.captured_at) AS sql_capture
                   FROM v4_production_schedule_inventories inventory
                   WHERE inventory.inventory_id=%s""",
                (inventory_id,),
            ).fetchone()
        assert roots[0] == roots[1], roots
        finalized = control.finalize_inventory(inventory_id)
        assert finalized.inventory_sha256 == receipt.inventory_sha256
        assert finalized.finalized_at is not None
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT finalized_at IS NOT NULL FROM v4_production_schedule_inventories WHERE inventory_id=%s",
                (inventory_id,),
            ).fetchone()[0] is True


def test_schedule_controller_has_no_effect_or_schedule_creation_authority():
    control = import_module("salience.cycles.production_schedule_control")

    assert all(
        callable(getattr(control.TemporalScheduleTarget, method, None))
        for method in ("list_page", "history_page", "describe", "pause", "resume_after")
    )
    assert not any(
        hasattr(control.TemporalScheduleTarget, method)
        for method in ("create", "update", "set_cadence", "start_workflow", "publish")
    )


def test_live_inventory_requires_complete_target_scoped_two_sided_binding():
    from support.production_route_authority import FakeRouteLeaseAuthority

    module = import_module("salience.cycles.production_schedule_control")
    empty_target = _PagedTarget()
    control, route, lease, _ = _route_control(empty_target)
    binding = _bound_binding(module, route, lease)
    target = _PagedTarget((_bound_descriptor(module, binding),))
    control, _, _, _ = _route_control(target)
    inventory_id = uuid4()
    with _disposable_database() as database:
        control.database_url = database
        with pytest.raises(PermissionError, match="one current canonical source"):
            control.capture_inventory(
                inventory_id=inventory_id,
                source_tool="task-6-test-fake",
                source_tool_version="1",
                candidate_sha256="c" * 64,
                configuration_sha256="d" * 64,
            )
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT count(*) FROM v4_production_schedule_inventories WHERE inventory_id=%s",
                (inventory_id,),
            ).fetchone()[0] == 0
    with pytest.raises(PermissionError, match="target and namespace"):
        module.ProductionTemporalScheduleControl(
            os.environ["TEST_DATABASE_URL"],
            workspace_id=binding.workspace_id,
            target_alias="other-target",
            namespace=binding.namespace,
            route=route,
            route_lease=lease,
            route_authority=FakeRouteLeaseAuthority(),
            target=empty_target,
            alerts=_AlertSink(),
        )


def test_production_binding_does_not_relax_fixture_target_guards():
    module = import_module("salience.cycles.production_schedule_control")
    from salience.cycles.schedule_cutover import FixtureLegacyScheduleControl
    from salience.cycles.temporal_schedule_control import TemporalFixtureLegacyScheduleControl

    assert issubclass(TemporalFixtureLegacyScheduleControl, FixtureLegacyScheduleControl)
    assert TemporalFixtureLegacyScheduleControl.supports_temporal is True
    with pytest.raises(ValueError, match="isolated legacy schedule queue"):
        TemporalFixtureLegacyScheduleControl(
            os.environ["TEST_DATABASE_URL"],
            workspace_id=UUID("00000000-0000-0000-0000-000000000615"),
            temporal_target="unused-fixture-target",
            task_queue="production-queue-must-not-pass",
        )
    assert module.ProductionTemporalScheduleControl.supports_temporal is True


def test_pending_requires_remote_pause_readback_and_drained_actions(monkeypatch):
    module, control, binding, target, *_ = _mutation_control(
        monkeypatch, running=1
    )
    fixture_module = import_module("salience.cycles.schedule_cutover")
    monkeypatch.setattr(fixture_module.FixtureLegacyScheduleControl, "pause", lambda *_: None)
    with pytest.raises(PermissionError, match="drained actions"):
        control.pause(binding.schedule_id)
    assert target.pause_calls == [(binding.remote_schedule_id, target.pause_calls[0][1])]
    assert target.describe_calls == [
        binding.remote_schedule_id,
        binding.remote_schedule_id,
    ]
    assert control.alerts.events[-1][2] == "schedule_pause_not_drained"


def test_pause_rejects_pre_read_action_route_drift_before_remote_mutation(monkeypatch):
    module, control, binding, target, *_ = _mutation_control(
        monkeypatch, overrides={"action_id": "out-of-band-action"}
    )
    with pytest.raises(PermissionError):
        control.pause(binding.schedule_id)
    assert target.pause_calls == []
    assert target.describe_calls == [binding.remote_schedule_id]


def test_lost_pause_ack_or_source_readback_reconciles_one_exact_schedule(monkeypatch):
    module, control, binding, target, *_ = _mutation_control(
        monkeypatch, behavior="lost_pause_ack"
    )
    fixture_module = import_module("salience.cycles.schedule_cutover")
    monkeypatch.setattr(fixture_module.FixtureLegacyScheduleControl, "pause", lambda *_: None)
    control.pause(binding.schedule_id)
    assert target.pause_calls and target.pause_calls[0][0] == binding.remote_schedule_id
    assert target.describe_calls == [
        binding.remote_schedule_id,
        binding.remote_schedule_id,
    ]
    assert control.alerts.events == []

    module, control, binding, target, *_ = _mutation_control(
        monkeypatch, overrides={"action_id": "out-of-band-action"}
    )
    with pytest.raises(PermissionError, match="readback"):
        control.describe(binding.schedule_id)
    assert target.describe_calls == [binding.remote_schedule_id]
    assert control.alerts.events[-1][2] == "schedule_source_or_route_drift"


def test_manual_event_and_scheduled_same_slot_coalesce_after_restart(monkeypatch):
    module = import_module("salience.cycles.production_schedule_cutover")
    fixture_module = import_module("salience.cycles.schedule_cutover")
    assert issubclass(module._ProductionCycleScheduleCutover, fixture_module.CycleScheduleCutover)
    durable = {}
    calls = []

    def durable_poll(self, goal_id, *, expected_revision, idempotency_key):
        calls.append((str(goal_id), expected_revision, idempotency_key))
        identity = (str(goal_id), expected_revision, idempotency_key)
        return durable.setdefault(
            identity,
            {
                "state": "active",
                "intent_ids": ["one-canonical-slot-intent"],
                "admissions": [],
            },
        )

    monkeypatch.setattr(module._ProductionCycleScheduleCutover, "poll", durable_poll)
    _, control, binding, *_ = _mutation_control(monkeypatch)
    control.describe = lambda *_: {"paused": True}
    control.assert_binding = lambda *_, **__: None
    first = module.ProductionScheduleCutover(
        os.environ["TEST_DATABASE_URL"], schedule_control=control, binding=binding
    )
    second = module.ProductionScheduleCutover(
        os.environ["TEST_DATABASE_URL"], schedule_control=control, binding=binding
    )
    expected = first.poll(
        binding.goal_id, expected_revision=1, idempotency_key="slot-restart-key"
    )
    replay = second.poll(
        binding.goal_id, expected_revision=1, idempotency_key="slot-restart-key"
    )
    assert expected == replay and expected["intent_ids"] == ["one-canonical-slot-intent"]
    assert calls == [
        (str(binding.goal_id), 1, "slot-restart-key"),
        (str(binding.goal_id), 1, "slot-restart-key"),
    ]


def test_stop_or_revocation_racing_activation_and_poll_fails_closed(monkeypatch):
    module, control, binding, *_ = _mutation_control(monkeypatch)
    control.describe = lambda *_: {"paused": True}
    checks = []

    def assert_binding(candidate, *, action_kind):
        checks.append(action_kind)
        if action_kind in {"schedule_activate", "schedule_poll"}:
            raise PermissionError("current stop or policy revocation")

    control.assert_binding = assert_binding
    production = module.ProductionScheduleCutover(
        os.environ["TEST_DATABASE_URL"], schedule_control=control, binding=binding
    )
    with pytest.raises(PermissionError, match="revocation"):
        production.activate(binding.goal_id, idempotency_key="activate")
    with pytest.raises(PermissionError, match="revocation"):
        production.poll(binding.goal_id, expected_revision=1, idempotency_key="poll")
    assert checks == ["schedule_prepare", "schedule_activate", "schedule_poll"]


class _SingleRow:
    def __init__(self, value):
        self.value = value

    def fetchone(self):
        return self.value


class _ReadbackConnection:
    def __init__(self, cutover):
        self.cutover = cutover

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, query, *args):
        if "SELECT state,rollback_after_slot" in query:
            return _SingleRow(self.cutover)
        return _SingleRow(None)


def test_out_of_band_unpause_or_action_route_drift_holds_and_alerts(monkeypatch):
    module, control, binding, target, *_ = _mutation_control(monkeypatch)
    target.readback = replace(target.readback, paused=False)
    control._connect = lambda: _ReadbackConnection(
        {"state": "active", "rollback_after_slot": None}
    )
    with pytest.raises(PermissionError, match="paused drained"):
        control.describe(binding.schedule_id)
    assert control.alerts.events[-1][2] == "schedule_fence_violation"

    module, control, binding, target, *_ = _mutation_control(
        monkeypatch, overrides={"route_tuple_sha256": "9" * 64}
    )
    with pytest.raises(PermissionError, match="route readback"):
        control.describe(binding.schedule_id)
    assert control.alerts.events[-1][2] == "schedule_source_or_route_drift"


def test_crash_at_each_cutover_boundary_recovers_from_durable_state(monkeypatch):
    module, control, binding, *_ = _mutation_control(monkeypatch)
    control.describe = lambda *_: {"paused": True}
    control.assert_binding = lambda *_, **__: None
    durable = {}
    boundary_calls = []

    def step(name):
        def invoke(self, goal_id, **kwargs):
            key = (name, str(goal_id), kwargs.get("idempotency_key"))
            boundary_calls.append(key)
            if key not in durable:
                durable[key] = {"state": name, "key": kwargs.get("idempotency_key")}
                raise RuntimeError("crash after durable boundary")
            return durable[key]
        return invoke

    for method in ("prepare", "activate", "poll", "rollback"):
        cutover_module = import_module("salience.cycles.production_schedule_cutover")
        monkeypatch.setattr(cutover_module._ProductionCycleScheduleCutover, method, step(method))
    production = module.ProductionScheduleCutover(
        os.environ["TEST_DATABASE_URL"], schedule_control=control, binding=binding
    )
    ops = (
        ("prepare", lambda: production.prepare(
            binding.goal_id,
            legacy_schedule_id=binding.schedule_id,
            expected_revision=1,
            first_v4_slot=binding.first_v4_slot,
            idempotency_key="prepare-key",
        )),
        ("activate", lambda: production.activate(binding.goal_id, idempotency_key="activate-key")),
        ("poll", lambda: production.poll(binding.goal_id, expected_revision=1, idempotency_key="poll-key")),
        ("rollback", lambda: production.rollback(binding.goal_id, idempotency_key="rollback-key")),
    )
    for _, action in ops:
        with pytest.raises(RuntimeError, match="durable boundary"):
            action()
        action()
    assert len(boundary_calls) == 8
    assert all(boundary_calls[index] == boundary_calls[index + 1] for index in range(0, 8, 2))


def test_unresolved_v4_work_or_remote_liability_blocks_rollback(monkeypatch):
    _, control, binding, target, *_ = _mutation_control(
        monkeypatch, paused=True, running=1
    )
    with pytest.raises(PermissionError, match="remote schedule actions"):
        control.resume_after(
            binding.schedule_id,
            binding.first_v4_slot,
            subject_id=binding.schedule_operator_subject_id,
        )
    assert target.resume_calls == []
    assert control.alerts.events[-1][2] == "schedule_remote_liability"


def test_rollback_requires_later_slot_readback_before_prior_route_restart(monkeypatch):
    module, control, binding, target, *_ = _mutation_control(
        monkeypatch, paused=True, behavior="early_resume"
    )
    with pytest.raises(PermissionError, match="later exact rollback slot"):
        control.resume_after(
            binding.schedule_id,
            binding.first_v4_slot,
            subject_id=binding.schedule_operator_subject_id,
        )
    assert target.resume_calls[0][0] == binding.remote_schedule_id
    assert control.alerts.events[-1][2] == "schedule_resume_readback_invalid"


def test_candidate_and_environment_mismatch_never_passes_live_acceptance(monkeypatch):
    module, control, binding, target, route, lease, authority, _ = _mutation_control(monkeypatch)
    with pytest.raises(PermissionError, match="target and namespace"):
        module.ProductionTemporalScheduleControl(
            os.environ["TEST_DATABASE_URL"],
            workspace_id=binding.workspace_id,
            target_alias="candidate-target-mismatch",
            namespace=binding.namespace,
            route=route,
            route_lease=lease,
            route_authority=authority,
            target=target,
            alerts=_AlertSink(),
        )
    target.readback = replace(target.readback, namespace="other-namespace")
    with pytest.raises(PermissionError, match="target, source action, and route"):
        control.describe(binding.schedule_id)


def test_production_schedule_binding_rechecks_current_cycles_schedule_policy_stops_and_route(monkeypatch):
    module, control, binding, *_ = _mutation_control(monkeypatch)
    fake_row = {
        "policy_version_id": binding.source_approval_id,
        "policy_document_hash": "a" * 64,
    }
    control._connect = lambda: _ReadbackConnection(None)
    control._binding_row = lambda *_, **__: fake_row
    control._binding_value = lambda _: binding
    control._require_current_schedule_authority = lambda *args, **kwargs: (_ for _ in ()).throw(
        PermissionError("current policy and both stops required")
    )
    route_calls = []
    control._route_decision = lambda **kwargs: route_calls.append(kwargs)
    control.assert_binding = MethodType(
        module.ProductionTemporalScheduleControl.assert_binding, control
    )
    with pytest.raises(PermissionError, match="both stops"):
        control.assert_binding(binding, action_kind="schedule_poll")
    assert route_calls == []

    changed = replace(binding, owner_id=UUID("00000000-0000-0000-0000-000000000623"))
    with pytest.raises(PermissionError, match="current route"):
        control._validate_binding_route(changed)


def test_production_schedule_admission_enrolls_intent_atomically_with_current_authority(monkeypatch):
    module, control, binding, *_ = _mutation_control(monkeypatch)
    key = module.ProductionIntentKey(
        UUID("00000000-0000-0000-0000-000000000624"), 1
    )
    events = []

    class _Transaction:
        def __enter__(self):
            events.append("transaction_begin")
            return self

        def __exit__(self, *_):
            events.append("transaction_commit")
            return False

    class _Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def transaction(self):
            return _Transaction()

    control._connect = lambda: _Connection()
    enrollment_module = import_module(
        "salience.cycles.production_intent_enrollment"
    )
    monkeypatch.setattr(
        enrollment_module,
        "current_authority",
        lambda *args, **kwargs: events.append("authority"),
    )
    writer = module.ProductionIntentEnrollmentWriter(control, binding)
    writer._enroll_in_transaction = lambda connection, operation, *, require_write: (
        events.append(("enroll", operation, require_write)) or {"intent_id": operation.intent_id}
    )
    from salience.cycles.admission import CycleAdmission

    monkeypatch.setattr(
        CycleAdmission,
        "_admit",
        lambda self, connection, intent_id: events.append(("admit", intent_id))
        or {"intent_id": intent_id, "disposition": "admitted"},
    )
    result = writer.enroll_and_admit(key)
    assert result["admission"]["intent_id"] == key.intent_id
    assert events[0] == "transaction_begin" and events[-1] == "transaction_commit"
    assert events.index(("enroll", key, True)) < events.index(("admit", key.intent_id))
    assert events.count("authority") == 2


def test_production_schedule_admission_writes_0047_and_admission_atomically():
    module = import_module("salience.cycles.production_schedule_control")
    with _production_enrollment_fixture() as (
        database,
        control,
        binding,
        key,
        authority,
        route_authority,
    ):
        writer = module.ProductionIntentEnrollmentWriter(control, binding)
        first = writer.enroll_and_admit(key)
        with psycopg.connect(database, row_factory=dict_row) as connection:
            persisted = connection.execute(
                """SELECT * FROM v4_production_intent_enrollments
                   WHERE intent_id=%s AND eligibility_revision=%s""",
                (key.intent_id, key.eligibility_revision),
            ).fetchone()
            admission = connection.execute(
                """SELECT * FROM v4_admissions
                   WHERE intent_id=%s AND eligibility_revision=%s""",
                (key.intent_id, key.eligibility_revision),
            ).fetchone()
            assert connection.execute(
                "SELECT count(*) AS count FROM v4_production_intent_enrollments WHERE intent_id=%s",
                (key.intent_id,),
            ).fetchone()["count"] == 1
        assert persisted["route_fence_decision_id"] is not None
        assert persisted["route_id"] == binding.route_id
        assert persisted["slot_id"] == binding.slot_id
        assert persisted["route_owner_id"] == binding.owner_id
        assert persisted["fencing_epoch"] == binding.fencing_epoch
        assert persisted["route_manifest_sha256"] == binding.route_manifest_sha256
        assert persisted["external_lock_sha256"] == binding.artifact_lock_sha256
        assert admission is not None
        route_decision = route_authority.decision_record(
            persisted["route_fence_decision_id"]
        ).decision
        assert route_decision.authorized is True
        assert route_decision.request.route_id == binding.route_id
        assert route_decision.request.route_tuple_sha256 == binding.route_tuple_sha256
        assert route_decision.request.slot_id == binding.slot_id
        assert route_decision.request.owner_id == binding.owner_id
        assert route_decision.request.fencing_epoch == binding.fencing_epoch
        assert route_decision.request.route_manifest_sha256 == binding.route_manifest_sha256
        assert route_decision.request.artifact_lock_sha256 == binding.artifact_lock_sha256
        assert route_decision.request.operation_id == key.intent_id
        assert route_decision.request.permit_id == binding.source_approval_id
        assert route_decision.request.action_kind == "schedule_admit"
        assert first["enrollment"]["route_fence_decision_id"] == persisted[
            "route_fence_decision_id"
        ]
        assert first["admission"]["id"] == admission["id"]

        replay = writer.enroll_and_admit(key)
        assert replay["enrollment"]["route_fence_decision_id"] == persisted[
            "route_fence_decision_id"
        ]
        assert replay["admission"]["id"] == admission["id"]
        assert route_authority.read_slot(binding.route_id, binding.slot_id).lease == control.route_lease


@pytest.mark.parametrize("drift", ["policy", "workspace_stop", "goal_stop", "route", "missing_grant"])
def test_intent_enrollment_rejects_stale_policy_stop_or_route(drift):
    module = import_module("salience.cycles.production_schedule_control")
    with _production_enrollment_fixture("task6_drift_") as (
        database,
        control,
        binding,
        key,
        authority,
        route_authority,
    ):
        with psycopg.connect(database) as connection:
            if drift == "policy":
                connection.execute(
                    "UPDATE policy_versions SET status='revoked' WHERE id=%s",
                    (authority["task6_policy_id"],),
                )
            elif drift in {"workspace_stop", "goal_stop"}:
                from salience.cycles.governance import CycleGovernance

                CycleGovernance(
                    database,
                    workspace_id=binding.workspace_id,
                    subject_id=authority["subject_id"],
                ).set_stop(
                    goal_id=(None if drift == "workspace_stop" else binding.goal_id),
                    stopped=True,
                    expected_revision=1,
                    idempotency_key="task6-stop-" + drift,
                    reason="test current stop revision denial",
                )
            elif drift == "missing_grant":
                connection.execute(
                    """DELETE FROM permission_grants
                       WHERE workspace_id=%s AND principal_id=%s
                         AND scope='cycles:schedule'""",
                    (binding.workspace_id, str(binding.schedule_operator_subject_id)),
                )
        if drift == "route":
            route_authority.release_slot(control.route_lease)
        writer = module.ProductionIntentEnrollmentWriter(control, binding)
        with pytest.raises(PermissionError):
            writer.enroll_and_admit(key)
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT count(*) FROM v4_production_intent_enrollments WHERE intent_id=%s",
                (key.intent_id,),
            ).fetchone()[0] == 0


def test_intent_enrollment_rejects_changed_route_tuple_and_missing_identity():
    module = import_module("salience.cycles.production_schedule_control")
    with _production_enrollment_fixture("task6_identity_") as (
        database,
        control,
        binding,
        key,
        authority,
        _route_authority,
    ):
        changed = replace(binding, route_tuple_sha256="f" * 64)
        for field, invalid_binding in (
            ("route_tuple_sha256", changed),
            ("route_id", replace(binding, route_id=uuid4())),
            ("slot_id", replace(binding, slot_id=uuid4())),
            ("owner_id", replace(binding, owner_id=uuid4())),
            ("fencing_epoch", replace(binding, fencing_epoch=binding.fencing_epoch + 1)),
            ("route_manifest_sha256", replace(binding, route_manifest_sha256="a" * 64)),
            ("artifact_lock_sha256", replace(binding, artifact_lock_sha256="c" * 64)),
        ):
            try:
                module.ProductionIntentEnrollmentWriter(
                    control, invalid_binding
                ).enroll_and_admit(key)
            except PermissionError:
                pass
            else:
                pytest.fail(f"changed {field} accepted for a finalized binding")
        with psycopg.connect(database) as connection:
            connection.execute(
                "UPDATE identity_subjects SET enabled=false WHERE id=%s",
                (binding.source_owner_subject_id,),
            )
        with pytest.raises(PermissionError, match="identit"):
            module.ProductionIntentEnrollmentWriter(control, binding).enroll_and_admit(key)
        with psycopg.connect(database, autocommit=True) as connection:
            assert connection.execute(
                "SELECT count(*) FROM v4_production_intent_enrollments WHERE intent_id=%s",
                (key.intent_id,),
            ).fetchone()[0] == 0


@pytest.mark.parametrize("action", ["pause", "resume_after"])
@pytest.mark.parametrize("drift", ["goal_revision", "disabled_source_owner"])
def test_schedule_mutations_recheck_current_source_revision_and_owner_before_mutation(
    action, drift
):
    module = import_module("salience.cycles.production_schedule_control")
    with _production_enrollment_fixture("task6_mutation_authority_") as (
        database,
        control,
        binding,
        _key,
        _authority,
        _route_authority,
    ):
        target = _MutationTarget(
            _readback(module, binding, paused=action == "resume_after")
        )
        control.target = target
        with psycopg.connect(database) as connection:
            if drift == "goal_revision":
                connection.execute("SET session_replication_role = replica")
                connection.execute(
                    "UPDATE v4_goals SET revision=revision+1 WHERE id=%s",
                    (binding.goal_id,),
                )
                connection.execute("SET session_replication_role = origin")
            else:
                connection.execute(
                    "UPDATE identity_subjects SET enabled=false WHERE id=%s",
                    (binding.source_owner_subject_id,),
                )

        denied = False
        try:
            if action == "pause":
                control.pause(binding.schedule_id)
            else:
                control.resume_after(
                    binding.schedule_id,
                    binding.first_v4_slot,
                    subject_id=binding.schedule_operator_subject_id,
                )
        except PermissionError:
            denied = True
        assert denied
        assert target.pause_calls == []
        assert target.resume_calls == []


def test_production_schedule_pause_then_resume_rollback_accepts_exact_paused_source():
    with _production_cutover_fixture("task6_lifecycle_rollback_") as (
        database,
        _control,
        binding,
        target,
        production,
        _prepared,
    ):
        activated = production.activate(
            binding.goal_id, idempotency_key="task6-lifecycle-activate"
        )
        assert activated["state"] == "active"
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT status FROM job_schedules WHERE id=%s",
                (binding.schedule_id,),
            ).fetchone()[0] == "paused"

        rolled_back = production.rollback(
            binding.goal_id, idempotency_key="task6-lifecycle-rollback"
        )
        assert rolled_back["state"] == "rolled_back"
        assert len(target.pause_calls) == 1
        assert len(target.resume_calls) == 1
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT status FROM job_schedules WHERE id=%s",
                (binding.schedule_id,),
            ).fetchone()[0] == "active"


def test_pending_activation_retry_accepts_exact_paused_source():
    with _production_cutover_fixture("task6_lifecycle_retry_") as (
        database,
        control,
        binding,
        target,
        production,
        prepared,
    ):
        assert prepared["state"] == "pending"
        control.pause(binding.schedule_id)
        first_pause = target.pause_calls[0]
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT status FROM job_schedules WHERE id=%s",
                (binding.schedule_id,),
            ).fetchone()[0] == "paused"
            assert connection.execute(
                "SELECT state FROM v4_schedule_cutovers WHERE goal_id=%s",
                (binding.goal_id,),
            ).fetchone()[0] == "pending"

        activated = production.activate(
            binding.goal_id, idempotency_key="task6-lifecycle-activate"
        )
        assert activated["state"] == "active"
        assert len(target.pause_calls) == 2
        assert target.pause_calls[1] == first_pause


def test_production_schedule_pause_rejects_invalid_local_status():
    with _production_cutover_fixture("task6_lifecycle_invalid_status_") as (
        database,
        control,
        binding,
        target,
        _production,
        _prepared,
    ):
        with psycopg.connect(database) as connection:
            connection.execute(
                "UPDATE job_schedules SET status='cancelled' WHERE id=%s",
                (binding.schedule_id,),
            )
        target.describe_calls.clear()
        with pytest.raises(PermissionError, match="invalid local schedule status"):
            control.pause(binding.schedule_id)
        assert target.pause_calls == []
        assert target.describe_calls == []


def test_general_application_role_cannot_insert_production_enrollment():
    with _production_enrollment_fixture("task6_direct_") as (
        database,
        _control,
        _binding,
        _key,
        _authority,
        _route_authority,
    ):
        role = "task6_enrollment_ro_" + uuid4().hex[:12]
        with psycopg.connect(database, autocommit=True) as connection:
            connection.execute(
                psycopg.sql.SQL("CREATE ROLE {} NOLOGIN").format(
                    psycopg.sql.Identifier(role)
                )
            )
            connection.execute(
                psycopg.sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(
                    psycopg.sql.Identifier(role)
                )
            )
            connection.execute(
                psycopg.sql.SQL("SET ROLE {}").format(psycopg.sql.Identifier(role))
            )
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(
                    "INSERT INTO v4_production_intent_enrollments DEFAULT VALUES"
                )
            connection.execute("RESET ROLE")
            connection.execute(
                psycopg.sql.SQL("REVOKE ALL ON SCHEMA public FROM {}").format(
                    psycopg.sql.Identifier(role)
                )
            )
            connection.execute(
                psycopg.sql.SQL("DROP ROLE {}").format(psycopg.sql.Identifier(role))
            )


def test_intent_enrollment_writer_has_no_standalone_public_write(monkeypatch):
    module = import_module("salience.cycles.production_schedule_control")
    _, control, binding, *_ = _mutation_control(monkeypatch)
    writer = module.ProductionIntentEnrollmentWriter(control, binding)
    assert not callable(getattr(writer, "enroll", None))
    assert callable(writer.enroll_and_admit)


def test_intent_enrollment_replay_is_exact_and_conflicting_revisions_deny():
    module = import_module("salience.cycles.production_schedule_control")
    writer = module.ProductionIntentEnrollmentWriter
    key = module.ProductionIntentKey(UUID("00000000-0000-0000-0000-000000000624"), 1)
    row = {
        "intent_id": key.intent_id,
        "eligibility_revision": key.eligibility_revision,
        "workspace_id": UUID("00000000-0000-0000-0000-000000000615"),
        "goal_id": UUID("00000000-0000-0000-0000-000000000617"),
        "goal_revision": 1,
        "source_owner_id": UUID("00000000-0000-0000-0000-000000000618"),
        "subject_id": UUID("00000000-0000-0000-0000-000000000619"),
        "subject_revision": 1,
        "grant_ids": ["00000000-0000-0000-0000-000000000625"],
        "policy_version_id": UUID("00000000-0000-0000-0000-000000000626"),
        "policy_document_hash": "a" * 64,
        "workspace_stop_revision": 1,
        "goal_stop_revision": 1,
        "artifact_sha256": "b" * 64,
        "route_id": UUID("00000000-0000-0000-0000-000000000610"),
        "slot_id": UUID("00000000-0000-0000-0000-000000000611"),
        "route_owner_id": UUID("00000000-0000-0000-0000-000000000612"),
        "fencing_epoch": 1,
        "route_manifest_sha256": "c" * 64,
        "external_lock_sha256": "d" * 64,
    }
    assert writer._same_enrollment(row, row)
    for field, changed in (
        ("eligibility_revision", 2),
        ("policy_document_hash", "e" * 64),
        ("workspace_stop_revision", 2),
        ("route_owner_id", UUID("00000000-0000-0000-0000-000000000627")),
        ("fencing_epoch", 2),
        ("external_lock_sha256", "f" * 64),
    ):
        assert not writer._same_enrollment(row, row | {field: changed})


def test_production_inventory_writer_persists_nonempty_history_receipts():
    module = import_module("salience.cycles.production_schedule_control")
    descriptor = _out_of_scope_descriptor("history-receipt-case")
    first_action = module.TemporalScheduleAction(
        action_id_sha256="1" * 64,
        scheduled_slot=datetime(2026, 10, 28, 10, 0, tzinfo=timezone.utc),
        started_at=datetime(2026, 10, 28, 10, 0, 1, tzinfo=timezone.utc),
        completed_at=datetime(2026, 10, 28, 10, 1, tzinfo=timezone.utc),
        outcome_class="completed",
        workflow_id_sha256="2" * 64,
    )
    second_action = module.TemporalScheduleAction(
        action_id_sha256="3" * 64,
        scheduled_slot=datetime(2026, 10, 28, 11, 0, tzinfo=timezone.utc),
        started_at=datetime(2026, 10, 28, 11, 0, 1, tzinfo=timezone.utc),
        completed_at=datetime(2026, 10, 28, 11, 1, tzinfo=timezone.utc),
        outcome_class="completed",
        workflow_id_sha256="4" * 64,
    )
    history = {
        descriptor.remote_schedule_id: [
            module.TemporalScheduleHistoryPage((first_action,), "history-next"),
            module.TemporalScheduleHistoryPage((second_action,), None),
        ]
    }
    target = _PagedTarget((descriptor,), history=history)
    inventory_id = uuid4()
    with _disposable_0048_database("task6_history_") as database:
        control, _, _, _ = _route_control(target, database)
        captured = control.capture_inventory(
            inventory_id=inventory_id,
            source_tool="task-6-test-fake",
            source_tool_version="1",
            candidate_sha256="c" * 64,
            configuration_sha256="d" * 64,
        )
        finalized = control.finalize_inventory(inventory_id)
        with psycopg.connect(database, row_factory=dict_row) as connection:
            member = connection.execute(
                "SELECT history_page_count,history_action_count,history_watermark,"
                "history_watermark_sha256 FROM v4_production_schedule_inventory_members "
                "WHERE inventory_id=%s",
                (inventory_id,),
            ).fetchone()
            receipt_rows = connection.execute(
                "SELECT page_index,item_count,is_terminal,page_receipt_sha256 "
                "FROM v4_production_schedule_inventory_pages WHERE inventory_id=%s "
                "AND remote_schedule_id=%s ORDER BY page_index",
                (inventory_id, descriptor.remote_schedule_id),
            ).fetchall()
        assert captured.member_count == 1 and finalized.finalized_at is not None
        assert member["history_page_count"] == 2 and member["history_action_count"] == 2
        assert member["history_watermark"] == second_action.scheduled_slot
        assert member["history_watermark_sha256"] == second_action.action_id_sha256
        assert [(row["item_count"], row["is_terminal"]) for row in receipt_rows] == [
            (1, False), (1, True)
        ]
        assert len({row["page_receipt_sha256"] for row in receipt_rows}) == 2


def test_production_inventory_writer_canonicalizes_next_action_to_timestamp_or_null():
    module = import_module("salience.cycles.production_schedule_control")
    descriptor = _out_of_scope_descriptor("canonical-next-action")
    object.__setattr__(descriptor, "next_action", None)
    inventory_id = uuid4()
    target = _PagedTarget((descriptor,))
    with _disposable_database() as database:
        control, _, _, _ = _route_control(target, database)
        control.capture_inventory(
            inventory_id=inventory_id,
            source_tool="task6-next-action-fake",
            source_tool_version="1",
            candidate_sha256="c" * 64,
            configuration_sha256="d" * 64,
        )
        with psycopg.connect(database, row_factory=dict_row) as connection:
            snapshot = connection.execute(
                """SELECT member_snapshot
                   FROM v4_production_schedule_inventory_members
                   WHERE inventory_id=%s AND remote_schedule_id=%s""",
                (inventory_id, descriptor.remote_schedule_id),
            ).fetchone()
        assert snapshot["member_snapshot"]["next_action"] is None

    malformed = _out_of_scope_descriptor("empty-next-action")
    object.__setattr__(malformed, "next_action", "")
    with pytest.raises(ValueError, match="timezone-aware"):
        module.TemporalScheduleDescriptor.__post_init__(malformed)
