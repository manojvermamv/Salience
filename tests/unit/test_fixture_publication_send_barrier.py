import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import asdict, replace
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from salience.fixtures.publication_send_barrier import (
    FixturePublicationBarrierScope,
    FixturePublicationSend,
    FixturePublicationSendBarrier,
    FixturePublicationSuccessorSend,
    verify_fixture_publication_no_effect,
)


def _successor_of(send):
    return FixturePublicationSuccessorSend(
        **(asdict(send) | {"generation": 2, "permit_id": uuid4(), "claim_id": uuid4()}),
    )


@pytest.fixture
def registered_predecessor(tmp_path):
    scope = FixturePublicationBarrierScope(uuid4(), "local-recovery", uuid4())
    send = FixturePublicationSend(uuid4(), "a" * 64, "mock", "private", "original-key", uuid4(), uuid4(), 1)
    signer = Ed25519PrivateKey.generate()
    path = tmp_path / "fixture.sqlite"
    gateway = FixturePublicationSendBarrier.create(path, scope=scope, signer=signer)
    gateway.register_predecessor(send)
    return gateway, send, path, scope, signer


def test_claimed_send_closure_survives_reopening_and_rejects_the_predecessor(tmp_path):
    scope = FixturePublicationBarrierScope(uuid4(), "local-recovery", uuid4())
    send = FixturePublicationSend(uuid4(), "a" * 64, "mock", "private", "original-key", uuid4(), uuid4(), 1)
    signer = Ed25519PrivateKey.generate()
    path = tmp_path / "mock-acceptance.sqlite"
    gateway = FixturePublicationSendBarrier.create(path, scope=scope, signer=signer)

    with pytest.raises(PermissionError):
        gateway.close_predecessor(send)
    gateway.register_predecessor(send)
    proof = gateway.close_predecessor(send)
    assert verify_fixture_publication_no_effect(
        proof, scope=scope, expected_send=send, public_key=signer.public_key()
    ) == proof

    restarted = FixturePublicationSendBarrier(path, scope=scope, signer=signer)
    restarted.register_predecessor(send)
    assert restarted.close_predecessor(send) == proof
    with pytest.raises(PermissionError):
        restarted.accept_predecessor(send)
    with pytest.raises(PermissionError):
        restarted.register_predecessor(replace(send, claim_id=uuid4()))
    with pytest.raises(PermissionError):
        FixturePublicationSendBarrier(path, scope=replace(scope, environment_id=uuid4()), signer=signer)
    with pytest.raises(PermissionError):
        FixturePublicationSendBarrier(path, scope=scope, signer=Ed25519PrivateKey.generate())
    with pytest.raises(FileExistsError):
        FixturePublicationSendBarrier.create(path, scope=scope, signer=signer)


def test_fixture_finality_proof_is_bound_to_scope_claim_and_configured_signer(tmp_path):
    scope = FixturePublicationBarrierScope(uuid4(), "local-recovery", uuid4())
    send = FixturePublicationSend(uuid4(), "a" * 64, "mock", "private", "original-key", uuid4(), uuid4(), 1)
    signer = Ed25519PrivateKey.generate()
    gateway = FixturePublicationSendBarrier.create(tmp_path / "fixture.sqlite", scope=scope, signer=signer)
    gateway.register_predecessor(send)
    proof = gateway.close_predecessor(send)

    other_send = replace(send, request_sha256="b" * 64)
    other_scope = replace(scope, namespace="other-recovery")
    for proposed, expected_scope, expected_send, key in (
        (proof, scope, other_send, signer.public_key()),
        (proof, other_scope, send, signer.public_key()),
        (proof, scope, send, Ed25519PrivateKey.generate().public_key()),
        (replace(proof, send=other_send), scope, other_send, signer.public_key()),
        (replace(proof, closure_id=uuid4()), scope, send, signer.public_key()),
    ):
        with pytest.raises(PermissionError):
            verify_fixture_publication_no_effect(
                proposed, scope=expected_scope, expected_send=expected_send, public_key=key,
            )


