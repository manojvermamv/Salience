"""Lazy optional Playwright adapter for bounded, read-only research."""

from __future__ import annotations

from hashlib import sha256
from tempfile import TemporaryDirectory

from salience.browser.contracts import (
    BrowserResearchRequest,
    BrowserResearchResult,
    BrowserUnavailable,
    NetworkScopeDenied,
)
from salience.contracts.storage import ObjectStore
from salience.research.http import assert_network_scope


class PlaywrightBrowserResearchTool:
    def __init__(self, *, object_store: ObjectStore, allowed_domains: frozenset[str]) -> None:
        self._object_store = object_store
        self._allowed_domains = allowed_domains

    async def read(self, request: BrowserResearchRequest) -> BrowserResearchResult:
        assert_network_scope(request.url, self._allowed_domains)
        try:
            from playwright.async_api import async_playwright
        except ImportError as error:
            raise BrowserUnavailable(
                "browser research requires the optional browser extra and an installed browser binary"
            ) from error

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                context = await browser.new_context(accept_downloads=False)
                await context.tracing.start(screenshots=True, snapshots=True)
                page = await context.new_page()

                async def enforce_route(route) -> None:
                    try:
                        assert_network_scope(route.request.url, self._allowed_domains)
                    except NetworkScopeDenied:
                        await route.abort()
                        return
                    await route.continue_()

                await page.route("**/*", enforce_route)
                await page.goto(request.url, timeout=round(request.timeout_seconds * 1000))
                assert_network_scope(page.url, self._allowed_domains)
                text = await page.locator("body").inner_text(timeout=round(request.timeout_seconds * 1000))
                if len(text.encode()) > request.max_response_bytes:
                    raise BrowserUnavailable("browser extraction exceeded the configured byte limit")
                screenshot = await page.screenshot()
                with TemporaryDirectory(prefix="salience-browser-") as directory:
                    trace_path = f"{directory}/trace.zip"
                    await context.tracing.stop(path=trace_path)
                    with open(trace_path, "rb") as trace_file:
                        trace = trace_file.read()
                base_key = f"browser/{sha256(page.url.encode()).hexdigest()}"
                text_receipt = self._object_store.put(
                    key=f"{base_key}.txt",
                    data=text.encode(),
                    content_type="text/plain",
                    metadata={"url": page.url},
                )
                screenshot_receipt = self._object_store.put(
                    key=f"{base_key}.png",
                    data=screenshot,
                    content_type="image/png",
                    metadata={"url": page.url},
                )
                trace_receipt = self._object_store.put(
                    key=f"{base_key}.zip",
                    data=trace,
                    content_type="application/zip",
                    metadata={"url": page.url},
                )
                return BrowserResearchResult(
                    canonical_url=page.url,
                    text_artifact_key=text_receipt.key,
                    screenshot_artifact_key=screenshot_receipt.key,
                    trace_artifact_key=trace_receipt.key,
                    text_hash=text_receipt.content_hash,
                )
            finally:
                await browser.close()
