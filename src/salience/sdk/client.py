from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel


class AgentView(BaseModel):
    agent_id: str
    version: str | None = None
    status: str | None = None


class AgentRunView(BaseModel):
    agent_id: str
    status: str
    run_id: str | None = None
    output: dict[str, Any] = {}


@dataclass
class AgentsClient:
    base_url: str
    token: str

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        response = httpx.request(
            method,
            f"{self.base_url.rstrip('/')}{path}",
            headers={
                "Authorization": f"Bearer {self.token}",
                "X-Salience-Scopes": "control:read,control:write",
            },
            json=payload,
            timeout=10,
        )
        response.raise_for_status()
        return response.json()

    def describe(self, agent_id: str) -> AgentView:
        return AgentView.model_validate(self._request("GET", f"/v1/agents/{agent_id}"))

    def run(
        self, agent_id: str, input: dict[str, Any], *, mode: str = "async"
    ) -> AgentRunView:
        return AgentRunView.model_validate(
            self._request(
                "POST",
                f"/v1/agents/{agent_id}/runs",
                {"input": input, "mode": mode},
            )
        )


class SalienceClient:
    def __init__(self, base_url: str, token: str) -> None:
        self.agents = AgentsClient(base_url, token)
