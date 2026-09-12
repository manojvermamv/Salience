from salience.agents.contracts import AgentManifest
from salience.agents.registry import AgentRegistry


class AgentRepository:
    """Repository seam; PostgreSQL persistence is introduced at the API boundary."""

    def __init__(self, registry: AgentRegistry | None = None) -> None:
        self._registry = registry or AgentRegistry()

    def register(self, manifest: AgentManifest):
        return self._registry.register(manifest)

    def describe(self, agent_id: str):
        return self._registry.describe(agent_id)