@pytest.mark.parametrize("first", ["accept", "close", "race"])
def test_fixture_acceptance_and_permanent_close_serialize_on_one_original_operation(tmp_path, first):
    scope = FixturePublicationBarrierScope(uuid4(), "local-recovery", uuid4())
    send = FixturePublicationSend(uuid4(), "a" * 64, "mock", "private", "original-key", uuid4(), uuid4(), 1)
    signer = Ed25519PrivateKey.generate()
    path = tmp_path / "fixture.sqlite"
    gateway = FixturePublicationSendBarrier.create(path, scope=scope, signer=signer)
    gateway.register_predecessor(send)
    other = FixturePublicationSendBarrier(path, scope=scope, signer=signer)
    start = Barrier(2)

    def accept():
        if first == "race":
            start.wait(timeout=5)
        try:
            return gateway.accept_predecessor(send)
        except PermissionError:
            return None

    def close():
        if first == "race":
            start.wait(timeout=5)
        return other.close_predecessor(send)

    if first == "accept":
        receipt, proof = accept(), close()
        assert receipt is not None
    elif first == "close":
        proof, receipt = close(), accept()
        assert proof is not None
    else:
        with ThreadPoolExecutor(max_workers=2) as executor:
            accepted = executor.submit(accept)
            closed = executor.submit(close)
            receipt, proof = accepted.result(timeout=10), closed.result(timeout=10)

    assert (receipt is None) != (proof is None)
    assert other.close_predecessor(send) == proof
    if receipt is not None:
        assert other.accept_predecessor(send) == receipt
    else:
        with pytest.raises(PermissionError):
            other.accept_predecessor(send)


def test_fixture_successor_lazily_extends_legacy_store_and_replays_exactly(registered_predecessor):
    gateway, predecessor, path, scope, signer = registered_predecessor
    proof = gateway.close_predecessor(predecessor)
    successor = _successor_of(predecessor)
    with closing(sqlite3.connect(path)) as database:
        assert database.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name").fetchall() == [
            ("fixture_identity",), ("predecessor",),
        ]

    reopened = FixturePublicationSendBarrier(path, scope=scope, signer=signer)
    receipt = reopened.accept_successor(predecessor, proof, successor)
    assert isinstance(receipt, UUID)
    assert reopened.accept_successor(predecessor, proof, successor) == receipt

    restarted = FixturePublicationSendBarrier(path, scope=scope, signer=signer)
    assert restarted.accept_successor(predecessor, proof, successor) == receipt
    assert restarted.close_predecessor(predecessor) == proof
    with pytest.raises(PermissionError):
        restarted.accept_predecessor(predecessor)


def test_fixture_successor_requires_exact_closure_in_the_store(tmp_path, registered_predecessor):
    gateway, predecessor, path, scope, signer = registered_predecessor
    clone_path = tmp_path / "clone.sqlite"
    with closing(sqlite3.connect(path)) as source, closing(sqlite3.connect(clone_path)) as target:
        source.backup(target)
    clone = FixturePublicationSendBarrier(clone_path, scope=scope, signer=signer)
    successor = _successor_of(predecessor)
    proof = gateway.close_predecessor(predecessor)
    assert verify_fixture_publication_no_effect(
        proof, scope=scope, expected_send=predecessor, public_key=signer.public_key(),
    ) == proof

    with pytest.raises(PermissionError):
        clone.accept_successor(predecessor, proof, successor)
    other_proof = clone.close_predecessor(predecessor)
    assert other_proof.closure_id != proof.closure_id
    with pytest.raises(PermissionError):
        clone.accept_successor(predecessor, proof, successor)
    with pytest.raises(PermissionError):
        gateway.accept_successor(predecessor, other_proof, successor)


