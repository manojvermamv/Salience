from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
import pytest

from salience.cycles.contracts import AllocationPolicy, GoalSpecV3
from salience.governance.costs import BudgetExceeded, IdempotencyConflict
from salience.governance.cycle_costs import CycleCostLedger
from test_v4_cadence_policy import policy, request
from test_v4_cycle_admission import approved_goal, cycles, rows


@pytest.fixture
def accounting(policy):
    service, _, spec, database = policy
    start = datetime.now(timezone.utc)-timedelta(hours=1)
    end = start+timedelta(days=1)
    budgets = tuple(uuid4() for _ in range(2))
    with psycopg.connect(database) as connection:
        for budget, scope in zip(budgets, ("program", "account:fixture-account")):
            connection.execute("INSERT INTO budgets (id,workspace_id,content_program_id,name,scope,currency,limit_amount,period_start,period_end) VALUES (%s,%s,%s,%s,%s,'USD',0.000100,%s,%s)", (budget, service.workspace_id, spec.content_program_id, str(budget), scope, start, end))
        connection.execute("INSERT INTO permission_grants (workspace_id,principal_type,principal_id,scope,effect,constraints,expires_at) VALUES (%s,'identity',%s,'cycles:accounting','allow','{}',now()+interval '1 hour')", (service.workspace_id,str(service.subject_id)))
    spec = GoalSpecV3.model_validate(spec.model_dump() | {"schema_version":"GoalSpec.local.v3", "allocation":AllocationPolicy(budget_ids=budgets, currency="USD", period_start=start, period_end=end, ceiling_micros=80)})
    return service, approved_goal(service, spec), spec, database


def admitted(accounting):
    service, goal, spec, _ = accounting
    return service.admit(service.request_cycle(goal, request(spec)))["cycle_id"]


def committed(database, budget):
    from salience.governance.cost_repository import CostReservationRepository
    with psycopg.connect(database) as connection, connection.cursor() as cursor:
        return CostReservationRepository._committed_micros(cursor, str(budget))


def transfer(service, cycle, operation=None, key="transfer", estimate=30, upper=50):
    return service.transfer_cost(cycle, operation_id=operation or uuid4(), idempotency_key=key, estimated_micros=estimate, reserved_micros=upper, category="generation")


def command(service, cycle, operation, action, key, **fields):
    return service.cost_command(cycle, operation_id=operation, action=action, idempotency_key=key, proof_ref="fixture-receipt:"+key, **fields)


def test_g0_g1_conservation_and_exact_retries(accounting):
    service, _, spec, database = accounting
    cycle = admitted(accounting)
    assert all(committed(database, budget)==80 for budget in spec.allocation.budget_ids)
    operation = uuid4()
    first = transfer(service, cycle, operation)
    assert transfer(service, cycle, operation)==first
    assert all(committed(database, budget)==80 for budget in spec.allocation.budget_ids)
    with pytest.raises(IdempotencyConflict):
        transfer(service, cycle, operation, upper=51)
    command(service, cycle, operation, "dispatch", "sent")
    settled = command(service, cycle, operation, "settle", "invoice", actual_micros=40)
    assert command(service, cycle, operation, "settle", "invoice", actual_micros=40)==settled
    assert all(committed(database, budget)==70 for budget in spec.allocation.budget_ids)
    with pytest.raises(IdempotencyConflict):
        command(service, cycle, operation, "settle", "other-invoice", actual_micros=41)
    service.close(cycle, disposition="completed", reason="accounting fixture")
    assert all(committed(database, budget)==40 for budget in spec.allocation.budget_ids)
    context = next(row["payload"] for row in rows(database,"v4_run_contexts") if row["cycle_id"]==cycle)
    assert context["schema_version"]=="RunContext.local.v3" and context["allocation_id"]==first["allocation_id"]
    assert context["dry_run"] is True and context["max_spend"]==0


