"""Shared bounded HTTP rules for every direct research source adapter."""

from __future__ import annotations

from urllib.parse import urljoin, urlparse

import httpx

from salience.research.contracts import (
    NetworkScopeDenied,
    SourceFetchError,
    SourceFetchRequest,
)


def assert_network_scope(url: str, allowed_domains: frozenset[str]) -> None:
    parsed = urlparse(url)
    hostname = parsed.hostname
    if parsed.scheme != "https" or not hostname or hostname not in allowed_domains:
        raise NetworkScopeDenied(f"research URL is outside the configured network scope: {url}")


async def bounded_get(
    client: httpx.AsyncClient,
    request: SourceFetchRequest,
    *,
    accepted_content_types: frozenset[str],
) -> httpx.Response:
    assert_network_scope(request.url, request.allowed_domains)
    response = await client.get(
        request.url,
        follow_redirects=False,
        timeout=request.timeout_seconds,
    )
    if response.is_redirect:
        location = response.headers.get("location")
        if location:
            assert_network_scope(urljoin(request.url, location), request.allowed_domains)
        raise SourceFetchError("redirected source fetches require an explicit configured URL")
    if response.is_error:
        raise SourceFetchError(f"source request failed with HTTP {response.status_code}")
    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
    if content_type and content_type not in accepted_content_types:
        raise SourceFetchError(f"unexpected source content type: {content_type}")
    if len(response.content) > request.max_response_bytes:
        raise SourceFetchError("source response exceeded configured byte limit")
    return response


def rate_limit_metadata(response: httpx.Response) -> dict[str, str]:
    retry_after = response.headers.get("retry-after")
    return {"retry_after": retry_after} if retry_after else {}
