from typing import Any
from uuid import UUID

from salience.a2a.contracts import A2ARemoteAgent, RemoteAgentDescriptor, RemoteAgentResult


class RemoteProtocolIncompatible(ValueError):
    pass


class RemoteAgentGateway:
    def __init__(self, *, agent: A2ARemoteAgent, supported_protocol_version: str) -> None:
        self._agent = agent
        self._supported_protocol_version = supported_protocol_version

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
        output, artifacts = await self._agent.invoke(input)
        return RemoteAgentResult(
            output=output,
            parent_run_id=parent_run_id,
            artifacts=artifacts,
            protocol_version=descriptor.protocol_version,
        )

    def _assert_compatible(self, version: str) -> None:
        if version != self._supported_protocol_version:
            raise RemoteProtocolIncompatible(
                f"A2A {version} is not compatible with {self._supported_protocol_version}"
            )