def test_concurrent_allocations_respect_shared_caps_and_no_orphan_cycle(accounting):
    service, goal, spec, database = accounting
    other = approved_goal(service, spec.model_copy(update={"allocation":spec.allocation.model_copy(update={"budget_ids":tuple(reversed(spec.allocation.budget_ids))})}))
    intents = [service.request_cycle(current,request(spec)) for current in (goal,other)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(service.admit,intents))
    assert sorted(result["disposition"] for result in results)==["admitted","deferred"]
    denied = next(result for result in results if result["disposition"]=="deferred")
    assert denied["reason"]=="budget_capacity" and denied["cycle_id"] is None
    assert all(committed(database,budget)==80 for budget in spec.allocation.budget_ids)
    assert len([row for row in rows(database,"v4_cycles") if row["intent_id"] in intents])==1


def test_concurrent_transfers_cannot_exceed_parent(accounting):
    service, _, spec, database = accounting
    cycle = admitted(accounting)
    def attempt(index):
        try:
            return transfer(service,cycle,key=f"transfer-{index}")
        except BudgetExceeded:
            return None
    with ThreadPoolExecutor(max_workers=3) as pool:
        outcomes=list(pool.map(attempt,range(3)))
    assert sum(result is not None for result in outcomes)==1
    assert all(committed(database,budget)==80 for budget in spec.allocation.budget_ids)


def test_cancel_unknown_liability_late_settlement_and_overage(accounting):
    service, _, spec, database = accounting
    cycle = admitted(accounting)
    operation = uuid4()
    transfer(service,cycle,operation)
    command(service,cycle,operation,"dispatch","sent")
    command(service,cycle,operation,"unknown","lost-response")
    service.close(cycle,disposition="cancelled",reason="unknown remote outcome")
    assert all(committed(database,budget)==50 for budget in spec.allocation.budget_ids)
    with pytest.raises(ValueError,match="liability"):
        command(service,cycle,operation,"release","unsafe-release")
    result=command(service,cycle,operation,"settle","late-invoice",actual_micros=120)
    assert result["state"]=="overage_pending_approval"
    assert all(committed(database,budget)==120 for budget in spec.allocation.budget_ids)
    new_goal=approved_goal(service,spec)
    assert service.admit(service.request_cycle(new_goal,request(spec)))["reason"]=="budget_capacity"


def test_caller_transaction_abort_rolls_back_all_g0_records(accounting,monkeypatch):
    service, goal, spec, database = accounting
    intent=service.request_cycle(goal,request(spec))
    original=service._event
    def fail(*args,**kwargs):
        raise RuntimeError("crash before admission commit")
    monkeypatch.setattr(service,"_event",fail)
    with pytest.raises(RuntimeError):
        service.admit(intent)
    assert all(committed(database,budget)==0 for budget in spec.allocation.budget_ids)
    assert not [row for row in rows(database,"v4_cycles") if row["intent_id"]==intent]
    monkeypatch.setattr(service,"_event",original)
    cycle=service.admit(intent)["cycle_id"]
    assert service.admit(intent)["cycle_id"]==cycle
    assert all(committed(database,budget)==80 for budget in spec.allocation.budget_ids)


def test_transfer_abort_and_restart_exact_identity(accounting):
    service, _, spec, database = accounting
    cycle=admitted(accounting)
    operation=uuid4()
    with pytest.raises(RuntimeError), psycopg.connect(database,row_factory=dict_row) as connection:
        ledger=CycleCostLedger(connection,workspace_id=service.workspace_id,subject_id=service.subject_id,traceparent=service.trace.to_carrier()["traceparent"])
        ledger.transfer(cycle,operation_id=operation,idempotency_key="transfer",estimated_micros=30,reserved_micros=50,category="generation")
        raise RuntimeError("crash before commit")
    result=transfer(service,cycle,operation)
    assert transfer(service,cycle,operation)==result
    assert all(committed(database,budget)==80 for budget in spec.allocation.budget_ids)


@pytest.mark.parametrize("field,value",[("currency","EUR"),("period_end",datetime(2030,1,1,tzinfo=timezone.utc)),("budget_ids",(uuid4(),))])
def test_scope_currency_period_fail_closed(accounting,field,value):
    service, _, spec, database=accounting
    changed=spec.model_copy(update={"allocation":spec.allocation.model_copy(update={field:value})})
    goal=approved_goal(service,changed)
    with pytest.raises((ValueError,PermissionError)):
        service.admit(service.request_cycle(goal,request(spec)))
    assert all(committed(database,budget)==0 for budget in spec.allocation.budget_ids)


