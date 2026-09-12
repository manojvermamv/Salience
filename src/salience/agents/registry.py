from collections import defaultdict

from salience.agents.contracts import AgentManifest, AgentVersionView


class AgentAlreadyRegistered(ValueError):
    pass


class AgentVersionDisabled(LookupError):
    pass


class AgentNotFound(LookupError):
    pass


class AgentRegistry:
    def __init__(self) -> None:
        self._versions: dict[str, list[AgentVersionView]] = defaultdict(list)

    def register(self, manifest: AgentManifest) -> AgentVersionView:
        versions = self._versions[manifest.agent_id]
        if any(version.version == manifest.version for version in versions):
            raise AgentAlreadyRegistered(f"{manifest.agent_id}@{manifest.version}")
        view = AgentVersionView(**manifest.model_dump())
        versions.append(view)
        return view

    def describe(self, agent_id: str, version: str | None = None) -> AgentVersionView:
        versions = self._versions.get(agent_id)
        if not versions:
            raise AgentNotFound(agent_id)
        if version is None:
            return versions[-1]
        for candidate in versions:
            if candidate.version == version:
                return candidate
        raise AgentNotFound(f"{agent_id}@{version}")

    def resolve(self, agent_id: str) -> AgentVersionView:
        version = self.describe(agent_id)
        if version.status != "enabled":
            raise AgentVersionDisabled(f"{agent_id}@{version.version}")
        return version

    def disable(self, agent_id: str, version: str) -> AgentVersionView:
        existing = self.describe(agent_id, version)
        disabled = existing.model_copy(update={"status": "disabled"})
        versions = self._versions[agent_id]
        versions[versions.index(existing)] = disabled
        return disabled

    def history(self, agent_id: str) -> tuple[AgentVersionView, ...]:
        if agent_id not in self._versions:
            raise AgentNotFound(agent_id)
        return tuple(self._versions[agent_id])
