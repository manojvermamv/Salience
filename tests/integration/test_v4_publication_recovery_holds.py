"""Qualify the immutable publication recovery-hold schema and its SQL guards."""

from concurrent.futures import ThreadPoolExecutor
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
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
REVISION_0043 = "0043_complete_publication_recovery_liabilities"
TABLE = "v4_legacy_publication_recovery_holds"
_EXPECTED_V1_KEYS = (
    "dispatch",
    "context",
    "cycle",
    "job",
    "permit_claim",
    "permit",
    "fixture_receipt",
    "fixture_progress",
    "publication_request",
    "publication_plan",
    "publication_attempt",
    "external_effect",
    "budget_reservation",
    "output",
    "remote_publication_receipts",
    "publication_status_events",
    "cycle_events",
    "cycle_outbox",
    "job_checkpoints",
    "audit_events",
    "provenance_records",
)


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
    'publication_attempt', to_jsonb(publication_attempt_row.*),
    'external_effect', COALESCE(to_jsonb(effect), 'null'::jsonb),
    'budget_reservation', COALESCE(to_jsonb(reservation), 'null'::jsonb),
    'output', to_jsonb(job.output_payload),
    'remote_publication_receipts', COALESCE((
        SELECT jsonb_agg(to_jsonb(remote_receipt) ORDER BY remote_receipt.created_at, remote_receipt.id)
        FROM remote_publication_receipts remote_receipt
        WHERE remote_receipt.publication_attempt_id=publication_attempt_row.id), '[]'::jsonb),
    'publication_status_events', COALESCE((
        SELECT jsonb_agg(to_jsonb(status_event) ORDER BY status_event.sequence_no, status_event.id)
        FROM publication_status_events status_event
        WHERE status_event.publication_attempt_id=publication_attempt_row.id), '[]'::jsonb),
    'cycle_events', COALESCE((
        SELECT jsonb_agg(to_jsonb(cycle_event) ORDER BY cycle_event.created_at, cycle_event.id)
        FROM v4_cycle_events cycle_event WHERE cycle_event.cycle_id=cycle.id), '[]'::jsonb),
    'cycle_outbox', COALESCE((
        SELECT jsonb_agg(to_jsonb(outbox) ORDER BY outbox.sequence, outbox.id)
        FROM v4_cycle_outbox outbox WHERE outbox.cycle_id=cycle.id), '[]'::jsonb),
    'job_checkpoints', COALESCE((
        SELECT jsonb_agg(to_jsonb(checkpoint) ORDER BY checkpoint.sequence_no, checkpoint.id)
        FROM job_checkpoints checkpoint WHERE checkpoint.job_id=job.id), '[]'::jsonb),
    'audit_events', COALESCE((
        SELECT jsonb_agg(to_jsonb(audit_event) ORDER BY audit_event.created_at, audit_event.sequence_no, audit_event.id)
        FROM audit_events audit_event WHERE audit_event.job_id=job.id), '[]'::jsonb),
    'provenance_records', COALESCE((
        SELECT jsonb_agg(to_jsonb(provenance) ORDER BY provenance.created_at, provenance.id)
        FROM provenance_records provenance WHERE provenance.job_id=job.id), '[]'::jsonb)
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
JOIN publication_attempts publication_attempt_row
    ON publication_attempt_row.publication_plan_id=plan.id
    AND publication_attempt_row.idempotency_key=receipt_row.idempotency_key||':attempt:1'