def test_release_only_unused_and_legacy_mutation_denied(accounting):
    from salience.governance.cost_repository import CostReservationRepository
    service, _, spec, database=accounting
    cycle=admitted(accounting)
    operation=uuid4()
    result=transfer(service,cycle,operation)
    command(service,cycle,operation,"release","never-dispatched")
    assert all(committed(database,budget)==30 for budget in spec.allocation.budget_ids)
    with pytest.raises(ValueError,match="V4"):
        CostReservationRepository(database)._release_unused(result["reservation_ids"][0])
    with pytest.raises(ValueError,match="released"):
        command(service,cycle,operation,"dispatch","late-dispatch")
    command(service,cycle,None,"release_allocation","unused-parent")
    assert all(committed(database,budget)==0 for budget in spec.allocation.budget_ids)


def test_default_cursor_caller_owned_transaction_and_autocommit_rejected(accounting):
    service, _, _, database=accounting
    cycle=admitted(accounting)
    with psycopg.connect(database) as connection:
        ledger=CycleCostLedger(connection,workspace_id=service.workspace_id,subject_id=service.subject_id,traceparent=service.trace.to_carrier()["traceparent"])
        result=ledger.transfer(cycle,operation_id=uuid4(),idempotency_key="default-cursor",estimated_micros=1,reserved_micros=1,category="research")
        assert result["state"]=="reserved"
        connection.rollback()
    with psycopg.connect(database,autocommit=True) as connection, pytest.raises(ValueError,match="caller-owned"):
        CycleCostLedger(connection,workspace_id=service.workspace_id,subject_id=service.subject_id,traceparent=service.trace.to_carrier()["traceparent"])


def test_expired_allocation_retains_unknown_liability_and_rollover_is_new_identity(accounting):
    import time
    service, _, spec, database=accounting
    start=datetime.now(timezone.utc)-timedelta(seconds=1)
    end=start+timedelta(seconds=3)
    with psycopg.connect(database) as connection:
        for budget in spec.allocation.budget_ids:
            connection.execute("UPDATE budgets SET period_start=%s,period_end=%s WHERE id=%s",(start,end,budget))
    policy=spec.allocation.model_copy(update={"period_start":start,"period_end":end})
    revised=spec.model_copy(update={"allocation":policy})
    goal=approved_goal(service,revised)
    cycle=service.admit(service.request_cycle(goal,request(spec)))["cycle_id"]
    operation=uuid4()
    transfer(service,cycle,operation)
    command(service,cycle,operation,"unknown","lost")
    time.sleep(2.05)
    with pytest.raises(ValueError,match="period"):
        transfer(service,cycle,key="expired",upper=1,estimate=1)
    assert all(committed(database,budget)==80 for budget in spec.allocation.budget_ids)
    command(service,cycle,None,"release_allocation","expired-parent")
    assert all(committed(database,budget)==50 for budget in spec.allocation.budget_ids)
    fresh_ids=tuple(uuid4() for _ in range(2))
    new_end=end+timedelta(days=1)
    with psycopg.connect(database) as connection:
        for old,new in zip(spec.allocation.budget_ids,fresh_ids):
            connection.execute("INSERT INTO budgets (id,workspace_id,content_program_id,name,scope,currency,limit_amount,period_start,period_end) SELECT %s,workspace_id,content_program_id,%s,scope,currency,limit_amount,%s,%s FROM budgets WHERE id=%s",(new,str(new),end,new_end,old))
    next_spec=spec.model_copy(update={"allocation":policy.model_copy(update={"budget_ids":fresh_ids,"period_start":end,"period_end":new_end})})
    next_goal=approved_goal(service,next_spec)
    assert service.admit(service.request_cycle(next_goal,request(spec)))["disposition"]=="admitted"
    command(service,cycle,operation,"settle","late-old-period",actual_micros=45)
    assert all(committed(database,budget)==45 for budget in spec.allocation.budget_ids)
    assert all(committed(database,budget)==80 for budget in fresh_ids)


