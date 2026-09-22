"""Lazy optional Playwright adapter for bounded, read-only research."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Mapping

from salience.browser.contracts import (
    BrowserEvidenceFailure,
    BrowserResearchRequest,
    BrowserResearchResult,
    BrowserUnavailable,
    NetworkScopeDenied,
)
from salience.contracts.storage import ObjectReceipt, ObjectStore
from salience.research.http import assert_network_scope


class PlaywrightBrowserResearchTool:
    def __init__(
        self,
        *,
        object_store: ObjectStore,
        allowed_domains: frozenset[str],
        allow_private_network: bool = False,
        ignore_https_errors: bool = False,
    ) -> None:
        self._object_store = object_store
        self._allowed_domains = allowed_domains
        self._allow_private_network = allow_private_network
        self._ignore_https_errors = ignore_https_errors

    async def read(self, request: BrowserResearchRequest) -> BrowserResearchResult:
        self._assert_network_scope(request.url)
        try:
            from playwright.async_api import async_playwright
        except ImportError as error:
            raise BrowserUnavailable(
                "browser research requires the optional browser extra and an installed browser binary"
            ) from error

        try:
            playwright_version = version("playwright")
        except PackageNotFoundError:
            playwright_version = "unknown"

        async with async_playwright() as playwright:
            try:
                browser = await playwright.chromium.launch(headless=True)
            except Exception as error:
                raise BrowserUnavailable(
                    "browser launch failed; provision a compatible browser outside Salience"
                ) from error
            try:
                return await self._capture(
                    browser=browser,
                    request=request,
                    playwright_version=playwright_version,
                )
            finally:
                await browser.close()

    async def _capture(
        self,
        *,
        browser: object,
        request: BrowserResearchRequest,
        playwright_version: str,
    ) -> BrowserResearchResult:
        base_key = self._base_key(request)
        browser_version = browser.version
        context = await browser.new_context(
            accept_downloads=False,
            ignore_https_errors=self._ignore_https_errors,
        )
        trace_started = False
        trace_artifact_key: str | None = None
        try:
            await context.tracing.start(screenshots=True, snapshots=True)
            trace_started = True
            page = await context.new_page()
            request_count = 0

            async def enforce_route(route) -> None:
                nonlocal request_count
                request_count += 1
                if request_count > request.step_limit:
                    await route.abort()
                    return
                try:
                    self._assert_network_scope(route.request.url)
                except NetworkScopeDenied:
                    await route.abort()
                    return
                await route.continue_()

            await page.route("**/*", enforce_route)
            timeout_ms = round(request.timeout_seconds * 1000)
            await page.goto(request.url, timeout=timeout_ms)
            self._assert_network_scope(page.url)
            text = await page.locator("body").inner_text(timeout=timeout_ms)
            if len(text.encode()) > request.max_response_bytes:
                raise BrowserEvidenceFailure("response_limit")
            screenshot = await page.screenshot()
            fetched_at = datetime.now(UTC).isoformat()
            trace_data = await self._stop_trace(context)
            trace_started = False
            metadata = self._metadata(
                source_url=page.url,
                fetched_at=fetched_at,
                playwright_version=playwright_version,
                browser_version=browser_version,
                request=request,
            )
            text_receipt = self._store_artifact(
                base_key=base_key,
                extension="txt",
                data=text.encode(),
                content_type="text/plain",
                metadata=metadata,
                artifact_type="text",
            )
            screenshot_receipt = self._store_artifact(
                base_key=base_key,
                extension="png",
                data=screenshot,
                content_type="image/png",
                metadata=metadata,
                artifact_type="screenshot",
            )
            trace_receipt = self._store_artifact(
                base_key=base_key,
                extension="zip",
                data=trace_data,
                content_type="application/zip",
                metadata=metadata,
                artifact_type="trace",
            )
            return BrowserResearchResult(
                canonical_url=page.url,
                text_artifact_key=text_receipt.key,
                screenshot_artifact_key=screenshot_receipt.key,
                trace_artifact_key=trace_receipt.key,
                text_hash=text_receipt.content_hash,
                screenshot_hash=screenshot_receipt.content_hash,
                trace_hash=trace_receipt.content_hash,
                fetched_at=fetched_at,
                playwright_version=playwright_version,
                browser_version=browser_version,
            )
        except Exception as error:
            if trace_started:
                try:
                    trace_data = await self._stop_trace(context)
                    trace_started = False
                    trace_receipt = self._store_artifact(
                        base_key=f"{base_key}.failure",
                        extension="zip",
                        data=trace_data,
                        content_type="application/zip",
                        metadata=self._metadata(
                            source_url=request.url,
                            fetched_at=datetime.now(UTC).isoformat(),
                            playwright_version=playwright_version,
                            browser_version=browser_version,
                            request=request,
                        ),
                        artifact_type="trace",
                    )
                    trace_artifact_key = trace_receipt.key
                except Exception:
                    trace_artifact_key = None
            if isinstance(error, BrowserEvidenceFailure):
                raise BrowserEvidenceFailure(
                    error.category,
                    trace_artifact_key=trace_artifact_key or error.trace_artifact_key,
                ) from error
            if isinstance(error, NetworkScopeDenied):
                category = "network_scope"
            elif "timeout" in type(error).__name__.lower():
                category = "timeout"
            else:
                category = "navigation"
            raise BrowserEvidenceFailure(category, trace_artifact_key=trace_artifact_key) from error
        finally:
            if trace_started:
                try:
                    await self._stop_trace(context)
                except Exception:
                    pass
            await context.close()

    def _assert_network_scope(self, url: str) -> None:
        assert_network_scope(
            url,
            self._allowed_domains,
            allow_private_network=self._allow_private_network,
        )

    @staticmethod
    def _base_key(request: BrowserResearchRequest) -> str:
        identity = request.tool_run_id or request.url
        return f"browser/{sha256(f'{request.url}:{identity}'.encode()).hexdigest()}"

    @staticmethod
    async def _stop_trace(context: object) -> bytes:
        with TemporaryDirectory(prefix="salience-browser-") as directory:
            trace_path = Path(directory) / "trace.zip"
            await context.tracing.stop(path=str(trace_path))
            return trace_path.read_bytes()

    @staticmethod
    def _metadata(
        *,
        source_url: str,
        fetched_at: str,
        playwright_version: str,
        browser_version: str,
        request: BrowserResearchRequest,
    ) -> dict[str, str]:
        return {
            "source_url": source_url,
            "fetched_at": fetched_at,
            "playwright_version": playwright_version,
            "browser_version": browser_version,
            "agent_run_id": request.agent_run_id or "",
            "tool_run_id": request.tool_run_id or "",
            "trace_id": request.trace_id or "",
        }

    def _store_artifact(
        self,
        *,
        base_key: str,
        extension: str,
        data: bytes,
        content_type: str,
        metadata: Mapping[str, str],
        artifact_type: str,
    ) -> ObjectReceipt:
        content_hash = sha256(data).hexdigest()
        return self._object_store.put(
            key=f"{base_key}.{extension}",
            data=data,
            content_type=content_type,
            metadata={
                **metadata,
                "artifact_type": artifact_type,
                "artifact_hash": content_hash,
            },
        )