LEFT JOIN external_effects effect ON effect.id=plan.external_effect_id
LEFT JOIN budget_reservations reservation ON reservation.id=plan.budget_reservation_id
WHERE binding.operation_id=%s
"""

# Migration 0042 pins this 16-key payload as part of its original trigger
# contract. Full immutability assertions use `_SNAPSHOT_SQL` above and add the
# separately named canonical collections excluded from this stored projection.
_RECOVERY_HOLD_SNAPSHOT_SQL = f"""
SELECT full_snapshot.snapshot - ARRAY[
    'output', 'remote_publication_receipts', 'job_checkpoints',
    'audit_events', 'provenance_records'
]::text[]
FROM ({_SNAPSHOT_SQL}) AS full_snapshot(snapshot)
"""
_RECOVERY_HOLD_SNAPSHOT_EXCLUDED_KEYS = frozenset(
    {
        "output",
        "remote_publication_receipts",
        "job_checkpoints",
        "audit_events",
        "provenance_records",
    }
)

_INSERT_SQL = f"""INSERT INTO public.{TABLE} (
    id, operation_id, workspace_id, goal_id, intent_id, cycle_id, context_id, job_id,
    publication_attempt_id, permit_id, owner_id, execution_deadline, permit_expires_at,
    request_fingerprint, receipt_fingerprint, liability_fingerprint, remote_id, remote_state,
    reason, state, liability_snapshot
) VALUES (
    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
)"""

_V1_VERSION = "salience.publication-liability.v1"
_INSERT_V1_SQL = f"""INSERT INTO public.{TABLE} (
    id, operation_id, workspace_id, goal_id, intent_id, cycle_id, context_id, job_id,
    publication_attempt_id, permit_id, owner_id, execution_deadline, permit_expires_at,
    request_fingerprint, receipt_fingerprint, liability_fingerprint, remote_id, remote_state,
    reason, state, liability_snapshot, liability_snapshot_version, liability_snapshot_v1,
    liability_fingerprint_v1
) VALUES (
    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
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
                  publication_attempt_row.id AS publication_attempt_id
           FROM v4_legacy_dispatches binding
           JOIN v4_cycles cycle ON cycle.id=binding.cycle_id
           JOIN v4_run_contexts context ON context.id=binding.context_id
           JOIN v4_permit_claims claim ON claim.cycle_id=cycle.id AND claim.operation_id=binding.operation_id
           JOIN v4_dispatch_permits permit ON permit.id=claim.current_permit_id AND permit.claim_id=claim.id
           JOIN v4_legacy_publication_fixture_receipts receipt_row ON receipt_row.operation_id=binding.operation_id
           JOIN v4_legacy_publication_fixture_progress progress ON progress.operation_id=receipt_row.operation_id
           JOIN publication_requests pub_request ON pub_request.id=(receipt_row.request->>'id')::uuid
           JOIN publication_plans plan ON plan.publication_request_id=pub_request.id
           JOIN publication_attempts publication_attempt_row
               ON publication_attempt_row.publication_plan_id=plan.id
               AND publication_attempt_row.idempotency_key=receipt_row.idempotency_key||':attempt:1'
           WHERE binding.operation_id=%s""",
        (operation_id,),
    ).fetchone()
    assert canonical is not None
    snapshot = connection.execute(_RECOVERY_HOLD_SNAPSHOT_SQL, (operation_id,)).fetchone()[0]
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


def _hold_values_v1(connection, operation_id, *, reason="published_during_cancel", overrides=None):
    """Build one valid 0043 insert from PostgreSQL's current canonical rows."""
    values = list(_hold_values(connection, operation_id, reason=reason))
    snapshot = connection.execute(_SNAPSHOT_SQL, (operation_id,)).fetchone()[0]
    assert snapshot is not None and len(snapshot) == 21
    fingerprint = connection.execute(
        "SELECT encode(public.digest(%s::jsonb::text,'sha256'),'hex')",
        (Jsonb(snapshot),),
    ).fetchone()[0]
    values.extend((_V1_VERSION, Jsonb(snapshot), fingerprint))
    if overrides:
        for index, value in overrides.items():
            values[index] = value
    return tuple(values)


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

            legacy_scalar_snapshot = dict(valid[-1].obj)
            legacy_scalar_snapshot["publication_attempt"] = legacy_scalar_snapshot["job"]["attempt"]
            legacy_scalar_values = list(valid)
            legacy_scalar_values[-1] = Jsonb(legacy_scalar_snapshot)
            legacy_scalar_values[15] = connection.execute(
                "SELECT encode(digest(%s::jsonb::text,'sha256'),'hex')",
                (Jsonb(legacy_scalar_snapshot),),
            ).fetchone()[0]
            with pytest.raises(psycopg.Error, match="snapshot"), connection.transaction():
                connection.execute(_INSERT_SQL, tuple(legacy_scalar_values))

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


@pytest_asyncio.fixture
async def expired_permit_recovery_case(monkeypatch, tmp_path):
    from test_v4_legacy_publication import (
        _allow,
        _hard_exit_publication_worker,
        _publication_fixture,
        _wait_for_publication_permit_expiry,
    )

    source = os.environ["TEST_DATABASE_URL"]
    name, database = _create_database(source, "recovery_expired_concurrent_")
    monkeypatch.setenv("SALIENCE_DEPLOYMENT_MODE", "fixture")
    monkeypatch.setenv("SALIENCE_EFFECTS_ENABLED", "false")
    monkeypatch.setenv("TEST_DATABASE_URL", database)
    try:
        upgraded = _migrate(database, "upgrade", "head")
        assert upgraded.returncode == 0, upgraded.stderr
        fixture = await _publication_fixture(database)
        submitted = fixture["bridge"].submit_publication(fixture["command"])
        operation_id = submitted["operation_id"]
        _allow(
            database,
            fixture["workspace_id"],
            fixture["actor_id"],
            ("legacy:publication:reconcile:account:" + fixture["account"].id,),
        )
        assert await _hard_exit_publication_worker(
            fixture,
            tmp_path,
            after_acceptance=True,
            permit_ttl_seconds=10,
            operation_id=operation_id,
        ) == 73
        await _wait_for_publication_permit_expiry(database, operation_id)
        yield fixture, submitted
    finally:
        _drop_database(source, name)


@pytest.mark.asyncio
async def test_0043_v1_guard_accepts_expired_permit_receipt(
    expired_permit_recovery_case,
):
    fixture, submitted = expired_permit_recovery_case
    with psycopg.connect(fixture["database"]) as connection:
        connection.execute(
            _INSERT_V1_SQL,
            _hold_values_v1(
                connection,
                submitted["operation_id"],
                reason="expired_permit_receipt",
            ),
        )
        row = connection.execute(
            f"SELECT reason, liability_snapshot_version FROM public.{TABLE} WHERE operation_id=%s",
            (submitted["operation_id"],),
        ).fetchone()
    assert row == ("expired_permit_receipt", _V1_VERSION)


@pytest_asyncio.fixture
async def cancel_process_death_recovery_case(monkeypatch):
    from test_v4_legacy_publication import _allow, _publication_fixture

    source = os.environ["TEST_DATABASE_URL"]
    name, database = _create_database(source, "recovery_cancel_crash_")
    monkeypatch.setenv("SALIENCE_DEPLOYMENT_MODE", "fixture")
    monkeypatch.setenv("SALIENCE_EFFECTS_ENABLED", "false")
    monkeypatch.setenv("TEST_DATABASE_URL", database)
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
        "publication_attempt",
        "remote_publication_receipts",
        "job",
        "output",
        "cycle",
        "publication_status_events",
        "external_effect",
        "budget_reservation",
        "cycle_events",
        "cycle_outbox",
        "job_checkpoints",
        "audit_events",
        "provenance_records",
    ):
        assert after[name] == before[name], name
    assert after["job"]["output_payload"] == before["job"]["output_payload"]


def _assert_recovery_hold_snapshot_matches_full(full_snapshot, stored_snapshot):
    assert set(stored_snapshot) == set(full_snapshot) - _RECOVERY_HOLD_SNAPSHOT_EXCLUDED_KEYS
    assert stored_snapshot == {
        key: full_snapshot[key]
        for key in stored_snapshot
    }


def _recovery_hold_snapshot(database, operation_id):
    with psycopg.connect(database) as connection:
        row = connection.execute(
            f"SELECT to_jsonb(hold) FROM public.{TABLE} hold WHERE operation_id=%s",
            (operation_id,),
        ).fetchone()
        return None if row is None else row[0]


