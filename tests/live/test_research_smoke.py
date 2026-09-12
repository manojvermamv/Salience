import os

import httpx
import pytest

from salience.research.contracts import SourceFetchRequest
from salience.research.hacker_news import HackerNewsConnector


pytestmark = pytest.mark.live


@pytest.mark.asyncio
async def test_configured_hacker_news_source_smoke() -> None:
    endpoint = os.environ.get("LIVE_HACKER_NEWS_ITEM_URL")
    if not endpoint:
        pytest.skip("set LIVE_HACKER_NEWS_ITEM_URL to run live research smoke tests")
    hostname = httpx.URL(endpoint).host
    if not hostname:
        pytest.skip("live source endpoint has no hostname")
    async with httpx.AsyncClient() as client:
        results = await HackerNewsConnector(client=client).fetch(
            SourceFetchRequest(
                source_id="hacker-news-live",
                source_version="v0",
                url=endpoint,
                allowed_domains=frozenset({hostname}),
                timeout_seconds=10,
                max_response_bytes=1_000_000,
            )
        )
    assert results
