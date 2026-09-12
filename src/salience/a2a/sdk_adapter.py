"""Official A2A SDK adapter projected into Salience-owned remote-agent contracts."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

import httpx
from a2a.client.card_resolver import A2ACardResolver
from a2a.client.client import ClientCallContext, ClientConfig
from a2a.client.client_factory import ClientFactory
from a2a.helpers.proto_helpers import get_data_parts, new_data_message
from a2a.types.a2a_pb2 import AgentCard, Role, SendMessageRequest, StreamResponse
from google.protobuf.json_format import MessageToDict

from salience.a2a.contracts import RemoteAgentDescriptor


class A2ASdkAdapterError(ValueError):
    """Raised when an A2A SDK response cannot safely enter owned contracts."""


class A2ASdkRemoteAdapter:
    """Resolve A2A cards and tasks through the official SDK at an owned edge."""

    def __init__(
        self,
        *,
        agent_id: str,
        endpoint: str,
        timeout_seconds: float,
        authorization_header: str | None = None,
        http_client_factory: Callable[[], AbstractAsyncContextManager[httpx.AsyncClient]]
        | None = None,
        client_factory: Any | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.protocol_version = "unresolved"
        self._endpoint = endpoint
        self._timeout_seconds = timeout_seconds
        self._authorization_header = authorization_header
        self._http_client_factory = http_client_factory or self._new_http_client
        self._client_factory = client_factory or ClientFactory(
            ClientConfig(streaming=False, polling=False)
        )
        self._card: AgentCard | None = None
        self._descriptor: RemoteAgentDescriptor | None = None

    def _new_http_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._timeout_seconds)

    async def descriptor(self) -> RemoteAgentDescriptor:
        if self._descriptor is not None:
            return self._descriptor
        async with self._http_client_factory() as http_client:
            resolver = A2ACardResolver(http_client, self._endpoint)
            headers = (
                {"Authorization": self._authorization_header}
                if self._authorization_header
                else None
            )
            self._card = await resolver.get_agent_card(
                http_kwargs={"headers": headers} if headers else None
            )

        interface = _select_interface(self._card)
        self.protocol_version = interface.protocol_version
        self._descriptor = RemoteAgentDescriptor(
            agent_id=self.agent_id,
            name=self._card.name,
            protocol_version=self.protocol_version,
            skills=[skill.id for skill in self._card.skills],
            preferred_transport=interface.protocol_binding,
            protocol_extensions=("task", "artifact", "cancel"),
            authentication_mode="adapter-edge",
        )
        return self._descriptor

    async def invoke(
        self, input: dict[str, Any]
    ) -> tuple[dict[str, Any], list[dict[str, Any]], str | None]:
        await self.descriptor()
        if self._card is None:
            raise A2ASdkAdapterError("A2A agent card was not resolved")
        request = SendMessageRequest(
            message=new_data_message(input, role=Role.ROLE_USER)
        )
        client = self._client_factory.create(self._card)
        output: dict[str, Any] | None = None
        artifacts: list[dict[str, Any]] = []
        remote_task_id: str | None = None
        try:
            responses = client.send_message(
                request,
                context=ClientCallContext(timeout=self._timeout_seconds),
            )
            async for response in responses:
                response_output, response_artifacts, task_id = _project_response(response)
                output = response_output or output
                artifacts.extend(response_artifacts)
                remote_task_id = task_id or remote_task_id
        finally:
            await client.close()
        if output is None:
            raise A2ASdkAdapterError("A2A agent returned no structured output")
        return output, artifacts, remote_task_id


def _select_interface(card: AgentCard):
    if not card.supported_interfaces:
        raise A2ASdkAdapterError("A2A Agent Card declares no supported interfaces")
    for interface in card.supported_interfaces:
        if interface.protocol_version == "1.0":
            return interface
    return card.supported_interfaces[0]


def _project_response(
    response: StreamResponse,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str | None]:
    if response.HasField("message"):
        return _structured_data(response.message), [], None
    if not response.HasField("task"):
        return None, [], None
    task = response.task
    artifacts = [
        _json_object(MessageToDict(artifact), "A2A artifact") for artifact in task.artifacts
    ]
    output: dict[str, Any] | None = None
    for artifact in task.artifacts:
        output = _structured_data(artifact) or output
    return output, artifacts, task.id or None


def _structured_data(value: Any) -> dict[str, Any] | None:
    parts = getattr(value, "parts", ())
    for data in get_data_parts(parts):
        return _json_object(data, "A2A structured output")
    return None


def _json_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise A2ASdkAdapterError(f"{label} must be a JSON object")
    return value
