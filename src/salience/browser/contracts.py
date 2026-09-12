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
