from salience.agents.contracts import AgentManifest
from salience.agents.execution import AgentService
from salience.agents.registry import AgentRegistry
from salience.agents.specialists import fixture_specialist_runtimes
from salience.models.contracts import ModelGateway
from salience.research.contracts import ResearchConnector


def fixture_agent_service(
    *,
    model_gateways: dict[str, ModelGateway] | None = None,
    research_connector: ResearchConnector | None = None,
    research_source_label: str = "fixture",
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
            output_schema={
                "type": "object",
                "required": [
                    "contract_version",
                    "niche",
                    "source",
                    "findings",
                    "contradictions",
                    "unresolved_questions",
                ],
            },
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
                "required": [
                    "contract_version",
                    "niche",
                    "source",
                    "content_pillars",
                    "audience",
                    "positioning",
                    "topic_priorities",
                    "uncertainties",
                ],
            },
            tool_scopes=[],
            memory_scopes=["semantic", "evidence"],
            effect_classification="read",
            supports_sync=True,
            supports_async=True,
        )
    )
    registry.register(
        AgentManifest(
            agent_id="browser_research_agent",
            version="1.0.0",
            input_schema={"type": "object", "required": ["url"]},
            output_schema={
                "type": "object",
                "required": [
                    "contract_version",
                    "url",
                    "effect_classification",
                    "artifacts",
                ],
            },
            tool_scopes=[],
            memory_scopes=[],
            effect_classification="read",
            supports_sync=True,
            supports_async=True,
        )
    )
    registry.register(
        AgentManifest(
            agent_id="writer_agent",
            version="1.0.0",
            input_schema={
                "type": "object",
                "required": [
                    "brief_id",
                    "content_program_id",
                    "claim_ids",
                    "evidence_ids",
                    "target_format",
                    "target_duration_seconds",
                ],
            },
            output_schema={
                "type": "object",
                "required": [
                    "contract_version",
                    "brief_id",
                    "content_program_id",
                    "claim_ids",
                    "evidence_ids",
                    "sections",
                ],
            },
            tool_scopes=["creative.script.plan"],
            memory_scopes=["evidence"],
            effect_classification="read",
            supports_sync=True,
            supports_async=True,
        )
    )
    registry.register(
        AgentManifest(
            agent_id="creative_director_agent",
            version="1.0.0",
            input_schema={"type": "object", "required": ["brief_id", "script_id"]},
            output_schema={
                "type": "object",
                "required": [
                    "contract_version",
                    "brief_id",
                    "script_id",
                    "provider_id",
                    "shots",
                    "capability_requests",
                ],
            },
            tool_scopes=["creative.direction.plan"],
            memory_scopes=["evidence"],
            effect_classification="read",
            supports_sync=True,
            supports_async=True,
        )
    )
    registry.register(
        AgentManifest(
            agent_id="production_agent",
            version="1.0.0",
            input_schema={
                "type": "object",
                "required": ["brief_id", "script_id", "capability", "max_variants"],
            },
            output_schema={
                "type": "object",
                "required": [
                    "contract_version",
                    "brief_id",
                    "script_id",
                    "capability",
                    "requested_variants",
                    "external_effect",
                ],
            },
            tool_scopes=["creative.production.plan"],
            memory_scopes=[],
            effect_classification="read",
            supports_sync=True,
            supports_async=True,
        )
    )
    registry.register(
        AgentManifest(
            agent_id="verifier_agent",
            version="1.0.0",
            input_schema={"type": "object", "required": ["subject_id", "subject_type"]},
            output_schema={
                "type": "object",
                "required": [
                    "contract_version",
                    "subject_id",
                    "subject_type",
                    "advisory_only",
                    "findings",
                    "deterministic_gate_required",
                ],
            },
            tool_scopes=["creative.verify"],
            memory_scopes=[],
            effect_classification="read",
            supports_sync=True,
            supports_async=True,
        )
    )
    return AgentService(
        registry=registry,
        runtimes=fixture_specialist_runtimes(
            research_connector=research_connector,
            source_label=research_source_label,
        ),
        model_gateways=model_gateways,
    )
