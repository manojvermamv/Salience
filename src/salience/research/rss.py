"""RSS/Atom source adapter with bounded transport and stable source identity."""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from hashlib import sha256
from typing import Any
from xml.etree import ElementTree

import httpx

from salience.research.contracts import (
    FetchedSource,
    ResearchFinding,
    SourceFetchError,
    SourceFetchRequest,
)
from salience.research.http import bounded_get, rate_limit_metadata


class RssAtomConnector:
    def __init__(self, *, client: httpx.AsyncClient) -> None:
        self._client = client

    async def fetch(self, request: SourceFetchRequest) -> list[FetchedSource]:
        response = await bounded_get(
            self._client,
            request,
            accepted_content_types=frozenset(
                {"application/rss+xml", "application/atom+xml", "application/xml", "text/xml"}
            ),
        )
        try:
            root = ElementTree.fromstring(response.content)
        except ElementTree.ParseError as error:
            raise SourceFetchError("source feed is not valid XML") from error
        entries = root.findall("./channel/item") or root.findall("{http://www.w3.org/2005/Atom}entry")
        fetched_at = datetime.now(UTC)
        return [
            _rss_item(request, entry, response.content, fetched_at, rate_limit_metadata(response))
            for entry in entries
        ]


class ConfiguredRssResearchConnector:
    """Convert operator-configured public feeds into the native Research contract."""

    def __init__(
        self,
        *,
        feed_urls: tuple[str, ...],
        allowed_domains: frozenset[str],
        timeout_seconds: int,
        max_response_bytes: int,
        client_factory: callable = httpx.AsyncClient,
    ) -> None:
        if not feed_urls:
            raise ValueError("at least one RSS/Atom feed URL is required")
        self._feed_urls = feed_urls
        self._allowed_domains = allowed_domains
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._client_factory = client_factory

    async def research(self, niche: str) -> list[ResearchFinding]:
        findings: list[ResearchFinding] = []
        failures: list[str] = []
        async with self._client_factory() as client:
            connector = RssAtomConnector(client=client)
            for index, url in enumerate(self._feed_urls):
                source_id = f"configured-rss-{index + 1}"
                try:
                    items = await connector.fetch(
                        SourceFetchRequest(
                            source_id=source_id,
                            source_version="1.0.0",
                            url=url,
                            allowed_domains=self._allowed_domains,
                            timeout_seconds=self._timeout_seconds,
                            max_response_bytes=self._max_response_bytes,
                        )
                    )
                except SourceFetchError as error:
                    failures.append(str(error))
                    continue
                findings.extend(_research_finding(item, niche) for item in items)
        if not findings:
            detail = "; ".join(failures) or "configured feeds produced no items"
            raise SourceFetchError(detail)
        return findings


def _rss_item(
    request: SourceFetchRequest,
    entry: ElementTree.Element,
    raw_feed: bytes,
    fetched_at: datetime,
    rate_limit: dict[str, str],
) -> FetchedSource:
    is_atom = entry.tag.endswith("entry")
    title = _text(entry, "title")
    raw_identity = _text(entry, "id" if is_atom else "guid") or _text(entry, "link")
    if not raw_identity:
        raise SourceFetchError("feed item has no stable GUID or link")
    canonical_url = _atom_link(entry) if is_atom else _text(entry, "link")
    if not canonical_url:
        raise SourceFetchError("feed item has no canonical link")
    published_at = _parse_date(
        _text(entry, "published" if is_atom else "pubDate")
        or _text(entry, "updated" if is_atom else "date")
    )
    content: dict[str, Any] = {
        "title": title,
        "summary": _text(entry, "summary" if is_atom else "description"),
    }
    return FetchedSource(
        source_id=request.source_id,
        source_version=request.source_version,
        source_type="rss_atom",
        resource_identity=raw_identity,
        canonical_url=canonical_url,
        raw_identity=raw_identity,
        raw_hash=sha256(raw_feed).hexdigest(),
        fetched_at=fetched_at,
        published_at=published_at,
        title=title,
        content=content,
        raw_payload=raw_feed,
        features={},
        rate_limit=rate_limit,
        trust_level="untrusted_external",
        provenance={"connector": "rss_atom", "request_url": request.url},
    )


def _research_finding(item: FetchedSource, niche: str) -> ResearchFinding:
    content = {
        **item.content,
        "niche": niche,
        "features": _available_features(item),
    }
    return ResearchFinding(
        source_uri=item.canonical_url,
        fetched_at=item.fetched_at,
        content=content,
        trust_level="untrusted_external",
        verification_status="unverified",
        source_identity=item.resource_identity,
        provenance={
            **item.provenance,
            "connector": "rss_atom",
            "source_id": item.source_id,
            "source_version": item.source_version,
            "raw_hash": item.raw_hash,
        },
        raw_content_classification="external",
    )


def _available_features(item: FetchedSource) -> dict[str, float]:
    if item.published_at is None:
        return {"confidence": 0.5}
    age_days = max(0.0, (item.fetched_at - item.published_at).total_seconds() / 86_400)
    return {"freshness": max(0.0, min(1.0, 1 - (age_days / 30))), "confidence": 0.5}


def _text(element: ElementTree.Element, local_name: str) -> str | None:
    direct = element.findtext(local_name)
    if direct:
        return direct.strip()
    namespaced = element.findtext(f"{{http://www.w3.org/2005/Atom}}{local_name}")
    return namespaced.strip() if namespaced else None


def _atom_link(element: ElementTree.Element) -> str | None:
    link = element.find("{http://www.w3.org/2005/Atom}link")
    return link.get("href") if link is not None else None


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC)
