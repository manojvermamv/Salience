import pytest
from uuid import uuid4

from salience.agents.execution import AgentExecutionContext, AgentInvocation
from salience.agents.fixtures import fixture_agent_service
from salience.agents.intelligence import BrowserResearchAgentRuntime
from salience.browser.contracts import BrowserResearchRequest, BrowserResearchResult
from salience.observability.tracing import TraceContext


@pytest.mark.asyncio
async def test_intelligence_agents_emit_versioned_provider_neutral_contracts() -> None:
    service = fixture_agent_service()

    research = await service.invoke(
        AgentInvocation("research_agent", {"niche": "home gardening"})
    )
    strategy = await service.invoke(
        AgentInvocation("strategy_agent", {"niche": "home gardening"})
    )
    browser = await service.invoke(
        AgentInvocation("browser_research_agent", {"url": "https://example.test/article"})
    )

    assert research.output["contract_version"] == "ResearchResult@v1"
    assert {"findings", "contradictions", "unresolved_questions"} <= research.output.keys()
    assert strategy.output["contract_version"] == "StrategyProposal@v1"
    assert {"audience", "positioning", "topic_priorities", "uncertainties"} <= strategy.output.keys()
    assert browser.output["contract_version"] == "BrowserResearchResult@v1"
    assert browser.output["effect_classification"] == "read"


class _RecordingBrowserTool:
    request: BrowserResearchRequest | None = None

    async def read(self, request: BrowserResearchRequest) -> BrowserResearchResult:
        self.request = request
        return BrowserResearchResult(
            canonical_url=request.url,
            text_artifact_key="browser/evidence.txt",
            screenshot_artifact_key="browser/evidence.png",
            trace_artifact_key="browser/evidence.zip",
            text_hash="text-hash",
            screenshot_hash="screenshot-hash",
            trace_hash="trace-hash",
            fetched_at="2026-09-12T00:00:00+00:00",
            playwright_version="1.62.0",
            browser_version="Chromium",
        )


@pytest.mark.asyncio
async def test_browser_agent_forwards_canonical_run_lineage() -> None:
    browser_tool = _RecordingBrowserTool()
    run_id = uuid4()
    trace_context = TraceContext.new_root()
    runtime = BrowserResearchAgentRuntime(browser_tool=browser_tool)

    output = await runtime.invoke(
        AgentInvocation("browser_research_agent", {"url": "https://allowed.test/page"}),
        AgentExecutionContext(run_id=run_id, trace_context=trace_context),
    )

    assert browser_tool.request is not None
    assert browser_tool.request.agent_run_id == str(run_id)
    assert browser_tool.request.tool_run_id == f"browser:{run_id}"
    assert browser_tool.request.trace_id == trace_context.trace_id
    assert output["trust_level"] == "untrusted_external"
