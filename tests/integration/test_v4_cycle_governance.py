from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import time
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg
import pytest

from salience.cycles.governance import CycleGovernance, PermitRequest, ReviewResponse
from test_v4_cadence_policy import policy, request
from test_v4_cycle_accounting import accounting, admitted as accounting_admitted, committed
from test_v4_cycle_admission import approved_goal, cycles, rows


@pytest.fixture
def governed(policy):
    service, goal, spec, database = policy
    with psycopg.connect(database) as connection:
        for scope in ("cycles:stop", "cycles:permit", "cycles:case_operator"):
            connection.execute("INSERT INTO permission_grants (workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at) VALUES (%s,'identity',%s,%s,'allow','{}',now()+interval '1 hour')", (service.workspace_id, str(service.subject_id), scope))
    return CycleGovernance(database, workspace_id=service.workspace_id, subject_id=service.subject_id), goal, spec, database


def admitted(governed):
    service, goal, spec, _ = governed
    intent_id = service.request_cycle(goal, request(spec))
    return intent_id, service.admit(intent_id)["cycle_id"]


def permit_request(database, cycle_id):
    with psycopg.connect(database) as connection:
        context_id, operation_id = connection.execute("SELECT context_id,operation_id FROM v4_cycles WHERE id=%s", (cycle_id,)).fetchone()
    return PermitRequest(context_id=context_id, operation_id=operation_id, expected_goal_revision=1,
                         account_ref="fixture-account", purpose="fixture_execution", effect="fixture.noop",
                         artifact_sha256="a" * 64, ttl_seconds=30)


def review_response(database, opened, *, action="approve_resume", reason="approved fixture"):
    with psycopg.connect(database) as connection:
        context_id, operation_id, artifact, account, purpose = connection.execute(
            "SELECT context_id,operation_id,artifact_sha256,account_ref,purpose FROM v4_case_reviews WHERE id=%s",
            (opened["review_id"],),
        ).fetchone()
    return ReviewResponse(review_id=opened["review_id"], expected_revision=1,
                          context_id=context_id, operation_id=operation_id,
                          artifact_sha256=artifact, account_ref=account, purpose=purpose,
                          action=action, reason=reason)


def test_stop_and_permit_ordering_exact_retries_and_expiry(governed):
    service, goal, _, database = governed
    _, cycle = admitted(governed)
    request_body = permit_request(database, cycle)
    stopped = service.set_stop(goal_id=goal, stopped=True, expected_revision=1, idempotency_key="stop", reason="fixture stop")
    assert service.set_stop(goal_id=goal, stopped=True, expected_revision=1, idempotency_key="stop", reason="fixture stop") == stopped
    with pytest.raises(PermissionError, match="stop"):
        service.issue_permit(cycle, request_body, idempotency_key="first")
    service.set_stop(goal_id=goal, stopped=False, expected_revision=2, idempotency_key="clear", reason="fixture clear")
    issued = service.issue_permit(cycle, request_body, idempotency_key="first")
    assert service.issue_permit(cycle, request_body, idempotency_key="first") == issued
    with pytest.raises(ValueError, match="fingerprint"):
        service.issue_permit(cycle, request_body.model_copy(update={"artifact_sha256":"b"*64}), idempotency_key="first")
    claimed = service.claim_permit(issued["permit_id"])
    assert claimed["dispatch_allowed"] is True
    assert service.claim_permit(issued["permit_id"])["dispatch_allowed"] is False
    service.set_stop(goal_id=goal, stopped=True, expected_revision=3, idempotency_key="second-stop", reason="fixture stop")
    assert service.issue_permit(cycle, request_body, idempotency_key="first") == issued
    assert service.claim_permit(issued["permit_id"])["dispatch_allowed"] is False
    with pytest.raises(PermissionError, match="stop"):
        service.issue_permit(cycle, request_body, idempotency_key="renew")
    assert len([row for row in rows(database,"v4_dispatch_permits") if str(row["claim_id"])==issued["claim_id"]])==1


