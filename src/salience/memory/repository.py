import asyncio
import json
from dataclasses import dataclass
from decimal import Decimal

import psycopg

from salience.memory.contracts import MemoryRecordInput


@dataclass(frozen=True)
class MemoryRecord:
    memory_id: str
    workspace_id: str
    program_id: str
    scope: str
    content: dict[str, object]
    trust_level: str
    confidence: Decimal


class MemoryRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    async def record(
        self, *, workspace_id: str, program_id: str, record: MemoryRecordInput
    ) -> MemoryRecord:
        return await asyncio.to_thread(
            self._record, workspace_id, program_id, record
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
                    confidence, evidence_ids, data_classification, retention_policy, expires_at
                ) VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s::jsonb, %s, %s, %s)
                RETURNING id::text, workspace_id::text, content_program_id::text, scope,
                    content, trust_level, confidence
                """,
                (
                    workspace_id,
                    program_id,
                    record.scope,
                    json.dumps(record.content),
                    record.trust_level,
                    record.confidence,
                    json.dumps(record.evidence_ids),
                    record.data_classification,
                    record.retention_policy,
                    record.expires_at,
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
                    content, trust_level, confidence
                FROM memory_records
                WHERE content_program_id = %s
                    AND scope = ANY(%s)
                    AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
                ORDER BY created_at
                """,
                (program_id, list(scopes)),
            )
            return [MemoryRecord(*row) for row in cursor.fetchall()]