def test_allocation_currency_period_and_ledger_history_are_immutable(accounting):
    service, _, spec, database=accounting
    cycle=admitted(accounting)
    transfer(service,cycle)
    with psycopg.connect(database) as connection:
        for statement, parameters in [
            ("UPDATE budgets SET currency='EUR' WHERE id=%s",(spec.allocation.budget_ids[0],)),
            ("UPDATE budgets SET period_end=period_end+interval '1 day' WHERE id=%s",(spec.allocation.budget_ids[0],)),
            ("UPDATE cost_ledger_entries SET actual_amount=0 WHERE budget_reservation_id IN (SELECT reservation_id FROM v4_allocation_reservations WHERE allocation_id=(SELECT id FROM v4_cycle_allocations WHERE cycle_id=%s))",(cycle,)),
        ]:
            with pytest.raises(psycopg.Error,match="immutable"), connection.transaction():
                connection.execute(statement,parameters)


def test_cycle_close_never_requires_new_accounting_authority(accounting):
    service, _, spec, database=accounting
    cycle=admitted(accounting)
    operation=uuid4()
    transfer(service,cycle,operation)
    command(service,cycle,operation,"unknown","unknown")
    with psycopg.connect(database) as connection:
        connection.execute("DELETE FROM permission_grants WHERE workspace_id=%s AND scope='cycles:accounting'",(service.workspace_id,))
    service.close(cycle,disposition="cancelled",reason="stop still available")
    assert all(committed(database,budget)==50 for budget in spec.allocation.budget_ids)


def test_budget_scope_and_funds_recovery_use_existing_intent(accounting):
    service, goal, spec, database=accounting
    with psycopg.connect(database) as connection:
        connection.execute("UPDATE budgets SET limit_amount=0 WHERE id=%s",(spec.allocation.budget_ids[0],))
    intent=service.request_cycle(goal,request(spec))
    assert service.admit(intent)["reason"]=="budget_capacity"
    with psycopg.connect(database) as connection:
        connection.execute("UPDATE budgets SET limit_amount=0.000100 WHERE id=%s",(spec.allocation.budget_ids[0],))
    service.wake(intent,expected_revision=1)
    assert service.admit(intent)["disposition"]=="admitted"
    assert len([row for row in rows(database,"v4_cycles") if row["intent_id"]==intent])==1


def test_concurrent_duplicate_transfer_and_settlement(accounting):
    service, _, spec, database=accounting
    cycle,operation=admitted(accounting),uuid4()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _: transfer(service,cycle,operation),range(4)))
    assert all(result==results[0] for result in results)
    reservations=results[0]["reservation_ids"]
    with pytest.raises(IdempotencyConflict):
        transfer(service,cycle,operation,key="other-key")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda index: command(service,cycle,operation,"settle",f"invoice-{index}",actual_micros=40),range(4)))
    assert all(result==results[0] for result in results)
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM cost_ledger_entries WHERE budget_reservation_id=ANY(%s::uuid[]) AND usage->>'kind'='settlement'",(reservations,)).fetchone()[0]==2
    assert all(committed(database,budget)==70 for budget in spec.allocation.budget_ids)


def test_settlement_abort_rolls_back_actual_and_receipt(accounting,monkeypatch):
    service, _, spec, database=accounting
    cycle,operation=admitted(accounting),uuid4()
    transfer(service,cycle,operation)
    original=CycleCostLedger._event
    def fail(*args,**kwargs):
        raise RuntimeError("settlement before commit")
    monkeypatch.setattr(CycleCostLedger,"_event",fail)
    with pytest.raises(RuntimeError):
        command(service,cycle,operation,"settle","invoice",actual_micros=40)
    assert all(committed(database,budget)==80 for budget in spec.allocation.budget_ids)
    monkeypatch.setattr(CycleCostLedger,"_event",original)
    result=command(service,cycle,operation,"settle","invoice",actual_micros=40)
    assert command(service,cycle,operation,"settle","invoice",actual_micros=40)==result
    assert all(committed(database,budget)==70 for budget in spec.allocation.budget_ids)


