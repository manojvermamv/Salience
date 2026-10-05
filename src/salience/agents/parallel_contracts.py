"""Immutable bounded plans for independently executable agent tasks."""
import json
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, model_validator

class AgentTask(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    key: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    agent_id: str = Field(pattern=r'^[a-z][a-z0-9_]*$')
    input: dict[str, Any]

class ParallelTeamRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    contract_version: str = Field(default='ParallelAgentTeam.local.v1', pattern=r'^ParallelAgentTeam\.local\.v1$')
    team_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,128}$')
    lead_agent_id: str = Field(pattern=r'^[a-z][a-z0-9_]*$')
    tasks: tuple[AgentTask, ...] = Field(min_length=1,max_length=16)
    max_concurrency: int = Field(default=4,strict=True,ge=1,le=8)
    timeout_seconds: int = Field(default=30,strict=True,ge=1,le=300)
    max_spend_micros: int = Field(default=0,strict=True,ge=0,le=0)

    @model_validator(mode='after')
    def bounded(self):
        if len({task.key for task in self.tasks})!=len(self.tasks):raise ValueError('unique task keys required')
        try:
            encoded=json.dumps(self.model_dump(),allow_nan=False).encode()
        except (TypeError,ValueError,RecursionError) as error:
            raise ValueError('finite JSON input required') from error
        if len(encoded)>8192:raise ValueError('team input exceeds 8192 bytes')
        return self
