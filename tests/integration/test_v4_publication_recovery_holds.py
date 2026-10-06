"""Qualify the immutable publication recovery-hold schema and its SQL guards."""

from concurrent.futures import ThreadPoolExecutor
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Barrier
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
import pytest
import pytest_asyncio


ROOT = Path(__file__).parents[2]
REVISION = "0042_publication_recovery_holds"
TABLE = "v4_legacy_publication_recovery_holds"


def _create_database(source, prefix):
    name = prefix + uuid4().hex[:12]
    database = urlunsplit(urlsplit(source)._replace(path="/" + name))
    with psycopg.connect(source, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    return name, database


def _drop_database(source, name, role=None):
    with psycopg.connect(source, autocommit=True) as admin:
        admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s",
            (name,),
        )
        admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
        if role:
            admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


def _migrate(database, direction, target):
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-x", f"database_url={database}", direction, target],
        cwd=ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=120,
    )


def _provision_fixture_role(database):
    role = "fixture_runtime_" + uuid4().hex[:12]
    with psycopg.connect(database) as connection:
        connection.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role)))
        connection.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
        connection.execute(
            sql.SQL("GRANT SELECT, INSERT ON TABLE public.{} TO {}").format(
                sql.Identifier(TABLE), sql.Identifier(role)
            )
        )
    return role


