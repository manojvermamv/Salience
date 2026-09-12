"""Provider-neutral Phase 5 intelligence agent runtimes."""

from __future__ import annotations

from typing import Any

from salience.agents.execution import AgentExecutionContext, AgentInvocation, AgentRuntime
from salience.browser.contracts import BrowserResearchRequest, BrowserResearchTool
from salience.research.contracts import ResearchConnector


class ResearchAgentRuntime:
    def __init__(self, *, connector: ResearchConnector, source_label: str = "research-runtime") -> None:
        self._connector = connector
        self._source_label = source_label

    async def invoke(
        self, invocation: AgentInvocation, context: AgentExecutionContext
    ) -> dict[str, Any]:
        niche = invocation.input["niche"]
        findings = await self._connector.research(niche)
        return {
            "contract_version": "ResearchResult@v1",
            "niche": niche,
            "source": self._source_label,
            "findings": [
                {
                    "source_uri": finding.source_uri,
                    "content": finding.content,
                    "trust_level": finding.trust_level,
                    "verification_status": finding.verification_status,
                    "provenance": finding.provenance,
                }
                for finding in findings
            ],
            "contradictions": [],
            "unresolved_questions": ["Verify fixture-derived findings with configured sources."],
        }


class StrategyAgentRuntime:
    def __init__(self, *, source_label: str = "strategy-runtime") -> None:
        self._source_label = source_label

    async def invoke(
        self, invocation: AgentInvocation, context: AgentExecutionContext
    ) -> dict[str, Any]:
        niche = invocation.input["niche"]
        return {
            "contract_version": "StrategyProposal@v1",
            "niche": niche,
            "source": self._source_label,
            "audience": "people seeking practical, trustworthy guidance",
            "positioning": f"Evidence-grounded guidance for {niche}",
            "content_pillars": ["education", "decision support"],
            "channels": ["owned editorial"],
            "topic_priorities": [niche],
            "cadence": "operator-approved",
            "metrics": ["evidence coverage", "source diversity"],
            "assumptions": ["No external source is trusted without verification."],
            "uncertainties": ["Live source availability and audience response remain unknown."],
        }


class BrowserResearchAgentRuntime:
    def __init__(self, *, browser_tool: BrowserResearchTool | None = None) -> None:
        self._browser_tool = browser_tool

    async def invoke(
        self, invocation: AgentInvocation, context: AgentExecutionContext
    ) -> dict[str, Any]:
        url = invocation.input["url"]
        artifacts: list[dict[str, str]] = []
        if self._browser_tool is not None:
            result = await self._browser_tool.read(BrowserResearchRequest(url=url))
            artifacts = [
                {
                    "text_artifact_key": result.text_artifact_key,
                    "screenshot_artifact_key": result.screenshot_artifact_key,
                    "trace_artifact_key": result.trace_artifact_key or "",
                    "text_hash": result.text_hash,
                }
            ]
        return {
            "contract_version": "BrowserResearchResult@v1",
            "url": url,
            "source": "browser-runtime",
            "effect_classification": "read",
            "artifacts": artifacts,
            "trust_level": "untrusted_external",
        }
