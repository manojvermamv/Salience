import sys
from pathlib import Path
from types import ModuleType

import pytest

from salience.browser.contracts import BrowserEvidenceFailure, BrowserResearchRequest, NetworkScopeDenied
from salience.browser.playwright import PlaywrightBrowserResearchTool
from salience.research.http import assert_network_scope
from salience.storage.memory import MemoryObjectStore


class _FakeTrace:
    async def start(self, **_: object) -> None:
        return None

    async def stop(self, *, path: str) -> None:
        Path(path).write_bytes(b"trace")


class _FakePage:
    url = "https://allowed.test/page"

    async def route(self, _pattern: str, _handler: object) -> None:
        return None

    async def goto(self, _url: str, *, timeout: int) -> None:
        assert timeout == 1_000

    def locator(self, _selector: str) -> "_FakePage":
        return self

    async def inner_text(self, *, timeout: int) -> str:
        assert timeout == 1_000
        return "untrusted evidence"

    async def screenshot(self) -> bytes:
        return b"screenshot"


class _FakeContext:
    tracing = _FakeTrace()

    async def new_page(self) -> _FakePage:
        return _FakePage()

    async def close(self) -> None:
        return None


class _FakeBrowser:
    version = "Fake Chromium 1.0"

    async def new_context(self, **kwargs: object) -> _FakeContext:
        assert kwargs == {"accept_downloads": False, "ignore_https_errors": False}
        return _FakeContext()

    async def close(self) -> None:
        return None


class _FakePlaywright:
    class chromium:
        @staticmethod
        async def launch(**kwargs: object) -> _FakeBrowser:
            assert kwargs == {"headless": True}
            return _FakeBrowser()


class _FakePlaywrightContext:
    async def __aenter__(self) -> _FakePlaywright:
        return _FakePlaywright()

    async def __aexit__(self, *_: object) -> None:
        return None


def _install_fake_playwright(monkeypatch: pytest.MonkeyPatch) -> None:
    package = ModuleType("playwright")
    package.__version__ = "1.62.0"
    async_api = ModuleType("playwright.async_api")
    async_api.async_playwright = _FakePlaywrightContext
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.async_api", async_api)


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
async def test_browser_persists_complete_evidence_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_playwright(monkeypatch)
    store = MemoryObjectStore()
    tool = PlaywrightBrowserResearchTool(
        object_store=store,
        allowed_domains=frozenset({"allowed.test"}),
    )

    result = await tool.read(
        BrowserResearchRequest(
            url="https://allowed.test/page",
            timeout_seconds=1,
            agent_run_id="agent-1",
            tool_run_id="tool-1",
            trace_id="trace-1",
        )
    )

    assert result.browser_version == "Fake Chromium 1.0"
    assert result.playwright_version == "1.62.0"
    assert result.screenshot_hash
    assert result.trace_hash
    assert result.fetched_at.endswith("+00:00")
    metadata = store.get(result.text_artifact_key).metadata
    assert metadata == {
        "source_url": "https://allowed.test/page",
        "fetched_at": result.fetched_at,
        "playwright_version": "1.62.0",
        "browser_version": "Fake Chromium 1.0",
        "agent_run_id": "agent-1",
        "tool_run_id": "tool-1",
        "trace_id": "trace-1",
        "artifact_type": "text",
        "artifact_hash": result.text_hash,
    }


@pytest.mark.asyncio
async def test_browser_response_limit_preserves_trace_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_playwright(monkeypatch)
    store = MemoryObjectStore()
    tool = PlaywrightBrowserResearchTool(
        object_store=store,
        allowed_domains=frozenset({"allowed.test"}),
    )

    with pytest.raises(BrowserEvidenceFailure) as raised:
        await tool.read(
            BrowserResearchRequest(
                url="https://allowed.test/page",
                timeout_seconds=1,
                max_response_bytes=1,
            )
        )

    assert raised.value.category == "response_limit"
    assert raised.value.trace_artifact_key is not None
    assert store.get(raised.value.trace_artifact_key).content_type == "application/zip"


@pytest.mark.asyncio
async def test_browser_blocks_redirect_to_unapproved_domain_before_browser_start() -> None:
    tool = PlaywrightBrowserResearchTool(
        object_store=MemoryObjectStore(), allowed_domains=frozenset({"allowed.test"})
    )

    with pytest.raises(NetworkScopeDenied):
        await tool.read(BrowserResearchRequest(url="https://blocked.test/redirect"))
