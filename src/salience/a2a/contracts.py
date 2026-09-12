from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID


@dataclass(frozen=True)
class RemoteAgentDescriptor:
    agent_id: str
    name: str
    protocol_version: str
    skills: list[str]
    preferred_transport: str


@dataclass(frozen=True)
class RemoteAgentResult:
    output: dict[str, Any]
    parent_run_id: UUID
    artifacts: list[dict[str, Any]]
    protocol_version: str


class A2ARemoteAgent(Protocol):
    agent_id: str
    protocol_version: str

    async def descriptor(self) -> RemoteAgentDescriptor: ...

    async def invoke(self, input: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]: ...
