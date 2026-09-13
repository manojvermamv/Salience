"""Idempotent canonical writes for the Phase 5-6 intelligence loop."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

import psycopg

from salience.intelligence.contracts import (
    ClaimInput,
    ContentBriefInput,
    FetchInput,
    OpportunityInput,
    PackageEvaluationInput,
    PackageInput,
    SignalInput,
    SourceInput,
)
from salience.agents.contracts import AgentManifest


class IntelligenceRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    async def record_source(
        self, *, workspace_id: str, program_id: str, source: SourceInput, trace_id: str
    ) -> str:
        return await self._thread(
            """
            INSERT INTO research_sources (
                workspace_id, content_program_id, source_key, version, connector, trust_level,
                configuration, network_scope, rate_limit, protocol_metadata
            ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb)
            ON CONFLICT (content_program_id, source_key, version)
            DO UPDATE SET updated_at = CURRENT_TIMESTAMP
            RETURNING id::text
            """,
            (
                workspace_id,
                program_id,
                source.source_key,
                source.version,
                source.connector,
                source.trust_level,
                _json(source.configuration),
                _json(source.network_scope),
                _json(source.rate_limit),
                _json({**source.protocol_metadata, "trace_id": trace_id}),
            ),
        )

    async def record_fetch(
        self, *, workspace_id: str, program_id: str, fetch: FetchInput, trace_id: str
    ) -> str:
        return await self._thread(
            """
            INSERT INTO research_fetches (
                workspace_id, content_program_id, research_source_id, resource_identity,
                window_key, request_fingerprint, canonical_url, raw_content_hash, cursor_value,
                status, provenance, trace_id
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s
            )
            ON CONFLICT (research_source_id, resource_identity, window_key)
            DO UPDATE SET updated_at = CURRENT_TIMESTAMP
            RETURNING id::text
            """,
            (
                workspace_id,
                program_id,
                fetch.source_id,
                fetch.resource_identity,
                fetch.window_key,
                fetch.request_fingerprint,
                fetch.canonical_url,
                fetch.raw_hash,
                fetch.cursor,
                fetch.status,
                _json(fetch.provenance),
                trace_id,
            ),
        )

    async def record_evidence(
        self,
        *,
        workspace_id: str,
        program_id: str,
        source_id: str,
        fetch_id: str,
        source_uri: str,
        content: dict[str, Any],
        content_hash: str,
        idempotency_key: str,
        trace_id: str,
    ) -> str:
        return await self._thread(
            """
            INSERT INTO research_evidence (
                workspace_id, content_program_id, source_uri, fetched_at, content, trust_level,
                verification_status, provenance, research_source_id, research_fetch_id,
                source_trust, content_hash, idempotency_key, source_identity
            ) VALUES (
                %s, %s, %s, CURRENT_TIMESTAMP, %s::jsonb, 'untrusted_external',
                'unverified', %s::jsonb, %s, %s, 'untrusted_external', %s, %s, %s
            )
            ON CONFLICT (content_program_id, idempotency_key)
            DO UPDATE SET provenance = research_evidence.provenance
            RETURNING id::text
            """,
            (
                workspace_id,
                program_id,
                source_uri,
                _json(content),
                _json({"trace_id": trace_id, "source_uri": source_uri}),
                source_id,
                fetch_id,
                content_hash,
                idempotency_key,
                source_uri,
            ),
        )

    async def record_signal(
        self, *, workspace_id: str, program_id: str, signal: SignalInput, trace_id: str
    ) -> str:
        return await self._thread(
            """
            INSERT INTO signals (
                workspace_id, content_program_id, fingerprint, topic, features,
                feature_availability, provenance, trace_id
            ) VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s)
            ON CONFLICT (content_program_id, fingerprint)
            DO UPDATE SET updated_at = CURRENT_TIMESTAMP
            RETURNING id::text
            """,
            (
                workspace_id,
                program_id,
                signal.fingerprint,
                signal.topic,
                _json(signal.features),
                _json(signal.availability),
                _json(signal.provenance),
                trace_id,
            ),
        )

    async def link_signal_support(
        self, *, signal_id: str, evidence_id: str, fetch_id: str, source_id: str, trace_id: str
    ) -> str:
        return await self._thread(
            """
            INSERT INTO signal_support (
                signal_id, research_evidence_id, research_fetch_id, research_source_id, provenance, trace_id
            ) VALUES (%s, %s, %s, %s, %s::jsonb, %s)
            ON CONFLICT (signal_id, research_evidence_id, research_fetch_id)
            DO UPDATE SET updated_at = CURRENT_TIMESTAMP
            RETURNING id::text
            """,
            (signal_id, evidence_id, fetch_id, source_id, _json({}), trace_id),
        )

    async def record_opportunity(
        self,
        *,
        workspace_id: str,
        program_id: str,
        opportunity: OpportunityInput,
        trace_id: str,
    ) -> str:
        return await self._thread(
            """
            INSERT INTO topic_opportunities (
                workspace_id, content_program_id, fingerprint, topic, status, score, base_score,
                semantic_adjustment, supporting_signal_ids, feature_availability, explanation, risks,
                provenance, trace_id
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s::jsonb, %s::jsonb, %s
            )
            ON CONFLICT (content_program_id, fingerprint)
            DO UPDATE SET updated_at = CURRENT_TIMESTAMP
            RETURNING id::text
            """,
            (
                workspace_id,
                program_id,
                opportunity.fingerprint,
                opportunity.topic,
                opportunity.status,
                opportunity.score,
                opportunity.base_score if opportunity.base_score is not None else opportunity.score,
                opportunity.semantic_adjustment,
                _json(opportunity.signal_ids),
                _json(opportunity.availability),
                opportunity.explanation,
                _json(opportunity.risks),
                _json(opportunity.provenance),
                trace_id,
            ),
        )

    async def record_package(
        self,
        *,
        workspace_id: str,
        program_id: str,
        opportunity_id: str,
        package: PackageInput,
        trace_id: str,
    ) -> str:
        return await self._thread(
            """
            INSERT INTO strategic_packages (
                workspace_id, content_program_id, topic_opportunity_id, diversity_fingerprint,
                status, package, provenance, trace_id
            ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
            ON CONFLICT (topic_opportunity_id, diversity_fingerprint)
            DO UPDATE SET updated_at = CURRENT_TIMESTAMP
            RETURNING id::text
            """,
            (
                workspace_id,
                program_id,
                opportunity_id,
                package.diversity_fingerprint,
                package.status,
                _json(package.content),
                _json(package.provenance),
                trace_id,
            ),
        )

    async def record_evaluation(
        self, *, package_id: str, evaluation: PackageEvaluationInput, trace_id: str
    ) -> str:
        return await self._thread(
            """
            INSERT INTO package_evaluations (
                strategic_package_id, evaluation_key, deterministic_score, semantic_score,
                status, reason, provenance, trace_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)
            ON CONFLICT (strategic_package_id, evaluation_key)
            DO UPDATE SET updated_at = CURRENT_TIMESTAMP
            RETURNING id::text
            """,
            (
                package_id,
                evaluation.evaluation_key,
                evaluation.score,
                evaluation.semantic_score,
                evaluation.status,
                evaluation.reason,
                _json(evaluation.provenance),
                trace_id,
            ),
        )

    async def select_package(
        self, *, package_id: str, reason: str, trace_id: str
    ) -> None:
        await self._thread_no_result(
            """
            UPDATE strategic_packages
            SET status = 'selected',
                provenance = provenance || %s::jsonb,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (_json({"selection_reason": reason, "selection_trace_id": trace_id}), package_id),
        )

    async def record_claim(
        self, *, workspace_id: str, program_id: str, claim: ClaimInput, trace_id: str
    ) -> str:
        return await self._thread(
            """
            INSERT INTO claims (
                workspace_id, content_program_id, fingerprint, claim_text, verification_status,
                confidence, provenance, trace_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)
            ON CONFLICT (content_program_id, fingerprint)
            DO UPDATE SET updated_at = CURRENT_TIMESTAMP
            RETURNING id::text
            """,
            (
                workspace_id,
                program_id,
                claim.fingerprint,
                claim.text,
                claim.verification_status,
                claim.confidence,
                _json(claim.provenance),
                trace_id,
            ),
        )

    async def link_claim_evidence(
        self, *, claim_id: str, evidence_id: str, relation: str, trace_id: str
    ) -> str:
        return await self._thread(
            """
            INSERT INTO claim_evidence (claim_id, research_evidence_id, relation, provenance, trace_id)
            VALUES (%s, %s, %s, %s::jsonb, %s)
            ON CONFLICT (claim_id, research_evidence_id, relation)
            DO UPDATE SET updated_at = CURRENT_TIMESTAMP
            RETURNING id::text
            """,
            (claim_id, evidence_id, relation, _json({}), trace_id),
        )

    async def record_content_brief(
        self, *, workspace_id: str, program_id: str, brief: ContentBriefInput, trace_id: str
    ) -> str:
        return await self._thread(
            """
            INSERT INTO content_brief_versions (
                workspace_id, content_program_id, brief_key, version, topic_opportunity_id,
                strategic_package_id, strategy_version_id, content, claim_ids, provenance, trace_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s)
            ON CONFLICT (content_program_id, brief_key, version)
            DO UPDATE SET updated_at = CURRENT_TIMESTAMP
            RETURNING id::text
            """,
            (
                workspace_id,
                program_id,
                brief.brief_key,
                brief.version,
                brief.opportunity_id,
                brief.package_id,
                brief.strategy_version_id,
                _json(brief.content),
                _json(brief.claim_ids),
                _json(brief.provenance),
                trace_id,
            ),
        )

    async def record_queue_entry(
        self,
        *,
        workspace_id: str,
        program_id: str,
        opportunity_id: str,
        strategy_version_id: str,
        priority: int,
        trace_id: str,
    ) -> str:
        return await self._thread(
            """
            INSERT INTO content_queue_entries (
                workspace_id, content_program_id, topic_opportunity_id, strategy_version_id,
                priority, status, provenance
            ) VALUES (%s, %s, %s, %s, %s, 'queued', %s::jsonb)
            ON CONFLICT (content_program_id, topic_opportunity_id)
            DO UPDATE SET
                priority = EXCLUDED.priority,
                strategy_version_id = EXCLUDED.strategy_version_id,
                updated_at = CURRENT_TIMESTAMP
            RETURNING id::text
            """,
            (
                workspace_id,
                program_id,
                opportunity_id,
                strategy_version_id,
                priority,
                _json({"trace_id": trace_id}),
            ),
        )

    async def record_strategy(
        self,
        *,
        workspace_id: str,
        program_id: str,
        strategy: dict[str, Any],
        evidence_ids: list[str],
        idempotency_key: str,
        trace_id: str,
    ) -> str:
        return await self._thread(
            """
            WITH next_version AS (
                SELECT COALESCE(MAX(version), 0) + 1 AS version
                FROM strategy_versions WHERE content_program_id = %s
            )
            INSERT INTO strategy_versions (
                workspace_id, content_program_id, version, status, strategy, assumptions,
                evidence_ids, provenance, idempotency_key
            ) SELECT %s, %s, next_version.version, 'provisional', %s::jsonb, %s::jsonb,
                     %s::jsonb, %s::jsonb, %s
            FROM next_version
            ON CONFLICT (content_program_id, idempotency_key)
            DO UPDATE SET provenance = strategy_versions.provenance
            RETURNING id::text
            """,
            (
                program_id,
                workspace_id,
                program_id,
                _json(strategy),
                _json(strategy.get("assumptions", [])),
                _json(evidence_ids),
                _json({"trace_id": trace_id, "idempotency_key": idempotency_key}),
                idempotency_key,
            ),
        )

    async def record_agent_run(
        self,
        *,
        run_id: str,
        workspace_id: str,
        program_id: str,
        job_id: str,
        manifest: AgentManifest,
        input_payload: dict[str, Any],
        output_payload: dict[str, Any],
        trace_id: str,
        span_id: str,
        parent_run_id: str | None = None,
    ) -> str:
        return await asyncio.to_thread(
            self._record_agent_run,
            run_id,
            workspace_id,
            program_id,
            job_id,
            manifest,
            input_payload,
            output_payload,
            trace_id,
            span_id,
            parent_run_id,
        )

    async def record_delegation(
        self, *, parent_run_id: str, child_run_id: str, trace_id: str
    ) -> None:
        await self._thread_no_result(
            """
            INSERT INTO agent_delegations (
                parent_agent_run_id, child_agent_run_id, delegated_scopes, delegated_authority
            ) VALUES (%s, %s, '[]'::jsonb, %s::jsonb)
            ON CONFLICT (parent_agent_run_id, child_agent_run_id) DO NOTHING
            """,
            (parent_run_id, child_run_id, _json({"trace_id": trace_id})),
        )

    async def count_fetches_for_job(self, job_id: str) -> int:
        return await asyncio.to_thread(self._count_fetches_for_job, job_id)

    async def opportunity_details(
        self, *, opportunity_id: str, program_id: str
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._opportunity_details, opportunity_id, program_id
        )

    async def brief_details(self, brief_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._brief_details, brief_id)

    async def exact_content_brief(
        self, *, brief_id: str, workspace_id: str, program_id: str
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._exact_content_brief, brief_id, workspace_id, program_id
        )

    async def lineage_for_brief(self, brief_id: str) -> dict[str, list[str] | str]:
        return await asyncio.to_thread(self._lineage_for_brief, brief_id)

    async def _thread(self, statement: str, values: tuple[Any, ...]) -> str:
        return await asyncio.to_thread(self._returning_id, statement, values)

    async def _thread_no_result(self, statement: str, values: tuple[Any, ...]) -> None:
        await asyncio.to_thread(self._execute, statement, values)

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(
            self._database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        )

    def _returning_id(self, statement: str, values: tuple[Any, ...]) -> str:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(statement, values)
            return cursor.fetchone()[0]

    def _execute(self, statement: str, values: tuple[Any, ...]) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(statement, values)

    def _record_agent_run(
        self,
        run_id: str,
        workspace_id: str,
        program_id: str,
        job_id: str,
        manifest: AgentManifest,
        input_payload: dict[str, Any],
        output_payload: dict[str, Any],
        trace_id: str,
        span_id: str,
        parent_run_id: str | None,
    ) -> str:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO agent_versions (
                    agent_id, version, input_schema, output_schema, tool_scopes, memory_scopes,
                    effect_classification, supports_sync, supports_async, timeout_seconds,
                    protocol_compatibility, trust_classification, delegated_authority_scopes
                ) VALUES (
                    %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb,
                    %s, %s, %s, %s, %s::jsonb, %s, %s::jsonb
                )
                ON CONFLICT (agent_id, version)
                DO UPDATE SET status = 'enabled'
                RETURNING id
                """,
                (
                    manifest.agent_id,
                    manifest.version,
                    _json(manifest.input_schema),
                    _json(manifest.output_schema),
                    _json(manifest.tool_scopes),
                    _json(manifest.memory_scopes),
                    manifest.effect_classification,
                    manifest.supports_sync,
                    manifest.supports_async,
                    manifest.timeout_seconds,
                    _json(manifest.protocol_compatibility),
                    manifest.trust_classification,
                    _json(manifest.delegated_authority_scopes),
                ),
            )
            agent_version_id = cursor.fetchone()[0]
            cursor.execute(
                """
                INSERT INTO agent_runs (
                    id, workspace_id, content_program_id, agent_version_id, job_id,
                    parent_agent_run_id, state, input_payload, output_payload, runtime_mapping,
                    trace_id, span_id, started_at, finished_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, 'succeeded', %s::jsonb, %s::jsonb, %s::jsonb,
                    %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT (id) DO UPDATE SET
                    output_payload = EXCLUDED.output_payload,
                    finished_at = CURRENT_TIMESTAMP
                RETURNING id::text
                """,
                (
                    UUID(run_id),
                    UUID(workspace_id),
                    UUID(program_id),
                    agent_version_id,
                    UUID(job_id),
                    UUID(parent_run_id) if parent_run_id else None,
                    _json(input_payload),
                    _json(output_payload),
                    _json({"runtime": "native"}),
                    trace_id,
                    span_id,
                ),
            )
            return cursor.fetchone()[0]

    def _count_fetches_for_job(self, job_id: str) -> int:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM research_fetches research_fetch
                JOIN jobs job ON job.content_program_id = research_fetch.content_program_id
                WHERE job.id = %s
                """,
                (job_id,),
            )
            return cursor.fetchone()[0]

    def _opportunity_details(
        self, opportunity_id: str, program_id: str
    ) -> dict[str, Any]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT opportunity.id::text, opportunity.topic, opportunity.score,
                       opportunity.fingerprint, opportunity.supporting_signal_ids,
                       program.workspace_id::text, program.niche
                FROM topic_opportunities opportunity
                JOIN content_programs program ON program.id = opportunity.content_program_id
                WHERE opportunity.id = %s AND opportunity.content_program_id = %s
                """,
                (opportunity_id, program_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise KeyError(opportunity_id)
            keys = (
                "id",
                "topic",
                "score",
                "fingerprint",
                "signal_ids",
                "workspace_id",
                "niche",
            )
            return dict(zip(keys, row, strict=True))

    def _brief_details(self, brief_id: str) -> dict[str, Any] | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id::text, content_program_id::text, topic_opportunity_id::text,
                       strategic_package_id::text, content, claim_ids
                FROM content_brief_versions WHERE id = %s
                """,
                (brief_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            keys = (
                "brief_id",
                "content_program_id",
                "opportunity_id",
                "package_id",
                "content",
                "claim_ids",
            )
            return dict(zip(keys, row, strict=True))

    def _exact_content_brief(
        self, brief_id: str, workspace_id: str, program_id: str
    ) -> dict[str, Any]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id::text, workspace_id::text, content_program_id::text, brief_key,
                       version, content, claim_ids, provenance, trace_id, span_id
                FROM content_brief_versions
                WHERE id = %s AND workspace_id = %s AND content_program_id = %s
                """,
                (brief_id, workspace_id, program_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise KeyError("content brief is outside the requested workspace/program")
            keys = (
                "brief_id",
                "workspace_id",
                "content_program_id",
                "brief_key",
                "version",
                "content",
                "claim_ids",
                "provenance",
                "trace_id",
                "span_id",
            )
            return dict(zip(keys, row, strict=True))

    def _lineage_for_brief(self, brief_id: str) -> dict[str, list[str] | str]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT topic_opportunity_id::text, strategic_package_id::text, claim_ids
                FROM content_brief_versions WHERE id = %s
                """,
                (brief_id,),
            )
            opportunity_id, package_id, claim_ids = cursor.fetchone()
            cursor.execute(
                "SELECT supporting_signal_ids FROM topic_opportunities WHERE id = %s",
                (opportunity_id,),
            )
            signal_ids = cursor.fetchone()[0]
            cursor.execute(
                """
                SELECT DISTINCT research_source_id::text, research_fetch_id::text
                FROM signal_support WHERE signal_id = ANY(%s::uuid[])
                """,
                (signal_ids,),
            )
            source_ids: list[str] = []
            fetch_ids: list[str] = []
            for source_id, fetch_id in cursor.fetchall():
                if source_id not in source_ids:
                    source_ids.append(source_id)
                if fetch_id not in fetch_ids:
                    fetch_ids.append(fetch_id)
        return {
            "source_ids": source_ids,
            "fetch_ids": fetch_ids,
            "signal_ids": signal_ids,
            "opportunity_id": opportunity_id,
            "package_id": package_id,
            "claim_ids": claim_ids,
        }


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, default=str)