def test_fixture_successor_rejects_an_accepted_predecessor(tmp_path, registered_predecessor):
    gateway, predecessor, _, scope, signer = registered_predecessor
    receipt = gateway.accept_predecessor(predecessor)
    other = FixturePublicationSendBarrier.create(tmp_path / "other.sqlite", scope=scope, signer=signer)
    other.register_predecessor(predecessor)
    proof = other.close_predecessor(predecessor)
    with pytest.raises(PermissionError):
        gateway.accept_successor(predecessor, proof, _successor_of(predecessor))
    assert gateway.accept_predecessor(predecessor) == receipt


def test_fixture_successor_rejects_scope_proof_and_identity_mismatches(registered_predecessor):
    gateway, predecessor, _, scope, _ = registered_predecessor
    successor = _successor_of(predecessor)
    proof = gateway.close_predecessor(predecessor)
    for wrong in (
        replace(proof, scope=replace(scope, namespace="wrong")),
        replace(proof, signature=bytes(64)),
        replace(proof, send=replace(predecessor, request_sha256="b" * 64)),
    ):
        with pytest.raises(PermissionError):
            gateway.accept_successor(predecessor, wrong, successor)

    for wrong in (
        replace(successor, operation_id=uuid4()),
        replace(successor, request_sha256="b" * 64),
        replace(successor, provider_id="other-mock"),
        replace(successor, account_id="other-private"),
        replace(successor, original_key="other-original-key"),
        replace(successor, permit_id=predecessor.permit_id),
        replace(successor, claim_id=predecessor.claim_id),
    ):
        with pytest.raises(PermissionError):
            gateway.accept_successor(predecessor, proof, wrong)
    assert isinstance(gateway.accept_successor(predecessor, proof, successor), UUID)


def test_fixture_generations_have_separate_entry_points(registered_predecessor):
    gateway, predecessor, _, _, _ = registered_predecessor
    successor = _successor_of(predecessor)
    for wrong_generation in (True, 1, 3):
        with pytest.raises(ValueError):
            replace(successor, generation=wrong_generation)

    with pytest.raises(PermissionError):
        gateway.register_predecessor(replace(successor, operation_id=uuid4(), original_key="new-key"))
    with pytest.raises(PermissionError):
        gateway.accept_predecessor(successor)
    with pytest.raises(PermissionError):
        gateway.close_predecessor(successor)
    proof = gateway.close_predecessor(predecessor)
    with pytest.raises(PermissionError):
        gateway.accept_successor(predecessor, proof, predecessor)


@pytest.mark.parametrize("exact_retry", [True, False])
def test_fixture_successor_acceptance_serializes_on_original_operation(registered_predecessor, exact_retry):
    gateway, predecessor, path, scope, signer = registered_predecessor
    other = FixturePublicationSendBarrier(path, scope=scope, signer=signer)
    proof = gateway.close_predecessor(predecessor)
    successor = _successor_of(predecessor)
    proposed = successor if exact_retry else replace(successor, claim_id=uuid4())
    start = Barrier(3)

    def accept(store, send):
        start.wait(timeout=5)
        try:
            return store.accept_successor(predecessor, proof, send)
        except PermissionError:
            return None

    def delayed_predecessor():
        start.wait(timeout=5)
        gateway.accept_predecessor(predecessor)

    with ThreadPoolExecutor(max_workers=3) as executor:
        first = executor.submit(accept, gateway, successor)
        second = executor.submit(accept, other, proposed)
        delayed = executor.submit(delayed_predecessor)
        accepted, competing = first.result(timeout=10), second.result(timeout=10)
        with pytest.raises(PermissionError, match="permanently closed"):
            delayed.result(timeout=10)

    if exact_retry:
        assert isinstance(accepted, UUID) and accepted == competing
    else:
        assert (accepted is None) != (competing is None)
        winner, loser = (successor, proposed) if accepted is not None else (proposed, successor)
        assert other.accept_successor(predecessor, proof, winner) == (accepted or competing)
        with pytest.raises(PermissionError):
            other.accept_successor(predecessor, proof, loser)
