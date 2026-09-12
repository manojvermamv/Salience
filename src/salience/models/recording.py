"""Hash-only model invocation lineage at the provider-neutral gateway boundary."""

from __future__ import annotations

import hashlib
import json
import asyncio
from dataclasses import dataclass, replace
from time import perf_counter
from typing import Protocol
from uuid import UUID, uuid4

from jsonschema import ValidationError, validate
import psycopg

from salience.models.contracts import ModelGateway, ModelRequest, ModelResult
from salience.models.gateway import ModelOutputInvalidError


@dataclass(frozen=True)
class ModelInvocation:
    id: UUID
    status: str
    capability: str
    runtime_id: str | None
    provider: str | None
    model: str | None
    provider_version: str | None
    input_hash: str
    output_hash: str | None
    input_artifact_reference: str | None
    output_artifact_reference: str | None
    parent_agent_run_id: UUID | None
    job_id: UUID | None
    trace_id: str | None
    span_id: str | None
    usage: dict[str, int]
    latency_ms: int
    actual_cost_micros: int
    error_type: str | None = None


class ModelInvocationRepository(Protocol):
    async def record(self, invocation: ModelInvocation) -> None: ...

    async def latest(self) -> ModelInvocation: ...


class InMemoryModelInvocationRepository:
    """Deterministic test implementation for unit contracts."""

    def __init__(self) -> None:
        self._invocations: list[ModelInvocation] = []

    async def record(self, invocation: ModelInvocation) -> None:
        self._invocations.append(invocation)

    async def latest(self) -> ModelInvocation:
        if not self._invocations:
            raise LookupError("no model invocations recorded")
        return self._invocations[-1]


class PostgreSQLModelInvocationRepository:
    """Canonical model-invocation persistence backed by migration 0004."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    async def record(self, invocation: ModelInvocation) -> None:
        await asyncio.to_thread(self._record, invocation)

    async def latest(self) -> ModelInvocation:
        return await asyncio.to_thread(self._latest)

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(
            self._database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        )

    def _record(self, invocation: ModelInvocation) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO model_invocations (
                    id, parent_agent_run_id, job_id, capability, status, runtime_id, provider,
                    model, provider_version, input_hash, output_hash, input_artifact_reference,
                    output_artifact_reference, usage, latency_ms, actual_cost_micros, error_type,
                    trace_id, span_id
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s,
                    %s, %s, %s
                ) ON CONFLICT (id) DO NOTHING
                """,
                (
                    invocation.id,
                    invocation.parent_agent_run_id,
                    invocation.job_id,
                    invocation.capability,
                    invocation.status,
                    invocation.runtime_id,
                    invocation.provider,
                    invocation.model,
                    invocation.provider_version,
                    invocation.input_hash,
                    invocation.output_hash,
                    invocation.input_artifact_reference,
                    invocation.output_artifact_reference,
                    json.dumps(invocation.usage),
                    invocation.latency_ms,
                    invocation.actual_cost_micros,
                    invocation.error_type,
                    invocation.trace_id,
                    invocation.span_id,
                ),
            )

    def _latest(self) -> ModelInvocation:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, status, capability, runtime_id, provider, model, provider_version,
                    input_hash, output_hash, input_artifact_reference, output_artifact_reference,
                    parent_agent_run_id, job_id, trace_id, span_id, usage, latency_ms,
                    actual_cost_micros, error_type
                FROM model_invocations ORDER BY created_at DESC LIMIT 1
                """
            )
            row = cursor.fetchone()
        if row is None:
            raise LookupError("no model invocations recorded")
        return ModelInvocation(
            id=row[0],
            status=row[1],
            capability=row[2],
            runtime_id=row[3],
            provider=row[4],
            model=row[5],
            provider_version=row[6],
            input_hash=row[7],
            output_hash=row[8],
            input_artifact_reference=row[9],
            output_artifact_reference=row[10],
            parent_agent_run_id=row[11],
            job_id=row[12],
            trace_id=row[13],
            span_id=row[14],
            usage=row[15],
            latency_ms=row[16],
            actual_cost_micros=row[17],
            error_type=row[18],
        )


class RecordedModelGateway:
    """Validate and record every model boundary without persisting prompts or secrets."""

    def __init__(
        self, *, gateway: ModelGateway, repository: ModelInvocationRepository
    ) -> None:
        self._gateway = gateway
        self._repository = repository

    async def complete(self, request: ModelRequest) -> ModelResult:
        input_hash = _hash(
            {
                "prompt": request.prompt,
                "output_schema": request.output_schema,
                "capability": request.capability,
                "metadata": request.metadata,
                "input_artifact_reference": request.input_artifact_reference,
            }
        )
        started = perf_counter()
        try:
            result = await self._gateway.complete(request)
            try:
                validate(result.output, request.output_schema)
            except ValidationError as error:
                raise ModelOutputInvalidError(str(error)) from error
        except ModelOutputInvalidError as error:
            await self._record_failure(
                request=request,
                input_hash=input_hash,
                started=started,
                status="invalid_output",
                error=error,
            )
            raise
        except Exception as error:
            await self._record_failure(
                request=request,
                input_hash=input_hash,
                started=started,
                status="failed",
                error=error,
            )
            raise

        completed = replace(
            result,
            input_hash=input_hash,
            output_hash=_hash(result.output),
        )
        await self._repository.record(
            _invocation(
                request=request,
                result=completed,
                status="succeeded",
                input_hash=input_hash,
                output_hash=completed.output_hash,
            )
        )
        return completed

    async def _record_failure(
        self,
        *,
        request: ModelRequest,
        input_hash: str,
        started: float,
        status: str,
        error: Exception,
    ) -> None:
        await self._repository.record(
            ModelInvocation(
                id=uuid4(),
                status=status,
                capability=request.capability,
                runtime_id=None,
                provider=None,
                model=None,
                provider_version=None,
                input_hash=input_hash,
                output_hash=None,
                input_artifact_reference=request.input_artifact_reference,
                output_artifact_reference=None,
                parent_agent_run_id=request.parent_agent_run_id,
                job_id=request.job_id,
                trace_id=request.trace_context.trace_id if request.trace_context else None,
                span_id=request.trace_context.span_id if request.trace_context else None,
                usage={},
                latency_ms=round((perf_counter() - started) * 1000),
                actual_cost_micros=0,
                error_type=type(error).__name__,
            )
        )


def _invocation(
    *,
    request: ModelRequest,
    result: ModelResult,
    status: str,
    input_hash: str,
    output_hash: str | None,
) -> ModelInvocation:
    return ModelInvocation(
        id=uuid4(),
        status=status,
        capability=request.capability,
        runtime_id=result.runtime_id,
        provider=result.provider,
        model=result.model,
        provider_version=result.provider_version,
        input_hash=input_hash,
        output_hash=output_hash,
        input_artifact_reference=request.input_artifact_reference,
        output_artifact_reference=result.output_artifact_reference,
        parent_agent_run_id=request.parent_agent_run_id,
        job_id=request.job_id,
        trace_id=request.trace_context.trace_id if request.trace_context else None,
        span_id=request.trace_context.span_id if request.trace_context else None,
        usage=dict(result.usage),
        latency_ms=result.latency_ms,
        actual_cost_micros=result.actual_cost_micros,
    )


def _hash(value: object) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()
