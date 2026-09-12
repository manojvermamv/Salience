import asyncio
import json
from dataclasses import dataclass
from decimal import Decimal

import psycopg

from salience.memory.contracts import MemoryRecordInput
from salience.governance.trust import TrustContext, TrustPolicy


@dataclass(frozen=True)
class MemoryRecord:
    memory_id: str
    workspace_id: str
    program_id: str
    scope: str
    content: dict[str, object]
    trust_level: str
    confidence: Decimal
    source_uri: str | None
    verification_status: str | None
    writer_identity: str | None


class MemoryRepository:
    def __init__(self, database_url: str, *, trust_policy: TrustPolicy | None = None) -> None:
        self._database_url = database_url
        self._trust_policy = trust_policy or TrustPolicy()

    async def record(
        self,
        *,
        workspace_id: str,
        program_id: str,
        record: MemoryRecordInput,
        context: TrustContext,
    ) -> MemoryRecord:
        self._trust_policy.authorize_memory_write(context, record)
        return await asyncio.to_thread(
            self._record, workspace_id, program_id, record
        )

    async def record_external_research(
        self,
        *,
        workspace_id: str,
        program_id: str,
        record: MemoryRecordInput,
        context: TrustContext,
    ) -> MemoryRecord:
        if context.trust_level != "untrusted_external":
            raise PermissionError("external research requires an untrusted_external context")
        sanitized = record.model_copy(
            update={
                "trust_level": "untrusted_external",
                "verification_status": "unverified",
                "source_uri": context.source_identity,
                "writer_identity": "external-research",
                "data_classification": "external",
                "provenance": {
                    **record.provenance,
                    "source_identity": context.source_identity,
                    "trust_level": "untrusted_external",
                },
                "source_identity": context.source_identity,
                "effect_classification": context.effect_classification,
                "tool_scope": sorted(context.tool_scope),
                "network_scope": sorted(context.network_scope),
                "memory_write_authority": sorted(context.memory_write_authority),
                "writer_actor": {"kind": "external-research"},
            }
        )
        return await self.record(
            workspace_id=workspace_id,
            program_id=program_id,
            record=sanitized,
            context=context,
        )

    async def retrieve(self, *, program_id: str, scopes: set[str]) -> list[MemoryRecord]:
        return await asyncio.to_thread(self._retrieve, program_id, scopes)

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(
            self._database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        )

    def _record(
        self, workspace_id: str, program_id: str, record: MemoryRecordInput
    ) -> MemoryRecord:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO memory_records (
                    workspace_id, content_program_id, scope, content, trust_level,
                    confidence, evidence_ids, source_uri, verification_status, writer_identity,
                    data_classification, retention_policy, expires_at, provenance, trace_id, span_id,
                    verified_at, valid_until, source_identity, effect_classification, tool_scope,
                    network_scope, memory_write_authority, writer_actor
                ) VALUES (
                    %s, %s, %s, %s::jsonb, %s, %s, %s::jsonb, %s, %s, %s,
                    %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb,
                    %s::jsonb, %s::jsonb
                )
                RETURNING id::text, workspace_id::text, content_program_id::text, scope,
                    content, trust_level, confidence, source_uri, verification_status, writer_identity
                """,
                (
                    workspace_id,
                    program_id,
                    record.scope,
                    json.dumps(record.content),
                    record.trust_level,
                    record.confidence,
                    json.dumps(record.evidence_ids),
                    record.source_uri,
                    record.verification_status,
                    record.writer_identity,
                    record.data_classification,
                    record.retention_policy,
                    record.expires_at,
                    json.dumps(record.provenance),
                    record.trace_id,
                    record.span_id,
                    record.verified_at,
                    record.valid_until,
                    record.source_identity,
                    record.effect_classification,
                    json.dumps(record.tool_scope),
                    json.dumps(record.network_scope),
                    json.dumps(record.memory_write_authority),
                    json.dumps(record.writer_actor),
                ),
            )
            return MemoryRecord(*cursor.fetchone())

    def _retrieve(self, program_id: str, scopes: set[str]) -> list[MemoryRecord]:
        if not scopes:
            return []
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id::text, workspace_id::text, content_program_id::text, scope,
                    content, trust_level, confidence, source_uri, verification_status, writer_identity
                FROM memory_records
                WHERE content_program_id = %s
                    AND scope = ANY(%s)
                    AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
                ORDER BY created_at
                """,
                (program_id, list(scopes)),
            )
            return [MemoryRecord(*row) for row in cursor.fetchall()]
