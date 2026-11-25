"""Claim-clock evidence cannot authorize a successor for a production publication."""

from dataclasses import replace
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest

from salience.cycles.production_dispatch import ProductionAuthorizationDenied, PublicationEffectBinding
from test_p1_production_policy_revocation import _insert_task7_no_effect_command, _production_case
from test_v4_cycle_accounting import accounting, policy
from test_v4_cycle_admission import cycles
from test_v4_migrations import _migration


_PREVIOUS_REVISION = "0059_p1_claimed_reserved_opening"
_DENIAL = "claimed_publication_reissue_requires_finality"
_ADVANCE = "public.v4_advance_production_current_dispatch_v1(uuid,integer,uuid,uuid,text,uuid)"
_FENCE_TRIGGER = "fence_legacy_claimed_publication_reissue"
_FENCE_FUNCTION = "public.v4_fence_legacy_claimed_publication_reissue_v1()"


def _seed_claimed_publication(accounting):
    case = _production_case(
        accounting,
        publication_scope={
            "platform": "p1-local", "destination": "channel:approved",
            "locale": "en-US", "territory": "US", "visibility": "public",
        },
        publication_binding=PublicationEffectBinding(
            publication_request_id=uuid4(), job_id=uuid4(), workflow_id="claimed-fence-workflow",
            workflow_run_id=str(uuid4()), publication_plan_id=uuid4(), publication_attempt_id=uuid4(),
            external_effect_id=uuid4(),
        ),
        seed_publication_rows=True,
    )
    request = case["request"]
    dispatch = case["dispatch"]
    predecessor = dispatch.issue_permit(request)
    claimed = dispatch.claim_permit(request.operation_id, predecessor.permit_id)
    proof = _insert_task7_no_effect_command(case, request)
    successor_request = replace(request, generation=2, recovery_command_id=proof["event_id"])
    return case, claimed, successor_request, proof


def _upgrade(database):
    result = _migration(database, "upgrade", "head")
    assert result.returncode == 0, result.stdout + result.stderr


def _insert_successor(connection, predecessor_id, successor_id, event_id, *, omit=()):
    connection.execute(
        """INSERT INTO public.v4_production_effect_permits
           SELECT (jsonb_populate_record(NULL::public.v4_production_effect_permits,
                   to_jsonb(prior) || jsonb_build_object(
                       'permit_id',%s::uuid,'generation',2,'recovery_command_id',%s::uuid,
                       'request_snapshot',(prior.request_snapshot - %s::text[]) || jsonb_build_object(
                           'generation',2,'recovery_command_id',%s::uuid)))).*
           FROM public.v4_production_effect_permits prior WHERE prior.permit_id=%s""",
        (successor_id, event_id, list(omit), event_id, predecessor_id),
    )


def _advance_pointer(connection, successor_request, successor_id, *, expected_generation=1, claim_id=None):
    connection.execute(
        "SELECT public.v4_advance_production_current_dispatch_v1(%s,%s,%s,%s,%s,%s)",
        (successor_request.operation_id, expected_generation, successor_id, claim_id, successor_request.idempotency_key,
         successor_request.recovery_command_id),
    )


def _insert_claim(connection, operation_id, successor_id, claim_id):
    connection.execute(
        """INSERT INTO public.v4_production_effect_permit_claims(
               claim_id,permit_id,operation_id,generation,route_fence_decision_id)
           VALUES(%s,%s,%s,2,%s)""",
        (claim_id, successor_id, operation_id, uuid4()),
    )


def _pointer(database, operation_id):
    with psycopg.connect(database) as connection:
        return connection.execute(
            """SELECT current_permit_id,generation,current_claim_id
               FROM public.v4_production_current_dispatch WHERE operation_id=%s""",
            (operation_id,),
        ).fetchone()


def _set_test_role(connection, *grants):
    role = sql.Identifier(f"p1_claimed_fence_{uuid4().hex[:12]}")
    connection.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(role))
    connection.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
    for grant in grants:
        connection.execute(sql.SQL("GRANT " + grant + " TO {}").format(role))
    connection.execute(sql.SQL("SET LOCAL ROLE {}").format(role))


@pytest.mark.parametrize("cycle_database", [_PREVIOUS_REVISION], indirect=True)
def test_publication_dispatch_denies_claim_clock_reissue_before_route_fencing(accounting):
    case, predecessor, proposed, _ = _seed_claimed_publication(accounting)
    database = case["database"]
    _upgrade(database)
    route_calls = list(case["route"].calls)

    with pytest.raises(ProductionAuthorizationDenied, match=_DENIAL):
        case["dispatch"].issue_permit(proposed)

    assert case["route"].calls == route_calls
    assert _pointer(database, proposed.operation_id) == (predecessor.permit_id, 1, predecessor.claim_id)
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT count(*) FROM public.v4_production_effect_permits WHERE operation_id=%s",
            (proposed.operation_id,),
        ).fetchone() == (1,)


