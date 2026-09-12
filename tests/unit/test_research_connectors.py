from datetime import UTC, datetime

import httpx
import pytest

from salience.research.contracts import SourceFetchRequest
from salience.research.hacker_news import HackerNewsConnector
from salience.research.rss import ConfiguredRssResearchConnector, RssAtomConnector


@pytest.mark.asyncio
async def test_rss_connector_preserves_guid_pubdate_and_raw_hash() -> None:
    feed = b"""<?xml version="1.0"?><rss><channel><item>
      <title>Garden trends</title><link>https://feeds.example/posts/42</link>
      <guid>guid-42</guid><pubDate>Tue, 10 Sep 2026 12:00:00 +0000</pubDate>
    </item></channel></rss>"""
    connector = RssAtomConnector(
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, content=feed, headers={"content-type": "application/rss+xml"}
                )
            )
        )
    )

    items = await connector.fetch(
        SourceFetchRequest(
            source_id="publisher-rss",
            source_version="v1",
            url="https://feeds.example/rss.xml",
            allowed_domains=frozenset({"feeds.example"}),
            timeout_seconds=2,
            max_response_bytes=10_000,
        )
    )

    assert items[0].raw_identity == "guid-42"
    assert items[0].published_at == datetime(2026, 9, 10, 12, tzinfo=UTC)
    assert len(items[0].raw_hash) == 64
    assert items[0].trust_level == "untrusted_external"


@pytest.mark.asyncio
async def test_hn_connector_maps_item_id_and_available_engagement() -> None:
    connector = HackerNewsConnector(
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "id": 123,
                        "type": "story",
                        "title": "Garden tools",
                        "url": "https://example.test/gardens",
                        "time": 1_789_041_600,
                        "score": 50,
                        "descendants": 8,
                    },
                )
            )
        )
    )

    item = (
        await connector.fetch(
            SourceFetchRequest(
                source_id="hacker-news",
                source_version="v0",
                url="https://hacker-news.firebaseio.com/v0/item/123.json",
                allowed_domains=frozenset({"hacker-news.firebaseio.com"}),
                timeout_seconds=2,
                max_response_bytes=10_000,
            )
        )
    )[0]

    assert item.raw_identity == "123"
    assert item.features["engagement"] == 50
    assert item.features["comments"] == 8
    assert item.canonical_url == "https://example.test/gardens"


@pytest.mark.asyncio
async def test_configured_rss_research_uses_the_native_research_result_contract() -> None:
    feed = b"""<rss><channel><item><title>Garden trends</title>
    <link>https://feeds.example/posts/42</link><guid>guid-42</guid></item></channel></rss>"""
    connector = ConfiguredRssResearchConnector(
        feed_urls=("https://feeds.example/rss.xml",),
        allowed_domains=frozenset({"feeds.example"}),
        timeout_seconds=2,
        max_response_bytes=10_000,
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, content=feed, headers={"content-type": "application/rss+xml"}
                )
            )
        ),
    )

    findings = await connector.research("urban gardening")

    assert findings[0].trust_level == "untrusted_external"
    assert findings[0].provenance["connector"] == "rss_atom"
