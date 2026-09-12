from typing import Any
from uuid import UUID

from salience.a2a.contracts import A2ARemoteAgent, RemoteAgentDescriptor, RemoteAgentResult


class RemoteProtocolIncompatible(ValueError):
    pass


class RemoteAgentGateway:
    def __init__(
        self,
        *,
        agent: A2ARemoteAgent,
        supported_protocol_version: str,
        legacy_protocol_versions: frozenset[str] = frozenset(),
    ) -> None:
        self._agent = agent
        self._supported_protocol_version = supported_protocol_version
        self._legacy_protocol_versions = legacy_protocol_versions

    async def discover(self, endpoint: str) -> RemoteAgentDescriptor:
        if endpoint != self._agent.agent_id:
            raise LookupError(endpoint)
        descriptor = await self._agent.descriptor()
        self._assert_compatible(descriptor.protocol_version)
        return descriptor

    async def invoke(
        self,
        descriptor: RemoteAgentDescriptor,
        input: dict[str, Any],
        parent_run_id: UUID,
    ) -> RemoteAgentResult:
        self._assert_compatible(descriptor.protocol_version)
        output, artifacts, remote_task_id = await self._agent.invoke(input)
        return RemoteAgentResult(
            output=output,
            parent_run_id=parent_run_id,
            artifacts=artifacts,
            protocol_version=descriptor.protocol_version,
            remote_task_id=remote_task_id,
        )

    def _assert_compatible(self, version: str) -> None:
        normalized_version = _normalize_version(version)
        supported_versions = {
            _normalize_version(self._supported_protocol_version),
            *(_normalize_version(value) for value in self._legacy_protocol_versions),
        }
        if normalized_version in supported_versions:
            return
        raise RemoteProtocolIncompatible(
            f"A2A {normalized_version} is not compatible with "
            f"{_normalize_version(self._supported_protocol_version)}; migrate the remote "
            "agent or configure an explicit legacy compatibility path"
        )


def _normalize_version(version: str) -> str:
    return version.removesuffix(".0") if version.count(".") == 2 else version
