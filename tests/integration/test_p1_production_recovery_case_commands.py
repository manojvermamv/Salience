"""Production recovery commands against the canonical PostgreSQL case ledger."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from threading import Event
from time import monotonic, sleep
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb
import pytest

from salience.cycles import production_recovery as recovery
from test_p1_production_recovery import (
    _RecoveryAuthority,
    _production_recovery_operation,
    _recovery_policy,
    _task9_set_stop_scope,
)
from test_v4_migrations import _migration


_REVISION = "0052_p1_recovery_case_commands"
_FUNCTION = (
    "public.v4_apply_production_recovery_case_command_v1"
    "(uuid,uuid,uuid,uuid,integer,text,text,jsonb,jsonb)"
)


class _CaseAuthority(_RecoveryAuthority):
    def __init__(self, bindings):
        super().__init__(recovery)
        self.bindings = bindings
        self.available = True

    def authorize_case_command(self, command):
        if not self.available:
            raise PermissionError("current case command authority unavailable")
        return recovery.ProductionRecoveryCaseAuthorization(
            command=command,
            policy_decision_id=uuid4(),
            stage_decision_id=uuid4(),
            route_decision_id=uuid4(),
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=45),
            **self.bindings,
        )

    def authorize_deadline_wake(self, work, source, action_kind, idempotency_key):
        return replace(
            super().authorize_deadline_wake(work, source, action_kind, idempotency_key),
            stop_revision=self.bindings["goal_stop_revision"],
            workspace_stop_revision=self.bindings["workspace_stop_revision"],
        )


@contextmanager
def _case_fixture(*, claimed=False, apply_upgrade=True, due=False):
    with _production_recovery_operation("p1_case_cmd_", task8_dispatch=True) as (
        database, _, evidence, enrollment,
    ):
        if apply_upgrade:
            upgraded = _migration(database, "upgrade", _REVISION)
            assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
        actor_id, case_id = uuid4(), uuid4()
        cycle = evidence["cycle"]
        workspace_id = evidence["workspace_id"]
        policy_document = {
            "schema_version": "salience.p1-production-policy.v1",
            "allowed_effects": ["publication"],
            "approval_required_effects": ["publication"],
            "expires_at": "2099-01-01T00:00:00Z",
            "jurisdiction_refs": ["fixture"],
        }
        policy_hash = sha256(
            json.dumps(policy_document, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        with psycopg.connect(database) as connection:
            connection.execute(
                """INSERT INTO identity_subjects(id,workspace_id,issuer,subject,expires_at)
                   VALUES(%s,%s,'https://case-authority.invalid',%s,clock_timestamp()+interval '1 day')""",
                (actor_id, workspace_id, str(actor_id)),
            )
            for action in ("review", "resume", "terminalize", "archive"):
                connection.execute(
                    """INSERT INTO permission_grants(workspace_id,principal_type,principal_id,
                                                     scope,effect,constraints,expires_at)
                       VALUES(%s,'identity',%s,%s,'allow','{}',clock_timestamp()+interval '1 day')""",
                    (workspace_id, str(actor_id), f"production:recovery:case:{action}"),
                )
            actor_revision = connection.execute(
                "SELECT revision FROM identity_subjects WHERE id=%s",
                (actor_id,),
            ).fetchone()[0]
            # Earlier migration fixtures intentionally use minimal rows. Complete their
            # policy, context and authority bindings before creating a recovery source.
            connection.execute("SET LOCAL session_replication_role=replica")
            account_id = connection.execute(
                """UPDATE approval_requests SET request_context=request_context
                       || jsonb_build_object('policy_document_hash',%s::text)
                   WHERE id=%s RETURNING request_context->>'account_id'""",
                (policy_hash, evidence["approval_id"]),
            ).fetchone()[0]
            connection.execute(
                """UPDATE policy_versions SET document=%s,document_hash=%s,
                       effective_at=clock_timestamp()-interval '1 second'
                   WHERE id=%s""",
                (Jsonb(policy_document), policy_hash, evidence["policy_id"]),
            )
            connection.execute(
                """UPDATE v4_production_intent_enrollments SET policy_document_hash=%s WHERE intent_id=%s""",
                (policy_hash, cycle["intent_id"]),
            )
            connection.execute(
                """UPDATE v4_production_effect_permits
                   SET account_id=%s, policy_document_hash=%s, route_id=%s, slot_id=%s,
                       owner_id=%s,subject_revision=%s,
                       request_snapshot=request_snapshot || jsonb_build_object(
                           'account_id',%s::text,'policy_document_hash',%s::text,
                           'route_id',%s::uuid,'slot_id',%s::uuid,'owner_id',%s::uuid,
                           'subject_revision',%s::bigint)
                   WHERE permit_id=%s""",
                (account_id, policy_hash, enrollment["route_id"], enrollment["slot_id"],
                 enrollment["route_owner_id"], enrollment["subject_revision"],
                 account_id, policy_hash, enrollment["route_id"], enrollment["slot_id"],
                 enrollment["route_owner_id"], enrollment["subject_revision"], evidence["permit_id"]),
            )
            connection.execute(
                """UPDATE v4_run_contexts SET schema_version='RunContext.local.v3',
                       payload=payload || jsonb_build_object(
                           'schema_version','RunContext.local.v3','operation_id',%s::uuid,
                           'execution_deadline',clock_timestamp()+interval '1 hour','max_wakes',3)
                   WHERE id=%s""",
                (cycle["operation_id"], cycle["context_id"]),
            )
            if not claimed:
                # Model an expired issue with no claim using the already seeded permit.
                connection.execute(
                    "UPDATE v4_production_current_dispatch SET current_claim_id=NULL WHERE operation_id=%s",
                    (evidence["operation_id"],),
                )
                connection.execute(
                    "DELETE FROM v4_production_effect_permit_claims WHERE claim_id=%s",
                    (evidence["claim_id"],),
                )
            connection.execute("SET LOCAL session_replication_role=origin")
            connection.execute(
                "UPDATE v4_cycles SET state='reconciling' WHERE id=%s",
                (cycle["cycle_id"],),
            )
            connection.execute(
                """INSERT INTO v4_recovery_cases(
                      id,workspace_id,goal_id,target_cycle_id,context_id,operation_id,kind,
                      open_fingerprint,failure_class,state,owner_id,reason,deadline,max_actions)
                   VALUES(%s,%s,%s,%s,%s,%s,'manual_review',%s,'manual_review',
                          'awaiting_review',%s,'requires review',clock_timestamp()+%s::interval,3)""",
                (
                    case_id, workspace_id, cycle["goal_id"], cycle["cycle_id"],
                    cycle["context_id"], cycle["operation_id"], "d" * 64, actor_id,
                    "-1 minute" if due else "1 hour",
                ),
            )
        repository = recovery.ProductionRecoveryWorkRepository(database)
        work = repository.bind_cycle_case(case_id, policy=_recovery_policy(recovery))
        yield database, work, evidence["approval_id"], _CaseAuthority(
            {
                "policy_version_id": evidence["policy_id"],
                "policy_document_hash": policy_hash,
                "route_id": enrollment["route_id"],
                "slot_id": enrollment["slot_id"],
                "fencing_epoch": 7,
                "workspace_stop_revision": 1,
                "goal_stop_revision": 1,
                "actor_revision": actor_revision,
            }
        )


def _command(work, action, revision, *, approval_id=None, **fields):
    return recovery.ProductionRecoveryCaseCommand(
        workspace_id=work.workspace_id,
        work_id=work.work_id,
        case_id=work.source_id,
        actor_id=work.owner_id,
        expected_revision=revision,
        action=action,
        idempotency_key=f"case-command:{action}:{revision}",
        approval_request_id=approval_id,
        **fields,
    )


def test_production_case_commands_preserve_canonical_identity_and_replay():
    with _case_fixture() as (database, work, approval_id, authority):
        controller = recovery.ProductionRecoveryCaseController(
            recovery.ProductionRecoveryWorkRepository(database), authority=authority, commands=None,
        )
        review = _command(work, "review_approve", 1, approval_id=approval_id, reason="reviewed")
        approved = controller.execute_case_command(review)
        assert (approved.case_id, approved.source_revision, approved.resulting_revision, approved.state) == (
            work.source_id, 1, 2, "retry_due",
        )
        resume = _command(work, "resume", 2, approval_id=approval_id)
        resumed = controller.execute_case_command(resume)
        assert (resumed.case_id, resumed.source_revision, resumed.resulting_revision, resumed.state) == (
            work.source_id, 2, 3, "resolved",
        )
        terminal = _command(work, "terminalize", 3)
        with pytest.raises(PermissionError, match="terminal business disposition"):
            controller.execute_case_command(terminal)
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT id,intent_id,context_id,operation_id,state FROM v4_cycles WHERE id=%s",
                (work.cycle_id,),
            ).fetchone() == (work.cycle_id, work.intent_id, work.context_id, work.operation_id, "runnable")
            connection.execute(
                """UPDATE v4_cycles SET state='closed', disposition='completed',closed_at=clock_timestamp()
                   WHERE id=%s""",
                (work.cycle_id,),
            )
        closed = controller.execute_case_command(terminal)
        assert (closed.resulting_revision, closed.state, closed.disposition) == (4, "terminal", "completed")
        archive = _command(
            work, "archive", 4, reason="retained", evidence_sha256="f" * 64,
            retain_until=datetime.now(timezone.utc) + timedelta(days=5),
        )
        archived = controller.execute_case_command(archive)
        assert archived.archive_id is not None
        assert (archived.source_revision, archived.resulting_revision, archived.state) == (4, 4, "terminal")
        authority.available = False
        assert controller.execute_case_command(review) == approved
        assert controller.execute_case_command(resume) == resumed
        assert controller.execute_case_command(terminal) == closed
        assert controller.execute_case_command(archive) == archived
        with pytest.raises(recovery.RecoveryIdentityConflict, match="idempotency"):
            controller.execute_case_command(replace(archive, reason="changed"))
        with pytest.raises(recovery.RecoveryIdentityConflict, match="cannot be reused"):
            controller.execute_case_command(replace(archive, idempotency_key="archive-another-key"))
        with psycopg.connect(database) as connection:
            commands = connection.execute(
                """SELECT command.action, command.result=event.payload, event.case_revision
                   FROM v4_case_commands command JOIN v4_case_events event
                     ON event.case_id=command.case_id AND event.action=command.action
                   WHERE command.case_id=%s ORDER BY command.created_at""",
                (work.source_id,),
            ).fetchall()
            assert commands == [
                ("review_approve", True, 2), ("resume", True, 3),
                ("terminalize", True, 4), ("archive", True, 4),
            ]
            assert connection.execute(
                "SELECT evidence, reason FROM v4_case_archives WHERE id=%s",
                (archived.archive_id,),
            ).fetchone() == ({"sha256": "f" * 64}, "retained")
            assert connection.execute(
                "SELECT count(*) FROM v4_case_reviews WHERE case_id=%s",
                (work.source_id,),
            ).fetchone() == (0,)
            assert connection.execute(
                "SELECT count(*) FROM v4_case_notifications WHERE case_id=%s",
                (work.source_id,),
            ).fetchone() == (0,)
        downgrade = _migration(database, "downgrade", "0051_p1_recovery_rollback_v2")
        assert downgrade.returncode != 0
        assert "preserve production case command history" in downgrade.stderr
        with psycopg.connect(database) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (_REVISION,)


def test_case_command_boundary_is_missing_at_the_prior_revision():
    with _case_fixture(apply_upgrade=False) as (database, work, _, authority):
        controller = recovery.ProductionRecoveryCaseController(
            recovery.ProductionRecoveryWorkRepository(database), authority=authority, commands=None,
        )
        command = _command(work, "terminalize", 1)
        with pytest.raises(psycopg.errors.UndefinedFunction):
            controller.execute_case_command(command)
        with psycopg.connect(database) as connection:
            assert connection.execute("SELECT to_regprocedure(%s)", (_FUNCTION,)).fetchone() == (None,)


def test_only_the_granted_command_role_can_execute_or_record_production_receipts():
    with _case_fixture() as (database, work, approval_id, authority):
        role = "p1_case_cmd_" + uuid4().hex
        connection_url = make_conninfo(database, options=f"-c role={role}")
        controller = recovery.ProductionRecoveryCaseController(
            recovery.ProductionRecoveryWorkRepository(connection_url), authority=authority, commands=None,
        )
        command = _command(work, "review_approve", 1, approval_id=approval_id, reason="reviewed")
        with psycopg.connect(database) as connection:
            connection.execute(sql.SQL("CREATE ROLE {}").format(sql.Identifier(role)))
        try:
            with pytest.raises(PermissionError, match="permission denied"):
                controller.execute_case_command(command)
            with psycopg.connect(database) as connection:
                connection.execute(
                    sql.SQL("GRANT EXECUTE ON FUNCTION {} TO {}").format(
                        sql.SQL(_FUNCTION), sql.Identifier(role),
                    )
                )
                connection.execute(
                    sql.SQL("GRANT INSERT ON v4_case_commands TO {}").format(sql.Identifier(role))
                )
            with psycopg.connect(connection_url) as restricted:
                with pytest.raises(psycopg.errors.InsufficientPrivilege, match="authorized command"):
                    with restricted.transaction():
                        restricted.execute("SELECT set_config('salience.production_case_command_v1',%s,true)",
                                           (str(work.source_id),))
                        restricted.execute(
                            """INSERT INTO v4_case_commands(id,case_id,idempotency_key,fingerprint,
                                                           action,actor_id,result)
                               VALUES(%s,%s,'forged',%s,'review_approve',%s,
                                      '{"schema_version":"salience.p1-production-recovery-case-command.v1"}')""",
                            (uuid4(), work.source_id, "a" * 64, work.owner_id),
                        )
            assert controller.execute_case_command(command).state == "retry_due"
        finally:
            with psycopg.connect(database) as connection:
                connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
                connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def test_case_resume_rejects_claimed_original_operation():
    with _case_fixture(claimed=True) as (database, work, approval_id, authority):
        controller = recovery.ProductionRecoveryCaseController(
            recovery.ProductionRecoveryWorkRepository(database), authority=authority, commands=None,
        )
        controller.execute_case_command(
            _command(work, "review_approve", 1, approval_id=approval_id, reason="reviewed")
        )
        with pytest.raises(PermissionError, match="claimed operation"):
            controller.execute_case_command(_command(work, "resume", 2, approval_id=approval_id))
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT state,revision FROM v4_recovery_cases WHERE id=%s",
                (work.source_id,),
            ).fetchone() == ("retry_due", 2)
            assert not connection.execute(
                "SELECT 1 FROM v4_case_commands WHERE case_id=%s AND action='resume'",
                (work.source_id,),
            ).fetchone()


def test_concurrent_review_replays_once_and_stale_scope_never_writes():
    with _case_fixture() as (database, work, approval_id, authority):
        controller = recovery.ProductionRecoveryCaseController(
            recovery.ProductionRecoveryWorkRepository(database), authority=authority, commands=None,
        )
        command = _command(work, "review_approve", 1, approval_id=approval_id, reason="reviewed")
        authority.bindings["fencing_epoch"] = 8
        with pytest.raises(PermissionError, match="route"):
            controller.execute_case_command(command)
        authority.bindings["fencing_epoch"] = 7
        with pytest.raises(PermissionError, match="operation-bound"):
            controller.execute_case_command(replace(command, approval_request_id=uuid4()))
        with pytest.raises(recovery.RecoveryIdentityConflict, match="stale"):
            controller.execute_case_command(replace(command, expected_revision=9))
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT state,revision FROM v4_recovery_cases WHERE id=%s", (work.source_id,),
            ).fetchone() == ("awaiting_review", 1)
        with ThreadPoolExecutor(max_workers=2) as pool:
            receipts = list(pool.map(controller.execute_case_command, [command, command]))
        assert receipts[0] == receipts[1]
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT count(*) FROM v4_case_commands WHERE case_id=%s",
                (work.source_id,),
            ).fetchone() == (1,)


def test_reviews_cannot_use_fixture_evidence_and_reject_requires_terminal_disposition():
    with _case_fixture() as (database, work, approval_id, authority):
        controller = recovery.ProductionRecoveryCaseController(
            recovery.ProductionRecoveryWorkRepository(database), authority=authority, commands=None,
        )
        with psycopg.connect(database) as connection:
            with pytest.raises(psycopg.errors.InsufficientPrivilege, match="fixture review"):
                with connection.transaction():
                    connection.execute(
                        """INSERT INTO v4_case_reviews(id,case_id,case_revision,context_id,
                                                       operation_id,artifact_sha256,account_ref,purpose,expires_at)
                           VALUES(%s,%s,1,%s,%s,%s,'fixture-account','fixture_execution',%s)""",
                        (uuid4(), work.source_id, work.context_id, work.operation_id, "a" * 64, work.deadline),
                    )
        rejected = _command(work, "review_reject", 1, reason="not approved")
        with pytest.raises(PermissionError, match="terminal business disposition"):
            controller.execute_case_command(rejected)
        with psycopg.connect(database) as connection:
            connection.execute(
                """UPDATE v4_cycles SET state='closed',disposition='cancelled',closed_at=clock_timestamp()
                   WHERE id=%s""",
                (work.cycle_id,),
            )
        receipt = controller.execute_case_command(rejected)
        assert (receipt.state, receipt.resulting_revision, receipt.disposition) == ("terminal", 2, "cancelled")
        with pytest.raises(PermissionError, match="bounded forward recovery"):
            controller.execute_case_command(_command(work, "resume", 2, approval_id=approval_id))


def test_revoked_production_command_grant_prevents_replay_and_mutation():
    with _case_fixture() as (database, work, approval_id, authority):
        controller = recovery.ProductionRecoveryCaseController(
            recovery.ProductionRecoveryWorkRepository(database), authority=authority, commands=None,
        )
        command = _command(work, "review_approve", 1, approval_id=approval_id, reason="reviewed")
        controller.execute_case_command(command)
        with psycopg.connect(database) as connection:
            connection.execute(
                """UPDATE permission_grants SET effect='deny' WHERE workspace_id=%s
                   AND principal_id=%s AND scope LIKE 'production:recovery:case:%%'""",
                (work.workspace_id, str(work.owner_id)),
            )
        with pytest.raises(PermissionError, match="scope"):
            controller.execute_case_command(command)
        with pytest.raises(PermissionError, match="scope"):
            controller.execute_case_command(_command(work, "resume", 2, approval_id=approval_id))


def test_fixture_case_privileges_cannot_forge_a_production_deadline_transition():
    with _case_fixture() as (database, work, _, _):
        role = "p1_case_cmd_" + uuid4().hex
        connection_url = make_conninfo(database, options=f"-c role={role}")
        with psycopg.connect(database) as connection:
            connection.execute(sql.SQL("CREATE ROLE {}").format(sql.Identifier(role)))
            connection.execute(
                sql.SQL("GRANT SELECT, UPDATE ON v4_recovery_cases TO {}").format(sql.Identifier(role))
            )
            connection.execute(
                sql.SQL("GRANT INSERT ON v4_case_commands, v4_case_events TO {}").format(sql.Identifier(role))
            )
        try:
            with psycopg.connect(connection_url) as restricted:
                key = (
                    f"deadline-wake:cycle_case:{work.source_id}:{work.source_revision}:"
                    f"{work.deadline.isoformat()}:deadline_suspend"
                )
                with pytest.raises(psycopg.errors.InsufficientPrivilege, match="production deadline"):
                    with restricted.transaction():
                        restricted.execute(
                            """INSERT INTO v4_case_commands(id,case_id,idempotency_key,fingerprint,action,actor_id,result)
                               VALUES(%s,%s,%s,%s,'deadline_suspend',%s,'{"resulting_revision":2}')""",
                            (uuid4(), work.source_id, key, "a" * 64, work.owner_id),
                        )
                with pytest.raises(psycopg.errors.InsufficientPrivilege, match="production deadline"):
                    with restricted.transaction():
                        restricted.execute(
                            """UPDATE v4_recovery_cases SET state='suspended',revision=2 WHERE id=%s""",
                            (work.source_id,),
                        )
                        restricted.execute(
                            """INSERT INTO v4_case_commands(id,case_id,idempotency_key,fingerprint,action,actor_id,result)
                               VALUES(%s,%s,'early-deadline',%s,'deadline_suspend',%s,'{"resulting_revision":2}')""",
                            (uuid4(), work.source_id, "a" * 64, work.owner_id),
                        )
                        restricted.execute(
                            """INSERT INTO v4_case_events(id,case_id,case_revision,action,actor_id,traceparent,payload)
                               VALUES(%s,%s,2,'escalated',%s,'fixture',%s)""",
                            (uuid4(), work.source_id, work.owner_id,
                             Jsonb({"state": "suspended", "case_id": str(work.source_id)})),
                        )
        finally:
            with psycopg.connect(database) as connection:
                connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
                connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def test_due_deadline_uses_the_guarded_production_command_and_replays_exactly():
    with _case_fixture(due=True) as (database, work, _, authority):
        role = "p1_deadline_cmd_" + uuid4().hex
        controller = recovery.ProductionRecoveryCaseController(
            recovery.ProductionRecoveryWorkRepository(make_conninfo(database, options=f"-c role={role}")),
            authority=authority, commands=None,
        )
        with psycopg.connect(database) as connection:
            connection.execute(sql.SQL("CREATE ROLE {}").format(sql.Identifier(role)))
            connection.execute(sql.SQL(
                """GRANT EXECUTE ON FUNCTION public.v4_read_production_case_deadline_v1(uuid),
                   public.v4_suspend_production_recovery_case_v1(uuid,text,jsonb) TO {}"""
            ).format(sql.Identifier(role)))
        try:
            wake = controller.wake_deadline(work.work_id)
            assert wake.command_result.case_id == work.source_id
            assert wake.command_result.resulting_revision == 2
            assert controller.wake_deadline(work.work_id) == wake
        finally:
            with psycopg.connect(database) as connection:
                connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
                connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT state,revision,action_count FROM v4_recovery_cases WHERE id=%s",
                (work.source_id,),
            ).fetchone() == ("suspended", 2, 0)
            assert connection.execute(
                """SELECT event.actor_id, event.payload->>'deadline', event.payload->>'command_id',
                          (event.payload->>'goal_stop_revision')::integer
                   FROM v4_production_recovery_events event WHERE event.event_id=%s""",
                (wake.event_id,),
            ).fetchone() == (
                work.owner_id, work.deadline.isoformat(), str(wake.command_result.command_id), 1,
            )


def test_guarded_deadline_requires_fresh_stop_revisions_after_a_stop_is_cleared():
    with _case_fixture(due=True) as (database, work, _, authority):
        controller = recovery.ProductionRecoveryCaseController(
            recovery.ProductionRecoveryWorkRepository(database), authority=authority, commands=None,
        )
        with psycopg.connect(database) as connection:
            for scope in ("workspace", f"goal:{work.goal_id}"):
                _task9_set_stop_scope(connection, work.workspace_id, scope, 2, True, work.owner_id, "test-stop")
                _task9_set_stop_scope(connection, work.workspace_id, scope, 3, False, work.owner_id, "test-clear")
        with pytest.raises(PermissionError, match="owner, policy, route and stops"):
            controller.wake_deadline(work.work_id)
        authority.bindings.update(workspace_stop_revision=3, goal_stop_revision=3)
        wake = controller.wake_deadline(work.work_id)
        assert wake.command_result.resulting_revision == work.case_revision + 1
        with psycopg.connect(database) as connection:
            assert connection.execute(
                """SELECT payload->>'workspace_stop_revision',payload->>'goal_stop_revision'
                   FROM v4_production_recovery_events WHERE event_id=%s""",
                (wake.event_id,),
            ).fetchone() == ("3", "3")
            assert connection.execute(
                "SELECT count(*) FROM v4_case_notifications WHERE case_id=%s",
                (work.source_id,),
            ).fetchone() == (0,)


def test_production_review_fails_if_authority_expires_while_approval_is_locked():
    with _case_fixture() as (database, work, approval_id, authority):
        entered = Event()
        expires_at = None
        authorize = authority.authorize_case_command

        def authorize_with_expiry(command):
            nonlocal expires_at
            approved = authorize(command)
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=3)
            entered.set()
            return replace(approved, expires_at=expires_at)

        authority.authorize_case_command = authorize_with_expiry
        application = "p1_case_review_expiry_" + uuid4().hex
        controller = recovery.ProductionRecoveryCaseController(
            recovery.ProductionRecoveryWorkRepository(
                make_conninfo(database, application_name=application)
            ),
            authority=authority,
            commands=None,
        )
        command = _command(work, "review_approve", 1, approval_id=approval_id, reason="reviewed")
        with ThreadPoolExecutor(max_workers=1) as pool:
            with psycopg.connect(database) as holder:
                holder.execute("SELECT id FROM approval_requests WHERE id=%s FOR UPDATE", (approval_id,))
                review = pool.submit(controller.execute_case_command, command)
                assert entered.wait(timeout=10)
                deadline = monotonic() + 2
                while monotonic() < deadline:
                    with psycopg.connect(database) as observer:
                        waiting = observer.execute(
                            """SELECT cardinality(pg_blocking_pids(pid)) AS blocked
                               FROM pg_stat_activity WHERE application_name=%s""",
                            (application,),
                        ).fetchone()
                    if waiting and waiting[0]:
                        break
                    sleep(0.03)
                else:
                    pytest.fail("review never reached the locked approval")
                holder.execute("SELECT pg_sleep_until(%s)", (expires_at,))
            with pytest.raises(PermissionError, match="expired before the write"):
                review.result(timeout=10)
        with psycopg.connect(database) as connection:
            assert connection.execute(
                "SELECT state,revision FROM v4_recovery_cases WHERE id=%s", (work.source_id,),
            ).fetchone() == ("awaiting_review", 1)
            assert connection.execute(
                "SELECT count(*) FROM v4_case_commands WHERE case_id=%s", (work.source_id,),
            ).fetchone() == (0,)
