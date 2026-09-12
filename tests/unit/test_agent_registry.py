import pytest

from salience.agents.contracts import AgentManifest
from salience.agents.registry import AgentRegistry, AgentVersionDisabled


def manifest() -> AgentManifest:
    return AgentManifest(
        agent_id="research_agent",
        version="1.0.0",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        tool_scopes=["research.read"],
        memory_scopes=["evidence"],
        effect_classification="read",
        supports_sync=True,
        supports_async=True,
    )


def test_manifest_identity_is_independent_of_runtime() -> None:
    registry = AgentRegistry()
    registry.register(manifest())

    described = registry.describe("research_agent")

    assert described.agent_id == "research_agent"
    assert described.runtime_id is None


def test_disabled_versions_preserve_history_but_cannot_be_resolved() -> None:
    registry = AgentRegistry()
    registry.register(manifest())
    registry.disable("research_agent", "1.0.0")

    assert registry.history("research_agent")[0].status == "disabled"
    with pytest.raises(AgentVersionDisabled):
        registry.resolve("research_agent")
