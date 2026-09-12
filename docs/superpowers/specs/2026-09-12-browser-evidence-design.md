# Governed Browser Evidence Design

**Status:** Approved by the requested browser-evidence goal on 2026-09-12.

## Purpose

This design makes the existing `BrowserResearchTool` a production-compatible, read-only evidence collector before Phase 7 begins. It validates a real Playwright Chromium session on the current Debian 13 EC2 host without adding a second browser architecture, a provider dependency, publishing automation, or Phase 7/8 capabilities.

## Evidence And Environment Baseline

- Host: Debian GNU/Linux 13 (trixie), Linux 6.12 AMD64, Python 3.13.5.
- Repository pin: `playwright==1.62.0` remains unchanged; PyPI confirms that exact release is available for the supported Python version.
- Browser baseline: neither `chromium`, `chromium-browser`, nor `google-chrome` is installed. The Debian package candidate is not selected because Playwright documents its revision-matched bundled browser as the supported runtime.
- Capacity baseline: approximately 6.5 GiB free. Docker images consume space, but no Docker or system cleanup is authorized or performed.
- Official installation decision: use project-owned `python -m playwright install-deps chromium` followed by `python -m playwright install chromium`. This is the Playwright-documented Linux installation sequence and installs only Chromium and its required shared-library dependencies. It does not install Firefox, WebKit, a system browser package, or a globally shared virtual environment.

References: [Playwright Python installation](https://playwright.dev/python/docs/intro), [browser management](https://playwright.dev/python/docs/browsers), and [browser launch API](https://playwright.dev/python/docs/api/class-browsertype).

## Build Versus Adopt

| Capability | Decision | Reason |
| --- | --- | --- |
| Browser automation/runtime | Adopt Playwright 1.62.0 plus its revision-matched Chromium | Mature, free, self-hostable, already an optional project dependency, and supports screenshots/traces. |
| Browser policy/evidence contract | Build a thin Salience adapter over Playwright | Existing owned DTO boundary keeps provider objects out of agents and persists governed evidence/provenance. |
| Object storage | Reuse the existing `ObjectStore` protocol | Avoids a competing storage system; the verifier supplies a disk-backed test implementation only. |
| Workflow/queue/auth/tracing | Reuse existing Salience contracts | This task adds browser evidence only and must not create parallel infrastructure. |
| Test TLS fixture | Build with Python standard library plus an ephemeral local certificate | Keeps the test isolated and credential-free while private-network access is explicitly enabled only by the fixture. |

## Design

### Operator Workflow

`scripts/setup-browser-evidence.sh` creates a project-local `.venv`, installs `.[browser,dev]`, installs Chromium dependencies through non-interactive `sudo`, then downloads Playwright's Chromium to the invoking operator cache. It is idempotent and reports the OS, free disk, Python, Playwright, browser executable, and browser version. It never removes Docker objects, volumes, packages, or browser caches.

`scripts/verify-browser-evidence.sh` is the single CI/operator entry point. Without `--install`, it checks prerequisites and returns a clear `NOT RUN` result if Chromium is absent. With `--install`, it invokes setup, creates `artifacts/browser-evidence/<run-id>/`, runs the browser-marked integration suite, and reports `PASS`, `FAIL`, or `NOT RUN` plus the evidence directory. Both scripts use `set -euo pipefail` and preserve generated evidence on failure.

### Governed Adapter

The existing `PlaywrightBrowserResearchTool` remains the only browser implementation. `BrowserResearchRequest` gains optional immutable run/provenance fields: `agent_run_id`, `tool_run_id`, and `trace_id`. `BrowserResearchResult` gains browser/runtime version, fetch timestamp, and content hashes for all artifacts.

For each request the adapter creates a fresh headless Chromium context with downloads disabled, starts Playwright tracing, and applies a route guard to every request. The guard requires HTTPS, exact allowlisted hostnames, and rejects loopback, private, link-local, multicast, unspecified, and reserved IP literals. A test-only constructor option allows the integration fixture's HTTPS loopback server; production callers cannot implicitly opt into private network access. Redirect destinations are guarded both at the route and after navigation.

The adapter permits no browser actions beyond bounded navigation, body text extraction, screenshot capture, and trace capture. It enforces a positive timeout, a page/network request limit, and a text byte limit. It does not expose storage state, secrets, downloads, write actions, browser-page evaluation, or arbitrary click/script interfaces.

Text is stored only as `text/plain` evidence and is returned as an artifact reference and SHA-256, never as trusted instructions. Screenshot and trace artifacts receive the same immutable metadata: source URL, fetch time, Playwright/browser version, trace ID, agent run ID, tool run ID, artifact type, and content hash. The error path stops and stores the trace when tracing was started, then raises a safe failure that contains category and artifact references rather than credentials, page body, or query-string secrets.

### Agent And Trust Lineage

`AgentExecutionContext` receives the run UUID before a runtime is invoked, so a real `BrowserResearchAgentRuntime` can pass a canonical agent-run ID, tool-run ID, and trace ID to the adapter. The persisted `AgentRun` retains that same UUID. Browser agent output remains `BrowserResearchResult@v1`, read-only, provider-neutral, and `untrusted_external`.

The existing `TrustPolicy` remains authoritative: untrusted browser text has no tool/delegated authority and cannot become verified memory. The browser layer therefore records evidence, not instructions or memory promotions.

### Test Harness

A browser-marked integration test starts a local TLS fixture that serves an allowlisted JavaScript page, a redirect, a download response, a deliberately slow response, and hostile prompt-injection text. The test grants loopback access and certificate relaxation only to the local fixture instance. A disk-backed `ObjectStore` test adapter writes every payload and adjacent metadata receipt below `SALIENCE_BROWSER_EVIDENCE_DIR`.

The suite proves: a real Chromium launch/version; JavaScript navigation; canonical structured result; text/screenshot/trace evidence; full provenance metadata and hashes; unapproved/redirect/private denial; download denial; bounded timeout with trace; untrusted hostile text with no authority/verified-memory promotion; and agent/tool/trace run lineage. It is invoked by the verifier script, not by a manually constructed shell test command.

### Documentation And Progress

README and operational documentation state the supported single command, evidence retention path, least-privilege scope, browser cache location, and EC2 limits. `docs/implementation-progress.md` records each completed checkpoint and final verification evidence so a later session can resume safely.

## Explicit Non-Goals

- AI content generation, publishing, media generation, analytics, learning loops, or Phase 7/8 work.
- A second browser provider/worker system, external browser SaaS, paid API, credentials, or new global infrastructure.
- DNS proxying or broad private-network access. The test fixture is the only explicit local exception.
- Destructive Docker/system cleanup, browser cache deletion, or changing the pinned Playwright version without a demonstrated compatibility failure.

## Acceptance Mapping

| Required proof | Implementation evidence |
| --- | --- |
| Real launch, version, JavaScript page | `pytest -m browser` fixture and verifier summary. |
| Structured evidence, artifact text, screenshot, trace | Extended owned DTOs and disk-backed evidence receipts. |
| Provenance/run/hash persistence | Artifact metadata assertions and agent runtime lineage test. |
| Scope, redirect, downloads, timeout safety | Route guard plus isolated HTTPS fixture cases. |
| Prompt injection remains untrusted | Browser agent result and `TrustPolicy` assertions. |
| One automated command | `bash scripts/verify-browser-evidence.sh [--install]`. |
