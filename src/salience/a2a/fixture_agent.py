from typing import Any

from salience.a2a.contracts import RemoteAgentDescriptor


class FixtureA2AAgent:
    agent_id = "fixture-a2a"
    protocol_version = "0.3.0"

    async def descriptor(self) -> RemoteAgentDescriptor:
        return RemoteAgentDescriptor(
            agent_id=self.agent_id,
            name="Fixture A2A Agent",
            protocol_version=self.protocol_version,
            skills=["niche.lookup"],
            preferred_transport="JSONRPC",
        )

    async def invoke(self, input: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        output = {"niche": input["niche"], "source": "fixture"}
        return output, [{"kind": "structured-data", "data": output}]
