"""SQLite acceptance boundary for a mock sink with no external effects."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


_PROOF_DOMAIN = b"salience.fixture-publication-permanent-close.v1\x00"


def _canonical(value) -> bytes:
    return json.dumps(
        asdict(value), default=str, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def _require_identity(value: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 512 or not value.isprintable():
        raise ValueError("a bounded fixture identity is required")


def _require_send_identity(send: FixturePublicationSend | FixturePublicationSuccessorSend) -> None:
    if not all(isinstance(value, UUID) for value in (send.operation_id, send.permit_id, send.claim_id)):
        raise ValueError("fixture operation, permit and claim UUIDs are required")
    if not isinstance(send.request_sha256, str) or re.fullmatch("[a-f0-9]{64}", send.request_sha256) is None:
        raise ValueError("the exact predecessor request fingerprint is required")
    for value in (send.provider_id, send.account_id, send.original_key):
        _require_identity(value)


@dataclass(frozen=True, slots=True)
class FixturePublicationBarrierScope:
    environment_id: UUID
    namespace: str
    barrier_id: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.environment_id, UUID) or not isinstance(self.barrier_id, UUID):
            raise ValueError("fixture environment and barrier UUIDs are required")
        _require_identity(self.namespace)


@dataclass(frozen=True, slots=True)
class FixturePublicationSend:
    operation_id: UUID
    request_sha256: str
    provider_id: str
    account_id: str
    original_key: str
    permit_id: UUID
    claim_id: UUID
    generation: int

    def __post_init__(self) -> None:
        _require_send_identity(self)
        if type(self.generation) is not int or self.generation != 1:
            raise ValueError("only the initial fixture generation can register a predecessor")


@dataclass(frozen=True, slots=True)
class FixturePublicationSuccessorSend:
    """Generation two of the same request, under a new permit and claim."""

    operation_id: UUID
    request_sha256: str
    provider_id: str
    account_id: str
    original_key: str
    permit_id: UUID
    claim_id: UUID
    generation: int

    def __post_init__(self) -> None:
        _require_send_identity(self)
        if type(self.generation) is not int or self.generation != 2:
            raise ValueError("only fixture generation two can send a successor")


@dataclass(frozen=True, slots=True)
class FixturePublicationNoEffectReceipt:
    scope: FixturePublicationBarrierScope
    send: FixturePublicationSend
    closure_id: UUID
    signature: bytes


def _proof_message(scope, send, closure_id) -> bytes:
    return _PROOF_DOMAIN + _canonical(scope) + b"\x00" + _canonical(send) + b"\x00" + str(closure_id).encode("ascii")


def verify_fixture_publication_no_effect(
    receipt: FixturePublicationNoEffectReceipt,
    *,
    scope: FixturePublicationBarrierScope,
    expected_send: FixturePublicationSend,
    public_key: Ed25519PublicKey,
) -> FixturePublicationNoEffectReceipt:
    if (
        not isinstance(receipt, FixturePublicationNoEffectReceipt)
        or not isinstance(scope, FixturePublicationBarrierScope)
        or not isinstance(expected_send, FixturePublicationSend)
        or not isinstance(public_key, Ed25519PublicKey)
        or receipt.scope != scope
        or receipt.send != expected_send
        or not isinstance(receipt.closure_id, UUID)
        or not isinstance(receipt.signature, bytes)
    ):
        raise PermissionError("fixture finality proof does not match its exact scope")
    try:
        public_key.verify(receipt.signature, _proof_message(scope, expected_send, receipt.closure_id))
    except InvalidSignature as error:
        raise PermissionError("fixture finality proof signature is invalid") from error
    return receipt


class FixturePublicationSendBarrier:
    """Serialize mock acceptance and permanent closure in the same store."""

    def __init__(self, path: Path, *, scope: FixturePublicationBarrierScope, signer: Ed25519PrivateKey):
        self._configure(path, scope, signer)
        with self._transaction():
            pass

    def _configure(self, path, scope, signer) -> None:
        if not isinstance(scope, FixturePublicationBarrierScope) or not isinstance(signer, Ed25519PrivateKey):
            raise ValueError("exact fixture scope and configured signer are required")
        self._path = Path(path).absolute()
        self._scope = scope
        self._signer = signer
        self._identity = (_canonical(scope), signer.public_key().public_bytes_raw())

    @classmethod
    def create(cls, path: Path, *, scope: FixturePublicationBarrierScope, signer: Ed25519PrivateKey):
        barrier = cls.__new__(cls)
        barrier._configure(path, scope, signer)
        descriptor = os.open(barrier._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        with closing(barrier._connect()) as database, database:
            database.executescript("""
                CREATE TABLE fixture_identity (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    scope BLOB NOT NULL, signer BLOB NOT NULL
                );
                CREATE TABLE predecessor (
                    operation_id TEXT PRIMARY KEY, identity BLOB NOT NULL,
                    provider_id TEXT NOT NULL, account_id TEXT NOT NULL, original_key TEXT NOT NULL,
                    acceptance_id TEXT, closure_id TEXT,
                    UNIQUE (provider_id, account_id, original_key),
                    CHECK (acceptance_id IS NULL OR closure_id IS NULL)
                );
            """)
            database.execute("INSERT INTO fixture_identity VALUES (1, ?, ?)", barrier._identity)
        return barrier

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(f"{self._path.as_uri()}?mode=rw", uri=True, timeout=5)

    @contextmanager
    def _transaction(self):
        with closing(self._connect()) as database, database:
            database.execute("BEGIN IMMEDIATE")
            identity = database.execute(
                "SELECT scope, signer FROM fixture_identity WHERE singleton = 1",
            ).fetchone()
            if identity != self._identity:
                raise PermissionError("fixture barrier store or signer scope does not match")
            yield database

    @staticmethod
    def _read_predecessor(database, send: FixturePublicationSend):
        if type(send) is not FixturePublicationSend:
            raise PermissionError("the exact claimed predecessor is required")
        row = database.execute(
            "SELECT identity, acceptance_id, closure_id FROM predecessor WHERE operation_id = ?",
            (str(send.operation_id),),
        ).fetchone()
        if row is None or row[0] != _canonical(send):
            raise PermissionError("the exact claimed predecessor must be registered")
        return row[1], row[2]

    def register_predecessor(self, send: FixturePublicationSend) -> None:
        if type(send) is not FixturePublicationSend:
            raise PermissionError("the exact claimed predecessor is required")
        with self._transaction() as database:
            database.execute(
                """INSERT INTO predecessor(operation_id, identity, provider_id, account_id, original_key)
                   VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING""",
                (str(send.operation_id), _canonical(send), send.provider_id, send.account_id, send.original_key),
            )
            self._read_predecessor(database, send)

    def accept_predecessor(self, send: FixturePublicationSend) -> UUID:
        with self._transaction() as database:
            acceptance_id, closure_id = self._read_predecessor(database, send)
            if closure_id is not None:
                raise PermissionError("the predecessor is permanently closed")
            if acceptance_id is None:
                acceptance_id = str(uuid4())
                database.execute(
                    "UPDATE predecessor SET acceptance_id = ? WHERE operation_id = ?",
                    (acceptance_id, str(send.operation_id)),
                )
        return UUID(acceptance_id)

    def close_predecessor(self, send: FixturePublicationSend) -> FixturePublicationNoEffectReceipt | None:
        with self._transaction() as database:
            acceptance_id, closure_id = self._read_predecessor(database, send)
            if acceptance_id is not None:
                return None
            if closure_id is None:
                closure_id = str(uuid4())
                database.execute(
                    "UPDATE predecessor SET closure_id = ? WHERE operation_id = ?",
                    (closure_id, str(send.operation_id)),
                )
        closed = UUID(closure_id)
        signature = self._signer.sign(_proof_message(self._scope, send, closed))
        return FixturePublicationNoEffectReceipt(self._scope, send, closed, signature)

    def accept_successor(
        self,
        predecessor: FixturePublicationSend,
        proof: FixturePublicationNoEffectReceipt,
        successor: FixturePublicationSuccessorSend,
    ) -> UUID:
        """Record one mock acceptance against this store's permanent predecessor closure."""
        if type(predecessor) is not FixturePublicationSend or type(successor) is not FixturePublicationSuccessorSend:
            raise PermissionError("the exact predecessor and generation two send are required")
        if (
            successor.operation_id != predecessor.operation_id
            or successor.request_sha256 != predecessor.request_sha256
            or successor.provider_id != predecessor.provider_id
            or successor.account_id != predecessor.account_id
            or successor.original_key != predecessor.original_key
            or successor.permit_id == predecessor.permit_id
            or successor.claim_id == predecessor.claim_id
        ):
            raise PermissionError("the successor must match the original request with a new permit and claim")

        operation_id = str(predecessor.operation_id)
        identity = _canonical(successor)
        with self._transaction() as database:
            verify_fixture_publication_no_effect(
                proof, scope=self._scope, expected_send=predecessor, public_key=self._signer.public_key(),
            )
            predecessor_acceptance, closure_id = self._read_predecessor(database, predecessor)
            if predecessor_acceptance is not None or closure_id != str(proof.closure_id):
                raise PermissionError("the exact permanent closure must exist in this fixture store")

            # Existing fixture databases need only this additive table, created under the same lock.
            database.execute("""
                CREATE TABLE IF NOT EXISTS successor_acceptance (
                    operation_id TEXT PRIMARY KEY NOT NULL, identity BLOB NOT NULL, acceptance_id TEXT NOT NULL
                )
            """)
            row = database.execute(
                "SELECT identity, acceptance_id FROM successor_acceptance WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if row is not None:
                if row[0] != identity:
                    raise PermissionError("a different fixture successor already accepted this operation")
                return UUID(row[1])

            acceptance_id = uuid4()
            database.execute(
                "INSERT INTO successor_acceptance VALUES (?, ?, ?)", (operation_id, identity, str(acceptance_id)),
            )
        return acceptance_id