def _assert_publication_attempt_snapshot(snapshot, publication_attempt_id):
    attempt = snapshot["publication_attempt"]
    assert isinstance(attempt, dict)
    assert attempt["id"] == str(publication_attempt_id)
    assert {
        "publication_plan_id",
        "attempt_number",
        "idempotency_key",
        "state",
        "request_fingerprint",
        "reconciliation",
        "created_at",
        "updated_at",
    } <= attempt.keys()


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
        ] == [("started", 1), ("failed", 1), ("started", 2)]
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
            hold_row_after_death = connection.execute(
                f"SELECT to_jsonb(hold) FROM public.{TABLE} hold WHERE operation_id=%s",
                (operation_id,),
            ).fetchone()[0]
            hold_id = hold_row_after_death["id"]
            assert hold_row_after_death["reason"] == "expired_permit_receipt"
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

        assert precommit_snapshot == after_process_death
        _assert_named_liabilities_unchanged(precommit_snapshot, after_process_death)
        assert hold_row_after_death["id"] == str(UUID(hold_row_after_death["id"]))
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

        assert precommit_snapshot == after_process_death == after_retry
        _assert_named_liabilities_unchanged(precommit_snapshot, after_retry)
        assert _recovery_hold_snapshot(database, operation_id) == hold_row_after_death
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
        publication_attempt_id, stored_snapshot, stored_snapshot_v1 = connection.execute(
            f"""SELECT publication_attempt_id, liability_snapshot,
                       liability_snapshot_v1
                FROM public.{TABLE} WHERE operation_id=%s""",
            (operation_id,),
        ).fetchone()
    _assert_publication_attempt_snapshot(stored_snapshot, publication_attempt_id)
    _assert_recovery_hold_snapshot_matches_full(before, stored_snapshot)
    assert stored_snapshot_v1 == before
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
async def test_exact_replay_rejects_attempt_only_privileged_mutation(
    published_cancelled_recovery_case,
):
    from salience.cycles.publication_recovery import (
        PublicationRecoveryConflictError,
        record_publication_recovery_hold,
    )

    fixture, submitted = published_cancelled_recovery_case
    database = fixture["database"]
    operation_id = UUID(submitted["operation_id"])
    hold = record_publication_recovery_hold(
        database,
        workspace_id=fixture["workspace_id"],
        operation_id=operation_id,
        reason="published_during_cancel",
    )
    with psycopg.connect(database) as connection:
        attempt_id, original_reconciliation = connection.execute(
            """SELECT publication_attempt_row.id,publication_attempt_row.reconciliation
               FROM publication_attempts publication_attempt_row
               JOIN publication_plans plan
                 ON plan.id=publication_attempt_row.publication_plan_id
               JOIN publication_requests request ON request.id=plan.publication_request_id
               WHERE request.request_key=%s""",
            ("v4-legacy:" + str(operation_id),),
        ).fetchone()
        connection.execute("ALTER TABLE public.publication_attempts DISABLE TRIGGER USER")
        connection.execute(
            """UPDATE public.publication_attempts
               SET reconciliation=jsonb_set(reconciliation,'{privileged_restore}','true'::jsonb)
               WHERE id=%s""",
            (attempt_id,),
        )
        connection.execute("ALTER TABLE public.publication_attempts ENABLE TRIGGER USER")

    try:
        with pytest.raises(PublicationRecoveryConflictError):
            record_publication_recovery_hold(
                database,
                workspace_id=fixture["workspace_id"],
                operation_id=operation_id,
                reason="published_during_cancel",
            )
    finally:
        with psycopg.connect(database) as connection:
            connection.execute("ALTER TABLE public.publication_attempts DISABLE TRIGGER USER")
            connection.execute(
                "UPDATE public.publication_attempts SET reconciliation=%s WHERE id=%s",
                (Jsonb(original_reconciliation), attempt_id),
            )
            connection.execute("ALTER TABLE public.publication_attempts ENABLE TRIGGER USER")

    assert _recovery_hold_count(database, operation_id) == 1
    with psycopg.connect(database) as connection:
        stored_attempt = connection.execute(
            f"SELECT liability_snapshot->'publication_attempt' FROM public.{TABLE} WHERE id=%s",
            (hold.id,),
        ).fetchone()[0]
    _assert_publication_attempt_snapshot({"publication_attempt": stored_attempt}, attempt_id)


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
    hold_row = _recovery_hold_snapshot(database, operation_id)
    assert hold_row is not None
    assert hold_row["id"] == str(views[0].id)
    assert hold_row["reason"] == "published_during_cancel"
    replay = record_publication_recovery_hold(
        database,
        workspace_id=fixture["workspace_id"],
        operation_id=operation_id,
        reason="published_during_cancel",
    )
    assert replay == views[0]
    assert _recovery_hold_snapshot(database, operation_id) == hold_row