def test_concurrent_stop_or_claim_has_one_serialized_outcome(governed):
    service, goal, _, database = governed
    _, cycle = admitted(governed)
    body = permit_request(database, cycle)
    issued = service.issue_permit(cycle, body, idempotency_key="issue")
    with ThreadPoolExecutor(max_workers=2) as pool:
        stop = pool.submit(service.set_stop, goal_id=goal, stopped=True, expected_revision=1, idempotency_key="stop", reason="race")
        claim = pool.submit(service.claim_permit, issued["permit_id"])
        stop.result()
        try:
            outcome = claim.result()
        except PermissionError:
            outcome = None
    with psycopg.connect(database) as connection:
        state = connection.execute("SELECT state FROM v4_permit_claims WHERE id=%s", (issued["claim_id"],)).fetchone()[0]
    assert (outcome is None and state == "issued") or (outcome and state == "claimed")


def test_duplicate_workers_claim_only_one_dispatch_authority(governed):
    service, _, _, database = governed
    _, cycle = admitted(governed)
    permit = service.issue_permit(cycle, permit_request(database, cycle), idempotency_key="issue")
    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(service.claim_permit, [permit["permit_id"]]*4))
    assert [entry["dispatch_allowed"] for entry in outcomes].count(True) == 1
    assert [entry["dispatch_allowed"] for entry in outcomes].count(False) == 3


def test_another_permit_holder_cannot_reuse_or_mutate_frozen_subject_claim(governed):
    service, _, _, database = governed
    _, cycle = admitted(governed)
    body = permit_request(database, cycle)
    issued = service.issue_permit(cycle, body, idempotency_key="issued")
    other_subject = uuid4()
    with psycopg.connect(database) as connection:
        connection.execute("INSERT INTO identity_subjects (id,workspace_id,issuer,subject,expires_at) VALUES (%s,%s,'https://fixture.invalid',%s,now()+interval '1 hour')", (other_subject, service.workspace_id, str(other_subject)))
        connection.execute("INSERT INTO permission_grants (workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at) VALUES (%s,'identity',%s,'cycles:permit','allow','{}',now()+interval '1 hour')", (service.workspace_id, str(other_subject)))
    other = CycleGovernance(database, workspace_id=service.workspace_id, subject_id=other_subject)
    with pytest.raises(PermissionError, match="permit subject"):
        other.issue_permit(cycle, body, idempotency_key="issued")
    with pytest.raises(PermissionError, match="permit subject"):
        other.claim_permit(issued["permit_id"])
    assert service.claim_permit(issued["permit_id"])["dispatch_allowed"] is True
    with pytest.raises(PermissionError, match="permit subject"):
        other.claim_permit(issued["permit_id"])
    with pytest.raises(PermissionError, match="permit subject"):
        other.mark_unknown(issued["permit_id"])
    assert service.mark_unknown(issued["permit_id"])["state"] == "unknown"


def test_bound_intent_review_resumes_same_intent_and_rejects_stale_response(governed):
    service, _, spec, database = governed
    goal = approved_goal(service, spec.model_copy(update={"review_required":True}))
    intent_id = service.request_cycle(goal, request(spec))
    assert service.admit(intent_id)["disposition"] == "review_required"
    opened = service.open_case(intent_id, target="intent", kind="admission_review", failure_class="admission_review",
                               owner_id=service.subject_id, deadline=datetime.now(timezone.utc)+timedelta(minutes=5),
                               reason="fixture needs review", artifact_sha256="a"*64, account_ref="fixture-account")
    assert service.open_case(intent_id, target="intent", kind="admission_review", failure_class="admission_review",
                             owner_id=service.subject_id, deadline=opened["deadline"], reason="fixture needs review",
                             artifact_sha256="a"*64, account_ref="fixture-account")["case_id"]==opened["case_id"]
    response = review_response(database, opened)
    with pytest.raises(PermissionError, match="binding"):
        service.respond_review(opened["case_id"], response.model_copy(update={"artifact_sha256":"b"*64}), idempotency_key="wrong-artifact")
    decision = service.respond_review(opened["case_id"], response, idempotency_key="approve")
    assert service.respond_review(opened["case_id"], response, idempotency_key="approve") == decision
    with pytest.raises(ValueError, match="stale"):
        service.respond_review(opened["case_id"], response.model_copy(update={"action":"reject","reason":"late"}), idempotency_key="late")
    resumed = service.resume_case(opened["case_id"], expected_revision=2, idempotency_key="resume")
    assert resumed["intent_id"] == str(intent_id) and resumed["cycle_id"] is None
    assert service.resume_case(opened["case_id"], expected_revision=2, idempotency_key="resume") == resumed
    assert service.admit(intent_id)["disposition"] == "admitted"
    assert len([row for row in rows(database,"v4_cycles") if row["intent_id"]==intent_id])==1


