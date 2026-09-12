"""Read-only adapter for the official Hacker News Firebase item endpoint."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256

import httpx

from salience.research.contracts import FetchedSource, SourceFetchError, SourceFetchRequest
from salience.research.http import bounded_get, rate_limit_metadata


class HackerNewsConnector:
    def __init__(self, *, client: httpx.AsyncClient) -> None:
        self._client = client

    async def fetch(self, request: SourceFetchRequest) -> list[FetchedSource]:
        response = await bounded_get(
            self._client,
            request,
            accepted_content_types=frozenset({"application/json"}),
        )
        try:
            item = response.json()
            item_id = str(item["id"])
        except (TypeError, KeyError, ValueError) as error:
            raise SourceFetchError("Hacker News item payload is invalid") from error
        if item.get("type") not in {"story", "job", "poll"}:
            return []
        features: dict[str, int | float] = {}
        if isinstance(item.get("score"), int):
            features["engagement"] = item["score"]
        if isinstance(item.get("descendants"), int):
            features["comments"] = item["descendants"]
        published_at = (
            datetime.fromtimestamp(item["time"], tz=UTC)
            if isinstance(item.get("time"), int)
            else None
        )
        canonical_url = item.get("url") or f"https://news.ycombinator.com/item?id={item_id}"
        return [
            FetchedSource(
                source_id=request.source_id,
                source_version=request.source_version,
                source_type="hacker_news",
                resource_identity=item_id,
                canonical_url=canonical_url,
                raw_identity=item_id,
                raw_hash=sha256(response.content).hexdigest(),
                fetched_at=datetime.now(UTC),
                published_at=published_at,
                title=item.get("title"),
                content={
                    "title": item.get("title"),
                    "text": item.get("text"),
                    "author": item.get("by"),
                    "item_type": item.get("type"),
                },
                raw_payload=response.content,
                features=features,
                rate_limit=rate_limit_metadata(response),
                trust_level="untrusted_external",
                provenance={"connector": "hacker_news", "request_url": request.url},
            )
        ]
