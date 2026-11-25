from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from salience.fixtures.publication_send_barrier import (
    FixturePublicationBarrierScope,
    FixturePublicationSend,
    FixturePublicationSendBarrier,
    verify_fixture_publication_no_effect,
)


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
