from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from salience.cycles import p1_recovery_rollback as rollback
from salience.cycles import production_recovery as recovery
from test_p1_production_recovery_case_commands import _case_fixture, _command
from test_p1_production_recovery import (
    _case_work, _production_recovery_operation, _rollback_v2_readbacks, _rollback_v2_valid_operation,
)
from test_v4_migrations import _migration


def _upgrade(database):
    result = _migration(database, "upgrade", "0053_p1_recovery_rollback_v3")
    assert result.returncode == 0, result.stdout + result.stderr


def test_successor_rollback_preserves_v2_inventory_and_authenticates_new_head():
    with _rollback_v2_valid_operation("rollback_case_v3_baseline_") as (
        database, authority, _, work, _,
    ):
        workspace_id = authority["workspace_id"]
        before = rollback.read_p1_recovery_rollback_inventory_v2(database, workspace_id=workspace_id)
        _upgrade(database)

        with pytest.raises(rollback.RecoveryRollbackVerificationError, match="exact 0051"):
            rollback.read_p1_recovery_rollback_inventory_v2(database, workspace_id=workspace_id)
        after = rollback.read_p1_recovery_rollback_inventory_v3(database, workspace_id=workspace_id)
        assert after.migration_head == "0053_p1_recovery_rollback_v3"
        assert after.digest != before.digest
        assert after.route_claims == before.route_claims
        assert after.inventory["recovery_work"] == before.inventory["recovery_work"]
        assert after.inventory["recovery_work"][0]["work_id"] == str(work.work_id)
        assert after.inventory["case_lineage"] == []

        route, temporal = _rollback_v2_readbacks(after.route_claims)
        verified = rollback.verify_p1_recovery_rollback_v3(
            database, workspace_id=workspace_id, route_authority=route, temporal_readback=temporal,
        )
        assert verified.digest == after.digest
        assert verified.temporal_readbacks and verified.readback_digest

        with psycopg.connect(database) as connection:
            connection.execute("INSERT INTO alembic_version(version_num) VALUES ('unexpected_branch')")
        with pytest.raises(rollback.RecoveryRollbackVerificationError, match="exact 0053"):
            rollback.read_p1_recovery_rollback_inventory_v3(database, workspace_id=workspace_id)


def test_successor_verifier_does_not_discard_unrelated_inventory_blockers():
    with _rollback_v2_valid_operation("rollback_case_v3_mismatch_") as (
        database, authority, _, _, _,
    ):
        workspace_id = authority["workspace_id"]
        _upgrade(database)
        with psycopg.connect(database) as connection:
            connection.execute("SET LOCAL session_replication_role=replica")
            connection.execute(
                "UPDATE v4_stop_scopes SET stopped=false WHERE workspace_id=%s AND scope_key='workspace'",
                (workspace_id,),
            )
        with pytest.raises(rollback.RecoveryRollbackVerificationError, match="stop_scope_not_active"):
            rollback.read_p1_recovery_rollback_inventory_v3(database, workspace_id=workspace_id)


def test_successor_inventory_requires_proof_for_an_advanced_case_revision():
    with _production_recovery_operation("rollback_unproved_case_v3_", task8_dispatch=True) as (
        database, _, authority, _,
    ):
        work = _case_work(recovery.ProductionRecoveryWorkRepository(database), authority, recovery)
        _upgrade(database)
        with psycopg.connect(database) as connection:
            baseline = connection.execute(
                "SELECT public.v4_p1_rollback_case_lineage_v3(%s,%s)", (work.workspace_id, work.work_id),
            ).fetchone()[0]
            assert baseline["current_state"] == work.state
            assert baseline["current_revision"] == work.source_revision

            connection.execute("SET LOCAL session_replication_role=replica")
            connection.execute(
                "UPDATE v4_recovery_cases SET state='suspended', revision=revision+1 WHERE id=%s",
                (work.source_id,),
            )
            snapshot = connection.execute(
                "SELECT public.v4_p1_recovery_rollback_inventory_v3(%s)", (work.workspace_id,),
            ).fetchone()[0]
            rejection = {
                blocker["kind"] for blocker in snapshot["blockers"]
                if blocker.get("work_id") == str(work.work_id)
            }
            assert rejection >= {
                "recovery_source_join_missing_or_mismatched",
                "production_case_lineage_missing_or_mismatched",
            }
            assert snapshot["case_lineage"] == []


