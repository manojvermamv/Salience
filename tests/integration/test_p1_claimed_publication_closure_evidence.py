"""Exercise the protected claimed-publication closure evidence boundary."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from time import sleep
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
import pytest

from salience.cycles import production_recovery as recovery
import test_p1_production_recovery_case_opening as opening
from test_p1_claimed_reserved_recovery import seed_claimed_reserved_publication
from test_p1_production_policy_revocation import _wait_for_pg_lock, accounting, cycles, policy
from test_p1_production_recovery import _task9_set_stop_scope
from test_v4_migrations import _migration


_REVISION = "0061_p1_claimed_closure_evidence"
_PREVIOUS_REVISION = "0060_p1_claimed_reissue_fence"
_WRITE = "public.v4_record_claimed_publication_closure_v1"
_READ = "public.v4_read_claimed_publication_closure_source_v1"
_INVENTORY = "public.v4_p1_recovery_rollback_inventory_v8"
_TABLE = "public.v4_claimed_publication_closures"


@pytest.fixture(scope="module")
def closure_database():
    with opening._opening_database() as database:
        yield database


@pytest.fixture
def closure_source(closure_database, monkeypatch, request):
    monkeypatch.setenv("TEST_DATABASE_URL", closure_database)
    case, authority, command = seed_claimed_reserved_publication(
        request.getfixturevalue("accounting"), closure_database,
    )
    case["closure_actor_id"] = command.actor_id
    receipt = opening._controller(closure_database, authority).open_case(command)
    return case, receipt, _evidence(case, command.request_fingerprint)


def _evidence(case, request_fingerprint):
    return {
        "operation_id": str(case["request"].operation_id),
        "permit_id": str(case["opening_permit_id"]),
        "claim_id": str(case["opening_claim_id"]),
        "generation": 1,
        "case_revision": 1,
        "workspace_stop_revision": 1,
        "goal_stop_revision": 1,
        "request_fingerprint": request_fingerprint,
        "environment_id": str(case["request"].route.environment_id),
        "namespace": "production",
        "barrier_id": str(uuid4()),
        "closure_id": str(uuid4()),
        "signer_sha256": "b" * 64,
        "signature_hex": "a" * 128,
        **_authorization(),
    }


def _authorization(*, expired=False):
    return {
        "policy_decision_id": str(uuid4()),
        "route_decision_id": str(uuid4()),
        "authority_expires_at": (
            datetime.now(timezone.utc) + timedelta(seconds=-1 if expired else 30)
        ).isoformat(),
    }


def _record(connection, receipt, evidence):
    return connection.execute(f"SELECT {_WRITE}(%s,%s)", (receipt.work_id, Jsonb(evidence))).fetchone()[0]


def test_record_retains_one_receipt_without_changing_publication_liability(closure_source):
    case, opening_receipt, evidence = closure_source
    database = case["database"]
    before = opening._publication_liabilities(database, opening_receipt.operation_id)
    with psycopg.connect(database) as connection:
        source = connection.execute(f"SELECT {_READ}(%s)", (opening_receipt.work_id,)).fetchone()[0]
        for field in (
            "operation_id", "permit_id", "claim_id", "generation", "request_fingerprint", "environment_id",
        ):
            assert source[field] == evidence[field]
        assert source["original_key"] == case["request"].idempotency_key
        assert source["provider_id"] == case["request"].provider_id
        assert source["account_id"] == str(case["request"].account_id)
        receipt = _record(connection, opening_receipt, evidence)
        assert receipt["schema_version"] == "salience.p1-claimed-publication-closure.v1"
        assert receipt["case_id"] == str(opening_receipt.case_id)
        assert receipt["work_id"] == str(opening_receipt.work_id)
        persisted_fields = evidence.keys() - {"workspace_stop_revision", "goal_stop_revision"}
        assert {key: receipt[key] for key in persisted_fields} == {
            key: evidence[key] for key in persisted_fields
        }
        assert datetime.fromisoformat(receipt["recorded_at"]) < datetime.fromisoformat(
            evidence["authority_expires_at"],
        )

    with psycopg.connect(database) as connection:
        assert _record(connection, opening_receipt, evidence | _authorization()) == receipt
        inventory = connection.execute(
            f"SELECT {_INVENTORY}(%s)", (case["request"].workspace_id,),
        ).fetchone()[0]
        assert inventory["migration_head"] == _REVISION
        assert inventory["schema_version"] == "salience.p1-recovery-rollback-inventory.v8"
        assert inventory["claimed_publication_closures"] == [receipt]
        assert inventory["relation_counts"]["claimed_publication_closures"] == 1
        assert not any("claimed_publication_closure" in item["kind"] for item in inventory["blockers"])

    assert opening._publication_liabilities(database, opening_receipt.operation_id) == before
    failed = _migration(database, "downgrade", _PREVIOUS_REVISION)
    assert failed.returncode != 0 and "claimed publication closure evidence" in failed.stderr


@pytest.mark.parametrize("field", ["operation_id", "permit_id", "claim_id", "generation", "case_revision",
                                   "environment_id", "request_fingerprint", "authority_expires_at"])
def test_writer_rejects_mismatched_or_expired_evidence(closure_source, field):
    case, receipt, evidence = closure_source
    invalid_value = {
        "generation": 2,
        "case_revision": 2,
        "request_fingerprint": "d" * 64,
        "authority_expires_at": _authorization(expired=True)["authority_expires_at"],
    }.get(field, str(uuid4()))
    with psycopg.connect(case["database"]) as connection:
        with pytest.raises((psycopg.errors.InvalidParameterValue, psycopg.errors.InsufficientPrivilege)):
            with connection.transaction():
                _record(connection, receipt, evidence | {field: invalid_value})
        assert connection.execute(
            f"SELECT count(*) FROM {_TABLE} WHERE work_id=%s", (receipt.work_id,),
        ).fetchone() == (0,)


def test_writer_rejects_conflicting_replay_and_requires_fresh_authority(closure_source):
    case, receipt, evidence = closure_source
    with psycopg.connect(case["database"]) as connection:
        recorded = _record(connection, receipt, evidence)
        with pytest.raises(psycopg.errors.InvalidParameterValue, match="closure evidence conflict"):
            with connection.transaction():
                _record(connection, receipt, evidence | {"closure_id": str(uuid4())})
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="authority"):
            with connection.transaction():
                _record(connection, receipt, evidence | _authorization(expired=True))
        assert _record(connection, receipt, evidence | _authorization()) == recorded


def test_writer_denies_after_a_remote_receipt_is_present(closure_source):
    case, receipt, evidence = closure_source
    with psycopg.connect(case["database"]) as connection:
        connection.execute(
            """INSERT INTO remote_publication_receipts (
                   publication_attempt_id,publisher_id,remote_id,state,safe_metadata_hash
               ) VALUES (%s,%s,%s,'published',%s)""",
            (case["publication_binding"].publication_attempt_id, case["request"].provider_id, str(uuid4()), "a" * 64),
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with connection.transaction():
                _record(connection, receipt, evidence)


@pytest.mark.parametrize("clear_stop", [False, True])
def test_writer_rechecks_stop_after_source_read(closure_source, clear_stop):
    case, receipt, evidence = closure_source
    database = case["database"]
    with psycopg.connect(database) as connection:
        assert connection.execute(f"SELECT {_READ}(%s)", (receipt.work_id,)).fetchone()[0]
    with psycopg.connect(database) as connection:
        _task9_set_stop_scope(
            connection, case["request"].workspace_id, "workspace", 2, True,
            case["closure_actor_id"], "closure-intake-stop",
        )
        if clear_stop:
            _task9_set_stop_scope(
                connection, case["request"].workspace_id, "workspace", 3, False,
                case["closure_actor_id"], "closure-intake-clear",
            )
    message = "current original claim, case and stops" if clear_stop else "current closure policy, stops"
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match=message):
        with psycopg.connect(database) as connection:
            _record(connection, receipt, evidence)
    if clear_stop:
        with psycopg.connect(database) as connection:
            _record(connection, receipt, evidence | {"workspace_stop_revision": 3})


def test_writer_rechecks_authority_after_waiting_for_the_insert(closure_source):
    case, receipt, evidence = closure_source
    database = case["database"]
    app_name = f"p1-closure-expiry-{uuid4().hex}"
    def record():
        with psycopg.connect(database, application_name=app_name) as connection:
            return _record(connection, receipt, evidence | {"authority_expires_at": expiry.isoformat()})

    with ThreadPoolExecutor(max_workers=1) as pool, psycopg.connect(database) as blocker:
        blocker.execute(f"LOCK TABLE {_TABLE} IN SHARE MODE")
        expiry = datetime.now(timezone.utc) + timedelta(seconds=2)
        future = pool.submit(record)
        assert _wait_for_pg_lock(database, app_name)
        sleep(max(0, (expiry - datetime.now(timezone.utc)).total_seconds()) + 0.02)
        blocker.commit()
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="authority expired during the write"):
            future.result(timeout=5)
    with psycopg.connect(database) as connection:
        assert connection.execute(
            f"SELECT count(*) FROM {_TABLE} WHERE work_id=%s", (receipt.work_id,),
        ).fetchone() == (0,)


def test_rollback_keeps_the_historical_receipt_after_terminalization(closure_database, monkeypatch, request):
    monkeypatch.setenv("TEST_DATABASE_URL", closure_database)
    case, authority, command = seed_claimed_reserved_publication(
        request.getfixturevalue("accounting"), closure_database,
    )
    controller = opening._controller(closure_database, authority)
    opened = controller.open_case(command)
    with psycopg.connect(closure_database) as connection:
        retained = _record(connection, opened, _evidence(case, command.request_fingerprint))
        connection.execute(
            """INSERT INTO permission_grants (
                   workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at
               ) VALUES (%s,'identity',%s,'production:recovery:case:terminalize','allow','{}',
                         clock_timestamp()+interval '1 hour')""",
            (command.workspace_id, str(command.actor_id)),
        )
        connection.execute(
            """UPDATE v4_cycles SET state='closed',disposition='cancelled',closed_at=clock_timestamp()
               WHERE id=%s""",
            (command.cycle_id,),
        )
        actor_revision, = connection.execute(
            "SELECT revision FROM identity_subjects WHERE id=%s", (command.actor_id,),
        ).fetchone()
        authority.bindings["actor_revision"] = actor_revision
    terminal = controller.execute_case_command(recovery.ProductionRecoveryCaseCommand(
        workspace_id=command.workspace_id, work_id=opened.work_id, case_id=opened.case_id,
        actor_id=command.actor_id, expected_revision=1, action="terminalize",
        idempotency_key=f"terminalize:{opened.case_id}",
    ))
    assert terminal.resulting_revision == 2
    with psycopg.connect(closure_database) as connection:
        inventory = connection.execute(f"SELECT {_INVENTORY}(%s)", (command.workspace_id,)).fetchone()[0]
    assert inventory["claimed_publication_closures"] == [retained]
    assert not any("claimed_publication_closure" in item["kind"] for item in inventory["blockers"])


def test_writer_is_restricted_and_table_rejects_direct_mutation(closure_source):
    case, receipt, evidence = closure_source
    with psycopg.connect(case["database"]) as connection, connection.transaction(force_rollback=True):
        role = sql.Identifier(f"p1_closure_test_{uuid4().hex[:12]}")
        connection.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(role))
        connection.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
        connection.execute(sql.SQL("SET LOCAL ROLE {}").format(role))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with connection.transaction():
                _record(connection, receipt, evidence)
        connection.execute("RESET ROLE")
        connection.execute(sql.SQL(f"GRANT EXECUTE ON FUNCTION {_WRITE}(uuid,jsonb) TO {{}}").format(role))
        connection.execute(sql.SQL(f"GRANT INSERT, UPDATE, DELETE, TRUNCATE, SELECT ON {_TABLE} TO {{}}").format(role))
        connection.execute(sql.SQL("SET LOCAL ROLE {}").format(role))
        persisted = _record(connection, receipt, evidence)
        for statement, parameters in (
            (f"INSERT INTO {_TABLE} SELECT * FROM {_TABLE} WHERE claim_id=%s", (persisted["claim_id"],)),
            (f"UPDATE {_TABLE} SET namespace='changed' WHERE claim_id=%s", (persisted["claim_id"],)),
            (f"DELETE FROM {_TABLE} WHERE claim_id=%s", (persisted["claim_id"],)),
            (f"TRUNCATE {_TABLE}", ()),
        ):
            with pytest.raises(psycopg.Error, match="closure evidence"):
                with connection.transaction():
                    connection.execute(statement, parameters)


def test_rollback_detects_protected_writer_definition_drift(closure_source):
    case, receipt, _ = closure_source
    with psycopg.connect(case["database"]) as connection, connection.transaction(force_rollback=True):
        connection.execute(f"ALTER FUNCTION {_WRITE}(uuid,jsonb) SET search_path=public")
        inventory = connection.execute(
            f"SELECT {_INVENTORY}(%s)", (case["request"].workspace_id,),
        ).fetchone()[0]
        assert {"kind": "production_claimed_publication_closure_definition_mismatch"} in inventory["blockers"]


def test_rollback_detects_closure_source_lineage_drift(closure_source):
    case, receipt, evidence = closure_source
    with psycopg.connect(case["database"]) as connection, connection.transaction(force_rollback=True):
        _record(connection, receipt, evidence)
        connection.execute("SET LOCAL session_replication_role=replica")
        connection.execute("UPDATE v4_case_commands SET result='{}'::jsonb WHERE id=%s", (receipt.command_id,))
        inventory = connection.execute(
            f"SELECT {_INVENTORY}(%s)", (case["request"].workspace_id,),
        ).fetchone()[0]
        assert {
            "kind": "production_claimed_publication_closure_lineage_mismatch",
            "work_id": str(receipt.work_id),
            "claim_id": evidence["claim_id"],
        } in inventory["blockers"]


def test_empty_closure_migration_can_downgrade_and_reapply():
    with opening._opening_database(_PREVIOUS_REVISION) as database:
        for direction, target in (
            ("upgrade", _REVISION), ("downgrade", _PREVIOUS_REVISION), ("upgrade", _REVISION),
        ):
            result = _migration(database, direction, target)
            assert result.returncode == 0, result.stdout + result.stderr