def test_cycle_resume_uses_original_context_operation_and_atomic_outbox(governed, monkeypatch):
    service, _, _, database = governed
    _, cycle = admitted(governed)
    original = permit_request(database, cycle)
    opened = service.open_case(cycle, target="cycle", kind="retry", failure_class="technical_failure",
                               owner_id=service.subject_id, deadline=datetime.now(timezone.utc)+timedelta(minutes=5),
                               reason="fixture retry", artifact_sha256="a"*64, account_ref="fixture-account")
    before = len([row for row in rows(database,"v4_cycle_outbox") if row["cycle_id"]==cycle])
    def fail(*args, **kwargs):
        raise RuntimeError("crash before commit")
    monkeypatch.setattr(service, "_event", fail)
    with pytest.raises(RuntimeError):
        service.resume_case(opened["case_id"], expected_revision=1, idempotency_key="resume")
    monkeypatch.undo()
    assert len([row for row in rows(database,"v4_cycle_outbox") if row["cycle_id"]==cycle]) == before
    resumed = service.resume_case(opened["case_id"], expected_revision=1, idempotency_key="resume")
    assert resumed["cycle_id"] == str(cycle) and resumed["operation_id"] == str(original.operation_id)
    assert service.resume_case(opened["case_id"], expected_revision=1, idempotency_key="resume") == resumed
    assert len([row for row in rows(database,"v4_cycle_outbox") if row["cycle_id"]==cycle]) == before+1
    assert service.open_case(cycle, target="cycle", kind="retry", failure_class="technical_failure",
                             owner_id=service.subject_id, deadline=opened["deadline"], reason="fixture retry",
                             artifact_sha256="a"*64, account_ref="fixture-account")["case_id"] == opened["case_id"]
    reopened = service.open_case(cycle, target="cycle", kind="retry", failure_class="technical_failure",
                                 owner_id=service.subject_id, deadline=datetime.now(timezone.utc)+timedelta(minutes=5),
                                 reason="second fixture retry", artifact_sha256="a"*64, account_ref="fixture-account")
    assert reopened["case_id"] != opened["case_id"]
    with pytest.raises(ValueError, match="active case"):
        service.open_case(cycle, target="cycle", kind="retry", failure_class="technical_failure",
                          owner_id=service.subject_id, deadline=datetime.now(timezone.utc)+timedelta(minutes=5),
                          reason="conflicting fixture retry", artifact_sha256="a"*64, account_ref="fixture-account")


def test_case_holds_permit_until_resume_and_review_bindings(governed):
    service, _, _, database = governed
    _, cycle = admitted(governed)
    body = permit_request(database, cycle)
    opened = service.open_case(cycle, target="cycle", kind="manual_review", failure_class="manual_review",
                               owner_id=service.subject_id, deadline=datetime.now(timezone.utc)+timedelta(minutes=5),
                               reason="fixture hold", artifact_sha256="a"*64, account_ref="fixture-account")
    with pytest.raises(ValueError, match="runnable"):
        service.issue_permit(cycle, body, idempotency_key="blocked")
    response = review_response(database, opened)
    for field, value in (("context_id", uuid4()), ("operation_id", uuid4()), ("artifact_sha256", "b"*64)):
        with pytest.raises(PermissionError, match="binding"):
            service.respond_review(opened["case_id"], response.model_copy(update={field:value}), idempotency_key=field)
    decision = service.respond_review(opened["case_id"], response, idempotency_key="approve")
    assert decision["state"] == "retry_due"
    with pytest.raises(ValueError, match="runnable"):
        service.issue_permit(cycle, body, idempotency_key="still-blocked")
    service.resume_case(opened["case_id"], expected_revision=2, idempotency_key="resume")
    assert service.issue_permit(cycle, body, idempotency_key="allowed")["version"] == 1