@pytest.mark.asyncio
async def test_expired_permit_concurrent_exact_hold_insert_returns_one_stable_hold(
    expired_permit_recovery_case,
):
    from salience.cycles.publication_recovery import record_publication_recovery_hold

    fixture, submitted = expired_permit_recovery_case
    database = fixture["database"]
    operation_id = UUID(submitted["operation_id"])
    before = _liability_snapshot(database, operation_id)
    assert _recovery_hold_count(database, operation_id) == 0
    barrier = Barrier(2)

    def record():
        barrier.wait(timeout=10)
        return record_publication_recovery_hold(
            database,
            workspace_id=fixture["workspace_id"],
            operation_id=operation_id,
            reason="expired_permit_receipt",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        views = list(pool.map(lambda _: record(), range(2)))

    assert views[0] == views[1]
    assert views[0].reason == "expired_permit_receipt"
    assert _recovery_hold_count(database, operation_id) == 1
    after = _liability_snapshot(database, operation_id)
    _assert_named_liabilities_unchanged(before, after)
    hold_row = _recovery_hold_snapshot(database, operation_id)
    assert hold_row is not None
    assert hold_row["id"] == str(views[0].id)
    assert hold_row["reason"] == "expired_permit_receipt"

    replay = record_publication_recovery_hold(
        database,
        workspace_id=fixture["workspace_id"],
        operation_id=operation_id,
        reason="expired_permit_receipt",
    )
    assert replay == views[0]
    assert _recovery_hold_count(database, operation_id) == 1
    assert _recovery_hold_snapshot(database, operation_id) == hold_row


@pytest.mark.asyncio
async def test_cancel_hold_commit_then_process_death_replays_without_liability_change(
    cancel_process_death_recovery_case, tmp_path
):
    from temporalio.client import Client, WorkflowExecutionStatus, WorkflowFailureError

    from test_v4_legacy_publication import (
        _exception_chain,
        _hard_exit_publication_worker,
    )

    from salience.cycles.outbox import CycleOutbox
    from salience.cycles.legacy_runtime import build_legacy_worker
    from salience.cycles.runtime import TemporalCycleTransport

    fixture, submitted = cancel_process_death_recovery_case
    database = fixture["database"]
    operation_id = UUID(submitted["operation_id"])
    assert _recovery_hold_count(database, operation_id) == 0

    async def close_after_processing_read():
        with psycopg.connect(database) as connection:
            updated = connection.execute(
                """UPDATE v4_legacy_publication_fixture_progress
                   SET state='published', polls=2, updated_at=clock_timestamp()
                   WHERE operation_id=%s AND state IN ('accepted','processing')
                   RETURNING state""",
                (str(operation_id),),
            ).fetchone()
        assert updated == ("published",)
        fixture["bridge"].close(
            submitted["cycle_id"],
            disposition="cancelled",
            reason="Preserve accepted publication liability while reading its final remote state",
        )
        client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
        outbox = CycleOutbox(
            database, workspace_id=fixture["workspace_id"], delivery_lane="legacy"
        )
        delivered = await outbox.dispatch_one(
            TemporalCycleTransport(client, task_queue=fixture["queue"])
        )
        assert delivered is True
        handle = client.get_workflow_handle(
            "salience-v4-legacy-operation:" + str(operation_id)
        )
        async with asyncio.timeout(10):
            while True:
                history = await handle.fetch_history()
                if any(
                    event.HasField("workflow_execution_signaled_event_attributes")
                    and event.workflow_execution_signaled_event_attributes.signal_name
                    == "request_cancellation"
                    for event in history.events
                ):
                    break
                description = await handle.describe()
                assert description.status == WorkflowExecutionStatus.RUNNING
                await asyncio.sleep(0.05)
    diagnostic_path = tmp_path / "cancel-hold-worker-diagnostics.jsonl"
    activity_log_path = tmp_path / "cancel-hold-worker-activities.jsonl"
    precommit_snapshot_path = tmp_path / "cancel-hold-precommit-liabilities.json"
    exit_code = await _hard_exit_publication_worker(
        fixture,
        tmp_path,
        after_acceptance=False,
        after_hold_commit=True,
        precommit_snapshot_path=precommit_snapshot_path,
        operation_id=operation_id,
        activity_log_path=activity_log_path,
        diagnostic_path=diagnostic_path,
        cancel_after_processing=True,
        cancel_ready_callback=close_after_processing_read,
    )
    assert exit_code == 74, (
        f"exit={exit_code}; diagnostics={diagnostic_path.read_text() if diagnostic_path.exists() else None!r}; "
        f"activity_log={activity_log_path.read_text() if activity_log_path.exists() else None!r}"
    )
    assert precommit_snapshot_path.exists()
    before = json.loads(precommit_snapshot_path.read_text(encoding="utf-8"))
    after_process_death = _liability_snapshot(database, operation_id)
    hold_row_after_death = _recovery_hold_snapshot(database, operation_id)
    assert hold_row_after_death is not None
    hold_id = hold_row_after_death["id"]
    assert hold_row_after_death["reason"] == "published_during_cancel"
    assert _recovery_hold_count(database, operation_id) == 1
    _assert_named_liabilities_unchanged(before, after_process_death)

    client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    handle = client.get_workflow_handle("salience-v4-legacy-operation:" + str(operation_id))
    assert (await handle.describe()).status == WorkflowExecutionStatus.RUNNING
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

    typed_failures = [
        error
        for error in _exception_chain(failure.value)
        if getattr(error, "type", None) == "LegacyPublicationRecoveryHeld"
    ]
    assert len(typed_failures) == 1
    assert typed_failures[0].details == ({"hold_id": hold_id},)
    after_retry = _liability_snapshot(database, operation_id)
    _assert_named_liabilities_unchanged(before, after_retry)
    assert _recovery_hold_count(database, operation_id) == 1
    assert _recovery_hold_snapshot(database, operation_id) == hold_row_after_death
    assert after_process_death == after_retry


@pytest.mark.asyncio
async def test_rollback_verifier_requires_both_reasons_and_unchanged_liabilities(
    published_cancelled_recovery_case, monkeypatch, tmp_path
):
    from salience.cycles.publication_recovery import (
        PublicationRecoveryConflictError,
        record_publication_recovery_hold,
        verify_publication_recovery_rollback,
    )

    from test_v4_legacy_publication import (
        _hard_exit_publication_worker,
        _publication_fixture,
        _wait_for_publication_permit_expiry,
    )

    cancelled_fixture, cancelled = published_cancelled_recovery_case
    database = cancelled_fixture["database"]
    workspace_id = cancelled_fixture["workspace_id"]
    cancelled_operation_id = UUID(cancelled["operation_id"])
    from temporalio.client import Client

    from salience.cycles.outbox import CycleOutbox
    from salience.cycles.runtime import TemporalCycleTransport

    client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    outbox = CycleOutbox(
        database, workspace_id=workspace_id, delivery_lane="legacy"
    )
    assert await outbox.dispatch_one(
        TemporalCycleTransport(client, task_queue=cancelled_fixture["queue"])
    )
    cancelled_hold = record_publication_recovery_hold(
        database,
        workspace_id=workspace_id,
        operation_id=cancelled_operation_id,
        reason="published_during_cancel",
    )

    import test_creative_release_gate_migration as release_gate

    original_ready_package = release_gate._approved_ready_package

    async def reuse_approved_ready_package(*, publication_proofs=True):
        return cancelled_fixture["ready"]

    monkeypatch.setattr(
        release_gate, "_approved_ready_package", reuse_approved_ready_package
    )
    try:
        expired_fixture = await _publication_fixture(database)
    finally:
        monkeypatch.setattr(
            release_gate, "_approved_ready_package", original_ready_package
        )
    assert expired_fixture["workspace_id"] == workspace_id
    assert expired_fixture["queue"] != cancelled_fixture["queue"]
    expired = expired_fixture["bridge"].submit_publication(expired_fixture["command"])
    assert await _hard_exit_publication_worker(
        expired_fixture, tmp_path, after_acceptance=True, permit_ttl_seconds=5
    ) == 73
    expired_operation_id = UUID(expired["operation_id"])
    await _wait_for_publication_permit_expiry(database, expired["operation_id"])
    expired_hold = record_publication_recovery_hold(
        database,
        workspace_id=workspace_id,
        operation_id=expired_operation_id,
        reason="expired_permit_receipt",
    )

    operation_ids = {
        "expired_permit_receipt": expired_operation_id,
        "published_during_cancel": cancelled_operation_id,
    }
    expected_holds = {
        "expired_permit_receipt": expired_hold.id,
        "published_during_cancel": cancelled_hold.id,
    }

    with pytest.raises(ValueError):
        verify_publication_recovery_rollback(
            database,
            workspace_id=workspace_id,
            operation_ids={"expired_permit_receipt": expired_operation_id},
        )
    with pytest.raises(ValueError):
        verify_publication_recovery_rollback(
            database,
            workspace_id=workspace_id,
            operation_ids={
                "expired_permit_receipt": expired_operation_id,
                "published_during_cancel": expired_operation_id,
            },
        )
    with pytest.raises(PublicationRecoveryConflictError):
        verify_publication_recovery_rollback(
            database,
            workspace_id=workspace_id,
            operation_ids={
                "expired_permit_receipt": cancelled_operation_id,
                "published_during_cancel": expired_operation_id,
            },
        )

    verifier_module = sys.modules["salience.cycles.publication_recovery"]
    original_connect = verifier_module.psycopg.connect
    transaction_settings = []

    class ReadOnlyConnectionObserver:
        def __init__(self, connection):
            self.connection = connection

        def __enter__(self):
            self.connection.__enter__()
            return self

        def __exit__(self, *args):
            return self.connection.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def execute(self, statement, *args, **kwargs):
            result = self.connection.execute(statement, *args, **kwargs)
            if str(statement).strip().upper().startswith("SET TRANSACTION"):
                settings = self.connection.execute(
                    "SELECT current_setting('transaction_read_only') AS transaction_read_only, "
                    "current_setting('transaction_isolation') AS transaction_isolation"
                ).fetchone()
                transaction_settings.append(
                    (settings["transaction_read_only"], settings["transaction_isolation"])
                )
            return result

    def observed_connect(*args, **kwargs):
        return ReadOnlyConnectionObserver(original_connect(*args, **kwargs))

    monkeypatch.setattr(verifier_module.psycopg, "connect", observed_connect)
    try:
        expired_view, cancelled_view = verify_publication_recovery_rollback(
            database, workspace_id=workspace_id, operation_ids=operation_ids
        )
    finally:
        monkeypatch.setattr(verifier_module.psycopg, "connect", original_connect)
    assert transaction_settings == [("on", "repeatable read")]
    assert (expired_view.id, cancelled_view.id) == (
        expected_holds["expired_permit_receipt"],
        expected_holds["published_during_cancel"],
    )

    script = ROOT / "scripts" / "verify-v4-publication-recovery-rollback.py"

    def run_script(*, expired_id, cancelled_id=None):
        arguments = [
            sys.executable,
            str(script),
            "--database-url",
            database,
            "--workspace-id",
            str(workspace_id),
            "--expired-operation-id",
            str(expired_id),
        ]
        if cancelled_id is not None:
            arguments.extend(("--cancel-operation-id", str(cancelled_id)))
        return subprocess.run(
            arguments,
            cwd=ROOT,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=30,
        )

    success = run_script(
        expired_id=expired_operation_id, cancelled_id=cancelled_operation_id
    )
    assert success.returncode == 0, success.stderr
    payload = json.loads(success.stdout)
    assert payload == {
        "workspace_id": str(workspace_id),
        "reasons": ["expired_permit_receipt", "published_during_cancel"],
        "operation_ids": {
            key: str(value) for key, value in operation_ids.items()
        },
        "hold_ids": {key: str(value) for key, value in expected_holds.items()},
        "verified": True,
    }

    def assert_script_fails_without_snapshots(result):
        assert result.returncode != 0
        assert "liability_snapshot" not in result.stdout + result.stderr
        assert "fixture_receipt" not in result.stdout + result.stderr

    assert_script_fails_without_snapshots(
        run_script(expired_id=expired_operation_id)
    )
    assert_script_fails_without_snapshots(
        run_script(expired_id=expired_operation_id, cancelled_id=expired_operation_id)
    )
    assert_script_fails_without_snapshots(
        run_script(expired_id=cancelled_operation_id, cancelled_id=expired_operation_id)
    )

    def mutate_and_restore(table, update, update_params, restore, restore_params):
        with psycopg.connect(database) as connection:
            connection.execute(f"ALTER TABLE public.{table} DISABLE TRIGGER USER")
            changed = connection.execute(update, update_params)
            assert changed.rowcount == 1, table
            connection.execute(f"ALTER TABLE public.{table} ENABLE TRIGGER USER")
        try:
            with pytest.raises(PublicationRecoveryConflictError):
                verify_publication_recovery_rollback(
                    database,
                    workspace_id=workspace_id,
                    operation_ids=operation_ids,
                )
            assert_script_fails_without_snapshots(
                run_script(
                    expired_id=expired_operation_id,
                    cancelled_id=cancelled_operation_id,
                )
            )
        finally:
            with psycopg.connect(database) as connection:
                connection.execute(
                    f"ALTER TABLE public.{table} DISABLE TRIGGER USER"
                )
                connection.execute(restore, restore_params)
                connection.execute(f"ALTER TABLE public.{table} ENABLE TRIGGER USER")

    with psycopg.connect(database) as connection:
        cancel_snapshot = connection.execute(
            f"SELECT liability_snapshot FROM public.{TABLE} WHERE operation_id=%s",
            (cancelled_operation_id,),
        ).fetchone()[0]
        receipt = connection.execute(
            "SELECT receipt FROM public.v4_legacy_publication_fixture_receipts WHERE operation_id=%s",
            (cancelled_operation_id,),
        ).fetchone()[0]
        progress_polls = connection.execute(
            "SELECT polls FROM public.v4_legacy_publication_fixture_progress WHERE operation_id=%s",
            (cancelled_operation_id,),
        ).fetchone()[0]
        effect = cancel_snapshot["external_effect"]
        reservation = cancel_snapshot["budget_reservation"]
        assert effect is not None and reservation is not None
        publication_attempt_id = connection.execute(
            f"SELECT publication_attempt_id FROM public.{TABLE} WHERE operation_id=%s",
            (cancelled_operation_id,),
        ).fetchone()[0]
        publication_attempt_reconciliation = connection.execute(
            "SELECT reconciliation FROM public.publication_attempts WHERE id=%s",
            (publication_attempt_id,),
        ).fetchone()[0]
        effect_updated_at = connection.execute(
            "SELECT updated_at FROM public.external_effects WHERE id=%s",
            (effect["id"],),
        ).fetchone()[0]
        reservation_updated_at = connection.execute(
            "SELECT updated_at FROM public.budget_reservations WHERE id=%s",
            (reservation["id"],),
        ).fetchone()[0]
        job_id = cancel_snapshot["job"]["id"]
        job_updated_at, output_payload = connection.execute(
            "SELECT updated_at,output_payload FROM public.jobs WHERE id=%s", (job_id,)
        ).fetchone()
        cycle_id = cancel_snapshot["cycle"]["id"]
        recovery_count = connection.execute(
            "SELECT recovery_count FROM public.v4_cycles WHERE id=%s", (cycle_id,)
        ).fetchone()[0]
        cycle_event = cancel_snapshot["cycle_events"][0]
        event_payload = connection.execute(
            "SELECT payload FROM public.v4_cycle_events WHERE id=%s",
            (cycle_event["id"],),
        ).fetchone()[0]
        cycle_outbox = cancel_snapshot["cycle_outbox"][0]
        outbox_attempts = connection.execute(
            "SELECT attempts FROM public.v4_cycle_outbox WHERE id=%s",
            (cycle_outbox["id"],),
        ).fetchone()[0]

    with psycopg.connect(database) as connection:
        connection.execute(
            f"ALTER TABLE public.{TABLE} DISABLE TRIGGER USER"
        )
        connection.execute(
            f"UPDATE public.{TABLE} SET reason='expired_permit_receipt' WHERE operation_id=%s",
            (cancelled_operation_id,),
        )
        connection.execute(f"ALTER TABLE public.{TABLE} ENABLE TRIGGER USER")
    try:
        with pytest.raises(PublicationRecoveryConflictError):
            verify_publication_recovery_rollback(
                database,
                workspace_id=workspace_id,
                operation_ids=operation_ids,
            )
        assert_script_fails_without_snapshots(
            run_script(
                expired_id=expired_operation_id,
                cancelled_id=cancelled_operation_id,
            )
        )
    finally:
        with psycopg.connect(database) as connection:
            connection.execute(
                f"ALTER TABLE public.{TABLE} DISABLE TRIGGER USER"
            )
            connection.execute(
                f"UPDATE public.{TABLE} SET reason='published_during_cancel' WHERE operation_id=%s",
                (cancelled_operation_id,),
            )
            connection.execute(f"ALTER TABLE public.{TABLE} ENABLE TRIGGER USER")

    from psycopg.types.json import Jsonb

    mutate_and_restore(
        "v4_legacy_publication_fixture_receipts",
        "UPDATE public.v4_legacy_publication_fixture_receipts SET receipt=jsonb_set(receipt,'{rollback_test}','true'::jsonb) WHERE operation_id=%s",
        (cancelled_operation_id,),
        "UPDATE public.v4_legacy_publication_fixture_receipts SET receipt=%s WHERE operation_id=%s",
        (Jsonb(receipt), cancelled_operation_id),
    )
    mutate_and_restore(
        "v4_legacy_publication_fixture_progress",
        "UPDATE public.v4_legacy_publication_fixture_progress SET polls=polls+1 WHERE operation_id=%s",
        (cancelled_operation_id,),
        "UPDATE public.v4_legacy_publication_fixture_progress SET polls=%s WHERE operation_id=%s",
        (progress_polls, cancelled_operation_id),
    )
    mutate_and_restore(
        "external_effects",
        "UPDATE public.external_effects SET updated_at=updated_at+interval '1 second' WHERE id=%s",
        (effect["id"],),
        "UPDATE public.external_effects SET updated_at=%s WHERE id=%s",
        (effect_updated_at, effect["id"]),
    )
    mutate_and_restore(
        "jobs",
        "UPDATE public.jobs SET updated_at=updated_at+interval '1 second' WHERE id=%s",
        (job_id,),
        "UPDATE public.jobs SET updated_at=%s WHERE id=%s",
        (job_updated_at, job_id),
    )
    mutate_and_restore(
        "jobs",
        "UPDATE public.jobs SET output_payload=%s WHERE id=%s",
        (Jsonb({"rollback_test": True}), job_id),
        "UPDATE public.jobs SET output_payload=%s WHERE id=%s",
        (Jsonb(output_payload) if output_payload is not None else None, job_id),
    )
    mutate_and_restore(
        "budget_reservations",
        "UPDATE public.budget_reservations SET updated_at=updated_at+interval '1 second' WHERE id=%s",
        (reservation["id"],),
        "UPDATE public.budget_reservations SET updated_at=%s WHERE id=%s",
        (reservation_updated_at, reservation["id"]),
    )
    mutate_and_restore(
        "v4_cycles",
        "UPDATE public.v4_cycles SET recovery_count=recovery_count+1 WHERE id=%s",
        (cycle_id,),
        "UPDATE public.v4_cycles SET recovery_count=%s WHERE id=%s",
        (recovery_count, cycle_id),
    )
    mutate_and_restore(
        "v4_cycle_events",
        "UPDATE public.v4_cycle_events SET payload=jsonb_set(payload,'{rollback_test}','true'::jsonb) WHERE id=%s",
        (cycle_event["id"],),
        "UPDATE public.v4_cycle_events SET payload=%s WHERE id=%s",
        (Jsonb(event_payload), cycle_event["id"]),
    )
    mutate_and_restore(
        "v4_cycle_outbox",
        "UPDATE public.v4_cycle_outbox SET attempts=attempts+1 WHERE id=%s",
        (cycle_outbox["id"],),
        "UPDATE public.v4_cycle_outbox SET attempts=%s WHERE id=%s",
        (outbox_attempts, cycle_outbox["id"]),
    )
    mutate_and_restore(
        "publication_attempts",
        "UPDATE public.publication_attempts SET reconciliation=jsonb_set(reconciliation,'{rollback_test}','true'::jsonb) WHERE id=%s",
        (publication_attempt_id,),
        "UPDATE public.publication_attempts SET reconciliation=%s WHERE id=%s",
        (Jsonb(publication_attempt_reconciliation), publication_attempt_id),
    )


async def _create_expired_operation_in_database(
    database, reference_fixture, monkeypatch, tmp_path
):
    from test_v4_legacy_publication import (
        _allow,
        _hard_exit_publication_worker,
        _publication_fixture,
    )

    import test_creative_release_gate_migration as release_gate

    original_ready_package = release_gate._approved_ready_package

    async def reuse_approved_ready_package(*, publication_proofs=True):
        return reference_fixture["ready"]

    monkeypatch.setattr(
        release_gate, "_approved_ready_package", reuse_approved_ready_package
    )
    try:
        fixture = await _publication_fixture(database)
    finally:
        monkeypatch.setattr(
            release_gate, "_approved_ready_package", original_ready_package
        )

    assert fixture["workspace_id"] == reference_fixture["workspace_id"]
    assert fixture["queue"] != reference_fixture["queue"]
    submitted = fixture["bridge"].submit_publication(fixture["command"])
    operation_id = submitted["operation_id"]
    _allow(
        database,
        fixture["workspace_id"],
        fixture["actor_id"],
        ("legacy:publication:reconcile:account:" + fixture["account"].id,),
    )
    assert await _hard_exit_publication_worker(
        fixture,
        tmp_path,
        after_acceptance=True,
        permit_ttl_seconds=5,
        operation_id=operation_id,
    ) == 73
    deadline = time.monotonic() + 15
    while True:
        with psycopg.connect(database) as connection:
            expired = connection.execute(
                """SELECT permit.expires_at<=clock_timestamp()
                   FROM public.v4_dispatch_permits permit
                   JOIN public.v4_permit_claims claim
                     ON claim.current_permit_id=permit.id
                   WHERE claim.operation_id=%s""",
                (operation_id,),
            ).fetchone()
        assert expired is not None, "publication permit should remain inspectable"
        if expired[0]:
            break
        if time.monotonic() >= deadline:
            pytest.fail("publication permit did not expire before the bounded deadline")
        await asyncio.sleep(0.05)
    return fixture, submitted


def _set_high_precision_output(database, operation_id):
    with psycopg.connect(database) as connection:
        connection.execute("ALTER TABLE public.jobs DISABLE TRIGGER USER")
        changed = connection.execute(
            """UPDATE public.jobs job
               SET output_payload='{"high_precision":0.1234567890123456789}'::jsonb
               FROM public.v4_legacy_dispatches binding
               WHERE binding.operation_id=%s AND binding.job_id=job.id""",
            (operation_id,),
        )
        connection.execute("ALTER TABLE public.jobs ENABLE TRIGGER USER")
    assert changed.rowcount == 1


def test_v1_snapshot_has_exact_ordered_21_key_projection(
    published_cancelled_recovery_case,
):
    from salience.cycles import publication_recovery as recovery

    fixture, submitted = published_cancelled_recovery_case
    operation_id = UUID(submitted["operation_id"])
    hold = recovery.record_publication_recovery_hold(
        fixture["database"],
        workspace_id=fixture["workspace_id"],
        operation_id=operation_id,
        reason="published_during_cancel",
    )

    projection = recovery._LIABILITY_SNAPSHOT_V1_SELECT
    projection_keys = tuple(
        re.findall(r"^\s*'([a-z_]+)'\s*,", projection, flags=re.MULTILINE)
    )
    assert projection_keys == _EXPECTED_V1_KEYS
    assert tuple(recovery._LIABILITY_SNAPSHOT_V1_KEYS) == _EXPECTED_V1_KEYS

    with psycopg.connect(fixture["database"]) as connection:
        stored = connection.execute(
            f"""SELECT liability_snapshot, liability_snapshot_version,
                       liability_snapshot_v1, liability_fingerprint_v1,
                       liability_snapshot_v1::text,
                       encode(public.digest(liability_snapshot_v1::text, 'sha256'), 'hex')
                FROM public.{TABLE} WHERE id=%s""",
            (hold.id,),
        ).fetchone()
        canonical = connection.execute(
            _SNAPSHOT_SQL, (operation_id,)
        ).fetchone()[0]
        old_projection = connection.execute(
            _RECOVERY_HOLD_SNAPSHOT_SQL, (operation_id,)
        ).fetchone()[0]
        projection_texts = connection.execute(
            "SELECT %s::jsonb::text, %s::jsonb::text",
            (Jsonb(stored[0]), Jsonb(old_projection)),
        ).fetchone()
        canonical_fingerprint = stored[5]
        array_types = connection.execute(
            """SELECT jsonb_typeof(%s::jsonb->'remote_publication_receipts'),
                      jsonb_typeof(%s::jsonb->'publication_status_events'),
                      jsonb_typeof(%s::jsonb->'cycle_events'),
                      jsonb_typeof(%s::jsonb->'cycle_outbox'),
                      jsonb_typeof(%s::jsonb->'job_checkpoints'),
                      jsonb_typeof(%s::jsonb->'audit_events'),
                      jsonb_typeof(%s::jsonb->'provenance_records')""",
            (Jsonb(stored[2]),) * 7,
        ).fetchone()

    assert stored[1] == "salience.publication-liability.v1"
    assert set(stored[2]) == set(_EXPECTED_V1_KEYS)
    assert stored[2] == canonical
    assert stored[2]["output"] is None
    assert stored[2]["remote_publication_receipts"] == []
    assert array_types == ("array",) * 7
    assert stored[0] == old_projection
    assert set(stored[0]) == set(_EXPECTED_V1_KEYS) - {
        "output",
        "remote_publication_receipts",
        "job_checkpoints",
        "audit_events",
        "provenance_records",
    }
    assert stored[3] == canonical_fingerprint
    assert projection_texts[0] == projection_texts[1]


@pytest.mark.parametrize(
    ("key", "relation", "alias", "filter_sql", "order_sql"),
    [
        (
            "remote_publication_receipts",
            "remote_publication_receipts",
            "remote_receipt",
            "remote_receipt.publication_attempt_id=publication_attempt_row.id",
            "remote_receipt.created_at,remote_receipt.id",
        ),
        (
            "publication_status_events",
            "publication_status_events",
            "status_event",
            "status_event.publication_attempt_id=publication_attempt_row.id",
            "status_event.sequence_no,status_event.id",
        ),
        (
            "cycle_events",
            "v4_cycle_events",
            "cycle_event",
            "cycle_event.cycle_id=cycle.id",
            "cycle_event.created_at,cycle_event.id",
        ),
        (
            "cycle_outbox",
            "v4_cycle_outbox",
            "outbox",
            "outbox.cycle_id=cycle.id",
            "outbox.sequence,outbox.id",
        ),
        (
            "job_checkpoints",
            "job_checkpoints",
            "checkpoint",
            "checkpoint.job_id=job.id",
            "checkpoint.sequence_no,checkpoint.id",
        ),
        (
            "audit_events",
            "audit_events",
            "audit_event",
            "audit_event.job_id=job.id",
            "audit_event.created_at,audit_event.sequence_no,audit_event.id",
        ),
        (
            "provenance_records",
            "provenance_records",
            "provenance",
            "provenance.job_id=job.id",
            "provenance.created_at,provenance.id",
        ),
    ],
    ids=[
        "remote-publication-receipts",
        "publication-status-events",
        "cycle-events",
        "cycle-outbox",
        "job-checkpoints",
        "audit-events",
        "provenance-records",
    ],
)
def test_v1_collection_filters_and_ordering_match_spec(
    key, relation, alias, filter_sql, order_sql
):
    from salience.cycles import publication_recovery as recovery

    app_projection = re.sub(
        r"\s+", "", recovery._LIABILITY_SNAPSHOT_V1_SELECT
    ).lower()
    migration_source = (
        ROOT / "migrations/versions/0043_complete_publication_recovery_liabilities.py"
    ).read_text()
    migration_projection_start = migration_source.index("SELECT jsonb_build_object(")
    migration_projection_end = migration_source.index(
        ") INTO expected_snapshot", migration_projection_start
    )
    migration_projection = re.sub(
        r"\s+",
        "",
        migration_source[migration_projection_start:migration_projection_end],
    ).lower()
    expected = re.sub(
        r"\s+",
        "",
        f"'{key}', COALESCE((SELECT jsonb_agg(to_jsonb({alias}) ORDER BY {order_sql}) "
        f"FROM public.{relation} {alias} WHERE {filter_sql}), '[]'::jsonb)",
    ).lower()
    assert expected in app_projection
    assert expected in migration_projection


@pytest.mark.asyncio
async def test_record_replay_verifier_share_atomic_v1_snapshot(
    published_cancelled_recovery_case, monkeypatch, tmp_path
):
    from salience.cycles import publication_recovery as recovery

    fixture, cancelled = published_cancelled_recovery_case
    database = fixture["database"]
    workspace_id = fixture["workspace_id"]
    cancelled_operation_id = UUID(cancelled["operation_id"])
    from temporalio.client import Client

    from salience.cycles.outbox import CycleOutbox
    from salience.cycles.runtime import TemporalCycleTransport

    client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    outbox = CycleOutbox(
        database, workspace_id=workspace_id, delivery_lane="legacy"
    )
    assert await outbox.dispatch_one(
        TemporalCycleTransport(client, task_queue=fixture["queue"])
    )
    _set_high_precision_output(database, cancelled_operation_id)
    before_cancelled = _liability_snapshot(database, cancelled_operation_id)

    original_connect = recovery.psycopg.connect
    observed_connections = []
    transaction_statuses = []
    transaction_boundaries = []

    def record_statement(statement, statements):
        statement = str(statement)
        statements.append(statement)
        if re.match(
            r"\s*(?:BEGIN|START\s+TRANSACTION|COMMIT|END|ROLLBACK|ABORT|"
            r"SAVEPOINT|RELEASE|PREPARE\s+TRANSACTION)\b",
            statement,
            flags=re.IGNORECASE,
        ):
            transaction_boundaries.append(statement)

    class CursorObserver:
        def __init__(self, cursor, observer):
            self.cursor = cursor
            self.observer = observer

        def __enter__(self):
            self.cursor.__enter__()
            return self

        def __exit__(self, *args):
            return self.cursor.__exit__(*args)

        def execute(self, statement, *args, **kwargs):
            record_statement(statement, self.observer.statements)
            return self.cursor.execute(statement, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self.cursor, name)

    class ConnectionObserver:
        def __init__(self, connection):
            self.connection = connection
            self.statements = []
            self.context_entries = 0
            self.context_exits = 0
            self.explicit_commit_calls = 0
            self.explicit_rollback_calls = 0
            self.transaction_context_calls = 0

        def __enter__(self):
            self.connection.__enter__()
            self.context_entries += 1
            return self

        def __exit__(self, *args):
            before = self.connection.info.transaction_status
            try:
                return self.connection.__exit__(*args)
            finally:
                self.context_exits += 1
                transaction_statuses.append(
                    (before, self.connection.info.transaction_status)
                )

        def execute(self, statement, *args, **kwargs):
            record_statement(statement, self.statements)
            return self.connection.execute(statement, *args, **kwargs)

        def cursor(self, *args, **kwargs):
            return CursorObserver(self.connection.cursor(*args, **kwargs), self)

        def commit(self):
            self.explicit_commit_calls += 1
            return self.connection.commit()

        def rollback(self):
            self.explicit_rollback_calls += 1
            return self.connection.rollback()

        def transaction(self, *args, **kwargs):
            self.transaction_context_calls += 1
            return self.connection.transaction(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self.connection, name)

    def observed_connect(*args, **kwargs):
        observer = ConnectionObserver(original_connect(*args, **kwargs))
        observed_connections.append(observer)
        return observer

    monkeypatch.setattr(recovery.psycopg, "connect", observed_connect)
    try:
        cancelled_hold = recovery.record_publication_recovery_hold(
            database,
            workspace_id=workspace_id,
            operation_id=cancelled_operation_id,
            reason="published_during_cancel",
        )
    finally:
        monkeypatch.setattr(recovery.psycopg, "connect", original_connect)

    assert len(observed_connections) == 1
    inserts = [
        statement
        for statement in observed_connections[0].statements
        if re.match(r"\s*INSERT\b", statement, flags=re.IGNORECASE)
    ]
    assert len(inserts) == 1
    assert all(
        column in inserts[0]
        for column in (
            "liability_snapshot",
            "liability_snapshot_version",
            "liability_snapshot_v1",
            "liability_fingerprint_v1",
        )
    )
    assert observed_connections[0].context_entries == 1
    assert observed_connections[0].context_exits == 1
    assert len(transaction_statuses) == 1
    assert transaction_statuses[0][0].name == "INTRANS"
    assert observed_connections[0].explicit_commit_calls == 0
    assert observed_connections[0].explicit_rollback_calls == 0
    assert observed_connections[0].transaction_context_calls == 0
    assert transaction_boundaries == []

    cancelled_replay = recovery.record_publication_recovery_hold(
        database,
        workspace_id=workspace_id,
        operation_id=cancelled_operation_id,
        reason="published_during_cancel",
    )
    expired_fixture, expired = await _create_expired_operation_in_database(
        database, fixture, monkeypatch, tmp_path
    )
    expired_operation_id = UUID(expired["operation_id"])
    _set_high_precision_output(database, expired_operation_id)
    before_expired = _liability_snapshot(database, expired_operation_id)
    expired_hold = recovery.record_publication_recovery_hold(
        database,
        workspace_id=workspace_id,
        operation_id=expired_operation_id,
        reason="expired_permit_receipt",
    )
    verified = recovery.verify_publication_recovery_rollback(
        database,
        workspace_id=workspace_id,
        operation_ids={
            "expired_permit_receipt": expired_operation_id,
            "published_during_cancel": cancelled_operation_id,
        },
    )
    with pytest.raises(recovery.PublicationRecoveryConflictError):
        recovery.verify_publication_recovery_rollback(
            database,
            workspace_id=uuid4(),
            operation_ids={
                "expired_permit_receipt": expired_operation_id,
                "published_during_cancel": cancelled_operation_id,
            },
        )

    assert cancelled_replay.id == cancelled_hold.id
    assert expired_fixture["workspace_id"] == workspace_id
    assert expired_hold.reason == "expired_permit_receipt"
    assert tuple(view.id for view in verified) == (
        expired_hold.id,
        cancelled_hold.id,
    )
    assert _liability_snapshot(database, cancelled_operation_id) == before_cancelled
    assert _liability_snapshot(database, expired_operation_id) == before_expired
    with psycopg.connect(database) as connection:
        rows = connection.execute(
            f"""SELECT operation_id, liability_snapshot, liability_snapshot_v1,
                       liability_snapshot_v1::text,
                       encode(public.digest(liability_snapshot_v1::text, 'sha256'), 'hex'),
                       liability_fingerprint_v1
                FROM public.{TABLE}
                WHERE operation_id = ANY(%s) ORDER BY reason""",
            ([cancelled_operation_id, expired_operation_id],),
        ).fetchall()
        for (
            operation_id,
            old_snapshot,
            v1_snapshot,
            v1_canonical_text,
            database_fingerprint,
            fingerprint,
        ) in rows:
            assert old_snapshot == connection.execute(
                _RECOVERY_HOLD_SNAPSHOT_SQL, (operation_id,)
            ).fetchone()[0]
            assert v1_snapshot == connection.execute(
                _SNAPSHOT_SQL, (operation_id,)
            ).fetchone()[0]
            assert "0.1234567890123456789" in v1_canonical_text
            assert database_fingerprint == fingerprint


@pytest.mark.asyncio
async def test_legacy_null_v1_hold_is_inspectable_but_not_replayable_or_verifiable(
    published_cancelled_recovery_case, monkeypatch, tmp_path
):
    from salience.cycles.publication_recovery import (
        PublicationRecoveryConflictError,
        load_publication_recovery_hold,
        record_publication_recovery_hold,
        verify_publication_recovery_rollback,
    )

    fixture, cancelled = published_cancelled_recovery_case
    database = fixture["database"]
    workspace_id = fixture["workspace_id"]
    cancelled_operation_id = UUID(cancelled["operation_id"])
    from temporalio.client import Client

    from salience.cycles.outbox import CycleOutbox
    from salience.cycles.runtime import TemporalCycleTransport

    client = await Client.connect(os.environ["TEST_TEMPORAL_TARGET"])
    outbox = CycleOutbox(
        database, workspace_id=workspace_id, delivery_lane="legacy"
    )
    assert await outbox.dispatch_one(
        TemporalCycleTransport(client, task_queue=fixture["queue"])
    )
    before_cancelled = _liability_snapshot(database, cancelled_operation_id)

    downgraded = _migrate(database, "downgrade", REVISION)
    assert downgraded.returncode == 0, downgraded.stderr
    with psycopg.connect(database) as connection:
        connection.execute(
            _INSERT_SQL,
            _hold_values(
                connection,
                cancelled_operation_id,
                reason="published_during_cancel",
            ),
        )
    upgraded = _migrate(database, "upgrade", REVISION_0043)
    assert upgraded.returncode == 0, upgraded.stderr

    with psycopg.connect(database) as connection:
        legacy_columns = connection.execute(
            f"""SELECT liability_snapshot_version, liability_snapshot_v1,
                       liability_fingerprint_v1
                FROM public.{TABLE} WHERE operation_id=%s""",
            (cancelled_operation_id,),
        ).fetchone()
        inspected = load_publication_recovery_hold(
            connection,
            workspace_id=workspace_id,
            operation_id=cancelled_operation_id,
        )
    assert legacy_columns == (None, None, None)
    assert inspected is not None
    assert inspected.reason == "published_during_cancel"

    with pytest.raises(PublicationRecoveryConflictError):
        record_publication_recovery_hold(
            database,
            workspace_id=workspace_id,
            operation_id=cancelled_operation_id,
            reason="published_during_cancel",
        )

    _, expired = await _create_expired_operation_in_database(
        database, fixture, monkeypatch, tmp_path
    )
    expired_operation_id = UUID(expired["operation_id"])
    before_expired = _liability_snapshot(database, expired_operation_id)
    expired_hold = record_publication_recovery_hold(
        database,
        workspace_id=workspace_id,
        operation_id=expired_operation_id,
        reason="expired_permit_receipt",
    )
    with pytest.raises(PublicationRecoveryConflictError):
        verify_publication_recovery_rollback(
            database,
            workspace_id=workspace_id,
            operation_ids={
                "expired_permit_receipt": expired_operation_id,
                "published_during_cancel": cancelled_operation_id,
            },
        )

    assert expired_hold.reason == "expired_permit_receipt"
    assert _liability_snapshot(database, cancelled_operation_id) == before_cancelled
    assert _liability_snapshot(database, expired_operation_id) == before_expired
    with psycopg.connect(database) as connection:
        assert connection.execute(
            f"""SELECT liability_snapshot_version, liability_snapshot_v1,
                       liability_fingerprint_v1
                FROM public.{TABLE} WHERE operation_id=%s""",
            (cancelled_operation_id,),
        ).fetchone() == (None, None, None)
