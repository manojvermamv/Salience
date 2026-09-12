from salience.agents.contracts import AgentManifest
from salience.agents.execution import AgentService
from salience.agents.registry import AgentRegistry
from salience.agents.specialists import fixture_specialist_runtimes
from salience.models.contracts import ModelGateway


def fixture_agent_service(
    *, model_gateways: dict[str, ModelGateway] | None = None
) -> AgentService:
    registry = AgentRegistry()
    registry.register(
        AgentManifest(
            agent_id="lead_content_agent",
            version="1.0.0",
            input_schema={"type": "object", "required": ["niche"]},
            output_schema={"type": "object", "required": ["niche", "source"]},
            tool_scopes=[],
            memory_scopes=[],
            effect_classification="read",
            supports_sync=True,
            supports_async=True,
        )
    )
    registry.register(
        AgentManifest(
            agent_id="research_agent",
            version="1.0.0",
            input_schema={"type": "object", "required": ["niche"]},
            output_schema={"type": "object", "required": ["niche", "source"]},
            tool_scopes=[],
            memory_scopes=["evidence"],
            effect_classification="read",
            supports_sync=True,
            supports_async=True,
        )
    )
    registry.register(
        AgentManifest(
            agent_id="strategy_agent",
            version="1.0.0",
            input_schema={"type": "object", "required": ["niche"]},
            output_schema={
                "type": "object",
                "required": ["niche", "source", "content_pillars"],
            },
            tool_scopes=[],
            memory_scopes=["semantic", "evidence"],
            effect_classification="read",
            supports_sync=True,
            supports_async=True,
        )
    )
    return AgentService(
        registry=registry,
        runtimes=fixture_specialist_runtimes(),
        model_gateways=model_gateways,
    )
