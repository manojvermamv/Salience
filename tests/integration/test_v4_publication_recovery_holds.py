"""Qualify the immutable publication recovery-hold schema and its SQL guards."""

from concurrent.futures import ThreadPoolExecutor
import asyncio
import os
from pathlib import Path
import subprocess
import sys
from threading import Barrier
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
import pytest


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