@pytest.mark.parametrize("cycle_database", [_PREVIOUS_REVISION], indirect=True)
@pytest.mark.parametrize("omit", [(), ("publication_request_id",)])
def test_database_fences_direct_legacy_successor_permit_insertion(accounting, omit):
    case, predecessor, proposed, proof = _seed_claimed_publication(accounting)
    database = case["database"]
    _upgrade(database)

    with psycopg.connect(database) as connection:
        with pytest.raises(psycopg.errors.RaiseException, match=_DENIAL), connection.transaction(force_rollback=True):
            _set_test_role(connection, "SELECT, INSERT ON public.v4_production_effect_permits")
            _insert_successor(connection, predecessor.permit_id, uuid4(), proof["event_id"], omit=omit)

    assert _pointer(database, proposed.operation_id) == (predecessor.permit_id, 1, predecessor.claim_id)


@pytest.mark.parametrize("cycle_database", [_PREVIOUS_REVISION], indirect=True)
def test_database_fences_protected_advance_for_pending_legacy_permit(accounting):
    case, predecessor, proposed, proof = _seed_claimed_publication(accounting)
    database = case["database"]
    pending_successor_id = uuid4()
    with psycopg.connect(database) as connection:
        _insert_successor(connection, predecessor.permit_id, pending_successor_id, proof["event_id"])
    _upgrade(database)

    with psycopg.connect(database) as connection:
        with pytest.raises(psycopg.errors.RaiseException, match=_DENIAL), connection.transaction(force_rollback=True):
            _set_test_role(connection, f"EXECUTE ON FUNCTION {_ADVANCE}")
            _advance_pointer(connection, proposed, pending_successor_id)

    assert _pointer(database, proposed.operation_id) == (predecessor.permit_id, 1, predecessor.claim_id)


@pytest.mark.parametrize("cycle_database", [_PREVIOUS_REVISION], indirect=True)
def test_database_and_dispatch_fence_a_current_unclaimed_legacy_permit(accounting):
    case, predecessor, proposed, proof = _seed_claimed_publication(accounting)
    database = case["database"]
    dispatch = case["dispatch"]
    successor_id = uuid4()
    with psycopg.connect(database) as connection:
        _insert_successor(connection, predecessor.permit_id, successor_id, proof["event_id"])
        _advance_pointer(connection, proposed, successor_id)
    _upgrade(database)
    route_calls = list(case["route"].calls)

    with pytest.raises(ProductionAuthorizationDenied, match=_DENIAL):
        dispatch.issue_permit(proposed)
    with pytest.raises(ProductionAuthorizationDenied, match=_DENIAL):
        dispatch.claim_permit(proposed.operation_id, successor_id)
    delivery = dispatch.authorize_action(proposed.operation_id, successor_id, "delivery")
    assert not delivery.authorized and delivery.reason_code == _DENIAL
    assert case["route"].calls == route_calls

    binding = proposed.publication_binding
    assert dispatch.cancel_unsubmitted_publication(
        proposed.operation_id, successor_id, binding.publication_request_id,
        binding.publication_plan_id, binding.publication_attempt_id, binding.external_effect_id,
    ) is None

    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT status FROM public.external_effects WHERE id=%s", (binding.external_effect_id,),
        ).fetchone() == ("planned",)
        assert connection.execute(
            "SELECT status FROM public.budget_reservations WHERE id=%s", (case["production_reservation_id"],),
        ).fetchone() == ("reserved",)
        with pytest.raises(psycopg.errors.RaiseException, match=_DENIAL), connection.transaction(force_rollback=True):
            _set_test_role(connection, "INSERT ON public.v4_production_effect_permit_claims")
            _insert_claim(connection, proposed.operation_id, successor_id, uuid4())
        inventory = connection.execute(
            "SELECT public.v4_p1_recovery_rollback_inventory_v7(%s)", (proposed.workspace_id,),
        ).fetchone()[0]
    assert inventory["schema_version"] == "salience.p1-recovery-rollback-inventory.v7"
    assert inventory["migration_head"] == "0060_p1_claimed_reissue_fence"
    assert {
        "kind": f"production_{_DENIAL}", "operation_id": str(proposed.operation_id),
        "permit_id": str(successor_id), "generation": 2,
    } in inventory["blockers"]
    assert _pointer(database, proposed.operation_id) == (successor_id, 2, None)


