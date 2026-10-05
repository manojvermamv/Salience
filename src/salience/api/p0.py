"""Isolated qualification API: authenticated local control, no effect dispatch."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import os
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
    def __init__(self, *, database_url, workspace_id, issuer, audience, public_key, enable_v4_fixture_commands=False, enable_parallel_agent_teams=False):
        if not issuer.startswith("https://") or not audience:
            raise ValueError("explicit HTTPS issuer and audience are required")
        self.database_url = database_url.replace("postgresql+asyncpg://", "postgresql://")
        self.workspace_id = UUID(str(workspace_id))
        self.issuer = issuer
        self.audience = audience
        self.public_key = jwt.get_algorithm_by_name("RS256").prepare_key(public_key)
        if not isinstance(self.public_key, RSAPublicKey) or self.public_key.key_size < 2048:
            raise ValueError("an RSA public key of at least 2048 bits is required")
        if enable_parallel_agent_teams:
            self.allowed_scopes = {'agents:delegate', 'agents:read'}
        else:
            self.allowed_scopes = set()
        self.allowed_scopes |= {"control:read", "control:write"}
        if enable_v4_fixture_commands:
            self.allowed_scopes.update({"goals:write", "goals:approve", "cycles:write", "cycles:read", "cycles:stop", "cycles:review", "cycles:schedule"})

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
                permitted = required_scope in self.allowed_scopes and required_scope in scopes
                if resource:
                    table, resource_id = resource
                    if table == "workspaces":
                        permitted = permitted and resource_id == self.workspace_id
                    elif table in {"jobs", "content_brief_versions", "ready_packages", "topic_opportunities"}:
                        cursor = await connection.execute(psycopg.sql.SQL("SELECT workspace_id FROM {} WHERE id=%s").format(psycopg.sql.Identifier(table)), (resource_id,))
                        row = await cursor.fetchone()
                        permitted = permitted and row is not None and row[0] == self.workspace_id
                    elif table == "v4_goals":
                        cursor = await connection.execute("SELECT workspace_id FROM v4_goals WHERE id=%s", (resource_id,))
                        row = await cursor.fetchone()
                        permitted = permitted and row is not None and row[0] == self.workspace_id
                    elif table == "v4_cycle_intents":
                        cursor = await connection.execute("SELECT goal.workspace_id FROM v4_cycle_intents AS intent JOIN v4_goals AS goal ON goal.id=intent.goal_id WHERE intent.id=%s", (resource_id,))
                        row = await cursor.fetchone()
                        permitted = permitted and row is not None and row[0] == self.workspace_id
                    elif table == "v4_cycles":
                        cursor = await connection.execute("SELECT goal.workspace_id FROM v4_cycles AS cycle JOIN v4_cycle_intents AS intent ON intent.id=cycle.intent_id JOIN v4_goals AS goal ON goal.id=intent.goal_id WHERE cycle.id=%s", (resource_id,))
                        row = await cursor.fetchone()
                        permitted = permitted and row is not None and row[0] == self.workspace_id
                    elif table == "v4_recovery_cases":
                        cursor = await connection.execute("SELECT goal.workspace_id FROM v4_recovery_cases AS recovery JOIN v4_goals AS goal ON goal.id=recovery.goal_id WHERE recovery.id=%s", (resource_id,))
                        row = await cursor.fetchone()
                        permitted = permitted and row is not None and row[0] == self.workspace_id
                    elif table == "v4_case_notifications":
                        cursor = await connection.execute("SELECT goal.workspace_id FROM v4_case_notifications AS notification JOIN v4_recovery_cases AS recovery ON recovery.id=notification.case_id JOIN v4_goals AS goal ON goal.id=recovery.goal_id WHERE notification.id=%s", (resource_id,))
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


def create_p0_app(*, database_url, workspace_id, issuer, audience, public_key, tracer=None, enable_v4_fixture_commands=False, enable_parallel_agent_teams=False, parallel_agent_service=None, enable_legacy_dispatch=False, legacy_fixture_queue=None, legacy_temporal_target=None):
    from salience.api.app import create_app
    from salience.api.dependencies import TemporalControlPlane

    if (enable_v4_fixture_commands or enable_parallel_agent_teams or enable_legacy_dispatch) and (os.environ.get("SALIENCE_DEPLOYMENT_MODE") != "fixture" or os.environ.get("SALIENCE_EFFECTS_ENABLED", "false") != "false"):
        raise ValueError("V4 public commands require explicit no-effects fixture mode")
    if enable_legacy_dispatch:
        from salience.cycles.legacy_dispatch import LegacyDispatch
        LegacyDispatch(database_url,workspace_id=workspace_id,subject_id=workspace_id,task_queue=legacy_fixture_queue)
        enable_v4_fixture_commands = True
    boundary = IdentityBoundary(database_url=database_url, workspace_id=workspace_id, issuer=issuer, audience=audience, public_key=public_key, enable_v4_fixture_commands=enable_v4_fixture_commands, enable_parallel_agent_teams=enable_parallel_agent_teams)
    legacy_router = None
    legacy_jobs_router = None
    if enable_legacy_dispatch:
        from salience.api.routes.legacy_dispatch import router as legacy_router
        from salience.api.routes.legacy_jobs import router as legacy_jobs_router
        boundary.allowed_scopes.add("legacy:dummy")
    app = create_app(control_token="", control_plane=TemporalControlPlane(database_url=database_url.replace("postgresql://", "postgresql+asyncpg://"), temporal_target="disabled.invalid:7233", task_queue="p0-disabled"),legacy_intelligence_router=legacy_router,legacy_control_router=legacy_jobs_router)
    app.state.identity_boundary = boundary
    app.state.enable_legacy_dispatch = enable_legacy_dispatch
    if enable_legacy_dispatch:
        app.state.legacy_fixture_queue = legacy_fixture_queue
        if legacy_temporal_target:
            from salience.cycles.temporal_schedule_control import TemporalFixtureLegacyScheduleControl
            app.state.legacy_schedule_control = TemporalFixtureLegacyScheduleControl(boundary.database_url,
                workspace_id=workspace_id,temporal_target=legacy_temporal_target,task_queue=legacy_fixture_queue)
    if enable_parallel_agent_teams:
        from salience.agents.fixtures import fixture_agent_service
        from salience.api.routes.parallel_teams import router
        app.state.parallel_agent_service = parallel_agent_service or fixture_agent_service()
        app.include_router(router)
    if enable_v4_fixture_commands:
        from salience.api.routes import v4_cycles

        app.include_router(v4_cycles.router)
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
                            request.state.trace_context = context
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
        if getattr(request.app.state,"enable_legacy_dispatch",False) and parts[:2]==["v1","jobs"]:
            if len(parts)==3 and parts[2]=="dummy" and request.method=="POST":
                return None,"legacy:dummy"
            if len(parts) in {3,4}:
                resource = ("jobs",UUID(parts[2]))
                if len(parts)==3 and request.method=="GET":
                    return resource,"cycles:read"
                if len(parts)==4 and parts[3]=="cancel" and request.method=="POST":
                    return resource,"cycles:write"
        if getattr(request.app.state,"enable_legacy_dispatch",False) and len(parts)==5 and parts[:3]==["v1","intelligence","opportunities"] and parts[4]=="briefs" and request.method=="POST":
            return ("topic_opportunities",UUID(parts[3])),"cycles:write"
        if getattr(request.app.state,"enable_legacy_dispatch",False) and len(parts)==5 and parts[:3]==["v1","intelligence","schedules"] and parts[4] in {"bind","adopt-native"} and request.method=="POST":
            return ("v4_goals",UUID(parts[3])),"cycles:schedule"
        if getattr(request.app.state,"enable_legacy_dispatch",False) and parts[:3] == ["v1","intelligence","runs"]:
            if len(parts)==3 and request.method=="POST":
                return None,"cycles:write"
            if len(parts) in {4,5}:
                resource = ("jobs",UUID(parts[3]))
                if len(parts)==4 and request.method=="GET":
                    return resource,"cycles:read"
                if len(parts)==5 and parts[4]=="cancel" and request.method=="POST":
                    return resource,"cycles:write"
        if len(parts) >= 4 and parts[:2] == ['v1', 'workspaces'] and parts[3] == 'agent-teams':
            resource = ('workspaces', UUID(parts[2]))
            if len(parts) == 4 and request.method == 'POST':
                return resource, 'agents:delegate'
            if len(parts) >= 5:
                UUID(parts[4])
                if len(parts) == 5 and request.method == 'GET':
                    return resource, 'agents:read'
                if len(parts) == 6 and parts[5] == 'cancel' and request.method == 'POST':
                    return resource, 'agents:delegate'
        if request.method == "POST" and len(parts) == 5 and parts[:2] == ["v1", "workspaces"] and parts[3:] == ["v4", "stop"]:
            return ("workspaces", UUID(parts[2])), "cycles:stop"
        if request.method == "POST" and len(parts) == 5 and parts[:2] == ["v1", "workspaces"] and parts[3:] == ["v4", "goals"]:
            return ("workspaces", UUID(parts[2])), "goals:write"
        if len(parts) >= 4 and parts[:3] == ["v1", "v4", "goals"]:
            resource = ("v4_goals", UUID(parts[3]))
            if request.method == "GET" and len(parts) == 4:
                return resource, "cycles:read"
            if len(parts) in {5, 6} and parts[4] == "schedule-cutover":
                if request.method == "GET" and len(parts) == 5:
                    return resource, "cycles:read"
                if request.method == "POST" and (len(parts) == 5 or parts[5] in {"activate", "poll", "rollback"}):
                    return resource, "cycles:schedule"
            if request.method == "POST" and len(parts) == 5 and parts[4] == "baseline":
                return resource, "goals:approve"
            if request.method == "POST" and len(parts) == 7 and parts[4] == "baseline" and parts[6] == "revoke":
                UUID(parts[5])
                return resource, "goals:approve"
            if request.method == "POST" and len(parts) == 5 and parts[4] in {"revisions", "state"}:
                return resource, "goals:write"
            if request.method == "POST" and len(parts) == 5 and parts[4] == "requests":
                return resource, "cycles:write"
            if request.method == "POST" and len(parts) == 5 and parts[4] == "stop":
                return resource, "cycles:stop"
        if request.method == "GET" and len(parts) == 4 and parts[:3] == ["v1", "v4", "intents"]:
            return ("v4_cycle_intents", UUID(parts[3])), "cycles:read"
        if request.method == "POST" and len(parts) == 5 and parts[:3] == ["v1", "v4", "intents"] and parts[4] == "admit":
            return ("v4_cycle_intents", UUID(parts[3])), "cycles:write"
        if request.method == "POST" and len(parts) == 5 and parts[:3] == ["v1", "v4", "intents"] and parts[4] == "cases":
            return ("v4_cycle_intents", UUID(parts[3])), "cycles:write"
        if len(parts) in {4, 5} and parts[:3] == ["v1", "v4", "cycles"]:
            resource = ("v4_cycles", UUID(parts[3]))
            if request.method == "GET" and (len(parts) == 4 or parts[4] == "events"):
                return resource, "cycles:read"
            if request.method == "POST" and len(parts) == 5 and parts[4] == "cancel":
                return resource, "cycles:write"
            if request.method == "POST" and len(parts) == 5 and parts[4] == "cases":
                return resource, "cycles:write"
        if len(parts) in {4, 5} and parts[:3] == ["v1", "v4", "cases"]:
            resource = ("v4_recovery_cases", UUID(parts[3]))
            if request.method == "GET" and len(parts) == 4:
                return resource, "cycles:read"
            if request.method == "POST" and len(parts) == 5:
                return resource, "cycles:review" if parts[4] == "review" else "cycles:write" if parts[4] in {"resume", "terminalize", "archive"} else "p0:disabled"
        if request.method == "POST" and len(parts) == 5 and parts[:3] == ["v1", "v4", "notifications"] and parts[4] == "ack":
            return ("v4_case_notifications", UUID(parts[3])), "cycles:review"
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
