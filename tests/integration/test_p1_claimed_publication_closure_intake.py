"""Accept fixture-signed closure through the canonical claimed publication case."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from uuid import uuid4

import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from salience.cycles import p1_recovery_rollback as rollback
from salience.cycles import production_recovery as recovery
from salience.fixtures.publication_send_barrier import (
    FixturePublicationBarrierScope,
    FixturePublicationSend,
    FixturePublicationSendBarrier,
)
from test_p1_claimed_reserved_recovery import seed_claimed_reserved_publication
from test_p1_production_policy_revocation import accounting, cycles, policy
from test_p1_production_recovery_case_opening import (
    _controller,
    _opening_database,
    _publication_liabilities,
    _restricted_role,
)
from test_p1_production_recovery import _task9_set_stop_scope


_EXECUTE_CLOSURE = (
    "EXECUTE ON FUNCTION public.v4_read_claimed_publication_closure_source_v1(uuid)",
    "EXECUTE ON FUNCTION public.v4_record_claimed_publication_closure_v1(uuid,jsonb)",
)


@pytest.fixture(scope="module")
def closure_database():
    with _opening_database() as database:
        yield database


@pytest.fixture
def claimed_source(closure_database, monkeypatch, request):
    monkeypatch.setenv("TEST_DATABASE_URL", closure_database)
    accounting_state = request.getfixturevalue("accounting")
    case, authority, command = seed_claimed_reserved_publication(accounting_state, closure_database)
    controller = _controller(closure_database, authority)
    opened = controller.open_case(command)
    original = case["request"]
    send = FixturePublicationSend(
        original.operation_id, command.request_fingerprint, original.provider_id, str(original.account_id),
        original.idempotency_key, case["opening_permit_id"], case["opening_claim_id"], 1,
    )
    scope = FixturePublicationBarrierScope(original.route.environment_id, "default", uuid4())
    return controller, opened, scope, send, command


def _close(path, signer, scope, send):
    barrier = FixturePublicationSendBarrier.create(path, scope=scope, signer=signer)
    barrier.register_predecessor(send)
    proof = barrier.close_predecessor(send)
    assert proof is not None
    return barrier, proof


def _authorized_controller(database, opener, trust, decide):
    opener.authority.authorize_claimed_publication_closure = decide
    return recovery.ProductionRecoveryCaseController(
        recovery.ProductionRecoveryWorkRepository(database),
        authority=opener.authority, commands=None, closure_trust=trust,
    )


def test_closure_intake_records_original_claim_without_changing_dispatch(claimed_source, tmp_path):
    opener, opened, scope, send, opening_command = claimed_source
    database = opener.repository.database_url
    signer = Ed25519PrivateKey.generate()
    barrier, proof = _close(tmp_path / "barrier.sqlite", signer, scope, send)
    _, competing_proof = _close(tmp_path / "competing-barrier.sqlite", signer, scope, send)
    trust = recovery.ProductionClaimedPublicationClosureTrust(scope, signer.public_key())
    decisions = []

    def decide(command):
        assert (command.work_id, command.workspace_id, command.case_id) == (
            opened.work_id, opening_command.workspace_id, opened.case_id,
        )
        decisions.append(recovery.ProductionClaimedPublicationClosureAuthorization(
            command, uuid4(), uuid4(), datetime.now(timezone.utc) + timedelta(seconds=30),
        ))
        return decisions[-1]

    before = _publication_liabilities(database, send.operation_id)
    work_before = opener.repository.get_work(opened.work_id)
    with _restricted_role(database, _EXECUTE_CLOSURE) as writer:
        controller = _authorized_controller(writer, opener, trust, decide)
        first = controller.record_claimed_publication_closure(opened.work_id, proof)
        replay = controller.record_claimed_publication_closure(opened.work_id, proof)
        with pytest.raises(recovery.RecoveryEventConflict, match="closure evidence conflict"):
            controller.record_claimed_publication_closure(opened.work_id, competing_proof)
        controller.authority.authorize_claimed_publication_closure = None
        with pytest.raises(PermissionError, match="closure authorization"):
            controller.record_claimed_publication_closure(opened.work_id, proof)

    assert replay == first
    assert (first.work_id, first.operation_id, first.closure_id, first.policy_decision_id, first.route_decision_id) == (
        opened.work_id, send.operation_id, proof.closure_id,
        decisions[0].policy_decision_id, decisions[0].route_decision_id,
    )
    assert [decision.command.proof for decision in decisions] == [proof, proof, competing_proof]
    assert _publication_liabilities(database, send.operation_id) == before
    assert opener.repository.get_work(opened.work_id) == work_before
    with pytest.raises(PermissionError):
        barrier.accept_predecessor(send)

    with psycopg.connect(database) as connection:
        for scope_key in ("workspace", f"goal:{work_before.goal_id}"):
            _task9_set_stop_scope(
                connection, opening_command.workspace_id, scope_key, 2, True,
                opening_command.actor_id, "closure-rollback-stop",
            )
    with pytest.raises(rollback.RecoveryRollbackVerificationError, match="exact 0060"):
        rollback.read_p1_recovery_rollback_inventory_v7(database, workspace_id=opening_command.workspace_id)
    inventory = rollback.read_p1_recovery_rollback_inventory_v8(
        database, workspace_id=opening_command.workspace_id,
    )
    assert inventory.inventory["relation_counts"]["claimed_publication_closures"] == 1
    persisted, = inventory.inventory["claimed_publication_closures"]
    assert {key: persisted[key] for key in (
        "operation_id", "permit_id", "claim_id", "request_fingerprint", "environment_id", "namespace",
        "barrier_id", "closure_id", "signer_sha256", "signature_hex",
    )} == {
        "operation_id": str(send.operation_id), "permit_id": str(send.permit_id), "claim_id": str(send.claim_id),
        "request_fingerprint": send.request_sha256, "environment_id": str(scope.environment_id),
        "namespace": scope.namespace, "barrier_id": str(scope.barrier_id), "closure_id": str(proof.closure_id),
        "signer_sha256": sha256(signer.public_key().public_bytes_raw()).hexdigest(),
        "signature_hex": proof.signature.hex(),
    }
    payload = inventory.inventory
    closure_counts = payload["relation_counts"]
    invalid_inventories = (
        ("incomplete", {"relation_counts": closure_counts | {"claimed_publication_closures": 0}}),
        ("differs from its source", {"claimed_publication_closures": [persisted | {"claim_id": str(uuid4())}]}),
        ("differs from its source", {"claimed_publication_closures": [persisted | {"permit_id": str(uuid4())}]}),
        ("differs from its source", {
            "claimed_publication_closures": [persisted, persisted],
            "relation_counts": closure_counts | {"claimed_publication_closures": 2},
        }),
        ("signature encoding", {"claimed_publication_closures": [persisted | {"signature_hex": "g" * 128}]}),
    )
    for error, changes in invalid_inventories:
        with pytest.raises(rollback.RecoveryRollbackVerificationError, match=error):
            rollback.validate_recovery_rollback_inventory_v8(
                payload | changes, workspace_id=opening_command.workspace_id,
            )


@pytest.mark.parametrize("mismatch", ["no_trust", "key", "barrier", "namespace", "environment", "operation", "original_key"])
def test_closure_intake_denies_untrusted_or_mismatched_proof(claimed_source, tmp_path, mismatch):
    opener, opened, scope, send, _ = claimed_source
    signer = Ed25519PrivateKey.generate()
    signed_send = send
    if mismatch == "operation":
        signed_send = replace(send, operation_id=uuid4())
    if mismatch == "original_key":
        signed_send = replace(send, original_key="different-original-key")
    if mismatch == "environment":
        scope = replace(scope, environment_id=uuid4())
    _, proof = _close(tmp_path / "barrier.sqlite", signer, scope, signed_send)
    trusted_scope = scope
    if mismatch == "barrier":
        trusted_scope = replace(scope, barrier_id=uuid4())
    if mismatch == "namespace":
        trusted_scope = replace(scope, namespace="another-namespace")
    trusted_signer = Ed25519PrivateKey.generate() if mismatch == "key" else signer

    def unexpected_decision(_command):
        pytest.fail("untrusted finality reached stage authorization")

    if mismatch == "no_trust":
        with pytest.raises(PermissionError):
            opener.record_claimed_publication_closure(opened.work_id, proof)
    else:
        trust = recovery.ProductionClaimedPublicationClosureTrust(trusted_scope, trusted_signer.public_key())
        with _restricted_role(opener.repository.database_url, _EXECUTE_CLOSURE) as writer:
            controller = _authorized_controller(writer, opener, trust, unexpected_decision)
            with pytest.raises(PermissionError):
                controller.record_claimed_publication_closure(opened.work_id, proof)


@pytest.mark.parametrize("mismatch", ["absent", "untyped", "other_work", "other_proof", "expired"])
def test_closure_intake_requires_current_exact_stage_decision(claimed_source, tmp_path, mismatch):
    opener, opened, scope, send, _ = claimed_source
    signer = Ed25519PrivateKey.generate()
    _, proof = _close(tmp_path / "barrier.sqlite", signer, scope, send)
    trust = recovery.ProductionClaimedPublicationClosureTrust(scope, signer.public_key())

    def decide(command):
        if mismatch == "untyped":
            return True
        if mismatch == "other_work":
            command = replace(command, work_id=uuid4())
        if mismatch == "other_proof":
            command = replace(command, proof=replace(proof, closure_id=uuid4()))
        expiry = datetime.now(timezone.utc) + timedelta(seconds=-1 if mismatch == "expired" else 30)
        return recovery.ProductionClaimedPublicationClosureAuthorization(command, uuid4(), uuid4(), expiry)

    with _restricted_role(opener.repository.database_url, _EXECUTE_CLOSURE[:1]) as reader:
        controller = _authorized_controller(reader, opener, trust, None if mismatch == "absent" else decide)
        with pytest.raises(PermissionError, match="closure authorization"):
            controller.record_claimed_publication_closure(opened.work_id, proof)


@pytest.mark.parametrize("clear_stop", [False, True], ids=["activated", "activated-then-cleared"])
def test_closure_intake_rechecks_stops_after_stage_authorization(claimed_source, tmp_path, clear_stop):
    opener, opened, scope, send, opening_command = claimed_source
    database = opener.repository.database_url
    signer = Ed25519PrivateKey.generate()
    _, proof = _close(tmp_path / "barrier.sqlite", signer, scope, send)
    trust = recovery.ProductionClaimedPublicationClosureTrust(scope, signer.public_key())

    def decide(command):
        decision = recovery.ProductionClaimedPublicationClosureAuthorization(
            command, uuid4(), uuid4(), datetime.now(timezone.utc) + timedelta(seconds=30),
        )
        with psycopg.connect(database) as connection:
            _task9_set_stop_scope(
                connection, opening_command.workspace_id, "workspace", 2, True,
                opening_command.actor_id, "closure-concurrent-stop",
            )
            if clear_stop:
                _task9_set_stop_scope(
                    connection, opening_command.workspace_id, "workspace", 3, False,
                    opening_command.actor_id, "closure-concurrent-clear",
                )
        return decision

    before = _publication_liabilities(database, send.operation_id)
    with _restricted_role(database, _EXECUTE_CLOSURE) as writer:
        controller = _authorized_controller(writer, opener, trust, decide)
        with pytest.raises(PermissionError):
            controller.record_claimed_publication_closure(opened.work_id, proof)
    with psycopg.connect(database) as connection:
        assert connection.execute(
            "SELECT 1 FROM v4_claimed_publication_closures WHERE work_id=%s", (opened.work_id,),
        ).fetchone() is None
    assert _publication_liabilities(database, send.operation_id) == before