def test_policy_denial_review_never_authorizes_retry(governed):
    service, _, _, database = governed
    _, cycle = admitted(governed)
    opened = service.open_case(cycle, target="cycle", kind="manual_review", failure_class="policy_denial",
                               owner_id=service.subject_id, deadline=datetime.now(timezone.utc)+timedelta(minutes=5),
                               reason="policy denies fixture", artifact_sha256="a"*64, account_ref="fixture-account")
    with pytest.raises(PermissionError, match="policy denial"):
        service.respond_review(opened["case_id"], review_response(database, opened), idempotency_key="unsafe")
    with psycopg.connect(database) as connection, pytest.raises(psycopg.errors.RaiseException, match="policy denial"):
        connection.execute("UPDATE v4_recovery_cases SET state='retry_due',revision=revision+1 WHERE id=%s", (opened["case_id"],))
    rejected = service.respond_review(opened["case_id"], review_response(database, opened, action="reject", reason="denied"), idempotency_key="reject")
    assert rejected["state"] == "terminal"


def test_expired_unused_permit_renews_but_unknown_claim_does_not(governed, monkeypatch):
    service, _, _, database = governed
    _, cycle = admitted(governed)
    body = permit_request(database, cycle).model_copy(update={"ttl_seconds":1})
    original_event = service._event
    def crash(*args, **kwargs):
        raise RuntimeError("crash before permit commit")
    monkeypatch.setattr(service, "_event", crash)
    with pytest.raises(RuntimeError, match="crash"):
        service.issue_permit(cycle, body, idempotency_key="rolled-back")
    monkeypatch.setattr(service, "_event", original_event)
    assert not [row for row in rows(database,"v4_permit_claims") if row["cycle_id"]==cycle]
    first = service.issue_permit(cycle, body, idempotency_key="first")
    time.sleep(1.05)
    with pytest.raises(PermissionError, match="expired"):
        service.claim_permit(first["permit_id"])
    renewed = service.issue_permit(cycle, body, idempotency_key="renewed")
    assert renewed["claim_id"] == first["claim_id"] and renewed["version"] == 2
    assert service.claim_permit(renewed["permit_id"])["dispatch_allowed"] is True
    assert service.mark_unknown(renewed["permit_id"]) == service.mark_unknown(renewed["permit_id"])
    assert service.issue_permit(cycle, body, idempotency_key="renewed") == renewed
    assert service.claim_permit(renewed["permit_id"])["state"] == "unknown"
    with pytest.raises(ValueError, match="uncertain"):
        service.issue_permit(cycle, body, idempotency_key="unsafe-renewal")


def test_workspace_stop_and_current_revocation_block_claim(governed):
    service, _, _, database = governed
    _, cycle = admitted(governed)
    body = permit_request(database, cycle)
    issued = service.issue_permit(cycle, body, idempotency_key="issue")
    service.set_stop(stopped=True, expected_revision=1, idempotency_key="workspace-stop", reason="fixture emergency")
    with pytest.raises(PermissionError, match="stop"):
        service.claim_permit(issued["permit_id"])
    service.set_stop(stopped=False, expected_revision=2, idempotency_key="workspace-clear", reason="fixture clear")
    with pytest.raises(PermissionError, match="changed"):
        service.claim_permit(issued["permit_id"])
    with psycopg.connect(database) as connection:
        connection.execute("UPDATE identity_subjects SET enabled=false WHERE id=%s", (service.subject_id,))
    with pytest.raises(PermissionError, match="subject"):
        service.issue_permit(cycle, body, idempotency_key="after-revocation")


def test_unavailable_authority_database_never_issues_permit(governed):
    service, _, _, database = governed
    _, cycle = admitted(governed)
    parts = urlsplit(database)
    unavailable = urlunsplit(parts._replace(netloc=f"{parts.hostname}:1"))
    isolated = CycleGovernance(unavailable, workspace_id=service.workspace_id, subject_id=service.subject_id)
    with pytest.raises(psycopg.OperationalError):
        isolated.issue_permit(cycle, permit_request(database, cycle), idempotency_key="unavailable")


