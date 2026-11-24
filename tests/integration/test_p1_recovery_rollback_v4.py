"""Exercise current and historical rollback evidence with real PostgreSQL."""

from copy import deepcopy
from uuid import uuid4

import psycopg
import pytest

from salience.cycles import p1_recovery_rollback as rollback
from salience.cycles import production_recovery as recovery
from test_p1_production_policy_revocation import accounting, cycles, policy
from test_p1_production_recovery import _rollback_v2_readbacks, _rollback_v2_valid_operation, _task9_set_stop_scope
from test_p1_production_recovery_case_opening import _opening_database, opening_source
from test_v4_migrations import _migration


@pytest.fixture(scope="module")
def opening_database():
    with _opening_database(rollback.ROLLBACK_MIGRATION_HEAD_V4) as database:
        yield database


def test_successor_inventory_preserves_history_and_requires_its_exact_head():
    with _rollback_v2_valid_operation("rollback_open_v4_history_") as (
        database, authority, _, work, _,
    ):
        workspace = authority["workspace_id"]
        before = rollback.read_p1_recovery_rollback_inventory_v2(database, workspace_id=workspace)
        upgraded = _migration(database, "upgrade", rollback.ROLLBACK_MIGRATION_HEAD_V4)
        assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr

        with pytest.raises(rollback.RecoveryRollbackVerificationError, match="exact 0053"):
            rollback.read_p1_recovery_rollback_inventory_v3(database, workspace_id=workspace)
        after = rollback.read_p1_recovery_rollback_inventory_v4(database, workspace_id=workspace)
        assert after.migration_head == rollback.ROLLBACK_MIGRATION_HEAD_V4
        assert after.inventory["opening_receipts"] == []
        assert after.inventory["recovery_work"][0]["opening_command_id"] is None
        assert after.inventory["recovery_work"][0]["work_id"] == str(work.work_id)
        assert after.route_claims == before.route_claims
        route, temporal = _rollback_v2_readbacks(after.route_claims)
        verified = rollback.verify_p1_recovery_rollback_v4(
            database, workspace_id=workspace, route_authority=route, temporal_readback=temporal,
        )
        assert verified.digest == after.digest and verified.readback_digest
        after.inventory["recovery_work"][0].pop("opening_command_id")
        with pytest.raises(rollback.RecoveryRollbackVerificationError, match="opening provenance"):
            rollback.validate_recovery_rollback_inventory_v4(after.inventory, workspace_id=workspace)


def test_public_successor_verifier_returns_canonical_opening_receipt(opening_source):
    case, authority, request_fields = opening_source
    database = case["database"]
    controller = recovery.ProductionRecoveryCaseController(
        recovery.ProductionRecoveryWorkRepository(database), authority=authority, commands=None,
    )
    command = recovery.ProductionRecoveryCaseOpenCommand(**request_fields)
    receipt = controller.open_case(command)
    with psycopg.connect(database) as connection:
        for scope in ("workspace", f"goal:{case['goal_id']}"):
            _task9_set_stop_scope(connection, command.workspace_id, scope, 2, True, command.actor_id, "rollback-stop")

    inventory = rollback.read_p1_recovery_rollback_inventory_v4(database, workspace_id=command.workspace_id)
    route, temporal = _rollback_v2_readbacks(inventory.route_claims)
    verified = rollback.verify_p1_recovery_rollback_v4(
        database, workspace_id=command.workspace_id, route_authority=route, temporal_readback=temporal,
    )
    assert verified.inventory["opening_receipts"][0]["command_id"] == str(receipt.command_id)
    damaged = deepcopy(verified.inventory)
    del damaged["opening_receipts"][0]["claim_id"]
    with pytest.raises(rollback.RecoveryRollbackVerificationError, match="opening receipt is incomplete"):
        rollback.validate_recovery_rollback_inventory_v4(damaged, workspace_id=command.workspace_id)
    for field, value in (("authorization", {}), ("alert_event_id", str(uuid4()))):
        damaged = deepcopy(verified.inventory)
        damaged["opening_receipts"][0][field] = value
        with pytest.raises(rollback.RecoveryRollbackVerificationError, match="authority or linked evidence"):
            rollback.validate_recovery_rollback_inventory_v4(damaged, workspace_id=command.workspace_id)


@pytest.mark.parametrize("drift", [
    "case_audit", "cycle_audit", "initial_alert", "provenance", "missing_work", "alert_payload",
    "stop_revision",
])
def test_successor_inventory_binds_opening_receipt_and_fails_closed_on_drift(opening_source, drift):
    case, authority, request_fields = opening_source
    database = case["database"]
    controller = recovery.ProductionRecoveryCaseController(
        recovery.ProductionRecoveryWorkRepository(database), authority=authority, commands=None,
    )
    command = recovery.ProductionRecoveryCaseOpenCommand(**request_fields)
    receipt = controller.open_case(command)
    with psycopg.connect(database) as connection:
        def read_inventory():
            return connection.execute(
                "SELECT public.v4_p1_recovery_rollback_inventory_v4(%s)", (command.workspace_id,),
            ).fetchone()[0]
        evidence = read_inventory()
        assert [entry["command_id"] for entry in evidence["opening_receipts"]] == [str(receipt.command_id)]
        assert evidence["relation_counts"]["production_case_opening_receipts"] == 1
        assert evidence["case_lineage"][0]["source_revision"] == 1

        connection.execute("SET LOCAL session_replication_role=replica")
        mutations = {
            "case_audit": ("DELETE FROM v4_case_events WHERE case_id=%s AND action='opened'", receipt.case_id),
            "cycle_audit": ("DELETE FROM v4_cycle_events WHERE cycle_id=%s AND kind='production_case_open'", command.cycle_id),
            "initial_alert": ("DELETE FROM v4_production_recovery_events WHERE work_id=%s AND event_kind='alert_intent'", receipt.work_id),
            "provenance": ("UPDATE v4_production_recovery_work SET opening_command_id=NULL WHERE work_id=%s", receipt.work_id),
            "missing_work": ("DELETE FROM v4_production_recovery_work WHERE work_id=%s", receipt.work_id),
            "alert_payload": (
                """UPDATE v4_production_recovery_events SET payload=payload - 'deadline',
                   payload_sha256=encode(public.digest((payload - 'deadline')::text,'sha256'),'hex')
                   WHERE work_id=%s AND event_kind='alert_intent'""",
                receipt.work_id,
            ),
        }
        if drift == "stop_revision":
            for table, column, condition, target in (
                ("v4_case_commands", "result", "id=%s", receipt.command_id),
                ("v4_case_events", "payload", "case_id=%s AND action='opened'", receipt.case_id),
                ("v4_cycle_events", "payload", "cycle_id=%s AND kind='production_case_open'", command.cycle_id),
            ):
                assert connection.execute(
                    f"UPDATE {table} SET {column}=jsonb_set({column}, '{{authorization,goal_stop_revision}}', '999') "
                    f"WHERE {condition}", (target,),
                ).rowcount == 1
        else:
            query, target = mutations[drift]
            assert connection.execute(query, (target,)).rowcount == 1
        broken = read_inventory()
        assert broken["opening_receipts"] == []
        assert {
            "kind": "production_case_opening_missing_or_mismatched", "work_id": str(receipt.work_id),
        } in broken["blockers"]
