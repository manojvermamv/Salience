import pytest

from salience.browser.contracts import BrowserResearchRequest, NetworkScopeDenied
from salience.browser.playwright import PlaywrightBrowserResearchTool
from salience.research.http import assert_network_scope
from salience.storage.memory import MemoryObjectStore


@pytest.mark.parametrize(
    ("field", "value"),
    [("timeout_seconds", 0), ("step_limit", 0), ("max_response_bytes", 0)],
)
def test_browser_request_rejects_non_positive_limits(field: str, value: int) -> None:
    with pytest.raises(ValueError, match=field):
        BrowserResearchRequest(url="https://allowed.test/page", **{field: value})


def test_network_scope_rejects_private_literal_even_when_allowlisted() -> None:
    with pytest.raises(NetworkScopeDenied, match="private"):
        assert_network_scope("https://127.0.0.1/page", frozenset({"127.0.0.1"}))


def test_network_scope_allows_explicit_private_fixture_access() -> None:
    assert_network_scope(
        "https://127.0.0.1/page",
        frozenset({"127.0.0.1"}),
        allow_private_network=True,
    )


def test_browser_request_carries_owned_execution_lineage() -> None:
    request = BrowserResearchRequest(
        url="https://allowed.test/page",
        agent_run_id="agent-1",
        tool_run_id="tool-1",
        trace_id="trace-1",
    )

    assert request.agent_run_id == "agent-1"
    assert request.tool_run_id == "tool-1"
    assert request.trace_id == "trace-1"


@pytest.mark.asyncio
async def test_browser_blocks_redirect_to_unapproved_domain_before_browser_start() -> None:
    tool = PlaywrightBrowserResearchTool(
        object_store=MemoryObjectStore(), allowed_domains=frozenset({"allowed.test"})
    )

    with pytest.raises(NetworkScopeDenied):
        await tool.read(BrowserResearchRequest(url="https://blocked.test/redirect"))