@pytest.mark.parametrize("drift", ["case_event", "command_fingerprint", "cycle_event", "policy_binding"])
def test_successor_inventory_accepts_canonical_command_and_holds_when_its_evidence_drifts(drift):
    with _case_fixture() as (database, work, approval_id, authority):
        _upgrade(database)
        controller = recovery.ProductionRecoveryCaseController(
            recovery.ProductionRecoveryWorkRepository(database), authority=authority, commands=None,
        )
        review = _command(work, "review_approve", 1, approval_id=approval_id, reason="reviewed")
        approved = controller.execute_case_command(review)
        with psycopg.connect(database) as connection:
            snapshot = connection.execute(
                "SELECT public.v4_p1_recovery_rollback_inventory_v3(%s)", (work.workspace_id,),
            ).fetchone()[0]
            lineage = snapshot["case_lineage"][0]
            assert (lineage["current_state"], lineage["current_revision"]) == ("retry_due", 2)
            assert lineage["command_receipts"][0]["command_id"] == str(approved.command_id)
            assert not any(
                item.get("work_id") == str(work.work_id) and item["kind"] in {
                    "recovery_source_join_missing_or_mismatched",
                    "production_case_lineage_missing_or_mismatched",
                }
                for item in snapshot["blockers"]
            )

            connection.execute("SET LOCAL session_replication_role=replica")
            if drift == "case_event":
                connection.execute(
                    "UPDATE v4_case_events SET payload=payload- 'authorization' WHERE case_id=%s AND action='review_approve'",
                    (work.source_id,),
                )
            elif drift == "command_fingerprint":
                connection.execute(
                    "UPDATE v4_case_commands SET fingerprint=repeat('a',64) WHERE id=%s",
                    (approved.command_id,),
                )
            elif drift == "cycle_event":
                connection.execute(
                    "DELETE FROM v4_cycle_events WHERE cycle_id=%s AND kind='production_case_review_approve'",
                    (work.cycle_id,),
                )
            else:
                result = connection.execute(
                    """UPDATE v4_case_commands
                       SET result=jsonb_set(result,'{authorization,policy_version_id}',to_jsonb(%s::text))
                       WHERE id=%s RETURNING result""",
                    ("00000000-0000-4000-8000-000000000000", approved.command_id),
                ).fetchone()[0]
                connection.execute(
                    "UPDATE v4_case_events SET payload=%s WHERE case_id=%s AND action='review_approve'",
                    (psycopg.types.json.Jsonb(result), work.source_id),
                )
                connection.execute(
                    "UPDATE v4_cycle_events SET payload=%s WHERE cycle_id=%s AND kind='production_case_review_approve'",
                    (psycopg.types.json.Jsonb(result), work.cycle_id),
                )
            snapshot = connection.execute(
                "SELECT public.v4_p1_recovery_rollback_inventory_v3(%s)", (work.workspace_id,),
            ).fetchone()[0]
            assert snapshot["case_lineage"] == []
            assert any(
                item["kind"] == "production_case_lineage_missing_or_mismatched"
                and item["work_id"] == str(work.work_id)
                for item in snapshot["blockers"]
            )


def test_successor_inventory_binds_archive_to_full_command_lineage():
    with _case_fixture() as (database, work, approval_id, authority):
        _upgrade(database)
        controller = recovery.ProductionRecoveryCaseController(
            recovery.ProductionRecoveryWorkRepository(database), authority=authority, commands=None,
        )
        controller.execute_case_command(
            _command(work, "review_approve", 1, approval_id=approval_id, reason="reviewed"),
        )
        controller.execute_case_command(_command(work, "resume", 2, approval_id=approval_id))
        with psycopg.connect(database) as connection:
            connection.execute(
                "UPDATE v4_cycles SET state='closed', disposition='completed', closed_at=clock_timestamp() WHERE id=%s",
                (work.cycle_id,),
            )
        controller.execute_case_command(_command(work, "terminalize", 3))
        archive = controller.execute_case_command(
            _command(work, "archive", 4, reason="retained", evidence_sha256="a" * 64,
                     retain_until=datetime.now(timezone.utc) + timedelta(days=7)),
        )
        with psycopg.connect(database) as connection:
            lineage = connection.execute(
                "SELECT public.v4_p1_rollback_case_lineage_v3(%s,%s)", (work.workspace_id, work.work_id),
            ).fetchone()[0]
            assert lineage["current_state"] == "terminal"
            assert [item["action"] for item in lineage["command_receipts"]] == [
                "review_approve", "resume", "terminalize",
            ]
            assert lineage["archive"]["record"]["id"] == str(archive.archive_id)
            assert lineage["archive"]["command_receipt"]["command_id"] == str(archive.command_id)

            connection.execute("SET LOCAL session_replication_role=replica")
            with connection.transaction() as savepoint:
                connection.execute("UPDATE v4_case_archives SET evidence='{}' WHERE id=%s", (archive.archive_id,))
                assert connection.execute(
                    "SELECT public.v4_p1_rollback_case_lineage_v3(%s,%s)", (work.workspace_id, work.work_id),
                ).fetchone() == (None,)
                raise psycopg.Rollback(savepoint)

            connection.execute("DELETE FROM v4_case_archives WHERE id=%s", (archive.archive_id,))
            assert connection.execute(
                "SELECT public.v4_p1_rollback_case_lineage_v3(%s,%s)", (work.workspace_id, work.work_id),
            ).fetchone() == (None,)


def test_successor_inventory_proves_authorized_deadline_transition():
    with _case_fixture(due=True) as (database, work, _, authority):
        _upgrade(database)
        controller = recovery.ProductionRecoveryCaseController(
            recovery.ProductionRecoveryWorkRepository(database), authority=authority, commands=None,
        )
        wake = controller.wake_deadline(work.work_id)
        with psycopg.connect(database) as connection:
            lineage = connection.execute(
                "SELECT public.v4_p1_rollback_case_lineage_v3(%s,%s)", (work.workspace_id, work.work_id),
            ).fetchone()[0]
        assert (lineage["current_revision"], lineage["current_state"]) == (2, "suspended")
        assert lineage["command_receipts"][0]["command_id"] == str(wake.command_result.command_id)
        assert lineage["command_receipts"][0]["recovery_event"]["event_id"] == str(wake.event_id)
