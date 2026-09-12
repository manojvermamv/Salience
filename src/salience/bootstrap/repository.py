import asyncio
import json
from dataclasses import dataclass

import psycopg

from salience.bootstrap.contracts import ResearchEvidenceInput, StrategyVersionInput


@dataclass(frozen=True)
class StrategyVersion:
    strategy_id: str
    version: int
    strategy: dict[str, object]
    assumptions: list[str]
    evidence_ids: list[str]


class BootstrapRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    async def record_evidence(
        self, *, workspace_id: str, program_id: str, evidence: ResearchEvidenceInput
    ) -> str:
        return await asyncio.to_thread(
            self._record_evidence, workspace_id, program_id, evidence
        )

    async def record_strategy(
        self, *, workspace_id: str, program_id: str, strategy: StrategyVersionInput
    ) -> StrategyVersion:
        return await asyncio.to_thread(
            self._record_strategy, workspace_id, program_id, strategy
        )

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(
            self._database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        )

    def _record_evidence(
        self, workspace_id: str, program_id: str, evidence: ResearchEvidenceInput
    ) -> str:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO research_evidence (
                    workspace_id, content_program_id, source_uri, fetched_at, content,
                    trust_level, verification_status, provenance
                ) VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s::jsonb)
                RETURNING id::text
                """,
                (
                    workspace_id,
                    program_id,
                    evidence.source_uri,
                    evidence.fetched_at,
                    json.dumps(evidence.content),
                    evidence.trust_level,
                    evidence.verification_status,
                    json.dumps({"source_uri": evidence.source_uri}),
                ),
            )
            return cursor.fetchone()[0]

    def _record_strategy(
        self, workspace_id: str, program_id: str, strategy: StrategyVersionInput
    ) -> StrategyVersion:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM strategy_versions WHERE content_program_id = %s",
                (program_id,),
            )
            version = cursor.fetchone()[0]
            cursor.execute(
                """
                INSERT INTO strategy_versions (
                    workspace_id, content_program_id, version, strategy, assumptions,
                    evidence_ids, provenance
                ) VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb)
                RETURNING id::text
                """,
                (
                    workspace_id,
                    program_id,
                    version,
                    json.dumps(strategy.strategy),
                    json.dumps(strategy.assumptions),
                    json.dumps(strategy.evidence_ids),
                    json.dumps({"evidence_ids": strategy.evidence_ids}),
                ),
            )
            strategy_id = cursor.fetchone()[0]
        return StrategyVersion(
            strategy_id=strategy_id,
            version=version,
            strategy=strategy.strategy,
            assumptions=strategy.assumptions,
            evidence_ids=strategy.evidence_ids,
        )