@pytest.mark.parametrize("cycle_database", [_PREVIOUS_REVISION], indirect=True)
@pytest.mark.parametrize("claim_attached", [False, True])
def test_inventory_flags_unresolved_legacy_successors_with_historical_claims(accounting, claim_attached):
    case, predecessor, proposed, proof = _seed_claimed_publication(accounting)
    database = case["database"]
    successor_id, claim_id = uuid4(), uuid4()
    with psycopg.connect(database) as connection:
        _insert_successor(connection, predecessor.permit_id, successor_id, proof["event_id"])
        _advance_pointer(connection, proposed, successor_id)
        _insert_claim(connection, proposed.operation_id, successor_id, claim_id)
        if claim_attached:
            _advance_pointer(connection, proposed, successor_id, expected_generation=2, claim_id=claim_id)
    _upgrade(database)

    if claim_attached:
        route_calls = list(case["route"].calls)
        for action in ("submit", "cancel"):
            decision = case["dispatch"].authorize_action(proposed.operation_id, successor_id, action)
            assert not decision.authorized and decision.reason_code == _DENIAL
        assert case["route"].calls == route_calls

    with psycopg.connect(database) as connection:
        if not claim_attached:
            with pytest.raises(psycopg.errors.RaiseException, match=_DENIAL), connection.transaction(force_rollback=True):
                _set_test_role(connection, f"EXECUTE ON FUNCTION {_ADVANCE}")
                _advance_pointer(connection, proposed, successor_id, expected_generation=2, claim_id=claim_id)
        inventory = connection.execute(
            "SELECT public.v4_p1_recovery_rollback_inventory_v7(%s)", (proposed.workspace_id,),
        ).fetchone()[0]
    assert {
        "kind": f"production_{_DENIAL}", "operation_id": str(proposed.operation_id),
        "permit_id": str(successor_id), "generation": 2,
    } in inventory["blockers"]
    assert _pointer(database, proposed.operation_id) == (
        successor_id, 2, claim_id if claim_attached else None,
    )


@pytest.mark.parametrize("cycle_database", [_PREVIOUS_REVISION], indirect=True)
def test_inventory_blocks_missing_disabled_or_changed_fence_guards(accounting):
    case, _, proposed, _ = _seed_claimed_publication(accounting)
    _upgrade(case["database"])

    def blockers(connection):
        return connection.execute(
            "SELECT public.v4_p1_recovery_rollback_inventory_v7(%s)->'blockers'",
            (proposed.workspace_id,),
        ).fetchone()[0]

    drift = {"kind": "production_claimed_reissue_fence_definition_mismatch"}
    with psycopg.connect(case["database"]) as connection:
        assert drift not in blockers(connection)
        for table in (
            "v4_production_effect_permits",
            "v4_production_effect_permit_claims",
            "v4_production_current_dispatch",
        ):
            for alteration in (
                f"ALTER TABLE public.{table} DISABLE TRIGGER {_FENCE_TRIGGER}",
                f"ALTER TABLE public.{table} ENABLE REPLICA TRIGGER {_FENCE_TRIGGER}",
                f"DROP TRIGGER {_FENCE_TRIGGER} ON public.{table}",
            ):
                with connection.transaction(force_rollback=True):
                    connection.execute(alteration)
                    assert drift in blockers(connection), alteration

        for action, predicate, function in (
            ("AFTER INSERT", "NEW.generation > 1", _FENCE_FUNCTION),
            ("BEFORE INSERT", "false", _FENCE_FUNCTION),
            ("BEFORE INSERT", "NEW.generation > 1", "public.v4_validate_production_effect_permit_v2()"),
        ):
            with connection.transaction(force_rollback=True):
                connection.execute(
                    f"DROP TRIGGER {_FENCE_TRIGGER} ON public.v4_production_effect_permits"
                )
                connection.execute(
                    f"CREATE TRIGGER {_FENCE_TRIGGER} {action} ON public.v4_production_effect_permits "
                    f"FOR EACH ROW WHEN ({predicate}) EXECUTE FUNCTION {function}"
                )
                assert drift in blockers(connection), (action, predicate, function)
        with connection.transaction(force_rollback=True):
            connection.execute(
                f"CREATE OR REPLACE FUNCTION {_FENCE_FUNCTION} RETURNS trigger "
                "LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog, public "
                "AS $$ BEGIN RETURN NEW; END $$"
            )
            assert drift in blockers(connection)