def test_dispatch_after_closure_denied_but_unknown_reconciliation_retained(accounting):
    service, _, spec, database=accounting
    cycle,operation=admitted(accounting),uuid4()
    transfer(service,cycle,operation)
    service.close(cycle,disposition="cancelled",reason="fixture stopped")
    with pytest.raises(ValueError,match="open cycle"):
        command(service,cycle,operation,"dispatch","late-dispatch")
    command(service,cycle,operation,"unknown","late-unknown")
    assert all(committed(database,budget)==50 for budget in spec.allocation.budget_ids)


def test_legacy_reservation_competes_in_same_canonical_cap(accounting):
    service, goal, spec, database=accounting
    with psycopg.connect(database) as connection:
        connection.execute("INSERT INTO budget_reservations (budget_id,reservation_key,estimated_amount,reserved_amount,status) VALUES (%s,'legacy',0.000030,0.000030,'reserved')",(spec.allocation.budget_ids[0],))
    intent=service.request_cycle(goal,request(spec))
    assert service.admit(intent)["reason"]=="budget_capacity"
    assert committed(database,spec.allocation.budget_ids[0])==30


def test_operation_scope_and_reserved_close_key(accounting):
    service,_,spec,database=accounting
    cycle,operation=admitted(accounting),uuid4()
    transfer(service,cycle,operation)
    with pytest.raises(ValueError,match="reserved closure"):
        transfer(service,cycle,key="cycle-close")
    with pytest.raises(ValueError,match="reserved closure"):
        command(service,cycle,None,"release_allocation","cycle-close")
    with psycopg.connect(database) as connection:
        connection.execute("UPDATE budgets SET limit_amount=1 WHERE id=ANY(%s)",(list(spec.allocation.budget_ids),))
    other=approved_goal(service,spec)
    other_cycle=service.admit(service.request_cycle(other,request(spec)))["cycle_id"]
    with pytest.raises(IdempotencyConflict,match="identity"):
        transfer(service,other_cycle,operation)
    with pytest.raises(PermissionError,match="scope"):
        command(service,other_cycle,operation,"settle","wrong-cycle",actual_micros=40)


def test_changed_frozen_authority_blocks_new_transfer_but_preserves_context(accounting):
    service,_,_,database=accounting
    cycle=admitted(accounting)
    original=next(row["payload"] for row in rows(database,"v4_run_contexts") if row["cycle_id"]==cycle)
    with psycopg.connect(database) as connection:
        connection.execute("UPDATE permission_grants SET expires_at=expires_at+interval '1 minute' WHERE workspace_id=%s AND scope='cycles:write'",(service.workspace_id,))
    with pytest.raises(PermissionError,match="frozen authority"):
        transfer(service,cycle)
    assert next(row["payload"] for row in rows(database,"v4_run_contexts") if row["cycle_id"]==cycle)==original


@pytest.mark.parametrize("category",["research","generation","publication","evaluation","training"])
def test_required_cost_categories_are_fixture_only_attributable(accounting,category):
    service,_,_,database=accounting
    cycle,operation=admitted(accounting),uuid4()
    result=service.transfer_cost(cycle,operation_id=operation,idempotency_key=category,estimated_micros=1,reserved_micros=1,category=category)
    with psycopg.connect(database) as connection:
        assert connection.execute("SELECT category,provider FROM v4_cost_operations WHERE id=%s",(operation,)).fetchone()==(category,"fixture.accounting")
    assert result["state"]=="reserved"


