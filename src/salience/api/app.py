import os

from fastapi import FastAPI

from salience.api.dependencies import ControlPlane, InMemoryControlPlane, TemporalControlPlane
from salience.api.routes import agents, control, health
from salience.agents.execution import AgentService
from salience.agents.fixtures import fixture_agent_service
from salience.config import Settings


def create_app(
    *,
    control_token: str,
    control_plane: ControlPlane | None = None,
    agent_service: AgentService | None = None,
) -> FastAPI:
    app = FastAPI(title="Salience control plane", version="0.1.0")
    app.state.control_token = control_token
    app.state.control_plane = control_plane or InMemoryControlPlane()
    app.state.agent_service = agent_service or fixture_agent_service()
    app.include_router(health.router)
    app.include_router(control.router)
    app.include_router(agents.router)
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
    )