def test_direct_sql_cannot_rewrite_governance_history(governed):
    service, _, _, database = governed
    _, cycle = admitted(governed)
    body = permit_request(database, cycle)
    with psycopg.connect(database) as connection, pytest.raises(psycopg.errors.RaiseException, match="scope"):
        connection.execute("INSERT INTO v4_permit_claims (id,cycle_id,operation_id,context_id,request_fingerprint) VALUES (%s,%s,%s,%s,%s)",
                           (uuid4(),cycle,uuid4(),body.context_id,"a"*64))
    issued = service.issue_permit(cycle, permit_request(database, cycle), idempotency_key="issue")
    service.claim_permit(issued["permit_id"])
    opened = service.open_case(cycle, target="cycle", kind="retry", failure_class="technical_failure",
                               owner_id=service.subject_id, deadline=datetime.now(timezone.utc)+timedelta(minutes=5),
                               reason="fixture retry", artifact_sha256="a"*64, account_ref="fixture-account")
    with psycopg.connect(database) as connection, pytest.raises(psycopg.errors.RaiseException, match="immutable"):
        connection.execute("UPDATE v4_dispatch_permits SET expires_at=now()+interval '1 hour' WHERE id=%s", (issued["permit_id"],))
    with psycopg.connect(database) as connection, pytest.raises(psycopg.errors.RaiseException, match="transition"):
        connection.execute("UPDATE v4_permit_claims SET state='issued',revision=revision+1 WHERE id=%s", (issued["claim_id"],))
    with pytest.raises(psycopg.errors.RaiseException, match="immutable event"):
        with psycopg.connect(database) as connection:
            connection.execute("UPDATE v4_permit_claims SET state='unknown',revision=revision+1 WHERE id=%s", (issued["claim_id"],))
    with psycopg.connect(database) as connection, pytest.raises(psycopg.errors.RaiseException, match="binding"):
        connection.execute("UPDATE v4_recovery_cases SET operation_id=%s,revision=revision+1,state='resolved' WHERE id=%s", (uuid4(),opened["case_id"]))
    with pytest.raises(psycopg.errors.RaiseException, match="immutable event"):
        with psycopg.connect(database) as connection:
            connection.execute("UPDATE v4_recovery_cases SET state='resolved',revision=revision+1 WHERE id=%s", (opened["case_id"],))
    with psycopg.connect(database) as connection, pytest.raises(psycopg.errors.RaiseException, match="identity"):
        connection.execute("DELETE FROM v4_stop_scopes WHERE workspace_id=%s", (service.workspace_id,))


