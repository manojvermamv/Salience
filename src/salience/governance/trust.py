"""Minimum-authority trust context for untrusted external material."""

from __future__ import annotations

from dataclasses import dataclass

from salience.memory.contracts import MemoryRecordInput


@dataclass(frozen=True)
class TrustContext:
    """Authority fixed by the caller, never by content or model output."""

    trust_level: str
    source_identity: str | None
    effect_classification: str
    delegated_authority: frozenset[str]
    tool_scope: frozenset[str]
    network_scope: frozenset[str]
    memory_write_authority: frozenset[str]

    @classmethod
    def untrusted_source(cls, source_identity: str) -> "TrustContext":
        return cls(
            trust_level="untrusted_external",
            source_identity=source_identity,
            effect_classification="read",
            delegated_authority=frozenset(),
            tool_scope=frozenset(),
            network_scope=frozenset({source_identity}),
            memory_write_authority=frozenset({"evidence"}),
        )

    @classmethod
    def internal_memory_writer(
        cls, source_identity: str, *, scopes: frozenset[str]
    ) -> "TrustContext":
        return cls(
            trust_level="trusted_internal",
            source_identity=source_identity,
            effect_classification="read",
            delegated_authority=frozenset(),
            tool_scope=frozenset(),
            network_scope=frozenset(),
            memory_write_authority=scopes,
        )


class TrustPolicy:
    """Fail closed before canonical memory receives externally controlled content."""

    def authorize_memory_write(
        self, context: TrustContext, record: MemoryRecordInput
    ) -> None:
        if record.scope not in context.memory_write_authority:
            raise PermissionError(
                f"memory_write_authority does not permit {record.scope} memory"
            )
        if context.trust_level != "untrusted_external":
            return
        if record.trust_level != "untrusted_external":
            raise PermissionError(
                "memory_write_authority cannot promote untrusted external content"
            )
        if record.verification_status != "unverified":
            raise PermissionError(
                "memory_write_authority cannot verify untrusted external content"
            )
        if record.source_uri != context.source_identity:
            raise PermissionError(
                "memory_write_authority requires the untrusted source identity"
            )