def _assert_schema_and_role(database, role):
    with psycopg.connect(database) as connection:
        columns = connection.execute(
            """SELECT column_name, data_type, is_nullable
               FROM information_schema.columns
               WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position""",
            (TABLE,),
        ).fetchall()
        assert [column[0] for column in columns] == [
            "id",
            "operation_id",
            "workspace_id",
            "goal_id",
            "intent_id",
            "cycle_id",
            "context_id",
            "job_id",
            "publication_attempt_id",
            "permit_id",
            "owner_id",
            "execution_deadline",
            "permit_expires_at",
            "request_fingerprint",
            "receipt_fingerprint",
            "liability_fingerprint",
            "remote_id",
            "remote_state",
            "reason",
            "state",
            "liability_snapshot",
            "created_at",
        ]
        assert all(column[2] == "NO" for column in columns)
        assert {column[1] for column in columns} == {"uuid", "timestamp with time zone", "text", "jsonb"}
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == REVISION

        constraints = [
            row[0]
            for row in connection.execute(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid=%s::regclass",
                ("public." + TABLE,),
            ).fetchall()
        ]
        assert any("PRIMARY KEY (id)" in definition for definition in constraints)
        assert any("UNIQUE (operation_id)" in definition for definition in constraints)
        assert any("expired_permit_receipt" in definition and "published_during_cancel" in definition for definition in constraints)
        assert any("state = 'held'" in definition or "state = 'held'::text" in definition for definition in constraints)
        assert sum("FOREIGN KEY" in definition for definition in constraints) >= 10

        public_acl = connection.execute(
            """SELECT count(*) FROM pg_class relation,
                      LATERAL aclexplode(COALESCE(relation.relacl, acldefault('r', relation.relowner))) acl
               WHERE relation.oid=%s::regclass AND acl.grantee=0""",
            ("public." + TABLE,),
        ).fetchone()[0]
        assert public_acl == 0
        assert connection.execute("SELECT has_schema_privilege(%s,'public','USAGE')", (role,)).fetchone()[0]
        assert not connection.execute("SELECT has_schema_privilege(%s,'public','CREATE')", (role,)).fetchone()[0]
        for privilege in ("SELECT", "INSERT"):
            assert connection.execute(
                "SELECT has_table_privilege(%s,%s,%s)", (role, "public." + TABLE, privilege)
            ).fetchone()[0]
        for privilege in ("UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
            assert not connection.execute(
                "SELECT has_table_privilege(%s,%s,%s)", (role, "public." + TABLE, privilege)
            ).fetchone()[0]


def test_migration_0042_schema_role_and_empty_rollback_reupgrade():
    source = os.environ["TEST_DATABASE_URL"]
    name, database = _create_database(source, "recovery_schema_")
    role = None
    try:
        before = _migrate(database, "upgrade", "0041_native_publication_sources")
        assert before.returncode == 0, before.stderr
        with psycopg.connect(database) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0041_native_publication_sources"
            assert connection.execute("SELECT to_regclass(%s)", ("public." + TABLE,)).fetchone()[0] is None

        upgraded = _migrate(database, "upgrade", REVISION)
        assert upgraded.returncode == 0, upgraded.stderr
        role = _provision_fixture_role(database)
        _assert_schema_and_role(database, role)

        rolled_back = _migrate(database, "downgrade", "0041_native_publication_sources")
        assert rolled_back.returncode == 0, rolled_back.stderr
        with psycopg.connect(database) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0041_native_publication_sources"
            assert connection.execute("SELECT to_regclass(%s)", ("public." + TABLE,)).fetchone()[0] is None

        upgraded_again = _migrate(database, "upgrade", REVISION)
        assert upgraded_again.returncode == 0, upgraded_again.stderr
        assert _migrate(database, "downgrade", "0041_native_publication_sources").returncode == 0
    finally:
        _drop_database(source, name, role)


_SNAPSHOT_SQL = """
SELECT jsonb_build_object(
    'dispatch', to_jsonb(binding),
    'context', to_jsonb(context),
    'cycle', to_jsonb(cycle),
    'job', to_jsonb(job),
    'permit_claim', to_jsonb(claim),
    'permit', to_jsonb(permit),
    'fixture_receipt', to_jsonb(receipt_row),
    'fixture_progress', to_jsonb(progress),
    'publication_request', to_jsonb(pub_request),
    'publication_plan', to_jsonb(plan),
    'publication_attempt', to_jsonb(attempt),
    'external_effect', COALESCE(to_jsonb(effect), 'null'::jsonb),
    'budget_reservation', COALESCE(to_jsonb(reservation), 'null'::jsonb),
    'publication_status_events', COALESCE((
        SELECT jsonb_agg(to_jsonb(status_event) ORDER BY status_event.sequence_no, status_event.id)
        FROM publication_status_events status_event
        WHERE status_event.publication_attempt_id=attempt.id), '[]'::jsonb),
    'cycle_events', COALESCE((
        SELECT jsonb_agg(to_jsonb(cycle_event) ORDER BY cycle_event.created_at, cycle_event.id)
        FROM v4_cycle_events cycle_event WHERE cycle_event.cycle_id=cycle.id), '[]'::jsonb),
    'cycle_outbox', COALESCE((
        SELECT jsonb_agg(to_jsonb(outbox) ORDER BY outbox.sequence, outbox.id)
        FROM v4_cycle_outbox outbox WHERE outbox.cycle_id=cycle.id), '[]'::jsonb)
)
FROM v4_legacy_dispatches binding
JOIN v4_cycles cycle ON cycle.id=binding.cycle_id AND cycle.operation_id=binding.operation_id
JOIN v4_run_contexts context ON context.id=binding.context_id AND context.cycle_id=cycle.id
JOIN jobs job ON job.id=binding.job_id AND job.workspace_id=binding.workspace_id
JOIN v4_permit_claims claim ON claim.cycle_id=cycle.id AND claim.operation_id=binding.operation_id
    AND claim.context_id=context.id
JOIN v4_dispatch_permits permit ON permit.id=claim.current_permit_id AND permit.claim_id=claim.id
JOIN v4_legacy_publication_fixture_receipts receipt_row ON receipt_row.operation_id=binding.operation_id
JOIN v4_legacy_publication_fixture_progress progress ON progress.operation_id=receipt_row.operation_id
JOIN publication_requests pub_request ON pub_request.id=(receipt_row.request->>'id')::uuid
JOIN publication_plans plan ON plan.publication_request_id=pub_request.id
JOIN publication_attempts attempt ON attempt.publication_plan_id=plan.id
    AND attempt.idempotency_key=receipt_row.idempotency_key||':attempt:1'
LEFT JOIN external_effects effect ON effect.id=plan.external_effect_id
LEFT JOIN budget_reservations reservation ON reservation.id=plan.budget_reservation_id
WHERE binding.operation_id=%s
"""

_INSERT_SQL = f"""INSERT INTO public.{TABLE} (
    id, operation_id, workspace_id, goal_id, intent_id, cycle_id, context_id, job_id,
    publication_attempt_id, permit_id, owner_id, execution_deadline, permit_expires_at,
    request_fingerprint, receipt_fingerprint, liability_fingerprint, remote_id, remote_state,
    reason, state, liability_snapshot
) VALUES (
    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
)"""


def _hold_values(connection, operation_id, *, reason="published_during_cancel", overrides=None):
    canonical = connection.execute(
        """SELECT binding.operation_id, binding.workspace_id, binding.goal_id, binding.intent_id,
                  binding.cycle_id, binding.context_id, binding.job_id, binding.actor_id,
                  (context.payload->>'execution_deadline')::timestamptz AS execution_deadline,
                  claim.current_permit_id AS permit_id, permit.expires_at AS permit_expires_at,
                  receipt_row.fingerprint AS request_fingerprint,
                  encode(digest(receipt_row.receipt::text,'sha256'),'hex') AS receipt_fingerprint,
                  receipt_row.remote_id, progress.state AS remote_state,
                  attempt.id AS publication_attempt_id
           FROM v4_legacy_dispatches binding
           JOIN v4_cycles cycle ON cycle.id=binding.cycle_id
           JOIN v4_run_contexts context ON context.id=binding.context_id
           JOIN v4_permit_claims claim ON claim.cycle_id=cycle.id AND claim.operation_id=binding.operation_id
           JOIN v4_dispatch_permits permit ON permit.id=claim.current_permit_id AND permit.claim_id=claim.id
           JOIN v4_legacy_publication_fixture_receipts receipt_row ON receipt_row.operation_id=binding.operation_id
           JOIN v4_legacy_publication_fixture_progress progress ON progress.operation_id=receipt_row.operation_id
           JOIN publication_requests pub_request ON pub_request.id=(receipt_row.request->>'id')::uuid
           JOIN publication_plans plan ON plan.publication_request_id=pub_request.id
           JOIN publication_attempts attempt ON attempt.publication_plan_id=plan.id
               AND attempt.idempotency_key=receipt_row.idempotency_key||':attempt:1'
           WHERE binding.operation_id=%s""",
        (operation_id,),
    ).fetchone()
    assert canonical is not None
    snapshot = connection.execute(_SNAPSHOT_SQL, (operation_id,)).fetchone()[0]
    assert snapshot is not None
    names = (
        "id",
        "operation_id",
        "workspace_id",
        "goal_id",
        "intent_id",
        "cycle_id",
        "context_id",
        "job_id",
        "publication_attempt_id",
        "permit_id",
        "owner_id",
        "execution_deadline",
        "permit_expires_at",
        "request_fingerprint",
        "receipt_fingerprint",
        "liability_fingerprint",
        "remote_id",
        "remote_state",
        "reason",
        "state",
        "liability_snapshot",
    )
    values = {
        "id": uuid4(),
        "operation_id": canonical[0],
        "workspace_id": canonical[1],
        "goal_id": canonical[2],
        "intent_id": canonical[3],
        "cycle_id": canonical[4],
        "context_id": canonical[5],
        "job_id": canonical[6],
        "publication_attempt_id": canonical[15],
        "permit_id": canonical[9],
        "owner_id": canonical[7],
        "execution_deadline": canonical[8],
        "permit_expires_at": canonical[10],
        "request_fingerprint": canonical[11],
        "receipt_fingerprint": canonical[12],
        "liability_fingerprint": connection.execute(
            "SELECT encode(digest(%s::jsonb::text,'sha256'),'hex')", (Jsonb(snapshot),)
        ).fetchone()[0],
        "remote_id": canonical[13],
        "remote_state": canonical[14],
        "reason": reason,
        "state": "held",
        "liability_snapshot": Jsonb(snapshot),
    }
    if overrides:
        values.update(overrides)
    return tuple(values[name] for name in names)


@pytest.mark.asyncio
async def test_direct_sql_recovery_hold_guards_race_and_populated_downgrade(monkeypatch, tmp_path):
    source = os.environ["TEST_DATABASE_URL"]
    name, database = _create_database(source, "recovery_direct_")
    role = None
    try:
        before = _migrate(database, "upgrade", "0041_native_publication_sources")
        assert before.returncode == 0, before.stderr
        upgraded = _migrate(database, "upgrade", REVISION)
        assert upgraded.returncode == 0, upgraded.stderr
        role = _provision_fixture_role(database)
        _assert_schema_and_role(database, role)

        from test_v4_legacy_publication import _hard_exit_publication_worker, _publication_fixture

        monkeypatch.setenv("TEST_DATABASE_URL", database)
        monkeypatch.setenv("SALIENCE_DEPLOYMENT_MODE", "fixture")
        monkeypatch.setenv("SALIENCE_EFFECTS_ENABLED", "false")
        fixture = await _publication_fixture(database)
        submitted = fixture["bridge"].submit_publication(fixture["command"])
        assert await _hard_exit_publication_worker(fixture, tmp_path, after_acceptance=True) == 73
        with psycopg.connect(database) as connection:
            connection.execute(
                """UPDATE v4_legacy_publication_fixture_progress
                   SET state='published', polls=2, updated_at=clock_timestamp()
                   WHERE operation_id=%s AND state IN ('accepted','processing')""",
                (submitted["operation_id"],),
            )
        fixture["bridge"].close(
            submitted["cycle_id"],
            disposition="cancelled",
            reason="Preserve accepted publication liability while reading its final remote state",
        )

        with psycopg.connect(database) as connection:
            valid = _hold_values(connection, submitted["operation_id"])
            assert connection.execute("SELECT jsonb_typeof(%s)", (valid[-1],)).fetchone()[0] == "object"

            alternate_workspace = uuid4()
            connection.execute(
                "INSERT INTO workspaces(id,slug,display_name) VALUES(%s,%s,'foreign hold workspace')",
                (alternate_workspace, str(alternate_workspace)),
            )
            wrong_workspace = list(valid)
            wrong_workspace[2] = alternate_workspace
            with pytest.raises(psycopg.Error), connection.transaction():
                connection.execute(_INSERT_SQL, tuple(wrong_workspace))

            wrong_operation = list(valid)
            wrong_operation[1] = uuid4()
            with pytest.raises(psycopg.Error), connection.transaction():
                connection.execute(_INSERT_SQL, tuple(wrong_operation))

            for fingerprint_index in (13, 14, 15):
                wrong_fingerprint = list(valid)
                wrong_fingerprint[fingerprint_index] = (
                    "0" * 64 if wrong_fingerprint[fingerprint_index] != "0" * 64 else "1" * 64
                )
                with pytest.raises(psycopg.Error), connection.transaction():
                    connection.execute(_INSERT_SQL, tuple(wrong_fingerprint))

            wrong_reason = list(valid)
            wrong_reason[18] = "expired_permit_receipt"
            with pytest.raises(psycopg.Error, match="reason|publication"), connection.transaction():
                connection.execute(_INSERT_SQL, tuple(wrong_reason))

            wrong_state = list(valid)
            wrong_state[19] = "resolved"
            with pytest.raises(psycopg.Error), connection.transaction():
                connection.execute(_INSERT_SQL, tuple(wrong_state))

        barrier = Barrier(2)

        def race_insert():
            try:
                with psycopg.connect(database) as connection:
                    connection.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
                    barrier.wait(timeout=10)
                    connection.execute(_INSERT_SQL, valid)
                return "inserted"
            except psycopg.errors.UniqueViolation:
                return "duplicate"

        with ThreadPoolExecutor(max_workers=2) as pool:
            race_results = list(pool.map(lambda _: race_insert(), range(2)))
        assert sorted(race_results) == ["duplicate", "inserted"]

        with psycopg.connect(database) as connection:
            row = connection.execute(
                f"SELECT to_jsonb(hold) FROM public.{TABLE} hold WHERE operation_id=%s",
                (submitted["operation_id"],),
            ).fetchone()[0]
            assert row["state"] == "held"
            assert row["reason"] == "published_during_cancel"
            assert row["remote_state"] == "published"
            with pytest.raises(psycopg.Error, match="immutable"), connection.transaction():
                connection.execute(f"UPDATE public.{TABLE} SET state='resolved' WHERE operation_id=%s", (submitted["operation_id"],))
            with pytest.raises(psycopg.Error, match="immutable"), connection.transaction():
                connection.execute(f"DELETE FROM public.{TABLE} WHERE operation_id=%s", (submitted["operation_id"],))
            before_downgrade = row

        refused = _migrate(database, "downgrade", "0041_native_publication_sources")
        assert refused.returncode != 0
        assert "preserve" in refused.stderr.lower()
        with psycopg.connect(database) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == REVISION
            assert connection.execute(
                f"SELECT to_jsonb(hold) FROM public.{TABLE} hold WHERE operation_id=%s",
                (submitted["operation_id"],),
            ).fetchone()[0] == before_downgrade
    finally:
        _drop_database(source, name, role)


@pytest_asyncio.fixture
async def published_cancelled_recovery_case(monkeypatch, tmp_path):
    from test_v4_legacy_publication import (
        _hard_exit_publication_worker,
        _publication_fixture,
    )

    source = os.environ["TEST_DATABASE_URL"]
    name, database = _create_database(source, "recovery_helper_")
    monkeypatch.setenv("SALIENCE_DEPLOYMENT_MODE", "fixture")
    monkeypatch.setenv("SALIENCE_EFFECTS_ENABLED", "false")
    monkeypatch.setenv("TEST_DATABASE_URL", database)
    try:
        upgraded = _migrate(database, "upgrade", "head")
        assert upgraded.returncode == 0, upgraded.stderr
        fixture = await _publication_fixture(database)
        submitted = fixture["bridge"].submit_publication(fixture["command"])
        assert await _hard_exit_publication_worker(
            fixture, tmp_path, after_acceptance=True
        ) == 73
        with psycopg.connect(database) as connection:
            connection.execute(
                """UPDATE v4_legacy_publication_fixture_progress
                   SET state='published', polls=2, updated_at=clock_timestamp()
                   WHERE operation_id=%s AND state IN ('accepted','processing')""",
                (submitted["operation_id"],),
            )
        fixture["bridge"].close(
            submitted["cycle_id"],
            disposition="cancelled",
            reason="Preserve accepted publication liability while reading its final remote state",
        )
        yield fixture, submitted
    finally:
        _drop_database(source, name)


def _liability_snapshot(database, operation_id):
    with psycopg.connect(database) as connection:
        row = connection.execute(_SNAPSHOT_SQL, (operation_id,)).fetchone()
        assert row is not None and row[0] is not None
        return row[0]


def _assert_named_liabilities_unchanged(before, after):
    assert after == before
    for name in (
        "fixture_receipt",
        "fixture_progress",
        "job",
        "cycle",
        "publication_status_events",
        "external_effect",
        "budget_reservation",
        "cycle_events",
        "cycle_outbox",
    ):
        assert after[name] == before[name], name
    assert after["job"]["output_payload"] == before["job"]["output_payload"]


def _recovery_hold_count(database, operation_id):
    with psycopg.connect(database) as connection:
        return connection.execute(
            f"SELECT count(*) FROM public.{TABLE} WHERE operation_id=%s",
            (operation_id,),
        ).fetchone()[0]


@pytest.mark.asyncio
async def test_hold_commit_then_process_death_replays_without_liability_change(
    monkeypatch, tmp_path
):
    from temporalio import activity
    from temporalio.client import Client, WorkflowExecutionStatus, WorkflowFailureError

    from test_v4_legacy_publication import (
        _allow,
        _exception_chain,
        _hard_exit_publication_worker,
        _publication_fixture,
    )

    source = os.environ["TEST_DATABASE_URL"]
    name, database = _create_database(source, "recovery_expired_receipt_")
    monkeypatch.setenv("TEST_DATABASE_URL", database)
    monkeypatch.setenv("SALIENCE_DEPLOYMENT_MODE", "fixture")
    monkeypatch.setenv("SALIENCE_EFFECTS_ENABLED", "false")
    try:
        upgraded = _migrate(database, "upgrade", "head")
        assert upgraded.returncode == 0, upgraded.stderr
        fixture = await _publication_fixture(database)
        submitted = fixture["bridge"].submit_publication(fixture["command"])
        _allow(
            database,
            fixture["workspace_id"],
            fixture["actor_id"],
            ("legacy:publication:reconcile:account:" + fixture["account"].id,),
        )
        operation_id = submitted["operation_id"]
        workflow_id = "salience-v4-legacy-operation:" + operation_id
        precommit_path = tmp_path / "recovery-hold-precommit-snapshot.json"
        activity_log_path = tmp_path / "recovery-hold-child-activity.jsonl"
        activity_timing_path = tmp_path / "recovery-hold-attempt-2-timing.json"
        hold_worker_exit = await _hard_exit_publication_worker(
            fixture,
            tmp_path,
            after_acceptance=True,
            permit_ttl_seconds=10,
            after_hold_commit=True,
            precommit_snapshot_path=precommit_path,
            expire_after_first_submit=True,
            activity_log_path=activity_log_path,
            activity_timing_path=activity_timing_path,
            operation_id=operation_id,
        )
        assert hold_worker_exit == 74, (
            f"hold worker exit={hold_worker_exit}, activity log="
            f"{activity_log_path.read_text() if activity_log_path.exists() else None!r}, "
            f"timing written={activity_timing_path.exists()}, "
            f"precommit written={precommit_path.exists()}"
        )
        child_activity_events = [
            json.loads(line) for line in activity_log_path.read_text().splitlines()
        ]
        assert [
            (event["event"], event["attempt"]) for event in child_activity_events
        ] == [("started", 1), ("failed", 1), ("started", 2), ("reconcile", 2)]
        attempt_2_timing = json.loads(activity_timing_path.read_text())
        assert attempt_2_timing["attempt"] == 2
        assert attempt_2_timing["deadline_is_future"] is True
        assert attempt_2_timing["permit_is_expired"] is True
        assert attempt_2_timing["runtime_hold_reasons"] == []
        assert (
            datetime.fromisoformat(attempt_2_timing["started_at"])
            > datetime.fromisoformat(child_activity_events[1]["at"])
        )

        client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
        handle = client.get_workflow_handle(workflow_id)
        precommit_snapshot = json.loads(precommit_path.read_text())
        execution_deadline = precommit_snapshot["context"]["payload"]["execution_deadline"]

        with psycopg.connect(database) as connection:
            hold_rows = connection.execute(
                f"SELECT id,reason,liability_snapshot FROM public.{TABLE} WHERE operation_id=%s",
                (operation_id,),
            ).fetchall()
            assert len(hold_rows) == 1
            hold_id, reason, hold_snapshot = hold_rows[0]
            assert reason == "expired_permit_receipt"
            after_process_death = connection.execute(
                _SNAPSHOT_SQL, (operation_id,)
            ).fetchone()[0]
            post_death_timing = connection.execute(
                """SELECT clock_timestamp(), context.payload->>'execution_deadline',
                          (context.payload->>'execution_deadline')::timestamptz > clock_timestamp()
                   FROM v4_run_contexts context WHERE context.id=%s""",
                (submitted["context_id"],),
            ).fetchone()
            runtime_holds_after_death = connection.execute(
                "SELECT reason,created_at FROM v4_runtime_holds WHERE cycle_id=%s",
                (submitted["cycle_id"],),
            ).fetchall()

        assert precommit_snapshot == hold_snapshot == after_process_death
        assert _recovery_hold_count(database, operation_id) == 1
        assert post_death_timing[2], post_death_timing
        assert runtime_holds_after_death == []

        description = await handle.describe()
        assert description.status == WorkflowExecutionStatus.RUNNING

        from salience.cycles.outbox import CycleOutbox
        from salience.cycles.legacy_publication_runtime import (
            DurableLegacyFixturePublisher,
            GuardedPublicationActivities,
        )
        from salience.cycles.legacy_runtime import build_legacy_worker
        import salience.cycles.publication_recovery as recovery

        original_record = recovery.record_publication_recovery_hold
        replayed_ids = []

        def track_replay(*args, **kwargs):
            hold = original_record(*args, **kwargs)
            replayed_ids.append(str(hold.id))
            return hold

        original_reconcile = DurableLegacyFixturePublisher.reconcile
        reconcile_calls = []

        async def track_reconcile(self, idempotency_key):
            receipt = await original_reconcile(self, idempotency_key)
            reconcile_calls.append(receipt)
            return receipt

        original_activity = GuardedPublicationActivities.submit_or_reconcile
        activity_attempts = []
        activity_started_at = []

        @activity.defn(name="salience.publication.submit_or_reconcile")
        async def tracked_activity(self, payload):
            activity_attempts.append(activity.info().attempt)
            activity_started_at.append(datetime.now(timezone.utc))
            return await original_activity(self, payload)

        monkeypatch.setattr(recovery, "record_publication_recovery_hold", track_replay)
        monkeypatch.setattr(DurableLegacyFixturePublisher, "reconcile", track_reconcile)
        monkeypatch.setattr(GuardedPublicationActivities, "submit_or_reconcile", tracked_activity)
        outbox = CycleOutbox(
            database, workspace_id=fixture["workspace_id"], delivery_lane="legacy"
        )
        worker = build_legacy_worker(client, task_queue=fixture["queue"], outbox=outbox)
        worker_task = asyncio.create_task(worker.run())
        try:
            with pytest.raises(WorkflowFailureError) as failure:
                await asyncio.wait_for(handle.result(), timeout=35)
        finally:
            await asyncio.wait_for(worker.shutdown(), timeout=10)
            await asyncio.wait_for(worker_task, timeout=10)

        assert activity_attempts == [3]
        assert activity_started_at[0] > datetime.fromisoformat(
            attempt_2_timing["started_at"]
        )
        assert activity_started_at[0] < datetime.fromisoformat(execution_deadline)
        assert reconcile_calls == []
        typed_failures = [
            error
            for error in _exception_chain(failure.value)
            if getattr(error, "type", None) == "LegacyPublicationRecoveryHeld"
        ]
        assert len(typed_failures) == 1
        assert typed_failures[0].details == ({"hold_id": str(hold_id)},)

        with psycopg.connect(database) as connection:
            after_retry = connection.execute(
                _SNAPSHOT_SQL, (operation_id,)
            ).fetchone()[0]
            receipt_count = connection.execute(
                "SELECT count(*) FROM v4_legacy_publication_fixture_receipts WHERE operation_id=%s",
                (operation_id,),
            ).fetchone()[0]
            job = connection.execute(
                "SELECT state,output_payload FROM jobs WHERE id=%s",
                (submitted["job_id"],),
            ).fetchone()
            publication_count = connection.execute(
                """SELECT count(*) FROM publications publication
                   JOIN publication_requests request
                     ON request.id=publication.publication_request_id
                   WHERE request.request_key=%s""",
                ("v4-legacy:" + operation_id,),
            ).fetchone()[0]
            retry_timing = connection.execute(
                """SELECT clock_timestamp(),
                          (context.payload->>'execution_deadline')::timestamptz > clock_timestamp(),
                          (SELECT jsonb_agg(to_jsonb(hold) ORDER BY hold.created_at)
                           FROM v4_runtime_holds hold WHERE hold.cycle_id=binding.cycle_id),
                          (SELECT jsonb_agg(to_jsonb(event) ORDER BY event.created_at,event.id)
                           FROM v4_cycle_events event
                           WHERE event.cycle_id=binding.cycle_id AND event.kind='runtime_held')
                   FROM v4_legacy_dispatches binding
                   JOIN v4_run_contexts context ON context.id=binding.context_id
                   WHERE binding.operation_id=%s""",
                (operation_id,),
            ).fetchone()

        assert precommit_snapshot == hold_snapshot == after_process_death == after_retry
        assert retry_timing[1] is True, retry_timing
        assert retry_timing[2] is None
        assert retry_timing[3] is None
        assert receipt_count == 1
        assert job[0] != "succeeded" and job[1] is None
        assert publication_count == 0
        assert replayed_ids == [str(hold_id)], (
            f"replayed_ids={replayed_ids!r}, activity_attempts={activity_attempts!r}, "
            f"execution_deadline={execution_deadline!r}, post_death_timing={post_death_timing!r}, "
            f"runtime_holds_after_death={runtime_holds_after_death!r}, retry_timing={retry_timing!r}"
        )
        for field in (
            "fixture_receipt",
            "external_effect",
            "budget_reservation",
            "job",
            "cycle",
            "publication_status_events",
            "cycle_events",
            "cycle_outbox",
        ):
            assert after_retry[field] == precommit_snapshot[field], field
    finally:
        _drop_database(source, name)


@pytest.mark.asyncio
async def test_exact_hold_replay_returns_stable_id(published_cancelled_recovery_case):
    from salience.cycles.publication_recovery import (
        load_publication_recovery_hold,
        record_publication_recovery_hold,
    )

    fixture, submitted = published_cancelled_recovery_case
    database = fixture["database"]
    operation_id = UUID(submitted["operation_id"])
    before = _liability_snapshot(database, operation_id)
    assert _recovery_hold_count(database, operation_id) == 0

    first = record_publication_recovery_hold(
        database,
        workspace_id=fixture["workspace_id"],
        operation_id=operation_id,
        reason="published_during_cancel",
    )
    replay = record_publication_recovery_hold(
        database,
        workspace_id=fixture["workspace_id"],
        operation_id=operation_id,
        reason="published_during_cancel",
    )

    assert replay == first
    assert replay.id is not None
    after = _liability_snapshot(database, operation_id)
    _assert_named_liabilities_unchanged(before, after)
    assert _recovery_hold_count(database, operation_id) == 1
    with psycopg.connect(database) as connection:
        hold = connection.execute(
            f"SELECT to_jsonb(row) FROM public.{TABLE} row WHERE operation_id=%s",
            (operation_id,),
        ).fetchone()[0]
    assert hold["id"] == str(first.id)
    assert hold["liability_snapshot"] == before
    with psycopg.connect(database) as connection:
        assert load_publication_recovery_hold(
            connection,
            workspace_id=fixture["workspace_id"],
            operation_id=operation_id,
        ) == first
        assert load_publication_recovery_hold(
            connection,
            workspace_id=uuid4(),
            operation_id=operation_id,
        ) is None


@pytest.mark.asyncio
async def test_hold_replay_conflicting_reason_or_evidence_denies(
    published_cancelled_recovery_case,
):
    from salience.cycles.publication_recovery import (
        PublicationRecoveryConflictError,
        record_publication_recovery_hold,
    )

    fixture, submitted = published_cancelled_recovery_case
    database = fixture["database"]
    operation_id = UUID(submitted["operation_id"])
    before = _liability_snapshot(database, operation_id)
    record_publication_recovery_hold(
        database,
        workspace_id=fixture["workspace_id"],
        operation_id=operation_id,
        reason="published_during_cancel",
    )

    with pytest.raises(PublicationRecoveryConflictError):
        record_publication_recovery_hold(
            database,
            workspace_id=fixture["workspace_id"],
            operation_id=operation_id,
            reason="expired_permit_receipt",
        )
    with pytest.raises(PublicationRecoveryConflictError):
        record_publication_recovery_hold(
            database,
            workspace_id=uuid4(),
            operation_id=operation_id,
            reason="published_during_cancel",
        )

    after = _liability_snapshot(database, operation_id)
    _assert_named_liabilities_unchanged(before, after)
    assert _recovery_hold_count(database, operation_id) == 1


@pytest.mark.asyncio
async def test_concurrent_exact_hold_insert_returns_one_stable_hold(
    published_cancelled_recovery_case,
):
    from salience.cycles.publication_recovery import record_publication_recovery_hold

    fixture, submitted = published_cancelled_recovery_case
    database = fixture["database"]
    operation_id = UUID(submitted["operation_id"])
    before = _liability_snapshot(database, operation_id)
    barrier = Barrier(2)

    def record():
        barrier.wait(timeout=10)
        return record_publication_recovery_hold(
            database,
            workspace_id=fixture["workspace_id"],
            operation_id=operation_id,
            reason="published_during_cancel",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        views = list(pool.map(lambda _: record(), range(2)))

    assert views[0] == views[1]
    assert _recovery_hold_count(database, operation_id) == 1
    after = _liability_snapshot(database, operation_id)
    _assert_named_liabilities_unchanged(before, after)
