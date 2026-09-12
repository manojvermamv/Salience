"""Owned browser DTOs; no Playwright objects cross this boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from salience.research.contracts import NetworkScopeDenied


class BrowserUnavailable(RuntimeError):
    """The optional package or operator-installed browser binary is unavailable."""


@dataclass(frozen=True)
class BrowserResearchRequest:
    url: str
    timeout_seconds: float = 30
    step_limit: int = 20
    max_response_bytes: int = 1_000_000
    agent_run_id: str | None = None
    tool_run_id: str | None = None
    trace_id: str | None = None

    def __post_init__(self) -> None:
        for field_name, value in (
            ("timeout_seconds", self.timeout_seconds),
            ("step_limit", self.step_limit),
            ("max_response_bytes", self.max_response_bytes),
        ):
            if value <= 0:
                raise ValueError(f"{field_name} must be positive")


@dataclass(frozen=True)
class BrowserResearchResult:
    canonical_url: str
    text_artifact_key: str
    screenshot_artifact_key: str
    trace_artifact_key: str | None
    text_hash: str


class BrowserResearchTool(Protocol):
    async def read(self, request: BrowserResearchRequest) -> BrowserResearchResult: ...


__all__ = ["BrowserResearchRequest", "BrowserResearchResult", "BrowserUnavailable", "NetworkScopeDenied"]