def test_nonowner_governance_role_needs_no_authority_edit_privilege(governed):
    service, _, _, database = governed
    _, cycle = admitted(governed)
    role, password = "v4_govern_"+uuid4().hex, uuid4().hex
    identifier = psycopg.sql.Identifier(role)
    with psycopg.connect(database, autocommit=True) as admin:
        admin.execute(psycopg.sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS").format(identifier, psycopg.sql.Literal(password)))
        try:
            admin.execute(psycopg.sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(identifier))
            admin.execute(psycopg.sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(identifier))
            admin.execute(psycopg.sql.SQL("GRANT INSERT ON v4_runtime_waits,v4_notification_deliveries,v4_stop_scopes,v4_stop_commands,v4_permit_claims,v4_dispatch_permits,v4_recovery_cases,v4_case_commands,v4_case_reviews,v4_review_decisions,v4_case_notifications,v4_case_acks,v4_case_archives,v4_case_events,v4_cycle_events,v4_cycle_outbox TO {}").format(identifier))
            admin.execute(psycopg.sql.SQL("GRANT UPDATE ON v4_goals,v4_cycle_intents,v4_cycles,v4_stop_scopes,v4_permit_claims,v4_recovery_cases TO {}").format(identifier))
            for function in ("p0_lock_identity(text,text,uuid)", "v4_lock_program(uuid,uuid)"):
                admin.execute(psycopg.sql.SQL("GRANT EXECUTE ON FUNCTION "+function+" TO {}").format(identifier))
            parts = urlsplit(database)
            restricted = urlunsplit(parts._replace(netloc=f"{role}:{password}@{parts.hostname}:{parts.port or 5432}"))
            with psycopg.connect(restricted) as connection:
                for table in ("identity_subjects", "permission_grants", "content_programs", "budgets"):
                    assert not connection.execute("SELECT has_table_privilege(current_user,%s,'UPDATE')", (table,)).fetchone()[0]
            local = CycleGovernance(restricted, workspace_id=service.workspace_id, subject_id=service.subject_id)
            issued = local.issue_permit(cycle, permit_request(database, cycle), idempotency_key="restricted")
            assert local.claim_permit(issued["permit_id"])["dispatch_allowed"] is True
            local.set_stop(stopped=True, expected_revision=1, idempotency_key="restricted-stop", reason="fixture")
            with pytest.raises(PermissionError, match="stop"):
                local.issue_permit(cycle, permit_request(database, cycle), idempotency_key="blocked")
        finally:
            admin.execute(psycopg.sql.SQL("DROP OWNED BY {}").format(identifier))
            admin.execute(psycopg.sql.SQL("DROP ROLE {}").format(identifier))


def test_v3_permit_claim_atomically_retains_all_child_liabilities(accounting, monkeypatch):
    base, _, spec, database = accounting
    with psycopg.connect(database) as connection:
        connection.execute("INSERT INTO permission_grants (workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at) VALUES (%s,'identity',%s,'cycles:permit','allow','{}',now()+interval '1 hour')", (base.workspace_id,str(base.subject_id)))
    service = CycleGovernance(database, workspace_id=base.workspace_id, subject_id=base.subject_id)
    cycle = accounting_admitted(accounting)
    body = permit_request(database, cycle)
    with pytest.raises(PermissionError, match="reservation"):
        service.issue_permit(cycle, body, idempotency_key="before-transfer")
    base.transfer_cost(cycle, operation_id=body.operation_id, idempotency_key="transfer", estimated_micros=30, reserved_micros=50, category="generation")
    issued = service.issue_permit(cycle, body, idempotency_key="permit")
    original_event = service._event
    def crash(*args, **kwargs):
        raise RuntimeError("crash before claim commit")
    monkeypatch.setattr(service, "_event", crash)
    with pytest.raises(RuntimeError, match="crash"):
        service.claim_permit(issued["permit_id"])
    monkeypatch.setattr(service, "_event", original_event)
    with psycopg.connect(database) as connection:
        assert {row[0] for row in connection.execute("SELECT reservation.status FROM v4_allocation_reservations AS mapping JOIN budget_reservations AS reservation ON reservation.id=mapping.reservation_id WHERE mapping.operation_id=%s",(body.operation_id,))} == {"reserved"}
    assert service.claim_permit(issued["permit_id"])["dispatch_allowed"] is True
    with psycopg.connect(database) as connection:
        assert {row[0] for row in connection.execute("SELECT reservation.status FROM v4_allocation_reservations AS mapping JOIN budget_reservations AS reservation ON reservation.id=mapping.reservation_id WHERE mapping.operation_id=%s",(body.operation_id,))} == {"pending_actual"}
    base.close(cycle, disposition="cancelled", reason="fixture cancellation")
    assert all(committed(database, budget)==50 for budget in spec.allocation.budget_ids)
    assert service.issue_permit(cycle, body, idempotency_key="permit") == issued
    assert service.claim_permit(issued["permit_id"])["dispatch_allowed"] is False
    service.mark_unknown(issued["permit_id"])
    assert all(committed(database, budget)==50 for budget in spec.allocation.budget_ids)
    base.cost_command(cycle, operation_id=body.operation_id, action="settle", idempotency_key="invoice", proof_ref="fixture-receipt:invoice", actual_micros=40)
    assert all(committed(database, budget)==40 for budget in spec.allocation.budget_ids)


def test_v3_permit_rechecks_every_budget_before_issue(accounting):
    base, _, spec, database = accounting
    with psycopg.connect(database) as connection:
        connection.execute("INSERT INTO permission_grants (workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at) VALUES (%s,'identity',%s,'cycles:permit','allow','{}',now()+interval '1 hour')", (base.workspace_id,str(base.subject_id)))
    service = CycleGovernance(database, workspace_id=base.workspace_id, subject_id=base.subject_id)
    cycle = accounting_admitted(accounting)
    body = permit_request(database, cycle)
    base.transfer_cost(cycle, operation_id=body.operation_id, idempotency_key="transfer", estimated_micros=30, reserved_micros=50, category="generation")
    with psycopg.connect(database) as connection:
        connection.execute("UPDATE budgets SET status='suspended' WHERE id=%s", (spec.allocation.budget_ids[0],))
    with pytest.raises(PermissionError, match="budgets"):
        service.issue_permit(cycle, body, idempotency_key="inactive")
    with psycopg.connect(database) as connection:
        connection.execute("UPDATE budgets SET status='active' WHERE id=%s", (spec.allocation.budget_ids[0],))
    assert service.issue_permit(cycle, body, idempotency_key="eligible")["version"] == 1


def test_notification_ack_deadline_escalation_and_terminal_archive(governed):
    service, _, _, database = governed
    _, cycle = admitted(governed)
    opened = service.open_case(cycle, target="cycle", kind="manual_review", failure_class="manual_review",
                               owner_id=service.subject_id, deadline=datetime.now(timezone.utc)+timedelta(seconds=1),
                               reason="fixture review", artifact_sha256="a"*64, account_ref="fixture-account")
    assert service.ack_notification(opened["notification_id"]) == service.ack_notification(opened["notification_id"])
    with pytest.raises(ValueError, match="disposition"):
        service.archive_case(opened["case_id"], reason="too early", evidence={"fixture":"yes"}, retain_until=datetime.now(timezone.utc)+timedelta(days=1))
    time.sleep(1.05)
    escalation = service.escalate_due(opened["case_id"])
    assert escalation == service.escalate_due(opened["case_id"])
    assert service.ack_notification(escalation["notification_id"])["notification_id"] == escalation["notification_id"]
    with pytest.raises(ValueError, match="stale"):
        service.respond_review(opened["case_id"], review_response(database, opened), idempotency_key="expired")
    service.close(cycle, disposition="cancelled", reason="fixture stopped")
    service.terminalize_case(opened["case_id"], expected_revision=2, idempotency_key="terminal")
    archived = service.archive_case(opened["case_id"], reason="fixture archive", evidence={"fixture":"yes"}, retain_until=datetime.now(timezone.utc)+timedelta(days=1))
    assert archived["case_id"] == str(opened["case_id"])
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM v4_case_archives WHERE case_id=%s", (opened["case_id"],)).fetchone()[0]==1


def test_reconciliation_case_cannot_resume_without_proof(governed):
    service, _, _, database = governed
    _, cycle = admitted(governed)
    with pytest.raises(ValueError, match="cannot bypass"):
        service.open_case(cycle, target="cycle", kind="retry", failure_class="unknown_effect",
                          owner_id=service.subject_id, deadline=datetime.now(timezone.utc)+timedelta(minutes=5),
                          reason="unsafe retry", artifact_sha256="a"*64, account_ref="fixture-account")
    opened = service.open_case(cycle, target="cycle", kind="reconciliation", failure_class="unknown_effect",
                               owner_id=service.subject_id, deadline=datetime.now(timezone.utc)+timedelta(minutes=5),
                               reason="unknown fixture result", artifact_sha256="a"*64, account_ref="fixture-account")
    with pytest.raises(ValueError, match="unapproved"):
        service.resume_case(opened["case_id"], expected_revision=1, idempotency_key="unsafe")
    assert len([row for row in rows(database,"v4_cycles") if row["id"]==cycle and row["state"]=="reconciling"]) == 1
    service.close(cycle, disposition="cancelled", reason="unknown held")
    service.terminalize_case(opened["case_id"], expected_revision=1, idempotency_key="terminal")


def test_rework_case_cannot_reuse_old_operation_as_retry(governed):
    service, _, _, database = governed
    _, cycle = admitted(governed)
    opened = service.open_case(cycle, target="cycle", kind="rework", failure_class="quality_failure",
                               owner_id=service.subject_id, deadline=datetime.now(timezone.utc)+timedelta(minutes=5),
                               reason="material fixture rework", artifact_sha256="a"*64, account_ref="fixture-account")
    with pytest.raises(ValueError, match="unapproved"):
        service.resume_case(opened["case_id"], expected_revision=1, idempotency_key="not-a-retry")
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT state FROM v4_recovery_cases WHERE id=%s", (opened["case_id"],)).fetchone()[0] == "rework_due"
