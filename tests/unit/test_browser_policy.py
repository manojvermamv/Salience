import pytest

from salience.browser.contracts import BrowserResearchRequest, NetworkScopeDenied
from salience.browser.playwright import PlaywrightBrowserResearchTool
from salience.storage.memory import MemoryObjectStore


@pytest.mark.asyncio
async def test_browser_blocks_redirect_to_unapproved_domain_before_browser_start() -> None:
    tool = PlaywrightBrowserResearchTool(
        object_store=MemoryObjectStore(), allowed_domains=frozenset({"allowed.test"})
    )

    with pytest.raises(NetworkScopeDenied):
        await tool.read(BrowserResearchRequest(url="https://blocked.test/redirect"))
