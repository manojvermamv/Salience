import os
from collections.abc import Mapping

from fastapi import FastAPI

from salience.api.dependencies import ControlPlane, InMemoryControlPlane, TemporalControlPlane
from salience.api.routes import agents, control, creative, health, intelligence
from salience.agents.execution import AgentService
from salience.agents.fixtures import fixture_agent_service
from salience.config import Settings
from salience.creative.providers import CreativeProvider
from salience.creative.repository import CreativeRepository


def create_app(
    *,
    control_token: str,
    control_plane: ControlPlane | None = None,
    agent_service: AgentService | None = None,
    creative_providers: Mapping[str, CreativeProvider] | None = None,
    creative_repository: CreativeRepository | None = None,
) -> FastAPI:
    app = FastAPI(title="Salience control plane", version="0.1.0")
    app.state.control_token = control_token
    app.state.control_plane = control_plane or InMemoryControlPlane()
    app.state.agent_service = agent_service or fixture_agent_service()
    app.state.creative_providers = dict(creative_providers or {})
    app.state.creative_repository = creative_repository
    app.include_router(health.router)
    app.include_router(control.router)
    app.include_router(agents.router)
    app.include_router(intelligence.router)
    app.include_router(creative.router)
    return app


def create_configured_app() -> FastAPI:
    settings = Settings.from_environment()
    return create_app(
        control_token=os.environ["CONTROL_PLANE_TOKEN"],
        control_plane=TemporalControlPlane(
            database_url=settings.database_url,
            temporal_target=settings.temporal_target,
            task_queue=settings.worker_task_queue,
        ),
        creative_repository=CreativeRepository(settings.database_url),
    )
