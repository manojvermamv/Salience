"""Isolated qualification API: authenticated local control, no effect dispatch."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID

from fastapi import Request
from fastapi.responses import JSONResponse
import jwt
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
import psycopg
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from salience.observability.tracing import OpenTelemetryTraceEmitter, TraceContext


@dataclass(frozen=True)
class Principal:
    subject_id: UUID
    workspace_id: UUID
    scopes: frozenset[str]


class IdentityBoundary:
    def __init__(self, *, database_url, workspace_id, issuer, audience, public_key):
        if not issuer.startswith("https://") or not audience:
            raise ValueError("explicit HTTPS issuer and audience are required")
        self.database_url = database_url.replace("postgresql+asyncpg://", "postgresql://")
        self.workspace_id = UUID(str(workspace_id))
        self.issuer = issuer
        self.audience = audience
        self.public_key = jwt.get_algorithm_by_name("RS256").prepare_key(public_key)
        if not isinstance(self.public_key, RSAPublicKey) or self.public_key.key_size < 2048:
            raise ValueError("an RSA public key of at least 2048 bits is required")

    async def authorize(self, token, required_scope, context, resource=None):
        claims = None
        try:
            claims = jwt.decode(token, self.public_key, algorithms=["RS256"], audience=self.audience, issuer=self.issuer, options={"require": ["sub", "iss", "aud", "iat", "nbf", "exp"], "strict_aud": True})
        except jwt.InvalidTokenError:
            pass
        principal = None
        outcome = "unauthenticated"
        async with await psycopg.AsyncConnection.connect(self.database_url, connect_timeout=3) as connection:
            await connection.execute("SET LOCAL statement_timeout = '3s'")
            subject = None
            if claims:
                cursor = await connection.execute("SELECT id, revision FROM public.p0_lock_identity(%s,%s,%s)", (self.issuer, claims["sub"], self.workspace_id))
                subject = await cursor.fetchone()
            if subject:
                cursor = await connection.execute("SELECT scope, effect FROM permission_grants WHERE workspace_id=%s AND principal_type='identity' AND principal_id=%s AND expires_at > clock_timestamp() AND constraints='{}'::jsonb", (self.workspace_id, str(subject[0])))
                grants = await cursor.fetchall()
                scopes = frozenset(scope for scope, effect in grants if effect == "allow") - frozenset(scope for scope, effect in grants if effect == "deny")
                permitted = required_scope in {"control:read", "control:write"} and required_scope in scopes
                if resource:
                    table, resource_id = resource
                    if table == "workspaces":
                        permitted = permitted and resource_id == self.workspace_id
                    elif table in {"jobs", "content_brief_versions", "ready_packages"}:
                        cursor = await connection.execute(psycopg.sql.SQL("SELECT workspace_id FROM {} WHERE id=%s").format(psycopg.sql.Identifier(table)), (resource_id,))
                        row = await cursor.fetchone()
                        permitted = permitted and row is not None and row[0] == self.workspace_id
                    else:
                        permitted = False
                outcome = "allow" if permitted else "deny"
                if permitted:
                    principal = Principal(subject[0], self.workspace_id, scopes)
            await connection.execute("INSERT INTO identity_access_events (subject_id, workspace_id, trace_id, span_id, required_scope, outcome, subject_revision) VALUES (%s,%s,%s,%s,%s,%s,%s)", (subject[0] if subject else None, self.workspace_id, context.trace_id, context.span_id, required_scope, outcome, subject[1] if subject else None))
        return principal, 401 if outcome == "unauthenticated" else 403

    async def ready(self):
        async with await psycopg.AsyncConnection.connect(self.database_url, connect_timeout=3) as connection:
            await connection.execute("SET LOCAL statement_timeout = '3s'")
            cursor = await connection.execute("SELECT EXISTS (SELECT 1 FROM workspaces WHERE id=%s AND status='active'), to_regclass('identity_access_events') IS NOT NULL, to_regclass('identity_subjects') IS NOT NULL, to_regclass('permission_grants') IS NOT NULL", (self.workspace_id,))
            return all(await cursor.fetchone())


def create_p0_app(*, database_url, workspace_id, issuer, audience, public_key, tracer=None):
    from salience.api.app import create_app
    from salience.api.dependencies import TemporalControlPlane

    boundary = IdentityBoundary(database_url=database_url, workspace_id=workspace_id, issuer=issuer, audience=audience, public_key=public_key)
    app = create_app(control_token="", control_plane=TemporalControlPlane(database_url=database_url.replace("postgresql://", "postgresql+asyncpg://"), temporal_target="disabled.invalid:7233", task_queue="p0-disabled"))
    app.state.identity_boundary = boundary
    if tracer is None:
        provider = TracerProvider(resource=Resource.create({"service.name": "salience-p0"}))
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter(), max_queue_size=512, max_export_batch_size=64, export_timeout_millis=5000))
        tracer = provider.get_tracer("salience.p0")
        app.state.trace_provider = provider
        original_lifespan = app.router.lifespan_context

        @asynccontextmanager
        async def lifespan(application):
            try:
                async with original_lifespan(application):
                    yield
            finally:
                provider.shutdown()

        app.router.lifespan_context = lifespan
    emitter = OpenTelemetryTraceEmitter(tracer)

    @app.get("/v1/workspaces/{workspace_id}/identity")
    async def identity(workspace_id: UUID, request: Request):
        principal = request.state.principal
        return {"subject_id": str(principal.subject_id), "workspace_id": str(principal.workspace_id), "scopes": sorted(principal.scopes), "effects_enabled": False}

    @app.middleware("http")
    async def isolate(request: Request, call_next):
        try:
            context = TraceContext.from_carrier({"traceparent": request.headers["traceparent"]})
        except (KeyError, ValueError):
            context = TraceContext.new_root()
        with emitter.active_span(context, "p0.control.request") as context:
            try:
                async with asyncio.timeout(5):
                    if request.url.path == "/health/live" and request.method == "GET":
                        response = JSONResponse({"status": "ok"})
                    elif request.url.path == "/health/ready" and request.method == "GET":
                        ready = await boundary.ready()
                        response = JSONResponse({"status": "ready" if ready else "not_ready", "effects_enabled": False}, status_code=200 if ready else 503)
                    else:
                        resource, scope = _request_scope(request)
                        authorization = request.headers.get("Authorization", "")
                        token = authorization[7:] if authorization.startswith("Bearer ") and len(authorization) <= 8192 else ""
                        principal, denied_status = await boundary.authorize(token, scope, context, resource)
                        if not principal:
                            response = JSONResponse({"detail": "access denied"}, status_code=denied_status)
                        else:
                            request.state.principal = principal
                            body_size = 0
                            chunks = []
                            async for chunk in request.stream():
                                body_size += len(chunk)
                                if body_size > 8192:
                                    break
                                chunks.append(chunk)
                            if body_size > 8192:
                                response = JSONResponse({"detail": "request too large"}, status_code=413)
                            else:
                                request._body = b"".join(chunks)
                                response = await call_next(request)
            except IntegrityError:
                response = JSONResponse({"detail": "conflicting control record"}, status_code=409)
            except (psycopg.Error, SQLAlchemyError, TimeoutError):
                response = JSONResponse({"detail": "dependency unavailable"}, status_code=503)
            response.headers["traceparent"] = context.to_carrier()["traceparent"]
            return response

    return app


def _request_scope(request):
    parts = request.url.path.strip("/").split("/")
    try:
        if len(parts) == 4 and parts[:2] == ["v1", "workspaces"]:
            resource = ("workspaces", UUID(parts[2]))
            if request.method == "GET" and parts[3] == "identity":
                return resource, "control:read"
            if request.method == "POST" and parts[3] == "programs":
                return resource, "control:write"
        if request.method == "GET" and len(parts) in {3, 4} and parts[:2] == ["v1", "jobs"]:
            return ("jobs", UUID(parts[2])), "control:read"
    except ValueError:
        pass
    return None, "p0:disabled"
