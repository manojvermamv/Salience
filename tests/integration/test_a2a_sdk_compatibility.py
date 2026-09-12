from collections.abc import AsyncIterator
from uuid import uuid4

import httpx
import pytest
from a2a.helpers.proto_helpers import new_data_part
from a2a.types.a2a_pb2 import Artifact, StreamResponse, Task, TaskState, TaskStatus
from google.protobuf.json_format import MessageToDict

from salience.a2a.gateway import RemoteAgentGateway, RemoteProtocolIncompatible
from salience.a2a.sdk_adapter import A2ASdkRemoteAdapter


class FixtureA2AClient:
    def send_message(self, request, *, context) -> AsyncIterator[StreamResponse]:
        request_data = MessageToDict(request)
        assert request_data["message"]["parts"][0]["data"] == {"niche": "gardening"}
        assert context.timeout == 2

        async def responses() -> AsyncIterator[StreamResponse]:
            task = Task(
                id="fixture-task",
                context_id="fixture-context",
                status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
                artifacts=[
                    Artifact(
                        artifact_id="fixture-artifact",
                        name="research-result",
                        parts=[
                            new_data_part(
                                {"niche": "gardening", "source": "a2a-sdk-fixture"}
                            )
                        ],
                    )
                ],
            )
            yield StreamResponse(task=task)

        return responses()

    async def close(self) -> None:
        return None


class FixtureA2AClientFactory:
    def create(self, card) -> FixtureA2AClient:
        assert card.name == "Fixture remote"
        return FixtureA2AClient()


@pytest.mark.asyncio
async def test_a2a_sdk_adapter_preserves_1_0_task_lineage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer fixture-token"
        assert request.url.path == "/.well-known/agent-card.json"
        return httpx.Response(
            200,
            json={
                "name": "Fixture remote",
                "description": "A2A SDK contract fixture",
                "version": "1.0",
                "supportedInterfaces": [
                    {
                        "url": "https://fixture.test/a2a",
                        "protocolBinding": "fixture",
                        "protocolVersion": "1.0",
                    }
                ],
                "capabilities": {},
                "defaultInputModes": ["application/json"],
                "defaultOutputModes": ["application/json"],
                "skills": [
                    {
                        "id": "research.fetch",
                        "name": "Research fetch",
                        "description": "Fetch fixture research",
                        "tags": ["research"],
                    }
                ],
            },
        )

    adapter = A2ASdkRemoteAdapter(
        agent_id="fixture-agent",
        endpoint="https://fixture.test",
        timeout_seconds=2,
        authorization_header="Bearer fixture-token",
        http_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ),
        client_factory=FixtureA2AClientFactory(),
    )
    gateway = RemoteAgentGateway(agent=adapter, supported_protocol_version="1.0")

    descriptor = await gateway.discover("fixture-agent")
    result = await gateway.invoke(descriptor, {"niche": "gardening"}, uuid4())

    assert descriptor.protocol_version == "1.0"
    assert descriptor.protocol_extensions == ("task", "artifact", "cancel")
    assert descriptor.authentication_mode == "adapter-edge"
    assert result.remote_task_id == "fixture-task"
    assert result.output == {"niche": "gardening", "source": "a2a-sdk-fixture"}


@pytest.mark.asyncio
async def test_a2a_0_3_descriptor_is_explicitly_rejected_by_strict_v1_gateway() -> None:
    from salience.a2a.fixture_agent import FixtureA2AAgent

    gateway = RemoteAgentGateway(
        agent=FixtureA2AAgent(), supported_protocol_version="1.0"
    )

    with pytest.raises(RemoteProtocolIncompatible, match="0.3"):
        await gateway.discover("fixture-a2a")