@pytest.mark.parametrize("action",["transfer","settle"])
def test_database_connection_loss_before_commit_retains_original_balance(accounting,action):
    service,_,spec,database=accounting
    cycle,operation=admitted(accounting),uuid4()
    if action=="settle":
        transfer(service,cycle,operation)
    with psycopg.connect(database) as admin:
        with pytest.raises(psycopg.OperationalError), psycopg.connect(database) as connection:
            ledger=CycleCostLedger(connection,workspace_id=service.workspace_id,subject_id=service.subject_id,traceparent=service.trace.to_carrier()["traceparent"])
            if action=="transfer":
                ledger.transfer(cycle,operation_id=operation,idempotency_key="transfer",estimated_micros=30,reserved_micros=50,category="generation")
            else:
                ledger.command(cycle,operation_id=operation,action="settle",idempotency_key="invoice",proof_ref="fixture-receipt:invoice",actual_micros=40)
            assert admin.execute("SELECT pg_terminate_backend(%s)",(connection.info.backend_pid,)).fetchone()[0]
    assert all(committed(database,budget)==80 for budget in spec.allocation.budget_ids)
    if action=="transfer":
        first=transfer(service,cycle,operation)
        assert transfer(service,cycle,operation)==first
    else:
        command(service,cycle,operation,"settle","invoice",actual_micros=40)
        assert all(committed(database,budget)==70 for budget in spec.allocation.budget_ids)


def test_accounting_nonowner_role_without_cap_or_authority_edit_privileges(accounting):
    from urllib.parse import urlsplit, urlunsplit
    from salience.cycles.admission import CycleAdmission
    service,goal,spec,database=accounting
    role,password="v4_cost_"+uuid4().hex,uuid4().hex
    identifier=psycopg.sql.Identifier(role)
    with psycopg.connect(database,autocommit=True) as admin:
        admin.execute(psycopg.sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS").format(identifier,psycopg.sql.Literal(password)))
        try:
            admin.execute(psycopg.sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(identifier))
            admin.execute(psycopg.sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(identifier))
            admin.execute(psycopg.sql.SQL("GRANT INSERT ON v4_cycle_requests,v4_cycle_intents,v4_cycle_events,v4_cycles,v4_run_contexts,v4_cycle_outbox,v4_admissions,v4_cycle_allocations,v4_cost_operations,v4_allocation_reservations,v4_cost_commands,budget_reservations,cost_ledger_entries TO {}").format(identifier))
            admin.execute(psycopg.sql.SQL("GRANT UPDATE ON v4_goals,v4_cycle_intents,v4_cycles,budget_reservations TO {}").format(identifier))
            for function in ["p0_lock_identity(text,text,uuid)","v4_lock_program(uuid,uuid)","v4_lock_budgets(uuid[],uuid)"]:
                admin.execute(psycopg.sql.SQL("GRANT EXECUTE ON FUNCTION "+function+" TO {}").format(identifier))
            parts=urlsplit(database)
            restricted=urlunsplit(parts._replace(netloc=f"{role}:{password}@{parts.hostname}:{parts.port or 5432}"))
            with psycopg.connect(restricted) as reader:
                for table in ["budgets","permission_grants","identity_subjects","content_programs"]:
                    assert not reader.execute("SELECT has_table_privilege(current_user,%s,'UPDATE')",(table,)).fetchone()[0]
                reader.execute("SELECT * FROM v4_lock_budgets(%s,%s)",(list(spec.allocation.budget_ids),service.workspace_id))
                with psycopg.connect(database) as writer, pytest.raises(psycopg.errors.LockNotAvailable):
                    writer.execute("SET LOCAL lock_timeout='100ms'")
                    writer.execute("UPDATE budgets SET limit_amount=0 WHERE id=%s",(spec.allocation.budget_ids[0],))
            local=CycleAdmission(restricted,workspace_id=service.workspace_id,subject_id=service.subject_id)
            cycle=local.admit(local.request_cycle(goal,request(spec)))["cycle_id"]
            operation=uuid4()
            transfer(local,cycle,operation)
            command(local,cycle,operation,"settle","invoice",actual_micros=40)
            with psycopg.connect(database) as writer:
                writer.execute("UPDATE permission_grants SET effect='deny' WHERE workspace_id=%s AND scope='cycles:accounting'",(service.workspace_id,))
            with pytest.raises(PermissionError):
                transfer(local,cycle,key="revoked",estimate=1,upper=1)
            local.close(cycle,disposition="completed",reason="restricted fixture")
            assert all(committed(database,budget)==40 for budget in spec.allocation.budget_ids)
        finally:
            admin.execute(psycopg.sql.SQL("DROP OWNED BY {}").format(identifier))
            admin.execute(psycopg.sql.SQL("DROP ROLE {}").format(identifier))
